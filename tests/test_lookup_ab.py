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
# 2026-10-25（用户口径）：A/B 自检**不写进真实会话**（否则报告分不清人跑的还是机器跑的 ✗）；
# `knowledge_lookup` 的 `session_no` 只进遥测、不校验 ⇒ 用专用标记，报告侧按前缀排除。
SELFTEST = ab_runner.SELFTEST_SESSION


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


def test_switch_changes_receipt(mcp, kbs):
    """同一查询、两种策略：**回执结构必须不同**（这是 A/B 的自变量）。"""
    args = {"kb_id": kbs["domain"], "mode": "keyword", "query": "流程",
            "session_no": SELFTEST, "question": "流程有哪些？"}
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
    assert tel.get("session_no") == SELFTEST, "遥测必须带上会话编号 ✗"
    assert tel.get("question") == "流程有哪些？", "遥测必须带原始问题 ✗"
    assert tel.get("question_key") and "？" not in tel["question_key"], \
        "问题归一化键应去掉全角问号 ✗：%r" % tel.get("question_key")
    # **同一次查询两组命中应一致**（开关只影响"提示面"，不影响检索本身 ✓）
    assert tel.get("hits") == (on.get("telemetry") or {}).get("hits"), \
        "开关不应改变命中数 ✗（%r vs %r）" % (tel.get("hits"),
                                            (on.get("telemetry") or {}).get("hits"))


def test_paired_dataset_complete(mcp, kbs):
    """配对数据完整性：两组样本数相等、逐题可比、报告与侧车日志落盘 ✓

    只跑前 6 题（够验证口径；全量 14 题在 `python3 tests/ab_runner.py` 里人工跑）。
    """
    qs = ab_runner.load_questions()
    assert len(qs) >= 12, "问题集太少，A/B 无统计意义 ✗（当前 %d）" % len(qs)
    rep = ab_runner.run_paired(mcp, kbs["domain"], SELFTEST, questions=qs[:6])

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
def test_telemetry_has_version_and_category(mcp, kbs):
    """遥测必须带**程序版本**（`v<语义>-<8位指纹>`）与**问题种类**（形态推断，不看结果 ✓）。"""
    r = mcp.call("knowledge_lookup", {"kb_id": kbs["domain"], "mode": "keyword",
                                     "query": "流程 活动", "session_no": SELFTEST,
                                     "question": "流程和活动是什么关系？"})
    tel = r.get("telemetry") or {}
    ver = str(tel.get("lookup_version") or "")
    assert re.match(r"^v\d+-[0-9a-f]{8}$", ver), "版本指纹格式应为 v<数字>-<8位hex> ✗（实测 %r）" % ver
    assert tel.get("code_rev") and tel["lookup_version"].endswith(tel["code_rev"])
    assert tel.get("q_category") == "多词", "两个词的 keyword 查询应归为『多词』✗（实测 %r）" % tel.get("q_category")
    # 形态推断**不看结果**：同一 query 在负样例（不存在词）里类目仍按形态 ✓
    neg = mcp.call("knowledge_lookup", {"kb_id": kbs["domain"], "mode": "keyword",
                                        "query": "zzz不存在词xxxx", "session_no": SELFTEST,
                                        "question": "zzz不存在词xxxx"})
    assert (neg.get("telemetry") or {}).get("q_category") == "单词", "负样例仍应按形态归『单词』✗"


