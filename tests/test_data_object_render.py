"""DataObject 正文：entity 渲染 + 契约块；多种写入方式不得重复正文。"""

import json
import re


def _save(mcp, kb, sno, name, note):
    r = mcp.call("save_knowledge", {
        "kb_id": kb, "stage": "graph", "mode": "apply", "session_no": sno,
        "nodes": [{"name": name, "type": "bpmn:DataObject",
                   "attributes": {"skos:definition": "对象定义", "skos:note": note}}]})
    assert r.get("applied") is True, json.dumps(r, ensure_ascii=False)[:600]
    slugs = [x.get("slug") or x.get("into") for x in ((r.get("created") or []) + (r.get("merged") or [])) if x.get("slug") or x.get("into")]
    assert slugs, json.dumps(r, ensure_ascii=False)[:600]
    return slugs[0], r


def _page(mcp, kb, sno, slug):
    r = mcp.call("knowledge_lookup", {"mode": "page", "query": slug,
                                      "session_no": sno, "kb_id": kb})
    assert r.get("ok") is True, json.dumps(r, ensure_ascii=False)[:600]
    return r.get("page") or {}


def _counts(text):
    return (len(re.findall(r"(?ms)^```bodhi-ext-contract\s*$.*?^```\s*$", str(text or ""))),
            str(text or "").count("## 字段表"))


def _note(tag):
    return ("建模任务编号：TEST-%s\n\n"
            "## 字段表\n\n| 字段 | 含义 |\n|---|---|\n| a | %s |\n" % (tag, tag))


def test_data_object_save_is_entity_and_block_idempotent(mcp, test_kb, session_no):
    tag = "DO-01"
    slug, first = _save(mcp, test_kb, session_no, "对象唯一性测试", _note(tag))
    page = _page(mcp, test_kb, session_no, slug)
    body = page.get("content_md") or ""
    assert _counts(body) == (1, 1), "首次写入重复：%r page_type=%r panel_blocks=%r" % (_counts(body), page.get("page_type"), first.get("panel_blocks"))

    slug2, _ = _save(mcp, test_kb, session_no, "对象唯一性测试", _note("DO-02"))
    assert slug2 == slug
    page = _page(mcp, test_kb, session_no, slug)
    body = page.get("content_md") or ""
    assert _counts(body) == (1, 1), "重复 save 后重复：%r" % (_counts(body),)
    assert "DO-02" in body and "DO-01" not in body


def test_data_object_attribute_preview_and_apply_do_not_duplicate(mcp, test_kb, session_no):
    slug, _ = _save(mcp, test_kb, session_no, "属性编辑测试", _note("ATTR-01"))
    note2 = _note("ATTR-02")
    dry = mcp.call("page_attributes_set", {"kb_id": test_kb, "slug": slug,
                                            "patch": {"skos:note": note2}, "dry_run": True})
    assert dry.get("ok") is True, json.dumps(dry, ensure_ascii=False)[:600]
    assert "ATTR-02" in (dry.get("content_preview") or "")
    assert _counts(dry.get("content_preview") or "") == (1, 1), "预览重复：%r" % (_counts(dry.get("content_preview") or ""),)

    applied = mcp.call("page_attributes_set", {"kb_id": test_kb, "slug": slug,
                                               "patch": {"skos:note": note2}, "dry_run": False})
    assert applied.get("ok") is True and applied.get("applied") is True, json.dumps(applied, ensure_ascii=False)[:600]
    page = _page(mcp, test_kb, session_no, slug)
    assert _counts(page.get("content_md") or "") == (1, 1), "写入后重复：%r" % (_counts(page.get("content_md") or ""),)
    assert "ATTR-01" not in (page.get("content_md") or "")


def test_data_object_binding_and_mapping_do_not_duplicate_body(mcp, test_kb, session_no):
    slug, _ = _save(mcp, test_kb, session_no, "绑定映射测试", _note("BIND-01"))
    import ke_ext_binding as B
    b1 = B.set_binding(test_kb, slug, "ext_issue_list")
    assert b1.get("ok") is True, b1
    b2 = B.set_binding(test_kb, slug, "ext_issue_list")
    assert b2.get("ok") is True, b2
    m1 = B.set_mapping(test_kb, slug, "ext_issue_list", [
        {"field": "issue_no", "label": "编号"},
        {"field": "desc", "label": "问题描述"}])
    assert m1.get("ok") is True, m1
    page = _page(mcp, test_kb, session_no, slug)
    assert _counts(page.get("content_md") or "") == (1, 1), "绑定/映射后重复：%r" % (_counts(page.get("content_md") or ""),)


