"""ke-core · 按「来源文档」统计与清理本体实例（用户 2026-09-20 口径）。

背景
----
WeKnora 删文档时只清**它自己产**的那部分（上游 wiki 页 + 它的 wiki 图），
不会碰我们 fork 的实例层，所以删完文档后「Bodhi 语义图」里仍留着该文档的实例页。
实例层的实际存法（实测 2026-09-20）：
  - PG `wiki_pages`：`source_refs` = 来源知识 id 数组、`chunk_refs` = 片段 id 数组，
    正文「## 原文依据」里还有 `（来源：《文档名》 片段 #N）`（只带标题，带不了 id）；
  - Neo4j `BodhiInstance`：只有 CLI `tools/ontology-extract/extract.py` 那条路径会写
    （属性 `knowledge_id` / `source_doc` / `kb`；当前部署为空）。
`/bodhi/graph`（前端「本体图谱」/`graph_page`）读的就是 PG 这些页，所以清页 = 清图。

能力
----
- `doc_index(kb_id)`   每个来源文档有多少实例页、是活的/已删的/行都没了（只读）；
- `pages_of_doc(...)`  某文档的实例页清单（只读）；
- `purge_document(...)` 按文档清理：**独占页删掉**、多源页只摘掉该文档的引用；
- `orphans(kb_id)` / `sweep(...)`  巡检：把「来源文档已删/不存在」的残留一次清掉。

纪律（与既有约定一致）
----------------------
- 默认 **dry-run**，只有 `apply=True` 才写库；
- 删页复用 `ke_pages.delete_pages`（硬删页 + 版本快照 + 重算 in_links + 目录树 + Neo4j slug 钩子），
  `index` 页永不删；
- 多源页（同一实例被多份文档提到）只摘引用、不删页：`source_refs` 去掉该 id，
  `page_metadata.ontology.doc` 若指向该文档则一并摘掉（`chunk_refs` 无法按文档拆分，保留原样）；
- 正文里的来源行按**标题**写，无法定位到具体 id（同名文档多版本时尤其如此）→ 不动正文，报告里说明。

用法（服务不可用时等价，必须 venv python）
------------------------------------------
    /opt/bodhi-venv/bin/python3 tools/ke-core/ke_docs.py stats  <kb_id>
    /opt/bodhi-venv/bin/python3 tools/ke-core/ke_docs.py pages  <kb_id> <knowledge_id>
    /opt/bodhi-venv/bin/python3 tools/ke-core/ke_docs.py purge  <kb_id> <knowledge_id|标题> [--apply]
    /opt/bodhi-venv/bin/python3 tools/ke-core/ke_docs.py sweep  <kb_id> [--apply]
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import ke_db  # noqa: E402
import ke_pages  # noqa: E402

# source_refs 可能是数组、也可能被上游写成标量（老数据）→ 一律守卫
REFS_ARR = "(CASE WHEN jsonb_typeof(p.source_refs) = 'array' THEN p.source_refs ELSE '[]'::jsonb END)"


def _refs_of_doc_sql() -> str:
    return ("EXISTS (SELECT 1 FROM jsonb_array_elements_text(%s) AS e(v) WHERE e.v = %%s)" % REFS_ARR)


def doc_index(kb_id: str) -> dict:
    """按来源文档聚合（只读）：每份文档的实例页数 / 独占页数 / 类型数 / 状态。

    `status`：`live`（文档在） / `deleted`（文档软删） / `missing`（knowledges 里没有这行，
    通常是被硬删或换了知识库）。
    """
    kb_id, _kb_name, _note = ke_db.resolve_kb_id(kb_id)   # 名称/UUID 都接受；不存在的库报错
    rows = ke_db.psql_csv(
        "SELECT ref.knowledge_id AS knowledge_id, count(*) AS pages, "
        "       count(*) FILTER (WHERE jsonb_array_length(%s) <= 1) AS exclusive_pages, "
        "       count(DISTINCT p.page_type) AS types, "
        "       min(p.created_at)::text AS first_at, max(p.updated_at)::text AS last_at, "
        "       coalesce(max(k.title), '') AS title, "
        "       coalesce(max(k.deleted_at)::text, '') AS deleted_at, "
        "       bool_or(k.id IS NULL) AS missing "
        "  FROM wiki_pages p "
        "  CROSS JOIN LATERAL jsonb_array_elements_text(%s) AS ref(knowledge_id) "
        "  LEFT JOIN knowledges k ON k.id = ref.knowledge_id AND k.knowledge_base_id = p.knowledge_base_id "
        " WHERE p.knowledge_base_id = %s AND p.deleted_at IS NULL "
        " GROUP BY ref.knowledge_id ORDER BY pages DESC, ref.knowledge_id"
        % (REFS_ARR, REFS_ARR, ke_db.sql_str(kb_id)))
    docs = []
    for row in rows:
        if str(row.get("missing")).lower() == "true":
            status = "missing"
        elif (row.get("deleted_at") or "").strip():
            status = "deleted"
        else:
            status = "live"
        docs.append({"knowledge_id": row["knowledge_id"], "title": row["title"],
                     "status": status, "deleted_at": (row.get("deleted_at") or "")[:19],
                     "pages": int(row["pages"] or 0),
                     "exclusive_pages": int(row["exclusive_pages"] or 0),
                     "types": int(row["types"] or 0),
                     "first_at": (row.get("first_at") or "")[:19],
                     "last_at": (row.get("last_at") or "")[:19]})
    no_source = ke_db.psql_csv(
        "SELECT count(*) AS n FROM wiki_pages p WHERE p.knowledge_base_id = %s "
        "AND p.deleted_at IS NULL AND jsonb_array_length(%s) = 0"
        % (ke_db.sql_str(kb_id), REFS_ARR))
    return {"kb_id": kb_id, "docs": docs,
            "totals": {"docs": len(docs),
                       "live": sum(1 for d in docs if d["status"] == "live"),
                       "deleted": sum(1 for d in docs if d["status"] == "deleted"),
                       "missing": sum(1 for d in docs if d["status"] == "missing"),
                       "pages": sum(d["pages"] for d in docs),
                       "pages_without_source": int((no_source or [{"n": 0}])[0]["n"] or 0)}}


def pages_of_doc(kb_id: str, knowledge_id: str) -> list[dict]:
    """该文档引用的实例页清单（只读；带 refs/chunks/meta，供清理与报告用）。"""
    kb_id, _kb_name, _note = ke_db.resolve_kb_id(kb_id)
    return [dict(r) for r in ke_db.psql_csv(
        "SELECT p.slug, p.title, COALESCE(p.page_type,'') AS page_type, COALESCE(p.version,1) AS version, "
        "       COALESCE(p.source_refs::text,'[]') AS refs, COALESCE(p.chunk_refs::text,'[]') AS chunks, "
        "       COALESCE(p.page_metadata::text,'{}') AS meta, "
        "       COALESCE(p.last_edit_source,'') AS src, COALESCE(p.updated_at::text,'') AS updated_at "
        "  FROM wiki_pages p WHERE p.knowledge_base_id = %s AND p.deleted_at IS NULL "
        "   AND %s ORDER BY p.slug" % (ke_db.sql_str(kb_id), _refs_of_doc_sql() % ke_db.sql_str(knowledge_id)))]


def _doc_row(kb_id: str, knowledge_id: str) -> dict:
    rows = ke_db.psql_csv(
        "SELECT id, COALESCE(title,'') AS title, COALESCE(deleted_at::text,'') AS deleted_at "
        "  FROM knowledges WHERE id = %s AND knowledge_base_id = %s"
        % (ke_db.sql_str(knowledge_id), ke_db.sql_str(kb_id)))
    if not rows:
        return {"id": knowledge_id, "title": "", "deleted_at": "", "status": "missing"}
    row = rows[0]
    return {"id": knowledge_id, "title": row["title"], "deleted_at": (row["deleted_at"] or "")[:19],
            "status": "deleted" if (row["deleted_at"] or "").strip() else "live"}


def _strip_doc_refs(kb_id: str, slug: str, keep_refs: list, drop_ids: list) -> None:
    """多源页：把这些文档从 source_refs 摘掉（`page_metadata.ontology.doc` 指向它们时一并摘）。

    不动正文：正文来源行只写标题（同名文档多版本时定位不到 id），改了反而可能误伤别的来源。
    """
    ids = [str(x) for x in (drop_ids or []) if str(x)]
    if not ids:
        return
    cond = ", ".join(ke_db.sql_str(x) for x in ids)
    ke_db.psql(
        "UPDATE wiki_pages SET source_refs = %s::jsonb, "
        "  page_metadata = CASE WHEN COALESCE(page_metadata->'ontology'->'doc'->>'id','') IN (%s) "
        "    THEN COALESCE(page_metadata, '{}'::jsonb) #- '{ontology,doc}' "
        "    ELSE COALESCE(page_metadata, '{}'::jsonb) END, "
        "  updated_at = now() "
        " WHERE knowledge_base_id = %s AND slug = %s AND deleted_at IS NULL;"
        % (ke_db.sql_json(sorted(set(str(r) for r in keep_refs))), cond,
           ke_db.sql_str(kb_id), ke_db.sql_str(slug)))


def neo4j_purge(knowledge_id: str) -> dict:
    """清实例层图数据（当前部署 `BodhiInstance` 为空，留着是为了 CLI 那条路径也行为一致）。"""
    out: dict = {"nodes": 0, "edges": 0}
    try:
        import ke_neo4j  # noqa: PLC0415
    except Exception as exc:  # noqa: BLE001
        out["skipped"] = "ke_neo4j 不可用：%s" % exc
        return out
    try:
        rows = ke_neo4j.query(
            "MATCH ()-[r]->() WHERE r.knowledge_id = $k OR r.source_doc = $k RETURN count(r) AS n",
            {"k": knowledge_id})
        out["edges"] = int((rows or [{"n": 0}])[0].get("n") or 0)
        if out["edges"]:
            ke_neo4j.query(
                "MATCH ()-[r]->() WHERE r.knowledge_id = $k OR r.source_doc = $k DELETE r",
                {"k": knowledge_id})
        rows = ke_neo4j.query(
            "MATCH (n:BodhiInstance) WHERE n.knowledge_id = $k OR n.source_doc = $k "
            "OR $k IN coalesce(n.source_docs, []) RETURN count(n) AS n", {"k": knowledge_id})
        out["nodes"] = int((rows or [{"n": 0}])[0].get("n") or 0)
        if out["nodes"]:
            ke_neo4j.query(
                "MATCH (n:BodhiInstance) WHERE n.knowledge_id = $k OR n.source_doc = $k "
                "OR $k IN coalesce(n.source_docs, []) DETACH DELETE n", {"k": knowledge_id})
    except Exception as exc:  # noqa: BLE001
        out["error"] = str(exc)
    return out


def purge_document(kb_id: str, knowledge_id: str = "", title: str = "", apply: bool = False,
                   sync_folders: bool = True, delete_exclusive: bool = True) -> dict:
    """按来源文档清理实例页：**独占页删掉**、多源页只摘引用（默认 dry-run）。

    `knowledge_id` 与 `title` 二选一；按标题给时，匹配到的（同名多版本）文档都处理。
    `delete_exclusive=False` = 只摘引用不删页（保守模式：图里不再归属该文档，但页还在）。
    """
    kb_id = (kb_id or "").strip()
    if not kb_id:
        raise ValueError("purge_document 需要 kb_id")
    kb_id, _kb_name, _note = ke_db.resolve_kb_id(kb_id)
    if not knowledge_id and not title:
        raise ValueError("purge_document 需要 knowledge_id 或 title")
    if knowledge_id:
        ids = [knowledge_id.strip()]
    else:
        want = (title or "").strip()
        index = doc_index(kb_id)["docs"]
        ids = [d["knowledge_id"] for d in index if d["title"] == want]
        if not ids:
            ids = [d["knowledge_id"] for d in index if want and want in d["title"]]
        if not ids:
            raise ValueError("该标题下没有实例页：《%s》" % want)

    plan: dict = {"kb_id": kb_id, "apply": bool(apply), "docs": []}
    for kid in ids:
        meta = _doc_row(kb_id, kid)
        pages = pages_of_doc(kb_id, kid)
        to_delete, to_strip = [], []
        for page in pages:
            try:
                refs = json.loads(page.get("refs") or "[]")
            except json.JSONDecodeError:
                refs = []
            if not isinstance(refs, list):
                refs = []
            keep = [r for r in refs if str(r) != kid]
            (to_strip if keep else to_delete).append({"slug": page["slug"], "keep": keep})
        item = {"knowledge_id": kid, "title": meta["title"], "status": meta["status"],
                "deleted_at": meta["deleted_at"], "pages_found": len(pages),
                "to_delete": len(to_delete), "to_strip": len(to_strip),
                "delete_slugs": [x["slug"] for x in to_delete][:20],
                "strip_slugs": [x["slug"] for x in to_strip][:20]}
        if apply:
            if to_delete and delete_exclusive:
                item["delete"] = ke_pages.delete_pages(
                    kb_id, [x["slug"] for x in to_delete], sync_folders=sync_folders)
            elif to_delete:
                item["kept_pages"] = len(to_delete)      # 保守模式：独占页也留着，只摘引用
                for x in to_delete:
                    to_strip.append({"slug": x["slug"], "keep": []})
            for x in to_strip:
                _strip_doc_refs(kb_id, x["slug"], x["keep"], [kid])
            item["stripped"] = len(to_strip)
            item["neo4j"] = neo4j_purge(kid)
        plan["docs"].append(item)
    plan["totals"] = {"docs": len(plan["docs"]),
                      "pages_found": sum(d["pages_found"] for d in plan["docs"]),
                      "to_delete": sum(d["to_delete"] for d in plan["docs"]),
                      "to_strip": sum(d["to_strip"] for d in plan["docs"])}
    return plan


def orphans(kb_id: str, include_missing: bool = True) -> dict:
    """巡检（只读）：实例页的来源文档**已删 / 行都不在了** —— 就是 WeKnora 删文档后的残留。

    `pages_without_source` 是没有任何 source_refs 的页（老数据 / 上游页）→ 归不了档，
    只报数不清理。
    """
    index = doc_index(kb_id)
    wanted = [d for d in index["docs"]
              if d["status"] == "deleted" or (include_missing and d["status"] == "missing")]
    return {"kb_id": kb_id, "orphans": wanted,
            "totals": {"docs": len(wanted), "pages": sum(d["pages"] for d in wanted),
                       "pages_exclusive": sum(d["exclusive_pages"] for d in wanted),
                       "live_docs": index["totals"]["live"],
                       "pages_without_source": index["totals"]["pages_without_source"]}}


def residue(kb_id: str, include_missing: bool = True) -> dict:
    """「来源文档已删/不存在」的**残留净效果**（一趟去重，供 sweep 与巡检清理共用）。

    - `will_delete`：引用集**全都**在孤儿文档里的页（该删）
    - `will_strip`：还有活来源的页 → `{slug: [保留的 refs]}`
    - `docs`：孤儿文档清单
    """
    rep = orphans(kb_id, include_missing=include_missing)
    refs_by_slug: dict = {}
    for doc in rep["orphans"]:
        for page in pages_of_doc(kb_id, doc["knowledge_id"]):
            try:
                refs = set(json.loads(page.get("refs") or "[]"))
            except json.JSONDecodeError:
                refs = set()
            refs_by_slug.setdefault(page["slug"], set()).update(refs)
    orphan_ids = sorted({d["knowledge_id"] for d in rep["orphans"]})
    oset = set(orphan_ids)
    will_delete = sorted(s for s, r in refs_by_slug.items() if r <= oset)
    will_strip = {s: sorted(r) for s, r in refs_by_slug.items() if r - oset}
    return {"kb_id": kb_id, "docs": rep["orphans"], "doc_ids": orphan_ids,
            "pages_distinct": len(refs_by_slug), "will_delete": will_delete,
            "will_strip": will_strip}


def sweep(kb_id: str, apply: bool = False, include_missing: bool = True,
          delete_exclusive: bool = True) -> dict:
    """把所有「来源文档已删/不存在」的残留一次清掉（人工兜底的「联动清理」）。

    已删文档的**独占页**会被删、多源页只摘引用；`index` 与无来源页不受影响。
    `delete_exclusive=False` = 只摘引用不删页。
    执行是**一趟、去重**的：先算出净效果（同一页被多份已删文档引用只算一次），
    再一次性删页 + 一次性摘引用 + 逐文档清 Neo4j，`index` 页永不删。
    """
    rep = orphans(kb_id, include_missing=include_missing)
    out: dict = {"kb_id": kb_id, "apply": bool(apply),
                 "hint": "dry-run：加 --apply（CLI）或 apply=true（HTTP）才会真删",
                 "docs": len(rep["orphans"]), "documents": []}
    for d in rep["orphans"]:
        out["documents"].append(purge_document(kb_id, knowledge_id=d["knowledge_id"], apply=False))
    out["totals"] = {
        "docs": sum(x["totals"]["docs"] for x in out["documents"]),
        "pages_found": sum(x["totals"]["pages_found"] for x in out["documents"]),
        "to_delete": sum(x["totals"]["to_delete"] for x in out["documents"]),
        "to_strip": sum(x["totals"]["to_strip"] for x in out["documents"])}

    # 去重净效果（与巡检清理共用一份实现）
    res = residue(kb_id, include_missing=include_missing)
    orphan_ids = res["doc_ids"]
    will_delete, will_strip = res["will_delete"], res["will_strip"]
    out["net"] = {"pages_distinct": res["pages_distinct"], "will_delete": len(will_delete),
                  "will_strip": len(will_strip), "delete_slugs": will_delete[:20]}

    if apply and (will_delete or will_strip or orphan_ids):
        applied: dict = {"deleted": 0, "stripped": 0, "neo4j": []}
        if will_delete and delete_exclusive:
            res = ke_pages.delete_pages(kb_id, will_delete)
            applied["deleted"] = res.get("deleted", 0)
            applied["delete_result"] = {k: res[k] for k in ("deleted", "neo4j_deleted")
                                       if k in res}
        elif will_delete:
            applied["kept_pages"] = len(will_delete)      # 保守模式：不删页，下面只摘引用
            for slug in will_delete:
                will_strip[slug] = []
        for slug, keep in will_strip.items():
            _strip_doc_refs(kb_id, slug, keep, orphan_ids)
        applied["stripped"] = len(will_strip)
        applied["neo4j"] = [{"knowledge_id": kid, **neo4j_purge(kid)} for kid in orphan_ids]
        out["applied"] = applied
    return out


def kb_ids_with_sources() -> list:
    """有「带来源引用」的实例页的知识库列表（定时巡检用，免得到处硬编码 kb_id）。"""
    rows = ke_db.psql_csv(
        "SELECT DISTINCT p.knowledge_base_id AS kb_id FROM wiki_pages p "
        " WHERE p.deleted_at IS NULL AND jsonb_array_length(%s) > 0 "
        " ORDER BY 1" % REFS_ARR)
    return [r["kb_id"] for r in rows if r.get("kb_id")]


def sweep_all(apply: bool = False, delete_exclusive: bool = True) -> dict:
    """对**所有**有来源引用的知识库跑一遍 sweep（定时巡检入口）。"""
    out: dict = {"apply": bool(apply), "kbs": []}
    for kb_id in kb_ids_with_sources():
        res = sweep(kb_id, apply=apply, delete_exclusive=delete_exclusive)
        out["kbs"].append({"kb_id": kb_id, "net": res.get("net"),
                           "applied": res.get("applied")})
    out["totals"] = {"kbs": len(out["kbs"]),
                     "will_delete": sum((k["net"] or {}).get("will_delete", 0) for k in out["kbs"]),
                     "will_strip": sum((k["net"] or {}).get("will_strip", 0) for k in out["kbs"])}
    return out


def _looks_like_id(text: str) -> bool:
    return len(text) == 36 and text.count("-") == 4


def main() -> int:
    parser = argparse.ArgumentParser(description="按来源文档统计/清理本体实例（Bodhi 语义图）")
    sub = parser.add_subparsers(dest="cmd", required=True)
    for name, help_text in (("stats", "按来源文档统计实例页（只读）"),
                            ("orphans", "来源文档已删/不存在的残留（只读）")):
        cmd = sub.add_parser(name, help=help_text)
        cmd.add_argument("kb_id")
    pages_cmd = sub.add_parser("pages", help="某文档的实例页清单（只读）")
    pages_cmd.add_argument("kb_id")
    pages_cmd.add_argument("knowledge_id")
    purge_cmd = sub.add_parser("purge", help="按文档清理（默认 dry-run）")
    purge_cmd.add_argument("kb_id")
    purge_cmd.add_argument("doc", help="knowledge_id（uuid）或文档标题")
    purge_cmd.add_argument("--by-title", action="store_true", help="把 doc 当标题（同名多版本全清）")
    purge_cmd.add_argument("--apply", action="store_true", help="真的执行（默认只出计划）")
    purge_cmd.add_argument("--strip-only", action="store_true", help="保守模式：只摘引用，不删独占页")
    sweep_cmd = sub.add_parser("sweep", help="清掉所有已删文档的残留（默认 dry-run）")
    sweep_cmd.add_argument("kb_id", nargs="?", default="", help="知识库 UUID；给 --all 时省略")
    sweep_cmd.add_argument("--all", action="store_true", help="对所有有来源引用的知识库各跑一遍")
    sweep_cmd.add_argument("--apply", action="store_true", help="真的执行（默认只出计划）")
    sweep_cmd.add_argument("--strip-only", action="store_true", help="保守模式：只摘引用，不删独占页")
    args = parser.parse_args()

    if args.cmd == "stats":
        out = doc_index(args.kb_id)
    elif args.cmd == "orphans":
        out = orphans(args.kb_id)
    elif args.cmd == "pages":
        out = {"kb_id": args.kb_id, "knowledge_id": args.knowledge_id,
               "pages": [{"slug": p["slug"], "title": p["title"], "page_type": p["page_type"],
                          "src": p["src"], "refs": p["refs"], "updated_at": p["updated_at"][:19]}
                         for p in pages_of_doc(args.kb_id, args.knowledge_id)]}
    elif args.cmd == "purge":
        by_title = args.by_title or not _looks_like_id(args.doc)
        out = purge_document(args.kb_id, knowledge_id="" if by_title else args.doc,
                             title=args.doc if by_title else "", apply=args.apply,
                             delete_exclusive=not args.strip_only)
    else:
        if args.all:
            out = sweep_all(apply=args.apply, delete_exclusive=not args.strip_only)
        elif not args.kb_id:
            raise SystemExit("sweep 需要 kb_id 或 --all")
        else:
            out = sweep(args.kb_id, apply=args.apply, delete_exclusive=not args.strip_only)
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
