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
- 详设/巡检/总览/技能：`service_*`、`ontology_types`、`skills`、`job_status`（异步任务回执）；
    巡检 `audit_scan`（scope 含 S1 库内近重复 / S2 域外同名）；运维 `rebuild_wiki_from_graph`（图→PG 正文）。
- **2026-09-22 退役**：整篇异步抽取工具（`extract_and_save` 及其状态查询 `extract_status`）
    已移除，原文留痕在 `tools/ontology-mcp/archive/async_extract_retired_2026-09-22.py.txt`。
    MCP 侧**不再持有 LLM 调用链** → `openai` 依赖随之去掉；`rdflib` 仅本体编译工具需要。
- `ontology_types(model)`：返回该模块的可用类与关系（供智能体自查）。

落库语义（2026-10-10 用户决策：相似度合并**永久取消**）
------------------------------------------------------
- 落库一律按 `(kb, slug)` 幂等 upsert：同 slug 既有活页 → **整页覆盖**（title/type/created_at 不动，
  写 `wiki_page_revisions` 快照 + `version+1`）；否则**新建**。**不再合并、不再生成待确认页。**
- 相似知识（库内近重复 / 跨库同名）的裁决统一走**巡检** `audit_scan`：
  S1 = 库内近重复（同类型 ≥0.86 / 跨类型 ≥0.93 提示）；S2 = 领域上下文之外的**同名页**。
- 历史遗留的 `ontology:PendingMerge` 页只读保留（不再新增、不再裁决）。
- **改本体类型仍走两段式 retag**（preview → 用户确认 → apply），不在落库热路径里静默改。

相似度实现（**落库已不再调用**；现由巡检 `ke_audit.check_similar` 使用）
----------------------------------------------------------------------
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
ARGS_DIR = LOG_DIR / "args"          # P1：每次工具调用的**完整入参**落盘（主日志只留摘要+文件名）
INDEX_PATH = REPO / "artifacts" / "weknora" / "ontology_index.json"
# 注意：wiki_pages.last_edit_source / wiki_page_revisions.edit_source 是 varchar(16)，
# 生成器标签**不能超过 16 个字符**（曾用 bodhi-ontology-mcp 导致写入整批回滚）。
TOOL_TAG = "bodhi-onto-mcp"
assert len(TOOL_TAG) <= 16, "TOOL_TAG 超过 last_edit_source 的 varchar(16)"

DB_CONTAINER, DB_USER, DB_NAME = "WeKnora-postgres", "postgres", "WeKnora"
# 口令不在此内置：由 `ke_db`（env `BODHI_DB_PASSWORD` → WeKnora `.env`）提供，见下方 import。
TYPE_PENDING = "ontology:PendingMerge"   # 仅历史遗留页只读识别（2026-10-10 起不再生成）

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
import ke_stats as ke_io_stats  # noqa: E402  P1：PG/Neo4j 的 IO 次数与时长统计
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
# 相似度 + 页 id 派生（P2 切片 S1 ✓ 已移到 `srv/text.py`，**名字不变** ⇒ 下游零改动 ✓）
# ---------------------------------------------------------------------------
from srv.text import (  # noqa: E402,F401
    _ngrams, cosine, embed_texts, lexical_similarity, normalize_name, page_id_for, squash,
)
# ---------------------------------------------------------------------------
# Postgres（与 weknora_sync.py 同一套约定：走容器里的 psql）
#   `_docker_prefix` / `psql` / `psql_csv` / `sql_str` / `sql_json` 已移到
#   tools/ke-core/ke_db.py，并在文件头 import 回来（名字不变，下游脚本无感）。
# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# P3-0f（2026-10-10）：**逐页只读查询**的批量预读 / 请求级 memo
#
# 实测归因（内网 33 页 dry_run，`|pg调用者:` 一行）：
#   `_resolve_page_id` **66 次**（每页 2 次：slug 查 id → 候选 id 查属主）、
#   `_session_page_ref` **32 次**（每页问一次，且问的是**同一个会话**）。
#   本机 1 次 psql ≈ 150 ms ⇒ 这两项就吃掉 ~14 s（pg 合计 138 次 / 20.9 s）。
#
# 做法：把"本批全部 slug"压成 **1 次** `IN (...)` 查询、"本批全部候选 id"再压成 1 次；
# 会话页引用是"同一问题问 N 遍"→ memo。作用域 = **一次工具调用**（`call_tool` 入口重建、
# 写库之后作废）⇒ 不跨请求复用，不会读到别处进程/别的工具的写入。
# ---------------------------------------------------------------------------
_REQ_CACHE = threading.local()


def _req_cache() -> dict:
    """本请求线程的只读查询 memo（无则自建：CLI/单测直接调这些函数时也能用）。"""
    cache = getattr(_REQ_CACHE, "d", None)
    if cache is None:
        cache = {"session_ref": {}}       # 只剩会话页引用 memo（页 id/版本预读已随 legacy 删除）
        _REQ_CACHE.d = cache
    return cache


def _reset_request_cache() -> None:
    """丢弃本请求的全部 memo。工具入口调一次；**写库之后**也必须调（页已变）。"""
    _REQ_CACHE.d = None




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
from srv.constants import (  # noqa: E402,F401  （P2 切片 S3/S8b-2a ✓ 取值表达式逐字一致）
    CHARS_PER_TOKEN, DEFAULT_ROUND_BUDGET, DOMAIN_STATE_DIR, EXPORT_DIR,
)
from srv.tools_ops import kb_export, kb_import  # noqa: E402,F401  （P2 切片 S8b-2a ✓）


from srv.state import (  # noqa: E402,F401  （P2 切片 S3 ✓ 状态读写，实现逐字未改）
    _empty_state, _iter_states, _state_path, extract_state, load_domain_state, save_domain_state,
)


from srv.outline import (  # noqa: E402,F401  （P2 切片 S4 ✓ 切片/预算/游标，实现逐字未改）
    _chunk_units, _est_tokens, _split_unit, doc_outline,
)











# --- 候选关联（跨上下文/跨库的关联，必须单独确认后才写库）-------------------
from srv.links import (  # noqa: E402,F401  （P2 切片 S6 ✓ 候选关联，实现逐字未改）
    _page_row_by_slug, link_candidates, list_link_candidates, resolve_link_candidate,
)














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


from srv.kbresolve import (  # noqa: E402,F401  （P2 切片 S2 ✓ 名字不变 ⇒ 下游零改动）
    get_kb_tenant, kb_choices, resolve_kb_id, resolve_knowledge_id, resolve_write_kb,
)








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




def element_slug(model_key: str, element: dict) -> str:
    local = (element["type"].split(":", 1)[-1] or "unknown").lower()
    return "%s/%s/%s" % (model_key, local, normalize_name(element["name"]) or
                         hashlib.sha1(element["name"].encode("utf-8")).hexdigest()[:12])


def element_page_slug(model: dict, element: dict) -> str:
    """页面 slug：设计载荷自带 `slug`（按**节点自身类所属模块**算，如 `bmm-ea-ext/apiservice/…`）；
    没有时才按模型 key 推导（抽取路径的原行为，保持不变）。"""
    explicit = (element.get("slug") or "").strip()
    return explicit or element_slug(model["key"], element)



from srv.render import (  # noqa: E402,F401  （P2 切片 S7 ✓ 小节渲染 + 技能上下文文案）
    SKILL_CONTEXTS, _design_sections, _type_label_of, context_meta,
)


