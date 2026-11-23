-- ============================================================
-- diag 事实字段/指标补充（2026-11-22）
-- ============================================================

BEGIN;

INSERT INTO analytics.fact_field
(field_id, fact_id, code, name, description, data_type, role, is_enum, sort_order, status) VALUES
('ff_turn_event_time', 'fact_agent_turn', 'event_time', '事件时间',
 'assistant 消息时间（含时区）。', 'timestamptz', 'display', false, 70, 'active'),
('ff_turn_total_tokens', 'fact_agent_turn', 'total_tokens', '总 Token',
 'usage.total_tokens；缓存命中也会反映。', 'bigint', 'display', false, 80, 'active'),
('ff_turn_cached_tokens', 'fact_agent_turn', 'cached_tokens', '缓存 Token',
 'usage.cached_tokens；用于评估缓存收益。', 'bigint', 'display', false, 90, 'active'),
('ff_tool_code', 'fact_agent_tool_call', 'tool_code', '工具编码',
 '与工具维度成员键关联。', 'text', 'key', false, 45, 'active'),
('ff_tool_round_no', 'fact_agent_tool_call', 'round_no', '轮次号',
 'agent_steps[].iteration + 1。', 'integer', 'display', false, 75, 'active'),
('ff_tool_call_no', 'fact_agent_tool_call', 'tool_call_no', '调用序号',
 '同一轮内工具调用顺序。', 'integer', 'key', false, 65, 'active'),
('ff_tool_event_time', 'fact_agent_tool_call', 'event_time', '事件时间',
 'assistant 消息时间（含时区）。', 'timestamptz', 'display', false, 90, 'active'),
('ff_tool_result_chars', 'fact_agent_tool_call', 'result_chars', '工具返回字符数',
 '工具回执 JSON 的长度；用于定位返回体过大。', 'integer', 'display', false, 95, 'active'),
('ff_tool_success', 'fact_agent_tool_call', 'tool_success', '工具成功',
 'result 带 error 或 success=false 时为 false。', 'boolean', 'filter', false, 85, 'active'),
('ff_tool_duration_ms', 'fact_agent_tool_call', 'duration_ms', '工具耗时',
 '单次工具调用耗时；来自 agent_steps[].tool_calls[].duration。', 'numeric', 'display', false, 92, 'active'),
('ff_tool_result_bytes', 'fact_agent_tool_call', 'result_bytes', '工具返回字节数',
 '工具回执 JSON 的 UTF-8 字节数；用于返回体量分析。', 'integer', 'display', false, 96, 'active'),
('ff_tool_error_code', 'fact_agent_tool_call', 'error_code', '错误码',
 '工具调用失败时的错误码。', 'text', 'attribute', false, 82, 'active'),
('ff_turn_input_prompt', 'fact_agent_turn', 'input_prompt', '输入提示词',
 '本轮 assistant 之前最近一条 user 消息的正文。', 'text', 'display', false, 65, 'active')
ON CONFLICT (field_id) DO UPDATE
SET fact_id = EXCLUDED.fact_id, code = EXCLUDED.code, name = EXCLUDED.name,
    description = EXCLUDED.description, data_type = EXCLUDED.data_type,
    role = EXCLUDED.role, sort_order = EXCLUDED.sort_order, status = EXCLUDED.status;

INSERT INTO analytics.base_metric
(metric_id, fact_id, code, name, description, data_type, unit, aggregation,
 additivity, filter_expression, format, sort_order, status) VALUES
('bm_turn_total_tokens', 'fact_agent_turn', 'total_tokens', '总 Token',
 'usage.total_tokens 求和。', 'bigint', 'token', 'sum', 'additive', '', '#,##0', 50, 'active'),
('bm_turn_cached_tokens', 'fact_agent_turn', 'cached_tokens', '缓存 Token',
 'usage.cached_tokens 求和。', 'bigint', 'token', 'sum', 'additive', '', '#,##0', 60, 'active'),
('bm_tool_result_chars', 'fact_agent_tool_call', 'tool_result_chars', '工具返回字符数',
 'result_chars 求和；用于观察回执体积。', 'bigint', 'chars', 'sum', 'additive', '', '#,##0', 50, 'active'),
('bm_tool_success_count', 'fact_agent_tool_call', 'tool_success_count', '工具成功次数',
 '只统计 tool_success=true。', 'integer', '次', 'count', 'additive', 'tool_success = true', '#,##0', 60, 'active')
ON CONFLICT (metric_id) DO UPDATE
SET fact_id = EXCLUDED.fact_id, code = EXCLUDED.code, name = EXCLUDED.name,
    description = EXCLUDED.description, data_type = EXCLUDED.data_type,
    unit = EXCLUDED.unit, aggregation = EXCLUDED.aggregation,
    additivity = EXCLUDED.additivity, filter_expression = EXCLUDED.filter_expression,
    format = EXCLUDED.format, sort_order = EXCLUDED.sort_order, status = EXCLUDED.status;

