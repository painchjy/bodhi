"""上下文写切片（A1-7）：`context_concept_apply` / `context_concept_rollback` / `context_authority_apply`。

迁移来源：`server.py::_call_tool_impl` 里对应的 `if name == "…"` 分支（**参数解析逐字照搬**）✓
实现：`srv/context_write.py`（与 `srv/http.py` 的 REST 路由**共用同一份实现**）

守门全在 `ke_context`：写权限（`X-Bodhi-Tenant` 租户）+ ticket 指纹 + `acknowledge_risks` 完全一致；
领域库**零写入** ✓。`context_authority_apply` 的 `apply` 采用"传了才覆盖默认 True"的旧语义（逐字保留）。
"""
from __future__ import annotations

from srv.context_write import (
    context_authority_apply, context_concept_apply, context_concept_rollback,
)
from srv.handlers import handle


@handle("context_concept_apply")
def context_concept_apply_tool(args: dict) -> dict:
    return context_concept_apply(str(args.get("slug", "")), str(args.get("ticket", "")),
                                 args.get("acknowledge_risks") or [],
                                 str(args.get("definition", "")),
                                 str(args.get("standard_name", "")),
                                 str(args.get("actor", "")))


@handle("context_concept_rollback")
def context_concept_rollback_tool(args: dict) -> dict:
    return context_concept_rollback(str(args.get("ticket", "")))


@handle("context_authority_apply")
def context_authority_apply_tool(args: dict) -> dict:
    return context_authority_apply(str(args.get("action", "")), str(args.get("slug", "")),
                                   str(args.get("master", "")), str(args.get("kb", "")),
                                   str(args.get("ticket", "")),
                                   args.get("acknowledge_risks") or [],
                                   str(args.get("actor", "")),
                                   bool(args["apply"]) if "apply" in args else True)
