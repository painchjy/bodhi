-- ============================================================
-- 事实字段/指标 → diag 事实视图映射（2026-11-22）
-- 目的：智能体直接看到可执行列，不再指向不存在的 diag.agent_turn/agent_tool_call。
-- ============================================================

BEGIN;

-- 所有事实映射先改到 diag 视图
UPDATE analytics.field_mapping
   SET source_schema = 'diag', source_table = 'agent_turn_fact'
 WHERE binding_id = 'bind_fact_agent_turn';
UPDATE analytics.field_mapping
   SET source_schema = 'diag', source_table = 'agent_tool_call_fact'
 WHERE binding_id = 'bind_fact_agent_tool_call';

-- 轮次指标/字段修正
UPDATE analytics.field_mapping SET source_column = 'prompt_tokens'
 WHERE mapping_id = 'map_turn_input';
UPDATE analytics.field_mapping SET source_column = 'completion_tokens'
 WHERE mapping_id = 'map_turn_output';
UPDATE analytics.field_mapping SET source_column = 'agent_duration_ms'
 WHERE mapping_id = 'map_turn_latency';
UPDATE analytics.field_mapping SET source_column = 'message_id'
 WHERE mapping_id IN ('map_turn_turn', 'map_turn_count');

-- 工具指标/字段修正
UPDATE analytics.field_mapping SET source_column = 'session_id'
 WHERE mapping_id = 'map_tool_session';
UPDATE analytics.field_mapping SET source_column = 'message_id'
 WHERE mapping_id = 'map_tool_turn';
UPDATE analytics.field_mapping SET source_column = 'tool_call_no', data_type = 'integer'
 WHERE mapping_id = 'map_tool_call_id';
UPDATE analytics.field_mapping SET source_column = 'tool_code'
 WHERE mapping_id IN ('map_tool_name', 'map_tool_call_count');
UPDATE analytics.field_mapping
   SET source_table = 'tool_dimension', source_column = 'category_name'
 WHERE mapping_id = 'map_tool_category';
UPDATE analytics.field_mapping
   SET source_table = 'tool_problem_dimension', source_column = 'category_l2_name'
 WHERE mapping_id = 'map_tool_subcategory';
UPDATE analytics.field_mapping SET source_column = 'tool_success', data_type = 'boolean'
 WHERE mapping_id = 'map_tool_status';
UPDATE analytics.field_mapping
   SET source_column = 'tool_success', expression = 'NOT tool_success'
 WHERE mapping_id = 'map_tool_error';
UPDATE analytics.field_mapping
   SET source_column = 'tool_code', expression = 'tool_success = false'
 WHERE mapping_id = 'map_tool_error_count';

-- 没有对应物理列的历史指标：保留但停用，避免误导智能体
UPDATE analytics.field_mapping SET status = 'inactive'
 WHERE mapping_id IN ('map_tool_latency', 'map_tool_retry');
UPDATE analytics.base_metric SET status = 'inactive'
 WHERE metric_id IN ('bm_tool_latency_ms', 'bm_tool_retry_count');

COMMIT;
