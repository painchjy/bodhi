"""知识库迁移（运维）：**导出指定知识库 → 导入到另一个知识库 → 比对一致性**。

覆盖两处真源：
  · **PG**：`knowledge_bases`(1 行) + `wiki_pages` + `wiki_folders` + `wiki_page_revisions` + `wiki_page_issues`
  · **Neo4j**：`BodhiInstance` 节点（按 `kb_id`）+ **库内边**（按「源 slug / 关系类型 / 目标 slug」）

用法
----
    # ① 导出（源库 → 目录）
    python3 tools/ke-core/kb_transfer.py export <kb_id> <out_dir>

    # ② 导入（目录 → 目标库；目标库可现建）
    python3 tools/ke-core/kb_transfer.py import <in_dir> --to <kb_id> [--keep-ids]
    python3 tools/ke-core/kb_transfer.py import <in_dir> --new-kb "测试库名" [--keep-ids]

    # ③ 建空库（测试/接收方用）
    python3 tools/ke-core/kb_transfer.py new-kb "测试库名" [--tenant 10000]

    # ④ 比对（PG + Neo4j）
    python3 tools/ke-core/kb_transfer.py verify <src_kb_id> --to <dst_kb_id>
    python3 tools/ke-core/kb_transfer.py verify <in_dir> --to <dst_kb_id>

导出产物
--------
    kb.json            知识库行
    folders.jsonl / pages.jsonl / revisions.jsonl / issues.jsonl
    graph_nodes.jsonl  BodhiInstance（labels + properties）
    graph_edges.jsonl  边（src_slug / type / dst_slug / properties）
    manifest.json      计数 + 每文件 sha256 + 源库信息 + 工具版本

说明
----
* **默认重编 id**（`--keep-ids` 可保留原 UUID）：同一台机器上导入到新库时，页/目录 UUID 不能重复。
  重编时同步修正 `folder_id`（页→目录）、`parent_id`（目录树）、`page_id`（版本快照）。
* 页的**唯一键是 (knowledge_base_id, slug)**：导入按 slug **先删后插**，可重复执行（幂等）。
* Neo4j 节点键是 **(kb_id, slug)**；边按端点 slug 重建（不使用内部 id）。
* 只用 ke-core 既有能力（`ke_db` / `ke_neo4j`），不引入新依赖。
"""
from __future__ import annotations

import csv
import hashlib
import json
import pathlib
import sys
import uuid

# `ke_db.psql_csv` 用 csv 解析；整表 `json_agg` 会变成**一个巨大字段**，超默认 128 KB 上限 → 抬到 1 GB。
csv.field_size_limit(min(sys.maxsize, 1 << 30))

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import ke_db  # noqa: E402
import ke_neo4j  # noqa: E402

TOOL_VERSION = "1.0.0"
PG_TABLES = ("wiki_folders", "wiki_pages", "wiki_page_revisions", "wiki_page_issues")
JSONL = {"kb": "kb.json", "folders": "folders.jsonl", "pages": "pages.jsonl",
         "revisions": "revisions.jsonl", "issues": "issues.jsonl",
         "graph_nodes": "graph_nodes.jsonl", "graph_edges": "graph_edges.jsonl"}


def q(sql: str) -> list[dict]:
    return ke_db.psql_csv(sql)


def q_json(sql: str) -> list:
    """整表取 JSON（**保留 NULL 与类型**）。"""
    raw = ke_db.psql_csv("SELECT coalesce(json_agg(t)::text, '[]') AS j FROM (%s) t" % sql)[0]["j"]
    return json.loads(raw or "[]")


def sql_lit(v) -> str:
    if v is None:
        return "NULL"
    if isinstance(v, (int, float)):
        return str(v)
    if isinstance(v, (dict, list)):
        return "'%s'::jsonb" % json.dumps(v, ensure_ascii=False).replace("'", "''")
    return "'%s'" % str(v).replace("'", "''")


