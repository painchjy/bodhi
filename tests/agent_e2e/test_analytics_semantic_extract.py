"""标准取数场景 → 语义层提取 · E2E（MCP + 真实数据库）。

覆盖：
1. 任务页/提示词已注册新工具与任务；
2. 案例 1：删除某事实的部分指标/字段（含枚举），用场景 SQL 计划 + 确认后补全；
3. 案例 2：清空一个可由场景重建的事实，再用场景重建（fact/fact_table/binding/
   指标/字段/维度/场景关联）；
4. 现有 6 个标准场景的只读计划回归（维表在前不误判、没有 blocking）。

前置：
- `bodhi-mcp` 已加载新工具；
- 需要真实智能体测试时设 `BODHI_RUN_ANALYTICS_E2E=1`；
- 写库测试结束在 finally 中恢复快照并重新同步页面。
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
for path in (str(HERE), str(REPO / "tests"), str(REPO / "tools" / "ke-core")):
    if path not in sys.path:
        sys.path.insert(0, path)

import ke_analytics  # noqa: E402
import ke_db  # noqa: E402
from harness import WeKnoraClient, run_query, tool_calls  # noqa: E402
from mcp_client import MCP  # noqa: E402

E2E = os.environ.get("BODHI_RUN_ANALYTICS_E2E") == "1"
PLAN = "scenario_semantic_plan"
APPLY = "scenario_semantic_apply"
VALIDATE = "scenario_semantic_validate"

TABLES = ["fact", "fact_table", "base_metric", "derived_metric", "fact_field",
          "binding", "field_mapping", "enum_mapping", "fact_dimension",
          "scenario_fact"]


def _kb_id() -> str:
    value = os.environ.get("BODHI_ANALYTICS_KB_ID", "").strip()
    if value:
        return value
    rows = ke_db.psql_csv(
        "SELECT id FROM knowledge_bases WHERE deleted_at IS NULL "
        "AND name=%s ORDER BY updated_at DESC LIMIT 1"
        % ke_db.sql_str("bodhi运行监控分析"))
    if not rows:
        raise RuntimeError("analytics kb not found")
    return str(rows[0]["id"])


def _json(sql: str) -> str:
    return ke_db.psql(sql).strip() or "[]"


def snapshot_fact(fact_id: str) -> dict:
    where = "fact_id=%s" % ke_db.sql_str(fact_id)
    binding_where = (
        "binding_id IN (SELECT binding_id FROM analytics.binding "
        "WHERE object_type='fact' AND object_id=%s)" % ke_db.sql_str(fact_id))
    return {
        "fact": _json("SELECT coalesce(jsonb_agg(to_jsonb(t)),'[]') FROM analytics.fact t WHERE %s" % where),
        "fact_table": _json("SELECT coalesce(jsonb_agg(to_jsonb(t)),'[]') FROM analytics.fact_table t WHERE %s" % where),
        "base_metric": _json("SELECT coalesce(jsonb_agg(to_jsonb(t)),'[]') FROM analytics.base_metric t WHERE %s" % where),
        "derived_metric": _json("SELECT coalesce(jsonb_agg(to_jsonb(t)),'[]') FROM analytics.derived_metric t WHERE %s" % where),
        "fact_field": _json("SELECT coalesce(jsonb_agg(to_jsonb(t)),'[]') FROM analytics.fact_field t WHERE %s" % where),
        "binding": _json("SELECT coalesce(jsonb_agg(to_jsonb(t)),'[]') FROM analytics.binding t WHERE object_type='fact' AND object_id=%s" % ke_db.sql_str(fact_id)),
        "field_mapping": _json("SELECT coalesce(jsonb_agg(to_jsonb(t)),'[]') FROM analytics.field_mapping t WHERE %s" % binding_where),
        "enum_mapping": _json("SELECT coalesce(jsonb_agg(to_jsonb(t)),'[]') FROM analytics.enum_mapping t WHERE %s" % binding_where),
        "fact_dimension": _json("SELECT coalesce(jsonb_agg(to_jsonb(t)),'[]') FROM analytics.fact_dimension t WHERE %s" % where),
        "scenario_fact": _json("SELECT coalesce(jsonb_agg(to_jsonb(t)),'[]') FROM analytics.scenario_fact t WHERE %s" % where),
    }


def restore_fact(kb_id: str, fact_id: str, snap: dict) -> None:
    fact = ke_db.sql_str(fact_id)
    statements = [
        "BEGIN;",
        "DELETE FROM analytics.fact_dimension WHERE fact_id=%s;" % fact,
        "DELETE FROM analytics.scenario_fact WHERE fact_id=%s;" % fact,
        "DELETE FROM analytics.enum_mapping WHERE binding_id IN "
        "(SELECT binding_id FROM analytics.binding WHERE object_type='fact' "
        " AND object_id=%s);" % fact,
        "DELETE FROM analytics.field_mapping WHERE binding_id IN "
        "(SELECT binding_id FROM analytics.binding WHERE object_type='fact' "
        " AND object_id=%s);" % fact,
        "DELETE FROM analytics.binding WHERE object_type='fact' AND object_id=%s;" % fact,
        "DELETE FROM analytics.base_metric WHERE fact_id=%s;" % fact,
        "DELETE FROM analytics.derived_metric WHERE fact_id=%s;" % fact,
        "DELETE FROM analytics.fact_field WHERE fact_id=%s;" % fact,
        "DELETE FROM analytics.fact_table WHERE fact_id=%s;" % fact,
        "DELETE FROM analytics.fact WHERE fact_id=%s;" % fact,
    ]
    for table in TABLES:
        statements.append(
            "INSERT INTO analytics.%s SELECT * FROM jsonb_populate_recordset("
            "NULL::analytics.%s, %s::jsonb);"
            % (table, table, ke_db.sql_str(snap.get(table) or "[]")))
    statements.append("COMMIT;")
    ke_db.psql("\n".join(statements), stdin=True)
    ke_analytics.sync_pages(kb_id, kind="fact", ids=[fact_id], dry_run=False)


def _assert_snapshot_restored(kb_id: str, fact_id: str, snap: dict) -> None:
    now = snapshot_fact(fact_id)
    assert {k: len(json.loads(v)) for k, v in now.items()} == \
           {k: len(json.loads(v)) for k, v in snap.items()}


def _scenario_sql(scenario_id: str) -> str:
    rows = ke_db.psql_csv(
        "SELECT source_sql FROM analytics.scenario WHERE scenario_id=%s"
        % ke_db.sql_str(scenario_id))
    assert rows, "scenario missing: %s" % scenario_id
    return rows[0]["source_sql"] or ""


def _set_scenario_sql(scenario_id: str, sql: str) -> None:
    ke_db.psql(
        "UPDATE analytics.scenario SET source_sql=%s, updated_at=now() "
        "WHERE scenario_id=%s"
        % (ke_db.sql_str(sql), ke_db.sql_str(scenario_id)))



@pytest.mark.agent_e2e
@pytest.mark.skipif(not E2E, reason="set BODHI_RUN_ANALYTICS_E2E=1 to run live MCP tests")
def test_semantic_tools_and_task_registered():
    kb = _kb_id()
    names = set(MCP().tools())
    assert {PLAN, APPLY, VALIDATE} <= names, names

    rows = ke_db.psql_csv(
        "SELECT content, page_metadata::text AS meta FROM wiki_pages "
        "WHERE knowledge_base_id=%s AND deleted_at IS NULL AND slug=%s"
        % (ke_db.sql_str(kb), ke_db.sql_str("bpmn/task/从标准取数场景提取语义层")))
    assert rows, "task page missing"
    content = rows[0]["content"] or ""
    meta = rows[0]["meta"] or ""
    for text in (PLAN, APPLY, "列别名", "answers", "plan_id", "枚举"):
        assert text in content or text in meta, text

    agent = ke_db.psql_csv(
        "SELECT config->>'system_prompt' AS prompt FROM custom_agents "
        "WHERE deleted_at IS NULL AND name LIKE %s ORDER BY created_at DESC LIMIT 1"
        % ke_db.sql_str("%智能问数%"))
    assert agent, "analytics agent missing"
    prompt = agent[0].get("prompt") or ""
    assert PLAN in prompt and "从标准取数场景提取语义层" in prompt
    assert "任务页 `skos:note`" in prompt or "skos:note" in prompt


@pytest.mark.agent_e2e
@pytest.mark.skipif(not E2E, reason="set BODHI_RUN_ANALYTICS_E2E=1 to run live MCP tests")
def test_case1_supplement_missing_metrics_and_fields():
    kb = _kb_id()
    client = MCP()
    fact = "fact_agent_tool_call"
    scenario = "sc_tool_return_chars"
    snap = snapshot_fact(fact)
    try:
        ke_db.psql("""
