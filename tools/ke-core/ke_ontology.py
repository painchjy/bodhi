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
import os
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


def module_short(module_key: str) -> str:
    """**模块短名**（TTL `bodhi:shortName`；未知则回退 label → key）。

    目录一级名必须用短名（≤16 字符、无全角括号）：长名会让前端目录路径对不上 → 目录里看不到页
    （2026-10-04 用户实测；口径与 `ontology_wiki.py` 的 `module.short_label or key` 一致）。
    """
    for model in (index_data().get("models") or []):
        if model.get("key") == module_key:
            return model.get("short_label") or model.get("label") or module_key
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
                # **模块短名**（TTL `bodhi:shortName`，≤16 字符、无全角括号）—— 目录一级名必须用它：
                # 2026-10-04 用户实测「目录里没有新页、统计不变」，根因就是这里用**长名**
                # （`智能体开发本体（业务智能体 + 技能/工具开发 + 评估与改进建议）`），
                # 而本体投影/其它库用短名 → 顶层目录名两套、前端按 category_path 分桶对不上。
                # 与 `ontology_wiki.py` 的目录口径（`module.short_label or key`）保持一致。
                "module_short": model.get("short_label") or model.get("label") or model["key"],
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


# ---------------------------------------------------------------------------
# 进程级元数据 memo（2026-10-10，P3-0g）
# 实测：本体元数据查询**每次都打 Neo4j**（relation_type_map=4 / target_closure=3 /
# category_path=3 / class_meta=3 / data_properties_for=2 / ancestors=1），而它们在
# per-node / per-edge 循环里被反复调用 —— 一次 15 节点/14 边的落库就产生 **621 次**
# Neo4j 往返（批次②23/16 → 1748 次）。这里按 `index_stamp()`（本体产物指纹）失效，
# 与 `data_properties()` **同一模式**：产物一变整片失效，不会读到旧本体。
_META_MEMO: dict = {}
_META_STAMP = None


def _memo_get(key):
    global _META_STAMP
    stamp = index_stamp()
    if stamp != _META_STAMP:
        _META_MEMO.clear()
        _META_STAMP = stamp
    return _META_MEMO.get(key)


def _memo_put(key, value):
    _META_MEMO[key] = value
    return value


def class_meta() -> dict[str, dict]:
    """{prefixed: 类元信息}（**进程级缓存**，按本体产物指纹失效；见 `_memo_get`）。"""
    hit = _memo_get("class_meta")
    if hit is None:
        hit = _memo_put("class_meta", _class_meta_uncached())
    return dict(hit)


