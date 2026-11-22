-- ============================================================
-- 自助取数分析 · 种子（示例口径，可按环境覆盖）
--
-- 覆盖用户 2026-11-21 的确认：
--   * 两个事实：AgentTurnFact / AgentToolCallFact
--   * 两个工具分类维度实例：2 层工具分类 / 1 层工具分类
--     两个事实都挂这两个维度，因此「工具分类」有 2 个可选维度
--   * 基础指标与派生指标分表；派生指标带 pseudo/SQL 计算逻辑
--   * 数据库类型示例：OceanBase / GaussDB / 河图(hetu) / PostgreSQL / MySQL
--
-- 注意：物理表名（diag.*）只是示例；接入时按实际库表改 field_mapping。
-- ============================================================

BEGIN;

-- ---------- 1. 维度 ----------
INSERT INTO analytics.dimension (dimension_id, code, name, description, member_key, status) VALUES
('dim_tool_category_2level', 'tool_category_2level', '工具分类（2层）',
 '工具大类 → 工具小类；用于需要下钻到小类的工具使用分析。', 'tool_code', 'active'),
('dim_tool_category_1level', 'tool_category_1level', '工具分类（1层）',
 '单层工具分类；用于只看大类汇总的工具使用分析。', 'tool_code', 'active'),
('dim_agent', 'agent', 'Agent', '发起调用的业务智能体。', 'agent_name', 'active'),
('dim_model', 'model', '模型', '本轮使用的模型。', 'model_id', 'active'),
('dim_session', 'session', '会话', '会话/会话分页。', 'session_id', 'active'),
('dim_time', 'time', '时间', '事件日期与小时。', 'event_time', 'active')
ON CONFLICT (dimension_id) DO UPDATE
SET code = EXCLUDED.code, name = EXCLUDED.name, description = EXCLUDED.description,
    member_key = EXCLUDED.member_key, status = EXCLUDED.status;

-- ---------- 2. 维度层级 ----------
INSERT INTO analytics.dimension_level (dimension_id, level_no, level_name, attribute_code, description) VALUES
('dim_tool_category_2level', 1, '问题大类', 'problem_l1_name', '第一层：内容安全 / 知识规范 / 运行控制。'),
('dim_tool_category_2level', 2, '工具分类', 'category_l2_name', '第二层：工具分类（大类下的细分）。'),
('dim_tool_category_1level', 1, '工具分类', 'tool_category', '单层：直接按工具分类汇总。'),
('dim_agent', 1, 'Agent', 'agent_name', '业务智能体名称。'),
('dim_model', 1, '模型', 'model_name', '模型名称。'),
('dim_session', 1, '会话', 'session_id', '会话标识。'),
('dim_time', 1, '日期', 'event_date', '事件日期。'),
('dim_time', 2, '小时', 'event_hour', '事件小时（0-23）。')
ON CONFLICT (dimension_id, level_no) DO UPDATE
SET level_name = EXCLUDED.level_name, attribute_code = EXCLUDED.attribute_code,
    description = EXCLUDED.description;

-- ---------- 3. 维度属性 ----------
INSERT INTO analytics.dimension_attribute
(dimension_id, attribute_code, name, data_type, is_enum, description, sort_order) VALUES
('dim_tool_category_2level', 'problem_l1_name', '问题大类', 'text', false, '第一层分类。', 1),
('dim_tool_category_2level', 'category_l2_name', '工具分类', 'text', false, '第二层分类。', 2),
('dim_tool_category_1level', 'tool_category', '工具分类', 'text', false, '单层分类。', 1),
('dim_agent', 'agent_name', 'Agent', 'text', false, '业务智能体名称。', 1),
('dim_model', 'model_name', '模型', 'text', false, '模型名称。', 1),
('dim_session', 'session_id', '会话', 'text', false, '会话标识。', 1),
('dim_time', 'event_date', '日期', 'date', false, '事件日期。', 1),
('dim_time', 'event_hour', '小时', 'integer', false, '事件小时。', 2)
ON CONFLICT (dimension_id, attribute_code) DO UPDATE
SET name = EXCLUDED.name, data_type = EXCLUDED.data_type, is_enum = EXCLUDED.is_enum,
    description = EXCLUDED.description, sort_order = EXCLUDED.sort_order;