INSERT INTO analytics.derived_metric
(metric_id, fact_id, code, name, description, expression_language, expression,
 unit, format, dependencies, sort_order, status) VALUES
('dm_tool_avg_result_chars', 'fact_agent_tool_call', 'tool_avg_result_chars', '工具平均返回字符数',
 'avg(result_chars)；用于定位返回体过大的工具。', 'pseudo', 'avg(result_chars)',
 'chars', '#,##0.0', '["bm_tool_result_chars", "bm_tool_call_count"]'::jsonb, 40, 'active')
ON CONFLICT (metric_id) DO UPDATE
SET fact_id = EXCLUDED.fact_id, code = EXCLUDED.code, name = EXCLUDED.name,
    description = EXCLUDED.description, expression_language = EXCLUDED.expression_language,
    expression = EXCLUDED.expression, unit = EXCLUDED.unit, format = EXCLUDED.format,
    dependencies = EXCLUDED.dependencies, sort_order = EXCLUDED.sort_order,
    status = EXCLUDED.status;

-- 语义页不暴露物理表：平均耗时用伪表达式，物理表只在 field_mapping
UPDATE analytics.derived_metric
   SET expression_language = 'pseudo', expression = 'avg(tool_latency_ms)'
 WHERE metric_id = 'dm_tool_avg_latency';

INSERT INTO analytics.field_mapping
(mapping_id, binding_id, semantic_kind, semantic_id, source_type, source_schema,
 source_table, source_column, data_type, aggregation, expression, sort_order, note, status) VALUES
('map_turn_event_time', 'bind_fact_agent_turn', 'non_metric_field', 'ff_turn_event_time',
 'mcp_table', 'diag', 'agent_turn_fact', 'event_time', 'timestamptz', '', '', 110, '事件时间', 'active'),
('map_turn_total_tokens', 'bind_fact_agent_turn', 'non_metric_field', 'ff_turn_total_tokens',
 'mcp_table', 'diag', 'agent_turn_fact', 'total_tokens', 'bigint', '', '', 120, '总 token', 'active'),
('map_turn_cached_tokens', 'bind_fact_agent_turn', 'non_metric_field', 'ff_turn_cached_tokens',
 'mcp_table', 'diag', 'agent_turn_fact', 'cached_tokens', 'bigint', '', '', 130, '缓存 token', 'active'),
('map_tool_code', 'bind_fact_agent_tool_call', 'non_metric_field', 'ff_tool_code',
 'mcp_table', 'diag', 'agent_tool_call_fact', 'tool_code', 'text', '', '', 45, '工具维度键', 'active'),
('map_tool_round_no', 'bind_fact_agent_tool_call', 'non_metric_field', 'ff_tool_round_no',
 'mcp_table', 'diag', 'agent_tool_call_fact', 'round_no', 'integer', '', '', 75, '轮次', 'active'),
('map_tool_call_no', 'bind_fact_agent_tool_call', 'non_metric_field', 'ff_tool_call_no',
 'mcp_table', 'diag', 'agent_tool_call_fact', 'tool_call_no', 'integer', '', '', 65, '调用序号', 'active'),
('map_tool_event_time', 'bind_fact_agent_tool_call', 'non_metric_field', 'ff_tool_event_time',
 'mcp_table', 'diag', 'agent_tool_call_fact', 'event_time', 'timestamptz', '', '', 90, '事件时间', 'active'),
('map_tool_result_chars', 'bind_fact_agent_tool_call', 'non_metric_field', 'ff_tool_result_chars',
 'mcp_table', 'diag', 'agent_tool_call_fact', 'result_chars', 'integer', '', '', 95, '返回字符数', 'active'),
('map_tool_success', 'bind_fact_agent_tool_call', 'non_metric_field', 'ff_tool_success',
 'mcp_table', 'diag', 'agent_tool_call_fact', 'tool_success', 'boolean', '', '', 85, '是否成功', 'active'),
('map_metric_turn_total', 'bind_fact_agent_turn', 'base_metric', 'bm_turn_total_tokens',
 'mcp_table', 'diag', 'agent_turn_fact', 'total_tokens', 'bigint', 'sum', '', 140, '总 token', 'active'),
('map_metric_turn_cached', 'bind_fact_agent_turn', 'base_metric', 'bm_turn_cached_tokens',
 'mcp_table', 'diag', 'agent_turn_fact', 'cached_tokens', 'bigint', 'sum', '', 150, '缓存 token', 'active'),
('map_metric_tool_result_chars', 'bind_fact_agent_tool_call', 'base_metric', 'bm_tool_result_chars',
 'mcp_table', 'diag', 'agent_tool_call_fact', 'result_chars', 'bigint', 'sum', '', 160, '返回字符数', 'active'),
('map_metric_tool_success', 'bind_fact_agent_tool_call', 'base_metric', 'bm_tool_success_count',
 'mcp_table', 'diag', 'agent_tool_call_fact', 'tool_code', 'text', 'count', 'tool_success = true', 170, '成功次数', 'active'),
