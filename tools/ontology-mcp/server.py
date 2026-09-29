"""本体知识「保存工具」——MCP 服务（給 WeKnora 智能体调用）。

为什么是 MCP（见 docs/weknora-fork.md §10.14）
--------------------------------------------
上游完整支持 MCP（`MCPService` 的 `sse`/`http-streamable`/`stdio`，`mcp_services` 表、
`MCPTool` 适配、前端配置页），工具名形如 `mcp_<服务名>_<工具名>`。
因此「分类 + 关系合规 + 与存量合并」可以做成**外部工具**挂给智能体，
**不需要改 Go、不需要重建 app 镜像**。

本服务暴露的工具
----------------
- 建模/落库（`domain_modeling` 技能的分批流程）：`doc_outline` / `save_knowledge` /
    `extract_state` / `link_candidates` / `list_link_candidates` / `resolve_link_candidate`；
    落库半段 `save_elements`（合规校验 + 相似度匹配 → 合并/新增/待确认 + 写页/版本）不变。
- 详设/巡检/总览/技能：`service_*`、`list_pending_merges`、`resolve_pending_merge`、
    `ontology_types`、`skills`、`job_status`（异步任务回执）。
- **2026-09-22 退役**：整篇异步抽取工具（`extract_and_save` 及其状态查询 `extract_status`）
    已移除，原文留痕在 `tools/ontology-mcp/archive/async_extract_retired_2026-09-22.py.txt`。
    MCP 侧**不再持有 LLM 调用链** → `openai` 依赖随之去掉；`rdflib` 仅本体编译工具需要。
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
import os
import pathlib
import re
import subprocess
import sys
import threading
import time
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

DB_CONTAINER, DB_USER, DB_NAME = "WeKnora-postgres", "postgres", "WeKnora"
# 口令不在此内置：由 `ke_db`（env `BODHI_DB_PASSWORD` → WeKnora `.env`）提供，见下方 import。
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
import ke_db  # noqa: E402  （模块级引用：resolve_kb_id 等）
import ke_admin  # noqa: E402
import ke_pages  # noqa: E402
import ke_docs  # noqa: E402  （按来源文档统计/清理本体实例，2026-09-20）
import ke_audit  # noqa: E402  （wiki↔图谱↔模型 一致性巡检，只读，2026-09-20 P1）
import ke_context
import ke_import
import ke_review
import ke_sheet  # noqa: E402  （跨库上下文映射：同名/同义/异义 + 依赖，只读，2026-09-28 一期）
import ke_yamlmini  # noqa: E402  （零依赖 YAML 子集：干净容器里没有 PyYAML 时的兜底）
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
def page_id_for(kb_id: str, slug: str) -> str:
    """**(知识库, slug) → 确定性页 id**。

    2026-09-24 修（用户实测）：旧实现只按 slug 派生 → **两个知识库里的同名页 id 相同** →
    `INSERT ... ON CONFLICT (id) DO UPDATE` 会把**别的库那一行**更新掉（回执却报成功、
    本库查不到该页）。现在把 kb 并入派生，跨库天然不冲突。
    迁移策略见 `_resolve_page_id`：本库已有同 slug 的页一律**沿用其现有 id**（老数据零迁移）。
    """
    return str(uuid.uuid5(uuid.NAMESPACE_URL, "bodhi-element:%s|%s" % (kb_id, slug)))


def _resolve_page_id(kb_id: str, slug: str) -> tuple[str, str]:
    """给 `(kb_id, slug)` 定一个**本库内唯一**的页 id，并说明用的哪种方式。

    ① 本库已有同 slug 的页 → **沿用它的现有 id**（继续更新它，不产生重复页；老数据零迁移）；
    ② 否则用 `page_id_for(kb_id, slug)`；该 id 若**属于别的知识库**（历史遗留的纯 slug 派生），
       加后缀再派生，直到拿到"空闲 / 属本库"的 id。

    返回 `(id, how)`，`how` ∈ `existing` / `new` / `suffixed`。
    """
    rows = psql_csv("SELECT id FROM wiki_pages WHERE knowledge_base_id = %s AND slug = %s "
                    "ORDER BY version DESC LIMIT 1" % (sql_str(kb_id), sql_str(slug)))
    if rows:
        return rows[0]["id"], "existing"
    base = page_id_for(kb_id, slug)
    for n in range(0, 50):
        cand = base if n == 0 else str(uuid.uuid5(
            uuid.NAMESPACE_URL, "bodhi-element:%s|%s|%d" % (kb_id, slug, n)))
        owner = psql_csv("SELECT knowledge_base_id AS kb FROM wiki_pages WHERE id = %s"
                         % sql_str(cand))
        if not owner:
            return cand, ("new" if n == 0 else "suffixed")
        if owner[0]["kb"] == kb_id:
            return cand, "new"
    raise RuntimeError("给（kb=%s, slug=%s）找不到可用页 id（已试 50 个后缀）" % (kb_id, slug))


def _warn_noncanonical_ids(strategies: list, subject: str = "") -> list[dict]:
    """把 `how != existing/new` 的页挑出来告警（日志 + 返回值），并**只报一次**同样的 slug。

    出现 `suffixed` 意味着：`uuid5("bodhi-element:<kb>|<slug>")` 已被**别的库**占着 ——
    在新方案下这几乎不可能（要 uuid5 碰撞或有人直接写库），所以它是一条**信号**而不是常态；
    巡检 D5 会独立复核这些页的 id。调用方把返回的清单带进回执即可。
    """
    odd = [s for s in (strategies or []) if s.get("how") not in ("existing", "new")]
    if odd:
        detail = "、".join("%s(→ %s, %s)" % (s.get("slug"), str(s.get("id"))[:8], s.get("how"))
                           for s in odd[:5])
        print("[mcp] ⚠ 页 id 走了兜底分支%s：%s —— 请核对是否有人直接写 wiki_pages（巡检 D5）"
              % (("（%s）" % subject) if subject else "", detail))
    return odd


# ---------------------------------------------------------------------------
# 片段读取 + 一次性抽取
# ---------------------------------------------------------------------------
def load_engine():
    here = str(REPO / "tools" / "ontology-extract")
    if here not in sys.path:
        sys.path.insert(0, here)
    import extract  # noqa: PLC0415
    return extract


# ---------------------------------------------------------------------------
# 领域建模 v2：按切片分批的上下文 + 会话页索引 + 候选关联（单独确认）
#   用户口径（2026-09-21）：不再一次性异步抽取；按切片大小与父子关系组织"合适的上下文"，
#   一次交互的输入 token 上限当作**会话参数**（超时就调小阈值重跑）；每轮落库都回执
#   **页面名称 + 编号**，会话上下文因此始终带着整个文档的索引；跨批/跨库的关联目标若不在
#   上下文里，先向量召回候选，**单独确认后**才写入领域模型。
# ---------------------------------------------------------------------------
DOMAIN_STATE_DIR = REPO / "state" / "domain_sessions"
try:
    DEFAULT_ROUND_BUDGET = int(os.environ.get("BODHI_ROUND_BUDGET_TOKENS") or 8000)
except ValueError:
    DEFAULT_ROUND_BUDGET = 8000
try:
    CHARS_PER_TOKEN = float(os.environ.get("BODHI_CHARS_PER_TOKEN") or 1.6)
except ValueError:
    CHARS_PER_TOKEN = 1.6


def _state_path(kb_id: str, knowledge_id: str) -> pathlib.Path:
    safe = re.sub(r"[^0-9a-zA-Z._-]", "_", knowledge_id or "__kb__")
    return DOMAIN_STATE_DIR / kb_id / ("%s.json" % safe)


def _empty_state(kb_id: str, knowledge_id: str) -> dict:
    return {"kb_id": kb_id, "knowledge_id": knowledge_id, "doc_title": "",
            "budget_tokens": DEFAULT_ROUND_BUDGET, "cursor": 0, "next_no": 1,
            "rounds": [], "pages": [], "pending_links": []}


def load_domain_state(kb_id: str, knowledge_id: str = "") -> dict:
    path = _state_path(kb_id, knowledge_id)
    if not path.is_file():
        return _empty_state(kb_id, knowledge_id)
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return _empty_state(kb_id, knowledge_id)
    base = _empty_state(kb_id, knowledge_id)
    base.update(state)
    return base


def save_domain_state(kb_id: str, knowledge_id: str, state: dict) -> pathlib.Path:
    path = _state_path(kb_id, knowledge_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")
    return path


def _est_tokens(text: str) -> int:
    return int(len(squash(text)) / CHARS_PER_TOKEN) + 1


def _chunk_units(knowledge_id: str) -> list[dict]:
    """把切片按**父子关系**组装成上下文单元：一个 parent_text（父）＋它名下的 text（子）。

    - 父块已经包含子块正文（实测：父 ≈ 子之和 + 头），所以**上下文用父块正文**，
      子块只用于定位（`chunk_refs`/`source_text` 匹配）；避免重复喂给模型。
    - 没有父块（非层级切分）时，按顺序把 text 块打包成等大单元。
    """
    rows = psql_csv("SELECT id, chunk_index, chunk_type, COALESCE(parent_chunk_id,'') AS parent_id, "
                    "content FROM chunks WHERE knowledge_id = %s AND deleted_at IS NULL "
                    "AND COALESCE(is_enabled, true) ORDER BY chunk_index, seq_id, id"
                    % sql_str(knowledge_id))
    parents = [r for r in rows if (r.get("chunk_type") or "") == "parent_text"]
    children: dict[str, list[dict]] = {}
    for row in rows:
        if row.get("parent_id"):
            children.setdefault(row["parent_id"], []).append(row)
    units: list[dict] = []
    if parents:
        for p in parents:
            kids = children.get(p["id"], [])
            text = p["content"] or ""
            units.append({"unit_id": p["id"], "kind": "parent",
                          "chunk_ids": [p["id"]] + [k["id"] for k in kids],
                          "child_count": len(kids), "chars": len(text), "text": text,
                          "parent_head": squash(text)[:120],
                          "children": [{"id": k["id"], "chars": len(k["content"] or ""),
                                        "text": k["content"] or "",
                                        "est_tokens": _est_tokens(k["content"] or "")}
                                       for k in kids]})
        # 有父块但有些 text 块没挂父（切分异常）→ 追加成独立单元，避免漏内容
        orphan = [r for r in rows if (r.get("chunk_type") or "") == "text" and not r.get("parent_id")]
        for r in orphan:
            units.append({"unit_id": r["id"], "kind": "orphan_text", "chunk_ids": [r["id"]],
                          "child_count": 0, "chars": len(r["content"] or ""), "text": r["content"] or "",
                          "parent_head": "", "children": []})
    else:
        buffer, chars, ids = [], 0, []
        for r in rows:
            buffer.append(r["content"] or "")
            ids.append(r["id"])
            chars += len(r["content"] or "")
            if chars >= 1500:
                units.append({"unit_id": ids[0], "kind": "packed_text", "chunk_ids": list(ids),
                              "child_count": len(ids) - 1, "chars": chars, "text": "\n".join(buffer),
                              "parent_head": "", "children": []})
                buffer, chars, ids = [], 0, []
        if buffer:
            units.append({"unit_id": ids[0], "kind": "packed_text", "chunk_ids": list(ids),
                          "child_count": len(ids) - 1, "chars": chars, "text": "\n".join(buffer),
                          "parent_head": "", "children": []})
    for u in units:
        u["est_tokens"] = _est_tokens(u["text"])
        u["headings"] = [squash((ln or ""))[:60] for ln in (u["text"] or "").splitlines()[:3] if (ln or "").strip()]
    return units


def _split_unit(unit: dict, budget: int) -> list[dict]:
    """父块本身超过预算时，**按子块细分**（这样"调小阈值"永远能继续缩小）。

    每个子块独立成一个上下文原子；带上父块开头 120 字作为归属提示（便于模型知道自己在读哪一段）。
    """
    children = unit.get("children") or []
    if not children:
        return [unit]                      # 没有子块可拆：只能整块给（回执里 oversized 会提示）
    atoms = []
    for i, child in enumerate(children):
        prefix = ("【父块：%s…】\n" % unit.get("parent_head", "")) if i == 0 else ""
        text = prefix + (child.get("text") or "")
        atoms.append({"unit_id": child["id"], "kind": "child_of",
                      "parent_id": unit["unit_id"], "chunk_ids": [child["id"]],
                      "child_count": 0, "chars": len(text), "text": text,
                      "headings": [squash(text)[:60]],
                      "est_tokens": _est_tokens(text), "children": []})
    return atoms


def doc_outline(kb_id: str, knowledge_id: str, budget_tokens: int = 0, cursor: int = 0,
                batches: int = 1, with_text: bool = True) -> dict:
    """按切片**父子关系**把文档组织成"大小合适"的上下文批次（领域建模 v2 的入口工具）。

    - `budget_tokens`：本批上下文的 token 上限（**会话参数**）。默认取环境变量
      `BODHI_ROUND_BUDGET_TOKENS`（默认 8000）。**超时就把它调小**再重跑本工具；
    - `cursor`：从第几个上下文单元开始（上一批回执里的 `next_cursor`）；
    - `batches`：本次取几批（默认 1，对应"一轮一次交互"）；
    - `with_text=False`：只给地图（标题/规模），不返回正文（省 token）。
    """
    kb_id, _note = resolve_kb_id(kb_id)
    knowledge_id, doc_title = resolve_knowledge_id(kb_id, knowledge_id)
    budget = max(500, int(budget_tokens or DEFAULT_ROUND_BUDGET))
    raw_units = _chunk_units(knowledge_id)
    if not raw_units:
        return {"error": "该文档没有可用切片：%s（先等切片完成）" % knowledge_id}
    # 父块本身超过预算 → 按子块细分（这样"调小阈值"永远能继续缩小上下文）
    units, split_parents = [], []
    for u in raw_units:
        if u["est_tokens"] > budget and (u.get("children") or []):
            atoms = _split_unit(u, budget)
            units.extend(atoms)
            split_parents.append({"parent_unit": u["unit_id"], "atoms": len(atoms)})
        else:
            units.append(u)
    total_chars = sum(u["chars"] for u in units)
    total_tokens = sum(u["est_tokens"] for u in units)
    start = max(0, min(int(cursor or 0), len(units)))
    out_batches, idx = [], start
    while idx < len(units) and len(out_batches) < max(1, int(batches or 1)):
        chars, group = 0, []
        while idx < len(units):
            unit = units[idx]
            if group and (chars + unit["chars"]) / CHARS_PER_TOKEN > budget:
                break
            group.append(unit)
            chars += unit["chars"]
            idx += 1
        if not group:
            break
        out_batches.append({
            "batch_no": len(out_batches) + 1,
            "units": [u["unit_id"] for u in group],
            "chunk_ids": [cid for u in group for cid in u["chunk_ids"]],
            "child_count": sum(u["child_count"] for u in group),
            "chars": chars,
            "est_tokens": int(chars / CHARS_PER_TOKEN) + 1,
            "headings": [h for u in group for h in u["headings"]][:8],
            "text": "\n\n---\n\n".join(u["text"] for u in group) if with_text else "",
        })
    return {
        "kb_id": kb_id, "knowledge_id": knowledge_id, "doc_title": doc_title,
        "budget_tokens": budget, "chars_per_token": CHARS_PER_TOKEN,
        "total_units": len(units), "total_chars": total_chars,
        "est_total_tokens": total_tokens,
        "est_total_batches": max(1, -(-total_tokens // budget)),
        "cursor": start, "next_cursor": idx, "done": idx >= len(units),
        "oversized_units": [u["unit_id"] for u in units if u["est_tokens"] > budget],
        "split_parents": split_parents,
        "batches": out_batches,
        "guidance": [
            "只从本批正文里抽知识：节点/关系必须有本批文本支撑（`source_text` 用原句，便于定位切片）。",
            "需要的目标节点**不在本批里**时：先用向量检索（wiki_search）找候选，再调 `link_candidates` "
            "登记候选 —— **不要自己编 slug**，也不要直接写关系。",
            "本批产出用 `save_knowledge(stage=\"graph\", mode=\"apply\", session={...})` 落库；回执里的 "
            "`created[].no` 就是页面的**会话编号**（下一轮引用编号/slug，而不是复述全文）。",
            "一批做完：用回执里的 `session.next_cursor` 作为本工具的 `cursor` 取下一批。",
            "某轮超时/被截断：把 `budget_tokens` 调小（8000 → 4000 → 2000）重跑本工具；"
            "父块超过预算时会**自动按子块细分**（回执 `split_parents` 可见）。",
            "**同一份文档尽量固定预算跑完**；中途改预算则 `cursor` 失效，从头重跑即可 —— "
            "落库是幂等的（同 slug 命中即合并更新，不会重复建页）。",
        ],
    }


def extract_state(kb_id: str, knowledge_id: str = "", action: str = "get") -> dict:
    """会话页索引与进度：每轮落库的**页面名称 + 编号**、已做轮次、待确认候选关联。"""
    kb_id, _note = resolve_kb_id(kb_id)
    if action == "reset":
        state = _empty_state(kb_id, knowledge_id)
        save_domain_state(kb_id, knowledge_id, state)
        return {"action": "reset", "kb_id": kb_id, "knowledge_id": knowledge_id, "state": state}
    if action != "get":
        raise RuntimeError("action 只能是 get 或 reset")
    state = load_domain_state(kb_id, knowledge_id)
    return {
        "kb_id": kb_id, "knowledge_id": knowledge_id, "doc_title": state.get("doc_title", ""),
        "budget_tokens": state.get("budget_tokens"), "cursor": state.get("cursor"),
        "rounds": state.get("rounds", []), "pages_total": len(state.get("pages", [])),
        "pages": state.get("pages", []), "next_no": state.get("next_no", 1),
        "pending_links": [x for x in state.get("pending_links", []) if x.get("status") == "pending"],
        "hint": "页面的 `no` 是本会话编号；引用既有页面用 slug/title，不要重建同名页。",
    }


# --- 候选关联（跨上下文/跨库的关联，必须单独确认后才写库）-------------------
def _page_row_by_slug(kb_id: str, slug: str) -> dict | None:
    rows = psql_csv("SELECT slug, title, page_type FROM wiki_pages WHERE knowledge_base_id = %s "
                    "AND slug = %s AND deleted_at IS NULL LIMIT 1"
                    % (sql_str(kb_id), sql_str(slug)))
    return rows[0] if rows else None


def link_candidates(kb_id: str, source_slug: str, relation: str, candidates: list,
                    knowledge_id: str = "", round_no: int = 0,
                    kb_ids: list | None = None, confirm_kb_match: bool = False) -> dict:
    """登记**候选关联**（只登记，不写库）。用户确认后用 `resolve_link_candidate` 落地。"""
    kb_id, _note, block = resolve_write_kb(kb_id, kb_ids, confirm_kb_match)
    if block:
        return block
    src = _page_row_by_slug(kb_id, source_slug)
    if not src:
        raise RuntimeError("源页不存在：%s（先用 slug 或标题确认它已落库）" % source_slug)
    if not relation:
        raise RuntimeError("relation 必填（本体里的关系 prefixed 名，如 bmm:definedBy）")
    if not isinstance(candidates, list):
        raise RuntimeError("candidates 必须是数组：[{target_slug, similarity?, reason?}]")
    state = load_domain_state(kb_id, knowledge_id)
    added, skipped, reopened = [], [], []
    for item in candidates:
        if isinstance(item, str):
            item = {"target_slug": item}
        target = str(item.get("target_slug") or item.get("slug") or "").strip()
        if not target:
            continue
        tgt = _page_row_by_slug(kb_id, target)
        cid = hashlib.sha1(("%s|%s|%s|%s" % (source_slug, relation, target, knowledge_id))
                           .encode("utf-8")).hexdigest()[:12]
        existing = next((x for x in state.get("pending_links", []) if x.get("candidate_id") == cid),
                        None)
        if existing is not None and existing.get("status") == "pending":
            skipped.append({"candidate_id": cid, "target_slug": target, "why": "已在待确认队列"})
            continue
        if existing is not None:
            # 之前驳回过（或已确认过）：**复活为待确认**，否则同一对永远无法再提交确认
            # （candidate_id 是按 source|relation|target|doc 稳定哈希，不是每次新生成）
            existing.update({"status": "pending", "reopened_at": now_text(),
                             "similarity": item.get("similarity"),
                             "reason": str(item.get("reason") or existing.get("reason") or "")[:300],
                             "round_no": int(round_no or 0),
                             "target_exists": bool(tgt),
                             "target_title": (tgt or {}).get("title", ""),
                             "target_type": (tgt or {}).get("page_type", "")})
            existing.pop("resolved_at", None)
            reopened.append({"candidate_id": cid, "target_slug": target,
                             "prev_status": "rejected_or_confirmed"})
            added.append(existing)
            continue
        record = {"candidate_id": cid, "source_slug": source_slug, "source_title": src["title"],
                  "relation": relation, "target_slug": target,
                  "target_title": (tgt or {}).get("title", ""),
                  "target_exists": bool(tgt), "target_type": (tgt or {}).get("page_type", ""),
                  "similarity": item.get("similarity"), "reason": str(item.get("reason") or "")[:300],
                  "round_no": int(round_no or 0), "status": "pending", "created_at": now_text()}
        state.setdefault("pending_links", []).append(record)
        added.append(record)
    save_domain_state(kb_id, knowledge_id, state)
    return {"kb_id": kb_id, "knowledge_id": knowledge_id, "added": len(added), "skipped": skipped,
            "reopened": reopened,
            "items": added, "state_file": str(_state_path(kb_id, knowledge_id)),
            "note": ("这些**还没有写进领域模型**。把清单交给用户逐条确认后，再调 "
                     "`resolve_link_candidate(action=\"confirm\")`；`target_exists=false` 的先别确认。")}


def _iter_states(kb_id: str, knowledge_id: str = "") -> list[tuple[str, dict]]:
    if knowledge_id:
        return [(knowledge_id, load_domain_state(kb_id, knowledge_id))]
    base = DOMAIN_STATE_DIR / kb_id
    out = []
    if base.is_dir():
        for path in sorted(base.glob("*.json")):
            out.append((path.stem, load_domain_state(kb_id, path.stem)))
    return out


def list_link_candidates(kb_id: str, knowledge_id: str = "", status: str = "pending") -> dict:
    """列出候选关联（默认只看待确认的）。

    按 `candidate_id` **去重**（保留同 id 的**最后一条**）：早期版本在"驳回后重新登记"时会
    append 出重复记录，这里做兼容收敛，避免同一对出现两条。
    """
    kb_id, _note = resolve_kb_id(kb_id)
    latest: dict[str, dict] = {}
    for kid, state in _iter_states(kb_id, knowledge_id):
        for rec in state.get("pending_links", []):
            cid = rec.get("candidate_id") or ""
            if not cid:
                continue
            latest[cid] = {**rec, "knowledge_id": kid}
    items = [r for r in latest.values()
             if not status or status == "all" or r.get("status") == status]
    return {"kb_id": kb_id, "status": status, "count": len(items), "items": items}


def resolve_link_candidate(kb_id: str, candidate_id: str, action: str, knowledge_id: str = "",
                           kb_ids: list | None = None, confirm_kb_match: bool = False) -> dict:
    """裁决候选关联：`confirm` = 写进领域模型（真关系边）；`reject` = 丢弃。"""
    kb_id, _note, block = resolve_write_kb(kb_id, kb_ids, confirm_kb_match)
    if block:
        return block
    action = (action or "").strip().lower()
    if action not in ("confirm", "reject"):
        raise RuntimeError("action 只能是 confirm 或 reject")
    for kid, state in _iter_states(kb_id, knowledge_id):
        matches = [rec for rec in state.get("pending_links", [])
                   if rec.get("candidate_id") == candidate_id]
        if not matches:
            continue
        # 同 id 可能有多条（历史遗留）：**优先取待确认的**，否则取最后一条
        rec = next((x for x in matches if x.get("status") == "pending"), matches[-1])
        if rec.get("status") != "pending":
            return {"candidate_id": candidate_id, "status": rec.get("status"),
                    "note": "该候选已裁决过，未重复执行"}
        if action == "reject":
            rec.update({"status": "rejected", "resolved_at": now_text()})
            save_domain_state(kb_id, kid, state)
            return {"candidate_id": candidate_id, "status": "rejected",
                    "source_slug": rec.get("source_slug"), "target_slug": rec.get("target_slug")}
        if not rec.get("target_exists"):
            raise RuntimeError("目标页不存在，不能确认：%s" % rec.get("target_slug"))
        res = ke_pages.add_relation(kb_id, rec["source_slug"], rec["relation"], rec["target_slug"])
        version = ((res or {}).get("relations") or {}).get("version") or (res or {}).get("version")
        rec.update({"status": "confirmed", "resolved_at": now_text(), "page_version": version})
        save_domain_state(kb_id, kid, state)
        return {"candidate_id": candidate_id, "status": "confirmed",
                "source_slug": rec.get("source_slug"), "relation": rec.get("relation"),
                "target_slug": rec.get("target_slug"), "page_version": version,
                "apply": res}
    raise RuntimeError("找不到候选：%s（先用 list_link_candidates 看清单）" % candidate_id)


def adopt_session(kb_id: str, session: dict | None, summary: dict) -> dict | None:
    """把本轮落库结果记进会话索引：给新建页编**会话编号**并回写 `session` 段。"""
    if not session:
        return None
    knowledge_id = str(session.get("knowledge_id") or "")
    state = load_domain_state(kb_id, knowledge_id)
    budget = int(session.get("budget_tokens") or state.get("budget_tokens") or DEFAULT_ROUND_BUDGET)
    cursor = int(session.get("cursor") or 0)
    dry = bool(summary.get("dry_run"))
    state["budget_tokens"] = budget
    if session.get("doc_title"):
        state["doc_title"] = str(session["doc_title"])
    assigned = []
    no_next = int(state.get("next_no") or 1)
    for entry in summary.get("created") or []:
        no = no_next
        entry["no"] = no
        no_next += 1          # 草稿计数：dry_run 也递增（只用于回执，不落盘）
        assigned.append({"no": no, "title": entry.get("name"), "slug": entry.get("slug"),
                         "type": entry.get("type")})
        if not dry:
            state["next_no"] = no_next
            state.setdefault("pages", []).append(
                {"no": no, "title": entry.get("name"), "slug": entry.get("slug"),
                 "type": entry.get("type"), "round_no": int(session.get("round_no") or 0),
                 "at": now_text()})
    if not dry:
        state.setdefault("rounds", []).append(
            {"round_no": int(session.get("round_no") or (len(state.get("rounds", [])) + 1)),
             "cursor": cursor, "budget_tokens": budget, "created": len(assigned), "at": now_text()})
        if session.get("next_cursor") is not None:
            state["cursor"] = int(session["next_cursor"])
        save_domain_state(kb_id, knowledge_id, state)
    return {"knowledge_id": knowledge_id, "budget_tokens": budget, "cursor": cursor,
            "assigned_no": assigned, "pages_total": len(state.get("pages", [])),
            "next_no": int(state.get("next_no") or 1), "next_cursor": state.get("cursor"),
            "dry_run": dry, "state_file": str(_state_path(kb_id, knowledge_id))}


def squash(text: str) -> str:
    return "".join((text or "").split())


def locate_chunk(source_text: str, pool: list[dict]) -> tuple[str, int]:
    needle = squash(source_text)
    if needle:
        for chunk in pool:
            if needle in squash(chunk["content"]):
                return chunk["id"], chunk["chunk_index"]
    return "", -1


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
    """把 kb_id 参数解析成真实 UUID：支持 UUID / UUID 前缀 / 知识库名称（精确或包含）。

    统一委托给 `ke_db.resolve_kb_id`（2026-09-21）：那里的报错**带 id**（形如
    `企业知识（dbc2528f…）`），调用方（智能体）能照着纠正 —— 实测它会自己编 `b1` 这种串。
    """
    kb_id, _name, note = ke_db.resolve_kb_id(raw)
    return kb_id, note


def kb_choices() -> list[dict]:
    """可选知识库清单（给「需要选择 / 需要确认」的回执用）。"""
    return [{"id": r["id"], "name": r["name"], "pages": None} for r in psql_csv(
        "SELECT id, name FROM knowledge_bases WHERE deleted_at IS NULL ORDER BY updated_at DESC")]


def resolve_write_kb(kb_id: str = "", kb_ids: list | None = None,
                     confirm: bool = False) -> tuple[str, str, dict | None]:
    """**写库**的目标库解析：唯一确定 + 模糊需二次确认（2026-09-22 用户口径）。

    为什么不能沿用读路径的宽松解析：会话可能绑定**多个**知识库（`<bound_knowledge_bases>`），
    智能体若随手传个含糊名称，宽松解析会静默命中"另一个库" → 回执成功但用户在自己的库里看不到。
    因此写路径：

    | 情形 | 行为 |
    |---|---|
    | `kb_id` 精确（完整 uuid / **精确**库名） | 直接写 |
    | `kb_id` 模糊（uuid 前缀 / 名称包含）且唯一命中 | **必须二次确认**（`confirm_kb_match=true`） |
    | `kb_id` 未给 + 会话只绑 1 个库（`kb_ids`） | 用那个库 |
    | `kb_id` 未给 + 会话绑了多个库 | **拒绝写** → `need_kb_selection`（请用户指明） |
    | 命中 0 个 / 多个 / 占位符 | 拒绝写 → `kb_unresolved` + 可选清单 |

    返回 `(uuid, note, block)`；`block` 非 None 时**不要写库**，直接把它作为工具回执返回。
    """
    ids = [str(x).strip() for x in (kb_ids or []) if str(x).strip()]
    raw = (kb_id or "").strip()
    if not raw:
        if len(ids) == 1:
            raw = ids[0]
        elif len(ids) > 1:
            return "", "", {
                "kb_unresolved": "multi_kb_session", "need_kb_selection": True,
                "bound_kb_ids": ids, "candidates": kb_choices(),
                "how": ("本次会话绑定了 %d 个知识库：**写操作必须明确唯一的目标库**。"
                        "请让用户指明写入哪一个，然后带 `kb_id=<该库 uuid 或精确名>` 重跑；"
                        "只读操作（doc_outline / extract_state / audit_scan …）不受限制。" % len(ids))}
        else:
            return "", "", {
                "kb_unresolved": "no_kb", "need_kb_selection": True,
                "candidates": kb_choices(),
                "how": ("没给 `kb_id` 也没提供会话绑定的库清单（`kb_ids`）："
                        "请把 `<bound_knowledge_bases>` 里的 id 传进 `kb_ids`（多库时会要求用户指明），"
                        "或直接传 `kb_id`")}
    try:
        uid, name, mode = ke_db.resolve_kb_candidate(raw)
    except ValueError as exc:
        return "", "", {"kb_unresolved": "not_found", "need_kb_selection": True,
                        "asked": raw, "candidates": kb_choices(), "how": str(exc)}
    if mode == "fuzzy" and not confirm:
        return "", "", {
            "kb_unresolved": "fuzzy_match", "need_kb_confirm": True,
            "matched": {"kb_id": uid, "name": name,
                        "how": "按 uuid 前缀 / 名称包含匹配到唯一库"},
            "how": ("模糊匹配即使只命中 1 个也必须确认：请改传**完整 uuid 或精确库名**，"
                    "或带 `confirm_kb_match=true` 重跑（表示「就是它」）")}
    note = "" if mode == "exact" else ("（kb_id「%s」按模糊匹配解析为 %s，已二次确认）" % (raw, uid))
    return uid, note, None


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
    """两个本体类是否「同类族」——相等、同一继承链、或**同根**（如 APIService 与 MCPService 同属 Service）。

    为什么放宽到同根（2026-09-21 实测）：智能体第二次跑时把服务类型从 `APIService` 改成 `MCPService`，
    严格继承判定认为不同族 → 各建一套页（用户看到"2 套服务"）。同根判定既能合并这类兄弟类，
    又能挡住跨域误合并（如把 `bmm:Goal` 并进 `ea:Step`）。
    """
    if not a or not b:
        return False
    if a == b:
        return True
    try:
        chain_a = list(ke_ontology.ancestors(a))   # 由近及远，末位是根
        chain_b = list(ke_ontology.ancestors(b))
    except Exception:  # noqa: BLE001
        return False
    if a in chain_b or b in chain_a:
        return True
    return bool(chain_a) and bool(chain_b) and chain_a[-1] == chain_b[-1]


def _design_sections(element: dict) -> list[str]:
    """把设计载荷里的结构化字段渲染成 wiki 小节（服务页要能自解释：用途/输入输出/规范/属性/被引用）。

    字段约定（设计智能体按此给 JSON）：
      purpose(str) / inputs(list[str]) / outputs(list[str])
      assertions(list[{id, kind(N|E), assertion}])
      attributes(dict{本体属性名: 值})           # 数据属性（如 ai_skill）
      incoming(list[{source_title, type}])        # 由 design_elements 反推的入边
    """
    lines: list[str] = []
    purpose = (element.get("purpose") or "").strip()
    body_text = (element.get("definition") or "").strip()
    # 正文首段与「用途」是同一段时不再重复输出（2026-09-21 用户实测：服务页里同一段出现两遍）
    if purpose and squash(purpose) and squash(purpose) in squash(body_text):
        purpose = ""
    if purpose:
        lines += ["## 用途", "", purpose, ""]
    inputs = [str(x).strip() for x in (element.get("inputs") or []) if str(x).strip()]
    outputs = [str(x).strip() for x in (element.get("outputs") or []) if str(x).strip()]
    if inputs or outputs:
        lines += ["## 输入 / 输出", "", "| 方向 | 业务对象 |", "|---|---|"]
        lines += ["| 输入 | %s |" % x for x in inputs]
        lines += ["| 输出 | %s |" % x for x in outputs]
        lines.append("")
    assertions = element.get("assertions") or []
    if assertions:
        lines += ["## 设计规范（正常 / 异常案例 · ASSERTION）", "",
                  "| 编号 | 类型 | 断言 |", "|---|---|---|"]
        for a in assertions:
            kind = "正常" if str(a.get("kind", "N")).upper().startswith("N") else "异常"
            lines.append("| %s | %s | %s |" % (a.get("id", ""), kind, a.get("assertion", "")))
        lines.append("")
    attributes = element.get("attributes") or {}
    if attributes:
        # 本体当 schema：属性名（中文标签，range）由本体元数据渲染，不靠调用方排版
        known = ke_ontology.data_properties_for(element.get("type") or "")
        lines += ["## 属性（数据属性）", ""]
        for name, value in attributes.items():
            meta = known.get(name) or {}
            suffix = ""
            if meta:
                rng = ke_ontology.short_iri(meta.get("range_literal") or "")
                suffix = "（%s%s）" % (meta.get("label") or "", ("，%s" % rng) if rng else "")
            lines.append("- %s%s = %s" % (name, suffix, value))
        lines.append("")
    # 边限定属性（如 `easvc:crudKind` = CRUD 种类）：单独小节，**不动 `## 本体关系` 的行格式**
    # （那行的语法被 ke_pages 解析成 out_links/in_links，改语法会连带审计与反向边）。
    qualified = [r for r in (element.get("relations") or []) if r.get("properties")]
    if qualified:
        lines += [ke_pages.QUAL_SECTION, ""]
        # 同 (类型, 目标) 合并成一行（如 C 与 R 两条边 → `crudKind=C,R`），
        # 否则同一键会输出多行，人工与解析都难读（2026-09-21 实测）。
        merged_qual: dict = {}
        order: list = []
        for rel in qualified:
            key = (rel["type"], rel.get("target_slug") or rel.get("target") or "")
            if key not in merged_qual:
                merged_qual[key] = {"rel": rel, "props": {}}
                order.append(key)
            for k, v in (rel["properties"] or {}).items():
                old = (merged_qual[key]["props"].get(k) or "").strip()
                vals = [x for x in (old + "," + str(v)).strip(",").split(",") if x]
                merged_qual[key]["props"][k] = ",".join(sorted(set(vals)))
        for key in order:
            rel = merged_qual[key]["rel"]
            lines.append(ke_pages.rel_qualifier_line(
                rel.get("label") or rel.get("type"), rel.get("type"), rel.get("target") or "",
                rel.get("target_slug") or "", merged_qual[key]["props"]))
        lines.append("")
    incoming = element.get("incoming") or []
    if incoming:
        lines += ["## 被引用（入边）", "",
                  "> 本节由系统按本体关系**自动生成**（正文里不要手写引用链接）；要改请到来源页「本体关系」小节。",
                  ""]
        for edge in incoming:
            lines.append("- 「%s」（`%s`）→ 本页" % (edge.get("source_title", ""), edge.get("type", "")))
        lines.append("")
    return lines


# ---------------------------------------------------------------------------
# 技能上下文（2026-09-22）：同一套落库代码被三个技能复用，**文案必须跟着技能走**
#   domain_modeling          领域建模（从文档抽 → 节点应当有原文引用）
#   ea_overview_design       企架概要设计（设计生成 → 无原文片段）
#   service_detailed_design  服务详细设计（设计生成 → 无原文片段）
# 旧实现把"概要设计"写死 → 领域建模的页面上也写"概要设计…/无原文片段"（用户实测报过）。
# `context` 是**可选**参数：不传时保持旧口径（design = 概要设计），向后兼容。
# ---------------------------------------------------------------------------
SKILL_CONTEXTS = {
    "domain_modeling": {
        "label": "领域建模",
        "report_label": "领域建模报告",
        "report_category": "领域建模报告",
        "report_source": "领域建模报告（由领域建模智能体按文档分批生成、人工确认后落库）",
        "no_quote": "（领域建模：未提供原文引用 —— 请在该节点的 source_text 里给出原文片段）",
        "generated_by": "领域建模智能体（未指定来源文档）",
        "evidence_generated": "领域建模：无原文片段（请补 source_text）",
    },
    "ea_overview_design": {
        "label": "企架概要设计",
        "report_label": "概要设计报告",
        "report_category": "概要设计报告",
        "report_source": "概要设计报告（由企架概要设计智能体生成、人工确认后落库）",
        "no_quote": "（概要设计，无原文片段）",
        "generated_by": "企架概要设计智能体（未指定来源文档）",
        "evidence_generated": "概要设计生成，无原文片段",
    },
    "service_detailed_design": {
        "label": "服务详细设计",
        "report_label": "服务详细设计报告",
        "report_category": "服务详细设计报告",
        "report_source": "服务详细设计报告（由服务详细设计智能体生成、人工确认后落库）",
        "no_quote": "（详细设计，无原文片段）",
        "generated_by": "服务详细设计智能体（未指定来源文档）",
        "evidence_generated": "详细设计生成，无原文片段",
    },
    # 兼容旧调用（不传 context 时的历史口径）
    "design": {
        "label": "设计",
        "report_label": "概要设计报告",
        "report_category": "概要设计报告",
        "report_source": "概要设计报告（由设计智能体生成、人工确认后落库）",
        "no_quote": "（概要设计，无原文片段）",
        "generated_by": "设计智能体（未指定来源文档）",
        "evidence_generated": "设计生成，无原文片段",
    },
}


def context_meta(context: str = "") -> dict:
    """取技能上下文文案（未知/空 → `design`，保持旧行为）。"""
    key = (context or "").strip().lower()
    return {"id": key if key in SKILL_CONTEXTS else "design",
            **SKILL_CONTEXTS.get(key, SKILL_CONTEXTS["design"])}


def build_new_page(engine, model: dict, element: dict, chunk_id: str, chunk_index: int,
                   doc_meta: dict) -> dict:
    """新要素 -> 页面（与 ontology_wiki/weknora_sync 同风格：能被人读，也能被图谱用）。"""
    rels = element.get("relations") or []
    upstream = [s for s in (element.get("upstream") or []) if s]
    slug = element_page_slug(model, element)
    # 无来源文档（设计路径的常见情形）时**不要写"来源"字样**：正文称有来源而 `source_refs` 空会被
    # 巡检判为 C3（2026-09-21 实测：报告页 v5 命中 "（来源：…"）。改成"生成方式"表述。
    has_doc = bool(doc_meta.get("id"))
    head_lines = ["# %s（`%s`）" % (element["name"], element["type"]), "",
                  "> **本体类型**：%s（`%s`）  " % (element["type_label"], element["type"])]
    if has_doc:
        head_lines.append("> **来源**：《%s》%s  " % (
            doc_meta["title"], (" 片段 #%d" % chunk_index) if chunk_index >= 0 else ""))
    else:
        head_lines.append("> **生成方式**：%s  " % context_meta(doc_meta.get("context"))["generated_by"])
    head_lines.append("> **首个版本生成**：%s（`%s`）" % (now_text(), TOOL_TAG))
    head_lines += ["", (element.get("definition") or "（暂无定义）").strip(), ""]
    lines = head_lines
    if element.get("description"):
        lines += ["## 判定依据", "", element["description"].strip(), ""]
    lines += _design_sections(element)
    lines += ["## 原文依据", "",
              ("- %s（来源：《%s》%s）" % (element.get("source_text", "").strip(), doc_meta["title"],
                                         (" 片段 #%d" % chunk_index) if chunk_index >= 0 else ""))
              if doc_meta.get("id") else
              "- %s（%s）" % (element.get("source_text", "").strip(),
                              context_meta(doc_meta.get("context"))["evidence_generated"]), ""]
    if rels:
        lines += ["## 本体关系", ""]
        # 同 (类型, 目标) 只写一行：同一操作对同一属性可能有 C 与 R 两条边（crudKind 不同），
        # 而本小节的行不含 crudKind → 写两行会出现**完全相同的重复行**（巡检 A5 命中；
        # 2026-09-21 实测：`受理注册申请 → 手机号码` 的 C/R 两条边）。
        # crudKind 这类限定值统一在「## 关系限定（边属性）」小节里表达。
        seen_rel = set()
        for rel in rels:
            key = (rel["type"], rel.get("target_slug") or rel.get("target") or "")
            if key in seen_rel:
                continue
            seen_rel.add(key)
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
        # 分类路径：设计载荷可显式指定（如报告页用 `["概要设计报告"]`）；否则按本体类推导
        "category_path": element.get("category_path")
                          or class_category_path(element["type"], model["key"], model["label"],
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


# 由载荷推导出来的小节（渲染器每次重算）→ 合并时**替换**而不是"已存在就跳过"
DERIVED_SECTIONS = {
    "## 用途", "## 输入 / 输出", "## 设计规范（正常 / 异常案例 · ASSERTION）",
    "## 属性（数据属性）", "## 关系限定（边属性）", "## 被引用（入边）",
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
    # 报告页/显式声明「正文整体替换」的载荷：**一律以新正文为准**。
    # 旧启发式「取更长的一方」在改版后正文更短时会静默保留旧正文（用户 2026-09-21 实测：
    # 报告从 API 服务版改成 MCP 服务版，回执成功但正文一直没变）。
    chosen = new_def if (element.get("replace_body") or len(new_def) > len(old_def)) else old_def
    merged = head[:last_meta + 1] + ["", chosen, ""] + tail

    evidence = ("- %s（来源：《%s》%s）" % (
        element.get("source_text", "").strip(), doc_meta["title"],
        (" 片段 #%d" % chunk_index) if chunk_index >= 0 else "")) if doc_meta.get("id") \
        else ("- %s（%s）" % (element.get("source_text", "").strip(),
                             context_meta(doc_meta.get("context"))["evidence_generated"]))
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

    design_lines = _design_sections(element)
    added_sections = []
    if design_lines:
        sections, cur = [], None
        for ln in design_lines:
            if ln.startswith("## "):
                cur = [ln]
                sections.append(cur)
            elif cur is not None:
                cur.append(ln)
        have = {ln.strip() for ln in merged if ln.startswith("## ")}
        for sec in sections:
            title_sec = sec[0].strip()
            if title_sec in DERIVED_SECTIONS:
                # 派生小节（用途/输入输出/设计规范/属性/被引用/关系限定）→ **每次替换**：
                # 它们的内容完全由本次载荷推导，"已存在就跳过"会让改设计（补属性、改 CRUD）
                # 永远落不到已有页上（2026-09-21 实测：补 `easvc:operationRetryPolicy` 没写进去）。
                start = next((i for i, ln in enumerate(merged) if ln.strip() == title_sec), -1)
                if start >= 0:
                    end = next((i for i in range(start + 1, len(merged))
                                if merged[i].startswith("## ")), len(merged))
                    merged = merged[:start] + list(sec) + merged[end:]
                else:
                    merged += [""] + list(sec)
                added_sections.append(title_sec)
                continue
            if title_sec in have:
                continue
            merged += [""] + list(sec) + [""]
            added_sections.append(title_sec)

    post = {"definition_upgraded": bool(new_def) and len(new_def) > len(old_def),
            "evidence_added": added_evidence, "relations_added": added_relations,
            "upstream_added": added_upstream, "design_sections_added": added_sections}
    return "\n".join(merged).rstrip() + "\n", post


def union_list(old, new) -> list:
    return list(dict.fromkeys(list(old or []) + list(new or [])))


def sql_update_page(page: dict, content: str, summary: str, source_refs: list,
                    chunk_refs: list, metadata: dict) -> str:
    """合并 = 更新：先快照旧版本到 revisions（version 用旧值），再 version+1。

    2026-09-21 补 **`out_links` 重算**（用户实测：设计节点把关系行追加进正文后，前端本体关系面板
    与「入边」区都看不到，因为它按 `in_links` 渲染，而 `in_links` 是按别的页 `out_links` 反推的）：
    旧实现只有 `sql_insert_page`（新建页）写 `out_links`，走合并的页（第二次跑、追加关系）
    一直是空数组 → 反向边整片缺失。正文里关系行的解析与 ke_pages 完全同源（`out_links_of`）。
    """
    return ("INSERT INTO wiki_page_revisions (id, tenant_id, knowledge_base_id, page_id, slug, version, "
            "       title, page_type, status, content, summary, aliases, edit_source, editor_id, "
            "       edited_at, created_at)\n"
            "SELECT gen_random_uuid()::text, tenant_id, knowledge_base_id, id, slug, version, "
            "       title, page_type, status, content, summary, aliases, '%s', "
            "       COALESCE(last_editor_id,''), now(), now()\n"
            "  FROM wiki_pages WHERE knowledge_base_id = %s AND slug = %s AND deleted_at IS NULL;\n"
            "UPDATE wiki_pages SET content = %s, out_links = %s::jsonb, summary = %s, "
            "       source_refs = %s, chunk_refs = %s, "
            "       page_metadata = %s, version = version + 1, updated_at = now(), "
            "       last_edit_source = '%s' "
            " WHERE knowledge_base_id = %s AND slug = %s;\n"
            % (TOOL_TAG, sql_str(page["knowledge_base_id"]), sql_str(page["slug"]),
               sql_str(content), sql_json(ke_pages.out_links_of(content)), sql_str(summary),
               sql_json(source_refs), sql_json(chunk_refs),
               sql_json(metadata), TOOL_TAG, sql_str(page["knowledge_base_id"]),
               sql_str(page["slug"])))


def sql_insert_page(page: dict, kb_id: str, tenant_id: int,
                    strategies: list | None = None) -> str:
    """插入新页；id 由 `(kb_id, slug)` 决定（见 `_resolve_page_id`），本库已有同 slug 页时沿用其 id。

    历史背景
    --------
    2026-09-19 实测崩溃：page id 由 slug 派生（UUIDv5），而上游的重复检查只看内存里
    加载到的活页（deleted_at IS NULL）。用户删文档后旧页是**软删除**、id 仍占着，
    于是重复抽取会 INSERT 撞主键（wiki_pages_pkey）→ 整批事务回滚 → 工具报错。
    用 ON CONFLICT(id) DO UPDATE 一次解决：复活、覆盖内容、版本+1、标记来源。

    2026-09-24 实测（用户报「保存成功但知识全在别的知识库」）：id 只按 slug 派生 → 跨库撞主键，
    upsert 把**别的库那一行**更新了。现在 ① id 包含 kb ② upsert 加**同库守卫**
    （`WHERE wiki_pages.knowledge_base_id = EXCLUDED.knowledge_base_id`）——
    任何情况下都不会再更新别的知识库的行。

    `strategies`：可选出参列表，逐页记录 `{"slug","id","how"}`（how = existing/new/suffixed）。
    正常只有 existing/new；一旦出现 suffixed 说明走到了兜底分支（见 `_resolve_page_id`），
    回执里能看到、不用去翻库。巡检 D5 也盯这一项。
    """
    pid, how = _resolve_page_id(kb_id, page["slug"])
    if strategies is not None:
        strategies.append({"slug": page["slug"], "id": pid, "how": how})
    values = [
        sql_str(pid), str(tenant_id), sql_str(kb_id),
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
        # 2026-09-21：护栏从「活页一律跳过」改为「**活页只更新内容字段**」——
        #   跳过会让写入静默失效（用户实测 4 次：报告页 slug 相同 → 撞主键 → 回执成功但正文不变）。
        #   保留原意：**同 id 命中活页时不得改它的 title / page_type / status**（事故：设计节点把报告页
        #   覆盖成「注册信息登记服务 / bmm-ea-ext:APIService」），只有类型相同时才跟着改标题。
        "content = EXCLUDED.content, summary = EXCLUDED.summary, "
        "out_links = EXCLUDED.out_links, "
        "category_path = EXCLUDED.category_path, wiki_path = EXCLUDED.wiki_path, "
        "page_metadata = EXCLUDED.page_metadata, aliases = EXCLUDED.aliases, "
        "source_refs = EXCLUDED.source_refs, chunk_refs = EXCLUDED.chunk_refs, "
        "title = CASE WHEN wiki_pages.deleted_at IS NOT NULL "
        "                  OR wiki_pages.page_type = EXCLUDED.page_type "
        "             THEN EXCLUDED.title ELSE wiki_pages.title END, "
        "page_type = CASE WHEN wiki_pages.deleted_at IS NOT NULL "
        "                 THEN EXCLUDED.page_type ELSE wiki_pages.page_type END, "
        "status = wiki_pages.status, "
        "deleted_at = NULL, version = wiki_pages.version + 1, "
        "last_edit_source = %s, updated_at = now() "
        # 同库守卫（2026-09-24）：撞主键时若那一行属于**别的知识库**，DO UPDATE 不生效
        # （宁可写不进去也不能改写别库；正常情况下 `_resolve_page_id` 已保证 id 属本库）
        "WHERE wiki_pages.knowledge_base_id = EXCLUDED.knowledge_base_id" % sql_str(TOOL_TAG))
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

    这段逻辑原先内联在**已退役的整篇抽取工具**里（2026-09-20 原样抽出，行为逐字保留；
    抽取工具 2026-09-22 退役，本函数现由设计/建模路径 `save_knowledge` 使用）：
    - 相似度 ≥ high：合并进存量页（定义取更完整、追加证据、关系去重、写入 merge_history）；
    - 相似度 ≤ low 或无候选：新建页；
    - 两者之间：生成「待确认合并」页，交人工裁决（`resolve_pending_merge`）。
    """
    tenant = tenant_id if tenant_id is not None else get_kb_tenant(kb_id)
    pages = fetch_existing_pages(kb_id)
    # 跨库引用护栏（2026-09-22）：关系目标只允许指本库的页（本库已有 + 本次要建的）。
    # 旧实现会因 `_graph_target_slug` 全库查而把**别的知识库**的 slug 写进本库页 → 回执成功但本库没记录。
    known_slugs = {p["slug"] for p in pages} | {p.get("slug") for p in payloads if p.get("slug")}
    dropped_relations: list = []
    # 副本写保护（2026-09-29 用户口径）：目标页若是**权威副本** → 拒写，回执给"从权威复制"的指引
    if payloads:
        kept_payloads: list = []
        for payload in payloads:
            try:
                ke_pages.replica_guard(kb_id, str(payload.get("slug") or ""), TOOL_TAG)
                kept_payloads.append(payload)
            except ValueError as exc:
                dropped_relations.append({
                    "source": payload.get("name"), "target_slug": payload.get("slug"),
                    "reason": str(exc), "action_required": "authority_pull"})
        payloads = kept_payloads
    for payload in payloads:
        kept = []
        for rel in payload.get("relations") or []:
            ts = str(rel.get("target_slug") or "").strip()
            if ts and ts not in known_slugs:
                dropped_relations.append({
                    "source": payload.get("name"), "relation": rel.get("type"),
                    "target": rel.get("target"), "target_slug": ts,
                    "reason": "目标 slug 不属于本知识库（跨库引用已丢弃；请在本库先建该节点）"})
                continue
            kept.append(rel)
        payload["relations"] = kept
    statements: list[str] = []
    # id 策略留痕（2026-09-24 加固）：逐页记 existing/new/suffixed，最后进回执 `id_strategy`。
    # 正常情况下只有 existing/new；出现 suffixed 立即在日志里告警（兜底分支按设计几乎不可达）。
    id_strategies: list[dict] = []
    summary = {
        "model": model["key"], "kb_id": kb_id, "knowledge_id": knowledge_id,
        "resolved_note": (resolved_note or "").strip(), "doc_title": doc_meta.get("title", ""),
        "elements": len(payloads), "relationships": len(checked.get("edges") or []),
        "created": [], "merged": [], "pending": [],
        "violations": checked["violations"], "unmatched": checked["unmatched"],
        "dry_run": dry_run, "thresholds": {"high": high, "low": low},
        "dropped_relations": dropped_relations,
        "generated_at": now_text(),
    }
    if extra:
        summary.update(extra)
    # 写库自证（2026-09-21 用户实测：`mode` 忘了传 → 默认 dry_run 只算不写，回执里只有一行
    # `dry_run: true`，极易被当成"已落库"，于是反复出现"回执成功但 wiki 没变"）：
    # 顶层给 `applied`（是否真写库）与 `write_note`，并附上被更新页的 version 变化。
    summary["applied"] = not dry_run
    if dry_run:
        summary["write_note"] = ("**未写库**（dry_run）：本回执只是预览。要真正落库请用**同一份载荷**、"
                                 "`mode=\"apply\"` 重跑；`created/merged/pending` 里的 slug 在 apply 时才生效。")
    summary["page_versions"] = []

    retagged: list = []
    retag_queue: list = []
    for element in payloads:
        # 幂等修正（2026-09-21）：**slug 完全相同**即同一要素（slug=模块/类/名称哈希），直接走合并；
        # 否则旧行为会因「同名同类的页已存在」而另建一个带哈希后缀的重复页。
        exact = next((p for p in pages if p["slug"] == element_page_slug(model, element)), None)
        if exact is not None:
            sim, candidate = 1.0, exact
        else:
            # 同类型守卫（设计路径开启）：只在同一本体类族（相等、同一继承链，或**同根**）的页之间做
            # 相似度合并，避免「设计节点并进报告页/需求页」这类跨类误合并（2026-09-21 沙箱实测事故）。
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
            summary["page_versions"].append({"slug": candidate["slug"],
                                             "before": candidate.get("version"),
                                             "action": "merged（正文已按载荷更新）",
                                             "applied": not dry_run})
            # 类型变更（retag，2026-09-27 用户口径）：**不再静默改类型** —— 改类型 = 迁移 slug +
            # 联动引用，必须走两段式（preview → 用户确认 → apply）。这里只登记"需要迁移"的清单，
            # 回执里给出 preview 调用方式，由人确认后执行（避免智能体绕过风险确认）。
            want_type = element["type"]
            if element.get("retag") and candidate.get("page_type") \
                    and candidate["page_type"] != want_type \
                    and same_type_family(candidate["page_type"], want_type):
                retag_queue.append({"slug": candidate["slug"], "from": candidate["page_type"],
                                    "to": want_type, "name": element["name"], "similarity": sim})
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
            # 回执如实（2026-09-21 用户实测：`fetch_existing_pages` 排除 summary/index 页，报告页会走
            # 到这里；若不说清是「覆盖既有活页」，就会出现"回执说 created，用户看到的是旧正文"的错觉）。
            prior = psql_csv("SELECT version, (deleted_at IS NOT NULL) AS dead FROM wiki_pages "
                             "WHERE knowledge_base_id = %s AND slug = %s"
                             % (sql_str(kb_id), sql_str(page["slug"])))
            if prior:
                if str(prior[0].get("dead", "f")).lower().startswith("t"):
                    action, before_v = "revived（复活软删旧行并覆盖）", int(prior[0]["version"] or 1)
                else:
                    action, before_v = "updated（同 slug 既有活页：正文按本次载荷覆盖，title/type 不动）", \
                                       int(prior[0]["version"] or 1)
            else:
                action, before_v = "created", 0
            if action == "created":
                statements.append(sql_insert_page(page, kb_id, tenant, strategies=id_strategies))
                summary["created"].append({"name": element["name"], "type": element["type"],
                                           "slug": page["slug"]})
            else:
                # 既有活页（典型：`summary` 报告页被 fetch_existing_pages 排除在合并候选之外）：
                # 用**本次渲染的整页**覆盖内容 —— 报告页是"生成物"，整体替换可避免旧正文里的过期小节、
                # 旧措辞（如"（来源：《…》）"）残留；title / page_type / created_at 一律不动。
                statements.append(sql_update_page({**page, "knowledge_base_id": kb_id},
                                                  page["content"], page["summary"],
                                                  page["source_refs"], page["chunk_refs"],
                                                  page["page_metadata"]))
                summary["merged"].append({"name": element["name"], "type": element["type"],
                                          "into": page["slug"], "similarity": 1.0, "note": action})
            summary["page_versions"].append({"slug": page["slug"], "before": before_v,
                                             "action": action, "applied": not dry_run})
            pages.append({**page, "version": 1, "knowledge_base_id": kb_id,
                          "status": "published", "in_links": [], "title": page["title"],
                          "summary": page["summary"], "aliases": page["aliases"],
                          "source_refs": page["source_refs"], "chunk_refs": page["chunk_refs"]})
        else:
            page = build_pending_page(model, element, candidate, sim, element["chunk_id"],
                                      element["chunk_index"], doc_meta, high, low)
            statements.append(sql_insert_page(page, kb_id, tenant, strategies=id_strategies))
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
        # 写后对账（2026-09-24）：回执里的每个 slug 必须**在本库**查得到 ——
        # 否则就是"回执成功、库里没有"（历史 bug：页 id 只按 slug 派生 → upsert 更新了别库那一行）。
        # 这里把它变成**显式的失败**：`applied` 回拨为 False + missing 清单，提示不要向用户汇报成功。
        wanted = [e.get("slug") for e in summary["created"]] \
            + [e.get("into") for e in summary["merged"]] \
            + [e.get("pending_slug") for e in summary["pending"]]
        wanted = [s for s in wanted if s]
        if wanted:
            rows = psql_csv("SELECT slug FROM wiki_pages WHERE knowledge_base_id = %s "
                            "AND deleted_at IS NULL AND slug IN (%s)"
                            % (sql_str(kb_id), ", ".join(sql_str(s) for s in wanted)))
            got = {r["slug"] for r in rows}
            missing = [s for s in wanted if s not in got]
            summary["write_check"] = {"kb_id": kb_id, "expected": len(wanted),
                                      "verified": len(got), "missing": missing}
            if missing:
                summary["applied"] = False
                summary["write_check"]["note"] = (
                    "这些 slug 未在本库落库 —— **不要向用户汇报成功**；请重跑本批或按清单排查"
                    "（历史原因：跨知识库同 slug 撞主键，旧实现会更新到别的库）")
        # 类型变更（retag）：**不在这里执行**（2026-09-27 用户口径：必须走两段式）。
        # 跳过执行，只把"需要迁移的类型"整理成 retag_required，回执里带 preview 调用方式。
        for item in retag_queue:
            try:
                prev = ke_pages.retag_preview(kb_id, item["slug"], item["to"])
                retagged.append({**item, "ok": None, "status": "需要两段式确认",
                                 "new_slug": (prev.get("slug_change") or {}).get("to", ""),
                                 "refs": (prev.get("refs") or {}).get("total", 0),
                                 "risks": prev.get("required_risks") or [],
                                 "ticket": prev.get("ticket", ""),
                                 "preview_http": "POST /bodhi/page/retag/preview",
                                 "apply_http": "POST /bodhi/page/retag/apply"})
            except Exception as exc:  # noqa: BLE001
                retagged.append({**item, "ok": False, "error": str(exc)[:160]})
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
        # 关系撤回（retract）：设计变更要能"减边"，否则旧边留在页面上、巡检跟着失真
        try:
            retract_list = checked.get("retract_edges") or []
            if retract_list:
                summary["retract"] = retract_relations(kb_id, retract_list)
        except Exception as exc:  # noqa: BLE001
            summary["retract"] = {"retracted": [], "missed": [], "error": str(exc)[:160]}
        # 服务详细设计：刷新「服务页的 CRUD 矩阵」（跨页聚合，必须**写库之后**再读才拿得到）
        try:
            svc_types = service_types()
            targets = []
            for entry in summary["created"] + summary["merged"]:
                slug = entry.get("slug") or entry.get("into")
                if slug and entry.get("type") in svc_types:
                    targets.append(slug)
            if targets:
                summary["crud_matrix"] = refresh_crud_matrix(kb_id, targets)
        except Exception as exc:  # noqa: BLE001
            summary["crud_matrix"] = "failed: %s" % exc
    # 类型变更：不再是"已改好"，而是"需要两段式确认"（2026-09-27 用户口径）
    summary["retag_required"] = retagged or [dict(x, ok=None, note="dry_run 未执行")
                                             for x in retag_queue]
    if retagged:
        summary["retag_note"] = (
            "改本体类型 = **迁移 slug + 联动引用**，必须两段式：先 `POST /bodhi/page/retag/preview` "
            "（只读：新 slug / 引用清单 / 会话命中 / 预计违规 + ticket）→ 用户确认后 "
            "`POST /bodhi/page/retag/apply`（带 ticket + acknowledge_risks）。"
            "**本次未改任何类型**（避免绕过风险确认）。")
    if id_strategies:
        # id 策略回执（2026-09-24 加固）：existing=沿用本库既有页 id（老数据/幂等），new=按 (kb, slug)
        # 新建；正常只有这两种，出现其它值会额外给 `id_notes`（兜底分支信号，巡检 D5 独立复核）。
        summary["id_strategy"] = {
            "existing": len([s for s in id_strategies if s.get("how") == "existing"]),
            "new": len([s for s in id_strategies if s.get("how") == "new"]),
        }
        odd = _warn_noncanonical_ids(id_strategies, subject="kb=%s" % kb_id[:8])
        if odd:
            summary["id_strategy"]["odd"] = odd
            summary["id_notes"] = ("以下页 id 未按规范派生（走了兜底后缀）：%s —— 查询/更新不受影响"
                                   "（一律按 (kb, slug)），但请核对是否有脚本直接写 wiki_pages"
                                   % "、".join(str(s.get("slug")) for s in odd[:10]))
    if dry_run:
        # 撤回是"减边"，dry_run 下不执行，但必须让调用方看到**将要撤回什么**（否则预览不完整）
        planned = checked.get("retract_edges") or []
        if planned:
            summary["retract_planned"] = [{"source": x.get("source"), "type": x.get("type"),
                                           "target": x.get("target"), "slug": x.get("slug")}
                                          for x in planned]
    return summary