-- ---------- 4. 事实 ----------
INSERT INTO analytics.fact (fact_id, code, name, description, grain, status) VALUES
('fact_agent_turn', 'agent_turn', 'AgentTurnFact',
 'Agent 轮次事实：一次用户请求/智能体轮次一行。', 'session_id + turn_id', 'active'),
('fact_agent_tool_call', 'agent_tool_call', 'AgentToolCallFact',
 'Agent 工具调用事实：一次工具调用一行。', 'session_id + turn_id + tool_call_id', 'active')
ON CONFLICT (fact_id) DO UPDATE
SET code = EXCLUDED.code, name = EXCLUDED.name, description = EXCLUDED.description,
    grain = EXCLUDED.grain, status = EXCLUDED.status;

-- ---------- 5. 事实 → 维度（语义键；物理映射不在本表）----------
-- 工具分类只属于工具调用事实；轮次事实不挂工具分类。
INSERT INTO analytics.fact_dimension
(fact_id, dimension_id, key_code, role, fact_key, dimension_key, join_type, sort_order) VALUES
('fact_agent_turn', 'dim_agent', 'agent_name', 'group_by', 'agent_name', 'agent_name', 'none', 30),
('fact_agent_turn', 'dim_model', 'model_id', 'group_by', 'model_id', 'model_id', 'none', 40),
('fact_agent_turn', 'dim_session', 'session_id', 'group_by', 'session_id', 'session_id', 'none', 50),
('fact_agent_turn', 'dim_time', 'event_time', 'group_by', 'event_time', 'event_time', 'none', 60),
('fact_agent_tool_call', 'dim_tool_category_2level', 'tool_code', 'group_by', 'tool_code', 'tool_code', 'left', 10),
('fact_agent_tool_call', 'dim_tool_category_1level', 'tool_code', 'group_by', 'tool_code', 'tool_code', 'left', 20),
('fact_agent_tool_call', 'dim_agent', 'agent_name', 'group_by', 'agent_name', 'agent_name', 'none', 40),
('fact_agent_tool_call', 'dim_model', 'model_id', 'group_by', 'model_id', 'model_id', 'none', 50),
('fact_agent_tool_call', 'dim_session', 'session_id', 'group_by', 'session_id', 'session_id', 'none', 60),
('fact_agent_tool_call', 'dim_time', 'event_time', 'group_by', 'event_time', 'event_time', 'none', 70)
ON CONFLICT (fact_id, dimension_id) DO UPDATE
SET key_code = EXCLUDED.key_code, role = EXCLUDED.role,
    fact_key = EXCLUDED.fact_key, dimension_key = EXCLUDED.dimension_key,
    join_type = EXCLUDED.join_type, sort_order = EXCLUDED.sort_order;

-- ---------- 6. 基础指标 ----------
INSERT INTO analytics.base_metric
(metric_id, fact_id, code, name, description, data_type, unit, aggregation, additivity,
 filter_expression, format, sort_order, status) VALUES
('bm_agent_turn_count', 'fact_agent_turn', 'turn_count', '轮次数',
 '按轮次计数；一行一个轮次。', 'integer', '次', 'count', 'additive', '', '#,##0', 10, 'active'),
('bm_turn_input_tokens', 'fact_agent_turn', 'input_tokens', '输入 Token',
 '输入的 token 数；可加。', 'integer', 'token', 'sum', 'additive', '', '#,##0', 20, 'active'),
('bm_turn_output_tokens', 'fact_agent_turn', 'output_tokens', '输出 Token',
 '输出的 token 数；可加。', 'integer', 'token', 'sum', 'additive', '', '#,##0', 30, 'active'),
('bm_turn_latency_ms', 'fact_agent_turn', 'latency_ms', '轮次耗时',
 '轮次端到端耗时；不可加，按 avg 计算。', 'numeric', 'ms', 'avg', 'non_additive', '', '#,##0', 40, 'active'),
('bm_tool_call_count', 'fact_agent_tool_call', 'tool_call_count', '工具调用次数',
 '按工具调用计数。', 'integer', '次', 'count', 'additive', '', '#,##0', 10, 'active'),
