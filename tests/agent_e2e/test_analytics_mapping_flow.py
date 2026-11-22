"""analytics 取数 e2e：先 mapping 后 scenario，且只用 canonical mapping 对象 ID。

真实智能体调用默认跳过；设置 `BODHI_RUN_ANALYTICS_E2E=1` 后运行。
确定性用例只校验 agent_steps，不连模型。
"""
from __future__ import annotations

import json
import os
import sys
import threading
import time
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from harness import WeKnoraClient  # noqa: E402

FACT_MATCH = "fact_match"
MAPPING = "mapping_lookup"
SCENARIO = "scenario_lookup"


def _tool_name(name: str) -> str:
    name = str(name or "")
    for tool in (FACT_MATCH, MAPPING, SCENARIO):
        if name == tool or name.endswith("_" + tool):
            return tool
    return name


def _calls(messages: list) -> list:
    out = []
    for msg in messages or []:
        for step in msg.get("agent_steps") or []:
            for call in step.get("tool_calls") or []:
                out.append({"idx": len(out), "name": _tool_name(call.get("name")),
                            "args": call.get("args") or {},
                            "result": call.get("result") or {},
                            "output": call.get("output") or ""})
    return out


def _result_json(call: dict) -> dict:
    result = call.get("result") or {}
    data = result.get("data") or {}
    for item in data.get("content_items") or []:
        if item.get("type") != "text":
            continue
        try:
            return json.loads(item.get("text") or "{}")
        except Exception:  # noqa: BLE001
            pass
    text = str(call.get("output") or "")
    for marker in ("\n{", "{"):
        pos = text.find(marker)
        if pos < 0:
            continue
        try:
            return json.loads(text[pos + (1 if marker == "\n{" else 0):])
        except Exception:  # noqa: BLE001
            pass
    return {}


def mapping_flow_errors(messages: list, expected_fact: str,
                        expected_dimensions: list,
                        require_fact_match: bool = False) -> list:
    """返回空列表 = 符合：canonical ID、首次 mapping 即成功、mapping 全部在 scenario 前。"""
    calls = _calls(messages)
    fact_match = [c for c in calls if c["name"] == FACT_MATCH]
    mapping = [c for c in calls if c["name"] == MAPPING]
    scenario = [c for c in calls if c["name"] == SCENARIO]
    errors = []
    if require_fact_match and not fact_match:
        errors.append("no_fact_match")
    for call in fact_match:
        args = call["args"]
        if not any(args.get(k) for k in ("metrics", "fact_tables", "filters",
                                         "group_by", "classification_fields", "intent")):
            errors.append("fact_match_missing_extraction")
        if any(k in args for k in ("object_type", "object_id", "fact_id", "dimension_id")):
            errors.append("legacy_fact_match_arg")
    if fact_match and mapping and fact_match[0]["idx"] > mapping[0]["idx"]:
        errors.append("fact_match_after_mapping")
    if not mapping:
        return errors + ["no_mapping_lookup"]
    seen = set()
    for call in mapping:
        args = call["args"]
        oid = str(args.get("mapping_object_id") or args.get("object_id") or
                  args.get("fact_id") or args.get("dimension_id") or "")
        typ = str(args.get("mapping_object_type") or args.get("object_type") or "")
        if any(k in args for k in ("object_type", "object_id", "fact_id",
                                   "dimension_id", "service_id", "canonical_id")):
            errors.append("legacy_arg:" + oid)
        if not oid:
            errors.append("mapping_object_id_missing")
            continue
        if not oid.startswith(("fact_", "dim_")) or oid.startswith("analytics/"):
            errors.append("noncanonical_mapping_object_id:" + oid)
        if typ == "fact" and not oid.startswith("fact_"):
            errors.append("type_id_mismatch:" + oid)
        if typ == "dimension" and not oid.startswith("dim_"):
            errors.append("type_id_mismatch:" + oid)
        seen.add(oid)
        out = _result_json(call)
        status = out.get("status")
        if out.get("ok") is False or status in ("missing", "invalid_argument"):
            errors.append("mapping_failed:" + oid + ":" + str(status))
        if not out:
            errors.append("mapping_result_unreadable:" + oid)
    if expected_fact not in seen:
        errors.append("fact_not_mapped:" + expected_fact)
    for did in expected_dimensions:
        if did not in seen:
            errors.append("dimension_not_mapped:" + did)
    for call in scenario:
        if any(k in call["args"] for k in ("fact_id", "service_id")):
            errors.append("legacy_scenario_arg")
    if scenario:
        first_mapping = mapping[0]["idx"]
        first_scenario = scenario[0]["idx"]
        if first_scenario < first_mapping:
            errors.append("scenario_before_mapping")
        if any(c["idx"] > first_scenario for c in mapping):
            errors.append("mapping_after_scenario")
    return errors


