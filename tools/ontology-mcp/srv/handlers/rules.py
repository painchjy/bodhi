"""规则工程工具：`ke_rule_check`（体检）+ `ke_rule_publish`（双门禁）。"""
from __future__ import annotations

import os

from srv.handlers import handle


def _default_spec() -> str:
    """默认规则集 spec：**由部署配置给**（`BODHI_DEFAULT_RULE_SPEC`），代码不写领域名 ✓。

    铁律①：工具只认"策略/规则页"这类**类型**，不认具体策略名 ⇒ 默认值走 env，
    没配就让调用方显式传 `spec`（宁可不猜 ✗）。
    """
    return str(os.environ.get("BODHI_DEFAULT_RULE_SPEC") or "").strip()


@handle("ke_rule_check")
def _rule_check(args: dict) -> dict:
    """**规则体检（门禁一）**：字段齐/可机算/枚举一致/可回溯；返回 errors + warns 分级。

    任务页「规则化/规则发布」写的就是它；此前只在 CLI 里，**没注册成 MCP 工具** ⇒
    智能体只能拿 `ke_rule_publish(apply=false)` 的硬错误（丢 warn 分级）。
    """
    import ke_rule_check as C  # noqa: PLC0415
    kb = str(args.get("kb") or args.get("kb_id") or "").strip()
    spec = str(args.get("spec") or "").strip() or _default_spec()
    if not kb:
        return {"ok": False, "error": "need_kb",
                "hint": "传 kb_id（领域库）；spec=policy:<策略 slug> 或 direct:<规则 slug,…>"}
    if not spec:
        return {"ok": False, "error": "need_spec",
                "hint": ("传 spec=policy:<策略页 slug> 或 direct:<规则 slug,…>"
                         "（默认值可由 env BODHI_DEFAULT_RULE_SPEC 配）")}
    res = C.check_rules(kb, spec)
    return dict(res, tool="ke_rule_check", kb=kb, spec=spec,
                counts={"rules": res.get("rules", 0),
                        "errors": len(res.get("errors") or []),
                        "warns": len(res.get("warns") or [])},
                md=C.render_md(res, kb, spec))


@handle("ke_rule_publish")
def _rule_publish(args: dict) -> dict:
    """**规则发布（双门禁）**：体检（errors+warns）+ 回放评测 → 通过才写 `bmm:ruleHash`。

    2026-10-30：回执**补 `warns` 分级**（此前只有硬错误 ⇒ 智能体看不到"未声明 applies_when"这类提醒）。
    `apply=false` 只算不写（dry_run）。
    """
    import ke_rule_check as C  # noqa: PLC0415
    import ke_rule_publish as P  # noqa: PLC0415
    kb = str(args.get("kb") or args.get("kb_id") or "").strip()
    spec = str(args.get("spec") or "").strip() or _default_spec()
    apply = bool(args.get("apply"))
    if not kb:
        return {"ok": False, "error": "need_kb", "hint": "传 kb_id（领域库）"}
    if not spec:
        return {"ok": False, "error": "need_spec",
                "hint": ("传 spec=policy:<策略页 slug> 或 direct:<规则 slug,…>"
                         "（默认值可由 env BODHI_DEFAULT_RULE_SPEC 配）")}
    out = P.publish_with_hash(kb, spec, apply=apply)
    chk = C.check_rules(kb, spec)
    out.update({"tool": "ke_rule_publish", "kb": kb, "spec": spec,
                "warns": chk.get("warns") or [],
                "check_errors": chk.get("errors") or [],
                "gate": "ok" if (chk.get("ok") and out.get("ok")) else "blocked"})
    return out

