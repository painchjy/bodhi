"""回归：**映射引擎**（P3-b，2026-10-30）——领域口径 ↔ 工具契约双向转换。

断言：
1. `business_columns`：业务列名 + **领域列序**（映射里没有的字段补在后面）；
2. 值转换：工具→业务 / 业务→工具；**未知值原样透传**并标未命中（不丢、不猜）；
3. `render_table`：表头用业务列名、值用业务枚举，并回 `unmapped`；
4. `to_tool_items`：业务键/业务值 → 工具键/工具值（写入前）；
5. 容错：`fieldMap` 为空 / 坏 JSON ⇒ 不抛、按原样（无映射）。

纯函数，不依赖 DB。
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
if os.path.join(REPO, "tools", "ke-core") not in sys.path:
    sys.path.insert(0, os.path.join(REPO, "tools", "ke-core"))

FMAP = {
    "order": ["issue_no", "desc", "verdict"],       # 领域展示顺序
    "items": [
        {"field": "issue_no", "label": "编号"},
        {"field": "desc", "label": "问题"},
        {"field": "verdict", "label": "结论",
         "enum": {"合格": "符合", "不合格": "不符合", "无法判断": "无法判定"}},
    ],
}
SCHEMA = {"order": ["issue_no", "desc", "rule", "verdict", "severity"],
          "labels": {"issue_no": "编号", "desc": "问题描述", "rule": "触发规则",
                     "verdict": "判定", "severity": "严重度"}}
ROWS = [{"issue_no": "P-01", "desc": "缺部署图", "rule": "R1",
         "verdict": "不符合", "severity": "严重"}]


def test_parse_tolerates_empty_and_bad_json():
    import ke_ext_map as M
    for bad in ("", "{}", None, "not-json"):
        m = M.parse(bad)
        assert m["has"] is False and m["order"] == []


def test_business_columns_uses_domain_order():
    import ke_ext_map as M
    pairs = M.business_columns(FMAP, SCHEMA["order"])
    assert [f for f, _ in pairs] == ["issue_no", "desc", "verdict", "rule", "severity"]
    assert dict(pairs)["verdict"] == "结论"
    assert dict(pairs)["rule"] == "", "映射没写的字段用字段名兜底（不含业务名）"


def test_value_conversion_both_ways():
    import ke_ext_map as M
    assert M.to_business_value("verdict", "不符合", FMAP) == ("不合格", True)
    assert M.to_tool_value("verdict", "不合格", FMAP) == ("不符合", True)
    # 未知值：原样透传 + 标未命中
    v, ok = M.to_business_value("verdict", "待定", FMAP)
    assert v == "待定" and ok is False
    v, ok = M.to_tool_value("verdict", "待定", FMAP)
    assert v == "待定" and ok is False


def test_render_table_uses_business_labels_and_enums():
    import ke_ext_map as M
    md, unmapped = M.render_table(ROWS, SCHEMA, FMAP)
    head = md.splitlines()[0]
    assert "编号" in head and "问题" in head and "结论" in head
    assert "判定" not in head, "表头用了工具标签（应为业务列名）"
    assert "不合格" in md and "不符合" not in md, "值没转成业务枚举"
    assert unmapped == []


def test_render_table_reports_unmapped():
    import ke_ext_map as M
    md, unmapped = M.render_table([{"verdict": "待定"}], SCHEMA, FMAP)
    assert "待定" in md
    assert unmapped and unmapped[0].startswith("verdict=")


def test_to_tool_items_maps_keys_and_values():
    import ke_ext_map as M
    items, unmapped = M.to_tool_items(
        [{"编号": "P-01", "问题": "缺部署图", "结论": "不合格"}], FMAP)
    assert items[0]["issue_no"] == "P-01"
    assert items[0]["desc"] == "缺部署图"
    assert items[0]["verdict"] == "不符合"
    assert unmapped == []


def test_to_tool_items_keeps_unknown_values():
    import ke_ext_map as M
    items, unmapped = M.to_tool_items([{"结论": "待定"}], FMAP)
    assert items[0]["verdict"] == "待定"
    assert unmapped == ["verdict=待定"]


# ── 列名归一化（2026-11-01 用户实测：块/模板写业务列名，外部接口只认工具字段）────────────
def test_resolve_columns_accepts_business_labels_and_fields():
    """中文列名 / 工具字段 / 全角逗号 都能归一成字段；顺序按调用方给的顺序。"""
    import ke_ext_map as M
    fields, unknown = M.resolve_columns(SCHEMA, FMAP, "编号,触发规则,问题描述")
    assert fields == ["issue_no", "rule", "desc"], fields
    assert unknown == [], unknown
    fields, unknown = M.resolve_columns(SCHEMA, FMAP, "issue_no，severity")
    assert fields == ["issue_no", "severity"] and unknown == [], (fields, unknown)


def test_resolve_columns_unknown_is_not_fatal():
    """未知列**不抛**（旧版直接 400）：原样回给调用方 warn，其余列照常返回。"""
    import ke_ext_map as M
    fields, unknown = M.resolve_columns(SCHEMA, FMAP, "编号,不存在的列,判定")
    assert fields == ["issue_no", "verdict"], fields
    assert unknown == ["不存在的列"], unknown


def test_resolve_columns_prefers_field_map_labels():
    """同一字段在映射与契约里标签不同 ⇒ **领域映射优先**（问题 vs 问题描述）。"""
    import ke_ext_map as M
    fields, _unknown = M.resolve_columns(SCHEMA, FMAP, "问题")
    assert fields == ["desc"], fields


def test_block_columns_match_table_header():
    """`ref_block.columns` 与 `render_table` 表头**逐字一致**（块 = 这张表的说明书 ✓）。"""
    import ke_ext_map as M
    md, _un = M.render_table(ROWS, SCHEMA, FMAP)
    head = [c.strip() for c in md.splitlines()[0].strip().strip("|").split("|")]
    assert M.block_columns(SCHEMA, FMAP) == head, (M.block_columns(SCHEMA, FMAP), head)
    # 子集：业务列名入参也认，只出这两列（顺序仍按领域顺序）
    md2, _un2 = M.render_table(ROWS, SCHEMA, FMAP, "结论,编号")
    assert md2.splitlines()[0].strip() == "| 编号 | 结论 |", md2
    assert M.block_columns(SCHEMA, FMAP, "结论,编号") == ["编号", "结论"]
