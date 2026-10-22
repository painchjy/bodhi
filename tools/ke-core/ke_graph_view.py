"""Graph views (P3 slice A: moved verbatim out of server.py).

Read-only: Neo4j ontology projection + PG pages -> nodes/edges for the graph page.
No protocol details here (transport lives in srv/http.py).
"""
from __future__ import annotations

import json
import re

import ke_db
import ke_ontology

psql_csv = ke_db.psql_csv
sql_str = ke_db.sql_str
now_text = ke_db.now_text


def _class_meta(model_key: str) -> dict[str, dict]:
    meta = ke_ontology.class_meta()
    if not model_key:
        return meta
    return {k: v for k, v in meta.items() if v.get("module") == model_key}


def all_class_meta() -> dict[str, dict]:
    """兼容别名：实现见 tools/ke-core/ke_ontology.py（标签/颜色/父类）。"""
    return ke_ontology.class_meta()


def class_group(type_name: str) -> str:
    """类的「大类」= 顶层父类的中文名（用于列表/树的二级分组）。"""
    return ke_ontology.top_group(type_name)


def class_category_path(type_name: str, fallback_module: str = "",
                        fallback_module_label: str = "", fallback_label: str = "") -> list[str]:
    """页面的一级分类路径：**模型标签 → 大类**（树只按它折叠两级别）。

    用户口径（2026-09-19 验收）：不需要三级目录——第三层直接就是知识页，
    本体「类」这一层改为**页面行前面的类型标签**表达（见 patch_frontend.py 的 v2 调整）。
    这样即使本体自身还有更深的层级，也只由标签区分，不再多分折叠层。
    实现已并入 ke_ontology.category_path（Neo4j 优先，JSON 兜底）。
    """
    return ke_ontology.category_path(type_name)


# 本体模型库页面用的普通 markdown 内链（`[标题](wiki:slug)`）
_WIKI_LINK_RE = re.compile(r"\[(?P<label>[^\]]+)\]\(wiki:(?P<slug>[^)]+)\)")
# 按「所在小节」推断边类型（本体 wiki 的结构：父类/子类/相关关系/属性定义/…）
_SECTION_EDGE = (
    ("父类", ("subclass", "父类")),
    ("子类", ("subclass", "子类")),
    ("相关关系", ("relation", "相关关系")),
    ("跨模块桥", ("relation", "跨模块桥")),
    ("本体关系", ("relation", "本体关系")),
    ("关系页", ("relation", "关系页")),
    ("反向属性", ("relation", "反向属性")),
    ("属性定义", ("property", "属性定义")),
    ("本体属性", ("property", "本体属性")),
    ("定义域", ("property", "定义域")),
    ("值域", ("property", "值域")),
    ("本体类", ("class", "包含类")),
)


def _section_edge(section: str, link_label: str):
    """本体 wiki 的内链 → (边类型, 中文标签)；识别不出小节时退回 (wiki, 链接文字)。"""
    for prefix, pair in _SECTION_EDGE:
        if section.startswith(prefix):
            return pair[0], ("%s：%s" % (pair[1], link_label)) if link_label else pair[1]
    return "wiki", (section + "：" + link_label) if section else (link_label or "链接")


