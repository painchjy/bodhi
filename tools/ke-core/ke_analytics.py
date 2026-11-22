"""自助取数分析 · 配置读取与 wiki 只读投影（零第三方依赖）。

职责边界
--------
* **配置真源**：PostgreSQL `analytics` schema（见 `sql/analytics/001_schema.sql`）；
* **wiki 页**：投影 Fact / Dimension / StandardQueryScenario / active MCP 服务四类页面；
* **映射**（binding / field_mapping / enum_mapping / scenario_join / scenario_filter）
  只在管理界面与校验中使用，**不生成知识页**；
* 代码只认表/列/类型，不写死任何业务对象名；业务数据在 SQL 种子或管理界面里。

页面同步
--------
    import ke_analytics
    ke_analytics.sync_pages(kb_id="<kb>", kind="all", dry_run=False)

同步是**幂等**的：按 `(kb_id, slug)` 更新页面；不会删除已有映射页/其它页。
"""
from __future__ import annotations

import json
import os
import sys
from functools import lru_cache
from typing import Any

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import ke_db  # noqa: E402

# 与 ontology/sources/analytics.ttl 的 analytics:databaseType 注释、SQL CHECK 同步
DB_TYPES = ("oceanbase", "gaussdb", "hetu", "postgresql", "mysql")
DB_TYPE_LABELS = {
    "oceanbase": "OceanBase",
    "gaussdb": "GaussDB",
    "hetu": "河图",
    "postgresql": "PostgreSQL",
    "mysql": "MySQL",
}

PUBLIC_PROMPT = """## 公共使用提示

本页只描述语义（对象、指标、字段、维度、层次）；物理映射不在知识页里。
页面上的映射接口：

- 事实/维度：`mapping_object_type`（fact｜dimension）+ `mapping_object_id`（内部对象 ID）；
- MCP 服务：`mapping_service_id`（analytics 服务 ID）+ `platform_mcp_service_id`（平台 ID，可选）+ `database_type`；
- 物理映射：调用 `mapping_lookup(mapping_object_type=..., mapping_object_id=..., mapping_service_id=...)`；
- 先确认 mapping，再调用 `scenario_lookup(mapping_object_id=..., mapping_service_id=..., intent=...)`；
- `status=mapped` 继续；`formula_only` 用 `levels[].formula_sql` 继续（bindings 为空不等于缺映射）；`missing` 停止并检查 ID/服务；
- 管理台：`analytics-admin` 的 `binding` / `field_mapping` / `enum_mapping`；接口视图只读。

不要从本页猜物理表名或列名；不要用页面 slug、标题或旧 ID 叫法调用 mapping_lookup。"""


def _tz_expr(key: str, timezone: str, database_type: str) -> str:
    if not timezone:
        return key
    if database_type == "mysql":
        return "CONVERT_TZ(%s, '+00:00', '%s')" % (key, timezone)
    return "(%s AT TIME ZONE '%s')" % (key, timezone)


@lru_cache(maxsize=256)
def render_formula(formula_kind: str, database_type: str, key: str,
                   params_json: str = "{}", formula_version: str = "") -> str:
    """把维度层级公式渲染成目标库 SQL 表达式（公式缓存，不缓存查询结果）。

    缓存键 = (formula_kind, database_type, key, normalized_params, formula_version)；
    `formula_version` 由调用方传层级配置 hash，配置变更即失效。
    """
    kind = (formula_kind or "direct").strip().lower()
    db = (database_type or "postgresql").strip().lower()
    try:
        params = json.loads(params_json or "{}")
    except Exception:  # noqa: BLE001
        params = {}
    if kind == "direct":
        return key
    timezone = str(params.get("timezone") or "")
    expr = _tz_expr(key, timezone, db)
    if kind == "date":
        return "date(%s)" % expr
    if kind == "hour":
        return "hour(%s)" % expr if db == "mysql" else "extract(hour from %s)" % expr
    if kind == "date_trunc":
        unit = str(params.get("unit") or "day")
        if db == "mysql":
            return "date(%s)" % expr
        return "date_trunc('%s', %s)" % (unit, expr)
    # custom 只允许受控 {key} 占位，不做任意 SQL 拼接
    template = str(params.get("template") or "{key}")
    return template.replace("{key}", expr)


def _s(value: Any) -> str:
    return "" if value is None else str(value)


def _json(value: Any, default):
    """psql_csv 把 jsonb 取成字符串；兼容已是 list/dict 的调用方。"""
    if value is None or value == "":
        return default
    if isinstance(value, (list, dict)):
        return value
    try:
        return json.loads(str(value))
    except Exception:  # noqa: BLE001
        return default


def _q(value: str) -> str:
    """SQL 字符串字面量（只用于内部已校验的表/ID）。"""
    return ke_db.sql_str(value)


def _cell(value: Any) -> str:
    """Markdown 单元格转义（表格里不能出现裸 | 与换行）。"""
    return (_s(value).replace("|", "\\|").replace("\r", " ").replace("\n", " ")).strip()


def _qualified(schema: Any, table: Any) -> str:
    s, t = _s(schema).strip(), _s(table).strip()
    if s and t:
        return "%s.%s" % (s, t)
    return t or s


def _code_block(value: Any) -> str:
    text = _s(value).strip()
    if not text:
        return ""
    if "```" in text:
        return text
    return "```sql\n%s\n```" % text