# ---------------------------------------------------------------------------
# 技能上下文（2026-09-22）：同一套落库代码被三个技能复用，**文案必须跟着技能走**
#   domain_modeling          领域建模（从文档抽 → 节点应当有原文引用）
#   ea_overview_design       企架概要设计（设计生成 → 无原文片段）
#   service_detailed_design  服务详细设计（设计生成 → 无原文片段）
# 旧实现把"概要设计"写死 → 领域建模的页面上也写"概要设计…/无原文片段"（用户实测报过）。
# `context` 是**可选**参数：不传时保持旧口径（design = 概要设计），向后兼容。
# ---------------------------------------------------------------------------






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
    _loc = (element.get("source_locator") or "").strip()
    lines += ["## 原文依据", "",
              ("- %s（来源：《%s》%s%s）" % (element.get("source_text", "").strip(), doc_meta["title"],
                                           (" 片段 #%d" % chunk_index) if chunk_index >= 0 else "",
                                           ("；定位：%s" % _loc) if _loc else ""))
              if doc_meta.get("id") else
              ("- %s（%s%s）" % (element.get("source_text", "").strip(),
                                context_meta(doc_meta.get("context"))["evidence_generated"],
                                ("；定位：%s" % _loc) if _loc else "")), ""]
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
    _cat = element.get("category_path") or class_category_path(
        element["type"], model["key"], model["label"], element["type_label"])
    return {
        "slug": slug, "title": element["name"], "page_type": element["type"],
        "summary": (element.get("definition") or "")[:500],
        "content": "\n".join(lines).rstrip() + "\n",
        # 分类路径：设计载荷可显式指定（如报告页用 `["概要设计报告"]`）；否则按本体类推导
        "category_path": _cat,
        # `wiki_path` 供**前端目录树显示**：口径必须是「目录路径/标题」（与 `ke_pages.upsert_page` /
        # `ke_import` 一致）。**不能写 slug** —— 2026-10-03 用户实测：写成 slug 时前端目录能列出目录名
        # 但**展开取不到页**（与知识库既有排障结论 TROUBLESHOOTING §「wiki_path 写成 slug」同因）。
        "wiki_path": "/".join([str(x) for x in _cat] + [element["name"]]),
        "source_refs": [doc_meta["id"]] if doc_meta.get("id") else [],
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



def union_list(old, new) -> list:
    return list(dict.fromkeys(list(old or []) + list(new or [])))



def save_elements(kb_id: str, model: dict, checked: dict, payloads: list, doc_meta: dict,
                  *, high: float = DEFAULT_HIGH, low: float = DEFAULT_LOW, dry_run: bool = False,
                  tenant_id: int | None = None, resolved_note: str = "", knowledge_id: str = "",
                  extra: dict | None = None, engine=None, same_type_only: bool = False,
                  skip_folders: bool = False, skip_crud: bool = False) -> dict:
    """**落库半段**（抽取路径与设计路径共用）：相似度匹配 → 合并/新增/待确认 → 写页 → 重算 links。

    `skip_folders` / `skip_crud`（2026-10-09 P0-b）：把**重型维护**移出热路径 ——
    目录树全量重建（`sync_folders.sync_kb`）与 CRUD 矩阵刷新在大库上要 10-60 s，
    而前端进 wiki 时会调 `/bodhi/folders/refresh`（接口已存在）→ 落库不必同步等它。

    这段逻辑原先内联在**已退役的整篇抽取工具**里（2026-09-20 原样抽出，行为逐字保留；
    抽取工具 2026-09-22 退役，本函数现由设计/建模路径 `save_knowledge` 使用）：
    - **按 `(kb, slug)` 幂等落库**：同 slug 既有活页 → 整页覆盖；否则新建。
      （**相似度合并 / 待确认合并页已永久取消**，2026-10-10 用户决策；相似知识治理走巡检 `audit_scan`。）
    """
    tenant = tenant_id if tenant_id is not None else get_kb_tenant(kb_id)
    pages = fetch_existing_pages(kb_id)
    # 2026-10-10（用户决策）：**相似度合并永久取消** —— `BODHI_SIMILARITY_MERGE` 开关、`pick_match`、
    # `merge_content`、待确认合并页与其裁决工具（`list_pending_merges` / `resolve_pending_merge`）全部删除。
    # 落库一律按 `(kb, slug)` 幂等 upsert（同 slug 覆盖、否则新建）；相似知识（库内近重复 / 跨库同名）
    # 的裁决统一走巡检 `audit_scan`（S1/S2，见 `ke_audit.check_similar`）。
    # P0-c-2（2026-10-10）：**库里既有页的快照 —— 必须在此刻做**。
    # 下面的循环会把"本次构建的页"逐个 `pages.append(...)` 进去（65 = 32 库里行 + 33 新构建），
    # 之后再按 slug 建映射会**取到新页**而非库里那一行 → 拿不到原有的「首个版本生成」等原值
    # （实测：那正是 kernel 与 legacy 页头差异的最后一行）。
    _exist_before = {p.get("slug"): p for p in pages if p.get("slug")}
    # 跨库引用护栏（2026-09-22）：关系目标只允许指本库的页（本库已有 + 本次要建的）。
    # 旧实现会因 `_graph_target_slug` 全库查而把**别的知识库**的 slug 写进本库页 → 回执成功但本库没记录。
    # P0-c-2（2026-10-10 用户口径 · 方案 a）：**文档类节点在 graph 阶段必须带正文**。
    # 本体 `render_mode(page_type)=="document"` 的类（设计单/建议/评测/安装指引/报告…）本该整篇正文
    # 入库；graph 阶段若只给 definition/attributes（没带 `wiki_content`），旧行为会把它按 entity 渲染成
    # 一页**错正文**（实测：设计单 v1 = 416 字 entity 版，随后被 report 分支的 4896 字 document 版覆盖
    # → 白占一个版本、巡检还会看到"两个版本两种形态"）。现在**跳过不落** + 回执回报 + 关系按
    # "目标页不存在"丢弃并给出准确原因，提示改用 `stage="report"`。
    _doc_skipped: dict = {}
    for _p in list(payloads):
        _t = str(_p.get("type") or "")
        try:
            _is_doc = bool(_t) and ke_ontology.render_mode(_t) == "document"
        except Exception:  # noqa: BLE001
            _is_doc = False
        if _is_doc and not str(_p.get("wiki_content") or "").strip():
            _doc_skipped[element_page_slug(model, _p)] = {"name": _p.get("name"), "type": _t}
    if _doc_skipped:
        payloads = [p for p in payloads if element_page_slug(model, p) not in _doc_skipped]
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
                _why = ("目标页是**文档类**且本次未提供 `wiki_content`（请用 `stage=\"report\"` 落整篇正文）：%s"
                        % ts if ts in _doc_skipped else
                        "目标 slug 不属于本知识库（跨库引用已丢弃；请在本库先建该节点）")
                dropped_relations.append({
                    "source": payload.get("name"), "relation": rel.get("type"),
                    "target": rel.get("target"), "target_slug": ts, "reason": _why})
                continue
            kept.append(rel)
        payload["relations"] = kept
    # id 策略留痕（2026-09-24 加固）：写库后由内核回执回填 existing/new（见下方 `_kres`），
    # 出现 suffixed 会告警（兜底分支按设计几乎不可达）。
    id_strategies: list[dict] = []
    summary = {
        "model": model["key"], "kb_id": kb_id, "knowledge_id": knowledge_id,
        "resolved_note": (resolved_note or "").strip(), "doc_title": doc_meta.get("title", ""),
        "elements": len(payloads), "relationships": len(checked.get("edges") or []),
        "created": [], "merged": [],
        "violations": checked["violations"], "unmatched": checked["unmatched"],
        "dry_run": dry_run, "thresholds": {"high": high, "low": low,
                                           "deprecated": "相似度阈值已无用途（合并永久取消）"},
        "dropped_relations": dropped_relations,
        "generated_at": now_text(),
    }
    if extra:
        summary.update(extra)
    summary["merge"] = {"enabled": False, "removed": True, "note": (
        "**相似度合并已永久取消**（2026-10-10 用户决策）：一律按 (kb, slug) 幂等 upsert —— 同 slug 覆盖、"
        "否则新建。相似知识（库内近重复 / 跨库同名）的裁决走巡检 `audit_scan`（S1/S2）。")}
    if _doc_skipped:
        summary["skipped_document_nodes"] = [
            {"slug": k, "name": v["name"], "type": v["type"]} for k, v in sorted(_doc_skipped.items())]
        summary["skipped_document_note"] = (
            "以下节点是**文档类**（本体 `render_mode=document`）：graph 阶段必须带 `wiki_content`（整篇正文）"
            "才能入库；本批未带 → **未建页/未改页**。请改用 `stage=\"report\"` 落整篇正文（同一 slug 幂等覆盖）。")
    # 写库自证（2026-09-21 用户实测：`mode` 忘了传 → 默认 dry_run 只算不写，回执里只有一行
    # `dry_run: true`，极易被当成"已落库"，于是反复出现"回执成功但 wiki 没变"）：
    # 顶层给 `applied`（是否真写库）与 `write_note`，并附上被更新页的 version 变化。
    summary["applied"] = not dry_run
    if dry_run:
        summary["write_note"] = ("**未写库**（dry_run）：本回执只是预览。要真正落库请用**同一份载荷**、"
                                 "`mode=\"apply\"` 重跑；`created/merged` 里的 slug 在 apply 时才生效。")
    summary["page_versions"] = []

    retagged: list = []
    retag_queue: list = []
    # 2026-10-10（用户口径）：**落库只走契约内核**（`BODHI_SAVE_ENGINE` 开关与 legacy 单事务写已删）。
    # `ke_pages.write_knowledge_batch` 统一契约 §8 写序（图节点 → 页 → 边）、批量写 PG（P0-c-3：
    # `_bulk_write_pages` 一次多行 INSERT + 一次快照/UPDATE）、失败只丢当前那条。
    # · 内核 spec 用 **`mode="document"` + `strip_legacy_head=True`**：实测与改造前的正文**逐字相同**；
    # · **不自带 relations**：边由调用方（`save_knowledge` 的 apply 段）整批 UNWIND 建；
    # · 页 id / 存在性 / 版本由内核自己批量预读（一次 `page_info_map` 免掉逐页查询）。
    built: list = []          # 收集"已构建好"的页（element + page + action），最后交内核批量写
    for element in payloads:
        # 相似度合并**永久取消**（2026-10-10，用户决策）：不再 `pick_match`、不再 `merge_content` 追加、
        # 不再生成「待确认合并」页。落库只有两条路径：**同 slug 既有活页 → 整页覆盖** / **新建**。
        # 相似知识的裁决统一交给巡检 `audit_scan`（S1 库内近重复 / S2 域外同名，见 ke_audit.check_similar）。
        sim, candidate = 0.0, None
        if candidate is None or sim <= low:
            page = build_new_page(engine or load_engine(), model=model, element=element,
                                  chunk_id=element["chunk_id"], chunk_index=element["chunk_index"],
                                  doc_meta=doc_meta)
            # **slug 就是唯一的键**（相似度合并取消后）—— 同 slug = 同一页，一律幂等覆盖
            # （内核 upsert 的 ON CONFLICT 守卫）。**绝不加哈希后缀**：实测回归证明加后缀会去查
            # 一个不存在的"带后缀 slug"→ 误判 created → apply 时建出一批重复页。
            # 回执如实（2026-09-21 用户实测：`fetch_existing_pages` 排除 summary/index 页，报告页会走
            # 到这里；若不说清是「覆盖既有活页」，就会出现"回执说 created，用户看到的是旧正文"的错觉）。
            # 2026-10-10：判「新建 / 覆盖」用**循环前的库里快照**（`_exist_before`，来自
            # `fetch_existing_pages` 那次预读）—— 不再逐页查 PG（旧实现每页 1 次 psql ≈ 5 s/33 页）。
            _old_row = _exist_before.get(page["slug"]) or {}
            if _old_row:
                action = "updated（同 slug 既有活页：正文按本次载荷覆盖，title/type 不动）"
                before_v = int(_old_row.get("version") or 1)
            else:
                # 注：库里只剩"软删旧行"时这里也判 created（内核 upsert 会复活它）；
                # 旧实现那一次单查 PG 能细分 `revived` —— 为省掉逐页查询，回执不再细分这一种。
                action, before_v = "created", 0
            if action == "created":
                built.append({"element": element, "page": page, "action": action})
                summary["created"].append({"name": element["name"], "type": element["type"],
                                           "slug": page["slug"]})
            else:
                # 既有活页（典型：`summary` 报告页被 fetch_existing_pages 排除在合并候选之外）：
                # 用**本次渲染的整页**覆盖内容 —— 报告页是"生成物"，整体替换可避免旧正文里的过期小节、
                # 旧措辞（如"（来源：《…》）"）残留；title / page_type / created_at 一律不动。
                built.append({"element": element, "page": page, "action": action})
                summary["merged"].append({"name": element["name"], "type": element["type"],
                                          "into": page["slug"], "similarity": 1.0, "note": action})
            summary["page_versions"].append({"slug": page["slug"], "before": before_v,
                                             "action": action, "applied": not dry_run})
            pages.append({**page, "version": 1, "knowledge_base_id": kb_id,
                          "status": "published", "in_links": [], "title": page["title"],
                          "summary": page["summary"], "aliases": page["aliases"],
                          "source_refs": page["source_refs"], "chunk_refs": page["chunk_refs"]})
    summary["engine"] = "kernel"
    if not dry_run and built:
        if built:      # 单一写路径（legacy 单事务写已删，2026-10-10）
            # P0-c-2：**走契约内核**（逐页小事务 + 按 (kb,slug) 幂等 upsert；图节点先入图 = §8 写序）。
            # 正文用 legacy 构建结果**原样托运**（`mode=document` + 剥 legacy 头，实测逐字一致）；
            # 关系不带上（由 `save_knowledge` 的 apply 段整批 UNWIND 建，比按页快）。
            import ke_pages as _kp  # noqa: PLC0415
            _id = _IDENT.ident or {}
            # 库里既有页用**循环前的快照**（`pages` 已混入本次新建的页，见 `_exist_before` 注释）
            if str(ke_db.env_value("BODHI_DEBUG_ENGINE", "0")) not in ("0", "", "false", "no"):
                print("[dbg-engine] exist_before=%d built=%d 样例=%s"
                      % (len(_exist_before), len(built),
                         [(b["page"].get("slug"), bool(_exist_before.get(b["page"].get("slug"))))
                          for b in built[:2]]))
            _specs = []
            _sno = str(_id.get("session_no") or "")
            _part = _session_part_no(kb_id, _sno) if _sno else ""
            _loc = (("session/%s/p%s" % (_sno, _part)) if (_sno and _part)
                    else (("session/%s" % _sno) if _sno else ""))
            for _b in built:
                _pg = _b["page"]
                _old = _exist_before.get(_pg.get("slug")) or {}
                # 「首个版本生成」的**权威值在既有页正文的页头里**（legacy 只把它渲染进正文，
                # 没写进 page_metadata —— 实测取 metadata 取不到、时间戳会被重置成当前时间）。
                # 兜底再看 metadata（内核写过的页会有 `ontology.created_at`）。
                _old_created = (_kp._parse_legacy_head(_old.get("content") or "")[0].get("首个版本生成")
                                or ((_old.get("page_metadata") or {}).get("ontology") or {})
                                .get("created_at"))
                # 不传 `session_slug`：页头的「来源会话」行由内核按 source 渲染，而 legacy 页头从来没有
                # 这一行 → 传了就会改正文（实测 +50 字符）。会话溯源仍由本路径既有的会话边逻辑负责。
                # **但 `locator` 要传**：它是 L2 数据属性 `bmm:sourceLocator`（含分页序 `/pN`），
                # 与内核自己的口径一致（实测库里就是 `session/<no>/p2`）。
                _s = _kp.spec_from_page(
                    _pg, _b["element"], mode="document", strip_legacy_head=True, relations=[],
                    source={"doc_refs": _pg.get("source_refs") or [],
                            "chunk_refs": _pg.get("chunk_refs") or [],
                            "locator": _loc})
                if _old_created:
                    # 更新既有页 → **沿用库里记录的"首个版本生成"**（口径：只在新建页写；否则每次
                    # 写库这一行都会变成当前时间，实测 diff 就差在这一行）。
                    _s["created_at"] = _old_created
                if _loc:
                    _attrs = dict(_s.get("attributes") or {})
                    _attrs["bmm:sourceLocator"] = _loc
                    _s["attributes"] = _attrs
                _specs.append(_s)
            _kres = _kp.write_knowledge_batch(kb_id, _specs, dry_run=False, strict_source=False,
                                              sync_folders=False)
            summary["kernel"] = {"planned": len(_specs), "applied": _kres.get("applied_count"),
                                 "failed": _kres.get("failed"),
                                 "bulk_write": _kres.get("bulk_write"),
                                 "resume_hint": _kres.get("resume_hint")}
            if _kres.get("failed"):
                summary["applied"] = False
            # id 策略回执：由**内核回执**回填（替代旧的逐页 `_resolve_page_id` 留痕）
            for _pr in (_kres.get("pages") or []):
                id_strategies.append({"slug": _pr.get("slug"), "id": "",
                                      "how": ("new" if _pr.get("created") else "existing")})
        # P3-0f：页已变 → 本请求的"会话页引用"memo 一律作废
        # （否则后续阶段——`link_source_session`、`sourceLocator`——会读到写之前的空结果）。
        _reset_request_cache()
        # 写后对账（2026-09-24）：回执里的每个 slug 必须**在本库**查得到 ——
        # 否则就是"回执成功、库里没有"（历史 bug：页 id 只按 slug 派生 → upsert 更新了别库那一行）。
        # 这里把它变成**显式的失败**：`applied` 回拨为 False + missing 清单，提示不要向用户汇报成功。
        wanted = [e.get("slug") for e in summary["created"]] \
            + [e.get("into") for e in summary["merged"]]
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
        if skip_folders:
            # P0-b：落库不再同步等"全库目录重建"（大库 10-60 s）；前端进 wiki 会调
            # `/bodhi/folders/refresh` 按需刷新（该接口就是为"目录更新早于最新页面"加的）。
            summary["folders_synced"] = "deferred（按需刷新：前端调 /bodhi/folders/refresh）"
        else:
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
        # P0-b：`skip_crud` 时跳过（每服务页 5-8 次往返；按需用 `/bodhi/crud/refresh` 或下次落库补）。
        if skip_crud:
            summary["crud_matrix"] = "deferred（按需刷新）"
        else:
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

    injected_identity: dict = {}
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
            "attributes": _attrs_with_identity(node, kb_id, injected_identity),
            "retag": bool(node.get("retag")),   # true = 合并进既有页时**同时把该页类型改成 element 的类型**
            "incoming": incoming_map.get(name, []),
            "source_text": node.get("source_text") or ctx["no_quote"],
            # 来源定位（2026-10-01）：显式 `source_locator`/`locator` 优先，其次 `chunk_index` 兜底；
            # 落到节点属性 `bmm:sourceLocator` + 页面「## 原文依据」的来源行（可查询、可复核）。
            "source_locator": (str(node.get("source_locator") or node.get("locator") or "").strip()
                               or _chunk_locator(node.get("chunk_index"))),
            "chunk_id": str(node.get("chunk_id") or ""),
            "chunk_index": _as_chunk_index(node.get("chunk_index")), "relations": rels,
            "upstream": upstream, "aliases": node.get("aliases") or [], "context": ctx["id"],
        })
    # 注：`bmm:sourceSession` 的自动挂**不在这里**（2026-10-04 修正）——把关系塞进 payload.relations 会因为
    # 拿不到 slug 而渲染成**纯文本**（`→ <标题>`），页面 `in_links` 解析不到、会话页「被引用」为空。
    # 改为**写入后**由 `link_source_session()` 按 slug 补双链（见 `save_knowledge` 的 graph 分支回执）。

    cross_kb = _other_kb_same_name(kb_id, unresolved)
    return payloads, {"edges": accepted, "violations": violations, "unmatched": [],
                      "retract_edges": retract_edges,
                      "cross_kb_same_name": cross_kb,
                      # 身份注入回执（2026-10-01）：会话类节点的 tenantName/agentName 由服务端注入，
                      # 客户端传值一律丢弃（防伪造）——这里说明注入了什么，便于回执核对。
                      "injected_identity": injected_identity,
                      "note": ("目标不在本库、但**同名页存在于其它知识库**：这些目标按"
                               "「本库没有」处理（不跨库合并、不入库）；要连到别库的页，"
                               "请先在本库建立对应节点。") if cross_kb else ""}


# ---------------------------------------------------------------------------
# 身份注入 & 来源定位（2026-10-01，闭环一）
#   · 会话类节点（`bmm:KnowledgeSession`）的**租户/智能体身份只能由服务端注入**：
#     客户端（智能体/用户）传的同名属性一律丢弃 —— 否则对话内容即可伪造身份。
#   · 来源定位 `bmm:sourceLocator`：显式值优先，缺失时用 `chunk_index` 兜底成「片段 #n」。
# ---------------------------------------------------------------------------
IDENTITY_CLASS = "bmm:KnowledgeSession"

# 目录层级参数（2026-10-04 用户口径）：
#   第一层永远是**模块短名**；`depth` = 模块下再展开几层类（0=直接到本类；1=直接 Thing 子类；上限 5）；
#   **weknora 自带类型**（无 `:`）固定第一层 `weknora`、第二层是类型名（与 depth 无关）。
MAX_DIR_DEPTH = 5
WEKNORA_MODULE_SHORT = "weknora"
WEKNORA_TYPE_LABELS = {"summary": "摘要", "entity": "实体", "concept": "概念",
                       "synthesis": "综合", "comparison": "对比", "question": "问题",
                       "faq": "FAQ", "pipeline": "流水线", "chunk": "切片",
                       "image": "图片", "table": "表格"}


from srv.session import (  # noqa: E402,F401  （P2 切片 S5 ✓ 会话身份解析，实现逐字未改）
    _IDENT, _attach_session, _resolve_session_identity, _session_ident,
)





_UUID_RE = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
                      r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")


def _pick_session_no(session_no, session, nodes, report) -> str:
    """取**会话编号**（= WeKnora `sessions.id`，uuid）。

    来源优先级：显式参数 `session_no` → `session` 字典 → `bmm:KnowledgeSession` 节点属性 → `report`。
    只接受 **uuid 形态**的值：非 uuid 一律忽略（防止把「会话-2026-10-04」这种自编号当会话编号）。
    """
    cands = [session_no]
    if isinstance(session, dict):
        cands += [session.get(k) for k in ("session_no", "sessionNo", "id")]
    for nd in (nodes or []):
        if isinstance(nd, dict) and (nd.get("type") or "").strip() == IDENTITY_CLASS:
            at = nd.get("attributes") or {}
            cands += [at.get("sessionNo"), at.get("session_no"), nd.get("session_no")]
    if isinstance(report, dict):
        cands += [report.get("session_no"), report.get("sessionNo")]
    for c in cands:
        v = str(c or "").strip()
        if v and _UUID_RE.match(v):
            return v
    return ""


