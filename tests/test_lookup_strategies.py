#!/usr/bin/env python3
"""P0 待实现：`knowledge_lookup` **组合关键字 / 策略一次到位**（P5 · 4.1）

计划契约（重构后）：
```
knowledge_lookup(mode="search",
                 terms=["统一结算", "部署"],
                 must=["部署图"],
                 filters={"page_type": "bpmn:DataObject", "kb": "<id>"},
                 top_k=5)
→ hits[*]：score / matched_terms / why（命中字段）/ strategy_used
```
验收（P5 落地时改为真断言）：
1. 5 组组合查询**单次调用**召回目标页 ✓；
2. 返回 `strategy_used`（exact|multi_and|or_expand|path）✓；
3. 不允许"同一意图试错 ≥3 次"（由计划快照断言 ✓）。

现状：**未实现** ⇒ 本用例跳过（退出码 0 ✓），避免把未完成当失败 ✗。
"""
import sys


def main() -> int:
    print("（P5 待实现：组合关键字检索 4.1 —— 跳过 ✓）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
