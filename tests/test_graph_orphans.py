#!/usr/bin/env python3
"""`graph` 清理的**节点级对账**（2026-11-02 用户口径）。

内网实测：`kinds=graph` 只判"库是否软删"，`没有对应 wiki 页的图节点` 恒为 0 —— 单库操作
判库状态没有意义。改为按 slug 对账：**图上有、该库活页里没有 ⇒ 孤儿**。

本测试只测纯函数 `diff_orphans`（不连库/图）。
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(REPO, "tools", "ke-core"))

import ke_graph as G  # noqa: E402


def test_diff_orphans_both_directions():
    orphans, missing = G.diff_orphans(["a", "b", "c"], ["a", "c", "d"])
    assert orphans == ["b"], orphans          # 图有页无 → 删
    assert missing == ["d"], missing          # 页有图无 → 只提示


def test_diff_orphans_blank_slug_ignored():
    """空/空白 slug 不参与差集（由调用方用 no_slug_nodes 单独统计）。"""
    orphans, missing = G.diff_orphans(["", " ", "a"], ["a"])
    assert orphans == [] and missing == [], (orphans, missing)


def test_diff_orphans_all_orphan_when_no_live_pages():
    """库里的页全删了（或库已软删）⇒ 图上节点全是孤儿。"""
    orphans, missing = G.diff_orphans(["a", "b"], [])
    assert orphans == ["a", "b"] and missing == []


def test_diff_orphans_all_missing_when_graph_empty():
    orphans, missing = G.diff_orphans([], ["a", "b"])
    assert orphans == [] and missing == ["a", "b"]


def test_diff_orphans_none_safe():
    assert G.diff_orphans(None, None) == ([], [])


if __name__ == "__main__":
    for fn in (test_diff_orphans_both_directions, test_diff_orphans_blank_slug_ignored,
               test_diff_orphans_all_orphan_when_no_live_pages,
               test_diff_orphans_all_missing_when_graph_empty, test_diff_orphans_none_safe):
        fn()
        print("✓ %s" % fn.__name__)
