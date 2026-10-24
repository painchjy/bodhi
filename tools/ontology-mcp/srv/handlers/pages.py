"""页面写切片（A1-3）：`retag_*`（改类型，写操作）。

迁移来源：`server.py::_call_tool_impl` 里对应的 `if name == "…"` 分支（**参数解析逐字照搬**）✓

`retag_*` 三个工具都先过 `resolve_write_kb`（写库口径：`kb_id`/`kb_ids`/`confirm_kb_match`），
`block` 非空 ⇒ 原样返回阻断回执（**不要在这里改成异常** ✗）。

2026-10-24（批 1）：`link_candidates` / `list_link_candidates` / `resolve_link_candidate` 三个
**已退役**工具（关系随 `save_knowledge` 的 relations 一次写入 ✓）的 handler 已删 —— 退役表是唯一事实源。
"""
from __future__ import annotations

import ke_pages
from srv.handlers import handle
from srv.kbresolve import resolve_write_kb


@handle("retag_preview")
def retag_preview(args: dict) -> dict:
    kb_id, _n, block = resolve_write_kb(str(args.get("kb_id", "")), args.get("kb_ids") or None,
                                        bool(args.get("confirm_kb_match")))
    if block:
        return block
    return ke_pages.retag_preview(kb_id, str(args["slug"]), str(args["new_type"]))


@handle("retag_apply")
def retag_apply(args: dict) -> dict:
    kb_id, _n, block = resolve_write_kb(str(args.get("kb_id", "")), args.get("kb_ids") or None,
                                        bool(args.get("confirm_kb_match")))
    if block:
        return block
    return ke_pages.retag_apply(kb_id, str(args["slug"]), str(args["new_type"]),
                                str(args.get("ticket", "")), args.get("acknowledge_risks") or [])


@handle("retag_rollback")
def retag_rollback(args: dict) -> dict:
    kb_id, _n, block = resolve_write_kb(str(args.get("kb_id", "")), args.get("kb_ids") or None,
                                        bool(args.get("confirm_kb_match")))
    if block:
        return block
    return ke_pages.retag_rollback(kb_id, str(args.get("ticket", "")))
