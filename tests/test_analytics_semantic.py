#!/usr/bin/env python3
"""标准取数场景 → 语义层 · 纯函数回归（静态，不连库）。

验证：
1. 6 个标准场景 SQL 都能被受控扫描器解析出表/JOIN/聚合/分组；
2. 表角色按 analytics 事实表/维度表集合判定（维表在前也不会误判事实）；
3. 过滤条件归一（NOT x / 裸布尔列 / x = true）能互相匹配；
4. 指标匹配对 DISTINCT、FILTER 差异有惩罚，不会把不同口径当同一指标；
5. UNION / CTE 等不支持的 SQL 明确返回 unsupported，不猜。
"""
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _helpers as H  # noqa: E402

KE_CORE = os.path.join(H.REPO, "tools", "ke-core")
if KE_CORE not in sys.path:
    sys.path.insert(0, KE_CORE)

import ke_semantic  # noqa: E402
import ke_semantic_sql  # noqa: E402


def _scenario_sql() -> dict:
    text = H.read(os.path.join(H.REPO, "sql", "analytics", "005_scenario_diag_seed.sql"))
    text += "\n" + H.read(os.path.join(H.REPO, "sql", "analytics", "012_fact_table_join.sql"))
    out = {}
    for match in re.finditer(
            r"\('(sc_[a-z_]+)',\s*'[^']*',\s*'[^']*',\s*'[^']*',\s*'[^']*',"
            r"\s*\$q\$([\s\S]*?)\$q\$", text):
        out[match.group(1)] = match.group(2).strip()
    return out


