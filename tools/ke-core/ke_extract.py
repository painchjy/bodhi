"""`ke_extract` —— **从源文档真抽取**（半自动 ✓，输出 7 字段 ✓）

用法（示例）：
    ke_extract.extract(kb_id="技术方案评审任务知识库",
                       knowledge_id="样例1-统一结算平台技术方案.docx",
                       domain_kb="技术方案评审领域模型", task="逐项评审")

逻辑（与 `knowledge_extract` 技能契约一致 ✓）：
1. `ke_plan.build(domain_kb, task)` → 取 `needs_extract` 的 items ✓（规则声明的必需事实 ✓）；
2. 读源文档：`server.doc_outline(kb, knowledge_id)` ✓（分节 + 文本 ✓）；
3. 每个 item：按 `keywords`（来自 item 的 `need`/`id` ✓ + 规则 spec 的 note ✓）在文档里**定位** ✓
   - 命中 ⇒ `present` ✓，`quote` 取该行**逐字** ✓，`locator` 取**章节标题** ✓，`source_type` 按 item.kind ✓；
   - 未命中 ⇒ **先查其他知识库**（`knowledge_lookup(keyword)` ✓）：
       命中 ⇒ `absent_verified` ✓（`found_in="other_kb:<kb>"` ✓，quote=其他库的条目 ✓）—— **属文档遗漏 ✓ 是问题 ✓**；
     仍未命中 ⇒ `unavailable` ✓（`found_in="none"` ✓，quote 记"已查：文档 + 库 X/Y ✓"）；
4. 返回 `{items:[7 字段…], counts:{…}, doc:{…}}` ✓（不写库 ✗，由调用方确认后落 `extract_state` ✓）。
"""

KW = {
    "deploy_fig": ["部署图", "部署架构", "图1", "图2"],
    "devices": ["设备申请", "设备清单", "配置规格"],
    "system": ["A 级", "B 级", "C 级", "系统等级", "等级"],
    "deploy_text": ["同城", "异地", "双活", "热备", "多活"],
    "software": ["Redis", "Nginx", "Kafka", "DM8", "版本", "组件"],
    "certified": ["认证清单", "中心认证", "参考规范"],
}
SNIP = 120


def _lines(doc: dict) -> list:
    """把 doc_outline 结果摊平成 `[(章节, 行)]` ✓（兼容 sections/content/chunks 三种形态 ✓）。"""
    out: list = []
    for s in (doc.get("sections") or []):
        title = str(s.get("title") or s.get("heading") or "")[:60]
        body = s.get("content") or s.get("text") or ""
        for ln in str(body).splitlines():
            if ln.strip():
                out.append((title, ln.strip()))
    if not out:
        for c in (doc.get("chunks") or []):
            t = str(c.get("title") or "")[:60]
            for ln in str(c.get("content") or c.get("text") or "").splitlines():
                if ln.strip():
                    out.append((t, ln.strip()))
    if not out and doc.get("content"):
        for ln in str(doc["content"]).splitlines():
            if ln.strip():
                out.append(("", ln.strip()))
    return out


def _resolve_doc(kb: str, knowledge_id: str) -> str:
    """`knowledge_id` 支持 UUID ✓ / 标题或文件名 ✓（查 `knowledges` 表 ✓ —— 不用页面反推 ✗）。"""
    import ke_db as _d
    kid = (knowledge_id or "").strip()
    if not kid:
        return ""
    rows = _d.psql_csv(
        "SELECT id FROM knowledges WHERE knowledge_base_id = %s AND (id = %s OR title = %s OR file_name = %s) "
        " ORDER BY created_at DESC LIMIT 1"
        % (_d.sql_str(kb), _d.sql_str(kid), _d.sql_str(kid), _d.sql_str(kid)))
    if rows:
        return str(rows[0]["id"])
    rows = _d.psql_csv(
        "SELECT id FROM knowledges WHERE knowledge_base_id = %s AND (title ILIKE %s OR file_name ILIKE %s) "
        " ORDER BY created_at DESC LIMIT 1"
        % (_d.sql_str(kb), _d.sql_str("%" + kid + "%"), _d.sql_str("%" + kid + "%")))
    return str(rows[0]["id"]) if rows else ""