('bm_tool_error_count', 'fact_agent_tool_call', 'tool_error_count', '工具失败次数',
 '只统计 status=error 的调用。', 'integer', '次', 'count', 'additive', $q$status = 'error'$q$, '#,##0', 20, 'active'),
('bm_tool_latency_ms', 'fact_agent_tool_call', 'tool_latency_ms', '工具耗时',
 '单次工具调用耗时；不可加，按 avg 计算。', 'numeric', 'ms', 'avg', 'non_additive', '', '#,##0', 30, 'active'),
('bm_tool_retry_count', 'fact_agent_tool_call', 'tool_retry_count', '工具重试次数',
 '工具调用重试次数；可加。', 'integer', '次', 'sum', 'additive', '', '#,##0', 40, 'active')
ON CONFLICT (metric_id) DO UPDATE
SET code = EXCLUDED.code, name = EXCLUDED.name, description = EXCLUDED.description,
    data_type = EXCLUDED.data_type, unit = EXCLUDED.unit, aggregation = EXCLUDED.aggregation,
    additivity = EXCLUDED.additivity, filter_expression = EXCLUDED.filter_expression,
    format = EXCLUDED.format, sort_order = EXCLUDED.sort_order, status = EXCLUDED.status;

-- ---------- 7. 派生指标（与基础指标分表）----------
INSERT INTO analytics.derived_metric
(metric_id, fact_id, code, name, description, expression_language, expression,
 unit, format, dependencies, sort_order, status) VALUES
('dm_turn_avg_tokens', 'fact_agent_turn', 'avg_tokens_per_turn', '轮次平均 Token',
 '每轮输入+输出的平均值；依赖两个基础指标。', 'pseudo',
 'avg(input_tokens + output_tokens)',
 'token', '#,##0.0',
 '["bm_turn_input_tokens", "bm_turn_output_tokens"]'::jsonb, 10, 'active'),
('dm_tool_success_rate', 'fact_agent_tool_call', 'tool_success_rate', '工具成功率',
 '1 - 失败次数 / 调用次数；依赖两个基础指标。', 'pseudo',
 '1 - tool_error_count / tool_call_count',
 '%', '0.0%',
 '["bm_tool_error_count", "bm_tool_call_count"]'::jsonb, 20, 'active'),
('dm_tool_avg_latency', 'fact_agent_tool_call', 'tool_avg_latency_ms', '工具平均耗时',
 '工具调用耗时的平均值；依赖基础指标 tool_latency_ms。', 'pseudo',
 'avg(tool_latency_ms)',
 'ms', '#,##0',
 '["bm_tool_latency_ms"]'::jsonb, 30, 'active')
ON CONFLICT (metric_id) DO UPDATE
SET code = EXCLUDED.code, name = EXCLUDED.name, description = EXCLUDED.description,
    expression_language = EXCLUDED.expression_language, expression = EXCLUDED.expression,
    unit = EXCLUDED.unit, format = EXCLUDED.format, dependencies = EXCLUDED.dependencies,
    sort_order = EXCLUDED.sort_order, status = EXCLUDED.status;

