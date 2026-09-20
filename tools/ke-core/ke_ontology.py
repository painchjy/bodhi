"""ke-core · 本体查询（Neo4j 为准，JSON 编译产物兜底）。

用途（对应用户 2026-09-19 需求 1/2）
-----------------------------------
1. **类清单**：`classes()` —— wiki 编辑页的本体类型下拉（带模块前缀 + 中文 label）。
2. **按类筛关系类型**：`relation_types_for(page_type)` —— 取该类的对象属性，
   **含父类继承**（`BODHI_SUBCLASS_OF` 闭包），并回带 `range`（能连到哪些类）。
3. **按 range 找目标页**：`target_closure(rel_type)` + `target_pages()` ——
   range 类的**子类闭包**内的 wiki 页（同样考虑类层次）。

数据来源
--------
- 主：Neo4j 本体投影（`artifacts/neo4j/10_ontology.cypher`，ontology-compiler 生成）。
- 兜底：`artifacts/weknora/ontology_index.json`（同一份 TTL 的编译产物，含 label/color/
  parents/relations）。Neo4j 挂了也能出结果，并在返回值里标 `source`，便于排查。

所有函数只读，不做任何写库操作。
"""

from __future__ import annotations

import json
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import ke_db  # noqa: E402
import ke_neo4j  # noqa: E402

INDEX_PATH = HERE.parents[1] / "artifacts" / "weknora" / "ontology_index.json"

# 模块展示顺序（与 artifacts 的 module_order 一致；Neo4j 里也能查，这里只做排序兜底）
_MODULE_ORDER_FALLBACK = ["bmm", "ea", "ea-service", "ea-ownership", "bmm-fd"]

_INDEX_CACHE: dict | None = None
# 产物指纹 (mtime, size)：磁盘上的 ontology_index.json 变了就重读 —— 这样
# 在**别的进程**里编译（Windows 侧 compile.py、CLI、refresh_ontology_kb.sh）也能自动生效。
_INDEX_STAMP: tuple | None = None


def invalidate_cache() -> None:
    """清空编译产物缓存（下次调用惰性重读）。

    什么时候需要：`ke_admin.load()` 在**同一进程内**重编译/灌库/重投影之后应立即调用，
    否则 `_INDEX_CACHE` 还是加载前的旧类清单 —— 表现为维护后新模块的类
    `/bodhi/ontology/relation-types?page_type=<新类>` 回 `source: "unknown-class"`、
    关系类型为空，直到重启服务才恢复（2026-09-20 新增模块 ea-test 实测踩到）。
    """
    global _INDEX_CACHE, _INDEX_STAMP
    _INDEX_CACHE = None
    _INDEX_STAMP = None


def index_stamp() -> tuple | None:
    """ontology_index.json 的 (mtime, size)；文件不存在返回 None。"""
    try:
        st = INDEX_PATH.stat()
        return (st.st_mtime, st.st_size)
    except OSError:
        return None


# 本体图谱配色（用户口径 2026-09-19：图要简单，**节点颜色 = 模块差异**）。
# ⚠️ 配色只在此处维护（原先另一份常量在 `src/ontology/neo4j_store.py`，该目录已于 2026-09-20 删除，不要再引用它）。
MODULE_COLORS = {
    "bmm": "#3b82f6",          # 蓝：BMM 业务动机模型
    "ea": "#10b981",           # 绿：EA 企业架构
    "ea-service": "#f59e0b",   # 橙：EA 服务契约扩展
    "ea-ownership": "#ef4444", # 红：EA 所有权与控制关系扩展
    "bmm-fd": "#8b5cf6",       # 紫：BMM 规则可执行化扩展
    "external": "#94a3b8",     # 灰：外部词汇占位
}
DEFAULT_MODULE_COLOR = "#64748b"


def module_color(module_key: str) -> str:
    return MODULE_COLORS.get(module_key or "", DEFAULT_MODULE_COLOR)


def module_label(module_key: str) -> str:
    """模块中文名（来自编译产物；未知则回退 key）。"""
    for model in (index_data().get("models") or []):
        if model.get("key") == module_key:
            return model.get("label") or module_key
    return module_key


