-- ============================================================
-- 自助取数分析 · 配置层（PostgreSQL）
--
-- 真源口径（2026-11-21 用户确认）：
--   * 维度 / 事实 / 基础指标 / 派生指标 / 非指标字段  → 本 schema 的表
--   * 绑定 / 字段映射 / 枚举映射 / 场景 join / 固定筛选 → 本 schema 的表
--   * wiki 页只读投影 Fact / Dimension / StandardQueryScenario；
--     映射不生成知识页，只供管理界面配置与校验
--   * 基础指标与派生指标分表，派生指标带 expression_language + expression
--   * database_type 控制取值：oceanbase / gaussdb / hetu / postgresql / mysql
--
-- 说明：
--   1. 本 schema 是 WeKnora 所在 PostgreSQL 的配置 schema，不是业务库；
--   2. 业务库可以是 OceanBase / GaussDB / 河图 / PostgreSQL / MySQL，
--      由 analytics.mcp_service.database_type 标识，智能体据此写对应 SQL；
--   3. 物理表/字段只在 field_mapping 中出现，不进 wiki 页。
-- ============================================================

CREATE SCHEMA IF NOT EXISTS analytics;

CREATE OR REPLACE FUNCTION analytics.touch_updated_at()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$;

-- ============================================================
-- 1. 维度
-- ============================================================
CREATE TABLE IF NOT EXISTS analytics.dimension (
    dimension_id   text PRIMARY KEY,
    code           text NOT NULL UNIQUE,
    name           text NOT NULL,
    description    text NOT NULL DEFAULT '',
    member_key     text NOT NULL DEFAULT '',
    status         text NOT NULL DEFAULT 'active'
                   CHECK (status IN ('active', 'inactive')),
    created_at     timestamptz NOT NULL DEFAULT now(),
    updated_at     timestamptz NOT NULL DEFAULT now()
);

COMMENT ON TABLE analytics.dimension IS '分析维度：一个维度一条；member_key 是语义成员键，层级/属性见 level/attribute。物理映射只在 binding + field_mapping。';

CREATE TABLE IF NOT EXISTS analytics.dimension_level (
    dimension_id   text NOT NULL REFERENCES analytics.dimension(dimension_id) ON DELETE CASCADE,
    level_no       integer NOT NULL CHECK (level_no > 0),
    level_name     text NOT NULL,
    attribute_code text NOT NULL,
    description    text NOT NULL DEFAULT '',
    formula_kind   text NOT NULL DEFAULT 'direct'
                   CHECK (formula_kind IN ('direct', 'date', 'hour', 'date_trunc', 'custom')),
    formula_params jsonb NOT NULL DEFAULT '{}'::jsonb,
    data_type      text NOT NULL DEFAULT 'text',
    PRIMARY KEY (dimension_id, level_no)
);

COMMENT ON TABLE analytics.dimension_level IS '维度层级：level_no=1 为最上层；同一个维度可以有 1 层、2 层或更多层。';

CREATE TABLE IF NOT EXISTS analytics.dimension_attribute (
    dimension_id   text NOT NULL REFERENCES analytics.dimension(dimension_id) ON DELETE CASCADE,
    attribute_code text NOT NULL,
    name           text NOT NULL,
    data_type      text NOT NULL DEFAULT 'text',
    is_enum        boolean NOT NULL DEFAULT false,
    description    text NOT NULL DEFAULT '',
    sort_order     integer NOT NULL DEFAULT 0,
    PRIMARY KEY (dimension_id, attribute_code)
);

COMMENT ON TABLE analytics.dimension_attribute IS '维度属性：层级引用 attribute_code；枚举值映射在 enum_mapping。';

-- 维度对象的物理表/视图来源：把场景 SQL 里的维表稳定识别成 Dimension。
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

COMMENT ON TABLE analytics.dimension_table IS '维度对象物理表/视图来源：用于语义提取识别维表；列级映射仍在 field_mapping。';

-- 2026-11-22：dimension_value / dimension_mapping 已删除。
-- 维度成员值在 diag.tool_dimension / diag.tool_problem_dimension；
-- 物理映射只在 binding + field_mapping；派生公式只在 dimension_level.formula_*。

