"""验证：巡检里报"孤儿图节点"，且 `audit_purge(kinds=orphan_graph_nodes)` 能一步清（dry_run 只读）。

用法：/opt/bodhi-venv/bin/python3 _audit_orphan.py [kb_id]
"""
import json
import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "tools" / "ke-core"))
import ke_audit  # noqa: E402

kb = sys.argv[1].strip() if len(sys.argv) > 1 else "af656ef0-e352-4db8-9636-10652dcb95fb"

r = ke_audit.audit(kb, scope="all", max_findings=1)
print("① audit_scan → summary.orphan_graph_nodes =", r["summary"].get("orphan_graph_nodes"))
print("   graph_orphans =", json.dumps(r.get("graph_orphans"), ensure_ascii=False)[:420])

d = ke_audit.purge(kb, kinds="orphan_graph_nodes", dry_run=True, tenant=10000)
print("\n② audit_purge(kinds=orphan_graph_nodes, dry_run=True) →",
      "mode=%s" % d.get("mode"), "orphan_nodes=%s" % (d.get("orphan_graph_nodes") or {}).get("orphan_nodes"))
print("   回执 =", json.dumps({k: v for k, v in d.items() if k != "orphan_graph_nodes"},
                              ensure_ascii=False)[:300])
