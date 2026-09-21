"""刷新设计页的派生小节（确定性渲染，不调 LLM）。

用途：
1. `## CRUD 矩阵`（服务详细设计）是**跨页聚合**得出的，落库时自动刷一遍；
   但历史页/手工改过关系行的页需要一次性重刷。
2. `--overview`：把所有服务的详设聚合渲染成**一页评审总览**
   （服务一览 / 业务属性与键 / 跨服务读依赖 / 操作明细 + 巡检结论），写进
   `ea/summary/it服务详细设计总览`（同 slug 复用 + 正文整体替换，可反复跑）。

用法
----
    python3 tools/ontology-mcp/refresh_design.py --kb <kb_id|名称> [--slug <服务页 slug> ...]
    python3 tools/ontology-mcp/refresh_design.py --kb 企业知识 --all-services
    python3 tools/ontology-mcp/refresh_design.py --kb 企业知识 --overview [--dry-run]

只改**这些页**的派生小节（版本快照 + 反向边重算由 ke_pages 负责），不动其它页。
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
    ap.add_argument("--overview", action="store_true",
                    help="生成/刷新「IT 服务详细设计总览」页（聚合所有服务）")
    ap.add_argument("--overview-slug", default="", help="总览页 slug（默认 %s）" % server.OVERVIEW_SLUG)
    ap.add_argument("--overview-title", default="", help="总览页标题（默认 %s）" % server.OVERVIEW_TITLE)
    ap.add_argument("--dry-run", action="store_true", help="只看会刷哪些，不写库")
    args = ap.parse_args()

    kb_id, note = server.resolve_kb_id(args.kb)
    if args.overview:
        data = server.service_design_summary(kb_id)  # 只聚合一次，渲染与统计共用
        lines = server.service_overview_lines(kb_id, True, data)
        print("== 知识库 %s%s 总览：%d 行 markdown" % (kb_id, note, len(lines)))
        if not lines:
            print("（没有已详设的服务；先做服务详细设计再跑）")
            return 1
        print("   服务 %d 个 / 操作 %d 个 / 属性 %d 个 / 跨服务依赖 %d 条"
              % (data["service_count"],
                 sum(len(s["model"]["operations"]) for s in data["services"].values()),
                 len(data["attributes"]), len(data["deps"])))
        if args.dry_run:
            print("（--dry-run，未写入）")
            for line in lines[:40]:
                print("   │ %s" % line)
            return 0
        res = server.refresh_service_overview(kb_id, args.overview_slug, args.overview_title)
        print("== 已写入：%s" % json.dumps(res, ensure_ascii=False)[:400])
        return 0

    targets = list(args.slug)
    if args.all_services or not targets:
        targets += service_slugs(kb_id)
    targets = sorted(set(targets))
    print("== 知识库 %s%s 目标 %d 页" % (kb_id, note, len(targets)))
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