-- ---------- 8. 非指标字段 ----------
INSERT INTO analytics.fact_field
(field_id, fact_id, code, name, description, data_type, role, is_enum, sort_order, status) VALUES
('ff_turn_session_id', 'fact_agent_turn', 'session_id', '会话标识', '轮次所属会话。', 'text', 'key', false, 10, 'active'),
('ff_turn_turn_id', 'fact_agent_turn', 'turn_id', '轮次标识', '轮次唯一标识。', 'text', 'key', false, 20, 'active'),
('ff_turn_agent_name', 'fact_agent_turn', 'agent_name', 'Agent 名称', '业务智能体名称。', 'text', 'display', false, 30, 'active'),
('ff_turn_model_name', 'fact_agent_turn', 'model_name', '模型名称', '本轮使用的模型。', 'text', 'display', false, 40, 'active'),
('ff_turn_status', 'fact_agent_turn', 'status', '轮次状态', '成功/失败等。', 'text', 'filter', true, 50, 'active'),
('ff_turn_error_code', 'fact_agent_turn', 'error_code', '错误码', '失败时的错误码。', 'text', 'attribute', true, 60, 'active'),
('ff_tool_session_id', 'fact_agent_tool_call', 'session_id', '会话标识', '调用所属会话。', 'text', 'key', false, 10, 'active'),
('ff_tool_turn_id', 'fact_agent_tool_call', 'turn_id', '轮次标识', '调用所属轮次。', 'text', 'key', false, 20, 'active'),
('ff_tool_call_id', 'fact_agent_tool_call', 'tool_call_id', '工具调用标识', '一次工具调用唯一标识。', 'text', 'key', false, 30, 'active'),
('ff_tool_name', 'fact_agent_tool_call', 'tool_name', '工具名称', '被调用的工具。', 'text', 'display', false, 40, 'active'),
('ff_tool_category', 'fact_agent_tool_call', 'tool_category', '工具大类', '工具分类第一层。', 'text', 'display', false, 50, 'active'),
('ff_tool_subcategory', 'fact_agent_tool_call', 'tool_subcategory', '工具小类', '工具分类第二层。', 'text', 'display', false, 60, 'active'),
('ff_tool_status', 'fact_agent_tool_call', 'status', '调用状态', '成功/失败等。', 'text', 'filter', true, 70, 'active'),
('ff_tool_error_code', 'fact_agent_tool_call', 'error_code', '错误码', '失败时的错误码。', 'text', 'attribute', true, 80, 'active')
ON CONFLICT (field_id) DO UPDATE
SET code = EXCLUDED.code, name = EXCLUDED.name, description = EXCLUDED.description,
    data_type = EXCLUDED.data_type, role = EXCLUDED.role, is_enum = EXCLUDED.is_enum,
    sort_order = EXCLUDED.sort_order, status = EXCLUDED.status;

-- ---------- 9. MCP 服务扩展（数据库类型）----------
-- 五类取值各给一行示例；真正接入时把 service_id 换成 WeKnora mcp_services.id。
INSERT INTO analytics.mcp_service
(service_id, service_name, database_type, scope, capabilities, description, status) VALUES
('svc_oceanbase', 'oceanbase_demo', 'oceanbase', '示例：OceanBase 业务库',
 'list_tables,describe_table,run_select', 'OceanBase 数据源示例。', 'inactive'),
('svc_gaussdb', 'gaussdb_demo', 'gaussdb', '示例：GaussDB 业务库',
 'list_tables,describe_table,run_select', 'GaussDB 数据源示例。', 'inactive'),
('svc_hetu', 'hetu_demo', 'hetu', '示例：河图业务库',
 'list_tables,describe_table,run_select', '河图（hetu）数据源示例。', 'inactive'),
('svc_postgresql', 'agent_diag', 'postgresql', 'Agent 运行数据（本示例）',
 'list_tables,describe_table,run_select', '自助取数 MCP；智能体按 postgresql 方言写 SQL。', 'active'),
('svc_mysql', 'mysql_demo', 'mysql', '示例：MySQL 业务库',
 'list_tables,describe_table,run_select', 'MySQL 数据源示例。', 'inactive')
ON CONFLICT (service_id) DO UPDATE
SET service_name = EXCLUDED.service_name, database_type = EXCLUDED.database_type,
    scope = EXCLUDED.scope, capabilities = EXCLUDED.capabilities,
    description = EXCLUDED.description, status = EXCLUDED.status;

-- ---------- 10. 绑定 ----------
INSERT INTO analytics.binding
(binding_id, object_type, object_id, service_id, binding_type, tool_name, status, metadata_hash) VALUES
('bind_fact_agent_turn', 'fact', 'fact_agent_turn', 'svc_postgresql', 'self_service', '', 'active',
 '{"diag.agent_turn": "sha256:example"}'::jsonb),
('bind_fact_agent_tool_call', 'fact', 'fact_agent_tool_call', 'svc_postgresql', 'self_service', '', 'active',
 '{"diag.agent_tool_call": "sha256:example"}'::jsonb)
ON CONFLICT (binding_id) DO UPDATE
SET object_type = EXCLUDED.object_type, object_id = EXCLUDED.object_id,
    service_id = EXCLUDED.service_id, binding_type = EXCLUDED.binding_type,
    tool_name = EXCLUDED.tool_name, status = EXCLUDED.status,
    metadata_hash = EXCLUDED.metadata_hash;

