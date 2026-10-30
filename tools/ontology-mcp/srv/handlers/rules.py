"""规则工程工具：`ke_rule_check`（体检）+ `ke_rule_publish`（双门禁）。"""
from __future__ import annotations

from srv.handlers import handle


@handle("ke_rule_check")
def _rule_check(args: dict) -> dict:
    """**规则体检（门禁一）**：字段齐/可机算/枚举一致/可回溯；返回 errors + warns 分级。

    任务页「规则化/规则发布」写的就是它；此前只在 CLI 里，**没注册成 MCP 工具** ⇒
    智能体只能拿 `ke_rule_publish(apply=false)` 的硬错误（丢 warn 分级）。
    """
    import ke_rule_check as C  # noqa: PLC0415
    kb = str(args.get("kb") or args.get("kb_id") or "").strip()
    spec = str(args.get("spec") or "").strip() or "policy:bmm/businesspolicy/技术方案评审策略"
    if not kb:
        return {"ok": False, "error": "need_kb",
                "hint": "传 kb_id（领域库）；spec 默认 policy:<策略 slug>"}
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
    spec = str(args.get("spec") or "").strip() or "policy:bmm/businesspolicy/技术方案评审策略"
    apply = bool(args.get("apply"))
    if not kb:
        return {"ok": False, "error": "need_kb", "hint": "传 kb_id（领域库）"}
    out = P.publish_with_hash(kb, spec, apply=apply)
    chk = C.check_rules(kb, spec)
    out.update({"tool": "ke_rule_publish", "kb": kb, "spec": spec,
                "warns": chk.get("warns") or [],
                "check_errors": chk.get("errors") or [],
                "gate": "ok" if (chk.get("ok") and out.get("ok")) else "blocked"})
    return out

