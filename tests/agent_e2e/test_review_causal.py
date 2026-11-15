from __future__ import annotations
import json
import os
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from harness import WeKnoraClient, load_cases, parse_scope, score_case


def test_plan_and_oracle_are_split():
    plan, oracle = load_cases()
    plan_text = json.dumps(plan, ensure_ascii=False)
    assert "expected_verdict" not in plan_text
    assert "forbidden_claims" not in plan_text
    assert set((oracle.get("cases") or {}).keys()) == {c["case_id"] for c in plan["cases"]}


def test_score_case_accepts_expected_answer():
    case = {"case_id": "CASE-CB-01"}
    oracle = {"expected_verdict": "符合", "expected_patterns": ["V-CB-01"], "forbidden_claims": ["熔断保证消息不丢失"], "required_terms": ["降级", "资源"]}
    run = {"report": "结论：符合。命中 V-CB-01；熔断快速失败，保护线程和资源，并返回降级结果。", "messages": []}
    assert score_case(case, oracle, run)["passed"] is True


def test_score_case_rejects_forbidden_claim():
    case = {"case_id": "CASE-CB-01"}
    oracle = {"expected_verdict": "符合", "expected_patterns": ["V-CB-01"], "forbidden_claims": ["熔断保证消息不丢失"], "required_terms": ["降级"]}
    run = {"report": "结论：符合。命中 V-CB-01；熔断保证消息不丢失。", "messages": []}
    result = score_case(case, oracle, run)
    assert result["passed"] is False
    assert "forbidden" in result["errors"]


def test_score_case_allows_denied_forbidden_claim():
    case = {"case_id": "CASE-CACHE-01"}
    oracle = {"expected_verdict": "符合", "expected_patterns": ["V-CA-01"],
              "forbidden_claims": ["缓存保证强一致"], "required_terms": ["失效"]}
    run = {"report": "结论：符合。命中 V-CA-01；写后失效；文档没有出现“缓存保证强一致”的主张。", "messages": []}
    assert score_case(case, oracle, run)["passed"] is True


def test_score_case_rejects_oracle_marker():
    case = {"case_id": "CASE-ORACLE"}
    oracle = {"expected_verdict": "符合", "expected_patterns": [], "forbidden_claims": [], "oracle_markers": ["HIDDEN-ORACLE"], "required_terms": []}
    run = {"report": "HIDDEN-ORACLE", "messages": []}
    result = score_case(case, oracle, run)
    assert result["passed"] is False
    assert "oracle_leak" in result["errors"]


def test_score_case_causal_scope_does_not_use_overall_verdict():
    case = {"case_id": "CASE-SCOPE"}
    oracle = {"expected_verdict": "符合", "verdict_scope": "causal", "expected_patterns": ["V-CB-01"],
              "forbidden_claims": [], "required_terms": ["降级"]}
    run = {"report": "## 1. 因果链专项\n结论：符合，命中 V-CB-01，有降级。\n\n## 2. 总体结论\n总体结论：不拟通过（待整改）。",
           "messages": []}
    assert score_case(case, oracle, run)["passed"] is True

    run["report"] = "## 1. 因果链专项\n未形成明确结论。\n\n## 2. 总体结论\n总体结论：符合。"
    assert score_case(case, oracle, run)["passed"] is False

    run["report"] = "## 1. 因果链专项\n不能判符合，缺前提。"
    assert score_case(case, oracle, run)["passed"] is False


@pytest.mark.agent_e2e
@pytest.mark.skipif(os.environ.get("BODHI_RUN_AGENT_E2E") != "1", reason="set BODHI_RUN_AGENT_E2E=1 to run live agent acceptance")
def test_live_review_cases():
    plan, oracle = load_cases()
    only = os.environ.get("BODHI_AGENT_E2E_CASE", "").strip()
    cases = [c for c in plan["cases"] if not only or c["case_id"] == only]
    try:
        client = WeKnoraClient.from_env()
    except Exception as exc:
        pytest.skip("agent E2E not configured: " + str(exc)[:160])
    ran = 0
    skipped = []
    failures = []
    for case in cases:
        raw = str(case.get("input_doc_id") or "")
        if raw.startswith("env:") and not os.environ.get(raw[4:]) and not case.get("source_file"):
            skipped.append(case.get("case_id"))
            continue
        run = client.run_case(case)
        result = score_case(case, oracle["cases"][case["case_id"]], run)
        ran += 1
        print(json.dumps({"case_id": case.get("case_id"), "session_id": run.get("session_id"), "result": result}, ensure_ascii=False))
        if not result["passed"]:
            failures.append(result)
    if not ran:
        pytest.skip("no configured case documents: " + str(skipped))
    assert not failures, failures



def test_scope_from_agent_prompt():
    prompt = "【库范围】domain_kb: 测试 | work_kb: 技术方案评审任务知识库（测试）"
    assert parse_scope(prompt) == ["测试", "技术方案评审任务知识库（测试）"]