def bodhi_model_graph(model: str = "", kb_id: str = "") -> dict:
    """**本体模型结构图**（本体模型库专用）：节点 = 本体类，边 = 子类 + 对象属性(domain→range)。

    与 `bodhi_graph`（按 wiki 页画图：类/关系/属性各是一页 → 关系也成了节点，且名字不全）的区别：
    这里直接读 **Neo4j 本体投影**，所以：
    - 节点只有**类**（带中文 label），颜色按**模块**；
    - 关系是**边**，边上带关系中文名（`label`）与 prefixed（`type`），子类是「子类」边。
    这样图就是「本体模型长什么样」，而不是「知识库里有哪些页」。
    """
    try:
        import ke_neo4j  # ke-core（与 bodhi-mcp 同目录体系）
        if not ke_neo4j.available():
            return {"view": "model", "model": model, "nodes": [], "edges": [],
                    "error": "Neo4j 本体投影不可用"}
    except Exception as exc:  # noqa: BLE001
        return {"view": "model", "model": model, "nodes": [], "edges": [],
                "error": "Neo4j 不可用：%s" % exc}

    params = {"m": model} if model else {}
    where = "WHERE c.external IS NULL " + ("AND c.module = $m " if model else "")
    rows = ke_neo4j.query(
        "MATCH (c:BodhiOntClass) " + where +
        "RETURN c.prefixed AS id, c.label AS label, c.local_name AS name, c.description AS definition, "
        "       c.module AS module ORDER BY c.module, c.local_name", params)
    nodes, ids = [], set()
    for r in rows:
        ident = r.get("id") or r.get("name") or ""
        if not ident or ident in ids:
            continue
        ids.add(ident)
        mod = r.get("module") or ""
        label = r.get("label") or r.get("name") or ident
        nodes.append({
            "slug": ident, "title": label, "page_type": "ontology:Class",
            "class_label": label, "module": mod, "module_label": ke_ontology.module_label(mod),
            "group": mod or "ontology", "group_label": ke_ontology.module_label(mod),
            "color": ke_ontology.module_color(mod), "version": 1,
            "summary": (r.get("definition") or "")[:200], "source_refs": [],
        })

    edges = []
    for r in ke_neo4j.query(
            "MATCH (c:BodhiOntClass)-[:BODHI_SUBCLASS_OF]->(p:BodhiOntClass) "
            "WHERE c.external IS NULL AND p.external IS NULL "
            "RETURN c.prefixed AS a, p.prefixed AS b"):
        if r.get("a") in ids and r.get("b") in ids:
            edges.append({"source": r["a"], "target": r["b"], "type": "subclass", "label": "子类"})
    for r in ke_neo4j.query(
            "MATCH (p:BodhiOntProperty {property_kind: 'object'})-[:BODHI_DOMAIN]->(d:BodhiOntClass) "
            "MATCH (p)-[:BODHI_RANGE]->(t:BodhiOntClass) "
            "WHERE d.external IS NULL AND t.external IS NULL "
            "RETURN p.prefixed AS pid, p.label AS plabel, d.prefixed AS a, t.prefixed AS b", {}):
        if r.get("a") in ids and r.get("b") in ids:
            edges.append({"source": r["a"], "target": r["b"],
                          "type": r.get("pid") or "relation",
                          "label": r.get("plabel") or r.get("pid") or ""})

    groups = {}
    for n in nodes:
        g = groups.setdefault(n["group"], {"label": n["group_label"], "color": n["color"], "count": 0})
        g["count"] += 1

    # 节点点击要能打开该类的 wiki 页：把节点 slug 换成**真实 wiki slug**（本体模型库里
    # `ontology/<模块>/<本地名小写>`），类名另存 `type_name`；页面不存在时保留 prefixed。
    if kb_id:
        def _wiki_slug(module_key: str, prefixed: str) -> str:
            """真实 wiki slug：`ontology/<模块 key>/<本地名小写>`。

            ⚠️ 模块 key 要用 Neo4j 的 `module`（ea-service / ea-ownership / bmm-fd），
            不能用 prefixed 的前缀（easvc:/eaown:/bmmfd:）——否则对不上 wiki 页（实测 26/47）。
            """
            local = prefixed.split(":", 1)[-1]
            return "ontology/%s/%s" % (module_key, re.sub(r"[^a-z0-9]+", "-", local.lower()).strip("-"))
        want = {n["slug"]: _wiki_slug(n.get("module") or "", n["slug"]) for n in nodes}
        try:
            rows = psql_csv(
                "SELECT slug FROM wiki_pages WHERE knowledge_base_id = %s AND deleted_at IS NULL "
                "AND slug IN (%s)"
                % (sql_str(kb_id), ", ".join(sql_str(s) for s in sorted(set(want.values())))))
            have = {r["slug"] for r in rows}
        except Exception:  # noqa: BLE001
            have = set()
        for n in nodes:
            n["type_name"] = n["slug"]
            if want.get(n["slug"]) in have:
                n["slug"] = want[n["slug"]]
        # ⚠️ 边的端点是 prefixed 名字（bmm:Goal），节点 slug 已换成 wiki slug，
        # 必须同步重映射，否则图里只剩节点、没有关系（2026-09-20 实测踩过）。
        remap = {n["type_name"]: n["slug"] for n in nodes}
        for edge in edges:
            edge["source"] = remap.get(edge["source"], edge["source"])
            edge["target"] = remap.get(edge["target"], edge["target"])

    return {"kb_id": kb_id, "model": model, "view": "model", "nodes": nodes, "edges": edges,
            "meta": {"node_count": len(nodes), "edge_count": len(edges),
                     "groups": [dict(key=k, **v) for k, v in sorted(groups.items())],
                     "relation_types": sorted({e["type"] for e in edges})}}


