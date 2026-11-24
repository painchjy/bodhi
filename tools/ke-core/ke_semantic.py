"""标准取数场景 → 语义层提取（只读计划 + 用户确认后写入）。

职责
----
* `scenario_semantic_plan`：**只读**。验证场景 SQL 执行前置条件（service/binding），
  扫描 SQL 表与列，和现有 Fact 语义层比对，列出缺失指标/字段/派生指标/维度/映射，
  并生成需要用户确认的中文含义与枚举问题。
* `scenario_semantic_apply`：**唯一写入口**。必须带 `plan_id` + `confirm=true`；
  只写 `analytics` 配置表，写完调用 `ke_analytics.sync_pages` 同步页面与图关系。

设计边界
--------
* 语义字段名、中文含义、枚举标签一律来自配置或用户回答，**不猜**；
* 事实字段只能映射 `analytics.fact_table` 登记的事实表；维表交给 Dimension；
* `scenario_join` 只写事实表↔事实表，维度 JOIN 不写入；
* 写操作默认开启，可用环境变量 `BODHI_ANALYTICS_WRITE=0` 关闭（kill switch）。
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from typing import Any

import ke_analytics
import ke_db
import ke_semantic_sql


def _s(value: Any) -> str:
    return "" if value is None else str(value)


def _q(value: Any) -> str:
    return ke_db.sql_str(_s(value))


def _rows(sql: str) -> list[dict]:
    return ke_db.psql_csv(sql)


def _one(sql: str) -> dict:
    rows = _rows(sql)
    return rows[0] if rows else {}


_CJK = re.compile(r"[\u4e00-\u9fff]")


def _has_cjk(text: Any) -> bool:
    return bool(_CJK.search(_s(text)))


def _ident(text: Any, fallback: str = "item") -> str:
    value = re.sub(r"[^a-z0-9_]+", "_", _s(text).lower()).strip("_")
    if not value:
        return fallback
    if value[0].isdigit():
        value = "c_" + value
    return value or fallback


def _norm(text: Any) -> str:
    return ke_analytics._normalize_text(_s(text))


def _sim(text_a: Any, text_b: Any) -> float:
    return ke_analytics._term_score(_s(text_a), _s(text_b))


def _norm_filter(expr: Any) -> str:
    """把过滤条件归一成可比较文本：去别名、`NOT x` → `x = false`、裸列 → `col = true`。"""
    text = re.sub(r"\b[A-Za-z_]\w*\s*\.", "", _s(expr))
    text = re.sub(r"\s+", " ", text).strip().lower()
    text = re.sub(r"^not\s+([a-z_]\w*)$", r"\1 = false", text)
    if re.fullmatch(r"[a-z_]\w*", text):
        text = text + " = true"
    return re.sub(r"\s*=\s*", "=", text)


def _norm_expr(expr: Any, aliases: list[str] | None = None) -> str:
    text = ke_semantic_sql.normalize_expression(_s(expr), aliases)
    if re.fullmatch(r"[a-z_]\w*", text):
        text = text + "=true"
    return text


def _write_enabled() -> bool:
    return _s(os.environ.get("BODHI_ANALYTICS_WRITE", "1")).strip().lower() \
        not in ("0", "false", "off", "no")


# ---------------------------------------------------------------------------
# 配置读取
# ---------------------------------------------------------------------------
def _json_one(sql):
    raw = _s(ke_db.psql(sql)).strip()
    return json.loads(raw) if raw else {}


def _load_catalog() -> dict:
    active = _q("active")
    dimension = _q("dimension")
    dot = _q(".")
    payload = _json_one(
        "SELECT jsonb_build_object("
        "%s, coalesce((SELECT jsonb_agg(to_jsonb(f) ORDER BY f.fact_id) "
        "FROM analytics.fact f WHERE f.status=%s), jsonb_build_array()),"
        "%s, coalesce((SELECT jsonb_agg(to_jsonb(t) ORDER BY t.fact_id, t.sort_order, t.table_id) "
        "FROM analytics.fact_table t WHERE t.status=%s), jsonb_build_array()),"
        "%s, coalesce((SELECT jsonb_agg(to_jsonb(d) ORDER BY d.dimension_id) "
        "FROM analytics.dimension d WHERE d.status=%s), jsonb_build_array()),"
        "%s, coalesce((SELECT jsonb_agg(to_jsonb(t) ORDER BY t.dimension_id, t.sort_order, t.table_id) "
        "FROM analytics.dimension_table t WHERE t.status=%s), jsonb_build_array()),"
        "%s, coalesce((SELECT jsonb_agg(to_jsonb(b) ORDER BY b.dimension_id) "
        "FROM (SELECT DISTINCT object_id AS dimension_id FROM analytics.binding "
        "WHERE object_type=%s AND status=%s) b), jsonb_build_array()),"
        "%s, coalesce((SELECT jsonb_agg(to_jsonb(x) ORDER BY x.dimension_id, x.sort_order) "
        "FROM (SELECT b.object_id AS dimension_id, m.semantic_kind, m.semantic_id, "
        "m.source_schema, m.source_table, m.source_column, "
        "split_part(m.semantic_id, %s, 2) AS semantic_code, m.sort_order "
        "FROM analytics.binding b JOIN analytics.field_mapping m "
        "ON m.binding_id=b.binding_id AND m.status=%s "
        "WHERE b.object_type=%s AND b.status=%s) x), jsonb_build_array())"
        ")" % (_q("facts"), active, _q("fact_tables"), active,
               _q("dimensions"), active, _q("dimension_tables"), active,
               _q("dimension_bindings"), dimension, active,
               _q("dimension_columns"), dot, active, dimension, active))
    payload = payload or {}
    facts = payload.get("facts") or []
    fact_tables = payload.get("fact_tables") or []
    dimensions = payload.get("dimensions") or []
    dim_tables = payload.get("dimension_tables") or []
    dim_bindings = payload.get("dimension_bindings") or []
    dim_columns = payload.get("dimension_columns") or []
    for row in fact_tables:
        row["grain_keys"] = ke_analytics._json(row.get("grain_keys"), [])
    by_id = {row["fact_id"]: row for row in facts}
    for row in facts:
        row["tables"] = []
    for row in fact_tables:
        if row["fact_id"] in by_id:
            by_id[row["fact_id"]]["tables"].append(row)
    return {
        "facts": facts,
        "facts_by_id": by_id,
        "fact_tables": fact_tables,
        "fact_table_keys": {(_s(r.get("source_schema")), _s(r.get("source_table"))) for r in fact_tables},
        "dimensions": dimensions,
        "dimensions_by_id": {row["dimension_id"]: row for row in dimensions},
        "dimension_tables": dim_tables,
        "dimension_table_keys": {(_s(r.get("source_schema")), _s(r.get("source_table"))) for r in dim_tables},
        "dimension_table_map": {(_s(r.get("source_schema")), _s(r.get("source_table"))): r.get("dimension_id") for r in dim_tables},
        "dimension_binding_ids": {r["dimension_id"] for r in dim_bindings},
        "dimension_columns": dim_columns,
    }

def _load_fact_catalog(fact_id: str) -> dict:
    if not fact_id:
        return {"base_metrics": [], "derived_metrics": [], "fact_fields": [],
                "mappings": [], "fact_dimensions": [], "by_semantic": {}}
    active = _q("active")
    fq = _q(fact_id)
    fact = _q("fact")
    payload = _json_one(
        "SELECT jsonb_build_object("
        "%s, coalesce((SELECT jsonb_agg(to_jsonb(b) ORDER BY b.sort_order, b.metric_id) "
        "FROM analytics.base_metric b WHERE b.fact_id=%s AND b.status=%s), jsonb_build_array()),"
        "%s, coalesce((SELECT jsonb_agg(to_jsonb(d) ORDER BY d.sort_order, d.metric_id) "
        "FROM analytics.derived_metric d WHERE d.fact_id=%s AND d.status=%s), jsonb_build_array()),"
        "%s, coalesce((SELECT jsonb_agg(to_jsonb(f) ORDER BY f.sort_order, f.field_id) "
        "FROM analytics.fact_field f WHERE f.fact_id=%s AND f.status=%s), jsonb_build_array()),"
        "%s, coalesce((SELECT jsonb_agg(to_jsonb(x) ORDER BY x.sort_order, x.mapping_id) "
        "FROM (SELECT m.*, b.service_id FROM analytics.field_mapping m "
        "JOIN analytics.binding b ON b.binding_id=m.binding_id "
        "WHERE b.object_type=%s AND b.object_id=%s AND b.status=%s AND m.status=%s) x), jsonb_build_array()),"
        "%s, coalesce((SELECT jsonb_agg(to_jsonb(d) ORDER BY d.sort_order, d.dimension_id) "
        "FROM analytics.fact_dimension d WHERE d.fact_id=%s AND d.status=%s), jsonb_build_array())"
        ")" % (_q("base_metrics"), fq, active, _q("derived_metrics"), fq, active,
               _q("fact_fields"), fq, active, _q("mappings"), fact, fq, active, active,
               _q("fact_dimensions"), fq, active))
    payload = payload or {}
    base = payload.get("base_metrics") or []
    derived = payload.get("derived_metrics") or []
    fields = payload.get("fact_fields") or []
    mappings = payload.get("mappings") or []
    fact_dimensions = payload.get("fact_dimensions") or []
    by_semantic = {}
    for row in mappings:
        by_semantic.setdefault((_s(row.get("semantic_kind")), _s(row.get("semantic_id"))), []).append(row)
    return {
        "base_metrics": base, "derived_metrics": derived, "fact_fields": fields,
        "mappings": mappings, "fact_dimensions": fact_dimensions,
        "by_semantic": by_semantic,
    }

def _load_scenario(scenario_id: str) -> dict:
    return _one(
        "SELECT s.*, m.service_name, m.database_type, m.status AS service_status "
        "  FROM analytics.scenario s "
        "  LEFT JOIN analytics.mcp_service m ON m.service_id=s.service_id "
        " WHERE s.scenario_id=%s" % _q(scenario_id))


def _scenario_facts(scenario_id: str) -> list[dict]:
    return _rows(
        "SELECT sf.fact_id, sf.sort_order, f.code, f.name "
        "  FROM analytics.scenario_fact sf "
        "  JOIN analytics.fact f ON f.fact_id=sf.fact_id AND f.status='active' "
        " WHERE sf.scenario_id=%s AND sf.status='active' "
        " ORDER BY sf.sort_order, sf.fact_id" % _q(scenario_id))


def _active_binding(fact_id: str, service_id: str) -> dict:
    return _one(
        "SELECT binding_id, binding_type FROM analytics.binding "
        " WHERE object_type='fact' AND object_id=%s AND service_id=%s "
        "   AND status='active' ORDER BY binding_id LIMIT 1"
        % (_q(fact_id), _q(service_id)))


def _service_bindings(fact_id: str) -> list[str]:
    return [_s(r.get("service_id")) for r in _rows(
        "SELECT DISTINCT service_id FROM analytics.binding "
        " WHERE object_type='fact' AND object_id=%s AND status='active'"
        % _q(fact_id))]



# ---------------------------------------------------------------------------
# SQL 扫描结果 → 语义候选（事实/维度/指标/字段）
# ---------------------------------------------------------------------------
def _split_qualified(value: str) -> tuple[str, str]:
    text = _s(value)
    if "." in text:
        schema, table = text.split(".", 1)
        return schema, table
    return "", text


def _dedupe_links(links: list[dict]) -> list[dict]:
    seen, out = set(), []
    for link in links:
        key = (link.get("dimension_id"), link.get("fact_key"),
               link.get("dimension_key"), link.get("join_type"))
        if key in seen:
            continue
        seen.add(key)
        out.append(link)
    return out


def _dedupe_warnings(warnings: list[dict]) -> list[dict]:
    seen, out = set(), []
    for item in warnings:
        key = json.dumps(item, ensure_ascii=False, sort_keys=True)
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out


def _is_boolean_column(parsed: dict, column: str) -> bool:
    """列在 WHERE/FILTER 里以布尔语义出现（裸列 / NOT 列 / = true|false）。"""
    texts = [c.get("filter") for c in parsed.get("columns") or []]
    texts += [f.get("expression") for f in parsed.get("filters") or []]
    for text in texts:
        normalized = _norm_filter(text)
        if normalized in ("%s=true" % _s(column).lower(),
                          "%s=false" % _s(column).lower()):
            return True
    return False


def _metric_source(column: dict, fact_refs: list, role_of, catalog: dict,
                   fact_id: str = "") -> dict:
    """聚合的来源物理列：count 取主表最细粒度键；其他优先聚合参数。"""
    if _s(column.get("aggregation")).lower() in ("count", "count_distinct"):
        fact_refs = []
    arg = _s(column.get("arg")).strip()
    for ref in fact_refs:
        name = _s(ref[2])
        if re.search(r"(?:^|\.)%s$" % re.escape(name), arg):
            return {"schema": _s(ref[0]), "table": _s(ref[1]), "column": name}
    for ref in fact_refs:
        return {"schema": _s(ref[0]), "table": _s(ref[1]), "column": _s(ref[2])}
    fact = catalog["facts_by_id"].get(fact_id) or {}
    primary = None
    for table in fact.get("tables") or []:
        if table.get("is_primary") in (True, "t", "true"):
            primary = table
            break
    if primary is None and fact.get("tables"):
        primary = fact["tables"][0]
    if primary:
        grain = primary.get("grain_keys") or []
        return {"schema": _s(primary.get("source_schema")),
                "table": _s(primary.get("source_table")),
                "column": _s(grain[-1]) if grain else "*"}
    for table in catalog["fact_tables"]:
        if _s(table.get("source_schema")) and _s(table.get("source_table")):
            return {"schema": _s(table.get("source_schema")),
                    "table": _s(table.get("source_table")), "column": "*"}
    return {"schema": "", "table": "", "column": "*"}



def _join_links(parsed: dict, catalog: dict, role_of, mark) -> tuple[list, list]:
    """识别事实↔维度 JOIN；返回 (维度链接候选, 告警)。"""
    links, warnings = [], []
    for join in parsed.get("joins") or []:
        left = _split_qualified(join.get("left_table"))
        right = _split_qualified(join.get("right_table"))
        lrole, rrole = role_of(left), role_of(right)
        mark(left + (_s(join.get("left_column")),), "join")
        mark(right + (_s(join.get("right_column")),), "join")
        if {lrole, rrole} != {"fact", "dimension"}:
            if "unknown" in (lrole, rrole):
                warnings.append({"kind": "unknown_table_join",
                                 "tables": [list(left), list(right)]})
            continue
        if lrole == "fact":
            fact_table, fact_col = left, _s(join.get("left_column"))
            dim_table, dim_col = right, _s(join.get("right_column"))
        else:
            fact_table, fact_col = right, _s(join.get("right_column"))
            dim_table, dim_col = left, _s(join.get("left_column"))
        dimension_id = catalog["dimension_table_map"].get(
            (_s(dim_table[0]), _s(dim_table[1])), "")
        if not dimension_id:
            continue
        dim = catalog["dimensions_by_id"].get(dimension_id) or {}
        links.append({
            "dimension_id": dimension_id,
            "key_code": _s(dim.get("member_key")) or dim_col,
            "fact_key": fact_col, "dimension_key": dim_col,
            "join_type": "inner" if join.get("join_type") == "inner" else "left",
            "source": "join",
        })
    return links, warnings


def _direct_link(catalog: dict, column: str) -> dict | None:
    """事实列 = 无物理 binding 的维度 member_key → 直接列维度。"""
    for dim in catalog["dimensions"]:
        if dim["dimension_id"] in catalog["dimension_binding_ids"]:
            continue
        if _norm(dim.get("member_key")) == _norm(column):
            return {"dimension_id": dim["dimension_id"],
                    "key_code": _s(dim.get("member_key")) or column,
                    "fact_key": column, "dimension_key": column,
                    "join_type": "none", "source": "direct"}
    return None



def _collect_fields(parsed: dict, role_of, resolve, mark, usage: dict) -> list[dict]:
    """把 SELECT / WHERE / FILTER / GROUP BY 中的事实列收敛成字段候选。"""
    fields: list[dict] = []
    index: dict[tuple, dict] = {}

    def add(ref: tuple, use: str, alias: str = "", label: str = "") -> None:
        key = (_s(ref[0]), _s(ref[1]), _s(ref[2]))
        mark(key, use)
        item = index.get(key)
        if item is None:
            item = {"kind": "fact_field", "table": [key[0], key[1]],
                    "column": key[2], "alias": alias, "label": label,
                    "code": _ident(alias or key[2], key[2]),
                    "source": {"schema": key[0], "table": key[1],
                               "column": key[2]},
                    "role": "display", "usages": set()}
            index[key] = item
            fields.append(item)
        item["usages"].add(use)
        if alias and not item.get("alias"):
            item["alias"] = alias
            item["code"] = _ident(alias, key[2])
        if label and not item.get("label"):
            item["label"] = label

    for column in parsed.get("columns") or []:
        if column.get("aggregation"):
            continue
        for ref in column.get("refs") or []:
            resolved = resolve(ref)
            if role_of(resolved) == "fact":
                add(resolved, "select", _s(column.get("alias")),
                    _s(column.get("label")))

    # GROUP BY 中仅用于粒度的列不生成事实字段；字段必须来自 SELECT/WHERE/FILTER。
    # WHERE/FILTER 里的列只作为过滤条件，不自动生成非指标字段。

    for item in fields:
        key = (_s(item["table"][0]), _s(item["table"][1]), _s(item["column"]))
        uses = item.pop("usages", set()) | usage.get(key, set())
        if "join" in uses:
            item["role"] = "key"
        elif "filter" in uses:
            item["role"] = "filter"
        item["boolean"] = _is_boolean_column(parsed, item["column"])
    return fields


def _analyze(parsed: dict, catalog: dict, fact_id: str = "") -> dict:
    """把扫描结果按表角色拆成：指标候选、事实字段候选、维度链接候选、告警。"""
    matched = ke_semantic_sql.match_tables(
        parsed, catalog["fact_table_keys"], catalog["dimension_table_keys"])
    tables = matched["tables"]
    role = {tuple(t["table_key"]): t["role"] for t in tables}
    alias_map = {t["alias"].lower(): (t["schema"], t["table"])
                 for t in tables if t["alias"]}
    usage: dict[tuple, set] = {}
    warnings: list[dict] = []

    def mark(key: tuple, use: str) -> None:
        usage.setdefault((_s(key[0]), _s(key[1]), _s(key[2])), set()).add(use)

    def resolve(ref: dict) -> tuple:
        key = alias_map.get(_s(ref.get("alias")).lower(), ("", ""))
        return key[0], key[1], _s(ref.get("column"))

    def role_of(key: tuple) -> str:
        return role.get((_s(key[0]), _s(key[1])), "unknown")

    links, join_warnings = _join_links(parsed, catalog, role_of, mark)
    warnings.extend(join_warnings)

    metrics = []
    for column in parsed.get("columns") or []:
        if not column.get("aggregation"):
            continue
        refs = []
        for ref in column.get("refs") or []:
            resolved = resolve(ref)
            if resolved not in refs:
                refs.append(resolved)
        fact_refs = [r for r in refs if role_of(r) == "fact"]
        alias = _s(column.get("alias"))
        label = _s(column.get("label"))
        source = _metric_source(column, fact_refs, role_of, catalog, fact_id)
        for ref in fact_refs:
            mark(ref, "select")
        metrics.append({
            "kind": "derived_metric"
                    if column.get("expression_language") == "derived"
                    else "base_metric",
            "index": column.get("index"), "alias": alias, "label": label,
            "auto_count": column.get("aggregation") in ("count", "count_distinct"),
            "code": _ident(alias or ("%s_%s" % (
                column.get("aggregation") or "metric", column.get("index") or 0))),
            "aggregation": ("count_distinct"
                            if column.get("aggregation") == "count"
                            and column.get("distinct")
                            else column.get("aggregation")),
            "expression": (_norm_expr(column.get("expression"),
                                      [t["alias"] for t in tables if t["alias"]])
                           if column.get("expression_language") == "derived" else ""),
            "filter": _norm_filter(column.get("filter")),
            "source": source, "refs": [list(r) for r in refs],
        })

    fields = _collect_fields(parsed, role_of, resolve, mark, usage)
    for item in fields:
        link = _direct_link(catalog, item["column"])
        if link:
            links.append(link)

    mapped_dim_cols = {
        (_s(r.get("dimension_id")), _s(r.get("source_column")))
        for r in catalog["dimension_columns"]}
    for column in parsed.get("columns") or []:
        for ref in column.get("refs") or []:
            resolved = resolve(ref)
            if role_of(resolved) != "dimension":
                continue
            dimension_id = catalog["dimension_table_map"].get(
                (_s(resolved[0]), _s(resolved[1])), "")
            if dimension_id and (dimension_id, _s(resolved[2])) not in mapped_dim_cols:
                warnings.append({"kind": "dimension_column_unmapped",
                                 "dimension_id": dimension_id,
                                 "column": resolved[2]})

    for table in tables:
        if table["role"] == "unknown":
            warnings.append({"kind": "unknown_table", "table": table["table_key"]})

    return {
        "parsed": matched, "tables": tables, "metrics": metrics, "fields": fields,
        "dimension_links": _dedupe_links(links),
        "unknown": [c for c in parsed.get("columns") or []
                    if not c.get("aggregation") and not (c.get("refs") or [])],
        "warnings": _dedupe_warnings(warnings),
        "fact_tables": [list(t["table_key"]) for t in tables
                        if t["role"] == "fact"],
        "dimension_tables": [list(t["table_key"]) for t in tables
                             if t["role"] == "dimension"],
    }




# ---------------------------------------------------------------------------
# 目标事实解析 + 现有语义匹配
# ---------------------------------------------------------------------------
def _resolve_target(analysis: dict, catalog: dict, scenario_facts: list,
                    fact_id: str) -> tuple[dict, str]:
    if fact_id:
        fact = catalog["facts_by_id"].get(fact_id)
        return fact or {}, ("explicit" if fact else "explicit_missing")
    if len(scenario_facts) == 1:
        fid = _s(scenario_facts[0].get("fact_id"))
        fact = catalog["facts_by_id"].get(fid)
        if fact:
            return fact, "scenario_fact"
    sql_keys = {tuple(k) for k in analysis.get("fact_tables") or []}
    matches = []
    for fact in catalog["facts"]:
        keys = {(_s(t.get("source_schema")), _s(t.get("source_table")))
                for t in fact.get("tables") or []}
        primary = {(_s(t.get("source_schema")), _s(t.get("source_table")))
                   for t in fact.get("tables") or []
                   if t.get("is_primary") in (True, "t", "true")}
        if not sql_keys or not sql_keys <= keys:
            continue
        if primary and not (primary & sql_keys):
            continue
        matches.append(fact)
    if len(matches) == 1:
        return matches[0], "fact_table"
    return {}, ("ambiguous" if matches else "create")


def _score_base_metric(cand: dict, metric: dict, maps: list) -> float:
    text = " ".join(_s(metric.get(k)) for k in ("code", "name", "description"))
    alias = cand.get("label") or cand.get("alias") or cand.get("code")
    score = 0.55 + 0.35 * _sim(alias, text)
    source = cand.get("source") or {}
    physical = any(
        _s(m.get("source_schema")) == _s(source.get("schema"))
        and _s(m.get("source_table")) == _s(source.get("table"))
        and _s(m.get("source_column")) == _s(source.get("column"))
        for m in maps)
    if physical and _s(metric.get("aggregation")) == _s(cand.get("aggregation")):
        score = 0.99
    if physical:
        score = max(score, 0.8)
    if _s(metric.get("aggregation")) == _s(cand.get("aggregation")):
        if score < 0.99:
            score += 0.1
    elif "count_distinct" in (_s(metric.get("aggregation")),
                              _s(cand.get("aggregation"))):
        score -= 0.5
    else:
        score -= 0.25
    filters = [_s(metric.get("filter_expression"))]
    filters += [_s(m.get("expression")) for m in maps]
    filters = [f for f in filters if f]
    cand_filter = _s(cand.get("filter"))
    if cand_filter:
        score += 0.2 if any(_norm_filter(f) == cand_filter for f in filters) else -0.25
    elif filters:
        score -= 0.2
    else:
        score += 0.15
    return min(1.0, max(0.0, score))


def _score_derived_metric(cand: dict, metric: dict) -> float:
    text = " ".join(_s(metric.get(k)) for k in ("code", "name", "description"))
    alias = cand.get("label") or cand.get("alias") or cand.get("code")
    if _norm_expr(metric.get("expression")) == _s(cand.get("expression")):
        return 0.99
    return min(1.0, max(0.55 + 0.35 * _sim(alias, text),
                       0.55 + 0.4 * _sim(cand.get("expression"),
                                         metric.get("expression"))))



def _match_metric(cand: dict, fact_catalog: dict) -> tuple[dict, float]:
    best, best_score = {}, 0.0
    if cand.get("kind") == "derived_metric":
        for metric in fact_catalog["derived_metrics"]:
            score = _score_derived_metric(cand, metric)
            if score > best_score:
                best, best_score = metric, score
        return best, best_score
    for metric in fact_catalog["base_metrics"]:
        maps = fact_catalog["by_semantic"].get(
            ("base_metric", _s(metric.get("metric_id"))), [])
        score = _score_base_metric(cand, metric, maps)
        if score > best_score:
            best, best_score = metric, score
    return best, best_score


def _match_field(cand: dict, fact_catalog: dict,
                 catalog: dict) -> tuple[dict, str, float]:
    for field in fact_catalog["fact_fields"]:
        maps = fact_catalog["by_semantic"].get(
            ("non_metric_field", _s(field.get("field_id"))), [])
        if any(_s(m.get("source_schema")) == _s(cand["table"][0])
               and _s(m.get("source_table")) == _s(cand["table"][1])
               and _s(m.get("source_column")) == _s(cand.get("column"))
               for m in maps):
            return field, "field", 0.99
        if _norm(field.get("code")) == _norm(cand.get("column")):
            return field, "field", 0.95
    for fd in fact_catalog["fact_dimensions"]:
        dim = catalog["dimensions_by_id"].get(_s(fd.get("dimension_id"))) or {}
        if _s(fd.get("join_type")) != "none":
            continue
        if _norm(fd.get("fact_key") or dim.get("member_key")) == \
                _norm(cand.get("column")):
            return {"dimension_id": fd.get("dimension_id")}, "dimension", 0.9
    return {}, "", 0.0


def _match_dimension_link(link: dict, fact_catalog: dict) -> bool:
    for fd in fact_catalog["fact_dimensions"]:
        if _s(fd.get("dimension_id")) != _s(link.get("dimension_id")):
            continue
        if _s(fd.get("fact_key")) == _s(link.get("fact_key")) \
                and _s(fd.get("dimension_key")) == _s(link.get("dimension_key")):
            return True
    return False


def _grain_keys(value):
    return [x.strip().lower() for x in re.split(r"[+,]", _s(value)) if x.strip()]

def _fact_grain_keys(catalog, fact):
    tables = (fact or {}).get("tables") or []
    primary = None
    for table in tables:
        if table.get("is_primary") in (True, "t", "true"):
            primary = table
            break
    if primary is None and tables:
        primary = tables[0]
    keys = [_s(x).strip().lower() for x in (primary or {}).get("grain_keys") or [] if _s(x).strip()]
    return keys or _grain_keys((fact or {}).get("grain"))

def _scenario_grain_keys(analysis):
    keys = []
    for group in (analysis.get("parsed") or {}).get("group_by") or []:
        for ref in group.get("refs") or []:
            col = _s(ref.get("column")).strip().lower()
            if col and col not in keys:
                keys.append(col)
    return keys

def _declared_grain(sql):
    match = re.search(r"(?im)^\s*--\s*grain\s*:\s*(.+)$", _s(sql))
    if not match:
        return []
    return [x.strip().lower() for x in re.split(r"[,+]", match.group(1)) if x.strip()]


def _grain_relation(scenario_keys, fact_keys):
    s, f = set(scenario_keys), set(fact_keys)
    if s == f:
        return "exact"
    if s and s < f:
        return "fact_contains"
    if f and f < s:
        return "scenario_contains"
    if s & f:
        return "partial"
    return "none"
def _fact_identity(scenario_id: str, analysis: dict, answers: dict) -> dict:
    """从 SQL 的主事实表推导 code/id；中文名与粒度来自用户回答或分组列。"""
    primary = {}
    for table in analysis["parsed"].get("tables") or []:
        if table.get("role") != "fact":
            continue
        if not primary or table.get("keyword") == "from":
            primary = table
    table = _s(primary.get("table"))
    code = re.sub(r"_(fact|facts|view)$", "", table, flags=re.I) or table
    code = _ident(code, "fact")
    fact_answers = (answers or {}).get("fact") or {}
    grain_cols = []
    for group in analysis["parsed"].get("group_by") or []:
        for ref in group.get("refs") or []:
            column = _s(ref.get("column"))
            if column and column not in grain_cols:
                grain_cols.append(column)
    grain = _s(fact_answers.get("grain")) or " + ".join(grain_cols) or table
    code = _s(fact_answers.get("code")) or code
    fact_id = _s(fact_answers.get("fact_id")) or ("fact_" + code)
    return {
        "fact_id": fact_id,
        "code": code,
        "name": _s(fact_answers.get("name")),
        "grain": grain,
        "description": _s(fact_answers.get("description")),
        "primary_table": [primary.get("schema"), primary.get("table")]
        if primary else [],
    }




# ---------------------------------------------------------------------------
# 计划：差异分析
# ---------------------------------------------------------------------------
def _match_existing(analysis: dict, catalog: dict, fact_catalog: dict) -> dict:
    """把 SQL 候选与现有语义层比对；返回 covered / missing / 维度链接。"""
    covered = {"base_metrics": [], "derived_metrics": [], "fact_fields": [],
               "dimensions": []}
    missing = {"base_metrics": [], "derived_metrics": [], "fact_fields": [],
               "dimensions": []}
    threshold = 0.80
    for cand in analysis.get("metrics") or []:
        metric, score = _match_metric(cand, fact_catalog)
        item = dict(cand, score=round(score, 3))
        bucket = ("derived_metrics" if cand.get("kind") == "derived_metric"
                  else "base_metrics")
        if metric and score >= threshold:
            covered[bucket].append({
                "id": metric.get("metric_id"), "code": metric.get("code"),
                "name": metric.get("name"), "score": round(score, 3),
                "input": cand.get("label") or cand.get("alias") or cand.get("code"),
            })
        else:
            missing[bucket].append(item)
    for cand in analysis.get("fields") or []:
        field, matched_as, score = _match_field(cand, fact_catalog, catalog)
        direct = _direct_link(catalog, _s(cand.get("column")))
        if field:
            covered["fact_fields"].append({
                "id": field.get("field_id"), "code": field.get("code"),
                "name": field.get("name"), "matched_as": matched_as,
                "score": round(score, 3), "input": cand.get("column"),
            })
            continue
        if direct:
            if direct not in analysis["dimension_links"]:
                analysis["dimension_links"].append(direct)
            covered["fact_fields"].append({
                "id": direct["dimension_id"], "code": cand.get("column"),
                "name": cand.get("column"), "matched_as": "dimension",
                "score": 0.9, "input": cand.get("column"),
            })
            # 直接维度列仍保留事实字段（如果有），继续走 missing
        missing["fact_fields"].append(dict(cand, score=round(score, 3)))
    for link in analysis.get("dimension_links") or []:
        if _match_dimension_link(link, fact_catalog):
            dim = catalog["dimensions_by_id"].get(link["dimension_id"]) or {}
            covered["dimensions"].append({
                "dimension_id": link["dimension_id"],
                "dimension_name": dim.get("name"), "fact_key": link["fact_key"],
                "dimension_key": link["dimension_key"]})
        else:
            missing["dimensions"].append(link)
    return {"covered": covered, "missing": missing}


def _label(answers: dict, *keys) -> str:
    labels = (answers or {}).get("labels") or {}
    for key in keys:
        if key and labels.get(key):
            return _s(labels[key])
    return ""


def _enum_answer(answers: dict, *keys) -> dict:
    enums = (answers or {}).get("enums") or {}
    for key in keys:
        value = enums.get(key)
        if isinstance(value, dict) and value:
            return value
    return {}


def _field_answer(answers: dict, column: str) -> dict:
    return ((answers or {}).get("fields") or {}).get(column) or {}


def _metric_answer(answers: dict, code: str) -> dict:
    return ((answers or {}).get("metrics") or {}).get(code) or {}


def _existing_id(table: str, fact_id: str, code: str, id_col: str) -> str:
    row = _one("SELECT %s AS id FROM %s WHERE fact_id=%s AND code=%s LIMIT 1"
               % (id_col, table, _q(fact_id), _q(code)))
    return _s(row.get("id"))


def _existing_mapping_id(binding_id: str, semantic_kind: str,
                         semantic_id: str) -> str:
    row = _one(
        "SELECT mapping_id AS id FROM analytics.field_mapping "
        " WHERE binding_id=%s AND semantic_kind=%s AND semantic_id=%s LIMIT 1"
        % (_q(binding_id), _q(semantic_kind), _q(semantic_id)))
    return _s(row.get("id"))


def _binding_id_exists(binding_id: str) -> bool:
    return bool(_one("SELECT binding_id AS id FROM analytics.binding "
                     "WHERE binding_id=%s" % _q(binding_id)))


def _table_grain(catalog: dict, key: tuple) -> list:
    for table in catalog["fact_tables"]:
        if (_s(table.get("source_schema")),
                _s(table.get("source_table"))) == tuple(key):
            return table.get("grain_keys") or []
    return []


def _fixed_source(cand: dict, fact_tables: list, primary_key: list,
                  catalog: dict) -> dict:
    source = dict(cand.get("source") or {})
    key = (_s(source.get("schema")), _s(source.get("table")))
    keys = [tuple(k) for k in fact_tables]
    if key not in keys:
        key = tuple(primary_key) if primary_key else (keys[0] if keys else ("", ""))
        source = {"schema": key[0], "table": key[1],
                  "column": _s(source.get("column"))}
    column = _s(source.get("column"))
    if column in ("", "*"):
        grain = _table_grain(catalog, key)
        column = _s(grain[-1]) if grain else "*"
    return {"schema": key[0], "table": key[1], "column": column}


def _enum_hint(text: str) -> dict:
    """从列别名里读一次性的布尔枚举语义，如 `执行结果（true=成功，false=失败）`。"""
    found: dict[str, str] = {}
    for match in re.finditer(
            r"\b(true|false)\s*[=:：]\s*([^,，;；、/|()（）]+)", _s(text), re.I):
        value = match.group(2).strip(" \"'`")
        if value:
            found[match.group(1).lower()] = value
    return found if len(found) == 2 else {}


def _strip_enum_hint(text: str) -> str:
    """去掉列别名里的枚举说明括号，保留可读名称。"""
    cleaned = re.sub(r"[（(][^（）()]*\b(?:true|false)\b[^（）()]*[）)]", "",
                     _s(text), flags=re.I).strip()
    return cleaned or _s(text)


def _label_question(kind: str, code: str, alias: str) -> dict:
    return {"kind": "label", "semantic_kind": kind, "code": code,
            "target": "%s:%s" % (kind, code),
            "prompt": "请补充「%s」的中文含义" % (alias or code),
            "blocking": True}


def _fact_actions(scenario: dict, catalog: dict, analysis: dict, target: dict,
                  target_source: str, fact_catalog: dict, answers: dict) -> dict:
    """事实 / 事实表 / binding 的动作与问题（指标/字段在 `_metric_actions`）。"""
    actions: list[dict] = []
    questions: list[dict] = []
    blocking: list[dict] = []
    identity = _fact_identity(_s(scenario.get("scenario_id")), analysis, answers)
    if target:
        identity.update({
            "fact_id": _s(target.get("fact_id")), "code": _s(target.get("code")),
            "name": _s(target.get("name")), "grain": _s(target.get("grain")),
            "description": _s(target.get("description")),
        })
    declared = analysis.get("declared_grain") or []
    scenario_grain = declared or _scenario_grain_keys(analysis)
    fact_grain = _fact_grain_keys(catalog, target) if target else []
    if target and not declared:
        known_fields = {_s(f.get("code")).lower() for f in fact_catalog.get("fact_fields") or []}
        known_fields |= {_s(m.get("source_column")).lower() for m in fact_catalog.get("mappings") or []}
        scenario_grain = [x for x in scenario_grain
                          if x in fact_grain or x not in known_fields]
    if target and not declared:
        grain_relation = "exact" if set(scenario_grain) == set(fact_grain) else ("fact_contains" if set(scenario_grain) <= set(fact_grain) else "unknown")
        grain_ok = True
    else:
        grain_relation = _grain_relation(scenario_grain, fact_grain) if target else "new"
        grain_ok = (not target) or grain_relation in ("exact", "fact_contains")
    grain_payload = {"scenario": scenario_grain, "fact": fact_grain,
                     "relation": grain_relation, "compatible": grain_ok}
    fact_answers = (answers or {}).get("fact") or {}
    if target and not grain_ok:
        blocking.append({"kind": "scenario_grain_mismatch", "scenario_grain": scenario_grain,
                         "fact_grain": fact_grain, "relation": grain_relation})
        questions.append({"kind": "fact_creation_required", "target": "fact:create",
                          "proposed_grain": scenario_grain, "blocking": True,
                          "prompt": "场景粒度与事实粒度不兼容；是否新建事实对象？"})
    if not target and not (fact_answers.get("create") or fact_answers.get("name")):
        questions.append({"kind": "fact_creation", "target": "fact:create",
                          "proposed_grain": scenario_grain, "blocking": True,
                          "prompt": "没有匹配的事实对象；是否按场景粒度新建？"})
    if target_source == "explicit_missing":
        blocking.append({"kind": "fact_not_found", "fact_id": identity["fact_id"]})
    if target_source == "ambiguous":
        blocking.append({"kind": "fact_ambiguous",
                         "hint": "多个事实匹配 SQL；请让用户选择或传 fact_id"})
        questions.append({
            "kind": "fact_selection", "target": "fact:selection",
            "candidates": [{"fact_id": f.get("fact_id"), "name": f.get("name"),
                            "tables": [[t.get("source_schema"), t.get("source_table")]
                                       for t in f.get("tables") or []]}
                           for f in catalog["facts"]],
            "blocking": True,
            "prompt": "SQL 可匹配多个事实对象，请选择目标事实",
        })
    fact_id, fact_code = identity["fact_id"], identity["code"]
    fact_tables = [tuple(k) for k in analysis.get("fact_tables") or []]
    if not target and not fact_tables:
        blocking.append({"kind": "no_fact_table",
                         "hint": "SQL 中没有可识别的事实表；请在 answers.tables 指定"})

    existing_keys = {(_s(t.get("source_schema")), _s(t.get("source_table")))
                     for t in (target or {}).get("tables") or []}
    primary_key = identity.get("primary_table") or []
    if tuple(primary_key) not in fact_tables:
        primary_key = list(fact_tables[0]) if fact_tables else []
    plan_fact = {
        "fact_id": fact_id, "code": fact_code,
        "name": identity["name"], "grain": identity["grain"],
        "description": identity["description"], "create": not bool(target),
        "fact_tables": [], "binding": {}, "base_metrics": [],
        "derived_metrics": [], "fact_fields": [], "field_mappings": [],
        "enum_mappings": [], "fact_dimensions": [], "scenario_fact": {},
    }

    if not target:
        if not _has_cjk(identity["name"]):
            questions.append({
                "kind": "fact_label", "target": "fact:name", "code": fact_code,
                "prompt": "请为新事实对象提供中文名称", "blocking": True})
        if _one("SELECT fact_id AS id FROM analytics.fact WHERE fact_id=%s"
                % _q(fact_id)):
            blocking.append({"kind": "fact_id_exists", "fact_id": fact_id})
        plan_fact["fact"] = {
            "fact_id": fact_id, "code": fact_code, "name": identity["name"],
            "description": identity["description"], "grain": identity["grain"],
        }
        actions.append({"kind": "fact_create", "id": fact_id,
                        "data": plan_fact["fact"]})
    else:
        patch = {}
        fact_answers = (answers or {}).get("fact") or {}
        update_existing = _s(fact_answers.get("update")).strip().lower() in (
            "1", "true", "yes", "on")
        if update_existing:
            for key in ("name", "description", "grain"):
                value = _s(fact_answers.get(key))
                if value and value != _s(target.get(key)):
                    patch[key] = value
        if patch:
            plan_fact["fact"] = dict({"fact_id": fact_id}, **patch)
            actions.append({"kind": "fact_update", "id": fact_id, "data": patch})

    has_primary = any(t.get("is_primary") in (True, "t", "true")
                      for t in (target or {}).get("tables") or [])
    for key in fact_tables:
        if key in existing_keys:
            continue
        table_id = _ident(key[1], "table")
        existing_table_ids = {_s(t.get("table_id")) for t in
                              (target or {}).get("tables") or []}
        if table_id in existing_table_ids:
            table_id = "%s_%s" % (table_id, len(existing_table_ids) + 1)
        grain = ([] if not target else _table_grain(catalog, key)) or [
            _s(x) for x in identity["grain"].split("+") if _s(x).strip()]
        item = {
            "fact_id": fact_id, "table_id": table_id,
            "source_schema": key[0], "source_table": key[1],
            "is_primary": bool(not has_primary and primary_key
                               and tuple(primary_key) == key),
            "grain_keys": [x.strip() for x in grain if x.strip()],
            "sort_order": 10 + len(plan_fact["fact_tables"]) * 10,
        }
        plan_fact["fact_tables"].append(item)
        actions.append({"kind": "fact_table", "id": "%s::%s" % (fact_id, table_id),
                        "data": item})

    service_id = _s(scenario.get("service_id"))
    binding = _active_binding(fact_id, service_id) if target else {}
    if service_id and not binding:
        binding_id = "bind_%s" % fact_id
        if _binding_id_exists(binding_id):
            binding_id = "bind_%s_%s" % (fact_id, _ident(service_id))
        plan_fact["binding"] = {
            "binding_id": binding_id, "object_type": "fact",
            "object_id": fact_id, "service_id": service_id,
            "binding_type": "self_service", "tool_name": "", "status": "active",
        }
        actions.append({"kind": "binding", "id": binding_id,
                        "data": plan_fact["binding"]})
    else:
        plan_fact["binding"] = {"binding_id": _s(binding.get("binding_id")),
                                "reuse": True}
    if not plan_fact["binding"].get("binding_id"):
        blocking.append({"kind": "binding_missing", "service_id": service_id})
    return {"actions": actions, "questions": questions, "blocking": blocking,
            "plan": dict(plan_fact, grain_ok=grain_ok), "grain": grain_payload}



def _code_id_map(table, fact_id, id_col):
    rows = _rows("SELECT code, %s AS id FROM %s WHERE fact_id=%s"
                 % (id_col, table, _q(fact_id)))
    return {_s(r.get("code")): _s(r.get("id")) for r in rows}


def _mapping_id_map(binding_id):
    if not binding_id:
        return {}
    rows = _rows("SELECT mapping_id, semantic_kind, semantic_id "
                 "FROM analytics.field_mapping WHERE binding_id=%s" % _q(binding_id))
    return {(_s(r.get("semantic_kind")), _s(r.get("semantic_id"))): _s(r.get("mapping_id"))
            for r in rows}


def _metric_actions(scenario: dict, catalog: dict, analysis: dict, target: dict,
                    plan_fact: dict, fact_catalog: dict, answers: dict,
                    matched: dict) -> dict:
    """缺失指标 / 字段 / 映射 / 枚举 / 维度 / 场景关联 的动作与问题。"""
    actions: list[dict] = []
    questions: list[dict] = []
    blocking: list[dict] = []
    fact_id, fact_code = plan_fact["fact_id"], plan_fact["code"]
    fact_tables = [tuple(k) for k in analysis.get("fact_tables") or []]
    all_tables = list(dict.fromkeys(fact_tables + [
        (_s(t.get("source_schema")), _s(t.get("source_table")))
        for t in (target or {}).get("tables") or []]))
    primary_key = list(fact_tables[0]) if fact_tables else []
    for table in (target or {}).get("tables") or []:
        if table.get("is_primary") in (True, "t", "true"):
            primary_key = [_s(table.get("source_schema")),
                           _s(table.get("source_table"))]
    binding_id = _s(plan_fact.get("binding", {}).get("binding_id"))
    base_ids = _code_id_map("analytics.base_metric", fact_id, "metric_id") if target else {}
    derived_ids = _code_id_map("analytics.derived_metric", fact_id, "metric_id") if target else {}
    field_ids = _code_id_map("analytics.fact_field", fact_id, "field_id") if target else {}
    mapping_ids = _mapping_id_map(binding_id)

    for index, cand in enumerate(matched["missing"]["base_metrics"]):
        code = _s(cand.get("code"))
        alias = _s(cand.get("alias"))
        label = _s(cand.get("label"))
        raw_code = _ident(code or alias or label)
        answer = _metric_answer(answers, raw_code)
        code = _ident(_s(answer.get("code")) or raw_code)
        name = _label(answers, code, alias, label) or label or alias or code
        if not _has_cjk(name) and not cand.get("auto_count"):
            questions.append(_label_question("base_metric", code,
                                             label or alias or code))
        metric_id = base_ids.get(code, "") or "bm_%s_%s" % (fact_code, code)
        aggregation = _s(cand.get("aggregation")) or "count"
        source = _fixed_source(cand, all_tables, primary_key, catalog)
        item = {
            "metric_id": metric_id, "fact_id": fact_id, "code": code,
            "name": name,
            "description": _s(answer.get("description")),
            "data_type": _s(answer.get("data_type"))
            or ("integer" if aggregation in ("count", "count_distinct")
                else "numeric"),
            "unit": _s(answer.get("unit")), "aggregation": aggregation,
            "additivity": "non_additive" if aggregation in ("avg", "min", "max")
            else "additive",
            "filter_expression": _s(cand.get("filter")), "format": "",
            "sort_order": 100 + index * 10, "status": "active",
        }
        plan_fact["base_metrics"].append(item)
        actions.append({"kind": "base_metric", "id": metric_id, "data": item})
        mapping = {
            "mapping_id": mapping_ids.get(("base_metric", metric_id), "")
            or "map_%s_metric_%s" % (fact_code, code),
            "binding_id": binding_id, "semantic_kind": "base_metric",
            "semantic_id": metric_id, "source_type": "mcp_table",
            "source_schema": source["schema"], "source_table": source["table"],
            "source_column": source["column"], "data_type": item["data_type"],
            "aggregation": aggregation, "expression": item["filter_expression"],
            "sort_order": item["sort_order"], "note": name, "status": "active",
        }
        plan_fact["field_mappings"].append(mapping)
        actions.append({"kind": "field_mapping", "id": mapping["mapping_id"],
                        "data": mapping})

    for index, cand in enumerate(matched["missing"]["derived_metrics"]):
        code = _ident(_s(cand.get("code")) or _s(cand.get("alias")))
        alias = _s(cand.get("alias"))
        label = _s(cand.get("label"))
        answer = _metric_answer(answers, code)
        name = _label(answers, code, alias, label) or label or alias or code
        if not _has_cjk(name):
            questions.append(_label_question("derived_metric", code,
                                             label or alias or code))
        metric_id = derived_ids.get(code, "") or "dm_%s_%s" % (fact_code, code)
        expression = _s(cand.get("expression"))
        dependencies = [
            _s(m.get("metric_id"))
            for m in plan_fact["base_metrics"]
            if _norm(m.get("code")) and re.search(
                r"(?<![a-z0-9_])%s(?![a-z0-9_])" % re.escape(_norm(m.get("code"))),
                expression)]
        item = {
            "metric_id": metric_id, "fact_id": fact_id, "code": code,
            "name": name,
            "description": _s(answer.get("description")),
            "expression_language": "pseudo", "expression": expression,
            "unit": _s(answer.get("unit")), "format": "",
            "dependencies": dependencies, "sort_order": 100 + index * 10,
            "status": "active",
        }
        plan_fact["derived_metrics"].append(item)
        actions.append({"kind": "derived_metric", "id": metric_id, "data": item})
    for index, cand in enumerate(matched["missing"]["fact_fields"]):
        column = _s(cand.get("column"))
        code = _ident(_s(cand.get("code")) or column, column)
        alias = _s(cand.get("alias")) or column
        label = _s(cand.get("label"))
        answer = _field_answer(answers, column)
        name = _label(answers, code, column) or _strip_enum_hint(label) \
            or alias or code
        if not _has_cjk(name):
            questions.append(_label_question("fact_field", code,
                                             _strip_enum_hint(label) or alias))
        is_bool = bool(cand.get("boolean"))
        enums = (_enum_answer(answers, code, column)
                 or _enum_hint(label) or _enum_hint(alias))
        is_enum = is_bool or bool(enums)
        field_type = _s(answer.get("data_type")) or ("boolean" if is_bool
                                                     else "text")
        role = _s(answer.get("role")) or _s(cand.get("role")) or "display"
        field_id = field_ids.get(code, "") or "ff_%s_%s" % (fact_code, code)
        source = _fixed_source(cand, all_tables, primary_key, catalog)
        item = {
            "field_id": field_id, "fact_id": fact_id, "code": code, "name": name,
            "description": _s(answer.get("description")),
            "data_type": field_type, "role": role, "is_enum": is_enum,
            "sort_order": 100 + index * 10, "status": "active",
        }
        plan_fact["fact_fields"].append(item)
        actions.append({"kind": "fact_field", "id": field_id, "data": item})
        mapping = {
            "mapping_id": mapping_ids.get(("non_metric_field", field_id), "")
            or "map_%s_field_%s" % (fact_code, code),
            "binding_id": binding_id, "semantic_kind": "non_metric_field",
            "semantic_id": field_id, "source_type": "mcp_table",
            "source_schema": source["schema"], "source_table": source["table"],
            "source_column": source["column"], "data_type": field_type,
            "aggregation": "", "expression": "",
            "sort_order": item["sort_order"], "note": name, "status": "active",
        }
        plan_fact["field_mappings"].append(mapping)
        actions.append({"kind": "field_mapping", "id": mapping["mapping_id"],
                        "data": mapping})
        labels = {"true": _s(enums.get("true")),
                  "false": _s(enums.get("false"))}
        if is_bool and not all(labels.values()):
            questions.append({
                "kind": "enum", "semantic_kind": "fact_field", "code": code,
                "target": "fact_field:%s" % code, "values": ["true", "false"],
                "prompt": "请补充「%s」枚举值 true/false 的中文含义"
                          % (_strip_enum_hint(label) or alias or code),
                "blocking": True})
        for physical, label in labels.items():
            if not label:
                continue
            physical_value = physical
            if _s(scenario.get("database_type")).lower() == "postgresql":
                physical_value = "t" if physical == "true" else "f"
            enum = {
                "enum_id": "enum_%s_%s_%s" % (fact_code, code, physical),
                "binding_id": binding_id, "semantic_kind": "non_metric_field",
                "semantic_id": field_id, "semantic_value": physical,
                "semantic_label": label, "source_schema": source["schema"],
                "source_table": source["table"], "source_column": source["column"],
                "physical_value": physical_value, "physical_label": physical_value,
                "status": "active",
            }
            plan_fact["enum_mappings"].append(enum)
            actions.append({"kind": "enum_mapping", "id": enum["enum_id"],
                            "data": enum})

    for index, link in enumerate(matched["missing"]["dimensions"]):
        dim = catalog["dimensions_by_id"].get(_s(link.get("dimension_id"))) or {}
        item = {
            "fact_id": fact_id, "dimension_id": _s(link.get("dimension_id")),
            "key_code": _s(link.get("key_code") or dim.get("member_key")
                           or link.get("fact_key")),
            "role": "group_by", "fact_key": _s(link.get("fact_key")),
            "dimension_key": _s(link.get("dimension_key")),
            "join_type": _s(link.get("join_type")) or "none", "status": "active",
            "sort_order": 100 + index * 10,
        }
        plan_fact["fact_dimensions"].append(item)
        actions.append({"kind": "fact_dimension",
                        "id": "%s::%s" % (fact_id, item["dimension_id"]),
                        "data": item})

    scenario_id = _s(scenario.get("scenario_id"))
    grain_ok = bool(plan_fact.get("grain_ok", True))
    linked = _one(
        "SELECT scenario_id FROM analytics.scenario_fact "
        " WHERE scenario_id=%s AND fact_id=%s" % (_q(scenario_id), _q(fact_id)))
    if grain_ok and not linked:
        item = {"scenario_id": scenario_id, "fact_id": fact_id,
                "sort_order": 10, "note": "从场景 SQL 提取语义层",
                "status": "active"}
        plan_fact["scenario_fact"] = item
        actions.append({"kind": "scenario_fact",
                        "id": "%s::%s" % (scenario_id, fact_id), "data": item})

    return {"actions": actions, "questions": questions, "blocking": blocking}


# ---------------------------------------------------------------------------
# 计划入口
# ---------------------------------------------------------------------------
def _preflight(scenario: dict, scenario_facts: list,
               mapping_service_id: str = "") -> tuple[dict, list]:
    service_id = _s(scenario.get("service_id"))
    checks = {
        "scenario_exists": bool(scenario),
        "scenario_active": _s(scenario.get("status")) == "active",
        "service_present": bool(service_id),
        "service_active": _s(scenario.get("service_status")) == "active",
        "scenario_sql_present": bool(_s(scenario.get("source_sql")).strip()),
        "service_match": not mapping_service_id or mapping_service_id == service_id,
    }
    binding_checks = []
    for fact in scenario_facts:
        binding = _active_binding(_s(fact.get("fact_id")), service_id)
        binding_checks.append({"fact_id": fact.get("fact_id"),
                               "bound": bool(binding),
                               "binding_id": _s(binding.get("binding_id"))})
    checks["fact_bindings"] = (all(x["bound"] for x in binding_checks)
                               if binding_checks else True)
    checks["fact_binding_checks"] = binding_checks
    blocking = []
    if not scenario:
        blocking.append({"kind": "scenario_not_found"})
        return checks, blocking
    if not checks["scenario_active"]:
        blocking.append({"kind": "scenario_inactive",
                         "scenario_id": scenario.get("scenario_id")})
    if not checks["service_present"]:
        blocking.append({"kind": "scenario_service_missing"})
    elif not checks["service_active"]:
        blocking.append({"kind": "scenario_service_inactive",
                         "service_id": service_id})
    if not checks["scenario_sql_present"]:
        blocking.append({"kind": "scenario_sql_missing"})
    if not checks["service_match"]:
        blocking.append({"kind": "service_mismatch",
                         "scenario_service_id": service_id,
                         "mapping_service_id": mapping_service_id})
    for item in binding_checks:
        if not item["bound"]:
            blocking.append({"kind": "scenario_fact_binding_missing",
                             "fact_id": item["fact_id"],
                             "service_id": service_id})
    return checks, blocking


def _plan_core(scenario_id: str, fact_id: str = "", answers: dict | None = None,
               mapping_service_id: str = "") -> dict:
    scenario_id = _s(scenario_id).strip()
    answers = answers if isinstance(answers, dict) else {}
    scenario = _load_scenario(scenario_id)
    if not scenario:
        return {"ok": False, "error": "scenario_not_found",
                "scenario_id": scenario_id,
                "hint": "先调用 scenario_lookup(scenario_id=...) 确认场景存在"}
    scenario_facts = _scenario_facts(scenario_id)
    checks, blocking = _preflight(scenario, scenario_facts, mapping_service_id)
    parsed = ke_semantic_sql.parse_sql(_s(scenario.get("source_sql")))
    catalog = _load_catalog()
    analysis = _analyze(parsed, catalog, fact_id) if parsed.get("ok") else {
        "parsed": parsed, "tables": [], "metrics": [], "fields": [],
        "dimension_links": [], "warnings": [], "fact_tables": [],
        "dimension_tables": [], "unknown": []}
    target, target_source = (None, "")
    if parsed.get("ok"):
        target, target_source = _resolve_target(
            analysis, catalog, scenario_facts, fact_id)
        if target and (answers.get("fact") or {}).get("create"):
            target, target_source = None, "create"
        if target and not fact_id:
            # 重新分析一次：count(*) 之类没有显式列时，来源列按目标事实的主表粒度键补
            analysis = _analyze(parsed, catalog, _s(target.get("fact_id")))
    analysis["declared_grain"] = _declared_grain(_s(scenario.get("source_sql")))
    fact_catalog = _load_fact_catalog(_s(target.get("fact_id"))) if target else {
        "base_metrics": [], "derived_metrics": [], "fact_fields": [],
        "mappings": [], "fact_dimensions": [], "by_semantic": {}}
    matched = _match_existing(analysis, catalog, fact_catalog)
    facts_part = _fact_actions(scenario, catalog, analysis, target,
                               target_source, fact_catalog, answers)
    metrics_part = _metric_actions(scenario, catalog, analysis, target,
                                   facts_part["plan"], fact_catalog, answers,
                                   matched)
    actions = facts_part["actions"] + metrics_part["actions"]
    questions = facts_part["questions"] + metrics_part["questions"]
    blocking = blocking + facts_part["blocking"] + metrics_part["blocking"]
    if not parsed.get("ok"):
        blocking.append({"kind": "sql_unsupported",
                         "unsupported": parsed.get("unsupported")})
    ok = bool(parsed.get("ok")) and not blocking
    ready = ok and not any(q.get("blocking") for q in questions)
    plan_fact = facts_part["plan"]
    payload = {
        "scenario_id": scenario_id, "sql_hash": parsed.get("sql_hash"),
        "fact_id": plan_fact.get("fact_id"), "answers": answers,
        "actions": actions, "questions": questions,
    }
    plan_id = hashlib.sha256(json.dumps(
        payload, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()[:32]
    return {
        "ok": ok, "scenario_id": scenario_id,
        "scenario": {"name": scenario.get("name"),
                     "service_id": scenario.get("service_id"),
                     "service_name": scenario.get("service_name"),
                     "database_type": scenario.get("database_type"),
                     "source_sql": scenario.get("source_sql")},
        "preflight": checks, "blocking": blocking,
        "sql": {"hash": parsed.get("sql_hash"),
                "unsupported": parsed.get("unsupported"),
                "tables": analysis["tables"],
                "joins": parsed.get("joins") or []},
        "target_fact": ({"fact_id": target.get("fact_id"),
                         "code": target.get("code"),
                         "name": target.get("name"),
                         "tables": [[t.get("source_schema"), t.get("source_table")]
                                    for t in target.get("tables") or []]}
                        if target else None),
        "target_source": target_source, "grain": facts_part.get("grain"),
        "coverage": matched["covered"], "missing": matched["missing"],
        "questions": questions, "ready_to_apply": ready,
        "plan": plan_fact, "actions": actions,
        "warnings": analysis.get("warnings") or [],
        "plan_id": plan_id,
        "hint": ("有中文含义/枚举值/事实选择问题；先向用户确认，不要猜。"
                 if questions else
                 ("语义层已完整；可先 dry_run 预览 diff，再 confirm=true apply。"
                  if ready else "前置条件未通过；先修复场景/服务/绑定，不要继续写库。")),
    }


def scenario_semantic_plan(scenario_id: str = "", fact_id: str = "",
                           answers: dict | None = None,
                           mapping_service_id: str = "") -> dict:
    """只读计划：SQL 表/列 → 现有语义层差异 + 确认问题 + 待执行动作。"""
    if not _s(scenario_id).strip():
        return {"ok": False, "error": "invalid_argument",
                "hint": "必须提供 scenario_id"}
    return _plan_core(scenario_id, fact_id, answers, mapping_service_id)


def scenario_semantic_validate(scenario_id: str = "",
                               mapping_service_id: str = "") -> dict:
    """只读前置校验：场景 active、service active、来源事实有 active binding、SQL 可解析。"""
    scenario = _load_scenario(_s(scenario_id).strip())
    if not scenario:
        return {"ok": False, "error": "scenario_not_found",
                "scenario_id": scenario_id}
    scenario_facts = _scenario_facts(_s(scenario_id))
    checks, blocking = _preflight(scenario, scenario_facts, mapping_service_id)
    parsed = ke_semantic_sql.parse_sql(_s(scenario.get("source_sql")))
    catalog = _load_catalog()
    analysis = _analyze(parsed, catalog) if parsed.get("ok") else {"tables": []}
    return {
        "ok": not blocking and bool(parsed.get("ok")),
        "scenario_id": _s(scenario_id), "preflight": checks,
        "blocking": blocking, "sql_ok": bool(parsed.get("ok")),
        "unsupported": parsed.get("unsupported") or [],
        "tables": analysis.get("tables") or [],
        "service": {"service_id": scenario.get("service_id"),
                    "service_name": scenario.get("service_name"),
                    "database_type": scenario.get("database_type"),
                    "status": scenario.get("service_status")},
        "facts": scenario_facts,
        "hint": ("前置校验通过；下一步用绑定 MCP 服务执行场景 SQL，"
                 "再调用 scenario_semantic_plan") if not blocking
        else "前置校验未通过；先修复 blocking 项，不要继续提取语义层",
    }





# ---------------------------------------------------------------------------
# 写入：唯一入口（confirm + plan_id + 事务）
# ---------------------------------------------------------------------------
def _lit(value: Any) -> str:
    return ke_db.sql_str(_s(value))


def _jsonb(value: Any) -> str:
    return _lit(json.dumps(value, ensure_ascii=False)) + "::jsonb"


def _action_sql(action: dict) -> str:
    """把一个动作翻成幂等 upsert / update 语句。"""
    kind = _s(action.get("kind"))
    data = action.get("data") or {}
    if kind == "fact_create":
        return (
            "INSERT INTO analytics.fact "
            "(fact_id, code, name, description, grain, status) VALUES "
            "(%s, %s, %s, %s, %s, 'active') "
            "ON CONFLICT (fact_id) DO UPDATE SET code=EXCLUDED.code, "
            "name=EXCLUDED.name, description=EXCLUDED.description, "
            "grain=EXCLUDED.grain, status=EXCLUDED.status;"
            % (_lit(data.get("fact_id")), _lit(data.get("code")),
               _lit(data.get("name")), _lit(data.get("description")),
               _lit(data.get("grain"))))
    if kind == "fact_update":
        fields = []
        for column in ("name", "description", "grain"):
            if column in data:
                fields.append("%s=%s" % (column, _lit(data.get(column))))
        return ("UPDATE analytics.fact SET %s WHERE fact_id=%s;"
                % (", ".join(fields), _lit(data.get("fact_id"))))
    if kind == "fact_table":
        return (
            "INSERT INTO analytics.fact_table "
            "(fact_id, table_id, source_schema, source_table, is_primary, "
            " grain_keys, sort_order, status) VALUES "
            "(%s, %s, %s, %s, %s, %s, %d, 'active') "
            "ON CONFLICT (fact_id, table_id) DO UPDATE SET "
            "source_schema=EXCLUDED.source_schema, "
            "source_table=EXCLUDED.source_table, "
            "is_primary=EXCLUDED.is_primary, grain_keys=EXCLUDED.grain_keys, "
            "sort_order=EXCLUDED.sort_order, status=EXCLUDED.status;"
            % (_lit(data.get("fact_id")), _lit(data.get("table_id")),
               _lit(data.get("source_schema")), _lit(data.get("source_table")),
               "true" if data.get("is_primary") else "false",
               _jsonb(data.get("grain_keys") or []),
               int(data.get("sort_order") or 0)))
    if kind == "binding":
        return (
            "INSERT INTO analytics.binding "
            "(binding_id, object_type, object_id, service_id, binding_type, "
            " tool_name, status, metadata_hash) VALUES "
            "(%s, 'fact', %s, %s, %s, %s, 'active', '{}'::jsonb) "
            "ON CONFLICT (binding_id) DO UPDATE SET "
            "object_type=EXCLUDED.object_type, object_id=EXCLUDED.object_id, "
            "service_id=EXCLUDED.service_id, binding_type=EXCLUDED.binding_type, "
            "tool_name=EXCLUDED.tool_name, status=EXCLUDED.status;"
            % (_lit(data.get("binding_id")), _lit(data.get("object_id")),
               _lit(data.get("service_id")),
               _lit(data.get("binding_type") or "self_service"),
               _lit(data.get("tool_name"))))
    if kind == "base_metric":
        return (
            "INSERT INTO analytics.base_metric "
            "(metric_id, fact_id, code, name, description, data_type, unit, "
            " aggregation, additivity, filter_expression, format, sort_order, status) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %d, 'active') "
            "ON CONFLICT (metric_id) DO UPDATE SET fact_id=EXCLUDED.fact_id, "
            "code=EXCLUDED.code, name=EXCLUDED.name, "
            "description=EXCLUDED.description, data_type=EXCLUDED.data_type, "
            "unit=EXCLUDED.unit, aggregation=EXCLUDED.aggregation, "
            "additivity=EXCLUDED.additivity, "
            "filter_expression=EXCLUDED.filter_expression, "
            "format=EXCLUDED.format, sort_order=EXCLUDED.sort_order, "
            "status=EXCLUDED.status;"
            % (_lit(data.get("metric_id")), _lit(data.get("fact_id")),
               _lit(data.get("code")), _lit(data.get("name")),
               _lit(data.get("description")), _lit(data.get("data_type")),
               _lit(data.get("unit")), _lit(data.get("aggregation")),
               _lit(data.get("additivity") or "additive"),
               _lit(data.get("filter_expression")), _lit(data.get("format")),
               int(data.get("sort_order") or 0)))
    if kind == "derived_metric":
        return (
            "INSERT INTO analytics.derived_metric "
            "(metric_id, fact_id, code, name, description, expression_language, "
            " expression, unit, format, dependencies, sort_order, status) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %d, 'active') "
            "ON CONFLICT (metric_id) DO UPDATE SET fact_id=EXCLUDED.fact_id, "
            "code=EXCLUDED.code, name=EXCLUDED.name, "
            "description=EXCLUDED.description, "
            "expression_language=EXCLUDED.expression_language, "
            "expression=EXCLUDED.expression, unit=EXCLUDED.unit, "
            "format=EXCLUDED.format, dependencies=EXCLUDED.dependencies, "
            "sort_order=EXCLUDED.sort_order, status=EXCLUDED.status;"
            % (_lit(data.get("metric_id")), _lit(data.get("fact_id")),
               _lit(data.get("code")), _lit(data.get("name")),
               _lit(data.get("description")),
               _lit(data.get("expression_language") or "pseudo"),
               _lit(data.get("expression")), _lit(data.get("unit")),
               _lit(data.get("format")),
               _jsonb(data.get("dependencies") or []),
               int(data.get("sort_order") or 0)))
    if kind == "fact_field":
        return (
            "INSERT INTO analytics.fact_field "
            "(field_id, fact_id, code, name, description, data_type, role, "
            " is_enum, sort_order, status) VALUES "
            "(%s, %s, %s, %s, %s, %s, %s, %s, %d, 'active') "
            "ON CONFLICT (field_id) DO UPDATE SET fact_id=EXCLUDED.fact_id, "
            "code=EXCLUDED.code, name=EXCLUDED.name, "
            "description=EXCLUDED.description, data_type=EXCLUDED.data_type, "
            "role=EXCLUDED.role, is_enum=EXCLUDED.is_enum, "
            "sort_order=EXCLUDED.sort_order, status=EXCLUDED.status;"
            % (_lit(data.get("field_id")), _lit(data.get("fact_id")),
               _lit(data.get("code")), _lit(data.get("name")),
               _lit(data.get("description")), _lit(data.get("data_type")),
               _lit(data.get("role") or "display"),
               "true" if data.get("is_enum") else "false",
               int(data.get("sort_order") or 0)))
    if kind == "field_mapping":
        return (
            "INSERT INTO analytics.field_mapping "
            "(mapping_id, binding_id, semantic_kind, semantic_id, source_type, "
            " source_schema, source_table, source_column, data_type, aggregation, "
            " expression, sort_order, note, status) VALUES "
            "(%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %d, %s, 'active') "
            "ON CONFLICT (binding_id, semantic_kind, semantic_id) DO UPDATE SET "
            "source_type=EXCLUDED.source_type, "
            "source_schema=EXCLUDED.source_schema, "
            "source_table=EXCLUDED.source_table, "
            "source_column=EXCLUDED.source_column, data_type=EXCLUDED.data_type, "
            "aggregation=EXCLUDED.aggregation, expression=EXCLUDED.expression, "
            "sort_order=EXCLUDED.sort_order, note=EXCLUDED.note, "
            "status=EXCLUDED.status;"
            % (_lit(data.get("mapping_id")), _lit(data.get("binding_id")),
               _lit(data.get("semantic_kind")), _lit(data.get("semantic_id")),
               _lit(data.get("source_type") or "mcp_table"),
               _lit(data.get("source_schema")), _lit(data.get("source_table")),
               _lit(data.get("source_column")), _lit(data.get("data_type")),
               _lit(data.get("aggregation")), _lit(data.get("expression")),
               int(data.get("sort_order") or 0), _lit(data.get("note"))))
    if kind == "enum_mapping":
        return (
            "INSERT INTO analytics.enum_mapping "
            "(enum_id, binding_id, semantic_kind, semantic_id, semantic_value, "
            " semantic_label, source_schema, source_table, source_column, "
            " physical_value, physical_label, status) VALUES "
            "(%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'active') "
            "ON CONFLICT (binding_id, semantic_kind, semantic_id, semantic_value) "
            "DO UPDATE SET semantic_label=EXCLUDED.semantic_label, "
            "source_schema=EXCLUDED.source_schema, "
            "source_table=EXCLUDED.source_table, "
            "source_column=EXCLUDED.source_column, "
            "physical_value=EXCLUDED.physical_value, "
            "physical_label=EXCLUDED.physical_label, status=EXCLUDED.status;"
            % (_lit(data.get("enum_id")), _lit(data.get("binding_id")),
               _lit(data.get("semantic_kind")), _lit(data.get("semantic_id")),
               _lit(data.get("semantic_value")),
               _lit(data.get("semantic_label")),
               _lit(data.get("source_schema")), _lit(data.get("source_table")),
               _lit(data.get("source_column")), _lit(data.get("physical_value")),
               _lit(data.get("physical_label"))))
    if kind == "fact_dimension":
        return (
            "INSERT INTO analytics.fact_dimension "
            "(fact_id, dimension_id, key_code, role, fact_key, dimension_key, "
            " join_type, status, sort_order) VALUES "
            "(%s, %s, %s, %s, %s, %s, %s, 'active', %d) "
            "ON CONFLICT (fact_id, dimension_id) DO UPDATE SET "
            "key_code=EXCLUDED.key_code, role=EXCLUDED.role, "
            "fact_key=EXCLUDED.fact_key, dimension_key=EXCLUDED.dimension_key, "
            "join_type=EXCLUDED.join_type, status=EXCLUDED.status, "
            "sort_order=EXCLUDED.sort_order;"
            % (_lit(data.get("fact_id")), _lit(data.get("dimension_id")),
               _lit(data.get("key_code")), _lit(data.get("role") or "group_by"),
               _lit(data.get("fact_key")), _lit(data.get("dimension_key")),
               _lit(data.get("join_type") or "none"),
               int(data.get("sort_order") or 0)))
    if kind == "scenario_fact":
        return (
            "INSERT INTO analytics.scenario_fact "
            "(scenario_id, fact_id, sort_order, note, status) VALUES "
            "(%s, %s, %d, %s, 'active') "
            "ON CONFLICT (scenario_id, fact_id) DO UPDATE SET "
            "sort_order=EXCLUDED.sort_order, note=EXCLUDED.note, "
            "status=EXCLUDED.status;"
            % (_lit(data.get("scenario_id")), _lit(data.get("fact_id")),
               int(data.get("sort_order") or 0), _lit(data.get("note"))))
    return ""


def scenario_semantic_apply(scenario_id: str = "", kb_id: str = "",
                            fact_id: str = "", answers: dict | None = None,
                            plan_id: str = "", confirm: bool = False,
                            dry_run: bool = True,
                            mapping_service_id: str = "",
                            session_no: str = "") -> dict:
    """确认后写入语义层（唯一写入口）。

    必须满足：`confirm=true` + 传入 plan 的 `plan_id` + `dry_run=false`。
    只写 analytics 配置表；写后同步页面/图关系并重新校验。
    """
    if not _write_enabled():
        return {"ok": False, "error": "analytics_write_disabled",
                "hint": "设置 BODHI_ANALYTICS_WRITE=1 后才允许写语义层"}
    if not _s(scenario_id).strip() or not _s(plan_id).strip():
        return {"ok": False, "error": "invalid_argument",
                "hint": "scenario_id 与 plan_id 必填；先调用 scenario_semantic_plan"}
    plan = _plan_core(scenario_id, fact_id, answers, mapping_service_id)
    if not plan.get("ok"):
        return {"ok": False, "error": "plan_blocked",
                "blocking": plan.get("blocking"),
                "questions": plan.get("questions"),
                "plan_id": plan.get("plan_id"),
                "hint": "计划未通过前置校验；先修复 blocking 项"}
    if not plan.get("ready_to_apply"):
        return {"ok": False, "error": "needs_confirmation",
                "questions": plan.get("questions"),
                "plan_id": plan.get("plan_id"),
                "hint": "先用 answers 补齐中文含义/枚举值，再重新 plan"}
    if _s(plan_id) != _s(plan.get("plan_id")):
        return {"ok": False, "error": "plan_stale",
                "expected_plan_id": plan.get("plan_id"),
                "received_plan_id": plan_id,
                "hint": "配置或 answers 已变化；重新 plan 并用新 plan_id apply"}
    if not confirm:
        return {"ok": False, "error": "confirmation_required",
                "plan_id": plan.get("plan_id"),
                "hint": "让用户确认 diff 后传 confirm=true"}
    actions = plan.get("actions") or []
    summary = {}
    for action in actions:
        summary[action.get("kind")] = summary.get(action.get("kind"), 0) + 1
    if not actions:
        return {"ok": True, "applied": False, "dry_run": bool(dry_run),
                "plan_id": plan.get("plan_id"), "summary": {},
                "hint": "语义层已完整，没有需要写入的动作"}
    if dry_run:
        return {"ok": True, "applied": False, "dry_run": True,
                "plan_id": plan.get("plan_id"), "summary": summary,
                "actions": actions,
                "hint": "dry_run 不写库；确认后传 dry_run=false"}
    if not _s(kb_id).strip():
        return {"ok": False, "error": "kb_id_required",
                "hint": "写入后要同步知识页；kb_id 必填"}
    statements = [sql for sql in (_action_sql(a) for a in actions) if sql]
    if len(statements) != len(actions):
        return {"ok": False, "error": "unsupported_action",
                "plan_id": plan.get("plan_id")}
    script = "BEGIN;\n" + "\n".join(statements) + "\nCOMMIT;\n"
    try:
        ke_db.psql(script, stdin=True)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": "apply_failed",
                "plan_id": plan.get("plan_id"), "detail": str(exc)[:500]}
    fact = plan.get("plan") or {}
    target_fact = _s(fact.get("fact_id"))
    service_id = _s((plan.get("scenario") or {}).get("service_id"))
    dimension_ids = [
        _s(d.get("dimension_id")) for d in fact.get("fact_dimensions") or []
        if _s(d.get("dimension_id"))]
    affected = {
        "fact": [target_fact] if target_fact else [],
        "dimension": dimension_ids,
        "scenario": [scenario_id],
        "service": [],
    }
    try:
        pages_raw = ke_analytics.sync_pages_affected(
            kb_id, affected, dry_run=False)
        pages = {"count": pages_raw.get("count"),
                 "written": pages_raw.get("written"),
                 "errors": [r for r in pages_raw.get("results") or []
                            if r.get("ok") is False][:10]}
    except Exception as exc:  # noqa: BLE001
        pages = {"error": str(exc)[:400]}
    try:
        mapping = ke_analytics.mapping_lookup("fact", target_fact, service_id)
        scenario_check = ke_analytics.scenario_lookup(scenario_id=scenario_id)
    except Exception as exc:  # noqa: BLE001
        mapping = {"ok": False, "error": str(exc)[:300]}
        scenario_check = {"ok": False, "error": str(exc)[:300]}
    after = _plan_core(scenario_id, fact_id, answers, mapping_service_id)
    return {
        "ok": True, "applied": True, "dry_run": False,
        "plan_id": plan.get("plan_id"), "summary": summary,
        "fact_id": target_fact,
        "pages": pages,
        "validation": {
            "mapping": {"ok": mapping.get("ok"),
                        "status": mapping.get("status"),
                        "fact_tables": mapping.get("fact_tables"),
                        "mapping_error": mapping.get("mapping_error")},
            "scenario": {"ok": scenario_check.get("ok"),
                         "count": scenario_check.get("count"),
                         "error": scenario_check.get("error")},
            "remaining_actions": len(after.get("actions") or []),
            "converged": not (after.get("actions") or []),
        },
        "hint": ("已写入并同步页面；请复核 fact/场景页与 mapping_lookup"
                 if (after.get("actions") or []) == [] else
                 "已写入，但重新 plan 仍有待补项；检查 answers 是否完整"),
    }

