"""工具处理注册表（A1 切片）——**结构拆分的落点**，不改任何工具语义。

为什么要有这一层
----------------
`server.py` 原来是一个 2700+ 行的 `if name == "xxx":` 长链：新增/修改一个工具要在同一文件里
翻几百行，且难以按"域"（读/写/抽取/巡检/上下文/导入）分开维护。

做法（**双轨、零行为变化**）
--------------------------
1. `@handle("<工具名>")` 把处理函数登记进 `REGISTRY`（**别在文档里写真实工具名**：
   `tests/_helpers.py::dispatcher_names()` 会把它当成"已注册工具"✓）；
2. `server._call_tool_impl` 先调 `dispatch(name, args)`：
   命中 ⇒ 直接用注册表；未命中（返回 `None`）⇒ 继续走**原来的 if 链**。
3. 逐片迁移：每迁一个工具就从 `server.py` 删掉对应分支（保留 `srv.*` 名字不变 ⇒ 下游零改动）。

约定（重要）
-----------
- `srv/handlers/**` **不得 import server**（否则循环依赖 ✗）——只用 `srv.*` 与 `ke_*`；
- 处理函数签名统一 `f(args: dict) -> dict`，参数解析（`args.get(...)` 与默认值）**逐字照搬**原分支，
  这样"迁移"只是搬家，不是重写；
- 返回 `None` 表示"未注册"，返回 dict（含 `ok=False`）表示已处理 —— 不在注册表里做二次兜底逻辑。
"""
from __future__ import annotations

from typing import Callable, Dict, Optional

REGISTRY: Dict[str, Callable[[dict], dict]] = {}

# 2026-11-04（用户口径）：**按知识库范围统一校验** —— 只校验「目标库 ∈ allowed」，
# **不套 domain/work**（否则运维智能体没领域库会被全拒）。跨库/概念类（context_*）与
# 不依赖库的（skills / ontology_types / lookup_turn_begin …）**不在名单里**。
KB_SCOPED_READ = {
    "audit_scan", "doc_outline", "doc_parse", "grep_chunks", "image_extract",
    "ext_doc_parse", "kb_export",
}
KB_SCOPED_WRITE = {
    "audit_plan", "audit_purge", "kb_import", "retag_preview", "retag_apply",
    "retag_rollback", "import_probe", "import_plan", "import_apply", "import_state",
    "import_refresh", "page_attributes_set", "clean_alias_noise", "ext_doc_push",
}
KB_SCOPED_TOOLS = KB_SCOPED_READ | KB_SCOPED_WRITE


def _kb_scope_guard(name: str, args: dict):
    """返回 dict = 拒绝；None = 放行。

    **只在「有 session_no + 给了 kb_id + scope 可用」时校验**：无会话的内部调用/CLI 不受影响
    （用户口径：不要造成无法使用）。只读工具额外允许**方法层库**（任务页在那里）。
    """
    if name not in KB_SCOPED_TOOLS:
        return None
    sid = str(args.get("session_no") or "").strip()
    raw_kb = str(args.get("kb_id") or "").strip()
    if not (sid and raw_kb):
        return None
    try:                                    # noqa: PLC0415
        import ke_db  # noqa: PLC0415
        import ke_scope  # noqa: PLC0415
        kb_id, kb_name, _note = ke_db.resolve_kb_id(raw_kb)
        sc = ke_scope.agent_scope(sid)
    except Exception:  # noqa: BLE001  解析不了/scope 不可用 ⇒ 交给工具自己报错
        return None
    if not sc.get("ok"):
        return None
    allowed = {str(x).lower() for x in (sc.get("allowed") or [])}
    if not allowed:
        return None
    ok_ids = set(allowed)
    if name in KB_SCOPED_READ:              # 只读：方法层库（任务页/步骤）也允许
        _m = str((sc.get("method_kb") or {}).get("id") or "").lower()
        if _m:
            ok_ids.add(_m)
    if str(kb_id).lower() not in ok_ids:
        return {"ok": False, "error": "out_of_scope", "tool": name,
                "kb": {"id": kb_id, "name": kb_name},
                "kb_scope": {"kb_mode": sc.get("kb_mode"), "allowed_count": len(allowed),
                             "allowed_sample": [k.get("name") or k.get("id")
                                                for k in (sc.get("agent_kbs") or [])[:5]]},
                "hint": ("目标库「%s」不在本智能体的知识库范围（kb_selection_mode=%s）"
                         % (kb_name or kb_id, sc.get("kb_mode")))}
    return None


def handle(name: str):
    """把处理函数登记为某工具的实现（同名重复登记直接报错，避免静默覆盖 ✗）。"""
    def deco(fn: Callable[[dict], dict]) -> Callable[[dict], dict]:
        if name in REGISTRY and REGISTRY[name] is not fn:
            raise RuntimeError("工具处理函数重复登记：%s" % name)
        REGISTRY[name] = fn
        return fn
    return deco


def dispatch(name: str, args: dict) -> Optional[dict]:
    """注册表优先分发；未登记 ⇒ `None`（调用方回落到旧 if 链 ✓）。"""
    fn = REGISTRY.get(name)
    if fn is None:
        return None
    args = args or {}
    blocked = _kb_scope_guard(name, args)      # 2026-11-04：统一库范围校验（只读含方法层库）
    if blocked:
        return blocked
    return fn(args)


def names() -> list:
    """已迁移的工具名（文档/自检用）。"""
    return sorted(REGISTRY)


# 各切片按"域"导入，导入即登记（顺序无关：只影响注册表填充，不影响分发）
from srv.handlers import read as _read  # noqa: E402,F401
from srv.handlers import ops as _ops  # noqa: E402,F401
from srv.handlers import import_ as _import  # noqa: E402,F401
from srv.handlers import query as _query  # noqa: E402,F401
from srv.handlers import pages as _pages  # noqa: E402,F401
from srv.handlers import write as _write  # noqa: E402,F401
from srv.handlers import ext as _ext  # noqa: E402,F401
from srv.handlers import ab as _ab  # noqa: E402,F401
from srv.handlers import catalog as _catalog  # noqa: E402,F401
from srv.handlers import context_ as _context  # noqa: E402,F401
from srv.handlers import media as _media  # noqa: E402,F401
# 2026-10-28：外部文档三件套（探测附件 / 推送并绑档案 / 解析正文）
from srv.handlers import ext_docs as _ext_docs  # noqa: E402,F401
# 2026-10-30：规则工程工具（ke_rule_check 体检）
from srv.handlers import rules as _rules  # noqa: E402,F401

