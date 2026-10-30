"""写库编排（A1-8 迁自 `server.py::save_knowledge`，**实现逐字未改**）

两段式：`stage="graph"`（节点/边 → 图谱 + 页面）与 `stage="report"`（报告页整体替换）。
守门：会话身份（`srv/session`）必须先解析成功，否则 `need_session` 拒写（fail-closed ✓）。

依赖方向**单向**：knowledge_write → elements / session_link → reqctx / clock；
不要反向 import `srv/meta.py` 之类高层模块（避免环）。
"""
from __future__ import annotations

import json

import os
import sys

_CORE = os.path.abspath(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                     "..", "ke-core"))
if _CORE not in sys.path:
    sys.path.insert(0, _CORE)

import ke_ontology  # noqa: E402
import ke_pages  # noqa: E402
from ke_db import psql, psql_csv, sql_json, sql_str  # noqa: E402
from srv.clock import now_text  # noqa: E402
from srv.constants import DEFAULT_HIGH, DEFAULT_LOW  # noqa: E402
from srv.elements import design_elements, save_elements  # noqa: E402
from srv.engine import load_engine  # noqa: E402
from srv.kbresolve import get_kb_tenant, resolve_knowledge_id, resolve_write_kb  # noqa: E402
from srv.render import _type_label_of, context_meta  # noqa: E402
from srv.session import (  # noqa: E402
    _IDENT, _attach_session, _resolve_session_identity, _session_ident,
)
from srv.session_link import (  # noqa: E402
    _pick_session_no, adopt_session, link_source_session,
)