-- ---------- 11. 字段映射：AgentTurnFact ----------
INSERT INTO analytics.field_mapping
(mapping_id, binding_id, semantic_kind, semantic_id, source_type, source_schema,
 source_table, source_column, data_type, aggregation, expression, sort_order, note, status) VALUES
('map_turn_session', 'bind_fact_agent_turn', 'non_metric_field', 'ff_turn_session_id',
 'mcp_table', 'diag', 'agent_turn', 'session_id', 'text', '', '', 10, '轮次会话键', 'active'),
('map_turn_turn', 'bind_fact_agent_turn', 'non_metric_field', 'ff_turn_turn_id',
 'mcp_table', 'diag', 'agent_turn', 'turn_id', 'text', '', '', 20, '轮次键', 'active'),
('map_turn_agent', 'bind_fact_agent_turn', 'non_metric_field', 'ff_turn_agent_name',
 'mcp_table', 'diag', 'agent_turn', 'agent_name', 'text', '', '', 30, 'Agent 维度', 'active'),
('map_turn_model', 'bind_fact_agent_turn', 'non_metric_field', 'ff_turn_model_name',
 'mcp_table', 'diag', 'agent_turn', 'model_name', 'text', '', '', 40, '模型维度', 'active'),
('map_turn_status', 'bind_fact_agent_turn', 'non_metric_field', 'ff_turn_status',
 'mcp_table', 'diag', 'agent_turn', 'status', 'text', '', '', 50, '轮次状态', 'active'),
('map_turn_error', 'bind_fact_agent_turn', 'non_metric_field', 'ff_turn_error_code',
 'mcp_table', 'diag', 'agent_turn', 'error_code', 'text', '', '', 60, '错误码', 'active'),
('map_turn_count', 'bind_fact_agent_turn', 'base_metric', 'bm_agent_turn_count',
 'mcp_table', 'diag', 'agent_turn', 'turn_id', 'text', 'count', '', 70, '轮次计数', 'active'),
('map_turn_input', 'bind_fact_agent_turn', 'base_metric', 'bm_turn_input_tokens',
 'mcp_table', 'diag', 'agent_turn', 'input_tokens', 'integer', 'sum', '', 80, '输入 token', 'active'),
('map_turn_output', 'bind_fact_agent_turn', 'base_metric', 'bm_turn_output_tokens',
 'mcp_table', 'diag', 'agent_turn', 'output_tokens', 'integer', 'sum', '', 90, '输出 token', 'active'),
('map_turn_latency', 'bind_fact_agent_turn', 'base_metric', 'bm_turn_latency_ms',
 'mcp_table', 'diag', 'agent_turn', 'latency_ms', 'numeric', 'avg', '', 100, '轮次耗时', 'active')
ON CONFLICT (mapping_id) DO UPDATE
SET binding_id = EXCLUDED.binding_id, semantic_kind = EXCLUDED.semantic_kind,
    semantic_id = EXCLUDED.semantic_id, source_type = EXCLUDED.source_type,
    source_schema = EXCLUDED.source_schema, source_table = EXCLUDED.source_table,
    source_column = EXCLUDED.source_column, data_type = EXCLUDED.data_type,
    aggregation = EXCLUDED.aggregation, expression = EXCLUDED.expression,
    sort_order = EXCLUDED.sort_order, note = EXCLUDED.note, status = EXCLUDED.status;

-- ---------- 12. 字段映射：AgentToolCallFact ----------
INSERT INTO analytics.field_mapping
(mapping_id, binding_id, semantic_kind, semantic_id, source_type, source_schema,
 source_table, source_column, data_type, aggregation, expression, sort_order, note, status) VALUES
('map_tool_session', 'bind_fact_agent_tool_call', 'non_metric_field', 'ff_tool_session_id',
 'mcp_table', 'diag', 'agent_tool_call', 'session_id', 'text', '', '', 10, '会话键', 'active'),
