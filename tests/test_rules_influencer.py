"""P0-2 回归：**链路口径规则解析**（影响因素 → 影响手段 → 策略 → 规则）

口径（2026-10-25 用户裁定）：
- 影响类问题只认**链路**：影响因素 → 影响手段 → 策略（含子策略）→ 规则；
- 规则边**双向**取（规则→策略 `isDerivedFrom` + 策略→规则 `isBasisFor`）——
  只取一个方向会漏（实测：某策略的 6 条规则只在出边上，只查入边回 0 条 ✗）；
- **越界项永不算**：链路外策略所辖的规则不得出现在结果里（见 `FORBID`）。

只断言**结构/集合**（不断言自由文本）；MCP / DB 不可用 ⇒ skip。
"""
import pytest

INFLUENCER = "信创与异地机房搬迁等重大架构调整"

# 真值（图里核过：链路口径 = 6 条，且**不含** FORBID）
EXPECT = {
    "bmm/operativebusinessrule/应用架构系统边界原则",
    "bmm/operativebusinessrule/技术方案文档结构规范",
    "bmm/operativebusinessrule/技术方案章节裁剪规则",
    "bmm/operativebusinessrule/技术架构开源软件适用规则",
    "bmm/operativebusinessrule/技术架构系统隔离原则",
    "bmm/operativebusinessrule/部署架构最低部署和灾备要求",
}
FORBID = {"bmm/operativebusinessrule/合理性行动方案合理性"}


def _slugs(resp: dict) -> set:
    return {str(r.get("slug")) for r in (resp.get("rules") or []) if r.get("slug")}


def test_influencer_route_is_chain_only(mcp, kbs):
    """`influencer:` ⇒ 恰好链路内 6 条；越界规则**不得**出现。"""
    r = mcp.call("rules_resolve", {"kb_id": kbs["domain"], "spec": "influencer:" + INFLUENCER})
    assert r.get("resolved_by") == "influencer", r
    assert (r.get("counts") or {}).get("rules") == len(EXPECT), \
        "链路规则数应为 %d，实测 %r" % (len(EXPECT), r.get("counts"))
    assert _slugs(r) == EXPECT, "链路规则集合不符：多=%r 少=%r" % (
        _slugs(r) - EXPECT, EXPECT - _slugs(r))
    assert not (_slugs(r) & FORBID), "越界规则混进链路：%r" % (_slugs(r) & FORBID)
    chain = r.get("chain") or []
    assert chain and chain[0]["influencer"]["name"], "链路明细缺失 ✗"
    assert chain[0]["policies"], "链路应给出 影响因素→策略 的明细 ✗"


def test_policy_uses_both_edge_directions(mcp, kbs):
    """`policy:` 必须**双向**取边：两条策略都应回到同一批 6 条（此前有一条回 0 ✗）。"""
    for spec in ("policy:技术方案逐项评审策略", "policy:技术方案评审策略"):
        r = mcp.call("rules_resolve", {"kb_id": kbs["domain"], "spec": spec})
        assert _slugs(r) == EXPECT, "%s 取数不符：多=%r 少=%r" % (
            spec, _slugs(r) - EXPECT, EXPECT - _slugs(r))


def test_lookup_rules_passes_chain_through(mcp, kbs):
    """`knowledge_lookup(mode=rules)` 是同一实现 ⇒ 6 条 + `chain` 必须透传（旧版会丢 ✗）。"""
    r = mcp.call("knowledge_lookup", {"kb_id": kbs["domain"], "mode": "rules",
                                      "spec": "influencer:" + INFLUENCER, "ontology_hint": True})
    assert r.get("resolved_by") == "influencer", r
    assert _slugs(r) == EXPECT
    assert r.get("chain"), "knowledge_lookup 应透传 chain（不再白名单挑字段）✗"
    assert (r.get("plan") or {}).get("route") == "influencer", "提示面应给出 plan.route ✗"


def test_ask_gives_next_step_not_wide_enumeration(mcp, kbs):
    """`ask` 只回策略清单 ⇒ 必须给**可执行的下一步**（spec=policy:/influencer:）✓"""
    r = mcp.call("knowledge_lookup", {"kb_id": kbs["domain"], "mode": "rules",
                                      "spec": "ask", "question": INFLUENCER + "要满足哪些规则？",
                                      "ontology_hint": True})
    assert (r.get("counts") or {}).get("rules") == 0, "ask 不应回规则 ✗"
    assert len(r.get("scope_options") or []) >= 6, "ask 应回策略清单 ✗"
    specs = [str((s.get("args") or {}).get("spec") or "") for s in (r.get("suggestions") or [])]
    assert any(x.startswith(("policy:", "influencer:")) for x in specs), \
        "ask 必须建议 policy:/influencer:（不能只说\"再选\"）✗ 实测=%r" % (specs,)


def test_missing_spec_gives_route_not_error(mcp, kbs):
    """把**问题**当 spec（历史踩坑）⇒ 不抛错，回可执行路线 ✓"""
    r = mcp.call("knowledge_lookup", {"kb_id": kbs["domain"], "mode": "rules",
                                      "spec": INFLUENCER + "需要哪些规则", "ontology_hint": True})
    assert r.get("ok") is True, r
    assert (r.get("plan") or {}).get("route") == "", "无 spec 时 route 应为空 ✗"
    assert r.get("suggestions"), "应给出可执行建议 ✗"


def test_plain_arm_keeps_results_hides_hints(mcp, kbs):
    """对照组只剥提示面：规则/链路照旧，plan/suggestions/next 不出现 ✓（A/B 完整性）"""
    r = mcp.call("knowledge_lookup", {"kb_id": kbs["domain"], "mode": "rules",
                                      "spec": "influencer:" + INFLUENCER, "ontology_hint": False})
    assert _slugs(r) == EXPECT and r.get("chain"), r
    for k in ("plan", "strategy", "suggestions", "next"):
        assert k not in r, "对照组不应回 %r ✗" % k


if __name__ == "__main__":            # 允许直接跑（不装 pytest 时）
    raise SystemExit(pytest.main([__file__, "-q"]))
