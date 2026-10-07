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


def _ensure_index() -> None:
    try:
        ke_neo4j.query(
            "CREATE INDEX bodhi_instance_idx IF NOT EXISTS "
            "FOR (n:BodhiInstance) ON (n.kb_id, n.slug)")
    except Exception:  # noqa: BLE001  老版本 Neo4j 不支持 IF NOT EXISTS
        try:
            ke_neo4j.query("CREATE INDEX ON :BodhiInstance(kb_id, slug)")
        except Exception:  # noqa: BLE001  已存在等
            pass


def _module_of(page_type: str) -> str:
    return (page_type or "").split(":", 1)[0] if ":" in (page_type or "") else ""


def _attrs_from_content(content: str) -> dict[str, str]:
    """正文「## 属性（数据属性）」小节的属性行 → {键去前缀: 值}（md 表达 = 数据属性的伴生）。"""
    out: dict[str, str] = {}
    sec = ""
    for line in (content or "").splitlines():
        if line.startswith("## "):
            sec = line[3:].strip()
            continue
        if sec not in ("属性（数据属性）", "属性"):
            continue
        m = re.match(r"^-\s*([A-Za-z_][\w:]*)\b[^\n=]*=\s*(.+?)\s*$", line.strip())
        if m:
            out[m.group(1).split(":")[-1]] = m.group(2).strip()
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


