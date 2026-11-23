# analytics 管理台（8781）

> 02 包组件；业务对象映射 / 枚举 / join / filter / 语义层配置的**唯一可视化入口**。
> 不暴露给智能体，不写知识库页面（只调用 `sync_pages` 投影）。

## 1. 启动

```bash
cd 02-mcp-server
cp config/analytics-admin.env.example config/analytics-admin.env   # 若 install 未生成
./start.sh admin
# 浏览器：http://<host>:8781/
```

配置：

```bash
BODHI_ANALYTICS_ADMIN_HOST=0.0.0.0
BODHI_ANALYTICS_ADMIN_PORT=8781
# 当前不启用 token；如需开启（后续版本），设置 BODHI_ANALYTICS_ADMIN_TOKEN
```

数据库连接来自 `config/common.env`。管理台直接读写 `analytics` schema，必须能访问 WeKnora Postgres。

## 2. 安全

- 监听 `0.0.0.0:8781` 时，**必须**用 nginx/VPN/防火墙 ACL 限制来源；
- 不要把 8781 暴露到办公网或公网；
- 当前不启用 token；需要时在 nginx 层加认证；
- 管理台不是 MCP 服务，不注册给智能体。

## 3. 能做什么

- 18 张 `analytics` 配置表：浏览/编辑/新建/删除（按 `information_schema` 自动取列/主键）；
- 含 `fact_table`、`dimension_table`、`scenario_fact`、`scenario_join`、`scenario_filter`；
- “同步 wiki 页面”：填 KB ID + kind + dry_run，调用 `ke_analytics.sync_pages`；
- “映射接口校验”：读取 `v_mapping_interface`，检查页面属性、binding、服务状态、事实字段来源、`scenario_join` 端点。

## 4. 页面同步的边界

只生成四类页：

```text
analytics/fact/<code>              -> analytics:Fact
analytics/dimension/<code>         -> analytics:Dimension
analytics/scenario/<code>          -> analytics:StandardQueryScenario
agent/mcpservice/<service_name>    -> agent:MCPService
```

映射/枚举/join/filter 只供配置与校验，不生成知识页。`v_mapping_interface` 只读。

## 5. 验收

```bash
curl -fsS http://127.0.0.1:8781/ | head
# 页面里能看到 dimension_table；
# 改一条 binding/field_mapping 后，“映射接口校验”应能返回对应问题或通过。
```

## 6. 依赖

- `tools/analytics-admin/server.py` + `index.html`；
- `tools/ke-core`（`ke_analytics` / `ke_db`）；
- `psql` 客户端（容器镜像已内置）。