def _table(headers: list[str], rows: list[list[Any]]) -> list[str]:
    out = ["| " + " | ".join(headers) + " |",
           "|" + "|".join(["---"] * len(headers)) + "|"]
    for row in rows:
        out.append("| " + " | ".join(_cell(v) for v in row) + " |")
    return out


# ---------------------------------------------------------------------------
# 配置读取（只读）
# ---------------------------------------------------------------------------
def fact_dimension_paths(fact_id: str = "") -> list[dict]:
    """事实→维度实例通路；一个事实可返回多行（同一类维度可有多个实例）。"""
    where = " WHERE fact_id = %s" % _q(fact_id) if fact_id else ""
    rows = ke_db.psql_csv(
        "SELECT fact_id, fact_code, fact_name, dimension_id, dimension_code, "
        "       dimension_name, key_code, role, sort_order, level_count, level_names "
        "  FROM analytics.v_fact_dimension_path%s "
        " ORDER BY fact_id, sort_order, dimension_id" % where)
    for row in rows:
        row["level_names"] = _json(row.get("level_names"), [])
        row["level_count"] = int(row.get("level_count") or 0)
        row["sort_order"] = int(row.get("sort_order") or 0)
    return rows


def _mapping_identity(object_type: str, object_id: str) -> dict:
    """页面 ID/type 只从 v_mapping_interface 投影，不人工维护第二套。"""
    rows = ke_db.psql_csv(
        "SELECT mapping_object_type, mapping_object_id "
        "  FROM analytics.v_mapping_interface "
        " WHERE mapping_object_type=%s AND mapping_object_id=%s LIMIT 1"
        % (_q(object_type), _q(object_id)))
    return rows[0] if rows else {}


def _mapping_services(object_type: str, object_id: str) -> list[dict]:
    return ke_db.psql_csv(
        "SELECT DISTINCT b.service_id, s.service_name, s.database_type "
        "  FROM analytics.binding b "
        "  JOIN analytics.mcp_service s ON s.service_id=b.service_id "
        " WHERE b.object_type=%s AND b.object_id=%s AND b.status='active' "
        "   AND s.status='active' ORDER BY b.service_id"
        % (_q(object_type), _q(object_id)))


def fact_spec(fact_id: str) -> dict:
    rows = ke_db.psql_csv(
        "SELECT * FROM analytics.v_fact_spec WHERE fact_id = %s LIMIT 1" % _q(fact_id))
    if not rows:
        raise KeyError("fact 不存在：%s" % fact_id)
    spec = dict(rows[0])
    for key in ("base_metrics", "derived_metrics", "fact_fields", "dimensions"):
        spec[key] = _json(spec.get(key), [])
    spec["scenarios"] = ke_db.psql_csv(
        "SELECT scenario_id, scenario_name, sort_order, note, status "
        "  FROM analytics.v_scenario_fact WHERE fact_id=%s AND status='active' "
        " ORDER BY sort_order, scenario_id" % _q(fact_id))
    spec["mapping_services"] = _mapping_services("fact", fact_id)
    identity = _mapping_identity("fact", fact_id)
    spec["mapping_object_type"] = identity.get("mapping_object_type") or "fact"
    spec["mapping_object_id"] = identity.get("mapping_object_id") or fact_id
    return spec


def dimension_spec(dimension_id: str) -> dict:
    rows = ke_db.psql_csv(
        "SELECT * FROM analytics.v_dimension_spec WHERE dimension_id = %s LIMIT 1"
        % _q(dimension_id))
    if not rows:
        raise KeyError("dimension 不存在：%s" % dimension_id)
    spec = dict(rows[0])
    for key in ("levels", "attributes", "facts"):
        spec[key] = _json(spec.get(key), [])
    spec["mapping_services"] = _mapping_services("dimension", dimension_id)
    identity = _mapping_identity("dimension", dimension_id)
    spec["mapping_object_type"] = identity.get("mapping_object_type") or "dimension"
    spec["mapping_object_id"] = identity.get("mapping_object_id") or dimension_id
    return spec


def scenario_spec(scenario_id: str) -> dict:
    rows = ke_db.psql_csv(
        "SELECT * FROM analytics.v_scenario_detail WHERE scenario_id = %s LIMIT 1"
        % _q(scenario_id))
    if not rows:
        raise KeyError("scenario 不存在：%s" % scenario_id)
    spec = dict(rows[0])
    for key in ("joins", "filters", "facts", "common_services"):
        spec[key] = _json(spec.get(key), [])
    return spec


def list_ids(kind: str) -> list[str]:
    """列出可投影对象 id：fact / dimension / scenario。"""
    table, column = {
        "fact": ("analytics.fact", "fact_id"),
        "dimension": ("analytics.dimension", "dimension_id"),
        "scenario": ("analytics.scenario", "scenario_id"),
    }.get(kind, ("", ""))
    if not table:
        raise ValueError("kind 只能是 fact / dimension / scenario")
    rows = ke_db.psql_csv(
        "SELECT %s AS id FROM %s WHERE status = 'active' ORDER BY 1" % (column, table))
    return [str(r["id"]) for r in rows]


