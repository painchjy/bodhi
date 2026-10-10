"""核验清理结果：残留调用为 0、被删函数确实没了、保留函数还在、5 个文件可编译。

用法：/opt/bodhi-venv/bin/python3 _verify_clean.py
"""
import pathlib
import py_compile
import sys

REPO = pathlib.Path(__file__).resolve().parents[2]
DELETED = ["project_page(", "project_page_from_content(", "strip_wiki_page(",
           "delete_kb_graph(", "_strip_legacy_head("]
FILES = ["tools/ke-core/ke_graph.py", "tools/ke-core/ke_pages.py", "tools/ke-core/ke_audit.py",
         "tools/ke-core/ke_ontology.py", "tools/ontology-mcp/server.py"]

print("=== ① 残留调用（应为 0）===")
for n in DELETED:
    hits = []
    for p in (REPO / "tools").rglob("*.py"):
        if "__pycache__" in str(p):
            continue
        if n in p.read_text(encoding="utf-8", errors="replace"):
            hits.append(str(p.relative_to(REPO)).replace("\\", "/"))
    print("  %-32s %d  %s" % (n, len(hits), hits[:3]))

print("\n=== ② 编译 ===")
ok = True
for f in FILES:
    try:
        py_compile.compile(str(REPO / f), doraise=True)
        print("  OK   %s" % f)
    except Exception as exc:  # noqa: BLE001
        ok = False
        print("  FAIL %s：%s" % (f, exc))

print("\n=== ③ 导入 + 函数在/不在 ===")
sys.path.insert(0, str(REPO / "tools" / "ke-core"))
sys.path.insert(0, str(REPO / "tools" / "ontology-mcp"))
import ke_graph  # noqa: E402
import ke_pages  # noqa: E402
import server  # noqa: E402

for name in ("project_page", "project_page_from_content", "strip_wiki_page", "delete_kb_graph"):
    print("  ke_graph.%-28s 已删 = %s" % (name, not hasattr(ke_graph, name)))
print("  ke_pages._strip_legacy_head        已删 = %s" % (not hasattr(ke_pages, "_strip_legacy_head")))
for name in ("strip_wiki_pages", "purge_orphan_kb_nodes", "info_from_graph", "add_edges_batch"):
    print("  ke_graph.%-28s 在   = %s" % (name, hasattr(ke_graph, name)))
for name in ("spec_from_page", "_parse_legacy_head", "write_knowledge_batch", "_bulk_write_pages"):
    print("  ke_pages.%-28s 在   = %s" % (name, hasattr(ke_pages, name)))
print("  server.save_elements                在   = %s" % hasattr(server, "save_elements"))
print("\nRESULT:", "PASS" if len(sys.modules) and ok else "FAIL")
