"""自助取数分析 · 最小配置管理端（stdlib HTTP，只读/表格编辑 + 页面同步）。

边界
----
* 仅管理 PostgreSQL `analytics` schema 的 17 张配置表（白名单）；
* 不暴露给智能体；默认只监听 127.0.0.1；
* 映射/枚举/join/filter 在这里可视化编辑，不生成 wiki 页；
* 「同步页面」调用 `ke_analytics.sync_pages`，只投影 Fact / Dimension / Scenario；
* `/api/mapping-interface` 只读 `v_mapping_interface` 并校验页面/属性/图边。

启动
----
    python tools/analytics-admin/server.py --host 127.0.0.1 --port 8781
    浏览器打开 http://127.0.0.1:8781/

环境（可选）
------------
    BODHI_ANALYTICS_ADMIN_HOST / BODHI_ANALYTICS_ADMIN_PORT
    BODHI_ANALYTICS_ADMIN_TOKEN  设置后请求头 X-Admin-Token 或 ?token= 必须一致
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib import parse as urlparse

HERE = os.path.dirname(os.path.abspath(__file__))
CORE = os.path.join(os.path.dirname(HERE), "ke-core")
if CORE not in sys.path:
    sys.path.insert(0, CORE)

import ke_analytics  # noqa: E402
import ke_db  # noqa: E402

TABLES = (
    "dimension", "dimension_level", "dimension_attribute", "fact", "fact_dimension",
    "fact_table", "base_metric", "derived_metric", "fact_field",
    "mcp_service", "binding", "field_mapping", "enum_mapping",
    "scenario", "scenario_join", "scenario_filter", "scenario_fact",
)
_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
TOKEN = ke_db.env_value("BODHI_ANALYTICS_ADMIN_TOKEN", "").strip()


def _ident(name: str) -> str:
    """只允许白名单表名/合法列名，避免拼 SQL 注入。"""
    if name not in TABLES:
        raise ValueError("未知表：%s" % name)
    return '"%s"' % name


def _col(name: str) -> str:
    if not _IDENT.match(name or ""):
        raise ValueError("非法列名：%s" % name)
    return '"%s"' % name


def _lit(value) -> str:
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, (dict, list)):
        return ke_db.sql_json(value)
    return ke_db.sql_str(str(value))


def _schema() -> dict:
    """从 information_schema 取列 + 主键；只返回白名单表。"""
    names = ",".join(ke_db.sql_str(t) for t in TABLES)
    rows = ke_db.psql_csv(
        "SELECT table_name, column_name, data_type, is_nullable, ordinal_position "
        "  FROM information_schema.columns "
        " WHERE table_schema = 'analytics' AND table_name IN (%s) "
        " ORDER BY table_name, ordinal_position" % names)
    out: dict = {t: {"table": t, "columns": [], "primary_key": []} for t in TABLES}
    for row in rows:
        table = str(row.get("table_name") or "")
        if table in out:
            out[table]["columns"].append({
                "name": row.get("column_name"),
                "data_type": row.get("data_type"),
                "nullable": str(row.get("is_nullable")).upper() == "YES",
            })
    pk = ke_db.psql_csv(
        "SELECT tc.table_name, kcu.column_name, kcu.ordinal_position "
        "  FROM information_schema.table_constraints tc "
        "  JOIN information_schema.key_column_usage kcu "
        "    ON tc.constraint_name = kcu.constraint_name "
        "   AND tc.table_schema = kcu.table_schema "
        " WHERE tc.table_schema = 'analytics' AND tc.constraint_type = 'PRIMARY KEY' "
        "   AND tc.table_name IN (%s) "
        " ORDER BY tc.table_name, kcu.ordinal_position" % names)
    for row in pk:
        table = str(row.get("table_name") or "")
        if table in out:
            out[table]["primary_key"].append(row.get("column_name"))
    return out


def _rows(table: str, limit: int = 200) -> list:
    limit = max(1, min(int(limit or 200), 1000))
    return ke_db.psql_csv(
        "SELECT * FROM analytics.%s ORDER BY 1 LIMIT %d" % (_ident(table), limit))


def mapping_interface(kb_id: str = "") -> dict:
    """知识页 ↔ analytics 映射表的只读接口 + 页面属性校验。"""
    rows = ke_db.psql_csv(
        "SELECT mapping_object_type, mapping_object_id, object_code, object_name, "
        "       page_slug, binding_id, mapping_service_id, service_name, database_type, "
        "       binding_status, service_status, formula_only "
        "  FROM analytics.v_mapping_interface "
        " ORDER BY mapping_object_type, mapping_object_id, mapping_service_id NULLS FIRST")
    page_attrs = {}
    if kb_id:
        pages = ke_db.psql_csv(
            "SELECT slug, COALESCE(page_metadata->'ontology'->'attributes', '{}'::jsonb)::text "
            "       AS attrs FROM wiki_pages WHERE knowledge_base_id=%s AND deleted_at IS NULL"
            % ke_db.sql_str(kb_id))
        for p in pages:
            try:
                page_attrs[p["slug"]] = json.loads(p.get("attrs") or "{}")
            except Exception:  # noqa: BLE001
                page_attrs[p["slug"]] = {}
    graph_edges, graph_error = set(), ""
    if kb_id:
        try:
            import ke_neo4j  # noqa: PLC0415
            graph_edges = {
                (str(r.get("a_slug") or ""), str(r.get("b_slug") or ""))
                for r in ke_neo4j.query(
                    "MATCH (a:BodhiInstance {kb_id:$kb})"
                    "-[:`analytics:objectUsesMcpService`]->"
                    "(b:BodhiInstance) RETURN a.slug AS a_slug, b.slug AS b_slug",
                    {"kb": kb_id})}
        except Exception as exc:  # noqa: BLE001
            graph_error = str(exc)[:300]
    fact_tables = {}
    for ft in ke_db.psql_csv(
            "SELECT fact_id, table_id, source_schema, source_table, is_primary "
            "  FROM analytics.fact_table WHERE status='active' "
            " ORDER BY fact_id, sort_order, table_id"):
        fact_tables.setdefault(ft["fact_id"], []).append(ft)
    fact_source_issues = {}
    for bad in ke_db.psql_csv(
            "SELECT b.object_id AS fact_id, m.mapping_id, m.source_schema, "
            "       m.source_table FROM analytics.binding b "
            "  JOIN analytics.field_mapping m ON m.binding_id=b.binding_id "
            " WHERE b.object_type='fact' AND b.status='active' AND m.status='active' "
            "   AND NOT EXISTS (SELECT 1 FROM analytics.fact_table ft "
            "                    WHERE ft.fact_id=b.object_id AND ft.status='active' "
            "                      AND ft.source_schema=m.source_schema "
            "                      AND ft.source_table=m.source_table)"):
        fact_source_issues.setdefault(bad["fact_id"], []).append(bad)
    scenario_join_issues = ke_db.psql_csv(
        "SELECT j.scenario_id, j.seq FROM analytics.scenario_join j "
        " WHERE NOT EXISTS (SELECT 1 FROM analytics.scenario_fact sf "
        "                     JOIN analytics.fact_table ft ON ft.fact_id=sf.fact_id "
        "                      AND ft.status='active' AND ft.source_schema=j.left_schema "
        "                      AND ft.source_table=j.left_table "
        "                    WHERE sf.scenario_id=j.scenario_id AND sf.status='active') "
        "    OR NOT EXISTS (SELECT 1 FROM analytics.scenario_fact sf "
        "                     JOIN analytics.fact_table ft ON ft.fact_id=sf.fact_id "
        "                      AND ft.status='active' AND ft.source_schema=j.right_schema "
        "                      AND ft.source_table=j.right_table "
        "                    WHERE sf.scenario_id=j.scenario_id AND sf.status='active') "
        " ORDER BY j.scenario_id, j.seq")
    for row in rows:
        attrs = page_attrs.get(row.get("page_slug") or "") or {}
        row["page_exists"] = (not kb_id) or bool(attrs) or (row.get("page_slug") in page_attrs)
        row["page_mapping_object_type"] = attrs.get("analytics:mappingObjectType", "")
        row["page_mapping_object_id"] = attrs.get("analytics:mappingObjectId", "")
        issues = []
        if kb_id and not row["page_exists"]:
            issues.append("page_missing")
        if kb_id and row["page_exists"] and (
                not row["page_mapping_object_type"] or not row["page_mapping_object_id"]):
            issues.append("attr_missing")
        if row["page_mapping_object_type"] and row["page_mapping_object_type"] != row["mapping_object_type"]:
            issues.append("mapping_object_type_mismatch")
        if row["page_mapping_object_id"] and row["page_mapping_object_id"] != row["mapping_object_id"]:
            issues.append("mapping_object_id_mismatch")
        if not row.get("binding_id"):
            if str(row.get("formula_only")).lower() not in ("t", "true", "1"):
                issues.append("binding_missing")
        elif row.get("binding_status") != "active":
            issues.append("binding_inactive")
        if row.get("binding_id") and row.get("service_status") != "active":
            issues.append("service_inactive")
        edge_expected = (row.get("page_slug") or "",
                         "agent/mcpservice/%s" % (row.get("service_name") or ""))
        row["edge_expected"] = " -> ".join(edge_expected)
        row["edge_exists"] = edge_expected in graph_edges if kb_id and not graph_error else None
        if row.get("binding_id") and row.get("service_name") and row["edge_exists"] is False:
            issues.append("object_service_edge_missing")
        if row.get("mapping_object_type") == "fact":
            fid = row.get("mapping_object_id")
            row["fact_tables"] = fact_tables.get(fid, [])
            if not row["fact_tables"]:
                issues.append("fact_table_missing")
            if fact_source_issues.get(fid):
                issues.append("fact_source_not_fact_table")
                row["fact_source_issues"] = fact_source_issues[fid]
        row["issues"] = issues
        row["valid"] = not issues
    services = []
    for svc in ke_db.psql_csv(
            "SELECT s.service_id, s.service_name, s.database_type, "
            "       (SELECT ms.id FROM mcp_services ms WHERE ms.name=s.service_name "
            "         AND ms.deleted_at IS NULL ORDER BY ms.created_at DESC LIMIT 1) "
            "         AS platform_service_id "
            "  FROM analytics.mcp_service s WHERE s.status='active' "
            " ORDER BY s.service_name"):
        slug = "agent/mcpservice/%s" % (svc.get("service_name") or "")
        attrs = page_attrs.get(slug) or {}
        issues = []
        if kb_id and slug not in page_attrs:
            issues.append("page_missing")
        if kb_id and not attrs.get("analytics:mappingServiceId"):
            issues.append("attr_missing")
        if attrs.get("analytics:mappingServiceId") and \
                attrs.get("analytics:mappingServiceId") != svc.get("service_id"):
            issues.append("service_id_mismatch")
        if attrs.get("analytics:databaseType") and \
                attrs.get("analytics:databaseType") != svc.get("database_type"):
            issues.append("database_type_mismatch")
        if svc.get("platform_service_id") and \
                attrs.get("analytics:platformMcpServiceId") and \
                attrs.get("analytics:platformMcpServiceId") != svc.get("platform_service_id"):
            issues.append("platform_service_id_mismatch")
        services.append(dict(svc, page_slug=slug, page_exists=slug in page_attrs,
                             page_attributes=attrs, issues=issues,
                             valid=not issues))
    scenario_join_violations = [
        {"mapping_object_type": "scenario_join", "scenario_id": x.get("scenario_id"),
         "seq": x.get("seq"), "issues": ["scenario_join_not_fact_table"],
         "valid": False}
        for x in scenario_join_issues]
    return {"ok": True, "kb_id": kb_id, "rows": rows, "services": services,
            "graph_error": graph_error,
            "scenario_join_violations": scenario_join_violations,
            "rules": ["mapping_object_type = fact|dimension",
                      "mapping_object_id = fact_id/dimension_id",
                      "mapping_service_id = analytics.mcp_service.service_id",
                      "fact field_mapping.source_table ∈ fact_table",
                      "scenario_join 两端 ∈ fact_table"],
            "violations": ([r for r in rows if not r["valid"]] +
                           [s for s in services if not s["valid"]] +
                           scenario_join_violations)}


def _select_one(table: str, pk: dict) -> dict:
    where = " AND ".join("%s = %s" % (_col(k), _lit(v)) for k, v in pk.items())
    rows = ke_db.psql_csv(
        "SELECT * FROM analytics.%s WHERE %s LIMIT 1" % (_ident(table), where))
    return rows[0] if rows else {}


def _upsert(table: str, values: dict) -> dict:
    if not isinstance(values, dict) or not values:
        raise ValueError("values 不能为空")
    for key, value in values.items():
        if isinstance(value, str) and "\ufffd" in value:
            raise ValueError("字段 %s 含 UTF-8 替换字符（乱码）；请用浏览器管理台或 UTF-8 客户端重新提交" % key)
    meta = _schema().get(table)
    if not meta or not meta["columns"]:
        raise ValueError("表不存在或没有列：%s" % table)
    allowed = {c["name"] for c in meta["columns"]}
    keys = [k for k in values if k in allowed]
    if not keys:
        raise ValueError("没有可写列")
    pk = {k: values[k] for k in meta["primary_key"] if k in values}
    if len(pk) != len(meta["primary_key"]):
        raise ValueError("主键不完整：需要 %s" % ", ".join(meta["primary_key"]))
    cols = ", ".join(_col(k) for k in keys)
    vals = ", ".join(_lit(values[k]) for k in keys)
    updates = [k for k in keys if k not in pk]
    if updates:
        action = "DO UPDATE SET " + ", ".join(
            "%s = EXCLUDED.%s" % (_col(k), _col(k)) for k in updates)
    else:
        action = "DO NOTHING"
    sql = ("INSERT INTO analytics.%s (%s) VALUES (%s) "
           "ON CONFLICT (%s) %s"
           % (_ident(table), cols, vals,
              ", ".join(_col(k) for k in meta["primary_key"]), action))
    ke_db.psql(sql)
    return _select_one(table, pk)


def _delete(table: str, values: dict) -> dict:
    meta = _schema().get(table) or {}
    pk = {k: values.get(k) for k in meta.get("primary_key") or []}
    if not pk or any(v is None for v in pk.values()):
        raise ValueError("删除需要完整主键")
    where = " AND ".join("%s = %s" % (_col(k), _lit(v)) for k, v in pk.items())
    ke_db.psql("DELETE FROM analytics.%s WHERE %s" % (_ident(table), where))
    return {"deleted": True, "table": table, "primary_key": pk}


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "analytics-admin/1.0"

    def log_message(self, fmt, *args):  # noqa: D401
        print("[admin] " + (fmt % args), flush=True)

    # ---------- 基础 ----------
    def _authorized(self, params: dict) -> bool:
        if not TOKEN:
            return True
        return ((self.headers.get("X-Admin-Token") or "") == TOKEN
                or params.get("token", [""])[0] == TOKEN)

    def _send(self, code: int, body, ctype: str = "application/json; charset=utf-8"):
        raw = body if isinstance(body, bytes) else json.dumps(
            body, ensure_ascii=False).encode("utf-8")
        try:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(raw)))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(raw)
        except (BrokenPipeError, ConnectionResetError):
            # 浏览器/脚本超时先断开：业务已执行完，不要把断连当 400
            return

    def _error(self, code: int, message: str):
        self._send(code, {"ok": False, "error": message})

    def _json_body(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        try:
            return json.loads(raw.decode("utf-8") or "{}")
        except Exception as exc:  # noqa: BLE001
            raise ValueError("JSON 无效：%r" % (str(exc)[:120],))

    # ---------- GET ----------
    def do_GET(self):  # noqa: N802
        parsed = urlparse.urlparse(self.path)
        params = urlparse.parse_qs(parsed.query)
        if parsed.path in ("/", "/index.html"):
            path = os.path.join(HERE, "index.html")
            try:
                with open(path, "rb") as fh:
                    self._send(200, fh.read(), "text/html; charset=utf-8")
            except OSError:
                self._error(404, "index.html 不存在")
            return
        if not self._authorized(params):
            self._error(401, "需要 X-Admin-Token")
            return
        try:
            if parsed.path == "/api/health":
                self._send(200, {"ok": True, "tables": len(TABLES)})
            elif parsed.path == "/api/schema":
                self._send(200, {"ok": True, "tables": _schema()})
            elif parsed.path == "/api/rows":
                table = str(params.get("table", [""])[0])
                limit = int(params.get("limit", ["200"])[0] or 200)
                self._send(200, {"ok": True, "table": table, "rows": _rows(table, limit)})
            elif parsed.path == "/api/mapping-interface":
                kb_id = str(params.get("kb_id", [""])[0])
                self._send(200, mapping_interface(kb_id))
            elif parsed.path == "/api/mapping-interface/validate":
                kb_id = str(params.get("kb_id", [""])[0])
                self._send(200, mapping_interface(kb_id))
            elif parsed.path == "/api/sync":
                self._send(200, {"ok": True, "note": "同步请用 POST"})
            else:
                self._error(404, "not found")
        except Exception as exc:  # noqa: BLE001
            self._error(400, str(exc)[:500])

    # ---------- POST ----------
    def do_POST(self):  # noqa: N802
        parsed = urlparse.urlparse(self.path)
        params = urlparse.parse_qs(parsed.query)
        if not self._authorized(params):
            self._error(401, "需要 X-Admin-Token")
            return
        try:
            body = self._json_body()
            if parsed.path == "/api/row":
                table = str(body.get("table") or "")
                row = _upsert(table, body.get("values") or {})
                self._send(200, {"ok": True, "table": table, "row": row})
            elif parsed.path == "/api/delete":
                table = str(body.get("table") or "")
                out = _delete(table, body.get("values") or {})
                self._send(200, dict(out, ok=True))
            elif parsed.path == "/api/sync":
                kb_id = str(body.get("kb_id") or "")
                kind = str(body.get("kind") or "all")
                ids = body.get("ids") or None
                dry_run = bool(body.get("dry_run", True))
                out = ke_analytics.sync_pages(kb_id, kind=kind, ids=ids,
                                              dry_run=dry_run)
                self._send(200, dict(out, ok=True))
            else:
                self._error(404, "not found")
        except Exception as exc:  # noqa: BLE001
            print("[admin] error %s: %s" % (parsed.path, str(exc)[:500]), flush=True)
            self._error(400, str(exc)[:500])


class AdminServer(ThreadingHTTPServer):
    daemon_threads = True

    def handle_error(self, request, client_address):
        """浏览器刷新/关闭造成的连接重置不是服务端错误，不打印整段栈。"""
        exc = sys.exc_info()[1]
        if isinstance(exc, (ConnectionResetError, BrokenPipeError)):
            return
        super().handle_error(request, client_address)


def main() -> int:
    parser = argparse.ArgumentParser(description="analytics 配置管理端")
    parser.add_argument("--host", default=ke_db.env_value(
        "BODHI_ANALYTICS_ADMIN_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(ke_db.env_value(
        "BODHI_ANALYTICS_ADMIN_PORT", "8781") or 8781))
    args = parser.parse_args()
    server = AdminServer((args.host, args.port), Handler)
    print("[admin] analytics 配置管理端 http://%s:%d/（表 %d 张）"
          % (args.host, args.port, len(TABLES)), flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("[admin] 停止", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
