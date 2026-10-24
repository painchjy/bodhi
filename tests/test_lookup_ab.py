"""需求 2 回归：**本体提示开关（A/B）与遥测数据完整性**

口径（2026-10-24 用户裁定 · 配对设计）：
- `ontology_hint=true`  ⇒ 回执带 `plan/strategy/suggestions/next`（提示用本体规划查询）
- `ontology_hint=false` ⇒ **只回** `hits/counts/kb/query`（对照组基线，不猜）
- 两组都回 `telemetry`（variant/session_no/question/hits/ms），并落 `logs/lookup_ab_*.jsonl`

本测试**只保证"开关真的改变回执 + 数据成对齐全 + 可复算"**，
**不对"哪组更好"下结论**（结论要靠真实 LLM 端到端跑，人工评估）✓
"""
import json
import os
import sys
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ab_runner  # noqa: E402

pytestmark = pytest.mark.slow

HINT_KEYS = ("plan", "strategy", "suggestions", "next", "cross_kb_hint")


def test_hint_default_from_env(monkeypatch):
    """开关默认值：显式布尔 > env（默认 on）——纯逻辑，不依赖服务。"""
    sys.path.insert(0, os.path.join(ab_runner.REPO, "tools", "ke-core"))
    import ke_lookup  # noqa: PLC0415

    monkeypatch.delenv("BODHI_ONTOLOGY_HINT", raising=False)
    assert ke_lookup._hint_enabled(None) is True, "未配置 env 时应默认开启 ✓"
    for off in ("0", "false", "off", "no"):
        monkeypatch.setenv("BODHI_ONTOLOGY_HINT", off)
        assert ke_lookup._hint_enabled(None) is False, "env=%s 应关闭 ✗" % off
    monkeypatch.setenv("BODHI_ONTOLOGY_HINT", "on")
    assert ke_lookup._hint_enabled(False) is False, "显式 False 必须优先于 env ✗"
    assert ke_lookup._hint_enabled(True) is True


def test_switch_changes_receipt(mcp, kbs, session_no):
    """同一查询、两种策略：**回执结构必须不同**（这是 A/B 的自变量）。"""
    args = {"kb_id": kbs["domain"], "mode": "keyword", "query": "流程",
            "session_no": session_no, "question": "流程有哪些？"}
    on = mcp.call("knowledge_lookup", dict(args, ontology_hint=True))
    off = mcp.call("knowledge_lookup", dict(args, ontology_hint=False))

    assert all(k in on for k in ("plan", "strategy", "suggestions")), \
        "hint=true 应带 plan/strategy/suggestions（实测 keys=%s）" % sorted(on)
    for k in HINT_KEYS:
        assert k not in off, "hint=false 不得回 %r（否则对照不成立 ✗）keys=%s" % (k, sorted(off))
    for k in ("hits", "counts", "kb", "query", "ok"):
        assert k in off, "hint=false 仍须回原始命中字段 %r ✗" % k

    assert (on.get("telemetry") or {}).get("variant") == "hint"
    assert (off.get("telemetry") or {}).get("variant") == "plain"
    tel = off["telemetry"]
    assert tel.get("session_no") == session_no, "遥测必须带上会话编号 ✗"
    assert tel.get("question") == "流程有哪些？", "遥测必须带原始问题 ✗"
    assert tel.get("question_key") and "？" not in tel["question_key"], \
        "问题归一化键应去掉全角问号 ✗：%r" % tel.get("question_key")
    # **同一次查询两组命中应一致**（开关只影响"提示面"，不影响检索本身 ✓）
    assert tel.get("hits") == (on.get("telemetry") or {}).get("hits"), \
        "开关不应改变命中数 ✗（%r vs %r）" % (tel.get("hits"),
                                            (on.get("telemetry") or {}).get("hits"))


def test_paired_dataset_complete(mcp, kbs, session_no):
    """配对数据完整性：两组样本数相等、逐题可比、报告与侧车日志落盘 ✓

    只跑前 6 题（够验证口径；全量 14 题在 `python3 tests/ab_runner.py` 里人工跑）。
    """
    qs = ab_runner.load_questions()
    assert len(qs) >= 12, "问题集太少，A/B 无统计意义 ✗（当前 %d）" % len(qs)
    rep = ab_runner.run_paired(mcp, kbs["domain"], session_no, questions=qs[:6])

    hint_rows = [r for r in rep["rows"] if r["variant"] == "hint"]
    plain_rows = [r for r in rep["rows"] if r["variant"] == "plain"]
    assert len(hint_rows) == len(plain_rows) == 6, "配对样本必须 1:1 ✗（%d vs %d）" % (
        len(hint_rows), len(plain_rows))
    assert rep["design"] == "paired"
    assert all(r["question_key"] for r in rep["rows"]), "每条都要能按问题归一化聚簇 ✗"
    assert all(r["attempts"] >= 1 for r in rep["rows"])

    s = rep["summary"]
    for variant in ("hint", "plain"):
        assert s[variant]["n"] == 6
        for key in ("found_rate", "case_ok_rate", "avg_attempts", "avg_ms"):
            assert key in s[variant], "汇总缺指标 %r ✗" % key
    # hint 组"有出路"必须 ≥ plain（plain 结构上没有 suggestions/next ✓）
    assert s["hint"]["guidance_rate"] >= s["plain"]["guidance_rate"]

    path = rep.get("report_path") or ""
    assert path and os.path.exists(path), "A/B 报告未落盘 ✗"
    saved = json.loads(open(path, encoding="utf-8").read())
    assert saved["summary"]["hint"]["n"] == saved["summary"]["plain"]["n"] == 6

    side = os.path.join(ab_runner.REPO, "logs", "lookup_ab_%s.jsonl" % time.strftime("%Y%m%d"))
    assert os.path.exists(side), "遥测侧车日志未生成 ✗（%s）" % side
    last = json.loads(open(side, encoding="utf-8").read().strip().splitlines()[-1])
    assert last["telemetry"]["variant"] in ("hint", "plain")
