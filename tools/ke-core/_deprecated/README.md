# tools/ke-core/_deprecated（归档，2026-10-23）

这些模块**没有任何活路径引用**（仅被彼此引用，根模块 `ke_rule_review` 无调用方），
内容是**领域专属**的台账/评测流水（报告解析硬编码章节名与列名），已按"域词退场"口径归档：

| 文件 | 原用途 | 为什么归档 |
|---|---|---|
| `ke_rule_review.py` | 规则评审流水 CLI（stats → ledger → 发布） | 无调用方（MCP 工具面没有它） |
| `ke_rule_ledger.py` | 规则工程台账（一规则一页） | 只被 `ke_rule_review` 引用 |
| `ke_rule_stats.py` | 报告解析 → 规则执行统计 | 只被 `ke_rule_ledger` 引用；且硬编码报告章节名/列名 ✗ |
| `ke_rule_copy.py` | 领域库→工作库规则副本同步 | 只被上面两个引用 |

仍在用的：`ke_rule_publish.py`（MCP `rule_publish`）、`ke_rule_check.py`、`ke_rule_eval.py`、`ke_fp.py`。

**恢复方式**：`git mv tools/ke-core/_deprecated/<f>.py tools/ke-core/`（历史与实现都在 git 里）。
