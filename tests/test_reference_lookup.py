#!/usr/bin/env python3
"""P0 待实现：`reference_lookup` 内网可用性（P5 · 4.3）

背景（内网实测）：参考规范填的是可访问 URL，`reference_lookup` 返回
`urlopen error timed out` ✗ —— 无代理/无超时/无白名单。

计划契约（重构后）：
- 配置：`BODHI_HTTP_PROXY` / `BODHI_REF_TIMEOUT`(默认 8s) / `BODHI_REF_HOSTS`(白名单)
- 失败返回**结构化** `{ok:false, reason: timeout|dns|tls|blocked|http_4xx}` ✓
- 技能侧：取不到 ⇒ 依既定口径标「不可得/无法判定」，**不得编造** ✗

验收（P5 落地时改为真断言）：mock 200 / 超时 / 403 三例均返回结构化结果 ✓。
现状：**未实现** ⇒ 跳过（退出码 0 ✓）。
"""
import sys


def main() -> int:
    print("（P5 待实现：reference_lookup 内网超时 4.3 —— 跳过 ✓）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
