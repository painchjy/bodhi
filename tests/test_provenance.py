"""来源 locator / 规则表达式 回归（纯函数，无 DB）。

口径：`bpmn:TextualDataObject` 的 locator 语法；`object/key` 只允许 ext-doc；
会话来源才配 sourceSession；`rules=` 支持多策略/组合策略/单条规则。
"""
import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(REPO, "tools", "ke-core"))

import ke_provenance as P  # noqa: E402
import ke_rule_expr as RX  # noqa: E402
import ke_source_text as ST  # noqa: E402

sys.path.insert(0, os.path.join(REPO, "tools", "ontology-mcp"))
from srv import elements as E  # noqa: E402


def test_ext_doc_requires_object_key_and_roundtrips():
    loc = P.build_locator(P.EXTERNAL_DOC, doc_id="doc-1", object="设备清单",
                          key="review_no=R-001", section="2.3 部署架构")
    assert loc.startswith("ext-doc:doc-1#object:")
    got = P.parse_locator(loc)
    assert got["ok"] is True, got
    assert got["kind"] == P.EXTERNAL_DOC
    assert got["doc_id"] == "doc-1"
    assert got["object"] == "设备清单"
    assert got["key_field"] == "review_no"
    assert got["key_value"] == "R-001"
    assert got["section"] == "2.3 部署架构"


def test_ext_doc_missing_object_or_key_is_invalid():
    bad = P.build_locator(P.EXTERNAL_DOC, doc_id="d", object="x", key="k=v")
    assert P.parse_locator(bad)["ok"] is True
    # 手写缺 object/key
    assert P.parse_locator("ext-doc:d#section:1")["ok"] is False
    assert P.parse_locator("ext-doc:d#object:x#section:1")["ok"] is False
    # key 没有 "="
    assert P.parse_locator("ext-doc:d#object:x#key:abc#section:1")["ok"] is False


def test_object_key_only_for_ext_doc():
    for loc in (
        "kb:kb-1/doc:d-1#object:x#key:k=v#section:1",
        "session:s-1/att:f.pdf#sha256:abc#object:x#key:k=v#section:1",
        "session:s-1/msg:m1#object:x#key:k=v",
        "page:kb-1/bpmn/task/x#object:x#key:k=v",
        "file:/tmp/f.pdf#sha256:abc#object:x#key:k=v",
    ):
        got = P.parse_locator(loc)
        assert got["ok"] is False, (loc, got)
        assert any("object/key 只允许 ext-doc" in e for e in got["errors"]), got


def test_other_kinds_parse_and_session_detection():
    cases = [
        (P.build_locator(P.KB_DOCUMENT, kb_id="kb-1", doc_id="d-1", section="2.1"),
         P.KB_DOCUMENT),
        (P.build_locator(P.SESSION_ATTACHMENT, session_no="s-1", filename="f.pdf",
                         sha256="abc", section="p3"), P.SESSION_ATTACHMENT),
        (P.build_locator(P.SESSION_TEXT, session_no="s-1", message_id="m-1", section="3"),
         P.SESSION_TEXT),
        (P.build_locator(P.WIKI_PAGE, kb_id="kb-1", slug="bpmn/task/x", section="步骤"),
         P.WIKI_PAGE),
        (P.build_locator(P.FILE, path="/tmp/f.pdf", sha256="abc"), P.FILE),
    ]
    for loc, kind in cases:
        got = P.parse_locator(loc)
        assert got["ok"] is True, (loc, got)
        assert got["kind"] == kind
        assert bool(P.resolver_for(kind)) is True
    assert P.is_session(P.SESSION_ATTACHMENT) is True
    assert P.is_session(P.SESSION_TEXT) is True
    assert P.is_session(P.KB_DOCUMENT) is False


def test_rule_expr_parse_multi_composite_individual():
    terms = RX.parse("policy:p1,policy:p2,rule:r1,r2")
    assert [(t["kind"], t["value"]) for t in terms] == [
        ("policy", "p1"), ("policy", "p2"), ("rule", "r1"), ("rule", "r2")]
    assert RX.parse("") == []


def test_source_excerpt_is_verbatim_and_bounded():
    text = "# 背景\n\n" + ("背景内容。" * 30) + "\n\n## 业务目标\n\n" \
        + ("提高评审效率，降低评审周期。" * 30) + "\n\n## 范围\n\n不应被取到。"
    got = ST.excerpt_from_text(text, "业务目标", max_chars=150)
    assert got["ok"] is True, got
    assert got["text"].startswith("提高评审效率")
    assert "背景内容" not in got["text"]
    assert "不应被取到" not in got["text"]
    assert len(got["text"]) == 150
    assert got["truncated"] is True
    assert got["matched_by"] == "section_exact"


