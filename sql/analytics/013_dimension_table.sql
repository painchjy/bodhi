-- ============================================================
-- 维度物理表/视图登记（2026-11-23）
--
-- 用途：语义提取扫描场景 SQL 时，需要把 `diag.tool_dimension_all` 这类
-- **维度视图**稳定识别成维度，而不是误判为事实表。
-- 只登记表级来源；列级映射仍在 binding + field_mapping。
-- ============================================================

BEGIN;

CREATE TABLE IF NOT EXISTS analytics.dimension_table (
    dimension_id   text NOT NULL REFERENCES analytics.dimension(dimension_id) ON DELETE CASCADE,
    table_id       text NOT NULL,
    source_schema  text NOT NULL DEFAULT '',
    source_table   text NOT NULL,
    table_role     text NOT NULL DEFAULT 'base'
                   CHECK (table_role IN ('base', 'view')),
    sort_order     integer NOT NULL DEFAULT 0,
    status         text NOT NULL DEFAULT 'active'
                   CHECK (status IN ('active', 'inactive')),
    created_at     timestamptz NOT NULL DEFAULT now(),
    updated_at     timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (dimension_id, table_id),
    UNIQUE (dimension_id, source_schema, source_table)
);

COMMENT ON TABLE analytics.dimension_table IS '维度对象的物理表/视图来源：用于把场景 SQL 中的维表识别为 Dimension；列级映射仍在 field_mapping。';

CREATE INDEX IF NOT EXISTS ix_dimension_table_dim ON analytics.dimension_table(dimension_id);

DROP TRIGGER IF EXISTS trg_dimension_table_updated ON analytics.dimension_table;
CREATE TRIGGER trg_dimension_table_updated BEFORE UPDATE ON analytics.dimension_table
FOR EACH ROW EXECUTE FUNCTION analytics.touch_updated_at();

INSERT INTO analytics.dimension_table
(dimension_id, table_id, source_schema, source_table, table_role, sort_order, status) VALUES
('dim_tool_category_1level', 'tool_dimension', 'diag', 'tool_dimension', 'base', 10, 'active'),
('dim_tool_category_1level', 'tool_dimension_all', 'diag', 'tool_dimension_all', 'view', 20, 'active'),
('dim_tool_category_2level', 'tool_problem_dimension', 'diag', 'tool_problem_dimension', 'base', 10, 'active'),
('dim_tool_category_2level', 'tool_problem_dimension_all', 'diag', 'tool_problem_dimension_all', 'view', 20, 'active')
ON CONFLICT (dimension_id, table_id) DO UPDATE
SET source_schema = EXCLUDED.source_schema, source_table = EXCLUDED.source_table,
    table_role = EXCLUDED.table_role, sort_order = EXCLUDED.sort_order,
    status = EXCLUDED.status;

COMMIT;
