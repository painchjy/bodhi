"""外部接口（模拟）三工具的回归：**清单查询 / 条目维护 / 文档与附件访问**

口径（2026-10-25）：
- 绑定来自业务对象页的 `bmm:sourceLocator` —— 本测试**注入**绑定，不依赖库/图（封闭、可重复）；
- 容器根用 `tmp_path`（`BODHI_EXT_ROOT`），种子来自 `deploy/mock-ext/`（`bootstrap` 复制）；
- 写操作**默认 dry_run**、幂等、`if_rev` 冲突、审计只在落盘时写；
- 取图必须能变成 `data_url`（多模态可用），且路径不能穿越、超限要结构化报错。
"""
import json
import os
import sys

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "tools", "ke-core"))

import ke_audit as A  # noqa: E402
import ke_ext_catalog as C  # noqa: E402
import ke_ext_docs as D  # noqa: E402
import ke_ext_issues as I  # noqa: E402
import ke_ext_store as S  # noqa: E402

BINDING = {"scheme": "mcp", "tool": "ext_catalog_query", "modes": ["query", "maintain"],
           "keys": ["software", "version"], "impl": "tools/ke-core/ke_ext_catalog.py"}


@pytest.fixture
def ext(tmp_path, monkeypatch):
    monkeypatch.setenv("BODHI_EXT_ROOT", str(tmp_path))
    S.bootstrap()
    monkeypatch.setattr(S, "resolve_binding",
                        lambda tool, kb_id="", object_slug="":
                        {"ok": True, "binding": dict(BINDING),
                         "object": {"slug": "bpmn/dataobject/x"}})
    return tmp_path


def _sys(ext, name):
    return ext / "it-platform" / name


def _read(ext, name):
    with open(_sys(ext, name), encoding="utf-8") as fh:
        return json.load(fh)


# ── 清单查询 ────────────────────────────────────────────────────────────
def test_catalog_check_allowed(ext):
    r = C.query(kb_id="kb", name="MySQL", version="8.0.32")
    assert r["ok"] and r["verdict"] == "allowed", r
    assert r["match"]["vendor"] == "Oracle"
    assert r["schema"]["status_enum"], "回执必须带取值域（调用方要按它校验）✗"


def test_catalog_check_denied_wins(ext):
    r = C.query(kb_id="kb", name="log4j-core", version="2.14.1")
    assert r["verdict"] == "denied" and r.get("reason"), r


def test_catalog_version_is_verbatim(ext):
    """8.0.31 ≠ 8.0.32 ⇒ `unknown`，但要给 near_misses（规则要求逐字 ✓）。"""
    r = C.query(kb_id="kb", name="MySQL", version="8.0.31")
    assert r["verdict"] == "unknown", r
    assert any(x.get("version") == "8.0.32" for x in r["near_misses"]), r


def test_catalog_ls_filters(ext):
    r = C.query(kb_id="kb", action="ls", vendor="Apache")
    names = {x["software"] for x in r["rows"]}
    assert names == {"Apache Tomcat", "Kafka"}, r
    assert "Apache Tomcat" in r["markdown"]
    one = C.query(kb_id="kb", action="ls", name="Tomcat")
    assert one["counts"]["rows"] == 1 and one["rows"][0]["software"] == "Apache Tomcat", one


def test_catalog_maintain_dry_run_does_not_write(ext):
    before = _read(ext, "allowlist.json")["rev"]
    r = C.query(kb_id="kb", mode="maintain", action="upsert", name="Etcd", version="3.5.10",
                vendor="CNCF", license="Apache-2.0", status="已认证",
                actor="tester", dry_run=True)
    assert r["ok"] and r["dry_run"] is True and r["diff"]["after"]["software"] == "Etcd", r
    assert _read(ext, "allowlist.json")["rev"] == before, "dry_run 不得改文件 ✗"
    assert not _sys(ext, "audit.jsonl").exists(), "dry_run 不得写审计 ✗"


def test_catalog_maintain_writes_and_audits(ext):
    r = C.query(kb_id="kb", mode="maintain", action="upsert", name="Etcd", version="3.5.10",
                vendor="CNCF", license="Apache-2.0", status="已认证",
                actor="tester", dry_run=False)
    assert r["ok"] and r["rev"] >= 4, r
    assert C.query(kb_id="kb", name="Etcd", version="3.5.10")["verdict"] == "allowed"
    audit = _sys(ext, "audit.jsonl").read_text(encoding="utf-8").strip().splitlines()
    assert len(audit) == 1 and "catalog:upsert" in audit[0], audit


def test_catalog_maintain_requires_actor_and_status(ext):
    assert C.query(kb_id="kb", mode="maintain", action="upsert", name="X", version="1",
                   dry_run=True)["error"].startswith("maintain 需要 actor")
    bad = C.query(kb_id="kb", mode="maintain", action="upsert", name="X", version="1",
                  status="不存在的状态", actor="t", dry_run=True)
    assert bad["error"] == "bad_status" and bad["allowed"], bad


# ── 条目维护 ────────────────────────────────────────────────────────────
def test_issues_list_seeded(ext):
    r = I.maintain(kb_id="kb", review_no="YYYYMM-889888", view="table")
    assert r["ok"] and r["counts"]["items"] == 2, r
    assert r["rev"] == 2 and r["summary"].get("符合") == 1, r
    assert "专家反馈" in r["markdown"], "表格必须带专家反馈列（新增栏位 ✓）"