def bodhi_graph(kb_id: str, model: str = "", types: str = "", limit: int = 300) -> dict:
    """要素节点 + 关系边（实例页读「## 本体关系」；本体 wiki 页按小节解析 markdown 内链）。

    节点带 `group` / `color`（**按模块配色**，用户 2026-09-19 口径：图要简单，颜色 = 模块差异）。
    """
    # 2026-10-05 M3：普通知识库图谱**读图实例**（关系只在图里）；图有实例才走这里，否则退下面 PG 逻辑
    try:
        import ke_graph as _kg  # noqa: PLC0415
        if kb_id and _kg.instance_count(kb_id):
            return _kg.instance_graph(kb_id, model,
                                      [t.strip() for t in types.split(",") if t.strip()], limit)
    except Exception:  # noqa: BLE001
        pass
    wanted = [t.strip() for t in types.split(",") if t.strip()]
    colors = _class_meta(model)
    sql = ("SELECT slug, title, COALESCE(page_type,'') AS page_type, COALESCE(summary,'') AS summary, "
           "COALESCE(version,1) AS version, COALESCE(content,'') AS content, "
           "COALESCE(page_metadata::text,'{}') AS meta, COALESCE(source_refs::text,'[]') AS refs "
           "FROM wiki_pages WHERE knowledge_base_id = %s AND deleted_at IS NULL "
           "AND COALESCE(page_type,'') NOT IN ('index','summary',%s)"
           % (sql_str(kb_id), sql_str(TYPE_PENDING)))
    if model:
        sql += " AND page_type LIKE %s" % sql_str(model + ":%")
    if wanted:
        sql += " AND page_type IN (%s)" % ", ".join(sql_str(t) for t in wanted)
    sql += " ORDER BY page_type, title LIMIT %d" % max(1, min(limit, 2000))
    rows = psql_csv(sql)

    nodes, known, edges = [], set(), []
    # 是否「本体模型库」：它的页类型统一是 ontology:*（类/关系/属性各一页）。
    # 是 → 图谱按**模块**分组配色（用户口径）；否（普通知识库）→ 维持原来的**按页类型**配色，
    # 否则普通知识库会把所有实例节点并成一种颜色、图例只剩模型名（2026-09-19 实测回归）。
    ontology_kb = any((r.get("page_type") or "").startswith("ontology:") for r in rows)
    for row in rows:
        try:
            meta = (json.loads(row["meta"] or "{}").get("ontology") or {})
        except json.JSONDecodeError:
            meta = {}
        cls = colors.get(row["page_type"], {})
        module = (meta.get("model") or cls.get("module")
                  or (row["page_type"].split(":", 1)[0] if ":" in row["page_type"] else model or ""))
        class_label = cls.get("label") or meta.get("class_label") or row["page_type"]
        if ontology_kb:
            group = module or row["page_type"]
            group_label = (cls.get("module_label") or meta.get("model_label")
                           or (ke_ontology.module_label(module) if module else "")) or class_label
            color = ke_ontology.module_color(module) if module else (cls.get("color") or "#94a3b8")
        else:
            group, group_label = row["page_type"], class_label
            color = cls.get("color") or "#94a3b8"
        nodes.append({
            "slug": row["slug"], "title": row["title"], "page_type": row["page_type"],
            "class_label": class_label,
            "module": module, "module_label": group_label,
            "group": group, "group_label": group_label,
            "color": color,
            "version": row["version"],
            "summary": (row["summary"] or "")[:200],
            "source_refs": json.loads(row["refs"] or "[]"),
        })
        known.add(row["slug"])
    for row in rows:
        section = ""
        for line in (row["content"] or "").splitlines():
            if line.startswith("## "):
                section = line[3:].strip()
                continue
            hit = parse_rel_line(line.strip())
            if hit:
                target_slug = hit.group("slug")
                if target_slug not in known:      # 只画两端都在结果集里的边
                    continue
                edges.append({"source": row["slug"], "target": target_slug,
                              "type": hit.group("type"), "label": hit.group("label")})
                continue
            # 本体模型库的页用普通 markdown 内链（`[标题](wiki:slug)`），没有「关系行」；
            # 这里按**所在小节**推断边类型，让本体图谱真的画出模型结构（用户口径：图要简单）。
            for m in _WIKI_LINK_RE.finditer(line):
                target_slug = m.group("slug").strip()
                if target_slug not in known or target_slug == row["slug"]:
                    continue
                etype, elabel = _section_edge(section, m.group("label").strip())
                edges.append({"source": row["slug"], "target": target_slug,
                              "type": etype, "label": elabel})
    return {"kb_id": kb_id, "model": model, "nodes": nodes, "edges": edges,
            "meta": {"node_count": len(nodes), "edge_count": len(edges),
                     "relation_types": sorted({e["type"] for e in edges}),
                     "classes": sorted({n["page_type"] for n in nodes}),
                     "modules": sorted({n["module"] for n in nodes if n["module"]}),
                     "generated_at": now_text(), "limit": limit}}


