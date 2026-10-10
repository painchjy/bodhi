"""核验交付包（02）：本轮改动是否进包 + `tools/diag` 是否被排除 + 校验和。

用法：/opt/bodhi-venv/bin/python3 tools/diag/_pkgcheck.py [version=20261010-2]
"""
import hashlib
import pathlib
import sys
import tarfile
import tempfile

REPO = pathlib.Path(__file__).resolve().parents[2]
VER = sys.argv[1] if len(sys.argv) > 1 else "20261010-2"
ROOT = REPO / "dist-delivery" / ("bodhi2-delivery-%s" % VER)

for p in sorted(ROOT.glob("*.tar.gz")):
    print("%-30s %6.2f MB  sha256=%s" % (p.name, p.stat().st_size / 1048576,
                                         hashlib.sha256(p.read_bytes()).hexdigest()))
sums = ROOT / "SHA256SUMS"
if sums.is_file():
    print("\n" + sums.read_text(encoding="utf-8").strip())

tar = ROOT / "bodhi2-02-mcp-server.tar.gz"
tmp = pathlib.Path(tempfile.mkdtemp())
with tarfile.open(tar) as tf:
    names = tf.getnames()
    tf.extractall(tmp, filter="data")
base = next(x for x in tmp.iterdir() if x.is_dir())
files = [x for x in base.rglob("*") if x.is_file()]
print("\n02 包文件数 =", len(files))
print("tools/diag 是否被打进 02 =", any("tools/diag" in n.replace("\\", "/") for n in names),
      "（应为 False）")
print("\n=== 关键改动是否进包 ===")
CHECKS = {
    "BODHI_SAVE_ENGINE 已删（应为 0）": ("BODHI_SAVE_ENGINE", 0),
    "bulk_write（P0-c-3）": ("bulk_write", 1),
    "spec_from_page（适配器）": ("def spec_from_page", 1),
    "purge_orphan_kb_nodes": ("def purge_orphan_kb_nodes", 1),
    "orphan_graph_nodes（巡检 kind）": ("orphan_graph_nodes", 1),
    "info_from_graph（校验读走图）": ("def info_from_graph", 1),
    "project_page 已删（应为 0）": ("def project_page", 0),
    "sql_insert_page 已删（应为 0）": ("def sql_insert_page", 0),
    "skipped_document_nodes（方案 a）": ("skipped_document_nodes", 1),
}
for label, (pat, want) in CHECKS.items():
    hits = []
    for f in files:
        try:
            if pat in f.read_text(encoding="utf-8", errors="ignore"):
                hits.append(str(f.relative_to(base)).replace("\\", "/"))
        except Exception:  # noqa: BLE001
            pass
    ok = (len(hits) >= 1) if want else (len(hits) == 0)
    print("  %-34s 命中 %d  %s  %s" % (label, len(hits), "OK " if ok else "!! ", ", ".join(hits[:2])))
