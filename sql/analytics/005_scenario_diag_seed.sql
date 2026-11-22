-- ============================================================
-- 标准取数场景（diag 事实 + 维度表）
-- 目的：智能体可直接复用/改写这些 SQL，而不是猜表名和 JOIN。
-- ============================================================

BEGIN;

INSERT INTO analytics.scenario
(scenario_id, name, description, service_id, source_locator, source_sql, status) VALUES
('sc_tool_usage', '工具使用频次（按 Agent/模型 + 未使用工具）',
 '工具维度 LEFT JOIN 工具调用事实；按 Agent/模型对比；未使用工具 usage_count=0。',
 'svc_postgresql', 'page:analytics/scenario/tool_usage',
 $q$SELECT d.tool_code, d.tool_name, d.category_name,
       f.agent_name, f.model_id,
       count(f.tool_code) AS usage_count
  FROM diag.tool_dimension_all d
  LEFT JOIN diag.agent_tool_call_fact f
    ON f.tool_code = d.tool_code
 GROUP BY d.tool_code, d.tool_name, d.category_name, f.agent_name, f.model_id
 ORDER BY usage_count DESC, d.tool_name, f.agent_name NULLS FIRST$q$, 'active'),
('sc_tool_unused', '未使用工具清单',
 '工具维度 LEFT JOIN 事实，事实键为空即未使用；包含待建工具标记。',
 'svc_postgresql', 'page:analytics/scenario/tool_unused',
 $q$SELECT d.tool_code, d.tool_name, d.category_name, d.is_pending
  FROM diag.tool_dimension d
  LEFT JOIN diag.agent_tool_call_fact f ON f.tool_code = d.tool_code
 WHERE f.tool_code IS NULL
 ORDER BY d.is_pending, d.category_name, d.tool_code$q$, 'active'),
('sc_tool_problem_usage', '按智能体问题分类统计工具使用',
 '内容安全/知识规范/运行控制 → 工具分类；适合看问题域的工具覆盖。',
 'svc_postgresql', 'page:analytics/scenario/tool_problem_usage',
 $q$SELECT d.problem_l1_name, d.category_l2_name,
       count(f.tool_code) AS usage_count,
       count(DISTINCT f.tool_code) AS used_tool_count
  FROM diag.tool_problem_dimension_all d
  LEFT JOIN diag.agent_tool_call_fact f ON f.tool_code = d.tool_code
 GROUP BY 1,2
 ORDER BY 1,2$q$, 'active'),
('sc_context_tokens', 'Token 消耗（按会话/Agent/模型/日期）',
 '轮次事实汇总 prompt/completion/total/cached token；token 只在轮次层求和。',
 'svc_postgresql', 'page:analytics/scenario/context_tokens',
 $q$SELECT f.session_id, f.agent_name, f.model_id,
       count(*) AS turns,
       sum(f.prompt_tokens) AS prompt_tokens,
       sum(f.completion_tokens) AS completion_tokens,
       sum(f.total_tokens) AS total_tokens,
       sum(f.cached_tokens) AS cached_tokens
  FROM diag.agent_turn_fact f
 GROUP BY 1,2,3
 ORDER BY total_tokens DESC$q$, 'active'),
('sc_tool_return_chars', '工具返回体量/成功率',
 '工具调用事实汇总 result_chars / success；用于定位返回体过大的工具。',
 'svc_postgresql', 'page:analytics/scenario/tool_return_chars',
 $q$SELECT f.tool_code,
       count(*) AS calls,
       sum(f.result_chars) AS result_chars,
       avg(f.result_chars)::numeric(18,1) AS avg_result_chars,
       count(*) FILTER (WHERE f.tool_success) AS success_calls,
       count(*) FILTER (WHERE NOT f.tool_success) AS failed_calls
  FROM diag.agent_tool_call_fact f
 GROUP BY 1 ORDER BY calls DESC$q$, 'active')
ON CONFLICT (scenario_id) DO UPDATE
SET name = EXCLUDED.name, description = EXCLUDED.description,
    service_id = EXCLUDED.service_id, source_locator = EXCLUDED.source_locator,
    source_sql = EXCLUDED.source_sql, status = EXCLUDED.status;

-- 场景关联路径（工具维度 LEFT JOIN；审计页可读）
INSERT INTO analytics.scenario_join
(scenario_id, seq, left_schema, left_table, left_column,
 right_schema, right_table, right_column, join_type, condition, note) VALUES
('sc_tool_usage', 1, 'diag', 'agent_tool_call_fact', 'tool_code',
 'diag', 'tool_dimension_all', 'tool_code', 'left', 'tool_code', '工具维度 LEFT JOIN'),
('sc_tool_unused', 1, 'diag', 'tool_dimension', 'tool_code',
 'diag', 'agent_tool_call_fact', 'tool_code', 'left', 'tool_code', '未使用工具'),
('sc_tool_problem_usage', 1, 'diag', 'tool_problem_dimension_all', 'tool_code',
 'diag', 'agent_tool_call_fact', 'tool_code', 'left', 'tool_code', '2 层工具分类 LEFT JOIN'),
('sc_tool_return_chars', 1, 'diag', 'agent_tool_call_fact', 'tool_code',
 'diag', 'tool_dimension_all', 'tool_code', 'left', 'tool_code', '工具维度 LEFT JOIN')
ON CONFLICT (scenario_id, seq) DO UPDATE
SET left_schema = EXCLUDED.left_schema, left_table = EXCLUDED.left_table,
    left_column = EXCLUDED.left_column, right_schema = EXCLUDED.right_schema,
    right_table = EXCLUDED.right_table, right_column = EXCLUDED.right_column,
    join_type = EXCLUDED.join_type, condition = EXCLUDED.condition, note = EXCLUDED.note;

INSERT INTO analytics.scenario_fact
(scenario_id, fact_id, sort_order, note, status) VALUES
('sc_tool_usage', 'fact_agent_tool_call', 10, '工具使用场景来源事实', 'active'),
('sc_tool_unused', 'fact_agent_tool_call', 10, '未使用工具场景来源事实', 'active'),
('sc_tool_problem_usage', 'fact_agent_tool_call', 10, '问题分类场景来源事实', 'active'),
('sc_context_tokens', 'fact_agent_turn', 10, 'Token 场景来源事实', 'active'),
('sc_tool_return_chars', 'fact_agent_tool_call', 10, '返回体量场景来源事实', 'active')
ON CONFLICT (scenario_id, fact_id) DO UPDATE
SET sort_order = EXCLUDED.sort_order, note = EXCLUDED.note, status = EXCLUDED.status;

COMMIT;