BEGIN;
DELETE FROM analytics.enum_mapping WHERE semantic_id IN ('ff_tool_status','ff_tool_success');
DELETE FROM analytics.field_mapping WHERE semantic_id IN
 ('bm_tool_result_chars','bm_tool_success_count','bm_tool_error_count',
  'ff_tool_result_chars','ff_tool_success','ff_tool_status');
DELETE FROM analytics.base_metric WHERE metric_id IN
 ('bm_tool_result_chars','bm_tool_success_count','bm_tool_error_count');
DELETE FROM analytics.derived_metric WHERE metric_id='dm_tool_avg_result_chars';
DELETE FROM analytics.fact_field WHERE field_id IN
 ('ff_tool_result_chars','ff_tool_success','ff_tool_status');
COMMIT;
""", stdin=True)
        plan = client.call(PLAN, {"scenario_id": scenario, "fact_id": fact})
        assert plan.get("ok") is True, plan
        assert plan.get("ready_to_apply") is False
        assert plan.get("target_fact", {}).get("fact_id") == fact
        missing = {c["code"] for c in plan["missing"]["base_metrics"]}
        assert {"result_chars", "success_calls", "failed_calls"} <= missing, missing
        assert any(c["code"] == "avg_result_chars"
                   for c in plan["missing"]["derived_metrics"]), plan["missing"]
        assert any(q.get("kind") == "enum" and q.get("code") == "tool_success"
                   for q in plan["questions"]), plan["questions"]

        answers = {
            "labels": {
                "result_chars": "工具返回字符数",
                "success_calls": "工具成功次数",
                "failed_calls": "工具失败次数",
                "avg_result_chars": "工具平均返回字符数",
                "tool_success": "工具成功",
            },
            "enums": {"tool_success": {"true": "成功", "false": "失败"}},
        }
        plan2 = client.call(PLAN, {"scenario_id": scenario, "fact_id": fact,
                                   "answers": answers})
        assert plan2.get("ready_to_apply") is True, plan2.get("questions")
        plan_id = plan2["plan_id"]

        dry = client.call(APPLY, {"scenario_id": scenario, "kb_id": kb,
                                  "fact_id": fact, "answers": answers,
                                  "plan_id": plan_id, "confirm": True,
                                  "dry_run": True})
        assert dry.get("ok") is True and dry.get("applied") is False
        assert dry.get("summary", {}).get("base_metric") == 3

        out = client.call(APPLY, {"scenario_id": scenario, "kb_id": kb,
                                  "fact_id": fact, "answers": answers,
                                  "plan_id": plan_id, "confirm": True,
                                  "dry_run": False})
        assert out.get("ok") is True and out.get("applied") is True, out
        assert out["validation"]["mapping"]["status"] == "mapped"
        assert out["validation"]["scenario"]["ok"] is True
        assert out["validation"]["converged"] is True

        metrics = {r["code"] for r in ke_db.psql_csv(
            "SELECT code FROM analytics.base_metric WHERE fact_id=%s AND status='active'"
            % ke_db.sql_str(fact))}
        assert {"result_chars", "success_calls", "failed_calls",
                "tool_call_count"} <= metrics
        fields = {r["code"] for r in ke_db.psql_csv(
            "SELECT code FROM analytics.fact_field WHERE fact_id=%s AND status='active'"
            % ke_db.sql_str(fact))}
        assert "tool_success" in fields
        bad = ke_db.psql_csv(
            "SELECT m.semantic_id, m.source_table FROM analytics.field_mapping m "
            "JOIN analytics.binding b ON b.binding_id=m.binding_id "
            "WHERE b.object_type='fact' AND b.object_id=%s AND m.status='active' "
            "AND (m.source_schema, m.source_table) NOT IN "
            "(SELECT source_schema, source_table FROM analytics.fact_table "
            " WHERE fact_id=%s AND status='active')"
            % (ke_db.sql_str(fact), ke_db.sql_str(fact)))
        assert not bad, bad
        sources = {r["code"]: r["source_column"] for r in ke_db.psql_csv(
            "SELECT f.code, m.source_column FROM analytics.field_mapping m "
            "JOIN analytics.binding b ON b.binding_id=m.binding_id "
            "JOIN analytics.fact_field f ON f.field_id=m.semantic_id "
            "WHERE b.object_type='fact' AND b.object_id=%s AND m.status='active' "
            "AND m.semantic_kind='non_metric_field'"
            % ke_db.sql_str(fact))}
        assert sources.get("tool_success") == "tool_success", sources
    finally:
        restore_fact(kb, fact, snap)
        _assert_snapshot_restored(kb, fact, snap)


@pytest.mark.agent_e2e
@pytest.mark.skipif(not E2E, reason="set BODHI_RUN_ANALYTICS_E2E=1 to run live MCP tests")
def test_one_shot_semantics_from_sql_aliases():
    """SQL 列别名直接给中文名与布尔枚举：第一次 plan 不需要 label/enum 追问。"""
    client = MCP()
    scenario = "sc_tool_context"
    original = _scenario_sql(scenario)
    sql = """SELECT f.tool_code,
       f.tool_name AS "工具名称",
       f.tool_success AS "执行结果（true=成功，false=失败）",
       t.input_prompt
  FROM diag.agent_tool_call_fact f
  LEFT JOIN diag.agent_turn_fact t ON t.message_id = f.message_id
 ORDER BY f.tool_code, f.message_id"""
    try:
        _set_scenario_sql(scenario, sql)
        plan = client.call(PLAN, {"scenario_id": scenario})
        assert plan.get("ok") is True, plan
        asks = [q for q in plan.get("questions") or []
                if q.get("kind") in ("label", "enum", "fact_label")]
        assert not asks, asks

        names = {a["data"].get("name") for a in plan.get("actions") or []
                 if a.get("kind") == "fact_field"}
        assert {"工具名称", "执行结果"} <= names, names
        sources = {(a["data"].get("source_column"), a["data"].get("source_table"))
                   for a in plan.get("actions") or []
                   if a.get("kind") == "field_mapping"
                   and a["data"].get("semantic_kind") == "non_metric_field"}
        assert {("tool_name", "agent_tool_call_fact"),
                ("tool_success", "agent_tool_call_fact")} <= sources, sources
        enums = {a["data"].get("semantic_label") for a in plan.get("actions") or []
                 if a.get("kind") == "enum_mapping"}
        assert {"成功", "失败"} <= enums, enums
        physical = {a["data"].get("semantic_value"): a["data"].get("physical_value")
                    for a in plan.get("actions") or []
                    if a.get("kind") == "enum_mapping"}
        assert physical == {"true": "t", "false": "f"}, physical
        assert plan.get("ready_to_apply") is True, plan.get("questions")
    finally:
        _set_scenario_sql(scenario, original)
        assert _scenario_sql(scenario) == original


@pytest.mark.agent_e2e
@pytest.mark.skipif(not E2E, reason="set BODHI_RUN_ANALYTICS_E2E=1 to run live MCP tests")
def test_agent_session_semantic_extract_readonly_plan():
    """真实智能体会话：读任务页 → validate → 执行场景 SQL → plan（不 apply）。"""
    client = WeKnoraClient.from_env(agent_name="智能问数（智能体运行状态监控）")
    query = """任务：从标准取数场景提取语义层（**只读计划，不要写库、不要 apply**）。
