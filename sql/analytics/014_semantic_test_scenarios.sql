BEGIN;
INSERT INTO analytics.scenario (scenario_id, name, description, service_id, source_locator, source_sql, status) VALUES
('sc_semantic_agent_turn_full', 'AgentTurnFact 全量语义测试场景', '覆盖轮次事实全部基础指标、派生指标和非指标字段；粒度 session_id + message_id。', 'svc_postgresql', 'page:analytics/scenario/semantic_agent_turn_full', '-- grain: session_id, message_id
SELECT f.session_id AS session_id, f.message_id AS turn_id, f.agent_name AS agent_name, f.model_id AS model_name, f.event_time AS event_time, f.total_tokens AS total_tokens, f.cached_tokens AS cached_tokens, f.input_prompt AS input_prompt, count(*) AS turn_count, sum(f.prompt_tokens) AS input_tokens, sum(f.completion_tokens) AS output_tokens, sum(f.total_tokens) AS total_tokens_sum, sum(f.cached_tokens) AS cached_tokens_sum, avg(f.agent_duration_ms) AS latency_ms, avg(f.prompt_tokens + f.completion_tokens) AS avg_tokens_per_turn FROM diag.agent_turn_fact f GROUP BY f.session_id, f.message_id, f.agent_name, f.model_id, f.event_time, f.total_tokens, f.cached_tokens, f.input_prompt', 'active'),
('sc_semantic_tool_context_full', 'AgentToolContextFact 多事实表语义测试场景', '覆盖工具调用与轮次上下文全部基础指标和非指标字段；粒度 session_id + message_id + tool_call_id。', 'svc_postgresql', 'page:analytics/scenario/semantic_tool_context_full', '-- grain: session_id, message_id, tool_call_id
SELECT f.tool_code AS tool_code, t.input_prompt AS input_prompt, count(f.tool_call_id) AS tool_call_count, count(f.tool_code) FILTER (WHERE f.tool_success = false) AS tool_error_count FROM diag.agent_tool_call_fact f LEFT JOIN diag.agent_turn_fact t ON t.message_id = f.message_id GROUP BY f.tool_call_id, f.session_id, f.message_id, f.tool_code, t.input_prompt', 'active')
ON CONFLICT (scenario_id) DO UPDATE SET name = EXCLUDED.name, description = EXCLUDED.description, service_id = EXCLUDED.service_id, source_locator = EXCLUDED.source_locator, source_sql = EXCLUDED.source_sql, status = EXCLUDED.status;
INSERT INTO analytics.scenario_fact (scenario_id, fact_id, sort_order, note, status) VALUES
('sc_semantic_agent_turn_full', 'fact_agent_turn', 10, '语义提取全量样例', 'active'),
('sc_semantic_tool_context_full', 'fact_agent_tool_context', 10, '语义提取多事实表样例', 'active')
ON CONFLICT (scenario_id, fact_id) DO UPDATE SET sort_order = EXCLUDED.sort_order, note = EXCLUDED.note, status = EXCLUDED.status;
INSERT INTO analytics.scenario_join (scenario_id, seq, left_schema, left_table, left_column, right_schema, right_table, right_column, join_type, condition, note) VALUES
('sc_semantic_tool_context_full', 1, 'diag', 'agent_tool_call_fact', 'message_id', 'diag', 'agent_turn_fact', 'message_id', 'left', 'message_id', '工具调用挂回所属轮次')
ON CONFLICT (scenario_id, seq) DO UPDATE SET left_schema = EXCLUDED.left_schema, left_table = EXCLUDED.left_table, left_column = EXCLUDED.left_column, right_schema = EXCLUDED.right_schema, right_table = EXCLUDED.right_table, right_column = EXCLUDED.right_column, join_type = EXCLUDED.join_type, condition = EXCLUDED.condition, note = EXCLUDED.note;
INSERT INTO analytics.scenario (scenario_id, name, description, service_id, source_locator, source_sql, status) VALUES
('sc_semantic_agent_turn_by_agent', '缺少粒度键的新建事实测试场景', '场景粒度为 session_id + message_id + agent_id，现有 AgentTurnFact 缺少 agent_id 粒度键；用于测试提示新建事实。', 'svc_postgresql', 'page:analytics/scenario/semantic_agent_turn_by_agent', '-- grain: session_id, message_id, agent_id
SELECT f.session_id AS session_id, f.message_id AS message_id, f.agent_id AS agent_id, count(*) AS turn_count, sum(f.prompt_tokens) AS input_tokens FROM diag.agent_turn_fact f GROUP BY f.session_id, f.message_id, f.agent_id', 'active')
ON CONFLICT (scenario_id) DO UPDATE SET name = EXCLUDED.name, description = EXCLUDED.description, service_id = EXCLUDED.service_id, source_locator = EXCLUDED.source_locator, source_sql = EXCLUDED.source_sql, status = EXCLUDED.status;

COMMIT;