# ---------------------------------------------------------------------------
# 页面正文渲染（纯函数，便于单测）
# ---------------------------------------------------------------------------
def render_fact_page(spec: dict) -> str:
    """事实页：基础指标/派生指标/非指标字段/维度键，四节分开。"""
    lines = ["# 事实：%s" % _s(spec.get("name") or spec.get("fact_id")),
             "",
             "- 粒度：%s" % _s(spec.get("grain") or "（未填写）"),
             "",
             "## 自助取数映射接口",
             "",
             "- mapping_object_type: `%s`" % _s(spec.get("mapping_object_type") or "fact"),
             "- mapping_object_id: `%s`" % _s(spec.get("mapping_object_id") or spec.get("fact_id") or "—")]
    services = spec.get("mapping_services") or []
    if services:
        lines.append("- 可用 MCP 服务：" + "；".join(
            "`%s`（mappingServiceId=`%s`，%s）" %
            (_s(s.get("service_name")), _s(s.get("service_id")),
             _s(s.get("database_type"))) for s in services))
    else:
        lines.append("- 可用 MCP 服务：（无 active binding）")
    if spec.get("description"):
        lines += ["- 说明：%s" % _s(spec["description"])]
    lines.append("")

    lines += ["## 基础指标", ""]
    base = spec.get("base_metrics") or []
    if base:
        lines += _table(["指标", "编码", "聚合", "单位", "口径/过滤", "说明"], [
            [m.get("name"), m.get("code"), m.get("aggregation"), m.get("unit"),
             m.get("filter_expression") or "—", m.get("description")]
            for m in base])
    else:
        lines.append("（无）")
    lines.append("")

    lines += ["## 派生指标", ""]
    derived = spec.get("derived_metrics") or []
    if derived:
        lines += _table(["指标", "编码", "计算语言", "计算逻辑", "单位", "说明"], [
            [m.get("name"), m.get("code"), m.get("expression_language"),
             "`%s`" % _cell(m.get("expression")), m.get("unit"), m.get("description")]
            for m in derived])
    else:
        lines.append("（无）")
    lines.append("")

    lines += ["## 非指标字段", ""]
    fields = spec.get("fact_fields") or []
    if fields:
        lines += _table(["字段", "编码", "类型", "用途", "枚举", "说明"], [
            [f.get("name"), f.get("code"), f.get("data_type"), f.get("role"),
             "是" if f.get("is_enum") else "否", f.get("description")]
            for f in fields])
    else:
        lines.append("（无）")
    lines.append("")

    lines += ["## 维度键（语义）", ""]
    dims = spec.get("dimensions") or []
    if dims:
        lines += _table(["维度", "层级", "事实键", "维度键", "JOIN", "说明"], [
            ["[%s](wiki:analytics/dimension/%s)" % (d.get("dimension_name"), d.get("dimension_code")),
             "%s 层：%s" % (d.get("level_count"), " / ".join(_s(x) for x in (d.get("level_names") or []))),
             d.get("fact_key") or d.get("key_code") or "—",
             d.get("dimension_key") or d.get("key_code") or "—",
             d.get("join_type") or "none",
             d.get("role")]
            for d in dims])
    else:
        lines.append("（无）")
    lines += ["", "## 参考标准场景", ""]
    scenarios = spec.get("scenarios") or []
    if scenarios:
        lines += _table(["场景", "说明"], [
            ["[%s](wiki:analytics/scenario/%s)" % (s.get("scenario_name"), s.get("scenario_id")),
             s.get("note") or "—"]
            for s in scenarios])
    else:
        lines.append("（无）")
    lines += ["", PUBLIC_PROMPT]
    return "\n".join(lines).rstrip() + "\n"


def render_dimension_page(spec: dict) -> str:
    """维度页：层级 + 属性 + 取数映射；实际值在 diag 维度表/事实列。"""
    lines = ["# 维度：%s" % _s(spec.get("name") or spec.get("dimension_id")), "",
             "## 自助取数映射接口", "",
             "- mapping_object_type: `%s`" % _s(spec.get("mapping_object_type") or "dimension"),
             "- mapping_object_id: `%s`" % _s(spec.get("mapping_object_id") or spec.get("dimension_id") or "—")]
    services = spec.get("mapping_services") or []
    if services:
        lines.append("- 可用 MCP 服务：" + "；".join(
            "`%s`（mappingServiceId=`%s`，%s）" %
            (_s(s.get("service_name")), _s(s.get("service_id")),
             _s(s.get("database_type"))) for s in services))
    else:
        lines.append("- 可用 MCP 服务：（无独立 binding；按 formula_only 处理）")
    if spec.get("description"):
        lines += ["- 说明：%s" % _s(spec["description"])]
    lines += ["", "- 成员键：`%s`" % _s(spec.get("member_key") or "—"), "", "## 层级", ""]
    levels = spec.get("levels") or []
    if levels:
        lines += _table(["层级", "名称", "属性", "计算方式", "参数", "说明"], [
            [l.get("level_no"), l.get("level_name"), l.get("attribute_code"),
             l.get("formula_kind") or "direct", l.get("formula_params") or "{}",
             l.get("description")]
            for l in levels])
    else:
        lines.append("（无）")
    lines += ["", "## 维度属性", ""]
    attrs = spec.get("attributes") or []
    if attrs:
        lines += _table(["属性", "名称", "类型", "枚举", "说明"], [
            [a.get("attribute_code"), a.get("name"), a.get("data_type"),
             "是" if a.get("is_enum") else "否", a.get("description")]
            for a in attrs])
    else:
        lines.append("（无）")
    lines += ["", "## 使用该维度的事实（语义）", ""]
    facts = spec.get("facts") or []
    if facts:
        lines += _table(["事实", "事实键", "维度键", "JOIN"], [
            ["[%s](wiki:analytics/fact/%s)" % (f.get("fact_name"), f.get("fact_code")),
             f.get("fact_key") or "—", f.get("dimension_key") or "—",
             f.get("join_type") or "none"]
            for f in facts])
    else:
        lines.append("（无）")
    lines += ["", PUBLIC_PROMPT]
    return "\n".join(lines).rstrip() + "\n"