def test_issues_create_is_idempotent(ext):
    args = dict(kb_id="kb", review_no="R1", mode="maintain", action="create",
                issue_no="P-01", actor="t", dry_run=False,
                fields={"desc": "首条", "verdict": "不符合", "severity": "一般"})
    r1 = I.maintain(**args)
    r2 = I.maintain(**args)
    assert r1["ok"] and r2["deduped"] is True, (r1.get("deduped"), r2.get("deduped"))
    assert r2["counts"]["items"] == 1, "同键 create 不得新增第二条 ✗ %r" % (r2,)
    assert r2["rev"] == 2, r2


def test_issues_if_rev_conflict(ext):
    I.maintain(kb_id="kb", review_no="R2", mode="maintain", action="create", issue_no="P-01",
               actor="t", dry_run=False, fields={"desc": "x"})
    bad = I.maintain(kb_id="kb", review_no="R2", mode="maintain", action="update",
                     issue_no="P-01", actor="t", dry_run=False, if_rev=99,
                     fields={"desc": "y"})
    assert bad["error"] == "conflict" and bad["rev"] == 1, bad


def test_issues_expert_feedback_roundtrip(ext):
    I.maintain(kb_id="kb", review_no="R3", mode="maintain", action="create", issue_no="P-07",
               actor="t", dry_run=False, fields={"desc": "待反馈"})
    I.maintain(kb_id="kb", review_no="R3", mode="maintain", action="update", issue_no="P-07",
               actor="expert", dry_run=False, if_rev=1,
               fields={"expert_feedback": "已确认，需补充同城节点"})
    r = I.maintain(kb_id="kb", review_no="R3", action="get", issue_no="P-07")
    assert r["row"]["expert_feedback"].startswith("已确认"), r


def test_issues_bad_enum_rejected(ext):
    bad = I.maintain(kb_id="kb", review_no="R4", mode="maintain", action="create",
                     issue_no="P-01", actor="t", dry_run=True,
                     fields={"desc": "x", "verdict": "随便写"})
    assert bad["error"] == "bad_verdict" and bad["allowed"], bad


def test_issues_columns_subset_and_order(ext):
    """报告只取一部分列 ⇒ `columns` 子集生效，且**按模板给的顺序**（工具字段 11 列 > 报告所需）✓。"""
    r = I.maintain(kb_id="kb", review_no="YYYYMM-889888", view="table",
                   columns="verdict,issue_no,expert_feedback")
    assert r["ok"], r
    cols = ["verdict", "issue_no", "expert_feedback"]
    assert r["schema"]["columns"] == cols, r["schema"]
    assert len(r["schema"]["order"]) == 11, "order 仍是工具全量契约（报告可配置任意子集）"
    lab = r["schema"]["labels"]
    assert r["markdown"].splitlines()[0] == "| " + " | ".join(lab[c] for c in cols) + " |", \
        r["markdown"].splitlines()[0]
    body = r["markdown"].splitlines()[2]
    assert body.count("|") == len(cols) + 1, "行内单元格数必须等于所选列数：%r" % body


def test_issues_columns_unknown_rejected(ext):
    """模板写了契约外的字段 ⇒ 可读回执（bad_columns + allowed），不得静默丢掉 ✓。"""
    bad = I.maintain(kb_id="kb", review_no="YYYYMM-889888", view="table",
                     columns="issue_no,plan_fix_date")
    assert bad["ok"] is False and bad["error"] == "bad_columns", bad
    assert "plan_fix_date" in bad["detail"], bad
    assert "issue_no" in bad["allowed"] and "expert_feedback" in bad["allowed"], bad


# ── G4（`audit_scan(scope="ext")`）：报告快照落后台账 ─────────────────────
def _ref_block(rev: str, rno: str = "R9") -> str:
    return ("```bodhi-ext\nobject: bpmn/dataobject/x\nreview_no: %s\n"
            "basis: it_platform / ext_issue_list\nrev: %s\nschema_hash: sha256:aa\n```"
            % (rno, rev))


def _report_page(content: str) -> dict:
    return {"slug": "bmm/report/sample", "page_type": "bmm:Report", "content": content}


def test_template_section_fields(ext):
    """模板「章节结构」的**第 5 列**= 该节用哪几列（顺序即展示顺序）；没这列 ⇒ 空=用全量 ✓。"""
    import ke_report as R
    body = ("## 章节结构\n"
            "| 序号 | 章节 | 必填 | 要求 | 数据对象字段（逗号分隔，顺序即展示顺序） |\n"
            "|---|---|---|---|---|\n"
            "| 1 | ① 范围与依据 | 是 | 说明范围 | — |\n"
            "| 2 | ② 条目清单 | 是 | 逐条：编号/判定/反馈 | issue_no,verdict,expert_feedback |\n"
            "\n## 使用约束\n- x\n")
    secs = R.parse_template_sections(body)
    assert [s["title"] for s in secs][1].endswith("条目清单"), secs
    assert secs[0]["fields"] == [], secs[0]
    assert secs[1]["fields"] == ["issue_no", "verdict", "expert_feedback"], secs[1]
    assert R.section_fields(secs, "② 条目清单") == ["issue_no", "verdict", "expert_feedback"]
    assert R.section_fields(secs, "不存在的节") == []
    # 老模板（没有第 5 列）⇒ fields 全空，行为不变 ✓
    old = "## 章节结构\n| 序号 | 章节 | 必填 | 要求 |\n|---|---|---|---|\n| 1 | ① 结论 | 是 | x |\n"
    assert R.parse_template_sections(old)[0]["fields"] == []


