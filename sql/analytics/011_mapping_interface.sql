-- ============================================================
-- 知识页 ↔ analytics 映射表的唯一只读接口
--   mapping_object_type/id 由 fact/dimension 内部 ID 投影
--   mapping_service_id 由 binding.service_id 投影
-- ============================================================
DROP VIEW IF EXISTS analytics.v_mapping_interface CASCADE;
CREATE OR REPLACE VIEW analytics.v_mapping_interface AS
SELECT 'fact'::text AS mapping_object_type,
       f.fact_id AS mapping_object_id,
       f.code AS object_code,
       f.name AS object_name,
       'analytics/fact/' || f.code AS page_slug,
       b.binding_id,
       b.service_id AS mapping_service_id,
       s.service_name,
       s.database_type,
       b.status AS binding_status,
       s.status AS service_status,
       false AS formula_only
  FROM analytics.fact f
  LEFT JOIN analytics.binding b
    ON b.object_type = 'fact' AND b.object_id = f.fact_id
  LEFT JOIN analytics.mcp_service s ON s.service_id = b.service_id
UNION ALL
SELECT 'dimension'::text AS mapping_object_type,
       d.dimension_id AS mapping_object_id,
       d.code AS object_code,
       d.name AS object_name,
       'analytics/dimension/' || d.code AS page_slug,
       b.binding_id,
       b.service_id AS mapping_service_id,
       s.service_name,
       s.database_type,
       b.status AS binding_status,
       s.status AS service_status,
       EXISTS (SELECT 1 FROM analytics.dimension_level l
                WHERE l.dimension_id=d.dimension_id) AS formula_only
  FROM analytics.dimension d
  LEFT JOIN analytics.binding b
    ON b.object_type = 'dimension' AND b.object_id = d.dimension_id
  LEFT JOIN analytics.mcp_service s ON s.service_id = b.service_id;
