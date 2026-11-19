"""外部 MCP 工具 → 领域库 `agent:Tool` **实例页**（契约同步；只新增/更新，不删）。

口径（2026-10-29 用户口径 / 图本原则）
- 工具实例属于**具体领域库**（与该库的 DataObject 同库）：前端可点开、`regen_wiki` 可核对、
  绑定边同库 ⇒ 不会出现"图里有边、PG 无页、前端 404"的半落地状态。
- 契约真源 = 外部 `tools/list` 的 `x-ext`；本库只存**契约快照**（列/入参/出参/hash）与
  业务侧**映射**（映射记在绑定边 `fieldMap`），不判断外部契约该长什么样。
- 幂等：字段没变 ⇒ 不写（回 `unchanged`）；人工填过的 `toolImpl` 只在为空时补。
"""
from __future__ import annotations

import json
import time

import ke_ext_client as C
import ke_neo4j
import ke_pages

_PROP = {"name": "toolName", "service": "toolService", "modes": "toolModes",
         "keys": "toolKeyFields", "schema": "toolSchemaHash"}


BLOCK = "```bodhi-ext-contract"


def _is_contract_page(page_type: str) -> bool:
    """契约块只属于 `bpmn:DataObject`（含子类）与 `agent:Tool`。

    2026-11-08 修：原实现不看 page_type，`save_knowledge` 对每个 merged 页
    都调用 ensure_contract_block ⇒ BMM 的 Goal/Objective/Influencer 全被写入空契约块。
    """
    pt = str(page_type or "").strip()
    if not pt:
        return False
    if pt == "agent:Tool":
        return True
    try:
        import ke_ontology  # noqa: PLC0415
        return "bpmn:DataObject" in ke_ontology.ancestors(pt)
    except Exception:  # noqa: BLE001  本体不可用 ⇒ 只认最保守的精确类
        return pt in ("bpmn:DataObject", "bpmn:TextualDataObject")


def _contract_block_for(kb_id: str, object_slug: str, page_type: str,
                        tool: str = "") -> dict:
    """Build the canonical contract block for one page (no writes)."""
    import ke_ext_binding as _S  # noqa: PLC0415

    # 2026-10-30（用户实测死锁）：**未绑定也要有块** —— 否则面板不出现 ⇒ 永远绑不上。
    # 工具页（agent:Tool）同样要有块，UI 才能"刷新契约"。
    kind = "tool" if page_type == "agent:Tool" else "contract"
    if kind == "tool" and not tool:
        tool = object_slug.rsplit("/", 1)[-1]
    binds = _S.list_bindings(kb_id, object_slug)
    binding = binds[0] if binds else {}
    tool = tool or binding.get("tool") or ""
    svc = ""
    try:
        import ke_neo4j  # noqa: PLC0415
        got = ke_neo4j.query(
            "MATCH (t:BodhiInstance)-[]->(s:BodhiInstance) "
            "WHERE t.kb_id=$kb AND last(split(t.slug,'/')) = $tool "
            "  AND s.slug STARTS WITH 'agent/mcpservice/' "
            "RETURN last(split(s.slug,'/')) AS svc LIMIT 1",
            {"kb": kb_id, "tool": tool}, timeout=6) or []
        svc = str((got[0] or {}).get("svc") or "") if got else ""
    except Exception:  # noqa: BLE001
        svc = ""
    fmap_items = []
    try:
        fmap_items = ((json.loads(binding.get("field_map") or "{}") or {}).get("items") or [])
    except Exception:  # noqa: BLE001
        fmap_items = []
    block = "\n".join([BLOCK, "kind: %s" % kind, "object: %s" % object_slug,
                       "tool: %s" % tool, "service: %s" % svc,
                       "mapping_status: %s" % (binding.get("status") or ""),
                       "mapped_columns: %d" % len(fmap_items),
                       "```", ""])
    return {"kind": kind, "tool": tool, "service": svc,
            "mapped_columns": len(fmap_items), "block": block}


