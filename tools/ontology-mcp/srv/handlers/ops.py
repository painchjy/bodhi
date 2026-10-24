"""巡检与运维切片（A1-2）：`audit_plan` / `audit_purge` / `audit_kb` / `migrate_article_body` / `clean_alias_noise`。

迁移来源：`server.py::_call_tool_impl` 里对应的 `if name == "…"` 分支（**参数解析逐字照搬**）✓
实现全在 `ke_audit` / `ke_graph` / `ke_pages`（本模块不做业务判断，只搬参数）。

约定：`kb_id` 为空 ⇒ 视为"全部库"（`ke_*` 已支持），这里**保留原样**，不追加校验 —— 迁移期只搬不改 ✓
"""
from __future__ import annotations

import ke_audit
import ke_graph
import ke_pages
from srv.handlers import handle
from srv.kbresolve import resolve_kb_id


@handle("audit_plan")
def audit_plan(args: dict) -> dict:
    return ke_audit.build_plan(str(args["kb_id"]), args.get("kinds", "all"),
                               str(args.get("scope", "all")))


@handle("audit_purge")
def audit_purge(args: dict) -> dict:
    return ke_audit.purge(str(args.get("kb_id", "")), str(args.get("kinds", "all")), "all", 5000,
                          args.get("slugs") or None, None, bool(args.get("dry_run", False)))


@handle("migrate_article_body")
def migrate_article_body(args: dict) -> dict:
    # 2026-10-22（4.2 前置）：**正文主键迁移** —— 图 `wiki_content → articleBody`
    # + 清 PG 里冗余的 `wikiContent` 属性键（默认 dry_run，只报影响面 ✓）
    _dry = bool(args.get("dry_run", True))
    _kid = str(args.get("kb_id", "") or "")
    if _kid:
        _kid, _n2 = resolve_kb_id(_kid)
    return {"ok": True, "kb_id": _kid or "all", "dry_run": _dry,
            "graph": ke_graph.backfill_article_body(_kid, dry_run=_dry),
            "page_metadata": ke_graph.strip_wiki_content_attr(_kid, dry_run=_dry),
            "note": ("正文事实源 = 图节点 `articleBody`（旧键 `wiki_content` 仅兼容）；"
                     "PG `content` 是渲染缓存，`page_metadata` 不再存正文")}


@handle("clean_alias_noise")
def clean_alias_noise(args: dict) -> dict:
    # 2026-10-24：运维清理 —— 删掉 aliases 里**与标题同名**的冗余标签（默认 dry_run 只念清单）
    _kid = str(args.get("kb_id", "") or "")
    if _kid:
        _kid, _n = resolve_kb_id(_kid)
    return ke_pages.clean_alias_noise(_kid, bool(args.get("dry_run", True)),
                                      int(args.get("limit", 2000) or 2000))
