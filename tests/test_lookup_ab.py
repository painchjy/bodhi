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
import re
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


# ---------------------------------------------------------------------------
# 2026-10-24（A/B 四维）：版本 / 首步口径 / 问题类目
# ---------------------------------------------------------------------------
def test_telemetry_has_version_and_category(mcp, kbs, session_no):
    """遥测必须带**程序版本**（`v<语义>-<8位指纹>`）与**问题种类**（形态推断，不看结果 ✓）。"""
    r = mcp.call("knowledge_lookup", {"kb_id": kbs["domain"], "mode": "keyword",
                                     "query": "流程 活动", "session_no": session_no,
                                     "question": "流程和活动是什么关系？"})
    tel = r.get("telemetry") or {}
    ver = str(tel.get("lookup_version") or "")
    assert re.match(r"^v\d+-[0-9a-f]{8}$", ver), "版本指纹格式应为 v<数字>-<8位hex> ✗（实测 %r）" % ver
    assert tel.get("code_rev") and tel["lookup_version"].endswith(tel["code_rev"])
    assert tel.get("q_category") == "多词", "两个词的 keyword 查询应归为『多词』✗（实测 %r）" % tel.get("q_category")
    # 形态推断**不看结果**：同一 query 在负样例（不存在词）里类目仍按形态 ✓
    neg = mcp.call("knowledge_lookup", {"kb_id": kbs["domain"], "mode": "keyword",
                                        "query": "zzz不存在词xxxx", "session_no": session_no,
                                        "question": "zzz不存在词xxxx"})
    assert (neg.get("telemetry") or {}).get("q_category") == "单词", "负样例仍应按形态归『单词』✗"


def test_turn_begin_and_first_step_gate(mcp, kbs, session_no):
    """`lookup_turn_begin` 只开边界；`knowledge_lookup` 带 `turn_id` 后能判"首步" ✓"""
    turn = mcp.call("lookup_turn_begin", {"session_no": session_no, "question": "流程有哪些？"})
    assert turn.get("ok") and turn.get("turn_id"), "turn 边界工具应返回 turn_id ✗：%r" % turn
    tid = turn["turn_id"]

    first = mcp.call("knowledge_lookup", {"kb_id": kbs["domain"], "mode": "keyword",
                                          "query": "流程", "session_no": session_no,
                                          "question": "流程有哪些？", "turn_id": tid,
                                          "first_step": True})
    tel = first.get("telemetry") or {}
    assert tel.get("turn_id") == tid
    assert tel.get("first_step") is True
    assert tel.get("turn_first_retrieval") == "knowledge_lookup", \
        "同轮第一个检索动作应是 knowledge_lookup ✗（实测 %r）" % tel.get("turn_first_retrieval")

    # 同一轮里再调一次：**首个动作不会被覆盖**（仍是 knowledge_lookup ✓）
    again = mcp.call("knowledge_lookup", {"kb_id": kbs["domain"], "mode": "keyword",
                                         "query": "活动", "session_no": session_no,
                                         "question": "流程有哪些？", "turn_id": tid})
    assert (again.get("telemetry") or {}).get("turn_first_retrieval") == "knowledge_lookup"


def test_report_filters_version_and_first_step(tmp_path):
    """报告器：C1 门禁会**排除并计数**（不是悄悄丢样本 ✗），版本可过滤且分表。"""
    sys.path.insert(0, os.path.join(ab_runner.REPO, "tools", "ke-core"))
    import ke_ab_report as rep_mod  # noqa: PLC0415

    base = {"variant": "hint", "question_key": "q", "hits": 1, "ms": 10, "source": "t.jsonl",
            "ts": "2026-10-24 10:00:00 +0800", "lookup_version": "v2-aaaaaaaa", "first_step": True}
    rows = [
        dict(base, turn_id="t1", turn_first_retrieval="knowledge_lookup"),          # 纳入
        dict(base, turn_id="t2", turn_first_retrieval="context_lookup"),            # 首步不是它
        dict(base),                                                                # 无 turn（历史数据）
        dict(base, turn_id="t4", turn_first_retrieval="knowledge_lookup", first_step=False),  # 自报否
        dict(base, turn_id="t5", turn_first_retrieval="knowledge_lookup", lookup_version="v1-bbbbbbbb"),
    ]
    kept, excl = rep_mod.apply_filters(rows, cohort="c1", first_step_only=True)
    assert len(kept) == 2, "应保留 2 条（C1 合规 + v1 的 1 条）✗ 实得 %d" % len(kept)
    assert excl.get("first_retrieval_other") == 1
    assert excl.get("turn_missing") == 1
    assert excl.get("self_report_false") == 1

    kept_v2, excl_v2 = rep_mod.apply_filters(rows, cohort="c1", version="v2-aaaaaaaa")
    assert all(r["lookup_version"] == "v2-aaaaaaaa" for r in kept_v2)
    assert excl_v2.get("version_mismatch") == 1

    # c3（不判首步）只排除无 turn 的样本，且**不排除**首步为其他工具的行
    kept_c3, _ = rep_mod.apply_filters(rows, cohort="c3")
    assert len(kept_c3) == 5

    # 时间窗
    kept_t, excl_t = rep_mod.apply_filters(rows, since="2026-10-25", cohort="c3")
    assert not kept_t and excl_t.get("out_of_range") == 5
