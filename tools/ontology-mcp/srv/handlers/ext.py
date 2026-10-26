"""`ext_describe`：把外部接口的**契约投射**到业务对象页（不手写列规范）。

为什么（2026-10-25 用户口径）
- 列名/列序/枚举/主键/模式只认 MCP 服务 `tools/list` 的 `x-ext`（**单一真源**）；
- 页面只保留**绑定**（4 个数据属性）+ **投影**（`page_metadata.ontology.projection`，
  带 `fetched_at` / `schemaHash` / `source`）⇒ 谁都能判"我手里的契约是否过期"；
- 写口不给改投影（只能重新订阅），`skos:note/definition` 也不再手写列规范 ✓。

本工具**只读外部服务**，写的是 bodhi 自己的页面元数据（PG），不动知识正文、不动图。
"""
from __future__ import annotations

import json

from srv.handlers import handle

TOOL = "ext_describe"


def _binds(kb_id: str, slug: str) -> list:
    """读某个 DataObject 页的绑定（与 `resolve_binding` 同一解析器，不重复实现）。"""
    import ke_ext_store as S
    return S.parse_binding(S._attrs_of(kb_id, slug))      # noqa: SLF001  同仓库内部解析器


def _service_of(binding: dict) -> str:
    """`impl` 形如 `it_platform:tools/...`，前缀即服务名；缺前缀 ⇒ 空串。"""
    impl = str((binding or {}).get("impl") or "")
    return impl.split(":", 1)[0].strip() if ":" in impl else ""


def _write_projection(kb_id: str, slug: str, proj: dict) -> dict:
    """把投影写进 `page_metadata.ontology.projection`（jsonb_set，幂等覆盖）。"""
    import ke_db
    sql = (
        "UPDATE wiki_pages SET page_metadata = jsonb_set("
        "  COALESCE(page_metadata, '{}'::jsonb), '{ontology,projection}', %s::jsonb, true), "
        "  updated_at = now() "
        "WHERE knowledge_base_id = %s AND slug = %s AND deleted_at IS NULL;"
        % (ke_db.sql_json(proj), ke_db.sql_str(kb_id), ke_db.sql_str(slug))
    )
    ke_db.psql(sql, stdin=True)
    return {"written": True}


def _upsert_anchor(kb_id: str, slug: str, block: str) -> dict:
    """把契约卡锚点块写进页面正文（**幂等**：已有同类块就整块替换，没有就追加）。

    为什么由工具写：锚点里的 `schema_hash` / `fetched_at` 会随契约变 ⇒ 手写必过期；
    工具订阅一次就把\"卡片的身份证\"刷新一次 ✓。只动这一个围栏块，其余正文不碰。
    """
    import re

    import ke_db
    rows = ke_db.psql_csv(
        "SELECT content, version FROM wiki_pages WHERE deleted_at IS NULL "
        "AND knowledge_base_id = %s AND slug = %s LIMIT 1"
        % (ke_db.sql_str(kb_id), ke_db.sql_str(slug)))
    if not rows:
        return {"written": False, "note": "page_not_found"}
    cur = rows[0].get("content") or ""
    pat = re.compile(r"```bodhi-ext-contract\n.*?```", re.S)
    m = pat.search(cur)
    if m:
        # 只在**契约本体**变化时重写：`fetched_at` 每次都会变，拿它比会导致"看一眼就涨版本" ✗
        norm = lambda s: re.sub(r"(?m)^fetched_at:.*$\n?", "", s)      # noqa: E731
        if norm(m.group(0)) == norm(block):
            return {"written": True, "changed": False, "version": rows[0].get("version")}
        new = pat.sub(lambda _m: block, cur, count=1)
    else:
        new = cur.rstrip() + "\n\n" + block + "\n"
    ke_db.psql(
        "UPDATE wiki_pages SET content = %s, version = COALESCE(version, 1) + 1, "
        "updated_at = now(), last_edit_source = 'ext_describe' "
        "WHERE knowledge_base_id = %s AND slug = %s AND deleted_at IS NULL;"
        % (ke_db.sql_str(new), ke_db.sql_str(kb_id), ke_db.sql_str(slug)), stdin=True)
    return {"written": True, "changed": True,
            "version": int(rows[0].get("version") or 1) + 1}


