"""验收基线 · **真实报告 ↔ 领域库模板** 对齐（T3 的"归零"断言）。

口径（2026-10-23 用户裁定）：模板以**领域库**为准，且模板章节表必须与**真实报告的标准结构**一致；
本用例把"巡检 T1 = 0"固化成基线 —— 任何一方被改坏（模板章节名、报告结构、匹配逻辑）都会红 ✓。
数据不存在（换环境/库被删）⇒ skip（不是 fail）。

跑法：`python3 -m pytest tests/test_template_alignment_real.py`
"""
import json


def test_real_reports_match_domain_template(mcp, kbs):
    r = mcp.call("audit_scan", {"kb_id": kbs["work"], "scope": "template",
                                "template_kb_id": kbs["domain"]})
    data = (r.get("data") or {}).get("template_sections") or {}
    assert int(data.get("templates") or 0) >= 1, "领域库没有模板 ✗：%s" % json.dumps(
        data, ensure_ascii=False)
    assert int(data.get("checked_docs") or 0) >= 1, "没有可判定的报告页（数据缺口）⇒ 基线无意义：%s" % \
        json.dumps(data, ensure_ascii=False)
    totals = r.get("totals") or {}
    assert not int(totals.get("T1") or 0), (
        "真实报告与领域库模板不一致（T1=%s）✗ —— 先看 findings：模板章节名要与报告标准结构对齐，"
        "不要为了过闸改报告内容\n%s" % (totals.get("T1"),
                                        json.dumps(r.get("findings"), ensure_ascii=False)[:800]))
    assert int(data.get("ambiguous_pages") or 0) == 0, "领域库同报告类出现多份模板（歧义）✗：%s" % \
        json.dumps(data, ensure_ascii=False)
