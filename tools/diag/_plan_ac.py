"""把 (A) 删相似度合并 + (C) rebuild 工具 需要的所有位置/原文一次性导出。

用法：/opt/bodhi-venv/bin/python3 tools/diag/_plan_ac.py
"""
import pathlib
import re

REPO = pathlib.Path(__file__).resolve().parents[2]
SRV = REPO / "tools" / "ontology-mcp" / "server.py"
KG = REPO / "tools" / "ke-core" / "ke_graph.py"
srv = SRV.read_text(encoding="utf-8").splitlines()

print("=== ① server.py 目标函数行号 ===")
for pat in ("def pick_match", "def score_element", "def merge_content", "def build_pending_page",
            "def same_type_family", "def normalize_name", "def resolve_pending_merge",
            "def list_pending_merges", "def save_elements", "def call_tool",
            "DEFAULT_HIGH", "TYPE_PENDING", "PENDING_PREFIX"):
    hits = [i + 1 for i, ln in enumerate(srv) if pat in ln]
    print("  %-28s %s" % (pat, hits))

print("\n=== ② merge / pending 分支（save_elements 内）===")
for i, ln in enumerate(srv, 1):
    if re.search(r"candidate is not None and sim >= high|merge_on|build_pending_page\(|"
                 r"summary\[\"pending\"\]\.append|exact = |pick_match\(", ln):
        print("  %5d  %s" % (i, ln.rstrip()[:120]))

print("\n=== ③ 两个工具的 tool 定义与 dispatch ===")
for i, ln in enumerate(srv, 1):
    if re.search(r'"name": "(list_pending_merges|resolve_pending_merge)"|'
                 r'if name == "(list_pending_merges|resolve_pending_merge)"', ln):
        for j in range(max(0, i - 1), min(len(srv), i + 8)):
            print("  %5d  %s" % (j + 1, srv[j].rstrip()[:130]))
        print("  ---")

print("\n=== ④ ke_graph.rebuild_kb_wiki 全文 ===")
kg = KG.read_text(encoding="utf-8").splitlines()
start = next((i for i, ln in enumerate(kg) if re.match(r"def rebuild_kb_wiki\s*\(", ln)), None)
if start is not None:
    end = start + 1
    while end < len(kg) and not re.match(r"^(def |class )", kg[end]):
        end += 1
    for j in range(start, end):
        print("  %5d  %s" % (j + 1, kg[j].rstrip()[:130]))

print("\n=== ⑤ 两个工具名 / 工具数在其他文件里的位置 ===")
for p in sorted(REPO.rglob("*")):
    s = str(p).replace("\\", "/")
    if not p.is_file() or p.suffix.lower() not in (".md", ".sql", ".py", ".yaml", ".yml", ".sh"):
        continue
    if any(x in s for x in ("/.git/", "__pycache__", "/dist-delivery/", "/docs/archive/",
                            "/tools/diag/")):
        continue
    try:
        txt = p.read_text(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        continue
    for i, ln in enumerate(txt.splitlines(), 1):
        if re.search(r"list_pending_merges|resolve_pending_merge", ln):
            print("  TOOL %-52s:%d  %s" % (s, i, ln.strip()[:110]))
        elif re.search(r"38\s*个|38 个", ln):
            print("  CNT  %-52s:%d  %s" % (s, i, ln.strip()[:110]))
