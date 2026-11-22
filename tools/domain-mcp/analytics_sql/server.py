"""analytics-sql · 只读自助分析 MCP（list_tables / describe_table / run_select）。

安全：只读 SELECT/CTE、单语句、拒绝 DML/DDL、行数上限；目标服务来自
analytics.mcp_service（当前先支持 PostgreSQL）。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
CORE = os.path.join(REPO, "tools", "ke-core")
if CORE not in sys.path:
    sys.path.insert(0, CORE)

import ke_db  # noqa: E402

SERVER_NAME = "analytics-sql-mcp"
SERVER_VERSION = "0.1.0"
PROTOCOL = "2024-11-05"
_SESSIONS: set[str] = set()
_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_FORBIDDEN = re.compile(
    r"\b(insert|update|delete|drop|alter|create|truncate|grant|revoke|copy|"
    r"call|do|merge|vacuum|analyze)\b", re.I)


def _sql_str(v: str) -> str:
    return "'" + str(v).replace("'", "''") + "'"


def _active_service(service_id: str = "") -> dict:
    where = "status='active'"
    if service_id:
        where += " AND service_id=%s" % _sql_str(service_id)
    rows = ke_db.psql_csv(
        "SELECT service_id, service_name, database_type, scope "
        "FROM analytics.mcp_service WHERE %s ORDER BY service_id LIMIT 1" % where)
    if not rows:
        raise ValueError("没有可用的 active analytics MCP 服务")
    svc = rows[0]
    if str(svc.get("database_type") or "").lower() != "postgresql":
        raise ValueError("当前 analytics-sql 只支持 postgresql，实际为 %s"
                         % svc.get("database_type"))
    return svc


def list_tables(service_id: str = "", keyword: str = "") -> dict:
    svc = _active_service(service_id)
    kw = (keyword or "").strip().lower()
    rows = ke_db.psql_csv(
        "SELECT table_schema AS schema_name, table_name, table_type "
        "FROM information_schema.tables "
        "WHERE table_schema IN ('diag','analytics') "
        "ORDER BY table_schema, table_name")
    if kw:
        rows = [r for r in rows if kw in (r.get("table_name") or "").lower()
                or kw in (r.get("schema_name") or "").lower()]
    return {"ok": True, "service": svc, "tables": rows, "count": len(rows)}


def describe_table(table: str, schema: str = "diag", service_id: str = "") -> dict:
    svc = _active_service(service_id)
    table = str(table or "").strip()
    schema = str(schema or "diag").strip()
    if not _IDENT.match(table) or not _IDENT.match(schema):
        raise ValueError("table/schema 必须是简单标识符")
    rows = ke_db.psql_csv(
        "SELECT column_name, data_type, is_nullable, ordinal_position "
        "FROM information_schema.columns "
        "WHERE table_schema=%s AND table_name=%s ORDER BY ordinal_position"
        % (_sql_str(schema), _sql_str(table)))
    if not rows:
        raise ValueError("表不存在或无字段：%s.%s" % (schema, table))
    return {"ok": True, "service": svc, "schema": schema, "table": table,
            "columns": rows, "count": len(rows)}


def run_select(sql: str, max_rows: int = 200, service_id: str = "") -> dict:
    svc = _active_service(service_id)
    text = str(sql or "").strip()
    if not text:
        raise ValueError("sql 不能为空")
    if len(text) > 20000:
        raise ValueError("sql 过长（>20000 字符）")
    body = text[:-1].rstrip() if text.endswith(";") else text
    if ";" in body:
        raise ValueError("只允许单条 SQL")
    if not re.match(r"^(select|with)\b", body, re.I):
        raise ValueError("只允许 SELECT / WITH 只读查询")
    if _FORBIDDEN.search(body):
        raise ValueError("检测到非只读关键字")
    if re.search(r"\b(public|pg_catalog|information_schema)\s*\.", body, re.I):
        raise ValueError("只允许访问 analytics / diag 业务表")
    limit = max(1, min(int(max_rows or 200), 1000))
    wrapped = "SELECT * FROM (%s) AS _analytics_q LIMIT %d" % (body, limit + 1)
    rows = ke_db.psql_csv(wrapped)
    truncated = len(rows) > limit
    rows = rows[:limit]
    columns = list(rows[0].keys()) if rows else []
    return {"ok": True, "service": svc, "columns": columns,
            "rows": rows, "row_count": len(rows), "truncated": truncated,
            "max_rows": limit}


TOOLS = [
    {"name": "list_tables",
     "description": "列出 analytics/diag 可查询表（只读）。",
     "inputSchema": {"type": "object", "properties": {
         "service_id": {"type": "string"}, "keyword": {"type": "string"}}}},
    {"name": "describe_table",
     "description": "查看表的字段、类型和可空性（只读）。",
     "inputSchema": {"type": "object", "properties": {
         "table": {"type": "string"}, "schema": {"type": "string"},
         "service_id": {"type": "string"}}, "required": ["table"]}},
    {"name": "run_select",
     "description": "执行单条只读 SELECT/CTE；禁止 DML/DDL，最多返回 max_rows 行。",
     "inputSchema": {"type": "object", "properties": {
         "sql": {"type": "string"}, "max_rows": {"type": "integer"},
         "service_id": {"type": "string"}}, "required": ["sql"]}},
]


def call_tool(name: str, args: dict) -> dict:
    a = args or {}
    if name == "list_tables":
        return list_tables(str(a.get("service_id") or ""), str(a.get("keyword") or ""))
    if name == "describe_table":
        return describe_table(str(a.get("table") or ""),
                              str(a.get("schema") or "diag"),
                              str(a.get("service_id") or ""))
    if name == "run_select":
        return run_select(str(a.get("sql") or ""), int(a.get("max_rows") or 200),
                          str(a.get("service_id") or ""))
    raise ValueError("tool not found: %s" % name)


def handle_rpc(req: dict) -> tuple:
    method = str(req.get("method") or "")
    rid = req.get("id")
    if method == "initialize":
        sid = uuid.uuid4().hex[:24]
        _SESSIONS.add(sid)
        return ({"jsonrpc": "2.0", "id": rid, "result": {
            "protocolVersion": PROTOCOL,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION}}}, sid, False)
    if method.startswith("notifications/"):
        return None, "", True
    if method == "ping":
        return {"jsonrpc": "2.0", "id": rid, "result": {}}, "", False
    if method == "tools/list":
        return ({"jsonrpc": "2.0", "id": rid,
                 "result": {"tools": TOOLS}}, "", False)
    if method == "tools/call":
        params = req.get("params") or {}
        try:
            out = call_tool(str(params.get("name") or ""),
                            params.get("arguments") or {})
            err = False
        except Exception as exc:  # noqa: BLE001
            out = {"ok": False, "error": str(exc)[:500]}
            err = True
        return ({"jsonrpc": "2.0", "id": rid, "result": {
            "content": [{"type": "text", "text": json.dumps(out, ensure_ascii=False)}],
            "isError": err}}, "", False)
    return ({"jsonrpc": "2.0", "id": rid,
             "error": {"code": -32601, "message": "method not found"}}, "", False)


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "%s/%s" % (SERVER_NAME, SERVER_VERSION)

    def log_message(self, fmt, *args):
        pass

    def _send(self, code: int, body: bytes, sid: str = ""):
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        if sid:
            self.send_header("Mcp-Session-Id", sid)
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        self._send(200, b'{"ok":true,"service":"analytics-sql-mcp"}')

    def do_POST(self):
        try:
            n = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(n).decode("utf-8") or "{}")
            payload, sid, notification = handle_rpc(body)
            if notification:
                self._send(204, b"", sid)
                return
            self._send(200, json.dumps(payload, ensure_ascii=False).encode("utf-8"), sid)
        except Exception as exc:  # noqa: BLE001
            self._send(400, json.dumps({"ok": False, "error": str(exc)[:300]},
                                       ensure_ascii=False).encode("utf-8"))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int,
                    default=int(os.environ.get("ANALYTICS_SQL_PORT", "8771")))
    args = ap.parse_args()
    ThreadingHTTPServer((args.host, args.port), Handler).serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
