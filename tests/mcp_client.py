"""P0.5 · MCP 客户端（**工具回归必须经 MCP 调用** ✓ 用户口径 2026-10-21）

零依赖（urllib）✓；支持 stdio 模式的服务一律用 **HTTP**（`http://127.0.0.1:8765/mcp` ✓）。

用法：
```
from mcp_client import MCP
c = MCP()
c.call("ontology_types", {"model": "bmm"})          # → dict 结果（已解包 content[0].text）
c.call_raw("knowledge_lookup", {...})                # → 完整 JSON-RPC 响应
```
"""
from __future__ import annotations

import json
import os
import urllib.request

DEFAULT_URL = os.environ.get("BODHI_MCP_URL", "http://127.0.0.1:8765/mcp")


class MCPError(RuntimeError):
    pass


class MCP:
    def __init__(self, url: str = "", timeout: int = 120):
        self.url = url or DEFAULT_URL
        self.timeout = timeout
        self.sid = ""
        self._init()

    # ── 传输 ───────────────────────────────────────────────────────
    def _post(self, payload: dict) -> dict:
        req = urllib.request.Request(
            self.url, data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json",
                     **({"Mcp-Session-Id": self.sid} if self.sid else {})})
        with urllib.request.urlopen(req, timeout=self.timeout) as r:
            sid = r.headers.get("Mcp-Session-Id", "")
            if sid:
                self.sid = sid
            body = r.read().decode() or "{}"
        try:
            return json.loads(body)
        except Exception as exc:  # noqa: BLE001
            raise MCPError("响应不是 JSON：%r / %s" % (body[:200], exc))

    def _init(self) -> None:
        res = self._post({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                          "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                                     "clientInfo": {"name": "regression-suite", "version": "1"}}})
        if "error" in res:
            raise MCPError("initialize 失败：%s" % res["error"])

    # ── 调用 ───────────────────────────────────────────────────────
    def call_raw(self, name: str, args: dict | None = None, rid: int = 2) -> dict:
        return self._post({"jsonrpc": "2.0", "id": rid, "method": "tools/call",
                           "params": {"name": name, "arguments": args or {}}})

    def call(self, name: str, args: dict | None = None) -> dict:
        """调用并解包：返回工具的业务结果 dict（解析 content[0].text 的 JSON）✓。"""
        res = self.call_raw(name, args)
        if "error" in res:
            raise MCPError("%s 调用失败：%s" % (name, res["error"]))
        content = ((res.get("result") or {}).get("content") or [])
        for c in content:
            if c.get("type") == "text":
                try:
                    return json.loads(c.get("text") or "{}")
                except Exception:  # noqa: BLE001
                    return {"_text": c.get("text")}
        return {}

    # ── 便捷 ───────────────────────────────────────────────────────
    def tools(self) -> list:
        res = self._post({"jsonrpc": "2.0", "id": 3, "method": "tools/list", "params": {}})
        return [t.get("name") for t in ((res.get("result") or {}).get("tools") or [])]

    def available(self) -> bool:
        try:
            return bool(self.tools())
        except Exception:  # noqa: BLE001
            return False