def test_source_section_not_found_lists_candidates_and_never_placeholders():
    got = ST.excerpt_from_text("# 背景\n\n正文\n\n## 业务目标\n\n目标正文", "不存在")
    assert got["ok"] is False
    assert got["code"] == "section_not_found"
    assert "业务目标" in got["available"]
    assert "无原文片段" not in str(got)
    old = ST.excerpt_from_text("# 原文依据\n\n（概要设计，无原文片段）")
    assert old["ok"] is False and old["code"] == "source_placeholder"


def test_source_payload_uses_verbatim_text_and_explicit_fallback(monkeypatch):
    monkeypatch.setattr(E.ST, "excerpt", lambda _loc: {
        "ok": True, "text": "逐字摘录", "chars": 4, "truncated": False,
        "section": "业务目标", "source_title": "需求.docx", "kind": "kb_document"})
    text, meta = E._source_payload({"source_text": "调用方旧文案"},
                                   "kb:K/doc:D#section:业务目标")
    assert text == "逐字摘录"
    assert meta["status"] == "resolved"
    assert meta["source_title"] == "需求.docx"

    monkeypatch.setattr(E.ST, "excerpt", lambda _loc: {
        "ok": False, "code": "section_not_found", "available": ["背景"]})
    text, meta = E._source_payload({"source_text": "调用方原文"},
                                   "kb:K/doc:D#section:不存在")
    assert text == "调用方原文"
    assert meta["status"] == "section_not_found"
    assert meta["explicit_fallback"] is True
    assert "无原文片段" not in text

    text, _meta = E._source_payload({"source_text": "（概要设计，无原文片段）"},
                                    "kb:K/doc:D#section:不存在")
    assert text == ""


def test_context_meta_old_context_maps_to_neutral():
    from srv.render import SKILL_CONTEXTS, context_meta
    assert "no_quote" not in SKILL_CONTEXTS.get("domain_modeling", {})
    assert "evidence_generated" not in SKILL_CONTEXTS.get("domain_modeling", {})
    for old in ("process_modeling", "rule_engineering", "design",
                "ea_overview_design", "service_detailed_design"):
        meta = context_meta(old)
        assert meta["id"] == "knowledge_modeling", (old, meta)
        assert "概要设计" not in str(meta)
        assert "无原文片段" not in str(meta)


def test_build_new_page_renders_verbatim_excerpt_not_placeholder():
    page = E.build_new_page(
        None, {"key": "bmm", "label": "BMM"},
        {"name": "目标", "type": "bmm:Goal", "type_label": "目标",
         "definition": "定义", "source_text": "提高评审效率，降低评审周期。",
         "source_locator": "kb:K/doc:D#section:业务目标",
         "source_meta": {"status": "resolved", "source_title": "需求.docx",
                         "section": "业务目标", "truncated": True, "chars": 150},
         "category_path": ["测试"]},
        "", -1, {"id": "D", "title": "需求.docx", "context": "rule_engineering"})
    content = page["content"]
    assert "## 原文依据" in content
    assert "提高评审效率" in content
    assert "无原文片段" not in content
    assert page["page_metadata"]["provenance"]["status"] == "resolved"
    assert page["page_metadata"]["provenance"]["source_title"] == "需求.docx"


def test_source_excerpt_smoke_from_kb_document():
    """真实库冒烟：任取一篇有 Markdown 标题的文档，按首个标题回读。"""
    import re
    import ke_db
    try:
        docs = ke_db.psql_csv(
            "SELECT knowledge_base_id, knowledge_id, content FROM chunks "
            "WHERE deleted_at IS NULL AND content LIKE '%# %' "
            "ORDER BY created_at DESC LIMIT 1")
    except Exception as exc:  # noqa: BLE001
        pytest.skip("DB 不可用：%r" % (str(exc)[:80],))
    if not docs:
        pytest.skip("库里没有可回读的文档片段")
    row = docs[0]
    m = re.search(r"^#{1,6}\s+(.+?)\s*$", row.get("content") or "", re.M)
    if not m:
        pytest.skip("文档片段没有 Markdown 标题")
    loc = P.build_locator(P.KB_DOCUMENT, kb_id=row["knowledge_base_id"],
                          doc_id=row["knowledge_id"], section=m.group(1).strip())
    got = ST.excerpt(loc)
    assert got.get("ok") is True, got
    assert got.get("text")
    assert "无原文片段" not in got["text"]
