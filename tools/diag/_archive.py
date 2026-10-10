"""把"历史讨论/一次性稿"归档到 docs/archive/（并 git add 新旧路径）。

用法：/opt/bodhi-venv/bin/python3 tools/diag/_archive.py [--commit]
"""
import pathlib
import subprocess
import sys

REPO = pathlib.Path(__file__).resolve().parents[2]
ARC = REPO / "docs" / "archive"
CANDS = ["handoff-ontology-upload.md", "session-handoff.md", "bodhi-doc-cleanup.md",
         "structured-data-import-plan.md", "ontology-rebuild-analysis.md",
         "ontology-refactor-ea-bmm.md", "ontology-modeling-recommendation.md",
         "knowledge-ops-strategy.md", "bodhi-ops-audit.md"]
ARC.mkdir(exist_ok=True)
moved = []
for name in CANDS:
    src, dst = REPO / "docs" / name, ARC / name
    if src.is_file():
        dst.write_bytes(src.read_bytes())
        src.unlink()
        moved.append(name)
        print("  归档 %s" % name)
print("归档 %d 个；docs/ 剩 %d 个 .md，archive/ 有 %d 个"
      % (len(moved), len(list((REPO / "docs").glob("*.md"))), len(list(ARC.glob("*")))))
paths = [str(ARC.relative_to(REPO))] + ["docs/" + n for n in moved]
subprocess.run(["git", "add", "-A", "--"] + paths, cwd=REPO, check=False)
print("已 git add：", subprocess.run(["git", "diff", "--cached", "--name-status"],
                                     cwd=REPO, capture_output=True, text=True).stdout.strip() or "（无）")
if "--commit" in sys.argv:
    subprocess.run(["git", "commit", "-q", "-m",
                    "docs: 归档历史讨论/一次性稿到 docs/archive（9 份：handoff/session-handoff/"
                    "doc-cleanup/structured-import-plan/ontology-rebuild-analysis/"
                    "ontology-refactor-ea-bmm/ontology-modeling-recommendation/"
                    "knowledge-ops-strategy/bodhi-ops-audit）"], cwd=REPO, check=False)
    print(subprocess.run(["git", "log", "--oneline", "-1"], cwd=REPO,
                         capture_output=True, text=True).stdout)
