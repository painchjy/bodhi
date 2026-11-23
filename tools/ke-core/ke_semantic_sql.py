"""受控 SQL 扫描器（零第三方依赖）。

用途
----
把**标准取数场景**里的单条 `SELECT` 拆成结构化事实：
表 / 别名 / JOIN 条件 / 输出列 / 聚合 / FILTER / GROUP BY / WHERE。

边界
----
* 只支持单条 `SELECT`；`UNION` / 多语句 / CTE 遇到就 `unsupported`，**不猜**；
* 不执行 SQL，不连数据库；表角色（事实/维度）由调用方结合 analytics 配置判定。
"""
from __future__ import annotations

import hashlib
import re
from typing import Any

AGG_FUNCS = ("count", "sum", "avg", "min", "max")

_ALIAS_STOP = {
    "where", "on", "group", "order", "limit", "having", "union", "intersect",
    "except", "left", "right", "full", "inner", "cross", "join", "as", "and",
    "or", "not", "null", "asc", "desc", "using", "lateral", "offset", "fetch",
    "window", "qualify", "returning",
}
_CLAUSE_WORDS = ("where", "group", "order", "limit", "having", "union",
                 "intersect", "except", "offset", "fetch")
_IDENT = r"[A-Za-z_][A-Za-z0-9_]*"
_TABLE = r"(?:%s\.)?%s" % (_IDENT, _IDENT)
_CJK = re.compile(r"[\u4e00-\u9fff]")
_QUOTED = "'\"`"


def _mask(sql: str) -> str:
    """把注释与字符串字面量替换成空格，保留字符位置（便于按偏移切原文）。"""
    out: list[str] = []
    i = 0
    text = sql or ""
    while i < len(text):
        ch = text[i]
        if ch == "'":
            j = i + 1
            while j < len(text):
                if text[j] == "'":
                    if j + 1 < len(text) and text[j + 1] == "'":
                        j += 2
                        continue
                    break
                j += 1
            out.append(" " * (min(j + 1, len(text)) - i))
            i = j + 1
            continue
        if text.startswith("--", i):
            j = text.find("\n", i)
            j = len(text) if j < 0 else j
            out.append(" " * (j - i))
            i = j
            continue
        if text.startswith("/*", i):
            j = text.find("*/", i + 2)
            j = len(text) if j < 0 else j + 2
            out.append(" " * (j - i))
            i = j
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def _find_kw(masked: str, word: str, start: int = 0,
             end: int | None = None) -> tuple[int, int]:
    """在括号深度 0 处找关键字，返回 (start, end)；找不到 (-1, -1)。"""
    stop = len(masked) if end is None else min(end, len(masked))
    pattern = re.compile(r"\b%s\b" % re.escape(word), re.I)
    depth = 0
    i = max(0, start)
    while i < stop:
        ch = masked[i]
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth = max(0, depth - 1)
        elif depth == 0:
            match = pattern.match(masked, i)
            if match:
                return match.start(), match.end()
        i += 1
    return -1, -1


def split_top_level(text: str, sep: str = ",") -> list[str]:
    """按顶层分隔符切分（忽略括号内与字符串内的分隔符）。"""
    masked = _mask(text)
    parts: list[str] = []
    depth = 0
    last = 0
    for i, ch in enumerate(masked):
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth = max(0, depth - 1)
        elif depth == 0 and ch == sep:
            parts.append(text[last:i].strip())
            last = i + 1
    tail = text[last:].strip()
    if tail:
        parts.append(tail)
    return [p for p in parts if p]


def _paren_end(text: str, open_index: int) -> int:
    """从 `(` 开始找配对右括号的位置；找不到返回 -1。"""
    depth = 0
    i = open_index
    while i < len(text):
        if text[i] == "(":
            depth += 1
        elif text[i] == ")":
            depth -= 1
            if depth == 0:
                return i
        i += 1
    return -1


