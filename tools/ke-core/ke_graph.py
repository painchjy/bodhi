"""本体实例图谱（实例层）—— 2026-10-05 全量重构：Neo4j 为主存储、PG wiki 为伴生索引。

本模块只做一件事：把「知识库里的页 + 页正文的 `## 本体关系` 行」投影成 Neo4j 实例层：
  - 节点：`(:BodhiInstance {kb_id, slug, page_type, module, name, tenant_id, <数据属性…>})`
  - 边：本体对象属性（prefixed 作关系类型，如 `` :`bmm:isBasisFor` ``）
`kb_id` 是**强制**隔离键：一切读写都必须带它（防跨库）。
"""
from __future__ import annotations

import json
import re
from typing import Iterable

import ke_neo4j
import ke_pages

INSTANCE_LABEL = "BodhiInstance"


_INDEX_READY = False


def _ensure_index() -> None:
    """确保 `(kb_id, slug)` 索引存在（**进程内只跑一次**；2026-10-09 P0）。

    为什么必须：`MERGE (n:BodhiInstance {kb_id, slug})` / `UNWIND … MERGE` 没有索引时
    退化为**标签全扫** —— 实测批量 `ensure_instances` 4 行要 **3.2 s**，建索引后降到几十 ms。
    """
    global _INDEX_READY
    if _INDEX_READY:
        return
    try:
        ke_neo4j.query(
            "CREATE INDEX bodhi_instance_idx IF NOT EXISTS "
            "FOR (n:BodhiInstance) ON (n.kb_id, n.slug)")
    except Exception:  # noqa: BLE001  老版本 Neo4j 不支持 IF NOT EXISTS
        try:
            ke_neo4j.query("CREATE INDEX ON :BodhiInstance(kb_id, slug)")
        except Exception:  # noqa: BLE001  已存在等
            pass
    _INDEX_READY = True


def _module_of(page_type: str) -> str:
    return (page_type or "").split(":", 1)[0] if ":" in (page_type or "") else ""


def _attrs_from_content(content: str) -> dict[str, str]:
    """正文「## 属性（数据属性）」小节 → {键去前缀: 值}（md 表达 = 数据属性的伴生）。

    双读（2026-10-08 契约 §4.3）：新格式 `### 名称（prefixed，range）` + 后续行（可多行 markdown）；
    旧格式 `- 名称… = 值`（兼容存量）。
    """
    out: dict[str, str] = {}
    sec = ""
    cur_key: str | None = None
    buf: list[str] = []

    def _flush() -> None:
        nonlocal cur_key, buf
        if cur_key and buf:
            out[cur_key] = "\n".join(buf).strip()
        cur_key, buf = None, []

    for raw in (content or "").splitlines():
        line = raw.rstrip()
        if line.startswith("## "):
            _flush()
            sec = line[3:].strip()
            continue
        if sec not in ("属性（数据属性）", "属性"):
            continue
        s = line.strip()
        if s.startswith("### "):
            _flush()
            head = s[4:].strip()
            m = re.search(r"[`(（]([A-Za-z_][\w]*:[A-Za-z_][\w]*)[`）)]", head)
            cur_key = (m.group(1) if m else head).split(":")[-1]
            continue
        mo = re.match(r"^-\s*([A-Za-z_][\w:]*)\b[^\n=]*=\s*(.+?)\s*$", s)
        if mo:
            _flush()
            out[mo.group(1).split(":")[-1]] = mo.group(2).strip()
            continue
        if cur_key is not None:
            buf.append(line)
    _flush()
    return out


def _attrs_of(page_meta_json: str) -> dict[str, str]:
    """从 `page_metadata.ontology.attributes` 取数据属性（键去模块前缀，只留标量）。"""
    out: dict[str, str] = {}
    try:
        data = json.loads(page_meta_json or "{}")
        attrs = (data.get("ontology") or {}).get("attributes") or {}
    except Exception:  # noqa: BLE001
        return out
    for k, v in (attrs or {}).items():
        if v is None:
            continue
        if isinstance(v, (dict, list)):
            continue
        out[str(k).split(":")[-1]] = str(v)
    return out


def _edges_of(content: str, known_slugs: set[str]) -> list[tuple[str, str]]:
    """页正文 `## 本体关系` 行 → [(关系类型, 目标 slug)]；只留目标在库内的边。"""
    out: list[tuple[str, str]] = []
    for r in ke_pages.parse_out_relations(content):
        if r.get("type") and r.get("slug") and r["slug"] in known_slugs:
            out.append((r["type"], r["slug"]))
    return out


def _run(statement: str, params: dict) -> list[dict]:
    return ke_neo4j.query(statement, params)


def _edge_count(kb_id: str) -> int:
    rows = _run("MATCH (a:BodhiInstance {kb_id:$kb})-[r]->(b) RETURN count(r) AS n", {"kb": kb_id})
    return int((rows[0].get("n") if rows else 0) or 0)