def _graph_target_slug(kb_id: str, name: str) -> str:
    """在**本库**已落库的页面里按标题找 slug（设计节点引用需求 wiki 页时用）。

    2026-09-22 修（用户实测"保存成功但自己库里没有记录"）：旧实现没有
    `knowledge_base_id` 过滤 → 同名节点会命中**别的知识库**的页，该 slug 被写进本库页的
    关系行/out_links，于是"回执成功、本库却没有那个节点"。跨库一律不认。
    """
    if not name:
        return ""
    rows = psql_csv("SELECT slug FROM wiki_pages WHERE knowledge_base_id = %s "
                    "AND deleted_at IS NULL AND title = %s LIMIT 1" % (sql_str(kb_id), sql_str(name)))
    return rows[0]["slug"] if rows else ""


def _graph_target_type(kb_id: str, name: str) -> str:
    rows = psql_csv("SELECT COALESCE(page_type,'') AS t FROM wiki_pages WHERE knowledge_base_id = %s "
                    "AND deleted_at IS NULL AND title = %s LIMIT 1" % (sql_str(kb_id), sql_str(name)))
    return rows[0]["t"] if rows else ""


def _other_kb_same_name(kb_id: str, names: list[str]) -> list[dict]:
    """同名页**只存在于别的知识库**时的清单（只读回报，用于解释"为什么本库新建/为什么不算命中"）。"""
    names = [n for n in {str(x).strip() for x in names} if n]
    if not names:
        return []
    lst = ", ".join(sql_str(n) for n in names)
    rows = psql_csv(
        "SELECT p.title, p.slug, p.knowledge_base_id AS kb, k.name AS kb_name "
        "FROM wiki_pages p LEFT JOIN knowledge_bases k ON k.id = p.knowledge_base_id "
        "WHERE p.deleted_at IS NULL AND p.knowledge_base_id <> %s AND p.title IN (%s) "
        "ORDER BY p.title" % (sql_str(kb_id), lst))
    return [{"name": r["title"], "other_kb_id": r["kb"], "other_kb_name": r["kb_name"] or "",
             "slug": r["slug"]} for r in rows]