-- ============================================================
-- 2. 事实与事实维度键
-- ============================================================
CREATE TABLE IF NOT EXISTS analytics.fact (
    fact_id        text PRIMARY KEY,
    code           text NOT NULL UNIQUE,
    name           text NOT NULL,
    description    text NOT NULL DEFAULT '',
    grain          text NOT NULL,
    status         text NOT NULL DEFAULT 'active'
                   CHECK (status IN ('active', 'inactive')),
    created_at     timestamptz NOT NULL DEFAULT now(),
    updated_at     timestamptz NOT NULL DEFAULT now()
);

COMMENT ON TABLE analytics.fact IS '分析事实：grain 为一行代表的业务粒度；指标与字段见 base_metric / derived_metric / fact_field。';

CREATE TABLE IF NOT EXISTS analytics.fact_dimension (
    fact_id        text NOT NULL REFERENCES analytics.fact(fact_id) ON DELETE CASCADE,
    dimension_id   text NOT NULL REFERENCES analytics.dimension(dimension_id) ON DELETE CASCADE,
    key_code       text NOT NULL DEFAULT '',
    role           text NOT NULL DEFAULT 'group_by'
                   CHECK (role IN ('group_by', 'filter', 'drill')),
    fact_key       text NOT NULL DEFAULT '',
    dimension_key  text NOT NULL DEFAULT '',
    join_type      text NOT NULL DEFAULT 'none'
                   CHECK (join_type IN ('none', 'left', 'inner')),
    status         text NOT NULL DEFAULT 'active'
                   CHECK (status IN ('active', 'inactive')),
    sort_order     integer NOT NULL DEFAULT 0,
    PRIMARY KEY (fact_id, dimension_id)
);

COMMENT ON TABLE analytics.fact_dimension IS '事实可用的维度（实例级事实→维度通路）；事实页「维度键」由本表生成。';

-- 事实对象允许的物理事实表：一个 Fact 可以由一张或多张事实表 JOIN 组成。
-- 这里只登记表级组成/主表/粒度键；列级物理映射仍只在 field_mapping。
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

COMMENT ON TABLE analytics.fact_table IS '事实对象的物理事实表组成：一个 Fact 可由多张事实表 JOIN；只登记表/主表/粒度，列级映射仍在 field_mapping。';

-- ============================================================
-- 3. 指标：基础与派生分表
-- ============================================================
CREATE TABLE IF NOT EXISTS analytics.base_metric (
    metric_id          text PRIMARY KEY,
    fact_id            text NOT NULL REFERENCES analytics.fact(fact_id) ON DELETE CASCADE,
    code               text NOT NULL,
    name               text NOT NULL,
    description        text NOT NULL DEFAULT '',
    data_type          text NOT NULL DEFAULT 'numeric',
    unit               text NOT NULL DEFAULT '',
    aggregation        text NOT NULL
                       CHECK (aggregation IN ('sum', 'count', 'count_distinct', 'avg', 'min', 'max', 'none')),
    additivity         text NOT NULL DEFAULT 'additive'
                       CHECK (additivity IN ('additive', 'semi_additive', 'non_additive')),
    filter_expression  text NOT NULL DEFAULT '',
    format             text NOT NULL DEFAULT '',
    sort_order         integer NOT NULL DEFAULT 0,
    status             text NOT NULL DEFAULT 'active'
                       CHECK (status IN ('active', 'inactive')),
    created_at         timestamptz NOT NULL DEFAULT now(),
    updated_at         timestamptz NOT NULL DEFAULT now(),
    UNIQUE (fact_id, code)
);

COMMENT ON TABLE analytics.base_metric IS '基础指标：直接来自物理字段；aggregation + filter_expression 表达口径。';