def rebuild_kb_graph(kb_id: str, tenant_id: int | None = None) -> dict:
    """按 PG 页**幂等全量重建**该知识库的实例层（先清本库旧实例，再灌）。"""
    import ke_db
    _ensure_index()
    edges_before = _edge_count(kb_id)                    # 回归防护：记录重建前边数
    rows = ke_db.psql_csv(
        "SELECT slug, title, COALESCE(page_type,'') AS page_type, COALESCE(content,'') AS content, "
        "       COALESCE(page_metadata::text,'{}') AS meta, tenant_id "
        "  FROM wiki_pages WHERE knowledge_base_id = %s AND deleted_at IS NULL "
        "  AND COALESCE(page_type,'') NOT IN ('index','summary')"
        % ke_db.sql_str(kb_id))
    known = {r["slug"] for r in rows}
    if not rows:
        _run("MATCH (n:BodhiInstance {kb_id:$kb}) DETACH DELETE n", {"kb": kb_id})
        return {"ok": True, "kb_id": kb_id, "nodes": 0, "edges": 0, "cleared": True}

    # S-10 修：重建**节点/属性**，**保留边**（边是图本态，由增量写 add_edge 维护；
    # **不再从正文派生**——正文已无关系）。只删"孤儿实例"（无对应活页的实例）。
    _run("MATCH (n:BodhiInstance {kb_id:$kb}) WHERE NOT n.slug IN $slugs DETACH DELETE n",
         {"kb": kb_id, "slugs": sorted(known)})
    nodes = 0
    for r in rows:
        # 数据属性来源：正文属性行（md 表达）作兜底，page_metadata 机器口径优先覆盖
        attrs = _attrs_from_content(r["content"])
        attrs.update(_attrs_of(r["meta"]))
        wc = strip_relation_sections(r["content"])     # S-10：全文入图
        params = {
            "kb": kb_id, "slug": r["slug"], "pt": r["page_type"],
            "module": _module_of(r["page_type"]), "name": r["title"],
            "tenant": r.get("tenant_id"), "wc": wc,
        }
        sets = ["n.page_type=$pt", "n.module=$module", "n.name=$name", "n.tenant_id=$tenant",
                "n.wiki_content=$wc"]
        for k, v in attrs.items():
            sets.append("n.%s=$%s" % (k, "a_" + k))
            params["a_" + k] = v
        _run("MERGE (n:BodhiInstance {kb_id:$kb, slug:$slug}) SET %s" % ", ".join(sets), params)
        nodes += 1
    edges = _edge_count(kb_id)
    edge_delta = edges - edges_before
    # 回归防护（2026-10-05）：本函数**保留边**，只删孤儿实例。若边数下降且不是由删孤儿导致，
    # 说明"重建清边"的回归又回来了 —— 直接抛错，绝不静默吞掉。
    orphan_edges = _run("MATCH (n:BodhiInstance {kb_id:$kb})-[r]-() WHERE NOT n.slug IN $slugs "
                        "RETURN count(r) AS n", {"kb": kb_id, "slugs": sorted(known)})
    orphan_n = int((orphan_edges[0].get("n") if orphan_edges else 0) or 0)
    if edge_delta < -orphan_n:
        raise RuntimeError("rebuild_kb_graph 边数回归：重建前 %d → 重建后 %d（非孤儿删除丢失 %d 条边）"
                           % (edges_before, edges, -(edge_delta + orphan_n)))
    return {"ok": True, "kb_id": kb_id, "nodes": nodes, "edges": edges,
            "edges_before": edges_before, "edges_delta": edge_delta}


def strip_relation_sections(content: str) -> str:
    """删正文里「本体关系」与「被引用（入边）」两小节（它们改为从图动态渲染，不落正文）。"""
    out: list[str] = []
    skip = False
    for line in (content or "").splitlines():
        if line.startswith("## "):
            sec = line[3:].strip()
            skip = sec.startswith("本体关系") or sec.startswith("被引用")
            if skip:
                continue
        if skip:
            continue
        out.append(line)
    return "\n".join(out).rstrip() + "\n"


def rebuild_kb_wiki(kb_id: str, dry_run: bool = False) -> dict:
    """**图→wiki 全量重建**（S-10）：按图节点 `wiki_content` 重建 PG `wiki_pages.content`，
    并清空出入链（关系只在图里）。图节点无 `wiki_content` 的页（如 index/原生页）不动。"""
    import ke_db
    insts = _run("MATCH (n:BodhiInstance {kb_id:$kb}) RETURN n.slug AS slug, n.wiki_content AS wc",
                 {"kb": kb_id})
    wc_map = {r["slug"]: (r.get("wc") or "") for r in insts}
    rows = ke_db.psql_csv(
        "SELECT slug, COALESCE(content,'') AS content FROM wiki_pages "
        "WHERE knowledge_base_id = %s AND deleted_at IS NULL" % ke_db.sql_str(kb_id))
    changed = 0
    for r in rows:
        wc = wc_map.get(r["slug"])
        if wc is None:
            continue
        if wc != r["content"]:
            changed += 1
        if not dry_run:
            ke_db.psql(
                "UPDATE wiki_pages SET content = %s, out_links = '[]'::jsonb, in_links = '[]'::jsonb, "
                "updated_at = now() WHERE knowledge_base_id = %s AND slug = %s;"
                % (ke_db.sql_str(wc), ke_db.sql_str(kb_id), ke_db.sql_str(r["slug"])), stdin=True)
    return {"ok": True, "kb_id": kb_id, "dry_run": bool(dry_run),
            "pages": len(rows), "content_changed": changed}


