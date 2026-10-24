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
INDEX_PATH = REPO / "artifacts" / "weknora" / "ontology_index.json"
# 2026-10-24 批 3c（A1-8）：`TOOL_TAG` 移到 `srv/constants.py`（含 `varchar(16)` 断言）
from srv.constants import TOOL_TAG  # noqa: E402,F401

DB_CONTAINER, DB_USER, DB_NAME = "WeKnora-postgres", "postgres", "WeKnora"
# 口令不在此内置：由 `ke_db`（env `BODHI_DB_PASSWORD` → WeKnora `.env`）提供，见下方 import。
# 2026-10-24 批 3c（A1-8）：`TYPE_PENDING` 随 `fetch_existing_pages` 迁入 `srv/elements.py`（见下方导入 ✓）

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


# 2026-10-24 批 3c（A1-8）：`now_text()` 迁入 `srv/clock.py` ✓（写库群与 `srv/*` 共用）
from srv.clock import now_text  # noqa: E402,F401


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
# 2026-10-24 批 3c（A1-8）：请求级只读 memo 迁入 `srv/reqctx.py` ✓
# （工具入口仍调 `_reset_request_cache()`；写库路径由 `save_elements` 内部清）
from srv.reqctx import _req_cache, _reset_request_cache  # noqa: E402,F401


# 2026-10-24 批 3c（A1-8）：**要素 → 页面**写库群迁入 `srv/elements.py` ✓
# （`fetch_existing_pages` / `element_slug` / `build_new_page` / `save_elements` / `design_elements` /
#   `retract_relations` / 图目标解析 …；这里统一再导出 ⇒ `import server` 的旧脚本/自检零改动）
from srv.elements import (  # noqa: E402,F401
    TYPE_PENDING, _as_chunk_index, _attrs_with_identity, _chunk_locator, _graph_target_slug,
    _graph_target_type, _other_kb_same_name, _page_slug_by_title, _tenant_name,
    _warn_noncanonical_ids, build_new_page, design_elements, element_page_slug, element_slug,
    fetch_existing_pages, retract_relations, save_elements,
)


# ---------------------------------------------------------------------------
# 片段读取 + 一次性抽取
# ---------------------------------------------------------------------------
# 2026-10-24 批 3b（A1-7）：`load_engine()` 迁入 `srv/engine.py` ✓（只做 sys.path + import，无状态）
from srv.engine import load_engine  # noqa: E402,F401


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
# A1 切片（2026-10-24）：工具处理注册表 —— **注册表优先，未命中回落旧 if 链**（双轨，零行为变化 ✓）
from srv.handlers import dispatch as _handler_dispatch, names as _handler_names  # noqa: E402


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


# 2026-10-24 批 3c（A1-8）：**会话身份 + 来源会话边**迁入 `srv/session_link.py` ✓
# （`IDENTITY_CLASS` / `_pick_session_no` / `_session_page_ref` / `_session_part_no` /
#   `_upsert_source_session_line` / `_resolve_session_for` / `link_source_session` / `adopt_session`）
from srv.session_link import (  # noqa: E402,F401
    IDENTITY_CLASS, SOURCE_SESSION_LABEL, SOURCE_SESSION_REL, _pick_session_no,
    _resolve_session_for, _session_page_ref, _session_part_no, _upsert_source_session_line,
    adopt_session, link_source_session,
)


# （A1-8：`locate_chunk` 全仓已无调用点 ⇒ 删除 ✓；切片定位在 `ke_extract` 侧）


# ---------------------------------------------------------------------------
# 存量页面 / 匹配 / 合并
# ---------------------------------------------------------------------------
# （A1-8：`PAGE_COLUMNS` 已无引用（`fetch_existing_pages` 用显式列清单）⇒ 删除 ✓）


from srv.kbresolve import (  # noqa: E402,F401  （P2 切片 S2 ✓ 名字不变 ⇒ 下游零改动）
    get_kb_tenant, kb_choices, resolve_kb_id, resolve_knowledge_id, resolve_write_kb,
)


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


# 由载荷推导出来的小节（渲染器每次重算）→ 合并时**替换**而不是"已存在就跳过"
DERIVED_SECTIONS = {
    "## 用途", "## 输入 / 输出", "## 设计规范（正常 / 异常案例 · ASSERTION）",
    "## 属性（数据属性）", "## 关系限定（边属性）", "## 被引用（入边）",
}


# （A1-8：`union_list` 已无调用点 ⇒ 删除 ✓）


from ke_category import (  # noqa: E402  (P3 slice B: 目录常量)
    MAX_DIR_DEPTH, WEKNORA_MODULE_SHORT, WEKNORA_TYPE_LABELS,
)


from srv.session import (  # noqa: E402,F401  （P2 切片 S5 ✓ 会话身份解析，实现逐字未改）
    _IDENT, _attach_session, _resolve_session_identity, _session_ident,
)


