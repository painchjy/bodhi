"""规则表达式 `rules=`（多策略 / 组合策略 / 单条规则）—— 纯解析 + 解析委托。

语法（任务 `bpmn:activityEntry/activityExit` 的 `rules=`）：

    rules := term ("," term)*
    term  := "policy:" <policy-slug> | "rule:" <rule-slug> | <rule-slug>

- `policy:` 复用 `ke_review.rules_resolve(policy:…)`：内置 `includesBusinessPolicy`
  递归闭包 + 双向规则边，**组合策略自动展开**；
- `rule:` / 裸 slug 走 `rules_resolve(direct:…)`；
- 多个 term 取并集、按规则 slug 去重；解析不到 ⇒ `unresolved[]`（不静默）。
"""
from __future__ import annotations


def parse(expr: str) -> list[dict]:
    """表达式 → `[{kind, value, raw}]`（kind=policy|rule）。"""
    out = []
    for raw in [x.strip() for x in str(expr or "").replace("，", ",").split(",") if x.strip()]:
        if raw.startswith("policy:"):
            out.append({"kind": "policy", "value": raw[len("policy:"):].strip(), "raw": raw})
        elif raw.startswith("rule:"):
            out.append({"kind": "rule", "value": raw[len("rule:"):].strip(), "raw": raw})
        else:
            out.append({"kind": "rule", "value": raw, "raw": raw})
    return [x for x in out if x["value"]]


def expand(kb_id: str, expr: str, limit: int = 500) -> dict:
    """展开表达式 → `{ok, terms[], policies[], rules[], unresolved[], errors[]}`。

    规则字段沿用 `ke_review.rules_resolve` 的 `rules[]`（slug/title/…）；
    去重键 = `slug`（无 slug 时用 title）。
    """
    import ke_review as _R  # noqa: PLC0415  懒导入（ke_review 依赖较多）

    terms = parse(expr)
    if not terms:
        return {"ok": True, "terms": [], "policies": [], "rules": [],
                "unresolved": [], "errors": []}
    rules: dict = {}
    policies: list = []
    unresolved: list = []
    errors: list = []
    for t in terms:
        spec = ("policy:%s" % t["value"]) if t["kind"] == "policy" else ("direct:%s" % t["value"])
        try:
            got = _R.rules_resolve(kb_id, spec, limit)
        except Exception as exc:  # noqa: BLE001
            errors.append("%s → %s" % (t["raw"], str(exc)[:160]))
            unresolved.append(t["raw"])
            continue
        rows = got.get("rules") or []
        if t["kind"] == "policy":
            policies.append(t["value"])
        if not rows:
            unresolved.append(t["raw"])
            continue
        for r in rows:
            key = str(r.get("slug") or r.get("title") or "")
            if key:
                rules.setdefault(key, r)
    return {"ok": not unresolved and not errors, "terms": terms,
            "policies": sorted(set(policies)), "rules": list(rules.values()),
            "unresolved": unresolved, "errors": errors}