CREATE TABLE IF NOT EXISTS analytics.derived_metric (
    metric_id           text PRIMARY KEY,
    fact_id             text NOT NULL REFERENCES analytics.fact(fact_id) ON DELETE CASCADE,
    code                text NOT NULL,
    name                text NOT NULL,
    description         text NOT NULL DEFAULT '',
    expression_language text NOT NULL
                        CHECK (expression_language IN ('pseudo', 'sql')),
    expression          text NOT NULL,
    unit                text NOT NULL DEFAULT '',
    format              text NOT NULL DEFAULT '',
    dependencies        jsonb NOT NULL DEFAULT '[]'::jsonb,
    sort_order          integer NOT NULL DEFAULT 0,
    status              text NOT NULL DEFAULT 'active'
                        CHECK (status IN ('active', 'inactive')),
    created_at          timestamptz NOT NULL DEFAULT now(),
    updated_at          timestamptz NOT NULL DEFAULT now(),
    UNIQUE (fact_id, code)
);

COMMENT ON TABLE analytics.derived_metric IS '派生指标：计算语言 + 计算逻辑；不映射物理字段；dependencies 记录依赖的基础指标/字段。';

CREATE TABLE IF NOT EXISTS analytics.fact_field (
    field_id       text PRIMARY KEY,
    fact_id        text NOT NULL REFERENCES analytics.fact(fact_id) ON DELETE CASCADE,
    code           text NOT NULL,
    name           text NOT NULL,
    description    text NOT NULL DEFAULT '',
    data_type      text NOT NULL DEFAULT 'text',
    role           text NOT NULL DEFAULT 'display'
                   CHECK (role IN ('key', 'filter', 'display', 'attribute')),
    is_enum        boolean NOT NULL DEFAULT false,
    sort_order     integer NOT NULL DEFAULT 0,
    status         text NOT NULL DEFAULT 'active'
                   CHECK (status IN ('active', 'inactive')),
    created_at     timestamptz NOT NULL DEFAULT now(),
    updated_at     timestamptz NOT NULL DEFAULT now(),
    UNIQUE (fact_id, code)
);

COMMENT ON TABLE analytics.fact_field IS '非指标字段：事实行上的键/筛选/展示/属性字段；不进入指标表。';

-- ============================================================
-- 4. MCP 服务扩展（数据库类型）
-- ============================================================
CREATE TABLE IF NOT EXISTS analytics.mcp_service (
    service_id    text PRIMARY KEY,
    service_name  text NOT NULL,
    database_type text NOT NULL
                  CHECK (database_type IN ('oceanbase', 'gaussdb', 'hetu', 'postgresql', 'mysql')),
    scope         text NOT NULL DEFAULT '',
    capabilities  text NOT NULL DEFAULT 'list_tables,describe_table,run_select',
    description   text NOT NULL DEFAULT '',
    status        text NOT NULL DEFAULT 'active'
                  CHECK (status IN ('active', 'inactive')),
    created_at    timestamptz NOT NULL DEFAULT now(),
    updated_at    timestamptz NOT NULL DEFAULT now()
);

COMMENT ON TABLE analytics.mcp_service IS '自助取数 MCP 服务扩展：service_id 对应 WeKnora mcp_services.id；database_type 供智能体适配 SQL 方言；hetu=河图。';

-- ============================================================
-- 5. 绑定 / 字段映射 / 枚举映射（只供管理界面）
-- ============================================================
CREATE TABLE IF NOT EXISTS analytics.binding (
    binding_id     text PRIMARY KEY,
    object_type    text NOT NULL CHECK (object_type IN ('fact', 'dimension')),
    object_id      text NOT NULL,
    service_id     text NOT NULL REFERENCES analytics.mcp_service(service_id) ON DELETE RESTRICT,
    binding_type   text NOT NULL CHECK (binding_type IN ('self_service', 'tool_contract')),
    tool_name      text NOT NULL DEFAULT '',
    status         text NOT NULL DEFAULT 'active'
                   CHECK (status IN ('active', 'inactive')),
    metadata_hash  jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at     timestamptz NOT NULL DEFAULT now(),
    updated_at     timestamptz NOT NULL DEFAULT now(),
    UNIQUE (object_type, object_id, service_id, binding_type, tool_name)
);

COMMENT ON TABLE analytics.binding IS '业务对象→MCP 服务/旧固定工具的绑定；不生成 wiki 页。';

