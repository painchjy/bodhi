"""回归：**规则工程闭环**（P0，2026-10-29）。

分两层：
- **纯函数层**（无外部依赖，必跑）：`summarize` 的统计/阈值/门禁口径；
- **库层**（DB/图不可用 ⇒ skip）：规则四要素、`ke_rule_check` 门禁、台账→评测分布一致、
  评测页存在且 `gate=ok`、规则工程 6 个任务页的 note 含确定性步骤标记。

用 `BODHI_RULE_REG_KB` 指定被回归的库（默认测试库 `_test_rule_regression_1029`）；
`BODHI_RULE_REG_REVIEW` 指定台账编号（默认 `20261027-101010`）。
"""
import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
for p in (HERE, os.path.join(REPO, "tools", "ke-core"),
          os.path.join(REPO, "tools", "ontology-mcp")):
    if p not in sys.path:
        sys.path.insert(0, p)

KB_ENV = "BODHI_RULE_REG_KB"
REVIEW_ENV = "BODHI_RULE_REG_REVIEW"
DEFAULT_REVIEW = "20261027-101010"
SIX_TASKS = ["规则化", "规则编辑", "规则发布", "规则评测", "评测设计", "规则溯源"]


# ── 纯函数层（必跑）─────────────────────────────────────────────────────────
def _items(rule, verdicts, sev=None, quote=True, loc=True):
    out = []
    for i, v in enumerate(verdicts):
        out.append({"rule": rule, "verdict": v,
                    "severity": (sev or ["一般"] * len(verdicts))[i],
                    "quote": "原文" if quote else "",
                    "doc_loc": "2.1 章节" if loc else ""})
    return out


def test_summarize_counts_match_input():
    """分布 = 台账逐行统计（不许自报）。"""
    import ke_rule_runtime_eval as E
    items = _items("R1", ["符合", "不符合", "不适用", "不符合"])
    got = E.summarize(items, "R1", ["符合", "不符合", "无法判定", "不适用"])
    assert got["triggered"] is True and got["items"] == 4
    assert got["verdicts"] == {"符合": 1, "不符合": 2, "无法判定": 0, "不适用": 1}


def test_summarize_gate_threshold_and_veto():
    """证据可回溯率 < 阈值 ⇒ insufficient；「不符合∧严重」计入否决数。"""
    import ke_rule_runtime_eval as E
    half = _items("R1", ["符合", "不符合"], sev=["一般", "严重"])
    half[1]["quote"] = ""                              # 1/2 可回溯 = 0.5 < 0.6
    got = E.summarize(half, "R1", ["符合", "不符合"], threshold=0.60)
    assert got["evidence_traceable_rate"] == 0.5
    assert got["gate"] == "insufficient"
    assert got["veto_count"] == 1


def test_summarize_not_triggered_semantics():
    """无条目：`always` ⇒ not_triggered（要查漏判）；有条件 ⇒ not_applicable。"""
    import ke_rule_runtime_eval as E
    assert E.summarize([], "R1", ["符合"], applies_when="always")["gate"] == "not_triggered"
    assert E.summarize([], "R1", ["符合"], applies_when="当存在A")["gate"] == "not_applicable"


def test_summarize_ignores_other_rules():
    """只统计本规则的行（多规则台账混在一起也不串）。"""
    import ke_rule_runtime_eval as E
    items = _items("R1", ["符合"]) + _items("R2", ["不符合", "不符合"])
    got = E.summarize(items, "R1", ["符合", "不符合"])
    assert got["items"] == 1 and got["verdicts"]["不符合"] == 0


# ── 库层（DB 不可用 ⇒ skip）────────────────────────────────────────────────
@pytest.fixture(scope="module")
def db():
    try:
        import ke_db
        ke_db.psql_csv("SELECT 1 AS ok")
    except Exception as exc:                       # noqa: BLE001
        pytest.skip("PostgreSQL 不可用：%r" % (str(exc)[:80],))
    return ke_db


@pytest.fixture(scope="module")
def reg_kb(db):
    kb = os.environ.get(KB_ENV) or db.resolve_kb_id("_test_rule_regression_1029")[0]
    if not kb:
        pytest.skip("找不到回归库（设 %s 指向含两条规则的测试库）" % KB_ENV)
    return kb


@pytest.fixture(scope="module")
def domain_kb(db):
    kb = db.resolve_kb_id("技术方案评审领域模型")[0]
    if not kb:
        pytest.skip("找不到技术方案评审领域模型库")
    return kb


