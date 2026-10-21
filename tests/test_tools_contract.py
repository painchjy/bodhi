#!/usr/bin/env python3
"""P0 契约：MCP 工具注册表快照（name + inputSchema）

口径：`tests/golden/tools_schema.json` 是**契约基准** ✓
- 首次运行自动生成（记录当前 31 个工具的 schema）；
- 之后任何**增删改**都必须显式 `--update-golden` 并说明（防止"粗废弃"重演 ✗）。

跑法：`python3 tests/test_tools_contract.py [--update-golden]`
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _helpers as H  # noqa: E402


def main() -> int:
    now = H.tool_definitions()
    golden, created = H.load_or_init_golden("tools_schema.json", now, H.update_golden_flag())
    checks = {"工具数量 = %d（基准 %d）" % (len(now), len(golden)): True}
    if created:
        print("⚠️ 已生成基准：tests/golden/tools_schema.json（%d 个工具）" % len(now))
        return 0
    added = sorted(set(now) - set(golden))
    removed = sorted(set(golden) - set(now))
    changed = sorted(n for n in set(now) & set(golden) if now[n] != golden[n])
    checks["无新增工具"] = not added
    checks["无删除工具"] = not removed
    checks["inputSchema 无漂移"] = not changed
    if added:
        print("新增：%s" % added)
    if removed:
        print("删除：%s" % removed)
    if changed:
        print("schema 变化：%s" % changed)
    return H.report(checks, "工具契约")


if __name__ == "__main__":
    sys.exit(main())