def save_knowledge(kb_id: str = "", *, stage: str = "report", model: str = "bmm",
                   report: dict | None = None, nodes: list | None = None, edges: list | None = None,
                   relations_only: bool = False,
                   mode: str = "dry_run", confirmed_new_applications: list | None = None,
                   high: float = DEFAULT_HIGH, low: float = DEFAULT_LOW,
                   session: dict | None = None, kb_ids: list | None = None,
                   confirm_kb_match: bool = False, context: str = "",
                   session_no: str = "", doc_id: str = "", intent: str = "",
                   acknowledge_intent_change: bool = False) -> dict:
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
    import time as _time
    _T0 = _time.time()          # 2026-10-28：分段耗时（超时排查用；不改任何语义）
    tenant_id = get_kb_tenant(kb_id)
    confirmed = {str(x).strip() for x in (confirmed_new_applications or []) if str(x).strip()}

    doc_meta = {"id": "", "title": (report or {}).get("source_document_title") or "（无来源文档）",
                "context": ctx["id"], "note": "", "external_ref": ""}
    if (report or {}).get("source_document_id"):
        # 2026-10-28：**来源文档解析失败不再阻断报告落库**（内网 20261027-101010 实测：
        # 智能体传的是外部台账的 doc_id（`chat-*`），工作库里没有对应 knowledges 记录）。
        # 解析不到 ⇒ 记成外部溯源（`bmm:sourceLocator = ext-doc://<doc_id>`），正文/回执如实说明。
        _raw_doc = str(report["source_document_id"])
        try:
            doc_meta["id"], doc_meta["note"] = resolve_knowledge_id(kb_id, _raw_doc, allow_empty=True)
        except Exception as exc:  # noqa: BLE001
            doc_meta["id"] = ""
            doc_meta["note"] = "来源文档解析失败：%s" % str(exc)[:120]
        if not doc_meta["id"]:
            doc_meta["external_ref"] = _raw_doc
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
        # 2026-10-23（T2）：章节口径来自**模板页**（`schema:DocumentTemplate` + `applicableReportType`）；
        # 找不到模板 ⇒ 回落内置结构，并在回执给 `template_fallback_reason`（可追溯 ✓）。
        _tpl = {}
        try:
            import ke_report as _kr  # noqa: PLC0415
            # 写模板页本身不必再找模板（否则回执里会多一条"无模板"噪音 ✓）
            if _ptype == "schema:DocumentTemplate":
                _report_warns = []
            else:
                _tpl = _kr.resolve_template(kb_id, _ptype, str((report or {}).get("template") or ""))
                _report_warns = _kr.check_report_md(
                    body, _tpl.get("sections") if _tpl.get("found") else None)
        except Exception:  # noqa: BLE001
            _report_warns = []
        _attrs = dict((report or {}).get("attributes") or {})
        if doc_meta.get("external_ref"):
            # 外部台账来源（it_platform 的 doc_id）落到 `bmm:sourceLocator`：**可追溯**，且不伪装成 WeKnora 文档
            _attrs.setdefault("bmm:sourceLocator", "ext-doc://%s" % doc_meta["external_ref"])
        spec = {
            "slug": slug,                    # 复用既有报告页时由上面的幂等查询给出；空则由内核派生
            "title": title, "page_type": _ptype, "type_label": _type_label_of(_ptype, ctx["report_label"]),
            "mode": "document",              # 报告=文档类；**不依赖本体判定**（legacy 的 `summary` 不是本体类）
            "wiki_content": body,
            # 2026-10-23（用户口径）：`stage="report"` 可**直接写任意 document 类页**（报告/模板同属
            # `schema:CreativeWork`），模板页因此**不需要再 retag**；属性随本调用一起写。
            "attributes": _attrs,
            "summary": (report or {}).get("summary") or "",
            "source": {
                "doc_refs": [doc_meta["id"]] if doc_meta.get("id") else [],
                "doc_title": doc_meta.get("title", ""),
                "source_text": ctx["report_source"],
                "derived_from": _upstream,
                # 外部台账来源（2026-10-28）：it_platform 的 doc_id，不是 WeKnora 的 knowledge UUID
                "external_ref": doc_meta.get("external_ref") or "",
                # 2026-10-22（4.6）：分页判定三件套（文档变化是**强信号**、意图变化是**弱信号**）
                "doc_id": str(doc_id or doc_meta.get("id") or ""),
                "intent": str(intent or ""),
                "acknowledge_intent_change": bool(acknowledge_intent_change),
            },

            "metadata_extra": {"design": {"upstream": _upstream, "context": ctx["id"]}},
            "aliases": (report or {}).get("aliases") or [],
        }
        if ident.get("ok"):
            spec["source"]["session_no"] = ident["session_no"]
            spec["source"]["agent_name"] = ident.get("agent_name", "")
        _t_prep = int((_time.time() - _T0) * 1000)   # 准备：来源解析 + 模板校验 + spec 组装
        _t1 = _time.time()
        res = ke_pages.write_knowledge(
            kb_id, spec, dry_run=(mode != "apply"),
            # 有 WeKnora 文档 / 有会话身份 / 有外部台账来源 ⇒ 都算"有溯源"
            strict_source=bool(doc_meta.get("id") or ident.get("ok") or doc_meta.get("external_ref")),
            sync_folders=not defer_maintenance)
        _t_kernel = int((_time.time() - _t1) * 1000)  # 内核：PG 写页 + 版本快照 + 图 + 正文渲染


        if mode == "apply" and res.get("applied") is False:
            # 2026-10-22（4.6）：内核**拒写**（如会话分页需确认）⇒ 不要把拒写伪装成成功 ✗
            return {"ok": False, "applied": False, "stage": "report", "kb_id": kb_id,
                    "slug": res.get("slug") or slug, "title": title,
                    "need_session_part": res.get("need_session_part"),
                    "session_part": res.get("session_part"), "ask_user": res.get("ask_user"),
                    "hint": res.get("hint"),
                    "engine": "kernel:write_knowledge/document"}
        _created = bool(res.get("created"))
        _action = ("created" if _created else "updated") if mode == "apply" else "preview"
        _graph = res.get("graph") or {}
        summary = {
            "model": model_obj["key"], "kb_id": kb_id, "knowledge_id": doc_meta["id"],
            "resolved_note": (kb_note or "").strip(), "doc_title": doc_meta.get("title", ""),
            # 2026-10-28：来源文档三态（requested 请求的 / resolved 绑到的 / note 说明）
            "source_document": {
                "requested": str((report or {}).get("source_document_id") or ""),
                "resolved": doc_meta.get("id") or "",
                "external_ref": doc_meta.get("external_ref") or "",
                "note": doc_meta.get("note") or "",
                "status": ("kb_document" if doc_meta.get("id")
                           else ("external" if doc_meta.get("external_ref") else "none")),
            },
            # 2026-10-28：超时排查（不改语义）—— 内容多大 / 准备多久 / 内核写库多久
            "content_bytes": len(body.encode("utf-8")),
            "timing": {"prepare_ms": _t_prep, "kernel_ms": _t_kernel,
                       "total_ms": int((_time.time() - _T0) * 1000)},

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
        # 2026-10-23（T2）：模板可见 —— 用了哪个模板、缺哪些必填章节、没用上模板的原因
        if _ptype == "schema:DocumentTemplate":
            pass  # 写的就是模板页本身，不报"无模板"
        elif _tpl.get("found"):
            summary["template"] = {"slug": _tpl.get("slug"), "title": _tpl.get("title"),
                                   "version": _tpl.get("version"), "found": True,
                                   "sections": len(_tpl.get("sections") or []),
                                   "required": sum(1 for s in (_tpl.get("sections") or [])
                                                   if s.get("required")),
                                   "ambiguous": bool(_tpl.get("ambiguous")),
                                   "candidates": _tpl.get("candidates") or []}
            # **跨库**用到模板 ⇒ 明说（本库没有 ⇒ 全库唯一命中才用 ✓，不静默）
            if _tpl.get("template_scope"):
                summary["template"]["scope"] = _tpl.get("template_scope")
                if _tpl.get("local_reason"):
                    summary["template"]["local_reason"] = _tpl.get("local_reason")
        else:
            summary["template"] = {"found": False}
            summary["template_fallback_reason"] = (_tpl.get("reason")
                                                   or "无可用模板（按内置结构生成）")
        _missing = [w.get("section") for w in (_report_warns or [])
                    if w.get("code") == "sections_missing"]
        if _missing:
            summary["missing_required_sections"] = _missing
        if mode != "apply":
            summary["write_note"] = ("**未写库**（dry_run）：本回执只是预览。要真正落库请用**同一份载荷**、"
                                     "`mode=\"apply\"` 重跑。")
        if _graph.get("session_page"):
            summary["source_session_link"] = dict(_graph["session_page"], ok=True, via="kernel")
        elif mode == "apply" and not ident.get("ok"):
            summary["source_session_link"] = {"ok": False, "reason": session_warn or "无会话身份"}
        return _attach_session(summary, ident, session_warn)

    # ── 纯关系模式（2026-10-30 用户口径）：**只补边、不写页、不动版本** ──────────────
    #   场景：报告/评测页必须单独建（`stage="report"`，document 类要有正文），
    #   建完再补"报告 ↔ 对象/规则"这类边；此时源页不在本批 payload 里，旧实现无能为力。
    #   入参：`edges=[{"source":"<slug>","type":"agent:evaluationOfRule","target_slug":"<slug>"}]`
    #   语义：`violations` 只回报不阻断（strict=true 时改为拦截）；`mode=dry_run` 只校验不写。
    if relations_only:
        rows = []
        for e in (edges or []):
            e = e or {}
            src = str(e.get("source") or e.get("from") or "").strip()
            rel = str(e.get("type") or e.get("rel_type") or "").strip()
            tgt = str(e.get("target_slug") or e.get("target") or e.get("to") or "").strip()
            if src and rel and tgt:
                rows.append((src, rel, tgt))
        if not rows:
            return {"ok": False, "stage": "graph", "relations_only": True,
                    "error": "edges 为空：需要 [{source,type,target_slug}, …]"}
        import ke_pages as _kp  # noqa: PLC0415
        if mode != "apply":
            import ke_graph as _kg  # noqa: PLC0415
            info = _kg.page_info_map(kb_id, sorted({s for r in rows for s in (r[0], r[2])}))
            checks = []
            for src, rel, tgt in rows:
                v = _kg.validate_edge(kb_id, src, rel, tgt, info=info)
                checks.append({"source": src, "type": rel, "target_slug": tgt,
                               "code": v.get("code"), "hint": v.get("hint"),
                               "st": v.get("st"), "tt": v.get("tt"),
                               "would_write": bool(v.get("ok")) or v.get("code") in
                               ("domain_mismatch", "range_mismatch", "unconstrained")})
            return {"ok": True, "stage": "graph", "relations_only": True, "dry_run": True,
                    "kb_id": kb_id, "total": len(rows), "checks": checks,
                    "write_note": "未写库（dry_run）：确认后用 mode=\"apply\" 同一份 edges 重跑"}
        out = _kp.write_relations_batch(kb_id, [{"source": s, "type": r, "target_slug": t}
                                                for s, r, t in rows])
        return {"ok": bool(out.get("ok")), "stage": "graph", "relations_only": True,
                "kb_id": kb_id, "total": out.get("total"), "written": out.get("written"),
                "violations": out.get("violations") or [],
                "errors": out.get("errors") or [],
                "note": ("violation 只记录不阻断（BODHI_STRICT_RELATIONS=1 可改为拦截）")}
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