def multi_fact_errors(messages: list, expected_fact: str = "fact_agent_tool_context") -> list:
    """校验：选择多事实表事实、mapping 返回两张事实表、scenario_join 仅事实表。"""
    calls = _calls(messages)
    mapping = [c for c in calls if c["name"] == MAPPING]
    scenario = [c for c in calls if c["name"] == SCENARIO]
    errors = []
    target = None
    for call in mapping:
        out = _result_json(call)
        if str(call["args"].get("mapping_object_id") or "") == expected_fact:
            target = out
            break
    if target is None:
        return ["multi_fact_not_mapped:" + expected_fact]
    if target.get("ok") is False or target.get("status") != "mapped":
        errors.append("multi_fact_mapping_failed:" + str(target.get("status")))
    tables = target.get("fact_tables") or []
    if len(tables) < 2:
        errors.append("multi_fact_tables_lt_2")
    table_keys = {(str(t.get("source_schema") or ""), str(t.get("source_table") or ""))
                  for t in tables}
    target_scenario = None
    for call in scenario:
        out = _result_json(call)
        candidates = out.get("candidates") or ([out.get("scenario")] if out.get("scenario") else [])
        for cand in candidates:
            if str(cand.get("scenario_id") or "") == "sc_tool_context":
                target_scenario = cand
                break
        if target_scenario:
            break
    if target_scenario is None:
        errors.append("multi_fact_scenario_missing")
    else:
        joins = target_scenario.get("joins") or []
        if not joins:
            errors.append("multi_fact_join_missing")
        for join in joins:
            left = (str(join.get("left_schema") or ""), str(join.get("left_table") or ""))
            right = (str(join.get("right_schema") or ""), str(join.get("right_table") or ""))
            if left not in table_keys or right not in table_keys:
                errors.append("multi_fact_join_not_fact_table")
    return errors


def _msg(steps: list) -> list:
    return [{"role": "assistant", "agent_steps": [{"tool_calls": steps}]}]


def _call(name: str, args: dict, status: str = "mapped", ok: bool = True,
          extra: dict | None = None) -> dict:
    body = {"ok": ok, "status": status, "bindings": []}
    body.update(extra or {})
    payload = json.dumps(body, ensure_ascii=False)
    return {"name": name, "args": args,
            "result": {"data": {"content_items": [{"type": "text", "text": payload}]}},
            "output": payload}


def test_mapping_flow_accepts_canonical_order():
    messages = _msg([
        _call(MAPPING, {"mapping_object_type": "fact",
                        "mapping_object_id": "fact_agent_tool_call",
                        "mapping_service_id": "svc_postgresql"}),
        _call(MAPPING, {"mapping_object_type": "dimension",
                        "mapping_object_id": "dim_time",
                        "mapping_service_id": "svc_postgresql"}, status="formula_only"),
        _call(SCENARIO, {"mapping_object_id": "fact_agent_tool_call",
                         "mapping_service_id": "svc_postgresql", "intent": "x"}),
    ])
    assert mapping_flow_errors(messages, "fact_agent_tool_call", ["dim_time"]) == []


def test_fact_match_extracts_all_categories_before_mapping():
    messages = _msg([
        _call(FACT_MATCH, {
            "metrics": ["工具耗时", "调用次数"],
            "fact_tables": ["agent_tool_call_fact"],
            "filters": ["失败", "最近3天"],
            "group_by": ["Agent", "小时"],
            "classification_fields": ["工具分类", "工具小类"],
            "intent": "工具使用分析",
        }, status="confident"),
        _call(MAPPING, {"mapping_object_type": "fact",
                        "mapping_object_id": "fact_agent_tool_call",
                        "mapping_service_id": "svc_postgresql"}),
        _call(SCENARIO, {"mapping_object_id": "fact_agent_tool_call",
                         "mapping_service_id": "svc_postgresql", "intent": "x"}),
    ])
    assert mapping_flow_errors(messages, "fact_agent_tool_call", [],
                               require_fact_match=True) == []


def test_multi_fact_flow_accepts_two_fact_tables_and_fact_join():
    messages = _msg([
        _call(MAPPING, {"mapping_object_type": "fact",
                        "mapping_object_id": "fact_agent_tool_context",
                        "mapping_service_id": "svc_postgresql"},
              extra={"fact_tables": [
                  {"source_schema": "diag", "source_table": "agent_tool_call_fact"},
                  {"source_schema": "diag", "source_table": "agent_turn_fact"},
              ]}),
        _call(SCENARIO, {"mapping_object_id": "fact_agent_tool_context",
                         "mapping_service_id": "svc_postgresql"},
              extra={"candidates": [{
                  "scenario_id": "sc_tool_context",
                  "joins": [{"left_schema": "diag", "left_table": "agent_tool_call_fact",
                             "right_schema": "diag", "right_table": "agent_turn_fact"}],
              }]}),
    ])
    assert multi_fact_errors(messages) == []


def test_multi_fact_flow_rejects_dimension_join():
    messages = _msg([
        _call(MAPPING, {"mapping_object_type": "fact",
                        "mapping_object_id": "fact_agent_tool_context"},
              extra={"fact_tables": [{"source_schema": "diag",
                                      "source_table": "agent_tool_call_fact"}]}),
        _call(SCENARIO, {"mapping_object_id": "fact_agent_tool_context"},
              extra={"candidates": [{
                  "scenario_id": "sc_tool_context",
                  "joins": [{"left_schema": "diag", "left_table": "agent_tool_call_fact",
                             "right_schema": "diag", "right_table": "tool_dimension"}],
              }]}),
    ])
    errors = multi_fact_errors(messages)
    assert "multi_fact_tables_lt_2" in errors
    assert "multi_fact_join_not_fact_table" in errors


