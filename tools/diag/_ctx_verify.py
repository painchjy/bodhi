"""验证：前端那两条告警的计数是否已按"活库"收敛（修 ke_context 后）。

调用 MCP `context_page(kb_id, slug)`，打印 peers 的上下文与 warnings。
"""
import json
import sys
import urllib.request

MCP = "http://127.0.0.1:8765/mcp"
KB = sys.argv[1] if len(sys.argv) > 1 else "7efad3eb-762a-4981-9164-cd3b3a226bd4"
SLUG = sys.argv[2] if len(sys.argv) > 2 else "bmm/operativebusinessrule/合理性行动方案合理性"


def call(name, args, rid=1):
    body = {"jsonrpc": "2.0", "id": rid, "method": "tools/call",
            "params": {"name": name, "arguments": args}}
    req = urllib.request.Request(MCP, data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json",
                                          "Accept": "application/json, text/event-stream"})
    with urllib.request.urlopen(req, timeout=120) as r:
        raw = r.read().decode("utf-8", "replace")
    if raw.lstrip().startswith("event:") or "\ndata:" in raw:
        for ln in raw.splitlines():
            if ln.startswith("data:"):
                return json.loads(ln[5:].strip())
    return json.loads(raw)


out = call("context_page", {"kb_id": KB, "slug": SLUG})
res = out.get("result", out)
try:
    d = json.loads("".join(c.get("text", "") for c in res["content"]))
except Exception:  # noqa: BLE001
    d = res
peers = d.get("peers") or []
dom = [p for p in peers if p.get("role") == "domain"]
print("slug = %s" % SLUG)
print("KB   = %s" % KB)
print("peers 共 %d 个；其中 domain %d 个" % (len(peers), len(dom)))
for p in dom:
    print("   · context=%-14s kb_name=%-12s slug=%s" % (p.get("context"), (p.get("kb_name") or "（空名）"), p.get("slug")))
for w in (d.get("warnings") or []):
    print("⚠️ %s: %s" % (w.get("kind"), str(w.get("detail"))[:120]))