def test_ext_blocks_parse_only_fenced_bodhi_ext(ext):
    """只抽 ```` ```bodhi-ext ```` 块（正文别处的 `rev:` 不算）；basis 拆出工具名 ✓。"""
    kv = A.ext_blocks("前言\nrev: 99\n\n" + _ref_block("2") + "\n\n后记")[0]
    assert kv["review_no"] == "R9" and kv["rev"] == "2", kv
    assert A.ext_block_tool(kv) == "ext_issue_list", kv
    assert A.ext_blocks("没有块") == []


def test_gate_report_items_first_table_only(ext):
    """G2 只数**第一张**带编号列的表（表后补充块里的「编号」表头不得把行数算大 ✗）。"""
    import ke_ext_gate as G
    md = ("| 编号 | 判定 |\n|---|---|\n| P-01 | 符合 |\n| P-02 | 不符合 |\n\n"
          "## 表后补充\n| 编号 | 需反馈内容 |\n|---|---|\n| P-03 | x |\n")
    items = G._report_items(md)                       # noqa: SLF001  单测内部函数
    assert [i["issue_no"] for i in items] == ["P-01", "P-02"], items


# ── G5（`audit_scan(scope="ext")`）：模板·契约·正文三方一致 + 职责边界 ──────
def _tpl_items(fields: str) -> list:
    """构造一份模板候选（`pick_template` 的入参形状）。"""
    content = ("## 章节结构\n| 序号 | 章节 | 必填 | 要求 | 数据对象字段 |\n|---|---|---|---|---|\n"
               "| 1 | ① 条目清单 | 是 | 逐条 | %s |\n" % fields)
    return [{"slug": "tpl/x", "title": "模板X", "version": 1, "content": content,
             "types": ["bmm:Report"], "selector": ""}]


def _g5_ctx(md: str, meta: str = "{}", page_type: str = "bmm:Report") -> dict:
    return {"kb_id": "kb", "data": {},
            "pages": [{"slug": "bmm/report/sample", "page_type": page_type,
                       "content": md, "meta": meta}]}


def _report_md(header: str, columns: str = "", rows: str = "| P-01 | 符合 |") -> str:
    sep = "|" + "---|" * len(header.split("|"))
    return ("## ① 条目清单\n|%s\n%s\n%s\n\n"
            "```bodhi-ext\nobject: bpmn/dataobject/x\nreview_no: R9\n"
            "basis: it_platform / ext_issue_list\n%sschema_hash: sha256:aa\n```\n"
            % (header, sep, rows, ("columns: %s\n" % columns) if columns else ""))


# ── 开源软件清单：允许版本范围 / 有效期（2026-10-26） ─────────────────────
def test_catalog_version_range_hit(ext):
    """白名单条目可声明 `versionRange`（逐字条目优先；范围内任一补丁版本 ⇒ allowed/range ✓）。"""
    r = C.query(kb_id="kb", name="Kafka", version="3.5.1")
    assert r["verdict"] == "allowed" and r["match_kind"] == "range", r
    assert r["range"]["min"] == "3.0.0", r
    out = C.query(kb_id="kb", name="Kafka", version="2.8.0")
    assert out["verdict"] == "unknown", out


def test_catalog_expired_entry_rejected(ext):
    """有效期已过 ⇒ `verdict=expired` + `expired=True`（视为未认证，不静默当 allowed ✗）。"""
    r = C.query(kb_id="kb", name="Nginx", version="1.24.0")
    assert r["verdict"] == "expired" and r["expired"] is True, r
    ok = C.query(kb_id="kb", name="MySQL", version="8.0.32")
    assert ok["verdict"] == "allowed" and ok["expired"] is False, ok


def test_doc_index_and_url(ext):
    """文档清单带**可下载 url**；`meta/url` 都回该地址（外部系统给 URL、不要上传 ✓）。"""
    u = D.access(kb_id="kb", action="url", doc_id="doc-sample-1")
    assert u["ok"] and u["url"].endswith("/files/doc-sample-1"), u
    m = D.access(kb_id="kb", action="meta", doc_id="doc-sample-1")
    assert m["url"].endswith("/files/doc-sample-1"), m


# ── 参考规范：抓取留痕 + 巡检 G7（2026-10-26） ───────────────────────────
def test_ref_cache_roundtrip(ext):
    """`reference_lookup` 抓完要**留痕**（同 URL 覆盖）；巡检据此判"抓过没"。"""
    r = S.ref_cache_put("http://techdoc.example/a", {"found": True, "reason": "", "chars": 12})
    assert r["ok"], r
    assert S.ref_cache_load()["http://techdoc.example/a"]["found"] is True
    S.ref_cache_put("http://techdoc.example/a", {"found": False, "reason": "dns"})
    assert S.ref_cache_load()["http://techdoc.example/a"]["reason"] == "dns", "同 URL 必须覆盖 ✓"


def _rule_page(url: str) -> dict:
    return {"slug": "bmm/rule/r1", "page_type": "bmm:OperativeBusinessRule", "content": "",
            "out_links": "[]",
            "meta": json.dumps({"ontology": {"attributes": {"bmm:ruleReference": url}}}),
            "in_links": "[]", "source_refs": "[]", "chunk_refs": "[]", "title": "r1"}