def relations_of(kb_id: str, slug: str):
    """读图实例的**出边/入边**（页面底部「本体关系」面板动态渲染用）。

    图里没有该实例、或图不可用 → 返回 `None`（调用方退 PG 兜底）。
    """
    import ke_ontology  # noqa: PLC0415
    rows = _run("MATCH (n:BodhiInstance {kb_id:$kb, slug:$slug}) "
                "RETURN n.name AS name, n.page_type AS pt", {"kb": kb_id, "slug": slug})
    if not rows:
        return None
    node = rows[0]
    meta = ke_ontology.class_meta()

    def tl(t: str) -> str:
        return (meta.get(t) or {}).get("label") or t

    out_r = _run("MATCH (n:BodhiInstance {kb_id:$kb, slug:$slug})-[r]->(m:BodhiInstance) "
                 "RETURN type(r) AS t, m.slug AS s, m.name AS nm, m.page_type AS pt ORDER BY t, s",
                 {"kb": kb_id, "slug": slug})
    out = [{"label": tl(x["t"]), "type": x["t"], "type_label": tl(x["t"]),
            "target_slug": x["s"], "target_title": x.get("nm") or (x["s"] or "").rsplit("/", 1)[-1],
            "target_type": x.get("pt") or "", "target_exists": True, "range_ok": True}
           for x in out_r]
    in_r = _run("MATCH (m:BodhiInstance)-[r]->(n:BodhiInstance {kb_id:$kb, slug:$slug}) "
                "RETURN type(r) AS t, m.slug AS s, m.name AS nm, m.page_type AS pt ORDER BY t, s",
                {"kb": kb_id, "slug": slug})
    inbound = [{"source_slug": x["s"], "source_title": x.get("nm") or (x["s"] or "").rsplit("/", 1)[-1],
                "source_type": x.get("pt") or "", "label": tl(x["t"]), "type": x["t"],
                "type_label": tl(x["t"])} for x in in_r]
    return {"slug": slug, "title": node.get("name") or (slug or "").rsplit("/", 1)[-1],
            "page_type": node.get("pt") or "",
            "type_label": tl(node.get("pt") or ""), "out": out, "in": inbound, "source": "graph"}


def _page_type(kb_id: str, slug: str) -> str:
    import ke_db
    row = ke_db.psql_csv("SELECT COALESCE(page_type,'') AS pt FROM wiki_pages "
                         "WHERE knowledge_base_id=%s AND slug=%s AND deleted_at IS NULL"
                         % (ke_db.sql_str(kb_id), ke_db.sql_str(slug)))
    return (row[0].get("pt") or "") if row else ""


def _check_domain(rel_type: str, source_type: str) -> None:
    import ke_ontology  # noqa: PLC0415
    allowed = ke_ontology.relation_type_map(source_type) if source_type else {}
    if rel_type not in allowed:
        raise ValueError("关系类型 `%s` 不适用于 %s（该类型可用：%s）"
                         % (rel_type, source_type or "（未知类）", "、".join(sorted(allowed)[:12]) or "无"))


def _check_range(rel_type: str, target_type: str) -> None:
    import ke_ontology  # noqa: PLC0415
    closure = ke_ontology.target_closure(rel_type)
    if closure and target_type and target_type not in closure:
        raise ValueError("目标页类型 %s 不在 `%s` 的 range 范围内（%s）"
                         % (target_type, rel_type, "、".join(closure[:10])))


def _ensure_instance(kb_id: str, slug: str, page_type: str = "") -> None:
    """确保实例节点存在（**增量写**路径：`add_edge` / `link_source_session` 等）。

    2026-10-05 修：**必须带 `name`** —— 前端图谱（`instance_graph`）读 `n.name`，缺失就回落
    到完整 slug（显示成 `bmm/类/知识名`）。旧实现只写 `page_type` → M3 增量建的节点没名字。
    顺带补 `module`/`tenant_id`，与 `rebuild_kb_graph`/`project_page` 口径一致。
    """
    import ke_db  # noqa: PLC0415
    row = ke_db.psql_csv(
        "SELECT COALESCE(title,'') AS title, COALESCE(page_type,'') AS pt, tenant_id "
        "FROM wiki_pages WHERE knowledge_base_id=%s AND slug=%s AND deleted_at IS NULL"
        % (ke_db.sql_str(kb_id), ke_db.sql_str(slug)))
    r = row[0] if row else {}
    pt = page_type or (r.get("pt") or "")
    name = (r.get("title") or "").strip() or (slug or "").rsplit("/", 1)[-1]
    _run("MERGE (n:BodhiInstance {kb_id:$kb, slug:$slug}) "
         "SET n.page_type=$pt, n.module=$module, n.name=$name, n.tenant_id=$tenant",
         {"kb": kb_id, "slug": slug, "pt": pt, "module": _module_of(pt), "name": name,
          "tenant": r.get("tenant_id")})