def render_scenario_page(spec: dict) -> str:
    """场景页：来源 + 固定筛选 + 关联路径；映射明细不进页面。"""
    db_type = _s(spec.get("database_type"))
    lines = ["# 标准取数场景：%s" % _s(spec.get("name") or spec.get("scenario_id")), ""]
    if spec.get("description"):
        lines += ["- 说明：%s" % _s(spec["description"])]
    if db_type:
        lines += ["- 数据库类型：%s（%s）" % (DB_TYPE_LABELS.get(db_type, db_type), db_type)]
    if spec.get("source_locator"):
        lines += ["- 来源定位：`%s`" % _s(spec["source_locator"])]
    facts = spec.get("facts") or []
    if facts:
        lines += ["- 来源事实：%s" % "、".join(
            "[%s](wiki:analytics/fact/%s)" % (f.get("fact_name"), f.get("fact_code"))
            for f in facts)]
    if spec.get("service_name"):
        lines += ["- MCP 服务：`%s`（`%s`）" % (_s(spec.get("service_name")), _s(spec.get("service_id")))]
    if facts:
        lines += ["- 公共 MCP 服务校验：%s" % (
            "通过" if spec.get("has_common_service") in (True, "t", "true", 1) else "未通过（join/filter 存疑）")]
        if spec.get("common_services"):
            lines += ["- 公共服务候选：`%s`" % "`, `".join(_s(x) for x in spec["common_services"])]
    if spec.get("source_sql"):
        lines += ["", "## 来源 SQL", "", _code_block(spec["source_sql"])]
    lines += ["", "## 固定筛选", ""]
    filters = spec.get("filters") or []
    if filters:
        lines += _table(["序号", "表", "字段", "条件", "含义", "必带"], [
            [f.get("seq"),
             ".".join(x for x in (_s(f.get("schema_name")), _s(f.get("table_name"))) if x),
             f.get("column_name"), "`%s`" % _cell(f.get("expression")),
             f.get("meaning"), "是" if f.get("required") else "否"]
            for f in filters])
    else:
        lines.append("（无）")
    lines += ["", "## 关联路径", ""]
    joins = spec.get("joins") or []
    if joins:
        lines += _table(["序号", "左表/字段", "右表/字段", "类型", "条件", "说明"], [
            [j.get("seq"),
             "%s.%s" % (".".join(x for x in (_s(j.get("left_schema")), _s(j.get("left_table"))) if x),
                        _s(j.get("left_column"))),
             "%s.%s" % (".".join(x for x in (_s(j.get("right_schema")), _s(j.get("right_table"))) if x),
                        _s(j.get("right_column"))),
             j.get("join_type"), j.get("condition"), j.get("note")]
            for j in joins])
    else:
        lines.append("（无）")
    return "\n".join(lines).rstrip() + "\n"


# ---------------------------------------------------------------------------
# 页面同步（幂等；只写 Fact / Dimension / StandardQueryScenario）
# ---------------------------------------------------------------------------
def _write_page(kb_id: str, page: dict) -> dict:
    """统一走 ke_pages.write_knowledge：PG 正文与图节点 articleBody 同源。"""
    import ke_pages  # noqa: PLC0415
    return ke_pages.write_knowledge(
        kb_id, page, strict_source=True, sync_folders=False, skip_graph=False)


def _service_slug(service_name: str) -> str:
    # 与本体页 slug 规则一致：<类型所属模块>/<类小写>/<名称>
    return "agent/mcpservice/%s" % _s(service_name)


def _service_page(row: dict) -> dict:
    name = _s(row.get("service_name"))
    db_type = _s(row.get("database_type"))
    lines = [
        "# MCP 服务：%s" % name, "",
        "## 自助取数映射接口", "",
        "- mapping_service_id: `%s`" % _s(row.get("service_id")),
        "- database_type: `%s`（%s）" % (db_type, DB_TYPE_LABELS.get(db_type, db_type)),
        "- platform_mcp_service_id: `%s`" % _s(row.get("platform_service_id") or "—"),
        "- 作用域：%s" % _s(row.get("scope") or "—"),
        "- 能力：%s" % _s(row.get("capabilities") or "—"),
        "- 说明：%s" % _s(row.get("description") or "—"), "",
        "## 使用该服务的对象", "",
    ]
    objects = row.get("objects") or []
    if objects:
        lines += _table(["类型", "对象", "绑定类型"], [
            [o.get("object_type"), o.get("object_name") or o.get("object_id"),
             o.get("binding_type")]
            for o in objects])
    else:
        lines.append("（无）")
    lines += ["", PUBLIC_PROMPT]
    return {
        "slug": _service_slug(name),
        "title": "MCP 服务：%s" % name,
        "page_type": "agent:MCPService",
        "mode": "document",
        "wiki_content": "\n".join(lines).rstrip() + "\n",
        "summary": ("MCP 服务 %s（%s）" % (name, db_type))[:200],
        "attributes": dict(
            {"analytics:databaseType": db_type,
             "analytics:mappingServiceId": _s(row.get("service_id")),
             "skos:definition": _s(row.get("description") or "")},
            **({"analytics:platformMcpServiceId": _s(row["platform_service_id"])}
               if row.get("platform_service_id") else {})),
        "source": {"source_text": "analytics mcp service projection"},
    }