def test_gate_reference_missing_cache(ext):
    """G7（low）：规则写了 URL 但没留痕 ⇒ 提示抓一次；留痕后在册 ✓。"""
    ctx = {"kb_id": "kb", "pages": [_rule_page("http://techdoc.example/rule-a")], "data": {}}
    rep = A.Report("kb", "ext", 50)
    A.check_ext_reference(ctx, rep)
    assert rep.totals.get("G7") == 1, rep.totals
    assert ctx["data"]["ext_reference"]["missing"] == 1
    assert "allow_fetch=true" in rep.findings[0]["fix_hint"]

    S.ref_cache_put("http://techdoc.example/rule-a", {"found": True})
    ctx2 = {"kb_id": "kb", "pages": [_rule_page("http://techdoc.example/rule-a")], "data": {}}
    rep2 = A.Report("kb", "ext", 50)
    A.check_ext_reference(ctx2, rep2)
    assert rep2.totals.get("G7") is None, rep2.totals
    assert ctx2["data"]["ext_reference"]["cached"] == 1


# ── 批量写入 / rule_fp 透传 / 文档上传（2026-10-26） ─────────────────────
def test_issue_bulk_create_single_rev(ext):
    """一次 `items=[…]` 建 3 条 ⇒ **rev 只 +1**（新建档案首写 = 1），不再是"一条一个版本" ✓。"""
    items = [{"issue_no": "P-%02d" % i, "desc": "d%d" % i, "rule": "规则A",
              "verdict": "不符合", "rule_fp": "16c4fde43637ed6c"} for i in (1, 2, 3)]
    dry = I.maintain(kb_id="kb", mode="maintain", action="create", review_no="R-BULK",
                     items=items, actor="t", dry_run=True)
    assert dry["dry_run"] is True and dry["planned"] == 3 and dry["rev"] == 0, dry
    res = I.maintain(kb_id="kb", mode="maintain", action="create", review_no="R-BULK",
                     items=items, actor="t", dry_run=False)
    assert res["created"] == 3 and res["rev"] == 1, res
    assert res["rev_note"].startswith("rev ="), res
    again = I.maintain(kb_id="kb", mode="maintain", action="create", review_no="R-BULK",
                       items=items[:1], actor="t", dry_run=False)
    assert again["updated"] == 1 and again["rev"] == 2, again


def test_issue_rule_fp_from_caller_kept(ext):
    """调用方给的 `rule_fp`（= 规则页 ruleHash）**原样保留**，不再自算 `r-xxxxxx` ✓。"""
    r = I.maintain(kb_id="kb", mode="maintain", action="create", review_no="R-FP",
                   issue_no="P-01", actor="t", dry_run=False,
                   fields={"rule": "规则A", "doc_loc": "2.1", "verdict": "不符合",
                           "quote": "原文", "rule_fp": "22e44ac2f97e874c"})
    assert r["diff"]["after"]["rule_fp"] == "22e44ac2f97e874c", r
    gen = I.maintain(kb_id="kb", mode="maintain", action="create", review_no="R-FP",
                     issue_no="P-02", actor="t", dry_run=False,
                     fields={"rule": "规则A", "doc_loc": "2.2", "verdict": "不符合",
                             "quote": "原文2"})
    assert str(gen["diff"]["after"]["rule_fp"]).startswith("r-"), "缺省时才自算短哈希"


def _mk_docx(path, paras):
    """最小 docx（只含 word/document.xml），用来验证上传切节 ✓。"""
    import zipfile
    body = "".join("<w:p><w:r><w:t>%s</w:t></w:r></w:p>" % t for t in paras)
    xml = ('<?xml version="1.0" encoding="UTF-8"?><w:document xmlns:w="http://schemas.'
           'openxmlformats.org/wordprocessingml/2006/main"><w:body>%s</w:body></w:document>'
           % body)
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("word/document.xml", xml)


def test_document_upload_then_sections(ext):
    """上传 docx ⇒ 落文件 + 切节 + 挂索引；随后 outline/section 可读（内网未接通时的补位）✓。"""
    import tempfile
    tmp = tempfile.mkdtemp()
    src = os.path.join(tmp, "方案.docx")
    _mk_docx(src, ["1 概述", "现状说明。", "2.1 应用架构", "涉及系统：A、B。"])
    raw = open(src, "rb").read()
    up = D.save_document(doc_id="doc-up-1", review_no="R-UP", filename="方案.docx",
                         raw=raw, title="方案（上传）", actor="t")
    assert up["ok"] and up["doc_id"] == "doc-up-1", up
    assert up["sections"] >= 2, up                      # 「1 概述」「2.1 应用架构」
    assert up["url"].endswith("/files/doc-up-1"), up
    out = D.access(kb_id="kb", action="outline", doc_id="doc-up-1", review_no="R-UP")
    assert out["ok"] and out["counts"]["sections"] >= 2, out
    sec = D.access(kb_id="kb", action="section", doc_id="doc-up-1", section_path="2.1")
    assert sec["ok"] and "涉及系统" in (sec.get("text") or ""), sec
    dl = D.doc_file("doc-up-1")
    assert dl["ok"] and dl["path"].endswith("doc-up-1.docx"), dl