def page_info_map(kb_id: str, slugs) -> dict:
    """**一次**取多个 slug 的 `{slug: {pt,title,tenant}}`（批量预读，替代逐条 `_page_type`）。

    为什么需要（2026-10-09 P0）：`add_edge` 每条边要读 2 次 `_page_type`；40 条边 =
    80 次 psql 往返，而**实测 1 次 psql 往返 ≈ 272ms** → 这是 2 分钟超时的主因之一。
    批量后整批只要 1 次往返。
    """
    import ke_db  # noqa: PLC0415
    want = [str(s).strip() for s in (slugs or []) if str(s).strip()]
    if not want:
        return {}
    out: dict = {}
    step = 200                                   # 分批，避免超长 SQL / 参数上限
    for i in range(0, len(want), step):
        chunk = want[i:i + step]
        rows = ke_db.psql_csv(
            "SELECT slug, COALESCE(page_type,'') AS pt, COALESCE(title,'') AS title, "
            "       tenant_id FROM wiki_pages "
            " WHERE knowledge_base_id=%s AND deleted_at IS NULL AND slug IN (%s)"
            % (ke_db.sql_str(kb_id), ", ".join(ke_db.sql_str(s) for s in chunk)))
        for r in rows:
            out[str(r.get("slug"))] = {"pt": r.get("pt") or "", "title": r.get("title") or "",
                                       "tenant": r.get("tenant_id")}
    return out


def ensure_instances(kb_id: str, rows) -> int:
    """**一次** UNWIND 批量「确保实例节点存在」（替代逐节点 `_ensure_instance`）。

    `rows` = `[{slug, name, page_type, tenant_id}]`（一般来自 `page_info_map`）。
    返回处理行数。旧实现每条边 2 次 `_ensure_instance`（各含 1 次 PG + 1 次 Neo4j）。
    """
    payload = []
    for r in (rows or []):
        d = dict(r or {})
        slug = str(d.get("slug") or "").strip()
        if not slug:
            continue
        pt = str(d.get("page_type") or "")
        payload.append({"slug": slug, "pt": pt, "module": _module_of(pt),
                        "name": str(d.get("name") or "").strip() or slug.rsplit("/", 1)[-1],
                        "tenant": d.get("tenant_id")})
    if not payload:
        return 0
    _ensure_index()                      # 批量 MERGE 前先确保索引（否则标签全扫，慢 50-100 倍）
    _run("UNWIND $rows AS row MERGE (n:BodhiInstance {kb_id:$kb, slug:row.slug}) "
         "SET n.page_type = row.pt, n.module = row.module, n.name = row.name, "
         "    n.tenant_id = coalesce(n.tenant_id, row.tenant) "
         "RETURN count(n) AS n",
         {"kb": kb_id, "rows": payload})
    return len(payload)


def _etyp(t: str) -> str:
    return (t or "").replace("`", "")


def upsert_node(kb_id: str, slug: str, title: str, page_type: str,
                attributes: dict | None = None, wiki_content: str | None = None) -> dict:
    """**直写/更新图节点**（2026-10-08 契约 §8「图本优先」）。

    与 `project_page` 的区别：本函数**不读 PG**（供内核先图后 PG 调用）；
    属性键按「去前缀」存（与 `project_page` 口径一致）。
    """
    pt = (page_type or "").strip() or _page_type(kb_id, slug)
    params = {"kb": kb_id, "slug": slug, "pt": pt, "module": _module_of(pt),
              "name": (title or "").strip() or (slug or "").rsplit("/", 1)[-1]}
    sets = ["n.page_type=$pt", "n.module=$module", "n.name=$name"]
    if wiki_content is not None:
        sets.append("n.wiki_content=$wc")
        params["wc"] = wiki_content
    for k, v in (attributes or {}).items():
        key = str(k).split(":")[-1]
        if not key or key in ("kb", "slug", "pt", "module", "name", "wc"):
            continue
        sets.append("n.%s=$a_%s" % (key, key))
        params["a_" + key] = (v if isinstance(v, (str, int, float, bool))
                              else json.dumps(v, ensure_ascii=False))
    _run("MERGE (n:BodhiInstance {kb_id:$kb, slug:$slug}) SET %s" % ", ".join(sets), params)
    return {"ok": True, "slug": slug}


