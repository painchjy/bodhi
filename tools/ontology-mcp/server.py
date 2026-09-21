"""本体知识「保存工具」——MCP 服务（給 WeKnora 智能体调用）。

为什么是 MCP（见 docs/weknora-fork.md §10.14）
--------------------------------------------
上游完整支持 MCP（`MCPService` 的 `sse`/`http-streamable`/`stdio`，`mcp_services` 表、
`MCPTool` 适配、前端配置页），工具名形如 `mcp_<服务名>_<工具名>`。
因此「分类 + 关系合规 + 与存量合并」可以做成**外部工具**挂给智能体，
**不需要改 Go、不需要重建 app 镜像**。

本服务暴露的工具
----------------
- `extract_and_save(model, kb_id, knowledge_id, ...)`
    取片段（正文用 parent_text、溯源用子块）→ **一次** LLM 抽取（复用 extract.py 引擎）
    → 本体合规校验（类型白名单 + domain→range）→ 逐要素向量匹配后
    **合并 / 新增 / 待人工确认**，并写 wiki 页面（含版本快照，可回退）。
- `list_pending_merges(kb_id)`：列出"疑似重复待确认"项。
- `resolve_pending_merge(kb_id, pending_slug, action)`：人工裁决（merge | create）。
- `ontology_types(model)`：返回该模块的可用类与关系（供智能体自查）。

合并语义（用户口径）
--------------------
- 相似度 `≥ high`（默认 0.90）→ **合并**：定义取更完整的一方，**追加原文证据**；
  同名多来源会在页面里累积（一个知识多个来源）；合并是**更新**，写 `wiki_page_revisions`
  旧版本快照 → `version+1`，**不改变 slug / 标题 / 反向链接**，可回退。
- 相似度 `≤ low`（默认 0.75）→ **新增**页面。
- 两者之间 → 生成一页 `ontology:PendingMerge`（slug `bodhi/pending/<hash>`），
  由人确认后调 `resolve_pending_merge`。

相似度实现
----------
默认 `lexical`：名称归一化精确匹配记 1.0，否则对「名称+定义」做**中文友好字符 3-gram 余弦**
（稀疏向量，零依赖、离线可用）。若配置了嵌入服务（`BODHI_EMBED_BASE_URL/API_KEY/MODEL`），
自动改用真实向量余弦（name+definition vs title+summary）。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import pathlib
import re
import subprocess
import sys
import threading
import uuid
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib import parse as urlparse
from urllib import request as urlrequest

REPO = pathlib.Path(__file__).resolve().parents[2]
LOG_DIR = REPO / "logs"
INDEX_PATH = REPO / "artifacts" / "weknora" / "ontology_index.json"
# 注意：wiki_pages.last_edit_source / wiki_page_revisions.edit_source 是 varchar(16)，
# 生成器标签**不能超过 16 个字符**（曾用 bodhi-ontology-mcp 导致写入整批回滚）。
TOOL_TAG = "bodhi-onto-mcp"
assert len(TOOL_TAG) <= 16, "TOOL_TAG 超过 last_edit_source 的 varchar(16)"

DB_CONTAINER, DB_USER, DB_NAME, DB_PASSWORD = "WeKnora-postgres", "postgres", "WeKnora", "postgres123!@#"
TYPE_PENDING = "ontology:PendingMerge"
PENDING_PREFIX = "bodhi/pending/"

DEFAULT_HIGH, DEFAULT_LOW = 0.90, 0.75

# Postgres/SQL 辅助与 now_text() 已移到 tools/ke-core/ke_db.py（见文件头 import）：
# 本模块仍以同样的名字暴露 psql / psql_csv / sql_str / sql_json / now_text，
# 因此 sync_folders.py 的 `server.psql_csv(...)` 这类调用**无需改动**
# （backfill_paths.py / relink_pages.py 是一次性脚本，已归档到 tools/ontology-mcp/archive/）。


# 同目录的 graph_page.py（Bodhi 语义图 HTML 页）
_HERE = str(pathlib.Path(__file__).resolve().parent)
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)
from graph_page import render_graph_page  # noqa: E402
from mdview import render as render_md  # noqa: E402

# ke-core（2026-09-19 拆分）：DB / 本体(Neo4j) / 页面维护各自独立成模块，
# 本文件只保留「MCP 传输 + 抽取合并流水线 + 只读图谱接口」。
KE_CORE = pathlib.Path(__file__).resolve().parents[1] / "ke-core"
if str(KE_CORE) not in sys.path:
    sys.path.insert(0, str(KE_CORE))
from ke_db import (  # noqa: E402,F401  （psql/sql_* 由同目录脚本 server.psql 等继续引用）
    DB_CONTAINER, DB_NAME, DB_PASSWORD, DB_USER,
    now_text as _now_text, psql, psql_csv, sql_json, sql_str,
)
import ke_ontology  # noqa: E402
import ke_admin  # noqa: E402
import ke_pages  # noqa: E402
import ke_docs  # noqa: E402  （按来源文档统计/清理本体实例，2026-09-20）
import ke_audit  # noqa: E402  （wiki↔图谱↔模型 一致性巡检，只读，2026-09-20 P1）
import ke_neo4j  # noqa: E402  （Neo4j 本体投影；ontology_types 补录、B5 一致性都用它）
from ke_pages import (  # noqa: E402,F401  （历史脚本 relink_pages.py 已归档，此别名保留兼容）
    REL_LINE, REL_LINE_V2, parse_rel_line, rel_line,
)

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            pass


def now_text() -> str:
    """（ke-core 拆分后保留的兼容别名：实现见 ke_db.now_text）"""
    return _now_text()





# ---------------------------------------------------------------------------
# 相似度：lexical（默认） / embedding（可选）
# ---------------------------------------------------------------------------
def normalize_name(text: str) -> str:
    return re.sub(r"[\s\u3000·、，,。.（）()【】\[\]:：;；\-—_/]+", "", (text or "").lower())


def _ngrams(text: str, n: int = 3) -> dict[str, int]:
    cleaned = re.sub(r"\s+", "", text or "")
    if len(cleaned) < n:
        return {cleaned: 1} if cleaned else {}
    grams: dict[str, int] = {}
    for i in range(len(cleaned) - n + 1):
        gram = cleaned[i:i + n]
        grams[gram] = grams.get(gram, 0) + 1
    return grams


def lexical_similarity(text_a: str, text_b: str) -> float:
    """字符 3-gram 余弦：中文不需要分词，短文本上表现稳定。"""
    a, b = _ngrams(text_a), _ngrams(text_b)
    if not a or not b:
        return 0.0
    common = set(a) & set(b)
    dot = sum(a[g] * b[g] for g in common)
    na = math.sqrt(sum(v * v for v in a.values()))
    nb = math.sqrt(sum(v * v for v in b.values()))
    return dot / (na * nb) if na and nb else 0.0


def embed_texts(texts: list[str]) -> list[list[float]] | None:
    """可选：调 OpenAI 兼容嵌入服务；未配置返回 None（调用方退回 lexical）。"""
    import os  # noqa: PLC0415
    base = os.environ.get("BODHI_EMBED_BASE_URL", "").rstrip("/")
    key = os.environ.get("BODHI_EMBED_API_KEY", "")
    model = os.environ.get("BODHI_EMBED_MODEL", "")
    if not (base and model):
        return None
    payload = json.dumps({"model": model, "input": texts}).encode("utf-8")
    req = urlrequest.Request(base + "/embeddings", data=payload, method="POST")
    req.add_header("Content-Type", "application/json")
    if key:
        req.add_header("Authorization", "Bearer " + key)
    try:
        with urlrequest.urlopen(req, timeout=60) as resp:
            body = json.loads(resp.read().decode("utf-8"))
        return [item["embedding"] for item in body["data"]]
    except Exception as exc:  # noqa: BLE001
        print("[mcp] 嵌入调用失败，退回 lexical：%s" % exc)
        return None


def cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


# ---------------------------------------------------------------------------
# Postgres（与 weknora_sync.py 同一套约定：走容器里的 psql）
#   `_docker_prefix` / `psql` / `psql_csv` / `sql_str` / `sql_json` 已移到
#   tools/ke-core/ke_db.py，并在文件头 import 回来（名字不变，下游脚本无感）。
# ---------------------------------------------------------------------------
def page_id_for(slug: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, "bodhi-element:" + slug))


# ---------------------------------------------------------------------------
# 片段读取 + 一次性抽取
# ---------------------------------------------------------------------------
def load_engine():
    here = str(REPO / "tools" / "ontology-extract")
    if here not in sys.path:
        sys.path.insert(0, here)
    import extract  # noqa: PLC0415
    return extract


def fetch_chunks(knowledge_id: str) -> list[dict]:
    rows = psql_csv("SELECT id, chunk_index, chunk_type, content FROM chunks "
                    "WHERE knowledge_id = %s AND deleted_at IS NULL "
                    "ORDER BY chunk_index, id" % sql_str(knowledge_id))
    for row in rows:
        row["chunk_index"] = int(row.get("chunk_index") or 0)
    return rows


def split_body_pool(chunks: list[dict]) -> tuple[list[dict], list[dict]]:
    body = [c for c in chunks if c.get("chunk_type") == "parent_text"]
    if not body:
        texts = [c for c in chunks if c.get("chunk_type") == "text"]
        pool = texts or [c for c in chunks if c.get("chunk_type") != "summary"]
        if pool:
            body = [max(pool, key=lambda c: len(c["content"] or ""))]
    pool = [c for c in chunks if c.get("chunk_type") == "text"] or body
    return body, pool


def squash(text: str) -> str:
    return "".join((text or "").split())


def locate_chunk(source_text: str, pool: list[dict]) -> tuple[str, int]:
    needle = squash(source_text)
    if needle:
        for chunk in pool:
            if needle in squash(chunk["content"]):
                return chunk["id"], chunk["chunk_index"]
    return "", -1


def run_extraction(engine, model_key: str, doc_name: str, doc_text: str,
                   from_log: str = "") -> dict:
    """调用 LLM 抽取一次并做本体校验（不写任何库）。"""
    index = engine.load_index(engine.DEFAULT_INDEX)
    model = engine.pick_model(index, model_key)
    system = engine.build_system_prompt(model, engine.load_light(model, engine.DEFAULT_PROMPTS))
    env = engine.load_env(REPO / ".env")
    user = engine.build_user_prompt(doc_name, doc_text, [])
    if from_log:
        raw = engine.read_raw_from_log(pathlib.Path(from_log))
    else:
        cfg = {
            "api_key": env.get("LLM_API_KEY", ""),
            "base_url": env.get("LLM_BASE_URL", "https://api.deepseek.com"),
            "model": env.get("LLM_MODEL", "deepseek-flash"),
            "temperature": float(env.get("LLM_TEMPERATURE", "0.1")),
            "max_tokens": int(env.get("LLM_MAX_TOKENS", "32768")),
            "json_mode": env.get("LLM_JSON_MODE", "1") == "1",
        }
        raw, _meta = engine.call_llm(cfg, system, user, method="mcp_extraction")
    checked = engine.validate(engine.parse_json(raw), model, doc_name)
    return {"model": model, "checked": checked, "raw_len": len(raw)}


# ---------------------------------------------------------------------------
# 存量页面 / 匹配 / 合并
# ---------------------------------------------------------------------------
PAGE_COLUMNS = [
    "id", "tenant_id", "knowledge_base_id", "slug", "title", "page_type", "status",
    "content", "summary", "parent_slug", "folder_id", "category_path", "wiki_path",
    "depth", "sort_order", "source_refs", "chunk_refs", "in_links", "out_links",
    "page_metadata", "aliases", "version", "last_edit_source", "last_editor_id",
]


def resolve_kb_id(raw: str) -> tuple[str, str]:
    """把 kb_id 参数解析成真实 UUID：支持 UUID / 知识库名称（精确或包含）。

    2026-09-19 实测：智能体传的是知识库**名称**（如「企业知识库」）而不是 UUID，
    老实现直接抛「知识库不存在」导致整次抽取失败。这里做容错解析，并把说明回传给
    智能体，让它下次直接用真实 id。
    """
    raw = (raw or "").strip()
    if re.fullmatch(r"[0-9a-fA-F-]{36}", raw):
        return raw, ""
    rows = psql_csv("SELECT id, name FROM knowledge_bases WHERE deleted_at IS NULL "
                    "ORDER BY updated_at DESC")

    def _norm(s: str) -> str:
        return re.sub(r"[\s　]+", "", s or "")

    if raw:
        want = _norm(raw)
        hit = [r for r in rows if _norm(r["name"]) == want] \
            or [r for r in rows if _norm(r["name"]) in want or want in _norm(r["name"])]
        if len(hit) == 1:
            return hit[0]["id"], "（kb_id「%s」按名称解析为 %s）" % (raw, hit[0]["name"])
        if len(hit) > 1:
            raise RuntimeError("知识库名称不唯一：%s → %s"
                               % (raw, "、".join(r["name"] for r in hit)))
    raise RuntimeError("知识库不存在：%s；可选：%s"
                       % (raw or "(空)", "、".join(r["name"] for r in rows) or "（无）"))


def resolve_knowledge_id(kb_id: str, raw: str) -> tuple[str, str]:
    """把 knowledge_id 解析成真实 UUID：支持 UUID / id 前缀 / 标题包含；
    占位符或无法解析时退回该知识库**最新一篇**文档（并在返回里说明）。"""
    rows = psql_csv("SELECT id, title FROM knowledges WHERE knowledge_base_id = %s AND deleted_at IS NULL "
                    "ORDER BY created_at DESC" % sql_str(kb_id))
    if not rows:
        raise RuntimeError("该知识库还没有文档：请先上传文档并等切片完成")
    raw = (raw or "").strip()
    if raw:
        for r in rows:
            if r["id"] == raw:
                return r["id"], ""
        for r in rows:
            if r["id"].startswith(raw):
                return r["id"], "（knowledge_id「%s」按 id 前缀匹配）" % raw
            if raw.lower() in (r["title"] or "").lower():
                return r["id"], "（knowledge_id「%s」按标题匹配：%s）" % (raw, r["title"])
    return rows[0]["id"], ("（knowledge_id「%s」无法解析，已改用该库最新文档：%s）"
                           % (raw or "(空)", rows[0]["title"]))


def get_kb_tenant(kb_id: str) -> int:
    row = psql("SELECT tenant_id FROM knowledge_bases WHERE id = %s AND deleted_at IS NULL"
               % sql_str(kb_id))
    if not row.strip():
        raise RuntimeError("知识库不存在：%s" % kb_id)
    return int(row.strip().splitlines()[0])


def fetch_existing_pages(kb_id: str) -> list[dict]:
    """只取"要素页"（排除待确认页与索引页）作为合并候选。"""
    rows = psql_csv(
        "SELECT id, tenant_id, knowledge_base_id, slug, title, page_type, status, content, "
        "       summary, version, aliases, COALESCE(source_refs::text,'[]') AS source_refs, "
        "       COALESCE(chunk_refs::text,'[]') AS chunk_refs, "
        "       COALESCE(in_links::text,'[]') AS in_links, COALESCE(out_links::text,'[]') AS out_links, "
        "       COALESCE(page_metadata::text,'{}') AS page_metadata "
        "FROM wiki_pages WHERE knowledge_base_id = %s AND deleted_at IS NULL "
        "AND page_type NOT IN ('index', 'summary', %s)" % (sql_str(kb_id), sql_str(TYPE_PENDING)))
    for row in rows:
        row["version"] = int(row.get("version") or 1)
        for key in ("aliases", "source_refs", "chunk_refs", "in_links", "out_links", "page_metadata"):
            try:
                row[key] = json.loads(row.get(key) or ("{}" if key == "page_metadata" else "[]"))
            except json.JSONDecodeError:
                row[key] = {} if key == "page_metadata" else []
    return rows


def score_element(element: dict, page: dict) -> float:
    """名称归一化相等直接 1.0；否则 3-gram 余弦（名称权重更高）。"""
    if normalize_name(element["name"]) == normalize_name(page["title"]):
        return 1.0
    for alias in page.get("aliases") or []:
        if normalize_name(element["name"]) == normalize_name(alias):
            return 1.0
    name_sim = lexical_similarity(element["name"], page["title"])
    text_sim = lexical_similarity(
        "%s %s" % (element["name"], element.get("definition", "")),
        "%s %s" % (page["title"], page.get("summary", "")))
    return round(max(name_sim, 0.5 * name_sim + 0.5 * text_sim), 4)


def pick_match(element: dict, pages: list[dict]) -> tuple[float, dict | None]:
    best_sim, best_page = 0.0, None
    for page in pages:
        sim = score_element(element, page)
        if sim > best_sim:
            best_sim, best_page = sim, page
    return best_sim, best_page


def element_slug(model_key: str, element: dict) -> str:
    local = (element["type"].split(":", 1)[-1] or "unknown").lower()
    return "%s/%s/%s" % (model_key, local, normalize_name(element["name"]) or
                         hashlib.sha1(element["name"].encode("utf-8")).hexdigest()[:12])


def element_page_slug(model: dict, element: dict) -> str:
    """页面 slug：设计载荷自带 `slug`（按**节点自身类所属模块**算，如 `bmm-ea-ext/apiservice/…`）；
    没有时才按模型 key 推导（抽取路径的原行为，保持不变）。"""
    explicit = (element.get("slug") or "").strip()
    return explicit or element_slug(model["key"], element)


def same_type_family(a: str, b: str) -> bool:
    """两个本体类是否同一族（相等或同一继承链）——用于「只在同类页之间做合并」的守卫。"""
    if not a or not b:
        return False
    if a == b:
        return True
    try:
        return a in ke_ontology.ancestors(b) or b in ke_ontology.ancestors(a)
    except Exception:  # noqa: BLE001
        return False


def build_new_page(engine, model: dict, element: dict, chunk_id: str, chunk_index: int,
                   doc_meta: dict) -> dict:
    """新要素 -> 页面（与 ontology_wiki/weknora_sync 同风格：能被人读，也能被图谱用）。"""
    rels = element.get("relations") or []
    upstream = [s for s in (element.get("upstream") or []) if s]
    slug = element_page_slug(model, element)
    lines = ["# %s（`%s`）" % (element["name"], element["type"]), "",
             "> **本体类型**：%s（`%s`）  " % (element["type_label"], element["type"]),
             "> **来源**：《%s》%s  " % (doc_meta["title"],
                                       (" 片段 #%d" % chunk_index) if chunk_index >= 0 else ""),
             "> **首个版本生成**：%s（`%s`）" % (now_text(), TOOL_TAG), "",
             (element.get("definition") or "（暂无定义）").strip(), ""]
    if element.get("description"):
        lines += ["## 判定依据", "", element["description"].strip(), ""]
    lines += ["## 原文依据", "",
              "- %s（来源：《%s》%s）" % (element.get("source_text", "").strip(), doc_meta["title"],
                                        (" 片段 #%d" % chunk_index) if chunk_index >= 0 else ""), ""]
    if rels:
        lines += ["## 本体关系", ""]
        for rel in rels:
            lines.append(rel_line(rel.get("label") or rel["type"], rel["type"],
                                  rel["target"], rel.get("target_slug") or ""))
        lines.append("")
    if upstream:
        # 方案 C 溯源：来源**文档**写在 `## 原文依据`；上游**页面**写在这里（设计页没有源文片段）
        # 注意：循环变量不要叫 slug —— 会覆盖上面的页面 slug（2026-09-21 沙箱实测踩到）
        lines += ["## 溯源", ""]
        for up in upstream:
            lines.append("- 上游页面：`%s`" % up)
        lines.append("")
    return {
        "slug": slug, "title": element["name"], "page_type": element["type"],
        "summary": (element.get("definition") or "")[:500],
        "content": "\n".join(lines).rstrip() + "\n",
        "category_path": class_category_path(element["type"], model["key"], model["label"],
                                             element["type_label"]),
        "wiki_path": slug, "source_refs": [doc_meta["id"]] if doc_meta.get("id") else [],
        "chunk_refs": [chunk_id] if chunk_id else [],
        "out_links": sorted({r.get("target_slug") for r in rels if r.get("target_slug")}),
        "aliases": element.get("aliases") or [element["name"]],
        "page_metadata": dict({
            "ontology": {
                "model": model["key"], "class": element["type"], "class_label": element["type_label"],
                "name": element["name"], "generator": TOOL_TAG, "created_at": now_text(),
            }}, **({"design": {"upstream": upstream, "generator": TOOL_TAG}} if upstream else {})),
    }


def merge_content(old_content: str, element: dict, chunk_id: str, chunk_index: int,
                  doc_meta: dict) -> tuple[str, dict]:
    """合并：定义取更完整的一方；追加原文证据；关系行去重后追加。不动标题/类型/其它章节。"""
    lines = old_content.splitlines()
    first_h2 = next((i for i, line in enumerate(lines) if line.startswith("## ")), len(lines))
    head, tail = lines[:first_h2], lines[first_h2:]
    last_meta = max((i for i, line in enumerate(head) if line.startswith("> ")), default=0)
    old_def = "\n".join(head[last_meta + 1:]).strip()
    new_def = (element.get("definition") or "").strip()
    chosen = new_def if len(new_def) > len(old_def) else old_def
    merged = head[:last_meta + 1] + ["", chosen, ""] + tail

    evidence = "- %s（来源：《%s》%s）" % (
        element.get("source_text", "").strip(), doc_meta["title"],
        (" 片段 #%d" % chunk_index) if chunk_index >= 0 else "")
    added_evidence = False
    if any(line.strip() == "## 原文依据" for line in merged):
        pos = next(i for i, line in enumerate(merged) if line.strip() == "## 原文依据")
        end = next((i for i in range(pos + 1, len(merged)) if merged[i].startswith("## ")), len(merged))
        existing = {squash(line) for line in merged[pos + 1:end] if line.strip()}
        if squash(evidence) not in existing:
            insert_at = end
            while insert_at > pos + 1 and not merged[insert_at - 1].strip():
                insert_at -= 1
            merged.insert(insert_at, evidence)
            added_evidence = True
    else:
        merged += ["", "## 原文依据", "", evidence, ""]

    added_relations = []
    rels = element.get("relations") or []
    if rels and any(line.strip() == "## 本体关系" for line in merged):
        pos = next(i for i, line in enumerate(merged) if line.strip() == "## 本体关系")
        end = next((i for i in range(pos + 1, len(merged)) if merged[i].startswith("## ")), len(merged))
        block = "\n".join(merged[pos + 1:end])
        for rel in rels:
            if rel.get("target_slug") and rel["target_slug"] in block:
                continue
            merged.insert(end, rel_line(rel.get("label") or rel["type"], rel["type"],
                                       rel["target"], rel.get("target_slug") or ""))
            added_relations.append(rel["type"])
            end += 1
    elif rels:
        merged += ["", "## 本体关系", ""]
        for rel in rels:
            merged.append(rel_line(rel.get("label") or rel["type"], rel["type"],
                                   rel["target"], rel.get("target_slug") or ""))
            added_relations.append(rel["type"])
        merged.append("")

    upstream = [s for s in (element.get("upstream") or []) if s]
    added_upstream = []
    if upstream:
        if any(line.strip() == "## 溯源" for line in merged):
            pos = next(i for i, line in enumerate(merged) if line.strip() == "## 溯源")
            end = next((i for i in range(pos + 1, len(merged)) if merged[i].startswith("## ")),
                       len(merged))
            existing = {squash(line) for line in merged[pos + 1:end] if line.strip()}
            for slug in upstream:
                item = "- 上游页面：`%s`" % slug
                if squash(item) not in existing:
                    merged.insert(end, item)
                    added_upstream.append(slug)
                    end += 1
        else:
            merged += ["", "## 溯源", ""] + ["- 上游页面：`%s`" % s for s in upstream] + [""]
            added_upstream = list(upstream)

    post = {"definition_upgraded": bool(new_def) and len(new_def) > len(old_def),
            "evidence_added": added_evidence, "relations_added": added_relations,
            "upstream_added": added_upstream}
    return "\n".join(merged).rstrip() + "\n", post


def union_list(old, new) -> list:
    return list(dict.fromkeys(list(old or []) + list(new or [])))


def sql_update_page(page: dict, content: str, summary: str, source_refs: list,
                    chunk_refs: list, metadata: dict) -> str:
    """合并 = 更新：先快照旧版本到 revisions（version 用旧值），再 version+1。"""
    return ("INSERT INTO wiki_page_revisions (id, tenant_id, knowledge_base_id, page_id, slug, version, "
            "       title, page_type, status, content, summary, aliases, edit_source, editor_id, "
            "       edited_at, created_at)\n"
            "SELECT gen_random_uuid()::text, tenant_id, knowledge_base_id, id, slug, version, "
            "       title, page_type, status, content, summary, aliases, '%s', "
            "       COALESCE(last_editor_id,''), now(), now()\n"
            "  FROM wiki_pages WHERE knowledge_base_id = %s AND slug = %s AND deleted_at IS NULL;\n"
            "UPDATE wiki_pages SET content = %s, summary = %s, source_refs = %s, chunk_refs = %s, "
            "       page_metadata = %s, version = version + 1, updated_at = now(), "
            "       last_edit_source = '%s' "
            " WHERE knowledge_base_id = %s AND slug = %s;\n"
            % (TOOL_TAG, sql_str(page["knowledge_base_id"]), sql_str(page["slug"]),
               sql_str(content), sql_str(summary), sql_json(source_refs), sql_json(chunk_refs),
               sql_json(metadata), TOOL_TAG, sql_str(page["knowledge_base_id"]),
               sql_str(page["slug"])))


def sql_insert_page(page: dict, kb_id: str, tenant_id: int) -> str:
    """插入新页；若该 slug 派生的确定性 id 已存在（含**软删除**的旧页）则改为复活+更新。

    2026-09-19 实测崩溃：page id 由 slug 派生（UUIDv5），而上游的重复检查只看内存里
    加载到的活页（deleted_at IS NULL）。用户删文档后旧页是**软删除**、id 仍占着，
    于是重复抽取会 INSERT 撞主键（wiki_pages_pkey）→ 整批事务回滚 → 工具报错。
    用 ON CONFLICT(id) DO UPDATE 一次解决：复活、覆盖内容、版本+1、标记来源。
    """
    values = [
        sql_str(page_id_for(page["slug"])), str(tenant_id), sql_str(kb_id),
        sql_str(page["slug"]), sql_str(page["title"]), sql_str(page["page_type"]),
        sql_str("published"), sql_str(page["content"]), sql_str(page["summary"]),
        sql_str(""), sql_str(""), sql_json(page["category_path"]), sql_str(page["wiki_path"]),
        str(len(page["category_path"])), "0", sql_json(page.get("source_refs") or []),
        sql_json(page.get("chunk_refs") or []), sql_json([]), sql_json(page.get("out_links") or []),
        sql_json(page["page_metadata"]), sql_json(page.get("aliases") or []), "1",
        sql_str(TOOL_TAG), sql_str(""),
    ]
    conflict = (
        "ON CONFLICT (id) DO UPDATE SET "
        "title = EXCLUDED.title, page_type = EXCLUDED.page_type, status = EXCLUDED.status, "
        "content = EXCLUDED.content, summary = EXCLUDED.summary, "
        "category_path = EXCLUDED.category_path, wiki_path = EXCLUDED.wiki_path, "
        "page_metadata = EXCLUDED.page_metadata, aliases = EXCLUDED.aliases, "
        "source_refs = EXCLUDED.source_refs, chunk_refs = EXCLUDED.chunk_refs, "
        "deleted_at = NULL, version = wiki_pages.version + 1, "
        "last_edit_source = %s, updated_at = now() "
        # 只对**软删除**行做「复活+覆盖」；活页被同一个 id 命中说明 slug 撞了（同要素应走合并），
        # 这时**不得**改写已有活页的 title/page_type —— 2026-09-21 沙箱实测：设计节点把报告页那行
        # 覆盖成了「注册信息登记服务 / bmm-ea-ext:APIService」。
        "WHERE wiki_pages.deleted_at IS NOT NULL" % sql_str(TOOL_TAG))
    return ("INSERT INTO wiki_pages (%s) VALUES (%s) %s;"
            % (", ".join(PAGE_COLUMNS), ", ".join(values), conflict))


def build_pending_page(model: dict, element: dict, candidate: dict, similarity: float,
                       chunk_id: str, chunk_index: int, doc_meta: dict,
                       high: float, low: float) -> dict:
    slug = PENDING_PREFIX + hashlib.sha1(
        ("%s|%s|%s" % (model["key"], element["type"], element["name"])).encode("utf-8")
    ).hexdigest()[:12]
    lines = ["# 待确认合并：%s（`%s`）" % (element["name"], element["type"]), "",
             "> **类型**：待确认合并（`%s`）  " % TYPE_PENDING,
             "> **相似度**：%.4f（≥%.2f 自动合并 ／ ≤%.2f 直接新增）  " % (similarity, high, low),
             "> **候选页**：`%s`（%s，版本 %s）  " % (candidate["slug"], candidate["title"],
                                                    candidate["version"]),
             "> **生成**：%s（`%s`）" % (now_text(), TOOL_TAG), "",
             "## 新要素", "",
             "- 名称：%s" % element["name"],
             "- 类型：%s（`%s`）" % (element["type_label"], element["type"]),
             "- 定义：%s" % (element.get("definition") or "（无）"),
             "- 原文依据：%s" % (element.get("source_text") or "（无）"),
             "- 来源：《%s》%s" % (doc_meta["title"],
                                (" 片段 #%d" % chunk_index) if chunk_index >= 0 else ""), "",
             "## 疑似同一的存量页面", "",
             "- %s（`%s`）" % (candidate["title"], candidate["slug"]),
             "- 定义：%s" % (candidate.get("summary") or "（无）"), "",
             "## 怎么裁决", "",
             "- 合并到该页：`resolve_pending_merge` 传 `pending_slug=\"%s\", action=\"merge\"`" % slug,
             "- 作为新页新增：`resolve_pending_merge` 传 `pending_slug=\"%s\", action=\"create\"`" % slug,
             ""]
    return {
        "slug": slug, "title": "待确认合并：%s" % element["name"], "page_type": TYPE_PENDING,
        "summary": "相似度 %.4f：新要素「%s」与已有页「%s」疑似同一，需人工裁决。"
                   % (similarity, element["name"], candidate["title"]),
        "content": "\n".join(lines).rstrip() + "\n",
        "category_path": [model["label"], "待确认合并"],
        "wiki_path": slug, "source_refs": [doc_meta["id"]],
        "chunk_refs": [chunk_id] if chunk_id else [],
        "out_links": [candidate["slug"]], "aliases": [],
        "page_metadata": {"ontology": {
            "kind": "pending_merge", "model": model["key"], "candidate_slug": candidate["slug"],
            "similarity": similarity, "high": high, "low": low,
            "element": {"name": element["name"], "type": element["type"],
                        "type_label": element["type_label"],
                        "definition": element.get("definition") or "",
                        "source_text": element.get("source_text") or "",
                        "relations": element.get("relations") or []},
            "doc": {"id": doc_meta["id"], "title": doc_meta["title"],
                    "chunk_id": chunk_id, "chunk_index": chunk_index},
            "generator": TOOL_TAG, "created_at": now_text(),
        }},
    }


# ---------------------------------------------------------------------------
# 主流程：抽取一次 → 合规校验 → 匹配 → 合并/新增/待确认
# ---------------------------------------------------------------------------
def element_payloads(engine, checked: dict, pool: list[dict]) -> list[dict]:
    """把校验后的 nodes/edges 整理成"待落页要素"（含出向关系与来源片段）。"""
    slug_by_name = {node["name"]: element_slug(node["module"], node) for node in checked["nodes"]}
    outgoing: dict[str, list[dict]] = {}
    for edge in checked["edges"]:
        outgoing.setdefault(edge["source"], []).append({
            "type": edge["type"], "label": edge["label"], "target": edge["target"],
            "target_slug": slug_by_name.get(edge["target"], ""),
            "source_text": edge.get("source_text", ""),
        })
    payloads = []
    for node in checked["nodes"]:
        chunk_id, chunk_index = locate_chunk(node.get("source_text", ""), pool)
        payloads.append({
            "name": node["name"], "type": node["type"], "type_label": node["type_label"],
            "module": node["module"], "definition": node["definition"],
            "description": node.get("description", ""), "source_text": node.get("source_text", ""),
            "chunk_id": chunk_id, "chunk_index": chunk_index,
            "relations": outgoing.get(node["name"], []),
        })
    return payloads


def sql_rebuild_in_links(kb_id: str) -> str:
    """按 out_links 重算 in_links（wiki 图谱的反向边）。实现见 ke_pages。

    注意：上游自己的页面（如根 index 页）可能把 out_links 写成**标量**，
    ke_pages 里已用 jsonb_typeof 守卫（保持此前的崩溃修复）。
    """
    return ke_pages.rebuild_in_links_sql(kb_id)


def save_elements(kb_id: str, model: dict, checked: dict, payloads: list, doc_meta: dict,
                  *, high: float = DEFAULT_HIGH, low: float = DEFAULT_LOW, dry_run: bool = False,
                  tenant_id: int | None = None, resolved_note: str = "", knowledge_id: str = "",
                  extra: dict | None = None, engine=None, same_type_only: bool = False) -> dict:
    """**落库半段**（抽取路径与设计路径共用）：相似度匹配 → 合并/新增/待确认 → 写页 → 重算 links。

    这段逻辑原先内联在 `extract_and_save` 里（2026-09-20 原样抽出，行为逐字保留）：
    - 相似度 ≥ high：合并进存量页（定义取更完整、追加证据、关系去重、写入 merge_history）；
    - 相似度 ≤ low 或无候选：新建页；
    - 两者之间：生成「待确认合并」页，交人工裁决（`resolve_pending_merge`）。
    """
    tenant = tenant_id if tenant_id is not None else get_kb_tenant(kb_id)
    pages = fetch_existing_pages(kb_id)
    statements: list[str] = []
    summary = {
        "model": model["key"], "kb_id": kb_id, "knowledge_id": knowledge_id,
        "resolved_note": (resolved_note or "").strip(), "doc_title": doc_meta.get("title", ""),
        "elements": len(payloads), "relationships": len(checked.get("edges") or []),
        "created": [], "merged": [], "pending": [],
        "violations": checked["violations"], "unmatched": checked["unmatched"],
        "dry_run": dry_run, "thresholds": {"high": high, "low": low},
        "generated_at": now_text(),
    }
    if extra:
        summary.update(extra)

    for element in payloads:
        # 幂等修正（2026-09-21）：**slug 完全相同**即同一要素（slug=模块/类/名称哈希），直接走合并；
        # 否则旧行为会因「同名同类的页已存在」而另建一个带哈希后缀的重复页。
        exact = next((p for p in pages if p["slug"] == element_page_slug(model, element)), None)
        if exact is not None:
            sim, candidate = 1.0, exact
        else:
            # 同类型守卫（设计路径开启）：只在同一本体类族（相等或同一继承链）的页之间做相似度合并，
            # 避免「设计节点并进报告页/需求页」这类跨类误合并（2026-09-21 沙箱实测事故）。
            pool = ([p for p in pages if same_type_family(element["type"], p.get("page_type"))]
                    if same_type_only else pages)
            sim, candidate = pick_match(element, pool)
        if candidate is not None and sim >= high:
            content, post = merge_content(candidate["content"], element, element["chunk_id"],
                                          element["chunk_index"], doc_meta)
            source_refs = union_list(candidate.get("source_refs"),
                                     [doc_meta["id"]] if doc_meta.get("id") else [])
            chunk_refs = union_list(candidate.get("chunk_refs"),
                                    [element["chunk_id"]] if element["chunk_id"] else [])
            metadata = candidate.get("page_metadata") or {}
            ont = metadata.setdefault("ontology", {})
            ont.setdefault("merge_history", []).append(
                {"at": now_text(), "doc": doc_meta["title"],
                 "chunk_index": element["chunk_index"], "similarity": sim, **post})
            ont["last_merge_at"] = now_text()
            upstream = [s for s in (element.get("upstream") or []) if s]
            if upstream:
                des = metadata.setdefault("design", {})
                des["upstream"] = sorted(set(list(des.get("upstream") or []) + upstream))
                des.setdefault("generator", TOOL_TAG)
            new_summary = (element.get("definition") or candidate.get("summary") or "")[:500]
            statements.append(sql_update_page(candidate, content, new_summary,
                                              source_refs, chunk_refs, metadata))
            summary["merged"].append({"name": element["name"], "type": element["type"],
                                      "into": candidate["slug"], "similarity": sim, **post})
            candidate.update({"content": content, "summary": new_summary,
                              "source_refs": source_refs, "chunk_refs": chunk_refs,
                              "page_metadata": metadata})
        elif candidate is None or sim <= low:
            page = build_new_page(engine or load_engine(), model=model, element=element,
                                  chunk_id=element["chunk_id"], chunk_index=element["chunk_index"],
                                  doc_meta=doc_meta)
            if any(p["slug"] == page["slug"] for p in pages):
                page["slug"] = "%s-%s" % (page["slug"],
                                          hashlib.sha1(element["type"].encode()).hexdigest()[:6])
                page["wiki_path"] = page["slug"]
            statements.append(sql_insert_page(page, kb_id, tenant))
            summary["created"].append({"name": element["name"], "type": element["type"],
                                       "slug": page["slug"]})
            pages.append({**page, "version": 1, "knowledge_base_id": kb_id,
                          "status": "published", "in_links": [], "title": page["title"],
                          "summary": page["summary"], "aliases": page["aliases"],
                          "source_refs": page["source_refs"], "chunk_refs": page["chunk_refs"]})
        else:
            page = build_pending_page(model, element, candidate, sim, element["chunk_id"],
                                      element["chunk_index"], doc_meta, high, low)
            statements.append(sql_insert_page(page, kb_id, tenant))
            summary["pending"].append({"name": element["name"], "type": element["type"],
                                       "pending_slug": page["slug"],
                                       "candidate": candidate["slug"], "similarity": sim})
            pages.append({**page, "version": 1, "knowledge_base_id": kb_id,
                          "status": "published", "in_links": [], "title": page["title"],
                          "summary": page["summary"], "aliases": page["aliases"],
                          "source_refs": page["source_refs"], "chunk_refs": page["chunk_refs"]})

    if not dry_run and statements:
        statements.append(sql_rebuild_in_links(kb_id))
        psql("BEGIN;\n" + "\n".join(statements) + "\nCOMMIT;\n", stdin=True)
        # 落库后**自动重建该知识库的目录树**（用户 2026-09-19 口径）：
        # 不重建的话前端树是平铺的（老问题）。目录 id 是 UUIDv5 确定性生成、逻辑幂等，
        # 所以每次落库后同步一遍是安全的；同步失败不影响本次结果（只记录状态）。
        try:
            import sync_folders  # 延迟导入：sync_folders 反过来 import server
            sync_folders.sync_kb(kb_id, dry_run=False, link_pages=True, prune=False)
            summary["folders_synced"] = True
        except Exception as exc:  # noqa: BLE001
            print("[mcp] 目录同步失败（不影响本次抽取）：%s" % exc)
            summary["folders_synced"] = "failed: %s" % exc
    return summary


def _graph_target_slug(name: str) -> str:
    """在**本库已落库的页面**里按标题找 slug（设计节点引用需求 wiki 页时用）。"""
    if not name:
        return ""
    rows = psql_csv("SELECT slug FROM wiki_pages WHERE deleted_at IS NULL AND title = %s LIMIT 1"
                    % sql_str(name))
    return rows[0]["slug"] if rows else ""


def _graph_target_type(name: str) -> str:
    rows = psql_csv("SELECT COALESCE(page_type,'') AS t FROM wiki_pages WHERE deleted_at IS NULL "
                    "AND title = %s LIMIT 1" % sql_str(name))
    return rows[0]["t"] if rows else ""


def design_elements(model_key: str, model: dict, nodes: list, edges: list, doc_meta: dict,
                    upstream: list | None = None) -> tuple[list, dict]:
    """把**概要设计报告**里的节点/关系转成「要素载荷」（与抽取路径同形），并做本体合规校验。

    - 节点：`{name, type, definition, description, source_text?, aliases?}` → 一页；
    - 关系：`{source, type, target, label?}` → 写进 source 页的 `## 本体关系`；
    - 校验：类必须在本体里（`ke_ontology.class_meta`），关系的 range 闭包必须包含目标页类型；
      不合规的进 `violations`（与抽取路径同一口径：违规不入库，回报给调用方）；
    - `upstream`：上游页 slug（需求页/报告页），写进 `page_metadata.design.upstream` 与正文 `## 溯源`。
    """
    meta = ke_ontology.class_meta()
    violations: list = []
    slug_by_name: dict = {}
    upstream = [s for s in (upstream or []) if s]
    for node in nodes:
        name = (node.get("name") or "").strip()
        cls = (node.get("type") or "").strip()
        if not name or not cls:
            violations.append({"kind": "node", "name": name, "reason": "缺少 name 或 type"})
            continue
        info = meta.get(cls)
        if not info:
            violations.append({"kind": "node", "name": name, "type": cls,
                               "reason": "本体里没有这个类（需先补本体或改用现有类）"})
            continue
        node["_module"] = info.get("module") or model_key
        node["_type_label"] = info.get("label") or cls
        slug_by_name[name] = element_slug(node["_module"],
                                         {"name": name, "type": cls})

    outgoing: dict = {}
    for edge in edges:
        rel_type = (edge.get("type") or "").strip()
        src = (edge.get("source") or "").strip()
        dst = (edge.get("target") or "").strip()
        closure = ke_ontology.target_closure(rel_type)
        if not closure:
            violations.append({"kind": "edge", "source": src, "type": rel_type, "target": dst,
                               "reason": "本体里没有这个对象属性"})
            continue
        dst_type = next((n.get("type") for n in nodes if (n.get("name") or "").strip() == dst), "")
        dst_slug = slug_by_name.get(dst, "")
        if not dst_slug:
            dst_slug, dst_type = _graph_target_slug(dst), (dst_type or _graph_target_type(dst))
        if not dst_slug:
            violations.append({"kind": "edge", "source": src, "type": rel_type, "target": dst,
                               "reason": "目标解析不到页面（既不在本次节点里，也不在本库里）"})
            continue
        if dst_type and dst_type not in closure:
            violations.append({"kind": "edge", "source": src, "type": rel_type, "target": dst,
                               "reason": "目标页类型 %s 不在 `%s` 的 range 内（%s）"
                                         % (dst_type, rel_type, "、".join(closure[:6]))})
            continue
        outgoing.setdefault(src, []).append({"type": rel_type, "label": edge.get("label", ""),
                                             "target": dst, "target_slug": dst_slug,
                                             "source_text": edge.get("source_text", "")})

    accepted = []
    payloads = []
    for node in nodes:
        name = (node.get("name") or "").strip()
        if name not in slug_by_name:
            continue
        rels = outgoing.get(name, [])
        accepted += [{"source": name, "type": r["type"], "target": r["target"]} for r in rels]
        payloads.append({
            "name": name, "type": node["type"], "type_label": node["_type_label"],
            "module": node["_module"], "slug": slug_by_name[name],
            "definition": node.get("definition") or "",
            "description": node.get("description") or "",
            "source_text": node.get("source_text") or "（概要设计，无原文片段）",
            "chunk_id": "", "chunk_index": -1, "relations": rels,
            "upstream": upstream, "aliases": node.get("aliases") or [],
        })
    return payloads, {"edges": accepted, "violations": violations, "unmatched": []}


def save_knowledge(kb_id: str, *, stage: str = "report", model: str = "ea",
                   report: dict | None = None, nodes: list | None = None, edges: list | None = None,
                   mode: str = "dry_run", confirmed_new_applications: list | None = None,
                   high: float = DEFAULT_HIGH, low: float = DEFAULT_LOW) -> dict:
    """**设计落库（不调 LLM）**：两段式，复用抽取路径的 `save_elements`（相似度合并/待确认/版本/目录）。

    - `stage="report"`：把**概要设计报告 md** 整篇写成 wiki 页（索引页/父页）。
      报告正文放在「定义」位置（所以正文完整保留），来源按方案 C 记到需求文档（`source_refs`），
      上游页写进正文 `## 溯源` 与 `page_metadata.design.upstream`。
    - `stage="graph"`：把报告**细分**出的节点/关系写成 wiki 页 + 本体关系（每个 IT 服务/应用系统一页）。
      节点页通过 `report.slug` 挂到报告页下（`parent_slug`），形成「报告页 → 细分页」的层级，
      与报告正文一一对应（图谱内容 = 设计 wiki 的细分）。
    - 合规：类必须在本体里、关系的 range 闭包必须包含目标页类型，违规进 `violations`（不入库）；
    - 确认：新建「应用/系统」类节点（`bmm-ea-ext:Application` / `ea:Application` / `ITAsset`）
      **必须**在 `confirmed_new_applications` 里列出，否则只在 dry_run 清单里回报；
    - 幂等：同标题（同 slug）重跑 = 合并更新，不重复建页。
    """
    kb_id, kb_note = resolve_kb_id(kb_id)
    engine = load_engine()
    model_obj = engine.pick_model(engine.load_index(engine.DEFAULT_INDEX), model)
    stage = (stage or "report").strip().lower()
    mode = (mode or "dry_run").strip().lower()
    if stage not in ("report", "graph"):
        raise ValueError("stage 只能是 report / graph")
    if mode not in ("dry_run", "apply"):
        raise ValueError("mode 只能是 dry_run / apply")
    tenant_id = get_kb_tenant(kb_id)
    confirmed = {str(x).strip() for x in (confirmed_new_applications or []) if str(x).strip()}

    doc_meta = {"id": "", "title": (report or {}).get("source_document_title") or "（无来源文档）"}
    if (report or {}).get("source_document_id"):
        doc_meta["id"] = resolve_knowledge_id(kb_id, str(report["source_document_id"]))[0]

    if stage == "report":
        title = ((report or {}).get("title") or "").strip()
        body = ((report or {}).get("content_md") or "").strip()
        if not title or not body:
            raise ValueError("stage=report 需要 report.title 与 report.content_md")
        element = {"name": title, "type": (report or {}).get("page_type") or "summary",
                   "type_label": "概要设计报告", "module": model,
                   "definition": body, "description": "",
                   "source_text": "概要设计报告（由设计智能体生成、人工确认后落库）",
                   "chunk_id": "", "chunk_index": -1, "relations": [],
                   "upstream": [s for s in ((report or {}).get("upstream") or []) if s],
                   "aliases": (report or {}).get("aliases") or []}
        checked = {"edges": [], "violations": [], "unmatched": []}
        summary = save_elements(kb_id, model_obj, checked, [element], doc_meta, high=high, low=low,
                                dry_run=(mode != "apply"), tenant_id=tenant_id,
                                resolved_note=kb_note, engine=engine, same_type_only=True)
        summary["stage"] = "report"
        return summary

    payloads, checked = design_elements(
        model, model_obj, nodes or [], edges or [], doc_meta,
        upstream=[s for s in ((report or {}).get("upstream") or []) if s])
    app_types = ("bmm-ea-ext:Application", "ea:Application", "bmm-ea-ext:ITAsset", "ea:ITAsset")
    allowed, blocked = [], []
    for payload in payloads:
        if payload["type"] in app_types and payload["name"] not in confirmed:
            blocked.append({"name": payload["name"], "type": payload["type"],
                            "reason": "新建「应用/系统」节点需人工确认",
                            "how": "确认后带 confirmed_new_applications=[\"%s\"] 重跑"
                                   % payload["name"]})
        else:
            allowed.append(payload)
    summary = save_elements(kb_id, model_obj, checked, allowed, doc_meta, high=high, low=low,
                            dry_run=(mode != "apply"), tenant_id=tenant_id,
                            resolved_note=kb_note, engine=engine, same_type_only=True)
    summary["stage"] = "graph"
    summary["pending_confirmation"] = blocked
    report_slug = ((report or {}).get("slug") or "").strip()
    if report_slug:
        summary["report_slug"] = report_slug
        if mode == "apply":
            for entry in summary["created"] + summary["merged"]:
                slug = entry.get("slug") or entry.get("into")
                if slug:
                    psql("UPDATE wiki_pages SET parent_slug = %s, updated_at = now() "
                         "WHERE knowledge_base_id = %s AND slug = %s AND deleted_at IS NULL;"
                         % (sql_str(report_slug), sql_str(kb_id), sql_str(slug)), stdin=True)
    return summary


def extract_and_save(model_key: str, kb_id: str, knowledge_id: str,
                     high: float = DEFAULT_HIGH, low: float = DEFAULT_LOW,
                     dry_run: bool = False, from_log: str = "") -> dict:
    engine = load_engine()
    # 参数容错（2026-09-19）：智能体常传知识库名称、或 d1 之类的占位符，
    # 这里统一解析成真实 UUID，并把解析说明回传给智能体（避免下次再传错）。
    kb_id, kb_note = resolve_kb_id(kb_id)
    knowledge_id, doc_note = resolve_knowledge_id(kb_id, knowledge_id)
    tenant_id = get_kb_tenant(kb_id)
    doc_rows = psql_csv("SELECT id, title FROM knowledges WHERE id = %s" % sql_str(knowledge_id))
    if not doc_rows:
        raise RuntimeError("文档不存在：%s" % knowledge_id)
    doc_title = doc_rows[0]["title"] or knowledge_id
    doc_meta = {"id": knowledge_id, "title": doc_title}

    chunks = fetch_chunks(knowledge_id)
    if not chunks:
        raise RuntimeError("文档没有可用片段：%s" % knowledge_id)
    body, pool = split_body_pool(chunks)
    doc_text = "\n\n---\n\n".join((c["content"] or "").strip() for c in body)

    result = run_extraction(engine, model_key, doc_title, doc_text, from_log=from_log)
    model, checked = result["model"], result["checked"]
    payloads = element_payloads(engine, checked, pool)
    # 落库半段统一走 save_elements（与「设计路径」同一份实现，见 save_knowledge）
    summary = save_elements(kb_id, model, checked, payloads, doc_meta,
                            high=high, low=low, dry_run=dry_run, tenant_id=tenant_id,
                            resolved_note=(kb_note + " " + doc_note), knowledge_id=knowledge_id,
                            extra={"chunks": len(chunks), "chars": len(doc_text)}, engine=engine)
    return summary


# ---------------------------------------------------------------------------
# 待确认裁决 + 查询工具
# ---------------------------------------------------------------------------
def list_pending_merges(kb_id: str) -> dict:
    # 参数容错：智能体常传知识库名称（如「企业知识」）或占位符，这里先解析成真实 UUID，
    # 否则按错误的 id 查询会返回空清单（2026-09-19 用户实测：报告 5 条待确认却查到 0 条）。
    kb_id, _note = resolve_kb_id(kb_id)
    rows = psql_csv(
        "SELECT slug, title, summary, page_metadata::text AS meta, created_at "
        "FROM wiki_pages WHERE knowledge_base_id = %s AND page_type = %s AND deleted_at IS NULL "
        "ORDER BY created_at" % (sql_str(kb_id), sql_str(TYPE_PENDING)))
    items = []
    for row in rows:
        try:
            meta = (json.loads(row["meta"] or "{}").get("ontology") or {})
        except json.JSONDecodeError:
            meta = {}
        items.append({"pending_slug": row["slug"], "title": row["title"],
                      "candidate_slug": meta.get("candidate_slug", ""),
                      "similarity": meta.get("similarity"), "summary": row["summary"]})
    return {"kb_id": kb_id, "count": len(items), "items": items}


def resolve_pending_merge(kb_id: str, pending_slug: str, action: str) -> dict:
    kb_id, _note = resolve_kb_id(kb_id)   # 同上：支持用知识库名称调用
    rows = psql_csv("SELECT id, tenant_id, page_metadata::text AS meta, source_refs::text AS refs, "
                    "chunk_refs::text AS chunks FROM wiki_pages WHERE knowledge_base_id = %s "
                    "AND slug = %s AND deleted_at IS NULL" % (sql_str(kb_id), sql_str(pending_slug)))
    if not rows:
        raise RuntimeError("待确认页不存在：%s" % pending_slug)
    meta = (json.loads(rows[0]["meta"] or "{}").get("ontology") or {})
    element = meta.get("element") or {}
    doc = meta.get("doc") or {}
    tenant_id = int(rows[0]["tenant_id"])
    doc_meta = {"id": doc.get("id", ""), "title": doc.get("title", "")}
    chunk_id, chunk_index = doc.get("chunk_id", ""), int(doc.get("chunk_index", -1))
    statements: list[str] = []
    result = {"pending_slug": pending_slug, "action": action}

    if action == "merge":
        target = meta.get("candidate_slug", "")
        page_rows = fetch_existing_pages(kb_id)
        target_page = next((p for p in page_rows if p["slug"] == target), None)
        if target_page is None:
            raise RuntimeError("候选页已不存在：%s" % target)
        content, post = merge_content(target_page["content"], element, chunk_id, chunk_index, doc_meta)
        metadata = target_page.get("page_metadata") or {}
        metadata.setdefault("ontology", {}).setdefault("merge_history", []).append(
            {"at": now_text(), "doc": doc_meta["title"], "manual": True,
             "pending_slug": pending_slug, **post})
        statements.append(sql_update_page(
            target_page, content, (element.get("definition") or target_page["summary"])[:500],
            union_list(target_page.get("source_refs"), [doc_meta["id"]] if doc_meta["id"] else []),
            union_list(target_page.get("chunk_refs"), [chunk_id] if chunk_id else []),
            metadata))
        result.update({"into": target, **post})
    elif action == "create":
        engine = load_engine()
        model = engine.pick_model(engine.load_index(engine.DEFAULT_INDEX), meta.get("model", "bmm"))
        page = build_new_page(engine, model, element, chunk_id, chunk_index, doc_meta)
        if any(p["slug"] == page["slug"] for p in fetch_existing_pages(kb_id)):
            page["slug"] = "%s-%s" % (page["slug"], hashlib.sha1(pending_slug.encode()).hexdigest()[:6])
            page["wiki_path"] = page["slug"]
        statements.append(sql_insert_page(page, kb_id, tenant_id))
        result.update({"created": page["slug"]})
    else:
        raise RuntimeError("action 只能是 merge 或 create")

    statements.append("DELETE FROM wiki_pages WHERE knowledge_base_id = %s AND slug = %s;"
                      % (sql_str(kb_id), sql_str(pending_slug)))
    statements.append(sql_rebuild_in_links(kb_id))
    psql("BEGIN;\n" + "\n".join(statements) + "\nCOMMIT;\n", stdin=True)
    return result


def ontology_types(model_key: str) -> dict:
    """某本体模型的类与关系清单（供智能体选类型/关系）。

    2026-09-21：**以 Neo4j 投影为真源补录**（`ke_ontology.class_meta()` 已含上传导入模块），
    否则上传的新模块（如 `bmm-ea-ext`）在旧产物里看不到 —— 表现就是智能体"看不见新类型/新关系"
    （对应巡检 B5：本体投影 ↔ 编译产物不一致）。
    """
    index = json.loads(INDEX_PATH.read_text(encoding="utf-8"))
    for model in index["models"]:
        if model["key"] == model_key:
            out = {
                "model": model["key"], "label": model["label"],
                "expert_role": model.get("expert_role", ""),
                "classes": [{"name": c["name"], "label": c.get("label"),
                             "definition": c.get("definition")} for c in model["classes"]],
                "relations": [{"name": r["name"], "label": r.get("label"),
                               "domain": r.get("domain"), "range": r.get("range")}
                              for r in list(model["relations"])
                              + list(model.get("cross_module_bridges") or [])],
                "source": "artifacts",
            }
            try:
                proj_classes = [r["name"] for r in ke_neo4j.query(
                    "MATCH (c:BodhiOntClass) WHERE c.bodhi_projection = 'ontology' "
                    "AND coalesce(c.external,false) = false AND c.prefixed IS NOT NULL "
                    "RETURN DISTINCT c.prefixed AS name ORDER BY name") if r.get("name")]
                proj_props = [{"name": r["name"], "label": r.get("label") or "",
                               "domain": r.get("domain"), "range": r.get("range")}
                              for r in ke_neo4j.query(
                    "MATCH (p:BodhiOntProperty {property_kind:'object'}) "
                    "WHERE p.bodhi_projection = 'ontology' AND p.prefixed IS NOT NULL "
                    "OPTIONAL MATCH (p)-[:BODHI_DOMAIN]->(d:BodhiOntClass) "
                    "OPTIONAL MATCH (p)-[:BODHI_RANGE]->(r:BodhiOntClass) "
                    "RETURN DISTINCT p.prefixed AS name, p.label AS label, "
                    "       collect(DISTINCT d.prefixed) AS domain, collect(DISTINCT r.prefixed) AS range "
                    "ORDER BY name")]
            except Exception:  # noqa: BLE001  投影不可用就用产物（保持旧行为）
                proj_classes, proj_props = [], []
            have_cls = {c["name"] for c in out["classes"]}
            for name in proj_classes:
                if name not in have_cls:
                    meta = ke_ontology.class_meta().get(name) or {}
                    out["classes"].append({"name": name, "label": meta.get("label") or name,
                                           "definition": "（来自运行投影：上传导入的模块）"})
            have_rel = {r["name"] for r in out["relations"]}
            for prop in proj_props:
                if prop["name"] not in have_rel:
                    out["relations"].append({**prop, "note": "（来自运行投影）"})
            if len(out["classes"]) != len(model["classes"]) or \
                    len(out["relations"]) != len(model["relations"]):
                out["source"] = "artifacts+projection"
                out["note"] = ("已用 Neo4j 投影补录（产物可能过期，见巡检 B5）："
                               "重编 `tools/ontology-compiler/compile.py compile`、"
                               "重载 `deploy/bootstrap-neo4j.sh`")
            return out
    raise RuntimeError("未知本体模型：%s" % model_key)


# ---------------------------------------------------------------------------
# MCP：工具定义与调用
# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# 异步任务层（2026-09-19 实测的必要性）
#   app 侧 MCP 客户端的超时是**硬编码 60 秒且无配置项**（app env / 上游 compose 里都没有
#   MCP 超时开关），而单次抽取的 LLM 调用就要 ~57s → 同步调用必然 context deadline exceeded。
#   因此工具改为「立即受理 + 后台执行」，用 extract_status 轮询结果。
# ---------------------------------------------------------------------------
JOBS: dict[str, dict] = {}
JOBS_LOCK = threading.Lock()
JOB_KEEP = {"status", "started_at", "finished_at", "doc_title", "created", "merged",
            "pending", "violations", "unmatched", "folders_synced", "elements",
            "relationships", "error"}


DETAIL_KEYS = ("created", "merged", "pending", "violations", "unmatched")


def _job_brief(result: dict) -> dict:
    """给智能体看的任务简报：**既要计数也要明细**。

    2026-09-19 用户实测：旧实现把列表一律转成 len()，于是智能体只看到
    `unmatched=7` / `pending=5` 却拿不到名称与理由，无法汇报也无法人工跟进。
    现在同时给出 `*_count` 与 `*`（明细，最多 20 条，控制体积）。
    """
    out: dict = {}
    for k in ("status", "started_at", "finished_at", "doc_title", "elements",
              "relationships", "folders_synced", "error", "resolved_note"):
        if k in result:
            out[k] = result[k]
    for k in DETAIL_KEYS:
        items = result.get(k)
        if isinstance(items, list):
            out[k + "_count"] = len(items)
            out[k] = items[:20]
    return out


def job_key(args: dict) -> str:
    """任务去重键：同一（知识库, 文档, 模型）视为同一次抽取。"""
    return "|".join([str(args.get("kb_id", "")).strip(),
                     str(args.get("knowledge_id", "")).strip(),
                     str(args.get("model", "")).strip()])


def find_existing_job(args: dict) -> tuple[str, dict] | None:
    """按去重键找已有的任务：在跑的、或最近的（无论成败）都复用，避免重复抽取。

    2026-09-19 用户实测：智能体每"查询进度"都会再调一次 extract_and_save，
    旧实现每次都新建 job → 同一篇文档被反复抽取。现在同组只保留一个任务，
    重复调用直接返回原 job_id；要强制重跑请换 knowledge_id 或显式传 fresh=true。
    """
    if args.get("fresh"):
        return None
    key = job_key(args)
    with JOBS_LOCK:
        for job_id in reversed(list(JOBS)):
            job = JOBS[job_id]
            if job.get("key") == key:
                return job_id, job
    return None


def start_extract_job(args: dict) -> dict:
    """受理一次抽取并立即返回；同名同文档的任务会复用（幂等）。"""
    existing = find_existing_job(args)
    if existing:
        job_id, job = existing
        return {"status": job.get("status", "running"), "job_id": job_id,
                "reused": True, "started_at": job.get("started_at"),
                "note": ("同一篇文档已有抽取任务（未重复发起）。请用 "
                         "extract_status(job_id=...) 查询该任务；确需重跑请传 fresh=true。"),
                **_job_brief(job)}

    key = job_key(args)
    job_id = uuid.uuid4().hex[:12]
    with JOBS_LOCK:
        JOBS[job_id] = {"status": "running", "started_at": now_text(), "key": key}
        if len(JOBS) > 30:  # 只保留最近若干条，避免长跑进程内存膨胀
            for stale in list(JOBS)[:-30]:
                if JOBS[stale].get("status") != "running":
                    JOBS.pop(stale, None)

    def _run() -> None:
        try:
            result = extract_and_save(
                str(args["model"]), str(args["kb_id"]), str(args["knowledge_id"]),
                high=float(args.get("high", DEFAULT_HIGH)),
                low=float(args.get("low", DEFAULT_LOW)),
                dry_run=bool(args.get("dry_run", False)),
                from_log=str(args.get("from_log", "")))
            with JOBS_LOCK:
                JOBS[job_id].update({"status": "done", "finished_at": now_text(),
                                     "doc_title": result.get("doc_title", "")})
                JOBS[job_id].update(result)
        except Exception as exc:  # noqa: BLE001
            print("[mcp] 异步抽取失败 job=%s：%s" % (job_id, exc))
            with JOBS_LOCK:
                JOBS[job_id].update({"status": "failed", "finished_at": now_text(),
                                     "error": str(exc)})

    threading.Thread(target=_run, daemon=True).start()
    return {
        "status": "started", "job_id": job_id, "started_at": now_text(),
        "note": ("抽取已受理并开始执行（约 1-2 分钟）。请立即用 extract_status(job_id=...) "
                 "查询进度与结果；**不要**再次调用 extract_and_save 以免重复抽取。"),
    }


def extract_status(job_id: str = "") -> dict:
    """查询异步抽取的状态/结果；不给 job_id 则列出最近的任务。"""
    with JOBS_LOCK:
        if job_id:
            job = JOBS.get(job_id)
            if not job:
                return {"job_id": job_id, "status": "unknown",
                        "note": "没有这个任务（可能服务重启过；重启会清空内存中的任务表）"}
            return {"job_id": job_id, "args": None, **_job_brief(job)}
        return {"count": len(JOBS),
                "jobs": {k: {"status": v.get("status"), "started_at": v.get("started_at"),
                             "finished_at": v.get("finished_at")}
                         for k, v in list(JOBS.items())}}


def tool_definitions() -> list[dict]:
    return [
        {
            "name": "extract_and_save",
            "description": ("按本体模型对**一篇文档**做一次抽取，并把结果写入 wiki："
                            "自动做类型/关系合规校验（不合规不入库），"
                            "再按向量相似度与存量比对 —— 高相似直接合并（追加原文证据、"
                            "定义取更完整者、版本+1、可回退），低相似新增页面，中间区间生成"
                            "「待确认合并」页由人工裁决。**只需调用一次**，不要自己重复读源文或"
                            "自己判断与存量是否重复。"),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "model": {"type": "string", "description": "本体模型 key：bmm / ea"},
                    "kb_id": {"type": "string", "description": "目标知识库 UUID"},
                    "knowledge_id": {"type": "string", "description": "源文档（knowledge）UUID"},
                    "high": {"type": "number", "description": "合并阈值，默认 0.90"},
                    "low": {"type": "number", "description": "新增阈值，默认 0.75"},
                    "dry_run": {"type": "boolean", "description": "只算不写，默认 false"},
                    "wait": {"type": "boolean",
                             "description": ("默认 false = 立即受理并后台执行（推荐，避免调用超时）；"
                                             "true = 同步等待结果（可能超过 60 秒，仅在本地调试时用）")},
                    "from_log": {"type": "string",
                                 "description": "调试用：从指定日志文件复放原始 LLM 输出，不发起真实调用"},
                },
                "required": ["model", "kb_id", "knowledge_id"],
            },
        },
        {
            "name": "extract_status",
            "description": ("查询本体抽取任务的状态与结果（配合 extract_and_save 的异步模式使用）。"
                            "返回 status=running/done/failed，done 时含 created/merged/pending/"
                            "violations/unmatched 明细与 folders_synced（目录是否已重建）。"),
            "inputSchema": {"type": "object",
                            "properties": {"job_id": {"type": "string",
                                                      "description": "extract_and_save 返回的 job_id"}}},
        },
        {
            "name": "list_pending_merges",
            "description": "列出「待确认合并」项（相似度处于两个阈值之间，需人工裁决）。",
            "inputSchema": {"type": "object",
                            "properties": {"kb_id": {"type": "string"}},
                            "required": ["kb_id"]},
        },
        {
            "name": "resolve_pending_merge",
            "description": "对一条「待确认合并」做裁决：action=merge 合并到候选页，action=create 作为新页新增。",
            "inputSchema": {
                "type": "object",
                "properties": {"kb_id": {"type": "string"},
                               "pending_slug": {"type": "string"},
                               "action": {"type": "string", "enum": ["merge", "create"]}},
                "required": ["kb_id", "pending_slug", "action"],
            },
        },
        {
            "name": "ontology_types",
            "description": "返回某本体模型的可用类与关系（含 domain/range），供核对类型是否合法。",
            "inputSchema": {"type": "object", "properties": {"model": {"type": "string"}},
                            "required": ["model"]},
        },
        {
            "name": "audit_scan",
            "description": ("知识运维**只读体检**：比对 wiki ↔ 本体图谱 ↔ 本体模型，并找出异常数据。"
                            "检查项：悬空出边(A1)、in_links 不一致(A2)、类型/元数据矛盾(A3/A6)、"
                            "重复或自环关系行(A5)、类型不在模型(B1)、关系不在模型(B2)、"
                            "range 违反(B3)、模型库页与投影不一致(B4)、"
                            "**无来源文档的实例页(C1)**、**来源文档已删/不存在(C2)**、"
                            "同语义多页(D1)、软删残留(D2)、孤儿快照(D3)。"
                            "返回 summary/totals/findings（每条含 severity、subject、detail、fix_hint）。"
                            "**本工具不写任何数据**；清理需另行确认（P2）。"),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "kb_id": {"type": "string", "description": "知识库 UUID"},
                    "scope": {"type": "string", "enum": list(ke_audit.SCOPES),
                              "description": "all(默认) / wiki(A) / model(B) / source(C) / dupes(D)"},
                    "max_findings": {"type": "integer",
                                     "description": "最多返回多少条明细，默认 50（计数始终完整）"},
                },
                "required": ["kb_id"],
            },
        },
        {
            "name": "audit_plan",
            "description": ("生成**只读**的清理/修复计划（**不写任何数据**）：列出要删/要改的清单与理由，"
                            "并给出 plan_id 与执行命令。kinds：`init`（初始化＝清空该 KB 的 wiki 与图谱）、"
                            "`all`（清理异常+修一致性问题：悬空关系行、in_links、重复关系行、无来源页、"
                            "来源已删页、软删残留、孤儿快照）、或具体 kind。"
                            "**执行必须由人工确认后走 CLI/HTTP（apply --confirm）** —— 本工具绝不执行。"),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "kb_id": {"type": "string", "description": "知识库 UUID"},
                    "kinds": {"type": "string",
                              "description": "init / all / 逗号分隔的具体 kind（默认 all）"},
                    "scope": {"type": "string", "enum": list(ke_audit.SCOPES),
                              "description": "顺带带出的现状快照范围，默认 all"},
                },
                "required": ["kb_id"],
            },
        },
        {
            "name": "save_knowledge",
            "description": ("**设计落库（不调用大模型）**：把概要设计内容写成 wiki 页与本体图谱。两段式："
                            "`stage=report` 先落**概要设计报告 md**（整篇写成索引页/父页）；"
                            "`stage=graph` 再把报告**细分**出的节点/关系落成各自 wiki 页 + 本体关系"
                            "（每个 IT 服务/应用系统一页，挂在报告页下）。"
                            "落库与抽取路径**共用同一份实现**：相似度两阈值（高→合并现有页、低→新增页、"
                            "中间→生成「待确认合并」页交人工裁决）、版本快照、溯源、目录重建都一致。"
                            "合规校验：类必须在本体里、关系必须满足 domain/range，违规进 violations 不入库。"
                            "**新建「应用/系统」节点必须人工确认**：默认 dry_run 只给清单，"
                            "确认后 mode=apply 且带 confirmed_new_applications 重跑（幂等，同标题只更新）。"),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "kb_id": {"type": "string", "description": "目标知识库 UUID 或名称"},
                    "stage": {"type": "string", "enum": ["report", "graph"],
                              "description": "report=先落报告页（返回 slug）；graph=再落细分节点与关系"},
                    "model": {"type": "string", "description": "本体模型 key，默认 ea"},
                    "report": {
                        "type": "object",
                        "description": ("报告页与溯源信息：{title, content_md(报告全文), slug(可选，"
                                        "graph 段要用它挂父页), upstream(上游页 slug 数组), "
                                        "source_document_id(需求文档 id，方案 C 溯源), "
                                        "source_document_title, page_type(默认 summary)}"),
                        "properties": {"title": {"type": "string"}, "content_md": {"type": "string"},
                                       "slug": {"type": "string"},
                                       "upstream": {"type": "array", "items": {"type": "string"}},
                                       "source_document_id": {"type": "string"},
                                       "source_document_title": {"type": "string"}},
                    },
                    "nodes": {
                        "type": "array",
                        "description": ("细分节点：[{name, type(本体类，如 bmm-ea-ext:Service / "
                                        "bmm-ea-ext:Application), definition, description, aliases?}]"),
                        "items": {"type": "object"},
                    },
                    "edges": {
                        "type": "array",
                        "description": ("关系：[{source, type(本体对象属性，如 bmm-ea-ext:applicationProvidesService / "
                                        "bmm-ea-ext:stepUsesService), target, label?}]；"
                                        "target 可以是本次节点名，也可以是库内已有页标题（会解析成 slug）"),
                        "items": {"type": "object"},
                    },
                    "mode": {"type": "string", "enum": ["dry_run", "apply"],
                             "description": "dry_run（默认，只算不写）/ apply（真正写入）"},
                    "confirmed_new_applications": {
                        "type": "array", "items": {"type": "string"},
                        "description": "**人工已确认**可新建的「应用/系统」节点名称清单",
                    },
                },
                "required": ["kb_id", "stage"],
            },
        },
    ]


def call_tool(name: str, args: dict) -> dict:
    if name == "extract_and_save":
        # 默认异步受理（app 侧 MCP 60s 硬超时，而抽取要 1-2 分钟）；
        # wait=true 才同步等待（本地调试用）。
        if not args.get("wait") and not args.get("from_log"):
            return start_extract_job(args)
        return extract_and_save(
            str(args["model"]), str(args["kb_id"]), str(args["knowledge_id"]),
            high=float(args.get("high", DEFAULT_HIGH)),
            low=float(args.get("low", DEFAULT_LOW)),
            dry_run=bool(args.get("dry_run", False)),
            from_log=str(args.get("from_log", "")))
    if name == "extract_status":
        return extract_status(str(args.get("job_id", "")))
    if name == "list_pending_merges":
        return list_pending_merges(str(args["kb_id"]))
    if name == "resolve_pending_merge":
        return resolve_pending_merge(str(args["kb_id"]), str(args["pending_slug"]),
                                     str(args["action"]))
    if name == "ontology_types":
        return ontology_types(str(args["model"]))
    if name == "audit_scan":
        return ke_audit.audit(str(args["kb_id"]), str(args.get("scope", "all")),
                              int(args.get("max_findings", 50)))
    if name == "audit_plan":
        return ke_audit.build_plan(str(args["kb_id"]), args.get("kinds", "all"),
                                   str(args.get("scope", "all")))
    if name == "save_knowledge":
        return save_knowledge(
            str(args["kb_id"]), stage=str(args.get("stage", "report")),
            model=str(args.get("model", "ea")), report=args.get("report"),
            nodes=args.get("nodes") or [], edges=args.get("edges") or [],
            mode=str(args.get("mode", "dry_run")),
            confirmed_new_applications=args.get("confirmed_new_applications") or [])
    raise RuntimeError("未知工具：%s" % name)


def tool_result(payload: dict, is_error: bool = False) -> dict:
    return {
        "content": [{"type": "text", "text": json.dumps(payload, ensure_ascii=False, indent=2)}],
        "isError": is_error,
    }


# ---------------------------------------------------------------------------
# MCP：Streamable HTTP 传输（JSON-RPC 2.0）
# ---------------------------------------------------------------------------
PROTOCOL_VERSION = "2025-03-26"
SERVER_NAME = "bodhi-ontology-mcp"
SERVER_VERSION = "0.1.0"


class MCPHandler(BaseHTTPRequestHandler):
    server_version = "%s/%s" % (SERVER_NAME, SERVER_VERSION)
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):  # 静音默认访问日志
        print("[mcp] %s - %s" % (self.address_string(), fmt % args))

    def _send(self, code: int, body: bytes, ctype: str, extra: dict | None = None) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("MCP-Protocol-Version", PROTOCOL_VERSION)
        for key, value in (extra or {}).items():
            self.send_header(key, value)
        self.end_headers()
        if body:
            self.wfile.write(body)

    def _json(self, payload: dict, code: int = 200, extra: dict | None = None) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self._send(code, body, "application/json", extra)

    def do_POST(self) -> None:  # noqa: N802
        # bodhi2 前端用的 JSON 接口（/bodhi/*）与 MCP 传输（/mcp）共用同一端口。
        parsed = urlparse.urlsplit(self.path)
        path = parsed.path.rstrip("/") or "/"
        if path.startswith("/bodhi/"):
            self._bodhi_post(path)
            return
        if path not in ("", "/mcp"):
            self._json({"jsonrpc": "2.0", "id": None,
                        "error": {"code": -32600, "message": "未知端点：%s" % self.path}}, 404)
            return
        length = int(self.headers.get("Content-Length") or 0)
        try:
            request = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
        except json.JSONDecodeError as exc:
            self._json({"jsonrpc": "2.0", "id": None,
                        "error": {"code": -32700, "message": "JSON 解析失败：%s" % exc}}, 400)
            return

        method = request.get("method", "")
        params = request.get("params") or {}
        rid = request.get("id")
        extra = {"Mcp-Session-Id": hashlib.sha1(str(uuid.uuid4()).encode()).hexdigest()[:24]}

        if method.startswith("notifications/"):
            self._send(202, b"", "application/json", extra)
            return

        if method == "initialize":
            result = {
                "protocolVersion": params.get("protocolVersion") or PROTOCOL_VERSION,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
                "instructions": ("本体知识保存工具：extract_and_save 一次调用即完成"
                                 "「读片段 → 抽取 → 合规校验 → 与存量向量比对 → 合并/新增/待确认」。"),
            }
        elif method == "ping":
            result = {}
        elif method == "tools/list":
            result = {"tools": tool_definitions()}
        elif method == "tools/call":
            name = params.get("name", "")
            arguments = params.get("arguments") or {}
            print("[mcp] tools/call %s %s" % (name, json.dumps(arguments, ensure_ascii=False)))
            try:
                result = tool_result(call_tool(name, arguments))
            except Exception as exc:  # noqa: BLE001
                print("[mcp] 工具失败：%s" % exc)
                result = tool_result({"error": str(exc)}, is_error=True)
        else:
            self._json({"jsonrpc": "2.0", "id": rid,
                        "error": {"code": -32601, "message": "未实现的方法：%s" % method}}, 200, extra)
            return

        payload = {"jsonrpc": "2.0", "id": rid, "result": result}
        if "text/event-stream" in (self.headers.get("Accept") or ""):
            body = ("event: message\ndata: %s\n\n"
                    % json.dumps(payload, ensure_ascii=False)).encode("utf-8")
            self._send(200, body, "text/event-stream", extra)
        else:
            self._json(payload, 200, extra)

    # ------------------------------------------------------------------
    # bodhi2 前端 JSON 接口（/bodhi/*）：与 MCP 传输共端口，见 do_POST 分支
    # ------------------------------------------------------------------
    CORS = {"Access-Control-Allow-Origin": "*"}

    def _read_json(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        try:
            return json.loads(self.rfile.read(length).decode("utf-8") or "{}")
        except json.JSONDecodeError as exc:
            raise ValueError("JSON 解析失败：%s" % exc) from exc

    def _bodhi_post(self, path: str) -> None:
        try:
            body = self._read_json()
        except ValueError as exc:
            self._json({"error": str(exc)}, 400, self.CORS)
            return
        handlers = {
            "/bodhi/relations/add":
                lambda b: ke_pages.add_relation(b.get("kb_id", ""), b.get("slug", ""),
                                                b.get("rel_type", ""), b.get("target_slug", ""),
                                                b.get("label", "")),
            "/bodhi/relations/update":
                lambda b: ke_pages.update_relation(b.get("kb_id", ""), b.get("slug", ""),
                                                   b.get("target_slug", ""), b.get("new_rel_type", ""),
                                                   b.get("new_target_slug", ""), b.get("label", "")),
            "/bodhi/relations/delete":
                lambda b: ke_pages.delete_relation(b.get("kb_id", ""), b.get("slug", ""),
                                                   b.get("target_slug", ""), b.get("rel_type", "")),
            "/bodhi/page/type":
                lambda b: ke_pages.set_page_type(b.get("kb_id", ""), b.get("slug", ""),
                                                 b.get("page_type", "")),
            "/bodhi/delete":
                lambda b: ke_pages.delete_pages(b.get("kb_id", ""), b.get("slugs") or [],
                                                bool(b.get("dry_run"))),
            "/bodhi/kb/wiki-flag":
                lambda b: ke_pages.set_wiki_enabled(b.get("kb_id", ""), bool(b.get("enabled"))),
            # 本体模型维护（用户 2026-09-20）：编译 → 清理旧模型 → 灌投影 → 重投影 wiki
            "/bodhi/ontology/load":
                lambda b: ke_admin.load_model(b.get("model_id", ""), b.get("kb_id", ""),
                                              bool(b.get("purge", True)),
                                              bool(b.get("compile", True)),
                                              bool(b.get("project_wiki", True))),
            "/bodhi/ontology/purge":
                lambda b: ke_admin.purge_model(b.get("model_id", ""), b.get("kb_id", "")),
            "/bodhi/ontology/apply":
                lambda b: ke_admin.apply_projection(),
            "/bodhi/ontology/wiki":
                lambda b: ke_admin.regen_wiki(b.get("kb_id", "")),
            # 上传即加载（用户 2026-09-20）：前端读文件文本 → JSON 传过来（免 multipart），
            # 落 ontology/uploads/ → 级联删下游 → 解析入库（零产物）。依赖未就绪会抛 ValueError → 400。
            "/bodhi/ontology/upload":
                lambda b: ke_admin.upload_ttl(b.get("filename", ""), b.get("content", ""),
                                              b.get("module_id", ""),
                                              bool(b.get("project_wiki", False)),
                                              b.get("kb_id", "")),
            # 按来源文档清理本体实例（用户 2026-09-20 第二问：删文档不会联动清实例层）
            #   删「独占页」+ 多源页摘引用；默认 dry-run（apply=false 只出计划）
            "/bodhi/docs/purge":
                lambda b: ke_docs.purge_document(b.get("kb_id", ""), b.get("knowledge_id", ""),
                                                 b.get("title", ""), bool(b.get("apply", False)),
                                                 bool(b.get("sync_folders", True)),
                                                 bool(b.get("delete_exclusive", True))),
            # 巡检清理：plan 只读出计划；apply 必须带 confirm=true（人工确认后才执行，硬删）
            "/bodhi/audit/plan":
                lambda b: ke_audit.build_plan(b.get("kb_id", ""), b.get("kinds", "all"),
                                              b.get("scope", "all"),
                                              int(b.get("page_limit", 5000) or 5000)),
            "/bodhi/audit/apply":
                lambda b: ke_audit.apply_plan(b.get("kb_id", ""), b.get("plan_id", ""),
                                              bool(b.get("confirm", False)),
                                              int(b.get("page_limit", 5000) or 5000)),
            # 巡检兜底：把所有「来源文档已删/不存在」的残留一次清掉（默认 dry-run）
            "/bodhi/docs/sweep":
                lambda b: ke_docs.sweep(b.get("kb_id", ""), bool(b.get("apply", False)),
                                        bool(b.get("include_missing", True)),
                                        bool(b.get("delete_exclusive", True))),
        }
        fn = handlers.get(path)
        if not fn:
            self._json({"error": "未知端点：%s" % path}, 404, self.CORS)
            return
        try:
            print("[mcp] %s %s" % (path, json.dumps(body, ensure_ascii=False)[:200]))
            self._json(fn(body), 200, self.CORS)
        except ValueError as exc:      # 校验类错误 → 400（前端直接展示原文）
            self._json({"error": str(exc)}, 400, self.CORS)
        except Exception as exc:  # noqa: BLE001
            print("[mcp] %s 失败：%s" % (path, exc))
            self._json({"error": str(exc)}, 500, self.CORS)

    def do_OPTIONS(self) -> None:  # noqa: N802
        self._send(204, b"", "text/plain",
                   {"Access-Control-Allow-Origin": "*",
                    "Access-Control-Allow-Methods": "GET,POST,OPTIONS",
                    "Access-Control-Allow-Headers": "Content-Type"})

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse.urlparse(self.path)
        path = parsed.path.rstrip("/")
        if path in ("/bodhi/ontology/classes", "/bodhi/ontology/classes.json"):
            try:
                self._json(ke_ontology.classes(), 200, self.CORS)
            except Exception as exc:  # noqa: BLE001
                print("[mcp] /bodhi/ontology/classes 失败：%s" % exc)
                self._json({"error": str(exc)}, 400, self.CORS)
            return
        if path.startswith("/bodhi/ontology/relation-types"):
            params = dict(urlparse.parse_qsl(parsed.query))
            try:
                self._json(ke_ontology.relation_types_for(params.get("page_type", "")), 200, self.CORS)
            except Exception as exc:  # noqa: BLE001
                print("[mcp] /bodhi/ontology/relation-types 失败：%s" % exc)
                self._json({"error": str(exc)}, 400, self.CORS)
            return
        if path.startswith("/bodhi/ontology/targets"):
            params = dict(urlparse.parse_qsl(parsed.query))
            try:
                data = ke_ontology.target_pages(params.get("kb_id", ""), params.get("rel_type", ""),
                                                params.get("slug", ""), params.get("q", ""),
                                                int(params.get("limit", 200) or 200))
                self._json(data, 200, self.CORS)
            except Exception as exc:  # noqa: BLE001
                print("[mcp] /bodhi/ontology/targets 失败：%s" % exc)
                self._json({"error": str(exc)}, 400, self.CORS)
            return
        if path in ("/bodhi/relations", "/bodhi/relations.json"):
            params = dict(urlparse.parse_qsl(parsed.query))
            try:
                data = ke_pages.page_relations(params.get("kb_id", ""), params.get("slug", ""))
                self._json(data, 200, self.CORS)
            except Exception as exc:  # noqa: BLE001
                print("[mcp] /bodhi/relations 失败：%s" % exc)
                self._json({"error": str(exc)}, 400, self.CORS)
            return
        if path in ("/bodhi/model-graph", "/bodhi/model-graph.json"):
            params = dict(urlparse.parse_qsl(parsed.query))
            try:
                self._json(bodhi_model_graph(params.get("model", ""), params.get("kb_id", "")),
                           200, self.CORS)
            except Exception as exc:  # noqa: BLE001
                print("[mcp] /bodhi/model-graph 失败：%s" % exc)
                self._json({"error": str(exc)}, 400, self.CORS)
            return
        if path == "/bodhi/ontology/deps":
            # 只读：上传前"会连带删除哪些下游模块"（给前端确认框 + 运维自查用）
            params = dict(urlparse.parse_qsl(parsed.query))
            model = params.get("model_id", "")
            try:
                victims = ke_admin.dependent_modules(model)
                self._json({"model_id": model, "dependent_modules": victims,
                            "purge_order": [*victims, model]}, 200, self.CORS)
            except Exception as exc:  # noqa: BLE001
                print("[mcp] %s 失败：%s" % (path, exc))
                self._json({"error": str(exc)}, 500, self.CORS)
            return
        if path in ("/bodhi/audit", "/bodhi/audit.json"):
            # 一致性巡检（只读）：?kb_id=&scope=all|wiki|model|source|dupes&max_findings=50
            params = dict(urlparse.parse_qsl(parsed.query))
            try:
                data = ke_audit.audit(params.get("kb_id", ""), params.get("scope", "all"),
                                      int(params.get("max_findings", 50) or 50),
                                      int(params.get("page_limit", 5000) or 5000))
                self._json(data, 200, self.CORS)
            except Exception as exc:  # noqa: BLE001
                print("[mcp] %s 失败：%s" % (path, exc))
                self._json({"error": str(exc)}, 400, self.CORS)
            return
        if path in ("/bodhi/docs", "/bodhi/docs.json"):
            # 按来源文档统计本体实例页（只读）；`?orphans=1` 只回「来源文档已删/不存在」的残留
            params = dict(urlparse.parse_qsl(parsed.query))
            kb_id = params.get("kb_id", "")
            try:
                data = ke_docs.orphans(kb_id) if params.get("orphans") else ke_docs.doc_index(kb_id)
                self._json(data, 200, self.CORS)
            except Exception as exc:  # noqa: BLE001
                print("[mcp] %s 失败：%s" % (path, exc))
                self._json({"error": str(exc)}, 400, self.CORS)
            return
        if path in ("/bodhi/docs/pages", "/bodhi/docs/pages.json"):
            # 某来源文档的实例页清单（只读）
            params = dict(urlparse.parse_qsl(parsed.query))
            try:
                pages = ke_docs.pages_of_doc(params.get("kb_id", ""), params.get("knowledge_id", ""))
                self._json({"kb_id": params.get("kb_id", ""),
                            "knowledge_id": params.get("knowledge_id", ""),
                            "pages": pages}, 200, self.CORS)
            except Exception as exc:  # noqa: BLE001
                print("[mcp] %s 失败：%s" % (path, exc))
                self._json({"error": str(exc)}, 400, self.CORS)
            return
        if path in ("/bodhi/graph", "/bodhi/graph.json"):
            params = dict(urlparse.parse_qsl(parsed.query))
            try:
                data = bodhi_graph(params.get("kb_id", ""), params.get("model", ""),
                                   params.get("types", ""), int(params.get("limit", 300) or 300))
                self._json(data, 200, {"Access-Control-Allow-Origin": "*"})
            except Exception as exc:  # noqa: BLE001
                print("[mcp] /bodhi/graph 失败：%s" % exc)
                self._json({"error": str(exc)}, 500, {"Access-Control-Allow-Origin": "*"})
            return
        if path in ("/bodhi/page", "/bodhi/page.json"):
            params = dict(urlparse.parse_qsl(parsed.query))
            try:
                data = bodhi_page(params.get("kb_id", ""), params.get("slug", ""))
                # 右栏要的是「渲染后的正文」而不是 md 源码（此前直接 <pre> 了原文）。
                # 渲染在服务端做：零依赖（mdview.py），前端与镜像都不用动。
                if isinstance(data, dict) and data.get("content"):
                    try:
                        data["content_html"] = render_md(data["content"])
                    except Exception as exc:  # noqa: BLE001
                        print("[mcp] markdown 渲染失败，退回纯文本：%s" % exc)
                        data["content_html"] = ""
                self._json(data, 200, {"Access-Control-Allow-Origin": "*"})
            except Exception as exc:  # noqa: BLE001
                self._json({"error": str(exc)}, 404, {"Access-Control-Allow-Origin": "*"})
            return
        if path in ("/graph", "/graph.html", "/bodhi/view"):
            params = dict(urlparse.parse_qsl(parsed.query))
            view = params.get("view", "")
            if not view:
                # 自动判定：**本体模型库**（只有这 5 种统一页类型）默认看「模型结构图」；
                # ⚠️ 不能只判 `ontology:%`：普通知识库里也有 `ontology:PendingMerge`（待确认合并）页。
                try:
                    rows = psql_csv(
                        "SELECT count(*) AS n FROM wiki_pages WHERE knowledge_base_id = %s "
                        "AND deleted_at IS NULL AND page_type IN "
                        "('ontology:Module','ontology:Class','ontology:Relation',"
                        "'ontology:Property','ontology:LightDoc')"
                        % sql_str(params.get("kb_id", "")))
                    view = "model" if rows and int(rows[0]["n"] or 0) > 0 else "browse"
                except Exception:  # noqa: BLE001
                    view = "browse"
            html = render_graph_page(params.get("kb_id", ""), params.get("model", ""), view)
            self._send(200, html.encode("utf-8"), "text/html; charset=utf-8")
            return
        if path in ("/bodhi/pending", "/bodhi/pending.json"):
            params = dict(urlparse.parse_qsl(parsed.query))
            try:
                data = list_pending_merges(params.get("kb_id", ""))
                self._json(data, 200, {"Access-Control-Allow-Origin": "*"})
            except Exception as exc:  # noqa: BLE001
                self._json({"error": str(exc)}, 400, {"Access-Control-Allow-Origin": "*"})
            return
        if path in ("/bodhi/resolve", "/bodhi/resolve.json"):
            params = dict(urlparse.parse_qsl(parsed.query))
            try:
                data = resolve_pending_merge(params.get("kb_id", ""), params.get("slug", ""),
                                             params.get("action", ""))
                self._json(data, 200, {"Access-Control-Allow-Origin": "*"})
            except Exception as exc:  # noqa: BLE001
                print("[mcp] /bodhi/resolve 失败：%s" % exc)
                self._json({"error": str(exc)}, 400, {"Access-Control-Allow-Origin": "*"})
            return
        # 本实现不使用服务端主动推送（GET SSE），按规范返回 405 即可
        self._json({"jsonrpc": "2.0", "id": None,
                    "error": {"code": -32000, "message": "GET 未支持；请用 POST /mcp"}}, 405)

    def do_DELETE(self) -> None:  # noqa: N802
        self._send(200, b"", "application/json")


def main() -> int:
    parser = argparse.ArgumentParser(description="本体知识保存工具（MCP over Streamable HTTP）")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    server = ThreadingHTTPServer((args.host, args.port), MCPHandler)
    print("[mcp] %s %s 监听 http://%s:%d/mcp（工具：%s）"
          % (SERVER_NAME, SERVER_VERSION, args.host, args.port,
             ", ".join(t["name"] for t in tool_definitions())))
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("[mcp] 停止")
    return 0


# ---------------------------------------------------------------------------
# 只读接口：Bodhi 语义图（供前端「Bodhi 图谱」页 / 服务自带 HTML 页使用）
#   GET /bodhi/graph?kb_id=..&model=bmm&types=bmm:Goal,bmm:Objective&limit=300
# 数据来源：页面正文的「## 本体关系」小节（我们写页时格式固定）+ 页面元数据
# 说明：REL_LINE / parse_rel_line / rel_line 已移到 tools/ke-core/ke_pages.py
#       （文件头 import 回来；relink_pages.py 的 server.rel_line 用法不变）。
# ---------------------------------------------------------------------------
def _class_meta(model_key: str) -> dict[str, dict]:
    meta = ke_ontology.class_meta()
    if not model_key:
        return meta
    return {k: v for k, v in meta.items() if v.get("module") == model_key}


def all_class_meta() -> dict[str, dict]:
    """兼容别名：实现见 tools/ke-core/ke_ontology.py（标签/颜色/父类）。"""
    return ke_ontology.class_meta()


def class_group(type_name: str) -> str:
    """类的「大类」= 顶层父类的中文名（用于列表/树的二级分组）。"""
    return ke_ontology.top_group(type_name)


def class_category_path(type_name: str, fallback_module: str = "",
                        fallback_module_label: str = "", fallback_label: str = "") -> list[str]:
    """页面的一级分类路径：**模型标签 → 大类**（树只按它折叠两级别）。

    用户口径（2026-09-19 验收）：不需要三级目录——第三层直接就是知识页，
    本体「类」这一层改为**页面行前面的类型标签**表达（见 patch_frontend.py 的 v2 调整）。
    这样即使本体自身还有更深的层级，也只由标签区分，不再多分折叠层。
    实现已并入 ke_ontology.category_path（Neo4j 优先，JSON 兜底）。
    """
    return ke_ontology.category_path(type_name)


# 本体模型库页面用的普通 markdown 内链（`[标题](wiki:slug)`）
_WIKI_LINK_RE = re.compile(r"\[(?P<label>[^\]]+)\]\(wiki:(?P<slug>[^)]+)\)")
# 按「所在小节」推断边类型（本体 wiki 的结构：父类/子类/相关关系/属性定义/…）
_SECTION_EDGE = (
    ("父类", ("subclass", "父类")),
    ("子类", ("subclass", "子类")),
    ("相关关系", ("relation", "相关关系")),
    ("跨模块桥", ("relation", "跨模块桥")),
    ("本体关系", ("relation", "本体关系")),
    ("关系页", ("relation", "关系页")),
    ("反向属性", ("relation", "反向属性")),
    ("属性定义", ("property", "属性定义")),
    ("本体属性", ("property", "本体属性")),
    ("定义域", ("property", "定义域")),
    ("值域", ("property", "值域")),
    ("本体类", ("class", "包含类")),
)


def _section_edge(section: str, link_label: str):
    """本体 wiki 的内链 → (边类型, 中文标签)；识别不出小节时退回 (wiki, 链接文字)。"""
    for prefix, pair in _SECTION_EDGE:
        if section.startswith(prefix):
            return pair[0], ("%s：%s" % (pair[1], link_label)) if link_label else pair[1]
    return "wiki", (section + "：" + link_label) if section else (link_label or "链接")


def bodhi_model_graph(model: str = "", kb_id: str = "") -> dict:
    """**本体模型结构图**（本体模型库专用）：节点 = 本体类，边 = 子类 + 对象属性(domain→range)。

    与 `bodhi_graph`（按 wiki 页画图：类/关系/属性各是一页 → 关系也成了节点，且名字不全）的区别：
    这里直接读 **Neo4j 本体投影**，所以：
    - 节点只有**类**（带中文 label），颜色按**模块**；
    - 关系是**边**，边上带关系中文名（`label`）与 prefixed（`type`），子类是「子类」边。
    这样图就是「本体模型长什么样」，而不是「知识库里有哪些页」。
    """
    try:
        import ke_neo4j  # ke-core（与 bodhi-mcp 同目录体系）
        if not ke_neo4j.available():
            return {"view": "model", "model": model, "nodes": [], "edges": [],
                    "error": "Neo4j 本体投影不可用"}
    except Exception as exc:  # noqa: BLE001
        return {"view": "model", "model": model, "nodes": [], "edges": [],
                "error": "Neo4j 不可用：%s" % exc}

    params = {"m": model} if model else {}
    where = "WHERE c.external IS NULL " + ("AND c.module = $m " if model else "")
    rows = ke_neo4j.query(
        "MATCH (c:BodhiOntClass) " + where +
        "RETURN c.prefixed AS id, c.label AS label, c.local_name AS name, c.description AS definition, "
        "       c.module AS module ORDER BY c.module, c.local_name", params)
    nodes, ids = [], set()
    for r in rows:
        ident = r.get("id") or r.get("name") or ""
        if not ident or ident in ids:
            continue
        ids.add(ident)
        mod = r.get("module") or ""
        label = r.get("label") or r.get("name") or ident
        nodes.append({
            "slug": ident, "title": label, "page_type": "ontology:Class",
            "class_label": label, "module": mod, "module_label": ke_ontology.module_label(mod),
            "group": mod or "ontology", "group_label": ke_ontology.module_label(mod),
            "color": ke_ontology.module_color(mod), "version": 1,
            "summary": (r.get("definition") or "")[:200], "source_refs": [],
        })

    edges = []
    for r in ke_neo4j.query(
            "MATCH (c:BodhiOntClass)-[:BODHI_SUBCLASS_OF]->(p:BodhiOntClass) "
            "WHERE c.external IS NULL AND p.external IS NULL "
            "RETURN c.prefixed AS a, p.prefixed AS b"):
        if r.get("a") in ids and r.get("b") in ids:
            edges.append({"source": r["a"], "target": r["b"], "type": "subclass", "label": "子类"})
    for r in ke_neo4j.query(
            "MATCH (p:BodhiOntProperty {property_kind: 'object'})-[:BODHI_DOMAIN]->(d:BodhiOntClass) "
            "MATCH (p)-[:BODHI_RANGE]->(t:BodhiOntClass) "
            "WHERE d.external IS NULL AND t.external IS NULL "
            "RETURN p.prefixed AS pid, p.label AS plabel, d.prefixed AS a, t.prefixed AS b", {}):
        if r.get("a") in ids and r.get("b") in ids:
            edges.append({"source": r["a"], "target": r["b"],
                          "type": r.get("pid") or "relation",
                          "label": r.get("plabel") or r.get("pid") or ""})

    groups = {}
    for n in nodes:
        g = groups.setdefault(n["group"], {"label": n["group_label"], "color": n["color"], "count": 0})
        g["count"] += 1

    # 节点点击要能打开该类的 wiki 页：把节点 slug 换成**真实 wiki slug**（本体模型库里
    # `ontology/<模块>/<本地名小写>`），类名另存 `type_name`；页面不存在时保留 prefixed。
    if kb_id:
        def _wiki_slug(module_key: str, prefixed: str) -> str:
            """真实 wiki slug：`ontology/<模块 key>/<本地名小写>`。

            ⚠️ 模块 key 要用 Neo4j 的 `module`（ea-service / ea-ownership / bmm-fd），
            不能用 prefixed 的前缀（easvc:/eaown:/bmmfd:）——否则对不上 wiki 页（实测 26/47）。
            """
            local = prefixed.split(":", 1)[-1]
            return "ontology/%s/%s" % (module_key, re.sub(r"[^a-z0-9]+", "-", local.lower()).strip("-"))
        want = {n["slug"]: _wiki_slug(n.get("module") or "", n["slug"]) for n in nodes}
        try:
            rows = psql_csv(
                "SELECT slug FROM wiki_pages WHERE knowledge_base_id = %s AND deleted_at IS NULL "
                "AND slug IN (%s)"
                % (sql_str(kb_id), ", ".join(sql_str(s) for s in sorted(set(want.values())))))
            have = {r["slug"] for r in rows}
        except Exception:  # noqa: BLE001
            have = set()
        for n in nodes:
            n["type_name"] = n["slug"]
            if want.get(n["slug"]) in have:
                n["slug"] = want[n["slug"]]
        # ⚠️ 边的端点是 prefixed 名字（bmm:Goal），节点 slug 已换成 wiki slug，
        # 必须同步重映射，否则图里只剩节点、没有关系（2026-09-20 实测踩过）。
        remap = {n["type_name"]: n["slug"] for n in nodes}
        for edge in edges:
            edge["source"] = remap.get(edge["source"], edge["source"])
            edge["target"] = remap.get(edge["target"], edge["target"])

    return {"kb_id": kb_id, "model": model, "view": "model", "nodes": nodes, "edges": edges,
            "meta": {"node_count": len(nodes), "edge_count": len(edges),
                     "groups": [dict(key=k, **v) for k, v in sorted(groups.items())],
                     "relation_types": sorted({e["type"] for e in edges})}}


def bodhi_graph(kb_id: str, model: str = "", types: str = "", limit: int = 300) -> dict:
    """要素节点 + 关系边（实例页读「## 本体关系」；本体 wiki 页按小节解析 markdown 内链）。

    节点带 `group` / `color`（**按模块配色**，用户 2026-09-19 口径：图要简单，颜色 = 模块差异）。
    """
    wanted = [t.strip() for t in types.split(",") if t.strip()]
    colors = _class_meta(model)
    sql = ("SELECT slug, title, COALESCE(page_type,'') AS page_type, COALESCE(summary,'') AS summary, "
           "COALESCE(version,1) AS version, COALESCE(content,'') AS content, "
           "COALESCE(page_metadata::text,'{}') AS meta, COALESCE(source_refs::text,'[]') AS refs "
           "FROM wiki_pages WHERE knowledge_base_id = %s AND deleted_at IS NULL "
           "AND COALESCE(page_type,'') NOT IN ('index','summary',%s)"
           % (sql_str(kb_id), sql_str(TYPE_PENDING)))
    if model:
        sql += " AND page_type LIKE %s" % sql_str(model + ":%")
    if wanted:
        sql += " AND page_type IN (%s)" % ", ".join(sql_str(t) for t in wanted)
    sql += " ORDER BY page_type, title LIMIT %d" % max(1, min(limit, 2000))
    rows = psql_csv(sql)

    nodes, known, edges = [], set(), []
    # 是否「本体模型库」：它的页类型统一是 ontology:*（类/关系/属性各一页）。
    # 是 → 图谱按**模块**分组配色（用户口径）；否（普通知识库）→ 维持原来的**按页类型**配色，
    # 否则普通知识库会把所有实例节点并成一种颜色、图例只剩模型名（2026-09-19 实测回归）。
    ontology_kb = any((r.get("page_type") or "").startswith("ontology:") for r in rows)
    for row in rows:
        try:
            meta = (json.loads(row["meta"] or "{}").get("ontology") or {})
        except json.JSONDecodeError:
            meta = {}
        cls = colors.get(row["page_type"], {})
        module = (meta.get("model") or cls.get("module")
                  or (row["page_type"].split(":", 1)[0] if ":" in row["page_type"] else model or ""))
        class_label = cls.get("label") or meta.get("class_label") or row["page_type"]
        if ontology_kb:
            group = module or row["page_type"]
            group_label = (cls.get("module_label") or meta.get("model_label")
                           or (ke_ontology.module_label(module) if module else "")) or class_label
            color = ke_ontology.module_color(module) if module else (cls.get("color") or "#94a3b8")
        else:
            group, group_label = row["page_type"], class_label
            color = cls.get("color") or "#94a3b8"
        nodes.append({
            "slug": row["slug"], "title": row["title"], "page_type": row["page_type"],
            "class_label": class_label,
            "module": module, "module_label": group_label,
            "group": group, "group_label": group_label,
            "color": color,
            "version": row["version"],
            "summary": (row["summary"] or "")[:200],
            "source_refs": json.loads(row["refs"] or "[]"),
        })
        known.add(row["slug"])
    for row in rows:
        section = ""
        for line in (row["content"] or "").splitlines():
            if line.startswith("## "):
                section = line[3:].strip()
                continue
            hit = parse_rel_line(line.strip())
            if hit:
                target_slug = hit.group("slug")
                if target_slug not in known:      # 只画两端都在结果集里的边
                    continue
                edges.append({"source": row["slug"], "target": target_slug,
                              "type": hit.group("type"), "label": hit.group("label")})
                continue
            # 本体模型库的页用普通 markdown 内链（`[标题](wiki:slug)`），没有「关系行」；
            # 这里按**所在小节**推断边类型，让本体图谱真的画出模型结构（用户口径：图要简单）。
            for m in _WIKI_LINK_RE.finditer(line):
                target_slug = m.group("slug").strip()
                if target_slug not in known or target_slug == row["slug"]:
                    continue
                etype, elabel = _section_edge(section, m.group("label").strip())
                edges.append({"source": row["slug"], "target": target_slug,
                              "type": etype, "label": elabel})
    return {"kb_id": kb_id, "model": model, "nodes": nodes, "edges": edges,
            "meta": {"node_count": len(nodes), "edge_count": len(edges),
                     "relation_types": sorted({e["type"] for e in edges}),
                     "classes": sorted({n["page_type"] for n in nodes}),
                     "modules": sorted({n["module"] for n in nodes if n["module"]}),
                     "generated_at": now_text(), "limit": limit}}


def bodhi_page(kb_id: str, slug: str) -> dict:
    """取单页内容（供图谱页点击查看；也可给其他工具复用）。"""
    rows = psql_csv("SELECT slug, title, COALESCE(page_type,'') AS page_type, COALESCE(summary,'') AS summary, "
                    "COALESCE(content,'') AS content, COALESCE(version,1) AS version, "
                    "COALESCE(source_refs::text,'[]') AS refs, COALESCE(chunk_refs::text,'[]') AS chunks, "
                    "COALESCE(out_links::text,'[]') AS outs, COALESCE(in_links::text,'[]') AS ins, "
                    "COALESCE(page_metadata::text,'{}') AS meta, COALESCE(updated_at::text,'') AS updated_at "
                    "FROM wiki_pages WHERE knowledge_base_id = %s AND slug = %s AND deleted_at IS NULL"
                    % (sql_str(kb_id), sql_str(slug)))
    if not rows:
        raise RuntimeError("页面不存在：%s" % slug)
    row = rows[0]
    try:
        meta = (json.loads(row["meta"] or "{}").get("ontology") or {})
    except json.JSONDecodeError:
        meta = {}
    return {
        "slug": row["slug"], "title": row["title"], "page_type": row["page_type"],
        "summary": row["summary"], "content": row["content"], "version": row["version"],
        "source_refs": json.loads(row["refs"] or "[]"), "chunk_refs": json.loads(row["chunks"] or "[]"),
        "out_links": json.loads(row["outs"] or "[]"), "in_links": json.loads(row["ins"] or "[]"),
        "class_label": meta.get("class_label", ""), "updated_at": row["updated_at"],
        "merge_history": meta.get("merge_history", []),
    }


if __name__ == "__main__":
    sys.exit(main())









