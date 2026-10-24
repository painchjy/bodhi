"""要素 → 页面（A1-8 迁自 `server.py`，**实现逐字未改**）

- `save_elements`（322 行）= 写库主路径：匹配存量页 → 建页/改页 → 关系边 → 会话溯源 → 刷新矩阵；
- `design_elements`（150 行）= 设计载荷同一套流程（按 slug 复用 + 正文整体替换）；
- `build_new_page` / `element_slug` / `element_page_slug` / `fetch_existing_pages`：页面渲染与候选；
- `_attrs_with_identity`：**身份只由服务端注入**（客户端同名属性一律丢弃，防伪造）；
- `retract_relations`：撤关系（按 `_graph_target_slug` 定位目标）。

本模块只依赖 `ke_*` 与 `srv.*` 低层模块，**不认识工具协议**（handler 在 `srv/handlers/write.py`）。
"""
from __future__ import annotations

import hashlib
import json
import re

import os
import sys

_CORE = os.path.abspath(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                     "..", "ke-core"))
if _CORE not in sys.path:
    sys.path.insert(0, _CORE)

import ke_db  # noqa: E402
import ke_ontology  # noqa: E402
import ke_pages  # noqa: E402
from ke_db import psql_csv, sql_str  # noqa: E402
from ke_graph_view import class_category_path  # noqa: E402
from ke_overview import refresh_crud_matrix, service_types  # noqa: E402
from ke_pages import rel_line  # noqa: E402
from srv.clock import now_text  # noqa: E402
from srv.constants import DEFAULT_HIGH, DEFAULT_LOW  # noqa: E402
from srv.constants import TOOL_TAG  # noqa: E402
from srv.engine import load_engine  # noqa: E402
from srv.kbresolve import get_kb_tenant  # noqa: E402
from srv.render import _design_sections, context_meta  # noqa: E402
from srv.reqctx import _reset_request_cache  # noqa: E402
from srv.session import _IDENT, _session_ident  # noqa: E402
from srv.session_link import IDENTITY_CLASS, _session_part_no  # noqa: E402
from srv.text import normalize_name  # noqa: E402

TYPE_PENDING = "ontology:PendingMerge"   # 仅历史遗留页只读识别（2026-10-10 起不再生成）


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
            "以下节点是**文档类**（本体 `render_mode=document`，即 `schema:CreativeWork` 谱系）："
            "`stage=\"graph\"` **只写实体类**、不接受正文 → 本批**未建页/未改页**。"
            "文档类（报告/模板/设计单…）请改用 `stage=\"report\"`（`report.page_type` 指定类、"
            "`report.content_md` 给整篇正文，同一 slug 幂等覆盖）。")
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
        # 写后核对（2026-09-24）：回执里的每个 slug 必须**在本库**查得到 ——
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
