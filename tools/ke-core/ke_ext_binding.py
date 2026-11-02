"""绑定 / 映射（**02 知识侧**）：DataObject ↔ 外部工具的关系边 + `fieldMap`。

为什么单独一层（2026-11-02 用户口径：两个 MCP 必须**代码级**隔离）
------------------------------------------------------------------
- "对象指向哪个外部接口"只有 02 有图/库 ⇒ 绑定归 02；
- 04（it_platform）是外部系统，**不判断绑定**（给什么数据就按什么干）；
- 以前这些函数和 04 的分片存储挤在 `ke_ext_store.py`（一个文件两种部署形态）⇒ 现在拆开。

口径
----
- **有没有绑定只看关系实例**：`-[:bpmn:dataObjectBindingTool]->`
  （2026-10-29 用户口径；页面 4 个 `dataObject*` 属性不参与，`BODHI_EXT_LEGACY_BINDING=1` 才回退）；
- `fieldMap`（业务列名 ↔ 工具字段 + 枚举）记在**边**上，`mappingStatus=draft|confirmed`；
- 列名以**领域**为准（报告/面板），工具字段只出现在外部接口的入参里。
"""
from __future__ import annotations

import json
import os
import time

# ---------------------------------------------------------------------------
# 绑定：**业务对象的数据属性**（2026-10-25 起）
#   · `bpmn:dataObjectInterface`     接口工具（多个用逗号分隔）
#   · `bpmn:dataObjectAccessMode`    每接口模式（`;` 分隔，与接口顺序对齐）
#   · `bpmn:dataObjectKeyFields`     每接口主键（`;` 分隔，顺序对齐）
#   · `bpmn:dataObjectInterfaceRef`  接口提供方（挡板页 slug；`;` 分隔）
# **不再借用** `bmm:sourceLocator`（那是落库内核的"来源定位"，语义不同 ✗）
# ---------------------------------------------------------------------------
BINDING_ATTRS = ("dataObjectInterface", "dataObjectAccessMode",
                 "dataObjectKeyFields", "dataObjectInterfaceRef")


def _split(text: str, sep: str) -> list:
    return [x.strip() for x in str(text or "").split(sep) if str(x).strip()]


def parse_binding(attrs: dict) -> list:
    """4 个数据属性 → `[{tool, modes, keys, stub}, …]`（按接口顺序对齐）。"""
    attrs = attrs or {}
    tools = _split(attrs.get("dataObjectInterface"), ",")
    modes = str(attrs.get("dataObjectAccessMode") or "").split(";")
    keys = str(attrs.get("dataObjectKeyFields") or "").split(";")
    refs = str(attrs.get("dataObjectInterfaceRef") or "").split(";")

    def _at(arr, i, default=""):
        v = arr[i].strip() if i < len(arr) else ""
        return v or default

    out = []
    for i, t in enumerate(tools):
        out.append({"tool": t,
                    "modes": _split(_at(modes, i, "query"), ","),
                    "keys": _split(_at(keys, i, ""), ","),
                    "impl": _at(refs, i, "")})
    return out


def _dataobject_types() -> list:
    try:
        import ke_ontology  # noqa: PLC0415
        out = ke_ontology.descendants("bpmn:DataObject") or []
        return sorted({str(x) for x in out if x}) or ["bpmn:DataObject"]
    except Exception:  # noqa: BLE001
        return ["bpmn:DataObject"]


def _kbs_with_slug(slug: str, exclude: str = "") -> list:
    """哪些库里有这个 slug（2026-10-25：绑定可能不在调用方传的那个库里）。"""
    try:
        import ke_db  # noqa: PLC0415
        rows = ke_db.psql_csv(
            "SELECT DISTINCT knowledge_base_id FROM wiki_pages WHERE slug = %s AND deleted_at IS NULL"
            % ke_db.sql_str(slug))
        return [str(r["knowledge_base_id"]) for r in rows
                if r.get("knowledge_base_id") and str(r["knowledge_base_id"]) != exclude]
    except Exception:  # noqa: BLE001
        return []


