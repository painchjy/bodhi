-- ============================================================
-- 语义层重构（2026-11-22，用户确认）
--   * Fact↔Dimension 只保留语义键关系，不存物理表/列/公式
--   * 维度成员键 + 层级公式在 dimension / dimension_level
--   * 物理映射复用 binding + field_mapping
--   * 删除 dimension_mapping / dimension_value / dim_tool
-- ============================================================

BEGIN;

ALTER TABLE analytics.dimension
    ADD COLUMN IF NOT EXISTS member_key text NOT NULL DEFAULT '';
ALTER TABLE analytics.dimension_level
    ADD COLUMN IF NOT EXISTS formula_kind text NOT NULL DEFAULT 'direct',
    ADD COLUMN IF NOT EXISTS formula_params jsonb NOT NULL DEFAULT '{}'::jsonb,
    ADD COLUMN IF NOT EXISTS data_type text NOT NULL DEFAULT 'text';
ALTER TABLE analytics.fact_dimension
    ADD COLUMN IF NOT EXISTS fact_key text NOT NULL DEFAULT '',
    ADD COLUMN IF NOT EXISTS dimension_key text NOT NULL DEFAULT '',
    ADD COLUMN IF NOT EXISTS join_type text NOT NULL DEFAULT 'none',
    ADD COLUMN IF NOT EXISTS status text NOT NULL DEFAULT 'active';

ALTER TABLE analytics.fact_dimension DROP CONSTRAINT IF EXISTS fact_dimension_pkey;
DELETE FROM analytics.fact_dimension a
 USING analytics.fact_dimension b
 WHERE a.ctid < b.ctid
   AND a.fact_id = b.fact_id
   AND a.dimension_id = b.dimension_id;
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
         WHERE conname = 'fact_dimension_pkey'
           AND conrelid = 'analytics.fact_dimension'::regclass
    ) THEN
        ALTER TABLE analytics.fact_dimension
            ADD CONSTRAINT fact_dimension_pkey PRIMARY KEY (fact_id, dimension_id);
    END IF;
END $$;

DELETE FROM analytics.fact_dimension WHERE dimension_id = 'dim_tool';
DELETE FROM analytics.dimension WHERE dimension_id = 'dim_tool';

UPDATE analytics.dimension SET member_key = 'tool_code'
 WHERE dimension_id IN ('dim_tool_category_1level', 'dim_tool_category_2level');
UPDATE analytics.dimension SET member_key = 'agent_name' WHERE dimension_id = 'dim_agent';
UPDATE analytics.dimension SET member_key = 'model_id' WHERE dimension_id = 'dim_model';
UPDATE analytics.dimension SET member_key = 'session_id' WHERE dimension_id = 'dim_session';
UPDATE analytics.dimension SET member_key = 'event_time' WHERE dimension_id = 'dim_time';

UPDATE analytics.dimension_level
   SET formula_kind = 'date', data_type = 'date',
       formula_params = '{"timezone":"Asia/Shanghai"}'::jsonb
 WHERE dimension_id = 'dim_time' AND level_no = 1;
UPDATE analytics.dimension_level
   SET formula_kind = 'hour', data_type = 'integer',
       formula_params = '{"timezone":"Asia/Shanghai"}'::jsonb
 WHERE dimension_id = 'dim_time' AND level_no = 2;
UPDATE analytics.dimension_level
   SET formula_kind = 'direct', data_type = 'text',
       formula_params = '{}'::jsonb
 WHERE formula_kind IS NULL OR formula_kind = '';

-- 2 层问题维度的层级/属性用语义名，和 field_mapping 的 semantic_id 对齐
UPDATE analytics.dimension_level SET attribute_code = 'problem_l1_name'
 WHERE dimension_id = 'dim_tool_category_2level' AND level_no = 1;
UPDATE analytics.dimension_level SET attribute_code = 'category_l2_name'
 WHERE dimension_id = 'dim_tool_category_2level' AND level_no = 2;

DELETE FROM analytics.dimension_attribute
 WHERE dimension_id = 'dim_tool_category_2level';
