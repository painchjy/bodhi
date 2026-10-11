"""(A)+(C) 重启后冒烟：工具数 37 + 新工具可调 + 相似度合并无残留。

用法：/opt/bodhi-venv/bin/python3 tools/diag/_smoke_ac.py
"""
import json
import urllib.request

URL = "http://127.0.0.1:8765/mcp"
ONT_KB = "08810cbd-af86-48d1-bd25-3b2c338e3d68"


def call(method, params=None, rid=1):
    body = {"jsonrpc": "2.0", "id": rid, "method": method}
    if params:
        body["params"] = params
    req = urllib.request.Request(URL, data=json.dumps(body).encode(), headers={
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream"})
    with urllib.request.urlopen(req, timeout=90) as r:
        raw = r.read().decode("utf-8", "replace")
    if raw.lstrip().startswith("event:") or "\ndata:" in raw:
        for ln in raw.splitlines():
            if ln.startswith("data:"):
                return json.loads(ln[5:].strip())
    return json.loads(raw)


tl = call("tools/list", {}, 1)
names = [t["name"] for t in tl["result"]["tools"]]
print("tools =", len(names))
print("  已删工具残留:", [n for n in names if "pending" in n] or "无")
print("  rebuild_wiki_from_graph:", "在" if "rebuild_wiki_from_graph" in names else "缺")
res = call("tools/call", {"name": "rebuild_wiki_from_graph",
                          "arguments": {"kb_id": ONT_KB, "dry_run": True}}, 2)
out = res.get("result", res)
try:
    out = json.loads("".join(c.get("text", "") for c in out["content"]))
except Exception:  # noqa: BLE001
    pass
print("rebuild dry_run:", json.dumps(out, ensure_ascii=False)[:500])
