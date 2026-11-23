# diag 配置手册（02 包 · agent_diag 8771）

> 适用：`02-mcp-server` 已解包；本组件不是独立包，和 `bodhi-mcp` 同一个安装器。
> 端口：**8771**（`agent_diag`），不是 dbbi gateway 的 8772，也不是 it_platform 的 8770。

## 1. 组件

| 项 | 值 |
|---|---|
| 名称 | `agent_diag`（WeKnora `mcp_services.name`） |
| 代码 | `tools/domain-mcp/analytics_sql/server.py` |
| 端口 | `8771` |
| 工具 | `list_tables` / `describe_table` / `run_select` |
| 配置 | `config/common.env` + `config/agent-diag.env` |
| 启动 | `./start.sh diag`（或 `docker compose --profile diag up -d`） |
| 健康检查 | `curl -fsS http://127.0.0.1:8771/` |

`agent_diag` 依赖本包的 `tools/ke-core/ke_db.py`；**只在 02 包**，04 包不再带它。

## 2. 初始化 diag

```bash
cd 02-mcp-server
./init-db.sh diag
# 实际执行：
#   sql/diag/001_schema.sql
#   sql/diag/002_tool_dimension_seed.sql
# 可选：psql ... -f sql/diag/003_examples.sql
```

初始化脚本幂等（`CREATE ... IF NOT EXISTS` / `ON CONFLICT`），重复执行安全。

## 3. 数据库账号

建议给 `agent_diag` 单独只读账号（不要用 postgres）：

```sql
CREATE ROLE bodhi_readonly LOGIN PASSWORD '<强口令>';
GRANT CONNECT ON DATABASE "WeKnora" TO bodhi_readonly;
GRANT USAGE ON SCHEMA diag TO bodhi_readonly;
GRANT SELECT ON ALL TABLES IN SCHEMA diag TO bodhi_readonly;
ALTER DEFAULT PRIVILEGES IN SCHEMA diag GRANT SELECT ON TABLES TO bodhi_readonly;
GRANT SELECT ON analytics.mcp_service TO bodhi_readonly;
```

`run_select` 只允许单条 `SELECT`/`WITH`，拒绝 DML/DDL；行数上限 200/1000。

## 4. 启动与配置

`config/agent-diag.env`：

```bash
ANALYTICS_SQL_PORT=8771
```

数据库连接统一在 `config/common.env`，不要在组件配置里重复口令。

```bash
./start.sh diag
./status.sh
curl -fsS http://127.0.0.1:8771/    # {"ok":true,"service":"analytics-sql-mcp"}
```

## 5. 在 WeKnora 注册

```sql
INSERT INTO mcp_services
(id, tenant_id, name, description, enabled, transport_type, url,
 headers, is_builtin, created_at, updated_at)
VALUES
('a7c1f0d2-1b2e-4f3a-9c4d-b0d100000002', 10000, 'agent_diag',
 'bodhi 自助分析只读 SQL MCP', true, 'http-streamable',
 'http://bodhi-agent-diag:8771/mcp', '{}'::jsonb, false, now(), now())
ON CONFLICT (id) DO UPDATE SET url=EXCLUDED.url, enabled=true, updated_at=now();
```

- 容器部署 URL 用 `http://bodhi-agent-diag:8771/mcp`；
- 宿主机/裸机用 `http://<host.docker.internal 或宿主机IP>:8771/mcp`；
- 再把该服务加入智能体的 `config.mcp_services`（不是 `allowed_tools`）。

`analytics.mcp_service` 里的 `svc_postgresql` 是 analytics 内部“数据服务”配置，和 WeKnora 的 `mcp_services` 不是同一张表。

## 6. 验收

1. `curl -fsS http://127.0.0.1:8771/` 返回 200；
2. MCP `tools/list` 能看到 `list_tables` / `describe_table` / `run_select`；
3. `run_select(service_id="svc_postgresql", sql="SELECT 1 AS ok")` 成功；
4. WeKnora 新会话 `tools_ready` 中出现 `mcp_agent_diag_*`。