def bodhi_page(kb_id: str, slug: str) -> dict:
    """取单页内容（供图谱页点击查看；也可给其他工具复用）。"""
    rows = psql_csv("SELECT slug, title, COALESCE(page_type,'') AS page_type, COALESCE(summary,'') AS summary, "
                    "COALESCE(content,'') AS content, COALESCE(version,1) AS version, "
                    "COALESCE(source_refs::text,'[]') AS refs, COALESCE(chunk_refs::text,'[]') AS chunks, "
                    "COALESCE(out_links::text,'[]') AS outs, COALESCE(in_links::text,'[]') AS ins, "
                    "COALESCE(page_metadata::text,'{}') AS meta, COALESCE(updated_at::text,'') AS updated_at "
                    "FROM wiki_pages WHERE knowledge_base_id = %s AND slug = %s AND deleted_at IS NULL"
                    % (sql_str(kb_id), sql_str(slug)))
    if not rows:
        raise RuntimeError("页面不存在：%s" % slug)
    row = rows[0]
    try:
        meta = (json.loads(row["meta"] or "{}").get("ontology") or {})
    except json.JSONDecodeError:
        meta = {}
    return {
        "slug": row["slug"], "title": row["title"], "page_type": row["page_type"],
        "summary": row["summary"], "content": row["content"], "version": row["version"],
        "source_refs": json.loads(row["refs"] or "[]"), "chunk_refs": json.loads(row["chunks"] or "[]"),
        "out_links": json.loads(row["outs"] or "[]"), "in_links": json.loads(row["ins"] or "[]"),
        "class_label": meta.get("class_label", ""), "updated_at": row["updated_at"],
        "merge_history": meta.get("merge_history", []),
    }
