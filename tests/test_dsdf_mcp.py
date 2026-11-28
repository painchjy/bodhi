"""DSDF MCP compatibility and JDBC boundary tests; no live database required."""
from __future__ import annotations

import importlib.util
import json
import sys
import threading
import urllib.request
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
APP = REPO / "tools" / "dsdf-mcp"
sys.path.insert(0, str(APP))

import jdbc  # noqa: E402
import server  # noqa: E402


def _configure(monkeypatch, tmp_path):
    jar = tmp_path / "postgresql-42.5.1.jar"
    jar.write_bytes(b"test jar")
    values = {
        "DSDF_DB_HOST": "dws.example",
        "DSDF_DB_PORT": "5544",
        "DSDF_DB_NAME": "warehouse",
        "DSDF_DB_USER": "reader",
        "DSDF_DB_PASSWORD": "test-secret",
        "DSDF_JDBC_DRIVER": "org.postgresql.Driver",
        "DSDF_JDBC_JAR": str(jar),
        "DSDF_SERVICE_ID": "dsdf",
        "DSDF_SERVICE_NAME": "DSDF DWS",
        "DSDF_SERVICE_SCOPE": "read-only",
    }
    for key, value in values.items():
        monkeypatch.setenv(key, value)
    return jar


def _legacy_tools():
    path = REPO / "tools" / "domain-mcp" / "analytics_sql" / "server.py"
    spec = importlib.util.spec_from_file_location("legacy_analytics_sql", path)
    assert spec and spec.loader
    legacy = importlib.util.module_from_spec(spec)
    core = str(REPO / "tools" / "ke-core")
    sys.path.insert(0, core)
    try:
        spec.loader.exec_module(legacy)
    finally:
        sys.path.remove(core)
    return legacy.TOOLS


def test_tool_contracts_match_analytics_sql():
    assert server.TOOLS == _legacy_tools()


def test_list_tables_uses_independent_service_metadata(monkeypatch, tmp_path):
    _configure(monkeypatch, tmp_path)
    monkeypatch.setattr(server.jdbc, "query", lambda sql, params=(): [
        {"schema_name": "analytics", "table_name": "fact_sales", "table_type": "BASE TABLE"},
        {"schema_name": "diag", "table_name": "events", "table_type": "VIEW"},
    ])

    result = server.list_tables(keyword="FACT", service_id="dsdf")

    assert result == {
        "ok": True,
        "service": {"service_id": "dsdf", "service_name": "DSDF DWS",
                    "database_type": "postgresql", "scope": "read-only"},
        "tables": [{"schema_name": "analytics", "table_name": "fact_sales",
                    "table_type": "BASE TABLE"}],
        "count": 1,
    }
    with pytest.raises(ValueError, match="没有可用"):
        server.list_tables(service_id="unknown")


def test_describe_table_uses_bound_parameters(monkeypatch, tmp_path):
    _configure(monkeypatch, tmp_path)
    calls = []

    def fake_query(sql, params=()):
        calls.append((sql, params))
        return [{"column_name": "id", "data_type": "integer",
                 "is_nullable": "NO", "ordinal_position": "1"}]

    monkeypatch.setattr(server.jdbc, "query", fake_query)
    result = server.describe_table("orders", "analytics")

    assert calls[0][1] == ("analytics", "orders")
    assert result["count"] == 1
    assert result["columns"][0]["column_name"] == "id"
    with pytest.raises(ValueError, match="简单标识符"):
        server.describe_table("orders;drop")


def test_run_select_preserves_limits_and_rejects_writes(monkeypatch, tmp_path):
    _configure(monkeypatch, tmp_path)
    calls = []

    def fake_query(sql, params=()):
        calls.append(sql)
        return [{"n": str(index)} for index in range(4)]

    monkeypatch.setattr(server.jdbc, "query", fake_query)
    result = server.run_select("SELECT n FROM analytics.fact_sales;", max_rows=3)

    assert "LIMIT 4" in calls[0]
    assert result["row_count"] == 3
    assert result["truncated"] is True
    assert result["max_rows"] == 3
    assert [row["n"] for row in result["rows"]] == ["0", "1", "2"]
    for sql in ("DELETE FROM analytics.fact_sales", "SELECT 1; SELECT 2",
                "SELECT * FROM information_schema.tables"):
        with pytest.raises(ValueError):
            server.run_select(sql)
    assert len(calls) == 1


