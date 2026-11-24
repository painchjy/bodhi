-- ============================================================
-- 场景来源事实（2026-11-22 用户确认）
--   * Scenario → Fact 只表达来源，不表达使用/补充
--   * 物理 join/filter 继续放 scenario_join / scenario_filter
-- ============================================================
BEGIN;

CREATE TABLE IF NOT EXISTS analytics.scenario_fact (
    scenario_id    text NOT NULL REFERENCES analytics.scenario(scenario_id) ON DELETE CASCADE,
    fact_id        text NOT NULL REFERENCES analytics.fact(fact_id) ON DELETE CASCADE,
    sort_order     integer NOT NULL DEFAULT 0,
    note           text NOT NULL DEFAULT '',
    status         text NOT NULL DEFAULT 'active'
                   CHECK (status IN ('active', 'inactive')),
    PRIMARY KEY (scenario_id, fact_id)
);

DO $$
BEGIN
    IF to_regclass('analytics.binding_scenario') IS NOT NULL THEN
        INSERT INTO analytics.scenario_fact
        (scenario_id, fact_id, sort_order, note, status)
        SELECT bs.scenario_id, b.object_id, 10,
               '迁移自 binding_scenario', 'active'
          FROM analytics.binding_scenario bs
          JOIN analytics.binding b ON b.binding_id = bs.binding_id
         WHERE b.object_type = 'fact'
        ON CONFLICT (scenario_id, fact_id) DO NOTHING;
        DROP TABLE analytics.binding_scenario;
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS ix_scenario_fact_fact
    ON analytics.scenario_fact(fact_id);

DROP VIEW IF EXISTS analytics.v_scenario_detail CASCADE;
DROP VIEW IF EXISTS analytics.v_scenario_fact CASCADE;

CREATE OR REPLACE VIEW analytics.v_scenario_fact AS
SELECT sf.scenario_id, sc.name AS scenario_name,
       sf.fact_id, f.code AS fact_code, f.name AS fact_name,
       sf.sort_order, sf.note, sf.status
  FROM analytics.scenario_fact sf
  JOIN analytics.scenario sc ON sc.scenario_id = sf.scenario_id
  JOIN analytics.fact f ON f.fact_id = sf.fact_id;

CREATE OR REPLACE VIEW analytics.v_scenario_detail AS
SELECT s.scenario_id, s.name, s.description, s.service_id, sc.service_name,
       sc.database_type, s.source_locator, s.source_sql, s.semantic_json, s.status,
       (SELECT coalesce(jsonb_agg(to_jsonb(j) ORDER BY j.seq), '[]'::jsonb)
          FROM analytics.scenario_join j
         WHERE j.scenario_id = s.scenario_id) AS joins,
       (SELECT coalesce(jsonb_agg(to_jsonb(x) ORDER BY x.seq), '[]'::jsonb)
          FROM analytics.scenario_filter x
         WHERE x.scenario_id = s.scenario_id) AS filters,
       (SELECT coalesce(jsonb_agg(to_jsonb(f) ORDER BY f.sort_order, f.fact_id), '[]'::jsonb)
          FROM analytics.v_scenario_fact f
         WHERE f.scenario_id = s.scenario_id AND f.status = 'active') AS facts,
       (SELECT coalesce(jsonb_agg(x.service_id ORDER BY x.service_id), '[]'::jsonb)
          FROM (
              SELECT b.service_id
                FROM analytics.scenario_fact sf
                JOIN analytics.binding b
                  ON b.object_type = 'fact' AND b.object_id = sf.fact_id
                 AND b.status = 'active'
               WHERE sf.scenario_id = s.scenario_id AND sf.status = 'active'
               GROUP BY b.service_id
              HAVING count(DISTINCT sf.fact_id) = (
                  SELECT count(*) FROM analytics.scenario_fact z
                   WHERE z.scenario_id = s.scenario_id AND z.status = 'active')
          ) x) AS common_services,
       (s.service_id = ANY (ARRAY(
           SELECT b.service_id
             FROM analytics.scenario_fact sf
             JOIN analytics.binding b
               ON b.object_type = 'fact' AND b.object_id = sf.fact_id
              AND b.status = 'active'
            WHERE sf.scenario_id = s.scenario_id AND sf.status = 'active'
            GROUP BY b.service_id
           HAVING count(DISTINCT sf.fact_id) = (
               SELECT count(*) FROM analytics.scenario_fact z
                WHERE z.scenario_id = s.scenario_id AND z.status = 'active')
       ))) AS has_common_service
  FROM analytics.scenario s
  LEFT JOIN analytics.mcp_service sc ON sc.service_id = s.service_id;

COMMIT;
