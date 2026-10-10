"""清点/清理"没有对应知识库"的图节点（默认 **dry_run 只读**）。

用法：
    /opt/bodhi-venv/bin/python3 _orphan.py            # 只清点（不改图）
    /opt/bodhi-venv/bin/python3 _orphan.py apply      # 真删（用户 2026-10-10 口径）
"""
import json
import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "tools" / "ke-core"))
import ke_graph  # noqa: E402

apply = (len(sys.argv) > 1 and sys.argv[1].strip().lower() in ("apply", "1", "true"))
print(json.dumps(ke_graph.purge_orphan_kb_nodes(dry_run=not apply), ensure_ascii=False, indent=2))