def _attrs_of(kb_id: str, slug: str) -> dict:
    """读绑定（**只认关系实例**；2026-10-29 用户口径）。

    绑定 = `(:BodhiInstance)-[:`bpmn:dataObjectBindingTool`]->(:BodhiInstance)`，
    **有边即绑定、无边即未绑定**。页面上的 `dataObject*` 属性（以及 PG
    `page_metadata` 里的同名键）**不再参与判断** —— 它们只描述"本系统如何操作外部工具"
    的业务说明，绑定关系由面板/工具写图。

    逃生门：`BODHI_EXT_LEGACY_BINDING=1` 时才回落到"节点属性 → PG"（迁移期用，默认关）。
    """
    blank = {a: "" for a in BINDING_ATTRS}
    # ① 关系（唯一真源）—— 用 `type(r)` 比对，避免反引号/命名空间写法差异导致静默失效
    try:
        import ke_neo4j  # noqa: PLC0415
        rows = ke_neo4j.query(
            "MATCH (o:BodhiInstance)-[r]->(t:BodhiInstance) "
            "WHERE o.slug = $s AND ($kb = '' OR o.kb_id = $kb) "
            "  AND type(r) = 'bpmn:dataObjectBindingTool' "
            "RETURN coalesce(t.toolName, last(split(t.slug,'/'))) AS tool, "
            "       coalesce(t.toolModes, r.modes, '') AS modes, "
            "       coalesce(t.toolKeyFields, r.keys, '') AS keys, "
            "       coalesce(r.impl, '') AS impl "
            "ORDER BY t.slug",
            {"kb": kb_id, "s": slug}, timeout=8) or []
        if rows:
            return {"dataObjectInterface": ",".join(str(r.get("tool") or "") for r in rows),
                    "dataObjectAccessMode": ";".join(str(r.get("modes") or "") for r in rows),
                    "dataObjectKeyFields": ";".join(str(r.get("keys") or "") for r in rows),
                    "dataObjectInterfaceRef": ";".join(str(r.get("impl") or "") for r in rows)}
    except Exception:  # noqa: BLE001  图不可用 ⇒ 视为未绑定（fail-closed）
        return blank
    if os.environ.get("BODHI_EXT_LEGACY_BINDING", "") not in ("1", "true", "yes"):
        return blank
    # ② 迁移期逃生门：图节点属性（历史写法）
    try:
        import ke_neo4j  # noqa: PLC0415
        rows = ke_neo4j.query(
            "MATCH (n:BodhiInstance {kb_id:$kb, slug:$s}) "
            "RETURN coalesce(n.dataObjectInterface,'') AS i, "
            "       coalesce(n.dataObjectAccessMode,'') AS m, "
            "       coalesce(n.dataObjectKeyFields,'') AS k, "
            "       coalesce(n.dataObjectInterfaceRef,'') AS r",
            {"kb": kb_id, "s": slug}, timeout=8) or []
        if rows:
            r0 = rows[0]
            got = {"dataObjectInterface": str(r0.get("i") or ""),
                   "dataObjectAccessMode": str(r0.get("m") or ""),
                   "dataObjectKeyFields": str(r0.get("k") or ""),
                   "dataObjectInterfaceRef": str(r0.get("r") or "")}
            if any(v.strip() for v in got.values()):
                return got
    except Exception:  # noqa: BLE001  图不可用 ⇒ 回 PG
        pass


    try:
        import ke_db  # noqa: PLC0415
        sel = ", ".join(
            "COALESCE(page_metadata->'ontology'->'attributes'->>'bpmn:%s','')" % a
            for a in BINDING_ATTRS)
        rows = ke_db.psql_csv(
            "SELECT %s FROM wiki_pages WHERE knowledge_base_id = %s AND slug = %s "
            " AND deleted_at IS NULL LIMIT 1"
            % (sel, ke_db.sql_str(kb_id), ke_db.sql_str(slug)))
        if rows:
            vals = list(rows[0].values())
            return {a: str(vals[i] or "") for i, a in enumerate(BINDING_ATTRS)}
    except Exception:  # noqa: BLE001
        pass
    return blank


