-- ============================================================
-- diag · 运行诊断数据层（与事实数据同库）
--
-- 口径（2026-11-22）：
--   * 维度值必须和事实数据在同一查询侧（diag），才能 JOIN；
--     analytics 只保留维度层次 / 属性 / MCP 表字段映射，不保存工具分类值；
--   * 工具分类需要维度表：tool_dimension（系统 1 层）、
--     tool_problem_dimension（内容安全/知识规范/运行控制 2 层）；
--   * 日期/小时不建维度表：由事实时间列公式派生（日期、小时）；
--   * agent / session / model 不分层：直接从事实列取。
-- ============================================================

CREATE SCHEMA IF NOT EXISTS diag;

-- ---------- 工具维度：系统 1 层分类 ----------
CREATE TABLE IF NOT EXISTS diag.tool_dimension (
    tool_code     text PRIMARY KEY,
    tool_name     text NOT NULL,
    category_code text NOT NULL,
    category_name text NOT NULL,
    is_pending    boolean NOT NULL DEFAULT false,
    description   text NOT NULL DEFAULT '',
    updated_at    timestamptz NOT NULL DEFAULT now()
);

COMMENT ON TABLE diag.tool_dimension IS '工具维度（系统 1 层分类）；事实表按 tool_code JOIN，LEFT JOIN 可列出未使用工具。';

-- ---------- 工具维度：面向智能体问题的 2 层分类 ----------
CREATE TABLE IF NOT EXISTS diag.tool_problem_dimension (
    tool_code         text PRIMARY KEY,
    tool_name         text NOT NULL,
    problem_l1_code   text NOT NULL,
    problem_l1_name   text NOT NULL,
    category_l2_code  text NOT NULL,
    category_l2_name  text NOT NULL,
    is_pending        boolean NOT NULL DEFAULT false,
    description       text NOT NULL DEFAULT '',
    updated_at        timestamptz NOT NULL DEFAULT now()
);

COMMENT ON TABLE diag.tool_problem_dimension IS '工具维度（内容安全/知识规范/运行控制 → 工具分类）；事实表按 tool_code JOIN。';

CREATE INDEX IF NOT EXISTS ix_tool_dimension_category
    ON diag.tool_dimension(category_code);
CREATE INDEX IF NOT EXISTS ix_tool_problem_dimension_l1
    ON diag.tool_problem_dimension(problem_l1_code, category_l2_code);

-- ---------- 轮次事实：assistant 消息一行 ----------
-- 视图列有增删时 CREATE OR REPLACE 不能改列名/顺序，先删除本层视图（仅 diag 自有）。
DROP VIEW IF EXISTS diag.tool_dimension_all CASCADE;
DROP VIEW IF EXISTS diag.tool_problem_dimension_all CASCADE;
DROP VIEW IF EXISTS diag.agent_tool_call_fact CASCADE;
DROP VIEW IF EXISTS diag.agent_turn_fact CASCADE;
CREATE OR REPLACE VIEW diag.agent_turn_fact AS
SELECT m.id AS message_id,
       m.session_id,
       m.agent_id,
       COALESCE(a.name, m.agent_id) AS agent_name,
       m.model_id,
       m.created_at AS event_time,
       (m.created_at AT TIME ZONE 'Asia/Shanghai')::date AS event_date,
       EXTRACT(HOUR FROM (m.created_at AT TIME ZONE 'Asia/Shanghai'))::integer AS event_hour,
       COALESCE((m.usage->>'prompt_tokens')::bigint,
                (m.usage->>'input_tokens')::bigint, 0) AS prompt_tokens,
       COALESCE((m.usage->>'completion_tokens')::bigint,
                (m.usage->>'output_tokens')::bigint, 0) AS completion_tokens,
       COALESCE((m.usage->>'total_tokens')::bigint,
                COALESCE((m.usage->>'prompt_tokens')::bigint,
                         (m.usage->>'input_tokens')::bigint, 0)
                + COALESCE((m.usage->>'completion_tokens')::bigint,
                           (m.usage->>'output_tokens')::bigint, 0), 0) AS total_tokens,
       COALESCE((m.usage->>'cached_tokens')::bigint, 0) AS cached_tokens,
       COALESCE((m.usage->>'cache_read_tokens')::bigint, 0) AS cache_read_tokens,
       COALESCE((m.usage->>'cache_miss_tokens')::bigint, 0) AS cache_miss_tokens,
       COALESCE(m.agent_duration_ms, 0) AS agent_duration_ms,
       length(COALESCE(m.content, '')) AS content_chars,
       COALESCE(u.content, '') AS input_prompt
  FROM public.messages m
  LEFT JOIN public.custom_agents a
    ON a.id = m.agent_id AND a.deleted_at IS NULL
  LEFT JOIN LATERAL (
       SELECT um.content
         FROM public.messages um
        WHERE um.session_id = m.session_id
          AND um.role = 'user'
          AND um.deleted_at IS NULL
          AND um.created_at < m.created_at
        ORDER BY um.created_at DESC
        LIMIT 1
  ) u ON true
 WHERE m.role = 'assistant'
   AND m.deleted_at IS NULL;

COMMENT ON VIEW diag.agent_turn_fact IS 'Agent 轮次事实：来自 messages（assistant 一行）；token 来自 messages.usage，时间为消息时间。';

