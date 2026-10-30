"""`/bodhi/ext/*`：前端面板用的 JSON 接口（与 MCP 传输共用 8765 端口）。

为什么要它
----------
MCP 是给**智能体**用的；浏览器面板只需要三件事：**读台账 / 改一行 / 刷新契约**。
这里把三者包成普通 JSON 接口，面板就不必实现 JSON-RPC（也不占 MCP 会话）✓。

路径（经 nginx `location /bodhi/` 反代，浏览器不直连端口）：
    GET  /bodhi/ext/list?object=<DataObject slug>&review_no=<编号>[&kb_id=…][&view=rows|table]
    POST /bodhi/ext/update        {object, review_no, issue_no, action?, fields:{…}, if_rev?, actor, session_no, dry_run?}
    POST /bodhi/ext/describe      {object, kb_id?, tool?, write_projection?}   ← 重新订阅契约（写投射）

写入口径与 MCP 完全一致（同一实现 `ke_ext_issues.maintain`）：`actor` 必填、`if_rev` 台账级、
`dry_run` 默认 true；冲突回 `conflict`（不静默覆盖 ✗）。
"""
from __future__ import annotations

import json

import ke_db
import ke_ext_issues as ISSUE
import ke_ext_store as STORE


def _projection(kb_id: str, slug: str) -> dict:
    """页面上已有的契约投射（`page_metadata.ontology.projection`）。"""
    try:
        rows = ke_db.psql_csv(
            "SELECT page_metadata FROM wiki_pages WHERE deleted_at IS NULL "
            "AND knowledge_base_id = %s AND slug = %s LIMIT 1"
            % (ke_db.sql_str(kb_id), ke_db.sql_str(slug)))
        md = json.loads((rows[0].get("page_metadata") if rows else "") or "{}")
        return ((md.get("ontology") or {}).get("projection") or {})
    except Exception:                     # noqa: BLE001  投射缺失不影响读台账
        return {}


def _resolve_kb(params: dict) -> str:
    """`kb_id` 可省：按 object slug 反查（**优先选真正声明了绑定的那个库** ✓）。

    2026-10-25 修：同一个 slug 可能在多个库都有（领域库 + 概念副本）⇒ 旧逻辑"只有 1 个才算"
    会直接回空 → `ext_describe` 报 `need_kb`（面板"刷新契约"就是这么失败的）。
    """
    kb = str(params.get("kb_id") or "").strip()
    if kb:
        return kb
    slug = str(params.get("object") or "").strip()
    if not slug:
        return ""
    cands = STORE._kbs_with_slug(slug)          # noqa: SLF001  同仓库内部解析器
    if len(cands) == 1:
        return cands[0]
    for kb_id in cands:                         # 多库同名：以"有绑定"的那个为准 ✓
        try:
            if STORE.parse_binding(STORE._attrs_of(kb_id, slug)):
                return kb_id
        except Exception:                       # noqa: BLE001
            continue
    return cands[0] if cands else ""


def schema_payload(params: dict) -> dict:
    """接口**列清单 + 主键标签**（不含数据，**不需要编号** ⇒ 面板可先用它定位编号 ✓）。

    用途（2026-10-25）：报告页的引用块可能缺 `review_no`，甚至**整块缺失**；
    面板要"从报告上下文取编号"就必须先知道**编号那一列叫什么**（标签来自契约，不写死）——
    这个名字只能来自契约（`x-ext` 的 `labels`），不能写死在代码/面板里 ✗。
    """
    slug = str(params.get("object") or "").strip()
    if not slug:
        return {"ok": False, "error": "need_object"}
    kb = _resolve_kb(params)
    binds = []
    try:
        binds = STORE.parse_binding(STORE._attrs_of(kb, slug))     # noqa: SLF001
    except Exception:                                             # noqa: BLE001
        binds = []
    binding = binds[0] if binds else {}
    proj = _projection(kb, slug) if kb else {}
    cols = ((proj.get("contract") or {}).get("columns") or [])
    labels = {c.get("field"): c.get("label") for c in cols if c.get("field")}
    order = [c["field"] for c in cols if c.get("field")]
    source = "projection"
    if not labels:
        try:
            import ke_ext_issues as I               # noqa: PLC0415
            sc = I._schema()                        # noqa: SLF001
            labels = dict(sc.get("labels") or {})
            order = list(sc.get("order") or [])
            source = "contract"
        except Exception:                           # noqa: BLE001
            source = "none"
    keys = list(binding.get("keys") or [])
    key = keys[0] if keys and labels.get(keys[0]) else (order[0] if order else "")
    return {"ok": True, "object": slug, "kb_id": kb, "source": source,
            "key": key, "key_label": labels.get(key, ""),
            "keys": keys, "labels": labels, "order": order,
            "tool": binding.get("tool") or "", "modes": binding.get("modes") or [],
            "schema_hash": proj.get("schemaHash") or ""}


