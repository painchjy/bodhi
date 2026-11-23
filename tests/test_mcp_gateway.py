"""MCP 网关纯逻辑用例（不启服务、不连网）。"""
from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "tools" / "mcp-gateway"))

import gateway as G  # noqa: E402


def test_notification_detection():
    assert G.is_notification("notifications/initialized")
    assert G.is_notification("notifications/cancelled")
    assert not G.is_notification("tools/list")
    assert not G.is_notification(None)


def test_inject_tool_args_fixed_wins_and_allowlist():
    params = {"name": "tool_a", "arguments": {"x": 1, "_tenant": "wrong"}}
    out, keys = G.inject_tool_args(
        params, {"_tenant": "10000", "_loginName": "u"}, ["*"])
    assert out["arguments"] == {"x": 1, "_tenant": "10000", "_loginName": "u"}
    assert keys == ["_loginName", "_tenant"]

    out2, keys2 = G.inject_tool_args(
        {"name": "tool_b", "arguments": {"x": 1}},
        {"_tenant": "10000"}, ["tool_a"])
    assert out2["arguments"] == {"x": 1}
    assert keys2 == []


def test_inject_tool_args_no_arguments():
    out, keys = G.inject_tool_args({"name": "tool_a"}, {"_tenant": "10000"}, ["*"])
    assert out["arguments"] == {"_tenant": "10000"}
    assert keys == ["_tenant"]


def test_fill_tool_schemas():
    result = {
        "tools": [
            {"name": "a"},
            {"name": "b", "inputSchema": {"type": "object", "properties": {"x": {"type": "string"}}}},
            {"name": "c", "inputSchema": {}},
            "not-a-dict",
        ]
    }
    out, changed = G.fill_tool_schemas(result)
    assert out["tools"][0]["inputSchema"]["type"] == "object"
    assert out["tools"][0]["inputSchema"]["properties"] == {}
    assert out["tools"][1]["inputSchema"]["properties"]["x"]["type"] == "string"
    assert out["tools"][2]["inputSchema"]["type"] == "object"
    assert out["tools"][2]["inputSchema"]["properties"] == {}
    assert changed >= 2


def test_local_initialize_result():
    config = {
        "protocol_version": "2024-11-05",
        "server_info": {"name": "mcp-gateway", "version": "1.0.0"},
        "instructions": "use tools carefully",
    }
    obj = G.local_initialize_result(7, config)
    assert obj["id"] == 7
    result = obj["result"]
    assert result["protocolVersion"] == "2024-11-05"
    assert result["capabilities"]["tools"]["listChanged"] is False
    assert result["serverInfo"]["name"] == "mcp-gateway"
    assert result["instructions"] == "use tools carefully"

    obj2 = G.local_initialize_result(8, {**config, "instructions": ""})
    assert "instructions" not in obj2["result"]


def test_normalize_sse_body():
    raw = b'data: {"jsonrpc":"2.0","id":1,"result":{}}\n\n'
    out, changed = G.normalize_sse_body(raw)
    assert changed
    assert out.startswith(b"event: message\ndata: ")
    assert b'"id":1' in out

    out2, _ = G.normalize_sse_body(out)
    out3, _ = G.normalize_sse_body(out2)
    assert out3 == out2

    raw2 = b"data: not-json\n\n"
    out4, changed4 = G.normalize_sse_body(raw2)
    assert out4 == raw2
    assert changed4 is False


def test_canonicalize_tools_standard():
    body = json.dumps({
        "jsonrpc": "2.0", "id": 1,
        "result": {"tools": [{
            "name": "tool_a",
            "description": "desc",
            "inputSchema": {"type": "object",
                            "properties": {"x": {"type": "string"}},
                            "required": ["x"]},
            "x-ext": {"columns": []},
            "annotations": "not-a-valid-object",
        }]},
    }).encode()
    payload, reason = G.canonicalize_tools_response(body, "application/json", 42)
    assert reason == ""
    assert payload["id"] == 42
    tool = payload["result"]["tools"][0]
    assert tool["name"] == "tool_a"
    assert tool["inputSchema"]["properties"]["x"]["type"] == "string"
    assert tool["x-ext"] == {"columns": []}
    assert "annotations" not in tool


def test_canonicalize_tools_sse_without_event_and_string_schema():
    tool = {
        "name": "query",
        "inputSchema": '{"type":"object","properties":{"sql":{"type":"string"}}}',
    }
    body = ("data: " + json.dumps({
        "jsonrpc": "2.0", "id": 1, "result": {"tools": [tool]},
    }) + "\n\n").encode()
    payload, reason = G.canonicalize_tools_response(body, "text/event-stream", 7)
    assert reason == ""
    assert payload["id"] == 7
    schema = payload["result"]["tools"][0]["inputSchema"]
    assert schema["properties"]["sql"]["type"] == "string"


