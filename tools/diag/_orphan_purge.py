"""图侧硬删：**全库**孤儿实例节点（对应知识库已删/软删）——与巡检 kind `orphan_graph_nodes` 同一实现。

用法：/opt/bodhi-venv/bin/python3 tools/diag/_orphan_purge.py [--apply]
"""
import json
import sys

sys.path.insert(0, "tools/ke-core")
import ke_graph  # noqa: E402

res = ke_graph.purge_orphan_kb_nodes(dry_run=("--apply" not in sys.argv))
print(json.dumps(res, ensure_ascii=False)[:1500])
