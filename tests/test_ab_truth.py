"""P2/P3 回归：**三态分臂** + **真值匹配/判分**（口径 2026-10-25）

三条断言：
1. 分臂：`abtest` 下同一 (问题, 会话) **稳定**、跨会话约 50/50；显式布尔永远优先；
2. 匹配：**锚点为主**（短问句也能命中，整句相似度只有 0.07）；并列/无关 ⇒ 不匹配（不猜）；
3. 判分：严格集合（缺/多/越界）+ **未匹配不进分母**（只影响 coverage）。
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                "tools", "ke-core"))
import ke_ab_truth as T  # noqa: E402
import ke_lookup as L  # noqa: E402

ENTRY = {
    "id": "T01",
    "question": "列出\"信创与异地机房搬迁\"类技术方案需要的评审规则",
    "anchors": ["信创与异地机房搬迁", "信创"],
    "intent": ["规则"],
    "expect": {"rules": ["a/规则一", "a/规则二", "a/规则三"], "forbid": ["a/越界规则"]},
}
TRUTH = {"version": "test", "questions": [ENTRY]}
DICT = {
    "a/规则一": {"title": "规则一", "aliases": []},
    "a/规则二": {"title": "规则二", "aliases": []},
    "a/规则三": {"title": "规则三", "aliases": []},
    "a/越界规则": {"title": "越界规则", "aliases": []},
    "a/其它规则": {"title": "其它规则", "aliases": []},
}


# ── 分臂 ────────────────────────────────────────────────────────────────
def test_arm_is_stable_and_about_half(monkeypatch):
    monkeypatch.setenv("BODHI_ONTOLOGY_HINT", "abtest")
    monkeypatch.setenv("BODHI_AB_ROTATION", "hash")
    a = L._resolve_arm(None, "信创要满足哪些规则", "s1")
    b = L._resolve_arm(None, "信创要满足哪些规则", "s1")
    assert a[1] == "abtest" and a[2]["arm"] in ("hint", "plain")
    assert a == b, "同一 (问题, 会话) 的分臂必须稳定（可复算）✗"
    arms = [L._resolve_arm(None, "问题%d" % i, "sess%d" % i)[2]["arm"] for i in range(200)]
    n_hint = arms.count("hint")
    assert 60 <= n_hint <= 140, "跨会话应约 50/50（实测 hint=%d/200）✗" % n_hint


def test_explicit_bool_beats_env(monkeypatch):
    monkeypatch.setenv("BODHI_ONTOLOGY_HINT", "abtest")
    hint, assignment, _ab = L._resolve_arm(False, "任意问题", "s")
    assert hint is False and assignment == "forced", "显式布尔必须优先于 env ✗"
    hint, assignment, _ab = L._resolve_arm(True, "任意问题", "s")
    assert hint is True and assignment == "forced"


def test_env_three_states(monkeypatch):
    monkeypatch.setenv("BODHI_ONTOLOGY_HINT", "on")
    assert L._hint_mode() == "on"
    monkeypatch.setenv("BODHI_ONTOLOGY_HINT", "off")
    assert L._hint_mode() == "off"
    monkeypatch.setenv("BODHI_ONTOLOGY_HINT", "abtest")
    assert L._hint_mode() == "abtest"
    monkeypatch.setenv("BODHI_ONTOLOGY_HINT", "随便写的")
    assert L._hint_mode() == "abtest", "未知值应落 abtest（不再静默当 on）✗"


# ── 匹配 ────────────────────────────────────────────────────────────────
def test_anchor_rescues_short_paraphrase():
    """「信创的项目要看哪些规则」整句相似度只有 0.067 ⇒ 必须靠**锚点**命中。"""
    m = T.match("信创的项目要看哪些规则", "", TRUTH)
    assert m["matched"] and m["entry_id"] == "T01", m
    assert m["matched_by"] == "anchor"
    assert T._sim_of(L._question_core("信创的项目要看哪些规则"), ENTRY) < 0.35, \
        "该例的整句相似度本就低于兜底阈值（用来说明锚点的必要性）✗"


def test_specificity_disambiguates(monkeypatch):
    """两个条目的锚点都可能命中时，取**更具体**的（完整包含 > 最长公共子串）。"""
    other = dict(ENTRY, id="T02", anchors=["技术方案逐项评审策略", "逐项评审"],
                 question="技术方案逐项评审策略包含哪些规则？")
    truth = {"version": "t", "questions": [ENTRY, other]}
    m = T.match("技术方案逐项评审策略包含哪些规则？", "", truth)
    assert m["entry_id"] == "T02", "应命中更具体的条目 ✗（实测 %r）" % (m.get("entry_id"),)
    m2 = T.match("列出\"信创与异地机房搬迁\"类技术方案需要的评审规则", "", truth)
    assert m2["entry_id"] == "T01", "完整包含锚点的条目应胜出 ✗"


def test_unrelated_question_not_matched():
    m = T.match("部署要求有哪些规则", "", TRUTH)
    assert not m["matched"], "无关问题不得硬匹配（未匹配 ⇒ 不进分母）✗ %r" % (m,)


# ── 判分 ────────────────────────────────────────────────────────────────
def test_judge_strict_set():
    ok = T.judge("规则一；规则二；规则三", ENTRY, DICT)
    assert ok["correct"] and ok["n_hit"] == 3, ok
    miss = T.judge("规则一；规则二", ENTRY, DICT)
    assert not miss["correct"] and len(miss["missing"]) == 1, miss
    extra = T.judge("规则一；规则二；规则三；其它规则", ENTRY, DICT)
    assert not extra["correct"] and extra["extra"] == ["a/其它规则"], extra


def test_forbidden_makes_it_wrong():
    r = T.judge("规则一；规则二；规则三；越界规则", ENTRY, DICT)
    assert not r["correct"], "越界项命中必须判错（即使其余全对）✗"
    assert r["forbidden"] == ["a/越界规则"], r


def test_fetched_judging_counts_forbidden():
    """检索口径（只看到 slug）也要能抓到越界项 —— 这是不看答案就能判的那一面。"""
    ok = T.judge_slugs(["a/规则一", "a/规则二", "a/规则三"], ENTRY)
    assert ok["correct"], ok
    bad = T.judge_slugs(["a/规则一", "a/规则二", "a/规则三", "a/越界规则"], ENTRY)
    assert not bad["correct"] and bad["forbidden"] == ["a/越界规则"], bad


def test_unmatched_rows_not_in_denominator(monkeypatch):
    monkeypatch.setattr(T, "load", lambda path="": dict(TRUTH))
    monkeypatch.setattr(T, "rule_dictionary", lambda kb: DICT)
    trace = [
        {"question": "信创的项目要看哪些规则", "qid": "", "answer": "规则一；规则二；规则三",
         "variant": "hint", "answer_len": 20,
         "rule_slugs": ["a/规则一", "a/规则二", "a/规则三"]},
        {"question": "给这个流程改个名字", "qid": "", "answer": "好的，已改名",
         "variant": "plain", "answer_len": 8},
    ]
    res = T.judge_trace(trace, truth_path="")
    st = res["stats"]
    assert st["n_total"] == 2 and st["n_matched"] == 1 and st["n_judged"] == 1, st
    assert st["coverage"] == 0.5, "未匹配行不得进入分母（coverage=1/2）✗ %r" % (st,)
    assert st["correct"] == 1 and st["accuracy"] == 1.0, st
    # 检索口径（不看答案）+ 采纳率
    assert st["n_fetched_judged"] == 1 and st["fetched_accuracy"] == 1.0, st
    assert st["adoption_avg"] == 1.0, "取到的 3 条都被答案提到 ⇒ 采纳率应为 1.0 ✗ %r" % (st,)
    assert [r["reason"] for r in res["rows"]][1] == "no_truth"


def test_group_dedupe_uses_one_sample_per_group(monkeypatch):
    """同一组多轮只算一个样本（组等权）—— 否则 n 会被同题重跑放大 ✗"""
    monkeypatch.setattr(T, "load", lambda path="": dict(TRUTH))
    monkeypatch.setattr(T, "rule_dictionary", lambda kb: DICT)
    trace = [
        {"question": "信创的项目要看哪些规则", "qid": "a1", "answer": "规则一；规则二；规则三",
         "variant": "hint", "question_group": "G1"},
        {"question": "信创的项目要看哪些规则", "qid": "a1", "answer": "规则一",
         "variant": "plain", "question_group": "G1"},
        {"question": "信创的项目要看哪些规则", "qid": "a2", "answer": "规则一；规则二；规则三",
         "variant": "hint", "question_group": "G2"},
    ]
    st = T.judge_trace(trace, truth_path="")["stats"]
    assert st["n_judged"] == 3 and st["correct"] == 2, st          # 逐轮口径
    assert st["n_groups"] == 2, "同组两轮应合成 1 个样本 ✗ %r" % (st,)
    assert st["group_accuracy"] == round((0.5 + 1.0) / 2, 3), st  # (1/2 + 1/1) / 2
    assert st["groups_all_correct"] == 1 and st["groups_none_correct"] == 0, st


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