def _page_by_title(title: str, kb_id: str = "") -> str:
    """按标题在同库（或全库）找**唯一**页 ⇒ slug（多个/没有 ⇒ 空，不猜 ✗）。"""
    title = (title or "").strip()
    if not title:
        return ""
    where = ("AND knowledge_base_id = %s " % ke_db.sql_str(kb_id)) if kb_id else ""
    try:
        rows = ke_db.psql_csv(
            "SELECT slug FROM wiki_pages WHERE deleted_at IS NULL %s "
            " AND (title = %s OR title LIKE %s) LIMIT 5"
            % (where, ke_db.sql_str(title), ke_db.sql_str("%" + title[-40:] + "%")))
    except Exception:                                   # noqa: BLE001
        return ""
    slugs = {r.get("slug") for r in rows or [] if r.get("slug")}
    return slugs.pop() if len(slugs) == 1 else ""


def _review_no_from_page(kb_id: str, slug: str, key_label: str = "") -> dict:
    """**从报告上下文取编号**（不写死、不猜）：① 引用块 → ② 正文里出现的**台账分片名** → ③ 表格主键行。

    ② 是最稳的一条：台账按外层主键分片存（文件名=编号），正文里出现哪个编号就用哪个
    （**必须唯一命中**；多个 ⇒ 不猜，交 ③/人工）✓ 代码里因此不含任何领域词。
    """
    import re
    if not (kb_id and slug):
        return {"value": "", "source": "no_page"}
    content = ""
    for where in (("knowledge_base_id = %s AND " % ke_db.sql_str(kb_id)) if kb_id else "",
                  ""):                                   # ① 指定库 → ② **全库按 slug**（跨库 ✓）
        try:
            rows = ke_db.psql_csv(
                "SELECT content FROM wiki_pages WHERE %sdeleted_at IS NULL "
                " AND slug = %s LIMIT 1" % (where, ke_db.sql_str(slug)))
        except Exception:                                # noqa: BLE001
            rows = []
        if rows:
            content = rows[0].get("content") or ""
            break
    if not content:
        return {"value": "", "source": "page_not_found"}
    m = re.search(r"(?ms)^```bodhi-ext\s*$.*?^```", content)
    if m:
        hit = re.search(r"(?m)^\s*review_no\s*:\s*(.+?)\s*$", m.group(0))
        if hit and hit.group(1).strip():
            return {"value": hit.group(1).strip(), "source": "ref_block"}
    try:
        import ke_ext_store as _S  # noqa: PLC0415
        names = [str(n) for n in _S.ledger_names() if n]          # 台账分片名（= 外层主键值）
    except Exception:                                   # noqa: BLE001
        names = []
    hit_names = [n for n in names if n in content]
    if len(hit_names) == 1:
        return {"value": hit_names[0], "source": "ledger_shard"}
    if key_label:
        for ln in content.splitlines():
            s = ln.strip()
            if not s.startswith("|"):
                continue
            cells = [c.strip() for c in s.strip().strip("|").split("|")]
            if len(cells) >= 2 and cells[0] == key_label and cells[1]:
                return {"value": cells[1], "source": "context_table"}
    return {"value": "", "source": "not_found"}


