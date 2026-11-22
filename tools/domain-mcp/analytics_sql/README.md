# analytics-sql · 只读自助分析 MCP

三个工具：

- `list_tables(service_id?, keyword?)`
- `describe_table(table, schema?, service_id?)`
- `run_select(sql, max_rows=200, service_id?)`

`run_select` 只允许单条 `SELECT`/`WITH`，拒绝 DML/DDL，限制 200/1000 行；目标服务由
`analytics.mcp_service` 的 active 行决定（当前 `agent_diag` / PostgreSQL）。

启动：

```bash
python tools/domain-mcp/analytics_sql/server.py --host 0.0.0.0 --port 8771
```

WeKnora 侧注册：

```text
mcp_services.name = agent_diag
mcp_services.url  = http://host.docker.internal:8771/mcp
transport_type    = http-streamable
```

并把该 service id 加入智能体的 `config.mcp_services`。
