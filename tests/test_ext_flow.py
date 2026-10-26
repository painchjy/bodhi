"""外部接口（模拟）· **评审流程上下文**（`ext_review_flow`）回归

口径（2026-10-26 用户裁定）：
- 智能体只提交「评审编号 + 文件名称」，**不需要上传文档**；
- 编号一律视为**平台已存在**：无档案 ⇒ 直接建档（`status=评审中, round=1, authority=agent_requested`）；
- 轮次以档案为准，智能体要求更小 ⇒ `warn=round_behind`（不静默）；
- 文档清单带**可下载 url**；正文仍走 `ext_doc_access`。
"""
import json
import os
import sys

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "tools", "ke-core"))

import ke_ext_flow as F  # noqa: E402
import ke_ext_store as S  # noqa: E402

BINDING = {"tool": "ext_review_flow", "modes": ["query", "maintain"],
           "keys": ["review_no"], "impl": "it_platform:tools/ke-core/ke_ext_flow.py"}


@pytest.fixture
def flow(tmp_path, monkeypatch):
    """容器根隔离 + 注入绑定；顺带准备一份"文档索引 + 文档"（与真实 mock 同结构）。"""
    monkeypatch.setenv("BODHI_EXT_ROOT", str(tmp_path))
    monkeypatch.setattr(S, "resolve_binding",
                        lambda tool, kb_id="", object_slug="":
                        {"ok": True, "binding": dict(BINDING),
                         "object": {"slug": "bpmn/dataobject/评审流程"}})
    d = tmp_path / "it-platform"
    (d / "docs").mkdir(parents=True, exist_ok=True)
    (d / "flows").mkdir(parents=True, exist_ok=True)
    (d / "docs" / "index.json").write_text(json.dumps(
        {"_default": ["doc-a"], "R-1": ["doc-a"]}), encoding="utf-8")
    (d / "schema.json").write_text(json.dumps({"x_ext": {"ext_review_flow": {
        "defaults": {"status": "处理中", "stage": "执行"}}}}), encoding="utf-8")
    (d / "docs" / "doc-a.json").write_text(json.dumps({
        "doc_id": "doc-a", "title": "方案A", "mime": "application/pdf", "size": 123,
        "round": 1, "sha256": "abcd1234",
        "sections": [{"path": "1 概述", "title": "概述", "text": "正文A"}],
        "files": [{"name": "方案A.pdf", "path": "files/doc-a.pdf", "mime": "application/pdf",
                   "size": 123}],
        "attachments": [], "images": []}, ensure_ascii=False), encoding="utf-8")
    return tmp_path


def test_number_unknown_creates_archive(flow):
    """编号不存在 ⇒ **建档**（平台已存在口径）；状态/阶段取契约 `defaults`（代码不写业务取值 ✓）。"""
    r = F.call(kb_id="kb", review_no="NEW-001")
    assert r["ok"] and r["status"] == "处理中" and r["stage"] == "执行", r
    assert r["round"] == 1 and r["authority"] == "agent_requested", r
    assert (r["counts"] or {}).get("docs") == 1, r
    assert r["docs"][0]["url"].endswith("/files/doc-a"), r["docs"][0]
    assert (flow / "it-platform" / "flows" / "NEW-001.json").is_file(), "档案必须落盘 ✓"


def test_open_binds_docs_by_filename(flow):
    """只给「编号 + 文件名」⇒ open 绑文档；回执给 next（不再要求上传）✓。"""
    r = F.call(kb_id="kb", review_no="R-1", action="open", round=1,
               filenames=["方案A.pdf"], actor="智能体")
    assert r["ok"] and r["round_source"] == "agent", r
    assert [d["doc_id"] for d in r["docs"]] == ["doc-a"], r["docs"]
    assert "ext_doc_access" in (r.get("next") or ""), r
    assert any("按文件名绑定" in n for n in (r.get("notes") or [])), r


def test_round_ahead_advances_and_behind_warns(flow):
    """轮次：智能体要求更大 ⇒ 推进（round_source=agent）；更小 ⇒ `round_behind` 且以档案为准。"""
    F.call(kb_id="kb", review_no="R-1", action="open", round=2, actor="a")
    r2 = F.call(kb_id="kb", review_no="R-1")
    assert r2["round"] == 2, r2
    r3 = F.call(kb_id="kb", review_no="R-1", action="open", round=1, actor="a")
    assert r3["warn"] == "round_behind" and r3["round"] == 2, r3


def test_docs_and_rounds_views(flow):
    F.call(kb_id="kb", review_no="R-1", action="open", round=2, actor="a")
    d = F.call(kb_id="kb", review_no="R-1", action="docs")
    assert d["counts"]["docs"] == 1 and d["rows"][0]["url"].endswith("/files/doc-a"), d
    rs = F.call(kb_id="kb", review_no="R-1", action="rounds")
    cur = [x for x in rs["rounds"] if x.get("is_current")]
    assert len(cur) == 1 and cur[0]["round"] == 2, rs      # 建档即按要求的轮次，当前轮唯一 ✓


def test_maintain_requires_actor_and_dry_run(flow):
    F.call(kb_id="kb", review_no="R-1")
    bad = F.call(kb_id="kb", review_no="R-1", mode="maintain", action="upsert_round", round=2)
    assert bad["error"] == "need_actor", bad
    dry = F.call(kb_id="kb", review_no="R-1", mode="maintain", action="upsert_round", round=2,
                 actor="t", dry_run=True)
    assert dry["dry_run"] is True and dry["diff"]["after"]["round"] == 2, dry
    assert json.loads((flow / "it-platform" / "flows" / "R-1.json").read_text(encoding="utf-8"))[
        "round"] == 1, "dry_run 不得改档案 ✗"


def test_doc_file_path_guard(flow, monkeypatch):
    """下载路径必须在容器根内（防穿越）⇒ 越界回 `file_not_found` ✓。"""
    import ke_ext_docs as D
    monkeypatch.setenv("BODHI_EXT_ROOT", str(flow))
    p = flow / "it-platform" / "docs" / "doc-a.json"
    p.write_text(json.dumps({"doc_id": "doc-a", "files": [
        {"name": "x", "path": "../../etc/passwd"}]}), encoding="utf-8")
    r = D.doc_file("doc-a")
    assert r["ok"] is False and r["error"] == "file_not_found", r