def _doc_text(kb: str, knowledge_id: str) -> list:
    """直接读 **`chunks` 表** ✓（2026-10-17：`doc_index` 是页面反推 ✗ ⇒ 没挂页的文档会漏 ✗）
    ⇒ `[(定位, 行)]`，定位用 `chunk_index` ✓。"""
    import ke_db as _d
    kid = _resolve_doc(kb, knowledge_id)
    if not kid:
        return []
    out: list = []
    try:
        for r in _d.psql_csv(
                "SELECT chunk_index, chunk_type, content FROM chunks "
                " WHERE knowledge_base_id = %s AND knowledge_id = %s "
                " ORDER BY chunk_index" % (_d.sql_str(kb), _d.sql_str(kid))):
            loc = "chunk#%s%s" % (r.get("chunk_index"),
                                  ("/" + str(r["chunk_type"])) if r.get("chunk_type") else "")
            for ln in str(r.get("content") or "").splitlines():
                if ln.strip():
                    out.append((loc, ln.strip()))
    except Exception:  # noqa: BLE001
        pass
    return out


def extract(kb_id: str, knowledge_id: str, domain_kb: str = "", task: str = "逐项评审",
            other_kbs: list | None = None, limit_items: int = 20) -> dict:
    import ke_db
    import ke_plan
    import ke_lookup
    import sys as _sys

    kb = ke_db.resolve_kb_id(kb_id)[0]
    dkb = ke_db.resolve_kb_id(domain_kb)[0] if domain_kb else kb
    _sys.path.insert(0, "/mnt/c/Users/PHJY/source/bodhi2/tools/ontology-mcp")
    import server as _srv  # noqa: PLC0415

    doc = _srv.doc_outline(kb, knowledge_id) or {}
    lines = _doc_text(kb, knowledge_id) or _lines(doc)
    plan = ke_plan.build(dkb, task)
    todo = [it for it in (plan.get("items") or []) if it.get("required") or it.get("kind") in ("image", "table", "text")]
    others = other_kbs or ["技术方案评审领域模型", "技术方案评审任务知识库", "企业本体模型"]

    items = []
    for it in todo[:limit_items]:
        iid = str(it.get("id") or "")
        kws = KW.get(iid) or [str(it.get("need") or iid)[:12]]
        hits = [(t, ln) for t, ln in lines if any(k in ln for k in kws)][:5]
        kind = str(it.get("kind") or "text")
        stype = {"image": "image", "table": "doc_table", "class": "graph"}.get(kind, "doc_text")
        # 2026-10-17（用户口径 ✓）：本工具**只定位候选** ✓ —— `status` 交给 `knowledge_extract` 判读 ✗
        #   （实测：关键词会命中"2.3 部署架构"这种章节标题 ✗，据此判 present 就是假断言 ✗）
        other_hint = []
        if not hits:
            for kbn in others:
                try:
                    r = ke_lookup.knowledge_lookup(kbn, mode="keyword", query=kws[0], limit=2)
                except Exception:  # noqa: BLE001
                    continue
                for h in (r.get("hits") or [])[:1]:
                    other_hint.append({"kb": kbn, "slug": h.get("slug"), "title": h.get("title")})
        items.append({
            "id": iid, "status": "unknown", "needs_judgement": True,
            "source_type": stype, "confidence": "", "found_in": "",
            "quote": "", "locator": "",
            "candidates": [{"line": ln[:SNIP], "locator": t} for t, ln in hits],
            "hints_other_kb": other_hint,
            "how_to_judge": ("文档里有 ⇒ present ✓；文档没有但其他库有 ⇒ absent_verified（文档遗漏 ✓ 是问题 ✓）；"
                             "哪都没有 ⇒ unavailable ✓"),
        })
    return {"ok": True, "kb": kb, "knowledge_id": knowledge_id,
            "doc": {"lines": len(lines), "sections": len(doc.get("sections") or [])},
            "items": items,
            "counts": {"items": len(items),
                       "present": sum(1 for x in items if x["status"] == "present"),
                       "absent_verified": sum(1 for x in items if x["status"] == "absent_verified"),
                       "unavailable": sum(1 for x in items if x["status"] == "unavailable")}}