def _objects_by_tool(kb_id: str, tool: str) -> list:
    """反查：哪些业务对象页声明的接口 = 本工具（`object` 省略时用）。

    2026-10-25：`kb_id` 为空（或本库查不到）⇒ **全库兜底**（调用方常传"工作库"，
    而绑定写在领域库）→ 每条候选带 `kb_id`，调用方据此回填 ✓。
    """
    needle = str(tool or "")
    out = []
    try:                                     # ① 关系（图本）：哪些对象绑定了本工具
        import ke_neo4j  # noqa: PLC0415
        rows = ke_neo4j.query(
            "MATCH (o:BodhiInstance)-[r]->(t:BodhiInstance) "
            "WHERE ($kb = '' OR o.kb_id = $kb) AND type(r) = 'bpmn:dataObjectBindingTool' "
            "  AND last(split(t.slug,'/')) = $q "
            "RETURN o.slug AS slug, coalesce(o.name,'') AS name, coalesce(o.kb_id,'') AS kb "
            "ORDER BY name LIMIT 10", {"kb": kb_id or "", "q": needle}, timeout=8) or []
        out = [{"slug": str(r.get("slug")), "name": str(r.get("name") or ""),
                "kb_id": str(r.get("kb") or "")} for r in rows]
    except Exception:  # noqa: BLE001
        out = []
    if out:
        return [x for x in out if x.get("slug")]
    if os.environ.get("BODHI_EXT_LEGACY_BINDING", "") not in ("1", "true", "yes"):
        return []
    try:                                     # 迁移期逃生门：图节点属性（历史写法）
        import ke_neo4j  # noqa: PLC0415
        rows = ke_neo4j.query(
            "MATCH (n:BodhiInstance) WHERE ($kb = '' OR n.kb_id = $kb) AND n.page_type IN $types "
            "  AND coalesce(n.dataObjectInterface,'') CONTAINS $q "
            "RETURN n.slug AS slug, coalesce(n.name,'') AS name, coalesce(n.kb_id,'') AS kb "
            "ORDER BY name", {"kb": kb_id or "", "types": _dataobject_types(), "q": needle},
            timeout=8) or []
        out = [{"slug": str(r.get("slug")), "name": str(r.get("name") or ""),
                "kb_id": str(r.get("kb") or "")} for r in rows]
    except Exception:  # noqa: BLE001  图不可用 ⇒ 回 PG

        try:
            import ke_db  # noqa: PLC0415
            where_kb = (" AND knowledge_base_id = %s" % ke_db.sql_str(kb_id)) if kb_id else ""
            rows = ke_db.psql_csv(
                "SELECT slug, COALESCE(title,'') AS name, knowledge_base_id AS kb "
                "  FROM wiki_pages WHERE deleted_at IS NULL AND page_type IN (%s) %s "
                "   AND page_metadata::text LIKE %s ORDER BY title LIMIT 10"
                % (", ".join(ke_db.sql_str(t) for t in _dataobject_types()), where_kb,
                   ke_db.sql_str("%" + needle + "%")))
            out = [{"slug": str(r["slug"]), "name": str(r.get("name") or ""),
                    "kb_id": str(r.get("kb") or "")} for r in rows]
        except Exception:  # noqa: BLE001
            out = []
    return [x for x in out if x.get("slug")]


REL_BINDING = "bpmn:dataObjectBindingTool"


def list_bindings(kb_id: str, object_slug: str) -> list:
    """该对象当前绑定的工具（关系实例）→ `[{tool, slug, role, status, field_map, modes, keys}]`。"""
    try:
        import ke_neo4j  # noqa: PLC0415
        rows = ke_neo4j.query(
            "MATCH (o:BodhiInstance)-[r]->(t:BodhiInstance) "
            "WHERE o.slug = $s AND ($kb = '' OR o.kb_id = $kb) AND type(r) = $rel "
            "RETURN coalesce(t.toolName, last(split(t.slug,'/'))) AS tool, t.slug AS slug, "
            "       coalesce(r.role,'') AS role, coalesce(r.mappingStatus,'') AS status, "
            "       coalesce(r.fieldMap,'') AS fmap, coalesce(t.toolModes,'') AS modes, "
            "       coalesce(t.toolKeyFields,'') AS keys "
            "ORDER BY tool", {"s": object_slug, "kb": kb_id or "", "rel": REL_BINDING},
            timeout=8) or []
    except Exception:  # noqa: BLE001
        return []
    return [{"tool": str(r.get("tool") or ""), "slug": str(r.get("slug") or ""),
             "role": str(r.get("role") or ""), "status": str(r.get("status") or ""),
             "field_map": str(r.get("fmap") or ""), "modes": str(r.get("modes") or ""),
             "keys": str(r.get("keys") or "")} for r in rows]


