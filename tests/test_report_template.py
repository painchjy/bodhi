"""T2 · 报告模板（`schema:DocumentTemplate`）：模板页直写 + 报告按模板校验章节。

口径（2026-10-23 用户裁定）：
- `stage="report"` **直接写文档类页**（报告/模板同属 `schema:CreativeWork`）⇒ 模板页**不需要 retag**；
- 模板章节口径来自**模板页正文**（`## 章节结构` 表），不再写死在代码里；
- 报告回执必须可见：`template`（用了哪个）、`missing_required_sections`（缺哪些必填）、
  `template_fallback_reason`（没用上模板的**原因**）；
- `stage="graph"` **只写实体类**，文档类节点仍进 `skipped_document_nodes`（不放宽 ✗）。

跑法：`python3 -m pytest tests/test_report_template.py`
"""
import json

TPL_SLUG = "schema.org/documenttemplate/_tpl-report-template"

TPL_BODY = """# 模板

## 章节结构
| 序号 | 章节 | 必填 | 要求 |
|---|---|---|---|
| 1 | ① 范围与依据 | 是 | 说明范围 |
| 2 | ② 问题清单 | 是 | 逐项列问题 |
| 3 | ③ 结论 | 否 | 可省 |

## 使用约束
- 只用于回归测试
"""


def _write_tpl(mcp, kb, session_no, slug=TPL_SLUG, report_type="bmm:TechnicalReviewReport"):
    return mcp.call("save_knowledge", {
        "kb_id": kb, "stage": "report", "mode": "apply", "session_no": session_no,
        "report": {"title": "回归模板", "slug": slug, "page_type": "schema:DocumentTemplate",
                   "content_md": TPL_BODY,
                   "attributes": {"schema:applicableReportType": report_type}}})


def _write_report(mcp, kb, session_no, slug, body, template=""):
    rep = {"title": "回归报告", "slug": slug, "content_md": body}
    if template:
        rep["template"] = template
    return mcp.call("save_knowledge", {
        "kb_id": kb, "stage": "report", "mode": "apply", "session_no": session_no,
        "report": rep})


REPORT_OK = "# 报告\n\n## ① 范围与依据\n- 全节齐\n\n## ② 问题清单\n| 1 | x |\n\n## ③ 结论\n- 通过\n"


def test_template_page_written_by_report_stage(mcp, test_kb, session_no):
    """模板页**一次调用**写入（无需 retag）：page_type 正确、属性随写、可实例检索。"""
    r = _write_tpl(mcp, test_kb, session_no)
    assert r.get("applied") is True, json.dumps(r, ensure_ascii=False)[:400]
    assert r.get("page_type_adjusted") is None, "模板页类型被改写 ✗：%s" % r.get("page_type_adjusted")
    page = r.get("report_page") or {}
    assert page.get("page_type") == "schema:DocumentTemplate", page

    got = mcp.call("knowledge_lookup", {"kb_id": test_kb, "mode": "keyword", "query": "回归模板"})
    hits = {h.get("slug") for h in (got.get("hits") or [])}
    assert TPL_SLUG in hits, "模板页未落库 ✗：%s" % json.dumps(got, ensure_ascii=False)[:300]


def test_report_uses_template(mcp, test_kb, session_no):
    """按 `applicableReportType` **自动选**模板：回执给 template + 无缺章告警。"""
    _write_tpl(mcp, test_kb, session_no)
    r = _write_report(mcp, test_kb, session_no, "review/_tpl-used", REPORT_OK)
    assert r.get("applied") is True, json.dumps(r, ensure_ascii=False)[:400]
    tpl = r.get("template") or {}
    assert tpl.get("slug") == TPL_SLUG, "没按 applicableReportType 选中模板 ✗：%s" % tpl
    assert int(tpl.get("required") or 0) == 2, "必填章节数不对 ✗：%s" % tpl
    assert not r.get("missing_required_sections"), "章节齐却报缺 ✗：%s" % r.get("missing_required_sections")


def test_report_reports_missing_required_section(mcp, test_kb, session_no):
    """缺**必填**章节 ⇒ 回执 `missing_required_sections`（只告警、不拒写 ✓）。"""
    _write_tpl(mcp, test_kb, session_no)
    body = "# 报告\n\n## ① 范围与依据\n- 只有第一节\n"
    r = _write_report(mcp, test_kb, session_no, "review/_tpl-missing", body)
    assert r.get("applied") is True, json.dumps(r, ensure_ascii=False)[:400]
    miss = r.get("missing_required_sections") or []
    assert any("问题清单" in str(x) for x in miss), "缺必填章节未报告 ✗：%s" % miss
    assert not any("结论" in str(x) for x in miss), "非必填章节被误报 ✗：%s" % miss


