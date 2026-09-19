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

# 同目录的 graph_page.py（Bodhi 语义图 HTML 页）
_HERE = str(pathlib.Path(__file__).resolve().parent)
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)
from graph_page import render_graph_page  # noqa: E402
from mdview import render as render_md  # noqa: E402

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            pass


def now_text() -> str:
    return datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M:%S %z")


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
# ---------------------------------------------------------------------------
def _docker_prefix() -> list[str]:
    import shutil  # noqa: PLC0415
    if shutil.which("docker"):
        probe = subprocess.run(["docker", "version", "--format", "{{.Server.Version}}"],
                               capture_output=True, text=True, check=False)
        if probe.returncode == 0:
            return ["docker"]
    if shutil.which("wsl"):
        probe = subprocess.run(["wsl", "-d", "Ubuntu", "-u", "root",
                                "docker", "version", "--format", "{{.Server.Version}}"],
                               capture_output=True, text=True, check=False)
        if probe.returncode == 0:
            return ["wsl", "-d", "Ubuntu", "-u", "root", "docker"]
    raise RuntimeError("找不到可用的 docker（Windows PATH 或 WSL 里都没有）")


def psql(sql: str, stdin: bool = False, csv: bool = False) -> str:
    cmd = _docker_prefix() + ["exec", "-i", "-e", "PGPASSWORD=" + DB_PASSWORD,
                              DB_CONTAINER, "psql", "-U", DB_USER, "-d", DB_NAME]
    if stdin:
        cmd += ["-v", "ON_ERROR_STOP=1", "-q", "-f", "-"]
        done = subprocess.run(cmd, input=sql, text=True, encoding="utf-8",
                              capture_output=True, check=False)
    else:
        cmd += (["--csv", "-c", sql] if csv else ["-t", "-A", "-c", sql])
        done = subprocess.run(cmd, text=True, encoding="utf-8", capture_output=True, check=False)
    if done.returncode != 0:
        raise RuntimeError("psql 失败：%s" % (done.stderr or done.stdout)[:600])
    return done.stdout


def psql_csv(sql: str) -> list[dict]:
    import csv as csvlib  # noqa: PLC0415
    import io  # noqa: PLC0415
    return list(csvlib.DictReader(io.StringIO(psql(sql, csv=True))))


def sql_str(value) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def sql_json(value) -> str:
    return sql_str(json.dumps(value, ensure_ascii=False)) + "::jsonb"


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


