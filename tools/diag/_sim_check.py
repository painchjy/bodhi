"""验证 S1/S2 相似知识检查（只读）。用法：python3 tools/diag/_sim_check.py [kb_id]"""
import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "tools" / "ke-core"))
import ke_audit  # noqa: E402

kb = sys.argv[1] if len(sys.argv) > 1 else "af656ef0-e352-4db8-9636-10652dcb95fb"
r = ke_audit.audit(kb, scope="similar", max_findings=6)
print("SCOPES =", ke_audit.SCOPES)
print("S1 疑似重复对 =", r["summary"]["checks"].get("S1", 0),
      "／ S2 域外同名 =", r["summary"]["checks"].get("S2", 0))
print("data =", {k: v for k, v in r["data"].items() if "similar" in k or "cross_kb" in k})
for f in r["findings"][:5]:
    print("  · [%s] %s | %s" % (f["check"], str(f["subject"])[:46], str(f["detail"])[:96]))
print("（scope=all 的 summary.checks 也会含 S1/S2）")
