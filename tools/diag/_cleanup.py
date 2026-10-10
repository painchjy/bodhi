"""清理 + 引用核验（Python 版，避开 PowerShell 引号/`$` 问题）。

用法：
    /opt/bodhi-venv/bin/python3 tools/diag/_cleanup.py list      # 只看，不动
    /opt/bodhi-venv/bin/python3 tools/diag/_cleanup.py apply     # 清根目录 _*.txt/_*.log（**不动 _*.py**）
"""
import pathlib
import re
import sys

REPO = pathlib.Path(__file__).resolve().parents[2]
MODE = sys.argv[1] if len(sys.argv) > 1 else "list"

print("=== ① 根目录 `_*` 清单 ===")
root = sorted(REPO.glob("_*"))
by_ext: dict = {}
for p in root:
    by_ext.setdefault(p.suffix or "(无后缀)", []).append(p)
for ext, items in sorted(by_ext.items()):
    print("  %-8s %3d 个  例：%s" % (ext, len(items), ", ".join(x.name for x in items[:6])))
print("  _*.py（**不动**）：", ", ".join(x.name for x in by_ext.get(".py", [])))
if MODE == "apply":
    n = 0
    for ext in (".txt", ".log"):
        for p in by_ext.get(ext, []):
            try:
                p.unlink()
                n += 1
            except Exception as exc:  # noqa: BLE001
                print("    !! 删不掉 %s：%s" % (p.name, exc))
    left = len(list(REPO.glob("_*")))
    print("  已删 %d 个（.txt/.log）；剩余 _* = %d" % (n, left))

print("\n=== ② Task1 候选死代码的引用（全仓 tools/**/*.py）===")
NAMES = ["_resolve_page_id", "_page_id_of_slug", "_page_id_owner", "_prior_version",
         "_prime_page_ids", "_prime_prior_versions", "_req_cache", "_reset_request_cache",
         "sql_insert_page", "sql_update_page", "page_id_for", "element_slug",
         "element_page_slug", "build_pending_page", "pick_match", "merge_content",
         "same_type_family", "_expect", "id_strategies"]
files = [p for p in (REPO / "tools").rglob("*.py") if "__pycache__" not in str(p)]
texts = {p: p.read_text(encoding="utf-8", errors="replace") for p in files}
for n in NAMES:
    hits = []
    for p, t in texts.items():
        c = len(re.findall(r"\b%s\b" % re.escape(n), t))
        if c:
            hits.append("%s:%d" % (str(p.relative_to(REPO)).replace("\\", "/").split("/")[-1], c))
    total = sum(int(h.rsplit(":", 1)[1]) for h in hits)
    print("  %-24s 引用 %2d  %s%s" % (n, total, ", ".join(hits[:5]),
                                     "  ← 候选死代码" if total <= 1 else ""))