def _with_contract_block(content: str, block: str) -> tuple[str, bool]:
    """Replace every legacy contract block with exactly one canonical block."""
    import re as _re  # noqa: PLC0415

    blocks = list(_re.finditer(r"(?ms)^```bodhi-ext-contract\s*$.*?^```\s*$", content or ""))
    if len(blocks) == 1 and blocks[0].group(0).strip() == block.strip():
        return content, False
    rest = content or ""
    for found in reversed(blocks):
        rest = rest[:found.start()] + rest[found.end():]
    rest = rest.lstrip("\n")
    return block + ("\n" + rest if rest.strip() else ""), True


def refresh_contract_block(kb_id: str, object_slug: str, content: str,
                           page_type: str = "", tool: str = "") -> dict:
    """Canonicalize a page body's contract block **without writing** (dry-run safe).

    This is the single body-rebuild primitive used by attribute edits, binding /
    mapping updates, and the ``rebuild_data_objects`` maintenance tool.
    """
    pt = str(page_type or "")
    if not _is_contract_page(pt):
        return {"ok": True, "changed": False, "content": content,
                "reason": "not_data_object_or_tool", "page_type": pt}
    # Binding/mapping also goes through this primitive: clean orphan field
    # tables left outside the attribute section by older writers.
    content = ke_pages.strip_orphan_field_tables(content, pt)
    spec = _contract_block_for(kb_id, object_slug, pt, tool=tool)
    new_content, changed = _with_contract_block(content, spec["block"])
    return {"ok": True, "changed": changed, "content": new_content,
            "block": spec["block"], "tool": spec["tool"],
            "service": spec["service"], "mapped_columns": spec["mapped_columns"]}


def ensure_contract_block(kb_id: str, object_slug: str, tool: str = "") -> dict:
    """给**已绑定**的业务对象页补 ````bodhi-ext-contract```` 块（面板靠它渲染）。

    为什么要它（2026-10-30 用户实测）：对象页没有这个块 ⇒ 前端"绑定/映射/刷新契约"面板不出现 ⇒
    用户以为绑定没生效。绑定成功后自动补块，幂等（已有同 object 的块 ⇒ 不动）。
    2026-11-19：改为**先规范化整段块**再一次性写页，重复块与旧字段一起清掉。
    """
    try:
        import ke_db  # noqa: PLC0415
        import ke_pages  # noqa: PLC0415
        rows = ke_db.psql_csv(
            "SELECT title, page_type, coalesce(content,'') AS c, page_metadata::text AS md "
            "FROM wiki_pages WHERE knowledge_base_id=%s AND slug=%s AND deleted_at IS NULL LIMIT 1"
            % (ke_db.sql_str(kb_id), ke_db.sql_str(object_slug)))
        if not rows:
            return {"ok": False, "error": "page_not_found"}
        row = rows[0]
        md = row.get("c") or ""
        _pt = str(row.get("page_type") or "")
        if not _is_contract_page(_pt):
            return {"ok": True, "changed": False, "reason": "not_data_object_or_tool",
                    "page_type": _pt, "block": BLOCK}
        refreshed = refresh_contract_block(kb_id, object_slug, md, _pt, tool=tool)
        if not refreshed.get("changed"):
            return {"ok": True, "changed": False, "reason": "has_block",
                    "tool": refreshed.get("tool"), "service": refreshed.get("service")}
        meta = json.loads(row.get("md") or "{}")
        ke_pages.upsert_page(kb_id, object_slug, row.get("title") or object_slug,
                             row.get("page_type") or "bpmn:DataObject",
                             refreshed["content"], metadata=meta, sync_folders=False)
        return {"ok": True, "changed": True, "tool": refreshed.get("tool"),
                "service": refreshed.get("service")}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": "write_failed", "detail": repr(exc)[:200]}


def _body_counts(content: str) -> dict:
    """Count generated body sections (used by rebuild preview/receipt)."""
    import re as _re  # noqa: PLC0415
    text = str(content or "")
    return {"contract_blocks": len(_re.findall(
                r"(?ms)^```bodhi-ext-contract\s*$.*?^```\s*$", text)),
            "field_tables": len(_re.findall(r"(?m)^## 字段表", text))}