def test_import_knowledge_creates_doc_and_flow(ext, monkeypatch):
    """**智能体用工具导入附件**（用户上传的那份）⇒ 建档 + 绑文档 + 可读正文（不走 shell）✓。"""
    import tempfile
    tmp = tempfile.mkdtemp()
    src = os.path.join(tmp, "方案.docx")
    _mk_docx(src, ["1 概述", "正文", "2.1 设计", "细节"])
    raw = open(src, "rb").read()
    monkeypatch.setattr(D, "_knowledge_rows", lambda **kw: [
        {"id": "kid-1", "tenant_id": 10000, "knowledge_base_id": "kb-x",
         "title": "方案.docx", "file_name": "方案.docx"}])
    monkeypatch.setattr(D, "_read_knowledge_bytes", lambda t, k, f="": (raw, {"path": "/x"}))
    r = D.import_knowledge(review_no="R-IMP", doc_title="方案.docx", actor="t")
    assert r["ok"] and r["doc_id"] == "doc-kid-1", r
    assert r["sections"] >= 2, r
    assert r["flow"]["ok"] and r["flow"]["created"] is True, r
    idx = json.load(open(os.path.join(_sys(ext, "docs/index.json")), encoding="utf-8"))
    assert "doc-kid-1" in (idx.get("R-IMP") or []), idx
    o = D.access(kb_id="kb", action="outline", doc_id="doc-kid-1", review_no="R-IMP")
    assert o["ok"] and o["counts"]["sections"] >= 2, o


def test_import_knowledge_ambiguous_does_not_guess(ext, monkeypatch):
    """同名附件多份 ⇒ `doc_ambiguous` + candidates（**不猜** ✗）。"""
    monkeypatch.setattr(D, "_knowledge_rows", lambda **kw: [
        {"id": "k1", "title": "a.docx", "file_name": "a.docx", "knowledge_base_id": "kb1"},
        {"id": "k2", "title": "a.docx", "file_name": "a.docx", "knowledge_base_id": "kb2"}])
    r = D.import_knowledge(review_no="R-IMP2", doc_title="a.docx")
    assert r["ok"] is False and r["error"] == "doc_ambiguous", r
    assert len(r["candidates"]) == 2


def test_import_knowledge_file_unreadable(ext, monkeypatch):
    """容器里读不到附件 ⇒ `file_not_found` + diag（排障要能看到路径/rc）✓。"""
    monkeypatch.setattr(D, "_knowledge_rows", lambda **kw: [
        {"id": "k3", "tenant_id": 1, "title": "b.docx", "file_name": "b.docx",
         "knowledge_base_id": "kb"}])
    monkeypatch.setattr(D, "_read_knowledge_bytes", lambda t, k, f="": (b"", {"rc": 1, "path": "/x"}))
    r = D.import_knowledge(review_no="R-IMP3", doc_title="b.docx")
    assert r["ok"] is False and r["error"] == "file_not_found" and r["diag"], r


def test_import_from_chat_uses_session_attachment(ext, monkeypatch):
    """**直接用会话里上传的附件**（`messages.attachments` + exports 原件）⇒ 建档+图+节 ✓。"""
    monkeypatch.setattr(D, "_latest_chat_attachment", lambda s="": {
        "message_id": "m-1", "session_id": "s-1", "at": "2026-10-26 05:26:25",
        "epoch": 1792992386, "items": [{"id": "a-1", "content": "1 概述\n正文"}]})
    monkeypatch.setattr(D, "_pick_chat_file", lambda ep, g: {
        "path": "/data/files/10000/exports/chat_attachment_x_1.pdf", "size": 10,
        "mtime": ep, "gap": 1, "candidates": 1})
    monkeypatch.setattr(D, "_container_bytes", lambda p: b"%PDF-1.4 fake")
    monkeypatch.setattr(D, "_pdf_sections_and_images", lambda raw, did, title: (
        [{"path": "1 概述", "title": "1 概述", "text": "正文"}],
        [{"name": "图片1.png", "path": "files/x_img1.png"}],
        {"text_source": "pdf_pypdf", "pages": 1, "images_available": True,
         "tables_text_only": True}))
    r = D.import_knowledge(review_no="R-CHAT", doc_title="方案.pdf", source="chat", actor="t")
    assert r["ok"] and r["source"] == "chat" and r["images"] == 1, r
    assert r["quality"]["tables_text_only"] is True
    assert "tables_text_only" in (r["warnings"] or []), r
    assert r["flow"]["ok"] and r["flow"]["created"] is True, r
    doc = json.load(open(os.path.join(_sys(ext, "docs/%s.json" % r["doc_id"])), encoding="utf-8"))
    assert doc["source"] == "chat" and doc["origin_path"].endswith(".pdf"), doc


def test_import_auto_falls_back_to_chat(ext, monkeypatch):
    """`source=auto`：知识库没有 ⇒ **自动改用会话附件**，回执 `tried=[kb, chat]` ✓。"""
    monkeypatch.setattr(D, "_knowledge_rows", lambda **kw: [])
    monkeypatch.setattr(D, "_latest_chat_attachment", lambda s="": {
        "message_id": "m-2", "session_id": "s-2", "at": "2026-10-26 05:26:25",
        "epoch": 1792992386, "items": [{"id": "a-2", "content": "1 概述"}]})
    monkeypatch.setattr(D, "_pick_chat_file", lambda ep, g: {
        "path": "/data/files/10000/exports/chat_attachment_y_2.pdf", "size": 9,
        "mtime": ep, "gap": 2, "candidates": 3})
    monkeypatch.setattr(D, "_container_bytes", lambda p: b"%PDF-1.4 fake")
    monkeypatch.setattr(D, "_pdf_sections_and_images", lambda raw, did, title: (
        [{"path": "全文", "title": "全文", "text": "1 概述"}], [],
        {"text_source": "pdf_pypdf", "pages": 1, "images_available": False}))
    r = D.import_knowledge(review_no="R-AUTO", doc_title="方案.pdf", actor="t")
    assert r["ok"] and r["source"] == "chat" and r["tried"] == ["kb", "chat"], r
    assert "images_unavailable" in (r["warnings"] or []), r


