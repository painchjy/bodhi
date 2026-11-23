# analytics 配置手册（02 包 · diag / analytics / 语义层）

> 适用：`02-mcp-server` 已解包；**不需要 03 包的 seed**。
> 三个常驻服务 + 一个初始化组件：
> `bodhi-mcp` 8765 · `agent_diag` 8771 · `analytics-admin` 8781 · `diag-init`（一次性脚本）。
> dbbi gateway 是外部服务（8772，独立包）；it_platform 是外部服务（8770，04 包）。

## 1. 配置

| 文件 | 用途 |
|---|---|
| `config/common.env` | 数据库连接（三服务 + init 共用） |
| `config/mcp.env` | 本体库/概念库/Neo4j/写入开关 |
| `config/agent-diag.env` | agent_diag 端口 8771 |
| `config/analytics-admin.env` | 管理台 0.0.0.0:8781 |
| `config/diag-init.env` | 可选：单独 DDL 账号覆盖 |

首次执行 `install.sh` 会生成 `config/*.env` 并退出；改完 `config/common.env` 的 `BODHI_DB_*` 后重跑。

## 2. 初始化 diag + analytics

```bash
cd 02-mcp-server
./init-db.sh all          # 先 diag，再 analytics
# 或分开：
./init-db.sh diag
./init-db.sh analytics
```

`init-db.sh` 使用 `psql`，不依赖 Python/`ke_db`。顺序固定：

```text
sql/diag/001_schema.sql
sql/diag/002_tool_dimension_seed.sql
sql/analytics/001_schema.sql
sql/analytics/002_seed.sql
sql/analytics/005_scenario_diag_seed.sql
sql/analytics/006_diag_fact_mapping.sql
sql/analytics/007_diag_metric_seed.sql
sql/analytics/008_semantic_refactor.sql
sql/analytics/009_semantic_views.sql
sql/analytics/010_scenario_fact.sql
sql/analytics/011_mapping_interface.sql
sql/analytics/012_fact_table_join.sql
sql/analytics/013_dimension_table.sql
sql/analytics/014_semantic_test_scenarios.sql
```

**012/013 不要漏**：`fact_table` 和 `dimension_table` 是语义层扫描场景 SQL 的物理依据。
初始化后应有：18 张配置表 + 8 个视图；`dimension_table` 至少 4 行（两个工具分类维度 × base/view）。

## 3. 服务启动

```bash
./start.sh mcp      # bodhi-mcp 8765
./start.sh diag     # agent_diag 8771
./start.sh admin    # analytics-admin 8781
./start.sh all
./status.sh
```

容器模式同一镜像；裸机可用包根 `bodhi2-*.service`（`bodhi2-mcp` / `bodhi2-agent-diag` / `bodhi2-analytics-admin`）。
管理台固定在 **0.0.0.0:8781**，当前不启用 token；必须用 nginx/VPN/ACL 限制来源。

## 4. 语义层 / mapping

- 事实/维度/指标准则仍以 `analytics` 配置表为真源；
- 业务对象映射与枚举在 `analytics-admin`（8781）维护；
- `bodhi-mcp`（8765）的 `scenario_semantic_validate/plan/apply` 负责从标准取数场景提取语义层；
- 管理台“同步 wiki 页面”调用 `ke_analytics.sync_pages`，投影 Fact / Dimension / Scenario / active MCP 服务页；
- `v_mapping_interface` 是只读校验视图，不是第二套映射真源。

## 5. 本体 / 知识库

本体和知识库**不通过本包 seed 发布**：
- 交付包 `03-manual/ontology/` 带 `*完整版.ttl`；
- 由用户在目标环境导出/导入知识库，或按 TTL 重新编译/投影；
- 本包不写 `seed/`、不写 ontology 页面。

语义提取任务页 `bpmn/task/从标准取数场景提取语义层` 属于知识库页面，按同一口径由用户导出/导入。

## 6. 验收

```bash
curl -fsS http://127.0.0.1:8765/mcp -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}'
curl -fsS http://127.0.0.1:8771/
curl -fsS http://127.0.0.1:8781/ | head
psql "$PSQL_URL" -c "SELECT count(*) FROM analytics.dimension_table;"   # >=4
```

WeKnora 侧需要注册两个 MCP 服务：
- `bodhi_ontology` → `http://bodhi-mcp:8765/mcp`
- `agent_diag` → `http://bodhi-agent-diag:8771/mcp`