def list_active_services() -> list[dict]:
    rows = ke_db.psql_csv(
        "SELECT s.service_id, s.service_name, s.database_type, s.scope, "
        "       s.capabilities, s.description, "
        "       (SELECT ms.id FROM mcp_services ms WHERE ms.name=s.service_name "
        "         AND ms.deleted_at IS NULL ORDER BY ms.created_at DESC LIMIT 1) "
        "         AS platform_service_id, "
        "       coalesce((SELECT jsonb_agg(jsonb_build_object("
        "           'object_type', b.object_type, 'object_id', b.object_id, "
        "           'binding_type', b.binding_type, 'object_name', "
        "           CASE b.object_type WHEN 'fact' THEN f.name "
        "                WHEN 'dimension' THEN d.name ELSE b.object_id END) "
        "           ORDER BY b.object_type, b.object_id) "
        "         FROM analytics.binding b "
        "         LEFT JOIN analytics.fact f ON b.object_type='fact' AND f.fact_id=b.object_id "
        "         LEFT JOIN analytics.dimension d ON b.object_type='dimension' AND d.dimension_id=b.object_id "
        "        WHERE b.service_id=s.service_id AND b.status='active'), '[]'::jsonb) AS objects "
        "  FROM analytics.mcp_service s WHERE s.status='active' ORDER BY s.service_name")
    for row in rows:
        row["objects"] = _json(row.get("objects"), [])
    return rows


def _semantic_edges() -> list[tuple]:
    """从配置重建全量语义边（事实维度 + 场景来源/服务 + 对象服务）。"""
    rows = ke_db.psql_csv(
        "SELECT fact_code, dimension_code FROM analytics.v_fact_dimension_path "
        "WHERE status='active'")
    edges = [("analytics/fact/%s" % r["fact_code"],
              "analytics:factUsesDimension",
              "analytics/dimension/%s" % r["dimension_code"]) for r in rows]
    scenario_facts = ke_db.psql_csv(
        "SELECT scenario_id, fact_code FROM analytics.v_scenario_fact "
        "WHERE status='active'")
    for r in scenario_facts:
        edges.append(("analytics/scenario/%s" % r["scenario_id"],
                      "analytics:scenarioBasedOnFact",
                      "analytics/fact/%s" % r["fact_code"]))
    scenario_services = ke_db.psql_csv(
        "SELECT s.scenario_id, m.service_name FROM analytics.scenario s "
        "JOIN analytics.mcp_service m ON m.service_id=s.service_id "
        "WHERE s.status='active' AND m.status='active'")
    for r in scenario_services:
        edges.append(("analytics/scenario/%s" % r["scenario_id"],
                      "analytics:scenarioUsesMcpService",
                      _service_slug(r["service_name"])))
    edges.extend(_binding_service_edges())
    return edges


def _binding_service_edges() -> list[tuple]:
    rows = ke_db.psql_csv(
        "SELECT b.object_type, "
        "       CASE b.object_type WHEN 'fact' THEN f.code "
        "            WHEN 'dimension' THEN d.code ELSE b.object_id END AS object_code, "
        "       s.service_name "
        "  FROM analytics.binding b "
        "  JOIN analytics.mcp_service s ON s.service_id=b.service_id AND s.status='active' "
        "  LEFT JOIN analytics.fact f ON b.object_type='fact' AND f.fact_id=b.object_id "
        "  LEFT JOIN analytics.dimension d ON b.object_type='dimension' AND d.dimension_id=b.object_id "
        " WHERE b.status='active' AND b.object_type IN ('fact','dimension')")
    out = []
    for row in rows:
        prefix = "analytics/fact" if row["object_type"] == "fact" else "analytics/dimension"
        out.append(("%s/%s" % (prefix, row["object_code"]),
                    "analytics:objectUsesMcpService",
                    _service_slug(row["service_name"])))
    return out


def _page_of(kind: str, spec: dict) -> dict:
    """把配置规格转成 ke_pages.write_knowledge 的 spec（不写映射页）。"""
    code = _s(spec.get("code") or spec.get("scenario_id") or spec.get("fact_id")
              or spec.get("dimension_id"))
    if kind == "fact":
        slug = "analytics/fact/%s" % code
        title = "事实：%s" % _s(spec.get("name") or code)
        page_type = "analytics:Fact"
        content = render_fact_page(spec)
        attrs = {"analytics:grain": _s(spec.get("grain")),
                 "analytics:mappingObjectType": _s(spec.get("mapping_object_type") or "fact"),
                 "analytics:mappingObjectId": _s(spec.get("mapping_object_id") or spec.get("fact_id"))}
    elif kind == "dimension":
        slug = "analytics/dimension/%s" % code
        title = "维度：%s" % _s(spec.get("name") or code)
        page_type = "analytics:Dimension"
        content = render_dimension_page(spec)
        attrs = {"analytics:mappingObjectType": _s(spec.get("mapping_object_type") or "dimension"),
                 "analytics:mappingObjectId": _s(spec.get("mapping_object_id") or spec.get("dimension_id"))}
    elif kind == "scenario":
        slug = "analytics/scenario/%s" % code
        title = "标准取数场景：%s" % _s(spec.get("name") or code)
        page_type = "analytics:StandardQueryScenario"
        content = render_scenario_page(spec)
        attrs = {}
    else:
        raise ValueError("kind 只能是 fact / dimension / scenario")
    if spec.get("description"):
        attrs["skos:definition"] = _s(spec["description"])
    return {"slug": slug, "title": title, "page_type": page_type, "mode": "document",
            "wiki_content": content, "summary": _s(spec.get("description"))[:200],
            "attributes": attrs,
            "source": {"source_text": "analytics config projection"}}