# 2026-10-24 批 3b（A1-7）：图片读取迁入 `srv/images.py` ✓
# （`_IMG_REF_RE` / `_image_root` / `_resolve_image_path` 一并搬走；这里导入即再导出，
#   `import server` 的旧脚本与自检仍然可用 ✓）
from srv.images import image_extract  # noqa: E402,F401


# 2026-10-24 批 3c（A1-8）：写库编排 `save_knowledge` 迁入 `srv/knowledge_write.py` ✓
# 依赖方向单向：knowledge_write → elements / session_link → reqctx / clock（不回到本文件）
from srv.knowledge_write import save_knowledge  # noqa: E402,F401


# 2026-10-24 批 3b（A1-7）：技能目录 + 本体面迁入 `srv/meta.py` ✓
from srv.meta import INDEX_PATH, SKILLS_DIR, load_skills, ontology_types, skills  # noqa: E402,F401


# ---------------------------------------------------------------------------
# 待确认裁决 + 查询工具
# ---------------------------------------------------------------------------


from ke_overview import (  # noqa: E402,F401  (P3 slice C: overview/CRUD aggregation)
    OVERVIEW_FOLDER, OVERVIEW_SLUG, OVERVIEW_TITLE, _ATTR_OF, _overview_detail_lines,
    _page_row, _replace_section, crud_matrix_lines, crud_model, detail_attributes,
    refresh_crud_matrix, service_design_summary, service_overview_lines, service_types,
)


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


# （`ontology_types` 已迁 `srv/meta.py`：见上方导入 ✓）


# ---------------------------------------------------------------------------
# MCP：工具定义与调用
# ---------------------------------------------------------------------------
# 2026-10-24 批 3b（A1-7）：异步任务表迁入 `srv/jobs.py` ✓
# 现状：**没有工具再提交任务**（整篇异步抽取 2026-09-22 退役、总览页刷新 2026-10-24 退役）
# ⇒ 任务表恒空；`job_status` 保留仅为兼容旧提示词/脚本，回执照实说「没有这个任务」✓
from srv.jobs import DETAIL_KEYS, JOBS, JOBS_LOCK, _job_brief, job_status  # noqa: E402,F401


from srv.schemas import TOOL_SCHEMAS  # noqa: E402  （P2 切片 S8a ✓ schema 单一来源）


def tool_definitions() -> list[dict]:
    """工具 schema 列表（**单一来源** = `srv/schemas.py` ✓）"""
    return TOOL_SCHEMAS

from srv.tools_read import (  # noqa: E402,F401  （P2 切片 S8b-1 ✓ 只读薄封装）
    audit_scan, context_authority, context_lookup, context_page, context_scan,
)


# 2026-10-24 批 3b（A1-7）：上下文写迁入 `srv/context_write.py` ✓
# 单独成模块的原因：`srv/http.py` 的 REST 路由也用它们（显式 import，不靠 `bind()` 注入）
from srv.context_write import (  # noqa: E402,F401
    context_authority_apply, context_concept_apply, context_concept_rollback,
)


# （2026-10-24 批 3a：`service_overview` 工具已退役 ⇒ `service_overview_tool` / `start_overview_job`
#  已删除；总览页刷新仍可由 CLI `tools/ontology-mcp/refresh_design.py --overview` 走
#  `refresh_service_overview`（内部调 save_knowledge）✓）
# ---------------------------------------------------------------------------
# P3-0i3（2026-10-10）：**同载荷重入保护** —— 客户端超时重发不再写出重复版本
#
# 实测（内网 09:40）：apply 一次 25-31 s，**超过客户端 ~30 s 超时** → 前端把**同一份载荷**
# 又发了一次（日志两条 BEGIN 相隔 30 s、args 文件不同内容相同）→ 同一批被写两遍、
# 页版本 +2（用户看到"重复版本"）。P3-0f2 把 apply 压到超时内之后应当不再触发，
# 但**兜底必须留着**：同一 (工具, 载荷指纹) 在窗口内再次到达 → **附着到正在跑的那一次**，
# 等它出结果后**原样返回同一份回执**（并标 `reentry.deduped=true`），绝不重复写库。
# ---------------------------------------------------------------------------
from srv.reentry import (  # noqa: E402,F401  (P3 slice E)
    _REENTRY, _REENTRY_LOCK, REENTRY_TOOLS, REENTRY_WINDOW_S, _prune_reentry, _reentry_key,
)