def test_other_entity_save_is_idempotent(mcp, test_kb, session_no):
    """非 DataObject 的 entity 类也不得因重复 save 重复正文。"""
    note = _note("TASK-01")
    args = {"kb_id": test_kb, "stage": "graph", "mode": "apply", "session_no": session_no,
            "nodes": [{"name": "实体幂等测试", "type": "bpmn:Task",
                       "attributes": {"skos:definition": "任务定义", "skos:note": note}}]}
    r1 = mcp.call("save_knowledge", args)
    assert r1.get("applied") is True, json.dumps(r1, ensure_ascii=False)[:600]
    slug = ((r1.get("created") or r1.get("merged") or [{}])[0]).get("slug") or ((r1.get("merged") or [{}])[0]).get("into")
    assert slug
    r2 = mcp.call("save_knowledge", args)
    assert r2.get("applied") is True, json.dumps(r2, ensure_ascii=False)[:600]
    page = _page(mcp, test_kb, session_no, slug)
    assert _counts(page.get("content_md") or "") == (0, 1), "entity 重复：%r" % (_counts(page.get("content_md") or ""),)


def test_data_object_regeneration_cleans_legacy_duplicate(mcp, test_kb, session_no):
    """旧页已有重复字段表/契约块时，重新 save_knowledge 必须覆盖成单份。"""
    slug, _ = _save(mcp, test_kb, session_no, "旧重复清理测试", _note("OLD-01"))
    import ke_db
    stale = ("\n\n```bodhi-ext-contract\nkind: stale\n```\n\n"
             "## 字段表\n\n| 字段 | 含义 |\n|---|---|\n| old | old |\n")
    ke_db.psql("UPDATE wiki_pages SET content = content || %s WHERE knowledge_base_id = %s "
               "AND slug = %s AND deleted_at IS NULL"
               % (ke_db.sql_str(stale), ke_db.sql_str(test_kb), ke_db.sql_str(slug)))
    _save(mcp, test_kb, session_no, "旧重复清理测试", _note("OLD-02"))
    page = _page(mcp, test_kb, session_no, slug)
    body = page.get("content_md") or ""
    assert _counts(body) == (1, 1), "旧页重生成后仍重复：%r" % (_counts(body),)
    assert "OLD-02" in body and "| old | old |" not in body


def test_other_entity_attribute_preview_and_apply_do_not_duplicate(mcp, test_kb, session_no):
    """非 DataObject entity 的数据属性预览/写入同样不得重复。"""
    note = _note("ENT-01")
    args = {"kb_id": test_kb, "stage": "graph", "mode": "apply", "session_no": session_no,
            "nodes": [{"name": "实体属性测试", "type": "bmm:OperativeBusinessRule",
                       "attributes": {"skos:definition": "规则定义", "skos:note": note}}]}
    r = mcp.call("save_knowledge", args)
    assert r.get("applied") is True, json.dumps(r, ensure_ascii=False)[:600]
    slug = ((r.get("created") or r.get("merged") or [{}])[0]).get("slug") or ((r.get("merged") or [{}])[0]).get("into")
    assert slug
    dry = mcp.call("page_attributes_set", {"kb_id": test_kb, "slug": slug,
                                            "patch": {"skos:note": _note("ENT-02")}, "dry_run": True})
    assert dry.get("ok") is True, json.dumps(dry, ensure_ascii=False)[:600]
    assert _counts(dry.get("content_preview") or "") == (0, 1), "entity 预览重复：%r" % (_counts(dry.get("content_preview") or ""),)
    applied = mcp.call("page_attributes_set", {"kb_id": test_kb, "slug": slug,
                                               "patch": {"skos:note": _note("ENT-02")}, "dry_run": False})
    assert applied.get("ok") is True and applied.get("applied") is True, json.dumps(applied, ensure_ascii=False)[:600]
    page = _page(mcp, test_kb, session_no, slug)
    assert _counts(page.get("content_md") or "") == (0, 1), "entity 写入后重复：%r" % (_counts(page.get("content_md") or ""),)