def add_edge(kb_id: str, slug: str, rel_type: str, target_slug: str, label: str = "",
             ctx: dict | None = None) -> dict:
    """新增一条实例边：本页 --rel_type--> target（**直写图**，range 校验）。

    `ctx` = **批量写路径的上下文**（2026-10-09 P0，可选）：
      `{"info": {slug: {pt,title,tenant}}, "nodes_ensured": True, "skip_relations": True}`
      · `info` 由 `page_info_map()` **一次**预读 → 免掉每条边 2 次 `_page_type`；
      · `nodes_ensured` → 节点已由 `ensure_instances()` 批量 MERGE 过 → 免掉 2 次 `_ensure_instance`；
      · `skip_relations` → 不回传全量关系（省 1 次 Neo4j；批量回执只要计数）。
    净效果：每条边从「4 次 PG + 5 次 Neo4j」（≈1.46 s）降到「1 次 Neo4j」（≈74 ms）。
    不传 `ctx` 时行为与旧版**逐字一致**（兼容既有调用）。
    """
    if not rel_type or not target_slug:
        raise ValueError("缺少 rel_type / target_slug")
    if target_slug == slug:
        raise ValueError("不能把关系指向本页")
    info = (ctx or {}).get("info") or {}
    if ctx is not None:
        tt = str(((info.get(target_slug) or {}).get("pt")) or "")
        st = str(((info.get(slug) or {}).get("pt")) or "")
    else:
        tt = _page_type(kb_id, target_slug)
        st = _page_type(kb_id, slug)
    if not tt:
        raise ValueError("目标页不存在：%s" % target_slug)
    _check_domain(rel_type, st)
    _check_range(rel_type, tt)
    if not (ctx or {}).get("nodes_ensured"):
        _ensure_instance(kb_id, slug, st)
        _ensure_instance(kb_id, target_slug, tt)
    dup = _run("MATCH (a:BodhiInstance {kb_id:$kb, slug:$s})-[r:`%s`]->(b:BodhiInstance {kb_id:$kb, slug:$t}) "
               "RETURN count(r) AS n" % _etyp(rel_type), {"kb": kb_id, "s": slug, "t": target_slug})
    ret = ({"changed": False, "reason": "同样的关系已存在", "slug": slug, "version": 1}
           if (dup and dup[0].get("n")) else None)
    if ret is None:
        _run("MATCH (a:BodhiInstance {kb_id:$kb, slug:$s}) MATCH (b:BodhiInstance {kb_id:$kb, slug:$t}) "
             "MERGE (a)-[r:`%s`]->(b)" % _etyp(rel_type), {"kb": kb_id, "s": slug, "t": target_slug})
        ret = {"changed": True, "action": "add", "slug": slug, "version": 1,
               "relation": {"type": rel_type, "target_slug": target_slug}}
    if not (ctx or {}).get("skip_relations"):
        ret["relations"] = relations_of(kb_id, slug)
    return ret


def add_edges_batch(kb_id: str, triples: list, ctx: dict | None = None) -> dict:
    """**批量建边（P3-0，2026-10-10）**：`triples=[(源slug, 关系类型, 目标slug)]`。

    与逐条 `add_edge` 的区别：
      · domain/range 仍在**内存**逐条校验（非法项逐条回报，不打断其余）；
      · **MERGE 按关系类型分组，每类一条 `UNWIND`** → N 条边从 **N 次** Neo4j 往返降到
        「去重后的关系类型数」次（例：40 条边 / 8 种关系 ≈ **8 次**）。
    幂等：`MERGE` 保证同一 (源,类型,目标) 只建一条 → 重跑即续作、不会重复。

    实测基线：1 次 Neo4j ≈ 74 ms（本机）；内网更高。返回 `{written, errors, types, total}`。
    """
    info = (ctx or {}).get("info") or {}
    by_type: dict = {}
    errors: list = []
    for row in (triples or []):
        src, rel, tgt = (str(row[0]).strip(), str(row[1]).strip(), str(row[2]).strip())
        if not (src and rel and tgt):
            continue
        if src == tgt:
            errors.append({"source": src, "type": rel, "target_slug": tgt,
                           "error": "不能把关系指向本页"})
            continue
        st = str(((info.get(src) or {}).get("pt")) or "") or (None if ctx is not None else _page_type(kb_id, src))
        tt = str(((info.get(tgt) or {}).get("pt")) or "") or (None if ctx is not None else _page_type(kb_id, tgt))
        try:
            if not tt and ctx is None:
                tt = _page_type(kb_id, tgt)
            if not tt:
                raise ValueError("目标页不存在：%s" % tgt)
            if st is None:
                st = _page_type(kb_id, src)
            _check_domain(rel, st or "")
            _check_range(rel, tt)
        except Exception as exc:  # noqa: BLE001
            errors.append({"source": src, "type": rel, "target_slug": tgt,
                           "error": str(exc)[:160]})
            continue
        by_type.setdefault(rel, []).append({"s": src, "t": tgt})
    written = 0
    for rel, rows in by_type.items():
        res = _run("UNWIND $rows AS row MATCH (a:BodhiInstance {kb_id:$kb, slug:row.s}) "
                   "MATCH (b:BodhiInstance {kb_id:$kb, slug:row.t}) "
                   "MERGE (a)-[r:`%s`]->(b) RETURN count(r) AS n" % _etyp(rel),
                   {"kb": kb_id, "rows": rows})
        written += int((res[0].get("n") if res else 0) or 0)
    return {"ok": True, "written": written, "errors": errors,
            "types": len(by_type), "total": len(triples or [])}