def rebuild_kb_graph(kb_id: str, tenant_id: int | None = None) -> dict:
    """按 PG 页**幂等全量重建**该知识库的实例层（先清本库旧实例，再灌）。"""
    import ke_db
    _ensure_index()
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

    # 先清本库旧实例（幂等重灌）
    _run("MATCH (n:BodhiInstance {kb_id:$kb}) DETACH DELETE n", {"kb": kb_id})
    nodes = 0
    edges = 0
    for r in rows:
        # 数据属性来源：正文属性行（md 表达）作兜底，page_metadata 机器口径优先覆盖
        attrs = _attrs_from_content(r["content"])
        attrs.update(_attrs_of(r["meta"]))
        params = {
            "kb": kb_id, "slug": r["slug"], "pt": r["page_type"],
            "module": _module_of(r["page_type"]), "name": r["title"],
            "tenant": r.get("tenant_id"),
        }
        sets = ["n.page_type=$pt", "n.module=$module", "n.name=$name", "n.tenant_id=$tenant"]
        for k, v in attrs.items():
            sets.append("n.%s=$%s" % (k, "a_" + k))
            params["a_" + k] = v
        _run("MERGE (n:BodhiInstance {kb_id:$kb, slug:$slug}) SET %s" % ", ".join(sets), params)
        nodes += 1
    # 边（先建节点，再连边，避免顺序问题）
    for r in rows:
        for rel_type, tgt in _edges_of(r["content"], known):
            _run("MATCH (a:BodhiInstance {kb_id:$kb, slug:$src}) "
                 "MATCH (b:BodhiInstance {kb_id:$kb, slug:$tgt}) "
                 "MERGE (a)-[e:`%s`]->(b)" % rel_type.replace("`", ""),
                 {"kb": kb_id, "src": r["slug"], "tgt": tgt})
            edges += 1
    return {"ok": True, "kb_id": kb_id, "nodes": nodes, "edges": edges}


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
    """wiki 伴生化（2026-10-05 重构）：清掉正文里与图重复的「本体关系 / 被引用」小节，
    并**清空出入链**（in_links/out_links = []，关系一律在图里；WeKnora 原生链不再由我们维护）。"""
    import ke_db
    rows = ke_db.psql_csv(
        "SELECT slug, COALESCE(content,'') AS content FROM wiki_pages "
        "WHERE knowledge_base_id = %s AND deleted_at IS NULL" % ke_db.sql_str(kb_id))
    changed = 0
    for r in rows:
        new = strip_relation_sections(r["content"])
        if new != r["content"]:
            changed += 1
        if not dry_run:
            ke_db.psql(
                "UPDATE wiki_pages SET content = %s, out_links = '[]'::jsonb, in_links = '[]'::jsonb, "
                "updated_at = now() WHERE knowledge_base_id = %s AND slug = %s;"
                % (ke_db.sql_str(new), ke_db.sql_str(kb_id), ke_db.sql_str(r["slug"])), stdin=True)
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
            "target_slug": x["s"], "target_title": x.get("nm") or x["s"],
            "target_type": x.get("pt") or "", "target_exists": True, "range_ok": True}
           for x in out_r]
    in_r = _run("MATCH (m:BodhiInstance)-[r]->(n:BodhiInstance {kb_id:$kb, slug:$slug}) "
                "RETURN type(r) AS t, m.slug AS s, m.name AS nm, m.page_type AS pt ORDER BY t, s",
                {"kb": kb_id, "slug": slug})
    inbound = [{"source_slug": x["s"], "source_title": x.get("nm") or x["s"],
                "source_type": x.get("pt") or "", "label": tl(x["t"]), "type": x["t"],
                "type_label": tl(x["t"])} for x in in_r]
    return {"slug": slug, "title": node.get("name") or slug, "page_type": node.get("pt") or "",
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
    if page_type:
        _run("MERGE (n:BodhiInstance {kb_id:$kb, slug:$slug}) SET n.page_type=$pt",
             {"kb": kb_id, "slug": slug, "pt": page_type})
    else:
        _run("MERGE (n:BodhiInstance {kb_id:$kb, slug:$slug})", {"kb": kb_id, "slug": slug})


def _etyp(t: str) -> str:
    return (t or "").replace("`", "")


def add_edge(kb_id: str, slug: str, rel_type: str, target_slug: str, label: str = "") -> dict:
    """新增一条实例边：本页 --rel_type--> target（**直写图**，range 校验）。"""
    if not rel_type or not target_slug:
        raise ValueError("缺少 rel_type / target_slug")
    if target_slug == slug:
        raise ValueError("不能把关系指向本页")
    tt = _page_type(kb_id, target_slug)
    if not tt:
        raise ValueError("目标页不存在：%s" % target_slug)
    _check_domain(rel_type, _page_type(kb_id, slug))
    _check_range(rel_type, tt)
    _ensure_instance(kb_id, slug, _page_type(kb_id, slug))
    _ensure_instance(kb_id, target_slug, tt)
    dup = _run("MATCH (a:BodhiInstance {kb_id:$kb, slug:$s})-[r:`%s`]->(b:BodhiInstance {kb_id:$kb, slug:$t}) "
               "RETURN count(r) AS n" % _etyp(rel_type), {"kb": kb_id, "s": slug, "t": target_slug})
    if dup and dup[0].get("n"):
        return {"changed": False, "reason": "同样的关系已存在", "slug": slug, "version": 1,
                "relations": relations_of(kb_id, slug)}
    _run("MATCH (a:BodhiInstance {kb_id:$kb, slug:$s}) MATCH (b:BodhiInstance {kb_id:$kb, slug:$t}) "
         "MERGE (a)-[r:`%s`]->(b)" % _etyp(rel_type), {"kb": kb_id, "s": slug, "t": target_slug})
    return {"changed": True, "action": "add", "slug": slug, "version": 1,
            "relation": {"type": rel_type, "target_slug": target_slug}, "relations": relations_of(kb_id, slug)}


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
        nodes.append({"slug": r["slug"], "title": r.get("name") or r["slug"], "page_type": pt,
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
        "SELECT slug, COALESCE(page_type,'') AS pt FROM wiki_pages "
        "WHERE knowledge_base_id=%s AND deleted_at IS NULL "
        "AND COALESCE(page_type,'') NOT IN ('index','summary')" % ke_db.sql_str(kb_id))
    page_map = {r["slug"]: r["pt"] for r in pages}
    insts = _run("MATCH (n:BodhiInstance {kb_id:$kb}) RETURN n.slug AS slug, n.page_type AS pt", {"kb": kb_id})
    inst_map = {r["slug"]: (r.get("pt") or "") for r in insts}
    out = {
        "kb_id": kb_id, "pages": len(page_map), "instances": len(inst_map),
        "missing_instance": sorted(set(page_map) - set(inst_map)),
        "orphan_instance": sorted(set(inst_map) - set(page_map)),
        "type_mismatch": [s for s in sorted(set(page_map) & set(inst_map))
                          if page_map.get(s) and inst_map.get(s) and page_map[s] != inst_map[s]],
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
                     or out["invalid_class"] or out["invalid_edge_types"])
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
    params = {"kb": kb_id, "slug": slug, "pt": r["pt"], "module": _module_of(r["pt"]),
              "name": r["title"], "tenant": r.get("tenant_id")}
    sets = ["n.page_type=$pt", "n.module=$module", "n.name=$name", "n.tenant_id=$tenant"]
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


def instance_count(kb_id: str) -> int:
    rows = _run("MATCH (n:BodhiInstance {kb_id:$kb}) RETURN count(n) AS n", {"kb": kb_id})
    return int((rows[0].get("n") if rows else 0) or 0)


def delete_kb_graph(kb_id: str) -> int:
    rows = _run("MATCH (n:BodhiInstance {kb_id:$kb}) WITH n, count(*) AS c DETACH DELETE n "
                "RETURN count(*) AS n", {"kb": kb_id})
    return int((rows[0].get("n") if rows else 0) or 0)