def rebuild_data_objects(kb_id: str, slugs: list | None = None,
                         dry_run: bool = True) -> dict:
    """Rebuild DataObject pages from their data attributes + current bindings.

    The body is a generated projection: attribute section and contract block are
    both rendered again, so old duplicate sections cannot survive. It does not
    change task pages, templates, or domain semantics.
    """
    import ke_db  # noqa: PLC0415
    import ke_pages  # noqa: PLC0415

    kb_id = str(kb_id or "").strip()
    if not kb_id:
        return {"ok": False, "error": "need_kb_id"}
    wanted = [str(x).strip() for x in (slugs or []) if str(x).strip()]
    where = ("knowledge_base_id=%s AND deleted_at IS NULL AND page_type='bpmn:DataObject'"
             % ke_db.sql_str(kb_id))
    if wanted:
        where += " AND slug IN (%s)" % ", ".join(ke_db.sql_str(x) for x in wanted)
    rows = ke_db.psql_csv(
        "SELECT slug, COALESCE(title,'') AS title, COALESCE(content,'') AS content "
        "FROM wiki_pages WHERE " + where + " ORDER BY slug")
    results = []
    for row in rows:
        slug = str(row.get("slug") or "")
        before = _body_counts(row.get("content") or "")
        info = ke_pages.page_attributes(kb_id, slug)
        if not info.get("ok"):
            results.append({"slug": slug, "ok": False, "error": info.get("error") or "page_attributes_failed"})
            continue
        attrs = dict(info.get("attributes") or {})
        preview = ke_pages.replace_attr_section(row.get("content") or "", "bpmn:DataObject", attrs)
        preview = ke_pages.strip_orphan_field_tables(preview, "bpmn:DataObject")
        rb = refresh_contract_block(kb_id, slug, preview, "bpmn:DataObject")
        after_content = rb.get("content") if rb.get("ok") else preview
        after = _body_counts(after_content)
        item = {"slug": slug, "title": row.get("title") or slug,
                "before": before, "after": after,
                "changed": bool(after_content != (row.get("content") or "")),
                "attributes": len(attrs),
                "contract_changed": bool(rb.get("changed"))}
        if not dry_run and item["changed"]:
            if attrs:
                applied = ke_pages.set_page_attributes(
                    kb_id, slug, attrs, actor="rebuild_data_objects", dry_run=False)
                if not applied.get("ok"):
                    item.update({"ok": False, "error": applied.get("error") or "write_failed"})
                    results.append(item)
                    continue
            else:
                applied = ensure_contract_block(kb_id, slug)
                if not applied.get("ok"):
                    item.update({"ok": False, "error": applied.get("error") or "write_failed"})
                    results.append(item)
                    continue
            # Re-read to report what is actually persisted, not the preview.
            check = ke_pages.page_attributes(kb_id, slug)
            if check.get("ok"):
                row2 = ke_db.psql_csv(
                    "SELECT COALESCE(content,'') AS c FROM wiki_pages WHERE knowledge_base_id=%s "
                    "AND slug=%s AND deleted_at IS NULL LIMIT 1"
                    % (ke_db.sql_str(kb_id), ke_db.sql_str(slug)))
                after = _body_counts((row2[0].get("c") if row2 else "") or "")
                item["after"] = after
        item["ok"] = True
        results.append(item)
    changed = sum(1 for x in results if x.get("changed"))
    failed = sum(1 for x in results if not x.get("ok"))
    return {"ok": failed == 0, "kb_id": kb_id, "dry_run": bool(dry_run),
            "applied": not dry_run, "total": len(results), "changed": changed, "failed": failed,
            "pages": results,
            "receipt": {"total": len(results), "changed": changed, "failed": failed,
                        "contract_blocks_after": sum(x.get("after", {}).get("contract_blocks", 0)
                                                     for x in results if x.get("ok")),
                        "field_tables_after": sum(x.get("after", {}).get("field_tables", 0)
                                                 for x in results if x.get("ok"))}}


