"""T1 矩阵 · 属性面板筛选（**期望值由本体数据属性动态推导，不写死**）。

口径（2026-10-23 用户裁定）：
- 面板可编辑项 = 该 `page_type` 在本体里声明的**数据属性**（含继承），**减去正文载体**；
- 每一类页都必须列 `skos:definition` / `skos:note`（无 domain ⇒ 全类可用）；
- 任何页都**不得**列 `bmm:wikiContent` / `schema:articleBody`（正文只有一份事实源 = 图 `articleBody`）。

覆盖四类各 2 个类实例页：**报告类**（CreativeWork 谱系，非模板）/ **模板类** /
**有继承**（有父类）/ **无继承**（无父类）—— 类与页都从本体与真实缺口里**动态挑**。

跑法：`python3 -m pytest tests/test_page_attributes_matrix.py`
"""
import json
import os
import urllib.parse
import urllib.request

from ke_ontology import (ancestors, class_meta, data_properties, data_properties_for,
                         is_body_property)

BASE = os.environ.get("BODHI_MCP_URL", "http://127.0.0.1:8765/mcp").replace("/mcp", "")
SKOS_ANNOTATIONS = ("definition", "note")          # 本体里无 domain 的注释属性（全类可用）
REPORT_ROOT = "schema:CreativeWork"
TEMPLATE_CLASS = "schema:DocumentTemplate"


def _local(name: str) -> str:
    return str(name).split("：")[-1].split(":")[-1].lower()


def _attrs_of(kb, slug):
    q = urllib.parse.urlencode({"kb_id": kb, "slug": slug})
    with urllib.request.urlopen(BASE + "/bodhi/page/attributes?" + q, timeout=30) as r:
        return json.loads(r.read().decode("utf-8"))


def _allowed_locals(d: dict) -> set:
    return {_local(a.rsplit("（", 1)[-1].rstrip("）")) for a in (d.get("allowed") or [])}


def _want_locals(page_type: str) -> set:
    """该类的**期望可编辑项**（本体数据属性 − 正文载体），含继承。"""
    return {_local(k) for k in (data_properties_for(page_type) or {})
            if not is_body_property(k)}


def _own_locals(page_type: str) -> set:
    """只声明在**该类自身**（domain = 该类）的数据属性（用来验证"继承也带过来"）。"""
    out = set()
    for name, meta in (data_properties().get("properties") or {}).items():
        if not is_body_property(name) and page_type in (meta.get("domains") or []):
            out.add(_local(name))
    return out


def _pick_classes() -> dict:
    """从本体挑四类候选（确定性排序 ⇒ 可复现）。"""
    cls = class_meta()
    reports, inherit, plain = [], [], []
    for name in sorted(cls):
        if name.startswith("ontology:") or name in ("owl:Thing", "rdfs:Resource"):
            continue
        chain = set(ancestors(name))
        if REPORT_ROOT in chain and name not in (REPORT_ROOT, TEMPLATE_CLASS):
            reports.append(name)
        if name == TEMPLATE_CLASS:
            continue
        (inherit if cls[name].get("parents") else plain).append(name)
    inherit = [c for c in inherit if c not in reports]          # 四类互不重叠
    plain = [c for c in plain if c not in reports]
    assert len(reports) >= 2, "本体里报告类不足 2 个：%s" % reports
    assert len(inherit) >= 2, "本体里有父类的类不足 2 个"
    assert len(plain) >= 2, "本体里无父类的类不足 2 个"
    return {"report": reports[:2], "inherit": inherit[:2], "plain": plain[:2]}


def _mk_doc(mcp, kb, session_no, page_type, title, body, attributes=None):
    rep = {"title": title, "page_type": page_type, "content_md": body}
    if attributes:
        rep["attributes"] = attributes
    r = mcp.call("save_knowledge", {"kb_id": kb, "stage": "report", "mode": "apply",
                                    "session_no": session_no, "report": rep})
    assert r.get("applied") is True, "文档页写入失败（%s）：%s" % (
        page_type, json.dumps(r, ensure_ascii=False)[:300])
    return (r.get("report_page") or {}).get("slug") or (r.get("created") or [{}])[0].get("slug")


def _mk_entity(mcp, kb, session_no, page_type, name):
    r = mcp.call("save_knowledge", {"kb_id": kb, "stage": "graph", "mode": "apply",
                                    "session_no": session_no,
                                    "nodes": [{"name": name, "type": page_type}]})
    assert r.get("applied") is True, "实体页写入失败（%s）：%s" % (
        page_type, json.dumps(r, ensure_ascii=False)[:300])
    slug = (r.get("created") or [{}])[0].get("slug")
    assert slug, "实体页未返回 slug（%s）：%s" % (page_type, json.dumps(r, ensure_ascii=False)[:300])
    return slug