def list_payload(params: dict) -> dict:
    """读台账（面板表格 + 列口径）。`review_no` 可省 ⇒ **从页面上下文取**（不写死 ✓）。"""
    slug = str(params.get("object") or "").strip()
    review_no = str(params.get("review_no") or "").strip()
    kb = _resolve_kb(params)
    src = "param" if review_no else ""
    if not review_no:
        page_slug = str(params.get("slug") or "").strip()
        if not page_slug and params.get("title"):     # 面板只知道"我在哪一页"（标题）也行 ✓
            # 注意：**不能**用绑定所在库收窄（报告在工作库、绑定在领域库 ⇒ 会找不到页 ✗）
            page_slug = _page_by_title(str(params.get("title") or ""),
                                       str(params.get("kb_id") or ""))
        if page_slug:                                 # 面板报页面 ⇒ 后端解析编号
            key_label = ""
            try:
                key_label = schema_payload({"object": slug, "kb_id": kb}).get("key_label") or ""
            except Exception:                        # noqa: BLE001
                key_label = ""
            got = _review_no_from_page(kb or str(params.get("kb_id") or ""), page_slug, key_label)
            review_no, src = got["value"], got["source"]
    if not review_no:
        return {"ok": False, "error": "need_review_no",
                "hint": ("面板要知道是哪一份台账：引用块缺 `review_no` 时，"
                         "请带上页面 `slug`（后端会从正文表格的「%s」行解析）"
                         % (schema_payload({"object": slug, "kb_id": kb}).get("key_label") or "主键"))}
    # 数据来源（2026-10-29）：**优先调 04**（it_platform MCP），不再读本地文件——
    # 02 与 04 独立部署、不共享磁盘；本地文件只在 04 不可达时作兜底（同机/离线）。
    q = {"mode": "query", "action": "list", "review_no": review_no,
         "view": str(params.get("view") or "rows")}
    if params.get("columns"):
        q["columns"] = params["columns"]
    if slug:
        q["object"] = slug
    if kb:
        q["kb_id"] = kb                      # 04 也要能定位库（它不判断绑定，但会用它取契约）
    out, src_kind, remote_err = {}, "local_store", ""
    try:
        import ke_ext_client as _C  # noqa: PLC0415
        got = _C.call("ext_issue_list", q)
        if got.get("ok"):
            out, src_kind = got, "it_platform_mcp"
        else:                                 # `ok=false` **不能当成功**（2026-10-30 修）
            remote_err = str(got.get("why") or got.get("error") or "remote_not_ok")
    except Exception:  # noqa: BLE001
        out = {}
    if not out:
        out = ISSUE.maintain(kb_id=kb, object_slug=slug, mode="query", action="list",
                             review_no=review_no, view=str(params.get("view") or "rows"),
                             columns=params.get("columns"))
        src_kind = "local_store"
        if remote_err:
            out["remote_error"] = remote_err
    out["data_source"] = src_kind
    if out.get("ok"):
        out["projection"] = _projection(kb, slug) if kb else {}
        out["kb_id"] = kb
        out["review_no"] = review_no
        out["review_no_source"] = src
        # ── 映射（2026-10-30）：**展示走业务口径**（列名/列序/枚举），`schema` 保持工具口径 ──
        try:
            import ke_ext_map as MAP  # noqa: PLC0415
            binds = STORE.list_bindings(kb, slug) if kb else []
            fmap = binds[0].get("field_map") if binds else ""
            m = MAP.parse(fmap)
            if m["has"]:
                pairs = MAP.business_columns(fmap, (out.get("schema") or {}).get("order") or [])
                out["display"] = {
                    "labels": {f: (lb or f) for f, lb in pairs},
                    "order": [f for f, _lb in pairs],
                    "enums": m["enums"],
                    "mapping_status": binds[0].get("status") or "",
                    "source": "binding.fieldMap",
                }
                if str(params.get("view") or "rows") == "table":
                    md2, unmapped = MAP.render_table(
                        out.get("rows") or [], out.get("schema") or {}, fmap,
                        params.get("columns"))
                    if md2:
                        out["markdown"] = md2
                    if unmapped:
                        out.setdefault("warnings", []).append(
                            "unmapped_enum: %s" % "、".join(unmapped[:5]))
            else:
                out["display"] = {"mapping_status": (binds[0].get("status") if binds else ""),
                                  "source": "none",
                                  "note": "该对象未写映射 ⇒ 按工具口径展示（列名/枚举取契约）"}
        except Exception as exc:  # noqa: BLE001  映射失败不影响读台账
            out.setdefault("warnings", []).append("mapping_failed: %r" % (str(exc)[:120],))
        # 引用块：basis/tool 用回执里的 binding（04 回 `unbound` 时也照实写），不再读本地绑定
        _b = out.get("binding") or {}
        out["ref_block"] = ("```bodhi-ext\nobject: %s\nreview_no: %s\nbasis: %s / %s\nrev: %s\n"
                            "schema_hash: %s\n%s```"
                            % (slug, review_no,
                               str(_b.get("impl") or "").split(":")[0]
                               or ((STORE.parse_binding(STORE._attrs_of(kb, slug)) or [{}])[0]
                                   .get("impl") or "").split(":")[0] or "ext",
                               _b.get("tool") or "",
                               out.get("rev"), (out.get("schema") or {}).get("schemaHash")
                               or ((out.get("projection") or {}).get("schemaHash") or ""),
                               ("columns: %s\n" % params.get("columns"))
                               if params.get("columns") else ""))
    return out



def _tool_rows(kb_id: str) -> list:
    """该库已建模的工具（`agent/tool/*`）→ `[{tool, slug, hash, modes, keys}]`。"""
    try:
        import ke_neo4j                                  # noqa: PLC0415
        rows = ke_neo4j.query(
            "MATCH (t:BodhiInstance {kb_id:$kb}) WHERE t.slug STARTS WITH 'agent/tool/' "
            "RETURN coalesce(t.name, last(split(t.slug,'/'))) AS tool, t.slug AS slug, "
            "       coalesce(t.toolSchemaHash,'') AS hash, coalesce(t.toolModes,'') AS modes, "
            "       coalesce(t.toolKeyFields,'') AS keys ORDER BY tool",
            {"kb": kb_id}, timeout=8) or []
    except Exception:                                    # noqa: BLE001
        return []
    return [{"tool": str(r.get("tool") or ""), "slug": str(r.get("slug") or ""),
             "schema_hash": str(r.get("hash") or ""), "modes": str(r.get("modes") or ""),
             "keys": str(r.get("keys") or "")} for r in rows]


