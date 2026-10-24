"""页面写与链接切片（A1-3）：`retag_*`（改类型，写操作）+ `link_candidates` / `list_link_candidates` /
`resolve_link_candidate`（候选关联，需单独确认 ✓）。

迁移来源：`server.py::_call_tool_impl` 里对应的 `if name == "…"` 分支（**参数解析逐字照搬**）✓

`retag_*` 三个工具都先过 `resolve_write_kb`（写库口径：`kb_id`/`kb_ids`/`confirm_kb_match`），
`block` 非空 ⇒ 原样返回阻断回执（**不要在这里改成异常** ✗）。
"""
from __future__ import annotations

import ke_pages
from srv.handlers import handle
from srv.kbresolve import resolve_write_kb
from srv.links import (
    link_candidates as _link_candidates, list_link_candidates as _list_link_candidates,
    resolve_link_candidate as _resolve_link_candidate,
)


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


@handle("link_candidates")
def link_candidates(args: dict) -> dict:
    return _link_candidates(str(args["kb_id"]), str(args.get("source_slug", "")),
                            str(args.get("relation", "")), args.get("candidates") or [],
                            str(args.get("knowledge_id", "")), int(args.get("round_no", 0) or 0),
                            kb_ids=args.get("kb_ids") or None,
                            confirm_kb_match=bool(args.get("confirm_kb_match")))


@handle("list_link_candidates")
def list_link_candidates(args: dict) -> dict:
    return _list_link_candidates(str(args["kb_id"]), str(args.get("knowledge_id", "")),
                                 str(args.get("status", "pending")))


@handle("resolve_link_candidate")
def resolve_link_candidate(args: dict) -> dict:
    return _resolve_link_candidate(str(args["kb_id"]), str(args.get("candidate_id", "")),
                                   str(args.get("action", "")),
                                   str(args.get("knowledge_id", "")),
                                   kb_ids=args.get("kb_ids") or None,
                                   confirm_kb_match=bool(args.get("confirm_kb_match")))