('map_tool_duration_ms', 'bind_fact_agent_tool_call', 'non_metric_field', 'ff_tool_duration_ms',
 'mcp_table', 'diag', 'agent_tool_call_fact', 'duration_ms', 'numeric', '', '', 92, '工具耗时', 'active'),
('map_tool_result_bytes', 'bind_fact_agent_tool_call', 'non_metric_field', 'ff_tool_result_bytes',
 'mcp_table', 'diag', 'agent_tool_call_fact', 'result_bytes', 'integer', '', '', 96, '返回字节数', 'active'),
('map_turn_input_prompt', 'bind_fact_agent_turn', 'non_metric_field', 'ff_turn_input_prompt',
 'mcp_table', 'diag', 'agent_turn_fact', 'input_prompt', 'text', '', '', 65, '输入提示词', 'active')
ON CONFLICT (mapping_id) DO UPDATE
SET binding_id = EXCLUDED.binding_id, semantic_kind = EXCLUDED.semantic_kind,
    semantic_id = EXCLUDED.semantic_id, source_type = EXCLUDED.source_type,
    source_schema = EXCLUDED.source_schema, source_table = EXCLUDED.source_table,
    source_column = EXCLUDED.source_column, data_type = EXCLUDED.data_type,
    aggregation = EXCLUDED.aggregation, expression = EXCLUDED.expression,
    sort_order = EXCLUDED.sort_order, note = EXCLUDED.note, status = EXCLUDED.status;

-- 旧 seed 的物理表/列修正（保持单一物理映射真源）
UPDATE analytics.field_mapping
   SET source_table = 'agent_tool_call_fact'
 WHERE mapping_id LIKE 'map_tool_%' AND source_table = 'agent_tool_call';
UPDATE analytics.field_mapping SET source_column = 'tool_name'
 WHERE mapping_id = 'map_tool_name';
UPDATE analytics.field_mapping SET source_column = 'tool_call_id'
 WHERE mapping_id = 'map_tool_call_id';
UPDATE analytics.field_mapping SET source_column = 'error_code'
 WHERE mapping_id = 'map_tool_error';
-- 事实字段不映射维表；工具分类/小类由 Dimension 自己映射。
UPDATE analytics.field_mapping SET source_column = 'tool_success'
 WHERE mapping_id = 'map_tool_status';
UPDATE analytics.field_mapping SET source_column = 'tool_call_id'
 WHERE mapping_id = 'map_tool_call_count';
UPDATE analytics.field_mapping
   SET source_column = 'tool_code', expression = 'tool_success = false'
 WHERE mapping_id = 'map_tool_error_count';
UPDATE analytics.field_mapping
   SET source_column = 'duration_ms', status = 'active'
 WHERE mapping_id = 'map_tool_latency';
UPDATE analytics.field_mapping
   SET status = 'inactive'
 WHERE mapping_id = 'map_tool_retry';

UPDATE analytics.field_mapping
   SET source_table = 'agent_turn_fact'
 WHERE mapping_id LIKE 'map_turn_%' AND source_table = 'agent_turn';
UPDATE analytics.field_mapping SET source_column = 'message_id'
 WHERE mapping_id = 'map_turn_turn_id';
UPDATE analytics.field_mapping SET source_column = 'prompt_tokens'
 WHERE mapping_id = 'map_turn_input';
UPDATE analytics.field_mapping SET source_column = 'completion_tokens'
 WHERE mapping_id = 'map_turn_output';
UPDATE analytics.field_mapping SET source_column = 'agent_duration_ms'
 WHERE mapping_id = 'map_turn_latency';
UPDATE analytics.field_mapping SET source_column = 'message_id'
 WHERE mapping_id = 'map_turn_count';
UPDATE analytics.field_mapping SET status = 'inactive'
 WHERE mapping_id IN ('map_turn_status', 'map_turn_error');

-- 场景 filter 修正：旧表 diag.agent_tool_call 已不存在
UPDATE analytics.fact SET grain = 'session_id + message_id'
 WHERE fact_id = 'fact_agent_turn';
UPDATE analytics.field_mapping SET source_column = 'model_id'
 WHERE mapping_id = 'map_turn_model';
UPDATE analytics.derived_metric SET expression = 'avg(prompt_tokens + completion_tokens)'
 WHERE metric_id = 'dm_turn_avg_tokens';
UPDATE analytics.fact_field SET status = 'inactive'
 WHERE field_id IN ('ff_turn_status', 'ff_turn_error_code');

UPDATE analytics.scenario_filter
   SET table_name = 'agent_tool_call_fact',
       column_name = 'tool_code',
       expression = $q$<> ''$q$,
       meaning = '排除无工具编码的调用'
 WHERE scenario_id = 'sc_tool_usage' AND table_name = 'agent_tool_call';

COMMIT;