def test_template_swap_changes_sections(mcp, test_kb, session_no):
    """指定 `report.template` ⇒ 用**指定模板**（换模板 ⇒ 章节口径随之变）。"""
    _write_tpl(mcp, test_kb, session_no)
    other = "schema.org/documenttemplate/_tpl-report-template-b"
    other_body = ("# 模板B\n\n## 章节结构\n| 序号 | 章节 | 必填 | 要求 |\n|---|---|---|---|\n"
                  "| 1 | ① 范围与依据 | 否 | - |\n")
    r0 = mcp.call("save_knowledge", {
        "kb_id": test_kb, "stage": "report", "mode": "apply", "session_no": session_no,
        "report": {"title": "回归模板B", "slug": other, "page_type": "schema:DocumentTemplate",
                   "content_md": other_body,
                   "attributes": {"schema:applicableReportType": "bmm:TechnicalReviewReport"}}})
    assert r0.get("applied") is True, json.dumps(r0, ensure_ascii=False)[:400]

    r = _write_report(mcp, test_kb, session_no, "review/_tpl-swap",
                      "# 报告\n\n## ① 范围与依据\n- 仅一节\n", template=other)
    tpl = r.get("template") or {}
    assert tpl.get("slug") == other, "未使用指定模板 ✗：%s" % tpl
    assert int(tpl.get("required", -1)) == 0, "章节表未按指定模板解析 ✗：%s" % tpl
    assert not r.get("missing_required_sections"), "指定模板无必填却报缺 ✗：%s" % r.get(
        "missing_required_sections")


def test_missing_template_falls_back_with_reason(mcp, test_kb, session_no):
    """没有模板 ⇒ 回落内置结构，并给 `template_fallback_reason`（可追溯 ✓）。"""
    r = _write_report(mcp, test_kb, session_no, "review/_tpl-none", REPORT_OK)
    assert r.get("applied") is True, json.dumps(r, ensure_ascii=False)[:400]
    assert (r.get("template") or {}).get("found") is False, r.get("template")
    assert r.get("template_fallback_reason"), "回落没给原因 ✗"


def test_audit_flags_missing_template_sections(mcp, test_kb, session_no):
    """T3：巡检 `scope="template"` 报 **T1（medium）** —— 文档类页缺模板必填章节。"""
    _write_tpl(mcp, test_kb, session_no)
    _write_report(mcp, test_kb, session_no, "review/_tpl-audit",
                  "# 报告\n\n## ① 范围与依据\n- 缺 ②③\n")
    r = mcp.call("audit_scan", {"kb_id": test_kb, "scope": "template"})
    assert r.get("ok") is not False, json.dumps(r, ensure_ascii=False)[:300]
    totals = r.get("totals") or {}
    assert int(totals.get("T1") or 0) >= 1, "巡检未报缺必填章节 ✗：%s" % json.dumps(totals)
    hit = next((f for f in (r.get("findings") or [])
                if f.get("check") == "T1" and f.get("subject") == "review/_tpl-audit"), None)
    assert hit, "T1 未指向该报告页 ✗：%s" % json.dumps(r.get("findings"), ensure_ascii=False)[:400]
    assert hit.get("severity") == "medium", hit
    assert "问题清单" in (hit.get("detail") or ""), hit
    data = (r.get("data") or {}).get("template_sections") or {}
    assert int(data.get("templates") or 0) == 1, data
    assert int(data.get("docs_with_missing") or 0) >= 1, data


def test_audit_flags_unusable_template(mcp, test_kb, session_no):
    """T3：模板页没声明 `applicableReportType` ⇒ **T2（low）**（模板不可被自动选中）。"""
    r0 = mcp.call("save_knowledge", {
        "kb_id": test_kb, "stage": "report", "mode": "apply", "session_no": session_no,
        "report": {"title": "无属性模板", "slug": "schema.org/documenttemplate/_tpl-bare",
                   "page_type": "schema:DocumentTemplate",
                   "content_md": "# 模板\n\n## 章节结构\n| 序号 | 章节 | 必填 | 要求 |\n"
                                 "|---|---|---|---|\n| 1 | ① 范围与依据 | 是 | - |\n"}})
    assert r0.get("applied") is True, json.dumps(r0, ensure_ascii=False)[:300]
    r = mcp.call("audit_scan", {"kb_id": test_kb, "scope": "template"})
    hit = next((f for f in (r.get("findings") or [])
                if f.get("check") == "T2" and f.get("subject") == "schema.org/documenttemplate/_tpl-bare"),
               None)
    assert hit, "未报模板缺 applicableReportType ✗：%s" % json.dumps(
        r.get("findings"), ensure_ascii=False)[:400]
    assert hit.get("severity") == "low", hit