def binding_payload(params: dict) -> dict:
    """`GET /bodhi/ext/binding` —— 该对象**当前绑定**（关系）+ 可绑工具候选。

    用户口径（2026-10-29）：**有没有绑定只看关系实例**；面板据此显示"绑定/解绑"，
    不再读页面元数据（`page_metadata` 那 4 个属性已下线）。
    """
    slug = str(params.get("object") or params.get("slug") or "").strip()
    if not slug:
        return {"ok": False, "error": "need_object"}
    kb = _resolve_kb(params) or str(params.get("kb_id") or "")
    binds = STORE.list_bindings(kb, slug)
    tools = _tool_rows(kb) if kb else []
    bound = {b["tool"] for b in binds}
    return {"ok": True, "object": slug, "kb_id": kb, "bindings": binds,
            "tools": tools,
            "unbound_tools": [t for t in tools if t["tool"] not in bound],
            "bound": bool(binds)}


def bind_payload(body: dict) -> dict:
    """`POST /bodhi/ext/bind` —— 建绑定关系（默认 `draft`，映射完成后 `confirmed`）。"""
    slug = str(body.get("object") or body.get("slug") or "").strip()
    kb = _resolve_kb(body) or str(body.get("kb_id") or "")
    if not slug or not kb:
        return {"ok": False, "error": "need_object_and_kb"}
    return STORE.set_binding(kb, slug, str(body.get("tool") or ""),
                             role=str(body.get("role") or "readwrite"),
                             tool_kb=str(body.get("tool_kb") or kb),
                             status=str(body.get("status") or "draft"))


def unbind_payload(body: dict) -> dict:
    """`POST /bodhi/ext/unbind` —— 删绑定关系（`tool` 省略 ⇒ 删全部）。"""
    slug = str(body.get("object") or body.get("slug") or "").strip()
    kb = _resolve_kb(body) or str(body.get("kb_id") or "")
    if not slug or not kb:
        return {"ok": False, "error": "need_object_and_kb"}
    return STORE.delete_binding(kb, slug, str(body.get("tool") or ""))


def mapping_payload(body: dict) -> dict:
    """`POST /bodhi/ext/mapping` —— 写映射（业务字段 → 工具字段，领域列序为准）。"""
    slug = str(body.get("object") or body.get("slug") or "").strip()
    kb = _resolve_kb(body) or str(body.get("kb_id") or "")
    if not slug or not kb:
        return {"ok": False, "error": "need_object_and_kb"}
    return STORE.set_mapping(kb, slug, str(body.get("tool") or ""),
                             body.get("fields") or [],
                             status=str(body.get("status") or "confirmed"))


def tool_payload(params: dict) -> dict:
    """`GET /bodhi/ext/tool` —— 工具页数据（契约快照 + 绑定它的对象）。"""
    kb = str(params.get("kb_id") or "").strip()
    tool = str(params.get("tool") or "").strip()
    if not tool:
        return {"ok": False, "error": "need_tool"}
    rows = [t for t in _tool_rows(kb) if t["tool"] == tool] if kb else []
    objs = []
    try:
        import ke_neo4j                                  # noqa: PLC0415
        objs = ke_neo4j.query(
            "MATCH (o:BodhiInstance)-[r]->(t:BodhiInstance) "
            "WHERE type(r) = $rel AND coalesce(t.name, last(split(t.slug,'/'))) = $tool "
            "RETURN o.slug AS slug, coalesce(r.mappingStatus,'') AS st ORDER BY slug",
            {"rel": STORE.REL_BINDING, "tool": tool}, timeout=8) or []
    except Exception:                                    # noqa: BLE001
        objs = []
    return {"ok": True, "tool": tool, "kb_id": kb,
            "modeled": bool(rows), "tool_row": rows[0] if rows else {},
            "bound_objects": [{"slug": str(r.get("slug") or ""),
                               "mapping_status": str(r.get("st") or "")} for r in objs]}


def tool_refresh_payload(body: dict) -> dict:
    """`POST /bodhi/ext/tool/refresh` —— **只刷新已建模的工具**的契约（不新建实例）。"""
    kb = str(body.get("kb_id") or "").strip()
    tool = str(body.get("tool") or "").strip()
    if not kb:
        return {"ok": False, "error": "need_kb"}
    modeled = {t["tool"] for t in _tool_rows(kb)}
    want = [tool] if tool else sorted(modeled)
    unknown = [t for t in want if t and t not in modeled]
    if tool and unknown:
        return {"ok": False, "error": "tool_not_modeled", "tool": tool,
                "hint": ("本库还没有该工具页 ⇒ 先在「MCP 服务页」同步工具契约"
                         "（POST /bodhi/ext/tool/sync）再刷新")}
    import ke_ext_tools as TOOLS                          # noqa: PLC0415
    return TOOLS.sync(str(body.get("service") or "it_platform"), kb, tools=[t for t in want if t])


def service_tools_payload(params: dict) -> dict:
    """`GET /bodhi/ext/service/tools` —— **只列工具清单，不建任何页**（2026-10-30）。"""
    service = str(params.get("service") or "it_platform").strip()
    import ke_ext_client as C  # noqa: PLC0415
    tried = []
    for u in C.service_urls(service):
        try:
            names = list(C.list_tools(u) or [])
        except Exception as exc:  # noqa: BLE001
            tried.append({"url": u, "error": str(exc)[:120]})
            continue
        tried.append({"url": u, "tools": len(names)})
        if names:
            return {"ok": True, "service": service, "endpoint": u,
                    "tools": names, "count": len(names), "tried": tried}
    return {"ok": False, "service": service, "error": "no_tools",
            "hint": "服务不可达或 tools/list 为空：检查 mcp_services 的 url（应为 http://127.0.0.1:<port>/mcp）",
            "tried": tried}


