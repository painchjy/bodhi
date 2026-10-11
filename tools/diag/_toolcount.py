"""打印 server.py 当前 MCP 工具清单与数量（用于 38→37 核对）。

用法：/opt/bodhi-venv/bin/python3 tools/diag/_toolcount.py
"""
import sys

sys.path.insert(0, "tools/ontology-mcp")
import server  # noqa: E402

td = server.tool_definitions()
names = sorted(t["name"] for t in td)
print("tools =", len(names))
print(", ".join(names))
for bad in ("list_pending_merges", "resolve_pending_merge"):
    print("  %-24s %s" % (bad, "仍在" if bad in names else "已移除"))
print("  rebuild_wiki_from_graph", "在" if "rebuild_wiki_from_graph" in names else "缺")
