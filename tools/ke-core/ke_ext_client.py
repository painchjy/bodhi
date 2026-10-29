"""外部 MCP 服务的**只读客户端**：按注册表找地址 → `tools/list` 取契约（零第三方依赖）。

口径（2026-10-25 用户口径：契约只留一个真源）
- **地址真源** = WeKnora `mcp_services`（`name` → `url`）；env 可覆盖（本地/离线最方便）：
  `BODHI_EXT_SERVICE_URLS={"it_platform":"http://127.0.0.1:8770/mcp"}`
- `host.docker.internal` 在 WSL/宿主侧常解析不了 ⇒ 自动追加一个"把主机名换成 `127.0.0.1`"的候选
  （**只改主机名**，路径/端口不动）；
- **只读**：只发 `tools/list`（不发 initialize、不写任何东西）；回执可能是纯 JSON，
  也可能是 SSE（`data: {...}` 行）⇒ 两种都解析 ✓。
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

try:                                     # 服务里一定有 ke_db（同目录可导入）
    import ke_db
except Exception:                        # noqa: BLE001  单测/离线场景下退化
    ke_db = None


def _env_map() -> dict:
    raw = os.environ.get("BODHI_EXT_SERVICE_URLS") or ""
    if not raw.strip():
        return {}
    try:
        doc = json.loads(raw)
        return {str(k): str(v) for k, v in doc.items()} if isinstance(doc, dict) else {}
    except Exception:                    # noqa: BLE001  写坏了不致命：忽略 env，继续查注册表
        return {}


def service_urls(name: str) -> list:
    """某个外部服务（`it_platform` 等）的**候选地址**（按尝试顺序去重）。"""
    name = str(name or "").strip()
    out = []
    env = _env_map().get(name)
    if env:
        out.append(env)
    if ke_db is not None and name:
        try:
            rows = ke_db.psql_csv(
                "SELECT url FROM mcp_services WHERE deleted_at IS NULL AND enabled "
                "AND name = %s ORDER BY updated_at DESC LIMIT 1" % ke_db.sql_str(name))
            if rows and rows[0].get("url"):
                out.append(rows[0]["url"])
        except Exception:                # noqa: BLE001  没有库也能用 env 兜底
            pass
    alt = [u.replace("host.docker.internal", "127.0.0.1") for u in out]
    seen = []
    for u in out + alt:
        if u and u not in seen:
            seen.append(u)
    return seen


def _decode(body: bytes) -> dict:
    """JSON 或 SSE（`data:` 行）→ dict。"""
    text = (body or b"").decode("utf-8", "ignore").strip()
    if not text:
        return {}
    if text.startswith("{"):
        return json.loads(text)
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("data:"):
            payload = line[5:].strip()
            if payload.startswith("{"):
                return json.loads(payload)
    raise ValueError("无法解析的 MCP 回执：%s" % text[:200])


def rpc(url: str, method: str, params: dict | None = None, timeout: float = 8.0) -> dict:
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method,
                       "params": params or {}}, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=body, headers={
        "Content-Type": "application/json", "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return _decode(resp.read())


def list_tools(url: str, timeout: float = 8.0) -> list:
    """`tools/list` → tools[]（失败抛异常，调用方转可读回执）。"""
    out = rpc(url, "tools/list", {}, timeout=timeout)
    return ((out.get("result") or {}).get("tools") or [])


def find_tool(url: str, tool: str, timeout: float = 8.0) -> tuple:
    """→ `(tool_def, tool_names)`；找不到 ⇒ `(None, names)`。"""
    tools = list_tools(url, timeout=timeout)
    names = [t.get("name") for t in tools]
    for item in tools:
        if item.get("name") == tool:
            return item, names
    return None, names


def call(tool: str, args: dict | None = None, service: str = "it_platform",
         url: str = "", timeout: float = 30.0) -> dict:
    """调外部 MCP 的 `tools/call`，并把回执里的 JSON 文本解析成 dict（**不抛**）。

    用途（2026-10-29）：02 的面板/报告补锚点需要**外部系统的实时数据**（台账行、rev、列集），
    但 02 与 04 **不共享磁盘**（04 独立部署）⇒ 一律走这个调用，不再读本地文件。
    """
    cands = [url] if url else service_urls(service)
    if not cands:
        return {"ok": False, "error": "no_service_url", "service": service,
                "hint": ("没找到服务地址：在 WeKnora `mcp_services` 建 `%s` 行，"
                         "或设 env `BODHI_EXT_SERVICE_URLS`" % service)}
    errs = []
    for u in cands:
        try:
            out = rpc(u, "tools/call", {"name": tool, "arguments": args or {}}, timeout=timeout)
        except (urllib.error.URLError, OSError, ValueError, KeyError) as exc:
            errs.append("%s: %r" % (u, exc))
            continue
        res = out.get("result") or {}
        txt = ""
        for c in (res.get("content") or []):
            if isinstance(c, dict) and c.get("type") == "text":
                txt = c.get("text") or ""
                break
        data = {}
        try:
            data = json.loads(txt) if (txt or "").strip().startswith("{") else {}
        except Exception:  # noqa: BLE001
            data = {"ok": False, "error": "bad_json", "raw": txt[:300]}
        if not data:
            data = {"ok": False, "error": "empty_result", "raw": (txt or "")[:200]}
        data["_service_url"] = u
        return data
    return {"ok": False, "error": "unreachable", "service": service,
            "tried": cands, "detail": errs[:3]}


def describe(service: str, tool: str, url: str = "", timeout: float = 8.0) -> dict:
    """连服务取 `x-ext` 契约 → `{ok, url, tool, contract, tools, error?}`（**不抛**）。"""
    cands = [url] if url else service_urls(service)
    if not cands:
        return {"ok": False, "error": "no_service_url", "service": service,
                "hint": ("没找到服务地址：在 WeKnora `mcp_services` 建同名行，"
                         "或设 env `BODHI_EXT_SERVICE_URLS='{\"%s\":\"http://127.0.0.1:8770/mcp\"}'"
                         % service)}
    errs = []
    for u in cands:
        try:
            item, names = find_tool(u, tool, timeout=timeout)
        except (urllib.error.URLError, OSError, ValueError, KeyError) as exc:
            errs.append("%s: %r" % (u, exc))
            continue
        if item is None:
            return {"ok": False, "error": "tool_not_found", "url": u,
                    "tool": tool, "tools": names}
        ext = item.get("x-ext") or {}
        if not ext:
            return {"ok": False, "error": "no_x_ext", "url": u, "tool": tool,
                    "hint": "该工具未下发 x-ext（服务版本过旧或未启用契约下发）"}
        return {"ok": True, "url": u, "tool": tool, "tools": names, "contract": ext}
    return {"ok": False, "error": "unreachable", "service": service,
            "tried": cands, "detail": errs[:3]}
