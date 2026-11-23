"""MCP 网关的纯逻辑：notifications 拦截、固定参数注入、schema 兜底、JSON/SSE 编解码。

背景
----
部分内网 MCP 服务没有实现 `notifications/initialized`，会返回
`{"code": -32000, "message": "unknown method"}`；WeKnora 的 mcp-go 在
Initialize 阶段发送该通知，收到错误后整个 MCP 客户端初始化失败。

本模块只做与 HTTP 无关的纯函数，便于单测：
- `is_notification()`：识别 JSON-RPC 通知；
- `inject_tool_args()`：在 tools/call 合并固定参数；
- `fill_tool_schemas()`：tools/list 缺 schema 时补最小 schema；
- `decode_mcp_body()` / `encode_mcp_body()`：兼容 JSON 与 SSE。

代码里不出现具体业务/服务名；上游地址、固定参数、工具白名单全部由配置提供。
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

DEFAULT_MIN_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {},
    "additionalProperties": True,
}

DEFAULT_PROTOCOL_VERSION = "2024-11-05"
DEFAULT_SERVER_INFO: dict[str, Any] = {
    "name": "mcp-gateway",
    "version": "1.0.0",
}

# 逐跳头（hop-by-hop）不转发给上游；Accept-Encoding 交给 urllib 自己处理，
# 避免上游压缩后网关未解压导致 WeKnora 收到 gzip body。
_BLOCKED_HEADERS = {
    "host", "content-length", "connection", "transfer-encoding", "keep-alive",
    "proxy-authenticate", "proxy-authorization", "te", "trailer", "upgrade",
    "accept-encoding",
}


def _as_bool(value: Any, default: bool = False) -> bool:
    if value is None or value == "":
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() not in ("0", "false", "no", "off", "n")


def _as_int(value: Any, default: int) -> int:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return default


def _as_json(value: Any, default: Any) -> Any:
    if value is None or value == "":
        return default
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(str(value))
    except (TypeError, ValueError) as exc:
        raise ValueError("配置不是合法 JSON：%r" % (value,)) from exc


def load_config(path: str | os.PathLike[str] | None = None) -> dict[str, Any]:
    """读取 JSON 配置，并允许 MCP_GATEWAY_* 环境变量覆盖。

    固定参数建议只放在 JSON 文件里（systemd EnvironmentFile 对引号/注释不友好）；
    环境变量主要用于部署时覆盖 host/port/upstream 等非敏感项。
    """
    config: dict[str, Any] = {}
    config_path = Path(path) if path else None
    if config_path and config_path.is_file():
        try:
            raw = json.loads(config_path.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001
            raise ValueError("读取配置失败 %s: %s" % (config_path, exc)) from exc
        if not isinstance(raw, dict):
            raise ValueError("配置文件必须是 JSON 对象：%s" % config_path)
        config.update(raw)

    env_map = {
        "host": "MCP_GATEWAY_HOST",
        "port": "MCP_GATEWAY_PORT",
        "upstream_url": "MCP_GATEWAY_UPSTREAM_URL",
        "timeout_seconds": "MCP_GATEWAY_TIMEOUT",
        "intercept_notifications": "MCP_GATEWAY_INTERCEPT_NOTIFICATIONS",
        "fill_schema": "MCP_GATEWAY_FILL_SCHEMA",
        "inject_args": "MCP_GATEWAY_INJECT_ARGS",
        "inject_tools": "MCP_GATEWAY_INJECT_TOOLS",
        "upstream_headers": "MCP_GATEWAY_UPSTREAM_HEADERS",
        "local_initialize": "MCP_GATEWAY_LOCAL_INITIALIZE",
        "protocol_version": "MCP_GATEWAY_PROTOCOL_VERSION",
        "server_info": "MCP_GATEWAY_SERVER_INFO",
        "instructions": "MCP_GATEWAY_INSTRUCTIONS",
        "normalize_sse": "MCP_GATEWAY_NORMALIZE_SSE",
        "canonicalize_tools": "MCP_GATEWAY_CANONICALIZE_TOOLS",
        "debug": "MCP_GATEWAY_DEBUG",
    }
    for key, env_name in env_map.items():
        if env_name in os.environ:
            config[key] = os.environ[env_name]

    config["host"] = str(config.get("host") or "0.0.0.0").strip()
    config["port"] = _as_int(config.get("port"), 8772)
    config["upstream_url"] = str(config.get("upstream_url") or "").strip().rstrip("/")
    config["timeout_seconds"] = _as_int(config.get("timeout_seconds"), 30)
    config["intercept_notifications"] = _as_bool(config.get("intercept_notifications"), True)
    config["fill_schema"] = _as_bool(config.get("fill_schema"), False)
    config["inject_args"] = _as_json(config.get("inject_args"), {})
    config["inject_tools"] = _as_json(config.get("inject_tools"), ["*"])
    config["upstream_headers"] = _as_json(config.get("upstream_headers"), {})
    config["local_initialize"] = _as_bool(config.get("local_initialize"), True)
    config["protocol_version"] = str(
        config.get("protocol_version") or DEFAULT_PROTOCOL_VERSION).strip()
    config["server_info"] = _as_json(config.get("server_info"), dict(DEFAULT_SERVER_INFO))
    config["instructions"] = str(config.get("instructions") or "")
    config["normalize_sse"] = _as_bool(config.get("normalize_sse"), True)
    config["canonicalize_tools"] = _as_bool(config.get("canonicalize_tools"), True)
    config["debug"] = _as_bool(config.get("debug"), False)

    if not isinstance(config["inject_args"], dict):
        raise ValueError("inject_args 必须是 JSON 对象")
    if isinstance(config["inject_tools"], str):
        config["inject_tools"] = [x.strip() for x in config["inject_tools"].split(",") if x.strip()]
    if not isinstance(config["inject_tools"], list):
        raise ValueError("inject_tools 必须是 JSON 数组或逗号分隔字符串")
    config["inject_tools"] = [str(x) for x in config["inject_tools"]]
    if not isinstance(config["upstream_headers"], dict):
        raise ValueError("upstream_headers 必须是 JSON 对象")
    if not isinstance(config["server_info"], dict):
        raise ValueError("server_info 必须是 JSON 对象")
    if not config["protocol_version"]:
        raise ValueError("protocol_version 不能为空")
    if not config["upstream_url"]:
        raise ValueError("缺少 upstream_url（配置文件或 MCP_GATEWAY_UPSTREAM_URL）")
    if not (config["upstream_url"].startswith("http://") or config["upstream_url"].startswith("https://")):
        raise ValueError("upstream_url 只支持 http/https")
    return config


def is_notification(method: Any) -> bool:
    """JSON-RPC 通知：方法名以 notifications/ 开头，没有 id，服务端不返回 JSON-RPC body。"""
    return isinstance(method, str) and method.startswith("notifications/")


def should_inject(tool_name: Any, inject_tools: list[str]) -> bool:
    if not isinstance(tool_name, str) or not tool_name:
        return False
    return "*" in inject_tools or tool_name in inject_tools


def inject_tool_args(
    params: dict[str, Any] | None,
    inject_args: dict[str, Any],
    inject_tools: list[str],
) -> tuple[dict[str, Any], list[str]]:
    """在 tools/call 的 params.arguments 中合并固定参数（固定参数优先）。"""
    params = dict(params or {})
    tool_name = params.get("name")
    if not inject_args or not should_inject(tool_name, inject_tools):
        return params, []
    arguments = params.get("arguments")
    if not isinstance(arguments, dict):
        arguments = {}
    merged = dict(arguments)
    merged.update(inject_args)
    params["arguments"] = merged
    return params, sorted(str(k) for k in inject_args)


def fill_tool_schemas(result: dict[str, Any]) -> tuple[dict[str, Any], int]:
    """tools/list 结果缺 schema 时补最小可用 schema；已有合法 schema 不改。"""
    if not isinstance(result, dict):
        return result, 0
    tools = result.get("tools")
    if not isinstance(tools, list):
        return result, 0
    changed = 0
    for tool in tools:
        if not isinstance(tool, dict):
            continue
        schema = tool.get("inputSchema")
        if not isinstance(schema, dict):
            tool["inputSchema"] = dict(DEFAULT_MIN_SCHEMA)
            changed += 1
            continue
        if not schema.get("type"):
            schema["type"] = "object"
            changed += 1
        if not isinstance(schema.get("properties"), dict):
            schema["properties"] = {}
            changed += 1
    return result, changed


def decode_mcp_body(body: bytes, content_type: str = "") -> tuple[dict[str, Any] | None, str]:
    """把 JSON 或 SSE（data: 行）解析成 dict。

    返回 `(obj, framing)`，framing 为 "json" / "sse" / "raw"。
    解析失败返回 `(None, "raw")`，调用方应原样透传。
    """
    text = (body or b"").decode("utf-8", "replace").strip()
    if not text:
        return None, "raw"
    try:
        obj = json.loads(text)
        return obj, "json"
    except Exception:  # noqa: BLE001
        pass
    data_lines = []
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("data:"):
            data_lines.append(line[5:].strip())
    for raw in data_lines:
        if not raw:
            continue
        try:
            return json.loads(raw), "sse"
        except Exception:  # noqa: BLE001
            continue
    return None, "raw"


def encode_mcp_body(obj: dict[str, Any], framing: str) -> bytes:
    raw = json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if framing == "sse":
        return b"event: message\ndata: " + raw + b"\n\n"
    return raw


def upstream_headers(
    client_headers: Any,
    extra_headers: dict[str, Any] | None = None,
) -> dict[str, str]:
    """构造转发给上游的请求头：保留 MCP/自定义头，剔除 hop-by-hop，再叠加配置头。"""
    headers: dict[str, str] = {}
    if client_headers is not None:
        for key, value in client_headers.items():
            if key.lower() in _BLOCKED_HEADERS or not value:
                continue
            headers[key] = str(value)
    headers.setdefault("Content-Type", "application/json")
    headers.setdefault("Accept", "application/json, text/event-stream")
    headers["User-Agent"] = "mcp-gateway/1.0"
    for key, value in (extra_headers or {}).items():
        if value is not None:
            headers[str(key)] = str(value)
    return headers


def jsonrpc_error(request_id: Any, code: int, message: str) -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "error": {"code": int(code), "message": str(message)[:1000]},
    }


def _sanitize_input_schema(value: Any) -> dict[str, Any]:
    """把 inputSchema 归一化为 mcp-go 能解析的对象。"""
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except Exception:  # noqa: BLE001
            value = None
    if not isinstance(value, dict):
        return dict(DEFAULT_MIN_SCHEMA)
    schema = dict(value)
    if not isinstance(schema.get("type"), str) or not schema.get("type"):
        schema["type"] = "object"
    properties = schema.get("properties")
    if not isinstance(properties, dict):
        properties = {}
    schema["properties"] = {
        str(key): prop for key, prop in properties.items() if isinstance(prop, dict)
    }
    required = schema.get("required")
    if isinstance(required, list):
        schema["required"] = [item for item in required if isinstance(item, str)]
    elif "required" in schema:
        schema.pop("required", None)
    additional = schema.get("additionalProperties")
    if additional is not None and not isinstance(additional, (bool, dict)):
        schema.pop("additionalProperties", None)
    return schema


def _extract_tools(obj: Any) -> list[Any] | None:
    """从常见外层结构里找 tools：result.tools / result.data.tools / data.tools / tools。"""
    if isinstance(obj, list):
        return obj
    if not isinstance(obj, dict):
        return None
    result = obj.get("result")
    if isinstance(result, str):
        try:
            result = json.loads(result)
        except Exception:  # noqa: BLE001
            result = None
    candidates: list[Any] = []
    if isinstance(result, dict):
        candidates.append(result.get("tools"))
        data = result.get("data")
        if isinstance(data, dict):
            candidates.append(data.get("tools"))
        elif isinstance(data, list):
            candidates.append(data)
    elif isinstance(result, list):
        candidates.append(result)
    candidates.append(obj.get("tools"))
    data = obj.get("data")
    if isinstance(data, dict):
        candidates.append(data.get("tools"))
    elif isinstance(data, list):
        candidates.append(data)
    for candidate in candidates:
        if isinstance(candidate, list):
            return candidate
        if isinstance(candidate, dict):
            return list(candidate.values())
    return None


def _error_code(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return -32000


def canonicalize_tools_response(
    body: bytes,
    content_type: str,
    request_id: Any,
) -> tuple[dict[str, Any] | None, str]:
    """解析上游 tools/list，重打包成标准 JSON-RPC。

    - id 用 WeKnora 的 request_id；
    - result.tools 每一项只保证 name/description/inputSchema 合法；
    - 上游额外字段（如 x-ext）原样保留；
    - 缺 inputSchema / 非对象 / 字符串 → sanitize 成最小合法 schema。
    """
    obj, framing = decode_mcp_body(body, content_type)
    if obj is None:
        return None, "upstream body 不是可解析的 JSON/SSE（framing=%s）" % framing
    if isinstance(obj, dict) and isinstance(obj.get("error"), dict):
        err = obj["error"]
        return jsonrpc_error(
            request_id, _error_code(err.get("code")),
            str(err.get("message") or "upstream error")), ""
    tools_raw = _extract_tools(obj)
    if tools_raw is None:
        return None, "upstream tools/list 没有 result.tools / data.tools / tools"
    tools: list[dict[str, Any]] = []
    for item in tools_raw:
        if isinstance(item, str):
            item = {"name": item}
        if not isinstance(item, dict):
            continue
        name = item.get("name")
        if not isinstance(name, str) or not name.strip():
            continue
        description = item.get("description")
        # 只保留 mcp-go 确定能解析的字段；x-ext 是我们自己的扩展，按需保留。
        # 其它字段（如类型错误的 annotations）会拖垮 typed ListToolsResult，直接丢弃。
        tool: dict[str, Any] = {
            "name": name,
            "description": description if isinstance(description, str) else "",
            "inputSchema": _sanitize_input_schema(item.get("inputSchema")),
        }
        if isinstance(item.get("x-ext"), dict):
            tool["x-ext"] = item["x-ext"]
        tools.append(tool)
    result: dict[str, Any] = {"tools": tools}
    result_obj = obj.get("result") if isinstance(obj, dict) else None
    if isinstance(result_obj, dict) and isinstance(result_obj.get("nextCursor"), str):
        result["nextCursor"] = result_obj["nextCursor"]
    return {"jsonrpc": "2.0", "id": request_id, "result": result}, ""


def local_initialize_result(request_id: Any, config: dict[str, Any]) -> dict[str, Any]:
    """网关本地返回的标准 MCP initialize 结果（不调用上游 initialize）。

    上游 initialize 可能返回非标准 result、缺 event: message、或版本不兼容；
    dbbi 的 initialize 只是连通性测试、没有会话/验证，因此直接本地返回，
    后续 tools/list / tools/call 继续转发。
    """
    result: dict[str, Any] = {
        "protocolVersion": str(config.get("protocol_version") or DEFAULT_PROTOCOL_VERSION),
        "capabilities": {"tools": {"listChanged": False}},
        "serverInfo": dict(config.get("server_info") or DEFAULT_SERVER_INFO),
    }
    instructions = str(config.get("instructions") or "").strip()
    if instructions:
        result["instructions"] = instructions
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def is_sse_content_type(content_type: str) -> bool:
    return "text/event-stream" in str(content_type or "").lower()


def normalize_sse_body(body: bytes) -> tuple[bytes, bool]:
    """把上游 SSE 统一成 `event: message` + `data: {...}`。

    内网服务可能只返回 `data: {...}`，没有 `event: message`；PowerShell 能看，
    但 mcp-go 的 SSE 解析可能不认。这里把每个可解析的 data JSON 重新发成标准 SSE。
    解析失败则原样返回，避免破坏非 JSON 的流。
    """
    text = (body or b"").decode("utf-8", "replace")
    data_lines: list[str] = []
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("data:"):
            raw = line[5:].strip()
            if raw:
                data_lines.append(raw)
    if not data_lines:
        return body, False
    events: list[bytes] = []
    for raw in data_lines:
        try:
            obj = json.loads(raw)
        except Exception:  # noqa: BLE001
            return body, False
        payload = json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        events.append(b"event: message\ndata: " + payload + b"\n\n")
    normalized = b"".join(events)
    return normalized, normalized != (body or b"")
