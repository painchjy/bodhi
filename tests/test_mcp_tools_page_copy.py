"""page_copy（P3.5 能力前移）L1 用例：副本保留类型/属性/关系，**源页零改动**。"""
import json


def test_page_copy_keeps_type_and_source_untouched(mcp, test_kb, session_no):
    args = {"kb_id": test_kb, "stage": "report", "mode": "apply", "session_no": session_no,
            "report": {"title": "复制源页", "slug": "review/_copy-src",
                       "content_md": "# 复制源页\n\n## ① 范围与依据\n- x\n"}}
    r = mcp.call("save_knowledge", args)
    assert r.get("applied") is True, json.dumps(r, ensure_ascii=False)[:300]
    before = mcp.call("knowledge_lookup", {"kb_id": test_kb, "mode": "keyword", "query": "_copy-src"})
    src = next((h for h in (before.get("hits") or []) if h.get("slug") == "review/_copy-src"), None)
    assert src, "源页未落库 ✗"
    src_ver = src.get("version")

    c = mcp.call("page_copy", {"kb_id": test_kb, "src_slug": "review/_copy-src"})
    assert c.get("ok") is True, json.dumps(c, ensure_ascii=False)[:300]
    assert c.get("slug") == "review/_copy-src-copy", c
    assert c.get("page_type") == "bmm:TechnicalReviewReport", c

    after = mcp.call("knowledge_lookup", {"kb_id": test_kb, "mode": "keyword", "query": "_copy-src"})
    hits = {h.get("slug"): h for h in (after.get("hits") or [])}
    assert "review/_copy-src-copy" in hits, "副本未落库 ✗：%s" % sorted(hits)
    assert hits.get("review/_copy-src", {}).get("version") == src_ver, "源页被改动 ✗"