def normalize_expression(expr: str, aliases: list[str] | None = None) -> str:
    """去掉表别名、类型转换与多余空白，得到稳定的表达式文本。"""
    text = str(expr or "")
    for alias in sorted({a for a in (aliases or []) if a}, key=len, reverse=True):
        text = re.sub(r"\b%s\s*\." % re.escape(alias), "", text)
    text = re.sub(r"::\s*%s(\s*\([^)]*\))?" % _IDENT, "", text)
    text = re.sub(r"\s+", " ", text).strip().lower()
    text = re.sub(r"\s*([(),+\-*/=<>])\s*", r"\1", text)
    return text


def _extract_alias(item: str) -> tuple[str, str, str]:
    """拆出 `expr AS alias`，返回 `(表达式, 英文别名, 中文名称)`。

    * 英文别名：`AS calls` / `AS "calls"` → 作为语义 code；
    * 中文名称：`AS "调用次数"` / `AS '调用次数'` / `AS 调用次数` → 直接作为 label，
      不再追问中文含义（单引号别名 PostgreSQL 不合法，扫描器仍兼容 MySQL/Trino）；
    * 只认显式 AS（隐式别名容易误判，不猜）。
    """
    match = re.search(r"\s+as\s+(.+?)\s*$", item, re.I | re.S)
    if not match:
        return item.strip(), "", ""
    expr = item[:match.start()].strip()
    token = match.group(1).strip()
    if len(token) >= 2 and token[0] == token[-1] and token[0] in _QUOTED:
        value = token[1:-1]
        if token[0] == "'":
            value = value.replace("''", "'")
        value = value.strip()
        if re.fullmatch(_IDENT, value):
            return expr, value, ""
        return expr, "", value
    if re.fullmatch(_IDENT, token):
        return expr, token, ""
    if _CJK.search(token):
        return expr, "", token.strip(_QUOTED)
    return expr, "", ""


def _extract_aggregate(expr: str) -> dict | None:
    """找第一个聚合函数调用，返回函数名 / 参数 / DISTINCT / 位置。"""
    match = re.search(r"\b(%s)\s*\(" % "|".join(AGG_FUNCS), expr, re.I)
    if not match:
        return None
    open_index = match.end() - 1
    close_index = _paren_end(expr, open_index)
    if close_index < 0:
        return None
    arg = expr[open_index + 1:close_index].strip()
    distinct = bool(re.match(r"distinct\b", arg, re.I))
    if distinct:
        arg = re.sub(r"^distinct\s+", "", arg, flags=re.I).strip()
    return {
        "func": match.group(1).lower(),
        "arg": arg,
        "distinct": distinct,
        "start": match.start(),
        "end": close_index + 1,
    }


def _extract_filter(expr: str) -> str:
    """取 `FILTER (WHERE ...)` 里的条件（括号配对，不按正则硬切）。"""
    match = re.search(r"\bfilter\s*\(", expr, re.I)
    if not match:
        return ""
    close_index = _paren_end(expr, match.end() - 1)
    if close_index < 0:
        return ""
    body = expr[match.end():close_index].strip()
    return re.sub(r"^where\b", "", body, flags=re.I).strip()


def _strip_filter(expr: str) -> str:
    """去掉 `FILTER (...)` 片段（判断聚合外层是否有 cast/round 时用）。"""
    match = re.search(r"\bfilter\s*\(", expr, re.I)
    if not match:
        return expr
    close_index = _paren_end(expr, match.end() - 1)
    if close_index < 0:
        return expr
    return (expr[:match.start()] + expr[close_index + 1:]).strip()


