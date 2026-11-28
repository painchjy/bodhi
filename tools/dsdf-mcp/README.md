# dsdf-mcp（DWS JDBC 只读分析服务）

`dsdf-mcp` 独立提供与 `analytics_sql` 相同的三个工具：

- `list_tables(service_id?, keyword?)`
- `describe_table(table, schema?, service_id?)`
- `run_select(sql, max_rows=200, service_id?)`

工具名、入参、成功回执结构、只读 SQL 校验及最多 1000 行的限制保持兼容。目标数据库来自本服务自己的配置，不访问 Bodhi/WeKnora PG 的 `analytics.mcp_service`。

## 连接边界

不复用 `tools/ke-core/ke_db.py`：它通过 `psql` 连接 Bodhi/WeKnora 数据库。`dsdf-mcp` 直接使用 JayDeBeApi 和 PostgreSQL JDBC 驱动，并在只读事务中执行查询。

配置项在 `/etc/dsdf-mcp.env`（模板见 `dsdf-mcp.env.example`）：

| 变量 | 说明 |
|---|---|
| `DSDF_DB_HOST` / `DSDF_DB_PORT` / `DSDF_DB_NAME` | DWS 地址、端口、数据库名 |
| `DSDF_DB_USER` / `DSDF_DB_PASSWORD` | DWS 账号；口令不入库 |
| `DSDF_JDBC_DRIVER` | 默认 `org.postgresql.Driver` |
| `DSDF_JDBC_JAR` | PostgreSQL JDBC JAR 的绝对路径；JAR 由现场提供，不随包发布 |
| `DSDF_SERVICE_ID` / `DSDF_SERVICE_NAME` / `DSDF_SERVICE_SCOPE` | 兼容回执中的 service 元数据 |
| `DSDF_MCP_HOST` / `DSDF_MCP_PORT` | 监听地址，默认 `0.0.0.0:8772` |

`service_id` 参数仍保留；固定连接只接受配置中的 `DSDF_SERVICE_ID`，不用于动态选择数据库。`list_tables` 保持原有 analytics/diag schema 范围。

## 依赖与部署

需要支持 JayDeBeApi 的 Python、Java/JVM、`JayDeBeApi==1.2.3` 和现场提供的 PostgreSQL JDBC 42.5.1 JAR。交付包不含 JDBC JAR、账号或口令。依赖安装需能访问 pip 镜像/本地 wheel。

打包：

```bash
python3 tools/delivery/pack_dsdf.py --version 20261127
```

安装前编辑包内 `deploy/delivery/dsdf-mcp/dsdf-mcp.env.example`，填写独立的 DWS 连接参数及 JAR 路径，然后按 `deploy/delivery/dsdf-mcp/README.md` 安装。服务的 WeKnora URL 为 `http://<host>:8772/mcp`。

本地开发启动：

```bash
python3 tools/dsdf-mcp/server.py --host 0.0.0.0 --port 8772
```