def build_new_page(engine, model: dict, element: dict, chunk_id: str, chunk_index: int,
                   doc_meta: dict) -> dict:
    """新要素 -> 页面（与 ontology_wiki/weknora_sync 同风格：能被人读，也能被图谱用）。"""
    slug = element_slug(model["key"], element)
    rels = element.get("relations") or []
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
    return {
        "slug": slug, "title": element["name"], "page_type": element["type"],
        "summary": (element.get("definition") or "")[:500],
        "content": "\n".join(lines).rstrip() + "\n",
        "category_path": class_category_path(element["type"], model["key"], model["label"],
                                             element["type_label"]),
        "wiki_path": slug, "source_refs": [doc_meta["id"]],
        "chunk_refs": [chunk_id] if chunk_id else [],
        "out_links": sorted({r.get("target_slug") for r in rels if r.get("target_slug")}),
        "aliases": [element["name"]],
        "page_metadata": {"ontology": {
            "model": model["key"], "class": element["type"], "class_label": element["type_label"],
            "name": element["name"], "generator": TOOL_TAG, "created_at": now_text(),
        }},
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

    post = {"definition_upgraded": bool(new_def) and len(new_def) > len(old_def),
            "evidence_added": added_evidence, "relations_added": added_relations}
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
    return ("INSERT INTO wiki_pages (%s) VALUES (%s);"
            % (", ".join(PAGE_COLUMNS), ", ".join(values)))


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
    """按 out_links 重算 in_links（wiki 图谱的反向边）。

    注意：上游自己的页面（如根 index 页）可能把 out_links 写成**标量**，
    直接 jsonb_array_elements_text 会报 "cannot extract elements from a scalar"，
    所以先用 jsonb_typeof 守卫。
    """
    arr = "(CASE WHEN jsonb_typeof(s.out_links) = 'array' THEN s.out_links ELSE '[]'::jsonb END)"
    return ("UPDATE wiki_pages SET in_links = '[]'::jsonb "
            " WHERE knowledge_base_id = %s AND deleted_at IS NULL; "
            "WITH edges AS (SELECT s.slug AS src, t.value AS dst FROM wiki_pages s "
            "CROSS JOIN LATERAL jsonb_array_elements_text(%s) t "
            "WHERE s.knowledge_base_id = %s AND s.deleted_at IS NULL), "
            "inbound AS (SELECT dst, jsonb_agg(DISTINCT src) AS arr FROM edges GROUP BY dst) "
            "UPDATE wiki_pages p SET in_links = COALESCE(i.arr, '[]'::jsonb) "
            "FROM wiki_pages x LEFT JOIN inbound i ON i.dst = x.slug "
            "WHERE p.knowledge_base_id = %s AND p.slug = x.slug AND p.deleted_at IS NULL;\n"
            % (sql_str(kb_id), arr, sql_str(kb_id), sql_str(kb_id)))


def extract_and_save(model_key: str, kb_id: str, knowledge_id: str,
                     high: float = DEFAULT_HIGH, low: float = DEFAULT_LOW,
                     dry_run: bool = False, from_log: str = "") -> dict:
    engine = load_engine()
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

    pages = fetch_existing_pages(kb_id)
    statements: list[str] = []
    summary = {
        "model": model["key"], "kb_id": kb_id, "knowledge_id": knowledge_id,
        "doc_title": doc_title, "chunks": len(chunks), "chars": len(doc_text),
        "elements": len(payloads), "relationships": len(checked["edges"]),
        "created": [], "merged": [], "pending": [],
        "violations": checked["violations"], "unmatched": checked["unmatched"],
        "dry_run": dry_run, "thresholds": {"high": high, "low": low},
        "generated_at": now_text(),
    }

    for element in payloads:
        sim, candidate = pick_match(element, pages)
        if candidate is not None and sim >= high:
            content, post = merge_content(candidate["content"], element, element["chunk_id"],
                                          element["chunk_index"], doc_meta)
            source_refs = union_list(candidate.get("source_refs"), [doc_meta["id"]])
            chunk_refs = union_list(candidate.get("chunk_refs"),
                                    [element["chunk_id"]] if element["chunk_id"] else [])
            metadata = candidate.get("page_metadata") or {}
            ont = metadata.setdefault("ontology", {})
            ont.setdefault("merge_history", []).append(
                {"at": now_text(), "doc": doc_meta["title"],
                 "chunk_index": element["chunk_index"], "similarity": sim, **post})
            ont["last_merge_at"] = now_text()
            new_summary = (element.get("definition") or candidate.get("summary") or "")[:500]
            statements.append(sql_update_page(candidate, content, new_summary,
                                              source_refs, chunk_refs, metadata))
            summary["merged"].append({"name": element["name"], "type": element["type"],
                                      "into": candidate["slug"], "similarity": sim, **post})
            candidate.update({"content": content, "summary": new_summary,
                              "source_refs": source_refs, "chunk_refs": chunk_refs,
                              "page_metadata": metadata})
        elif candidate is None or sim <= low:
            page = build_new_page(engine, model, element, element["chunk_id"],
                                  element["chunk_index"], doc_meta)
            if any(p["slug"] == page["slug"] for p in pages):
                page["slug"] = "%s-%s" % (page["slug"],
                                          hashlib.sha1(element["type"].encode()).hexdigest()[:6])
                page["wiki_path"] = page["slug"]
            statements.append(sql_insert_page(page, kb_id, tenant_id))
            summary["created"].append({"name": element["name"], "type": element["type"],
                                       "slug": page["slug"]})
            pages.append({**page, "version": 1, "knowledge_base_id": kb_id,
                          "status": "published", "in_links": [], "title": page["title"],
                          "summary": page["summary"], "aliases": page["aliases"],
                          "source_refs": page["source_refs"], "chunk_refs": page["chunk_refs"]})
        else:
            page = build_pending_page(model, element, candidate, sim, element["chunk_id"],
                                      element["chunk_index"], doc_meta, high, low)
            statements.append(sql_insert_page(page, kb_id, tenant_id))
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
    return summary


# ---------------------------------------------------------------------------
# 待确认裁决 + 查询工具
# ---------------------------------------------------------------------------
def list_pending_merges(kb_id: str) -> dict:
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
    index = json.loads(INDEX_PATH.read_text(encoding="utf-8"))
    for model in index["models"]:
        if model["key"] == model_key:
            return {
                "model": model["key"], "label": model["label"],
                "expert_role": model.get("expert_role", ""),
                "classes": [{"name": c["name"], "label": c.get("label"),
                             "definition": c.get("definition")} for c in model["classes"]],
                "relations": [{"name": r["name"], "label": r.get("label"),
                               "domain": r.get("domain"), "range": r.get("range")}
                              for r in list(model["relations"])
                              + list(model.get("cross_module_bridges") or [])],
            }
    raise RuntimeError("未知本体模型：%s" % model_key)


# ---------------------------------------------------------------------------
# MCP：工具定义与调用
# ---------------------------------------------------------------------------
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
                },
                "required": ["model", "kb_id", "knowledge_id"],
            },
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
    ]