def _session_page_ref(kb_id: str, session_no: str) -> tuple[str, str]:
    """本会话在**目标库**里的会话页 → `(title, slug)`；优先**分页页**（partNo 最大的 p<n>）。

    用于把 `bmm:sourceSession`（**对象属性**）连到会话页 —— 工具侧不再把它当数据属性写。
    库里没有该会话页时返回 `("", "")`（**不编造**）。
    """
    sid = (session_no or "").strip()
    if not sid:
        return "", ""
    # P3-0f（2026-10-10）：**命中即 memo**。`save_knowledge` 建页阶段每页要一次 `bmm:partNo`
    # （`_session_part_no`）→ 实测 33 页 = **32 次**同一个查询（1 次 psql ≈ 150 ms，白花 ~4.8 s）。
    # **只缓存命中**（查不到时不缓存）：会话页可能正是**本批**要建的，写库后应重新查
    # （`save_elements` 写库后会 `_reset_request_cache()`，双保险）。
    memo = _req_cache()["session_ref"]
    key = (kb_id, sid)
    if key in memo:
        return memo[key]
    hit = ("", "")
    try:
        # 2026-10-04 修：**不能只按 slug 前缀 `session/<编号>` 找** —— 实测智能体常把会话页
        # 命名成 `bmm/knowledgesession/会话-XXX`（slug 里没有编号前缀）⇒ 匹配不到、自动边挂不上。
        # 改为：在本库的 `bmm:KnowledgeSession` 页里，按 **`bmm:sessionNo` 属性值**（正文里有
        # `bmm:sessionNo（会话编号…） = <uuid>`）或 slug 前缀匹配；**分页页优先**（`/p<n>`）。
        rows = psql_csv(
            "SELECT title, slug FROM wiki_pages WHERE knowledge_base_id = %s AND deleted_at IS NULL "
            "AND page_type = 'bmm:KnowledgeSession' AND (content LIKE %s OR slug LIKE %s) "
            "ORDER BY (slug LIKE '%%/p%%') DESC, length(slug) DESC LIMIT 1"
            % (sql_str(kb_id), sql_str("%" + sid + "%"), sql_str("session/" + sid + "%")))
        if rows:
            hit = ((rows[0].get("title") or "").strip(), (rows[0].get("slug") or "").strip())
    except Exception:  # noqa: BLE001
        hit = ("", "")
    # P3-0f：命中与**未命中**都记（本次实测：本库当时没有该会话页 → 33 页白问 32 次、
    # 每次 ~150 ms）。不担心"会话页稍后由本批创建"：`save_elements` 落库后会
    # `_reset_request_cache()`、`call_tool` 入口也会清 —— memo 生命周期严格限定在"写入之前"。
    memo[key] = hit
    return hit


def _session_part_no(kb_id: str, session_no: str) -> str:
    """会话分页序号（`bmm:partNo`）——`sourceLocator` 用它定位到**具体分页**。"""
    title, slug = _session_page_ref(kb_id, session_no)
    if not slug:
        return ""
    m = re.search(r"/p(\d+)$", slug)
    return m.group(1) if m else ""


# ---------------------------------------------------------------------------
# 来源会话边（`bmm:sourceSession`）：**写入后按 slug 补双链**（2026-10-04 用户实测修正）
#   为什么不能用 payload 的 relations 自动追加：`ke_pages.relation_line()` 在**拿不到 slug**
#   时会把关系渲染成**纯文本**（`→ <标题>`）→ 页面 `in_links` 解析不到、会话页「被引用」为空
#   （用户实测："知识没有建立和会话的关系"）。
#   所以：① 写入时**不再**往 payload.relations 里塞（避免产生纯文本行）；
#         ② 写完后用本函数按 **slug** 补 `- 知识来源会话（`bmm:sourceSession`）→ [[slug|title]]`；
#         ③ 同一函数也是**显式补挂入口**（存量页补一次 / 巡检用）。
# ---------------------------------------------------------------------------
SOURCE_SESSION_REL = "bmm:sourceSession"
SOURCE_SESSION_LABEL = "知识来源会话"
REL_SECTION = "## 本体关系"
_SRC_SESS_LINE_RE = re.compile(r"^-\s*[^（(]*（`%s`）→\s*(.*)$" % re.escape(SOURCE_SESSION_REL))


def _upsert_source_session_line(content: str, line: str) -> tuple[str, bool]:
    """把来源会话行写进「## 本体关系」节（**幂等**：同目标已存在则替换成双链形态）。

    返回 `(新正文, 是否改动)`；没有该小节则**补建**小节。
    """
    lines = (content or "").splitlines()
    idx = next((i for i, l in enumerate(lines) if l.strip() == REL_SECTION), -1)
    if idx < 0:
        body = (content or "").rstrip() + "\n\n" + REL_SECTION + "\n\n" + line + "\n"
        return body, True
    # 节范围 = 标题后到下一个二级标题（或 EOF）
    end = next((j for j in range(idx + 1, len(lines)) if lines[j].startswith("## ")), len(lines))
    for j in range(idx + 1, end):
        m = _SRC_SESS_LINE_RE.match(lines[j].strip())
        if m:
            if lines[j].strip() == line:
                return content, False
            lines[j] = line
            return "\n".join(lines) + ("\n" if content.endswith("\n") else ""), True
    # 没有该行 → 插到标题后的第一个空行之后（保持小节排版）
    ins = idx + 1
    while ins < end and not lines[ins].strip():
        ins += 1
    lines.insert(ins, line)
    return "\n".join(lines) + ("\n" if content.endswith("\n") else ""), True


def _resolve_session_for(kb_id: str, session_no: str = "") -> tuple[str, str]:
    """`session_no`（WeKnora 会话 id，缺省取本线程已解析的会话身份）→ `(session_no, session_slug)`。

    解析不到 → 返回空串（**不阻断写**，由内核给 warn）。`session_no` 显式传入时优先
    （供 `review_apply` 等非 `save_knowledge` 路径——它们的会话身份**不由** `_IDENT` 承载）。
    """
    sno = str(session_no or "").strip()
    if not sno:
        se = _session_ident()
        sno = str(se.get("session_no") or "") if se.get("ok") else ""
    if not sno or not kb_id:
        return sno, ""
    try:
        kid, _ = resolve_kb_id(kb_id)
        _title, slug = _session_page_ref(kid, sno)
        return sno, (slug or "")
    except Exception:  # noqa: BLE001
        return sno, ""


_IMG_REF_RE = re.compile(r"!\[([^\]]*)\]\((resource://[^)]+)\)")


def _image_root() -> str:
    """WeKnora local 存储根（**宿主机路径** ✓）。

    2026-10-19 实测（P0 图可读）：容器 `WeKnora-app` 的 `/data/files` 挂载到
    `/var/lib/docker/volumes/weknora_data-files/_data` ✓；资源 `physical_path` 形如
    `storage://<backend>/local://<tenant>/exports/<file>` ⇒ 落盘 = `<root>/<tenant>/exports/<file>` ✓。
    可用环境变量 `BODHI_IMAGE_ROOT` 覆盖（换部署时不用改代码 ✓）。
    """
    return (os.environ.get("BODHI_IMAGE_ROOT")
            or "/var/lib/docker/volumes/weknora_data-files/_data").rstrip("/")


def _resolve_image_path(handle: str) -> tuple:
    """handle → (本地绝对路径 or "", 诊断信息 ✓)。"""
    try:
        rows = psql_csv("SELECT physical_path, mime_type, size FROM resources "
                        "WHERE handle = %s AND deleted_at IS NULL LIMIT 1" % sql_str(handle))
    except Exception as exc:  # noqa: BLE001
        return "", "resources_query_failed:%r" % (str(exc)[:60],)
    if not rows:
        return "", "resource_not_found"
    pp = str(rows[0].get("physical_path") or "")
    # storage://<backend>/local://<tenant>/<rest>  →  <root>/<tenant>/<rest>
    rest = ""
    if "local://" in pp:
        rest = pp.split("local://", 1)[1]
    elif pp.startswith("/"):
        return pp if os.path.exists(pp) else "", "abs_path_missing"
    tried = []
    for root in (_image_root(), "/data/files"):
        cand = os.path.join(root, rest) if rest else ""
        if cand:
            tried.append(cand)
            if os.path.exists(cand):
                return cand, "ok"
    return "", "storage_unresolved:%s" % ("|".join(tried[:2]) or pp[:80])


def image_extract(kb_id: str = "", knowledge_id: str = "", figure_no: int = 0,
                  inline: bool = True, max_bytes: int = 1_500_000) -> dict:
    """**取文档内嵌图（只读）**（2026-10-08）。

    WeKnora 把 docx/pptx 的**内嵌图**抽成正文里的 markdown 引用 `![alt](resource://<id>)`
    （存于 `chunks.content`，`chunk_type` 没有 `image`）。本工具把图引用取出来交给
    **会话多模态模型**看；识别按技能 `multimodal_extraction` 的**契约 v0 + 门禁 1–4** 输出。
    """
    kb = (kb_id or "").strip()
    if kb:                                  # 2026-10-19 修：入参可能是**库名**，必须先解析成 UUID ✓
        try:
            kb = resolve_kb_id(kb)[0]
        except Exception:  # noqa: BLE001
            pass
    kid = (knowledge_id or "").strip()
    where = ["deleted_at IS NULL"]
    if kb:
        where.append("knowledge_base_id = %s" % sql_str(kb))
    if kid:
        where.append("(knowledge_id = %s OR knowledge_id IN (SELECT id FROM knowledges "
                     "WHERE title LIKE %s AND deleted_at IS NULL))"
                     % (sql_str(kid), sql_str("%" + kid + "%")))
    rows = psql_csv("SELECT knowledge_id, chunk_index, COALESCE(content,'') AS content FROM chunks "
                    "WHERE %s ORDER BY knowledge_id, chunk_index" % " AND ".join(where))
    imgs, by_doc = [], {}
    for r in rows:
        for m in _IMG_REF_RE.finditer(r["content"]):
            imgs.append({"no": len(imgs) + 1, "alt": m.group(1), "image_ref": m.group(2),
                         "markdown": m.group(0), "knowledge_id": r["knowledge_id"],
                         "chunk_index": r["chunk_index"]})
            by_doc[r["knowledge_id"]] = by_doc.get(r["knowledge_id"], 0) + 1
    # 去重（2026-10-19 ✓）：同一张图常被多个 chunk 重复引用（样例2 实测 2 张图 → 4 条引用 ✗）
    seen, uniq = {}, []
    for x in imgs:
        key = x.get("image_ref") or ""
        if key in seen:
            seen[key].setdefault("also_in_chunks", []).append(x["chunk_index"])
            continue
        x["also_in_chunks"] = []
        seen[key] = x
        uniq.append(x)
    for i, x in enumerate(uniq, 1):
        x["no"] = i
    imgs = uniq
    picked = [x for x in imgs if x["no"] == int(figure_no)] if figure_no else imgs

    # **P0 图可读**（2026-10-19 ✓）：把 `resource://<handle>` 解析成宿主机文件 → base64 data URL ✓
    # （多模态模型只认 http/data URL，认不了 resource:// ✗；WeKnora 图片端点要鉴权 401 ✗）
    import base64  # noqa: PLC0415

    readable = 0
    for x in picked:
        handle = str(x.get("image_ref") or "").replace("resource://", "").strip()
        path, why = _resolve_image_path(handle) if handle else ("", "no_handle")
        x["handle"], x["resolve"] = handle, why
        if not path:
            x.update({"readable": False, "reason": why})
            continue
        try:
            size = os.path.getsize(path)
            x["bytes"] = size
            if size > int(max_bytes):
                x.update({"readable": False, "reason": "too_large", "path": path})
                continue
            with open(path, "rb") as fh:
                raw = fh.read()
            mime = ("image/png" if raw[:8] == b"\x89PNG\r\n\x1a\n"
                    else "image/gif" if raw[:3] == b"GIF"
                    else "image/jpeg")
            x.update({"readable": True, "reason": "ok", "mime": mime, "path": path})
            if inline:
                x["data_url"] = "data:%s;base64,%s" % (mime, base64.b64encode(raw).decode())
            readable += 1
        except Exception as exc:  # noqa: BLE001
            x.update({"readable": False, "reason": "read_failed:%r" % (str(exc)[:60],)})

    return {"ok": True, "kb_id": kb, "knowledge_id": kid, "images_found": len(imgs),
            "by_document": by_doc, "images": picked or imgs,
            "images_readable": readable, "inline": bool(inline),
            "how": ("**可读图**：`data_url` 直接放进推理上下文 ✓（多模态模型认 data/http，不认 `resource://` ✗）；"
                    "**不可读**（`readable=false`）⇒ 看 `reason`（`storage_unresolved` / `too_large` / "
                    "`resource_not_found`），报告里**必须写具体原因** ✗ 不许笼统写「未取得可机读图像」；"
                    "识别结果按 `multimodal_extraction` 契约 v0 + 门禁 1–4 输出，再 `save_knowledge`（带 session_no）")}


