#!/usr/bin/env python3
"""bodhi2 MCP 服务自检（只读）：握手 → tools/list → skills()。

用法：
    python3 selfcheck.py [--url http://127.0.0.1:8765/mcp] [--expect-tools 10]

只依赖标准库；不写任何数据。退出码：0 全通，1 有失败项。
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request

PROTOCOL = "2024-11-05"
EXPECT_SKILLS = {"domain_modeling", "ea_overview_design", "service_detailed_design"}


def rpc(url: str, method: str, params: dict | None, session: str = "", rpc_id: int = 1,
        timeout: int = 60):
    """发一次 JSON-RPC。返回 (payload, 新 session)。兼容 SSE 与纯 JSON 两种响应。"""
    body = json.dumps({"jsonrpc": "2.0", "id": rpc_id, "method": method,
                       "params": params or {}}).encode("utf-8")
    headers = {"Content-Type": "application/json",
               "Accept": "application/json, text/event-stream"}
    if session:
        headers["mcp-session-id"] = session
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        new_session = resp.headers.get("mcp-session-id") or session
        raw = resp.read().decode("utf-8", "replace")
    if raw.lstrip().startswith("event:") or "\ndata:" in raw:
        payloads = [ln[5:].strip() for ln in raw.splitlines() if ln.startswith("data:")]
        raw = payloads[-1] if payloads else "{}"
    try:
        return json.loads(raw or "{}"), new_session
    except json.JSONDecodeError:
        return {"_raw": raw[:400]}, new_session


def main() -> int:
    ap = argparse.ArgumentParser(description="bodhi2 MCP 自检（只读）")
    ap.add_argument("--url", default="http://127.0.0.1:8765/mcp")
    ap.add_argument("--expect-tools", type=int, default=15)
    args = ap.parse_args()
    ok = True

    try:
        init, session = rpc(args.url, "initialize", {
            "protocolVersion": PROTOCOL, "capabilities": {},
            "clientInfo": {"name": "bodhi2-selfcheck", "version": "1.0"}})
        info = (init.get("result") or {}).get("serverInfo") or {}
        print("initialize  OK（session=%s，server=%s %s）"
              % (session[:12] or "-", info.get("name", "?"), info.get("version", "")))
    except Exception as exc:  # noqa: BLE001
        print("initialize  失败：%s（服务没起来 / URL 不对 / 被防火墙拦）" % exc)
        return 1

    try:
        listed, session = rpc(args.url, "tools/list", None, session, rpc_id=2)
        tools = [t.get("name") for t in ((listed.get("result") or {}).get("tools") or [])]
        flag = "OK" if len(tools) == args.expect_tools else "!! 数量不符"
        print("tools/list  %s（%d 个，期望 %d）：%s"
              % (flag, len(tools), args.expect_tools, ", ".join(tools)))
        ok = ok and len(tools) == args.expect_tools
    except Exception as exc:  # noqa: BLE001
        print("tools/list  失败：%s" % exc)
        return 1

    try:
        called, _ = rpc(args.url, "tools/call", {"name": "skills", "arguments": {}},
                        session, rpc_id=3)
        content = (called.get("result") or {}).get("content") or []
        text = " ".join(c.get("text", "") for c in content if isinstance(c, dict))
        payload = {}
        try:
            payload = json.loads(text)
        except Exception:  # noqa: BLE001
            payload = {}
        ids = set()
        catalog = payload.get("catalog")
        if isinstance(catalog, list):                      # 新形态：[{id, name, when, …}, …]
            ids = {i.get("id") for i in catalog if isinstance(i, dict) and i.get("id")}
        if not ids:                                        # 兼容：catalog 是字符串 / 别处藏了清单
            ids = {k for k in EXPECT_SKILLS if k in text}
        missing = EXPECT_SKILLS - ids if ids else EXPECT_SKILLS
        flag = "OK" if not missing else "!! 缺 %s" % ", ".join(sorted(missing))
        print("skills()    %s（返回 %s）" % (flag, ", ".join(sorted(ids)) or "（空）"))
        ok = ok and not missing
    except Exception as exc:  # noqa: BLE001
        print("skills()    失败：%s" % exc)
        ok = False

    print()
    print("结论：%s" % ("全通 ✅" if ok else "有失败项 ❌（对照 ../TROUBLESHOOTING.md 排查）"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