def _parse_tables(from_clause: str) -> list[dict]:
    """解析 FROM/JOIN 后的表与别名，并带上 JOIN 的 ON 条件。"""
    masked = _mask(from_clause)
    pattern = re.compile(
        r"\b(from|(?:(left|right|full|inner|cross)\s+)?join)\b", re.I)
    hits = list(pattern.finditer(masked))
    tables = []
    for index, hit in enumerate(hits):
        seg_end = hits[index + 1].start() if index + 1 < len(hits) else len(from_clause)
        seg = from_clause[hit.end():seg_end]
        seg_masked = masked[hit.end():seg_end]
        match = re.match(r"\s*(%s)(?:\s+(?:as\s+)?(%s))?" % (_TABLE, _IDENT),
                         seg, re.I)
        if not match:
            continue
        qualified = match.group(1)
        alias = match.group(2) or ""
        if alias and alias.lower() in _ALIAS_STOP:
            alias = ""
        schema, table = "", qualified
        if "." in qualified:
            schema, table = qualified.split(".", 1)
        keyword = (hit.group(1) or "").lower()
        join_type = (hit.group(2) or "").lower() or ("from" if keyword == "from" else "inner")
        item = {
            "keyword": keyword, "join_type": join_type,
            "schema": schema, "table": table, "qualified": qualified,
            "alias": alias, "condition": "",
        }
        on_start, on_end = _find_kw(seg_masked, "on")
        if on_start >= 0:
            item["condition"] = seg[on_end:].strip()
        tables.append(item)
    return tables


def _parse_join_condition(condition: str,
                          alias_tables: dict[str, str]) -> list[dict]:
    """把 ON 条件拆成 `左表.列 = 右表.列`（只认等值连接）。"""
    out = []
    for part in split_top_level(condition, ","):
        for clause in re.split(r"\band\b", part, flags=re.I):
            match = re.search(
                r"\b(%s)\s*\.\s*(%s)\s*=\s*(%s)\s*\.\s*(%s)\b"
                % (_IDENT, _IDENT, _IDENT, _IDENT), clause, re.I)
            if not match:
                continue
            left_alias, left_col, right_alias, right_col = match.groups()
            out.append({
                "left_table": alias_tables.get(left_alias.lower(), ""),
                "left_alias": left_alias, "left_column": left_col,
                "right_table": alias_tables.get(right_alias.lower(), ""),
                "right_alias": right_alias, "right_column": right_col,
            })
    return out


def _colrefs(text: str) -> list[dict]:
    """提取 `alias.column` 引用（不带别名的列由调用方按表达式判断）。"""
    refs = []
    seen = set()
    for match in re.finditer(r"\b(%s)\s*\.\s*(%s)\b" % (_IDENT, _IDENT), text or ""):
        key = (match.group(1).lower(), match.group(2).lower())
        if key in seen:
            continue
        seen.add(key)
        refs.append({"alias": match.group(1), "column": match.group(2)})
    return refs


