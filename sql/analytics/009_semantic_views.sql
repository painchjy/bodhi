-- 语义视图（在 008 结构迁移后执行）
DROP VIEW IF EXISTS analytics.v_fact_spec CASCADE;
DROP VIEW IF EXISTS analytics.v_dimension_spec CASCADE;
DROP VIEW IF EXISTS analytics.v_fact_dimension_path CASCADE;

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
