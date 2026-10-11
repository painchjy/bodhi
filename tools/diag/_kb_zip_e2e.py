"""zip 端到端（走 MCP 工具）：给 zip 路径 → kb_import → 自动 verify。用法：_kb_zip_e2e.py <zip> <新库名>"""
import json
import sys
import urllib.request

MCP = "http://127.0.0.1:8765/mcp"
ZIP, NAME = sys.argv[1], sys.argv[2]


def call(method, params=None, rid=1):
    body = {"jsonrpc": "2.0", "id": rid, "method": method}
    if params is not None:
        body["params"] = params
    req = urllib.request.Request(MCP, data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json",
                                          "Accept": "application/json, text/event-stream"})
    with urllib.request.urlopen(req, timeout=900) as r:
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
    except Exception as exc:  # noqa: BLE001
        print("!! 解析失败：%s" % exc)
        print("!! 原始返回：%s" % json.dumps(res, ensure_ascii=False)[:1500])
        return out


res = payload(call("tools/call", {"name": "kb_import",
                                  "arguments": {"source": ZIP, "new_kb": NAME,
                                                "keep_ids": True, "verify": True}}, 2))
print("ok=%s target=%s" % (res.get("ok"), res.get("target_kb")))
print("counts=%s" % json.dumps(res.get("counts"), ensure_ascii=False))
print("verify_ok=%s  verify=%s" % (res.get("verify_ok"), json.dumps(res.get("verify"), ensure_ascii=False)))
print("note=%s" % res.get("note"))
raise SystemExit(0 if res.get("verify_ok") else 1)