def strip_contract_block(kb_id: str, object_slug: str, dry_run: bool = True) -> dict:
    """移除**非 DataObject/Tool 页面**上的错误契约块（幂等，默认只预览）。

    2026-11-08：旧 `save_knowledge` 给所有 merged 页写空契约块；修复后需要
    清理存量。DataObject/Tool 的合法契约块不动。
    """
    import re as _re  # noqa: PLC0415

    import ke_db  # noqa: PLC0415

    rows = ke_db.psql_csv(
        "SELECT title, page_type, coalesce(content,'') AS c, page_metadata::text AS md "
        "FROM wiki_pages WHERE knowledge_base_id=%s AND slug=%s AND deleted_at IS NULL LIMIT 1"
        % (ke_db.sql_str(kb_id), ke_db.sql_str(object_slug)))
    if not rows:
        return {"ok": False, "changed": False, "error": "page_not_found"}
    row = rows[0]
    _pt = str(row.get("page_type") or "")
    if _is_contract_page(_pt):
        return {"ok": True, "changed": False, "reason": "protected_data_object_or_tool",
                "page_type": _pt}
    md = row.get("c") or ""
    pat = _re.compile(r"(?ms)^```bodhi-ext-contract\s*$.*?^```\s*$")
    blocks = list(pat.finditer(md))
    if not blocks:
        return {"ok": True, "changed": False, "reason": "no_block"}
    new_md = pat.sub("", md).lstrip("\n")
    if dry_run:
        return {"ok": True, "changed": True, "dry_run": True, "blocks": len(blocks),
                "slug": object_slug, "page_type": _pt, "chars_before": len(md),
                "chars_after": len(new_md)}
    meta = json.loads(row.get("md") or "{}")
    ke_pages.upsert_page(kb_id, object_slug, row.get("title") or object_slug,
                         row.get("page_type") or "", new_md, metadata=meta,
                         sync_folders=False)
    return {"ok": True, "changed": True, "blocks": len(blocks), "slug": object_slug,
            "page_type": _pt}


def strip_contract_blocks(kb_id: str, dry_run: bool = True) -> dict:
    """扫描本库所有含 `bodhi-ext-contract` 的非 DataObject/Tool 页面并清理。"""
    import ke_db  # noqa: PLC0415

    rows = ke_db.psql_csv(
        "SELECT slug, page_type FROM wiki_pages WHERE knowledge_base_id=%s "
        "AND deleted_at IS NULL AND content LIKE %s ORDER BY slug"
        % (ke_db.sql_str(kb_id), ke_db.sql_str(BLOCK + "%")))
    targets = [r for r in rows if not _is_contract_page(r.get("page_type") or "")]
    if dry_run:
        return {"ok": True, "dry_run": True, "kb_id": kb_id, "total": len(targets),
                "targets": [{"slug": r.get("slug"), "page_type": r.get("page_type")}
                            for r in targets]}
    results = [strip_contract_block(kb_id, str(r.get("slug") or ""), dry_run=False)
               for r in targets]
    return {"ok": True, "dry_run": False, "kb_id": kb_id, "total": len(targets),
            "changed": sum(1 for r in results if r.get("changed")),
            "results": results}


def _impl_of(service: str, tool: str) -> str:
    """实现落点（尽力而为的默认值；人工可改）。"""
    base = "tools/ke-core/ke_ext_%s.py"
    hint = {"ext_issue_list": base % "issues", "ext_review_flow": base % "flow",
            "ext_catalog_query": base % "catalog", "ext_doc_access": base % "docs",
            "ext_doc_push": base % "docs_client", "ext_doc_parse": base % "doc_parse"}
    return "%s:%s" % (service, hint.get(tool, "tools/"))


def _keys_of(con: dict) -> list:
    """契约里的主键字段（服务端键名是 `keyFields`；兼容 `keys`）。"""
    return list(con.get("keyFields") or con.get("keys") or [])