CREATE TABLE IF NOT EXISTS analytics.field_mapping (
    mapping_id     text PRIMARY KEY,
    binding_id     text NOT NULL REFERENCES analytics.binding(binding_id) ON DELETE CASCADE,
    semantic_kind  text NOT NULL
                   CHECK (semantic_kind IN ('base_metric', 'non_metric_field', 'dimension_key', 'dimension_attribute')),
    semantic_id    text NOT NULL,
    source_type    text NOT NULL DEFAULT 'mcp_table'
                   CHECK (source_type IN ('mcp_table', 'tool_contract')),
    source_schema  text NOT NULL DEFAULT '',
    source_table   text NOT NULL DEFAULT '',
    source_column  text NOT NULL DEFAULT '',
    data_type      text NOT NULL DEFAULT '',
    aggregation    text NOT NULL DEFAULT '',
    expression     text NOT NULL DEFAULT '',
    sort_order     integer NOT NULL DEFAULT 0,
    note           text NOT NULL DEFAULT '',
    status         text NOT NULL DEFAULT 'active'
                   CHECK (status IN ('active', 'inactive')),
    UNIQUE (binding_id, semantic_kind, semantic_id)
);

COMMENT ON TABLE analytics.field_mapping IS '基础指标/非指标字段/维度键/维度属性的物理字段或旧工具字段映射；派生指标不在此表。';

CREATE TABLE IF NOT EXISTS analytics.enum_mapping (
    enum_id        text PRIMARY KEY,
    binding_id     text NOT NULL REFERENCES analytics.binding(binding_id) ON DELETE CASCADE,
    semantic_kind  text NOT NULL
                   CHECK (semantic_kind IN ('base_metric', 'non_metric_field', 'dimension_key', 'dimension_attribute')),
    semantic_id    text NOT NULL,
    semantic_value text NOT NULL,
    semantic_label text NOT NULL DEFAULT '',
    source_schema  text NOT NULL DEFAULT '',
    source_table   text NOT NULL DEFAULT '',
    source_column  text NOT NULL DEFAULT '',
    physical_value text NOT NULL,
    physical_label text NOT NULL DEFAULT '',
    status         text NOT NULL DEFAULT 'active'
                   CHECK (status IN ('active', 'inactive')),
    UNIQUE (binding_id, semantic_kind, semantic_id, semantic_value)
);

COMMENT ON TABLE analytics.enum_mapping IS '语义枚举值→物理库取值映射；只供配置/校验，不进知识库检索。';

-- ============================================================
-- 6. 标准取数场景
-- ============================================================
CREATE TABLE IF NOT EXISTS analytics.scenario (
    scenario_id    text PRIMARY KEY,
    name           text NOT NULL,
    description    text NOT NULL DEFAULT '',
    service_id     text REFERENCES analytics.mcp_service(service_id) ON DELETE SET NULL,
    source_locator text NOT NULL DEFAULT '',
    source_sql     text NOT NULL DEFAULT '',
    status         text NOT NULL DEFAULT 'active'
                   CHECK (status IN ('active', 'inactive')),
    created_at     timestamptz NOT NULL DEFAULT now(),
    updated_at     timestamptz NOT NULL DEFAULT now()
);

COMMENT ON TABLE analytics.scenario IS '标准取数场景/建模起点：来源定位、原始 SQL、说明；明细 join/filter 见子表。';

CREATE TABLE IF NOT EXISTS analytics.scenario_join (
    scenario_id    text NOT NULL REFERENCES analytics.scenario(scenario_id) ON DELETE CASCADE,
    seq            integer NOT NULL CHECK (seq > 0),
    left_schema    text NOT NULL DEFAULT '',
    left_table     text NOT NULL,
    left_column    text NOT NULL,
    right_schema   text NOT NULL DEFAULT '',
    right_table    text NOT NULL,
    right_column   text NOT NULL,
    join_type      text NOT NULL DEFAULT 'left'
                   CHECK (join_type IN ('inner', 'left', 'right', 'full', 'cross')),
    condition      text NOT NULL DEFAULT '',
    note           text NOT NULL DEFAULT '',
    PRIMARY KEY (scenario_id, seq)
);

COMMENT ON TABLE analytics.scenario_join IS '场景关联路径：一个场景多条按 seq 排序；不写进知识库关系图。';