def parse_sql(sql: str) -> dict:
    """解析单条 SELECT；返回结构化扫描结果（不判断事实/维度角色）。"""
    raw = (sql or "").strip().rstrip(";").strip()
    result: dict[str, Any] = {
        "ok": False, "sql_hash": hashlib.sha256(raw.encode("utf-8")).hexdigest(),
        "unsupported": [], "tables": [], "joins": [], "columns": [],
        "group_by": [], "filters": [], "select_sql": "", "from_sql": "",
        "where_sql": "", "group_sql": "", "order_sql": "",
    }
    if not raw:
        result["unsupported"].append("empty_sql")
        return result
    masked = _mask(raw)
    for word in ("union", "intersect", "except"):
        if _find_kw(masked, word)[0] >= 0:
            result["unsupported"].append("unsupported_%s" % word)
    start, select_end = _find_kw(masked, "select")
    if start < 0:
        result["unsupported"].append("missing_select")
        return result
    if re.search(r"\bwith\b", masked[:start], re.I):
        result["unsupported"].append("unsupported_cte")
    from_start, from_end = _find_kw(masked, "from", select_end)
    if from_start < 0:
        result["unsupported"].append("missing_from")
        return result

    boundaries = {}
    for word in _CLAUSE_WORDS:
        pos, end = _find_kw(masked, word, from_end)
        if pos >= 0:
            boundaries[word] = (pos, end)
    ordered = sorted(boundaries.items(), key=lambda item: item[1][0])
    next_pos = ordered[0][1][0] if ordered else len(raw)
    from_clause = raw[from_start:next_pos]
    select_clause = raw[select_end:from_start]

    result["select_sql"] = select_clause.strip()
    result["from_sql"] = raw[from_end:next_pos].strip()
    for word in ("where", "group", "order", "having"):
        if word not in boundaries:
            continue
        pos, end = boundaries[word]
        following = [p for p, _e in boundaries.values() if p > pos]
        clause_end = min(following) if following else len(raw)
        text = raw[end:clause_end].strip()
        if word == "where":
            result["where_sql"] = text
        elif word == "group":
            result["group_sql"] = re.sub(r"^by\b", "", text, flags=re.I).strip()
        elif word == "order":
            result["order_sql"] = re.sub(r"^by\b", "", text, flags=re.I).strip()

    tables = _parse_tables(from_clause)
    result["tables"] = tables
    alias_tables = {
        t["alias"].lower(): "%s%s" % ((t["schema"] + ".") if t["schema"] else "",
                                      t["table"])
        for t in tables if t["alias"]}
    for table in tables:
        for join in _parse_join_condition(table["condition"], alias_tables):
            join["join_type"] = table["join_type"]
            result["joins"].append(join)

    aliases = [t["alias"] for t in tables if t["alias"]]
    columns = []
    for index, item in enumerate(split_top_level(select_clause, ","), start=1):
        expr, alias, label = _extract_alias(item)
        agg = _extract_aggregate(expr)
        filter_text = _extract_filter(expr)
        refs = _colrefs(expr) + _colrefs(filter_text)
        column = {
            "index": index, "alias": alias, "label": label,
            "expression": expr,
            "normalized": normalize_expression(expr, aliases),
            "aggregation": "", "distinct": False, "arg": "",
            "filter": filter_text, "refs": refs, "expression_language": "",
        }
        if agg:
            column["aggregation"] = agg["func"]
            column["distinct"] = agg["distinct"]
            column["arg"] = agg["arg"]
            base_expr = _strip_filter(expr)
            wrapper = (base_expr[:agg["start"]] + base_expr[agg["end"]:]).strip()
            other_aggs = len(re.findall(
                r"\b(?:%s)\s*\(" % "|".join(AGG_FUNCS), expr, re.I))
            plain_arg = bool(re.fullmatch(
                r"(?:%s\.)?%s|\*" % (_IDENT, _IDENT), agg["arg"].strip()))
            if other_aggs > 1 or bool(wrapper) or not plain_arg:
                column["expression_language"] = "derived"
            else:
                column["expression_language"] = "base"
        columns.append(column)
    result["columns"] = columns

    for item in split_top_level(result["group_sql"], ","):
        token = item.strip()
        if token.isdigit():
            ordinal = int(token)
            if 1 <= ordinal <= len(columns):
                source = columns[ordinal - 1]
                result["group_by"].append({
                    "expression": source["expression"],
                    "normalized": source["normalized"],
                    "refs": source["refs"], "ordinal": ordinal,
                })
            continue
        result["group_by"].append({
            "expression": token,
            "normalized": normalize_expression(token, aliases),
            "refs": _colrefs(token), "ordinal": None,
        })

    for clause in re.split(r"\band\b", result["where_sql"], flags=re.I):
        if clause.strip():
            result["filters"].append({
                "expression": clause.strip(),
                "normalized": normalize_expression(clause, aliases),
                "refs": _colrefs(clause),
            })

    if not tables:
        result["unsupported"].append("no_tables")
    if not columns:
        result["unsupported"].append("no_columns")
    result["ok"] = not result["unsupported"]
    return result


def match_tables(parsed: dict, fact_keys: set, dimension_keys: set) -> dict:
    """给每张表标角色：fact / dimension / unknown（调用方给已知物理表集合）。"""
    annotated = []
    for table in parsed.get("tables") or []:
        key = (table.get("schema") or "", table.get("table") or "")
        if key in fact_keys:
            role = "fact"
        elif key in dimension_keys:
            role = "dimension"
        else:
            role = "unknown"
        annotated.append(dict(table, role=role, table_key=[key[0], key[1]]))
    return dict(parsed, tables=annotated)