def design_elements(model_key: str, model: dict, nodes: list, edges: list, doc_meta: dict,
                    upstream: list | None = None, kb_id: str = "",
                    context: str = "") -> tuple[list, dict]:
    """把**设计报告/建模结果**里的节点/关系转成「要素载荷」（与抽取路径同形），并做本体合规校验。

    - 节点：`{name, type, definition, description, source_text?, aliases?}` → 一页；
    - 关系：`{source, type, target, label?}` → 写进 source 页的 `## 本体关系`；
    - 校验：类必须在本体里（`ke_ontology.class_meta`），关系的 range 闭包必须包含目标页类型；
      不合规的进 `violations`（与抽取路径同一口径：违规不入库，回报给调用方）；
    - `upstream`：上游页 slug（需求页/报告页），写进 `page_metadata.design.upstream` 与正文 `## 溯源`；
    - `kb_id`（2026-09-22 新增，**必传才能跨页找目标**）：目标解析只认**本库**的页 —— 同名但属于
      别的知识库的页一律不认（旧实现全库按标题找 → 关系写到别的库的 slug，回执成功而本库没记录）；
      这类"只在别库同名"的会在 `cross_kb_same_name` 里回报，便于向用户解释。
    - `context`（2026-09-22 新增）：技能上下文（`domain_modeling` / `ea_overview_design` /
      `service_detailed_design`），决定占位文案（如缺 `source_text` 时写哪句）。
    """
    meta = ke_ontology.class_meta()
    ctx = context_meta(context)
    violations: list = []
    slug_by_name: dict = {}
    unresolved: list[str] = []          # 关系目标既不在本次节点、也不在本库 → 待做跨库同名回报
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
        # 通用校验（本体当 schema）：节点 attributes 的键必须在本体里声明为该类（或祖先）的数据属性。
        # **不拦写入**（blocking=False）：历史数据/新模块可能还没把数据属性补进本体，
        # 这里只回报，让"扩展本体"有明确信号；要长期使用就补 `ontology/*.ttl` 或走上传导入。
        allowed_attrs = ke_ontology.data_properties_for(cls)
        for attr_name in (node.get("attributes") or {}):
            if attr_name not in allowed_attrs:
                violations.append({"kind": "attribute", "name": name, "type": cls,
                                   "attribute": attr_name, "blocking": False,
                                   "reason": "本体里没有为该类声明这个数据属性（该模块可用：%s）"
                                             % ("、".join(sorted(allowed_attrs)[:8]) or "无")})
        slug_by_name[name] = element_slug(node["_module"],
                                         {"name": name, "type": cls})

    outgoing: dict = {}
    retract_edges: list = []
    for edge in edges:
        rel_type = (edge.get("type") or "").strip()
        src = (edge.get("source") or "").strip()
        dst = (edge.get("target") or "").strip()
        # 关系撤回（retract）放在**本体校验之前**：撤回可能针对已被废弃的关系类型/目标，
        # 这时不该因"本体里没有这个对象属性"而被拒（否则改设计永远减不掉旧边）。
        if edge.get("retract"):
            dst_slug = slug_by_name.get(dst) or _graph_target_slug(kb_id, dst) or dst
            retract_edges.append({"source": src, "type": rel_type, "target": dst, "slug": dst_slug})
            continue
        closure = ke_ontology.target_closure(rel_type)
        if not closure:
            violations.append({"kind": "edge", "source": src, "type": rel_type, "target": dst,
                               "reason": "本体里没有这个对象属性"})
            continue
        dst_type = next((n.get("type") for n in nodes if (n.get("name") or "").strip() == dst), "")
        dst_slug = slug_by_name.get(dst, "")
        if not dst_slug:
            dst_slug, dst_type = _graph_target_slug(kb_id, dst), (dst_type or _graph_target_type(kb_id, dst))
        if not dst_slug:
            unresolved.append(dst)
            violations.append({"kind": "edge", "source": src, "type": rel_type, "target": dst,
                               "reason": "目标解析不到页面（既不在本次节点里，也**不在本库**）"})
            continue
        if dst_type and dst_type not in closure:
            violations.append({"kind": "edge", "source": src, "type": rel_type, "target": dst,
                               "reason": "目标页类型 %s 不在 `%s` 的 range 内（%s）"
                                         % (dst_type, rel_type, "、".join(closure[:6]))})
            continue
        # ---- 关系撤回（retract）见循环开头（已收集进 retract_edges）
        outgoing.setdefault(src, []).append({"type": rel_type, "label": edge.get("label", ""),
                                             "target": dst, "target_slug": dst_slug,
                                             "properties": dict(edge.get("properties") or {}),
                                             "source_text": edge.get("source_text", "")})
        # 边限定属性（如 `easvc:crudKind`）：本体当 schema —— 允许的是**源类**（或祖先）声明的数据属性。
        props = dict(edge.get("properties") or {})
        if props:
            src_type = next((n.get("type") for n in nodes
                             if (n.get("name") or "").strip() == src), "") or _graph_target_type(kb_id, src)
            allowed_src = ke_ontology.data_properties_for(src_type)
            for prop_name in props:
                if prop_name not in allowed_src:
                    violations.append({"kind": "edge-property", "source": src, "type": rel_type,
                                       "property": prop_name, "blocking": False,
                                       "reason": "本体里没有为源类 %s 声明这个数据属性（可用：%s）"
                                                 % (src_type or "?", "、".join(sorted(allowed_src)[:8]) or "无")})

    incoming_map: dict = {}
    for edge in edges:
        rel_type = (edge.get("type") or "").strip()
        src = (edge.get("source") or "").strip()
        dst = (edge.get("target") or "").strip()
        if dst and src and rel_type:
            incoming_map.setdefault(dst, []).append({"source_title": src, "type": rel_type})

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
            # 概要设计字段（服务页要自解释：用途/输入输出/ASSERTION 规范/数据属性/被引用入边）
            "purpose": node.get("purpose") or node.get("definition") or "",
            "inputs": node.get("inputs") or [],
            "outputs": node.get("outputs") or [],
            "assertions": node.get("assertions") or [],
            "attributes": node.get("attributes") or {},
            "retag": bool(node.get("retag")),   # true = 合并进既有页时**同时把该页类型改成 element 的类型**
            "incoming": incoming_map.get(name, []),
            "source_text": node.get("source_text") or ctx["no_quote"],
            "chunk_id": "", "chunk_index": -1, "relations": rels,
            "upstream": upstream, "aliases": node.get("aliases") or [], "context": ctx["id"],
        })
    cross_kb = _other_kb_same_name(kb_id, unresolved)
    return payloads, {"edges": accepted, "violations": violations, "unmatched": [],
                      "retract_edges": retract_edges,
                      "cross_kb_same_name": cross_kb,
                      "note": ("目标不在本库、但**同名页存在于其它知识库**：这些目标按"
                               "「本库没有」处理（不跨库合并、不入库）；要连到别库的页，"
                               "请先在本库建立对应节点。") if cross_kb else ""}