INSERT INTO analytics.dimension_attribute
(dimension_id, attribute_code, name, data_type, is_enum, description, sort_order)
VALUES
('dim_tool_category_2level', 'problem_l1_name', '问题大类', 'text', false, '第一层分类。', 1),
('dim_tool_category_2level', 'category_l2_name', '工具分类', 'text', false, '第二层分类。', 2)
ON CONFLICT (dimension_id, attribute_code) DO UPDATE
SET name = EXCLUDED.name, data_type = EXCLUDED.data_type,
    is_enum = EXCLUDED.is_enum, description = EXCLUDED.description,
    sort_order = EXCLUDED.sort_order;

DELETE FROM analytics.fact_dimension
 WHERE fact_id = 'fact_agent_turn'
   AND dimension_id IN ('dim_tool_category_1level', 'dim_tool_category_2level');

UPDATE analytics.fact_dimension fd SET
    fact_key = x.fact_key, dimension_key = x.dimension_key, join_type = x.join_type
  FROM (VALUES
    ('fact_agent_turn', 'dim_agent', 'agent_name', 'agent_name', 'none'),
    ('fact_agent_turn', 'dim_model', 'model_id', 'model_id', 'none'),
    ('fact_agent_turn', 'dim_session', 'session_id', 'session_id', 'none'),
    ('fact_agent_turn', 'dim_time', 'event_time', 'event_time', 'none'),
    ('fact_agent_tool_call', 'dim_agent', 'agent_name', 'agent_name', 'none'),
    ('fact_agent_tool_call', 'dim_model', 'model_id', 'model_id', 'none'),
    ('fact_agent_tool_call', 'dim_session', 'session_id', 'session_id', 'none'),
    ('fact_agent_tool_call', 'dim_time', 'event_time', 'event_time', 'none'),
    ('fact_agent_tool_call', 'dim_tool_category_1level', 'tool_code', 'tool_code', 'left'),
    ('fact_agent_tool_call', 'dim_tool_category_2level', 'tool_code', 'tool_code', 'left')
  ) AS x(fact_id, dimension_id, fact_key, dimension_key, join_type)
 WHERE fd.fact_id = x.fact_id AND fd.dimension_id = x.dimension_id;

INSERT INTO analytics.binding
(binding_id, object_type, object_id, service_id, binding_type, tool_name, status, metadata_hash)
VALUES
('bind_dim_tool_category_1level', 'dimension', 'dim_tool_category_1level',
 'svc_postgresql', 'self_service', '', 'active', '{}'::jsonb),
('bind_dim_tool_category_2level', 'dimension', 'dim_tool_category_2level',
 'svc_postgresql', 'self_service', '', 'active', '{}'::jsonb)
ON CONFLICT (binding_id) DO UPDATE
SET object_type = EXCLUDED.object_type, object_id = EXCLUDED.object_id,
    service_id = EXCLUDED.service_id, binding_type = EXCLUDED.binding_type,
    tool_name = EXCLUDED.tool_name, status = EXCLUDED.status;

DELETE FROM analytics.field_mapping WHERE mapping_id LIKE 'map_dim_toolcat%';

INSERT INTO analytics.field_mapping
(mapping_id, binding_id, semantic_kind, semantic_id, source_type, source_schema,
 source_table, source_column, data_type, aggregation, expression, sort_order, note, status) VALUES
('map_dim_toolcat1_key', 'bind_dim_tool_category_1level', 'dimension_key',
 'dim_tool_category_1level.tool_code', 'mcp_table', 'diag', 'tool_dimension',
 'tool_code', 'text', '', '', 10, '用途分类成员键', 'active'),
('map_dim_toolcat1_name', 'bind_dim_tool_category_1level', 'dimension_attribute',
 'dim_tool_category_1level.tool_category', 'mcp_table', 'diag', 'tool_dimension',
 'category_name', 'text', '', '', 20, '用途分类名称', 'active'),
('map_dim_toolcat2_key', 'bind_dim_tool_category_2level', 'dimension_key',
 'dim_tool_category_2level.tool_code', 'mcp_table', 'diag', 'tool_problem_dimension',
 'tool_code', 'text', '', '', 10, '问题分类成员键', 'active'),
('map_dim_toolcat2_l1', 'bind_dim_tool_category_2level', 'dimension_attribute',
 'dim_tool_category_2level.problem_l1_name', 'mcp_table', 'diag', 'tool_problem_dimension',
 'problem_l1_name', 'text', '', '', 20, '问题第一层', 'active'),