def test_import_chat_without_attachment(ext, monkeypatch):
    """会话里没有带附件的消息 ⇒ `chat_attachment_not_found`（不猜 ✗）。"""
    monkeypatch.setattr(D, "_latest_chat_attachment", lambda s="": {})
    r = D.import_knowledge(review_no="R-CHAT2", source="chat")
    assert r["ok"] is False and r["error"] == "chat_attachment_not_found", r


def test_gate_skips_doc_without_ext_ref(ext):
    """与接口无关的页（模板/说明页）⇒ 门禁直接跳过，不报 `ext_no_ref_block` ✗。"""
    import ke_ext_gate as G
    r = G.check_report("# 模板\n\n## 章节结构\n| 序号 | 章节 |\n|---|---|\n| 1 | ① 结论 |\n")
    assert r["warnings"] == [], r
    assert r.get("skipped") == "no_ext_ref", r


def test_auto_ref_block_added_from_report_context(ext, monkeypatch):
    """缺引用块 ⇒ 工具补锚点：**编号从正文主键列对台账反查**，object 按契约标签匹配 ✓。"""
    import ke_ext_gate as G
    _bump_ledger("R9", 3)                       # 台账 rev=3、条目 P-01
    monkeypatch.setattr(G, "_kb_objects", lambda kb: [
        {"slug": "bpmn/dataobject/x", "kb_id": "kb",
         "binding": {"tool": "ext_issue_list", "keys": ["review_no"],
                     "impl": "it_platform:tools/ke-core/ke_ext_issues.py"}}])
    monkeypatch.setattr(G, "_contract_of",
                        lambda obj: {"labels": {"issue_no": "编号", "verdict": "判定",
                                                "severity": "严重度"},
                                     "order": ["issue_no", "verdict", "severity"],
                                     "source": "projection"})
    monkeypatch.setattr(G, "_schema_hash", lambda kb, slug: "sha256:aa")
    md = "## ③ 清单\n\n| 编号 | 判定 | 严重度 |\n|---|---|---|\n| P-01 | 符合 |  |\n"
    r = G.auto_ref_block(md, "kb")
    assert r["changed"] is True, r
    assert r["review_no"] == "R9" and r["rev"] == 3, r
    assert "review_no: R9" in r["md"] and "columns: issue_no,verdict,severity" in r["md"], r["md"]
    assert r["md"].index("bodhi-ext") > r["md"].index("| P-01 |"), "块必须紧跟表格之后"


def test_auto_ref_block_skips_when_present_or_ambiguous(ext, monkeypatch):
    """已有块 ⇒ 不动；object 多候选 ⇒ **不猜**（交人定）✓。"""
    import ke_ext_gate as G
    _bump_ledger("R9", 1)
    assert G.auto_ref_block(_ref_block("1"), "kb")["changed"] is False
    assert G.auto_ref_block("## ① 无表\n正文", "kb")["reason"] == "no_item_table"
    monkeypatch.setattr(G, "_kb_objects", lambda kb: [
        {"slug": "a", "kb_id": "kb", "binding": {"tool": "t", "keys": ["review_no"], "impl": "s:x"}},
        {"slug": "b", "kb_id": "kb", "binding": {"tool": "t", "keys": ["review_no"], "impl": "s:x"}}])
    monkeypatch.setattr(G, "_contract_of",
                        lambda obj: {"labels": {"issue_no": "编号", "verdict": "判定",
                                                "severity": "严重度"},
                                     "order": ["issue_no", "verdict", "severity"],
                                     "source": "projection"})
    md = "| 编号 | 判定 | 严重度 |\n|---|---|---|\n| P-01 | 符合 |  |\n"
    r = G.auto_ref_block(md, "kb")
    assert r["changed"] is False and r["reason"] == "object_ambiguous", r
    assert r["candidates"] == ["a", "b"], r


def test_g5_consistent_report_no_finding(ext, monkeypatch):
    """表头 = 契约 labels(columns) = 模板 fields ⇒ 不报（三方一致是常态 ✓）。"""
    monkeypatch.setattr(A, "_template_items_of", lambda kb_id="": _tpl_items("issue_no,verdict"))
    rep = A.Report("kb", "ext", 50)
    ctx = _g5_ctx(_report_md(" 编号 | 判定 ", "issue_no,verdict"))
    A.check_ext_consistency(ctx, rep)
    assert rep.totals.get("G5") is None, rep.findings
    d = ctx["data"]["ext_consistency"]
    assert d["reports"] == 1 and d["ledger_checked"] == 1 and d["template_checked"] == 1, d