def save_knowledge(kb_id: str = "", *, stage: str = "report", model: str = "ea",
                   report: dict | None = None, nodes: list | None = None, edges: list | None = None,
                   mode: str = "dry_run", confirmed_new_applications: list | None = None,
                   high: float = DEFAULT_HIGH, low: float = DEFAULT_LOW,
                   session: dict | None = None, kb_ids: list | None = None,
                   confirm_kb_match: bool = False, context: str = "") -> dict:
    """**设计落库（不调 LLM）**：两段式，复用抽取路径的 `save_elements`（相似度合并/待确认/版本/目录）。

    - `stage="report"`：把**概要设计报告 md** 整篇写成 wiki 页（索引页/父页）。
      报告正文放在「定义」位置（所以正文完整保留），来源按方案 C 记到需求文档（`source_refs`），
      上游页写进正文 `## 溯源` 与 `page_metadata.design.upstream`。
    - `stage="graph"`：把报告**细分**出的节点/关系写成 wiki 页 + 本体关系（每个 IT 服务/应用系统一页）。
      节点页通过 `report.slug` 挂到报告页下（`parent_slug`），形成「报告页 → 细分页」的层级，
      与报告正文一一对应（图谱内容 = 设计 wiki 的细分）。
    - 合规：类必须在本体里、关系的 range 闭包必须包含目标页类型，违规进 `violations`（不入库）；
    - 确认：新建「应用/系统」类节点（`bmm:MainSystem` / `bmm:SubSystem` / `bmm:HardwareAsset`；兼容旧的
      `ea:Application` / `ITAsset`）
      **必须**在 `confirmed_new_applications` 里列出，否则只在 dry_run 清单里回报；
    - 幂等：同标题（同 slug）重跑 = 合并更新，不重复建页。
    """
    kb_id, kb_note, block = resolve_write_kb(kb_id, kb_ids, confirm_kb_match)
    if block:
        return block
    ctx = context_meta(context)
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

    doc_meta = {"id": "", "title": (report or {}).get("source_document_title") or "（无来源文档）",
                "context": ctx["id"]}
    if (report or {}).get("source_document_id"):
        doc_meta["id"] = resolve_knowledge_id(kb_id, str(report["source_document_id"]))[0]
    elif (report or {}).get("source_document_title"):
        # 只给了标题也要解析出 id：否则正文写着「来源：《<需求文档>》」而 `source_refs` 为空，
        # 巡检会判 C3（2026-09-21 实测：报告页 v5 命中；设计页的 source_refs 还靠它继承）。
        try:
            doc_meta["id"] = resolve_knowledge_id(kb_id, str(report["source_document_title"]))[0]
        except Exception:  # noqa: BLE001  解析不到就保持空（渲染会写"生成方式"而不是"来源"）
            doc_meta["id"] = ""
        if not doc_meta["id"]:
            # 解析不到时不要把"来源"字样留在正文里（与 source_refs 空自相矛盾 → C3）
            doc_meta["title"] = "（无来源文档）"
    if not doc_meta["id"]:
        # 设计页没有源文片段，来源按方案 C 记到**需求文档**；调用方没给文档时，
        # 从 `report.slug`（报告页）**或 `report.upstream` 里的上游页**继承 source_refs
        # —— 否则巡检 C1 会把设计页判成"无来源"（2026-09-21 实测：详设新建的属性/操作页命中 8 条 C1）。
        cands = [((report or {}).get("slug") or "").strip()]
        cands += [str(s).strip() for s in ((report or {}).get("upstream") or [])]
        cands = [c for c in cands if c]
        if cands:
            lst = ", ".join(sql_str(c) for c in cands)
            rows = psql_csv(
                "SELECT slug, COALESCE(source_refs::text,'[]') AS refs FROM wiki_pages "
                "WHERE knowledge_base_id = %s AND slug IN (%s) AND deleted_at IS NULL "
                "ORDER BY array_position(ARRAY[%s]::text[], slug)"
                % (sql_str(kb_id), lst, lst))
            for row in rows:
                try:
                    refs = json.loads(row["refs"] or "[]")
                except Exception:  # noqa: BLE001
                    refs = []
                if refs:
                    doc_meta["id"] = refs[0]
                    doc_meta["title"] = doc_meta["title"] or "（继承自上游页/报告页的来源文档）"
                    break

    if stage == "report":
        title = ((report or {}).get("title") or "").strip()
        body = ((report or {}).get("content_md") or "").strip()
        if not title or not body:
            raise ValueError("stage=report 需要 report.title 与 report.content_md")
        slug = ((report or {}).get("slug") or "").strip()
        if not slug:
            # 幂等（2026-09-21 用户实测：第二次跑把标题改成「…V2 需求澄清版」→ 又建一页，太乱）：
            # 同一需求文档 / 同一上游页 的既有**报告页**直接复用它的 slug（= 更新，不新建）。
            conds = []
            if doc_meta.get("id"):
                conds.append("source_refs @> %s::jsonb" % sql_json([doc_meta["id"]]))
            ups = [s for s in ((report or {}).get("upstream") or []) if s]
            if ups:
                conds.append("page_metadata->'design'->'upstream' @> %s::jsonb" % sql_json(ups[:1]))
            if conds:
                rows = psql_csv(
                    "SELECT slug FROM wiki_pages WHERE knowledge_base_id = %s AND deleted_at IS NULL "
                    "AND COALESCE(page_type,'') = 'summary' AND (%s) "
                    "ORDER BY updated_at DESC LIMIT 1" % (sql_str(kb_id), " OR ".join(conds)))
                if rows:
                    slug = rows[0]["slug"]
            if not slug and title:
                # 同标题的既有报告页也要复用（用户实测：不带 doc/upstream 时，同标题重跑必须更新同一页，
                # 否则 slug 相同 → 撞主键 → 只有靠 sql_insert_page 的「活页更新」兜底，回执口径也不清楚）。
                rows = psql_csv(
                    "SELECT slug FROM wiki_pages WHERE knowledge_base_id = %s AND deleted_at IS NULL "
                    "AND COALESCE(page_type,'') = 'summary' AND title = %s "
                    "ORDER BY updated_at DESC LIMIT 1" % (sql_str(kb_id), sql_str(title)))
                if rows:
                    slug = rows[0]["slug"]
        element = {"name": title, "type": (report or {}).get("page_type") or "summary",
                   "type_label": ctx["report_label"], "module": model, "slug": slug,
                   "category_path": (report or {}).get("category_path") or [ctx["report_category"]],
                   "definition": body, "description": "",
                   "source_text": ctx["report_source"],
                   "chunk_id": "", "chunk_index": -1, "relations": [],
                   "upstream": [s for s in ((report or {}).get("upstream") or []) if s],
                   "aliases": (report or {}).get("aliases") or [],
                   # 报告页复用时**正文整体替换**（改版后正文更短也必须换掉旧版，别走"取更长"启发式）
                   "replace_body": True}
        checked = {"edges": [], "violations": [], "unmatched": []}
        summary = save_elements(kb_id, model_obj, checked, [element], doc_meta, high=high, low=low,
                                dry_run=(mode != "apply"), tenant_id=tenant_id,
                                resolved_note=kb_note, engine=engine, same_type_only=True)
        summary["stage"] = "report"
        summary["context"] = ctx["id"]
        summary["report_page"] = {"slug": slug, "title": title, "chars": len(body),
                                  "type_label": ctx["report_label"],
                                  "category_path": (element.get("category_path")
                                                    or [ctx["report_category"]])}
        if summary.get("merged"):
            summary["report_page"]["action"] = "updated"
        elif summary.get("pending"):
            summary["report_page"]["action"] = "pending"
        else:
            summary["report_page"]["action"] = "created"
        return summary

    payloads, checked = design_elements(
        model, model_obj, nodes or [], edges or [], doc_meta,
        upstream=[s for s in ((report or {}).get("upstream") or []) if s],
        kb_id=kb_id, context=ctx["id"])
    app_types = ("bmm:MainSystem", "bmm:SubSystem", "bmm:HardwareAsset", "bmm:ITAsset",
                 # 兼容存量页（EA 瘦身前建的 6 个 ea:Application 页；用户口径：本期不动）
                 "bmm-ea-ext:Application", "ea:Application", "bmm-ea-ext:ITAsset", "ea:ITAsset")
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
    summary["context"] = ctx["id"]
    # 跨库同名（只读回报）：目标不在本库、但同名页在别的知识库 → 让用户/智能体一眼看到"没跨库合并"
    summary["cross_kb_same_name"] = checked.get("cross_kb_same_name") or []
    if checked.get("cross_kb_same_name"):
        summary["cross_kb_note"] = checked.get("note")
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
    # 领域建模 v2：把本轮结果记进**会话索引**（新建页编会话编号；dry_run 只预览不落盘）
    try:
        adopted = adopt_session(kb_id, session, summary)
        if adopted:
            summary["session"] = adopted
    except Exception as exc:  # noqa: BLE001
        summary["session"] = {"error": str(exc)[:200]}
    return summary


# ---------------------------------------------------------------------------
# 技能（skills/）：目录 + 指令 + 该技能的本体面
# ---------------------------------------------------------------------------
# 为什么由 MCP 承载技能而不是塞进智能体提示词：
#   ① 省 token（智能体先看目录，用到哪个取哪个）；② 技能改了不用重新注册智能体；
#   ③ 未来若开 WeKnora 沙箱技能（tenant_skills/bundle），同一份 SKILL.md 可直接打成 bundle。
# 本次部署的 WeKnora 是**沙箱安装型**技能（tenant_sandbox_configs 为空、SANDBOX_DOCKER=false），
# 原生技能不可用 —— 所以由 MCP 提供等价能力（见 docs/agent-design-flow.md §11）。
SKILLS_DIR = REPO / "skills"
_SKILLS_CACHE: dict | None = None
_SKILLS_STAMP: tuple | None = None


def _skills_stamp() -> tuple:
    try:
        return tuple(sorted((p.parent.name, p.stat().st_mtime, p.stat().st_size)
                            for p in SKILLS_DIR.glob("*/SKILL.md")))
    except Exception:  # noqa: BLE001
        return ()


def load_skills() -> dict:
    """读 `skills/<id>/SKILL.md`（front-matter + 正文），按 mtime 缓存。"""
    global _SKILLS_CACHE, _SKILLS_STAMP
    stamp = _skills_stamp()
    if _SKILLS_CACHE is not None and stamp == _SKILLS_STAMP:
        return dict(_SKILLS_CACHE)
    try:
        import yaml as _yaml            # 有 PyYAML 就用它（本机开发环境通常有）
    except Exception:                    # noqa: BLE001  干净容器没有 PyYAML → 用 ke-core 的子集解析
        _yaml = None
    out: dict[str, dict] = {}
    for path in sorted(SKILLS_DIR.glob("*/SKILL.md")):
        text = path.read_text(encoding="utf-8")
        meta, body = {}, text
        if text.startswith("---"):
            parts = text.split("---", 2)
            if len(parts) >= 3:
                try:
                    meta = (_yaml.safe_load(parts[1]) if _yaml else ke_yamlmini.safe_load(parts[1])) or {}
                except Exception:  # noqa: BLE001  front-matter 写坏不该让技能整块消失
                    meta = {}
                body = parts[2].strip()
        sid = str(meta.get("id") or path.parent.name)
        out[sid] = {"id": sid, "dir": path.parent.name, "path": str(path),
                    "meta": meta, "instructions": body}
    _SKILLS_CACHE, _SKILLS_STAMP = out, stamp
    return dict(out)


def skills(skill: str = "", model: str = "") -> dict:
    """技能目录 / 技能全文 + 该技能需要的那部分本体面。

    - 不传 `skill`：返回**目录**（id / name / when / models / stages / tools）；
    - 传 `skill`：返回该技能**完整指令**（SKILL.md 正文）+ `front_matter`，
      并按 front-matter 的 `scope`（focus/classes/relations）把 `ontology_types` **收窄**后一并返回
      —— 智能体拿到"这个技能能用的类/关系/数据属性"，不用再自己筛。
    """
    all_skills = load_skills()
    if not skill:
        catalog = []
        for sid, item in all_skills.items():
            meta = item["meta"]
            catalog.append({"id": sid, "name": meta.get("name") or sid,
                            "when": meta.get("when") or "",
                            "models": meta.get("models") or [],
                            "default_model": meta.get("default_model") or "",
                            "stages": meta.get("stages") or [],
                            "tools": meta.get("tools") or []})
        return {"count": len(catalog), "catalog": catalog,
                "how_to_use": ("先用本目录选技能，再 `skills(skill=\"<id>\")` 取该技能完整指令"
                               "（含它需要的类/关系/数据属性）。**不要凭记忆猜步骤**。")}
    if skill not in all_skills:
        raise ValueError("未知技能：%s（可用：%s）" % (skill, "、".join(sorted(all_skills)) or "无"))
    item = all_skills[skill]
    meta = item["meta"]
    scope = meta.get("scope") or {}
    model_key = (model or meta.get("default_model")
                 or ((meta.get("models") or ["ea"])[0]))
    ontology = {}
    try:
        ontology = ontology_types(model_key, focus=str(scope.get("focus") or ""),
                                  classes=scope.get("classes") or None,
                                  relations=scope.get("relations") or None)
    except Exception as exc:  # noqa: BLE001  技能仍可用（只是没有本体面）
        ontology = {"error": str(exc)[:160]}
    if isinstance(ontology, dict):   # 去重（ontology_types 会把"本模块引用"的类/关系并进来）
        for key, field in (("classes", "name"), ("relations", "name")):
            seen, uniq = set(), []
            for row in ontology.get(key) or []:
                if row.get(field) in seen:
                    continue
                seen.add(row.get(field))
                uniq.append(row)
            if uniq:
                ontology[key] = uniq
    return {"id": skill, "name": meta.get("name") or skill, "when": meta.get("when") or "",
            "front_matter": meta, "instructions": item["instructions"],
            "ontology_model": model_key, "ontology": ontology,
            "source": item["path"].replace(str(REPO) + "/", "")}


# ---------------------------------------------------------------------------
# 待确认裁决 + 查询工具
# ---------------------------------------------------------------------------
def list_pending_merges(kb_id: str = "", kb_ids: list | None = None) -> dict:
    """列出「待确认合并」项（只读）。

    单库：传 `kb_id`；**多库**（会话绑了多个库）：传 `kb_ids`（`<bound_knowledge_bases>` 的 id 清单）
    —— 逐个库查，每项带 `kb_id`/`kb_name`，不做跨库合并（同名不同义）。
    """
    ids = [str(x).strip() for x in (kb_ids or []) if str(x).strip()]
    if ids:
        merged, per = [], []
        for raw in ids:
            try:
                uid, name, _note = ke_db.resolve_kb_id(raw)
            except ValueError as exc:
                per.append({"asked": raw, "error": str(exc)[:200]})
                continue
            res = list_pending_merges(uid)
            merged += [{**item, "kb_id": uid, "kb_name": name} for item in res["items"]]
            per.append({"kb_id": uid, "kb_name": name, "count": res["count"]})
        return {"multi": True, "count": len(merged), "per_kb": per, "items": merged,
                "note": "多库清单：每条都带 kb_id；裁决时请把对应 kb_id 传给 resolve_pending_merge"}
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


def resolve_pending_merge(kb_id: str, pending_slug: str, action: str,
                          kb_ids: list | None = None, confirm_kb_match: bool = False) -> dict:
    kb_id, _note, block = resolve_write_kb(kb_id, kb_ids, confirm_kb_match)   # 写入口：唯一库 + 模糊需确认
    if block:
        return block
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
    id_strats: list[dict] = []
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
        statements.append(sql_insert_page(page, kb_id, tenant_id, strategies=id_strats))
        result.update({"created": page["slug"]})
    else:
        raise RuntimeError("action 只能是 merge 或 create")

    statements.append("DELETE FROM wiki_pages WHERE knowledge_base_id = %s AND slug = %s;"
                      % (sql_str(kb_id), sql_str(pending_slug)))
    statements.append(sql_rebuild_in_links(kb_id))
    psql("BEGIN;\n" + "\n".join(statements) + "\nCOMMIT;\n", stdin=True)
    if id_strats:
        result["id_strategy"] = id_strats
        _warn_noncanonical_ids(id_strats)
    return result


# ---------------------------------------------------------------------------
# 服务详细设计：CRUD 矩阵（跨页聚合，确定性渲染 —— 不靠 LLM 排版）
# ---------------------------------------------------------------------------
CRUD_SECTION = "## CRUD 矩阵"
CRUD_ORDER = ("C", "R", "U", "D")
_ATTR_VALUE = re.compile(r"^- (?P<name>[^（=]+?)(?:（[^）]*）)?\s*=\s*(?P<value>.+?)\s*$")


def _page_slug_by_title(kb_id: str, title: str) -> str:
    """按标题在**本库**里找 slug（设计载荷里的 source 可能是本批新建的页）。"""
    if not title:
        return ""
    rows = psql_csv("SELECT slug FROM wiki_pages WHERE knowledge_base_id = %s AND deleted_at IS NULL "
                    "AND title = %s ORDER BY updated_at DESC LIMIT 1"
                    % (sql_str(kb_id), sql_str(title)))
    return rows[0]["slug"] if rows else ""


def retract_relations(kb_id: str, items: list) -> dict:
    """撤回关系：删掉源页里指向目标的 `## 本体关系` 行 + 同键的 `## 关系限定（边属性）` 行。

    为什么需要：`save_knowledge` 的语义是"只追加/合并"，改设计（例如把某个写方收口）时必须能**减边**；
    否则旧边永远留在页面上，巡检（E1 写耦合等）也跟着失真。
    版本快照 + `out_links`/`in_links` 重算由 `ke_pages.rewrite_page_content` 负责。
    """
    done, missed = [], []
    for item in (items or []):
        src_slug = _page_slug_by_title(kb_id, item.get("source") or "") or \
                   _page_slug_by_title(kb_id, item.get("source_slug") or "")
        if not src_slug:
            missed.append({**item, "why": "源页不存在"})
            continue
        page = _page_row(kb_id, src_slug)
        if not page:
            missed.append({**item, "why": "源页已删"})
            continue
        rel_type = item.get("type") or ""
        target_slug = item.get("slug") or ""
        keep, removed = [], 0
        for line in (page["content"] or "").splitlines():
            text = line.strip()
            if text.startswith("- ") and ("（`%s`）" % rel_type) in text \
                    and (not target_slug or target_slug in text):
                removed += 1
                continue
            keep.append(line)
        if removed:
            ke_pages.rewrite_page_content(kb_id, src_slug, "\n".join(keep).rstrip() + "\n")
            done.append({**item, "source_slug": src_slug, "removed_lines": removed})
        else:
            missed.append({**item, "why": "没找到该关系行（可能已删）", "source_slug": src_slug})
    return {"retracted": done, "missed": missed}


def service_types() -> set:
    """「IT 服务」类的集合 = `ea:Service` 的**子类闭包**（domain 侧）。

    注意别用 `target_closure("easvc:serviceHasOperation")`：那是 **range** 闭包（ServiceOperation），
    会把操作页当成服务页（2026-09-21 实测：CRUD 矩阵刷错对象）。
    """
    meta = ke_ontology.class_meta()
    out = set()
    for name in meta:
        try:
            if "ea:Service" in ke_ontology.ancestors(name, meta):
                out.add(name)
        except Exception:  # noqa: BLE001
            continue
    return out


def _page_row(kb_id: str, slug: str) -> dict | None:
    rows = psql_csv("SELECT slug, title, COALESCE(page_type,'') AS page_type, "
                    "       COALESCE(content,'') AS content FROM wiki_pages "
                    "WHERE knowledge_base_id = %s AND slug = %s AND deleted_at IS NULL"
                    % (sql_str(kb_id), sql_str(slug)))
    return rows[0] if rows else None


def detail_attributes(page_content: str) -> dict:
    """解析页里 `## 属性（数据属性）` 小节 → {prefixed: 值}（如 `easvc:keyRole` = PK）。"""
    lines = (page_content or "").splitlines()
    start = next((i for i, line in enumerate(lines) if line.strip() == "## 属性（数据属性）"), -1)
    if start < 0:
        return {}
    end = next((i for i in range(start + 1, len(lines)) if lines[i].startswith("## ")), len(lines))
    out = {}
    for idx in range(start + 1, end):
        hit = _ATTR_VALUE.match(lines[idx].strip())
        if hit:
            out[hit.group("name").strip()] = hit.group("value").strip()
    return out