def sha256_file(p: pathlib.Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def kb_row(kb_id: str) -> dict:
    rows = q_json("SELECT * FROM knowledge_bases WHERE id = %s" % sql_lit(kb_id))
    if not rows:
        raise SystemExit("知识库不存在：%s" % kb_id)
    return rows[0]


def edges_of(kb_id: str) -> list[dict]:
    """库内边：源/类型/目标（都用 slug）+ 边属性。"""
    rows = ke_neo4j.query(
        "MATCH (a:BodhiInstance)-[r]->(b:BodhiInstance) "
        "WHERE a.kb_id = $kb AND b.kb_id = $kb "
        "RETURN a.slug AS src_slug, type(r) AS type, b.slug AS dst_slug, properties(r) AS props",
        {"kb": kb_id})
    return [{"src_slug": r.get("src_slug"), "type": r.get("type"),
             "dst_slug": r.get("dst_slug"), "props": r.get("props") or {}} for r in rows]


def nodes_of(kb_id: str) -> list[dict]:
    rows = ke_neo4j.query(
        "MATCH (n:BodhiInstance) WHERE n.kb_id = $kb "
        "RETURN labels(n) AS labels, properties(n) AS props", {"kb": kb_id})
    return [{"labels": r.get("labels") or [], "props": r.get("props") or {}} for r in rows]



def now_text() -> str:
    from datetime import datetime
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def read_jsonl(p: pathlib.Path) -> list[dict]:
    if not p.is_file():
        return []
    return [json.loads(ln) for ln in p.read_text(encoding="utf-8").splitlines() if ln.strip()]


def write_jsonl(p: pathlib.Path, rows: list[dict]) -> None:
    with p.open("w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def table_columns(table: str) -> list[str]:
    return [r["column_name"] for r in q(
        "SELECT column_name FROM information_schema.columns WHERE table_name='%s'" % table)]


def _col_types(table: str) -> dict[str, str]:
    return {r["column_name"]: r["data_type"] for r in q(
        "SELECT column_name, data_type FROM information_schema.columns WHERE table_name='%s'" % table)}


def _cast(col: str, dtype: str) -> str:
    d = (dtype or "").lower()
    if "json" in d:
        return "t.%s::jsonb" % col
    if d in ("integer", "bigint", "smallint"):
        return "t.%s::%s" % (col, d)
    if d == "boolean":
        return "t.%s::boolean" % col
    if d.startswith("timestamp"):
        return "t.%s::timestamptz" % col
    return "t.%s" % col


def bulk_insert(table: str, rows: list[dict], max_bytes: int = 60000, max_rows: int = 400) -> int:
    """**批量写**：一次多行 `INSERT … SELECT … FROM jsonb_to_recordset(...)`。

    关键约束：`ke_db` 走的是 `docker exec psql -c "<SQL>"`，**单条 SQL 不能超 ~128 KB**（ARG_MAX）
    —— 所以按**字节**切批（正文大时自动变小批），而不是固定行数。
    """
    rows = [r for r in rows if r]
    if not rows:
        return 0
    types = _col_types(table)
    cols = [c for c in table_columns(table) if c in rows[0]]
    sel = ", ".join(_cast(c, types.get(c, "text")) for c in cols)
    defs = ", ".join("%s %s" % (c, types.get(c, "text")) for c in cols)
    done = 0

    def flush(batch: list[dict]) -> None:
        nonlocal done
        payload = json.dumps(batch, ensure_ascii=False)
        q("INSERT INTO %s (%s) SELECT %s FROM jsonb_to_recordset(%s::jsonb) AS t(%s)"
          % (table, ", ".join(cols), sel, sql_lit(payload), defs))
        done += len(batch)

    batch: list[dict] = []
    size = 0
    for r in rows:
        item = {c: r.get(c) for c in cols}
        js = json.dumps(item, ensure_ascii=False)
        if batch and (size + len(js) > max_bytes or len(batch) >= max_rows):
            flush(batch)
            batch, size = [], 0
        batch.append(item)
        size += len(js) + 1
    if batch:
        flush(batch)
    return done


def bulk_delete(table: str, kb_id: str, column: str, values: list, chunk: int = 120) -> None:
    """按 (knowledge_base_id, column IN (…)) **批量删**（幂等重跑用；同样受 ARG_MAX 约束，故取 120）。"""
    vals = [v for v in values if v is not None]
    for i in range(0, len(vals), chunk):
        sub = vals[i:i + chunk]
        q("DELETE FROM %s WHERE knowledge_base_id = %s AND %s IN (%s)"
          % (table, sql_lit(kb_id), column,
             ", ".join(sql_lit(v) for v in sub)))



# ============================================================ ① 导出
def cmd_export(kb_id: str, out_dir: str, make_zip: bool = False) -> dict:
    kb = kb_row(kb_id)
    out = pathlib.Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / JSONL["kb"]).write_text(json.dumps(kb, ensure_ascii=False, indent=2), encoding="utf-8")
    counts = {"kb": 1}
    for key, table in (("folders", "wiki_folders"), ("pages", "wiki_pages"),
                       ("revisions", "wiki_page_revisions"), ("issues", "wiki_page_issues")):
        cols_of = table_columns(table)
        where = "WHERE knowledge_base_id = %s" % sql_lit(kb_id)
        if "deleted_at" in cols_of:            # **只导活行**（与 verify 的统计口径一致）
            where += " AND deleted_at IS NULL"
        rows = q_json("SELECT * FROM %s %s ORDER BY 1" % (table, where))
        write_jsonl(out / JSONL[key], rows)
        counts[key] = len(rows)
    nodes, edges = nodes_of(kb_id), edges_of(kb_id)
    write_jsonl(out / JSONL["graph_nodes"], nodes)
    write_jsonl(out / JSONL["graph_edges"], edges)
    counts["graph_nodes"], counts["graph_edges"] = len(nodes), len(edges)
    manifest = {
        "tool": "kb_transfer", "version": TOOL_VERSION, "generated_at": now_text(),
        "source": {"kb_id": kb_id, "name": kb.get("name"), "tenant_id": kb.get("tenant_id")},
        "counts": counts,
        "sha256": {fname: sha256_file(out / fname) for fname in JSONL.values()
                   if (out / fname).is_file()},
    }
    (out / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2),
                                       encoding="utf-8")
    if make_zip:                                  # 打包成一个 zip（便于上传/下载）
        import shutil as _sh
        z = _sh.make_archive(str(out), "zip", root_dir=str(out.parent), base_dir=out.name)
        manifest["zip"] = z
        print("已打包：%s" % z)
    print("导出完成 → %s" % out)
    for k, v in counts.items():
        print("   %-14s %s" % (k, v))
    return manifest