('map_tool_turn', 'bind_fact_agent_tool_call', 'non_metric_field', 'ff_tool_turn_id',
 'mcp_table', 'diag', 'agent_tool_call', 'turn_id', 'text', '', '', 20, '轮次键', 'active'),
('map_tool_call_id', 'bind_fact_agent_tool_call', 'non_metric_field', 'ff_tool_call_id',
 'mcp_table', 'diag', 'agent_tool_call', 'tool_call_id', 'text', '', '', 30, '调用键', 'active'),
('map_tool_name', 'bind_fact_agent_tool_call', 'non_metric_field', 'ff_tool_name',
 'mcp_table', 'diag', 'agent_tool_call', 'tool_name', 'text', '', '', 40, '工具维度', 'active'),
('map_tool_category', 'bind_fact_agent_tool_call', 'non_metric_field', 'ff_tool_category',
 'mcp_table', 'diag', 'agent_tool_call', 'tool_category', 'text', '', '', 50, '工具大类', 'active'),
('map_tool_subcategory', 'bind_fact_agent_tool_call', 'non_metric_field', 'ff_tool_subcategory',
 'mcp_table', 'diag', 'agent_tool_call', 'tool_subcategory', 'text', '', '', 60, '工具小类', 'active'),
('map_tool_status', 'bind_fact_agent_tool_call', 'non_metric_field', 'ff_tool_status',
 'mcp_table', 'diag', 'agent_tool_call', 'status', 'text', '', '', 70, '调用状态', 'active'),
('map_tool_error', 'bind_fact_agent_tool_call', 'non_metric_field', 'ff_tool_error_code',
 'mcp_table', 'diag', 'agent_tool_call', 'error_code', 'text', '', '', 80, '错误码', 'active'),
('map_tool_call_count', 'bind_fact_agent_tool_call', 'base_metric', 'bm_tool_call_count',
 'mcp_table', 'diag', 'agent_tool_call', 'tool_call_id', 'text', 'count', '', 90, '调用次数', 'active'),
('map_tool_error_count', 'bind_fact_agent_tool_call', 'base_metric', 'bm_tool_error_count',
 'mcp_table', 'diag', 'agent_tool_call', 'tool_call_id', 'text', 'count', $q$status = 'error'$q$, 100, '失败次数', 'active'),
('map_tool_latency', 'bind_fact_agent_tool_call', 'base_metric', 'bm_tool_latency_ms',
 'mcp_table', 'diag', 'agent_tool_call', 'latency_ms', 'numeric', 'avg', '', 110, '工具耗时', 'active'),
('map_tool_retry', 'bind_fact_agent_tool_call', 'base_metric', 'bm_tool_retry_count',
 'mcp_table', 'diag', 'agent_tool_call', 'retry_count', 'integer', 'sum', '', 120, '重试次数', 'active')
ON CONFLICT (mapping_id) DO UPDATE
SET binding_id = EXCLUDED.binding_id, semantic_kind = EXCLUDED.semantic_kind,
    semantic_id = EXCLUDED.semantic_id, source_type = EXCLUDED.source_type,
    source_schema = EXCLUDED.source_schema, source_table = EXCLUDED.source_table,
    source_column = EXCLUDED.source_column, data_type = EXCLUDED.data_type,
    aggregation = EXCLUDED.aggregation, expression = EXCLUDED.expression,
    sort_order = EXCLUDED.sort_order, note = EXCLUDED.note, status = EXCLUDED.status;

-- ---------- 13. 维度物理映射见 008（binding + field_mapping）----------

-- ---------- 14. 枚举映射 ----------
INSERT INTO analytics.enum_mapping
(enum_id, binding_id, semantic_kind, semantic_id, semantic_value, semantic_label,
 source_schema, source_table, source_column, physical_value, physical_label, status) VALUES
('enum_turn_status_ok', 'bind_fact_agent_turn', 'non_metric_field', 'ff_turn_status',
 'success', '成功', 'diag', 'agent_turn', 'status', 'succeeded', '成功', 'active'),
('enum_turn_status_err', 'bind_fact_agent_turn', 'non_metric_field', 'ff_turn_status',
 'error', '失败', 'diag', 'agent_turn', 'status', 'failed', '失败', 'active'),