def test_g5_template_mismatch_reported(ext, monkeypatch):
    """正文列集与模板「数据对象字段」不一致 ⇒ G5 medium（模板是口径真源 ✓）。"""
    monkeypatch.setattr(A, "_template_items_of", lambda kb_id="": _tpl_items("issue_no,verdict"))
    rep = A.Report("kb", "ext", 50)
    ctx = _g5_ctx(_report_md(" 编号 | 判定 | 严重度 ", "issue_no,verdict,severity"))
    A.check_ext_consistency(ctx, rep)
    hits = [f for f in rep.findings if f["check"] == "G5" and "模板" in f["detail"]]
    assert hits and hits[0]["severity"] == "medium", rep.findings
    assert ctx["data"]["ext_consistency"]["header_mismatch"] == 0, "与回执一致时不该报回执不符 ✗"


def test_g5_columns_not_in_contract_reported(ext, monkeypatch):
    """引用块 `columns:` 写了契约外字段 ⇒ G5 medium（不静默放过 ✓）。"""
    monkeypatch.setattr(A, "_template_items_of", lambda kb_id="": _tpl_items("issue_no,verdict"))
    rep = A.Report("kb", "ext", 50)
    ctx = _g5_ctx(_report_md(" 编号 | 判定 ", "issue_no,nope"))
    A.check_ext_consistency(ctx, rep)
    hits = [f for f in rep.findings if "契约外字段" in f["detail"]]
    assert hits, rep.findings
    assert ctx["data"]["ext_consistency"]["columns_not_in_contract"] == 1


def test_g5_dataobject_definition_overreach_low(ext, monkeypatch):
    """DataObject 定义又写「1 名称｜2 名称｜…」列清单 ⇒ G5 low（职责越界 ✓）。"""
    monkeypatch.setattr(A, "_template_items_of", lambda kb_id="": [])
    bad = {"ontology": {"attributes": {"skos:definition":
           "口径：\n1 编号｜2 描述｜3 规则｜4 位置｜5 摘录｜"}}}
    rep = A.Report("kb", "ext", 50)
    ctx = _g5_ctx("", meta=json.dumps(bad, ensure_ascii=False), page_type="bpmn:DataObject")
    A.check_ext_consistency(ctx, rep)
    hits = [f for f in rep.findings if "职责越界" in f["detail"]]
    assert hits and hits[0]["severity"] == "low", rep.findings

    clean = {"ontology": {"attributes": {"skos:definition":
             "本页只写接口定义：字段口径来自契约投影，列集由模板决定 ✓"}}}
    rep2 = A.Report("kb", "ext", 50)
    ctx2 = _g5_ctx("", meta=json.dumps(clean, ensure_ascii=False), page_type="bpmn:DataObject")
    A.check_ext_consistency(ctx2, rep2)
    assert rep2.totals.get("G5") is None, rep2.findings


def test_ext_block_lag_pure(ext):
    """rev 比对是纯函数：落后/持平/不可比 三种都覆盖（台账不可比不得当 0 版）✓。"""
    assert A.ext_block_lag({"rev": "2"}, 5)["lag"] == 3
    assert A.ext_block_lag({"rev": "5"}, 5)["lag"] == 0
    assert A.ext_block_lag({"rev": "7"}, 5)["lag"] == 0, "快照比台账新也不算落后（台账被回滚过）"
    assert A.ext_block_lag({"rev": "?"}, 5)["comparable"] is False


def _bump_ledger(rno: str, rev_target: int) -> None:
    """把测试台账推到指定 rev（create 起 1，之后每次 update +1）。"""
    I.maintain(kb_id="kb", review_no=rno, mode="maintain", action="create", issue_no="P-01",
               actor="t", dry_run=False, fields={"desc": "x"})
    for i in range(1, rev_target):
        I.maintain(kb_id="kb", review_no=rno, mode="maintain", action="update", issue_no="P-01",
                   actor="t", dry_run=False, if_rev=i, fields={"desc": "v%d" % (i + 1)})


def test_g4_stale_snapshot_reported(ext):
    """快照 rev < 台账 rev ⇒ 一条 low（能定位到页 + 差几版 + 一行修法）✓。"""
    _bump_ledger("R9", 3)                     # 台账 rev = 3
    rep = A.Report("kb", "ext", 50)
    ctx = {"kb_id": "kb", "pages": [_report_page(_ref_block("1"))], "data": {}}
    A.check_ext_ledger(ctx, rep)
    assert rep.totals.get("G4") == 1, rep.totals
    f = rep.findings[0]
    assert f["severity"] == "low" and f["subject"] == "bmm/report/sample", f
    assert "落后" in f["detail"] and "差 2 版" in f["detail"], f["detail"]
    assert ctx["data"]["ext_ledger"]["stale"] == 1


def test_g4_fresh_snapshot_no_finding(ext):
    """快照与台账同版 ⇒ 不报（巡检不能靠\"随便报一条\"刷存在感 ✗）。"""
    _bump_ledger("R9", 3)
    rep = A.Report("kb", "ext", 50)
    ctx = {"kb_id": "kb", "pages": [_report_page(_ref_block("3"))], "data": {}}
    A.check_ext_ledger(ctx, rep)
    assert rep.totals.get("G4") is None, rep.totals
    assert ctx["data"]["ext_ledger"]["ledgers"] == 1