@handle(TOOL)
def ext_describe(args: dict) -> dict:
    import ke_ext_client as C
    import ke_db

    kb_id = str(args.get("kb_id") or "").strip()
    slug = str(args.get("object") or args.get("slug") or "").strip()
    want_tool = str(args.get("tool") or "").strip()
    url = str(args.get("url") or "").strip()
    write = args.get("write_projection", args.get("write", True))
    if isinstance(write, str):
        write = write.strip().lower() not in ("0", "false", "no", "off")

    if not kb_id:
        return {"ok": False, "tool": TOOL, "error": "need_kb",
                "hint": "需要 kb_id（业务对象所在库）"}
    if not slug:
        return {"ok": False, "tool": TOOL, "error": "need_object",
                "hint": "需要 object（业务对象页 slug）"}

    binds = _binds(kb_id, slug)
    if not binds:
        return {"ok": False, "tool": TOOL, "error": "no_binding", "object": {"slug": slug},
                "hint": ("该页没有绑定：先写 4 个数据属性 `bpmn:dataObjectInterface` / "
                         "`dataObjectAccessMode` / `dataObjectKeyFields` / `dataObjectInterfaceRef`")}
    names = [b.get("tool") for b in binds]
    binding = next((b for b in binds if b.get("tool") == want_tool), None) if want_tool else binds[0]
    if binding is None:
        return {"ok": False, "tool": TOOL, "error": "tool_mismatch", "available_tools": names,
                "object": {"slug": slug}}
    tool = str(binding.get("tool") or "")
    service = _service_of(binding)

    got = C.describe(service, tool, url=url)
    if not got.get("ok"):
        return {"ok": False, "tool": TOOL, **{k: got[k] for k in
                ("error", "hint", "tried", "detail", "tools") if got.get(k)},
                "object": {"slug": slug}, "binding": binding}

    contract = got["contract"]
    fetched_at = ke_db.now_text()
    proj = {"source": "mcp:tools/list", "service": service, "url": got["url"],
            "tool": tool, "fetched_at": fetched_at,
            "schemaHash": contract.get("schemaHash") or "",
            "contract": contract}

    # 漂移：与现有投影的 hash 比（换版本/改列/改枚举都会变 ✓）
    prev = {}
    try:
        rows = ke_db.psql_csv(
            "SELECT page_metadata FROM wiki_pages WHERE deleted_at IS NULL "
            "AND knowledge_base_id = %s AND slug = %s" % (ke_db.sql_str(kb_id), ke_db.sql_str(slug)))
        if rows:
            meta = json.loads(rows[0].get("page_metadata") or "{}")
            prev = ((meta.get("ontology") or {}).get("projection") or {})
    except Exception:                    # noqa: BLE001  读不到旧投影不影响本次投射
        prev = {}
    drift = {"changed": bool(prev) and prev.get("schemaHash") != proj["schemaHash"],
             "previous_hash": prev.get("schemaHash") or "",
             "previous_fetched_at": prev.get("fetched_at") or ""}

    out = {"ok": True, "tool": TOOL, "object": {"slug": slug},
           "binding": {"tool": tool, "modes": binding.get("modes"),
                       "keys": binding.get("keys"), "impl": binding.get("impl")},
           "service": {"name": service, "url": got["url"]},
           "contract": contract, "drift": drift, "fetched_at": fetched_at}
    if write:
        out["projection"] = _write_projection(kb_id, slug, proj)
    else:
        out["projection"] = {"written": False, "note": "write_projection=false（只回契约）"}
    out["hint"] = ("报告/面板都读本投射（`page_metadata.ontology.projection`）；"
                   "列规范不要再写进 `skos:note/definition` ✓")
    # 页面锚点：DataObject 实例页正文放这个块 ⇒ 面板渲染「接口契约卡」（只读 + 刷新契约）
    out["ref_block"] = ("```bodhi-ext-contract\nkind: contract\nobject: %s\nimpl: %s\ntool: %s\n"
                        "schema_hash: %s\nfetched_at: %s\n```"
                        % (slug, service, tool, proj["schemaHash"], fetched_at))
    anchor = args.get("write_anchor", False)
    if isinstance(anchor, str):
        anchor = anchor.strip().lower() not in ("0", "false", "no", "off")
    if anchor:
        # 显式要求才动正文（默认不动：读契约不该改页面 ✓）
        out["anchor"] = _upsert_anchor(kb_id, slug, out["ref_block"])
    return out
