"""T1 · 属性面板/写口：**正文载体属性不是数据属性**（2026-10-23 用户实测裁定）。

现象（UI）：报告页「数据属性」面板列出 `bmm:wikiContent` / `schema:articleBody`（正文！），
真正的结构化属性反而看不到。口径：
- **正文只有一份事实源**（图节点 `articleBody`；`wikiContent` 是兼容别名）⇒ 它**不是**可编辑数据属性；
- **面板不列**（`allowed` 去掉正文键；另给 `body_keys` 说明）；
- **写口拒收**（`page_attributes_set` 传正文键 ⇒ 拒绝 + 指路 `stage="report"`）；
- `save_knowledge` 的 `attributes` 里带正文键 ⇒ **静默剔除**（不因客户端习惯而失败）；
- 存量残留由 `migrate_article_body` 清（键清单来自 `ke_ontology.body_properties()`）。

跑法：`python3 -m pytest tests/test_page_attributes_body_filter.py`
"""
import json
import os
import urllib.parse
import urllib.request

BASE = os.environ.get("BODHI_MCP_URL", "http://127.0.0.1:8765/mcp").replace("/mcp", "")

BODY_KEYS = ("articlebody", "wikicontent")


def _attrs_of(kb, slug):
    q = urllib.parse.urlencode({"kb_id": kb, "slug": slug})
    with urllib.request.urlopen(BASE + "/bodhi/page/attributes?" + q, timeout=30) as r:
        return json.loads(r.read().decode("utf-8"))


def _local(name: str) -> str:
    return str(name).split("：")[-1].split(":")[-1].lower()


def _allowed_locals(d: dict) -> set:
    out = set()
    for a in d.get("allowed") or []:
        out.add(_local(a.rsplit("（", 1)[-1].rstrip("）")))
    return out


def _write_report(mcp, kb, session_no, slug, body, attributes=None):
    rep = {"title": "属性面板用例", "slug": slug, "content_md": body}
    if attributes is not None:
        rep["attributes"] = attributes
    return mcp.call("save_knowledge", {"kb_id": kb, "stage": "report", "mode": "apply",
                                       "session_no": session_no, "report": rep})


def test_panel_hides_body_properties(mcp, test_kb, session_no):
    """报告页面板：**不列正文**（articleBody/wikiContent），仍列 溯源/定义/备注。"""
    r = _write_report(mcp, test_kb, session_no, "review/_attr-report",
                      "# 属性面板用例\n\n## ① 范围与依据\n- 正文\n")
    assert r.get("applied") is True, json.dumps(r, ensure_ascii=False)[:400]
    d = _attrs_of(test_kb, "review/_attr-report")
    allowed = _allowed_locals(d)
    assert not (allowed & set(BODY_KEYS)), "面板仍列出正文载体属性 ✗：%s" % sorted(allowed)
    assert "sourcelocator" in allowed or "sourceLocation" in allowed or "sourcelocator" in allowed, \
        "面板应仍列溯源：%s" % sorted(allowed)
    assert "definition" in allowed, sorted(allowed)
    assert d.get("body_keys"), "没回正文键说明（body_keys）✗"
    attrs_locals = {_local(k) for k in (d.get("attributes") or {})}
    assert not (attrs_locals & set(BODY_KEYS)), "现值里仍有正文键 ✗：%s" % sorted(attrs_locals)


def test_write_rejects_body_property(mcp, test_kb, session_no):
    """写口拒收：`page_attributes_set(patch={"schema:articleBody": …})` ⇒ ok=false + 指路。"""
    _write_report(mcp, test_kb, session_no, "review/_attr-gate",
                  "# 属性面板用例\n\n## ① 范围与依据\n- 正文\n")
    bad = mcp.call("page_attributes_set", {"kb_id": test_kb, "slug": "review/_attr-gate",
                                          "patch": {"schema:articleBody": "想用属性写正文"},
                                          "dry_run": False})
    assert bad.get("ok") is False, "正文键未被拒 ✗：%s" % json.dumps(bad, ensure_ascii=False)[:300]
    assert any(_local(k) in BODY_KEYS for k in (bad.get("body_keys") or [])), bad
    assert "report" in str(bad.get("error") or ""), "拒收回执没指路 ✗：%s" % bad.get("error")


def test_save_knowledge_drops_body_attribute(mcp, test_kb, session_no):
    """写路径静默剔除：`attributes` 里带正文键 ⇒ 不报错、也不落属性。"""
    r = _write_report(mcp, test_kb, session_no, "review/_attr-drop",
                      "# 属性面板用例\n\n## ① 范围与依据\n- 正文\n",
                      attributes={"schema:articleBody": "正文不该进属性",
                                  "skos:note": "备注照常写"})
    assert r.get("applied") is True, json.dumps(r, ensure_ascii=False)[:400]
    d = _attrs_of(test_kb, "review/_attr-drop")
    attrs = d.get("attributes") or {}
    assert not [k for k in attrs if _local(k) in BODY_KEYS], "正文键落了属性 ✗：%s" % attrs
    assert any(_local(k) == "note" for k in attrs), "正常属性被误删 ✗：%s" % attrs


def test_template_page_exposes_template_properties(mcp, test_kb, session_no):
    """模板页面板：列 `适用报告类型` / `模板选用规则`（**不含正文**），现值只含结构化属性。"""
    r = mcp.call("save_knowledge", {
        "kb_id": test_kb, "stage": "report", "mode": "apply", "session_no": session_no,
        "report": {"title": "面板模板", "slug": "schema.org/documenttemplate/_attr-tpl",
                   "page_type": "schema:DocumentTemplate",
                   "content_md": "# 模板\n\n## 章节结构\n| 序号 | 章节 | 必填 | 要求 |\n"
                                 "|---|---|---|---|\n| 1 | ① 范围与依据 | 是 | - |\n",
                   "attributes": {"schema:applicableReportType": "bmm:TechnicalReviewReport"}}})
    assert r.get("applied") is True, json.dumps(r, ensure_ascii=False)[:400]
    d = _attrs_of(test_kb, "schema.org/documenttemplate/_attr-tpl")
    allowed = _allowed_locals(d)
    assert "applicablereporttype" in allowed, "面板没列 `适用报告类型` ✗：%s" % sorted(allowed)
    assert "templateselector" in allowed, "面板没列 `模板选用规则` ✗：%s" % sorted(allowed)
    assert not (allowed & set(BODY_KEYS)), "面板仍列正文 ✗：%s" % sorted(allowed)
    assert any(_local(k) == "applicablereporttype" for k in (d.get("attributes") or {})), d
