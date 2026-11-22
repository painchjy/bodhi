-- ============================================================
-- 事实表组成 + 事实↔事实 JOIN（2026-11-23）
--
-- 口径：
--   * field_mapping 只映射事实对象自己的事实表列；
--   * 维度表由 Dimension 的 binding + field_mapping 负责；
--   * scenario_join 只记录事实表↔事实表路径；
--   * 一个 Fact 可以由多张事实表 JOIN 组成。
-- ============================================================

BEGIN;

CREATE TABLE IF NOT EXISTS analytics.fact_table (
    fact_id        text NOT NULL REFERENCES analytics.fact(fact_id) ON DELETE CASCADE,
    table_id       text NOT NULL,
    source_schema  text NOT NULL DEFAULT '',
    source_table   text NOT NULL,
    is_primary     boolean NOT NULL DEFAULT false,
    grain_keys     jsonb NOT NULL DEFAULT '[]'::jsonb,
    sort_order     integer NOT NULL DEFAULT 0,
    status         text NOT NULL DEFAULT 'active'
                   CHECK (status IN ('active', 'inactive')),
    created_at     timestamptz NOT NULL DEFAULT now(),
    updated_at     timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (fact_id, table_id),
    UNIQUE (fact_id, source_schema, source_table)
);

CREATE INDEX IF NOT EXISTS ix_fact_table_fact ON analytics.fact_table(fact_id);
CREATE UNIQUE INDEX IF NOT EXISTS ux_fact_table_primary
    ON analytics.fact_table(fact_id) WHERE is_primary;

-- 现有事实：每个事实一张主事实表
INSERT INTO analytics.fact_table
(fact_id, table_id, source_schema, source_table, is_primary, grain_keys, sort_order, status) VALUES
('fact_agent_turn', 'turn_fact', 'diag', 'agent_turn_fact', true,
 '["session_id", "message_id"]'::jsonb, 10, 'active'),
('fact_agent_tool_call', 'tool_call_fact', 'diag', 'agent_tool_call_fact', true,
 '["session_id", "message_id", "tool_call_id"]'::jsonb, 10, 'active')
ON CONFLICT (fact_id, table_id) DO UPDATE
SET source_schema = EXCLUDED.source_schema, source_table = EXCLUDED.source_table,
    is_primary = EXCLUDED.is_primary, grain_keys = EXCLUDED.grain_keys,
    sort_order = EXCLUDED.sort_order, status = EXCLUDED.status;

-- 清理：事实字段映射到维表的行（旧案例 map_tool_category/subcategory）
DELETE FROM analytics.field_mapping m
 USING analytics.binding b
 WHERE m.binding_id = b.binding_id
   AND b.object_type = 'fact'
   AND EXISTS (
       SELECT 1
         FROM analytics.binding db
         JOIN analytics.field_mapping dm
           ON dm.binding_id = db.binding_id AND dm.status = 'active'
        WHERE db.object_type = 'dimension'
          AND db.status = 'active'
          AND dm.source_schema = m.source_schema
          AND dm.source_table = m.source_table
   );

-- 这些字段已经由 Dimension 的 category_name / category_l2_name 表达
DELETE FROM analytics.fact_field
 WHERE field_id IN ('ff_tool_category', 'ff_tool_subcategory');

-- 清理：scenario_join 中任一端不是该场景来源事实的事实表
DELETE FROM analytics.scenario_join j
 WHERE NOT EXISTS (
           SELECT 1
             FROM analytics.scenario_fact sf
             JOIN analytics.fact_table ft
               ON ft.fact_id = sf.fact_id
              AND ft.status = 'active'
              AND ft.source_schema = j.left_schema
              AND ft.source_table = j.left_table
            WHERE sf.scenario_id = j.scenario_id
              AND sf.status = 'active'
       )
    OR NOT EXISTS (
           SELECT 1
             FROM analytics.scenario_fact sf
             JOIN analytics.fact_table ft
               ON ft.fact_id = sf.fact_id
              AND ft.status = 'active'
              AND ft.source_schema = j.right_schema
              AND ft.source_table = j.right_table
            WHERE sf.scenario_id = j.scenario_id
              AND sf.status = 'active'
       );