def sync_pages(kb_id: str, kind: str = "all", ids: list[str] | None = None,
               dry_run: bool = True, skip_graph: bool = False) -> dict:
    """把配置表投影成 wiki 页（幂等）。返回逐页回执，不抛单页失败。

    投影事实 / 维度 / 场景 / active MCP 服务四类页；binding/mapping/enum/join/
    filter **不生成知识页**。统一走 `ke_pages.write_knowledge`（PG 正文与图
    articleBody 同源）；`skip_graph` 仅为兼容旧调用保留。
    """
    import ke_pages  # noqa: PLC0415  懒导入：纯渲染测试不需要页面依赖

    if not kb_id:
        raise ValueError("sync_pages 需要 kb_id")
    kinds = (["fact", "dimension", "scenario", "service"] if kind == "all"
             else [kind])
    results: list[dict] = []
    edges: list[tuple] = []
    ok = 0
    if not dry_run:
        # 用户 2026-11-22 确认：dim_tool 冗余维度硬删除（PG 页 + 图节点/边）
        try:
            old = ke_db.psql_csv(
                "SELECT slug FROM wiki_pages WHERE knowledge_base_id=%s "
                "AND deleted_at IS NULL AND slug LIKE 'analytics/mcpservice/%%'"
                % _q(kb_id))
            old_slugs = ["analytics/dimension/tool"] + [r["slug"] for r in old]
            deleted = ke_pages.delete_pages(
                kb_id, old_slugs, sync_folders=False)
            results.append({"kind": "cleanup", "id": "dim_tool_old_service",
                            "ok": True, "receipt": deleted})
        except Exception as exc:  # noqa: BLE001
            results.append({"kind": "cleanup", "id": "dim_tool_old_service",
                            "ok": False, "error": str(exc)[:400]})
    for k in kinds:
        if k == "service":
            continue
        target_ids = ids if (ids and kind != "all") else list_ids(k)
        for object_id in target_ids:
            try:
                spec = {"fact": fact_spec, "dimension": dimension_spec,
                        "scenario": scenario_spec}[k](object_id)
                page = _page_of(k, spec)
                if dry_run:
                    receipt = {"dry_run": True, "slug": page["slug"]}
                else:
                    receipt = _write_page(kb_id, page)
                    if not (receipt.get("ok") or receipt.get("applied")):
                        raise RuntimeError(receipt.get("error") or receipt.get("hint")
                                           or "write_knowledge 未写入")
                results.append({"kind": k, "id": object_id,
                                "slug": page["slug"], "ok": True, "receipt": receipt})
                if not dry_run:
                    ok += 1
                    if k == "fact":
                        for d in spec.get("dimensions") or []:
                            code = _s(d.get("dimension_code"))
                            if code:
                                edges.append((page["slug"], "analytics:factUsesDimension",
                                              "analytics/dimension/%s" % code))
            except Exception as exc:  # noqa: BLE001  单页失败不打断整批
                results.append({"kind": k, "id": object_id, "ok": False,
                                "error": str(exc)[:400]})
    if "service" in kinds:
        for row in list_active_services():
            try:
                page = _service_page(row)
                if dry_run:
                    receipt = {"dry_run": True, "slug": page["slug"]}
                else:
                    receipt = _write_page(kb_id, page)
                    if not (receipt.get("ok") or receipt.get("applied")):
                        raise RuntimeError(receipt.get("error") or receipt.get("hint")
                                           or "write_knowledge 未写入")
                    ok += 1
                results.append({"kind": "service", "id": row.get("service_id"),
                                "slug": page["slug"], "ok": True, "receipt": receipt})
            except Exception as exc:  # noqa: BLE001
                results.append({"kind": "service", "id": row.get("service_id"),
                                "ok": False, "error": str(exc)[:400]})
    if not dry_run:
        # 无论本次同步哪一类页面，都按配置重建全量语义边，避免局部同步清空其它边
        edges = _semantic_edges()
    if not dry_run and results:
        try:
            ke_pages.sync_folders(kb_id)
        except Exception as exc:  # noqa: BLE001
            results.append({"kind": "folders", "ok": False, "error": str(exc)[:400]})
    edge_out = {}
    if not dry_run:
        try:
            # 语义关系重建：先删本库 analytics 对象的旧出边，避免维度减少后残留
            import ke_neo4j  # noqa: PLC0415
            for rel in ("`analytics:factUsesDimension`",
                        "`analytics:objectUsesMcpService`",
                        "`analytics:scenarioBasedOnFact`",
                        "`analytics:scenarioUsesMcpService`"):
                ke_neo4j.query(
                    "MATCH (a:BodhiInstance {kb_id:$kb})-[r:%s]->() "
                    "WHERE a.slug STARTS WITH 'analytics/' DELETE r" % rel,
                    {"kb": kb_id})
        except Exception as exc:  # noqa: BLE001
            results.append({"kind": "edge_cleanup", "ok": False,
                            "error": str(exc)[:400]})
    if not dry_run and edges:
        try:
            import ke_graph  # noqa: PLC0415
            edge_out = ke_graph.add_edges_batch(kb_id, edges)
            results.append({"kind": "edges", "ok": True, "edges": edge_out})
        except Exception as exc:  # noqa: BLE001
            results.append({"kind": "edges", "ok": False, "error": str(exc)[:400]})
    return {"kb_id": kb_id, "kind": kind, "dry_run": bool(dry_run),
            "count": len(results), "written": ok, "edges": edge_out,
            "results": results}