CREATE TABLE IF NOT EXISTS analytics.scenario_filter (
    scenario_id    text NOT NULL REFERENCES analytics.scenario(scenario_id) ON DELETE CASCADE,
    seq            integer NOT NULL CHECK (seq > 0),
    schema_name    text NOT NULL DEFAULT '',
    table_name     text NOT NULL,
    column_name    text NOT NULL,
    expression     text NOT NULL,
    meaning        text NOT NULL DEFAULT '',
    required       boolean NOT NULL DEFAULT true,
    PRIMARY KEY (scenario_id, seq)
);

COMMENT ON TABLE analytics.scenario_filter IS '场景固定筛选：required=true 表示取数必须带上，不得由智能体省略。';

CREATE TABLE IF NOT EXISTS analytics.scenario_fact (
    scenario_id    text NOT NULL REFERENCES analytics.scenario(scenario_id) ON DELETE CASCADE,
    fact_id        text NOT NULL REFERENCES analytics.fact(fact_id) ON DELETE CASCADE,
    sort_order     integer NOT NULL DEFAULT 0,
    note           text NOT NULL DEFAULT '',
    status         text NOT NULL DEFAULT 'active'
                   CHECK (status IN ('active', 'inactive')),
    PRIMARY KEY (scenario_id, fact_id)
);

COMMENT ON TABLE analytics.scenario_fact IS '场景来源事实：只表达 Scenario 基于哪个 Fact，不表达使用/补充；物理 join/filter 仍在 scenario_join/scenario_filter。';

-- ---------- 索引 ----------
CREATE INDEX IF NOT EXISTS ix_dimension_level_dim ON analytics.dimension_level(dimension_id);
CREATE INDEX IF NOT EXISTS ix_dimension_attr_dim ON analytics.dimension_attribute(dimension_id);
CREATE INDEX IF NOT EXISTS ix_dimension_table_dim ON analytics.dimension_table(dimension_id);
CREATE INDEX IF NOT EXISTS ix_fact_dimension_dim ON analytics.fact_dimension(dimension_id);
CREATE INDEX IF NOT EXISTS ix_fact_table_fact ON analytics.fact_table(fact_id);
CREATE UNIQUE INDEX IF NOT EXISTS ux_fact_table_primary
    ON analytics.fact_table(fact_id) WHERE is_primary;
CREATE INDEX IF NOT EXISTS ix_base_metric_fact ON analytics.base_metric(fact_id);
CREATE INDEX IF NOT EXISTS ix_derived_metric_fact ON analytics.derived_metric(fact_id);
CREATE INDEX IF NOT EXISTS ix_fact_field_fact ON analytics.fact_field(fact_id);
CREATE INDEX IF NOT EXISTS ix_binding_object ON analytics.binding(object_type, object_id);
CREATE INDEX IF NOT EXISTS ix_field_mapping_binding ON analytics.field_mapping(binding_id);
CREATE INDEX IF NOT EXISTS ix_enum_mapping_binding ON analytics.enum_mapping(binding_id);
CREATE INDEX IF NOT EXISTS ix_scenario_service ON analytics.scenario(service_id);
CREATE INDEX IF NOT EXISTS ix_scenario_fact_fact ON analytics.scenario_fact(fact_id);

-- ---------- updated_at 触发器 ----------
DROP TRIGGER IF EXISTS trg_dimension_table_updated ON analytics.dimension_table;
CREATE TRIGGER trg_dimension_table_updated BEFORE UPDATE ON analytics.dimension_table
FOR EACH ROW EXECUTE FUNCTION analytics.touch_updated_at();

DROP TRIGGER IF EXISTS trg_dimension_updated ON analytics.dimension;
CREATE TRIGGER trg_dimension_updated BEFORE UPDATE ON analytics.dimension
FOR EACH ROW EXECUTE FUNCTION analytics.touch_updated_at();

DROP TRIGGER IF EXISTS trg_fact_updated ON analytics.fact;
CREATE TRIGGER trg_fact_updated BEFORE UPDATE ON analytics.fact
FOR EACH ROW EXECUTE FUNCTION analytics.touch_updated_at();