-- ---------- 多事实表示例：工具调用 + 轮次上下文 ----------
INSERT INTO analytics.fact
(fact_id, code, name, description, grain, status) VALUES
('fact_agent_tool_context', 'agent_tool_context', 'AgentToolContextFact',
 '工具调用事实 + 所属轮次上下文；一次工具调用一行，轮次字段来自同一条 assistant 消息。',
 'session_id + message_id + tool_call_id', 'active')
ON CONFLICT (fact_id) DO UPDATE
SET code = EXCLUDED.code, name = EXCLUDED.name, description = EXCLUDED.description,
    grain = EXCLUDED.grain, status = EXCLUDED.status;

INSERT INTO analytics.fact_table
(fact_id, table_id, source_schema, source_table, is_primary, grain_keys, sort_order, status) VALUES
('fact_agent_tool_context', 'tool_call_fact', 'diag', 'agent_tool_call_fact', true,
 '["session_id", "message_id", "tool_call_id"]'::jsonb, 10, 'active'),
('fact_agent_tool_context', 'turn_context', 'diag', 'agent_turn_fact', false,
 '["session_id", "message_id"]'::jsonb, 20, 'active')
ON CONFLICT (fact_id, table_id) DO UPDATE
SET source_schema = EXCLUDED.source_schema, source_table = EXCLUDED.source_table,
    is_primary = EXCLUDED.is_primary, grain_keys = EXCLUDED.grain_keys,
    sort_order = EXCLUDED.sort_order, status = EXCLUDED.status;

INSERT INTO analytics.base_metric
(metric_id, fact_id, code, name, description, data_type, unit, aggregation,
 additivity, filter_expression, format, sort_order, status) VALUES
('bm_ctx_tool_call_count', 'fact_agent_tool_context', 'tool_call_count',
 '工具调用次数', '工具调用行计数。', 'integer', '次', 'count', 'additive',
 '', '#,##0', 10, 'active'),
('bm_ctx_tool_error_count', 'fact_agent_tool_context', 'tool_error_count',
 '工具失败次数', '只统计 tool_success=false 的调用。', 'integer', '次', 'count',
 'additive', 'tool_success = false', '#,##0', 20, 'active')
ON CONFLICT (metric_id) DO UPDATE
SET fact_id = EXCLUDED.fact_id, code = EXCLUDED.code, name = EXCLUDED.name,
    description = EXCLUDED.description, data_type = EXCLUDED.data_type,
    unit = EXCLUDED.unit, aggregation = EXCLUDED.aggregation,
    additivity = EXCLUDED.additivity, filter_expression = EXCLUDED.filter_expression,
    format = EXCLUDED.format, sort_order = EXCLUDED.sort_order, status = EXCLUDED.status;

INSERT INTO analytics.fact_field
(field_id, fact_id, code, name, description, data_type, role, is_enum, sort_order, status) VALUES
('ff_ctx_tool_code', 'fact_agent_tool_context', 'tool_code', '工具编码',
 '工具调用事实上的工具成员键。', 'text', 'key', false, 10, 'active'),
('ff_ctx_input_prompt', 'fact_agent_tool_context', 'input_prompt', '输入提示词',
 '所属轮次（同 message_id）的最近一条用户输入。', 'text', 'display', false, 20, 'active')
ON CONFLICT (field_id) DO UPDATE
SET fact_id = EXCLUDED.fact_id, code = EXCLUDED.code, name = EXCLUDED.name,
    description = EXCLUDED.description, data_type = EXCLUDED.data_type,
    role = EXCLUDED.role, is_enum = EXCLUDED.is_enum,
    sort_order = EXCLUDED.sort_order, status = EXCLUDED.status;


INSERT INTO analytics.binding
(binding_id, object_type, object_id, service_id, binding_type, tool_name,
 status, metadata_hash) VALUES
('bind_fact_agent_tool_context', 'fact', 'fact_agent_tool_context',
 'svc_postgresql', 'self_service', '', 'active', '{}'::jsonb)
ON CONFLICT (binding_id) DO UPDATE
SET object_type = EXCLUDED.object_type, object_id = EXCLUDED.object_id,
    service_id = EXCLUDED.service_id, binding_type = EXCLUDED.binding_type,
    tool_name = EXCLUDED.tool_name, status = EXCLUDED.status;