def test_fact_match_rejects_missing_extraction_and_reverse_order():
    missing = _msg([
        _call(FACT_MATCH, {"intent": ""}, status="ambiguous"),
        _call(MAPPING, {"mapping_object_type": "fact",
                        "mapping_object_id": "fact_agent_tool_call"}),
    ])
    errors = mapping_flow_errors(missing, "fact_agent_tool_call", [])
    assert "fact_match_missing_extraction" in errors

    reverse = _msg([
        _call(MAPPING, {"mapping_object_type": "fact",
                        "mapping_object_id": "fact_agent_tool_call"}),
        _call(FACT_MATCH, {"metrics": ["耗时"]}, status="confident"),
    ])
    errors = mapping_flow_errors(reverse, "fact_agent_tool_call", [])
    assert "fact_match_after_mapping" in errors


def test_mapping_flow_rejects_slug_and_reverse_order():
    messages = _msg([
        _call(SCENARIO, {"mapping_object_id": "fact_agent_tool_call"}),
        _call(MAPPING, {"object_type": "fact",
                        "object_id": "analytics/fact/agent_tool_call"}),
    ])
    errors = mapping_flow_errors(messages, "fact_agent_tool_call", ["dim_time"])
    assert "noncanonical_mapping_object_id:analytics/fact/agent_tool_call" in errors
    assert "scenario_before_mapping" in errors
    assert "fact_not_mapped:fact_agent_tool_call" in errors


def _chat_and_wait(client, session_id: str, query: str, timeout: float = 240.0) -> None:
    """SSE 可能长时间不关闭；以 DB 中最终 assistant 消息为准，避免 e2e 卡死。"""
    errors = []

    def _run():
        try:
            client.chat(session_id, query)
        except Exception as exc:  # noqa: BLE001
            errors.append(str(exc)[:300])

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()
    deadline = time.time() + timeout
    while time.time() < deadline:
        msgs = client.messages(session_id)
        assistant = [m for m in msgs if m.get("role") == "assistant"
                     and (m.get("content") or "").strip()]
        if assistant:
            time.sleep(3)  # 等最后一轮工具调用/消息落库稳定
            return
        if errors:
            raise RuntimeError(errors[0])
        time.sleep(3)
    raise TimeoutError("analytics agent did not produce an assistant message")


@pytest.mark.agent_e2e
@pytest.mark.skipif(os.environ.get("BODHI_RUN_ANALYTICS_E2E") != "1",
                    reason="set BODHI_RUN_ANALYTICS_E2E=1 to run live analytics agent")
def test_live_analytics_mapping_order():
    client = WeKnoraClient.from_env(
        agent_name="智能问数（智能体运行状态监控）",
        agent_id_env="BODHI_ANALYTICS_AGENT_ID",
        agent_name_env="BODHI_ANALYTICS_AGENT_NAME")
    cases = [
        ("最近3天，所有会话的工具访问平均耗时和次数，按用途分类、工具名称统计",
         "fact_agent_tool_call", ["dim_tool_category_1level"]),
        ("最近3天，所有会话的工具访问平均耗时和次数，按问题分类、工具小类、工具名称、小时组合统计",
         "fact_agent_tool_call", ["dim_tool_category_2level", "dim_time"]),
        ("最近3天，所有会话的轮次 token 使用量，按会话、小时统计",
         "fact_agent_turn", ["dim_session", "dim_time"]),
    ]
    failures = []
    for query, fact, dims in cases:
        sid = client.create_session("analytics-e2e")
        _chat_and_wait(client, sid, query)
        errors = mapping_flow_errors(client.messages(sid), fact, dims,
                                     require_fact_match=True)
        if errors:
            failures.append({"query": query, "session_id": sid, "errors": errors})
    assert not failures, failures


@pytest.mark.agent_e2e
@pytest.mark.skipif(os.environ.get("BODHI_RUN_ANALYTICS_E2E") != "1",
                    reason="set BODHI_RUN_ANALYTICS_E2E=1 to run live analytics agent")
def test_live_analytics_multi_fact_context():
    client = WeKnoraClient.from_env(
        agent_name="智能问数（智能体运行状态监控）",
        agent_id_env="BODHI_ANALYTICS_AGENT_ID",
        agent_name_env="BODHI_ANALYTICS_AGENT_NAME")
    sid = client.create_session("analytics-multi-fact-e2e")
    _chat_and_wait(
        client, sid,
        "最近3天，查看每次工具调用对应的用户输入提示词、工具名称和工具耗时，"
        "按工具名称统计；需要同时使用工具调用事实和轮次上下文。")
    errors = multi_fact_errors(client.messages(sid))
    assert not errors, {"session_id": sid, "errors": errors}