('map_dim_toolcat2_l2', 'bind_dim_tool_category_2level', 'dimension_attribute',
 'dim_tool_category_2level.category_l2_name', 'mcp_table', 'diag', 'tool_problem_dimension',
 'category_l2_name', 'text', '', '', 30, '工具分类第二层', 'active')
ON CONFLICT (mapping_id) DO UPDATE
SET binding_id = EXCLUDED.binding_id, semantic_kind = EXCLUDED.semantic_kind,
    semantic_id = EXCLUDED.semantic_id, source_type = EXCLUDED.source_type,
    source_schema = EXCLUDED.source_schema, source_table = EXCLUDED.source_table,
    source_column = EXCLUDED.source_column, data_type = EXCLUDED.data_type,
    sort_order = EXCLUDED.sort_order, note = EXCLUDED.note, status = EXCLUDED.status;

DROP VIEW IF EXISTS analytics.v_dimension_mapping CASCADE;
DROP TABLE IF EXISTS analytics.dimension_mapping CASCADE;
DROP TABLE IF EXISTS analytics.dimension_value CASCADE;

CREATE OR REPLACE VIEW analytics.v_fact_dimension_path AS
SELECT fd.fact_id, f.code AS fact_code, f.name AS fact_name,
       fd.dimension_id, d.code AS dimension_code, d.name AS dimension_name,
       fd.key_code, fd.role, fd.sort_order,
       fd.fact_key, fd.dimension_key, fd.join_type, fd.status,
       (SELECT count(*)::integer FROM analytics.dimension_level l
         WHERE l.dimension_id = d.dimension_id) AS level_count,
       (SELECT coalesce(jsonb_agg(l.level_name ORDER BY l.level_no), '[]'::jsonb)
          FROM analytics.dimension_level l
         WHERE l.dimension_id = d.dimension_id) AS level_names
  FROM analytics.fact_dimension fd
  JOIN analytics.fact f ON f.fact_id = fd.fact_id
  JOIN analytics.dimension d ON d.dimension_id = fd.dimension_id;

CREATE OR REPLACE VIEW analytics.v_dimension_spec AS
SELECT d.dimension_id, d.code, d.name, d.description, d.member_key, d.status,
       (SELECT coalesce(jsonb_agg(to_jsonb(l) ORDER BY l.level_no), '[]'::jsonb)
          FROM analytics.dimension_level l WHERE l.dimension_id = d.dimension_id) AS levels,
       (SELECT coalesce(jsonb_agg(to_jsonb(a) ORDER BY a.sort_order, a.attribute_code), '[]'::jsonb)
          FROM analytics.dimension_attribute a WHERE a.dimension_id = d.dimension_id) AS attributes,
       (SELECT coalesce(jsonb_agg(to_jsonb(p) ORDER BY p.fact_id), '[]'::jsonb)
          FROM analytics.v_fact_dimension_path p WHERE p.dimension_id = d.dimension_id) AS facts
  FROM analytics.dimension d;

CREATE OR REPLACE VIEW analytics.v_fact_spec AS
SELECT f.fact_id, f.code, f.name, f.description, f.grain, f.status,
       (SELECT coalesce(jsonb_agg(to_jsonb(b) ORDER BY b.sort_order, b.metric_id), '[]'::jsonb)
          FROM analytics.base_metric b WHERE b.fact_id = f.fact_id) AS base_metrics,
       (SELECT coalesce(jsonb_agg(to_jsonb(d) ORDER BY d.sort_order, d.metric_id), '[]'::jsonb)
          FROM analytics.derived_metric d WHERE d.fact_id = f.fact_id) AS derived_metrics,
       (SELECT coalesce(jsonb_agg(to_jsonb(x) ORDER BY x.sort_order, x.field_id), '[]'::jsonb)
          FROM analytics.fact_field x WHERE x.fact_id = f.fact_id) AS fact_fields,
       (SELECT coalesce(jsonb_agg(to_jsonb(p) ORDER BY p.sort_order, p.dimension_id), '[]'::jsonb)
          FROM analytics.v_fact_dimension_path p WHERE p.fact_id = f.fact_id) AS dimensions
  FROM analytics.fact f;

COMMIT;
