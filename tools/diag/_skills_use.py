"""查清"技能怎么被用"：MCP skills() 目录 + 各智能体的 allowed_tools 里有没有 skills/执行能力。"""
import json
import sys
import urllib.request

sys.path.insert(0, "tools/ke-core")
import ke_db  # noqa: E402


def call(method, params=None, rid=1):
    body = {"jsonrpc": "2.0", "id": rid, "method": method}
    if params is not None:
        body["params"] = params
    req = urllib.request.Request("http://127.0.0.1:8765/mcp", data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json",
                                          "Accept": "application/json, text/event-stream"})
    with urllib.request.urlopen(req, timeout=60) as r:
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


print("=== ① MCP skills() 目录 ===")
cat = payload(call("tools/call", {"name": "skills", "arguments": {}}))
items = cat.get("catalog") if isinstance(cat, dict) else None
if items is None:
    print("   原始返回：", json.dumps(cat, ensure_ascii=False)[:300])
    items = []
names = [s.get("id") for s in items if isinstance(s, dict)]
print("   count=%s" % (cat.get("count") if isinstance(cat, dict) else "?"))
print("   技能（%d）：%s" % (len(names), ", ".join(names)))
print("   含 kb_transfer ?", "✅ 是" if "kb_transfer" in names else "❌ 否")
for s in items:
    if s.get("id") == "kb_transfer":
        print("     ↳ name=%s" % s.get("name"))
        print("     ↳ when=%s" % (s.get("when") or "")[:120])
        print("     ↳ stages=%s tools=%s" % (s.get("stages"), s.get("tools")))

print("=== ② skills(skill='kb_transfer') 能否取到全文 ===")
one = payload(call("tools/call", {"name": "skills", "arguments": {"skill": "kb_transfer"}}, 2))
txt = json.dumps(one, ensure_ascii=False)
print("   返回长度 %d；首 160 字：%s" % (len(txt), txt[:160]))

print("=== ③ 各智能体的 allowed_tools（有没有 skills / 执行能力）===")
try:
    rows = ke_db.psql_csv(
        "SELECT id, "
        "jsonb_array_length(COALESCE(config->'allowed_tools','[]'::jsonb)) AS n, "
        "(COALESCE(config->'allowed_tools','[]'::jsonb) ? 'mcp_bodhi_ontology_skills') AS has_skills, "
        "(COALESCE(config->'allowed_tools','[]'::jsonb) ? 'bash') AS has_bash, "
        "(COALESCE(config->'allowed_tools','[]'::jsonb) ? 'sandbox') AS has_sandbox "
        "FROM custom_agents WHERE id LIKE 'bodhi%' OR id LIKE 'knowledge%' ORDER BY id")
    for r in rows:
        print("   %-24s 工具数=%-3s skills=%-5s bash=%-5s sandbox=%-5s"
              % (r["id"], r["n"], r["has_skills"], r["has_bash"], r["has_sandbox"]))
except Exception as exc:  # noqa: BLE001
    print("   查 custom_agents 失败：%s" % str(exc)[:120])

print("=== ④ 运维智能体（bodhi-kb-ops）的完整工具面 ===")
try:
    rows = ke_db.psql_csv("SELECT config->'allowed_tools' AS t FROM custom_agents WHERE id='bodhi-kb-ops'")
    if rows:
        print("  ", rows[0]["t"])
except Exception as exc:  # noqa: BLE001
    print("   ", str(exc)[:120])