def main() -> int:
    sqls = _scenario_sql()
    fact_keys = {("diag", "agent_turn_fact"), ("diag", "agent_tool_call_fact")}
    dim_keys = {("diag", "tool_dimension"), ("diag", "tool_dimension_all"),
                ("diag", "tool_problem_dimension"),
                ("diag", "tool_problem_dimension_all")}
    parsed = {sid: ke_semantic_sql.parse_sql(sql) for sid, sql in sqls.items()}
    roles = {sid: {(t["schema"], t["table"]): t["role"]
                   for t in ke_semantic_sql.match_tables(p, fact_keys, dim_keys)["tables"]}
             for sid, p in parsed.items()}

    def agg(sid):
        return [c["aggregation"] for c in parsed[sid]["columns"] if c["aggregation"]]

    checks = {
        "6 个标准场景": set(sqls) == {
            "sc_tool_usage", "sc_tool_unused", "sc_tool_problem_usage",
            "sc_context_tokens", "sc_tool_return_chars", "sc_tool_context"},
        "全部 SQL 可解析": all(p["ok"] for p in parsed.values()),
        "sc_tool_usage 维表在前仍识别": roles.get("sc_tool_usage", {}).get(
            ("diag", "tool_dimension_all")) == "dimension"
            and roles["sc_tool_usage"].get(("diag", "agent_tool_call_fact")) == "fact",
        "sc_tool_problem_usage 2 层维表": roles.get("sc_tool_problem_usage", {}).get(
            ("diag", "tool_problem_dimension_all")) == "dimension",
        "sc_context_tokens 单事实表": roles.get("sc_context_tokens") == {
            ("diag", "agent_turn_fact"): "fact"},
        "sc_tool_context 两事实表 + JOIN": roles.get("sc_tool_context") == {
            ("diag", "agent_tool_call_fact"): "fact",
            ("diag", "agent_turn_fact"): "fact"}
            and len(parsed["sc_tool_context"]["joins"]) == 1,
        "聚合识别": (agg("sc_context_tokens").count("sum") == 4
                     and agg("sc_context_tokens").count("count") == 1
                     and agg("sc_tool_return_chars").count("count") == 3),
        "DISTINCT 识别": any(c["distinct"] for c in
                             parsed["sc_tool_problem_usage"]["columns"]),
        "FILTER 识别": any(c["filter"].replace("f.", "") == "tool_success"
                           for c in parsed["sc_tool_return_chars"]["columns"]),
        "派生列（avg + cast）": any(c["expression_language"] == "derived"
                                    for c in parsed["sc_tool_return_chars"]["columns"]),
        "GROUP BY 序号解析": [g["normalized"] for g in
                              parsed["sc_tool_problem_usage"]["group_by"]]
                            == ["problem_l1_name", "category_l2_name"],
        "过滤条件归一": (
            ke_semantic._norm_filter("f.tool_success") == "tool_success=true"
            and ke_semantic._norm_filter("NOT f.tool_success") == "tool_success=false"
            and ke_semantic._norm_filter("tool_success = true") == "tool_success=true"),
        "UNION 不支持": (lambda p: (not p["ok"])
                         and "unsupported_union" in p["unsupported"])(
            ke_semantic_sql.parse_sql("SELECT a FROM x UNION SELECT a FROM y")),
        "CTE 不支持": (lambda p: (not p["ok"])
                      and "unsupported_cte" in p["unsupported"])(
            ke_semantic_sql.parse_sql("WITH t AS (SELECT 1 AS a) SELECT a FROM t")),
    }

    # 一次性语义：中文列别名 → label；布尔枚举写在别名里 → 不再追问
    alias_parsed = ke_semantic_sql.parse_sql(
        'SELECT f.tool_success AS "执行结果（true=成功，false=失败）", '
        'count(*) AS "调用次数" FROM diag.agent_tool_call_fact f')
    alias_labels = [c["label"] for c in alias_parsed["columns"]]
    checks["中文列别名识别"] = alias_labels == [
        "执行结果（true=成功，false=失败）", "调用次数"]
    enum_label = alias_labels[0]
    checks["枚举提示解析"] = ke_semantic._enum_hint(enum_label) == {
        "true": "成功", "false": "失败"}
    checks["枚举提示剥离"] = ke_semantic._strip_enum_hint(enum_label) == "执行结果"
    checks["纯中文 code 回退"] = ke_semantic._ident("调用次数", "fallback") == "fallback"

    catalog = {
        "fact_table_keys": {("diag", "agent_tool_call_fact")},
        "dimension_table_keys": set(), "dimension_table_map": {},
        "dimensions": [], "dimension_binding_ids": set(),
        "dimension_columns": [], "facts_by_id": {}, "fact_tables": [],
    }
    analysis = ke_semantic._analyze(alias_parsed, catalog)
    checks["指标/字段带中文名称"] = (
        analysis["metrics"][0]["label"] == "调用次数"
        and analysis["metrics"][0]["code"] == "count_2"
        and analysis["fields"][0]["label"] == enum_label
        and analysis["fields"][0]["code"] == "tool_success")

    # 口径差异：DISTINCT / FILTER 不能被当作同一指标
    metric = {"metric_id": "bm_x", "code": "tool_call_count", "name": "工具调用次数",
              "description": "", "aggregation": "count", "filter_expression": ""}
    cand_plain = {"kind": "base_metric", "alias": "calls", "code": "calls",
                  "aggregation": "count", "filter": "",
                  "source": {"schema": "diag", "table": "agent_tool_call_fact",
                             "column": "message_id"}}
    cand_distinct = dict(cand_plain, aggregation="count_distinct")
    cand_filtered = dict(cand_plain, filter="tool_success=true")
    checks["普通 count 命中"] = ke_semantic._score_base_metric(
        cand_plain, metric, []) >= 0.80
    checks["count_distinct 不命中普通 count"] = ke_semantic._score_base_metric(
        cand_distinct, metric, []) < 0.80
    checks["FILTER 口径不命中无过滤指标"] = ke_semantic._score_base_metric(
        cand_filtered, metric, []) < 0.80
    # 014 三个语义样例：SQL 首行声明粒度；avg(表达式) 为派生指标，不生成新基础指标。
    sample_text = H.read(os.path.join(H.REPO, "sql", "analytics", "014_semantic_test_scenarios.sql"))
    samples = re.findall(r"'-- grain: ([^\n]+)\n(.*?)',\s*'active'", sample_text, re.S)
    sample_parsed = [ke_semantic_sql.parse_sql(sql) for _, sql in samples]
    checks["014 三个样例带 grain 声明"] = [g.strip() for g, _ in samples] == [
        "session_id, message_id",
        "session_id, message_id, tool_call_id",
        "session_id, message_id, agent_id"]
    checks["014 样例均可解析"] = len(sample_parsed) == 3 and all(p["ok"] for p in sample_parsed)
    sample_avg = [c for c in sample_parsed[0]["columns"]
                  if c.get("alias") == "avg_tokens_per_turn"]
    checks["avg(表达式) 归派生"] = bool(sample_avg) and sample_avg[0]["expression_language"] == "derived"
    sample_counts = [c for c in sample_parsed[0]["columns"] if c.get("aggregation") == "count"]
    checks["count 仍是基础指标"] = bool(sample_counts) and sample_counts[0].get("expression_language") in (None, "base")
    checks["粒度关系"] = (
        ke_semantic._grain_relation(["session_id", "message_id"], ["session_id", "message_id"]) == "exact"
        and ke_semantic._grain_relation(["session_id", "message_id"], ["session_id", "message_id", "agent_id"]) == "fact_contains"
        and ke_semantic._grain_relation(["session_id", "message_id", "agent_id"], ["session_id", "message_id"]) == "scenario_contains")

    # 015 两个 golden 场景：semantic_json.answers 自包含，plan 不依赖调用方补答案。
    golden_text = H.read(os.path.join(H.REPO, "sql", "analytics",
                                      "015_golden_semantic_definitions.sql"))
    payloads = [json.loads(raw) for raw in
                re.findall(r"\$json\$(.*?)\$json\$", golden_text, re.S)]
    checks["015 两个 golden 场景 JSON"] = len(payloads) == 2
    checks["golden JSON 结构"] = all(
        p.get("version") == 1 and (p.get("answers") or {}).get("labels")
        and (p.get("definitions") or {}).get("columns") for p in payloads)
    column_counts = sorted(len((p.get("definitions") or {}).get("columns") or [])
                           for p in payloads)
    checks["golden 定义覆盖全部输出列"] = column_counts == [4, 15]
    checks["golden answers 带单位/字段类型"] = all(
        all("unit" in m for m in (p["answers"].get("metrics") or {}).values())
        and all("data_type" in f for f in (p["answers"].get("fields") or {}).values())
        for p in payloads)
    return H.report(checks, "语义层提取（扫描/匹配）")


if __name__ == "__main__":
    sys.exit(main())