DROP TRIGGER IF EXISTS trg_fact_table_updated ON analytics.fact_table;
CREATE TRIGGER trg_fact_table_updated BEFORE UPDATE ON analytics.fact_table
FOR EACH ROW EXECUTE FUNCTION analytics.touch_updated_at();

DROP TRIGGER IF EXISTS trg_base_metric_updated ON analytics.base_metric;
CREATE TRIGGER trg_base_metric_updated BEFORE UPDATE ON analytics.base_metric
FOR EACH ROW EXECUTE FUNCTION analytics.touch_updated_at();

DROP TRIGGER IF EXISTS trg_derived_metric_updated ON analytics.derived_metric;
CREATE TRIGGER trg_derived_metric_updated BEFORE UPDATE ON analytics.derived_metric
FOR EACH ROW EXECUTE FUNCTION analytics.touch_updated_at();

DROP TRIGGER IF EXISTS trg_fact_field_updated ON analytics.fact_field;
CREATE TRIGGER trg_fact_field_updated BEFORE UPDATE ON analytics.fact_field
FOR EACH ROW EXECUTE FUNCTION analytics.touch_updated_at();

DROP TRIGGER IF EXISTS trg_mcp_service_updated ON analytics.mcp_service;
CREATE TRIGGER trg_mcp_service_updated BEFORE UPDATE ON analytics.mcp_service
FOR EACH ROW EXECUTE FUNCTION analytics.touch_updated_at();

DROP TRIGGER IF EXISTS trg_binding_updated ON analytics.binding;
CREATE TRIGGER trg_binding_updated BEFORE UPDATE ON analytics.binding
FOR EACH ROW EXECUTE FUNCTION analytics.touch_updated_at();

DROP TRIGGER IF EXISTS trg_scenario_updated ON analytics.scenario;
CREATE TRIGGER trg_scenario_updated BEFORE UPDATE ON analytics.scenario
FOR EACH ROW EXECUTE FUNCTION analytics.touch_updated_at();

-- ============================================================
-- 7. 只读视图（页面投影 / 管理界面 / 路径验证）
-- ============================================================

-- 事实→维度实例通路：一个事实可能返回多行（本种子中工具分类返回 2 行）
CREATE OR REPLACE VIEW analytics.v_fact_dimension_path AS
SELECT fd.fact_id,
       f.code AS fact_code,
       f.name AS fact_name,
       fd.dimension_id,
       d.code AS dimension_code,
       d.name AS dimension_name,
       fd.key_code,
       fd.role,
       fd.sort_order,
       fd.fact_key,
       fd.dimension_key,
       fd.join_type,
       fd.status,
       (SELECT count(*)::integer
          FROM analytics.dimension_level l
         WHERE l.dimension_id = d.dimension_id) AS level_count,
       (SELECT coalesce(jsonb_agg(l.level_name ORDER BY l.level_no), '[]'::jsonb)
          FROM analytics.dimension_level l
         WHERE l.dimension_id = d.dimension_id) AS level_names
  FROM analytics.fact_dimension fd
  JOIN analytics.fact f ON f.fact_id = fd.fact_id
  JOIN analytics.dimension d ON d.dimension_id = fd.dimension_id;

-- 指标目录：基础指标 + 派生指标（派生指标带计算语言与计算逻辑）
CREATE OR REPLACE VIEW analytics.v_metric_catalog AS
SELECT b.fact_id, b.metric_id, b.code, b.name, 'base'::text AS metric_kind,
       b.aggregation AS calculation, NULL::text AS expression_language,
       b.filter_expression AS expression, b.unit, b.format, b.sort_order, b.status
  FROM analytics.base_metric b
UNION ALL
SELECT d.fact_id, d.metric_id, d.code, d.name, 'derived'::text AS metric_kind,
       NULL::text AS calculation, d.expression_language, d.expression,
       d.unit, d.format, d.sort_order, d.status
  FROM analytics.derived_metric d;