def crud_model(kb_id: str, service_slug: str) -> dict:
    """把「服务 → 操作 → 属性 + crudKind」聚合出来（只读，跨页）。

    数据真源是 **wiki 页的关系行**（与 in_links/关系面板同一口径）：
      - 服务页 `## 本体关系`：`easvc:serviceHasOperation` → 操作页；
      - 操作页 `## 本体关系`：`easvc:operationOperatesOnAttribute` → 属性页；
      - 操作页 `## 关系限定（边属性）`：同一目标的 `easvc:crudKind`（C/R/U/D）；
      - 属性页 `## 属性（数据属性）`：`easvc:keyRole`（PK/FK/UNIQUE）。
    """
    svc = _page_row(kb_id, service_slug)
    if not svc:
        raise ValueError("页面不存在：%s" % service_slug)
    operations, attributes = [], {}
    for rel in ke_pages.parse_out_relations(svc["content"]):
        if rel["type"] != "easvc:serviceHasOperation" or not rel["slug"]:
            continue
        op = _page_row(kb_id, rel["slug"])
        if not op:
            continue
        quals: dict = {}
        for q in ke_pages.parse_rel_qualifiers(op["content"]):
            quals.setdefault((q["type"], q["slug"]), {}).update(q["properties"])
        touched = []
        for orel in ke_pages.parse_out_relations(op["content"]):
            if orel["type"] != "easvc:operationOperatesOnAttribute" or not orel["slug"]:
                continue
            crud = (quals.get((orel["type"], orel["slug"])) or {}).get("easvc:crudKind", "")
            kinds = [k for k in CRUD_ORDER if k in (crud or "").upper()]
            touched.append({"slug": orel["slug"], "title": orel["target"], "crud": kinds,
                            "crud_raw": crud})
            attr = attributes.setdefault(orel["slug"], {"slug": orel["slug"], "title": orel["target"],
                                                        "key_role": "", "ops": {}})
            for kind in kinds:
                # 同一操作对同一属性可能有多条同类边（如 C 与 R 分开给）→ 矩阵里**只记一次**
                bucket = attr["ops"].setdefault(kind, [])
                if op["title"] not in bucket:
                    bucket.append(op["title"])
            if not attr["key_role"]:
                attr_page = _page_row(kb_id, orel["slug"])
                if attr_page:
                    attr["key_role"] = detail_attributes(attr_page["content"]).get("easvc:keyRole", "")
        operations.append({"slug": rel["slug"], "title": op["title"],
                           "attrs": detail_attributes(op["content"]), "touched": touched})
    return {"service": svc["title"], "service_slug": service_slug,
            "operations": operations, "attributes": attributes}


def crud_matrix_lines(kb_id: str, service_slug: str) -> list[str]:
    """渲染 `## CRUD 矩阵` 小节（没有操作/属性时返回 []，不留空表）。"""
    data = crud_model(kb_id, service_slug)
    attrs = [a for a in data["attributes"].values() if a["ops"]]
    if not attrs:
        return []
    lines = [CRUD_SECTION, "",
             "> 本表由系统按**本体关系**自动生成（服务 → 操作 → 属性，`easvc:crudKind` 为边限定属性）；"
             "改设计请去操作页的「本体关系 / 关系限定」小节，不要手改本表。", "",
             "| 业务属性 | 键 | C | R | U | D | 涉及操作 |", "|---|---|---|---|---|---|---|"]
    for attr in sorted(attrs, key=lambda a: a["slug"]):
        cells = ["√" if kind in attr["ops"] else "" for kind in CRUD_ORDER]
        ops = "、".join("%s(%s)" % (op, kind) for kind in CRUD_ORDER
                        for op in attr["ops"].get(kind, []))
        role = (attr.get("key_role") or "").upper() or "—"
        lines.append("| %s | %s | %s | %s |" % (attr["title"], role, " | ".join(cells), ops))
    writes = sum(1 for a in attrs if any(k in a["ops"] for k in ("C", "U", "D")))
    lines += ["", "**汇总**：操作 %d 个；属性 %d 个（其中被本服务写 %d 个、只读 %d 个）。"
              % (len(data["operations"]), len(attrs), writes, len(attrs) - writes), ""]
    return lines


def _replace_section(content: str, title: str, block: list[str]) -> str:
    """把正文里的 `title` 小节替换成 `block`（不存在则追加到末尾）。"""
    lines = (content or "").splitlines()
    start = next((i for i, line in enumerate(lines) if line.strip() == title), -1)
    if start < 0:
        return "\n".join(lines).rstrip() + "\n\n" + "\n".join(block).rstrip() + "\n"
    end = next((i for i in range(start + 1, len(lines)) if lines[i].startswith("## ")), len(lines))
    merged = lines[:start] + list(block) + lines[end:]
    return "\n".join(merged).rstrip() + "\n"


def refresh_crud_matrix(kb_id: str, service_slugs: list) -> dict:
    """把服务页的 `## CRUD 矩阵` 刷成最新（读库 → 改正文 → 版本快照 + 反向边重算）。

    为什么放在落库之后单独刷：矩阵是**跨页聚合**（服务 → 操作 → 属性），
    本次新写的操作/属性页刚落库，只有写库完成后再读才拿得到。
    """
    done, skipped = [], []
    for slug in sorted({s for s in (service_slugs or []) if s}):
        try:
            page = _page_row(kb_id, slug)
            if not page:
                continue
            block = crud_matrix_lines(kb_id, slug)
            if not block:
                skipped.append(slug)
                continue
            if "\n".join(block).rstrip() in (page["content"] or ""):
                skipped.append(slug)
                continue
            ke_pages.rewrite_page_content(kb_id, slug,
                                          _replace_section(page["content"], CRUD_SECTION, block))
            done.append(slug)
        except Exception as exc:  # noqa: BLE001  刷矩阵失败不影响落库结果
            skipped.append("%s(%s)" % (slug, str(exc)[:60]))
    return {"refreshed": done, "skipped": skipped}


# ---------------------------------------------------------------------------
# 服务详细设计总览（评审用；确定性渲染，跨页聚合）
# ---------------------------------------------------------------------------
OVERVIEW_SLUG = "ea/summary/it服务详细设计总览"
OVERVIEW_TITLE = "IT 服务详细设计总览"
OVERVIEW_FOLDER = "服务详细设计"
_ATTR_OF = "easvc:attributeOf"


def service_design_summary(kb_id: str) -> dict:
    """把库里所有 IT 服务的详设聚合成结构（只读）：服务 → 操作/属性/键/依赖。"""
    kb_id = resolve_kb_id(kb_id)[0]  # 名称 / UUID 都要能传（2026-09-21 实测：传名字直接返回空）
    svc_types = sorted(service_types())
    if not svc_types:
        return {"services": [], "attributes": {}, "deps": [], "service_count": 0}
    cond = ", ".join(sql_str(t) for t in svc_types)
    rows = psql_csv("SELECT slug, title, page_type FROM wiki_pages WHERE knowledge_base_id = %s "
                    "AND deleted_at IS NULL AND page_type IN (%s) ORDER BY title"
                    % (sql_str(kb_id), cond))
    services, attr_writers, attr_of, op_service, deps = {}, {}, {}, {}, []
    collected = []  # (服务标题, 操作)——操作页稍后**一次批量查**（省 N 次 psql：29 操作 ≈ 12s）
    for row in rows:
        model = crud_model(kb_id, row["slug"])
        writes, reads = {}, {}
        for attr in model["attributes"].values():
            for kind in CRUD_ORDER:
                if kind not in attr["ops"]:
                    continue
                target = writes if kind in ("C", "U", "D") else reads
                target.setdefault(attr["title"], []).extend(attr["ops"][kind])
                if kind in ("C", "U", "D"):
                    attr_writers.setdefault(attr["title"], set()).add(row["title"])
        for op in model["operations"]:
            op_service[op["slug"]] = row["title"]
            op["retry"] = (op["attrs"].get("easvc:operationRetryPolicy") or "").strip()
            collected.append((row["title"], op))
        services[row["title"]] = {"slug": row["slug"], "page_type": row["page_type"], "model": model,
                                  "writes": {k: sorted(set(v)) for k, v in writes.items()},
                                  "reads": {k: sorted(set(v)) for k, v in reads.items()}}
    op_slugs = [op["slug"] for _t, op in collected if op.get("slug")]
    op_pages = {}
    if op_slugs:
        lst = ", ".join(sql_str(s) for s in op_slugs)
        for r in psql_csv("SELECT slug, COALESCE(content,'') AS content FROM wiki_pages "
                          "WHERE knowledge_base_id = %s AND deleted_at IS NULL AND slug IN (%s)"
                          % (sql_str(kb_id), lst)):
            op_pages[r["slug"]] = r["content"]
    for svc_title, op in collected:
        for rel in ke_pages.parse_out_relations(op_pages.get(op["slug"], "")):
            if rel["type"] == "easvc:operationDependsOnOperation" and rel["slug"]:
                deps.append({"reader": svc_title, "op": op["title"],
                             "dep_op_slug": rel["slug"], "dep_op": rel["target"]})
    for row in psql_csv("SELECT slug, title, COALESCE(content,'') AS content FROM wiki_pages "
                        "WHERE knowledge_base_id = %s AND deleted_at IS NULL "
                        "AND page_type = 'easvc:BusinessAttribute'" % sql_str(kb_id)):
        attrs = detail_attributes(row["content"])
        ref = next((r for r in ke_pages.parse_out_relations(row["content"])
                    if r["type"] == _ATTR_OF), None)
        attr_of[row["title"]] = {"key_role": (attrs.get("easvc:keyRole") or "").strip().upper(),
                                 "entity": (ref or {}).get("target", ""),
                                 "writers": sorted(attr_writers.get(row["title"], []))}
    for item in deps:
        item["dep_service"] = op_service.get(item["dep_op_slug"], "")
    return {"services": services, "attributes": attr_of, "deps": deps,
            "service_count": len(services)}


def service_overview_lines(kb_id: str, with_audit: bool = True, data: dict | None = None) -> list[str]:
    """渲染「IT 服务详细设计总览」Markdown（评审用）。

    `with_audit=False` 时不跑巡检；`data` 可传入已算好的 `service_design_summary()`
    结果（避免重复聚合：实测一次聚合 ~30s，重复一次就翻倍）。
    """
    kb_id = resolve_kb_id(kb_id)[0]
    data = data or service_design_summary(kb_id)
    services, attrs = data["services"], data["attributes"]
    if not services:
        return []
    op_total = sum(len(s["model"]["operations"]) for s in services.values())
    keys = {n: m for n, m in attrs.items() if m["key_role"] in ("PK", "UNIQUE", "FK")}
    checks = {}
    if with_audit:
        try:
            checks = ke_audit.audit(kb_id, scope="coupling", max_findings=0)["summary"]["checks"]
        except Exception:  # noqa: BLE001
            checks = {}
    lines = ["# %s（自动生成）" % OVERVIEW_TITLE, "",
             "> 本页由系统按**本体关系**自动生成（服务 → 操作 → 业务属性 → CRUD/键/依赖），**不要手改**。",
             "> 口径：**写方收敛**（每个业务属性只有一个服务写 → 无 E1）+ 跨服务读一律声明 "
             "`easvc:operationDependsOnOperation`（**经接口**，E2 为合理耦合）。", "",
             "## 1. 规模与结论", "",
             "- 服务 **%d** 个；操作 **%d** 个；业务属性 **%d** 个（键属性 %d：PK %d / UNIQUE %d / FK %d）"
             % (data["service_count"], op_total, len(attrs), len(keys),
                sum(1 for m in keys.values() if m["key_role"] == "PK"),
                sum(1 for m in keys.values() if m["key_role"] == "UNIQUE"),
                sum(1 for m in keys.values() if m["key_role"] == "FK")),
             "- 巡检（coupling）：%s"
             % ("E1 写耦合 **%d**；E2 读耦合 **%d**（经接口）；E3 完整性 **%d**；E4 键一致性 **%d**"
                % (checks.get("E1", 0), checks.get("E2", 0), checks.get("E3", 0), checks.get("E4", 0))
                if with_audit else "见 `audit_scan(scope=\"coupling\")`（本页落库时由运维脚本补全）"),
             "- 生成时间：%s" % datetime.now().strftime("%Y-%m-%d %H:%M"), ""]

    lines += ["## 2. 服务一览", "",
              "| 服务 | 类型 | 操作 | 本服务作为写方的属性 | 只读的属性（他人写） | 涉及键 |",
              "|---|---|---|---|---|---|"]
    for name in sorted(services):
        svc = services[name]
        # 「写方」= 有 C/U/D 即算（同一操作对同属性读写并用时两边都出现，故不能相减）
        own = sorted(set(svc["writes"]))
        ro = sorted(set(svc["reads"]) - set(svc["writes"]))
        svc_keys = sorted(k for k in keys if k in set(svc["writes"]) | set(svc["reads"]))
        lines.append("| %s | `%s` | %d | %s | %s | %s |"
                     % (name, svc["page_type"], len(svc["model"]["operations"]),
                        "、".join(own) or "—", "、".join(ro) or "—", "、".join(svc_keys) or "—"))
    lines.append("")

    lines += ["## 3. 业务属性与键（数据设计）", "",
              "| 业务属性 | 键角色 | 所属实体 | 写它的服务（唯一） |", "|---|---|---|---|"]
    for name in sorted(attrs, key=lambda n: (attrs[n]["key_role"] or "Z", n)):
        meta = attrs[name]
        lines.append("| %s | %s | %s | %s |" % (name, meta["key_role"] or "非键",
                                               meta["entity"] or "—", "、".join(meta["writers"]) or "—"))
    lines += ["",
              "> 写方为 `—` = 该属性由**本流程之外的既有系统**维护，本设计只读（不是遗漏）；",
              "> 外键一律用 `easvc:referencesAttribute` 表达（`keyRole` 只标 PK / UNIQUE），"
              "见各服务页「## CRUD 矩阵」与关系面板。", ""]

    lines += ["## 4. 跨服务读依赖（经接口）", "",
              "| 读方服务 | 读的操作 | 被依赖的写方操作 | 写方服务 |", "|---|---|---|---|"]
    for item in sorted(data["deps"], key=lambda x: (x["reader"], x["op"])):
        lines.append("| %s | %s | %s | %s |" % (item["reader"], item["op"], item["dep_op"],
                                                item["dep_service"] or "—"))
    lines.append("")
    return lines + _overview_detail_lines(services, keys)


def _overview_detail_lines(services: dict, keys: dict) -> list[str]:
    """总览第 5 节：逐服务的操作明细（写/读属性、方法、幂等、事务、重试）。"""
    lines = ["## 5. 操作明细", ""]
    for name in sorted(services):
        svc = services[name]
        lines += ["### %s（`%s`）" % (name, svc["page_type"]), "",
                  "| 操作 | 实现方式 | 幂等 | 事务边界 | 写 | 读 |", "|---|---|---|---|---|---|"]
        for op in svc["model"]["operations"]:
            a = op["attrs"]
            lines.append("| %s | %s | %s | %s | %s | %s |"
                         % (op["title"], a.get("easvc:operationMethod", "—"),
                            a.get("easvc:isIdempotent", "—"),
                            a.get("easvc:transactionBoundary", "—"),
                            "、".join(sorted(k for k, v in svc["writes"].items() if op["title"] in v)) or "—",
                            "、".join(sorted(k for k, v in svc["reads"].items() if op["title"] in v)) or "—"))
        retry = [op for op in svc["model"]["operations"] if op.get("retry")]
        if retry:
            lines += ["", "非幂等写的重试/补偿口径："]
            lines += ["- **%s**：%s" % (op["title"], op["retry"]) for op in retry]
        lines.append("")
    return lines


def refresh_service_overview(kb_id: str, slug: str = "", title: str = "") -> dict:
    """生成/更新「IT 服务详细设计总览」页（走既有 report 段：同 slug 复用 + 正文整体替换）。"""
    lines = service_overview_lines(kb_id)
    if not lines:
        return {"skipped": "没有已详设的服务"}
    return save_knowledge(kb_id, stage="report", model="ea", mode="apply",
                          report={"title": title or OVERVIEW_TITLE,
                                  "slug": slug or OVERVIEW_SLUG,
                                  "content_md": "\n".join(lines),
                                  "category_path": [OVERVIEW_FOLDER],
                                  "upstream": []})