def set_binding(kb_id: str, object_slug: str, tool: str, role: str = "readwrite",
                tool_kb: str = "", field_map: dict | None = None,
                status: str = "draft") -> dict:
    """建/改**绑定关系**（有边即绑定）。工具节点不存在则按 slug 建一个占位（name 取末段）。

    用户口径（2026-10-29）：绑定**只能由面板/工具写关系**，不再写页面元数据；
    `fieldMap`（业务词→工具字段）记在**边**上，`mappingStatus=draft|confirmed`。
    """
    tool = str(tool or "").strip()
    if not tool:
        return {"ok": False, "error": "need_tool"}
    slug = "agent/tool/%s" % tool
    try:
        import ke_neo4j  # noqa: PLC0415
        ke_neo4j.query(
            "MERGE (t:BodhiInstance {kb_id:$tkb, slug:$tslug}) "
            "ON CREATE SET t.page_type='agent:Tool', t.module='agent', t.name=$tool "
            "SET t.name=$tool, t.toolName=$tool "
            "WITH t "
            "MATCH (o:BodhiInstance {kb_id:$kb, slug:$obj}) "
            "MERGE (o)-[r:`bpmn:dataObjectBindingTool`]->(t) "
            "SET r.role=$role, r.mappingStatus=$st, "
            "    r.mappedAt=toString(datetime()) "
            + (" , r.fieldMap=$fm" if field_map is not None else ""),
            {"kb": kb_id, "obj": object_slug, "tkb": tool_kb or kb_id,
             "tslug": slug, "tool": tool, "role": role or "readwrite",
             "st": status or "draft",
             "fm": json.dumps(field_map or {}, ensure_ascii=False)}, timeout=10)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": "graph_write_failed", "detail": repr(exc)[:200]}
    # 绑定成功后：**给对象页补契约块**（否则前端"绑定/映射"面板不出现）
    block = {}
    try:
        import ke_ext_tools as _T  # noqa: PLC0415
        block = _T.ensure_contract_block(kb_id, object_slug, tool)
    except Exception as exc:  # noqa: BLE001
        block = {"ok": False, "error": "block_failed", "detail": repr(exc)[:120]}
    return {"ok": True, "object": object_slug, "tool": tool, "tool_slug": slug,
            "role": role, "mapping_status": status, "contract_block": block}



def delete_binding(kb_id: str, object_slug: str, tool: str = "") -> dict:
    """删绑定关系（`tool` 省略 ⇒ 删该对象的**全部**绑定）。"""
    tool = str(tool or "").strip()
    try:
        import ke_neo4j  # noqa: PLC0415
        rows = ke_neo4j.query(
            "MATCH (o:BodhiInstance {kb_id:$kb, slug:$obj})-[r]->(t:BodhiInstance) "
            "WHERE type(r) = $rel "
            "  AND ($tool = '' OR last(split(t.slug,'/')) = $tool) "
            "WITH r, last(split(t.slug,'/')) AS tool "
            "DELETE r RETURN collect(tool) AS removed",
            {"kb": kb_id, "obj": object_slug, "rel": REL_BINDING, "tool": tool},
            timeout=10) or []
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": "graph_write_failed", "detail": repr(exc)[:200]}
    removed = (rows[0].get("removed") if rows else []) or []
    return {"ok": True, "object": object_slug, "removed": removed}


def set_mapping(kb_id: str, object_slug: str, tool: str, fields: list,
                status: str = "confirmed") -> dict:
    """写**映射**（业务字段 → 工具字段，含顺序）：`fields=[{field,label,business,note}]`。

    用户口径：领域内列名/列序**以 DataObject 为准**；即使与工具完全一致也要显式映射一次，
    便于"语义转换"留痕（以后不一致时直接改这一处）。
    """
    tool = str(tool or "").strip()
    rows = list_bindings(kb_id, object_slug)
    hit = [r for r in rows if r["tool"] == tool]
    if not hit:
        return {"ok": False, "error": "no_binding",
                "hint": "先绑定工具（POST /bodhi/ext/bind）再写映射"}
    fmap = {"order": [f.get("field") for f in fields if f.get("field")],
            "items": fields, "updatedAt": time.strftime("%Y-%m-%d %H:%M:%S")}
    try:
        import ke_neo4j  # noqa: PLC0415
        ke_neo4j.query(
            "MATCH (o:BodhiInstance {kb_id:$kb, slug:$obj})-[r]->(t:BodhiInstance) "
            "WHERE type(r) = $rel AND last(split(t.slug,'/')) = $tool "
            "SET r.fieldMap=$fm, r.mappingStatus=$st",
            {"kb": kb_id, "obj": object_slug, "rel": REL_BINDING, "tool": tool,
             "fm": json.dumps(fmap, ensure_ascii=False), "st": status or "confirmed"},
            timeout=10)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": "graph_write_failed", "detail": repr(exc)[:200]}
    # 映射定稿后也要刷新契约块（mapping_status 变了）
    try:
        import ke_ext_tools as _T  # noqa: PLC0415
        _T.ensure_contract_block(kb_id, object_slug, tool)
    except Exception:  # noqa: BLE001
        pass
    return {"ok": True, "object": object_slug, "tool": tool,
            "mapping_status": status, "columns": len(fmap["order"])}