def test_audit_template_from_domain_kb_and_ambiguity(mcp, test_kb, session_no):
    """T3 口径（2026-10-23 用户裁定）：**模板以领域库为准** + **多候选不静默**。

    ① 不给 `template_kb_id` ⇒ 只看本库：没有模板 ⇒ T2（low）提示指定领域库，**不跨库乱扫** ✗；
    ② 给 `template_kb_id`（领域库）⇒ 用它判 T1；
    ③ 该库里有多份模板同适用 ⇒ **只报歧义、不选、不判章节**。
    """
    import time

    from kb_factory import TestKB
    tpl_kb = TestKB("tpllib")
    tpl_kb.create()
    try:
        t0 = time.time()
        while True:
            probe = mcp.call("knowledge_lookup", {"kb_id": tpl_kb.kb_id, "mode": "keyword",
                                                  "query": "_t"})
            if not (probe.get("kb_unresolved") or probe.get("need_kb_selection")
                    or "知识库不存在" in str(probe.get("error") or "")):
                break
            assert time.time() - t0 < 90, "服务端 90s 内仍看不到模板库"
            time.sleep(5)
        r0 = _write_tpl(mcp, tpl_kb.kb_id, session_no)
        assert r0.get("applied") is True, json.dumps(r0, ensure_ascii=False)[:300]
        _write_report(mcp, test_kb, session_no, "review/_tpl-xkb",
                      "# 报告\n\n## ① 范围与依据\n- 缺 ②③\n")

        # ① 本库无模板 ⇒ 只提示（T2 low），不跨库乱扫
        r = mcp.call("audit_scan", {"kb_id": test_kb, "scope": "template"})
        data = (r.get("data") or {}).get("template_sections") or {}
        assert int(data.get("templates") or 0) == 0, data
        assert not (r.get("totals") or {}).get("T1"), "本库无模板却判了章节 ✗"
        assert int((r.get("totals") or {}).get("T2") or 0) >= 1, json.dumps(r.get("totals"))

        # ② 指定模板库（=领域库）⇒ 判 T1
        r2 = mcp.call("audit_scan", {"kb_id": test_kb, "scope": "template",
                                     "template_kb_id": tpl_kb.kb_id})
        data2 = (r2.get("data") or {}).get("template_sections") or {}
        assert data2.get("template_source") == tpl_kb.kb_id, data2
        assert int(data2.get("templates") or 0) == 1, data2
        assert int((r2.get("totals") or {}).get("T1") or 0) >= 1, json.dumps(r2.get("totals"))

        # ③ 同库再来一份同适用模板 ⇒ 多候选：只报歧义、不选、不判章节
        r3 = mcp.call("save_knowledge", {
            "kb_id": tpl_kb.kb_id, "stage": "report", "mode": "apply", "session_no": session_no,
            "report": {"title": "回归模板B", "slug": "schema.org/documenttemplate/_tpl-report-template-b",
                       "page_type": "schema:DocumentTemplate",
                       "content_md": "# 模板B\n\n## 章节结构\n| 序号 | 章节 | 必填 | 要求 |\n"
                                     "|---|---|---|---|\n| 1 | ① 范围与依据 | 否 | - |\n",
                       "attributes": {"schema:applicableReportType": "bmm:TechnicalReviewReport"}}})
        assert r3.get("applied") is True, json.dumps(r3, ensure_ascii=False)[:300]
        r4 = mcp.call("audit_scan", {"kb_id": test_kb, "scope": "template",
                                     "template_kb_id": tpl_kb.kb_id})
        data4 = (r4.get("data") or {}).get("template_sections") or {}
        assert int(data4.get("ambiguous_pages") or 0) >= 1, data4
        assert not (r4.get("totals") or {}).get("T1"), "多候选时不该硬判缺章 ✗"
        hit = next((f for f in (r4.get("findings") or []) if f.get("check") == "T2"
                    and "多候选" in (f.get("detail") or "")), None)
        assert hit, "多候选未报 ✗：%s" % json.dumps(r4.get("findings"), ensure_ascii=False)[:400]
    finally:
        tpl_kb.drop()


def test_graph_stage_skips_document_nodes(mcp, test_kb, session_no):
    """`stage="graph"` 只写实体类：文档类节点 ⇒ `skipped_document_nodes`（不放宽 ✓）。"""
    r = mcp.call("save_knowledge", {
        "kb_id": test_kb, "stage": "graph", "mode": "apply", "session_no": session_no,
        "nodes": [{"name": "回归模板X", "type": "schema:DocumentTemplate"}]})
    skipped = r.get("skipped_document_nodes") or []
    assert skipped, "graph 阶段未跳过文档类节点 ✗（口径：只写实体类）"
    assert any(s.get("type") == "schema:DocumentTemplate" for s in skipped), skipped
