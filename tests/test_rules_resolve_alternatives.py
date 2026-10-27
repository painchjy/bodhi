"""`rules_resolve` 的 `alternatives`（规则集歧义）单测 —— 2026-10-27。

用户口径：`spec=policy:` 时，把**策略闭包内各子策略**各自解析出的规则集与本次结果比对，
**只有集合（hash）不同才回** `[{via, rules, hash, extra, missing}]`；非 policy ⇒ 空数组。

覆盖：
  ① 子策略规则集 ⊂ 总集（少规则）⇒ 回 1 条，`missing` 指向总策略独有的规则；
  ② 子策略与总集**一致** ⇒ 不回（不制造噪声）；
  ③ 单策略闭包 / `direct:` ⇒ 不回。

网络层（neo4j / PG / 规则页）全部换成替身，用例**不连任何库**。
跑法：`python3 tests/test_rules_resolve_alternatives.py`（`tests/run_all.py` 也会跑到）。
"""
import contextlib
import os
import sys
import traceback
import types

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "tools", "ke-core"))

import ke_review  # noqa: E402

ROOT = {"slug": "pol-arch", "name": "部署架构设计原则"}
SUB = {"slug": "pol-net", "name": "网络架构要求"}


@contextlib.contextmanager
def _patched(closure: dict, by_pol: dict, root: dict = ROOT):
    """把 rules_resolve 依赖的四层全换成替身（策略清单 / 闭包 / 策略规则边 / 规则契约）。"""
    class _Neo(types.ModuleType):
        @staticmethod
        def query(cypher, params=None, timeout=8):      # `_pols()` 走图查优先
            return [{"slug": root["slug"], "t": root["name"]}]

    old_neo = sys.modules.get("ke_neo4j")
    sys.modules["ke_neo4j"] = _Neo("ke_neo4j")
    saved = []

    def _set(obj, name, value):
        saved.append((obj, name, getattr(obj, name)))
        setattr(obj, name, value)

    try:
        _set(ke_review.ke_db, "resolve_kb_id", lambda kb_id="": ("kb-t", "测试库", 0))
        _set(ke_review, "_policy_closure",
             lambda kb, slug: closure.get(slug) or [{"slug": slug, "name": ""}])
        _set(ke_review, "_rule_slugs_of_policies",
             lambda kb, pols: {k: list(v) for k, v in by_pol.items()})
        _set(ke_review, "_rule_slugs_pg", lambda kb, slug: [])
        _set(ke_review, "_rule_contracts",
             lambda kb, slugs, name="": [{"slug": s, "title": s.upper(), "needs_extract": False,
                                          "ruleHash": "h-" + s} for s in slugs])
        yield
    finally:
        for obj, name, value in reversed(saved):
            setattr(obj, name, value)
        if old_neo is None:
            sys.modules.pop("ke_neo4j", None)
        else:
            sys.modules["ke_neo4j"] = old_neo


def test_child_policy_yields_alternatives():
    """① 双路径：子策略少一条规则 ⇒ 回 alternatives（"怎么少"一目了然）。"""
    with _patched({"pol-arch": [ROOT, SUB], "pol-net": [SUB]},
                  {"pol-arch": ["rule-a1"], "pol-net": ["rule-n1", "rule-n2"]}):
        out = ke_review.rules_resolve("", "policy:部署架构")
    assert out["counts"]["rules"] == 3, out["counts"]
    alts = out["alternatives"]
    assert len(alts) == 1, alts
    assert alts[0]["via"] == "policy:网络架构要求", alts[0]
    assert alts[0]["rules"] == 2, alts[0]
    assert alts[0]["missing"] == ["rule-a1"], alts[0]          # 走子策略会漏掉这条
    assert alts[0]["extra"] == [], alts[0]
    assert len(alts[0]["hash"]) == 16, alts[0]


def test_same_rule_set_has_no_alternatives():
    """② 集合相同（hash 相同）⇒ **不回**，避免每轮都制造噪声。"""
    with _patched({"pol-arch": [ROOT, SUB], "pol-net": [SUB]},
                  {"pol-arch": ["rule-a1"], "pol-net": ["rule-a1"]}):
        out = ke_review.rules_resolve("", "policy:部署架构")
    assert out["alternatives"] == [], out["alternatives"]


def test_single_policy_and_direct_are_empty():
    """③ 单路径（闭包只有自己）/ 非 policy spec ⇒ 空数组（字段恒在，调用方不用判 None）。"""
    with _patched({"pol-arch": [ROOT]}, {"pol-arch": ["rule-a1"]}):
        assert ke_review.rules_resolve("", "policy:部署架构")["alternatives"] == []
        assert ke_review.rules_resolve("", "direct:rule-a1")["alternatives"] == []


def main() -> int:
    tests = [(n, f) for n, f in sorted(globals().items())
             if n.startswith("test_") and callable(f)]
    bad = 0
    for name, fn in tests:
        try:
            fn()
            print("  ✓ %s" % name)
        except Exception as exc:  # noqa: BLE001
            bad += 1
            print("  ✗ %s：%s" % (name, exc))
            traceback.print_exc()
    print("alternatives 用例：%d 个 ｜ 通过 %d ｜ 失败 %d" % (len(tests), len(tests) - bad, bad))
    return 0 if not bad else 1


if __name__ == "__main__":
    sys.exit(main())