def link_source_session(kb_id: str = "", session_no: str = "", slugs: list | None = None,
                        all_pages: bool = False, dry_run: bool = True) -> dict:
    """给知识页补 `bmm:sourceSession`（**双链**）→ **会话页（分页优先）**，并重建 `in_links`。

    - 目标会话页：优先 `session_no` 指定；未给则用**本库唯一的** `bmm:KnowledgeSession` 页（多于一页时拒，不猜）；
      分页存在时**优先分页**（`_session_page_ref` 已按 partNo 优先）。
    - 要补的页：`slugs` 指定；或 `all_pages=True` 时=本库所有**缺该行**的非会话页。
    - `dry_run=True`（默认）只出清单，不写库。
    """
    kid = (kb_id or "").strip()
    if not kid:
        return {"ok": False, "reason": "缺少 kb_id"}
    kb_id, note = resolve_kb_id(kid)
    if session_no:
        title, slug = _session_page_ref(kb_id, session_no)
    else:
        rows = psql_csv("SELECT title, slug FROM wiki_pages WHERE knowledge_base_id = %s AND deleted_at IS NULL "
                        "AND page_type = 'bmm:KnowledgeSession' ORDER BY (slug LIKE '%%/p%%') DESC, "
                        "length(slug) DESC" % sql_str(kb_id))
        if len(rows) > 1:
            return {"ok": False, "kb_id": kb_id, "reason": "本库有多个会话页且未指定 session_no（不猜）",
                    "candidates": [r["slug"] for r in rows]}
        title, slug = ((rows[0].get("title") or "").strip(), (rows[0].get("slug") or "").strip()) if rows else ("", "")
    if not slug:
        return {"ok": False, "kb_id": kb_id, "reason": "本库没有会话页（先在会话里落一张 bmm:KnowledgeSession 页）"}
    line = "- %s（`%s`）→ [[%s|%s]]" % (SOURCE_SESSION_LABEL, SOURCE_SESSION_REL, slug, title)
    if slugs:
        rows = psql_csv("SELECT slug, content, out_links FROM wiki_pages WHERE knowledge_base_id = %s AND deleted_at IS NULL "
                        "AND slug IN (%s) AND COALESCE(page_type,'') NOT IN ('bmm:KnowledgeSession', 'index')"
                        % (sql_str(kb_id), ", ".join(sql_str(s) for s in slugs)))
    else:
        # 2026-10-04 修正：**不要**只挑"没有该行"的页 —— 存量页常常**有行但是纯文本形态**
        # （`→ <标题>`，正是要修的那种）。统一取本库全部非会话页，由下面两步幂等判断。
        rows = psql_csv("SELECT slug, content, out_links FROM wiki_pages WHERE knowledge_base_id = %s AND deleted_at IS NULL "
                        "AND COALESCE(page_type,'') NOT IN ('bmm:KnowledgeSession', 'index')"
                        % sql_str(kb_id))
    changes = []
    if dry_run:
        changes = [{"slug": row["slug"]} for row in rows]
    elif rows:
        try:
            import ke_graph  # noqa: PLC0415
            # 2026-10-09 P0：**批量**（旧实现每页 2 次 PG + 2 次 Neo4j；40 页 = ~160 次往返）：
            #   ① 一次预读页信息 ② 一次 UNWIND 确保节点 ③ 一次 UNWIND 建全部 sourceSession 边
            targets = [str(row["slug"]) for row in rows]
            info = ke_graph.page_info_map(kb_id, targets + [slug])
            if slug not in info:
                info[slug] = {"pt": ke_graph._page_type(kb_id, slug), "title": title, "tenant": None}
            ke_graph.ensure_instances(kb_id, [
                {"slug": k, "name": (v or {}).get("title") or k.rsplit("/", 1)[-1],
                 "page_type": (v or {}).get("pt") or "", "tenant_id": (v or {}).get("tenant")}
                for k, v in info.items()])
            ke_graph._run(
                "UNWIND $slugs AS s MATCH (a:BodhiInstance {kb_id:$kb, slug:s}) "
                "MATCH (b:BodhiInstance {kb_id:$kb, slug:$t}) "
                "MERGE (a)-[r:`bmm:sourceSession`]->(b) RETURN count(r) AS n",
                {"kb": kb_id, "slugs": targets, "t": slug})
            changes = [{"slug": t, "added": True} for t in targets]
        except Exception as exc:  # noqa: BLE001
            changes = [{"slug": str(row["slug"]), "error": str(exc)[:120]} for row in rows]
    out = {"ok": True, "kb_id": kb_id, "session_page": {"slug": slug, "title": title},
           "dry_run": bool(dry_run), "changed": len(changes), "changes": changes[:50]}
    return out


def _attrs_with_identity(node: dict, kb_id: str, injected: dict) -> dict:
    """节点数据属性（含**身份注入**与**来源会话注入**）。

    规则（2026-10-04 用户口径）：
      · `bmm:KnowledgeSession` 节点：`sessionNo` / `agentName` **由会话身份覆盖**（客户端值丢弃）、
        `tenantName` = 租户名（服务端查 `tenants.name`）；
      · **其它节点**：自动补 `bmm:sourceSession`（= `session/<会话编号>`）与
        `bmm:sourceLocator` 兜底（= `session/<会话编号>/p<分页序号>`）→ 每条知识都能回到会话；
      · **无 env 兜底**：会话身份解析不到时，`save_knowledge` 层已经**拒绝写库**（fail-closed）。
    """
    attrs = dict(node.get("attributes") or {})
    ident = _session_ident()
    is_session_node = (node.get("type") or "").strip() == IDENTITY_CLASS

    if is_session_node:
        # 键统一 prefixed（2026-10-05 B1）：清两套键，服务端只注入 prefixed 权威值
        for k in ("tenantName", "bmm:tenantName", "sessionNo", "bmm:sessionNo",
                  "agentName", "bmm:agentName"):
            attrs.pop(k, None)
        tenant = get_kb_tenant(kb_id)
        if tenant not in (None, ""):
            attrs["bmm:tenantName"] = _tenant_name(tenant)
            injected["bmm:tenantName"] = attrs["bmm:tenantName"]
        if ident.get("ok"):
            attrs["bmm:sessionNo"] = ident["session_no"]
            attrs["bmm:agentName"] = ident["agent_name"]
            injected["bmm:sessionNo"] = ident["session_no"]
            injected["bmm:agentName"] = ident["agent_name"]
        return attrs

    # 非会话节点：只自动补「会话片段定位」数据属性 `bmm:sourceLocator`（B1：统一 prefixed）。
    # `bmm:sourceSession` 是对象属性（关系）⇒ 由 `design_elements`/`link_source_session` 补边。
    attrs.pop("sourceLocator", None)            # 清无前缀旧键（去重）
    if ident.get("ok"):
        part = _session_part_no(kb_id, ident["session_no"])
        loc = ("session/%s/p%s" % (ident["session_no"], part)) if part \
            else ("session/%s" % ident["session_no"])
        attrs["bmm:sourceLocator"] = loc         # 覆盖抽取可能传的“文档章节”值（B1：sourceLocator=会话片段定位）
        injected["bmm:sourceLocator"] = loc
    return attrs


def _chunk_locator(chunk_index) -> str:
    """`chunk_index` → 「片段 #n」；无效/负值返回空串（**不编造定位**）。"""
    try:
        n = int(chunk_index)
    except (TypeError, ValueError):
        return ""
    return "片段 #%d" % n if n >= 0 else ""


def _as_chunk_index(chunk_index) -> int:
    try:
        return int(chunk_index)
    except (TypeError, ValueError):
        return -1


def _tenant_name(tenant_id) -> str:
    """租户 id → **租户名**（`tenants` 表；查不到就回落 id 本身）。"""
    try:
        rows = ke_db.psql_csv("SELECT name FROM tenants WHERE id = %d LIMIT 1" % int(tenant_id))
        name = ((rows[0].get("name") if rows else "") or "").strip()
        return name or str(tenant_id)
    except Exception:  # noqa: BLE001  查不到不改写主流程
        return str(tenant_id)


def save_knowledge(kb_id: str = "", *, stage: str = "report", model: str = "bmm",
                   report: dict | None = None, nodes: list | None = None, edges: list | None = None,
                   mode: str = "dry_run", confirmed_new_applications: list | None = None,
                   high: float = DEFAULT_HIGH, low: float = DEFAULT_LOW,
                   session: dict | None = None, kb_ids: list | None = None,
                   confirm_kb_match: bool = False, context: str = "",
                   session_no: str = "") -> dict:
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
    # 本体模型库只读（2026-10-15 用户口径）：`save_knowledge` 不能直接改本体库内容（
    # 本体库只能靠"加载编译"更新：改 ontology/*.ttl → 前端「上传本体文件」/ load_model）
    if ke_ontology.is_ontology_kb(kb_id):
        return {"ok": False, "kb_id": kb_id, "refused": "ontology_kb_readonly",
                "error": ("本体模型库（%s…）**只读**：不能用 `save_knowledge` 直接改它的内容 —— "
                          "请改 `ontology/*.ttl` 后走**加载编译**（前端「上传本体文件」或 `load_model`）。"
                          % str(kb_id)[:8])}
    ctx = context_meta(context)
    engine = load_engine()
    model_obj = engine.pick_model(engine.load_index(engine.DEFAULT_INDEX), model)
    stage = (stage or "report").strip().lower()
    mode = (mode or "dry_run").strip().lower()
    if stage not in ("report", "graph"):
        raise ValueError("stage 只能是 report / graph")
    if mode not in ("dry_run", "apply"):
        raise ValueError("mode 只能是 dry_run / apply")
    # —— 会话身份门禁（2026-10-04 用户口径，fail-closed）——
    # `agentName` **只认会话**：`sessionNo` → `sessions.agent_config->>'agent_id'` → `custom_agents.name`；
    # 不信任客户端自报、**无 env 兜底**；**取不到会话的智能体名称 → 不允许触碰知识库**
    # （apply 直接拒写；dry_run 只回报、不落库）。同一批次的其它页也用它自动挂 `sourceSession`。
    ident = _resolve_session_identity(_pick_session_no(session_no, session, nodes, report))
    _IDENT.ident = ident if ident.get("ok") else None
    if not ident.get("ok"):
        if mode == "apply":
            return {"ok": False, "stage": stage, "need_session": True,
                    "reason": ident.get("reason"), "kb_id": kb_id,
                    "hint": ("落库需要真实会话上下文：请把 WeKnora **会话编号**作为 `session_no`"
                             "（或写在会话页属性 `bmm:sessionNo`）传入。服务端据此取出"
                             "「会话选择的智能体」作为 `agentName`，并给本次所有页自动挂 `bmm:sourceSession`。")}
        session_warn = ident.get("reason")
    else:
        session_warn = ""
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

    # P0-b（2026-10-09）：把**重型维护**移出落库热路径（全库目录重建 / CRUD 矩阵刷新）。
    # 默认延迟 —— 前端进 wiki tab 时会调 `/bodhi/folders/refresh`（接口已存在，就是为
    # "目录更新早于最新页面"加的）；要同步等可以用 BODHI_DEFER_MAINTENANCE=0 关掉。
    defer_maintenance = str(os.environ.get("BODHI_DEFER_MAINTENANCE", "1")).lower() \
        not in ("0", "false", "no")

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
        # P0-c（2026-10-09）：**报告分支改走内核** `write_knowledge(mode="document")`。
        # 报告就是「文档类」：调用方给整篇正文（`wiki_content`）。相比 legacy `save_elements`：
        #   · 不再做相似度合并/待确认（用户口径：按 slug 幂等 upsert）；
        #   · 不再"整批一个事务"（内核按 (kb,slug) 幂等 + 逐条小事务）；
        #   · **会话溯源边由内核自动挂**（`source.session_no` → `ensure_session_page` + `bmm:sourceSession`），
        #     所以本分支不再手工调 `link_source_session`；
        #   · 溯源列 `source_refs`（L3 兼容字段）与正文「## 原文依据」由内核写。
        _ptype_raw = ((report or {}).get("page_type") or "").strip() or "bmm:TechnicalReviewReport"
        # **硬约束（2026-10-10，用户实测修正）**：报告页类型只能取**本体类**。
        # 实测：领域建模智能体在 `report.page_type` 里顺手传了 WeKnora 内建的 `summary`
        # （不在本体里、正是要退役的"不合理模式"）→ 页面就落成了摘要类型。
        # 既然"渲染/类型由本体决定"，就不该让调用方把非本体类型带进来 —— 这里直接改写，
        # 并在回执里给 `page_type_adjusted` 说明（可核对、可追溯）。
        _ptype, _ptype_note = _ptype_raw, None
        try:
            _known_classes = ke_ontology.class_meta()
        except Exception:  # noqa: BLE001  本体读不到 → 保持原值（不强行改，避免误伤）
            _known_classes = {}
        if _known_classes and _ptype_raw not in _known_classes:
            _fallback = ("bmm:TechnicalReviewReport" if "bmm:TechnicalReviewReport" in _known_classes
                         else _ptype_raw)
            if _fallback != _ptype_raw:
                _ptype = _fallback
                _ptype_note = {
                    "from": _ptype_raw, "to": _fallback,
                    "why": ("`%s` 不是本体类（WeKnora 内建 wiki 类型）→ 报告属**文档类**，"
                            "已按本体改写为 `%s`（要固定其它类型请传本体类名）"
                            % (_ptype_raw, _fallback)),
                }
        _upstream = [s for s in ((report or {}).get("upstream") or []) if s]
        # 报告**只读校验**（2026-10-19 用户口径 Q2=C ✓：只告警，不静默改 ✗）
        try:
            import ke_report as _kr  # noqa: PLC0415
            _report_warns = _kr.check_report_md(body)
        except Exception:  # noqa: BLE001
            _report_warns = []
        spec = {
            "slug": slug,                    # 复用既有报告页时由上面的幂等查询给出；空则由内核派生
            "title": title, "page_type": _ptype, "type_label": _type_label_of(_ptype, ctx["report_label"]),
            "mode": "document",              # 报告=文档类；**不依赖本体判定**（legacy 的 `summary` 不是本体类）
            "wiki_content": body,
            "summary": (report or {}).get("summary") or "",
            "source": {
                "doc_refs": [doc_meta["id"]] if doc_meta.get("id") else [],
                "doc_title": doc_meta.get("title", ""),
                "source_text": ctx["report_source"],
                "derived_from": _upstream,
            },
            "metadata_extra": {"design": {"upstream": _upstream, "context": ctx["id"]}},
            "aliases": (report or {}).get("aliases") or [],
        }
        if ident.get("ok"):
            spec["source"]["session_no"] = ident["session_no"]
            spec["source"]["agent_name"] = ident.get("agent_name", "")
        res = ke_pages.write_knowledge(
            kb_id, spec, dry_run=(mode != "apply"),
            strict_source=bool(doc_meta.get("id") or ident.get("ok")),
            sync_folders=not defer_maintenance)
        _created = bool(res.get("created"))
        _action = ("created" if _created else "updated") if mode == "apply" else "preview"
        _graph = res.get("graph") or {}
        summary = {
            "model": model_obj["key"], "kb_id": kb_id, "knowledge_id": doc_meta["id"],
            "resolved_note": (kb_note or "").strip(), "doc_title": doc_meta.get("title", ""),
            "elements": 1, "relationships": 0,
            "created": [{"name": title, "type": _ptype, "slug": res.get("slug")}] if _created else [],
            "merged": ([] if _created else
                       [{"name": title, "type": _ptype, "into": res.get("slug"),
                         "similarity": 1.0, "note": "按 (kb,slug) 幂等更新"}]),
            "pending": [], "violations": [], "unmatched": [], "dropped_relations": [],
            "dry_run": mode != "apply", "applied": mode == "apply",
            "thresholds": {"high": high, "low": low}, "generated_at": now_text(),
            "page_versions": [{"slug": res.get("slug"), "before": res.get("before_version"),
                               "action": _action,
                               "applied": mode == "apply"}],
            "graph": _graph, "warnings": res.get("warnings") or [],
            "report_warnings": _report_warns,
            "source_session_edges": [],
            "stage": "report", "context": ctx["id"],
            "maintenance": "deferred" if defer_maintenance else "inline",
            "engine": "kernel:write_knowledge/document",
            "report_page": {"slug": res.get("slug"), "title": title, "chars": len(body),
                            "type_label": _type_label_of(_ptype, ctx["report_label"]), "page_type": _ptype,
                            "action": _action,
                            "category_path": (report or {}).get("category_path")
                                             or [ctx["report_category"]],
                            "session_page": _graph.get("session_page")},
            "write_check": {"kb_id": kb_id, "mode": "kernel", "expected": 1,
                            "verified": 1 if mode == "apply" else 0,
                            "note": "落库由内核完成（按 (kb,slug) 幂等、逐条小事务）"},
        }
        if _ptype_note:
            summary["page_type_adjusted"] = _ptype_note
        if mode != "apply":
            summary["write_note"] = ("**未写库**（dry_run）：本回执只是预览。要真正落库请用**同一份载荷**、"
                                     "`mode=\"apply\"` 重跑。")
        if _graph.get("session_page"):
            summary["source_session_link"] = dict(_graph["session_page"], ok=True, via="kernel")
        elif mode == "apply" and not ident.get("ok"):
            summary["source_session_link"] = {"ok": False, "reason": session_warn or "无会话身份"}
        return _attach_session(summary, ident, session_warn)

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
                            resolved_note=kb_note, engine=engine, same_type_only=True,
                            skip_folders=defer_maintenance, skip_crud=defer_maintenance)
    summary["stage"] = "graph"
    summary["pending_confirmation"] = blocked
    summary["context"] = ctx["id"]
    summary["maintenance"] = ("deferred" if defer_maintenance else "inline")
    # P0-b（2026-10-09）：**把关系真正写进图** —— 旧实现只把关系渲染进页面正文、不写图，
    # 所以出现「索引列表先出现、本体图谱要等一段时间」。批量写：一次预读 + 一次
    # `ensure_instances` + 逐条 `add_edge(ctx)`（每条边 1-2 次 Neo4j），幂等可重跑。
    if mode == "apply":
        _slug_of = {}
        for _e in (summary.get("created") or []):
            if _e.get("name") and _e.get("slug"):
                _slug_of[_e["name"]] = _e["slug"]
        for _e in (summary.get("merged") or []):
            if _e.get("name") and _e.get("into"):
                _slug_of[_e["name"]] = _e["into"]
        _pairs = []
        for _p in allowed:
            _src = _slug_of.get(_p.get("name")) or _p.get("slug")
            for _rel in (_p.get("relations") or []):
                if _src and _rel.get("type") and _rel.get("target_slug"):
                    _pairs.append({"source": _src, "type": _rel["type"],
                                   "target_slug": _rel["target_slug"]})
        if _pairs:
            try:
                summary["graph_edges"] = ke_pages.write_relations_batch(kb_id, _pairs)
            except Exception as exc:  # noqa: BLE001  写边失败不影响页面落库，但必须回报
                summary["graph_edges"] = {"ok": False, "total": len(_pairs),
                                          "written": 0, "errors": [{"error": str(exc)[:200]}]}
    # 来源会话边回执（2026-10-04）：服务端自动补的 `bmm:sourceSession`（对象属性）——
    # 让调用方能一眼确认"本次每条知识都连回了会话页"（用户口径）。
    # 写入后**按 slug 补**来源会话边（双链）→ 会话页「被引用」可见；回执给明细（2026-10-04 修正）
    if mode == "apply":
        _se = _session_ident()
        _slugs = [e.get("slug") for e in (summary.get("created") or []) if e.get("slug")]
        _slugs += [e.get("into") for e in (summary.get("merged") or []) if e.get("into")]
        if _se.get("ok") and _slugs:
            try:
                summary["source_session_link"] = link_source_session(
                    kb_id, _se["session_no"], slugs=_slugs, dry_run=False)
            except Exception as exc:  # noqa: BLE001  补挂失败不影响落库，但要在回执里说清
                summary["source_session_link"] = {"ok": False, "reason": str(exc)[:200]}
    summary["source_session_edges"] = [e for e in (checked.get("edges") or [])
                                       if e.get("type") == "bmm:sourceSession"]
    # **实例图投影（2026-10-05 全量重构：Neo4j 为主存储）**：落库后按 PG 全量重建该库实例层
    #（幂等；节点带 kb_id 隔离、边=本体对象属性）。失败不阻断落库，但回执里必须说清。
    if mode == "apply":
        # 2026-10-05 M3（图主补全）：① 关系**直写图边**（正文不再承载关系）；② 正文只 strip（不动图边）。
        try:
            import ke_graph  # noqa: PLC0415
            _n_edges = 0
            _edge_errors = []
            _planned = 0
            _triples: list = []          # P3-0f2：先收集，再**批量**建边（见下方）
            # 2026-10-05 M3 修（回归缺陷）：边一律用 `design_elements` **已解析好的**
            # `payload.relations[].target_slug`（覆盖"本批节点"与"本库既有页"两种目标）；
            # 旧实现遍历 `checked["edges"]` 的**裸节点名**、且用 `":" in src` 判错变量（src 是 slug/名，
            # 恒无冒号 → 条件恒假）→ 知识间边一条都没写。这里同时**不再静默失败**，回执给 `edge_errors`。
            for _p in payloads:
                _src = _p.get("slug")
                for _r in (_p.get("relations") or []):
                    _t, _dst = _r.get("type"), _r.get("target_slug")
                    if _src and _dst and _t:
                        _planned += 1
                        _triples.append((_src, _t, _dst))
            _all = [e.get("slug") for e in (summary.get("created") or []) if e.get("slug")]
            _all += [e.get("into") for e in (summary.get("merged") or []) if e.get("into")]
            _all = list(dict.fromkeys(_all))
            # P3-0f2（2026-10-10）：**批量写图 + 批量 strip**。
            # 旧实现：每条边 `add_edge`（4 次 PG + 5 次 Neo4j，实测 `_page_type`=2×边数、
            # `_ensure_instance`=2×边数）+ 每页逐条 strip（读正文 + UPDATE 各 1 次 psql）→ 27 边/33 页 =
            # **190 次 psql / 25-31 s**，超过客户端 ~30 s 超时 → 前端重发同一批 → **重复版本**。
            # 现在：① 一次 `page_info_map` 预读页信息（免掉每条边 2 次 `_page_type`）；
            # ② 一次 `ensure_instances` UNWIND 确保节点（免掉每条边 2 次 `_ensure_instance`）；
            # ③ `add_edges_batch` 按关系类型分组 UNWIND（N 边 → 去重后类型数次 Neo4j）；
            # ④ `strip_wiki_pages` 整批一次读 + 一条 `UPDATE..FROM (VALUES)`。
            _info = ke_graph.page_info_map(
                kb_id, [s for t in _triples for s in (t[0], t[2])] + _all)
            _nodes = [{"slug": s, "name": (v or {}).get("title") or s.rsplit("/", 1)[-1],
                       "page_type": (v or {}).get("pt") or "", "tenant_id": (v or {}).get("tenant")}
                      for s, v in (_info or {}).items()]
            if _nodes:
                ke_graph.ensure_instances(kb_id, _nodes)
            _res = (ke_graph.add_edges_batch(kb_id, _triples,
                                            {"info": _info, "nodes_ensured": True})
                    if _triples else {"written": 0, "errors": [], "types": 0, "total": 0})
            _edge_errors = list(_res.get("errors") or [])
            # 回执口径与旧版一致：`edges_written` = **校验通过并入图的条目数**
            # （旧版是"成功调用 add_edge 的条数"）；另给 `edge_merge_hits` 是 MERGE 命中数
            # （含"同样的边已存在"，因为 `MERGE` 命中既有边也计 1）。
            _n_edges = _planned - len(_edge_errors)
            _strip = ke_graph.strip_wiki_pages(kb_id, _all)
            summary["graph"] = {"edges_written": _n_edges, "edges_planned": _planned,
                                "edge_errors": _edge_errors[:20],
                                "edge_errors_total": len(_edge_errors),
                                "edge_types": _res.get("types"),
                                "edge_merge_hits": _res.get("written"),
                                "stripped": _strip.get("stripped"),
                                "strip_total": _strip.get("total"),
                                "batched": True}
            # C 回归防护：声明了边却一条都没落图 → 显式告警（防"循环静默失效"回归）
            if _planned and _n_edges == 0:
                summary["graph"]["warn"] = (
                    "声明了 %d 条关系，但一条都没写入图（检查关系解析/图连接）" % _planned)
        except Exception as exc:  # noqa: BLE001
            summary["graph"] = {"ok": False, "reason": str(exc)[:200]}
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
    # 会话身份回执（2026-10-04）：说明本次落库用的**会话事实**（agentName 来自会话而非自报）
    return _attach_session(summary, ident, session_warn)


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



