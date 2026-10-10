"""收尾侦察：相似度合并相关符号 / 待确认合并两个工具 / _expect / 工具数口径 / docs 归档清单。

用法：/opt/bodhi-venv/bin/python3 tools/diag/_recon.py
"""
import pathlib
import re

REPO = pathlib.Path(__file__).resolve().parents[2]
SYMS = ["merge_on", "pick_match", "score_element", "merge_content", "build_pending_page",
        "resolve_pending_merge", "list_pending_merges", "same_type_only", "same_type_family",
        "BODHI_SIMILARITY_MERGE", "retag_queue", "_expect", "TYPE_PENDING", "PENDING_PREFIX",
        "resolve_pending_merge", "merge_history", "thresholds"]
files = [p for p in (REPO / "tools").rglob("*.py") if "__pycache__" not in str(p)]
texts = {str(p.relative_to(REPO)).replace("\\", "/"): p.read_text(encoding="utf-8", errors="replace")
         for p in files}
print("=== ① 符号引用（tools/**/*.py）===")
for n in dict.fromkeys(SYMS):
    hits = ["%s:%d" % (f.split("/")[-1], len(re.findall(r"\b%s\b" % re.escape(n), t)))
            for f, t in texts.items() if re.search(r"\b%s\b" % re.escape(n), t)]
    print("  %-26s %s" % (n, ", ".join(hits) or "（无）"))

print("\n=== ② 两个「待确认合并」工具的出现位置（全仓 py/md/sql）===")
for n in ("list_pending_merges", "resolve_pending_merge"):
    hits = []
    for p in REPO.rglob("*"):
        s = str(p).replace("\\", "/")
        if not p.is_file() or p.suffix.lower() not in (".py", ".md", ".sql", ".sh", ".yaml", ".yml"):
            continue
        if any(x in s for x in ("/.git/", "__pycache__", "/dist-delivery/", "/archive/")):
            continue
        if n in p.read_text(encoding="utf-8", errors="replace"):
            hits.append(str(p.relative_to(REPO)).replace("\\", "/"))
    print("  %-24s %d  %s" % (n, len(hits), ", ".join(hits[:8])))

print("\n=== ③ 工具数口径（“38 个”等断言）===")
for p in REPO.rglob("*"):
    s = str(p).replace("\\", "/")
    if not p.is_file() or p.suffix.lower() not in (".md", ".py", ".sql", ".sh"):
        continue
    if any(x in s for x in ("/.git/", "__pycache__", "/dist-delivery/", "/archive/")):
        continue
    for i, ln in enumerate(p.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
        if re.search(r"(38|36|37)\s*个|tools/list.{0,20}(38|36|37)|工具.{0,6}(38|36|37)", ln):
            print("  %-46s:%d  %s" % (s, i, ln.strip()[:110]))

print("\n=== ④ docs 归档候选是否已存在 archive/ ===")
arc = REPO / "docs" / "archive"
print("  docs/archive 存在 =", arc.is_dir(),
      "（%d 个文件）" % len(list(arc.glob("*"))) if arc.is_dir() else "")
CANDS = ["handoff-ontology-upload.md", "session-handoff.md", "bodhi-doc-cleanup.md",
         "structured-data-import-plan.md", "ontology-rebuild-analysis.md",
         "ontology-refactor-ea-bmm.md", "ontology-modeling-recommendation.md",
         "knowledge-ops-strategy.md", "bodhi-ops-audit.md"]
for c in CANDS:
    print("  %-42s 存在=%s" % (c, (REPO / "docs" / c).is_file()))
