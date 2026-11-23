#!/usr/bin/env python3
"""自助取数分析 · 语义配置/页面投影回归（静态，不连库）。

验证：
1. 配置 schema 18 张表 / 8 个视图，dimension_value、dimension_mapping 已删除；
2. Fact↔Dimension 只保留语义键（fact_key/dimension_key/join_type），工具分类只挂在工具调用事实；
3. 维度成员键 + 层级公式（formula_kind/formula_params）在维度侧定义；
4. 页面只写语义，物理映射提示走 mapping_lookup / 管理台；
5. MCP 服务页只投影 active 服务。

跑法：`python3 tests/test_analytics_config.py`
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _helpers as H  # noqa: E402

SQL_DIR = os.path.join(H.REPO, "sql", "analytics")
SCHEMA = os.path.join(SQL_DIR, "001_schema.sql")
SEED = os.path.join(SQL_DIR, "002_seed.sql")
MIGRATION = os.path.join(SQL_DIR, "008_semantic_refactor.sql")
VIEWS = os.path.join(SQL_DIR, "009_semantic_views.sql")
MAPPING_VIEW = os.path.join(SQL_DIR, "011_mapping_interface.sql")
FACT_TABLE = os.path.join(SQL_DIR, "012_fact_table_join.sql")
DIM_TABLE = os.path.join(SQL_DIR, "013_dimension_table.sql")
ADMIN = os.path.join(H.REPO, "tools", "analytics-admin", "server.py")
DIAG_SCHEMA = os.path.join(H.REPO, "sql", "diag", "001_schema.sql")
DIAG_EXAMPLES = os.path.join(H.REPO, "sql", "diag", "003_examples.sql")
KE_CORE = os.path.join(H.REPO, "tools", "ke-core")
if KE_CORE not in sys.path:
    sys.path.insert(0, KE_CORE)


def main() -> int:
    schema = H.read(SCHEMA)
    seed = H.read(SEED)
    migration = H.read(MIGRATION)
    views_text = H.read(VIEWS)
    mapping_view = H.read(MAPPING_VIEW)
    fact_table_sql = H.read(FACT_TABLE)
    dim_table_sql = H.read(DIM_TABLE)
    semantic_code = H.read(os.path.join(KE_CORE, "ke_semantic.py"))
    semantic_sql_code = H.read(os.path.join(KE_CORE, "ke_semantic_sql.py"))
    admin_code = H.read(ADMIN)
    diag_schema = H.read(DIAG_SCHEMA)
    diag_examples = H.read(DIAG_EXAMPLES)
    code = H.read(os.path.join(KE_CORE, "ke_analytics.py"))

    tables = set(re.findall(r"CREATE TABLE IF NOT EXISTS analytics\.([a-z_]+)", schema))
    views = set(re.findall(r"CREATE OR REPLACE VIEW analytics\.([a-z_]+)", schema))
    expected_tables = {
        "dimension", "dimension_level", "dimension_attribute", "dimension_table",
        "fact", "fact_dimension", "fact_table", "base_metric", "derived_metric",
        "fact_field", "mcp_service", "binding", "field_mapping", "enum_mapping",
        "scenario", "scenario_join", "scenario_filter", "scenario_fact",
    }
    expected_views = {
        "v_fact_dimension_path", "v_metric_catalog", "v_dimension_spec",
        "v_fact_spec", "v_binding_detail", "v_scenario_detail", "v_scenario_fact",
        "v_mapping_interface",
    }
    db_check = re.search(
        r"database_type text NOT NULL\s+CHECK \(database_type IN \(([^)]+)\)\)", schema)
    db_types = set(re.findall(r"'([a-z]+)'", db_check.group(1))) if db_check else set()
    expected_db_types = {"oceanbase", "gaussdb", "hetu", "postgresql", "mysql"}

    fd_rows = set(re.findall(
        r"\('(fact_[a-z_]+)', '(dim_[a-z_0-9]+)'", seed))
    expected_pairs = {
        ("fact_agent_tool_call", "dim_tool_category_2level"),
        ("fact_agent_tool_call", "dim_tool_category_1level"),
        ("fact_agent_tool_call", "dim_agent"),
        ("fact_agent_tool_call", "dim_model"),
        ("fact_agent_tool_call", "dim_session"),
        ("fact_agent_tool_call", "dim_time"),
        ("fact_agent_turn", "dim_agent"),
        ("fact_agent_turn", "dim_model"),
        ("fact_agent_turn", "dim_session"),
        ("fact_agent_turn", "dim_time"),
    }
    level_formulas = (
        "formula_kind = 'date'" in migration
        and "formula_kind = 'hour'" in migration
        and '"timezone":"Asia/Shanghai"' in migration)
    physical_mapping = all(x in migration for x in
                           ("'bind_dim_tool_category_1level'", "'diag', 'tool_dimension'",
                            "'diag', 'tool_problem_dimension'"))


    # 页面投影（纯函数）
    import ke_analytics  # noqa: E402
    fact = {
        "fact_id": "fact_x", "code": "x", "name": "XFact", "grain": "k",
        "description": "示例事实",
        "mapping_services": [{"service_id": "svc_x", "service_name": "db_x",
                              "database_type": "postgresql"}],
        "base_metrics": [{"name": "基础A", "code": "a", "aggregation": "sum",
                          "unit": "次", "filter_expression": "", "description": ""}],
        "derived_metrics": [{"name": "派生B", "code": "b", "expression_language": "pseudo",
                             "expression": "a / 2", "unit": "%", "description": ""}],
        "fact_fields": [{"name": "字段C", "code": "c", "data_type": "text",
                         "role": "key", "is_enum": False, "description": ""}],
        "dimensions": [
            {"dimension_code": "d2", "dimension_name": "维度2层",
             "fact_key": "tool_code", "dimension_key": "tool_code", "join_type": "left",
             "role": "group_by", "level_count": 2, "level_names": ["层1", "层2"]},
            {"dimension_code": "d1", "dimension_name": "维度1层",
             "fact_key": "event_time", "dimension_key": "event_time", "join_type": "none",
             "role": "group_by", "level_count": 1, "level_names": ["层1"]},
        ],
    }
    page = ke_analytics._page_of("fact", fact)
    content = page["wiki_content"]
    page_ok = (page["page_type"] == "analytics:Fact"
               and "## 基础指标" in content and "## 派生指标" in content
               and "## 非指标字段" in content and "## 维度键（语义）" in content
               and "tool_code" in content and "event_time" in content
               and "left" in content and ke_analytics.PUBLIC_PROMPT in content
               and "mapping_object_type: `fact`" in content
               and "mapping_object_id: `fact_x`" in content
               and "mappingServiceId=`svc_x`" in content
               and page["attributes"].get("analytics:mappingObjectType") == "fact"
               and page["attributes"].get("analytics:mappingObjectId") == "fact_x"
               and "diag." not in content)

    dim = {
        "dimension_id": "dim_x", "code": "x", "name": "X维度", "member_key": "tool_code",
        "description": "示例维度",
        "mapping_services": [{"service_id": "svc_x", "service_name": "db_x",
                              "database_type": "postgresql"}],
        "levels": [{"level_no": 1, "level_name": "日期", "attribute_code": "event_date",
                    "formula_kind": "date", "formula_params": {"timezone": "Asia/Shanghai"}}],
        "attributes": [{"attribute_code": "k", "name": "键", "data_type": "text",
                        "is_enum": False}],
        "facts": [{"fact_code": "x", "fact_name": "XFact", "fact_key": "tool_code",
                   "dimension_key": "tool_code", "join_type": "left"}],
    }
    dim_page = ke_analytics._page_of("dimension", dim)
    dim_ok = (dim_page["page_type"] == "analytics:Dimension"
              and "成员键" in dim_page["wiki_content"]
              and "计算方式" in dim_page["wiki_content"]
              and "date" in dim_page["wiki_content"]
              and "使用该维度的事实" in dim_page["wiki_content"]
              and "## 分类值" not in dim_page["wiki_content"]
              and "mapping_object_type: `dimension`" in dim_page["wiki_content"]
              and "mapping_object_id: `dim_x`" in dim_page["wiki_content"]
              and "mappingServiceId=`svc_x`" in dim_page["wiki_content"]
              and dim_page["attributes"].get("analytics:mappingObjectId") == "dim_x"
              and "diag." not in dim_page["wiki_content"]
              and ke_analytics.PUBLIC_PROMPT in dim_page["wiki_content"])

    service = ke_analytics._service_page({
        "service_id": "svc_x", "service_name": "db_x", "database_type": "postgresql",
        "scope": "scope", "capabilities": "run_select", "description": "示例服务",
        "objects": [{"object_type": "fact", "object_name": "XFact",
                     "binding_type": "self_service"}],
    })
    service_ok = (service["page_type"] == "agent:MCPService"
                  and service["slug"] == "agent/mcpservice/db_x"
                  and "mapping_service_id: `svc_x`" in service["wiki_content"]
                  and "database_type: `postgresql`" in service["wiki_content"]
                  and "使用该服务的对象" in service["wiki_content"]
                  and service["attributes"].get("analytics:mappingServiceId") == "svc_x"
                  and ke_analytics.PUBLIC_PROMPT in service["wiki_content"])

    scenario = ke_analytics.render_scenario_page({
        "scenario_id": "s", "name": "S", "description": "d", "database_type": "hetu",
        "source_locator": "page:x", "source_sql": "select 1",
        "filters": [{"seq": 1, "schema_name": "x", "table_name": "t", "column_name": "c",
                     "expression": "= 1", "meaning": "m", "required": True}],
        "joins": [{"seq": 1, "left_schema": "x", "left_table": "a", "left_column": "k",
                   "right_schema": "x", "right_table": "b", "right_column": "k",
                   "join_type": "left", "condition": "k", "note": ""}],
    })

    formula = ke_analytics.render_formula(
        "hour", "postgresql", "event_time", '{"timezone":"Asia/Shanghai"}')
    formula_mysql = ke_analytics.render_formula(
        "date", "mysql", "event_time", '{"timezone":"Asia/Shanghai"}')
    formula_ok = ("extract(hour from" in formula and "AT TIME ZONE" in formula
                  and "CONVERT_TZ" in formula_mysql)

    checks = {
        "表 = 18（实际 %d）" % len(tables): tables == expected_tables,
        "已删除 dimension_value/dimension_mapping":
            "CREATE TABLE IF NOT EXISTS analytics.dimension_value" not in schema
            and "CREATE TABLE IF NOT EXISTS analytics.dimension_mapping" not in schema,
        "视图 = 8（实际 %s）" % ",".join(sorted(views)): views == expected_views,
        "009 视图 = 3":
            all(v in views_text for v in
                ("v_fact_dimension_path", "v_dimension_spec", "v_fact_spec")),
        "database_type = 5 类（实际 %s）" % sorted(db_types): db_types == expected_db_types,
        "事实维度语义关系（实际 %s）" % sorted(fd_rows): fd_rows == expected_pairs,
        "事实维度主键 = (fact_id,dimension_id)":
            "PRIMARY KEY (fact_id, dimension_id)" in schema,
        "维度成员键 + 公式": "member_key" in schema and "formula_kind" in schema
                            and "formula_params" in schema and level_formulas,
        "物理映射复用 binding/field_mapping": physical_mapping,
        "工具体系只有 2 个分类维度": "'dim_tool'" not in seed and
            "dim_tool" not in {r[1] for r in fd_rows},
        "binding_scenario 已替换为 scenario_fact": "binding_scenario" not in schema
            and "scenario_fact" in schema
            and "scenario_fact" in seed,
        "事实页语义化": page_ok,
        "维度页语义化": dim_ok,
        "MCP 服务页只写 active 服务": service_ok,
        "场景页含数据库类型/筛选/关联": ("河图" in scenario and "## 固定筛选" in scenario
                                        and "## 关联路径" in scenario),
        "公式渲染按 database_type 分支": formula_ok,
        "统一走 write_knowledge（不再 PG-only）":
            "write_knowledge" in code and "_update_existing_page" not in code,
        "图边重建 + 服务关系": "objectUsesMcpService" in code
                              and "factUsesDimension" in code and "edge_cleanup" in code,
        "diag 维度数据源存在": "tool_dimension" in diag_schema
                              and "tool_problem_dimension" in diag_schema
                              and "agent_tool_call" in diag_examples,
        "mapping_lookup 状态化": all(x in code for x in
                                    ("mapping_object_type", "mapping_object_id",
                                     "formula_only", "\"missing\"")) and
                                 'out["ok"] = True' not in code,
        "映射接口只读视图": "v_mapping_interface" in mapping_view and
                            "formula_only" in mapping_view,
        "管理台映射接口": "/api/mapping-interface" in admin_code and
                          "v_mapping_interface" in admin_code,
        "管理台含 dimension_table": '"dimension_table"' in admin_code,
        "fact_table 组成": "CREATE TABLE IF NOT EXISTS analytics.fact_table" in schema
                           and "fact_table" in fact_table_sql,
        "事实字段不映射维表": "DELETE FROM analytics.field_mapping" in fact_table_sql
                              and "fact_source_not_fact_table" in code,
        "scenario_join 只事实表": "scenario_join_not_fact_table" in code
                                  and "scenario_join" in fact_table_sql,
        "多事实表示例": "fact_agent_tool_context" in fact_table_sql
                        and "'agent_turn_fact'" in fact_table_sql
                        and "'agent_tool_call_fact'" in fact_table_sql,
        "维度视图登记": "CREATE TABLE IF NOT EXISTS analytics.dimension_table" in schema
                        and "'tool_dimension_all'" in dim_table_sql
                        and "'tool_problem_dimension_all'" in dim_table_sql,
        "语义提取工具边界": all(x in semantic_code for x in
                                ("scenario_semantic_plan", "scenario_semantic_apply",
                                 "scenario_semantic_validate", "confirm",
                                 "plan_stale", "needs_confirmation"))
                            and "ke_semantic_sql" in semantic_code,
        "SQL 扫描器拒绝不支持语法": all(
            x in semantic_sql_code for x in
            ("unsupported_cte", "unsupported_%s", "split_top_level",
             "normalize_expression", '"union"')),
    }
    return H.report(checks, "analytics 配置/投影")


if __name__ == "__main__":
    sys.exit(main())
