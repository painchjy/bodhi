"""取 audit_scan 明细（改本体后确认 3 个新类没引入问题）。"""
import json
import urllib.request

ONT = "08810cbd-af86-48d1-bd25-3b2c338e3d68"


def call(method, params, rid=1):
    body = {"jsonrpc": "2.0", "id": rid, "method": method, "params": params}
    req = urllib.request.Request("http://127.0.0.1:8765/mcp", data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json",
                                          "Accept": "application/json, text/event-stream"})
    with urllib.request.urlopen(req, timeout=300) as r:
        raw = r.read().decode("utf-8", "replace")
    if raw.lstrip().startswith("event:") or "\ndata:" in raw:
        for ln in raw.splitlines():
            if ln.startswith("data:"):
                return json.loads(ln[5:].strip())
    return json.loads(raw)


res = call("tools/call", {"name": "audit_scan", "arguments": {"kb_id": ONT, "scope": "all"}})
out = res.get("result", res)
try:
    data = json.loads("".join(c.get("text", "") for c in out["content"]))
except Exception:  # noqa: BLE001
    data = out
summ = (data or {}).get("summary") or {}
print("checks =", summ.get("checks"), "| findings =", summ.get("findings"))
print("RAW findings:", json.dumps(((data or {}).get("findings") or [])[:3], ensure_ascii=False)[:1800])
print("RAW keys:", list((data or {}).keys()))
