"""页面写切片（A1-4 + A1-8）：`save_knowledge` / `page_copy` / `rule_publish` / `metric_compute` / `page_attributes_set`。

迁移来源：`server.py::_call_tool_impl` 里对应的 `if name == "…"` 分支（**参数解析逐字照搬**）✓
实现：`srv/knowledge_write.py`（写库编排，A1-8 迁入）/ `ke_page_ops` / `ke_rule_publish` / `ke_metrics` / `ke_pages`

2026-10-24（A1-8）：`save_knowledge` 已迁入 `srv/knowledge_write.py` ⇒ 本切片接管它的分发，
`server.py` 的 if 链**清零**（注册表 37/37 全覆盖 ✓）。

`page_attributes_set.dry_run` 做**字符串布尔**兼容（MCP 客户端可能传 `"false"`），原样保留 ✓
"""
from __future__ import annotations

import ke_metrics
import ke_page_ops
import ke_pages
import ke_rule_publish
from srv.handlers import handle
from srv.knowledge_write import save_knowledge


@handle("page_copy")
def page_copy(args: dict) -> dict:
    return ke_page_ops.copy_page(str(args.get("kb_id", "")), str(args.get("src_slug", "")),
                                 str(args.get("new_slug", "")), str(args.get("new_title", "")),
                                 bool(args.get("copy_relations", True)))


@handle("rule_publish")
def rule_publish(args: dict) -> dict:
    return ke_rule_publish.publish_with_hash(str(args.get("kb_id", "")), str(args.get("spec", "")),
                                             bool(args.get("apply", False)))


@handle("metric_compute")
def metric_compute(args: dict) -> dict:
    return ke_metrics.compute(str(args.get("kb_id", "")), str(args.get("spec", "")),
                              bool(args.get("with_rules", False)))


@handle("page_attributes_set")
def page_attributes_set(args: dict) -> dict:
    _pa_dry = args.get("dry_run", True)
    if isinstance(_pa_dry, str):
        _pa_dry = _pa_dry.strip().lower() not in ("0", "false", "no", "off", "")
    return ke_pages.set_page_attributes(str(args.get("kb_id", "")), str(args.get("slug", "")),
                                        args.get("patch") or {},
                                        actor=str(args.get("actor", "")),
                                        dry_run=bool(_pa_dry))


@handle("save_knowledge")
def save_knowledge_tool(args: dict) -> dict:
    """写库唯一入口（`stage=graph|report`）。**参数解析逐字照搬**原 `if name == "save_knowledge"` 分支 ✓

    2026-10-25（P3 闸门，分级）：`stage=report` 时先跑外部台账闸门 ——
    **G3 硬**（报告里的条目编号必须来自台账，`mode=apply` 才拦）、**G1/G2 软**（契约漂移 / 行数不符 ⇒ `warnings`）。
    """
    _stage = str(args.get("stage", "report"))
    _gate = {}
    _anchor = {}
    # 2026-10-31（用户口径 A）：**引用块由任务步骤按台账回执原样插入**。
    # `save_knowledge` 不再"补块"，也不再做**依赖领域知识**的台账/契约校验
    # （否则领域知识一改，写库能力就静默失效 ✗）。块的生成/校验下放到：
    #   · 任务步骤（`ext_issue_list(view="table")` 回执的 `ref_block` 原样插入）
    #   · 独立巡检（`ke_ext_gate.check_report`，按需跑、不阻断写库）
    if _stage == "report":
        _anchor = {"changed": False, "reason": "disabled_by_design"}
        _gate = {"ok": True, "skipped": "ext_gate_disabled",
                 "note": "台账/契约校验已下放到任务步骤（ext_issue_list 回执 + 任务页自检）"}
    _out = save_knowledge(
        str(args["kb_id"]), stage=str(args.get("stage", "report")),
        model=str(args.get("model", "bmm")), report=args.get("report"),
        nodes=args.get("nodes") or [], edges=args.get("edges") or [],
        mode=str(args.get("mode", "dry_run")),
        confirmed_new_applications=args.get("confirmed_new_applications") or [],
        session=args.get("session") or None, kb_ids=args.get("kb_ids") or None,
        confirm_kb_match=bool(args.get("confirm_kb_match")),
        context=str(args.get("context", "")),
        session_no=str(args.get("session_no", "")),
        doc_id=str(args.get("doc_id", "")), intent=str(args.get("intent", "")),
        acknowledge_intent_change=bool(args.get("acknowledge_intent_change", False)))
    # 软警告（G1/G2）与闸门明细一并回给调用方（不拦写 ✓）
    if isinstance(_out, dict) and _gate.get("warnings"):
        _w = list(dict.fromkeys((_out.get("warnings") or []) + _gate["warnings"]))
        _out["warnings"] = _w
        _out["ext_gate"] = _gate
    if isinstance(_out, dict) and _anchor.get("changed"):
        # 让调用方知道"锚点是工具补的"（编号取自报告上下文 → 台账分片 ✓）
        _out["ext_ref_block"] = {k: _anchor.get(k) for k in
                                 ("object", "review_no", "rev", "columns",
                                  "review_no_source", "reason")}
    return _out

