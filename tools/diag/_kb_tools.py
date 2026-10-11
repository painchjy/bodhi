"""验证新增的 kb_export / kb_import 工具与下载端点（端到端）。"""
import json
import sys
import urllib.request

MCP = "http://127.0.0.1:8765/mcp"
SRC_KB = "7efad3eb-762a-4981-9164-cd3b3a226bd4"


def call(method, params=None, rid=1):
    body = {"jsonrpc": "2.0", "id": rid, "method": method}
    if params is not None:
        body["params"] = params
    req = urllib.request.Request(MCP, data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json",
                                          "Accept": "application/json, text/event-stream"})
    with urllib.request.urlopen(req, timeout=600) as r:
        raw = r.read().decode("utf-8", "replace")
    if raw.lstrip().startswith("event:") or "\ndata:" in raw:
        for ln in raw.splitlines():
            if ln.startswith("data:"):
                return json.loads(ln[5:].strip())
    return json.loads(raw)


def payload(res):
    out = res.get("result", res)
    try:
        return json.loads("".join(c.get("text", "") for c in out["content"]))
    except Exception:  # noqa: BLE001
        return out


tl = call("tools/list", {})
names = sorted(t["name"] for t in tl["result"]["tools"])
print("tools =", len(names))
print("  含 kb_export/kb_import ?",
      ("kb_export" in names, "kb_import" in names))

print("=== kb_export（源库）===")
res = payload(call("tools/call", {"name": "kb_export",
                                  "arguments": {"kb_id": SRC_KB}}, 2))
print("  ok=%s counts=%s" % (res.get("ok"), json.dumps(res.get("counts"), ensure_ascii=False)))
print("  zip=%s" % res.get("zip"))
print("  download=%s" % res.get("download"))

dl = res.get("download") or ""
if dl:
    with urllib.request.urlopen("http://127.0.0.1:8765" + dl, timeout=120) as r:
        data = r.read()
    print("=== 下载端点 ===")
    print("  HTTP %s，%d 字节（%.2f MB）" % (r.status, len(data), len(data) / 1048576.0))
    sys.stdout.write("  ZIP 头：%s\n" % (data[:4] == b"PK\x03\x04"))
