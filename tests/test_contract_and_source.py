"""契约块类型门禁 + sourceLocator 优先级（纯函数/无写库）。"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
for p in (os.path.join(REPO, "tools", "ke-core"),
          os.path.join(REPO, "tools", "ontology-mcp")):
    if p not in sys.path:
        sys.path.insert(0, p)

import ke_ext_tools  # noqa: E402
from srv import elements as E  # noqa: E402
from srv.handlers import write as W  # noqa: E402


def test_contract_block_only_dataobject_or_tool():
    assert ke_ext_tools._is_contract_page("bpmn:DataObject") is True
    assert ke_ext_tools._is_contract_page("bpmn:TextualDataObject") is True
    assert ke_ext_tools._is_contract_page("agent:Tool") is True
    for pt in ("bmm:Goal", "bmm:Objective", "bmm:InternalInfluencer",
               "bmm:CourseOfAction", "bpmn:Task"):
        assert ke_ext_tools._is_contract_page(pt) is False, pt


def test_explicit_source_locator_wins(monkeypatch):
    monkeypatch.setattr(E, "_session_ident", lambda: {"ok": True, "session_no": "S1"})
    monkeypatch.setattr(E, "_session_part_no", lambda *_a: "1")
    node = {"type": "bmm:Goal", "attributes":
            {"bmm:sourceLocator": "kb:K/doc:D#section:业务目标"}}
    got = E._attrs_with_identity(node, "K", {})
    assert got["bmm:sourceLocator"] == "kb:K/doc:D#section:业务目标", got


def test_no_explicit_source_falls_back_to_session(monkeypatch):
    monkeypatch.setattr(E, "_session_ident", lambda: {"ok": True, "session_no": "S1"})
    monkeypatch.setattr(E, "_session_part_no", lambda *_a: "1")
    got = E._attrs_with_identity({"type": "bmm:Goal", "attributes": {}}, "K", {})
    assert got["bmm:sourceLocator"] == "session/S1/p1", got


def test_compact_receipt_keeps_data_and_errors_drops_hints():
    from srv.receipt import compact_receipt
    payload = {
        "ok": True,
        "hint": "试错提示",
        "next": "下一步",
        "why": "解释",
        "rows": [{"issue_no": "P1", "note": "业务字段保留", "rule_fp": "abc"}],
        "error": {"code": "need_key", "field": "review_no"},
    }
    got = compact_receipt(payload, mode="compact")
    assert "hint" not in got and "next" not in got and "why" not in got
    assert got["rows"][0]["note"] == "业务字段保留"
    assert got["rows"][0]["rule_fp"] == "abc"
    assert got["error"]["code"] == "need_key"
    assert got["receipt_mode"] == "compact"
    full = compact_receipt(payload, mode="full")
    assert full["hint"] == "试错提示"


def test_compact_result_keeps_totals():
    out = {"created": [], "merged": [{"i": i} for i in range(10)],
           "page_versions": [{"i": i} for i in range(10)], "receipt": {"merged": 10}}
    got = W._compact_result(out, limit=3)
    assert len(got["merged"]) == 3 and got["merged_total"] == 10
    assert len(got["page_versions"]) == 3 and got["page_versions_total"] == 10
    assert got["details_truncated"] is True
