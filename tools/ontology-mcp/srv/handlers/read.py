"""只读切片（A1-1）：巡检 / 跨库上下文 / 库导出导入 —— **纯参数搬运，实现全在 `srv.*`**。

迁移来源：`server.py::_call_tool_impl` 里对应的 `if name == "…"` 分支（逐字照搬）✓
- `audit_scan` / `context_scan` / `context_lookup` / `context_page` / `context_authority`
  ⇒ `srv.tools_read`（P2 S8b-1 已抽出的只读薄封装）
- `kb_export` / `kb_import` ⇒ `srv.tools_ops`（P2 S8b-2a）

为什么不在这里再包一层校验：**迁移期只搬不改** —— 任何口径调整另开切片，否则无法用
"同参数同回执"来验证迁移正确性 ✓
"""
from __future__ import annotations

from srv import ab_turn
from srv.handlers import handle
from srv.tools_ops import kb_export as _kb_export, kb_import as _kb_import
from srv.tools_read import (
    audit_scan as _audit_scan, context_authority as _context_authority,
    context_lookup as _context_lookup, context_page as _context_page,
    context_scan as _context_scan,
)

@handle("audit_scan")
def audit_scan(args: dict) -> dict:
    return _audit_scan(str(args.get("kb_id", "")), args.get("kb_ids") or None,
                       str(args.get("scope", "all")), int(args.get("max_findings", 50)),
                       str(args.get("template_kb_id", "") or ""))


@handle("kb_export")
def kb_export(args: dict) -> dict:
    return _kb_export(str(args.get("kb_id", "")), str(args.get("out_dir", "")),
                      bool(args.get("make_zip", True)))


@handle("kb_import")
def kb_import(args: dict) -> dict:
    return _kb_import(str(args.get("source", "")), str(args.get("kb_id", "")),
                      str(args.get("new_kb", "")), bool(args.get("keep_ids", False)),
                      bool(args.get("verify", True)))


@handle("context_scan")
def context_scan(args: dict) -> dict:
    return _context_scan(args.get("kb_ids") or None, int(args.get("limit", 200) or 200),
                         bool(args.get("write", True)))


@handle("context_lookup")
def context_lookup(args: dict) -> dict:
    # A/B C1：与 knowledge_lookup 同轮时认领"首个检索动作"（顺序判定用 ✓）
    _turn = str(args.get("turn_id", "") or "")
    if _turn:
        ab_turn.claim(_turn, "context_lookup")
    return _context_lookup(args.get("kb_ids") or None, str(args.get("q", "")),
                           str(args.get("slug", "")))


@handle("context_page")
def context_page(args: dict) -> dict:
    return _context_page(str(args.get("kb_id", "")), str(args.get("slug", "")),
                         str(args.get("q", "")))


@handle("context_authority")
def context_authority(args: dict) -> dict:
    return _context_authority(str(args.get("slug", "")), str(args.get("master", "")))