def _class_meta_uncached() -> dict[str, dict]:
    """{prefixed: 类元信息} —— 供分组/分类路径/标签使用（含父类）。"""
    out: dict[str, dict] = {}
    for name, meta in _index_classes().items():
        out[name] = {"key": name, "label": meta["label"], "color": meta["color"],
                     "module": meta["module"], "module_label": meta["module_label"],
                     "module_short": meta.get("module_short") or meta["module_label"],
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
                             "module_short": module_short(mod) or mod,
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


# 知识**渲染模式**（2026-10-09 用户口径：**只有两种** —— 由本体决定，调用方不自己选）
RENDER_MODES = ("entity", "document")


def render_mode(type_name: str, meta: dict[str, dict] | None = None) -> str:
    """该类知识的渲染模式：`entity`（默认）或 `document`。

    **判定方式（2026-10-09 用户口径，不引入任何新注解）**：
    本体里给**半结构化（文档类）**的类声明一个数据属性 **`wikiContent`**
    （`wiki_content` 也行，匹配时归一化），谁声明了它、谁就按 `document` 渲染；
    其余一律 `entity`。好处：
      · 复用**已有的数据属性机制 + 类继承**（声明在基类上，子类自动继承），不需要新注解、
        也不需要改编译器（数据属性本来就编进 `ontology_index.json` 并投影成 `BodhiOntProperty`）；
      · 语义自洽 —— "这个类带一篇长正文" 本身就是"文档类"的定义。

    ⚠️ 判定**只看 `domains` 显式命中**（不认 `data_properties_for` 的"全局可用"兜底）：
    数据属性若**不声明 domain** 就是全局属性，那样会让**所有类**变 document —— 所以
    声明 `wikiContent` 时必须带 `rdfs:domain`。
    """
    key = (type_name or "").strip()
    if not key:
        return "entity"
    try:
        props = data_properties().get("properties") or {}
        chain = set(ancestors(key, meta))
    except Exception:  # noqa: BLE001  本体读不到 → 安全回落 entity
        return "entity"
    for name, info in props.items():
        local = str(name).split(":")[-1].replace("_", "").lower()
        if local != "wikicontent":
            continue
        domains = set((info or {}).get("domains") or [])
        if domains and (chain & domains):
            return "document"
    return "entity"


def ancestors(type_name: str, meta: dict[str, dict] | None = None) -> list[str]:
    """类的祖先闭包（含自身，由近及远）。Neo4j 优先（**进程级缓存**）。"""
    if not type_name:
        return []
    if meta is not None:                    # 显式传入视图 → 不缓存（调用方在自定义 meta 上算）
        return _ancestors_uncached(type_name, meta)
    key = ("ancestors", type_name)
    hit = _memo_get(key)
    if hit is None:
        hit = _memo_put(key, _ancestors_uncached(type_name, None))
    return list(hit)


def _ancestors_uncached(type_name: str, meta: dict[str, dict] | None = None) -> list[str]:
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
    """类的后代闭包（含自身）—— range 的实际可连范围（**进程级缓存**）。"""
    if not type_name:
        return []
    if meta is not None:
        return _descendants_uncached(type_name, meta)
    key = ("descendants", type_name)
    hit = _memo_get(key)
    if hit is None:
        hit = _memo_put(key, _descendants_uncached(type_name, None))
    return list(hit)


def _descendants_uncached(type_name: str, meta: dict[str, dict] | None = None) -> list[str]:
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


def top_ancestor(type_name: str, meta: dict[str, dict] | None = None) -> str:
    """**顶层类** = 沿 `subClassOf` 向上走到的根（= 直接继承 owl:Thing 的那个类）。

    2026-10-04 修（重要）：原实现用 `ancestors()[-1]`，而那条 Cypher **没有 ORDER BY**
    （`*0..` 变长路径的返回顺序**不保证**）⇒ 顶层类会**乱**：实测 `bmm:MainSystem`
    被算成「影响因素」→ `recategorize` 会把主系统页迁到错的目录。
    这里改为**确定性**取根：优先 Neo4j 里"没有任何父的祖先"（多根时取**路径最短**的），
    再退化为 JSON 里的单链游走（取第一个父直到无父）。
    """
    if not type_name:
        return ""
    # P0-c-2（2026-10-10）：**进程级 memo** —— 这条 Cypher 对**每个类型**都要打一遍，
    # 实测一次 33 页落库 `ke_ontology.top_group` = **33 次 Neo4j**（每页一次，且从不命中缓存）。
    # 结果只依赖本体产物（按 `index_stamp()` 失效，见 `_memo_get`）+ 类名，故与
    # `ancestors`/`descendants` 同款安全 memo。
    key = ("topanc", type_name)
    hit = _memo_get(key)
    if hit is not None:
        return hit
    try:
        if ke_neo4j.available():
            rows = ke_neo4j.query(
                "MATCH p=(c:BodhiOntClass {prefixed: $p})-[:BODHI_SUBCLASS_OF*1..]->(t:BodhiOntClass) "
                "WHERE NOT (t)-[:BODHI_SUBCLASS_OF]->(:BodhiOntClass) "
                "RETURN t.prefixed AS prefixed, length(p) AS d ORDER BY d ASC LIMIT 1",
                {"p": type_name})
            for row in rows:
                if row.get("prefixed"):
                    _memo_put(key, row["prefixed"])
                    return row["prefixed"]
    except Exception:  # noqa: BLE001
        pass
    meta = meta or class_meta()
    cur, seen, last = type_name, set(), type_name
    while cur and cur not in seen:
        seen.add(cur)
        last = cur
        plist = (meta.get(cur) or {}).get("parents") or []
        cur = plist[0] if plist else ""
    _memo_put(key, last)
    return last


def top_group(type_name: str, meta: dict[str, dict] | None = None) -> str:
    """顶层大类中文名（目录第二级）。**进程级 memo**（P0-c-2，2026-10-10）。

    实测归因：一次 33 页落库，`ke_ontology.top_group` **33 次 Neo4j**（每页一次 `top_ancestor`
    的 Cypher，且**从不命中缓存**）—— 它是纯本体函数（只依赖本体产物 + 继承链），按
    `index_stamp()` 失效即可，与 `class_meta`/`ancestors`/`descendants` 同款 memo。
    显式传 `meta` 时不缓存（调用方已自备元数据，可能是临时/覆盖版本）。
    """
    key = None
    if meta is None:
        key = ("topgroup", type_name)
        hit = _memo_get(key)
        if hit is not None:
            return hit
    _meta = meta or class_meta()
    top = top_ancestor(type_name, _meta) or type_name
    info = _meta.get(top) or {}
    out = info.get("label") or top
    if key is not None:
        _memo_put(key, out)
    return out


def category_path(type_name: str) -> list[str]:
    """目录路径 = [模型中文名, 顶层大类中文名]（与前端两级折叠一致）。

    **一级名一律用「模块短名」**（TTL `bodhi:shortName`，≤16 字符、无全角括号）：
    长名（`智能体开发本体（业务智能体 + 技能/工具开发 + 评估与改进建议）`）会让前端目录路径
    对不上/被截断 → 目录里看不到页、统计不变（2026-10-04 用户实测；与 `ontology_wiki.py`
    的目录口径 `module.short_label or key` 对齐）。
    """
    meta = class_meta()
    info = meta.get(type_name) or {}
    module_label = (info.get("module_short") or info.get("module_label")
                    or info.get("module") or type_name)
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
    """某类可用的对象属性（**进程级缓存**，P3-0g：每条边做 domain 校验都会调它）。"""
    hit = _memo_get(("relmap", page_type))
    if hit is None:
        hit = _memo_put(("relmap", page_type),
                        {r["prefixed"]: r for r in relation_types_for(page_type)["relation_types"]})
    return dict(hit)


# ---------------------------------------------------------------------------
# 数据属性（datatype property）—— 「通用保存」的 schema 一半
# ---------------------------------------------------------------------------
# 为什么需要：节点上的 `attributes`（如 `ea:ai_skill`）与边上的限定属性（如 `easvc:crudKind`）
# 必须由**本体**决定「这个类允许哪些属性」，否则扩展一处就要改一次工具/校验代码；
# 本体的真源是 Neo4j 投影（上传导入的模块不产 JSON，见 docs/session-handoff.md §3.4bis）。
# 声明在这些类上的数据属性 = **全局可用**（OWL 里的顶层通配；`bmm:name`/`bmm:definition` 就声明在 owl:Thing）
_GLOBAL_DOMAINS = {"http://www.w3.org/2002/07/owl#Thing", "http://www.w3.org/2000/01/rdf-schema#Resource"}
# 常见标准命名空间 → 短前缀（range 展示用；避免页面里出现整条 IRI）
_STD_PREFIXES = (
    ("http://www.w3.org/2001/XMLSchema#", "xsd:"),
    ("http://www.w3.org/1999/02/22-rdf-syntax-ns#", "rdf:"),
    ("http://www.w3.org/2000/01/rdf-schema#", "rdfs:"),
    ("http://www.w3.org/2002/07/owl#", "owl:"),
    ("http://www.w3.org/ns/shacl#", "sh:"),
)


def short_iri(iri: str) -> str:
    """把 IRI 缩成 prefixed 形式（`xsd:string` / `bmm:Goal`），拿不准就原样返回。

    用于页面展示（数据属性 range）与 `ontology_types.class_attributes`：长 IRI 在正文里很难读。
    """
    text = (iri or "").strip()
    if not text:
        return ""
    for base, prefix in _STD_PREFIXES:
        if text.startswith(base):
            return prefix + text[len(base):]
    for prefix, base in (index_data().get("namespace") or {}).items():
        if base and text.startswith(base):
            return "%s:%s" % (prefix, text[len(base):])
    return text


_DATA_PROP_CACHE: dict | None = None
_DATA_PROP_STAMP = None


def data_properties() -> dict:
    """全部数据属性：{prefixed: {label, module, range_literal, domains[]}}。

    `domains` 来自投影的 `BODHI_DOMAIN` 边（该数据属性声明在哪些类上）；
    没有声明 domain 的（老 TTL）→ `domains=[]`，调用方按「任意类都允许」处理。
    """
    global _DATA_PROP_CACHE, _DATA_PROP_STAMP
    stamp = index_stamp()
    if _DATA_PROP_CACHE is not None and stamp == _DATA_PROP_STAMP:
        return dict(_DATA_PROP_CACHE)
    props: dict[str, dict] = {}
    source = "neo4j"
    try:
        if not ke_neo4j.available():
            raise RuntimeError("Neo4j 投影为空或不可用")
        for row in ke_neo4j.query(
                "MATCH (p:BodhiOntProperty) WHERE p.bodhi_projection = 'ontology' "
                "AND toLower(coalesce(p.property_kind,'')) = 'datatype' AND p.prefixed IS NOT NULL "
                "OPTIONAL MATCH (p)-[:BODHI_DOMAIN]->(d:BodhiOntClass) "
                "RETURN p.prefixed AS prefixed, coalesce(p.label,'') AS label, "
                "       coalesce(p.module,'') AS module, coalesce(p.range_literal,'') AS range_literal, "
                "       collect(DISTINCT d.prefixed) AS domains ORDER BY p.prefixed"):
            name = row.get("prefixed")
            if not name:
                continue
            props[name] = {"prefixed": name, "label": row.get("label") or name,
                           "module": row.get("module") or "", "range_literal": row.get("range_literal") or "",
                           "domains": [d for d in (row.get("domains") or []) if d]}
    except Exception as exc:  # noqa: BLE001  JSON 产物只有计数、没有名单 → 只能空表兜底
        source = "unavailable: %s" % exc
    _DATA_PROP_CACHE = {"source": source, "properties": props}
    _DATA_PROP_STAMP = stamp
    return dict(_DATA_PROP_CACHE)


def data_properties_for(type_name: str) -> dict[str, dict]:
    """某类（含其祖先）允许的数据属性 —— 节点 `attributes` / 边限定属性的白名单。

    规则：数据属性声明在 `domains` 里的任一类**或其祖先**上即允许（继承语义）；
    `domains` 为空、或声明在 `owl:Thing` / `rdfs:Resource` 上的视为**全局可用**
    （如 `bmm:name` / `bmm:definition` 这类通用属性，不该被判"未声明"）。
    """
    all_props = data_properties()["properties"]
    chain = set(ancestors(type_name)) if type_name else set()
    out: dict[str, dict] = {}
    for name, meta in all_props.items():
        domains = set(meta.get("domains") or [])
        if not domains or (domains & _GLOBAL_DOMAINS) or (chain & domains):
            out[name] = meta
    return out


def target_closure(rel_type: str, meta: dict[str, dict] | None = None) -> list[str]:
    """某对象属性的 range 闭包（**进程级缓存**，P3-0g：每条边做 range 校验都会调它）。"""
    if meta is not None:                      # 显式传入视图 → 不缓存
        return _target_closure_uncached(rel_type, meta)
    hit = _memo_get(("closure", rel_type))
    if hit is None:
        hit = _memo_put(("closure", rel_type), _target_closure_uncached(rel_type, None))
    return list(hit)


def _target_closure_uncached(rel_type: str, meta: dict[str, dict] | None = None) -> list[str]:
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


# ---------------------------------------------------------------------------
# 「本体模型知识库」的识别（2026-09-22，用户实测：前端按钮不出现）
# ---------------------------------------------------------------------------
# 旧做法（前端补丁 v6）：把本体库 uuid 作为**构建期常量**注进 KnowledgeBase.vue，
# `isOntologyKb = kbId === 常量` —— 客户环境换了库 uuid → 按钮永不出现（且上游若改
# uuid，我们这边也要重建镜像）。这里改成**按库的特征认**，给出单一真源的解析：
#   ① env `BODHI_ONTOLOGY_KB_ID`（旧名 `ONTOLOGY_KB_ID` 兼容）—— 显式指定，最权威；
#   ② 库上打标记：`knowledge_bases.wiki_config->>'bodhi_ontology_kb' = 'true'`（一行 SQL）；
#   ③ 库名匹配 env `BODHI_ONTOLOGY_KB_NAME`（默认「企业本体模型」；先精确、再包含）；
#   ④ 内容探测：`wiki_pages` 里 `page_type LIKE 'ontology:%'` 页数最多且 ≥ 阈值的库。
# 调用方（ke_admin 的默认 kb、MCP 的 `GET /bodhi/ontology/kb`、前端运行时判定）共用本函数。
ONTOLOGY_MARK_KEY = "bodhi_ontology_kb"
ONTOLOGY_DEFAULT_NAME = "企业本体模型"
ONTOLOGY_MIN_PAGES = 10


def _kb_candidates() -> list[dict]:
    """所有知识库 + 各自的 `ontology:*` 页数（一次两条只读查询，库里库数很少）。"""
    rows = ke_db.psql_csv(
        "SELECT id, name, COALESCE(wiki_config::text, '{}') AS wiki_config "
        "FROM knowledge_bases WHERE deleted_at IS NULL ORDER BY created_at")
    counts = {r["kb"]: int(r["n"] or 0) for r in ke_db.psql_csv(
        "SELECT knowledge_base_id AS kb, count(*) AS n FROM wiki_pages "
        "WHERE deleted_at IS NULL AND page_type LIKE 'ontology:%' GROUP BY 1")}
    out = []
    for r in rows:
        wiki = r["wiki_config"] or "{}"
        out.append({"id": r["id"], "name": r["name"] or "",
                    "pages": counts.get(r["id"], 0),
                    "marked": ('"%s"' % ONTOLOGY_MARK_KEY) in wiki and "true" in wiki})
    return out


def _match_kb(cands: list[dict], raw: str) -> dict | None:
    """把「uuid / uuid 前缀 / 库名（精确或包含）」解析成候选里的一条。"""
    key = (raw or "").strip()
    if not key:
        return None
    low = key.lower()
    for c in cands:
        if c["id"] == key:
            return c
    for c in cands:
        if c["id"].startswith(low):
            return c
    for c in cands:
        if c["name"] == key:
            return c
    for c in cands:
        if key in c["name"] or c["name"] in key:
            return c
    return None


def resolve_ontology_kb(asked: str = "") -> dict:
    """认「本体模型知识库」：返回 `{id, name, source, pages, candidates[]}`（认不出 id 为空）。"""
    env_id = (ke_db.env_value("BODHI_ONTOLOGY_KB_ID") or ke_db.env_value("ONTOLOGY_KB_ID")).strip()
    env_name = (ke_db.env_value("BODHI_ONTOLOGY_KB_NAME") or ONTOLOGY_DEFAULT_NAME).strip()
    cands = _kb_candidates()
    hit, source = None, ""
    if env_id:                                     # ① env 显式指定
        hit, source = _match_kb(cands, env_id), "env"
        if hit is None:
            # env 指到一个库里没有的 id：不静默，交给调用方决定（前端仍可继续按内容判定）
            hit = {"id": env_id, "name": env_id, "pages": 0,
                   "note": "env 指定的库不在库里（可能尚未创建/已被删）"}
    if hit is None:                                # ② wiki_config 标记
        marked = [c for c in cands if c["marked"]]
        if marked:
            hit, source = max(marked, key=lambda c: c["pages"]), "wiki_config"
    if hit is None and env_name:                   # ③ 库名（精确 → 包含）
        exact = [c for c in cands if c["name"] == env_name]
        loose = [c for c in cands if env_name and env_name in c["name"]]
        if exact or loose:
            hit, source = (exact or loose)[0], "name"
    if hit is None:                                # ④ 内容探测（ontology:* 页最多者）
        rich = [c for c in cands if c["pages"] >= ONTOLOGY_MIN_PAGES]
        if rich:
            hit, source = max(rich, key=lambda c: c["pages"]), "pages"
    return {"id": (hit or {}).get("id", ""), "name": (hit or {}).get("name", ""),
            "source": source or "none", "pages": int((hit or {}).get("pages", 0)),
            "note": (hit or {}).get("note", ""), "candidates": cands}


def ontology_kb_report(asked: str = "") -> dict:
    """**只读判定**：`asked`（前端当前打开的知识库）是不是本体模型库；供 `GET /bodhi/ontology/kb` 使用。"""
    det = resolve_ontology_kb()
    cands = det.pop("candidates", [])
    out = {"ontology_kb": det, "asked": None, "candidates": cands,
           "rule": ("env BODHI_ONTOLOGY_KB_ID/ONTOLOGY_KB_ID → wiki_config.%s=true → "
                    "库名（默认「%s」）→ ontology:* 页数 ≥ %d" %
                    (ONTOLOGY_MARK_KEY, ONTOLOGY_DEFAULT_NAME, ONTOLOGY_MIN_PAGES))}
    if not (asked or "").strip():
        return out
    row = _match_kb(cands, asked)
    if row is None:
        out["asked"] = {"kb_id": asked, "name": "", "is_ontology_kb": False,
                        "reason": "库里没有这个知识库（id/名称都对不上）"}
        return out
    reasons = []
    # 2026-10-04 修：**优先级**而不是并列 OR —— env/权威识别的本体库（`det.id`）存在时，
    # **只有它**才判为本体库；`wiki_config.bodhi_ontology_kb` 标记与内容探测**仅作兜底**
    # （未配 env 时才用）。原来三者并列 OR ⇒ 一个**误标**（或残留标记）的非本体库
    # 也会被判 true → 非本体库界面出现「上传本体文件」按钮（用户实测：系统与规则台账 被误标）。
    if det["id"]:
        if row["id"] == det["id"]:
            reasons.append("就是识别出的本体库（source=%s）" % det["source"])
    else:
        if row["marked"]:
            reasons.append("带标记 wiki_config.%s=true（未配 env，兜底）" % ONTOLOGY_MARK_KEY)
        if row["pages"] >= ONTOLOGY_MIN_PAGES:
            reasons.append("含 %d 页 ontology:* 页面（未配 env，兜底）" % row["pages"])
    out["asked"] = {"kb_id": row["id"], "name": row["name"],
                    "is_ontology_kb": bool(reasons),
                    "reason": "；".join(reasons) or ("不是本体库（权威=%s；本库标记=%s、ontology:* 页数 %d）"
                                                     % (str(det.get("id") or "未配")[:8],
                                                        row["marked"], row["pages"]))}
    return out



