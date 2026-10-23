"""只读工具实现（P2 切片 S8b-1 ✓ 从 `server.py` 抽出，**实现逐字未改**）

都是**薄封装**：真正的逻辑在 `ke_context` / `ke_audit` 里 ✓
本模块只负责参数归一化（`kb_ids` 去空白）+ 委托，方便 `_call_tool_impl` 分组路由。
"""
from __future__ import annotations

import os
import sys

_CORE = os.path.abspath(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                     "..", "ke-core"))
if _CORE not in sys.path:
    sys.path.insert(0, _CORE)

import ke_audit  # noqa: E402
import ke_context  # noqa: E402
import ke_db  # noqa: E402
from srv.kbresolve import resolve_kb_id  # noqa: E402


def context_scan(kb_ids: list | None = None, limit: int = 200, write: bool = True) -> dict:
    """**只读**：跨库同名/同实例候选（`ke_context.scan` 的入口）。

    用户口径（2026-09-28）：**同名** = 归一化 `slug` 相同；**同实例** = 本体类 + 归一化标题相同
    —— 两者合并成**一张**候选表（`matched_by` 标出命中哪条判据）。只写 `state/context_map/`，不碰任何页。
    """
    kb_ids = [str(k).strip() for k in (kb_ids or []) if str(k).strip()]
    return ke_context.scan(kb_ids or None, limit=int(limit), write=bool(write))


def context_lookup(kb_ids: list | None = None, q: str = "", slug: str = "") -> dict:
    """**只读**：检索前查跨库同名/同义/异义/依赖（`ke_context.lookup` 的入口）。"""
    kb_ids = [str(k).strip() for k in (kb_ids or []) if str(k).strip()]
    return ke_context.lookup(kb_ids or None, q=q, slug=slug)


def context_page(kb_id: str = "", slug: str = "", q: str = "") -> dict:
    """**只读**：领域页 ←（**同名 slug**）→ 概念页 的渲染视图（`ke_context.page_view` 的入口）。

    用户口径（2026-09-28）：领域库**不写 uuid、不互相引用**；关系按 slug 同名查概念库；
    跨域关系只能**经企业共享概念页转换**。
    """
    return ke_context.page_view(str(kb_id or ""), slug=str(slug or ""), q=str(q or ""))


def context_authority(slug: str, master: str = "") -> dict:
    """**只读**：权威/副本分工与影响面（`ke_context.authority_preview` 的入口）。"""
    return ke_context.authority_preview(str(slug or ""), str(master or ""))


def audit_scan(kb_id: str = "", kb_ids: list | None = None, scope: str = "all",
               max_findings: int = 50, template_kb_id: str = "") -> dict:
    """**只读**巡检（`ke_audit.audit` 的入口）。

    - 单库：传 `kb_id`；
    - **多库**：传 `kb_ids`（会话绑定的库清单）→ 逐库巡检，`per_kb[]` 里每库一份报告；
      计数**不跨库合并**（同名不同类型/不同语义是常态，合并会误导）；
    - `template_kb_id`：`scope="template"` 时模板页所在库 —— **模板以领域库为准**（报告在任务库、
      模板在领域库 ⇒ 显式给领域库）；空则只看本库（不跨库乱扫 ✗），多候选只报不选。
    """
    ids = [str(x).strip() for x in (kb_ids or []) if str(x).strip()]
    if ids:
        per = []
        for raw in ids:
            try:
                uid, name, _note = ke_db.resolve_kb_id(raw)
            except ValueError as exc:
                per.append({"asked": raw, "error": str(exc)[:200]})
                continue
            rep = ke_audit.audit(uid, scope, max_findings, template_kb_id=template_kb_id)
            per.append({"kb_id": uid, "kb_name": name, "report": rep})
        return {"multi": True, "count": len(per), "per_kb": per,
                "note": "多库巡检：每库一份独立报告（findings 不跨库合并）"}
    if not (kb_id or "").strip():
        return {"error": "需要 kb_id（单库）或 kb_ids（多库，会话绑定的库清单）"}
    uid, note = resolve_kb_id(kb_id)
    rep = ke_audit.audit(uid, scope, max_findings, template_kb_id=template_kb_id)
    rep["kb_id"] = uid
    if note:
        rep["resolved_note"] = note
    return rep
