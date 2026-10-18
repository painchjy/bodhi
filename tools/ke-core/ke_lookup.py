"""knowledge_lookup —— **一个检索入口，三种模式**（只读 ✓）

| mode | 用途 | 收什么 | 出什么 | 取数 |
|---|---|---|---|---|
| `rules` | 取规则契约（**按步骤解析**） | `spec`（direct:/policy:/ask/task:） | 规则契约 + hash ✓ | 图 ✓（复用 `rules_resolve`） |
| `keyword` | 按关键词找知识 | `query` | 命中列表（title/summary/type）+ **来源标注** | **图优先 ✓ → PG 收窄融合 ✓** |
| `path` | 沿关系多跳 | `start`（slug）+ `depth` | 路径（节点链 + 关系链） | 图 ✓ |

设计口径（与 `docs/graph-first-read-contract.md` 一致 ✓）：
- 图先查 ✓（实测 5–15 ms ✓），PG 只做**补字段/收窄** ✓，每条命中都带 `source`
  （`graph` / `graph+pg` / `pg` ✓）⇒ 一旦大量 `pg` ⇒ 说明图缺数据 ✗ ⇒ 跑 W10/rebuild ✓。
"""

import json


def knowledge_lookup(kb_id: str = "", mode: str = "rules", query: str = "",
                     spec: str = "", start: str = "", depth: int = 2,
                     limit: int = 20) -> dict:
    import ke_db as _db
    import ke_review as _rv

    kb, kb_name, note = _db.resolve_kb_id(kb_id)
    m = (mode or "rules").strip().lower()
    base = {"ok": True, "mode": m, "kb": {"id": kb, "name": kb_name}}

    # ── ① rules：按步骤解析规则（唯一规则入口 ✓）────────────────────────
    if m == "rules":
        r = _rv.rules_resolve(kb, spec or query, int(limit) or 300)
        return {**base, "resolved_by": r.get("resolved_by"), "spec": r.get("spec"),
                "rules": r.get("rules"), "counts": r.get("counts"),
                "scope_options": r.get("scope_options"), "hash": r.get("hash"),
                "source": "graph:BodhiInstance"}

    # ── ② keyword：图优先 → PG 收窄融合 ──────────────────────────────
    if m == "keyword":
        q = (query or "").strip()
        if not q:
            raise ValueError("keyword 模式需要 query")
        lim = max(1, int(limit or 20))
        hits: dict = {}
        try:
            import ke_neo4j as _neo
            for x in _neo.query(
                    "MATCH (n:BodhiInstance {kb_id:$kb}) "
                    "WHERE n.slug CONTAINS $q OR coalesce(n.name,'') CONTAINS $q "
                    "RETURN n.slug AS slug, coalesce(n.name,'') AS name, "
                    "       coalesce(n.page_type,'') AS pt LIMIT $lim",
                    {"kb": kb, "q": q, "lim": lim * 2}, timeout=8) or []:
                slug = str(x.get("slug") or "")
                if slug:
                    hits[slug] = {"slug": slug, "title": str(x.get("name") or ""),
                                  "page_type": str(x.get("pt") or ""), "source": "graph"}
        except Exception:  # noqa: BLE001  图不可用 ⇒ 纯 PG ✓
            pass
        try:                                    # PG 收窄：命中标题/正文，且补 summary ✓
            for r in _db.psql_csv(
                    "SELECT slug, COALESCE(title,'') AS t, COALESCE(page_type,'') AS pt, "
                    "       LEFT(COALESCE(summary,''), 160) AS s "
                    "  FROM wiki_pages WHERE knowledge_base_id = %s AND deleted_at IS NULL "
                    "   AND (COALESCE(title,'') ILIKE %s OR slug ILIKE %s) "
                    " ORDER BY updated_at DESC LIMIT %d"
                    % (_db.sql_str(kb), _db.sql_str("%" + q + "%"), _db.sql_str("%" + q + "%"),
                       lim * 2)):
                slug = str(r.get("slug") or "")
                if not slug:
                    continue
                h = hits.get(slug)
                if h:
                    h["summary"] = r.get("s") or ""
                    h["source"] = "graph+pg"
                else:
                    hits[slug] = {"slug": slug, "title": r.get("t") or "",
                                  "page_type": r.get("pt") or "",
                                  "summary": r.get("s") or "", "source": "pg"}
        except Exception:  # noqa: BLE001
            pass
        items = list(hits.values())[:lim]
        src_count: dict = {}
        for h in items:
            src_count[h["source"]] = src_count.get(h["source"], 0) + 1
        return {**base, "query": q, "counts": {"hits": len(items), "by_source": src_count},
                "hits": items,
                "note": ("若 `pg` 占比高 ⇒ 图缺数据 ✗ ⇒ 跑 W10 / rebuild_kb_graph ✓"
                         if src_count.get("pg") else "图命中为主 ✓")}

    # ── ③ path：沿关系多跳（起点必须给 slug ✓）─────────────────────────
    if m == "path":
        s = (start or "").strip()
        if not s:
            raise ValueError("path 模式需要 start（起始 slug）")
        d = max(1, min(int(depth or 2), 4))
        try:
            import ke_neo4j as _neo
            rows = _neo.query(
                "MATCH p=(a:BodhiInstance {kb_id:$kb, slug:$s})-[*1..%d]->(b:BodhiInstance {kb_id:$kb}) "
                "RETURN [x IN nodes(p) | x.slug] AS nodes, "
                "       [r IN relationships(p) | type(r)] AS rels LIMIT $lim" % d,
                {"kb": kb, "s": s, "lim": max(1, int(limit or 20))}, timeout=10)
        except Exception as exc:  # noqa: BLE001
            return {**base, "ok": False, "error": "graph_unavailable", "detail": str(exc)[:200]}
        paths = []
        for r in rows or []:
            nodes = [str(x) for x in (r.get("nodes") or [])]
            rels = [str(x) for x in (r.get("rels") or [])]
            paths.append({"nodes": nodes, "rels": rels, "len": len(rels)})
        paths.sort(key=lambda x: x["len"])
        return {**base, "start": s, "depth": d, "counts": {"paths": len(paths)},
                "paths": paths, "source": "graph:path"}
        # JSON 便于外部复算 hash ✓（调用方可自行序列化）

    raise ValueError("mode 只能是 rules / keyword / path（收到 %r）" % mode)
