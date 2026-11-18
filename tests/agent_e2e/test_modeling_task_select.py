from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
PLAN = HERE / "cases" / "modeling_task_select_plan.json"
ORACLE = HERE / "cases" / "modeling_task_select_oracle.json"


def _load():
    plan = json.loads(PLAN.read_text(encoding="utf-8-sig"))
    oracle = json.loads(ORACLE.read_text(encoding="utf-8-sig"))
    return plan, oracle


def _task_page_calls(calls):
    out = []
    for call in calls:
        if call.get("name") != "knowledge_lookup":
            continue
        args = call.get("args") or {}
        if str(args.get("mode") or "").strip().lower() != "page":
            continue
        q = str(args.get("query") or args.get("slug") or "")
        if "bpmn/task/" in q:
            out.append(call)
    return out


def score_selection(calls, report, oracle):
    names = [c.get("name") for c in calls]
    domain_idx = next((i for i, c in enumerate(calls)
                      if c.get("name") == "knowledge_lookup"
                      and str((c.get("args") or {}).get("mode") or "").lower() == "domain"), -1)
    pages = _task_page_calls(calls)
    writes = [c for c in calls if c.get("name") == "save_knowledge"
              and str((c.get("args") or {}).get("mode") or "").lower() == "apply"]
    checks = {
        "domain_first": domain_idx >= 0 and (not pages or domain_idx < calls.index(pages[0])),
        "max_task_pages": len(pages) <= int(oracle.get("max_task_page_calls") or 1),
        "no_page_all_tasks": not oracle.get("forbid_task_page_all") or len(pages) <= 1,
        "expected_task": str(oracle.get("expected_task") or "") in str(report or ""),
        "expected_role": str(oracle.get("expected_role") or "") in str(report or ""),
        "no_applied_write": not oracle.get("forbid_applied_write") or not writes,
    }
    errors = [k for k, ok in checks.items() if not ok]
    return {"passed": not errors, "checks": checks, "errors": errors, "tool_seq": names, "task_pages": len(pages)}


def test_selection_scorer_rejects_page_all_tasks():
    oracle = {"expected_task": "规则化", "expected_role": "业务专家", "max_task_page_calls": 1,
              "forbid_task_page_all": True, "forbid_applied_write": True}
    calls = [{"name": "knowledge_lookup", "args": {"mode": "domain"}},
             {"name": "knowledge_lookup", "args": {"mode": "page", "query": "bpmn/task/意图建模"}},
             {"name": "knowledge_lookup", "args": {"mode": "page", "query": "bpmn/task/规则化"}}]
    result = score_selection(calls, "规则化 业务专家", oracle)
    assert result["passed"] is False
    assert "max_task_pages" in result["errors"]


@pytest.mark.agent_e2e
@pytest.mark.skipif(os.environ.get("BODHI_RUN_AGENT_E2E") != "1", reason="set BODHI_RUN_AGENT_E2E=1 to run live agent acceptance")
def test_live_modeling_task_select():
    from harness import WeKnoraClient, run_query, tool_calls
    plan, oracle_all = _load()
    case = (plan.get("cases") or [])[0]
    oracle = (oracle_all.get("cases") or {}).get(case["case_id"]) or {}
    try:
        client = WeKnoraClient.from_env(
            agent_name=os.environ.get("BODHI_MODELING_AGENT_NAME", "企业知识管理建模智能体"),
            agent_id_env="BODHI_MODELING_AGENT_ID",
            agent_name_env="BODHI_MODELING_AGENT_NAME")
    except Exception as exc:  # noqa: BLE001
        pytest.skip("modeling agent E2E not configured: " + str(exc)[:160])
    run = run_query(client, case["question"], title="agent-e2e-task-select")
    calls = tool_calls(run.get("messages") or [])
    result = score_selection(calls, run.get("report") or "", oracle)
    assert result["passed"], result
    assert len(_task_page_calls(calls)) <= 1