def _reentry_receipt(prev: dict, args: dict) -> dict:
    """重入去重时的回执（2026-10-24 修 B4）：**不能原样复用第一次的回执** ✗

    重入**没有写库** ⇒ 第一次的 `created` / `before=0` 已经过时，会让调用方以为"又建了一页"。
    这里按**库中现值**刷新：`before` = 当前版本、`action="unchanged"`，`created` 挪进 `merged`。
    """
    out = dict(prev)
    out["reentry"] = {"deduped": True, "note": (
        "同一份载荷已由本服务处理（客户端超时重发）：**未重复写库**；"
        "回执已按库中现值刷新（`action=unchanged`）。")}
    pvs = out.get("page_versions") or []
    kb = str((args or {}).get("kb_id") or "")
    if kb:
        try:
            kb_id = resolve_kb_id(kb)[0]
        except Exception:  # noqa: BLE001
            kb_id = kb
        for pv in pvs:
            slug = str((pv or {}).get("slug") or "")
            if not slug:
                continue
            try:
                rows = ke_db.psql_csv(
                    "SELECT COALESCE(version,1) AS v FROM wiki_pages WHERE "
                    "knowledge_base_id = %s AND slug = %s AND deleted_at IS NULL LIMIT 1"
                    % (ke_db.sql_str(kb_id), ke_db.sql_str(slug)))
            except Exception:  # noqa: BLE001
                rows = []
            if rows:
                pv["before"] = int(rows[0]["v"] or 1)
                pv["action"] = "unchanged"
                pv["applied"] = True
    if out.get("created"):
        out["merged"] = (out.get("merged") or []) + [
            {"name": c.get("name"), "type": c.get("type"), "into": c.get("slug"),
             "similarity": 1.0, "note": "同载荷重入：未重复写库（页已是该版本）"}
            for c in out["created"] if isinstance(c, dict)]
        out["created"] = []
    return out


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
            return _reentry_receipt(prev, args)
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
    "rules_of_policy": "改用 `knowledge_lookup(mode=\"rules\", spec=\"policy:<策略>\"|\"direct:<slug,…>\"|\"ask\")` 或 `rules_resolve(spec=…)` ✓",
    "rules_of_task": "旧「任务→规则」路径已删除 ✓ —— 规则只按步骤解析：`knowledge_lookup(mode=\"rules\", spec=\"policy:<策略>\"|\"direct:<slug,…>\"|\"ask\")`；兼容旧口径可用 `rules_resolve(spec=\"task:<任务>\")` ✓",
    "graph_query": "已归一化到 `knowledge_lookup` ⟹ 查知识 `mode=\"keyword\"`、查多跳 `mode=\"path\"`、取规则 `mode=\"rules\"` ✓（不再接受直接拼 Cypher ✗）",
    "review_apply": "判定结论改由 save_knowledge(mode=document) 落 `bmm:TechnicalReviewReport` 一页 ✓（本工具会产 `bmm:Assessment` 旧口径 ✗）",
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
    # A1 切片：**注册表优先**（已迁移的工具直接走 `srv/handlers/*`）；未登记的工具仍走下面的 if 链 ✓
    _handled = _handler_dispatch(name, args)
    if _handled is not None:
        return _handled
    # （原 `list_pending_merges` / `resolve_pending_merge` 两个工具已随「相似度合并永久取消」删除，2026-10-10）
    raise RuntimeError("未知工具：%s" % name)


def tool_result(payload: dict, is_error: bool = False) -> dict:
    return {
        "content": [{"type": "text", "text": json.dumps(payload, ensure_ascii=False, indent=2)}],
        "isError": is_error,
    }


from srv.obs import (  # noqa: E402,F401  (P3 slice D)
    _dump_args, _log_brief, _log_line, _log_tool_begin, _log_tool_call,
)


from ke_category import (  # noqa: E402,F401  (P3 slice B: 目录/分类)
    _category_path_for, _class_chain_labels, _realign_wiki_paths, recategorize, refresh_folders,
)


def main() -> int:
    """HTTP entry (P2 slice S9: transport lives in srv/http.py)."""
    from srv import http as _http
    _http.bind(globals())
    return _http.main()


# ---------------------------------------------------------------------------
# 只读接口：Bodhi 语义图（供前端「Bodhi 图谱」页 / 服务自带 HTML 页使用）
#   GET /bodhi/graph?kb_id=..&model=bmm&types=bmm:Goal,bmm:Objective&limit=300
# 数据来源：页面正文的「## 本体关系」小节（我们写页时格式固定）+ 页面元数据
# 说明：REL_LINE / parse_rel_line / rel_line 已移到 tools/ke-core/ke_pages.py
#       （文件头 import 回来；relink_pages.py 的 server.rel_line 用法不变）。
# ---------------------------------------------------------------------------
from ke_graph_view import (  # noqa: E402,F401  (P3 slice A: graph views moved to ke-core)
    _SECTION_EDGE, _WIKI_LINK_RE, _class_meta, _section_edge, all_class_meta, bodhi_graph,
    bodhi_model_graph, bodhi_page, class_category_path, class_group,
)

if __name__ == "__main__":
    sys.exit(main())