def _content(tool: str, service: str, url: str, con: dict, at: str, impl: str) -> str:
    cols = con.get("columns") or []
    modes = ", ".join(con.get("modes") or []) or "—"
    keys = ", ".join(_keys_of(con)) or "—"

    lines = ["```bodhi-ext-contract",
             "kind: tool",
             "object: agent/tool/%s" % tool,
             "tool: %s" % tool,
             "service: %s" % service,
             "```", "",
             "# 工具：%s" % tool, "",
             "- **外部服务**：%s（`%s`）" % (service, url),
             "- **访问模式**：%s" % modes,
             "- **主键字段**：%s" % keys,
             "- **契约版本**：`%s`（同步于 %s）" % (con.get("schemaHash") or "—", at),
             "- **实现**：`%s`" % (impl or "—"),
             "", "## 列契约（x-ext → 字段 / 标签 / 枚举）", "",
             "| 字段 | 标签 | 枚举 |", "|---|---|---|"]

    for c in cols:
        lines.append("| `%s` | %s | %s |" % (
            c.get("field") or "", c.get("label") or "—",
            " / ".join(c.get("enum") or []) or "—"))
    if not cols:
        lines.append("| — | （该工具未下发列契约） | — |")
    lines += ["", "## 绑定口径", "",
              "- 数据对象通过关系 `bpmn:dataObjectBindingTool` 引用本工具 —— **有边即绑定**；",
              "- 业务词 ↔ 字段 / 枚举值的映射记在**绑定边**（`fieldMap`），"
              "定稿状态 `mappingStatus = draft | confirmed`；",
              "- 未绑定本工具的数据对象按 `skos:definition` / `skos:note` 作**软定义表格**处理，不调外部服务。",
              "", "> 本页由「契约同步」生成：只更新契约快照，不改业务映射。"]
    return "\n".join(lines) + "\n"


def _attrs(tool: str, service: str, con: dict, at: str, impl: str) -> dict:
    """工具页属性：**只放契约快照**（服务/实现由 MCP 服务页 + `agent:toolProvidedByMcp` 关系表达）。"""
    cols = {"order": [c.get("field") for c in (con.get("columns") or []) if c.get("field")],
            "labels": {c.get("field"): c.get("label")
                       for c in (con.get("columns") or []) if c.get("field")}}
    return {"agent:" + _PROP["name"]: tool,
            "agent:" + _PROP["modes"]: ",".join(con.get("modes") or []),
            "agent:" + _PROP["keys"]: ",".join(_keys_of(con)),
            "agent:" + _PROP["schema"]: con.get("schemaHash") or ""}




