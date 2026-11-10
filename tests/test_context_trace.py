"""context_trace：只读解析器（不连 DB、不连 Docker）。"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools" / "diag"))
import context_trace as ct  # noqa: E402

SESSION = "be8f82bf-dc5f-4b81-a450-46ac3a3e3e2a"

LOG = "\n".join([
    "INFO [2026-11-10 14:08:11.664] [req1] qa.go:149[parseQARequest] | "
    "[AgentQA] Request: session_id=%s, request={}" % SESSION,
    "INFO [2026-11-10 14:08:12.410] [req1] engine.go:606[runReActIteration] | "
    "[Agent][Round-1/10] Starting: 2 messages, 49 tools, est_tokens=2690",
    "DEBUG[2026-11-10 14:08:12.438] [req1] context_debug.go:119 | "
    "[Agent][Round-1][ctx] predicted=2690 | request=22183",
    "DEBUG[2026-11-10 14:08:12.438] [req1] context_debug.go:123 | "
    "[Agent][Round-1][ctx] breakdown: msgs=2 total=22183 text=2679 reasoning=0 "
    "tool_results=0 tool_args=0 tool_schemas=19496 largest=user(1428)",
    "INFO [2026-11-10 14:08:15.243] [req1] usage.go:19[logUsage] | "
    "[LLM Usage] model=deepseek-flash, purpose=agent_round, prompt_tokens=20749, "
    "completion_tokens=255, total_tokens=21004, session_id=%s" % SESSION,
    "DEBUG[2026-11-10 14:08:15.249] [req1] context_debug.go:154 | "
    "[Agent][Round-1][ctx] drift: predicted=2690 actual_prompt=20749 "
    "diff=-18059 (-87.0%) completion=255",
    "INFO [2026-11-10 14:08:14.900] [req1] act.go:550[runToolCall] | "
    "[Agent][Round-1][Tool lookup (1/?)] Completed in 10ms: success=true, output=777 chars",
    "INFO [2026-11-10 14:08:17.507] [req1] usage.go:19[logUsage] | "
    "[LLM Usage] prompt_tokens=21194, completion_tokens=203, session_id=%s" % SESSION,
    "DEBUG[2026-11-10 14:08:17.511] [req1] context_debug.go:154 | "
    "[Agent][Round-2][ctx] drift: predicted=21251 actual_prompt=21194 "
    "diff=+57 (+0.3%) completion=203",
])


def test_parse_logs_handles_debug_without_space() -> None:
    req = ct.parse_logs(LOG)["req1"]
    assert req["session"] == SESSION
    assert req["rounds"][1]["actual_prompt"] == 20749
    assert req["rounds"][1]["completion"] == 255
    assert req["rounds"][1]["request"] == 22183
    assert req["rounds"][1]["tool_schemas"] == 19496
    assert req["rounds"][1]["tool_outputs"] == [{"name": "lookup", "output_chars": 777}]
    assert req["usage"][0] == {"prompt_tokens": 20749, "completion_tokens": 255}


def test_parse_steps_counts_args_and_results() -> None:
    raw = json.dumps([
        {"iteration": 0, "tool_calls": [{"name": "a", "args": {"x": 1}, "result": {"ok": True}}]},
        {"iteration": 1, "tool_calls": [{"name": "b", "args": {}, "result": "hello"}]},
    ], ensure_ascii=False)
    steps = ct.parse_steps(raw)
    assert steps[1]["tools"] == ["a"]
    assert steps[1]["args_chars"] > 0
    assert steps[1]["result_chars"] > 0
    assert steps[2]["tools"] == ["b"]


def test_trace_joins_steps_to_round_prompt(monkeypatch) -> None:
    steps = ct.parse_steps(json.dumps([
        {"iteration": 0, "tool_calls": [{"name": "lookup", "args": {"q": "x"},
                                           "result": {"ok": True}}]},
        {"iteration": 1, "tool_calls": [{"name": "read", "args": {},
                                           "result": {"text": "y"}}]},
    ]))
    monkeypatch.setattr(ct, "load_messages", lambda _session: [{
        "id": "m1", "ts": "2026-11-10 14:08:11.691143", "steps": steps,
        "usage": {"prompt_tokens": 41943, "completion_tokens": 458},
        "content_chars": 10,
    }])
    out = ct.trace(SESSION, logs=LOG)
    item = out["requests"][0]
    assert item["request_id"] == "req1"
    assert item["rounds"][0]["actual_prompt"] == 20749
    assert item["rounds"][0]["tools"] == ["lookup"]
    assert item["rounds"][0]["result_chars"] == 777
    assert item["rounds"][0]["tool_calls"][0]["result_source"] == "log"
    assert item["rounds"][1]["prompt_delta"] == 445
    md = ct.to_markdown(out)
    assert "Context Trace" in md and "lookup" in md
