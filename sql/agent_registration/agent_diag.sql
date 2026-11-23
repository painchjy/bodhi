-- agent_diag（8771）：bodhi 自助分析只读 SQL MCP
-- 装前替换：__AGENT_DIAG_URL__（容器 DNS 用 http://bodhi-agent-diag:8771/mcp）
INSERT INTO mcp_services
(id, tenant_id, name, description, enabled, transport_type, url,
 headers, is_builtin, created_at, updated_at)
VALUES
('a7c1f0d2-1b2e-4f3a-9c4d-b0d100000002', 10000, 'agent_diag',
 'bodhi 自助分析只读 SQL MCP（list_tables/describe_table/run_select）', true,
 'http-streamable', '__AGENT_DIAG_URL__', '{}'::jsonb, false, now(), now())
ON CONFLICT (id) DO UPDATE
SET name = EXCLUDED.name, description = EXCLUDED.description,
    enabled = true, transport_type = EXCLUDED.transport_type,
    url = EXCLUDED.url, updated_at = now();
