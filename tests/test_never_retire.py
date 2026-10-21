#!/usr/bin/env python3
"""P0 契约：治理类工具**永不退役**（本轮教训：`context_authority_apply` 被误退役 ✗）

NEVER_RETIRE 清单（任一进入 `_RETIRED_TOOLS` ⇒ 失败）：
- 权威/副本裁决：`context_authority_apply`
- 概念页：`context_concept_apply` / `context_concept_rollback`
- 实例改类型（前端在用）：`retag_preview` / `retag_apply` / `retag_rollback`
- 备份/恢复：`kb_export` / `kb_import`
- 巡检清理：`audit_purge`

跑法：`python3 tests/test_never_retire.py`
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _helpers as H  # noqa: E402

NEVER_RETIRE = {
    "context_authority_apply",
    "context_concept_apply", "context_concept_rollback",
    "retag_preview", "retag_apply", "retag_rollback",
    "kb_export", "kb_import",
    "audit_purge",
}


def main() -> int:
    reg = set(H.tool_definitions())
    ret = H.retired_tools()
    wrong = sorted(NEVER_RETIRE & ret)
    missing = sorted(NEVER_RETIRE - reg - ret)
    checks = {
        "治理类工具未被退役 ✓": not wrong,
        "治理类工具都在注册表 ✓": not missing,
    }
    if wrong:
        print("被误退役：%s" % wrong)
    if missing:
        print("缺失（既未注册也未退役）：%s" % missing)
    return H.report(checks, "never-retire")


if __name__ == "__main__":
    sys.exit(main())