def test_rule_pages_have_four_elements(reg_kb):
    """规则页四要素齐（定义/适用范围/执行级别/实现方式）—— 规则化门禁的前提。"""
    import ke_db
    rows = ke_db.psql_csv(
        "SELECT slug, title, "
        "coalesce(page_metadata->'ontology'->'attributes'->>'skos:definition','') AS d, "
        "coalesce(page_metadata->'ontology'->'attributes'->>'bmm:ruleScope','') AS s, "
        "coalesce(page_metadata->'ontology'->'attributes'->>'bmm:enforcementLevel','') AS e, "
        "coalesce(page_metadata->'ontology'->'attributes'->>'bmm:ruleImplementation','') AS i "
        "FROM wiki_pages WHERE deleted_at IS NULL AND knowledge_base_id = %s "
        " AND page_type LIKE 'bmm:%%Rule' ORDER BY slug LIMIT 10"
        % ke_db.sql_str(reg_kb))
    assert rows, "回归库里没有规则页"
    for r in rows:
        lack = [k for k in ("d", "s", "e", "i") if not str(r.get(k) or "").strip()]
        assert not lack, "规则 %s 缺要素 %r" % (r["title"], lack)


def test_rule_check_gate_passes(domain_kb):
    """规则集门禁：`ke_rule_check` 通过（错误 0；提醒允许并记录）。"""
    import ke_rule_check as C
    res = C.check_rules(domain_kb, "policy:bmm/businesspolicy/技术方案评审策略")
    assert res.get("rules", 0) > 0, res
    assert res["ok"] is True, "规则体检未通过：%r" % (res.get("errors") or [])[:3]


def test_ledger_stats_equal_eval_page(reg_kb):
    """台账逐行统计 == 评测页 `verdicts`（**不允许自报**）。"""
    import ke_db
    import ke_rule_runtime_eval as E
    review = os.environ.get(REVIEW_ENV) or DEFAULT_REVIEW
    items = E.ledger_items(review)
    if not items:
        pytest.skip("台账 %s 不可读（外部服务未启动）" % review)
    verdicts = ["符合", "不符合", "无法判定", "不适用"]
    rules = sorted({str(x.get("rule") or "") for x in items if x.get("rule")})
    assert rules, "台账里没有 rule 列值"
    checked = 0
    for rule in rules[:3]:
        stat = E.summarize(items, rule, verdicts)
        rows = ke_db.psql_csv(
            "SELECT coalesce(page_metadata->'ontology'->'attributes'->>'skos:note','') AS note "
            "FROM wiki_pages WHERE deleted_at IS NULL AND knowledge_base_id = %s "
            " AND page_type='agent:RuleEvaluation' AND title LIKE %s LIMIT 1"
            % (ke_db.sql_str(reg_kb), ke_db.sql_str("%" + rule[:24] + "%")))
        if not rows:
            continue
        note = rows[0].get("note") or ""
        for v, n in (stat.get("verdicts") or {}).items():
            if n:
                assert ("%s %d" % (v, n)) in note or (v in note), \
                    "评测页 %s 的分布与台账不一致（%s=%d）" % (rule, v, n)
        checked += 1
    assert checked > 0, "没有匹配到评测页"


def test_rule_task_pages_have_steps(db):
    """规则工程 6 个任务页的 note 含**确定性步骤标记**（技能退役后的执行载体）。"""
    import ke_db
    kb = db.resolve_kb_id("企业知识管理领域")[0]
    if not kb:
        pytest.skip("找不到企业知识管理领域库")
    rows = ke_db.psql_csv(
        "SELECT title, coalesce(page_metadata->'ontology'->'attributes'->>'skos:note','') AS note "
        "FROM wiki_pages WHERE deleted_at IS NULL AND knowledge_base_id = %s "
        " AND slug LIKE 'bpmn/task/%%'" % ke_db.sql_str(kb))
    got = {r["title"]: (r.get("note") or "") for r in rows}
    for name in SIX_TASKS:
        hit = [t for t in got if name in t]
        assert hit, "缺任务页：%s" % name
        note = got[hit[0]]
        assert len(note) >= 200, "%s 的步骤过短（%d 字）" % (name, len(note))
    joined = " ".join(got.values())
    assert "ke_rule_check" in joined and "ke_rule_runtime_eval" in joined, \
        "规则工程步骤里缺门禁/评测工具名（确定性步骤不完整）"
    assert "domain_modeling" not in joined, "规则工程仍引用已退役技能 domain_modeling"