def _has_kb_sources() -> bool:
    """本进程**是否具备读绑定**的能力（有 `ke_db` 或 `ke_neo4j`）。

    04（it_platform）包只带 5 个 ext 运行库（store/docs/flow/issues/catalog），
    既没有图也没有库 ⇒ 它们**不该判断调用方是否绑定**（绑定是 02 的职责）。
    """
    try:
        import ke_db  # noqa: F401,PLC0415
        return True
    except Exception:  # noqa: BLE001
        pass
    try:
        import ke_neo4j  # noqa: F401,PLC0415
        return True
    except Exception:  # noqa: BLE001
        return False


def resolve_binding(tool: str, kb_id: str = "", object_slug: str = "") -> dict:
    """→ `{ok, binding, object, candidates, why}`（**不抛**；入口据此给可读回执）。

    2026-10-29 用户口径（架构纠偏）：**外部服务不判断绑定** ——
    04（无图/无库）直接放行（`source="unbound"`），按入参工作；
    "对象是否绑定了外部服务"由 **02** 决定（02 的 `_attrs_of` 已图优先）。
    """
    tool = str(tool or "").strip()
    if not _has_kb_sources():
        return {"ok": True, "source": "unbound",
                "binding": {"tool": tool, "modes": ["query", "maintain"], "keys": [], "impl": ""},
                "object": {"slug": str(object_slug or "").strip()},
                "warn": ("本服务不校验绑定（无图/库能力，属外部服务职责边界）；"
                         "绑定是否存在由 02 的 ext_describe / 报告门禁判断")}

    slug = str(object_slug or "").strip()
    # 2026-10-25：`kb_id` 可省 —— 只给 slug 也能反查；`kb_id + slug` 都给了但本库查不到绑定时
    # 会跨库兜底（智能体常传"工作库"，而绑定写在领域库 ⇒ 不能直接判 no_binding ✗）
    if not kb_id and slug:
        cands_kb = _kbs_with_slug(slug)
        if len(cands_kb) == 1:
            kb_id = cands_kb[0]
        elif cands_kb:
            # 多库同 slug（领域库 + 概念副本/测试库）⇒ 选**真正有绑定关系**的那个 ✓
            for _k in cands_kb:
                try:
                    if parse_binding(_attrs_of(_k, slug)):
                        kb_id = _k
                        break
                except Exception:                  # noqa: BLE001
                    continue
            if not kb_id:
                return {"ok": False, "why": "need_kb", "object": {"slug": slug},
                        "candidates": [{"kb_id": k} for k in cands_kb[:10]],
                        "hint": ("同 slug 出现在 %d 个库、且都没有绑定关系 ⇒ 请用 kb_id 指定"
                                 % len(cands_kb))}
    if not slug:
        cands = _objects_by_tool(kb_id, tool)
        if len(cands) == 1:
            slug = cands[0]["slug"]
            kb_id = cands[0].get("kb_id") or kb_id
        elif not cands:
            return {"ok": False, "why": "no_binding", "candidates": [],
                    "hint": ("没有业务对象声明接口 %r（先在对象页写 "
                             "`bpmn:dataObjectInterface` 等 4 个数据属性）" % tool)}
        else:
            return {"ok": False, "why": "need_object", "candidates": cands[:10],
                    "hint": "多个对象都声明了本接口 ⇒ 请用 object=<slug> 指定"}
    if not kb_id:
        return {"ok": False, "why": "need_kb", "candidates": [],
                "hint": "需要 kb_id 或 object（业务对象页 slug）；绑定在哪个库都能解析"}
    searched = [kb_id]
    binds = parse_binding(_attrs_of(kb_id, slug))
    binding_kb = kb_id
    if not binds:
        for other in _kbs_with_slug(slug, exclude=kb_id):
            searched.append(other)
            b2 = parse_binding(_attrs_of(other, slug))
            if b2:
                binds, binding_kb = b2, other
                break
    if not binds:
        return {"ok": False, "why": "no_binding", "object": {"slug": slug},
                "searched_kbs": searched[:5], "candidates": [],
                "hint": ("该对象**尚未绑定外部工具**（软定义表格，属合规状态）："
                         "在对象页面板点「绑定」选工具；绑定后才调外部服务。")}
    hit = [b for b in binds if b.get("tool") == tool]
    if not hit:
        return {"ok": False, "why": "tool_mismatch", "object": {"slug": slug},
                "binding": binds[0], "candidates": [],
                "available_tools": [b.get("tool") for b in binds],
                "hint": "该页声明的接口是 %r，不含 %r"
                        % ([b.get("tool") for b in binds], tool)}
    return {"ok": True, "binding": hit[0], "object": {"slug": slug},
            "binding_kb": binding_kb, "binding_count": len(binds)}