def _resolve_mapping_object(object_type: str, object_id: str) -> tuple[dict, str]:
    """解析 canonical / code / 标题 / 旧 slug 到内部对象；返回 (row, input_form)。"""
    typ = (object_type or "").strip().lower()
    val = (object_id or "").strip()
    if typ not in ("fact", "dimension") or not val:
        return {}, ""
    table, id_col, code_col = {
        "fact": ("analytics.fact", "fact_id", "code"),
        "dimension": ("analytics.dimension", "dimension_id", "code"),
    }[typ]
    rows = ke_db.psql_csv(
        "SELECT %s AS object_id, %s AS object_code, name FROM %s "
        "WHERE %s=%s OR %s=%s OR name=%s OR ('analytics/%s/' || %s)=%s "
        "ORDER BY CASE WHEN %s=%s THEN 0 ELSE 1 END LIMIT 2"
        % (id_col, code_col, table, id_col, _q(val), code_col, _q(val), _q(val),
           typ, code_col, _q(val), id_col, _q(val)))
    if not rows:
        return {}, ""
    if len(rows) > 1:
        # 同名对象不自动选，避免歧义
        return {}, "ambiguous"
    return rows[0], "resolved" if rows[0]["object_id"] != val else "canonical"


def mapping_lookup(mapping_object_type: str = "", mapping_object_id: str = "",
                   mapping_service_id: str = "") -> dict:
    """按知识页映射接口查物理映射（只读；知识页不承载物理表/列）。

    正式且唯一参数：mapping_object_type / mapping_object_id / mapping_service_id。
    旧参数 object_type / object_id / fact_id / dimension_id / canonical_id 不再接受。
    """
    typ = (mapping_object_type or "").strip().lower()
    oid = (mapping_object_id or "").strip()
    sid = (mapping_service_id or "").strip()
    if typ not in ("fact", "dimension") or not oid:
        return {"ok": False, "status": "invalid_argument",
                "mapping_object_type": typ, "mapping_object_id": oid,
                "mapping_service_id": sid or None, "bindings": [], "levels": [],
                "error": "invalid_argument",
                "hint": ("必须使用 mapping_object_type=fact|dimension，"
                         "mapping_object_id=fact_id/dimension_id；不要传 slug、标题或 object_id")}
    obj, how = _resolve_mapping_object(typ, oid)
    if not obj:
        return {"ok": False, "status": "missing", "mapping_object_type": typ,
                "mapping_object_id": oid, "mapping_service_id": sid or None,
                "bindings": [], "levels": [], "error": "unknown_mapping_object",
                "hint": ("对象不存在或有同名歧义；请从知识页复制 "
                         "mapping_object_type / mapping_object_id，不要用 slug 或标题")}
    resolved = str(obj["object_id"])
    where = ("WHERE b.object_type=%s AND b.object_id=%s AND b.status='active'"
             % (_q(typ), _q(resolved)))
    if sid:
        where += " AND b.service_id=%s" % _q(sid)
    rows = ke_db.psql_csv(
        "SELECT b.binding_id, b.object_type, b.object_id, b.service_id, "
        "       b.binding_type, b.tool_name, s.service_name, s.database_type, "
        "       s.status AS service_status, "
        "       COALESCE((SELECT jsonb_agg(to_jsonb(m) ORDER BY m.sort_order, m.mapping_id) "
        "                   FROM analytics.field_mapping m "
        "                  WHERE m.binding_id=b.binding_id AND m.status='active'), '[]'::jsonb) "
        "         AS field_mappings, "
        "       COALESCE((SELECT jsonb_agg(to_jsonb(e) ORDER BY e.semantic_id, e.semantic_value) "
        "                   FROM analytics.enum_mapping e "
        "                  WHERE e.binding_id=b.binding_id AND e.status='active'), '[]'::jsonb) "
        "         AS enum_mappings "
        "  FROM analytics.binding b "
        "  LEFT JOIN analytics.mcp_service s ON s.service_id=b.service_id "
        + where + " ORDER BY b.service_id, b.binding_id")
    db_type = next((r.get("database_type") for r in rows
                    if r.get("service_status") == "active"), "") or "postgresql"
    levels = []
    if typ == "dimension":
        member = ke_db.psql_csv(
            "SELECT member_key FROM analytics.dimension WHERE dimension_id=%s"
            % _q(resolved))
        member_key = (member[0].get("member_key") if member else "") or ""
        levels = ke_db.psql_csv(
            "SELECT level_no, level_name, attribute_code, formula_kind, "
            "       formula_params::text AS formula_params, data_type "
            "  FROM analytics.dimension_level WHERE dimension_id=%s "
            " ORDER BY level_no" % _q(resolved))
        for lv in levels:
            lv["formula_sql"] = render_formula(
                lv.get("formula_kind") or "direct", db_type,
                member_key, lv.get("formula_params") or "{}")
    for row in rows:
        row["field_mappings"] = _json(row.get("field_mappings"), [])
        row["enum_mappings"] = _json(row.get("enum_mappings"), [])
        row["mapping_object_type"] = typ
        row["mapping_object_id"] = resolved
        row["mapping_service_id"] = row.get("service_id")
        row["levels"] = levels
    active_rows = [r for r in rows if r.get("service_status") == "active"]
    service_missing = False
    if sid and not rows:
        service = ke_db.psql_csv(
            "SELECT service_id FROM analytics.mcp_service "
            " WHERE service_id=%s AND status='active'" % _q(sid))
        service_missing = not service
    has_mapping = any(r.get("field_mappings") for r in active_rows)
    if (rows and not active_rows) or service_missing:
        status, ok = "missing", False
    elif active_rows and has_mapping:
        status, ok = "mapped", True
    elif active_rows:
        status, ok = "missing", False
    elif typ == "dimension" and levels:
        # 维度可以没有独立 binding：直接列/公式维度从事实 binding 或 formula_sql 取。
        status, ok = "formula_only", True
    else:
        status, ok = "missing", False
    hint = ""
    if not ok:
        if active_rows:
            hint = "active binding 缺少 active field_mapping；按缺映射处理"
        else:
            hint = ("MCP 服务未 active；请检查 mapping_service_id 或服务状态"
                    if rows else
                    "没有 active binding/field_mapping；请检查 mapping_service_id 或对象 ID")
    elif status == "formula_only" and not rows:
        hint = ("该维度无独立 binding：使用 levels[].formula_sql；"
                "直接列维度同时参考事实 binding 的 field_mapping")
    elif len(rows) > 1:
        hint = "多个服务可用，请让用户选择"
    elif how != "canonical":
        hint = ("输入不是 canonical mapping_object_id，已兼容解析为 `%s`；"
                "后续请直接使用知识页的 mapping_object_id" % resolved)
    return {"ok": ok, "status": status,
            "mapping_object_type": typ, "mapping_object_id": resolved,
            "input_mapping_object_id": oid, "resolved_from": how,
            "mapping_service_id": sid or None,
            "bindings": rows, "levels": levels, "hint": hint}