def sync(service: str, kb_id: str, tools: list | None = None, dry_run: bool = False) -> dict:
    """把 `service` 的工具契约同步成 `kb_id` 里的 `agent:Tool` 实例页（幂等）。

    同时确保 **MCP 服务页**存在（`agent/mcpservice/<service>`，带 `agent:endpoint`），
    工具页通过 `agent:toolProvidedByMcp` 指向它 —— 服务的地址/名称只维护一处（用户口径）。
    """
    urls = C.service_urls(service)
    if not urls:
        return {"ok": False, "error": "no_service_url", "service": service}
    # 逐个候选地址探活（`host.docker.internal` 在宿主/WSL 侧常解析不了 ⇒ 自动换下一个）
    url, names, errs = "", [], []
    for u in urls:
        try:
            names = list(C.list_tools(u) or [])
            url = u
            break
        except Exception as exc:  # noqa: BLE001
            errs.append("%s: %r" % (u, exc))
    if not url:
        return {"ok": False, "error": "list_tools_failed", "tried": urls, "detail": errs[:3]}

    targets = [t for t in (tools or names) if t in names] or list(tools or [])
    at = time.strftime("%Y-%m-%d %H:%M:%S")
    svc_slug = "agent/mcpservice/%s" % service
    svc_out = {}
    if not dry_run:                       # MCP 服务页（一次；地址/名称只维护一处）
        try:
            svc_out = ke_pages.write_knowledge(
                kb_id,
                {"slug": svc_slug, "title": "MCP 服务：%s" % service,
                 "page_type": "agent:MCPService", "mode": "entity",
                 "wiki_content": ("# MCP 服务：%s\n\n- **端点**：`%s`\n- **工具清单**：由服务端 "
                                  "`tools/list` 同步（工具页各自一页，关系 `toolProvidedByMcp`）\n"
                                  "- **刷新契约**：在各工具页点「刷新契约」（只同步**已建模**的工具）\n"
                                  % (service, url)),
                 "summary": "外部 MCP 服务 %s（%s）" % (service, url),
                 "attributes": {"agent:endpoint": url},
                 "source": {"doc_refs": [], "source_text": "契约同步 %s" % at}},
                strict_source=False, sync_folders=False)
        except Exception as exc:  # noqa: BLE001
            svc_out = {"error": repr(exc)[:200]}
    done, skipped = [], []
    for tool in targets:
        d = C.describe(service, tool, url=url)
        if not d.get("ok"):
            skipped.append({"tool": tool, "why": d.get("error") or "describe_failed",
                            "hint": d.get("hint") or ""})
            continue
        con = d.get("contract") or {}
        slug = "agent/tool/%s" % tool
        impl = _impl_of(service, tool)
        attrs = _attrs(tool, service, con, at, impl)
        if dry_run:
            done.append({"tool": tool, "slug": slug, "dry_run": True,
                         "schemaHash": con.get("schemaHash"),
                         "columns": len(con.get("columns") or [])})
            continue
        spec = {"slug": slug, "title": "工具：%s" % tool, "page_type": "agent:Tool",
                "mode": "entity",
                "wiki_content": _content(tool, service, url, con, at, impl),
                "summary": "外部工具 %s（%s）契约快照" % (tool, service),
                "attributes": attrs,
                "source": {"doc_refs": [], "source_text": "契约同步 %s/%s" % (service, at)}}
        ver = None
        try:
            out = ke_pages.write_knowledge(kb_id, spec, strict_source=False,
                                           sync_folders=False)
            ver = out.get("version")
        except Exception as exc:  # noqa: BLE001
            skipped.append({"tool": tool, "why": "write_failed", "detail": repr(exc)[:300]})
            continue

        # 图节点裸属性（绑定读口按 t.toolModes / t.toolKeyFields 读）+ 服务关系
        svc_slug = "agent/mcpservice/%s" % service
        ke_neo4j.query(
            "MERGE (s:BodhiInstance {kb_id:$kb, slug:$svc_slug}) "
            "ON CREATE SET s.page_type='agent:MCPService', s.module='agent', s.name=$svc, "
            "              s.endpoint=$url "
            "WITH s "
            "MERGE (t:BodhiInstance {kb_id:$kb, slug:$slug}) "
            "SET t.page_type='agent:Tool', t.module='agent', t.toolName=$tool, t.name=$tool, "
            "    t.toolModes=$modes, t.toolKeyFields=$keys, "
            "    t.toolSchemaHash=$hash "
            "MERGE (t)-[:`agent:toolProvidedByMcp`]->(s)",
            {"kb": kb_id, "slug": slug, "tool": tool, "svc": service, "svc_slug": svc_slug,
             "url": url, "modes": attrs["agent:toolModes"], "keys": attrs["agent:toolKeyFields"],
             "hash": con.get("schemaHash") or ""})


        done.append({"tool": tool, "slug": slug, "version": ver,
                     "schemaHash": con.get("schemaHash"),
                     "columns": len(con.get("columns") or [])})
    return {"ok": True, "service": service, "url": url, "kb_id": kb_id,
            "service_page": {"slug": svc_slug,
                             "version": (svc_out or {}).get("version"),
                             "error": (svc_out or {}).get("error")},
            "tools_total": len(names), "synced": done, "skipped": skipped}



