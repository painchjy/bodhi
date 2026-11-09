"""来源 locator / 规则表达式 回归（纯函数，无 DB）。

口径：`bpmn:TextualDataObject` 的 locator 语法；`object/key` 只允许 ext-doc；
会话来源才配 sourceSession；`rules=` 支持多策略/组合策略/单条规则。
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(REPO, "tools", "ke-core"))

import ke_provenance as P  # noqa: E402
import ke_rule_expr as RX  # noqa: E402


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
