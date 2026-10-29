"""外部 MCP 工具 → 领域库 `agent:Tool` **实例页**（契约同步；只新增/更新，不删）。

口径（2026-10-29 用户口径 / 图本原则）
- 工具实例属于**具体领域库**（与该库的 DataObject 同库）：前端可点开、`regen_wiki` 可对账、
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
         "keys": "toolKeyFields", "cols": "toolColumns", "schema": "toolSchemaHash",
         "at": "toolContractAt", "impl": "toolImpl"}


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
            "agent:" + _PROP["cols"]: json.dumps(cols, ensure_ascii=False),
            "agent:" + _PROP["schema"]: con.get("schemaHash") or "",
            "agent:" + _PROP["at"]: at}



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
            "SET t.page_type='agent:Tool', t.module='agent', t.name=$tool, "
            "    t.toolModes=$modes, t.toolKeyFields=$keys, "
            "    t.toolColumns=$cols, t.toolSchemaHash=$hash, t.toolContractAt=$at "
            "MERGE (t)-[:`agent:toolProvidedByMcp`]->(s)",
            {"kb": kb_id, "slug": slug, "tool": tool, "svc": service, "svc_slug": svc_slug,
             "url": url, "modes": attrs["agent:toolModes"], "keys": attrs["agent:toolKeyFields"],
             "cols": attrs["agent:toolColumns"], "hash": con.get("schemaHash") or "",
             "at": at})

        done.append({"tool": tool, "slug": slug, "version": ver,
                     "schemaHash": con.get("schemaHash"),
                     "columns": len(con.get("columns") or [])})
    return {"ok": True, "service": service, "url": url, "kb_id": kb_id,
            "service_page": {"slug": svc_slug,
                             "version": (svc_out or {}).get("version"),
                             "error": (svc_out or {}).get("error")},
            "tools_total": len(names), "synced": done, "skipped": skipped}