INSERT INTO analytics.field_mapping
(mapping_id, binding_id, semantic_kind, semantic_id, source_type, source_schema,
 source_table, source_column, data_type, aggregation, expression, sort_order, note, status) VALUES
('map_ctx_tool_code', 'bind_fact_agent_tool_context', 'non_metric_field', 'ff_ctx_tool_code',
 'mcp_table', 'diag', 'agent_tool_call_fact', 'tool_code', 'text', '', '', 10, '工具成员键', 'active'),
('map_ctx_input_prompt', 'bind_fact_agent_tool_context', 'non_metric_field', 'ff_ctx_input_prompt',
 'mcp_table', 'diag', 'agent_turn_fact', 'input_prompt', 'text', '', '', 20, '轮次上下文', 'active'),
('map_ctx_tool_call_count', 'bind_fact_agent_tool_context', 'base_metric', 'bm_ctx_tool_call_count',
 'mcp_table', 'diag', 'agent_tool_call_fact', 'tool_call_id', 'text', 'count', '', 30, '调用次数', 'active'),
('map_ctx_tool_error_count', 'bind_fact_agent_tool_context', 'base_metric', 'bm_ctx_tool_error_count',
 'mcp_table', 'diag', 'agent_tool_call_fact', 'tool_call_id', 'text', 'count',
 'tool_success = false', 40, '失败次数', 'active')
ON CONFLICT (mapping_id) DO UPDATE
SET binding_id = EXCLUDED.binding_id, semantic_kind = EXCLUDED.semantic_kind,
    semantic_id = EXCLUDED.semantic_id, source_type = EXCLUDED.source_type,
    source_schema = EXCLUDED.source_schema, source_table = EXCLUDED.source_table,
    source_column = EXCLUDED.source_column, data_type = EXCLUDED.data_type,
    aggregation = EXCLUDED.aggregation, expression = EXCLUDED.expression,
    sort_order = EXCLUDED.sort_order, note = EXCLUDED.note, status = EXCLUDED.status;


INSERT INTO analytics.scenario
(scenario_id, name, description, service_id, source_locator, source_sql, status) VALUES
('sc_tool_context', '工具调用上下文（输入提示词）',
 '工具调用事实 JOIN 所属轮次事实；用于同时查看工具指标与用户输入提示词。',
 'svc_postgresql', 'page:analytics/scenario/tool_context',
 $q$SELECT f.tool_code, f.tool_name, f.tool_success, t.input_prompt
   FROM diag.agent_tool_call_fact f
   LEFT JOIN diag.agent_turn_fact t ON t.message_id = f.message_id
  ORDER BY f.tool_code, f.message_id$q$, 'active')
ON CONFLICT (scenario_id) DO UPDATE
SET name = EXCLUDED.name, description = EXCLUDED.description,
    service_id = EXCLUDED.service_id, source_locator = EXCLUDED.source_locator,
    source_sql = EXCLUDED.source_sql, status = EXCLUDED.status;

INSERT INTO analytics.scenario_fact
(scenario_id, fact_id, sort_order, note, status) VALUES
('sc_tool_context', 'fact_agent_tool_context', 10,
 '工具调用 + 轮次上下文来源事实', 'active')
ON CONFLICT (scenario_id, fact_id) DO UPDATE
SET sort_order = EXCLUDED.sort_order, note = EXCLUDED.note, status = EXCLUDED.status;

INSERT INTO analytics.scenario_join
(scenario_id, seq, left_schema, left_table, left_column,
 right_schema, right_table, right_column, join_type, condition, note) VALUES
('sc_tool_context', 1, 'diag', 'agent_tool_call_fact', 'message_id',
 'diag', 'agent_turn_fact', 'message_id', 'left', 'message_id',
 '多事实表事实对象：工具调用挂回所属轮次')
ON CONFLICT (scenario_id, seq) DO UPDATE
SET left_schema = EXCLUDED.left_schema, left_table = EXCLUDED.left_table,
    left_column = EXCLUDED.left_column, right_schema = EXCLUDED.right_schema,
    right_table = EXCLUDED.right_table, right_column = EXCLUDED.right_column,
    join_type = EXCLUDED.join_type, condition = EXCLUDED.condition, note = EXCLUDED.note;

COMMIT;
