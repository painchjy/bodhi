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


# ── 2026-10-26 用户实测：专家反馈保存+刷新后「问题清单表格错位」 ──────────────
BROKEN = """## ③ 问题清单

| 编号 | 问题描述 | 触发规则 | 文档位置 | 原文摘录 | 判定 |
|---|---|---|---|---|---|
| P-01 | d1 | r1 | 2.1 | 摘录一 | 符合 |
| P-02 | d2 | r2 | 2.2 | 达梦数据库 DM8
业务库
8.1.3.100
同城 | 不符合 |
| P-03 | d3 | r3 | 2.3 | 摘录三 | 无法判定 |

> 表后补充：本表逐字来自台账回执。
"""


def test_replace_repairs_broken_multiline_rows():
    """单元格含换行被写散的历史坏行 ⇒ 一次替换后**不留残行**（也不吞表后正文）。"""
    table = ("| 编号 | 问题描述 | 触发规则 | 文档位置 | 原文摘录 | 判定 |\n"
             "|---|---|---|---|---|---|\n"
             "| P-01 | d1 | r1 | 2.1 | 摘录一 | 符合 |\n"
             "| P-02 | d2 | r2 | 2.2 | 达梦数据库 DM8<br>业务库<br>8.1.3.100<br>同城 | 不符合 |\n"
             "| P-03 | d3 | r3 | 2.3 | 摘录三 | 无法判定 |")
    new, ok = E._replace_section_table(BROKEN, table, "编号")
    assert ok is True
    assert "业务库" in new and "业务库<br>" in new, "换行必须压成 <br> 回到单元格里"
    assert "\n业务库\n" not in new, "坏行残片必须被替换掉（不能留裸行）"
    assert new.count("| 编号 | 问题描述 |") == 1
    assert "> 表后补充" in new, "表后正文不能被吞"
    for ln in new.splitlines():
        s = ln.strip()
        assert not s or s.startswith(("|", "#", ">")) or s.startswith("表后"), "出现裸行：%r" % ln


def test_md_escapes_newlines_in_cells():
    """台账渲染：单元格内换行 → `<br>`，竖线 → `\\|`；行数必须 = 条目数 + 2。"""
    import ke_ext_issues as ISS
    schema = {"fields": ["issue_no", "quote"], "order": ["issue_no", "quote"],
              "labels": {"issue_no": "编号", "quote": "原文摘录"},
              "verdict_enum": [], "severity_enum": []}
    items = [{"issue_no": "P-01", "quote": "达梦数据库 DM8\n业务库\n8.1.3.100\n同城"},
             {"issue_no": "P-02", "quote": "a|b"}]
    md = ISS._md(items, schema)
    lines = md.splitlines()
    assert len(lines) == 4, md
    assert "达梦数据库 DM8<br>业务库<br>8.1.3.100<br>同城" in lines[2]
    assert "a\\|b" in lines[3]