def _page_slug_by_title(kb_id: str, title: str) -> str:
    """按标题在**本库**里找 slug（设计载荷里的 source 可能是本批新建的页）。"""
    if not title:
        return ""
    rows = psql_csv("SELECT slug FROM wiki_pages WHERE knowledge_base_id = %s AND deleted_at IS NULL "
                    "AND title = %s ORDER BY updated_at DESC LIMIT 1"
                    % (sql_str(kb_id), sql_str(title)))
    return rows[0]["slug"] if rows else ""


def retract_relations(kb_id: str, items: list) -> dict:
    """撤回关系（**2026-10-05 M3：关系是图本态**）——删 **Neo4j 图边**（`ke_graph.delete_edge`）。

    为什么需要：`save_knowledge` 的语义是"只追加/合并"，改设计（例如把某个写方收口）时必须能**减边**；
    否则旧边永远留在图里，巡检（E1 写耦合等）也跟着失真。
    旧实现删的是正文 `## 本体关系` 行——M3 后正文已不承载关系 → 删不掉任何边；这里改为删图边。
    """
    import ke_graph  # noqa: PLC0415
    done, missed = [], []
    for item in (items or []):
        src_slug = (item.get("source_slug")
                    or _page_slug_by_title(kb_id, item.get("source") or "")
                    or _page_slug_by_title(kb_id, item.get("source_slug") or ""))
        if not src_slug:
            missed.append({**item, "why": "源页不存在"})
            continue
        rel_type = item.get("type") or ""
        target_slug = item.get("slug") or _graph_target_slug(kb_id, str(item.get("target") or ""))
        if not target_slug:
            missed.append({**item, "why": "目标解析不到 slug", "source_slug": src_slug})
            continue
        # 先确认图里确有这条出边（否则回报 missed，语义与旧实现一致）
        rels = ke_graph.relations_of(kb_id, src_slug) or {}
        hit = any((o.get("target_slug") == target_slug) and (not rel_type or o.get("type") == rel_type)
                  for o in (rels.get("out") or []))
        if not hit:
            missed.append({**item, "why": "图里没有这条出边（可能已删）", "source_slug": src_slug})
            continue
        ke_graph.delete_edge(kb_id, src_slug, target_slug, rel_type)
        done.append({**item, "source_slug": src_slug, "target_slug": target_slug})
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
    """解析页里 `## 属性（数据属性）` 小节 → {prefixed: 值}（如 `easvc:keyRole` = PK）。

    双读（2026-10-08 契约 §4.3）：新 `### 名称（prefixed，range）` + 后续行（可多行）；旧 `- 名… = 值`。
    """
    lines = (page_content or "").splitlines()
    start = next((i for i, line in enumerate(lines) if line.strip() == "## 属性（数据属性）"), -1)
    if start < 0:
        return {}
    end = next((i for i in range(start + 1, len(lines)) if lines[i].startswith("## ")), len(lines))
    out, cur, buf = {}, None, []

    def _flush():
        nonlocal cur, buf
        if cur and buf:
            out[cur] = "\n".join(buf).strip()
        cur, buf = None, []

    for idx in range(start + 1, end):
        s = lines[idx].strip()
        if s.startswith("### "):
            _flush()
            m = re.search(r"[A-Za-z_]\w*:[A-Za-z_]\w*", s[4:])
            cur = m.group(0) if m else s[4:].strip()
            continue
        hit = _ATTR_VALUE.match(s)
        if hit:
            _flush()
            out[hit.group("name").strip()] = hit.group("value").strip()
            continue
        if cur is not None:
            buf.append(lines[idx].rstrip())
    _flush()
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
    return save_knowledge(kb_id, stage="report", model="bmm", mode="apply",
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


DETAIL_KEYS = ("created", "merged", "violations", "unmatched")


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


from srv.schemas import TOOL_SCHEMAS  # noqa: E402  （P2 切片 S8a ✓ schema 单一来源）


def tool_definitions() -> list[dict]:
    """工具 schema 列表（**单一来源** = `srv/schemas.py` ✓）"""
    return TOOL_SCHEMAS

from srv.tools_read import (  # noqa: E402,F401  （P2 切片 S8b-1 ✓ 只读薄封装）
    audit_scan, context_authority, context_lookup, context_page, context_scan,
)


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


def rebuild_wiki_from_graph(kb_id: str = "", dry_run: bool = True) -> dict:
    """**运维 · 图→wiki 全量重建**：按图节点的 `wiki_content` 重写 PG `wiki_pages.content`。

    用途（2026-10-10 纳入运维）：**正本是图、正文是派生** —— PG 正文被误改/丢失而图仍完整时，
    用它把正文从图恢复。图节点没有 `wiki_content` 的页（index / 原生页）**不动**；
    关系只在图里、不回写正文（重建后出入链清空）。
    `dry_run=True`（默认）只报影响面。**改本体类型 / 迁移 slug 不走这里**（仍走两段式 retag）。
    """
    import ke_graph as _kg  # noqa: PLC0415
    kid = (kb_id or "").strip()
    if not kid:
        return {"ok": False, "reason": "缺少 kb_id"}
    real_kb, note = resolve_kb_id(kid)
    res = _kg.rebuild_kb_wiki(real_kb, dry_run=bool(dry_run))
    out = {"ok": True, "kb_id": real_kb, "kb_note": note, "dry_run": bool(dry_run),
           "note": "图→PG 正文重建（运维恢复手段）；关系只在图里，正文不写回关系；"
                   "图节点无 wiki_content 的页保持不动"}
    if isinstance(res, dict):
        out.update(res)
    return out





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


# ---------------------------------------------------------------------------
# P3-0i3（2026-10-10）：**同载荷重入保护** —— 客户端超时重发不再写出重复版本
#
# 实测（内网 09:40）：apply 一次 25-31 s，**超过客户端 ~30 s 超时** → 前端把**同一份载荷**
# 又发了一次（日志两条 BEGIN 相隔 30 s、args 文件不同内容相同）→ 同一批被写两遍、
# 页版本 +2（用户看到"重复版本"）。P3-0f2 把 apply 压到超时内之后应当不再触发，
# 但**兜底必须留着**：同一 (工具, 载荷指纹) 在窗口内再次到达 → **附着到正在跑的那一次**，
# 等它出结果后**原样返回同一份回执**（并标 `reentry.deduped=true`），绝不重复写库。
# ---------------------------------------------------------------------------
_REENTRY: dict = {}                                  # key → {"event","result","done","at"}
_REENTRY_LOCK = threading.Lock()
REENTRY_TOOLS = ("save_knowledge",)                  # 只有会产生版本/页面的写工具需要
REENTRY_WINDOW_S = 600                               # 兜底窗口（远大于客户端超时即可）


def _reentry_key(name: str, args: dict) -> str:
    """同载荷指纹；**只对真正的写库调用**生效（dry_run 幂等，重复无害，不该被挡）。"""
    if name not in REENTRY_TOOLS:
        return ""
    if str((args or {}).get("mode") or "dry_run").strip().lower() != "apply":
        return ""
    blob = json.dumps(args, ensure_ascii=False, sort_keys=True, default=str)
    return "%s|%s" % (name, hashlib.sha1(blob.encode("utf-8")).hexdigest())


def _prune_reentry(now: float) -> None:
    for k, v in list(_REENTRY.items()):
        if v.get("done") and now - float(v.get("at") or 0.0) > REENTRY_WINDOW_S:
            _REENTRY.pop(k, None)


def call_tool(name: str, args: dict) -> dict:
    """工具**唯一入口**：同载荷重入保护（P3-0i3）→ 真正实现（`_call_tool_impl`）。"""
    key = _reentry_key(name, args)
    if not key:
        return _call_tool_impl(name, args)
    with _REENTRY_LOCK:
        _prune_reentry(time.time())
        slot = _REENTRY.get(key)
        if slot is None:
            slot = {"event": threading.Event(), "result": None, "done": False, "at": time.time()}
            _REENTRY[key] = slot
            owner = True
        else:
            owner = False
    if not owner:
        slot["event"].wait(timeout=REENTRY_WINDOW_S)     # 等正在跑的那一次出结果
        prev = slot.get("result")
        if isinstance(prev, dict):
            out = dict(prev)
            out["reentry"] = {"deduped": True, "note": (
                "同一份载荷已由本服务处理（客户端超时重发）：**未重复写库**，"
                "本回执与那一次完全一致。")}
            return out
        with _REENTRY_LOCK:                              # 上一次没留下回执（异常/中断）→ 自己跑
            slot = {"event": threading.Event(), "result": None, "done": False, "at": time.time()}
            _REENTRY[key] = slot
    try:
        result = _call_tool_impl(name, args)
    except Exception:
        slot["event"].set()
        raise
    slot["result"] = result
    slot["done"] = True
    slot["at"] = time.time()
    slot["event"].set()
    return result


_RETIRED_TOOLS = {
    "rules_of_policy": "改用 `rules_resolve(spec=…)`（同一份图本优先实现 ✓）",
    "review_apply": "评审结论改由 save_knowledge(mode=document) 落 `bmm:TechnicalReviewReport` 一页 ✓（本工具会产 `bmm:Assessment` 旧口径 ✗）",
    "service_overview": "旧「服务概览」，被 `knowledge_lookup` + `doc_outline` 覆盖 ✓",
    "rebuild_wiki_from_graph": "重建页已并入保存路径（图→PG 写序）✓，请用 `save_knowledge` / `recategorize`",
    "link_source_session": "会话溯源由 `save_knowledge` 自动写 `bmm:sourceSession` ✓",
    "link_candidates": "候选关联旧流程 ✗；关系随 `save_knowledge` 的 relations 一次写入 ✓",
    "list_link_candidates": "同上 ✗",
    "resolve_link_candidate": "同上 ✗",
    "import_state": "结构化导入进度可由 `import_plan` 回执查看 ✓",
    "import_refresh": "同上 ✗（重跑 `import_plan` 即刷新 ✓）",
    "recategorize": "目录由 `ke_category.recompute` + `sync_folders` 在写库时自动收敛 ✓（CLI: `python3 -m ke_category`）",
    "audit_kb": "与 `audit_scan` 重复 ✓，统一用 `audit_scan`",
    # 2026-10-21：以下两个是**早期就删掉、但没进退役表**的（日志里还能看到调用 ⇒ 属"悄悄消失"✗）。
    # 补进来只为**可解释**：调用方拿到明确指引，而不是莫名 unknown tool。
    "resolve_pending_merge": "「相似度合并」已永久取消（2026-10-10）⇒ 待确认合并不复存在；按 `(kb, slug)` 幂等 `save_knowledge` 即可 ✓",
    "selftest": "自检改为 `pytest tests`（回归套件 ✓）+ `audit_scan`（数据体检 ✓）",
}


def _call_tool_impl(name: str, args: dict) -> dict:
    if name in _RETIRED_TOOLS:
        return {"ok": False, "deprecated": True, "since": "2026-10-18",
                "tool": name, "error": "该工具已退役", "hint": _RETIRED_TOOLS[name]}
    # P3-0f（2026-10-10）：每次工具调用先清**请求级只读 memo**（页 id / 属主 / 会话页引用）。
    # 放在这里而不是各工具里：唯一入口、线程局部、绝不会把上一次调用的结果带进来。
    _reset_request_cache()
    if name == "job_status":
        return job_status(str(args.get("job_id", "")))
    # （原 `list_pending_merges` / `resolve_pending_merge` 两个工具已随「相似度合并永久取消」删除，2026-10-10）
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
    if name == "kb_export":
        return kb_export(str(args.get("kb_id", "")), str(args.get("out_dir", "")),
                         bool(args.get("make_zip", True)))
    if name == "kb_import":
        return kb_import(str(args.get("source", "")), str(args.get("kb_id", "")),
                         str(args.get("new_kb", "")), bool(args.get("keep_ids", False)),
                         bool(args.get("verify", True)))
    if name == "rebuild_wiki_from_graph":
        return rebuild_wiki_from_graph(str(args.get("kb_id", "")), bool(args.get("dry_run", True)))
    if name == "audit_scan":
        return audit_scan(str(args.get("kb_id", "")), args.get("kb_ids") or None,
                          str(args.get("scope", "all")), int(args.get("max_findings", 50)))
    if name == "audit_plan":
        return ke_audit.build_plan(str(args["kb_id"]), args.get("kinds", "all"),
                                   str(args.get("scope", "all")))
    if name == "link_source_session":
        return link_source_session(str(args.get("kb_id", "")), str(args.get("session_no", "")),
                                   slugs=args.get("slugs") or None, all_pages=bool(args.get("all")),
                                   dry_run=not bool(args.get("apply")))
    if name == "audit_kb":
        import ke_graph  # noqa: PLC0415
        return ke_graph.audit_kb(str(args.get("kb_id", "")), fix=bool(args.get("fix")))
    if name == "recategorize":
        return recategorize(str(args.get("kb_id", "")), dry_run=not bool(args.get("apply")),
                            depth=int(args.get("depth", 1) or 1))
    if name == "save_knowledge":
        return save_knowledge(
            str(args["kb_id"]), stage=str(args.get("stage", "report")),
            model=str(args.get("model", "bmm")), report=args.get("report"),
            nodes=args.get("nodes") or [], edges=args.get("edges") or [],
            mode=str(args.get("mode", "dry_run")),
            confirmed_new_applications=args.get("confirmed_new_applications") or [],
            session=args.get("session") or None, kb_ids=args.get("kb_ids") or None,
            confirm_kb_match=bool(args.get("confirm_kb_match")),
            context=str(args.get("context", "")),
            session_no=str(args.get("session_no", "")))
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
    if name == "image_extract":
        return image_extract(str(args.get("kb_id", "")), str(args.get("knowledge_id", "")),
                             int(args.get("figure_no", 0) or 0))
    if name == "page_attributes_set":
        _pa_dry = args.get("dry_run", True)
        if isinstance(_pa_dry, str):
            _pa_dry = _pa_dry.strip().lower() not in ("0", "false", "no", "off", "")
        return ke_pages.set_page_attributes(str(args.get("kb_id", "")), str(args.get("slug", "")),
                                            args.get("patch") or {},
                                            actor=str(args.get("actor", "")),
                                            dry_run=bool(_pa_dry))
    if name == "extract_candidates":
        # 2026-10-17：**候选抽取**（只定位 ✓ 不判定 ✗）—— 判读交给知识提取技能/模型 ✓
        import ke_extract  # noqa: PLC0415
        return ke_extract.extract(str(args.get("kb_id", "")), str(args.get("knowledge_id", "")),
                                  str(args.get("domain_kb", "")),
                                  str(args.get("task", "逐项评审") or "逐项评审"),
                                  args.get("other_kbs") or None,
                                  int(args.get("limit_items", 20) or 20))
    if name == "evidence_score":
        # 2026-10-17：**证据/结论可信度**（只读纯函数 ✓，口径唯一 ✓，智能体不必自己算 ✗）
        import ke_score  # noqa: PLC0415
        return ke_score.score_rule(args.get("facts") or [], args.get("checks") or [],
                                   float(args.get("threshold", 0.60) or 0.60))
    if name == "knowledge_lookup":
        # 2026-10-17：**统一检索入口**（rules / keyword 图优先 / path）
        import ke_lookup  # noqa: PLC0415
        return ke_lookup.knowledge_lookup(
            str(args.get("kb_id", "")), str(args.get("mode", "rules") or "rules"),
            str(args.get("query", "")), str(args.get("spec", "")), str(args.get("start", "")),
            int(args.get("depth", 2) or 2), int(args.get("limit", 20) or 20))
    if name == "rules_resolve":
        # 2026-10-16（用户口径）：**按步骤解析规则**（direct/policy 递归/ask/task 兼容/path 预留）
        return ke_review.rules_resolve(str(args.get("kb_id", "")), str(args.get("spec", "")),
                                       int(args.get("limit", 300) or 300))
    if name == "rules_of_task":
        # 2026-10-18（用户口径 ✓）：**已下架** ✗ —— 旧"任务→规则（taskExecutesRule）"路径已删除 ✓
        return {"ok": False, "deprecated": True, "error": "rules_of_task 已下架",
                "hint": ("规则只按步骤解析：`knowledge_lookup(kb_id=<领域库>, mode=\"rules\", "
                         "spec=\"policy:<策略>\" | \"direct:<规则slug,…>\" | \"ask\")` ✓；"
                         "若要按策略取全部规则：spec=\"policy:技术方案逐项评审策略\" ✓"),
                "since": "2026-10-18"}
    if name == "rules_of_task_legacy":
        # 2026-10-16（用户口径）：**按任务取规则契约**（图边 taskExecutesRule + 页面属性真读；
        # 带 needs_extract 分流 / required_fields / source 出处 / hash 指纹 ⇒ 反假断言可验）
        return ke_review.rules_of_task(str(args.get("kb_id", "")), str(args.get("task", "")),
                                       int(args.get("limit", 300) or 300))
    if name == "rules_of_policy":
        return ke_review.rules_of_policy(str(args.get("kb_id", "")), str(args.get("policy", "")),
                                         int(args.get("limit", 300) or 300))
    if name == "graph_query":
        # 2026-10-18（用户口径 ✓）：**归一化到 knowledge_lookup** ✗ 不再建议直接拼 Cypher
        return {"ok": False, "deprecated": True, "error": "graph_query 已归一化到 knowledge_lookup",
                "hint": ("查知识：`knowledge_lookup(mode=\"keyword\", query=…)` ✓；"
                         "查多跳：`knowledge_lookup(mode=\"path\", start=<slug>, depth=2)` ✓；"
                         "取规则：`knowledge_lookup(mode=\"rules\", spec=…)` ✓"),
                "since": "2026-10-18"}
    if name == "graph_query_legacy":
        return ke_review.graph_query(str(args.get("cypher", "")), int(args.get("limit", 200) or 200))
    if name == "review_apply":
        _rv_sno, _rv_sslug = _resolve_session_for(str(args.get("kb_id", "")),
                                                  str(args.get("session_no", "")))
        return ke_review.review_apply(kb_id=str(args.get("kb_id", "")), doc=str(args.get("doc", "")),
                                      policy=str(args.get("policy", "")),
                                      policy_slug=str(args.get("policy_slug", "")),
                                      findings=args.get("findings") or [],
                                      page_type=str(args.get("page_type", "") or "bmm:TechnicalReviewReport"),
                                      actor=str(args.get("actor", "agent:document_review")),
                                      session_no=_rv_sno, session_slug=_rv_sslug)
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


def _log_brief(value, limit=60):
    """入参/结果摘要（日志用；不追求无损）。"""
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    if isinstance(value, str):
        return value if len(value) <= limit else value[:limit] + "…"
    if isinstance(value, list):
        return [_log_brief(v, 24) for v in value[:4]] \
            + (["…共%d项" % len(value)] if len(value) > 4 else [])
    if isinstance(value, dict):
        return {k: _log_brief(v, 40) for k, v in list(value.items())[:6]}
    return str(value)[:limit]


def _log_line(text: str) -> None:
    """主日志追加一行（`logs/mcp_calls_YYYYMMDD.log`）。"""
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    path = LOG_DIR / ("mcp_calls_%s.log" % datetime.now().strftime("%Y%m%d"))
    with path.open("a", encoding="utf-8") as fh:
        fh.write(text + "\n")


def _dump_args(name: str, args: dict) -> str:
    """把**完整入参**落盘 `logs/args/<时间>-<工具>.json`（P1，2026-10-09）。

    为什么：主日志里 args 一直按 `[:300]` 截断 → 超时/异常时**无法还原当时到底传了什么**
    （用户实测：一次超时的 `knowledge_save` 连 nodes/edges 都看不全，无法重放/续作）。
    这里落一份完整 JSON，主日志只留摘要 + 文件名。
    """
    try:
        ARGS_DIR.mkdir(parents=True, exist_ok=True)
        fname = "%s-%s.json" % (datetime.now().strftime("%Y%m%d-%H%M%S-%f"),
                                re.sub(r"[^\w.-]", "_", name or "tool"))
        (ARGS_DIR / fname).write_text(json.dumps(args, ensure_ascii=False, indent=2),
                                      encoding="utf-8")
        return "args/" + fname
    except Exception:  # noqa: BLE001
        return ""


def _log_tool_begin(name: str, args: dict) -> str:
    """**两段写日志的"开始"段**（P1）：进入工具**之前**就落一行，返回 args 文件名。

    为什么必须两段：旧实现只在 `call_tool` **返回后**写日志 → 被客户端超时切断的调用
    **一行记录都没有**（这正是内网 `knowledge_save` 超 2 分钟时的观测盲区：
    连"调没调、传了什么、卡在哪"都无法判断）。现在超时也能看到 `BEGIN` 行 + 完整入参文件。
    """
    args_file = _dump_args(name, args)
    try:
        _log_line("%s\t%-26s\t  BEGIN\targs_file=%s\targs=%s"
                  % (now_text(), name, args_file or "(未落盘)",
                     json.dumps(_log_brief(args), ensure_ascii=False)[:300]))
    except Exception:  # noqa: BLE001  日志失败绝不影响工具执行
        pass
    return args_file


def _log_tool_call(name: str, args: dict, result, ms: float, io: dict | None = None,
                   args_file: str = "", phase: str = "DONE") -> None:
    """**两段写日志的"结束"段**：时间 / 工具 / 耗时 / **IO 统计** / 结果摘要。

    `io` = `ke_stats.snapshot()` —— **PG / Neo4j 各自的往返次数与时长**。
    从此"这 2 分钟到底花在 PG 还是 Neo4j、打了多少次往返"可以直接从日志读出来，
    不必再靠推算（实测基线：1 次 psql ≈ 272 ms、1 次 Neo4j ≈ 74 ms）。
    """
    try:
        summary = result if isinstance(result, dict) else {}
        keys = ("applied", "created", "merged", "violations", "unmatched", "retract",
                "retract_planned", "crud_matrix", "report_page", "page_versions", "count",
                "catalog", "id", "name", "error", "how_to_use", "source", "note",
                "images_found", "images", "by_document",
                # P0/P0-b：落库性能与写图结果（便于内网自证"边有没有真的写进图"）
                "graph_edges", "edge_errors", "folders_synced", "maintenance", "resume_hint",
                "merge", "engine", "kernel")
        picked = {}
        for key in keys:
            if key in summary:
                value = summary[key]
                if isinstance(value, list):
                    value = "len=%d %s" % (len(value),
                                           json.dumps(_log_brief(value), ensure_ascii=False)[:120])
                elif isinstance(value, dict) and len(json.dumps(value, ensure_ascii=False)) > 160:
                    value = json.dumps(_log_brief(value), ensure_ascii=False)[:160] + "…"
                picked[key] = value
        io_txt = "-"
        if isinstance(io, dict):
            d = io.get("detail") or {}
            parts = []
            for kind in ("pg", "neo4j"):
                cell = d.get(kind) or {}
                parts.append("%s:%s次/%.0fms%s" % (kind, cell.get("n", 0), cell.get("ms", 0),
                                                   ("/错%s" % cell["err"]) if cell.get("err") else ""))
            io_txt = ("io=%s |合计%s次/%.0fms |墙钟%.0fms"
                      % ("，".join(parts), io.get("calls", 0), io.get("ms", 0),
                         io.get("wall_ms", 0)))
            marks = io.get("marks") or []
            if marks:
                io_txt += " |阶段:%s" % "；".join("%sms:%s" % (m[0], m[1]) for m in marks[-4:])
            # P3-0i（2026-10-10）：**按调用者归因** —— "io=neo4j:1145 次"这类总数无从定位，
            # 这里直接给出打得最多的几个函数（谁在打图/打库一眼可见）。
            # P3-0i2（2026-10-10）：**按类型分开**打印 —— 此前 pg/neo4j 混在一张表，
            # "138 次 psql 到底谁打的"读不出来；分开后 PG 侧消费点直接可见
            # （重启到新代码后实测：neo4j 1214→64 次，瓶颈已转到 pg 138 次）。
            for kind in ("pg", "neo4j"):
                tc = (d.get(kind) or {}).get("top_callers") or []
                if tc:
                    io_txt += " |%s调用者:%s" % (kind, "；".join(
                        "%s=%s次" % (k, v) for k, v in tc[:4]))
        _log_line("%s\t%-26s\t%7.0fms\t%s\t%s\targs_file=%s\tresult=%s"
                  % (now_text(), name, ms, phase, io_txt, args_file or "-",
                     json.dumps(picked, ensure_ascii=False)[:500]))
    except Exception:  # noqa: BLE001  日志失败绝不影响工具结果
        pass


# ---------------------------------------------------------------------------
# MCP：Streamable HTTP 传输（JSON-RPC 2.0）
# ---------------------------------------------------------------------------
PROTOCOL_VERSION = "2025-03-26"
SERVER_NAME = "bodhi-ontology-mcp"
SERVER_VERSION = "0.1.0"


# ---------------------------------------------------------------------------
# 目录按需全量重建（2026-10-04，用户口径）
#   `wiki_folders` 是**物化**的目录表：新页落下后若不重建，前端（懒加载 + 已加载层不重取）
#   就看不到新目录/新页（用户实测："目录更新时间早于最新页面"）。
#   判据 = `max(页.updated_at) > max(目录.updated_at)` → **全量重建**该库目录树（幂等）：
#   重算 category_path 前缀 → upsert 目录 → 把页挂到最深一级 → prune 掉不在计划里的空目录。
#   前端进入 wiki tab / 切库时先调本接口（`/bodhi/folders/refresh?kb_id=…`）再拉目录。
# ---------------------------------------------------------------------------
def _realign_wiki_paths(kb_id: str) -> dict:
    """对齐**派生列**：`depth = len(category_path)` 与 `wiki_path = 目录路径/标题`（**幂等**）。

    ⚠️ 为什么两列都要（2026-10-16 实测，用户 UI 报的「评估目录 9 页、列表只有 8 页」）：
    前端目录列表按 `category_path::jsonb = [...] AND depth = N` **精确匹配**（见 `TROUBLESHOOTING`
    §目录名过长 / §目录缺失）—— 只要 `depth` 与 `category_path` 层数不一致，**该页就不出现在目录里**
    （实测差的正是那 1 页 `depth=1` 而 `category_path` 是 2 层 ✗）；
    `wiki_path` 则是「目录路径/标题」的显示串（历史上有页被写成 slug → 展开取不到页 ✗）。
    只动这两列 —— **不改** `category_path` 本身、目录层级选择、正文、`slug`（那是 `recategorize` 的职责）。
    `category_path` 为空的页跳过。返回 `{"depths_fixed", "paths_fixed"}`（本次修复页数）。
    """
    out = {"depths_fixed": 0, "paths_fixed": 0}
    try:
        rows = psql_csv(
            "UPDATE wiki_pages "
            "   SET depth = jsonb_array_length(COALESCE(category_path,'[]'::jsonb)), updated_at = now() "
            " WHERE knowledge_base_id = %s AND deleted_at IS NULL "
            "   AND jsonb_array_length(COALESCE(category_path,'[]'::jsonb)) > 0 "
            "   AND COALESCE(depth,-1) <> jsonb_array_length(COALESCE(category_path,'[]'::jsonb)) "
            " RETURNING slug" % sql_str(kb_id))
        out["depths_fixed"] = len(rows)
    except Exception as exc:  # noqa: BLE001  修复失败不影响刷新目录本身
        print("[mcp] depth 对齐失败：%s" % exc)
        out["depths_fixed"] = -1
    try:
        rows = psql_csv(
            "WITH t AS ("
            "  SELECT slug, COALESCE(wiki_path,'') AS old_wp,"
            "         array_to_string(ARRAY(SELECT jsonb_array_elements_text(category_path))"
            "                         || ARRAY[COALESCE(title,'')], '/') AS new_wp"
            "    FROM wiki_pages"
            "   WHERE knowledge_base_id = %s AND deleted_at IS NULL"
            "     AND jsonb_array_length(COALESCE(category_path,'[]'::jsonb)) > 0) "
            "UPDATE wiki_pages p SET wiki_path = t.new_wp, updated_at = now()"
            "  FROM t WHERE p.knowledge_base_id = %s AND p.slug = t.slug AND t.old_wp <> t.new_wp"
            " RETURNING p.slug"
            % (sql_str(kb_id), sql_str(kb_id)))
        out["paths_fixed"] = len(rows)
    except Exception as exc:  # noqa: BLE001
        print("[mcp] wiki_path 对齐失败：%s" % exc)
        out["paths_fixed"] = -1
    return out


def refresh_folders(kb_id: str = "", force: bool = False) -> dict:
    kid = (kb_id or "").strip()
    if not kid:
        return {"ok": False, "reason": "缺少 kb_id"}
    kb_id, note = resolve_kb_id(kid)
    # 2026-10-16（用户口径）：刷新目录**顺带把派生列对齐** —— `depth=len(category_path)` 与
    #   `wiki_path="目录路径/标题"`。存量漂移（如「评估」目录 depth=1 却有 2 层 → 页面不进列表 ✗）
    #   在这里一次修好，幂等；只看这两列，不动 category_path/层级/正文/slug。
    #   回执字段：`paths_fixed` / `depths_fixed`。
    fixed = _realign_wiki_paths(kb_id)
    row = psql_csv(
        "SELECT COALESCE(max(updated_at)::text,'') AS pages, "
        "COALESCE((SELECT max(updated_at)::text FROM wiki_folders WHERE knowledge_base_id = %s "
        "AND deleted_at IS NULL), '') AS folders "
        "FROM wiki_pages WHERE knowledge_base_id = %s AND deleted_at IS NULL"
        % (sql_str(kb_id), sql_str(kb_id)))
    pages_at = ((row[0].get("pages") if row else "") or "")
    folders_at = ((row[0].get("folders") if row else "") or "")
    out = {"ok": True, "kb_id": kb_id, "kb_note": note, "refreshed": False,
           "paths_fixed": fixed.get("paths_fixed"), "depths_fixed": fixed.get("depths_fixed"),
           "stale": bool(force or (pages_at > folders_at)),
           "max_page_updated_at": pages_at, "max_folder_updated_at": folders_at}
    if not out["stale"]:
        return out
    try:
        import sync_folders  # noqa: PLC0415  惰性导入：它 import server（避免环）
        out["statements"] = sync_folders.sync_kb(kb_id, dry_run=False, link_pages=True, prune=True)
        out["refreshed"] = True
    except Exception as exc:  # noqa: BLE001
        out.update({"ok": False, "reason": str(exc)[:300]})
        return out
    row2 = psql_csv("SELECT COALESCE(max(updated_at)::text,'') AS folders FROM wiki_folders "
                    "WHERE knowledge_base_id = %s AND deleted_at IS NULL" % sql_str(kb_id))
    out["max_folder_updated_at"] = ((row2[0].get("folders") if row2 else "") or "")
    return out


def _class_chain_labels(ptype: str, meta: dict[str, dict]) -> list[str]:
    """`[根, …, 本类]` 的中文名（**确定性**：沿 `parents[0]` 上溯再反转）。"""
    names, cur, seen = [], ptype, set()
    while cur and cur not in seen:
        seen.add(cur)
        names.append(cur)
        plist = (meta.get(cur) or {}).get("parents") or []
        cur = plist[0] if plist else ""
    names.reverse()
    return [(meta.get(n) or {}).get("label") or n for n in names]


def _category_path_for(ptype: str, depth: int, meta: dict[str, dict]) -> list[str]:
    """按**层级参数**生成目录路径（2026-10-04 用户口径）。

    - **第一层永远是模块短名**（`bodhi:shortName`）；
    - 本体实例类型（含 `:`）：`depth=0` → `[模块, 本类]`（第一层下直接展开**最底层的类**）；
      `depth=N≥1` → `[模块, 根, …, 第 N 层类]`（N=1 即"模块下 1 层 = 直接 Thing 的子类"，
      共展示 2 层；链不足 N 层时到本类为止）；N 上限 5；
    - **weknora 自带类型**（无 `:`：`concept`/`entity`/`summary`…）：固定 `[weknora, 类型中文名]`
      —— **与 depth 无关**（用户口径："weknora 也算一个模块放第一层，下面的类型放第二层"）。
    """
    info = meta.get(ptype) or {}
    module = (info.get("module_short") or info.get("module_label")
              or info.get("module") or ptype)
    if ":" not in ptype:
        return [WEKNORA_MODULE_SHORT, WEKNORA_TYPE_LABELS.get(ptype, ptype)]
    chain = _class_chain_labels(ptype, meta)
    if int(depth) <= 0:
        tail = chain[-1:]
    else:
        tail = chain[:max(1, min(int(depth), MAX_DIR_DEPTH))]
    return [module] + tail


def recategorize(kb_id: str = "", dry_run: bool = True, depth: int = 1) -> dict:
    """**全量重刷目录（按钮/显式入口触发）**：按**当前本体 + 层级参数**重算该库所有页的
    `category_path` / `wiki_path`，再重建目录树（挂页 + prune 空目录）。

    层级参数 `depth`（**缺省 1**，范围 **0–5**；用户口径 2026-10-04）：
    `1` = 模块下 1 层（= 直接 Thing 的子类，界面共 2 层）；`0` = 第一层模块下直接展开最底层的类；
    `2–5` = 再往下细化到第 N 层类。**weknora 自带类型**（`concept`/`entity`/`summary`…）
    固定放 `weknora/<类型>`（与 `depth` 无关）。

    **本体模型库不支持**（它的目录是另一套 `ontology:*`，由本体投影维护）→ 直接拒绝。
    只动「本体实例页 + weknora 类型页」，跳过 `ontology:*` 与 `index`。
    """
    kid = (kb_id or "").strip()
    if not kid:
        return {"ok": False, "reason": "缺少 kb_id"}
    kb_id, note = resolve_kb_id(kid)
    rep = ke_ontology.ontology_kb_report(kb_id)
    if (rep.get("asked") or {}).get("is_ontology_kb"):
        return {"ok": False, "kb_id": kb_id, "reason": "本体模型库的目录是另一套（ontology:* 由本体投影维护），"
                                                      "本功能（按本体重算目录）不适用"}
    depth = max(0, min(int(depth or 0), MAX_DIR_DEPTH))
    meta = ke_ontology.class_meta()
    try:
        rows = psql_csv("SELECT slug, title, page_type, COALESCE(category_path::text,'[]') AS cat "
                        "FROM wiki_pages WHERE knowledge_base_id = %s AND deleted_at IS NULL "
                        "AND COALESCE(page_type,'') NOT LIKE 'ontology:%%' AND page_type <> 'index'"
                        % sql_str(kb_id))
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "reason": str(exc)[:300]}
    changes, skipped = [], 0
    for row in rows:
        ptype = (row.get("page_type") or "").strip()
        if not ptype:
            skipped += 1
            continue
        if ":" in ptype and ptype not in meta:
            skipped += 1                      # 未知本体类型（本体里没有）→ 不动
            continue
        new_path = _category_path_for(ptype, depth, meta)
        new_wp = "/".join(new_path + [(row.get("title") or "").strip()])
        old_path = row.get("cat") or "[]"
        if old_path == json.dumps(new_path, ensure_ascii=False):
            continue
        changes.append({"slug": row["slug"], "title": row.get("title"), "page_type": ptype,
                        "old_path": json.loads(old_path or "[]"), "new_path": new_path,
                        "new_wiki_path": new_wp})
    out = {"ok": True, "kb_id": kb_id, "kb_note": note, "dry_run": bool(dry_run), "depth": depth,
           "scanned": len(rows), "skipped_unknown_type": skipped, "changes": changes,
           "changed": len(changes)}
    if dry_run or not changes:
        out["hint"] = ("dry_run：加 dry_run=false（或 HTTP `dry_run=0`）才写库；"
                       "写库后会自动重建该库目录树（挂页 + prune 空目录）")
        return out
    statements = []
    for ch in changes:
        statements.append(
            "UPDATE wiki_pages SET category_path = %s::jsonb, wiki_path = %s, updated_at = now() "
            "WHERE knowledge_base_id = %s AND slug = %s;"
            % (sql_json(ch["new_path"]), sql_str(ch["new_wiki_path"]), sql_str(kb_id), sql_str(ch["slug"])))
    psql("BEGIN;\n" + "\n".join(statements) + "\nCOMMIT;\n", stdin=True)
    out["folders"] = refresh_folders(kb_id, force=True)
    return out


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
        if path in ("/bodhi/kb-upload", "/bodhi/kb-import-upload"):
            self._kb_upload(path)      # **原始字节**上传（非 JSON），故必须在解析 JSON 之前分流
            return
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
                # P1（2026-10-09）**两段写日志**：进入工具前先落 BEGIN 行 + 完整入参文件，
                # 再开 IO 统计桶；这样被客户端超时切断的调用**也有痕迹**（旧实现一行都没有）。
                _args_file = _log_tool_begin(name, arguments)
                ke_io_stats.begin()
                payload = call_tool(name, arguments)
                result = tool_result(payload)
                _log_tool_call(name, arguments, payload, ms=(time.time() - _started) * 1000.0,
                               io=ke_io_stats.snapshot(), args_file=_args_file)
            except Exception as exc:  # noqa: BLE001
                print("[mcp] 工具失败：%s" % exc)
                result = tool_result({"error": str(exc)}, is_error=True)
                try:
                    _log_tool_call(name, arguments, {"error": str(exc)},
                                   ms=(time.time() - _started) * 1000.0,
                                   io=ke_io_stats.snapshot(), args_file=_args_file, phase="ERR")
                except Exception:  # noqa: BLE001
                    pass
            finally:
                ke_io_stats.reset()
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

    def _kb_upload(self, path: str) -> None:
        """`POST /bodhi/kb-upload?name=<file.zip>[&new_kb=…|&kb_id=…][&keep_ids=1][&verify=1]`

        **body 就是文件原始字节**（不是 JSON、不是 multipart）→ curl / 浏览器 fetch 都能发：

            curl -X POST --data-binary @kb-xfer.zip \\
              "http://<mcp主机>:8765/bodhi/kb-upload?name=kb-xfer.zip&new_kb=技术方案评审领域模型&verify=1"

        存到 `exports/uploads/<name>`；带 `new_kb`/`kb_id` 时**顺带导入**（默认 verify=1 → 回执含 `verify_ok`）。
        """
        params = dict(urlparse.parse_qsl(urlparse.urlsplit(self.path).query))
        name = pathlib.Path(params.get("name", "")).name
        if not name.lower().endswith(".zip"):
            self._json({"error": "只接收 .zip：请给 name=<文件名.zip>（body 用 --data-binary @文件）"},
                       400, self.CORS)
            return
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            self._json({"error": "空 body：请用 curl --data-binary @<zip> 上传（或上传页选文件）"},
                       400, self.CORS)
            return
        updir = EXPORT_DIR / "uploads"
        updir.mkdir(parents=True, exist_ok=True)
        fp = updir / name
        fp.write_bytes(self.rfile.read(length))
        out = {"ok": True, "saved": str(fp), "bytes": length,
               "note": "已收到 zip；带 new_kb/kb_id 时本端点会顺带导入"}
        if params.get("new_kb") or params.get("kb_id"):
            # 2026-10-20 修（用户实测）：导入异常时**必须返回 JSON 错误** ✓
            # 旧实现异常直接冒泡 ⇒ 浏览器收不到响应 ⇒ 前端永远停在"上传中…"✗
            try:
                out["import"] = kb_import(str(fp), kb_id=params.get("kb_id", ""),
                                          new_kb=params.get("new_kb", ""),
                                          keep_ids=bool(params.get("keep_ids")),
                                          verify=bool(params.get("verify", "1")))
            except BaseException as exc:  # noqa: BLE001  SystemExit 也要接住 ✓
                import traceback as _tb  # noqa: PLC0415
                tb = _tb.format_exc()
                out.update({"ok": False, "error": "%s: %s" % (type(exc).__name__, str(exc)[:400]),
                            "traceback_tail": tb[-1200:],
                            "note": ("导入中断 ✗（zip 已收下，可修好后重跑本端点 ✓）；"
                                     "常见：修订唯一键冲突 / 图写入失败 —— 看 error 与 traceback_tail")})
        self._json(out, 200 if out.get("ok") else 500, self.CORS)

    def _bodhi_post(self, path: str) -> None:
        try:
            body = self._read_json()
        except ValueError as exc:
            self._json({"error": str(exc)}, 400, self.CORS)
            return
        def _reject_if_failed(result, what: str):
            """门禁回执（`ok=False`）→ 抛 ValueError（HTTP 层映射成 400 `{"error": …}`）。

            用户口径 2026-09-30：不合格的 TTL **不允许导入**，且提示里要给出**可直接复制**的
            修复片段。HTTP 层原本把 `ok=False` 的 dict 当 200 返回 → 前端弹"导入完成"面板，
            用户会以为"删掉 bodhi:shortName 也能导入"（实测确认）。前端只展示 400 的
            `error` 原文，所以下面把它拼成多行文本（含两行片段）。
            """
            if not (isinstance(result, dict) and result.get("ok") is False):
                return result
            parts = ["%s 被拒绝：%s" % (what, result.get("error") or "校验未通过")]
            for key in ("note", "hint", "snippet"):
                if result.get(key):
                    parts.append(str(result[key]))
            for m in result.get("missing") or []:
                parts.append("· %s：%s" % (m.get("module") or "",
                                           "、".join(m.get("missing") or [])))
                if m.get("snippet"):
                    parts.append(str(m["snippet"]))
                if m.get("note"):
                    parts.append(str(m["note"]))
            for h in result.get("hints") or []:
                parts.append("· %s" % h)
            raise ValueError("\n".join(p for p in parts if p))

        import ke_graph as _ke_graph  # noqa: PLC0415  关系读写统一走图实例（2026-10-05 M2）

        handlers = {
            "/bodhi/relations/add":
                lambda b: _ke_graph.add_edge(b.get("kb_id", ""), b.get("slug", ""),
                                             b.get("rel_type", ""), b.get("target_slug", ""),
                                             b.get("label", "")),
            "/bodhi/relations/update":
                lambda b: _ke_graph.update_edge(b.get("kb_id", ""), b.get("slug", ""),
                                                b.get("target_slug", ""), b.get("new_rel_type", ""),
                                                b.get("new_target_slug", ""), b.get("label", "")),
            "/bodhi/relations/delete":
                lambda b: _ke_graph.delete_edge(b.get("kb_id", ""), b.get("slug", ""),
                                                b.get("target_slug", ""), b.get("rel_type", "")),
            "/bodhi/page/type":
                lambda b: ke_pages.set_page_type(b.get("kb_id", ""), b.get("slug", ""),
                                                 b.get("page_type", "")),
            # 数据属性写口（方案 D，2026-10-13）：**只改数据属性**（entity 模式页）；
            # `dry_run=1`（默认）只回 diff。三处同写：PG 元数据 + 图节点属性 + 正文属性段（标题式）。
            "/bodhi/page/attributes":
                lambda b: ke_pages.set_page_attributes(b.get("kb_id", ""), b.get("slug", ""),
                                                       b.get("patch") or {},
                                                       actor=str(b.get("actor", "")),
                                                       dry_run=bool(b.get("dry_run", True)),
                                                       # 「以摘要为准同步」按钮（2026-10-15 用户口径）：
                                                       # 把 PG `summary` 写进定义键的 元数据 + 图节点（**不含正文**）
                                                       sync_from_summary=bool(b.get("sync_from_summary", False))),
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
                lambda b: _reject_if_failed(ke_admin.upload_ttl(b.get("filename", ""), b.get("content", ""),
                                              b.get("module_id", ""),
                                              # 默认 True：上传会级联删下游，必须重投影（否则目录空）
                                              bool(b.get("project_wiki", True)),
                                              b.get("kb_id", ""),
                                              # 2026-09-24：默认"落真源 + 编译并生效"（可在前端关掉）
                                              write_source=bool(b.get("write_source", True)),
                                              compile_after=bool(b.get("compile_after", True)),
                                              apply_after=bool(b.get("apply_after", False))),
                                               what="上传导入"),
            # 运维修复（幂等）：编译 → 灌 Neo4j 投影 → 重投影本体库 wiki → 一致性体检
            "/bodhi/ontology/repair":
                lambda b: _reject_if_failed(ke_admin.repair_all(b.get("kb_id", ""),
                                                                bool(b.get("compile", True)),
                                                                bool(b.get("project_wiki", True))),
                                            what="repair"),
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
                lambda b: ke_review.review_apply(
                    kb_id=str(b.get("kb_id", "")), doc=str(b.get("doc", "")),
                    policy=str(b.get("policy", "")), policy_slug=str(b.get("policy_slug", "")),
                    findings=b.get("findings") or [],
                    page_type=str(b.get("page_type", "") or "bmm:TechnicalReviewReport"),
                    actor=str(b.get("actor", "http:review")),
                    session_no=_resolve_session_for(str(b.get("kb_id", "")), str(b.get("session_no", "")))[0],
                    session_slug=_resolve_session_for(str(b.get("kb_id", "")), str(b.get("session_no", "")))[1]),
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
        if path.startswith("/bodhi/page/attributes"):
            # 数据属性**读**口（方案 D，2026-10-13，只读）：前端「数据属性」面板用 ——
            # 回 `attributes`（事实源现值）、`source`（meta/graph/content）、`editable`（仅 entity）、
            # `allowed`（该类可编辑属性）、`drift`（三处不一致的键）。
            params = dict(urlparse.parse_qsl(parsed.query))
            try:
                self._json(ke_pages.page_attributes(str(params.get("kb_id", "")),
                                                    str(params.get("slug", ""))), 200, self.CORS)
            except Exception as exc:  # noqa: BLE001
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
        if path.startswith("/bodhi/session/link"):
            # **补来源会话边（显式入口）**：给知识页补 `bmm:sourceSession` 双链 → 会话页（分页优先）。
            # `dry_run=1`（默认）只出清单；`all=1` = 本库所有缺该行的非会话页。
            params = dict(urlparse.parse_qsl(parsed.query))
            try:
                slugs = [s for s in (params.get("slug", "") or "").split(",") if s.strip()]
                self._json(link_source_session(params.get("kb_id", ""), params.get("session_no", ""),
                                               slugs=slugs or None,
                                               all_pages=params.get("all", "0") not in ("0", "false", ""),
                                               dry_run=params.get("dry_run", "1") not in ("0", "false", "")),
                           200, self.CORS)
            except Exception as exc:  # noqa: BLE001
                print("[mcp] /bodhi/session/link 失败：%s" % exc)
                self._json({"error": str(exc)}, 400, self.CORS)
            return
        if path.startswith("/bodhi/folders/recategorize"):
            # **重新编目（显式入口，不自动跑）**：按当前本体重算本体实例页的 category_path/wiki_path
            # 再重建目录（挂页+prune）。`dry_run=1`（默认）只出清单。用户口径 2026-10-04。
            params = dict(urlparse.parse_qsl(parsed.query))
            try:
                self._json(recategorize(params.get("kb_id", ""),
                                        params.get("dry_run", "1") not in ("0", "false", ""),
                                        int(params.get("depth", "1") or 1)),
                           200, self.CORS)
            except Exception as exc:  # noqa: BLE001
                print("[mcp] /bodhi/folders/recategorize 失败：%s" % exc)
                self._json({"error": str(exc)}, 400, self.CORS)
            return
        if path.startswith("/bodhi/folders/refresh"):
            # 目录按需全量重建（2026-10-04）：有比目录更新的页 → 重建（幂等，一般是 no-op）
            params = dict(urlparse.parse_qsl(parsed.query))
            try:
                self._json(refresh_folders(params.get("kb_id", ""),
                                           params.get("force", "0") not in ("0", "false", "")),
                           200, self.CORS)
            except Exception as exc:  # noqa: BLE001
                print("[mcp] /bodhi/folders/refresh 失败：%s" % exc)
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
                # 2026-10-05 M2：本体关系面板**只读图实例**（pg 不再是主本、正文/出入链已无关系信息）。
                # 图不可用/无该实例 → 返回空结构（不再退 PG）。
                data = None
                try:
                    import ke_graph  # noqa: PLC0415
                    data = ke_graph.relations_of(kb, slug)
                except Exception:  # noqa: BLE001
                    data = None
                if data is None:
                    data = {"slug": slug, "title": "", "page_type": "", "type_label": "",
                            "out": [], "in": [], "source": "graph", "graph_unavailable": True}
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
        # 上传页（不依赖前端 UI：选 zip → 直接 POST 字节 → 显示导入回执）
        if path in ("/bodhi/kb-upload", "/bodhi/kb-upload.html"):
            html = """<!DOCTYPE html><meta charset="utf-8"><title>知识库导入（上传 zip）</title>
<body style="font:14px/1.6 system-ui;max-width:720px;margin:32px auto">
<h3>知识库导入 · 上传导出包（zip）</h3>
<p>导出包来自 `kb_export`（或 <code>kb_transfer.py export --zip</code>）。填 <b>新库名</b>（跨环境迁移推荐）
或 <b>既有 kb_id</b>；<code>keep_ids</code> 仅跨实例迁移时勾（同实例会自动降级为重编 UUID）。</p>
<form id="f">
  <p><input type="file" id="file" accept=".zip" required></p>
  <p>新库名 <input name="new_kb" id="new_kb" size="32" placeholder="如：技术方案评审领域模型">
     或既有 kb_id <input name="kb_id" id="kb_id" size="36" placeholder="UUID"></p>
  <p><label><input type="checkbox" id="keep_ids" checked> keep_ids（跨实例迁移）</label></p>
  <p><button>上传并导入</button></p>
</form>
<pre id="out" style="background:#f6f8fa;padding:12px;white-space:pre-wrap"></pre>
<script>
f.onsubmit = async (e) => {
  e.preventDefault();
  const file = document.getElementById('file').files[0];
  if (!file) { document.getElementById('out').textContent = '请先选一个 .zip'; return; }
  const q = new URLSearchParams({name: file.name,
    new_kb: document.getElementById('new_kb').value.trim(),
    kb_id: document.getElementById('kb_id').value.trim(),
    keep_ids: document.getElementById('keep_ids').checked ? 1 : 0, verify: 1});
  document.getElementById('out').textContent = '上传中…（大包请耐心等）';
  const r = await fetch('/bodhi/kb-upload?' + q.toString(), {method: 'POST', body: file});
  let j; try { j = await r.json(); } catch (err) { j = {http: r.status, text: await r.text()}; }
  document.getElementById('out').textContent = JSON.stringify(j, null, 2);
};
</script></body>"""
            self._send(200, html.encode("utf-8"), "text/html; charset=utf-8",
                       {"Access-Control-Allow-Origin": "*"})
            return
        # 导出包下载（`kb_export` 的产物 → 直接拉 zip；只允许 exports/ 下的文件名，防目录穿越）
        if path in ("/bodhi/kb-export", "/bodhi/kb-export.zip"):
            params = dict(urlparse.parse_qsl(parsed.query))
            name = pathlib.Path(params.get("name", "")).name
            fp = EXPORT_DIR / name
            if not name or not fp.is_file():
                self._json({"error": "找不到导出包：%s（在 %s 下）" % (name, EXPORT_DIR)}, 404,
                           {"Access-Control-Allow-Origin": "*"})
                return
            self._send(200, fp.read_bytes(), "application/zip",
                       {"Content-Disposition": 'attachment; filename="%s"' % name,
                        "Access-Control-Allow-Origin": "*"})
            return
        # （原 `/bodhi/pending`、`/bodhi/resolve`（待确认合并的查/裁决）已随「相似度合并永久取消」删除，
        #  2026-10-10；相似知识治理改走巡检 `audit_scan` S1/S2。）
        if path in ("/bodhi/pending", "/bodhi/pending.json", "/bodhi/resolve", "/bodhi/resolve.json"):
            self._json({"error": "待确认合并已废弃（2026-10-10）：相似知识治理走 audit_scan（S1/S2）"},
                       410, {"Access-Control-Allow-Origin": "*"})
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
        # **非致命**：ensure-marks 只是把 wiki_config 标记补齐（配了 env 就按 env 走，标记是兜底）。
        # 但若这里报「未设置数据库口令」，说明 DB 访问整体不可用 → 所有落库工具会一起失败，必须修。
        print("[mcp] ensure-marks 跳过（非致命，但若因「未设置数据库口令」则 DB 类工具会全部失败）：%s" % exc)
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
    # 2026-10-05 M3：普通知识库图谱**读图实例**（关系只在图里）；图有实例才走这里，否则退下面 PG 逻辑
    try:
        import ke_graph as _kg  # noqa: PLC0415
        if kb_id and _kg.instance_count(kb_id):
            return _kg.instance_graph(kb_id, model,
                                      [t.strip() for t in types.split(",") if t.strip()], limit)
    except Exception:  # noqa: BLE001
        pass
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