('enum_tool_status_ok', 'bind_fact_agent_tool_call', 'non_metric_field', 'ff_tool_status',
 'success', '成功', 'diag', 'agent_tool_call', 'status', 'succeeded', '成功', 'active'),
('enum_tool_status_err', 'bind_fact_agent_tool_call', 'non_metric_field', 'ff_tool_status',
 'error', '失败', 'diag', 'agent_tool_call', 'status', 'failed', '失败', 'active'),
('enum_tool_err_timeout', 'bind_fact_agent_tool_call', 'non_metric_field', 'ff_tool_error_code',
 'timeout', '超时', 'diag', 'agent_tool_call', 'error_code', 'TO', '超时', 'active')
ON CONFLICT (enum_id) DO UPDATE
SET binding_id = EXCLUDED.binding_id, semantic_kind = EXCLUDED.semantic_kind,
    semantic_id = EXCLUDED.semantic_id, semantic_value = EXCLUDED.semantic_value,
    semantic_label = EXCLUDED.semantic_label, source_schema = EXCLUDED.source_schema,
    source_table = EXCLUDED.source_table, source_column = EXCLUDED.source_column,
    physical_value = EXCLUDED.physical_value, physical_label = EXCLUDED.physical_label,
    status = EXCLUDED.status;

-- ---------- 15. 标准取数场景 ----------
INSERT INTO analytics.scenario
(scenario_id, name, description, service_id, source_locator, source_sql, status) VALUES
('sc_tool_usage', '工具使用分析',
 '按工具分类/工具/Agent/模型统计调用次数、失败次数与耗时；可作为工具使用分析的建模起点。',
 'svc_postgresql', 'page:analytics/scenario/tool_usage',
 'SELECT t.tool_category, t.tool_subcategory, t.tool_name, t.agent_name, t.model_name,
        count(*) AS tool_call_count,
        count(*) FILTER (WHERE t.status = ''failed'') AS tool_error_count,
        avg(t.latency_ms) AS tool_latency_ms
   FROM diag.agent_tool_call t
  WHERE t.status <> ''deleted''
  GROUP BY 1,2,3,4,5', 'active')
ON CONFLICT (scenario_id) DO UPDATE
SET name = EXCLUDED.name, description = EXCLUDED.description,
    service_id = EXCLUDED.service_id, source_locator = EXCLUDED.source_locator,
    source_sql = EXCLUDED.source_sql, status = EXCLUDED.status;

INSERT INTO analytics.scenario_join
(scenario_id, seq, left_schema, left_table, left_column,
 right_schema, right_table, right_column, join_type, condition, note) VALUES
('sc_tool_usage', 1, 'diag', 'agent_tool_call', 'turn_id',
 'diag', 'agent_turn', 'turn_id', 'left', 'turn_id', '工具调用挂回轮次')
ON CONFLICT (scenario_id, seq) DO UPDATE
SET left_schema = EXCLUDED.left_schema, left_table = EXCLUDED.left_table,
    left_column = EXCLUDED.left_column, right_schema = EXCLUDED.right_schema,
    right_table = EXCLUDED.right_table, right_column = EXCLUDED.right_column,
    join_type = EXCLUDED.join_type, condition = EXCLUDED.condition, note = EXCLUDED.note;

INSERT INTO analytics.scenario_filter
(scenario_id, seq, schema_name, table_name, column_name, expression, meaning, required) VALUES
('sc_tool_usage', 1, 'diag', 'agent_tool_call', 'status', '<> ''deleted''',
 '排除已删除记录', true)
ON CONFLICT (scenario_id, seq) DO UPDATE
SET schema_name = EXCLUDED.schema_name, table_name = EXCLUDED.table_name,
    column_name = EXCLUDED.column_name, expression = EXCLUDED.expression,
    meaning = EXCLUDED.meaning, required = EXCLUDED.required;

INSERT INTO analytics.scenario_fact
(scenario_id, fact_id, sort_order, note, status) VALUES
('sc_tool_usage', 'fact_agent_tool_call', 10, '工具调用场景来源事实', 'active')
ON CONFLICT (scenario_id, fact_id) DO UPDATE
SET sort_order = EXCLUDED.sort_order, note = EXCLUDED.note, status = EXCLUDED.status;

COMMIT;