def test_g4_unreadable_ledger_still_reported(ext, monkeypatch):
    """绑定/台账不可读 ⇒ 也从实报（否则\"0 问题\"是假象），且提示怎么修 ✓。"""
    monkeypatch.setattr(S, "resolve_binding",
                        lambda tool, kb_id="", object_slug="": {"ok": False, "why": "no_binding"})
    rep = A.Report("kb", "ext", 50)
    ctx = {"kb_id": "kb", "pages": [_report_page(_ref_block("1"))], "data": {}}
    A.check_ext_ledger(ctx, rep)
    assert rep.totals.get("G4") == 1, rep.totals
    assert "不可读" in rep.findings[0]["detail"] and "no_binding" in rep.findings[0]["detail"]
    assert ctx["data"]["ext_ledger"]["unreadable"] == 1


# ── 文档 / 附件 / 图（2026-10-26：样例文档换成真实案例 ⇒ 断言按"结构"而非固定条数） ──
def test_docs_outline_and_section(ext):
    o = D.access(kb_id="kb", action="outline", doc_id="doc-sample-1")
    assert o["ok"] and o["counts"]["sections"] >= 5, o
    paths = [x["path"] for x in o["outline"]]
    assert any(p.startswith("2.1") for p in paths), paths
    s = D.access(kb_id="kb", action="section", doc_id="doc-sample-1", section_path="2.1")
    assert s["ok"] and s["text"], "正文必须是逐字（外部系统不给下载 ⇒ 只能按章节取）✗"
    assert s["path"].startswith("2.1"), s["path"]


def test_docs_section_paging(ext):
    s0 = D.access(kb_id="kb", action="section", doc_id="doc-sample-1", cursor=0)
    assert s0.get("next_cursor") == 1, s0
    s1 = D.access(kb_id="kb", action="section", doc_id="doc-sample-1", cursor=s0["next_cursor"])
    assert s1["cursor"] == 1 and s1["path"] != s0["path"], s1


def test_docs_section_not_found_lists_available(ext):
    r = D.access(kb_id="kb", action="section", doc_id="doc-sample-1", section_path="9 不存在")
    assert r["error"] == "section_not_found" and r["available"], r


def test_docs_image_inline_data_url(ext):
    r = D.access(kb_id="kb", action="image", doc_id="doc-sample-1", inline_media=True)
    assert r["counts"]["readable"] >= 1, r
    img = [x for x in r["images"] if x.get("readable")][0]
    assert img["data_url"].startswith("data:image/"), img
    assert img["mime"].startswith("image/") and img["sha256"], img


def test_docs_image_too_large(ext):
    r = D.access(kb_id="kb", action="image", doc_id="doc-sample-1", inline_media=False,
                 max_bytes=10)
    assert r["images"][0]["reason"] == "too_large" and not r["images"][0]["readable"], r


def test_docs_image_path_traversal_blocked(ext):
    """容器内相对路径不能穿出根目录（`../` ⇒ 直接不可读 ✓）。"""
    d = {"doc_id": "d-evil", "title": "x", "sections": [],
         "images": [{"name": "evil.png", "path": "../evil.png"}]}
    with open(_sys(ext, "docs/d-evil.json"), "w", encoding="utf-8") as fh:
        json.dump(d, fh, ensure_ascii=False)
    r = D.access(kb_id="kb", action="image", doc_id="d-evil", inline_media=True)
    assert r["images"][0]["readable"] is False, "路径穿越必须被拦 ✗ %r" % (r,)


def test_docs_attachment_lists(ext):
    """附件清单：有 `url`（可下载）且 `readable` 如实反映文件是否在容器根内 ✓。"""
    r = D.access(kb_id="kb", action="attachment", doc_id="doc-sample-1")
    assert r["counts"]["attachments"] >= 1 and r["rows"][0]["name"], r
    assert r["rows"][0]["url"].endswith("/files/doc-sample-1"), r["rows"][0]
    assert all("readable" in x for x in r["rows"]), r


# ── 绑定解析 / 存储 ─────────────────────────────────────────────────────
def test_parse_binding_attrs():
    """4 个数据属性 → 绑定列表（接口逗号分隔；模式/主键/提供方分号顺序对齐）✓"""
    binds = S.parse_binding({
        "dataObjectInterface": "ext_a, ext_b",
        "dataObjectAccessMode": "query;query,maintain",
        "dataObjectKeyFields": "k1;k2,k3",
        "dataObjectInterfaceRef": "tools/ke-core/ke_ext_catalog.py;tools/ke-core/ke_ext_issues.py"})
    assert [b["tool"] for b in binds] == ["ext_a", "ext_b"], binds
    assert binds[0]["modes"] == ["query"] and binds[0]["keys"] == ["k1"], binds[0]
    assert binds[1]["modes"] == ["query", "maintain"] and binds[1]["keys"] == ["k2", "k3"], binds[1]
    assert binds[1]["impl"] == "tools/ke-core/ke_ext_issues.py"
    assert S.parse_binding({}) == [], "没声明接口 ⇒ 空列表（调用方回 no_binding）✗"


def test_parse_binding_single_defaults():
    """只写接口时：模式默认 query、主键为空（不猜更多 ✓）"""
    binds = S.parse_binding({"dataObjectInterface": "ext_only"})
    assert binds == [{"tool": "ext_only", "modes": ["query"], "keys": [], "impl": ""}], binds


def test_atomic_write_leaves_valid_json(ext):
    p = str(_sys(ext, "probe.json"))
    S.write_json(p, {"a": 1})
    S.write_json(p, {"a": 2})
    with open(p, encoding="utf-8") as fh:
        assert json.load(fh) == {"a": 2}
    assert not [x for x in os.listdir(os.path.dirname(p)) if ".tmp." in x], "临时文件必须清掉 ✗"
