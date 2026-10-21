"""L2 历史差集（P1 起点 ✓）：**在用的工具**里，哪些**从没被真实调用覆盖**过。

为什么需要：日志（1054 次调用 / 13 天）是唯一的"真实使用证据"。某个工具在注册表里、
但从没出现在日志 ⇒ 它的参数/回执**从未被真实场景验证**，是**造例缺口**（不是 bug ✗）。

口径（结构判定 ✓ 无领域词）：
- 覆盖 = `tests/golden/mined_index.json` 里出现过该工具名（任意形态 ✓）
- 未覆盖清单 = 注册表 − 覆盖集；写入 `tests/golden/tools_no_history.json`（显式承认）
- 断言：清单必须与 golden **一致** ⇒ 新工具"悄悄上线且无历史"会立刻红 ✓（逼着造例）
"""
import json
import os

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
GOLDEN = os.path.join(HERE, "golden")
NO_HIST = os.path.join(GOLDEN, "tools_no_history.json")
INDEX = os.path.join(GOLDEN, "mined_index.json")


def _registry() -> set:
    import _helpers as H
    return set(H.tool_definitions()) | set(H.dispatcher_names())


def _history() -> set:
    if not os.path.isfile(INDEX):
        pytest.skip("未生成 mined_index.json（先跑 python3 tests/mine_logs.py）")
    d = json.load(open(INDEX, encoding="utf-8"))
    return {c["tool"] for c in (d.get("cases") or [])}


def test_tools_without_history_matches_golden():
    reg, hist = _registry(), _history()
    missing = sorted(reg - hist)
    if not os.path.isfile(NO_HIST):
        os.makedirs(GOLDEN, exist_ok=True)
        json.dump(missing, open(NO_HIST, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        pytest.skip("首次生成 tools_no_history.json（%d 个工具无历史）——复核后提交 ✓" % len(missing))
    recorded = json.load(open(NO_HIST, encoding="utf-8"))
    new = sorted(set(missing) - set(recorded))
    gone = sorted(set(recorded) - set(missing))
    assert not new, ("新工具**没有任何真实调用历史** ⇒ 必须造例（不能默默上线 ✗）：%s\n"
                     "若确认先不上线，请把它加进 tests/golden/tools_no_history.json ✓" % new)
    assert not gone, ("这些工具现在有历史了 ⇒ 从 tools_no_history.json 移除 ✓：%s" % gone)


def test_no_history_ratio_reported():
    """覆盖率只做**报告**（不设硬阈值 ✗）：数字进 CI 输出，便于盯"假接口"增长 ✓"""
    reg, hist = _registry(), _history()
    if not reg:
        pytest.skip("注册表解析为空（server.py 结构变了？）")
    cov = 100.0 * len(reg & hist) / len(reg)
    print("\n工具历史覆盖率：%d/%d = %.1f%%（无历史：%s）"
          % (len(reg & hist), len(reg), cov, sorted(reg - hist)))
    assert 0.0 <= cov <= 100.0
