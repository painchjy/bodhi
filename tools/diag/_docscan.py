"""文档现状扫描（收尾用）：体量 / 小节 / 过期口径 / 残留配置。

用法：/opt/bodhi-venv/bin/python3 tools/diag/_docscan.py
"""
import pathlib
import re

REPO = pathlib.Path(__file__).resolve().parents[2]


def sizes(pattern):
    out = []
    for p in sorted(REPO.glob(pattern), key=lambda x: -x.stat().st_size):
        out.append("%8d  %s" % (p.stat().st_size, str(p.relative_to(REPO)).replace("\\", "/")))
    return out


print("=== docs/*.md（按体量） ===")
for line in sizes("docs/*.md")[:22]:
    print(" ", line)
print("\n=== docs/cases/*.md ===")
for line in sizes("docs/cases/*/*.md")[:10]:
    print(" ", line)
print("\n=== deploy/delivery/*.md ===")
for line in sizes("deploy/delivery/*.md"):
    print(" ", line)

perf = REPO / "docs" / "knowledge-save-perf-and-staged-modeling.md"
print("\n=== perf 文档小节 ===")
for i, ln in enumerate(perf.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
    if re.match(r"^#{1,3} ", ln):
        print("  %4d  %s" % (i, ln[:96]))
print("  总行数：%d" % len(perf.read_text(encoding="utf-8", errors="replace").splitlines()))

con = REPO / "docs" / "knowledge-write-contract.md"
print("\n=== 契约 §8 写序现状 ===")
lines = con.read_text(encoding="utf-8", errors="replace").splitlines()
for i, ln in enumerate(lines, 1):
    if "落库顺序" in ln or "图本优先" in ln:
        for j in range(max(0, i - 1), min(len(lines), i + 7)):
            print("  %4d  %s" % (j + 1, lines[j][:110]))
        print("  ---")

print("\n=== 过期口径残留 ===")
for f, pats in (("tools/ke-core/ke_pages.py", ["默认.*关闭", "待 P0-c", "BODHI_SAVE_ENGINE"]),
                ("tools/ontology-mcp/server.py", ["BODHI_SAVE_ENGINE"]),
                (".env", ["BODHI_SAVE_ENGINE"])):
    p = REPO / f
    if not p.is_file():
        continue
    for i, ln in enumerate(p.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
        for pat in pats:
            if re.search(pat, ln):
                print("  %-34s:%d  %s" % (f, i, ln.strip()[:100]))
