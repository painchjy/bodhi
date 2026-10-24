"""页面写切片（A1-4）：`page_copy` / `rule_publish` / `metric_compute` / `page_attributes_set`。

迁移来源：`server.py::_call_tool_impl` 里对应的 `if name == "…"` 分支（**参数解析逐字照搬**）✓
实现：`ke_page_ops` / `ke_rule_publish` / `ke_metrics` / `ke_pages`

**`save_knowledge` 暂不迁**：它依赖 `server.py` 内的写库编排（`resolve_write_kb` 之外的预算/意图/复用逻辑），
要先把那些内部函数抽到 `srv/` 才能搬 —— 留作 A1 最后一片 ✓

`page_attributes_set.dry_run` 做**字符串布尔**兼容（MCP 客户端可能传 `"false"`），原样保留 ✓
"""
from __future__ import annotations

import ke_metrics
import ke_page_ops
import ke_pages
import ke_rule_publish
from srv.handlers import handle


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
