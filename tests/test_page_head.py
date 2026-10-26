"""页头口径回归：「首个版本生成」= 首版时间；页头**幂等**（不再每次保存多一段）。

2026-10-26 用户实测两处问题：
1. `…（YYYYMM-666555 · 第 1 轮）` 的首个版本生成被重置成**最新版本时间**；
2. `…（第 1 轮）` 的首版本生成内容**重复 9 段**（每个版本留了一段）。
"""
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "tools", "ke-core"))

import ke_pages as P  # noqa: E402

TITLE = "技术方案评审报告 —— 样例（第 1 轮）"
BODY = "# 技术方案评审报告 —— 样例（第 1 轮）\n\n## ① 范围与依据\n\n| 项 | 内容 |\n|---|---|\n| 轮次 | 第 1 轮 |\n"


def _head(when: str, session: str = "s-1") -> str:
    return ("# %s（`bmm:TechnicalReviewReport`）\n\n"
            "> **本体类型**：技术方案评审报告（`bmm:TechnicalReviewReport`）  \n"
            "> **来源会话**：%s  \n"
            "> **首个版本生成**：%s\n\n" % (TITLE, session, when))


def test_strip_heads_collapses_repeated_heads():
    """9 段重复页头 ⇒ 全剥掉，正文（含自己的 H1）原样保留 ✓。"""
    content = _head("2026-10-26 05:28:57 +0800") * 3 + _head("2026-10-25 20:53:11 +0800") + BODY
    head, rest = P.strip_heads(content)
    assert rest.strip() == BODY.strip(), rest[:200]
    assert "首个版本生成" not in rest
    assert head["首个版本生成"] == "2026-10-26 05:28:57 +0800", "首段页头字段优先保留"


def test_strip_heads_leaves_plain_page_alone():
    """没有「首个版本生成」的正常页（只有正文 H1）⇒ 一点都不动 ✓。"""
    head, rest = P.strip_heads(BODY)
    assert head == {} and rest == BODY


def test_contract_head_uses_given_created_at():
    """页头这一行**只认传进来的值**（不传才取当前时间）✓。"""
    lines = P._contract_head({"title": "T", "page_type": "bmm:TechnicalReviewReport",
                              "created_at": "2026-10-25 20:49:19 +0800"})
    assert any("> **首个版本生成**：2026-10-25 20:49:19 +0800" == x for x in lines), lines


def test_first_version_text_formats_plus_0800(monkeypatch):
    """「首个版本生成」的权威值 = DB `created_at` → `YYYY-MM-DD HH:MM:SS +0800` ✓。"""
    monkeypatch.setattr(P.ke_db, "psql_csv",
                        lambda q: [{"t": "2026-10-25 20:49:19"}] if "to_char" in q else [])
    assert P.first_version_text("kb", "slug") == "2026-10-25 20:49:19 +0800"
    monkeypatch.setattr(P.ke_db, "psql_csv", lambda q: [])
    assert P.first_version_text("kb", "slug") == ""


def test_repair_head_precedence_and_body(monkeypatch):
    """`repair_head`：参数 > **DB 首版时间** > 页头现值；写回正文已去掉重复页头 ✓。"""
    content = _head("2026-10-26 05:28:57 +0800") * 2 + BODY
    seen = {}

    def fake_sql(q):
        if "to_char" in q:
            return [{"t": "2026-10-25 20:49:19"}]
        return [{"title": TITLE, "page_type": "bmm:TechnicalReviewReport", "content": content,
                 "meta": '{"ontology": {"class_label": "技术方案评审报告"}}'}]

    def fake_write(kb_id, spec, **kw):
        seen.update(spec=spec, kw=kw)
        return {"applied": not kw.get("dry_run"), "before_version": 11,
                "content_preview": spec["wiki_content"][:60]}

    monkeypatch.setattr(P.ke_db, "psql_csv", fake_sql)
    monkeypatch.setattr(P, "write_knowledge", fake_write)
    r = P.repair_head("kb", "slug")
    assert r["heads_before"] == 2 and r["dry_run"] is True
    assert seen["spec"]["created_at"] == "2026-10-25 20:49:19 +0800", "默认取 DB 首版时间"
    assert "首个版本生成" not in seen["spec"]["wiki_content"]
    assert seen["kw"].get("strict_source") is False

    r2 = P.repair_head("kb", "slug", created_at="2026-10-25 20:49:19 +0800", dry_run=False)
    assert r2["created_at_used"] == "2026-10-25 20:49:19 +0800" and seen["spec"]["created_at"] == \
        "2026-10-25 20:49:19 +0800"


def test_repair_head_missing_page(monkeypatch):
    monkeypatch.setattr(P.ke_db, "psql_csv", lambda q: [])
    r = P.repair_head("kb", "nope")
    assert r["ok"] is False and r["error"] == "page_not_found"
