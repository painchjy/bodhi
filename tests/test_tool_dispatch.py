#!/usr/bin/env python3
"""P0 契约：注册工具必须可 dispatch；退役工具必须被**守卫前置拦截**；两表不得交集

判定（2026-10-21 修正）：
1. 注册表每个 name ⇒ `dispatcher_names()` 覆盖 ✓
2. 守卫 `if name in _RETIRED_TOOLS:` 必须**先于**任何 `if name == "…":` ✓
   （退役工具的旧实现分支是**死代码**，留给 HTTP/CLI 复用是允许的 ✓ —— 不再断言"无分支"✗）
3. 注册表 ∩ 退役表 = 空 ✓（本轮实测抓到 `kb_import`/`retag_rollback` 交集 ✗ 已修）

跑法：`python3 tests/test_tool_dispatch.py`
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _helpers as H  # noqa: E402


def main() -> int:
    reg = set(H.tool_definitions())
    disp = H.dispatcher_names()
    ret = H.retired_tools()
    src = H.read(H.SERVER)
    i_guard = src.find("if name in _RETIRED_TOOLS:")
    no_disp = sorted(reg - disp)
    both = sorted(reg & ret)
    # **精准断言**（2026-10-21）：守卫之前的实现分支里**不得出现退役工具** ✓
    #   （守卫排在部分分支之后本身无害 ✓；只有"退役工具被前面的分支先接走"才是 bug ✗）
    before = re.findall(r'if name == "([a-z_0-9]+)"', src[:i_guard]) if i_guard > 0 else []
    shadowed = sorted(set(before) & ret)
    checks = {
        "注册工具 %d 个全部有 dispatch ✓" % len(reg): not no_disp,
        "守卫存在 ✓": i_guard > 0,
        "守卫之前的 %d 个分支里无退役工具 ✓" % len(before): not shadowed,
        "注册表与退役表无交集 ✓": not both,
    }
    if no_disp:
        print("缺 dispatch：%s" % no_disp)
    if both:
        print("既注册又退役（守卫会拦掉 ⇒ 实际不可用 ✗）：%s" % both)
    if shadowed:
        print("退役工具被前面的分支先接走 ✗：%s" % shadowed)
    return H.report(checks, "dispatch")


if __name__ == "__main__":
    sys.exit(main())