def test_canonicalize_tools_data_tools_and_invalid_schema():
    body = json.dumps({
        "result": {"data": {"tools": [{
            "name": "table_info",
            "inputSchema": {"type": "object", "properties": []},
        }]}},
    }).encode()
    payload, reason = G.canonicalize_tools_response(body, "application/json", 8)
    assert reason == ""
    schema = payload["result"]["tools"][0]["inputSchema"]
    assert schema["type"] == "object"
    assert schema["properties"] == {}


def test_canonicalize_tools_missing_and_error():
    payload, reason = G.canonicalize_tools_response(
        b'{"result":{}}', "application/json", 9)
    assert payload is None
    assert "没有" in reason

    body = json.dumps({
        "jsonrpc": "2.0", "id": 1,
        "error": {"code": -32601, "message": "nope"},
    }).encode()
    payload2, reason2 = G.canonicalize_tools_response(
        body, "application/json", 10)
    assert reason2 == ""
    assert payload2["id"] == 10
    assert payload2["error"]["code"] == -32601


def test_decode_json_and_sse_and_encode():
    obj, framing = G.decode_mcp_body(b'{"jsonrpc":"2.0","id":1,"result":{}}')
    assert framing == "json"
    assert obj["id"] == 1

    sse = b'event: message\ndata: {"jsonrpc":"2.0","id":2,"result":{}}\n\n'
    obj2, framing2 = G.decode_mcp_body(sse, "text/event-stream")
    assert framing2 == "sse"
    assert obj2["id"] == 2

    raw = G.encode_mcp_body(obj2, "sse")
    assert raw.startswith(b"event: message\ndata: ")
    assert raw.endswith(b"\n\n")


def test_upstream_headers_filters_hop_by_hop():
    headers = G.upstream_headers(
        {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "Mcp-Session-Id": "sid-1",
            "X-Custom": "v",
            "Accept-Encoding": "gzip",
            "Host": "example",
            "Content-Length": "10",
        },
        {"Authorization": "Bearer x"},
    )
    assert headers["Mcp-Session-Id"] == "sid-1"
    assert headers["X-Custom"] == "v"
    assert headers["Authorization"] == "Bearer x"
    assert "Accept-Encoding" not in headers
    assert "Host" not in headers
    assert "Content-Length" not in headers


def test_load_config_and_env_override(tmp_path, monkeypatch):
    for key in (
        "MCP_GATEWAY_HOST", "MCP_GATEWAY_PORT", "MCP_GATEWAY_UPSTREAM_URL",
        "MCP_GATEWAY_TIMEOUT", "MCP_GATEWAY_INTERCEPT_NOTIFICATIONS",
        "MCP_GATEWAY_FILL_SCHEMA", "MCP_GATEWAY_INJECT_ARGS",
        "MCP_GATEWAY_INJECT_TOOLS", "MCP_GATEWAY_UPSTREAM_HEADERS",
        "MCP_GATEWAY_LOCAL_INITIALIZE", "MCP_GATEWAY_PROTOCOL_VERSION",
        "MCP_GATEWAY_SERVER_INFO", "MCP_GATEWAY_INSTRUCTIONS",
        "MCP_GATEWAY_NORMALIZE_SSE", "MCP_GATEWAY_CANONICALIZE_TOOLS",
        "MCP_GATEWAY_DEBUG",
    ):
        monkeypatch.delenv(key, raising=False)

    cfg = tmp_path / "gateway.json"
    cfg.write_text(json.dumps({
        "upstream_url": "http://127.0.0.1:8770/mcp",
        "port": 8772,
        "inject_args": {"_tenant": "10000"},
        "inject_tools": ["*"],
        "fill_schema": False,
    }), encoding="utf-8")

    config = G.load_config(cfg)
    assert config["port"] == 8772
    assert config["inject_args"]["_tenant"] == "10000"
    assert config["fill_schema"] is False
    assert config["intercept_notifications"] is True
    assert config["local_initialize"] is True
    assert config["normalize_sse"] is True
    assert config["canonicalize_tools"] is True
    assert config["protocol_version"] == "2024-11-05"

    monkeypatch.setenv("MCP_GATEWAY_PORT", "9999")
    monkeypatch.setenv("MCP_GATEWAY_FILL_SCHEMA", "1")
    monkeypatch.setenv("MCP_GATEWAY_LOCAL_INITIALIZE", "1")
    monkeypatch.setenv("MCP_GATEWAY_NORMALIZE_SSE", "0")
    monkeypatch.setenv("MCP_GATEWAY_CANONICALIZE_TOOLS", "0")
    config2 = G.load_config(cfg)
    assert config2["port"] == 9999
    assert config2["fill_schema"] is True
    assert config2["local_initialize"] is True
    assert config2["normalize_sse"] is False
    assert config2["canonicalize_tools"] is False

    monkeypatch.setenv("MCP_GATEWAY_LOCAL_INITIALIZE", "0")
    config3 = G.load_config(cfg)
    assert config3["local_initialize"] is False