def _check_page(kb, slug, page_type):
    """核心断言：面板 = 本体声明（含继承）− 正文；并必含 skos 两个注释属性。"""
    d = _attrs_of(kb, slug)
    assert d.get("ok") is not False, "%s：面板读取失败 %s" % (slug, d.get("error"))
    got = _allowed_locals(d)
    want = _want_locals(page_type)

    for a in SKOS_ANNOTATIONS:                       # ① skos 两个注释属性必须在
        assert a in got, "%s（%s）缺 `skos:%s` ✗：%s" % (slug, page_type, a, sorted(got))
    for a in ("articlebody", "wikicontent"):         # ② 正文载体一律不得出现
        assert a not in got, "%s（%s）仍列正文 `%s` ✗：%s" % (slug, page_type, a, sorted(got))
    missing = sorted(want - got)                     # ③ 本体声明（含继承）都要出现
    assert not missing, "%s（%s）缺本体声明属性 ✗：%s（实际：%s）" % (
        slug, page_type, missing, sorted(got))
    extra = sorted(got - want)                       # ④ 不多列
    assert not extra, "%s（%s）多列了本体未声明项 ✗：%s" % (slug, page_type, extra)
    assert d.get("body_keys") is not None, "回执缺 `body_keys` ✗"
    return got, want


def test_panel_matches_ontology_report_classes(mcp, test_kb, session_no):
    """报告类 ×2：面板 = 本体数据属性（含继承）− 正文；skos 两个注释属性在列。"""
    for i, cls in enumerate(_pick_classes()["report"]):
        slug = _mk_doc(mcp, test_kb, session_no, cls, "面板矩阵报告%d" % (i + 1),
                       "# 面板矩阵报告%d\n\n## ① 范围与依据\n- 正文\n" % (i + 1))
        _check_page(test_kb, slug, cls)


def test_panel_matches_ontology_template_class(mcp, test_kb, session_no):
    """模板类 ×2 实例页：列 `适用报告类型`/`模板选用规则`，不列正文。"""
    for i in range(2):
        slug = _mk_doc(mcp, test_kb, session_no, TEMPLATE_CLASS, "面板矩阵模板%d" % (i + 1),
                       "# 模板%d\n\n## 章节结构\n| 序号 | 章节 | 必填 | 要求 |\n"
                       "|---|---|---|---|\n| 1 | ① 范围与依据 | 是 | - |\n" % (i + 1),
                       attributes={"schema:applicableReportType": "bmm:TechnicalReviewReport"})
        got, want = _check_page(test_kb, slug, TEMPLATE_CLASS)
        assert {"applicablereporttype", "templateselector"} <= got, sorted(got)
        assert "applicablereporttype" in want, "本体没把模板属性声明给模板类 ✗：%s" % sorted(want)


def test_panel_includes_inherited_properties(mcp, test_kb, session_no):
    """有继承 ×2：**父类声明的数据属性也要出现在面板**（继承语义）。"""
    checked = 0
    for i, cls in enumerate(_pick_classes()["inherit"]):
        slug = _mk_entity(mcp, test_kb, session_no, cls, "面板矩阵继承%d" % (i + 1))
        got, want = _check_page(test_kb, slug, cls)
        inherited = want - _own_locals(cls)
        if inherited:                       # 该类的父类确实贡献了属性 ⇒ 必须体现
            assert inherited <= got, "%s（%s）父类属性未带进面板 ✗：%s" % (
                slug, cls, sorted(inherited - got))
            checked += 1
    assert checked >= 1, "挑到的两个有继承类，父类都没贡献数据属性 ⇒ 覆盖不足（换类或补本体）"


def test_panel_no_extra_for_root_classes(mcp, test_kb, session_no):
    """无继承 ×2：只有全局属性（skos 两条等），不凭空多出别的类属性。"""
    for i, cls in enumerate(_pick_classes()["plain"]):
        slug = _mk_entity(mcp, test_kb, session_no, cls, "面板矩阵独立%d" % (i + 1))
        got, want = _check_page(test_kb, slug, cls)
        assert got == want, "%s（%s）面板与本体不一致：多 %s / 缺 %s" % (
            slug, cls, sorted(got - want), sorted(want - got))