def test_turn_begin_and_first_step_gate(mcp, kbs):
    """`lookup_turn_begin` 只开边界；`knowledge_lookup` 带 `turn_id` 后能判首步 ✓"""
    turn = mcp.call("lookup_turn_begin", {"session_no": SELFTEST, "question": "流程有哪些？"})
    assert turn.get("ok") and turn.get("turn_id"), "turn 边界工具应返回 turn_id ✗：%r" % turn
    tid = turn["turn_id"]

    first = mcp.call("knowledge_lookup", {"kb_id": kbs["domain"], "mode": "keyword",
                                          "query": "流程", "session_no": SELFTEST,
                                          "question": "流程有哪些？", "turn_id": tid,
                                          "first_step": True})
    tel = first.get("telemetry") or {}
    assert tel.get("turn_id") == tid
    assert tel.get("first_step") is True
    assert tel.get("turn_first_retrieval") == "knowledge_lookup", \
        "同轮第一个检索动作应是 knowledge_lookup ✗（实测 %r）" % tel.get("turn_first_retrieval")

    # 同一轮里再调一次：**首个动作不会被覆盖**（仍是 knowledge_lookup ✓）
    again = mcp.call("knowledge_lookup", {"kb_id": kbs["domain"], "mode": "keyword",
                                         "query": "活动", "session_no": SELFTEST,
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
    kept, excl = rep_mod.apply_filters(rows, cohort="c1")
    # 2026-10-25 修：自报 `first_step=false` **不再**是排除理由（turn_begin 必然排第一）⇒ 保留 3 条
    assert len(kept) == 3, "自报 first_step=false 不该被排除 ✗ 实得 %d" % len(kept)
    assert excl.get("first_retrieval_other") == 1
    assert excl.get("turn_missing") == 1
    assert not excl.get("self_report_false"), "默认不再按自报排除 ✗"
    # 想回到旧口径时可显式开 strict
    kept_s, excl_s = rep_mod.apply_filters(rows, cohort="c1", strict_self_report=True)
    assert len(kept_s) == 2 and excl_s.get("self_report_false") == 1

    kept_v2, excl_v2 = rep_mod.apply_filters(rows, cohort="c1", version="v2-aaaaaaaa")
    assert all(r["lookup_version"] == "v2-aaaaaaaa" for r in kept_v2)
    assert excl_v2.get("version_mismatch") == 1

    # c3（不判首步）只排除无 turn 的样本，且**不排除**首步为其他工具的行
    kept_c3, _ = rep_mod.apply_filters(rows, cohort="c3")
    assert len(kept_c3) == 5

    # 时间窗
    kept_t, excl_t = rep_mod.apply_filters(rows, since="2026-10-25", cohort="c3")
    assert not kept_t and excl_t.get("out_of_range") == 5


# ---------------------------------------------------------------------------
# 2026-10-25（口径 v2，用户四条意见）
#   ① 查询次数要看**真实 PG/Neo4j 往返**，且**空记录算失败**；
#   ② 要**总耗时**（单次 / 每问合计 / 端到端）；
#   ③ "hint 35 / plain 0；问题集合不一致（23 vs 0）"要能自解释；
#   ④ 问题要能按"语义相近"归组（不同编号 = 同一问题的多轮），形态不同算不同类。
# ---------------------------------------------------------------------------
def test_telemetry_counts_real_db_queries(mcp, kbs):
    """**1 次 MCP 调用 ≠ 1 次查库**：遥测必须给真实往返数与空结果数 ✓"""
    hit = mcp.call("knowledge_lookup", {"kb_id": kbs["domain"], "mode": "keyword",
                                        "query": "流程", "session_no": SELFTEST,
                                        "question": "流程有哪些？"})
    tel = hit.get("telemetry") or {}
    retr, io = tel.get("retrieval") or {}, tel.get("io") or {}
    assert retr and io, "遥测缺 retrieval/io（口径 v2 必须给真实 IO）✗ telemetry=%s" % sorted(tel)
    assert retr["queries"] >= 1, "一次命中检索至少要打一次库/图 ✗"
    assert io.get("queries") == retr["queries"], "io 与 retrieval 的往返数必须一致 ✗"
    assert retr["pg"] + retr["neo4j"] == retr["queries"], "PG+图 应等于总往返数 ✗"
    assert retr["ok"] is True and tel["hits"] > 0
    assert retr["hit_queries"] >= 1, "命中场景应有**有结果的往返** ✗"

    # 负样例：往返照样发生，但**没有一次返回记录** ⇒ 空查询>0、ok=False（空=失败 ✓）
    miss = mcp.call("knowledge_lookup", {"kb_id": kbs["domain"], "mode": "keyword",
                                         "query": "zzz不存在词xxxx", "session_no": SELFTEST,
                                         "question": "zzz不存在词xxxx"})
    m = (miss.get("telemetry") or {}).get("retrieval") or {}
    assert m["queries"] >= 1, "查不到也要有往返记录（否则等于没统计）✗"
    assert m["empty_queries"] >= 1, "空记录必须计入失败 ✗"
    assert m["ok"] is False, "0 命中的调用不能算成功 ✗"


def test_question_group_and_shape():
    """同编号=同问题多轮；编号/文件名/轮次不同但语义相近 ⇒ 同一语义核；形态不同 ⇒ 不同类 ✓"""
    sys.path.insert(0, os.path.join(ab_runner.REPO, "tools", "ke-core"))
    import ke_lookup as kl  # noqa: PLC0415

    q1 = "评审编号：YYYYMM-889999；评审文档：样例1-统一结算平台技术方案.pdf；第一轮，开始评审"
    q2 = "评审编号：YYYYMM-889998；评审文档：样例1-统一结算平台技术方案.docx；第二轮，开始评审"
    assert kl._extract_qid(q1) == "yyyymm-889999", "应能抽出业务编号 ✗"
    assert kl._extract_qid("流程有哪些？") == "", "没有编号时应回空串 ✗"
    c1, c2 = kl._question_core(q1), kl._question_core(q2)
    assert c1 == c2, "编号/文件名/轮次不同 ⇒ 语义核应相同 ✗（%r vs %r）" % (c1, c2)

    s1 = kl._q_shape("keyword", "流程", "", {})["q_shape"]
    s3 = kl._q_shape("keyword", "技术方案 评审 角色", "", {})["q_shape"]
    assert s1 != s3, "词数不同 ⇒ 形态应不同（不同类问题）✗"
    assert s1.startswith("1|") and s3.startswith("3+|"), "词数档应为 1 / 3+ ✗（%r, %r）" % (s1, s3)
    inst = kl._q_shape("instances", "", "bpmn:Activity", {})["q_shape"]
    assert inst.endswith("实例"), "instances 模式的层次应为『实例』✗（实测 %r）" % inst
    both = kl._q_shape("keyword", "流程", "bpmn:Process", {})["q_shape"]
    assert both.endswith("类+文本"), "有类又有文本 ⇒ 层次『类+文本』✗（实测 %r）" % both


def test_report_pairing_explains_single_arm():
    """只有一组数据时，报告必须说**对照臂为空、不可比**（不是「集合不一致」✗）"""
    sys.path.insert(0, os.path.join(ab_runner.REPO, "tools", "ke-core"))
    import ke_ab_report as rep_mod  # noqa: PLC0415

    rows = [
        {"variant": "hint", "question": "评审编号 A，开始评审", "question_key": "评审编号a开始评审",
         "qid": "a", "question_core": "评审编号开始评审", "q_shape": "1|文本",
         "hits": 3, "ms": 120, "queries": 2, "empty_queries": 0, "has_io": True,
         "ts": "2026-10-25 10:00:00", "source": "t.jsonl"},
        {"variant": "hint", "question": "评审编号 B，开始评审", "question_key": "评审编号b开始评审",
         "qid": "b", "question_core": "评审编号开始评审", "q_shape": "1|文本",
         "hits": 3, "ms": 180, "queries": 2, "empty_queries": 1, "has_io": True,
         "ts": "2026-10-25 10:05:00", "source": "t.jsonl"},
    ]
    rows = rep_mod.group_rows(rows, 0.62)
    p = rep_mod.pairing(rows)
    assert p["arm_missing"] == "plain" and not p["comparable"], "缺对照组时不可判可比 ✗"
    assert p["hint_groups"] == 1, "同语义核（不同编号）应归为同一问题组 ✗（实测 %d）" % p["hint_groups"]
    rep = {"summary": rep_mod.summarize_rows(rows, {}), "pairing": p,
           "groups": rep_mod.by_group(rows), "versions": {}, "excluded": {},
           "cohort": "c1", "cohort_note": "c1"}
    md = rep_mod.render_md(rep, rows, {}, "单臂测试")
    assert "对照臂为空" in md, "报告必须直说「对照臂为空」✗"
    assert "23 vs 0" in md, "报告必须解释旧版 23 vs 0 的含义 ✗"
    g = rep["groups"][0]
    assert g["hint_runs"] == 2 and g["hint_queries"] == 4, "组内多轮应合并计数 ✗（%r）" % g
    # 每问总耗时 = 组内多轮合计（用户要的"总耗时"）
    assert rep["summary"]["hint"]["group_ms_median"] == 300, "每问总耗时应为 120+180=300 ✗"


def test_trace_importer_parses_agent_steps():
    """C2 真值：真实工具序列 + 真·首步 + 端到端耗时（不再靠自报 ✓）"""
    sys.path.insert(0, os.path.join(ab_runner.REPO, "tools", "ke-core"))
    import ke_ab_trace as tr  # noqa: PLC0415

    steps = json.dumps([
        {"iteration": 1, "tool_calls": [
            {"name": "mcp_bodhi_ontology_lookup_turn_begin", "args": {}},
            {"name": "mcp_bodhi_ontology_knowledge_lookup",
             "args": {"mode": "keyword", "query": "流程"}}]},
        {"iteration": 2, "tool_calls": [{"name": "wiki_search", "args": {"q": "x"}}]},
    ], ensure_ascii=False)
    row = tr.build_row({"message_id": "m1", "session_no": "s1", "ts": "2026-10-25 10:00:00.5",
                        "agent_ms": 1234, "steps": steps,
                        "question": "评审编号 YYYYMM-889999；第一轮，开始评审"})
    assert row["tool_seq"] == ["lookup_turn_begin", "knowledge_lookup", "wiki_search"], \
        "工具序列应按 iteration 展开并去掉 mcp_ 前缀 ✗（%r）" % row["tool_seq"]
    assert row["first_tool"] == "lookup_turn_begin" and row["turn_begin_first"] is True, \
        "真·首工具是 turn_begin（开轮纪律）⇒ turn_begin_first 必须为 True ✗"
    assert row["first_retrieval"] == "knowledge_lookup", "首个**检索类**动作是 lookup ✗"
    # 用户口径 2026-10-25：首工具必然是 turn_begin ⇒ 判"首步"要看**首个检索动作** ✓
    assert row["first_is_lookup"] is True, "首个检索动作是 lookup ⇒ first_is_lookup 必须 True ✗"
    assert row["lookup_calls"] == 1 and row["native_retrieval_calls"] == 1
    assert row["agent_ms"] == 1234, "端到端耗时必须带出来 ✗"
    assert row["qid"] == "yyyymm-889999" and row["q_shape"].startswith("1|")