def mapping_candidates_payload(params: dict) -> dict:
    """`GET /bodhi/ext/mapping/candidates` —— 业务列 ↔ 工具列 + 枚举差异（供 UI 表 + 一键全接受）。"""
    slug = str(params.get("object") or params.get("slug") or "").strip()
    kb = _resolve_kb(params) or str(params.get("kb_id") or "")
    tool = str(params.get("tool") or "").strip()
    if not slug or not kb:
        return {"ok": False, "error": "need_object_and_kb"}
    binds = STORE.list_bindings(kb, slug)
    hit = [b for b in binds if (not tool or b["tool"] == tool)]
    if not hit:
        return {"ok": False, "error": "no_binding",
                "hint": "先绑定工具（/bodhi/ext/bind）再出映射候选"}
    b = hit[0]
    con = {}
    rows = _tool_rows(kb)
    for t in rows:
        if t["tool"] == b["tool"]:
            try:
                import json as _j  # noqa: PLC0415
                con = _j.loads(t.get("columns") or "{}") or {}
            except Exception:  # noqa: BLE001
                con = {}
    tool_cols = list(con.get("order") or [])
    labels = con.get("labels") or {}
    cur = {}
    try:
        import json as _j2  # noqa: PLC0415
        cur = _j2.loads(b.get("field_map") or "{}") or {}
    except Exception:  # noqa: BLE001
        cur = {}
    mapped = {i.get("field"): (i.get("label") or "") for i in (cur.get("items") or [])}
    cand = []
    for f in tool_cols:
        cand.append({"field": f, "tool_label": labels.get(f) or f,
                     "business_label": mapped.get(f) or labels.get(f) or f,
                     "mapped": f in mapped,
                     "enum": {}})
    missing = [f for f in mapped if f and f not in tool_cols]
    return {"ok": True, "object": slug, "kb_id": kb, "tool": b["tool"],
            "candidates": cand, "missing_in_tool": missing,
            "conflicts": ([{"type": "field_not_in_contract", "fields": missing}] if missing else []),
            "hint": "确认后 POST /bodhi/ext/mapping 一次性写入（可一键全接受）"}


def mapping_stale_payload(params: dict) -> dict:
    """`GET /bodhi/ext/mapping/stale` —— 列出「工具契约已变但对象未重映射」的绑定（2026-10-30）。"""
    kb = str(params.get("kb_id") or "").strip()
    if not kb:
        return {"ok": False, "error": "need_kb"}
    import json as _j  # noqa: PLC0415
    tools = {t["tool"]: t for t in _tool_rows(kb)}
    out = []
    try:
        import ke_neo4j  # noqa: PLC0415
        rows = ke_neo4j.query(
            "MATCH (o:BodhiInstance)-[r]->(t:BodhiInstance) "
            "WHERE o.kb_id=$kb AND type(r) = 'bpmn:dataObjectBindingTool' "
            "RETURN o.slug AS object, last(split(t.slug,'/')) AS tool, "
            "       coalesce(t.toolSchemaHash,'') AS hash, "
            "       coalesce(r.mappingStatus,'') AS status, "
            "       coalesce(r.fieldMap,'') AS fmap", {"kb": kb}, timeout=10) or []
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": "graph_unavailable", "detail": str(exc)[:160]}
    for r in rows:
        try:
            fm = _j.loads(r.get("fmap") or "{}") or {}
        except Exception:  # noqa: BLE001
            fm = {}
        snap = str(fm.get("contractHash") or "")
        cur = str(r.get("hash") or "")
        if cur and snap and snap != cur:
            out.append({"object": r["object"], "tool": r["tool"],
                        "status": r.get("status"), "contractHash_bound": snap,
                        "contractHash_now": cur})
        elif cur and not snap:
            out.append({"object": r["object"], "tool": r["tool"],
                        "status": r.get("status"), "contractHash_bound": "",
                        "contractHash_now": cur, "note": "尚未记录快照（首版可忽略）"})
    return {"ok": True, "kb_id": kb, "stale": out, "count": len(out),
            "hint": "有 stale ⇒ 跑 bpmn/task/接口变更重映射（人工确认后重写 fieldMap）"}


def tool_create_payload(body: dict) -> dict:
    """`POST /bodhi/ext/tool/create` —— **只为勾选的工具建页**（白名单必填；无服务页自动补）。"""
    kb = str(body.get("kb_id") or "").strip()
    tools = [str(x).strip() for x in (body.get("tools") or []) if str(x).strip()]
    if not kb:
        return {"ok": False, "error": "need_kb"}
    if not tools:
        return {"ok": False, "error": "need_tools",
                "hint": "先 GET /bodhi/ext/service/tools 拿清单，再把要绑定的工具名放进来（不传=不建页）"}
    import ke_ext_tools as TOOLS  # noqa: PLC0415
    return TOOLS.sync(str(body.get("service") or "it_platform"), kb, tools=tools)
