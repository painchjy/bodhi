from __future__ import annotations
import json, os, re, sys
from pathlib import Path
import pytest
HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
for p in (str(HERE), str(REPO / "tests"), str(REPO / "tools" / "ke-core")): sys.path.insert(0, p)
import ke_analytics
import ke_db
from mcp_client import MCP
from test_analytics_semantic_extract import snapshot_fact, restore_fact, _kb_id
E2E = os.environ.get("BODHI_RUN_ANALYTICS_E2E") == "1"
PLAN = "scenario_semantic_plan"
APPLY = "scenario_semantic_apply"
GOLDEN = HERE / "golden"
CASES = [
    ("fact_agent_turn", "sc_semantic_agent_turn_full", "agent_turn_fact.json"),
    ("fact_agent_tool_context", "sc_semantic_tool_context_full",
     "agent_tool_context_fact.json"),
]


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


def _golden_snapshot(name: str) -> dict:
    data = json.loads((GOLDEN / name).read_text(encoding="utf-8"))
    return {k: json.dumps(v, ensure_ascii=False) for k, v in data.items()}


def _sem(snap, scenario: str = ""):
    """Golden 视图：只比语义身份/属性/来源，不比自动生成的内部 ID 和时间戳。"""
    d = {k: [r for r in json.loads(v) if r.get("status") == "active"]
         for k, v in snap.items()}
    fact = d.get("fact") or [{}]
    code_by_id = {r["metric_id"]: r["code"] for r in d["base_metric"]}
    code_by_id.update({r["field_id"]: r["code"] for r in d["fact_field"]})
    base = sorted((r["code"], r.get("name"), r.get("description") or "",
                   r.get("data_type"), r.get("unit"), r.get("aggregation"),
                   r.get("additivity"), r.get("filter_expression") or "",
                   r.get("format") or "", r.get("sort_order"))
                  for r in d["base_metric"])
    derived = sorted((r["code"], r.get("name"), r.get("description") or "",
                      re.sub(r"\s+", "", r.get("expression") or ""),
                      r.get("unit"), r.get("format") or "",
                      r.get("sort_order")) for r in d["derived_metric"])
    fields = sorted((r["code"], r.get("name"), r.get("data_type"), r.get("role"),
                     bool(r.get("is_enum")), r.get("description") or "",
                     r.get("sort_order")) for r in d["fact_field"])
    tables = sorted((r.get("source_schema"), r.get("source_table"),
                     bool(r.get("is_primary")),
                     tuple(r.get("grain_keys") or [])) for r in d["fact_table"])
    dims = sorted((r.get("dimension_id"), r.get("fact_key"), r.get("dimension_key"),
                   r.get("join_type")) for r in d["fact_dimension"])
    mappings = sorted((m.get("semantic_kind"), code_by_id.get(m.get("semantic_id"), ""),
                       m.get("source_schema"), m.get("source_table"),
                       m.get("source_column"))
                      for m in d["field_mapping"]
                      if code_by_id.get(m.get("semantic_id")))
    sf = sorted(r["scenario_id"] for r in d["scenario_fact"]
                if not scenario or r["scenario_id"] == scenario)
    return (fact[0].get("name"), fact[0].get("grain"), base, derived, fields,
            tables, dims, mappings, sf)


@pytest.mark.agent_e2e
@pytest.mark.skipif(not E2E, reason="set BODHI_RUN_ANALYTICS_E2E=1 to run live MCP tests")
def test_golden_roundtrip_without_caller_answers():
    """场景 semantic_json 自带完整 answers：删除语义后 plan/apply 不需要再追问。"""
    kb = _kb_id(); c = MCP()
    restores = []
    try:
        for fact, scenario, golden_name in CASES:
            snap = snapshot_fact(fact)
            restores.append((fact, scenario, snap))
            golden = _golden_snapshot(golden_name)
            assert _sem(golden, scenario) == _sem(snap, scenario), ("golden drift", fact)
            _delete_semantics(fact)
            plan = c.call(PLAN, {"scenario_id": scenario})
            assert plan.get("ok") and plan.get("ready_to_apply"), (
                fact, plan.get("grain"), plan.get("questions"), plan.get("blocking"))
            assert not plan.get("questions"), plan.get("questions")
            assert plan["grain"]["compatible"] and plan["grain"]["relation"] in (
                "exact", "fact_contains"), plan["grain"]
            out = c.call(APPLY, {"scenario_id": scenario, "kb_id": kb,
                                 "plan_id": plan["plan_id"], "confirm": True,
                                 "dry_run": False})
            assert out.get("ok") and out.get("applied"), out
            assert out["validation"]["converged"] is True, out.get("validation")
            after = _sem(snapshot_fact(fact), scenario)
            assert after == _sem(golden, scenario), (fact, after, _sem(golden, scenario))
    finally:
        for fact, scenario, snap in restores:
            restore_fact(kb, fact, snap, sync_pages=False)
        if restores:
            ke_analytics.sync_pages_affected(kb, {"fact": [x[0] for x in restores], "scenario": [x[1] for x in restores]}, dry_run=False)
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

