"""analytics 取数 e2e：先 mapping 后 scenario，且只用 canonical mapping 对象 ID。

真实智能体调用默认跳过；设置 `BODHI_RUN_ANALYTICS_E2E=1` 后运行。
确定性用例只校验 agent_steps，不连模型。
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from harness import WeKnoraClient  # noqa: E402

MAPPING = "mapping_lookup"
SCENARIO = "scenario_lookup"


def _tool_name(name: str) -> str:
    name = str(name or "")
    for tool in (MAPPING, SCENARIO):
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
                        expected_dimensions: list) -> list:
    """返回空列表 = 符合：canonical ID、首次 mapping 即成功、mapping 全部在 scenario 前。"""
    calls = _calls(messages)
    mapping = [c for c in calls if c["name"] == MAPPING]
    scenario = [c for c in calls if c["name"] == SCENARIO]
    errors = []
    if not mapping:
        return ["no_mapping_lookup"]
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


def _msg(steps: list) -> list:
    return [{"role": "assistant", "agent_steps": [{"tool_calls": steps}]}]


def _call(name: str, args: dict, status: str = "mapped", ok: bool = True) -> dict:
    payload = json.dumps({"ok": ok, "status": status, "bindings": []})
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
        client.chat(sid, query)
        errors = mapping_flow_errors(client.messages(sid), fact, dims)
        if errors:
            failures.append({"query": query, "session_id": sid, "errors": errors})
    assert not failures, failures
