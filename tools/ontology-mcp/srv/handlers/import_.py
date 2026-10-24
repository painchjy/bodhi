"""导入 / 参考规范切片（A1-2）：`import_probe` / `import_plan` / `import_apply` / `import_refresh` / `import_state`
+ `reference_lookup`。

迁移来源：`server.py::_call_tool_impl` 里对应的 `if name == "…"` 分支（**参数解析逐字照搬**）✓
实现全在 `ke_sheet` / `ke_import` / `ke_review`（本模块不做业务判断，只搬参数）。

注意 `import_apply` 的 `prune` 是**三态**（缺省 ⇒ `None` = 沿用票据；显式 false ⇒ 覆盖），
不能写成 `bool(args.get("prune"))` ✗
"""
from __future__ import annotations

import ke_import
import ke_review
import ke_sheet
from srv.handlers import handle


@handle("import_probe")
def import_probe(args: dict) -> dict:
    return ke_sheet.probe(str(args["file"]), str(args.get("sheet", "")), int(args.get("sample", 3) or 3))


@handle("import_plan")
def import_plan(args: dict) -> dict:
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


@handle("import_apply")
def import_apply(args: dict) -> dict:
    return ke_import.apply(str(args.get("ticket", "")), actor=str(args.get("actor", "agent:import")),
                           prune=bool(args["prune"]) if "prune" in args else None)


@handle("import_refresh")
def import_refresh(args: dict) -> dict:
    return ke_import.refresh_metadata(str(args.get("kb_id", "")), bool(args.get("dry_run", False)),
                                      int(args.get("limit", 5000) or 5000))


@handle("import_state")
def import_state(args: dict) -> dict:
    return ke_import.state(str(args.get("batch", "")))


@handle("reference_lookup")
def reference_lookup(args: dict) -> dict:
    _to = args.get("timeout")
    return ke_review.reference_lookup(str(args.get("kb_id", "")), str(args.get("reference", "")),
                                      bool(args.get("allow_fetch", False)),
                                      int(args.get("max_chars", 6000) or 6000),
                                      float(_to) if _to not in (None, "") else 8.0)