# ============================================================ ② 建空库
def cmd_new_kb(name: str, tenant: str = "10000") -> str:
    """建一个空知识库（配置字段从现有库拷贝，避免 NOT NULL 列缺默认值）。"""
    src = {}
    rows = q_json("SELECT chunking_config, image_processing_config, cos_config, vlm_config, "
                  "extract_config, wiki_config, faq_config, question_generation_config, "
                  "storage_provider_config, asr_config, auto_tag_config, indexing_strategy, "
                  "storage_backend_id, embedding_model_id, summary_model_id, vector_store_id "
                  "FROM knowledge_bases WHERE deleted_at IS NULL ORDER BY created_at LIMIT 1")
    if rows:
        src = rows[0]
    kid = q("SELECT uuid_generate_v4() AS id")[0]["id"]
    cols = {"id": kid, "name": name, "tenant_id": tenant, "type": "document",
            "description": "kb_transfer 迁移/测试库（%s）" % now_text()}
    cols.update({k: v for k, v in src.items() if k not in cols})
    keys = [c for c in table_columns("knowledge_bases") if c in cols]
    q("INSERT INTO knowledge_bases (%s) VALUES (%s)"
      % (", ".join(keys), ", ".join(sql_lit(cols[k]) for k in keys)))
    print("已新建知识库：%s  （%s）" % (name, kid))
    return kid


def find_kb_by_name(name: str) -> str:
    rows = q_json("SELECT id FROM knowledge_bases WHERE name = %s AND deleted_at IS NULL "
                  "ORDER BY created_at DESC" % sql_lit(name))
    return rows[0]["id"] if rows else ""


def ensure_kb(name: str, tenant: str = "10000") -> str:
    """同名库**复用**（便于反复自测）；不存在才新建。"""
    kid = find_kb_by_name(name)
    if kid:
        print("复用已存在的知识库：%s  （%s）" % (name, kid))
        return kid
    return cmd_new_kb(name, tenant)



# ============================================================ ③ 导入
def _insert_row(table: str, row: dict) -> None:
    keys = [k for k in table_columns(table) if k in row]
    q("INSERT INTO %s (%s) VALUES (%s)"
      % (table, ", ".join(keys), ", ".join(sql_lit(row[k]) for k in keys)))