def update_edge(kb_id: str, slug: str, target_slug: str, new_rel_type: str = "",
                new_target_slug: str = "", label: str = "") -> dict:
    """改一条出边：删旧边（src→target_slug）→ 加新边（新类型/新目标）。"""
    old = _run("MATCH (a:BodhiInstance {kb_id:$kb, slug:$s})-[r]->(b:BodhiInstance {kb_id:$kb, slug:$t}) "
               "RETURN type(r) AS t LIMIT 1", {"kb": kb_id, "s": slug, "t": target_slug})
    if not old:
        raise ValueError("本页没有指向 %s 的出边" % target_slug)
    final_type = new_rel_type or old[0]["t"]
    final_slug = new_target_slug or target_slug
    if final_slug == slug:
        raise ValueError("不能把关系指向本页")
    tt = _page_type(kb_id, final_slug)
    if not tt:
        raise ValueError("目标页不存在：%s" % final_slug)
    _check_domain(final_type, _page_type(kb_id, slug))
    _check_range(final_type, tt)
    _ensure_instance(kb_id, slug, _page_type(kb_id, slug))
    _ensure_instance(kb_id, final_slug, tt)
    _run("MATCH (a:BodhiInstance {kb_id:$kb, slug:$s})-[r]->(b:BodhiInstance {kb_id:$kb, slug:$t}) DELETE r",
         {"kb": kb_id, "s": slug, "t": target_slug})
    _run("MATCH (a:BodhiInstance {kb_id:$kb, slug:$s}) MATCH (b:BodhiInstance {kb_id:$kb, slug:$t}) "
         "MERGE (a)-[r:`%s`]->(b)" % _etyp(final_type), {"kb": kb_id, "s": slug, "t": final_slug})
    return {"changed": True, "action": "update", "slug": slug, "version": 1,
            "before": {"type": old[0]["t"], "target_slug": target_slug},
            "after": {"type": final_type, "target_slug": final_slug}, "relations": relations_of(kb_id, slug)}


def delete_edge(kb_id: str, slug: str, target_slug: str, rel_type: str = "") -> dict:
    """删本页出边（可只删某类）。"""
    if rel_type:
        _run("MATCH (a:BodhiInstance {kb_id:$kb, slug:$s})-[r:`%s`]->(b:BodhiInstance {kb_id:$kb, slug:$t}) "
             "DELETE r" % _etyp(rel_type), {"kb": kb_id, "s": slug, "t": target_slug})
    else:
        _run("MATCH (a:BodhiInstance {kb_id:$kb, slug:$s})-[r]->(b:BodhiInstance {kb_id:$kb, slug:$t}) DELETE r",
             {"kb": kb_id, "s": slug, "t": target_slug})
    return {"changed": True, "action": "delete", "slug": slug, "version": 1, "relations": relations_of(kb_id, slug)}


def instance_graph(kb_id: str, model: str = "", types=None, limit: int = 300) -> dict:
    """普通知识库图谱（**读图实例**）：节点=BodhiInstance，边=本体实例边（带类型/中文名）。"""
    import ke_ontology  # noqa: PLC0415
    colors = ke_ontology.class_meta()
    conds = ["n.kb_id=$kb"]
    params = {"kb": kb_id, "lim": max(1, min(int(limit), 2000))}
    if model:
        conds.append("n.module=$m")
        params["m"] = model
    if types:
        conds.append("n.page_type IN $types")
        params["types"] = [str(t) for t in types]
    where = " AND ".join(conds)
    nrows = _run("MATCH (n:BodhiInstance) WHERE %s "
                 "RETURN n.slug AS slug, n.name AS name, n.page_type AS pt, n.module AS module "
                 "ORDER BY n.page_type, n.name LIMIT $lim" % where, params)
    nodes, known = [], set()
    for r in nrows:
        cls = colors.get(r["pt"], {})
        pt = r["pt"] or ""
        module = r.get("module") or (pt.split(":", 1)[0] if ":" in pt else "")
        label = cls.get("label") or pt
        nodes.append({"slug": r["slug"],
                      "title": r.get("name") or (r["slug"] or "").rsplit("/", 1)[-1], "page_type": pt,
                      "class_label": label, "module": module, "module_label": label,
                      "group": pt, "group_label": label, "color": cls.get("color") or "#94a3b8",
                      "version": 1, "summary": "", "source_refs": []})
        known.add(r["slug"])
    erows = _run("MATCH (a:BodhiInstance {kb_id:$kb})-[r]->(b:BodhiInstance {kb_id:$kb}) "
                 "RETURN a.slug AS s, type(r) AS t, b.slug AS d", {"kb": kb_id})
    edges = []
    for r in erows:
        if r["s"] in known and r["d"] in known:
            edges.append({"source": r["s"], "target": r["d"], "type": r["t"],
                          "label": (colors.get(r["t"]) or {}).get("label") or r["t"]})
    groups = {}
    for n in nodes:
        g = groups.setdefault(n["group"], {"label": n["group_label"], "color": n["color"], "count": 0})
        g["count"] += 1
    return {"kb_id": kb_id, "model": model, "view": "graph", "nodes": nodes, "edges": edges,
            "meta": {"node_count": len(nodes), "edge_count": len(edges),
                     "groups": [dict(key=k, **v) for k, v in sorted(groups.items())],
                     "relation_types": sorted({e["type"] for e in edges})}}


