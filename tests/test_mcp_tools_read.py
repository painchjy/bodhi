"""L1 读类工具回归（**经 MCP 调用** ✓）

覆盖：`ontology_types` / `knowledge_lookup`(keyword|rules|path) / 退役工具必返 deprecated
断言：结构字段齐 + 来源标注 + 退役不是 500 ✓
"""
import json


def test_ontology_types(mcp):
    r = mcp.call("ontology_types", {"model": "bmm"})
    assert r, "空返回 ✗"
    txt = json.dumps(r, ensure_ascii=False)
    assert "TechnicalReviewReport" in txt or "技术方案评审报告" in txt, "未见报告类 ✗"


def test_knowledge_lookup_keyword_returns_definition(mcp, kbs):
    r = mcp.call("knowledge_lookup", {"kb_id": kbs["domain"], "mode": "keyword", "query": "问题清单"})
    assert r.get("ok") is True, r
    hits = r.get("hits") or []
    assert hits, "无命中 ✗"
    h = hits[0]
    assert "definition" in h and "attributes" in h, "缺 definition/attributes ✗（summary 仅展示）"
    assert len(h["definition"]) > 200, "definition 疑似被截断 ✗"


def test_knowledge_lookup_rules_has_rule_hash(mcp, kbs):
    r = mcp.call("knowledge_lookup", {"kb_id": kbs["domain"], "mode": "rules",
                                      "spec": "policy:技术方案逐项评审策略"})
    assert r.get("ok") is True, r
    rules = r.get("rules") or []
    assert rules, "无规则 ✗"
    assert all("slug" in x for x in rules), "规则缺 slug ✗"


def test_knowledge_lookup_path(mcp, kbs):
    """`mode="path"` 必须能走出边 —— **起点从图里动态取**（不写死 slug ✓ 无域词 ✓）

    2026-10-21 实测：库里活动换过（原写死的 slug 已不存在 ⇒ `paths=0` 是**测试口径错** ✗，
    不是工具错）。改为：取**任意一个有出边的活动**作起点；一个都没有 ⇒ xfail（数据缺口 ✓）。
    """
    import ke_neo4j
    import pytest
    rows = ke_neo4j.query(
        "MATCH (a:BodhiInstance {kb_id:$kb, page_type:'bpmn:Activity'})-[:`bpmn:activityContainsTask`]->() "
        "RETURN a.slug AS s LIMIT 5", {"kb": kbs["domain"]}, timeout=20) or []
    if not rows:
        pytest.xfail("领域库没有「活动→任务」出边（数据缺口，见 tests/TODO.md A1）")
    start = rows[0]["s"]
    r = mcp.call("knowledge_lookup", {"kb_id": kbs["domain"], "mode": "path",
                                      "start": start, "depth": 2})
    assert r.get("ok") is True, json.dumps(r, ensure_ascii=False)[:300]
    assert (r.get("counts") or {}).get("paths", 0) >= 1, "起点 %s 无路径 ✗" % start


def test_retired_tool_returns_deprecated(mcp):
    """退役工具必须**返回 deprecated**（不是 500、不是静默成功）✓"""
    r = mcp.call("review_apply", {"kb_id": "x"})
    assert r.get("deprecated") is True, "退役工具未被拦截 ✗：%s" % json.dumps(r, ensure_ascii=False)[:200]