场景：sc_tool_context；目标事实：fact_agent_tool_context；粒度：message_id。
请按任务页 `bpmn/task/从标准取数场景提取语义层` 的 skos:note 执行：
选任务并读任务页 → scenario_semantic_validate → 用场景绑定服务执行原 SQL 验证 →
scenario_semantic_plan(scenario_id="sc_tool_context", fact_id="fact_agent_tool_context")。
这次一次性给全语义，不要追问，也不要改现有事实：
- tool_name 中文名=工具名称；
- tool_success 是布尔枚举：true=成功，false=失败；
- input_prompt 中文名=输入提示词。
需要 answers 时请在**同一次** plan 调用里带上 labels/enums/fields 后重算。
最终只报告：调用的工具名、plan_id、questions、missing、ready_to_apply；不要 apply。"""
    run = run_query(client, query, title="semantic-extract-e2e")
    session_id = run["session_id"]
    rows = ke_db.psql_csv(
        "SELECT id FROM sessions WHERE id=%s AND deleted_at IS NULL"
        % ke_db.sql_str(session_id))
    assert rows, "session not persisted: %s" % session_id

    calls = tool_calls(run["messages"])
    names = [c["name"] for c in calls]
    assert "knowledge_lookup" in names, names
    assert "scenario_semantic_validate" in names, names
    assert "scenario_semantic_plan" in names, names
    assert "scenario_semantic_apply" not in names, names
    assert "fact_match" not in names, names
    plans = [c for c in calls if c["name"] == "scenario_semantic_plan"]
    assert any((c["args"].get("answers") or {}).get("enums") for c in plans), plans
    assert "ready_to_apply" in run["report"] or "plan_id" in run["report"]


@pytest.mark.agent_e2e
@pytest.mark.skipif(not E2E, reason="set BODHI_RUN_ANALYTICS_E2E=1 to run live MCP tests")
def test_case2_rebuild_fact_from_scenario():
    kb = _kb_id()
    client = MCP()
    fact = "fact_agent_turn"
    scenario = "sc_context_tokens"
    snap = snapshot_fact(fact)
    try:
        ke_db.psql("""
