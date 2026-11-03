#!/usr/bin/env python3
"""`audit_purge` 的 kinds 口径回归（2026-11-02 简化：**只允许单库**）。

事故背景
--------
内网 `audit_purge(kb_id=<活库>, kinds="all")` 只清了「软删库残留」，目标活库一条没清：
因为 `all` 里含跨库动作 `deleted_kb_residue`，该分支扫**全部软删库**并**提前 return**。

本测试锁住新口径：
- `all` = `pages/rows/edges/graph`（**单库**，不含 `init`）；
- `init` 必须显式（清空该库 wiki+图，保留 index）；
- 旧名兼容映射；`deleted_kb_residue` **直接报错**（跨库动作已取消）；
- `KIND_HELP` 覆盖所有 kind（工具回执要能解释每个值）。
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(REPO, "tools", "ke-core"))

import ke_audit as A  # noqa: E402


def test_all_is_single_kb_and_excludes_init():
    assert A._normalize_kinds("all") == ["edges", "graph", "pages", "rows"]
    assert "init" not in A._normalize_kinds("all")


def test_init_is_explicit():
    assert A._normalize_kinds("init") == ["init"]
    assert A._normalize_kinds("all,init") == ["edges", "graph", "init", "pages", "rows"]


def test_alias_mapping():
    """旧名一个版本兼容（含语义已改单库的 orphan_graph_nodes）。"""
    cases = {
        "no_source_pages": "pages", "deleted_source_pages": "pages", "mixed_source_refs": "pages",
        "soft_deleted_rows": "rows", "orphan_revisions": "rows",
        "dangling_edges": "edges", "dup_edges": "edges",
        "orphan_graph_nodes": "graph",
        "init_wiki": "init", "init_graph": "init",
    }
    for old, new in cases.items():
        assert A._normalize_kinds(old) == [new], (old, A._normalize_kinds(old))


def test_deleted_kb_residue_rejected():
    """跨库动作已取消：**报错**并指路 init（不静默映射，避免"名字还在、行为变了"）。"""
    for bad in ("deleted_kb_residue", "all,deleted_kb_residue"):
        try:
            A._normalize_kinds(bad)
        except ValueError as exc:
            msg = str(exc)
            assert "已取消" in msg and "init" in msg, msg
        else:
            raise AssertionError("应当拒绝：%s" % bad)


def test_unknown_kind_rejected():
    try:
        A._normalize_kinds("whatever")
    except ValueError as exc:
        assert "未知 kind" in str(exc), exc
    else:
        raise AssertionError("未知 kind 应当报错")


def test_kind_help_covers_all():
    missing = [k for k in A.ALL_KINDS if k not in A.KIND_HELP]
    assert not missing, "KIND_HELP 缺：%s" % missing
    assert A.LOCAL_KINDS == ("pages", "rows", "edges", "graph")


if __name__ == "__main__":
    for fn in (test_all_is_single_kb_and_excludes_init, test_init_is_explicit,
               test_alias_mapping, test_deleted_kb_residue_rejected,
               test_unknown_kind_rejected, test_kind_help_covers_all):
        fn()
        print("✓ %s" % fn.__name__)