-- ---------- 工具调用事实：agent_steps[].tool_calls[] 展开一行一次 ----------
CREATE OR REPLACE VIEW diag.agent_tool_call_fact AS
SELECT m.id AS message_id,
       m.session_id,
       m.agent_id,
       COALESCE(a.name, m.agent_id) AS agent_name,
       m.model_id,
       m.created_at AS event_time,
       (m.created_at AT TIME ZONE 'Asia/Shanghai')::date AS event_date,
       EXTRACT(HOUR FROM (m.created_at AT TIME ZONE 'Asia/Shanghai'))::integer AS event_hour,
       COALESCE((s.step_json->>'iteration')::integer + 1, s.step_no::integer) AS round_no,
       tc.tc_no::integer AS tool_call_no,
       COALESCE(tc.tc_json->>'id',
                m.id || ':' || COALESCE(s.step_json->>'iteration', s.step_no::text)
                || ':' || tc.tc_no::text) AS tool_call_id,
       COALESCE(td.tool_code, NULLIF(tc.tc_json->>'name', '')) AS tool_code,
       COALESCE(tc.tc_json->>'name', '') AS tool_name,
       length(COALESCE((tc.tc_json->'args')::text, '')) AS args_chars,
       length(COALESCE((tc.tc_json->'result')::text, '')) AS result_chars,
       octet_length(COALESCE((tc.tc_json->'result')::text, '')) AS result_bytes,
       COALESCE((tc.tc_json->>'duration')::numeric, 0) AS duration_ms,
       COALESCE(tc.tc_json->'result'->>'error',
                tc.tc_json->'result'->>'error_code', '') AS error_code,
       CASE
           WHEN (tc.tc_json->'result') ? 'error' THEN false
           WHEN (tc.tc_json->'result'->>'success') IS NOT NULL
               THEN (tc.tc_json->'result'->>'success')::boolean
           ELSE true
       END AS tool_success,
       COALESCE((m.usage->>'prompt_tokens')::bigint,
                (m.usage->>'input_tokens')::bigint, 0) AS turn_prompt_tokens,
       COALESCE((m.usage->>'completion_tokens')::bigint,
                (m.usage->>'output_tokens')::bigint, 0) AS turn_completion_tokens,
       COALESCE((m.usage->>'total_tokens')::bigint,
                COALESCE((m.usage->>'prompt_tokens')::bigint,
                         (m.usage->>'input_tokens')::bigint, 0)
                + COALESCE((m.usage->>'completion_tokens')::bigint,
                           (m.usage->>'output_tokens')::bigint, 0), 0) AS turn_total_tokens,
       COALESCE((m.usage->>'cached_tokens')::bigint, 0) AS turn_cached_tokens,
       COALESCE((m.usage->>'cache_read_tokens')::bigint, 0) AS turn_cache_read_tokens,
       COALESCE((m.usage->>'cache_miss_tokens')::bigint, 0) AS turn_cache_miss_tokens
  FROM public.messages m
  LEFT JOIN public.custom_agents a
    ON a.id = m.agent_id AND a.deleted_at IS NULL
 CROSS JOIN LATERAL jsonb_array_elements(
       COALESCE(m.agent_steps, '[]'::jsonb)
   ) WITH ORDINALITY AS s(step_json, step_no)
 CROSS JOIN LATERAL jsonb_array_elements(
       COALESCE(s.step_json->'tool_calls', '[]'::jsonb)
   ) WITH ORDINALITY AS tc(tc_json, tc_no)
  LEFT JOIN LATERAL (
       SELECT d.tool_code
         FROM diag.tool_dimension d
        WHERE d.tool_code = COALESCE(tc.tc_json->>'name', '')
           OR COALESCE(tc.tc_json->>'name', '') LIKE 'mcp_%_' || d.tool_code
        LIMIT 1
  ) td ON true

 WHERE m.role = 'assistant'
   AND m.deleted_at IS NULL;

COMMENT ON VIEW diag.agent_tool_call_fact IS 'Agent 工具调用事实：一行一次工具调用；tool_success/result_chars 来自 agent_steps；turn_*_tokens 是所属轮次的 usage（聚合时不要跨工具重复求和）。';

-- ---------- 有效工具维度：维度表 + 事实里出现但未登记的“未分类”工具 ----------
CREATE OR REPLACE VIEW diag.tool_dimension_all AS
SELECT tool_code, tool_name, category_code, category_name, is_pending
  FROM diag.tool_dimension
UNION ALL
SELECT DISTINCT f.tool_code, f.tool_name, 'unclassified', '未分类', false
  FROM diag.agent_tool_call_fact f
  LEFT JOIN diag.tool_dimension d ON d.tool_code = f.tool_code
 WHERE d.tool_code IS NULL AND f.tool_code <> '';

COMMENT ON VIEW diag.tool_dimension_all IS '工具使用统计用：已登记工具 + 事实里出现但维度表未登记的工具（未分类）。';

CREATE OR REPLACE VIEW diag.tool_problem_dimension_all AS
SELECT tool_code, tool_name, problem_l1_code, problem_l1_name,
       category_l2_code, category_l2_name, is_pending
  FROM diag.tool_problem_dimension
UNION ALL
SELECT DISTINCT f.tool_code, f.tool_name, 'unclassified', '未分类',
       'unclassified', '未分类', false
  FROM diag.agent_tool_call_fact f
  LEFT JOIN diag.tool_problem_dimension d ON d.tool_code = f.tool_code
 WHERE d.tool_code IS NULL AND f.tool_code <> '';

COMMENT ON VIEW diag.tool_problem_dimension_all IS '2 层工具分类统计用：已登记工具 + 未登记工具（未分类）。';