BEGIN;
DELETE FROM analytics.binding WHERE object_type='fact' AND object_id='fact_agent_turn';
DELETE FROM analytics.fact WHERE fact_id='fact_agent_turn';
COMMIT;
""", stdin=True)
        plan = client.call(PLAN, {"scenario_id": scenario})
        assert plan.get("ok") is True, plan
        assert plan.get("ready_to_apply") is False
        assert plan.get("target_fact") is None
        assert plan.get("target_source") == "create"
        assert any(q.get("kind") == "fact_label" for q in plan["questions"])
        assert any(q.get("kind") == "label" and q.get("code") == "turns"
                   for q in plan["questions"])

        answers = {
            "fact": {"name": "Agent轮次事实", "grain": "session_id + message_id"},
            "labels": {
                "turns": "轮次数",
                "prompt_tokens": "输入 Token",
                "completion_tokens": "输出 Token",
                "total_tokens": "总 Token",
                "cached_tokens": "缓存 Token",
            },
        }
        plan2 = client.call(PLAN, {"scenario_id": scenario, "answers": answers})
        assert plan2.get("ready_to_apply") is True, plan2.get("questions")
        kinds = [a["kind"] for a in plan2["actions"]]
        assert "fact_create" in kinds and "fact_table" in kinds and "binding" in kinds
        assert kinds.count("fact_dimension") >= 3
        plan_id = plan2["plan_id"]

        out = client.call(APPLY, {"scenario_id": scenario, "kb_id": kb,
                                  "answers": answers, "plan_id": plan_id,
                                  "confirm": True, "dry_run": False})
        assert out.get("ok") is True and out.get("applied") is True, out
        assert out["validation"]["mapping"]["status"] == "mapped"
        assert out["validation"]["converged"] is True

        fact_row = ke_db.psql_csv(
            "SELECT fact_id, code, name, grain FROM analytics.fact WHERE fact_id=%s"
            % ke_db.sql_str(fact))
        assert fact_row and fact_row[0]["name"] == "Agent轮次事实"
        tables = ke_db.psql_csv(
            "SELECT source_schema, source_table, is_primary FROM analytics.fact_table "
            "WHERE fact_id=%s AND status='active'" % ke_db.sql_str(fact))
        assert tables and tables[0]["source_table"] == "agent_turn_fact"
        assert tables[0]["is_primary"] in (True, "t", "true")
        metrics = {r["code"] for r in ke_db.psql_csv(
            "SELECT code FROM analytics.base_metric WHERE fact_id=%s AND status='active'"
            % ke_db.sql_str(fact))}
        assert {"turns", "prompt_tokens", "completion_tokens",
                "total_tokens", "cached_tokens"} <= metrics, metrics
        dims = {r["dimension_id"] for r in ke_db.psql_csv(
            "SELECT dimension_id FROM analytics.fact_dimension WHERE fact_id=%s "
            "AND status='active'" % ke_db.sql_str(fact))}
        assert {"dim_session", "dim_agent", "dim_model"} <= dims, dims
        linked = ke_db.psql_csv(
            "SELECT scenario_id FROM analytics.scenario_fact WHERE fact_id=%s "
            "AND status='active'" % ke_db.sql_str(fact))
        assert linked and linked[0]["scenario_id"] == scenario
    finally:
        restore_fact(kb, fact, snap)
        _assert_snapshot_restored(kb, fact, snap)


@pytest.mark.agent_e2e
@pytest.mark.skipif(not E2E, reason="set BODHI_RUN_ANALYTICS_E2E=1 to run live MCP tests")
def test_all_standard_scenarios_plan_without_blocking():
    client = MCP()
    expected = {
        "sc_context_tokens": (set(), set()),
        "sc_tool_return_chars": (set(), set()),
        "sc_tool_unused": (set(), set()),
        "sc_tool_usage": (set(), set()),
        "sc_tool_problem_usage": ({"used_tool_count"}, set()),
        "sc_tool_context": (set(), {"tool_name", "tool_success"}),
    }
    failures = {}
    for scenario, (expected_metrics, expected_fields) in expected.items():
        plan = client.call(PLAN, {"scenario_id": scenario})
        if not plan.get("ok") or plan.get("blocking"):
            failures[scenario] = {"ok": plan.get("ok"),
                                  "blocking": plan.get("blocking")}
            continue
        missing_metrics = {c["code"] for c in plan["missing"]["base_metrics"]}
        missing_fields = {c["code"] for c in plan["missing"]["fact_fields"]}
        if missing_metrics != expected_metrics:
            failures[scenario] = {"base_metrics": sorted(missing_metrics),
                                  "expected": sorted(expected_metrics)}
        if missing_fields != expected_fields:
            failures.setdefault(scenario, {}).update(
                {"fact_fields": sorted(missing_fields),
                 "expected_fields": sorted(expected_fields)})
        for table in plan["sql"]["tables"]:
            if table.get("role") == "unknown":
                failures.setdefault(scenario, {}).setdefault(
                    "unknown_tables", []).append(table.get("table_key"))
        if not plan["sql"]["tables"]:
            failures.setdefault(scenario, {}).setdefault("no_tables", True)
    assert not failures, failures