def cmd_import(in_dir: str, to_kb: str = "", new_kb: str = "", keep_ids: bool = False) -> dict:
    d = pathlib.Path(in_dir)
    if d.is_file() and d.suffix.lower() == ".zip":        # 直接吃 zip（上传/下载场景）
        import tempfile
        import zipfile
        tmp = pathlib.Path(tempfile.mkdtemp(prefix="kb-xfer-"))
        with zipfile.ZipFile(d) as z:
            z.extractall(tmp)
        subs = [x for x in tmp.iterdir() if (x / "manifest.json").is_file()]
        d = subs[0] if subs else tmp
        print("已解包：%s → %s" % (in_dir, d))
    if not (d / "manifest.json").is_file():
        raise SystemExit("不是有效的导出目录/zip（缺 manifest.json）：%s" % d)
    kid = ensure_kb(new_kb) if new_kb else to_kb
    if not kid or not q_json("SELECT id FROM knowledge_bases WHERE id = %s" % sql_lit(kid)):
        raise SystemExit("目标知识库不存在：%s" % kid)
    new_id = (lambda old: old) if keep_ids else (lambda old: str(uuid.uuid4()))
    if keep_ids:            # 同实例迁移会与**源库既有 UUID** 撞主键 → 自动降级为重编 id
        probe = [r["id"] for r in read_jsonl(d / JSONL["folders"])][:500] + \
                [r["id"] for r in read_jsonl(d / JSONL["pages"])][:500]
        if probe:
            hit = q_json("SELECT count(*) AS c FROM wiki_pages WHERE id IN (%s)"
                         % ", ".join(sql_lit(x) for x in probe))[0]["c"]
            hit += q_json("SELECT count(*) AS c FROM wiki_folders WHERE id IN (%s)"
                          % ", ".join(sql_lit(x) for x in probe))[0]["c"]
            if int(hit or 0) > 0:
                print("⚠️ --keep-ids 与库内既有 id 冲突（%d 个，属**同实例**迁移）→ 自动改为重编 id"
                      "（跨实例迁移才应保留原 UUID）" % hit)
                keep_ids = False
                new_id = lambda old: str(uuid.uuid4())  # noqa: E731

    folders = read_jsonl(d / JSONL["folders"])
    fmap = {r["id"]: new_id(r["id"]) for r in folders}
    frows = []
    for r in folders:
        row = dict(r)
        row["id"] = fmap[r["id"]]
        row["knowledge_base_id"] = kid
        if row.get("parent_id") in fmap:
            row["parent_id"] = fmap[row["parent_id"]]
        frows.append(row)
    bulk_delete("wiki_folders", kid, "path", [r.get("path") for r in frows])
    bulk_insert("wiki_folders", frows)

    pages = read_jsonl(d / JSONL["pages"])
    pmap = {r["id"]: new_id(r["id"]) for r in pages}
    prows = []
    for r in pages:
        row = dict(r)
        row["id"] = pmap[r["id"]]
        row["knowledge_base_id"] = kid
        if row.get("folder_id") in fmap:
            row["folder_id"] = fmap[row["folder_id"]]
        prows.append(row)
    bulk_delete("wiki_pages", kid, "slug", [r.get("slug") for r in prows])
    bulk_insert("wiki_pages", prows)

    revs = read_jsonl(d / JSONL["revisions"])
    rrows = []
    for r in revs:
        row = dict(r)
        row["id"] = new_id(r["id"])
        row["knowledge_base_id"] = kid
        row["page_id"] = pmap.get(r.get("page_id"), r.get("page_id"))
        rrows.append(row)
    bulk_delete("wiki_page_revisions", kid, "slug", sorted({r.get("slug") for r in rrows}))
    bulk_insert("wiki_page_revisions", rrows)

    issues = read_jsonl(d / JSONL["issues"])
    irows = []
    for r in issues:
        row = dict(r)
        row["id"] = new_id(r["id"])
        row["knowledge_base_id"] = kid
        irows.append(row)
    bulk_delete("wiki_page_issues", kid, "slug", [r.get("slug") for r in irows])
    bulk_insert("wiki_page_issues", irows)

    nodes = [{"slug": (n.get("props") or {}).get("slug"),
              "props": {k: v for k, v in (n.get("props") or {}).items() if k != "kb_id"}}
             for n in read_jsonl(d / JSONL["graph_nodes"])]
    nodes = [n for n in nodes if n["slug"]]
    if nodes:
        ke_neo4j.query("UNWIND $rows AS n MERGE (x:BodhiInstance {kb_id: $kb, slug: n.slug}) "
                       "SET x += n.props RETURN count(x) AS c", {"kb": kid, "rows": nodes})

    import re as _re
    edges = read_jsonl(d / JSONL["graph_edges"])
    by_type: dict[str, list] = {}
    for e in edges:
        t = str(e.get("type") or "")
        if not _re.match(r"^[A-Za-z0-9_:]+$", t):
            continue
        by_type.setdefault(t, []).append({"src": e.get("src_slug"), "dst": e.get("dst_slug"),
                                          "props": e.get("props") or {}})
    for t, rows in by_type.items():
        ke_neo4j.query("UNWIND $rows AS e MATCH (a:BodhiInstance {kb_id:$kb, slug:e.src}), "
                       "(b:BodhiInstance {kb_id:$kb, slug:e.dst}) "
                       "MERGE (a)-[r:`%s`]->(b) SET r += e.props RETURN count(r) AS c" % t,
                       {"kb": kid, "rows": rows})

    out = {"kb_id": kid, "folders": len(folders), "pages": len(pages), "revisions": len(revs),
           "issues": len(issues), "graph_nodes": len(nodes), "graph_edges": len(edges),
           "edge_types": len(by_type)}
    print("导入完成 → %s" % kid)
    for k, v in out.items():
        print("   %-12s %s" % (k, v))
    return out