def call_tool(name: str, args: dict) -> dict:
    if name == "extract_and_save":
        return extract_and_save(
            str(args["model"]), str(args["kb_id"]), str(args["knowledge_id"]),
            high=float(args.get("high", DEFAULT_HIGH)),
            low=float(args.get("low", DEFAULT_LOW)),
            dry_run=bool(args.get("dry_run", False)),
            from_log=str(args.get("from_log", "")))
    if name == "list_pending_merges":
        return list_pending_merges(str(args["kb_id"]))
    if name == "resolve_pending_merge":
        return resolve_pending_merge(str(args["kb_id"]), str(args["pending_slug"]),
                                     str(args["action"]))
    if name == "ontology_types":
        return ontology_types(str(args["model"]))
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
        if self.path.rstrip("/") not in ("", "/mcp"):
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

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse.urlparse(self.path)
        path = parsed.path.rstrip("/")
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
            html = render_graph_page(params.get("kb_id", ""), params.get("model", ""))
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
# ---------------------------------------------------------------------------
REL_LINE = re.compile(r"^- (?P<label>.+?)（`(?P<type>[^`]+)`）→ \[(?P<target>.+?)\]\(wiki:(?P<slug>[^)]+)\)\s*$")
# 2026-09-19 起的统一格式：[[slug|正文]]（上游前端 citationMarkdown 支持 [[wiki]] 链接；
# 旧的 [正文](wiki:slug) 会被 markdown 渲染器当未知协议退化成纯文本，页面里看不到链接）
REL_LINE_V2 = re.compile(r"^- (?P<label>.+?)（`(?P<type>[^`]+)`）→ \[\[(?P<slug>[^|\]]+)\|(?P<target>[^\]]+)\]\]\s*$")


def parse_rel_line(line: str):
    """解析「本体关系」小节的一行，兼容新旧两种链接格式。"""
    return REL_LINE.match(line) or REL_LINE_V2.match(line)


def rel_line(label: str, rel_type: str, target: str, slug: str) -> str:
    """生成「本体关系」小节的一行（统一 [[slug|正文]] 站内链接格式）。"""
    if slug:
        return "- %s（`%s`）→ [[%s|%s]]" % (label, rel_type, slug, target)
    return "- %s（`%s`）→ %s" % (label, rel_type, target)


def _class_meta(model_key: str) -> dict[str, dict]:
    index = json.loads(INDEX_PATH.read_text(encoding="utf-8"))
    out: dict[str, dict] = {}
    for model in index["models"]:
        if model_key and model["key"] != model_key:
            continue
        for cls in model["classes"]:
            out[cls["name"]] = {"color": cls.get("color"), "label": cls.get("label"),
                                "module": model["key"], "module_label": model["label"],
                                "parents": cls.get("parents") or []}
    return out


_ALL_META: dict[str, dict] | None = None


def all_class_meta() -> dict[str, dict]:
    global _ALL_META
    if _ALL_META is None:
        _ALL_META = _class_meta("")
    return _ALL_META


def class_group(type_name: str) -> str:
    """类的「大类」= 顶层父类的中文名（用于列表/树的二级分组）。"""
    meta = all_class_meta()
    cur = meta.get(type_name)
    seen: set[str] = set()
    while cur and (cur.get("parents") or []):
        parent = cur["parents"][0]
        if parent in seen or parent not in meta:
            break
        seen.add(parent)
        cur = meta[parent]
    if not cur:
        return ""
    return cur.get("label") or cur.get("name") or ""


def class_category_path(type_name: str, fallback_module: str = "",
                        fallback_module_label: str = "", fallback_label: str = "") -> list[str]:
    """页面的一级分类路径：**模型标签 → 大类**（树只按它折叠两级别）。

    用户口径（2026-09-19 验收）：不需要三级目录——第三层直接就是知识页，
    本体「类」这一层改为**页面行前面的类型标签**表达（见 patch_frontend.py 的 v2 调整）。
    这样即使本体自身还有更深的层级，也只由标签区分，不再多分折叠层。
    """
    meta = all_class_meta().get(type_name) or {}
    module_label = meta.get("module_label") or fallback_module_label or fallback_module
    label = meta.get("label") or fallback_label or type_name
    group = class_group(type_name) or label
    return [module_label, group]


def bodhi_graph(kb_id: str, model: str = "", types: str = "", limit: int = 300) -> dict:
    """要素节点 + 本体关系边（边带关系类型与中文标签；方向 = 页面里的箭头方向）。"""
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
    for row in rows:
        try:
            meta = (json.loads(row["meta"] or "{}").get("ontology") or {})
        except json.JSONDecodeError:
            meta = {}
        cls = colors.get(row["page_type"], {})
        nodes.append({
            "slug": row["slug"], "title": row["title"], "page_type": row["page_type"],
            "class_label": cls.get("label") or meta.get("class_label") or row["page_type"],
            "module": cls.get("module") or model or "", "module_label": cls.get("module_label") or "",
            "color": cls.get("color") or "#94a3b8", "version": row["version"],
            "summary": (row["summary"] or "")[:200],
            "source_refs": json.loads(row["refs"] or "[]"),
        })
        known.add(row["slug"])
    for row in rows:
        for line in (row["content"] or "").splitlines():
            hit = parse_rel_line(line.strip())
            if not hit:
                continue
            target_slug = hit.group("slug")
            if target_slug not in known:      # 只画两端都在结果集里的边
                continue
            edges.append({"source": row["slug"], "target": target_slug,
                          "type": hit.group("type"), "label": hit.group("label")})
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









