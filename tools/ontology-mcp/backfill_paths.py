"""把已有页面的一级分类路径补成三级：模型 → 大类 → 类（供 wiki 树三级折叠）。

用法：
    python tools/ontology-mcp/backfill_paths.py --kb-id <UUID> [--dry-run]

说明：
- 只处理「本体要素页」（`page_type` 含 `:`），索引页/待确认页/上游 entity/concept 页不动；
- 幂等：按当前 ontology_index 重算 `category_path` 与 `wiki_path`（slug 不变）；
- 与写入侧 `server.class_category_path()` 完全同源，避免两处硬编码漂移。
"""

from __future__ import annotations

import argparse
import json as _json
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import server  # noqa: E402

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            pass


def main() -> int:
    parser = argparse.ArgumentParser(description="回填三级 category_path")
    parser.add_argument("--kb-id", required=True)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    rows = server.psql_csv(
        "SELECT slug, COALESCE(page_type,'') AS page_type, COALESCE(category_path::text,'[]') AS cat "
        "FROM wiki_pages WHERE knowledge_base_id = %s AND deleted_at IS NULL "
        "AND COALESCE(page_type,'') LIKE '%%:%%' "
        "AND COALESCE(page_type,'') NOT IN (%s)"
        % (server.sql_str(args.kb_id), server.sql_str(server.TYPE_PENDING)))

    statements, changed, skipped = [], 0, 0
    for row in rows:
        new_path = server.class_category_path(row["page_type"])
        if len(new_path) < 2 or not all(new_path):
            skipped += 1
            continue
        try:
            old_path = _json.loads(row["cat"] or "[]")
        except Exception:  # noqa: BLE001
            old_path = []
        if old_path == new_path:
            continue
        statements.append("UPDATE wiki_pages SET category_path = %s, wiki_path = %s "
                          "WHERE knowledge_base_id = %s AND slug = %s;"
                          % (server.sql_json(new_path), server.sql_str("/".join(new_path)),
                             server.sql_str(args.kb_id), server.sql_str(row["slug"])))
        changed += 1

    print("扫描 %d 页：需要更新 %d，跳过（无类信息）%d" % (len(rows), changed, skipped))
    for stmt in statements[:5]:
        print("  样例：%s" % stmt[:180])
    if args.dry_run or not statements:
        print("（--dry-run 或无需更新，未写入）")
        return 0
    server.psql("BEGIN;\n" + "\n".join(statements) + "\nCOMMIT;\n", stdin=True)
    print("已写入 %d 条 category_path 更新" % changed)

    # 打印新的树结构预览（模型 → 大类 → 页数；两级，第三层就是知识页）
    tree = server.psql_csv(
        "SELECT category_path->>0 AS lv1, category_path->>1 AS lv2, count(*) AS n "
        "FROM wiki_pages WHERE knowledge_base_id = %s AND deleted_at IS NULL "
        "AND jsonb_typeof(category_path)='array' AND jsonb_array_length(category_path) >= 2 "
        "GROUP BY 1,2 ORDER BY 1,2" % server.sql_str(args.kb_id))
    print("== 两级树（%d 个大类） ==" % len(tree))
    for row in tree[:40]:
        print("  %s → %s（%s 页）" % (row["lv1"], row["lv2"], row["n"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