def delete_page(kb_id: str, slug: str) -> dict:
    """删该库该页的实例节点及其边（**kb_id 作用域**，防跨库误删）。"""
    rows = _run("MATCH (n:BodhiInstance {kb_id:$kb, slug:$slug}) WITH n, count(*) AS c "
                "DETACH DELETE n RETURN count(*) AS n", {"kb": kb_id, "slug": slug})
    return {"ok": True, "slug": slug, "deleted": int((rows[0].get("n") if rows else 0) or 0)}


def audit_kb(kb_id: str, fix: bool = False) -> dict:
    """一致性巡检（2026-10-05 M3）：PG 页 ↔ 图实例 比对 + T-Box 越界扫描。

    - 页无实例 / 实例无页 / 类型不一致 → 报出；
    - 实例的 `page_type` 已不在本体（T-Box 类被删/改名）→ `invalid_class`；
    - 实例边类型已不在本体（对象属性被删/改名）→ `invalid_edge_types`。
    """
    import ke_db  # noqa: PLC0415
    pages = ke_db.psql_csv(
        "SELECT slug, COALESCE(page_type,'') AS pt, COALESCE(content,'') AS content FROM wiki_pages "
        "WHERE knowledge_base_id=%s AND deleted_at IS NULL "
        "AND COALESCE(page_type,'') NOT IN ('index','summary')" % ke_db.sql_str(kb_id))
    page_map = {r["slug"]: r["pt"] for r in pages}
    insts = _run("MATCH (n:BodhiInstance {kb_id:$kb}) RETURN n.slug AS slug, n.page_type AS pt, "
                 "n.wiki_content AS wc", {"kb": kb_id})
    inst_map = {r["slug"]: (r.get("pt") or "") for r in insts}
    wc_map = {r["slug"]: (r.get("wc") or "") for r in insts}
    out = {
        "kb_id": kb_id, "pages": len(page_map), "instances": len(inst_map),
        "missing_instance": sorted(set(page_map) - set(inst_map)),
        "orphan_instance": sorted(set(inst_map) - set(page_map)),
        "type_mismatch": [s for s in sorted(set(page_map) & set(inst_map))
                          if page_map.get(s) and inst_map.get(s) and page_map[s] != inst_map[s]],
        "content_mismatch": sorted({r["slug"] for r in pages
                                    if r["slug"] in wc_map
                                    and wc_map[r["slug"]] != strip_relation_sections(r["content"])}),
        "invalid_class": [], "invalid_edge_types": [],
    }
    try:
        valid_cls = {r["p"] for r in _run(
            "MATCH (c:BodhiOntClass) WHERE c.external IS NULL RETURN c.prefixed AS p") if r.get("p")}
        if valid_cls:
            out["invalid_class"] = sorted({s for s, pt in inst_map.items() if pt and pt not in valid_cls})
    except Exception:  # noqa: BLE001
        valid_cls = set()
    try:
        valid_props = {r["p"] for r in _run(
            "MATCH (p:BodhiOntProperty {property_kind:'object'}) RETURN p.prefixed AS p") if r.get("p")}
        if valid_props:
            bad = _run("MATCH (a:BodhiInstance {kb_id:$kb})-[r]->(b) WITH type(r) AS t, count(*) AS n "
                       "WHERE NOT t IN $props RETURN t, n", {"kb": kb_id, "props": sorted(valid_props)})
            out["invalid_edge_types"] = sorted({r["t"]: r["n"] for r in bad}.items())
    except Exception:  # noqa: BLE001
        pass
    out["ok"] = not (out["missing_instance"] or out["orphan_instance"] or out["type_mismatch"]
                     or out["content_mismatch"] or out["invalid_class"] or out["invalid_edge_types"])
    if fix and not out["ok"]:
        # 一致性修复 = 按 PG 全量重建该库实例层（删孤儿 + 补缺失 + 重灌边，幂等）
        out["reconcile"] = rebuild_kb_graph(kb_id)
        return audit_kb(kb_id)
    return out


