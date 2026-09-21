"""刷新设计页的派生小节（确定性渲染，不调 LLM）。

用途：`## CRUD 矩阵`（服务详细设计）是**跨页聚合**得出的，落库时自动刷一遍；
但历史页/手工改过关系行的页需要一次性重刷 —— 就是本脚本。

用法
----
    python3 tools/ontology-mcp/refresh_design.py --kb <kb_id|名称> [--slug <服务页 slug> ...]
    python3 tools/ontology-mcp/refresh_design.py --kb 企业知识 --all-services

只改**这些服务页**的派生小节（版本快照 + 反向边重算由 ke_pages 负责），不动其它页。
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
import server  # noqa: E402

for _s in (sys.stdout, sys.stderr):
    if hasattr(_s, "reconfigure"):
        try:
            _s.reconfigure(encoding="utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            pass


def service_slugs(kb_id: str) -> list[str]:
    """库里所有「服务页」的 slug（`ea:Service` 的子类闭包 —— 别用 range 闭包）。"""
    types = sorted(server.service_types())
    if not types:
        return []
    cond = ", ".join(server.sql_str(t) for t in types)
    rows = server.psql_csv(
        "SELECT slug FROM wiki_pages WHERE knowledge_base_id = %s AND deleted_at IS NULL "
        "AND page_type IN (%s) ORDER BY slug" % (server.sql_str(kb_id), cond))
    return [r["slug"] for r in rows]


def main() -> int:
    ap = argparse.ArgumentParser(description="刷新设计页派生小节（CRUD 矩阵）")
    ap.add_argument("--kb", required=True, help="知识库 UUID 或名称")
    ap.add_argument("--slug", action="append", default=[], help="指定服务页 slug（可多次）")
    ap.add_argument("--all-services", action="store_true", help="库里所有服务页")
    ap.add_argument("--dry-run", action="store_true", help="只看会刷哪些，不写库")
    args = ap.parse_args()

    kb_id, kb_name, note = server.resolve_kb_id(args.kb)
    targets = list(args.slug)
    if args.all_services or not targets:
        targets += service_slugs(kb_id)
    targets = sorted(set(targets))
    print("== 知识库 %s（%s）%s 目标 %d 页" % (kb_name, kb_id, note, len(targets)))
    for slug in targets:
        try:
            block = server.crud_matrix_lines(kb_id, slug)
        except Exception as exc:  # noqa: BLE001
            print("   %-44s 跳过：%s" % (slug, str(exc)[:60]))
            continue
        print("   %-44s %s" % (slug, "矩阵 %d 行" % len(block) if block else "无操作/属性，跳过"))
    if args.dry_run:
        print("（--dry-run，未写入）")
        return 0
    res = server.refresh_crud_matrix(kb_id, targets)
    print("== 已刷新：%s" % json.dumps(res, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