# ============================================================ ④ 比对（PG + Neo4j）
_PG_KEYS = ("slug", "title", "page_type", "status", "content", "summary", "parent_slug",
            "category_path", "wiki_path", "depth", "sort_order", "source_refs", "chunk_refs",
            "page_metadata", "aliases", "version")
_NODE_SKIP = ("kb_id",)


def _h(obj) -> str:
    return hashlib.sha256(json.dumps(obj, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


def _as_dir(p: pathlib.Path) -> pathlib.Path:
    """zip → 解包后的目录（目录原样返回）。"""
    if p.is_file() and p.suffix.lower() == ".zip":
        import tempfile
        import zipfile
        tmp = pathlib.Path(tempfile.mkdtemp(prefix="kb-xfer-"))
        with zipfile.ZipFile(p) as z:
            z.extractall(tmp)
        subs = [x for x in tmp.iterdir() if (x / "manifest.json").is_file()]
        return subs[0] if subs else tmp
    return p


def fingerprint(src: str) -> dict:
    """src 可为 **kb_id**、**导出目录** 或 **zip**；两侧用同一套归一化，故可直接比哈希。"""
    d = pathlib.Path(src)
    if d.exists() and (d.is_dir() or d.suffix.lower() == ".zip"):
        d = _as_dir(d)
    if d.is_dir():
        pages, folders = read_jsonl(d / JSONL["pages"]), read_jsonl(d / JSONL["folders"])
        revs, nodes, edges = (read_jsonl(d / JSONL["revisions"]), read_jsonl(d / JSONL["graph_nodes"]),
                              read_jsonl(d / JSONL["graph_edges"]))
    else:
        pages = q_json("SELECT * FROM wiki_pages WHERE knowledge_base_id=%s AND deleted_at IS NULL" % sql_lit(src))
        folders = q_json("SELECT * FROM wiki_folders WHERE knowledge_base_id=%s AND deleted_at IS NULL" % sql_lit(src))
        revs = q_json("SELECT * FROM wiki_page_revisions WHERE knowledge_base_id=%s" % sql_lit(src))
        nodes, edges = nodes_of(src), edges_of(src)
    return {
        "pages": {r["slug"]: _h({k: r.get(k) for k in _PG_KEYS}) for r in pages},
        "links": {r["slug"]: _h([sorted(r.get("in_links") or []), sorted(r.get("out_links") or [])])
                  for r in pages},
        "folders": {(r.get("path") or ""): _h([r.get("name"), r.get("depth"), r.get("sort_order")])
                    for r in folders},
        "revisions": {"%s#%s" % (r.get("slug"), r.get("version")):
                      _h([r.get("title"), r.get("content"), r.get("summary")]) for r in revs},
        "nodes": {(n["props"].get("slug")): _h({k: v for k, v in n["props"].items() if k not in _NODE_SKIP})
                  for n in nodes},
        "edges": sorted("%s|%s|%s" % (e.get("src_slug"), e.get("type"), e.get("dst_slug")) for e in edges),
    }


def _diff(a: dict, b: dict, label: str, limit: int = 8) -> dict:
    sa, sb = set(a), set(b)
    missing, extra = sorted(sa - sb), sorted(sb - sa)
    mismatch = sorted(k for k in (sa & sb) if a[k] != b[k])
    ok = not (missing or extra or mismatch)
    print("  %-8s %s   源=%d 目标=%d" % (label, "✅ 一致" if ok else "❌ 不一致", len(sa), len(sb)))
    for name, lst in (("缺（目标没有）", missing), ("多（目标多余）", extra), ("内容不一致", mismatch)):
        if lst:
            print("      %s %d：%s" % (name, len(lst),
                                       ", ".join(lst[:limit]) + ("…" if len(lst) > limit else "")))
    return {"ok": ok, "missing": missing, "extra": extra, "mismatch": mismatch}


def cmd_verify(src: str, dst: str) -> dict:
    print("比对：\n  源   = %s\n  目标 = %s" % (src, dst))
    fa, fb = fingerprint(src), fingerprint(dst)
    rep = {k: _diff(fa[k], fb[k], lbl) for k, lbl in
           (("pages", "页内容"), ("links", "页链"), ("folders", "目录"),
            ("revisions", "版本"), ("nodes", "图节点"))}
    ea, eb = fa["edges"], fb["edges"]
    only_src, only_dst = sorted(set(ea) - set(eb)), sorted(set(eb) - set(ea))
    rep["edges"] = {"ok": ea == eb, "source": len(ea), "target": len(eb)}
    print("  %-8s %s   源=%d 目标=%d" % ("图边", "✅ 一致" if ea == eb else "❌ 不一致", len(ea), len(eb)))
    for nm, lst in (("仅源有", only_src), ("仅目标有", only_dst)):
        if lst:
            print("      %s %d：%s" % (nm, len(lst), lst[:5]))
    ok = all(v["ok"] for v in rep.values())
    print("\n== 结论：%s ==" % ("✅ PG + Neo4j 一致" if ok else "❌ 存在差异（见上）"))
    return {"ok": ok, "detail": rep}


# ============================================================ ⑤ 硬删（核实后处理数据）
def purge_scan(kb_ids: list[str] | None = None) -> list[dict]:
    """列出**可硬删的残留**：已软删知识库 + 其活页/目录/版本/图节点计数。

    口径（2026-10-12 用户）：**已软删的库**，其内容（PG 页/目录/版本 + Neo4j 节点）默认不再参与
    同名扫描与渲染（`ke_context` 已按"活库"过滤），但**数据仍在** → 需要清理时用本函数核对、
    再由 `cmd_purge` 处理。**只列软删库**（活库不会被误删）。
    """
    ids = [k for k in (kb_ids or []) if k]
    where = ("AND kb.id IN (%s)" % ", ".join(sql_lit(k) for k in ids)) if ids else ""
    rows = q_json(
        "SELECT kb.id AS kb_id, COALESCE(kb.name,'') AS name, kb.deleted_at::text AS deleted_at, "
        "  (SELECT count(*) FROM wiki_pages p WHERE p.knowledge_base_id = kb.id) AS pages_all, "
        "  (SELECT count(*) FROM wiki_pages p WHERE p.knowledge_base_id = kb.id AND p.deleted_at IS NULL) AS pages_live, "
        "  (SELECT count(*) FROM wiki_folders f WHERE f.knowledge_base_id = kb.id) AS folders, "
        "  (SELECT count(*) FROM wiki_page_revisions r WHERE r.knowledge_base_id = kb.id) AS revisions, "
        "  (SELECT count(*) FROM wiki_page_issues i WHERE i.knowledge_base_id = kb.id) AS issues "
        "FROM knowledge_bases kb WHERE kb.deleted_at IS NOT NULL %s ORDER BY kb.created_at" % where)
    out = []
    for r in rows:
        nodes = 0
        try:
            nodes = int((ke_neo4j.query("MATCH (n:BodhiInstance) WHERE n.kb_id = $kb "
                                        "RETURN count(n) AS c", {"kb": r["kb_id"]}) or [{}])[0].get("c") or 0)
        except Exception:  # noqa: BLE001
            nodes = -1
        r["graph_nodes"] = nodes
        out.append(r)
    return out


def cmd_purge(kb_ids: list[str], dry_run: bool = True, drop_kb_row: bool = False) -> dict:
    """**硬删**这些知识库的内容（PG 页/目录/版本/问题 + Neo4j 节点）；可选连 `knowledge_bases` 行一起删。

    安全约束：**只处理已软删的库**（`deleted_at IS NOT NULL`）；活库一律拒（除非先软删）。
    `dry_run=True` 只列影响面（默认）。`drop_kb_row=True` 时连库行一起删（默认 False=保留库行）。
    """
    plan = purge_scan(kb_ids)
    if not plan:
        print("没有可硬删的库（只处理**已软删**的库；活库请先软删）")
        return {"ok": True, "kb_ids": [], "dry_run": dry_run, "items": []}
    print("%s：%d 个已软删库的残留" % ("dry-run（未写）" if dry_run else "硬删", len(plan)))
    for r in plan:
        print("  %s  %-16s 页(全部/活)=%s/%s 目录=%s 版本=%s 问题=%s 图节点=%s"
              % (r["kb_id"][:8], (r["name"] or "（空名）")[:14], r["pages_all"], r["pages_live"],
                 r["folders"], r["revisions"], r["issues"], r["graph_nodes"]))
    res = {"ok": True, "dry_run": dry_run, "items": plan, "deleted": {}}
    if dry_run:
        return res
    ids = [r["kb_id"] for r in plan]
    for table in ("wiki_page_revisions", "wiki_pages", "wiki_folders", "wiki_page_issues"):
        for kid in ids:
            q("DELETE FROM %s WHERE knowledge_base_id = %s" % (table, sql_lit(kid)))
    for kid in ids:
        try:
            ke_neo4j.query("MATCH (n:BodhiInstance) WHERE n.kb_id = $kb DETACH DELETE n", {"kb": kid})
        except Exception as exc:  # noqa: BLE001
            print("  !! 图删除失败 %s：%s" % (kid[:8], str(exc)[:80]))
    if drop_kb_row:
        q("DELETE FROM knowledge_bases WHERE id IN (%s)" % ", ".join(sql_lit(k) for k in ids))
    res["deleted"] = {"kb_ids": ids, "drop_kb_row": drop_kb_row}
    print("硬删完成：%d 个库的内容已清（库行%s）" % (len(ids), "已删" if drop_kb_row else "保留"))
    return res


# ============================================================ CLI
def _opt(argv: list, name: str, default: str = "") -> str:
    return argv[argv.index(name) + 1] if name in argv and argv.index(name) + 1 < len(argv) else default


def main(argv: list) -> int:
    a = argv[1:]
    if not a or a[0] in ("-h", "--help", "help"):
        print(__doc__)
        return 0
    if a[0] == "export":
        cmd_export(a[1], a[2], make_zip="--zip" in a)
        return 0
    if a[0] == "new-kb":
        cmd_new_kb(a[1], tenant=_opt(a, "--tenant", "10000"))
        return 0
    if a[0] == "import":
        import time as _t
        t0 = _t.time()
        cmd_import(a[1], to_kb=_opt(a, "--to"), new_kb=_opt(a, "--new-kb"),
                   keep_ids="--keep-ids" in a)
        print("耗时 %.1f s" % (_t.time() - t0))
        return 0
    if a[0] == "purge":
        ids = [x for x in a[1:] if not x.startswith("--")]
        cmd_purge(ids, dry_run=("--apply" not in a), drop_kb_row=("--drop-kb-row" in a))
        return 0
    if a[0] == "purge-scan":
        for r in purge_scan([x for x in a[1:] if not x.startswith("--")]):
            print(json.dumps(r, ensure_ascii=False))
        return 0
    if a[0] == "verify":
        return 0 if cmd_verify(a[1], _opt(a, "--to"))["ok"] else 1
    print(__doc__)
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))

