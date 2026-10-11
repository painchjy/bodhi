"""验证 MCP `kb_import`（直接吃 zip + 自动比对）。"""
import json
import urllib.request

MCP = "http://127.0.0.1:8765/mcp"
ZIP = "kb-7efad3eb-20261012-024549.zip"


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
    except Exception:  # noqa: BLE001
        return out


res = payload(call("tools/call", {"name": "kb_import",
                                  "arguments": {"source": ZIP,
                                                "new_kb": "迁移测试-技术方案评审领域-B"}}, 2))
print("ok=%s target=%s" % (res.get("ok"), res.get("target_kb")))
print("counts=%s" % json.dumps(res.get("counts"), ensure_ascii=False))
print("verify_ok=%s" % res.get("verify_ok"))
print("verify=%s" % json.dumps(res.get("verify"), ensure_ascii=False))
print("note=%s" % res.get("note"))