def tool_sync_payload(body: dict) -> dict:
    """`POST /bodhi/ext/tool/sync` —— 从 MCP 服务同步**选定工具**为 `agent:Tool` 页。"""
    kb = str(body.get("kb_id") or "").strip()
    if not kb:
        return {"ok": False, "error": "need_kb"}
    import ke_ext_tools as TOOLS                          # noqa: PLC0415
    return TOOLS.sync(str(body.get("service") or "it_platform"), kb,
                      tools=body.get("tools") or None,
                      dry_run=bool(body.get("dry_run")))


def update_payload(body: dict) -> dict:
    """改/增/删一行（写台账）。默认 `dry_run=true`（只回 diff）。"""
    slug = str(body.get("object") or "").strip()
    kb = _resolve_kb(body)
    if not body.get("actor"):
        return {"ok": False, "error": "need_actor",
                "hint": "写台账要带执行者（组长/智能体名），审计要用"}
    return ISSUE.maintain(
        kb_id=kb, object_slug=slug,
        mode="maintain", action=str(body.get("action") or "update"),
        review_no=str(body.get("review_no") or ""), issue_no=str(body.get("issue_no") or ""),
        fields=body.get("fields") or {}, view=str(body.get("view") or "rows"),
        if_rev=body.get("if_rev"), dry_run=body.get("dry_run", True) is not False and
        str(body.get("dry_run", "true")).lower() not in ("false", "0", "no", "off"),
        actor=str(body.get("actor") or ""), session_no=str(body.get("session_no") or ""))


def _pipes(line: str) -> int:
    """数"未转义的竖线"（`\\|` 是单元格里的字面竖线，不参与列数判断）。"""
    return line.replace("\\|", "").count("|")


def _table_span(lines: list, i: int, expect_cells: int) -> int:
    """从表头行 `i` 扫到表格结束（含 **被写散的坏行**）→ 结束下标（不含）。

    为什么需要它（2026-10-26 用户实测 bug）：
      单元格里含换行时，old 渲染会把一行拆成多行（`| P-04 | … | 达梦数据库 DM8` / `业务库` /
      `8.1.3.100` / `同城 | 不符合 | …`）。这些"裸行"不以 `|` 开头 ⇒ 旧扫描认为表格到此为止，
      于是同步只替换了前几行，尾部坏行留在正文里 ⇒ 表格永远修不好（手工删行后一刷新又回来）。

    规则：
      - `|` 开头 ⇒ 属于本表；
      - 空行 / 标题（`#`）/ 围栏（```）/ 引用块（`>`）⇒ 表格结束；
      - 其它"裸行" ⇒ **仅当上一行是残缺行**（未转义竖线数 ≠ 表头列数+1）才并入；否则视为表后正文，结束。
    这样正常表格不会被误吞，坏行能被整段替换掉 ✓。
    """
    expect = expect_cells + 1
    j, n = i + 1, len(lines)
    while j < n:
        s = lines[j].strip()
        if s.startswith("|"):
            j += 1
            continue
        if not s or s.startswith(("#", "```", ">")):
            break
        if _pipes(lines[j - 1]) != expect:      # 上一行是被写散的残缺行 ⇒ 本行是它的续行
            j += 1
            continue
        break
    return j


def _replace_section_table(content: str, table_md: str, label: str) -> tuple:
    """把"**表头单元格**含 `label`"的那张表换成 `table_md`（其余不动）。→ `(新正文, 是否替换)`

    2026-10-26 修（用户实测）：旧实现按「任意一行包含 label」定位 ⇒ ① 里那种"两列小表"的
    数据行（形如 `| xx编号 | YYYYMM-… |`）也会命中（label 是它的子串）⇒ 把 ① 的数据行换成了
    条目表（多出一层表头、① 原内容丢失 ✗）。现在只认**表头行**，并优先"表头单元格精确相等"。
    """
    if not table_md.strip() or not label:
        return content, False
    lines = content.splitlines()
    tables = []                                       # [(header_idx, end_idx, header_cells)]
    i = 0
    while i < len(lines):
        if lines[i].lstrip().startswith("|"):
            cells = [c.strip() for c in lines[i].strip().strip("|").split("|")]
            tables.append((i, _table_span(lines, i, len(cells)), cells))
            i = tables[-1][1]
        else:
            i += 1
    pick = None
    for h, e, cells in tables:                        # ① 表头某格 == label（精确）
        if label in cells:
            pick = (h, e)
            break
    if pick is None:                                  # ② 表头含 label 且列数 ≥3（排除 2 列小表）
        for h, e, cells in tables:
            if len(cells) >= 3 and any(label in c for c in cells):
                pick = (h, e)
                break
    if pick is None:
        return content, False
    h, e = pick
    new = lines[:h] + table_md.splitlines() + lines[e:]
    return "\n".join(new) + ("\n" if content.endswith("\n") else ""), True


