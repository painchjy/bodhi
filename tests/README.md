# tests/ —— 重构回归（P0 起）

零依赖 ✓（只用标准库；`pytest` 未安装，内网不可装 ✗）。
风格与 `tools/ke-core/tests/test_graph_first_write.py` 一致：**每个用例 = 独立脚本 + 退出码** ✓。

## 跑法
```bash
python3 tests/run_all.py             # 全部（DB 用例在 DB 不可用时自动跳过 ✓）
python3 tests/run_all.py --list      # 只列用例
python3 tests/test_no_business_semantics.py --update-golden   # 命中下降后固化基线 ✓
```

## 用例清单
| 用例 | 作用 | 依赖 |
|---|---|---|
| `test_tools_contract.py` | MCP 工具 name+schema **快照**（防"粗废弃"重演 ✗） | 静态 |
| `test_tool_dispatch.py` | 每个工具可 dispatch；退役工具只走守卫 ✓ | 静态 |
| `test_never_retire.py` | **治理类工具永不退役**（权威裁决/概念页/改类型/备份/清理）✓ | 静态 |
| `test_no_business_semantics.py` | 铁律①：`tools/**`、`skills/**` 域词命中 **只降不升** ✓ | 静态 |
| `test_ontology_no_instance.py` | 铁律②：`ontology/*.ttl` 不得出现**实例名** ✓ | 静态 |
| `test_export_integrity.py` | 导出包完整性（manifest 计数 == 行数；zip 含图数据 ✓） | 静态 |
| `test_report_structure.py` | 报告 5 节 / ② 7 列 / ③ 末列=规则指纹（P4 改模板驱动时同步改写 ✓） | DB |
| `test_lookup_strategies.py` | **P5 待实现**：组合关键字检索（4.1） | 跳过 |
| `test_reference_lookup.py` | **P5 待实现**：内网 URL 超时结构化（4.3） | 跳过 |

## 基准文件（`tests/golden/`）
- `tools_schema.json` —— 工具契约基准（变更须显式 `--update-golden` 并说明 ✓）
- `biz_terms_baseline.json` —— 域词命中基线（**只许降** ✓，P3 逐项压到 0）

## 与之配套的重构阶段
P0 测试（本目录） → P1 工具回归整合 → P2 server 拆分 → P3 内核通用化 → P4 报告模板化 → P5 四项优化 → P6 规则固化 ✓