-- 维度规格：维度 + 成员键 + 层级公式 + 属性 + 使用它的事实（纯语义）
CREATE OR REPLACE VIEW analytics.v_dimension_spec AS
SELECT d.dimension_id, d.code, d.name, d.description, d.member_key, d.status,
       (SELECT coalesce(jsonb_agg(to_jsonb(l) ORDER BY l.level_no), '[]'::jsonb)
          FROM analytics.dimension_level l
         WHERE l.dimension_id = d.dimension_id) AS levels,
       (SELECT coalesce(jsonb_agg(to_jsonb(a) ORDER BY a.sort_order, a.attribute_code), '[]'::jsonb)
          FROM analytics.dimension_attribute a
         WHERE a.dimension_id = d.dimension_id) AS attributes,
       (SELECT coalesce(jsonb_agg(to_jsonb(p) ORDER BY p.fact_id), '[]'::jsonb)
          FROM analytics.v_fact_dimension_path p
         WHERE p.dimension_id = d.dimension_id) AS facts
  FROM analytics.dimension d;

-- 事实规格：事实 + 基础指标 + 派生指标 + 非指标字段 + 维度键
CREATE OR REPLACE VIEW analytics.v_fact_spec AS
SELECT f.fact_id, f.code, f.name, f.description, f.grain, f.status,
       (SELECT coalesce(jsonb_agg(to_jsonb(b) ORDER BY b.sort_order, b.metric_id), '[]'::jsonb)
          FROM analytics.base_metric b
         WHERE b.fact_id = f.fact_id) AS base_metrics,
       (SELECT coalesce(jsonb_agg(to_jsonb(d) ORDER BY d.sort_order, d.metric_id), '[]'::jsonb)
          FROM analytics.derived_metric d
         WHERE d.fact_id = f.fact_id) AS derived_metrics,
       (SELECT coalesce(jsonb_agg(to_jsonb(x) ORDER BY x.sort_order, x.field_id), '[]'::jsonb)
          FROM analytics.fact_field x
         WHERE x.fact_id = f.fact_id) AS fact_fields,
       (SELECT coalesce(jsonb_agg(to_jsonb(p) ORDER BY p.sort_order, p.dimension_id), '[]'::jsonb)
          FROM analytics.v_fact_dimension_path p
         WHERE p.fact_id = f.fact_id) AS dimensions
  FROM analytics.fact f;

-- 绑定明细：binding + 字段映射 + 枚举映射（只给管理界面）
CREATE OR REPLACE VIEW analytics.v_binding_detail AS
SELECT b.binding_id, b.object_type, b.object_id, b.service_id, s.service_name,
       s.database_type, b.binding_type, b.tool_name, b.status, b.metadata_hash,
       (SELECT coalesce(jsonb_agg(to_jsonb(m) ORDER BY m.sort_order, m.mapping_id), '[]'::jsonb)
          FROM analytics.field_mapping m
         WHERE m.binding_id = b.binding_id) AS field_mappings,
       (SELECT coalesce(jsonb_agg(to_jsonb(e) ORDER BY e.semantic_id, e.semantic_value), '[]'::jsonb)
          FROM analytics.enum_mapping e
         WHERE e.binding_id = b.binding_id) AS enum_mappings
  FROM analytics.binding b
  JOIN analytics.mcp_service s ON s.service_id = b.service_id;

-- 场景来源事实：Scenario 基于 Fact（只表达来源，不表达使用/补充）
CREATE OR REPLACE VIEW analytics.v_scenario_fact AS
SELECT sf.scenario_id, sc.name AS scenario_name,
       sf.fact_id, f.code AS fact_code, f.name AS fact_name,
       sf.sort_order, sf.note, sf.status
  FROM analytics.scenario_fact sf
  JOIN analytics.scenario sc ON sc.scenario_id = sf.scenario_id
  JOIN analytics.fact f ON f.fact_id = sf.fact_id;

-- 场景明细：语义来源 + 公共 MCP 服务 + 关联路径 + 固定筛选
CREATE OR REPLACE VIEW analytics.v_scenario_detail AS
SELECT s.scenario_id, s.name, s.description, s.service_id, sc.service_name,
       sc.database_type, s.source_locator, s.source_sql, s.status,
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

-- 知识页 ↔ analytics 映射表唯一接口（页面投影/管理台校验共用）
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