def _dedupe_anchor_tables(content: str, label: str) -> tuple:
    """删掉**重复的锚点表**（表头单元格含 `label` 的第 2 张及以后）→ `(新正文, 删了几张)`

    2026-10-26 用户实测：历史上有过"把条目表插进 ① 表位置"的坏写，之后同步只替换了**第一张**
    锚点表 ⇒ 正文里残留第二张（③ 里出现 P-03/P-04 重复）✗。这里按"锚点相同 ⇒ 只留一张"清理。
    """
    if not content or not label:
        return content, 0
    lines = content.splitlines()
    spans, i = [], 0
    while i < len(lines):
        if lines[i].lstrip().startswith("|"):
            cells = [c.strip() for c in lines[i].strip().strip("|").split("|")]
            end = _table_span(lines, i, len(cells))
            if label in cells:
                spans.append((i, end))
            i = end
        else:
            i += 1
    if len(spans) <= 1:
        return content, 0
    drop = set()
    for h, e in spans[1:]:
        drop.update(range(h, e))
    kept = [ln for k, ln in enumerate(lines) if k not in drop]
    return "\n".join(kept) + ("\n" if content.endswith("\n") else ""), len(spans) - 1


def _refresh_block(content: str, led: dict, columns=None) -> str:
    """更新 ```` ```bodhi-ext ```` 块里的 `rev` / `schema_hash` / `columns`（其余字段不动）。

    `columns`（模板给的字段子集）⇒ 写回块里，前端面板据此**只渲染这几列**（顺序即展示顺序 ✓）；
    省略 ⇒ 保留块里原有取值（不清空：模板没声明 ≠ 要改成全量 ✗）。
    """
    import re
    rev = str(led.get("rev"))
    h = ((led.get("projection") or {}).get("schemaHash")
         or (led.get("schema") or {}).get("schemaHash") or "")
    cols = str(columns or "").strip()

    def _sub(m):
        blk = m.group(0)
        blk = re.sub(r"(?m)^(\s*rev\s*:\s*).*$", r"\g<1>" + rev, blk)
        if h:
            blk = re.sub(r"(?m)^(\s*schema_hash\s*:\s*).*$", r"\g<1>" + h, blk)
        if cols:
            if re.search(r"(?m)^\s*columns\s*:", blk):
                blk = re.sub(r"(?m)^(\s*columns\s*:\s*).*$", r"\g<1>" + cols, blk)
            else:                            # 插在 `basis:` 之后（保持块的可读顺序）
                blk = re.sub(r"(?m)^(\s*basis\s*:.*)$", r"\1\ncolumns: " + cols, blk, count=1)
        return blk

    return re.sub(r"(?ms)^```bodhi-ext\s*$.*?^```\s*$", _sub, content)


def _block_columns(content: str) -> str:
    """报告引用块里已声明的 `columns:`（模板配置随块走 ⇒ 再同步时不必重复传）。"""
    import re
    m = re.search(r"(?ms)^```bodhi-ext\s*$.*?^```\s*$", content or "")
    if not m:
        return ""
    hit = re.search(r"(?m)^\s*columns\s*:\s*(.+?)\s*$", m.group(0))
    return hit.group(1).strip() if hit else ""


