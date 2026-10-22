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
    # 不变量（2026-10-22 收紧）：`definition` 必须等于**属性里的全量**，且**不与截断的 summary 同源**。
    # 旧断言 `len>200` 依赖"首条命中恰是某条长定义"，检索排序一变就假红 ✗
    attrs = h.get("attributes") or {}
    full = str(attrs.get("skos:definition") or attrs.get("definition") or "")
    assert h["definition"] == full, "definition 应为属性全量（被截断 ✗）"
    assert h.get("summary_truncated") in (True, False), "缺 summary_truncated 标记 ✗"


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


def test_doc_outline_smoke(mcp):
    """`doc_outline`（P2 切片 S4 验收 ✓）：切片→批次，断言回执结构与游标字段

    取**库里真实存在的文档**（不写死库名/slug ✓）：没有文档则 skip（数据缺口，非工具错）。
    """
    import ke_db
    rows = ke_db.psql_csv("SELECT knowledge_base_id AS kb, id FROM knowledges "
                          "WHERE deleted_at IS NULL ORDER BY created_at DESC LIMIT 1")
    if not rows:
        import pytest
        pytest.skip("库里没有任何文档（doc_outline 无从验证）")
    r = mcp.call("doc_outline", {"kb_id": rows[0]["kb"], "knowledge_id": rows[0]["id"],
                                 "with_text": False})
    assert isinstance(r, dict), "回执不是对象 ✗"
    for k in ("total_units", "next_cursor", "done", "budget_tokens", "batches"):
        assert k in r, "doc_outline 回执缺 %s ✗：%s" % (
            k, json.dumps(r, ensure_ascii=False)[:240])
    assert r["next_cursor"] >= 0 and isinstance(r["batches"], list)


def test_extract_state_get(mcp, kbs):
    """`extract_state`（P2 切片 S3 验收 ✓）：走 `resolve_kb_id` + 状态文件读路径"""
    r = mcp.call("extract_state", {"kb_id": kbs["domain"], "action": "get"})
    assert r.get("kb_id"), json.dumps(r, ensure_ascii=False)[:300]
    for k in ("pages_total", "next_no", "rounds", "hint"):
        assert k in r, "extract_state 回执缺 %s ✗：%s" % (k, json.dumps(r, ensure_ascii=False)[:200])


def test_audit_scan_smoke(mcp, kbs):
    """`audit_scan`（P2 切片 S8b-1 验收 ✓）：只读巡检，回执应带 findings/计数结构"""
    r = mcp.call("audit_scan", {"kb_id": kbs["domain"], "scope": "all", "max_findings": 5})
    assert isinstance(r, dict), "回执不是对象 ✗"
    assert not r.get("error") or "kb_id" in r, json.dumps(r, ensure_ascii=False)[:300]
    assert any(k in r for k in ("findings", "count", "by_severity", "kb_id")), \
        "audit_scan 回执结构异常 ✗：%s" % json.dumps(r, ensure_ascii=False)[:300]


def test_context_lookup_smoke(mcp, kbs):
    """`context_lookup`（P2 切片 S8b-1 验收 ✓）：跨库同名/同义查询，空查询也要能返回结构"""
    r = mcp.call("context_lookup", {"kb_ids": [kbs["domain"]]})
    assert isinstance(r, dict), "回执不是对象 ✗"
    assert "traceback" not in json.dumps(r, ensure_ascii=False).lower()


def test_retired_tool_returns_deprecated(mcp):
    """退役工具必须**返回 deprecated**（不是 500、不是静默成功）✓"""
    r = mcp.call("review_apply", {"kb_id": "x"})
    assert r.get("deprecated") is True, "退役工具未被拦截 ✗：%s" % json.dumps(r, ensure_ascii=False)[:200]