def project_page(kb_id: str, slug: str, edges=None) -> dict:
    """单页图投影（**先图后 wiki** 的"图"侧，2026-10-05 M3 统一写路径）。

    - MERGE 实例节点 + 数据属性（正文属性行兜底、metadata 优先）；
    - 删本页旧出边，再按 `edges`（[(关系类型, 目标 slug), …]）MERGE 新边。
    """
    import ke_db  # noqa: PLC0415
    row = ke_db.psql_csv(
        "SELECT COALESCE(title,'') AS title, COALESCE(page_type,'') AS pt, COALESCE(content,'') AS content, "
        "COALESCE(page_metadata::text,'{}') AS meta, tenant_id FROM wiki_pages "
        "WHERE knowledge_base_id=%s AND slug=%s AND deleted_at IS NULL"
        % (ke_db.sql_str(kb_id), ke_db.sql_str(slug)))
    if not row:
        return {"ok": False, "slug": slug, "reason": "页不存在"}
    r = row[0]
    attrs = _attrs_from_content(r["content"])
    attrs.update(_attrs_of(r["meta"]))
    wc = strip_relation_sections(r["content"])     # S-10：全文入图（去关系小节），供图→wiki 重建
    params = {"kb": kb_id, "slug": slug, "pt": r["pt"], "module": _module_of(r["pt"]),
              "name": r["title"], "tenant": r.get("tenant_id"), "wc": wc}
    sets = ["n.page_type=$pt", "n.module=$module", "n.name=$name", "n.tenant_id=$tenant",
            "n.wiki_content=$wc"]
    for k, v in attrs.items():
        if k in ("kb", "slug", "pt", "module", "name", "tenant"):
            continue
        sets.append("n.%s=$a_%s" % (k, k))
        params["a_" + k] = v
    _run("MERGE (n:BodhiInstance {kb_id:$kb, slug:$slug}) SET %s" % ", ".join(sets), params)
    _run("MATCH (n:BodhiInstance {kb_id:$kb, slug:$slug})-[r]->() DELETE r", {"kb": kb_id, "slug": slug})
    n_edges = 0
    if edges:
        known = {x["slug"] for x in ke_db.psql_csv(
            "SELECT slug FROM wiki_pages WHERE knowledge_base_id=%s AND deleted_at IS NULL"
            % ke_db.sql_str(kb_id))}
        for rel_type, tgt in edges:
            if tgt not in known:
                continue
            _ensure_instance(kb_id, tgt, _page_type(kb_id, tgt))
            _run("MATCH (a:BodhiInstance {kb_id:$kb, slug:$s}) MATCH (b:BodhiInstance {kb_id:$kb, slug:$t}) "
                 "MERGE (a)-[e:`%s`]->(b)" % _etyp(rel_type), {"kb": kb_id, "s": slug, "t": tgt})
            n_edges += 1
    return {"ok": True, "slug": slug, "attrs": len(attrs), "edges": n_edges}


def project_page_from_content(kb_id: str, slug: str) -> dict:
    """先图后 wiki（单页）：从该页正文抽关系 → 写图实例+边 → 正文去掉关系小节 + 清出入链。

    只用于**新建页**（INSERT 未 strip 的路径）；合并/更新页已由 `_apply_content_update` 投影。
    """
    import ke_db, ke_pages  # noqa: PLC0415
    row = ke_db.psql_csv(
        "SELECT COALESCE(content,'') AS content FROM wiki_pages "
        "WHERE knowledge_base_id=%s AND slug=%s AND deleted_at IS NULL"
        % (ke_db.sql_str(kb_id), ke_db.sql_str(slug)))
    if not row:
        return {"ok": False, "slug": slug, "reason": "页不存在"}
    content = row[0]["content"]
    edges = [(r["type"], r["slug"]) for r in ke_pages.parse_out_relations(content) if r.get("slug")]
    projected = project_page(kb_id, slug, edges=edges)
    stripped = strip_relation_sections(content)
    ke_db.psql(
        "UPDATE wiki_pages SET content = %s, out_links = '[]'::jsonb, in_links = '[]'::jsonb, "
        "updated_at = now() WHERE knowledge_base_id = %s AND slug = %s;"
        % (ke_db.sql_str(stripped), ke_db.sql_str(kb_id), ke_db.sql_str(slug)), stdin=True)
    return {"ok": True, "slug": slug, "edges": len(edges), "projected": projected.get("ok")}


def strip_wiki_page(kb_id: str, slug: str) -> dict:
    """只 strip 该页正文（去关系小节 + 清出入链），**不动图边**（图边已由 add_edge 直写）。"""
    import ke_db  # noqa: PLC0415
    row = ke_db.psql_csv(
        "SELECT COALESCE(content,'') AS content FROM wiki_pages "
        "WHERE knowledge_base_id=%s AND slug=%s AND deleted_at IS NULL"
        % (ke_db.sql_str(kb_id), ke_db.sql_str(slug)))
    if not row:
        return {"ok": False, "slug": slug, "reason": "页不存在"}
    stripped = strip_relation_sections(row[0]["content"])
    ke_db.psql(
        "UPDATE wiki_pages SET content = %s, out_links = '[]'::jsonb, in_links = '[]'::jsonb, "
        "updated_at = now() WHERE knowledge_base_id = %s AND slug = %s;"
        % (ke_db.sql_str(stripped), ke_db.sql_str(kb_id), ke_db.sql_str(slug)), stdin=True)
    return {"ok": True, "slug": slug}


def instance_count(kb_id: str) -> int:
    rows = _run("MATCH (n:BodhiInstance {kb_id:$kb}) RETURN count(n) AS n", {"kb": kb_id})
    return int((rows[0].get("n") if rows else 0) or 0)


def delete_kb_graph(kb_id: str) -> int:
    rows = _run("MATCH (n:BodhiInstance {kb_id:$kb}) WITH n, count(*) AS c DETACH DELETE n "
                "RETURN count(*) AS n", {"kb": kb_id})
    return int((rows[0].get("n") if rows else 0) or 0)
