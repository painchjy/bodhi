"""MCP 网关 HTTP 服务：只补 notifications，其他原样转发；tools/call 注入固定参数。

为什么需要
----------
内网某些 MCP 服务没有实现 `notifications/initialized`，返回 -32000 unknown method，
导致 WeKnora 的 mcp-go 在 Initialize 阶段失败。网关在本地把这个通知吃掉，
返回 202 空 body，其余 initialize / tools/list / tools/call 原样转发。

设计约束
--------
- 标准库；无第三方依赖。
- 代码里不出现具体业务/服务名；上游地址、固定参数、工具白名单全部来自配置。
- notifications/* 永远不转发上游；tools/call 只做参数合并；响应原样透传。
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
import gateway as G  # noqa: E402

VERSION = "1.0.0"


class GatewayHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, server_address, handler, config):
        super().__init__(server_address, handler)
        self.config = config


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "mcp-gateway/" + VERSION

    @property
    def config(self):
        return self.server.config

    def log_message(self, fmt, *args):  # noqa: A003
        pass

    def _log(self, msg: str):
        if self.config.get("quiet"):
            return
        print("[%s] %s" % (time.strftime("%Y-%m-%d %H:%M:%S"), msg), flush=True)

    def _send(self, code: int, body: bytes = b"", content_type: str = "application/json",
              extra: dict[str, str] | None = None):
        body = body or b""
        self.send_response(code)
        if content_type:
            self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Access-Control-Allow-Origin", "*")
        for key, value in (extra or {}).items():
            if value:
                self.send_header(str(key), str(value))
        self.end_headers()
        if body:
            self.wfile.write(body)

    def _read_body(self) -> bytes:
        length = int(self.headers.get("Content-Length") or 0)
        return self.rfile.read(length) if length else b""

    def do_OPTIONS(self):  # noqa: N802
        self._send(204, b"", "application/json", {
            "Access-Control-Allow-Headers": "content-type, accept, mcp-session-id, authorization",
            "Access-Control-Allow-Methods": "POST, GET, OPTIONS",
        })

    def do_GET(self):  # noqa: N802
        path = self.path.split("?", 1)[0].rstrip("/") or "/"
        if path == "/health":
            body = json.dumps({
                "ok": True,
                "service": "mcp-gateway",
                "version": VERSION,
                "upstream": self.config.get("upstream_url", ""),
                "fill_schema": bool(self.config.get("fill_schema")),
                "local_initialize": bool(self.config.get("local_initialize")),
                "protocol_version": self.config.get("protocol_version"),
                "normalize_sse": bool(self.config.get("normalize_sse")),
                "canonicalize_tools": bool(self.config.get("canonicalize_tools")),
                "instructions": bool(self.config.get("instructions")),
                "inject_keys": sorted(str(k) for k in (self.config.get("inject_args") or {})),
            }, ensure_ascii=False).encode("utf-8")
            self._send(200, body, "application/json")
            return
        if path == "/mcp":
            self._send(405, b'{"error":"use POST"}', "application/json",
                       {"Allow": "POST, OPTIONS"})
            return
        self._send(404, b'{"error":"not found"}', "application/json")

    def do_POST(self):  # noqa: N802
        path = self.path.split("?", 1)[0].rstrip("/") or "/"
        if path != "/mcp":
            self._send(404, b'{"error":"not found"}', "application/json")
            return
        raw = self._read_body()
        try:
            request = json.loads(raw.decode("utf-8") or "{}")
        except Exception as exc:  # noqa: BLE001
            self._send(400, json.dumps(
                G.jsonrpc_error(None, -32700, "bad json: %s" % exc)).encode("utf-8"),
                "application/json")
            return
        if not isinstance(request, dict):
            self._send(400, json.dumps(
                G.jsonrpc_error(None, -32600, "invalid request")).encode("utf-8"),
                "application/json")
            return
        method = request.get("method")
        request_id = request.get("id")

        # 唯一改协议的地方：内网服务没实现 notifications/initialized，
        # 本地直接 202 空 body，不转发上游。
        if G.is_notification(method) and self.config.get("intercept_notifications"):
            self._log("notification %s -> 202 (local)" % method)
            self._send(202, b"", "application/json")
            return

        # 本地标准 initialize：不转发上游 initialize。
        # dbbi 的 initialize 只是连通性测试、没有会话/验证；本地返回可绕开
        # 非标准 result / 缺 event: message / 版本差异，后续 tools/list 仍转发。
        if method == "initialize" and self.config.get("local_initialize"):
            payload = G.local_initialize_result(request_id, self.config)
            body_out = json.dumps(
                payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            self._log("initialize -> local (id=%s, version=%s, instructions=%s)" % (
                request_id, self.config.get("protocol_version"),
                "yes" if self.config.get("instructions") else "no"))
            self._send(200, body_out, "application/json")
            return

        injected: list[str] = []
        if method == "tools/call":
            params, injected = G.inject_tool_args(
                request.get("params"), self.config.get("inject_args") or {},
                self.config.get("inject_tools") or ["*"])
            request["params"] = params

        body = json.dumps(request, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        headers = G.upstream_headers(self.headers, self.config.get("upstream_headers") or {})
        upstream = self.config["upstream_url"]
        start = time.time()
        status = 0
        content_type = "application/json"
        response_body = b""
        extra: dict[str, str] = {}

        try:
            req = urllib.request.Request(upstream, data=body, headers=headers, method="POST")
            try:
                resp = urllib.request.urlopen(req, timeout=self.config.get("timeout_seconds", 30))
                status = getattr(resp, "status", 200)
                content_type = resp.headers.get("Content-Type") or "application/json"
                session_id = resp.headers.get("Mcp-Session-Id")
                if session_id:
                    extra["Mcp-Session-Id"] = session_id
                response_body = resp.read()
            except urllib.error.HTTPError as exc:
                status = exc.code
                content_type = exc.headers.get("Content-Type") or "application/json"
                session_id = exc.headers.get("Mcp-Session-Id")
                if session_id:
                    extra["Mcp-Session-Id"] = session_id
                response_body = exc.read()
        except Exception as exc:  # noqa: BLE001
            elapsed = int((time.time() - start) * 1000)
            self._log("forward %s -> ERROR %s (%dms)" % (method, exc, elapsed))
            if request_id is None:
                self._send(502, json.dumps(
                    {"ok": False, "error": "upstream unreachable"}).encode("utf-8"),
                    "application/json")
            else:
                err = G.jsonrpc_error(request_id, -32000, "upstream unreachable: %s" % exc)
                self._send(200, json.dumps(err, ensure_ascii=False).encode("utf-8"),
                           "application/json")
            return

        if self.config.get("debug"):
            self._log("upstream method=%s status=%s content_type=%s bytes=%d id=%s" % (
                method, status, content_type, len(response_body), request_id))

        # tools/list 统一解析 + 重打包成纯 JSON；彻底绕开上游 SSE/外层/schema 差异。
        # id 用 WeKnora 的 request_id；x-ext 有就保留，没有就不加。
        if method == "tools/list" and self.config.get("canonicalize_tools"):
            payload, reason = G.canonicalize_tools_response(
                response_body, content_type, request_id)
            if payload is not None:
                response_body = json.dumps(
                    payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
                content_type = "application/json"
                if isinstance(payload.get("result"), dict):
                    self._log("canonicalize tools/list count=%d id=%s" % (
                        len(payload["result"].get("tools") or []), request_id))
                else:
                    self._log("canonicalize tools/list error id=%s" % request_id)
            else:
                self._log("canonicalize tools/list failed: %s body=%r" % (
                    reason, response_body[:300]))
                err = G.jsonrpc_error(
                    request_id, -32000,
                    "invalid upstream tools/list response: %s" % reason)
                response_body = json.dumps(
                    err, ensure_ascii=False).encode("utf-8")
                content_type = "application/json"
                status = 200

        # 上游 SSE 可能只有 data: 没有 event: message；统一成标准 SSE。
        if self.config.get("normalize_sse") and G.is_sse_content_type(content_type):
            before = len(response_body)
            response_body, changed = G.normalize_sse_body(response_body)
            if changed:
                content_type = "text/event-stream"
                if self.config.get("debug"):
                    self._log("normalize_sse method=%s bytes=%d->%d" % (
                        method, before, len(response_body)))

        # 可选：tools/list 缺 schema 时补最小 schema（默认关闭）。
        if method == "tools/list" and self.config.get("fill_schema"):
            obj, framing = G.decode_mcp_body(response_body, content_type)
            if isinstance(obj, dict) and isinstance(obj.get("result"), dict):
                _, changed = G.fill_tool_schemas(obj["result"])
                if changed:
                    response_body = G.encode_mcp_body(obj, framing)
                    if framing == "sse":
                        content_type = "text/event-stream"
                    self._log("tools/list filled schema=%d" % changed)

        elapsed = int((time.time() - start) * 1000)
        tool_name = ""
        if method == "tools/call":
            tool_name = str((request.get("params") or {}).get("name") or "")
        self._log("forward %s%s status=%s %dms inject=%s" % (
            method, ("/%s" % tool_name) if tool_name else "", status, elapsed,
            ",".join(injected) if injected else "-"))
        self._send(status or 200, response_body, content_type, extra)


def main() -> int:
    config_path = os.environ.get("MCP_GATEWAY_CONFIG") or os.path.join(HERE, "gateway.json")
    try:
        config = G.load_config(config_path)
    except Exception as exc:  # noqa: BLE001
        print("mcp-gateway 配置错误：%s" % exc, file=sys.stderr)
        return 2
    config["quiet"] = str(os.environ.get("MCP_GATEWAY_QUIET", "")).strip().lower() in (
        "1", "true", "yes", "on")
    server = GatewayHTTPServer((config["host"], config["port"]), Handler, config)
    keys = ",".join(sorted(str(k) for k in (config.get("inject_args") or {}))) or "-"
    print("mcp-gateway %s listening on http://%s:%d/mcp -> %s "
          "(inject=%s, fill_schema=%s, local_initialize=%s, normalize_sse=%s, "
          "canonicalize_tools=%s)" % (
              VERSION, config["host"], config["port"], config["upstream_url"], keys,
              "on" if config.get("fill_schema") else "off",
              "on" if config.get("local_initialize") else "off",
              "on" if config.get("normalize_sse") else "off",
              "on" if config.get("canonicalize_tools") else "off"), flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
