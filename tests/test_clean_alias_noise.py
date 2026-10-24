"""维护工具 `clean_alias_noise`：清掉"别名=标题"的冗余项（默认 dry_run，只念清单）。

口径（2026-10-24 用户批准）：
- **只删与标题完全相同**的别名；真别名（缩写/英文名）保留 ✓；
- 执行：先写 `wiki_page_revisions` 快照 → 改 `aliases` → `version+1`（可回退 ✓）；
- 清理后巡检 W7（别名含标题）应归零。

跑法：`python3 -m pytest tests/test_clean_alias_noise.py`
"""
import json


def _mk(mcp, kb, sno, slug, title, aliases):
    """建页 + **模拟历史噪音**：直接给 PG `aliases` 塞值（新写路径已会自动剔除标题 ✗）。"""
    r = mcp.call("save_knowledge", {
        "kb_id": kb, "stage": "report", "mode": "apply", "session_no": sno,
        "report": {"title": title, "slug": slug,
                   "content_md": "# %s\n\n## ① 范围与依据\n- x\n" % title}})
    assert r.get("applied") is True, json.dumps(r, ensure_ascii=False)[:300]
    import ke_db
    ke_db.psql("UPDATE wiki_pages SET aliases = %s::jsonb "
               "WHERE knowledge_base_id = %s AND slug = %s AND deleted_at IS NULL;"
               % (ke_db.sql_json(list(aliases)), ke_db.sql_str(kb), ke_db.sql_str(slug)),
               stdin=True)
    return r


def _aliases(kb, slug):
    import ke_db
    rows = ke_db.psql_csv(
        "SELECT COALESCE(aliases::text,'[]') AS a, COALESCE(version,1) AS v FROM wiki_pages "
        "WHERE knowledge_base_id = %s AND slug = %s AND deleted_at IS NULL"
        % (ke_db.sql_str(kb), ke_db.sql_str(slug)))
    return (json.loads(rows[0]["a"]) if rows else []), (int(rows[0]["v"]) if rows else 0)


def test_dry_run_lists_without_writing(mcp, test_kb, session_no):
    slug, title = "review/_alias-noise", "别名噪音用例"
    _mk(mcp, test_kb, session_no, slug, title, [title, "真别名-缩写"])
    before, ver0 = _aliases(test_kb, slug)
    assert title in before, "前置：别名里应含标题（噪音）：%s" % before

    r = mcp.call("clean_alias_noise", {"kb_id": test_kb, "dry_run": True})
    assert r.get("ok") is True, r
    assert int(r.get("hits") or 0) >= 1, "dry_run 没念出清单 ✗：%s" % json.dumps(r, ensure_ascii=False)[:300]
    pages = {p.get("slug"): p for p in (r.get("pages") or [])}
    assert slug in pages, "清单里没有该页 ✗：%s" % list(pages)
    assert title in (pages[slug].get("removed") or []), pages[slug]
    assert "真别名-缩写" in (pages[slug].get("kept") or []), pages[slug]

    after, ver1 = _aliases(test_kb, slug)
    assert after == before and ver1 == ver0, "dry_run 竟然写了库 ✗：%s → %s（v%s→v%s）" % (
        before, after, ver0, ver1)


def test_apply_removes_title_alias_only(mcp, test_kb, session_no):
    slug, title = "review/_alias-clean", "别名清理用例"
    _mk(mcp, test_kb, session_no, slug, title, [title, "ABC-缩写"])
    before, ver0 = _aliases(test_kb, slug)
    assert title in before and "ABC-缩写" in before, before

    r = mcp.call("clean_alias_noise", {"kb_id": test_kb, "dry_run": False})
    assert r.get("ok") is True and int(r.get("cleaned") or 0) >= 1, \
        json.dumps(r, ensure_ascii=False)[:300]

    after, ver1 = _aliases(test_kb, slug)
    assert title not in after, "标题别名没被清掉 ✗：%s" % after
    assert "ABC-缩写" in after, "真别名被误删 ✗：%s" % after
    assert ver1 == ver0 + 1, "清理应 version+1：%s → %s" % (ver0, ver1)

    # 幂等：再跑一次不该再有该页的命中
    r2 = mcp.call("clean_alias_noise", {"kb_id": test_kb, "dry_run": True})
    pages2 = {p.get("slug") for p in (r2.get("pages") or [])}
    assert slug not in pages2, "清理不幂等（二次仍命中）✗"


def test_cleanup_turns_w7_off(mcp, test_kb, session_no):
    """清理后巡检 W7（别名含标题）对该页应消失。"""
    slug, title = "review/_alias-w7", "别名巡检用例"
    _mk(mcp, test_kb, session_no, slug, title, [title])
    before = mcp.call("audit_scan", {"kb_id": test_kb, "scope": "wiki", "max_findings": 200})
    hit = [f for f in (before.get("findings") or [])
           if f.get("check") == "W7" and f.get("subject") == slug]
    assert hit, "前置：巡检应报 W7 ✗：%s" % json.dumps(
        (before.get("totals") or {}), ensure_ascii=False)

    mcp.call("clean_alias_noise", {"kb_id": test_kb, "dry_run": False})
    after = mcp.call("audit_scan", {"kb_id": test_kb, "scope": "wiki", "max_findings": 200})
    still = [f for f in (after.get("findings") or [])
             if f.get("check") == "W7" and f.get("subject") == slug]
    assert not still, "清理后 W7 仍命中 ✗：%s" % json.dumps(still, ensure_ascii=False)[:300]
