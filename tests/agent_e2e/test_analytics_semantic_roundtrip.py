from __future__ import annotations
import json, os, re, sys
from pathlib import Path
import pytest
HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
for p in (str(HERE), str(REPO / "tests"), str(REPO / "tools" / "ke-core")): sys.path.insert(0, p)
import ke_db
from mcp_client import MCP
from test_analytics_semantic_extract import snapshot_fact, restore_fact, _kb_id
E2E = os.environ.get("BODHI_RUN_ANALYTICS_E2E") == "1"
PLAN = "scenario_semantic_plan"
APPLY = "scenario_semantic_apply"
def _delete_semantics(fact):
    q = ke_db.sql_str(fact); obj = ke_db.sql_str("fact")
    sql = """BEGIN;
DELETE FROM analytics.enum_mapping WHERE binding_id IN (SELECT binding_id FROM analytics.binding WHERE object_type=%s AND object_id=%s);
DELETE FROM analytics.field_mapping WHERE binding_id IN (SELECT binding_id FROM analytics.binding WHERE object_type=%s AND object_id=%s);
DELETE FROM analytics.base_metric WHERE fact_id=%s;
DELETE FROM analytics.derived_metric WHERE fact_id=%s;
DELETE FROM analytics.fact_field WHERE fact_id=%s;
DELETE FROM analytics.fact_dimension WHERE fact_id=%s;
DELETE FROM analytics.scenario_fact WHERE fact_id=%s;
COMMIT;""" % (obj, q, obj, q, q, q, q, q, q)
    ke_db.psql(sql, stdin=True)
def _sem(snap):
    d = {k: [r for r in json.loads(v) if r.get("status") == "active"] for k, v in snap.items()}
    base = sorted((r["code"], r.get("name"), r.get("aggregation")) for r in d["base_metric"])
    derived = sorted((r["code"], r.get("name"), re.sub(r"\s+", "", r.get("expression") or "")) for r in d["derived_metric"])
    fields = sorted((r["code"], r.get("name")) for r in d["fact_field"])
    dims = sorted((r["dimension_id"], r.get("fact_key"), r.get("dimension_key"), r.get("join_type")) for r in d["fact_dimension"])
    sf = sorted((r["scenario_id"], r["fact_id"]) for r in d["scenario_fact"] if r["scenario_id"].startswith("sc_semantic_"))
    return base, derived, fields, dims, sf, sorted((m.get("semantic_kind"), m.get("source_table"), m.get("source_column")) for m in d["field_mapping"])
L1 = {"session_id":"会话标识","turn_id":"轮次标识","agent_name":"Agent 名称","model_name":"模型名称","event_time":"事件时间","total_tokens":"总 Token","cached_tokens":"缓存 Token","input_prompt":"输入提示词","turn_count":"轮次数","input_tokens":"输入 Token","output_tokens":"输出 Token","latency_ms":"轮次耗时","avg_tokens_per_turn":"轮次平均 Token"}
M1 = {"total_tokens_sum":{"code":"total_tokens"},"cached_tokens_sum":{"code":"cached_tokens"}}
L2 = {"tool_code":"工具编码","input_prompt":"输入提示词","tool_call_count":"工具调用次数","tool_error_count":"工具失败次数"}
CASES = [("fact_agent_turn", "sc_semantic_agent_turn_full", L1, M1), ("fact_agent_tool_context", "sc_semantic_tool_context_full", L2, {})]
@pytest.mark.agent_e2e
@pytest.mark.skipif(not E2E, reason="set BODHI_RUN_ANALYTICS_E2E=1 to run live MCP tests")
def test_semantic_roundtrip_samples():
    kb = _kb_id(); c = MCP()
    for fact, scenario, labels, metrics in CASES:
        snap = snapshot_fact(fact)
        try:
            _delete_semantics(fact)
            answers = {"labels": labels, "metrics": metrics}
            plan = c.call(PLAN, {"scenario_id": scenario, "answers": answers})
            assert plan.get("ok") and plan.get("ready_to_apply"), (fact, plan.get("grain"), plan.get("questions"), plan.get("blocking"))
            assert plan["grain"]["compatible"] and plan["grain"]["relation"] in ("exact", "fact_contains"), plan["grain"]
            out = c.call(APPLY, {"scenario_id": scenario, "kb_id": kb, "answers": answers, "plan_id": plan["plan_id"], "confirm": True, "dry_run": False})
            assert out.get("ok") and out.get("applied"), out
            assert out["validation"]["converged"] is True, out.get("validation")
            before, after = _sem(snap), _sem(snapshot_fact(fact))
            for i, key in enumerate(("base", "derived", "fields", "dims", "scenario_fact", "mapping")):
                assert before[i] == after[i], (fact, key, before[i], after[i])
        finally:
            restore_fact(kb, fact, snap)
@pytest.mark.agent_e2e
@pytest.mark.skipif(not E2E, reason="set BODHI_RUN_ANALYTICS_E2E=1 to run live MCP tests")
def test_new_fact_requires_confirmation():
    c = MCP(); labels = {"session_id":"会话标识","message_id":"消息标识","agent_id":"Agent 标识","turn_count":"轮次数","input_tokens":"输入 Token"}
    p = c.call(PLAN, {"scenario_id": "sc_semantic_agent_turn_by_agent", "answers": {"labels": labels}})
    assert any(b.get("kind") == "scenario_grain_mismatch" for b in p.get("blocking") or []), p
    assert any(q.get("kind") == "fact_creation_required" for q in p.get("questions") or []), p
    answers = {"labels": labels, "fact": {"create": True, "fact_id": "fact_agent_turn_by_agent", "name": "Agent轮次Agent事实", "code": "agent_turn_by_agent", "grain": "session_id + message_id + agent_id"}}
    p2 = c.call(PLAN, {"scenario_id": "sc_semantic_agent_turn_by_agent", "answers": answers})
    assert any(a.get("kind") == "fact_create" and a.get("id") == "fact_agent_turn_by_agent" for a in p2.get("actions") or []), p2.get("actions")
    tables = [a.get("data", {}).get("grain_keys") for a in p2.get("actions") or [] if a.get("kind") == "fact_table"]
    assert tables == [["session_id", "message_id", "agent_id"]], tables