def test_jdbc_query_uses_configured_driver_and_read_only_transaction(monkeypatch, tmp_path):
    jar = _configure(monkeypatch, tmp_path)
    events = []

    class JdbcConnection:
        def setReadOnly(self, enabled):
            events.append(("read_only", enabled))

        def setAutoCommit(self, enabled):
            events.append(("auto_commit", enabled))

    class Cursor:
        description = None

        def execute(self, sql, params=()):
            events.append(("execute", sql, params))
            if sql == "SELECT item FROM t":
                self.description = [("item",)]

        def fetchall(self):
            return [(1,), (None,), (True,)]

        def close(self):
            events.append(("cursor_close",))

    class Connection:
        jconn = JdbcConnection()

        def cursor(self):
            return Cursor()

        def rollback(self):
            events.append(("rollback",))

        def close(self):
            events.append(("connection_close",))

    def connector(driver, url, credentials, jars):
        events.append(("connect", driver, url, credentials, jars))
        return Connection()

    rows = jdbc.query("SELECT item FROM t", connector=connector)

    assert events[0] == ("connect", "org.postgresql.Driver",
                         "jdbc:postgresql://dws.example:5544/warehouse",
                         ["reader", "test-secret"], [str(jar)])
    assert events[1:3] == [("read_only", True), ("auto_commit", False)]
    assert events[3] == ("execute", "SET TRANSACTION READ ONLY", ())
    assert events[4] == ("execute", "SELECT item FROM t", ())
    assert rows == [{"item": "1"}, {"item": ""}, {"item": "t"}]
    assert events[-3:] == [("cursor_close",), ("rollback",), ("connection_close",)]


def test_jdbc_configuration_requires_credentials_and_jar(monkeypatch):
    monkeypatch.delenv("DSDF_DB_HOST", raising=False)
    monkeypatch.delenv("DSDF_DB_NAME", raising=False)
    monkeypatch.delenv("DSDF_DB_USER", raising=False)
    monkeypatch.delenv("DSDF_DB_PASSWORD", raising=False)
    monkeypatch.delenv("DSDF_JDBC_JAR", raising=False)
    with pytest.raises(ValueError, match="缺少 DSDF JDBC 配置"):
        jdbc.validate_config(jdbc.load_config())


def test_http_mcp_health_tools_and_call(monkeypatch, tmp_path):
    _configure(monkeypatch, tmp_path)
    monkeypatch.setattr(server.jdbc, "query", lambda sql, params=(): [
        {"schema_name": "analytics", "table_name": "fact_sales", "table_type": "BASE TABLE"},
    ])
    httpd = server.ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
    thread = threading.Thread(target=httpd.serve_forever)
    thread.start()
    base = "http://127.0.0.1:%d" % httpd.server_address[1]
    try:
        with urllib.request.urlopen(base + "/health", timeout=3) as response:
            assert json.loads(response.read())["server"] == "dsdf-mcp"

        def post(body):
            request = urllib.request.Request(
                base + "/mcp", data=json.dumps(body).encode("utf-8"),
                headers={"Content-Type": "application/json"}, method="POST")
            with urllib.request.urlopen(request, timeout=3) as response:
                return json.loads(response.read())

        listed = post({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
        assert [tool["name"] for tool in listed["result"]["tools"]] == [
            "list_tables", "describe_table", "run_select"]

        called = post({"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                       "params": {"name": "list_tables", "arguments": {}}})
        payload = json.loads(called["result"]["content"][0]["text"])
        assert payload["ok"] is True
        assert payload["count"] == 1
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=3)


def test_default_listener_uses_port_8772(monkeypatch):
    monkeypatch.delenv("DSDF_MCP_HOST", raising=False)
    monkeypatch.delenv("DSDF_MCP_PORT", raising=False)
    captured = []

    class FakeHttpServer:
        def __init__(self, address, handler):
            captured.append(address)

        def serve_forever(self):
            return None

    monkeypatch.setattr(server, "ThreadingHTTPServer", FakeHttpServer)
    assert server.main([]) == 0
    assert captured == [("0.0.0.0", 8772)]