def scenario_lookup(scenario_id: str = "", mapping_object_id: str = "",
                    mapping_service_id: str = "", intent: str = "") -> dict:
    """按场景/事实/服务/意图查标准场景候选（只读，不生成页面）。

    有多个候选或候选存在多条 JOIN 路径时返回 requires_user_confirmation。
    """
    if scenario_id:
        spec = scenario_spec(scenario_id)
        return {"ok": True, "scenario": spec, "candidates": [spec], "count": 1,
                "requires_user_confirmation": len(spec.get("joins") or []) > 1}
    if mapping_object_id:
        fact = ke_db.psql_csv(
            "SELECT fact_id FROM analytics.fact WHERE fact_id=%s"
            % _q(mapping_object_id))
        if not fact:
            return {"ok": False, "error": "invalid_mapping_object",
                    "scenario": None, "candidates": [], "count": 0,
                    "requires_user_confirmation": False,
                    "hint": "mapping_object_id 必须是事实的 fact_id；先调用 mapping_lookup"}
        if mapping_service_id:
            active = ke_db.psql_csv(
                "SELECT binding_id FROM analytics.binding "
                " WHERE object_type='fact' AND object_id=%s AND service_id=%s "
                "   AND status='active'" %
                (_q(mapping_object_id), _q(mapping_service_id)))
            if not active:
                return {"ok": False, "error": "service_mismatch",
                        "scenario": None, "candidates": [], "count": 0,
                        "requires_user_confirmation": False,
                        "hint": "事实在该 MCP 服务下没有 active binding；不得用场景 SQL 猜字段"}
    rows = ke_db.psql_csv(
        "SELECT * FROM analytics.v_scenario_detail WHERE status='active' "
        "ORDER BY scenario_id")
    terms = [x for x in (intent or "").lower().replace("，", " ").replace(",", " ").split()
             if x]
    out = []
    for row in rows:
        spec = dict(row)
        for key in ("joins", "filters", "facts", "common_services"):
            spec[key] = _json(spec.get(key), [])
        facts = spec.get("facts") or []
        if mapping_object_id and not any(f.get("fact_id") == mapping_object_id for f in facts):
            continue
        if mapping_service_id and spec.get("service_id") != mapping_service_id:
            continue
        hay = " ".join([str(spec.get("name") or ""), str(spec.get("description") or "")] +
                       [str(f.get("fact_name") or "") for f in facts])
        score = sum(1 for t in terms if t in hay.lower())
        spec["match_score"] = score
        spec["match_reasons"] = [t for t in terms if t in hay.lower()]
        out.append(spec)
    out.sort(key=lambda x: (-int(x.get("match_score") or 0), str(x.get("scenario_id"))))
    ambiguous = (len(out) > 1 and
                 (out[0].get("match_score") == out[1].get("match_score")
                  or not out[0].get("match_score")))
    multi_join = any(len(x.get("joins") or []) > 1 for x in out)
    return {"ok": True,
            "scenario": out[0] if len(out) == 1 else None,
            "candidates": out, "count": len(out),
            "requires_user_confirmation": bool(ambiguous or multi_join),
            "reason": ("multiple_join_paths" if multi_join else
                       "ambiguous_scenario_match" if ambiguous else "")}