# ---------------------------------------------------------------------------
# JSON 编译产物（兜底 + 颜色/顺序增强）
# ---------------------------------------------------------------------------
def index_data() -> dict:
    global _INDEX_CACHE, _INDEX_STAMP
    stamp = index_stamp()
    # 惰性加载 + 指纹校验：缓存为空，或产物被重新编译过（mtime/size 变）就重读
    if _INDEX_CACHE is None or (stamp is not None and stamp != _INDEX_STAMP):
        try:
            _INDEX_CACHE = json.loads(INDEX_PATH.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            _INDEX_CACHE = {"models": [], "module_order": _MODULE_ORDER_FALLBACK}
        _INDEX_STAMP = stamp
    return _INDEX_CACHE


def _index_classes() -> dict[str, dict]:
    """prefixed -> {label, color, module, module_label, parents, order}（来自 JSON）。"""
    out: dict[str, dict] = {}
    order = 0
    for mi, model in enumerate(index_data().get("models") or []):
        for cls in model.get("classes") or []:
            order += 1
            out[cls["name"]] = {
                "label": cls.get("label") or cls["name"],
                "color": cls.get("color") or "#94a3b8",
                "module": model["key"],
                "module_label": model.get("label") or model["key"],
                "parents": list(cls.get("parents") or []),
                "is_enum": bool(cls.get("is_enum")),
                "order": mi * 1000 + order,
            }
    return out


def _index_relations() -> list[dict]:
    """[{prefixed, label, module, domain[], range[], inverse_of}]（来自 JSON）。"""
    out: list[dict] = []
    for model in (index_data().get("models") or []):
        for rel in model.get("relations") or []:
            out.append({
                "prefixed": rel.get("name") or "",
                "label": rel.get("label") or rel.get("name") or "",
                "module": model["key"],
                "module_label": model.get("label") or model["key"],
                "domain": list(rel.get("domain") or []),
                "range": list(rel.get("range") or []),
                "inverse_of": rel.get("inverse_of") or "",
            })
    return out


def _module_label_map() -> dict[str, str]:
    return {m["key"]: m.get("label") or m["key"] for m in (index_data().get("models") or [])}


def _module_order() -> list[str]:
    order = [m["key"] for m in (index_data().get("models") or [])]
    return order or _MODULE_ORDER_FALLBACK


# ---------------------------------------------------------------------------
# 类清单
# ---------------------------------------------------------------------------
def classes(include_external: bool = False) -> dict:
    """全部本体类（含模块前缀与中文 label）。Neo4j 优先，JSON 增强/兜底。"""
    idx = _index_classes()
    rows: list[dict] = []
    source = "neo4j"
    try:
        if not ke_neo4j.available():
            raise RuntimeError("Neo4j 投影为空或不可用")
        cypher = (
            "MATCH (c:BodhiOntClass) WHERE c.bodhi_projection = 'ontology' "
            + ("" if include_external else "AND c.external IS NULL ")
            + "OPTIONAL MATCH (m:BodhiModule)-[:BODHI_DECLARES]->(c) "
              "RETURN c.prefixed AS prefixed, c.label AS label, c.local_name AS local_name, "
              "       c.module AS module, c.is_enum AS is_enum, m.label AS module_label "
              "ORDER BY c.module, c.local_name"
        )
        rows = [dict(r) for r in ke_neo4j.query(cypher)]
    except Exception as exc:  # noqa: BLE001
        source = "json: %s" % exc
        mod_label = _module_label_map()
        for name, meta in idx.items():
            rows.append({"prefixed": name, "label": meta["label"],
                         "local_name": name.split(":", 1)[-1], "module": meta["module"],
                         "module_label": mod_label.get(meta["module"], meta["module"]),
                         "is_enum": meta["is_enum"]})

    out = []
    for row in rows:
        name = row.get("prefixed") or ""
        if not name or name.startswith("http"):
            continue
        meta = idx.get(name) or {}
        out.append({
            "prefixed": name,
            "label": row.get("label") or meta.get("label") or name,
            "module": row.get("module") or meta.get("module") or "",
            "module_label": row.get("module_label") or meta.get("module_label") or "",
            "color": meta.get("color") or "#94a3b8",
            "is_enum": str(row.get("is_enum")).lower() == "true" or bool(meta.get("is_enum")),
            "order": meta.get("order", 999999),
        })
    order = _module_order()
    out.sort(key=lambda r: (order.index(r["module"]) if r["module"] in order else 99,
                            r["order"], r["prefixed"]))
    return {"source": source, "classes": out}


def class_meta() -> dict[str, dict]:
    """{prefixed: 类元信息} —— 供分组/分类路径/标签使用（含父类）。"""
    out: dict[str, dict] = {}
    for name, meta in _index_classes().items():
        out[name] = {"key": name, "label": meta["label"], "color": meta["color"],
                     "module": meta["module"], "module_label": meta["module_label"],
                     "parents": list(meta["parents"]), "is_enum": meta["is_enum"],
                     "order": meta["order"]}
    # 补录"不在编译产物里的类" —— **上传导入的模块不产 json**（见 docs/session-handoff.md §3.4bis）。
    # 类清单的真源是 Neo4j 投影，这里把 json 里没有的类补进来（label/模块取 Neo4j，父类由下面的循环补）。
    # 实测：不补录的话 relation-types?page_type=<上传模块的类> 会回 unknown-class、改类型也会被判"未知本体类型"。
    try:
        if ke_neo4j.available():
            for row in ke_neo4j.query(
                    "MATCH (c:BodhiOntClass) WHERE c.bodhi_projection = 'ontology' "
                    "AND coalesce(c.external, false) = false AND c.prefixed IS NOT NULL "
                    "RETURN c.prefixed AS prefixed, coalesce(c.label,'') AS label, "
                    "       coalesce(c.module,'') AS module"):
                name = row.get("prefixed")
                if not name or name in out:
                    continue
                mod = row.get("module") or ""
                out[name] = {"key": name, "label": row.get("label") or name,
                             "color": module_color(mod), "module": mod,
                             "module_label": module_label(mod) or mod,
                             "parents": list(), "is_enum": False, "order": 999999}
    except Exception:  # noqa: BLE001  （补录失败不影响 json 版结果）
        pass
    try:
        if ke_neo4j.available():
            for row in ke_neo4j.query(
                    "MATCH (c:BodhiOntClass)-[:BODHI_SUBCLASS_OF]->(p:BodhiOntClass) "
                    "WHERE c.bodhi_projection = 'ontology' AND p.bodhi_projection = 'ontology' "
                    "RETURN c.prefixed AS c, p.prefixed AS p ORDER BY c.prefixed, p.prefixed"):
                name = row.get("c")
                if name in out and row.get("p"):
                    out[name]["parents"] = [row["p"]]
    except Exception:  # noqa: BLE001  （父类继续用 JSON 版本，够用）
        pass
    return out


def ancestors(type_name: str, meta: dict[str, dict] | None = None) -> list[str]:
    """类的祖先闭包（含自身，由近及远）。Neo4j 优先。"""
    if not type_name:
        return []
    try:
        if ke_neo4j.available():
            rows = ke_neo4j.query(
                "MATCH (c:BodhiOntClass {prefixed: $p})-[:BODHI_SUBCLASS_OF*0..]->(s:BodhiOntClass) "
                "RETURN s.prefixed AS prefixed", {"p": type_name})
            got = [r["prefixed"] for r in rows if r.get("prefixed")]
            if got:
                return got
    except Exception:  # noqa: BLE001
        pass
    meta = meta or class_meta()
    out, seen, cur = [], set(), type_name
    while cur and cur not in seen:
        out.append(cur)
        seen.add(cur)
        plist = (meta.get(cur) or {}).get("parents") or []
        cur = plist[0] if plist else ""
    return out


def descendants(type_name: str, meta: dict[str, dict] | None = None) -> list[str]:
    """类的后代闭包（含自身）—— range 的实际可连范围。"""
    if not type_name:
        return []
    try:
        if ke_neo4j.available():
            rows = ke_neo4j.query(
                "MATCH (s:BodhiOntClass)-[:BODHI_SUBCLASS_OF*0..]->(c:BodhiOntClass {prefixed: $p}) "
                "RETURN s.prefixed AS prefixed", {"p": type_name})
            got = [r["prefixed"] for r in rows if r.get("prefixed")]
            if got:
                return got
    except Exception:  # noqa: BLE001
        pass
    meta = meta or class_meta()
    out, queue = [], [type_name]
    while queue:
        cur = queue.pop(0)
        if cur in out:
            continue
        out.append(cur)
        for name, info in meta.items():
            if cur in (info.get("parents") or []):
                queue.append(name)
    return out


def top_group(type_name: str, meta: dict[str, dict] | None = None) -> str:
    meta = meta or class_meta()
    chain = ancestors(type_name, meta)
    top = chain[-1] if chain else type_name
    info = meta.get(top) or {}
    return info.get("label") or top


def category_path(type_name: str) -> list[str]:
    """目录路径 = [模型中文名, 顶层大类中文名]（与前端两级折叠一致）。"""
    meta = class_meta()
    info = meta.get(type_name) or {}
    module_label = info.get("module_label") or info.get("module") or type_name
    label = info.get("label") or type_name
    group = top_group(type_name, meta) or label
    return [module_label, group]


# ---------------------------------------------------------------------------
# 按类筛对象属性（含继承）
# ---------------------------------------------------------------------------
def relation_types_for(page_type: str) -> dict:
    """该（本体类型的）页面可用的**对象属性**清单。

    - `domain` ∈ 该类的祖先闭包 ⇒ **可继承**使用（父类定义的关系子类可用）；
    - 回带 `range`（能连到的类）与 `inverse_of`（反向属性名）。
    """
    meta = class_meta()
    if page_type not in meta:
        return {"source": "unknown-class", "page_type": page_type, "relation_types": []}

    rows: list[dict] = []
    source = "neo4j"
    try:
        if not ke_neo4j.available():
            raise RuntimeError("Neo4j 投影为空或不可用")
        cypher = (
            "MATCH (c:BodhiOntClass {prefixed: $p})-[:BODHI_SUBCLASS_OF*0..]->(anc:BodhiOntClass) "
            "MATCH (prop:BodhiOntProperty {property_kind: 'object'})-[:BODHI_DOMAIN]->(anc) "
            "WHERE prop.bodhi_projection = 'ontology' "
            "OPTIONAL MATCH (prop)-[:BODHI_RANGE]->(r:BodhiOntClass) "
            "OPTIONAL MATCH (prop)-[:BODHI_INVERSE_OF]->(inv:BodhiOntProperty) "
            "OPTIONAL MATCH (inv)-[:BODHI_DOMAIN]->(invdom:BodhiOntClass) "
            "RETURN prop.prefixed AS prefixed, prop.label AS label, prop.module AS module, "
            "       anc.prefixed AS domain, anc.label AS domain_label, "
            "       collect(DISTINCT r.prefixed) AS range, collect(DISTINCT r.label) AS range_labels, "
            "       inv.prefixed AS inverse_of, collect(DISTINCT invdom.prefixed) AS inverse_domains "
            "ORDER BY prop.prefixed"
        )
        for row in ke_neo4j.query(cypher, {"p": page_type}):
            rows.append({
                "prefixed": row.get("prefixed") or "",
                "label": row.get("label") or row.get("prefixed") or "",
                "module": row.get("module") or "",
                "domain": row.get("domain") or "",
                "domain_label": row.get("domain_label") or "",
                "range": [x for x in (row.get("range") or []) if x],
                "range_labels": [x for x in (row.get("range_labels") or []) if x],
                "inverse_of": row.get("inverse_of") or "",
                "inverse_domains": [x for x in (row.get("inverse_domains") or []) if x],
            })
        # 没有声明 domain 的对象属性对任何类开放（投影里可能有外部 domain 的占位）
        extra = ke_neo4j.query(
            "MATCH (prop:BodhiOntProperty {property_kind: 'object'}) "
            "WHERE prop.bodhi_projection = 'ontology' "
            "AND NOT (prop)-[:BODHI_DOMAIN]->(:BodhiOntClass) "
            "RETURN prop.prefixed AS prefixed, prop.label AS label, prop.module AS module "
            "ORDER BY prop.prefixed")
        have = {r["prefixed"] for r in rows}
        for row in extra:
            if row.get("prefixed") and row["prefixed"] not in have:
                rows.append({"prefixed": row["prefixed"], "label": row.get("label") or row["prefixed"],
                             "module": row.get("module") or "", "domain": "",
                             "domain_label": "（未声明 domain）", "range": [], "range_labels": [],
                             "inverse_of": "", "inverse_domains": []})
    except Exception as exc:  # noqa: BLE001
        source = "json: %s" % exc
        chain = set(ancestors(page_type, meta))
        for rel in _index_relations():
            if rel["domain"] and not (set(rel["domain"]) & chain):
                continue
            first = rel["domain"][0] if rel["domain"] else ""
            rows.append({"prefixed": rel["prefixed"], "label": rel["label"], "module": rel["module"],
                         "domain": first,
                         "domain_label": (meta.get(first) or {}).get("label", "")
                         if first else "（未声明 domain）",
                         "range": list(rel["range"]), "range_labels": [],
                         "inverse_of": rel["inverse_of"], "inverse_domains": []})

    for row in rows:
        if not row["range_labels"]:
            row["range_labels"] = [(meta.get(r) or {}).get("label") or r for r in row["range"]]
        row["inherited_from"] = row["domain"] if row["domain"] and row["domain"] != page_type else ""
        row["range_display"] = "、".join(
            "%s（%s）" % (lab, ref) for lab, ref in zip(row["range_labels"], row["range"]))
    rows.sort(key=lambda r: (r["module"], r["prefixed"]))
    return {"source": source, "page_type": page_type, "relation_types": rows}


def relation_type_map(page_type: str) -> dict[str, dict]:
    return {r["prefixed"]: r for r in relation_types_for(page_type)["relation_types"]}


def target_closure(rel_type: str, meta: dict[str, dict] | None = None) -> list[str]:
    """某对象属性的 range 闭包（range 类 + 其所有子类）——「能连到哪些类」。"""
    meta = meta or class_meta()
    ranges: list[str] = []
    try:
        if ke_neo4j.available():
            rows = ke_neo4j.query(
                "MATCH (p:BodhiOntProperty {prefixed: $p})-[:BODHI_RANGE]->(r:BodhiOntClass) "
                "RETURN r.prefixed AS r", {"p": rel_type})
            ranges = [x["r"] for x in rows if x.get("r")]
    except Exception:  # noqa: BLE001
        pass
    if not ranges:
        for rel in _index_relations():
            if rel["prefixed"] == rel_type:
                ranges = list(rel["range"])
                break
    out: list[str] = []
    for r in ranges:
        for cls in descendants(r, meta):
            if cls not in out:
                out.append(cls)
    return out


def target_pages(kb_id: str, rel_type: str, exclude_slug: str = "", q: str = "",
                 limit: int = 200) -> dict:
    """按对象属性的 range 闭包，找可连的目标 wiki 页（同库、未删除、排除自己）。"""
    allowed = target_closure(rel_type)
    if not allowed:
        return {"source": "no-range", "relation_type": rel_type, "classes": [], "pages": []}
    cond = ("knowledge_base_id = %s AND deleted_at IS NULL AND page_type IN (%s) AND slug <> %s"
            % (ke_db.sql_str(kb_id), ", ".join(ke_db.sql_str(t) for t in allowed),
               ke_db.sql_str(exclude_slug)))
    if q:
        cond += " AND (title ILIKE %s OR slug ILIKE %s)" % (
            ke_db.sql_str("%" + q + "%"), ke_db.sql_str("%" + q + "%"))
    rows = ke_db.psql_csv(
        "SELECT slug, title, page_type, COALESCE(version,1) AS version FROM wiki_pages "
        "WHERE %s ORDER BY page_type, title LIMIT %d" % (cond, max(1, min(int(limit), 500))))
    meta = class_meta()
    pages = [{"slug": r["slug"], "title": r["title"], "page_type": r["page_type"],
              "type_label": (meta.get(r["page_type"]) or {}).get("label") or r["page_type"],
              "version": int(r["version"] or 1)} for r in rows]
    return {"source": "neo4j", "relation_type": rel_type, "classes": allowed,
            "class_labels": [(meta.get(c) or {}).get("label") or c for c in allowed],
            "pages": pages}



