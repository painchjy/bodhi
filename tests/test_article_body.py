"""W4a · 正文主键 `articleBody`（图本）+ 迁移工具（全部走 MCP）。

口径（2026-10-22 用户裁定）：
- **正文事实源 = 图节点 `articleBody`**；旧键 `wiki_content` 只作兼容别名（双写、读用 coalesce）；
- PG `wiki_pages.content` 是**渲染缓存**（`rebuild_wiki_from_graph` 可从图重建）；
- `page_metadata.ontology.attributes` **不再存正文**（`wikiContent` 会被写路径剔除，历史残留由迁移工具清）。

跑法：`python3 -m pytest tests/test_article_body.py`
"""
import json


def _write(mcp, kb, session_no, slug, title):
    return mcp.call("save_knowledge", {
        "kb_id": kb, "stage": "report", "mode": "apply", "session_no": session_no,
        "report": {"title": title, "slug": slug,
                   "content_md": "# %s\n\n## ① 范围与依据\n- 正文主键用例\n" % title}})


def test_write_sets_article_body_and_skips_attr(mcp, test_kb, session_no):
    r = _write(mcp, test_kb, session_no, "review/_ab-page", "正文主键用例")
    assert r.get("applied") is True, json.dumps(r, ensure_ascii=False)[:400]

    dry = mcp.call("migrate_article_body", {"kb_id": test_kb, "dry_run": True})
    assert dry.get("ok") is True, dry
    g = dry.get("graph") or {}
    pm = dry.get("page_metadata") or {}
    assert int(g.get("pending") or 0) == 0, "写路径没双写 articleBody ✗：%s" % g
    assert int(pm.get("hits") or 0) == 0, "正文被写进 page_metadata（应剔除）✗：%s" % pm


def test_migrate_is_idempotent(mcp, test_kb, session_no):
    _write(mcp, test_kb, session_no, "review/_ab-idem", "正文主键幂等")
    first = mcp.call("migrate_article_body", {"kb_id": test_kb, "dry_run": False})
    assert first.get("ok") is True, first
    second = mcp.call("migrate_article_body", {"kb_id": test_kb, "dry_run": False})
    assert int((second.get("graph") or {}).get("migrated") or 0) == 0, "回填不幂等 ✗：%s" % second
    assert int((second.get("page_metadata") or {}).get("cleaned") or 0) == 0, \
        "清理不幂等 ✗：%s" % second


def test_snippet_comes_from_graph_article_body(mcp, test_kb, session_no):
    """命中片段来自**图里的 `articleBody`**（不是 PG 截断 summary）⇒ 证明图本可读 ✓。"""
    mark = "正文主键标记XYZ"
    r = mcp.call("save_knowledge", {
        "kb_id": test_kb, "stage": "report", "mode": "apply", "session_no": session_no,
        "report": {"title": "正文片段用例", "slug": "review/_ab-snippet",
                   "content_md": "# 正文片段用例\n\n## ① 范围与依据\n- %s\n" % mark}})
    assert r.get("applied") is True, json.dumps(r, ensure_ascii=False)[:400]

    got = mcp.call("knowledge_lookup", {"kb_id": test_kb, "mode": "keyword", "query": mark})
    hit = next((h for h in (got.get("hits") or []) if h.get("slug") == "review/_ab-snippet"), None)
    assert hit, "按正文标记检索不到（说明正文没进图 / 没走 articleBody）✗：%s" % \
        json.dumps(got, ensure_ascii=False)[:300]
    assert mark in (hit.get("snippet") or ""), "snippet 不含命中词 ⇒ 片段不是取自图正文 ✗：%r" % \
        hit.get("snippet")