def ontology_types(model_key: str, focus: str = "", classes: list | None = None,
                   relations: list | None = None, with_attributes: bool = True) -> dict:
    """某本体模型的类与关系清单（供智能体选类型/关系）。

    2026-09-21 收窄参数（「领域知识建模」技能按对话缩范围用）：
      - `focus`：关键词（匹配类/关系的中文 label 或 prefixed，大小写不敏感）；
      - `classes` / `relations`：显式白名单（prefixed）；给了白名单就以它为准。
    2026-09-21 数据属性：每个类带 `attributes`（该类+祖先允许的数据属性，见 ke_ontology.data_properties_for）
      —— 「通用保存」的 schema 一半：节点 attributes 的键、边限定属性的键都按它校验，不再是硬编码。

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
            # 只把「本模块引用」与「上传导入（该前缀不在产物模块里）」的类/关系补进结果；
            # 说明文案也**只在真有上传导入模块时**才标「产物可能过期」——
            # 否则投影里其它模块的类会被当成漂移，出现「已用投影补录（见 B5）」的误导提示
            # （2026-09-21：产物与投影已一致，提示却仍在）。
            model_prefixes = {str(m.get("prefix") or "") for m in index["models"]}
            ref_classes = {c["name"] for c in (model.get("referenced") or {}).get("classes") or []}
            have_cls = {c["name"] for c in out["classes"]}
            uploaded_cls, referenced_cls = [], []
            for name in proj_classes:
                if name in have_cls:
                    continue
                meta = ke_ontology.class_meta().get(name) or {}
                if name.split(":")[0] in model_prefixes:
                    if name not in ref_classes:
                        continue  # 别的模块自己的类，不是本模块契约
                    out["classes"].append({"name": name, "label": meta.get("label") or name,
                                           "definition": meta.get("definition") or ""})
                    referenced_cls.append(name)
                else:
                    out["classes"].append({"name": name, "label": meta.get("label") or name,
                                           "definition": "（来自运行投影：上传导入的模块）"})
                    uploaded_cls.append(name)
            have_rel = {r["name"] for r in out["relations"]}
            uploaded_rel = []
            for prop in proj_props:
                if prop["name"] in have_rel:
                    continue
                if str(prop["name"]).split(":")[0] in model_prefixes:
                    out["relations"].append({**prop, "note": "（本模块引用）"})
                else:
                    out["relations"].append({**prop, "note": "（来自运行投影：上传导入的模块）"})
                    uploaded_rel.append(prop["name"])
            # 收窄：显式白名单优先，其次关键词（匹配 prefixed 或中文 label）
            key = (focus or "").strip().lower()
            cls_list = [c for c in out["classes"]
                        if (not classes or c["name"] in set(classes))
                        and (not key or key in c["name"].lower()
                             or key in (c.get("label") or "").lower())]
            rel_list = [r for r in out["relations"]
                        if (not relations or r["name"] in set(relations))
                        and (not key or key in r["name"].lower()
                             or key in (r.get("label") or "").lower())]
            if key or classes or relations:
                out["classes"], out["relations"] = cls_list, rel_list
                out["narrowed"] = {"focus": focus or "", "classes": len(cls_list),
                                   "relations": len(rel_list)}
            if with_attributes:
                # 「通用保存」的 schema：每个类允许哪些数据属性（类 + 祖先声明）
                attrs = {}
                for cls in out["classes"]:
                    allowed = ke_ontology.data_properties_for(cls["name"])
                    if allowed:
                        attrs[cls["name"]] = [
                            {"name": a["prefixed"], "label": a.get("label") or "",
                             "range": ke_ontology.short_iri(a.get("range_literal") or "")}
                            for a in sorted(allowed.values(), key=lambda x: x["prefixed"])]
                out["class_attributes"] = attrs
            # 数据属性（如 ea:ai_skill）—— 设计要写"AI 技能/工具定义"，必须让智能体看得到
            try:
                out["data_properties"] = [{"name": r["name"], "label": r.get("label") or ""}
                                          for r in ke_neo4j.query(
                        "MATCH (p:BodhiOntProperty {property_kind:'datatype'}) "
                        "WHERE p.bodhi_projection = 'ontology' AND p.prefixed IS NOT NULL "
                        "RETURN DISTINCT p.prefixed AS name, p.label AS label ORDER BY name")
                                          if r.get("name")]
            except Exception:  # noqa: BLE001
                out["data_properties"] = []
            if uploaded_cls or uploaded_rel:
                out["source"] = "artifacts+projection"
                out["note"] = ("投影里有**上传导入、不产 JSON 产物**的模块（%s），"
                               "其类型/关系在产物里看不到（见巡检 B5）："
                               "重编 `tools/ontology-compiler/compile.py compile`、"
                               "重载 `deploy/bootstrap-neo4j.sh`"
                               % "、".join(sorted(uploaded_cls + uploaded_rel)[:6]))
            return out
    raise RuntimeError("未知本体模型：%s" % model_key)


# ---------------------------------------------------------------------------
# MCP：工具定义与调用
# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# 异步任务层（2026-09-19 实测的必要性）
#   app 侧 MCP 客户端的超时是**硬编码 60 秒且无配置项**（app env / 上游 compose 里都没有
#   MCP 超时开关），而单次抽取的 LLM 调用就要 ~57s → 同步调用必然 context deadline exceeded。
#   因此工具改为「立即受理 + 后台执行」，用 job_status 轮询结果。
# ---------------------------------------------------------------------------
JOBS: dict[str, dict] = {}
JOBS_LOCK = threading.Lock()


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


def job_status(job_id: str = "") -> dict:
    """查询**异步任务**的状态/结果（当前唯一用户：`service_overview` 刷新）。

    不给 job_id 则列出最近的任务。

    沿革：本工具原名 `job_status`，只服务于「整篇异步抽取」；该抽取已于 2026-09-22
    退役（改为 `domain_modeling` 技能分批交互），任务池保留给总览页刷新，故改名去歧义。
    """
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
            "name": "list_pending_merges",
            "description": "列出「待确认合并」项（相似度处于两个阈值之间，需人工裁决）。",
            "inputSchema": {"type": "object",
                            "properties": {"kb_id": {"type": "string"},
                    "kb_ids": {"type": "array", "items": {"type": "string"},
                               "description": ("会话里绑定的知识库 id 清单（`<bound_knowledge_bases>` 原样传）。"
                                               "**写操作**：>1 个且未传 kb_id 会被拒（要求用户指明）；"
                                               "恰好 1 个时等价于显式指定。**读操作**：多库一起查")},
                            },
                            "required": []},
        },
        {
            "name": "resolve_pending_merge",
            "description": "对一条「待确认合并」做裁决：action=merge 合并到候选页，action=create 作为新页新增。",
            "inputSchema": {
                "type": "object",
                "properties": {"kb_id": {"type": "string"},
                               "pending_slug": {"type": "string"},
                               "action": {"type": "string", "enum": ["merge", "create"]},
                    "kb_ids": {"type": "array", "items": {"type": "string"},
                               "description": ("会话里绑定的知识库 id 清单（`<bound_knowledge_bases>` 原样传）。"
                                               "**写操作**：>1 个且未传 kb_id 会被拒（要求用户指明）；"
                                               "恰好 1 个时等价于显式指定。**读操作**：多库一起查")},
                    "confirm_kb_match": {"type": "boolean",
                                        "description": ("模糊匹配（uuid 前缀 / 名称包含）命中唯一库时的**二次确认**："
                                                        "true 才允许写；精确匹配（完整 uuid / 精确库名）不需要")},
                },

                "required": ["kb_id", "pending_slug", "action"],
            },
        },
        {
            "name": "ontology_types",
            "description": ("返回某本体模型的可用类与关系（含 domain/range），供核对类型是否合法。"
                            "**可用 `focus` / `classes` / `relations` 收窄**（技能按对话缩范围："
                            "如 focus=\"Service\" 只看服务相关）。返回里 `class_attributes` 给出**每个类允许的"
                            "数据属性**（本体声明，含祖先）—— 落库时 `nodes[].attributes` 与 "
                            "`edges[].properties` 的键就按它填（未声明的键会被回报为 violations，不拦写入）。"),
            "inputSchema": {"type": "object", "properties": {
                "model": {"type": "string", "description": "本体模型 key（bmm / ea / ea-service …）"},
                "focus": {"type": "string",
                          "description": "关键词收窄（匹配类/关系的中文标签或 prefixed，大小写不敏感）"},
                "classes": {"type": "array", "items": {"type": "string"},
                            "description": "显式白名单（prefixed），给了就以它为准"},
                "relations": {"type": "array", "items": {"type": "string"},
                              "description": "显式白名单（prefixed）"},
                "with_attributes": {"type": "boolean",
                                    "description": "是否附带 class_attributes（默认 true）"},
            }, "required": ["model"]},
        },
        {
            "name": "skills",
            "description": ("**技能目录 / 技能指令**（本服务的技能库在 `skills/<id>/SKILL.md`，单一来源）。"
                            "不传 `skill` → 返回目录（id / name / when / models / stages / tools）；"
                            "传 `skill` → 返回该技能**完整指令**，并附带它需要的**本体面**"
                            "（按技能声明的 scope 收窄好的类 / 关系 / 每个类的数据属性）。"
                            "用法：先看目录判断用哪个技能，再取全文照做 —— **不要凭记忆猜步骤**。"),
            "inputSchema": {"type": "object", "properties": {
                "skill": {"type": "string",
                          "description": "技能 id（domain_modeling / ea_overview_design / "
                                         "service_detailed_design …）；留空 = 只看目录"},
                "model": {"type": "string",
                          "description": "可选：本次想用的本体模型（覆盖技能默认值，如 bmm）"},
            }},
        },
        {
            "name": "job_status",
            "description": ("查询**异步任务回执**（当前用于 `service_overview(apply=true)` 的总览页刷新，"
                            "渲染 12 个服务约 1-3 分钟）。不传 `job_id` → 列出最近任务概览。"
                            "整篇抽取工具已于 2026-09-22 退役：建模改用 `domain_modeling` 技能的分批"
                            "流程（`doc_outline` → `save_knowledge(stage=\"graph\", session={...})` "
                            "→ `extract_state`）。"),
            "inputSchema": {"type": "object", "properties": {
                "job_id": {"type": "string", "description": "异步受理回执里的 job_id（留空 = 列出最近任务）"},
            }},
        },
        {
            "name": "service_overview",
            "description": ("**IT 服务详细设计总览**（评审页）：把所有 IT 服务的详设跨页聚合渲染成一页 —— "
                            "服务一览（操作数 / 写方属性 / 只读属性 / 涉及键）、业务属性与键、"
                            "跨服务读依赖（经接口）、逐操作明细（方法/幂等/事务/写读）、规模与巡检结论。"
                            "不传 `apply` → 只读预览（含统计 + 前若干行，不写库）；"
                            "`apply=true` → **异步**渲染并写入/刷新总览页（同 slug 复用），"
                            "用 `job_status(job_id=...)` 查回执。"
                            "服务详设改动后刷一遍，评审就看这一页。"),
            "inputSchema": {"type": "object", "properties": {
                "kb_id": {"type": "string", "description": "知识库 UUID 或名称（原样传可选清单里的）"},
                "apply": {"type": "boolean",
                          "description": "true = 渲染并写总览页（异步）；留空 = 只读预览"},
                "slug": {"type": "string", "description": "总览页 slug（默认 ea/summary/it服务详细设计总览）"},
                "title": {"type": "string", "description": "总览页标题（默认 IT 服务详细设计总览）"},
                "preview_lines": {"type": "integer", "description": "预览返回的 markdown 行数（默认 25）"},
            }, "required": ["kb_id"]},
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
                    "kb_id": {"type": "string", "description": "知识库 UUID（单库巡检）"},
                    "kb_ids": {"type": "array", "items": {"type": "string"},
                               "description": "多库巡检：会话绑定的库 id 清单（每库一份独立报告）"},
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
                            "**落库必须显式 `mode=\"apply\"`**（默认 dry_run 只算不写，回执 `applied=false`）。"
                            "再跑同一份内容 = **更新同一页**（报告页按标题/上游自动复用 slug，正文整体替换；"
                            "节点页 slug 命中原页即合并，不会重复建页）。"
                            "页面 slug 规则：`<类型所属模块>/<类小写>/<名称>`，故 MCP 服务页在 "
                            "`ea/mcpservice/<名称>`（历史模块 `bmm-ea-ext/…` 已废弃，按旧前缀检索必然 0 结果）。"
                            "落库与抽取路径**共用同一份实现**：相似度两阈值（高→合并现有页、低→新增页、"
                            "中间→生成「待确认合并」页交人工裁决）、版本快照、溯源、目录重建都一致；"
                            "**每次写页都会重算该页出边并重建全库 `in_links`**，所以关系面板/入边即时可见。"
                            "合规校验：类必须在本体里、关系必须满足 domain/range，违规进 violations 不入库。"
                            "**新建「应用/系统」节点必须人工确认**：默认 dry_run 只给清单，"
                            "确认后 mode=apply 且带 confirmed_new_applications 重跑（幂等，同标题只更新）。"),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "kb_id": {"type": "string", "description": "目标知识库 UUID 或精确库名（**写库前必须唯一确定**）"},
                    "kb_ids": {"type": "array", "items": {"type": "string"},
                               "description": ("会话里绑定的知识库 id 清单（`<bound_knowledge_bases>` 原样传）。"
                                               "**写操作**：>1 个且未传 kb_id 会被拒（要求用户指明）；"
                                               "恰好 1 个时等价于显式指定。**读操作**：多库一起查")},
                    "confirm_kb_match": {"type": "boolean",
                                        "description": ("模糊匹配（uuid 前缀 / 名称包含）命中唯一库时的**二次确认**："
                                                        "true 才允许写；精确匹配（完整 uuid / 精确库名）不需要")},
                    "context": {"type": "string",
                                "enum": ["domain_modeling", "ea_overview_design", "service_detailed_design"],
                                "description": ("本技能上下文：决定文案（缺 source_text 时写哪句、报告页标题/分类）。"
                                                "不传时按旧口径（概要设计）渲染")},
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
                        "description": ("细分节点：[{name, type(本体类，如 ea:Service / ea:APIService / "
                                        "ea:MCPService / ea:SkillService / bmm:SubSystem / bmm:MainSystem), "
                                        "purpose(用途), inputs[], outputs[], "
                                        "assertions[{id,kind:'N|E',assertion}], attributes{数据属性:值}, "
                                        "definition, description?, aliases?, retag?}]；"
                                        "**`attributes` 的键 = 本体为该类（或祖先）声明的数据属性**"
                                        "（见 `ontology_types(model).class_attributes`；未声明的键会被回报为 "
                                        "violations(kind=attribute)，不拦写入，但要长期用就补本体）。"
                                        "服务页会自动渲染 用途/输入输出/设计规范(ASSERTION)/属性/被引用(入边)。"
                                        "**类型变更（如 API 服务→MCP 服务）用 `retag: true`**：命中同根类的既有页时"
                                        "合并并改类型，不会新建第二份"),
                        "items": {"type": "object"},
                    },
                    "edges": {
                        "type": "array",
                        "description": ("关系：[{source, type(本体对象属性，如 ea:applicationProvidesService / "
                                        "ea:stepUsesService / easvc:operationOperatesOnAttribute), target, "
                                        "label?, properties?(边限定属性，键 = **源类**声明的数据属性)}]；"
                                        "`properties` 会渲染成页面里独立的 `## 关系限定（边属性）` 小节"
                                        "（不改 `## 本体关系` 的行语法，反向边/审计不受影响）；"
                                        "**改设计要减边时给 `retract: true`**（撤回该关系：删掉源页里指向 "
                                        "target 的那一行 + 同键的关系限定行，回执列在 `retract`）；"
                                        "target 可以是本次节点名，也可以是库内已有页标题（会解析成 slug）"),
                        "items": {"type": "object"},
                    },
                    "mode": {"type": "string", "enum": ["dry_run", "apply"],
                             "description": ("**默认 dry_run = 只算不写库**（回执里 `applied=false`、"
                                             "`dry_run=true`、`write_note` 会提示）。**要真正落库必须显式传 "
                                             "`apply`**；写完用回执 `page_versions`（含 before 版本）或巡检复核。")},
                    "confirmed_new_applications": {
                        "type": "array", "items": {"type": "string"},
                        "description": "**人工已确认**可新建的「应用/系统」节点名称清单",
                    },
                    "session": {
                        "type": "object",
                        "description": ("**领域建模 v2 的分批会话**（可选；只在按批次建模时传）："
                                        "{knowledge_id(本篇文档), round_no(第几轮), budget_tokens(本轮 token 上限),"
                                        " cursor(本批起始单元), next_cursor(回执里 doc_outline 给的下一批游标),"
                                        " doc_title}。带上它时：回执会给每个**新建页**编会话编号 "
                                        "`created[].no`，并把页面索引/轮次/游标写进会话状态（`extract_state` 可查）。"
                                        "dry_run 只预览编号，不落状态。"),
                        "properties": {"knowledge_id": {"type": "string"}, "round_no": {"type": "integer"},
                                       "budget_tokens": {"type": "integer"}, "cursor": {"type": "integer"},
                                       "next_cursor": {"type": "integer"}, "doc_title": {"type": "string"}},
                    },
                },
                "required": ["kb_id", "stage"],
            },
        },
        {
            "name": "import_probe",
            "description": ("**结构化批量建模·探表（只读）**：读 Excel/CSV 的**结构**——sheet 名、表头、行数、抽样 3 行、"
                            "**重复表头**、**列前缀**（判断一张表里有几个实体）、疑似主键列。"
                            "用法：先 `import_probe(file=…, sheet=…)`，和用户对齐「哪些列是哪个类/属性/外键」后，"
                            "再逐个目标 `import_plan` → `import_apply`。零依赖（xlsx 走标准库 zip+xml）。"),
            "inputSchema": {"type": "object", "properties": {
                "file": {"type": "string", "description": "文件路径（宿主机可读路径）"},
                "sheet": {"type": "string", "description": "可选：只探某个 sheet"},
                "sample": {"type": "integer", "description": "抽样行数（默认 3）"}},
                "required": ["file"]},
        },
        {
            "name": "import_plan",
            "description": ("**结构化批量建模·计划（只读）**：**一次只处理一个类（含其数据属性）或一条关系**。"
                            "`kind=class` 给 `target`（本体类）+ `key_column`（行→slug 的键列）+ `mapping`（列→数据属性）；"
                            "`kind=relation` 给 `target`（本体关系）+ `source_key_column`/`target_key_column` + "
                            "`source_class`/`target_class`。回执给影响面（create/update/duplicate_keys/empty_keys/dangling）、"
                            "页样例、待确认问题与 `ticket`。工具**只做确定性校验**（类/属性是否已声明、键唯一、外键命中），"
                            "语义由你（智能体）从用户描述得出。"),
            "inputSchema": {"type": "object", "properties": {
                "kind": {"type": "string", "enum": ["class", "relation"]},
                "target": {"type": "string", "description": "类（bmm:MainSystem）或关系（bmm:mainSystemContainsSubSystem）"},
                "file": {"type": "string"},
                "kb_id": {"type": "string", "description": "目标知识库（uuid 或精确库名）"},
                "sheet": {"type": "string"},
                "key_column": {"type": "string"},
                "mapping": {"type": "object", "description": "列名 → 数据属性（systemNo 或 bmm:systemNo）"},
                "title": {"type": "string", "description": "标题模板，如 {主系统英文名称}（{主系统系统编号}）"},
                "aliases": {"type": "array", "items": {"type": "string"}},
                "source_key_column": {"type": "string"},
                "target_key_column": {"type": "string"},
                "source_class": {"type": "string"},
                "target_class": {"type": "string"},
                "unknown_to_description": {"type": "boolean", "description": "未映射列是否聚合进 description（默认 true；部门这类实体建议 false）"},
                "enums": {"type": "object", "description": "枚举列 → 关系（不是数据属性）：{\"级别\":{\"relation\":\"bmm:hasEnforcementLevel\",\"values\":{\"强制\":\"bmm:Strict\",\"推荐\":\"bmm:Advisory\"}}}；工具只校验关系对该类是否合法，取值映射由技能从用户口径给"},
                "prune": {"type": "boolean", "description": "重跑时软删本批多出来的页"},
                "batch_id": {"type": "string", "description": "同一份文件多次目标共用一个 batch（便于 import_state 收敛）"},
                "limit": {"type": "integer"}},
                "required": ["kind", "target", "file", "kb_id", "key_column"]},
        },
        {
            "name": "import_apply",
            "description": ("**结构化批量建模·执行（写）**：按 `import_plan` 给的 `ticket` 落库，**只写那一个目标**；"
                            "内部按 500 行/事务分批；幂等（内容没变的页**零写入**，变化走快照 + version+1）；"
                            "关系批次把关系行写进 **domain 侧页**的「## 本体关系」并重算 in_links。"
                            "写权限：对该库有写权限（属主 / kb_shares 的 editor|writer|admin）。"),
            "inputSchema": {"type": "object", "properties": {
                "ticket": {"type": "string"},
                "actor": {"type": "string"},
                "prune": {"type": "boolean"}},
                "required": ["ticket"]},
        },
        {
            "name": "import_state",
            "description": ("**结构化批量建模·账本（只读）**：不传 `batch` → 列最近批次；传 `batch` → 每个目标的"
                            "状态与 **`remaining`**（为空 = 这张表所有类/关系都建完了）。智能体靠它循环到收敛。"),
            "inputSchema": {"type": "object", "properties": {
                "batch": {"type": "string"}}},
        },
        {
            "name": "import_refresh",
            "description": ("**结构化批量建模·元数据与目录刷新（写）**：① 把结构化导入页的 `page_metadata.ontology` 刷成当前口径"
                            "（`attributes` 键=**本体属性名**如 `bmm:ruleScope`、`attributes_by_column`=中文列名；"
                            "**只升级不降级**，中文列名按「账本 mapping → 本体 label」升级）；"
                            "② 把 `wiki_path` 对齐成「**目录路径/标题**」（前端目录树靠它显示；写 slug 会导致**目录缺失**）"
                            "并重建 `wiki_folders` + 页 `folder_id`。只改元数据/路径（正文/version 不动）；幂等。"
                            "`dry_run=true` 只看会改哪些页（含 `wiki_path_before/after`）。"),
            "inputSchema": {"type": "object", "properties": {
                "kb_id": {"type": "string"},
                "dry_run": {"type": "boolean"},
                "limit": {"type": "integer"}},
                "required": ["kb_id"]},
        },
        {
            "name": "audit_purge",
            "description": ("**巡检清理·一步硬删（写）**：只要调用者对该知识库**有写权限**就执行，不需要后台 plan/confirm。"
                            "两种用法：① 给 `slugs` → 只硬删这些页（含快照/关系行清理 + 重算 in_links）；"
                            "② 不给 → 按 `kinds`（all=清理异常+修问题；也可 no_source_pages/deleted_source_pages/"
                            "mixed_source_refs/soft_deleted_rows/orphan_revisions 等）生成计划并立即执行。"
                            "`dry_run=true` 只看影响面。**删除不可逆**，删前先 `audit_scan` 把 target 念给用户确认。"),
            "inputSchema": {"type": "object", "properties": {
                "kb_id": {"type": "string"},
                "kinds": {"type": "string", "description": "all（默认）或逗号分隔的具体 kind"},
                "slugs": {"type": "array", "items": {"type": "string"}},
                "dry_run": {"type": "boolean"}},
                "required": ["kb_id"]},
        },
        {
            "name": "rules_of_policy",
            "description": ("**文档评审·取规则清单（只读）**：给一条业务策略（`bmm:BusinessPolicy` 的 slug/标题片段），"
                            "返回它下面的**业务规则**清单：每条的名称、**级别**（Strict/Advisory/Override）、"
                            "**适用范围**（`ruleScope`，一般=文档章节）、**实现方式**（`ruleImplementation`："
                            "LLM软规则 / 图检索）、**参考规范**（`ruleReference`）与规则原文依据。"
                            "评审时**逐条**按实现方式分流（LLM软规则→大模型判定；图检索→`graph_query`）。"),
            "inputSchema": {"type": "object", "properties": {
                "kb_id": {"type": "string"},
                "policy": {"type": "string", "description": "策略的 slug 片段或标题片段"},
                "limit": {"type": "integer"}},
                "required": ["kb_id", "policy"]},
        },
        {
            "name": "graph_query",
            "description": ("**文档评审·图检索（只读）**：执行**只读** Cypher（MATCH/OPTIONAL MATCH/WITH/UNWIND/RETURN 开头），"
                            "写操作与存储过程调用一律拒；自动补 LIMIT（默认 200，上限 1000）。"
                            "给「实现方式=图检索」的规则出结论：把规则语义翻成 Cypher → 拿命中行 → 结论里附**语句+命中数**。"
                            "拿不准图谱标签时先跑探针 `MATCH (n) RETURN labels(n)[0] AS kind, count(*) AS n`。"),
            "inputSchema": {"type": "object", "properties": {
                "cypher": {"type": "string"},
                "limit": {"type": "integer"}},
                "required": ["cypher"]},
        },
        {
            "name": "reference_lookup",
            "description": ("**文档评审·取参考规范（只读）**：按规则里的「参考规范」（`bmm:ruleReference`，URL 或规范名）"
                            "找**规范内容**：① 知识库文档/附件同名 → 回 `knowledge_id`（正文用 `doc_outline` 取）；"
                            "② 知识库 wiki 页标题/slug 命中 → 直接回正文片段；③ `allow_fetch=true` 且是 URL → 抓取并剥 HTML"
                            "（内网可能不可达，**默认关**）。都找不到 → `found=false` + `note`：按技能口径标"
                            "「参考规范不可得」，**只按规则原文判定，不要编造规范内容**。"),
            "inputSchema": {"type": "object", "properties": {
                "kb_id": {"type": "string"},
                "reference": {"type": "string", "description": "规则里的参考规范（URL / 规范名 / 文件名）"},
                "allow_fetch": {"type": "boolean"},
                "max_chars": {"type": "integer"}},
                "required": ["reference"]},
        },
        {
            "name": "review_apply",
            "description": ("**文档评审·写结论（写）**：把**逐条**评审结论写成**一页报告**（`slug=review/<文档>-<策略>`，"
                            "版本化可回退）：正文=结论汇总 + 逐条结论表 + 每条明细（含**原文依据**逐字证据 / 图检索语句 / 建议）。"
                            "`findings[]` 每条：{rule, verdict(符合|不符合|不适用|无法判定), severity, scope, evidence, how, cypher, suggestion}。"
                            "写权限同其它写路径；`policy_slug` 给了会加一条 `bmm:promotesDirective` 关系指向策略页。"),
            "inputSchema": {"type": "object", "properties": {
                "kb_id": {"type": "string"},
                "doc": {"type": "string", "description": "被评审的文档名/知识 id"},
                "policy": {"type": "string"},
                "policy_slug": {"type": "string"},
                "findings": {"type": "array", "items": {"type": "object"}},
                "page_type": {"type": "string", "description": "结论页的页类型（默认 bmm:Assessment）"},
                "actor": {"type": "string"}},
                "required": ["kb_id", "doc", "policy", "findings"]},
        },
        {
            "name": "doc_outline",
            "description": ("**领域建模 v2 的入口**：按切片**父子关系**把一个文档组织成"
                            "\"大小合适\"的上下文批次（父块正文 + 子块用于定位），把"
                            "**一次交互的输入 token 上限**（`budget_tokens`）当会话参数。"
                            "用法：先 `doc_outline(kb_id, knowledge_id, budget_tokens)` 拿**本批正文** → "
                            "只从本批抽节点/关系 → `save_knowledge(stage=\"graph\", mode=\"apply\", session={...})` "
                            "落库 → 用回执的 `session.next_cursor` 取下一批，直到 `done=true`。"
                            "**超时/被截断就把 budget_tokens 调小**（8000→4000→2000）重跑本工具。"
                            "`with_text=false` 只要地图（标题/规模）。"),
            "inputSchema": {"type": "object", "properties": {
                "kb_id": {"type": "string", "description": "知识库 UUID 或名称"},
                "knowledge_id": {"type": "string", "description": "文档 id 或标题（本篇要建模的文档）"},
                "budget_tokens": {"type": "integer",
                                  "description": "本批上下文 token 上限（会话参数；默认取 BODHI_ROUND_BUDGET_TOKENS=8000）"},
                "cursor": {"type": "integer", "description": "从第几个上下文单元开始（上一批的 next_cursor）"},
                "batches": {"type": "integer", "description": "本次取几批（默认 1）"},
                "with_text": {"type": "boolean", "description": "是否返回本批正文（默认 true）"},
            }, "required": ["kb_id", "knowledge_id"]},
        },
        {
            "name": "extract_state",
            "description": ("**领域建模会话的页索引与进度**：每轮落库的页面**名称 + 会话编号**"
                            "（`pages: [{no, title, slug, type, round_no}]`）、已做轮次、游标、"
                            "待确认候选关联。用途：上下文里不必复述全文 —— 引用编号/slug 即可；"
                            "跨会话续跑也先调它对齐进度。`action=\"reset\"` 清空该文档的会话状态。"),
            "inputSchema": {"type": "object", "properties": {
                "kb_id": {"type": "string", "description": "知识库 UUID 或名称"},
                "knowledge_id": {"type": "string", "description": "文档 id/标题；留空 = 该库全部文档会话"},
                "action": {"type": "string", "enum": ["get", "reset"], "description": "默认 get"},
            }, "required": ["kb_id"]},
        },
        {
            "name": "link_candidates",
            "description": ("**候选关联登记**（跨批/跨库关联的\"单独确认\"环节）：当节点/关系需要的目标"
                            "**不在当前上下文**里时，先用向量检索（wiki_search）找到候选页，再用本工具登记。"
                            "本工具**只登记不写库**，回执含 `candidate_id`；把清单给用户逐条确认后调 "
                            "`resolve_link_candidate(action=\"confirm\")` 才真正写入领域模型。"),
            "inputSchema": {"type": "object", "properties": {
                "kb_id": {"type": "string"},
                "kb_ids": {"type": "array", "items": {"type": "string"},
                           "description": ("会话绑定的知识库 id 清单。**写操作**：>1 个且未传 kb_id 会被拒"
                                           "（要求用户指明）；恰好 1 个时等价于显式指定")},
                "confirm_kb_match": {"type": "boolean",
                                     "description": ("模糊匹配（uuid 前缀 / 名称包含）命中唯一库时的二次确认："
                                                     "true 才允许写；精确匹配不需要")},
                "source_slug": {"type": "string", "description": "源页 slug（必须已落库）"},
                "relation": {"type": "string", "description": "本体关系 prefixed 名，如 bmm:definedBy"},
                "candidates": {"type": "array", "items": {"type": "object"},
                               "description": "[{target_slug, similarity?, reason?}]；target_slug 用检索结果的 slug"},
                "knowledge_id": {"type": "string", "description": "所属文档（把候选归到该会话）"},
                "round_no": {"type": "integer", "description": "第几轮登记的"},
            }, "required": ["kb_id", "source_slug", "relation", "candidates"]},
        },
        {
            "name": "list_link_candidates",
            "description": "列出候选关联（默认 `status=pending`；`all` 看全部含已裁决）。",
            "inputSchema": {"type": "object", "properties": {
                "kb_id": {"type": "string"},
                "knowledge_id": {"type": "string", "description": "留空 = 该库全部会话"},
                "status": {"type": "string", "enum": ["pending", "confirmed", "rejected", "all"]},
            }, "required": ["kb_id"]},
        },
        {
            "name": "resolve_link_candidate",
            "description": ("候选关联**裁决**：`confirm` = 写入领域模型（调 `ke_pages.add_relation`，"
                            "页面版本 +1 并可回退）；`reject` = 丢弃。**必须先拿到用户确认**"
                            "（`target_exists=false` 的不能确认）。"),
            "inputSchema": {"type": "object", "properties": {
                "kb_id": {"type": "string"},
                "kb_ids": {"type": "array", "items": {"type": "string"},
                           "description": "会话绑定的知识库 id 清单（多库且未传 kb_id 时会被拒，要求用户指明）"},
                "confirm_kb_match": {"type": "boolean",
                                     "description": "模糊匹配命中唯一库时的二次确认（true 才允许写）"},
                "candidate_id": {"type": "string", "description": "link_candidates 回执里的 candidate_id"},
                "action": {"type": "string", "enum": ["confirm", "reject"]},
                "knowledge_id": {"type": "string"},
            }, "required": ["kb_id", "candidate_id", "action"]},
        },
        {
            "name": "retag_preview",
            "description": ("**改本体类型 · 第一步（只读预览）**：算改类型后的影响面 —— "
                            "① 新 slug（`模块/类/名称`）；② 引用了该页的地方（关系行 / out_links / 正文 / "
                            "`## 溯源` / `page_metadata` / **建模会话状态**）；③ 预计 domain-range 违规；"
                            "④ 需要用户确认的风险项 `required_risks`；⑤ **ticket**（影响面指纹）。"
                            "流程：本工具 → **把影响面与风险念给用户**、等用户明确同意 → "
                            "用**同一 ticket** 调 `retag_apply`。**不要跳过预览直接改类型**。"),
            "inputSchema": {"type": "object", "properties": {
                "kb_id": {"type": "string", "description": "目标知识库（会话绑定多库时必须明确，否则被拒）"},
                "kb_ids": {"type": "array", "items": {"type": "string"},
                           "description": "会话绑定的库清单（见 runtime_context 的 bound_knowledge_bases）"},
                "confirm_kb_match": {"type": "boolean", "description": "kb_id 模糊匹配命中唯一库时的二次确认"},
                "slug": {"type": "string", "description": "要改类型的页 slug（如 ea/step/某步骤）"},
                "new_type": {"type": "string", "description": "目标本体类型（prefixed，如 ea:Activity）"},
            }, "required": ["kb_id", "slug", "new_type"]},
        },
        {
            "name": "retag_apply",
            "description": ("**改本体类型 · 第二步（执行）**：迁移 slug + 改写所有引用（关系行 / out_links / "
                            "正文 / 溯源 / 元数据）+ 重算 in_links + 改写建模会话状态；页 id 不变，可回滚。"
                            "**必须**带 `ticket`（来自同一影响面的 `retag_preview`）与 `acknowledge_risks`"
                            "（与 preview 返回的 `required_risks` 完全一致）—— 缺一即拒；ticket 不匹配"
                            "（例如引用变了）也会被拒，需要重新 preview。调用前必须已获得用户明确同意。"),
            "inputSchema": {"type": "object", "properties": {
                "kb_id": {"type": "string"},
                "kb_ids": {"type": "array", "items": {"type": "string"}},
                "confirm_kb_match": {"type": "boolean"},
                "slug": {"type": "string"},
                "new_type": {"type": "string"},
                "ticket": {"type": "string", "description": "retag_preview 返回的 ticket（必须原样带回）"},
                "acknowledge_risks": {"type": "array", "items": {"type": "string"},
                                      "description": "用户确认过的风险项（= preview 的 required_risks，如 "
                                                     "['url_break','refs_rewrite','agent_session']）"},
            }, "required": ["kb_id", "slug", "new_type", "ticket", "acknowledge_risks"]},
        },
        {
            "name": "retag_rollback",
            "description": ("**回滚一次类型迁移**（运维用）：按 apply 留下的迁移记录 "
                            "`state/retag/<ticket>.json` 恢复 slug / 类型 / 正文 / 引用 / 会话状态；幂等。"),
            "inputSchema": {"type": "object", "properties": {
                "kb_id": {"type": "string"},
                "kb_ids": {"type": "array", "items": {"type": "string"}},
                "confirm_kb_match": {"type": "boolean"},
                "ticket": {"type": "string", "description": "要回滚的那次迁移 ticket"},
            }, "required": ["kb_id", "ticket"]},
        },
        {
            "name": "context_scan",
            "description": ("**跨库上下文扫描（只读）**：把「跨库同名 / 同实例」的知识页聚成**一张**候选表 —— "
                            "判据 L1 归一化 `slug` 字面同名（用户口径）+ L2 本体类 + 归一化标题相同（原 F1）；"
                            "每组给出 `signals`（标题/类/定义相似度/关系签名）与**建议结论**"
                            "（`equivalent` 同义 / `distinct` 异义 / `unknown` 需人判）、`required_risks`、`ticket`。"
                            "**同名不等于同义**：本工具只出建议，绝不自动改任何页；"
                            "同义 → 让用户指认「企业共享概念模型」里的概念页；异义 → 登记 ACL 映射（二期两段式 apply）。"
                            "写入路径见 docs/context-mapping-plan.md §7/§13。"),
            "inputSchema": {"type": "object", "properties": {
                "kb_ids": {"type": "array", "items": {"type": "string"},
                           "description": "要一起比较的知识库（省略=全部活着的库；本体库与共享概念库不参与分组）"},
                "limit": {"type": "integer", "description": "最多返回多少条候选（默认 200）"},
                "write": {"type": "boolean", "description": "是否落扫描报告到 state/context_map/（默认 true；只写我们自己的状态目录）"},
            }},
        },
        {
            "name": "context_lookup",
            "description": ("**跨库上下文查询（只读，检索前用）**：给定 `slug`（或 `q` 关键词）→ 返回该页在**各库**的"
                            "同名页、已挂的企业标准概念（`page_metadata.same_as`）、ACL 映射、"
                            "引用它的页（`depended_by`），以及 `warnings`（如 `same_name_no_decision` = "
                            "跨库同名但**没有任何裁决** ← 跨库引用最危险的场景）。"
                            "**跨库引用前必须先查这个**：不得因为 slug 相同就假定同义。"),
            "inputSchema": {"type": "object", "properties": {
                "slug": {"type": "string", "description": "精确 slug（如 ea/businessentity/客户信息）"},
                "q": {"type": "string", "description": "关键词（按 slug/标题模糊找，再展开跨库同名组）"},
                "kb_ids": {"type": "array", "items": {"type": "string"},
                           "description": "会话绑定的库（仅回显/限定）"},
            }, "required": []},
        },
        {
            "name": "context_page",
            "description": ("**跨库上下文渲染（只读）**：领域页 ←（**同名 slug**）→「企业共享概念模型」页 的关系视图。"
                            "返回：① 概念页（企业标准名称/定义 + `## 各领域映射` 表）；② 同名领域页清单（含类/版本）；"
                            "③ ACL 映射；④ **经企业概念转换**的中转关系；⑤ `warnings`"
                            "（`concept_page_missing` / `cross_kb_reference_forbidden` / `same_name_no_decision`）。"
                            "口径：**领域库不写 uuid、不互相引用**；关系统一按 slug 同名查概念库，"
                            "跨域关系只能经概念页转换（docs/context-mapping-plan.md §13.11）。"),
            "inputSchema": {"type": "object", "properties": {
                "slug": {"type": "string", "description": "领域页 slug（与概念页同名）"},
                "q": {"type": "string", "description": "关键词（按 slug/标题找页，再渲染）"},
                "kb_id": {"type": "string", "description": "本库 id（给了才能判定「本页是否跨库引用」）"},
            }},
        },
        {
            "name": "context_concept_apply",
            "description": ("**把某同名组落成「企业共享概念模型」里的概念页（写路径）**：概念页 slug 与领域页**同名**、"
                            "`page_type` = 该组本体类；正文含「标准定义 + 各领域映射表」（**映射关系的事实源**）。"
                            "**必须**：先 `context_scan`/人读预览拿 `ticket`、带 `acknowledge_risks`（= preview 的 "
                            "`required_risks`）、且**对企业共享概念模型库有写权限**（服务端按 `X-Bodhi-Tenant` 判）。"
                            "领域库**零写入**（不动领域页/版本）；概念页版本化可回滚（`context_concept_rollback`）。"),
            "inputSchema": {"type": "object", "properties": {
                "slug": {"type": "string", "description": "组内 slug（与概念页同名）"},
                "ticket": {"type": "string", "description": "预览返回的 ticket（原样带回）"},
                "acknowledge_risks": {"type": "array", "items": {"type": "string"},
                                      "description": "用户确认过的风险（= preview 的 required_risks）"},
                "definition": {"type": "string", "description": "企业标准定义（可空 → 用组内首选页摘要）"},
                "standard_name": {"type": "string", "description": "企业标准名称（可空 → 用标题）"},
                "actor": {"type": "string", "description": "操作者标识（记进概念页元数据与记录）"},
            }, "required": ["slug", "ticket", "acknowledge_risks"]},
        },
        {
            "name": "context_concept_rollback",
            "description": ("**回滚一次概念页 apply**（按 `state/context_map/history/<ticket>.json` 恢复旧正文或"
                            "删除新建页；幂等），并刷新映射缓存。"),
            "inputSchema": {"type": "object", "properties": {
                "ticket": {"type": "string", "description": "apply 时的 ticket"},
            }, "required": ["ticket"]},
        },
        {
            "name": "context_authority",
            "description": ("**权威/副本（只读）**：某跨库同名组的「谁是企业权威、谁是只读副本」分工与影响面 —— "
                            "master（唯一可编辑）、replicas（含是否 `local_drift`/`outdated`）、需要的风险确认与 ticket。"
                            "口径：同义知识**认定一个领域为权威**，其它领域**只读**、只能 `authority_pull` 从权威复制。"),
            "inputSchema": {"type": "object", "properties": {
                "slug": {"type": "string", "description": "同名组的 slug（概念页同名）"},
                "master": {"type": "string", "description": "候选权威库（id/前缀/精确库名）；留空=用概念页已登记的"},
            }, "required": ["slug"]},
        },
        {
            "name": "context_authority_apply",
            "description": ("**权威/副本（写，两段式）**：`action=decide` 把分工写进概念页的「权威与副本」小节（只写概念库）；"
                            "`action=pull` 从权威**复制正文**到某副本页（只写副本库，副本页 `version+1` 可回退，并写同库自述元数据）。"
                            "两者都要 `ticket` + `acknowledge_risks`（与预览一致）+ 对目标库的写权限。"
                            "副本页**不允许**用其它写路径修改（服务端会拒并提示 pull）。"),
            "inputSchema": {"type": "object", "properties": {
                "action": {"type": "string", "enum": ["decide", "pull"]},
                "slug": {"type": "string"},
                "master": {"type": "string", "description": "action=decide 时的权威库（id/前缀/库名）"},
                "kb": {"type": "string", "description": "action=pull 时的副本库（id/名称）"},
                "ticket": {"type": "string"},
                "acknowledge_risks": {"type": "array", "items": {"type": "string"}},
                "actor": {"type": "string"},
            }, "required": ["action", "slug", "ticket", "acknowledge_risks"]},
        },
    ]


def context_scan(kb_ids: list | None = None, limit: int = 200, write: bool = True) -> dict:
    """**只读**：跨库同名/同实例候选（`ke_context.scan` 的入口）。

    用户口径（2026-09-28）：**同名** = 归一化 `slug` 相同；**同实例** = 本体类 + 归一化标题相同
    —— 两者合并成**一张**候选表（`matched_by` 标出命中哪条判据）。只写 `state/context_map/`，不碰任何页。
    """
    kb_ids = [str(k).strip() for k in (kb_ids or []) if str(k).strip()]
    return ke_context.scan(kb_ids or None, limit=int(limit), write=bool(write))


def context_lookup(kb_ids: list | None = None, q: str = "", slug: str = "") -> dict:
    """**只读**：检索前查跨库同名/同义/异义/依赖（`ke_context.lookup` 的入口）。"""
    kb_ids = [str(k).strip() for k in (kb_ids or []) if str(k).strip()]
    return ke_context.lookup(kb_ids or None, q=q, slug=slug)


def context_page(kb_id: str = "", slug: str = "", q: str = "") -> dict:
    """**只读**：领域页 ←（**同名 slug**）→ 概念页 的渲染视图（`ke_context.page_view` 的入口）。

    用户口径（2026-09-28）：领域库**不写 uuid、不互相引用**；关系按 slug 同名查概念库；
    跨域关系只能**经企业共享概念页转换**。
    """
    return ke_context.page_view(str(kb_id or ""), slug=str(slug or ""), q=str(q or ""))


def context_concept_apply(slug: str, ticket: str = "", acknowledge_risks: list | None = None,
                          definition: str = "", standard_name: str = "", actor: str = "") -> dict:
    """**写**：把同名组落成「企业共享概念模型」里的概念页（`ke_context.concept_apply` 的入口）。

    守门在 ke_context 里：写权限（`ke_db.assert_can_write`，调用者租户来自 `X-Bodhi-Tenant` 头）
    + ticket（影响面指纹）+ `acknowledge_risks` 完全一致；领域库**零写入**。
    """
    return ke_context.concept_apply(str(slug or ""), str(ticket or ""),
                                    [str(x) for x in (acknowledge_risks or [])],
                                    definition=str(definition or ""),
                                    standard_name=str(standard_name or ""),
                                    actor=str(actor or ""))


def context_concept_rollback(ticket: str) -> dict:
    """**写**：回滚概念页 apply（幂等）+ 刷新缓存。"""
    return ke_context.concept_rollback(str(ticket or ""))


def context_authority(slug: str, master: str = "") -> dict:
    """**只读**：权威/副本分工与影响面（`ke_context.authority_preview` 的入口）。"""
    return ke_context.authority_preview(str(slug or ""), str(master or ""))


def context_authority_apply(action: str, slug: str, master: str = "", kb: str = "",
                            ticket: str = "", acknowledge_risks: list | None = None,
                            actor: str = "") -> dict:
    """**写**：`action=decide` 写概念库（登记权威）；`action=pull` 写副本库（从权威复制）。"""
    act = str(action or "").strip().lower()
    ack = [str(x) for x in (acknowledge_risks or [])]
    if act == "decide":
        return ke_context.authority_decide(str(slug or ""), str(master or ""), str(ticket or ""), ack,
                                           actor=str(actor or ""))
    if act == "pull":
        return ke_context.authority_pull(str(kb or ""), str(slug or ""), str(ticket or ""), ack,
                                         actor=str(actor or ""), apply=True)
    return {"error": "action 只能是 decide / pull"}


def audit_scan(kb_id: str = "", kb_ids: list | None = None, scope: str = "all",
               max_findings: int = 50) -> dict:
    """**只读**巡检（`ke_audit.audit` 的入口）。

    - 单库：传 `kb_id`；
    - **多库**：传 `kb_ids`（会话绑定的库清单）→ 逐库巡检，`per_kb[]` 里每库一份报告；
      计数**不跨库合并**（同名不同类型/不同语义是常态，合并会误导）。
    """
    ids = [str(x).strip() for x in (kb_ids or []) if str(x).strip()]
    if ids:
        per = []
        for raw in ids:
            try:
                uid, name, _note = ke_db.resolve_kb_id(raw)
            except ValueError as exc:
                per.append({"asked": raw, "error": str(exc)[:200]})
                continue
            rep = ke_audit.audit(uid, scope, max_findings)
            per.append({"kb_id": uid, "kb_name": name, "report": rep})
        return {"multi": True, "count": len(per), "per_kb": per,
                "note": "多库巡检：每库一份独立报告（findings 不跨库合并）"}
    if not (kb_id or "").strip():
        return {"error": "需要 kb_id（单库）或 kb_ids（多库，会话绑定的库清单）"}
    uid, note = resolve_kb_id(kb_id)
    rep = ke_audit.audit(uid, scope, max_findings)
    rep["kb_id"] = uid
    if note:
        rep["resolved_note"] = note
    return rep


def service_overview_tool(kb_id: str, apply: bool = False, slug: str = "", title: str = "",
                          preview_lines: int = 25) -> dict:
    """工具面：`service_overview(kb_id)` 只读预览；`apply=True` → 落库（异步，见 start_overview_job）。"""
    data = service_design_summary(kb_id)  # 只聚合一次（~30s），渲染与统计共用
    lines = service_overview_lines(kb_id, with_audit=True, data=data)
    if not lines:
        return {"error": "这个库里还没有已详设的 IT 服务：先按 service_detailed_design 技能做详设"}
    return {"markdown_lines": len(lines),
            "service_count": data["service_count"],
            "operations": sum(len(s["model"]["operations"]) for s in data["services"].values()),
            "attributes": len(data["attributes"]), "cross_service_deps": len(data["deps"]),
            "preview": "\n".join(lines[:max(1, int(preview_lines))]),
            "applied": False, "overview_slug": slug or OVERVIEW_SLUG,
            "note": ("只读预览。要生成/刷新评审页请再调本工具 **apply=true**"
                     "（异步落库，用 job_status(job_id=...) 查回执；落库版会带上巡检结论）。")}


def start_overview_job(args: dict) -> dict:
    """异步刷新总览页（渲染 + 写页要 1-3 分钟，超过 app 侧 MCP 60s 硬超时）。"""
    kb_id, kb_note, block = resolve_write_kb(str(args.get("kb_id", "")), args.get("kb_ids") or None,
                                            bool(args.get("confirm_kb_match")))
    if block:
        return block
    args = {**args, "kb_id": kb_id}
    slug = str(args.get("slug", "") or OVERVIEW_SLUG)
    key = "overview:%s:%s" % (kb_id, slug)
    with JOBS_LOCK:
        for job_id in reversed(list(JOBS)):
            job = JOBS[job_id]
            if job.get("key") == key and job.get("status") == "running":
                return {"status": "running", "job_id": job_id, "reused": True,
                        "note": "同一总览页正在刷新，请用 job_status(job_id=...) 查回执。"}
        job_id = uuid.uuid4().hex[:12]
        JOBS[job_id] = {"status": "running", "started_at": now_text(), "key": key, "tool": "service_overview"}

    def _run() -> None:
        try:
            result = refresh_service_overview(kb_id, slug, str(args.get("title", "")))
            with JOBS_LOCK:
                JOBS[job_id].update({"status": "done", "finished_at": now_text()})
                if isinstance(result, dict):
                    JOBS[job_id].update(result)
        except Exception as exc:  # noqa: BLE001
            print("[mcp] 总览刷新失败 job=%s：%s" % (job_id, exc))
            with JOBS_LOCK:
                JOBS[job_id].update({"status": "failed", "finished_at": now_text(), "error": str(exc)})

    threading.Thread(target=_run, daemon=True).start()
    return {"status": "started", "job_id": job_id, "started_at": now_text(), "slug": slug,
            "note": ("总览页刷新已受理（渲染 12 个服务约 1-3 分钟）。请用 job_status(job_id=...) "
                     "查回执；**不要**重复调用 apply=true。")}


def call_tool(name: str, args: dict) -> dict:
    if name == "job_status":
        return job_status(str(args.get("job_id", "")))
    if name == "list_pending_merges":
        return list_pending_merges(str(args.get("kb_id", "")), args.get("kb_ids") or None)
    if name == "resolve_pending_merge":
        return resolve_pending_merge(str(args["kb_id"]), str(args["pending_slug"]),
                                     str(args["action"]), kb_ids=args.get("kb_ids") or None,
                                     confirm_kb_match=bool(args.get("confirm_kb_match")))
    if name == "ontology_types":
        return ontology_types(str(args["model"]), str(args.get("focus", "")),
                              args.get("classes") or None, args.get("relations") or None,
                              bool(args.get("with_attributes", True)))
    if name == "skills":
        return skills(str(args.get("skill", "")), str(args.get("model", "")))
    if name == "service_overview":
        if args.get("apply"):
            return start_overview_job(args)
        return service_overview_tool(str(args["kb_id"]), False,
                                     str(args.get("slug", "")), str(args.get("title", "")),
                                     int(args.get("preview_lines", 25)))
    if name == "audit_scan":
        return audit_scan(str(args.get("kb_id", "")), args.get("kb_ids") or None,
                          str(args.get("scope", "all")), int(args.get("max_findings", 50)))
    if name == "audit_plan":
        return ke_audit.build_plan(str(args["kb_id"]), args.get("kinds", "all"),
                                   str(args.get("scope", "all")))
    if name == "save_knowledge":
        return save_knowledge(
            str(args["kb_id"]), stage=str(args.get("stage", "report")),
            model=str(args.get("model", "ea")), report=args.get("report"),
            nodes=args.get("nodes") or [], edges=args.get("edges") or [],
            mode=str(args.get("mode", "dry_run")),
            confirmed_new_applications=args.get("confirmed_new_applications") or [],
            session=args.get("session") or None, kb_ids=args.get("kb_ids") or None,
            confirm_kb_match=bool(args.get("confirm_kb_match")),
            context=str(args.get("context", "")))
    if name == "import_probe":
        return ke_sheet.probe(str(args["file"]), str(args.get("sheet", "")), int(args.get("sample", 3) or 3))
    if name == "import_plan":
        return ke_import.plan(kind=str(args.get("kind", "")), target=str(args.get("target", "")),
                              file=str(args.get("file", "")), kb_id=str(args.get("kb_id", "")),
                              sheet=str(args.get("sheet", "")), mapping=args.get("mapping") or {},
                              key_column=str(args.get("key_column", "")), title=str(args.get("title", "")),
                              aliases=args.get("aliases") or [],
                              source_key_column=str(args.get("source_key_column", "")),
                              target_key_column=str(args.get("target_key_column", "")),
                              source_class=str(args.get("source_class", "")),
                              target_class=str(args.get("target_class", "")),
                              unknown_to_description=bool(args.get("unknown_to_description", True)),
                              prune=bool(args.get("prune", False)),
                              enums=args.get("enums") or None,
                              batch_id=str(args.get("batch_id", "")), limit=int(args.get("limit", 0) or 0))
    if name == "import_apply":
        return ke_import.apply(str(args.get("ticket", "")), actor=str(args.get("actor", "agent:import")),
                               prune=bool(args["prune"]) if "prune" in args else None)
    if name == "import_refresh":
        return ke_import.refresh_metadata(str(args.get("kb_id", "")), bool(args.get("dry_run", False)),
                                          int(args.get("limit", 5000) or 5000))
    if name == "reference_lookup":
        return ke_review.reference_lookup(str(args.get("kb_id", "")), str(args.get("reference", "")),
                                          bool(args.get("allow_fetch", False)),
                                          int(args.get("max_chars", 6000) or 6000))
    if name == "import_state":
        return ke_import.state(str(args.get("batch", "")))
    if name == "audit_purge":
        return ke_audit.purge(str(args.get("kb_id", "")), str(args.get("kinds", "all")), "all", 5000,
                              args.get("slugs") or None, None, bool(args.get("dry_run", False)))
    if name == "rules_of_policy":
        return ke_review.rules_of_policy(str(args.get("kb_id", "")), str(args.get("policy", "")),
                                         int(args.get("limit", 300) or 300))
    if name == "graph_query":
        return ke_review.graph_query(str(args.get("cypher", "")), int(args.get("limit", 200) or 200))
    if name == "review_apply":
        return ke_review.review_apply(kb_id=str(args.get("kb_id", "")), doc=str(args.get("doc", "")),
                                      policy=str(args.get("policy", "")),
                                      policy_slug=str(args.get("policy_slug", "")),
                                      findings=args.get("findings") or [],
                                      page_type=str(args.get("page_type", "") or "bmm:Assessment"),
                                      actor=str(args.get("actor", "agent:document_review")))
    if name == "doc_outline":
        return doc_outline(str(args["kb_id"]), str(args.get("knowledge_id", "")),
                           int(args.get("budget_tokens", 0) or 0), int(args.get("cursor", 0) or 0),
                           int(args.get("batches", 1) or 1),
                           bool(args.get("with_text", True)))
    if name == "extract_state":
        return extract_state(str(args["kb_id"]), str(args.get("knowledge_id", "")),
                             str(args.get("action", "get")))
    if name == "link_candidates":
        return link_candidates(str(args["kb_id"]), str(args.get("source_slug", "")),
                               str(args.get("relation", "")), args.get("candidates") or [],
                               str(args.get("knowledge_id", "")), int(args.get("round_no", 0) or 0),
                               kb_ids=args.get("kb_ids") or None,
                               confirm_kb_match=bool(args.get("confirm_kb_match")))
    if name == "list_link_candidates":
        return list_link_candidates(str(args["kb_id"]), str(args.get("knowledge_id", "")),
                                    str(args.get("status", "pending")))
    if name == "resolve_link_candidate":
        return resolve_link_candidate(str(args["kb_id"]), str(args.get("candidate_id", "")),
                                      str(args.get("action", "")),
                                      str(args.get("knowledge_id", "")),
                                      kb_ids=args.get("kb_ids") or None,
                                      confirm_kb_match=bool(args.get("confirm_kb_match")))
    if name == "retag_preview":
        kb_id, _n, block = resolve_write_kb(str(args.get("kb_id", "")), args.get("kb_ids") or None,
                                            bool(args.get("confirm_kb_match")))
        if block:
            return block
        return ke_pages.retag_preview(kb_id, str(args["slug"]), str(args["new_type"]))
    if name == "retag_apply":
        kb_id, _n, block = resolve_write_kb(str(args.get("kb_id", "")), args.get("kb_ids") or None,
                                            bool(args.get("confirm_kb_match")))
        if block:
            return block
        return ke_pages.retag_apply(kb_id, str(args["slug"]), str(args["new_type"]),
                                    str(args.get("ticket", "")), args.get("acknowledge_risks") or [])
    if name == "retag_rollback":
        kb_id, _n, block = resolve_write_kb(str(args.get("kb_id", "")), args.get("kb_ids") or None,
                                            bool(args.get("confirm_kb_match")))
        if block:
            return block
        return ke_pages.retag_rollback(kb_id, str(args.get("ticket", "")))
    if name == "context_scan":
        return context_scan(args.get("kb_ids") or None, int(args.get("limit", 200) or 200),
                            bool(args.get("write", True)))
    if name == "context_lookup":
        return context_lookup(args.get("kb_ids") or None, str(args.get("q", "")),
                              str(args.get("slug", "")))
    if name == "context_page":
        return context_page(str(args.get("kb_id", "")), str(args.get("slug", "")),
                            str(args.get("q", "")))
    if name == "context_concept_apply":
        return context_concept_apply(str(args.get("slug", "")), str(args.get("ticket", "")),
                                     args.get("acknowledge_risks") or [],
                                     str(args.get("definition", "")),
                                     str(args.get("standard_name", "")),
                                     str(args.get("actor", "")))
    if name == "context_concept_rollback":
        return context_concept_rollback(str(args.get("ticket", "")))
    if name == "context_authority":
        return context_authority(str(args.get("slug", "")), str(args.get("master", "")))
    if name == "context_authority_apply":
        return context_authority_apply(str(args.get("action", "")), str(args.get("slug", "")),
                                       str(args.get("master", "")), str(args.get("kb", "")),
                                       str(args.get("ticket", "")),
                                       args.get("acknowledge_risks") or [],
                                       str(args.get("actor", "")))
    raise RuntimeError("未知工具：%s" % name)


def tool_result(payload: dict, is_error: bool = False) -> dict:
    return {
        "content": [{"type": "text", "text": json.dumps(payload, ensure_ascii=False, indent=2)}],
        "isError": is_error,
    }


def _log_tool_call(name: str, args: dict, result, ms: float) -> None:
    """把每次工具调用落一条可核对的行（观测用）。

    为什么需要：智能体"是否先看技能目录、是否照技能做、是否真的落库"必须**可核对**，
    否则只能听它自述。日志 `logs/mcp_calls_YYYYMMDD.log` 一行一次调用：
    时间 / 工具 / 耗时 / 入参摘要 / 结果摘要。
    """
    try:
        def brief(value, limit=60):
            if isinstance(value, (int, float, bool)) or value is None:
                return value
            if isinstance(value, str):
                return value if len(value) <= limit else value[:limit] + "…"
            if isinstance(value, list):
                return [brief(v, 24) for v in value[:4]] + (["…共%d项" % len(value)] if len(value) > 4 else [])
            if isinstance(value, dict):
                return {k: brief(v, 40) for k, v in list(value.items())[:6]}
            return str(value)[:limit]

        summary = result if isinstance(result, dict) else {}
        keys = ("applied", "created", "merged", "pending", "violations", "unmatched", "retract",
                "retract_planned", "crud_matrix", "report_page", "page_versions", "count",
                "catalog", "id", "name", "error", "how_to_use", "source", "note")
        picked = {}
        for key in keys:
            if key in summary:
                value = summary[key]
                if isinstance(value, list):
                    value = "len=%d %s" % (len(value), json.dumps(brief(value), ensure_ascii=False)[:120])
                elif isinstance(value, dict) and len(json.dumps(value, ensure_ascii=False)) > 160:
                    value = json.dumps(brief(value), ensure_ascii=False)[:160] + "…"
                picked[key] = value
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        path = LOG_DIR / ("mcp_calls_%s.log" % datetime.now().strftime("%Y%m%d"))
        with path.open("a", encoding="utf-8") as fh:
            fh.write("%s\t%-26s\t%7.0fms\targs=%s\tresult=%s\n"
                     % (now_text(), name, ms,
                        json.dumps(brief(args), ensure_ascii=False)[:300],
                        json.dumps(picked, ensure_ascii=False)[:500]))
    except Exception:  # noqa: BLE001  日志失败绝不影响工具结果
        pass


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
        # 调用者租户：`X-Bodhi-Tenant` 头（WeKnora 的 mcp_services.headers 可配）→ 写权限校验用；
        # 没带就问 env `BODHI_TENANT_ID`；都没有 → 写路径 fail-closed 拒（只读照常）。
        raw_tenant = str(self.headers.get("X-Bodhi-Tenant") or "").strip()
        ke_db.set_request_tenant(int(raw_tenant) if raw_tenant.isdigit() else None)
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
                "instructions": ("本体知识保存工具：按 `domain_modeling` 技能**分批**工作 —— "
                                 "`doc_outline` 取本批上下文 → 自己比对后 "
                                 "`save_knowledge(stage=\"graph\")` 落库 → `extract_state` 看会话进度；"
                                 "服务端只做确定性校验与写入。整篇抽取工具（原名 `extract_and_save`）"
                                 "已于 2026-09-22 退役。"),
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
                _started = time.time()
                payload = call_tool(name, arguments)
                result = tool_result(payload)
                _log_tool_call(name, arguments, payload, ms=(time.time() - _started) * 1000.0)
            except Exception as exc:  # noqa: BLE001
                print("[mcp] 工具失败：%s" % exc)
                result = tool_result({"error": str(exc)}, is_error=True)
                try:
                    _log_tool_call(name, arguments, {"error": str(exc)},
                                   ms=(time.time() - _started) * 1000.0)
                except Exception:  # noqa: BLE001
                    pass
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
                                              b.get("kb_id", ""),
                                              # 2026-09-24：默认"落真源 + 编译并生效"（可在前端关掉）
                                              write_source=bool(b.get("write_source", True)),
                                              compile_after=bool(b.get("compile_after", True)),
                                              apply_after=bool(b.get("apply_after", False))),
            # 运维修复（幂等）：编译 → 灌 Neo4j 投影 → 重投影本体库 wiki → 一致性体检
            "/bodhi/ontology/repair":
                lambda b: ke_admin.repair_all(b.get("kb_id", ""),
                                              bool(b.get("compile", True)),
                                              bool(b.get("project_wiki", True))),
            # 类型迁移（retag，2026-09-27 用户口径）：改本体类型 = **迁移 slug + 联动引用**，
            #   两段式：preview（只读影响面 + ticket）→ 用户确认 → apply（ticket + 风险确认，缺一即拒）
            "/bodhi/page/retag/preview":
                lambda b: ke_pages.retag_preview(b.get("kb_id", ""), b.get("slug", ""),
                                                 b.get("new_type", "")),
            "/bodhi/page/retag/apply":
                lambda b: ke_pages.retag_apply(b.get("kb_id", ""), b.get("slug", ""),
                                               b.get("new_type", ""), b.get("ticket", ""),
                                               b.get("acknowledge_risks") or []),
            "/bodhi/page/retag/rollback":
                lambda b: ke_pages.retag_rollback(b.get("kb_id", ""), b.get("ticket", "")),
            # 按来源文档清理本体实例（用户 2026-09-20 第二问：删文档不会联动清实例层）
            #   删「独占页」+ 多源页摘引用；默认 dry-run（apply=false 只出计划）
            "/bodhi/context/concept/preview":
                lambda b: ke_context.concept_preview(str(b.get("slug", "")),
                                                     bool(b.get("include_unknown", False)),
                                                     allow_distinct=bool(b.get("allow_distinct", False))),
            "/bodhi/context/concept/apply":
                lambda b: context_concept_apply(str(b.get("slug", "")), str(b.get("ticket", "")),
                                               b.get("acknowledge_risks") or [],
                                               str(b.get("definition", "")),
                                               str(b.get("standard_name", "")),
                                               str(b.get("actor", ""))),
            "/bodhi/context/concept/rollback":
                lambda b: context_concept_rollback(str(b.get("ticket", ""))),
            "/bodhi/context/cache/rebuild":
                lambda b: ke_context.rebuild_cache(),
            "/bodhi/context/concept/state":
                lambda b: ke_context.set_concept_state(str(b.get("slug", "")), str(b.get("state", "")),
                                                       by=str(b.get("by", "")), note=str(b.get("note", ""))),
            "/bodhi/context/authority/decide":
                lambda b: ke_context.authority_decide(str(b.get("slug", "")), str(b.get("master", "")),
                                                      str(b.get("ticket", "")),
                                                      b.get("acknowledge_risks") or [],
                                                      str(b.get("actor", ""))),
            "/bodhi/context/authority/pull":
                lambda b: ke_context.authority_pull(str(b.get("kb", "")), str(b.get("slug", "")),
                                                    str(b.get("ticket", "")),
                                                    b.get("acknowledge_risks") or [],
                                                    str(b.get("actor", "")),
                                                    apply=bool(b.get("apply", False))),
            # 结构化批量建模（一次一个类/一条关系）
            "/bodhi/import/plan":
                lambda b: ke_import.plan(kind=str(b.get("kind", "")), target=str(b.get("target", "")),
                                         file=str(b.get("file", "")), kb_id=str(b.get("kb_id", "")),
                                         sheet=str(b.get("sheet", "")), mapping=b.get("mapping") or {},
                                         key_column=str(b.get("key_column", "")),
                                         title=str(b.get("title", "")), aliases=b.get("aliases") or [],
                                         source_key_column=str(b.get("source_key_column", "")),
                                         target_key_column=str(b.get("target_key_column", "")),
                                         source_class=str(b.get("source_class", "")),
                                         target_class=str(b.get("target_class", "")),
                                         unknown_to_description=bool(b.get("unknown_to_description", True)),
                                         prune=bool(b.get("prune", False)),
                                         enums=b.get("enums") or None,
                                         batch_id=str(b.get("batch_id", "")),
                                         limit=int(b.get("limit", 0) or 0)),
            "/bodhi/import/apply":
                lambda b: ke_import.apply(str(b.get("ticket", "")), actor=str(b.get("actor", "http:import")),
                                          prune=bool(b["prune"]) if "prune" in b else None),
            # 结构化批量建模：元数据刷新（存量页一次性对齐）
            "/bodhi/import/refresh":
                lambda b: ke_import.refresh_metadata(str(b.get("kb_id", "")), bool(b.get("dry_run", False)),
                                                     int(b.get("limit", 5000) or 5000)),
            # 巡检清理：有库写权限即可一步硬删
            "/bodhi/audit/purge":
                lambda b: ke_audit.purge(str(b.get("kb_id", "")), str(b.get("kinds", "all")), "all", 5000,
                                         b.get("slugs") or None, None, bool(b.get("dry_run", False))),
            # 文档评审
            "/bodhi/review/apply":
                lambda b: ke_review.review_apply(kb_id=str(b.get("kb_id", "")), doc=str(b.get("doc", "")),
                                                 policy=str(b.get("policy", "")),
                                                 policy_slug=str(b.get("policy_slug", "")),
                                                 findings=b.get("findings") or [],
                                                 page_type=str(b.get("page_type", "") or "bmm:Assessment"),
                                                 actor=str(b.get("actor", "http:review"))),
            "/bodhi/context/ensure-marks":
                lambda b: ke_context.ensure_marks(),
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
        if path in ("/bodhi/ontology/kb", "/bodhi/ontology/kb.json"):
            # 「当前打开的是不是本体模型库」——**只读判定，不靠 uuid 相等**（2026-09-22，
            # 用户报「上传本体文件按钮不出现」：旧实现是前端拿构建期常量比 uuid，客户环境必失效）。
            # 判定顺序：env BODHI_ONTOLOGY_KB_ID/ONTOLOGY_KB_ID → wiki_config.bodhi_ontology_kb=true
            # → 库名（默认「企业本体模型」）→ 内容探测（ontology:* 页数最多且 ≥ 阈值）。
            params = dict(urlparse.parse_qsl(parsed.query))
            try:
                self._json(ke_ontology.ontology_kb_report(params.get("kb_id", "")), 200, self.CORS)
            except Exception as exc:  # noqa: BLE001
                print("[mcp] /bodhi/ontology/kb 失败：%s" % exc)
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
        if path in ("/bodhi/context/page", "/bodhi/context/page.json"):
            # 领域页 ←同名 slug→ 概念页 的渲染视图（只读）：**领域库不写 uuid、不互相引用**
            params = dict(urlparse.parse_qsl(parsed.query))
            try:
                self._json(context_page(params.get("kb_id", ""), params.get("slug", ""),
                                        params.get("q", "")), 200, self.CORS)
            except Exception as exc:  # noqa: BLE001
                print("[mcp] /bodhi/context/page 失败：%s" % exc)
                self._json({"error": str(exc)}, 400, self.CORS)
            return
        if path in ("/bodhi/context/concept/preview", "/bodhi/context/concept/preview.json"):
            # 二期概念页生成的**只读预览**（dry-run）：GET 便于 curl；POST 版见写端点表
            params = dict(urlparse.parse_qsl(parsed.query))
            try:
                self._json(ke_context.concept_preview(
                    params.get("slug", ""),
                    params.get("include_unknown", "0") not in ("0", "false")), 200, self.CORS)
            except Exception as exc:  # noqa: BLE001
                print("[mcp] /bodhi/context/concept/preview 失败：%s" % exc)
                self._json({"error": str(exc)}, 400, self.CORS)
            return
        if path in ("/bodhi/import/probe", "/bodhi/import/probe.json"):
            params = dict(urlparse.parse_qsl(parsed.query))
            try:
                self._json(ke_sheet.probe(params.get("file", ""), params.get("sheet", ""),
                                          int(params.get("sample", 3) or 3)), 200, self.CORS)
            except Exception as exc:  # noqa: BLE001
                print("[mcp] /bodhi/import/probe 失败：%s" % exc)
                self._json({"error": str(exc)}, 400, self.CORS)
            return
        if path in ("/bodhi/import/state", "/bodhi/import/state.json"):
            params = dict(urlparse.parse_qsl(parsed.query))
            try:
                self._json(ke_import.state(params.get("batch", "")), 200, self.CORS)
            except Exception as exc:  # noqa: BLE001
                print("[mcp] /bodhi/import/state 失败：%s" % exc)
                self._json({"error": str(exc)}, 400, self.CORS)
            return
        if path in ("/bodhi/review/rules", "/bodhi/review/rules.json"):
            params = dict(urlparse.parse_qsl(parsed.query))
            try:
                self._json(ke_review.rules_of_policy(params.get("kb_id", ""), params.get("policy", ""),
                                                     int(params.get("limit", 300) or 300)), 200, self.CORS)
            except Exception as exc:  # noqa: BLE001
                print("[mcp] /bodhi/review/rules 失败：%s" % exc)
                self._json({"error": str(exc)}, 400, self.CORS)
            return
        if path in ("/bodhi/review/reference", "/bodhi/review/reference.json"):
            params = dict(urlparse.parse_qsl(parsed.query))
            try:
                self._json(ke_review.reference_lookup(params.get("kb_id", ""), params.get("reference", ""),
                                                      params.get("allow_fetch", "").lower() in ("1", "true", "yes"),
                                                      int(params.get("max_chars", 6000) or 6000)),
                           200, self.CORS)
            except Exception as exc:  # noqa: BLE001
                print("[mcp] /bodhi/review/reference 失败：%s" % exc)
                self._json({"error": str(exc)}, 400, self.CORS)
            return
        if path in ("/bodhi/review/graph", "/bodhi/review/graph.json"):
            params = dict(urlparse.parse_qsl(parsed.query))
            try:
                self._json(ke_review.graph_query(params.get("cypher", ""),
                                                 int(params.get("limit", 200) or 200)), 200, self.CORS)
            except Exception as exc:  # noqa: BLE001
                print("[mcp] /bodhi/review/graph 失败：%s" % exc)
                self._json({"error": str(exc)}, 400, self.CORS)
            return
        if path in ("/bodhi/context/authority", "/bodhi/context/authority.json"):
            # 权威/副本（只读）：给前端面板与运维看分工、副本漂移、pull 预览 ticket
            params = dict(urlparse.parse_qsl(parsed.query))
            try:
                self._json(ke_context.authority_preview(params.get("slug", ""), params.get("master", "")),
                           200, self.CORS)
            except Exception as exc:  # noqa: BLE001
                print("[mcp] /bodhi/context/authority 失败：%s" % exc)
                self._json({"error": str(exc)}, 400, self.CORS)
            return
        if path in ("/bodhi/context/cache", "/bodhi/context/cache.json"):
            # 映射**缓存**（技术产物；事实源是概念页正文）：给前端/运维看缓存与失效情况
            try:
                self._json(ke_context.read_cache(), 200, self.CORS)
            except Exception as exc:  # noqa: BLE001
                print("[mcp] /bodhi/context/cache 失败：%s" % exc)
                self._json({"error": str(exc)}, 400, self.CORS)
            return
        if path in ("/bodhi/contexts", "/bodhi/contexts.json"):
            # 跨库上下文映射（一期只读）：上下文注册表（**KB = 限界上下文**，见 docs/context-mapping-plan.md）
            try:
                self._json(ke_context.contexts(), 200, self.CORS)
            except Exception as exc:  # noqa: BLE001
                print("[mcp] /bodhi/contexts 失败：%s" % exc)
                self._json({"error": str(exc)}, 400, self.CORS)
            return
        if path in ("/bodhi/context/scan", "/bodhi/context/scan.json"):
            params = dict(urlparse.parse_qsl(parsed.query))
            try:
                kbs = [x.strip() for x in (params.get("kb_ids") or "").split(",") if x.strip()]
                self._json(context_scan(kbs or None, int(params.get("limit", 200) or 200),
                                        params.get("write", "1") not in ("0", "false")), 200, self.CORS)
            except Exception as exc:  # noqa: BLE001
                print("[mcp] /bodhi/context/scan 失败：%s" % exc)
                self._json({"error": str(exc)}, 400, self.CORS)
            return
        if path in ("/bodhi/context/lookup", "/bodhi/context/lookup.json"):
            params = dict(urlparse.parse_qsl(parsed.query))
            try:
                self._json(context_lookup(None, params.get("q", ""), params.get("slug", "")),
                           200, self.CORS)
            except Exception as exc:  # noqa: BLE001
                print("[mcp] /bodhi/context/lookup 失败：%s" % exc)
                self._json({"error": str(exc)}, 400, self.CORS)
            return
        if path in ("/bodhi/crud", "/bodhi/crud.json"):
            # 服务详细设计的 CRUD 矩阵（只读派生视图，与页面里的小节同源）：
            # 给前端/运维直接查「某服务读了写了哪些属性、谁是写耦合热点」。
            params = dict(urlparse.parse_qsl(parsed.query))
            try:
                kb, slug = params.get("kb_id", "") or "", params.get("slug", "") or ""
                data = crud_model(kb, slug) if slug else {"error": "需要 slug"}
                if isinstance(data, dict) and "error" not in data:
                    data["matrix_md"] = "\n".join(crud_matrix_lines(kb, slug))
                self._json(data, 200, self.CORS)
            except Exception as exc:  # noqa: BLE001
                print("[mcp] /bodhi/crud 失败：%s" % exc)
                self._json({"error": str(exc)}, 400, self.CORS)
            return
        if path in ("/bodhi/overview", "/bodhi/overview.md"):
            # 「IT 服务详细设计总览」只读渲染（评审用）：不写库、与页面同源。
            params = dict(urlparse.parse_qsl(parsed.query))
            try:
                kb = params.get("kb_id", "") or ""
                lines = service_overview_lines(kb)
                if params.get("format") == "json":
                    self._json(service_design_summary(kb), 200, self.CORS)
                else:
                    body = "\n".join(lines).encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type", "text/markdown; charset=utf-8")
                    self.send_header("Content-Length", str(len(body)))
                    for k, v in self.CORS.items():
                        self.send_header(k, v)
                    self.end_headers()
                    self.wfile.write(body)
            except Exception as exc:  # noqa: BLE001
                print("[mcp] /bodhi/overview 失败：%s" % exc)
                self._json({"error": str(exc)}, 400, self.CORS)
            return
        if path in ("/bodhi/relations", "/bodhi/relations.json"):
            # ⚠️ 前端 `BodhiRelationsPanel.vue` 用 **GET** `/bodhi/relations?kb_id=&slug=` 取「出边 + 入边」
            #    （写边才用 POST /bodhi/relations/{add,update,delete}）。2026-09-21 实测 GET 曾落到
            #    http.server 兜底 404（HTML）→ 关系面板空白（用户报"查不到引入的本体关系"）——
            #    排查时务必确认本分支在 `do_GET` 链里（而不是只在 `_bodhi_post` 的 handlers 里）。
            params = dict(urlparse.parse_qsl(parsed.query))
            try:
                kb = params.get("kb_id", "") or ""
                slug = params.get("slug", "") or ""
                data = ke_pages.page_relations(kb, slug)
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
    # 启动自愈：把「本体库 / 企业共享概念模型库」的 wiki_config 标记补齐（**派生配置**，幂等，见 §14/A1）
    try:
        marks = ke_context.ensure_marks()
        print("[mcp] ensure-marks：%s" % json.dumps(marks.get("marks"), ensure_ascii=False))
    except Exception as exc:  # noqa: BLE001
        print("[mcp] ensure-marks 跳过：%s" % exc)
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









