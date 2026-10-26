"""P5 · 内网问题 4.1/4.4 —— **检索策略 v2 用例**（表驱动，全部走 MCP）。

用户口径（2026-10-22）：
- 先认**本体类**，再定范围（"流程/活动/任务"是类，不是普通关键字）；
- 无其它关键字 ⇒ 列该类全部实例；
- 多关键字 AND 大概率空 ⇒ **一次调用内**降级 OR + 明确标注；
- 空结果必须带**诊断 + 可执行建议 + next**（不让智能体反复试错）；
- 每条建议都必须**可直接调用**（`tool` + 完整 `args`）。

用例定义：`tests/cases/lookup_cases.json`（先写用例，再改实现 ✓）
跑法：`python3 -m pytest tests/test_lookup_strategy.py`（MCP/DB 不可用 ⇒ skip）
"""
import json
import os

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
CASES = os.path.join(HERE, "cases", "lookup_cases.json")


def _load() -> dict:
    with open(CASES, encoding="utf-8") as f:
        return json.load(f)


_DATA = _load()


def _get(obj, path: str):
    """点号路径取值（`plan.scope_classes`）。"""
    cur = obj
    for part in path.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return None
        cur = cur[part]
    return cur


def _check(expect: dict, resp: dict) -> list:
    problems = []
    for p in expect.get("present") or []:
        if _get(resp, p) in (None, "", [], {}):
            problems.append("缺 %s" % p)
    for p, want in (expect.get("equals") or {}).items():
        got = _get(resp, p)
        if got != want:
            problems.append("%s=%r（期望 %r）" % (p, got, want))
    for p, n in (expect.get("min_len") or {}).items():
        v = _get(resp, p)
        try:
            size = len(v)
        except TypeError:
            size = 0
        if size < int(n):
            problems.append("%s 长度 %d < %d" % (p, size, n))
    if expect.get("any_true"):
        if not any(_get(resp, p) for p in expect["any_true"]):
            problems.append("这些都不成立：%s" % expect["any_true"])
    return problems


@pytest.mark.parametrize("case", _DATA["cases"], ids=[c["id"] for c in _DATA["cases"]])
def test_lookup_strategy_cases(mcp, kbs, case):
    args = dict(case["args"])
    args.setdefault("kb_id", kbs["domain"])
    # 2026-10-25（P2）：`.env` 生产口径已切 `abtest`（随机分臂）⇒ 本用例断言的是**提示面内容**，
    # 必须显式要提示，否则会随分臂间歇性红（不是回归 ✗）。
    args.setdefault("ontology_hint", True)
    resp = mcp.call("knowledge_lookup", args)
    problems = _check(case["expect"], resp)
    assert not problems, ("%s %s ✗ %s\n回执=%s"
                          % (case["id"], case["why"], problems,
                             json.dumps(resp, ensure_ascii=False)[:900]))
    # 硬约束：每条建议必须可直接执行（省来回的关键 ✓）
    for s in (resp.get("suggestions") or []):
        assert s.get("tool") and isinstance(s.get("args"), dict), "建议不可执行 ✗：%s" % s
    # 兼容约束：关键词模式必须仍回 hits/counts（老调用方不受影响）
    if args.get("mode") in (None, "", "keyword"):
        assert isinstance(resp.get("hits"), list), resp
        assert isinstance((resp.get("counts") or {}).get("by_source"), dict), resp