def sync_report_payload(body: dict) -> dict:
    """**快照回写**：台账 → 报告正文（替换该节表格 + 同步引用块 rev/hash + version+1）。

    面板保存一行后调用；也用于"报告落后台账"的一键同步。
    """
    object_slug = str(body.get("object") or "").strip()
    review_no = str(body.get("review_no") or "").strip()
    kb = _resolve_kb(body)
    if not review_no:                                # 编号可省 ⇒ 从该页上下文解析（不写死 ✓）
        page_slug = str(body.get("slug") or "").strip()
        if page_slug:
            key_label = ""
            try:
                key_label = schema_payload({"object": object_slug, "kb_id": kb}).get("key_label") or ""
            except Exception:                        # noqa: BLE001
                key_label = ""
            got = _review_no_from_page(kb or str(body.get("kb_id") or ""), page_slug, key_label)
            review_no = got["value"]
    if not review_no:
        return {"ok": False, "error": "need_review_no",
                "hint": "给 `review_no`，或给 `slug`（后端会从正文表格的主键行解析编号）"}
    try:                                            # 定位列：用台账回执的标签，不写死领域词 ✓
        label = (ISSUE._schema().get("labels") or {}).get("issue_no") or ""  # noqa: SLF001
    except Exception:                               # noqa: BLE001
        label = ""
    # 报告页在**工作库**、绑定在**领域库** ⇒ 报告页默认**全库找**（只有显式给 kb_id 才收窄 ✓）
    kb_q = str(body.get("kb_id") or "").strip()
    where = (" AND knowledge_base_id = %s" % ke_db.sql_str(kb_q)) if kb_q else ""
    rows = ke_db.psql_csv(
        "SELECT knowledge_base_id, slug, title, page_type, content, page_metadata, version "
        " FROM wiki_pages "
        " WHERE deleted_at IS NULL AND page_type LIKE %s AND content LIKE %s %s "
        " ORDER BY updated_at DESC LIMIT 1"
        % (ke_db.sql_str("%Report%"), ke_db.sql_str("%review_no: " + review_no + "%"), where))
    if not rows:
        return {"ok": False, "error": "report_not_found", "review_no": review_no,
                "hint": "没找到引用该台账编号的报告页（正文里应有 `review_no: <编号>`）"}
    page = rows[0]
    # 列集优先级：本次显式传入 > 引用块里已声明的 `columns:`（模板配置随块走 ✓）
    cols = str(body.get("columns") or "").strip() or _block_columns(page["content"] or "")
    led = ISSUE.maintain(kb_id=(kb or page["knowledge_base_id"]), object_slug=object_slug,
                         mode="query", action="list", review_no=review_no, view="table",
                         columns=(cols or None))
    if not led.get("ok"):
        return {"ok": False, "error": led.get("error") or "ledger_unreadable",
                "detail": {k: led.get(k) for k in ("why", "hint") if led.get(k)}}
    table_md = led.get("markdown") or ""
    if not label and table_md:                      # 兜底：用回执表头第一格当定位标签（仍是数据驱动 ✓）
        label = table_md.splitlines()[0].strip().strip("|").split("|")[0].strip()
    content, replaced = _replace_section_table(page["content"] or "", table_md, label)
    content = _refresh_block(content, led, cols)
    content, _dups = _dedupe_anchor_tables(content, label)
    if not replaced:
        return {"ok": False, "error": "section_not_found", "label": label,
                "hint": "报告里找不到表头含「%s」的表格（先粘一次工具回执的 markdown）" % label}
    # **幂等**（2026-10-26 用户实测：保存+同步后正文没变、版本历史多出"无差异"的两版）：
    # 渲染结果与库里正文一致 ⇒ 不写库、不加版本 ✓
    if content.strip() == (page.get("content") or "").strip():
        return {"ok": True, "changed": False, "slug": page["slug"],
                "version": page.get("version"), "rows": len(led.get("rows") or []),
                "rev": led.get("rev"), "columns": cols,
                "schema_hash": (led.get("projection") or {}).get("schemaHash"),
                "note": "正文与台账一致 ⇒ 未写新版本（避免空 diff）"}
    # `dry_run`：只回\"将要写成什么\"，**不写库**（自测/预览用，避免体检把人家的正文改小 ✗）
    dry = body.get("dry_run", False)
    if isinstance(dry, str):
        dry = dry.strip().lower() not in ("0", "false", "no", "off")
    if dry:
        return {"ok": True, "dry_run": True, "slug": page["slug"],
                "version": page.get("version"), "rows": len(led.get("rows") or []),
                "rev": led.get("rev"), "columns": (led.get("schema") or {}).get("columns"),
                "table": table_md, "content_md": content,
                "schema_hash": (led.get("projection") or {}).get("schemaHash")}
    session_no = str(body.get("session_no") or "")
    if not session_no:                              # 写库 fail-closed：缺会话身份会被拒 ⇒ 从页面溯源兜底
        try:
            _md = json.loads(page.get("page_metadata") or "{}")
            _loc = str((( _md.get("ontology") or {}).get("attributes") or {})
                       .get("bmm:sourceLocator") or "")
            session_no = _loc.split("/")[1] if "/" in _loc else ""
        except Exception:                           # noqa: BLE001
            session_no = ""
    from srv.handlers.write import save_knowledge_tool      # noqa: PLC0415  走 handler ⇒ 闸门生效
    out = save_knowledge_tool({
        "kb_id": page["knowledge_base_id"], "stage": "report", "model": "bmm",
        "mode": "apply", "session_no": session_no,
        "report": {"title": page["title"], "page_type": page["page_type"],
                   "slug": page["slug"], "content_md": content}})
    return {"ok": bool(out.get("applied") or out.get("ok")), "changed": True, "slug": page["slug"],
            "version": (out.get("page_versions") or [{}])[0].get("before"),
            "rows": len(led.get("rows") or []), "rev": led.get("rev"),
            "schema_hash": (led.get("projection") or {}).get("schemaHash"),
            "warnings": out.get("warnings") or [], "ext_gate": out.get("ext_gate") or {},
            # 排障用：写库回执全文（截断），失败时前端能直接看到原因
            "save_out": json.dumps(out, ensure_ascii=False)[:900]}


def describe_payload(body: dict) -> dict:
    """重新订阅契约（= MCP 的 `ext_describe`，DataObject 实例页的「刷新契约」用）。"""
    b = dict(body or {})
    if not b.get("kb_id"):
        b["kb_id"] = _resolve_kb(b)            # 与 list 同口径：kb 可省 ⇒ 按 slug 反查
    from srv.handlers.ext import ext_describe          # noqa: PLC0415  避免模块级循环导入
    return ext_describe(b)
