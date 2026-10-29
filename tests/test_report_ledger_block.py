"""回归：**技术方案评审报告必须带问题清单台账块**（2026-10-30 用户要求）。

覆盖：
1. 台账 → 报告：`auto_ref_block` 能从**报告正文的编号/条目**定位台账，把 ```bodhi-ext 块
   补在 ③ 问题清单表之后（值全来自台账/契约，不让模型手写编号）；
2. 幂等：已有块 ⇒ `has_block`（不重复插）；
3. 无条目表 / 无台账 ⇒ 明确 reason（**不猜** ✗）；
4. `check_report`：块内编号与台账一致时 `ok=True` 且 `unknown_items=[]`。

依赖：本地台账分片（容器根 `state/ext/issues/<review_no>.json`）+ 领域库（读 DataObject 契约）。
缺任一 ⇒ skip。
"""
import json
import os
import sys
import uuid

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
for p in (HERE, os.path.join(REPO, "tools", "ke-core"),
          os.path.join(REPO, "tools", "ontology-mcp")):
    if p not in sys.path:
        sys.path.insert(0, p)

OBJ_SLUG = "bpmn/dataobject/问题清单"


@pytest.fixture(scope="module")
def env():
    try:
        import ke_db
        import ke_ext_gate as G                      # noqa: F401
        ke_db.psql_csv("SELECT 1 AS ok")
    except Exception as exc:                         # noqa: BLE001
        pytest.skip("依赖不可用：%r" % (str(exc)[:80],))
    kb = ke_db.resolve_kb_id("技术方案评审领域模型")[0]
    if not kb:
        pytest.skip("找不到技术方案评审领域模型库")
    return {"kb": kb, "db": ke_db}


@pytest.fixture
def ledger(env):
    """临时台账（走 `ke_ext_issues.maintain` 真写口，用完删分片）。"""
    import ke_ext_issues as ISSUE
    review_no = "TEST-%s" % uuid.uuid4().hex[:8].upper()
    items = [
        {"issue_no": "P-01", "desc": "部署图缺异地云标注", "rule": "技术架构-开源软件适用规则",
         "doc_loc": "2.3 部署架构", "quote": "同城双活", "verdict": "不符合",
         "severity": "严重", "fix": "补异地云标注", "confidence": "高",
         "rule_fp": "22e44ac2f97e874c", "expert_feedback": ""},
        {"issue_no": "P-02", "desc": "未给出应用清单", "rule": "技术方案文档结构规范",
         "doc_loc": "3 应用清单", "quote": "见附表", "verdict": "无法判定",
         "severity": "一般", "fix": "补应用清单", "confidence": "中",
         "rule_fp": "acd26930b9eb2542", "expert_feedback": ""},
    ]
    out = ISSUE.maintain(kb_id=env["kb"], object_slug=OBJ_SLUG, mode="maintain", action="create",
                         review_no=review_no, items=items, actor="pytest",
                         session_no="pytest", dry_run=False)
    if not out.get("ok"):
        pytest.skip("台账不可写：%r" % (out.get("error") or out.get("why"),))
    yield {"review_no": review_no, "items": items}
    try:                                             # 清理：扫容器根找该编号的分片
        import glob
        import ke_ext_store as S
        for p in glob.glob(os.path.join(S.system_dir(), "**", "%s.json" % review_no),
                           recursive=True):
            os.remove(p)
    except Exception:                                # noqa: BLE001
        pass


def _report_md(review_no: str) -> str:
    """最小合规报告：① 含编号；③ 表头与 DataObject 契约标签对齐（编号/问题描述/触发规则…）。"""
    return "\n".join([
        "# 技术方案评审报告（回归）", "",
        "## ① 范围与依据", "",
        "| 评审编号 | 评审文档 | 评审轮次 |", "|---|---|---|",
        "| %s | 示例方案.pdf | 1 |" % review_no, "",
        "## ③ 问题清单", "",
        "| 编号 | 问题描述 | 触发规则 | 文档位置 | 原文摘录 | 判定 | 严重度 |",
        "|---|---|---|---|---|---|---|",
        "| P-01 | 部署图缺异地云标注 | 技术架构-开源软件适用规则 | 2.3 部署架构 | 同城双活 | 不符合 | 严重 |", "",
        "## ⑤ 结论与建议", "", "见上表。", "",
    ])


def test_report_gets_ledger_block(env, ledger):
    """缺块 ⇒ 自动补，且块在 ③ 表之后、内容来自台账。"""
    import ke_ext_gate as G
    md = _report_md(ledger["review_no"])
    assert "```bodhi-ext" not in md

    out = G.auto_ref_block(md, env["kb"])
    assert out["changed"] is True, out
    assert out["object"] == OBJ_SLUG, out
    assert out["review_no"] == ledger["review_no"]
    assert out["rev"] >= 1
    assert out["columns"], "块里必须写列集（不允许空）"

    new_md = out["md"]
    assert "```bodhi-ext" in new_md and "```" in new_md.split("```bodhi-ext", 1)[1]
    block = new_md.split("```bodhi-ext", 1)[1].split("```", 1)[0]
    for key in ("object:", "review_no:", "basis:", "columns:", "rev:"):
        assert key in block, "块缺字段 %s：%s" % (key, block)
    assert "schema_hash:" in block

    # 块必须紧跟 ③ 表（不是插到文末/文首）
    idx_tbl = new_md.index("| P-01 |")
    idx_blk = new_md.index("```bodhi-ext")
    idx_sec5 = new_md.index("## ⑤")
    assert idx_tbl < idx_blk < idx_sec5, "块位置不对：应在 ③ 表之后、⑤ 之前"


def test_block_is_idempotent(env, ledger):
    """已有块 ⇒ 不再插（`has_block`）。"""
    import ke_ext_gate as G
    md = G.auto_ref_block(_report_md(ledger["review_no"]), env["kb"])["md"]
    again = G.auto_ref_block(md, env["kb"])
    assert again["changed"] is False and again["reason"] == "has_block"
    assert again["md"].count("```bodhi-ext") == 1


def test_no_item_table_is_not_guessed(env, ledger):
    """没有条目表 ⇒ `no_item_table`（不猜、不硬塞块）。"""
    import ke_ext_gate as G
    md = "# 报告\n\n## ① 范围与依据\n\n| 评审编号 |\n|---|\n| %s |\n" % ledger["review_no"]
    out = G.auto_ref_block(md, env["kb"])
    assert out["changed"] is False
    assert out["reason"] in ("no_item_table", "no_object_match", "ledger_no_match"), out


def test_check_report_accepts_block(env, ledger):
    """块内编号与台账一致 ⇒ `check_report` ok 且没有未知条目。"""
    import ke_ext_gate as G
    md = G.auto_ref_block(_report_md(ledger["review_no"]), env["kb"])["md"]
    res = G.check_report(md, env["kb"])
    assert res["ok"] is True, res
    assert res.get("ledger") == ledger["review_no"], res
    assert res.get("unknown_items") == [], res
    assert res.get("report_items", 0) >= 1
