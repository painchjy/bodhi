-- ============================================================
-- diag 查询示例（智能体可据此生成 SQL）
-- 口径：
--   * 工具使用：diag.agent_tool_call_fact LEFT JOIN diag.tool_dimension_all
--   * 未使用工具：维度表 LEFT JOIN 事实，事实键 IS NULL
--   * 日期/小时：事实 event_date/event_hour（由 event_time 公式派生）
--   * agent/model/session：事实列直取，无需维度表
--   * token：用 diag.agent_turn_fact；不要按工具行重复 sum（工具事实重复携带轮次 token）
-- ============================================================

-- 1) 工具使用频次 + 按 Agent/模型对比（含未使用工具）
--    未使用工具会以 agent_name/model_id = NULL、usage_count = 0 出现。
SELECT d.tool_code,
       d.tool_name,
       d.category_name,
       f.agent_name,
       f.model_id,
       count(f.tool_code) AS usage_count
  FROM diag.tool_dimension_all d
  LEFT JOIN diag.agent_tool_call_fact f
    ON f.tool_code = d.tool_code
 GROUP BY d.tool_code, d.tool_name, d.category_name, f.agent_name, f.model_id
 ORDER BY usage_count DESC, d.tool_name, f.agent_name NULLS FIRST;

-- 2) 每个 Agent/模型 都要出现所有工具（含 0 次）
WITH agents AS (
    SELECT DISTINCT agent_name, model_id
      FROM diag.agent_tool_call_fact
     WHERE agent_name IS NOT NULL AND model_id IS NOT NULL
), usage AS (
    SELECT tool_code, agent_name, model_id, count(*) AS usage_count
      FROM diag.agent_tool_call_fact
     GROUP BY 1,2,3
)
SELECT d.tool_code, d.tool_name, a.agent_name, a.model_id,
       COALESCE(u.usage_count, 0) AS usage_count
  FROM diag.tool_dimension_all d
 CROSS JOIN agents a
  LEFT JOIN usage u
    ON u.tool_code = d.tool_code
   AND u.agent_name = a.agent_name
   AND u.model_id = a.model_id
 ORDER BY d.tool_name, a.agent_name, a.model_id;

-- 3) 未使用工具（维度表 LEFT JOIN 事实）
SELECT d.tool_code, d.tool_name, d.category_name, d.is_pending
  FROM diag.tool_dimension d
  LEFT JOIN diag.agent_tool_call_fact f ON f.tool_code = d.tool_code
 WHERE f.tool_code IS NULL
 ORDER BY d.is_pending, d.category_name, d.tool_code;

-- 4) 按 2 层问题分类统计工具使用
SELECT d.problem_l1_name, d.category_l2_name,
       count(f.tool_code) AS usage_count,
       count(DISTINCT f.tool_code) AS used_tool_count
  FROM diag.tool_problem_dimension_all d
  LEFT JOIN diag.agent_tool_call_fact f ON f.tool_code = d.tool_code
 GROUP BY 1,2
 ORDER BY 1,2;

-- 5) 日期/小时统计（不需要时间维度表）
SELECT f.event_date, f.event_hour,
       count(*) AS tool_calls,
       count(*) FILTER (WHERE NOT f.tool_success) AS failed_calls
  FROM diag.agent_tool_call_fact f
 GROUP BY 1,2
 ORDER BY 1,2;

-- 6) token 消耗：按会话 + Agent + 模型汇总
SELECT f.session_id, f.agent_name, f.model_id,
       count(*) AS turns,
       sum(f.prompt_tokens) AS prompt_tokens,
       sum(f.completion_tokens) AS completion_tokens,
       sum(f.total_tokens) AS total_tokens,
       sum(f.cached_tokens) AS cached_tokens
  FROM diag.agent_turn_fact f
 GROUP BY 1,2,3
 ORDER BY total_tokens DESC;

-- 7) token 消耗：按日期汇总
SELECT f.event_date,
       count(*) AS turns,
       sum(f.prompt_tokens) AS prompt_tokens,
       sum(f.completion_tokens) AS completion_tokens,
       sum(f.total_tokens) AS total_tokens
  FROM diag.agent_turn_fact f
 GROUP BY 1 ORDER BY 1;

-- 8) 工具返回体量 + 成功率（按工具）
SELECT f.tool_code,
       count(*) AS calls,
       sum(f.result_chars) AS result_chars,
       avg(f.result_chars)::numeric(18,1) AS avg_result_chars,
       count(*) FILTER (WHERE f.tool_success) AS success_calls,
       count(*) FILTER (WHERE NOT f.tool_success) AS failed_calls
  FROM diag.agent_tool_call_fact f
 GROUP BY 1 ORDER BY calls DESC;

-- 9) 单个会话的“token + 工具返回”对照
--    token 只在 turn 层求和；工具只求 result_chars，避免重复计算 token。
WITH turns AS (
    SELECT session_id, sum(total_tokens) AS total_tokens,
           sum(prompt_tokens) AS prompt_tokens,
           sum(completion_tokens) AS completion_tokens
      FROM diag.agent_turn_fact
     GROUP BY 1
), tools AS (
    SELECT session_id, count(*) AS tool_calls,
           sum(result_chars) AS result_chars,
           count(*) FILTER (WHERE NOT tool_success) AS failed_calls
      FROM diag.agent_tool_call_fact
     GROUP BY 1
)
SELECT t.session_id, t.prompt_tokens, t.completion_tokens, t.total_tokens,
       COALESCE(x.tool_calls, 0) AS tool_calls,
       COALESCE(x.result_chars, 0) AS result_chars,
       COALESCE(x.failed_calls, 0) AS failed_calls
  FROM turns t
  LEFT JOIN tools x ON x.session_id = t.session_id
 ORDER BY t.total_tokens DESC;
