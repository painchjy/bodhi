"""`/bodhi/ext/sync-report` 的**定点替换**回归（2026-10-26 用户实测两处坏味）

1. 保存+同步后正文没变：同步把 **① 表的数据行**当成了条目表（`label="编号"` 是
   「评审编号」的子串）⇒ ① 被换成条目表、③ 没更新、版本还多出两版无差异；
2. ③ 里出现重复条目：历史上残留了第二张锚点表。

口径：
- `_replace_section_table` 只认**表头单元格**（精确优先；退化时要求列数 ≥3）；
- `_dedupe_anchor_tables` 同一锚点只留第一张表。
"""
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for p in (os.path.join(REPO, "tools", "ke-core"), os.path.join(REPO, "tools", "ontology-mcp")):
    if p not in sys.path:
        sys.path.insert(0, p)

from srv import ext_api as E  # noqa: E402

SEC1 = """## ① 范围与依据

| 项 | 内容 |
|---|---|
| 评审编号 / 轮次 | YYYYMM-1 ／ 第 1 轮 |
| 评审对象 | 《方案》（doc-1） |
| 评审依据 | 策略（6 条规则） |
"""

SEC3 = """## ③ 问题清单

| 编号 | 问题描述 | 触发规则 | 判定 | 严重度 | 规则指纹 |
|---|---|---|---|---|---|
| P-01 | d1 | r1 | 不符合 | 一般 | 16c4fde43637ed6c |
| P-02 | d2 | r2 | 符合 |  | acd26930b9eb2542 |
"""

TABLE = ("| 编号 | 问题描述 | 触发规则 | 判定 | 严重度 | 规则指纹 |\n"
         "|---|---|---|---|---|---|\n"
         "| P-01 | d1 | r1 | 不符合 | 严重 | 16c4fde43637ed6c |\n"
         "| P-02 | d2 | r2 | 符合 |  | acd26930b9eb2542 |")


def test_replace_targets_issue_table_not_range_section():
    """① 表里那行「评审编号」不能被当成条目表 ⇒ ③ 被替换、① 原样 ✓。"""
    content = SEC1 + "\n" + SEC3
    new, ok = E._replace_section_table(content, TABLE, "编号")
    assert ok is True
    assert "| 评审编号 / 轮次 | YYYYMM-1 ／ 第 1 轮 |" in new, "① 必须原样保留"
    assert "| 评审对象 | 《方案》（doc-1） |" in new
    assert new.count("| 编号 | 问题描述 |") == 1, "不能出现两层表头"
    assert "| P-01 | d1 | r1 | 不符合 | 严重 |" in new, "③ 必须换成新表"


def test_replace_no_table_when_label_only_in_body_rows():
    """表头里没有锚点列 ⇒ 判定"找不到"，绝不拿数据行当锚点（宁可不写 ✗）。"""
    only1 = SEC1
    new, ok = E._replace_section_table(only1, TABLE, "编号")
    assert ok is False and new == only1


def test_dedupe_keeps_single_anchor_table():
    """③ 里残留第二张锚点表（历史坏写）⇒ 只留第一张 ✓。"""
    content = SEC3 + "\n" + "| 编号 | 问题描述 | 触发规则 | 判定 | 严重度 | 规则指纹 |\n" \
                          "|---|---|---|---|---|---|\n" \
                          "| P-03 | d3 | r3 | 无法判定 | 严重 | fa7004fd7b4054c6 |\n"
    new, n = E._dedupe_anchor_tables(content, "编号")
    assert n == 1
    assert new.count("| 编号 | 问题描述 |") == 1
    assert "P-03" not in new


def test_dedupe_noop_on_single_table():
    new, n = E._dedupe_anchor_tables(SEC3, "编号")
    assert n == 0 and new == SEC3
