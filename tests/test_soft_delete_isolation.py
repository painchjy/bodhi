"""B7 · **部分唯一索引**语义：软删行与活行可同名，所有"按 slug 取页"必须只认活行。

背景（2026-10-21 实测 `pg_indexes`）：唯一索引是
`UNIQUE (knowledge_base_id, slug) WHERE deleted_at IS NULL` ⇒ 软删后可再插同名页（**设计如此** ✓），
所以任何"按 slug 取最新"的逻辑必须显式 `deleted_at IS NULL`，否则会拿到死行 ✗。

本用例**行为级**验证（不靠数 SQL）：放一条软删"诱饵行"（version=9、内容含 DECOY），
然后确认 ① 面板读 ② 检索 ③ 覆盖写 三条路径都只认活行。

跑法：`python3 -m pytest tests/test_soft_delete_isolation.py`
"""
import json
import os
import urllib.parse
import urllib.request

BASE = os.environ.get("BODHI_MCP_URL", "http://127.0.0.1:8765/mcp").replace("/mcp", "")
SLUG = "review/_sd-live"
DECOY = "DECOY-死行-不该被读到"


def _panel(kb, slug):
    q = urllib.parse.urlencode({"kb_id": kb, "slug": slug})
    with urllib.request.urlopen(BASE + "/bodhi/page/attributes?" + q, timeout=30) as r:
        return json.loads(r.read().decode())


def _write(mcp, kb, sno, body, slug=SLUG):
    return mcp.call("save_knowledge", {
        "kb_id": kb, "stage": "report", "mode": "apply", "session_no": sno,
        "report": {"title": "软删隔离用例", "slug": slug, "content_md": body}})


def _plant_decoy(kb):
    """插一条**同名但已软删**的页（version=9，内容带 DECOY）——部分索引允许 ✓。"""
    import ke_db
    ke_db.psql(
        "INSERT INTO wiki_pages (id, tenant_id, knowledge_base_id, slug, title, page_type, status, "
        "content, summary, version, last_edit_source, page_metadata, deleted_at) "
        "SELECT gen_random_uuid()::text, tenant_id, id, %s, 'DECOY', '', 'published', %s, '', 9, "
        "'pytest', '{}'::jsonb, now() FROM knowledge_bases WHERE id = %s;"
        % (ke_db.sql_str(SLUG), ke_db.sql_str(DECOY), ke_db.sql_str(kb)), stdin=True)
    import ke_db as k2
    return k2.psql_csv(
        "SELECT version, deleted_at IS NOT NULL AS del FROM wiki_pages "
        "WHERE knowledge_base_id = %s AND slug = %s AND deleted_at IS NOT NULL"
        % (k2.sql_str(kb), k2.sql_str(SLUG)))


def test_panel_reads_live_row_not_soft_deleted(mcp, test_kb, session_no):
    r = _write(mcp, test_kb, session_no, "# 软删隔离用例\n\n## ① 范围与依据\n- LIVE-v1\n")
    assert r.get("applied") is True, json.dumps(r, ensure_ascii=False)[:300]
    decoys = _plant_decoy(test_kb)
    assert decoys and int(decoys[0]["version"] or 0) == 9, "诱饵行没插进去：%s" % decoys

    d = _panel(test_kb, SLUG)
    assert d.get("ok") is not False, d
    assert int(d.get("version") or 0) == 1, "面板读到了死行（version=%s）✗：%s" % (d.get("version"), d)
    assert DECOY not in json.dumps(d, ensure_ascii=False), "面板回执里出现了死行内容 ✗"


def test_lookup_hits_live_row_only(mcp, test_kb, session_no):
    r = _write(mcp, test_kb, session_no, "# 软删隔离用例\n\n## ① 范围与依据\n- LIVE-v1\n")
    assert r.get("applied") is True, json.dumps(r, ensure_ascii=False)[:300]
    _plant_decoy(test_kb)

    got = mcp.call("knowledge_lookup", {"kb_id": test_kb, "mode": "keyword", "query": SLUG})
    hits = [h for h in (got.get("hits") or []) if h.get("slug") == SLUG]
    assert hits, "活行检索不到 ✗：%s" % json.dumps(got, ensure_ascii=False)[:300]
    assert len(hits) == 1, "同 slug 出现多条命中（含死行）✗：%s" % json.dumps(hits, ensure_ascii=False)[:300]
    blob = json.dumps(hits, ensure_ascii=False)
    assert DECOY not in blob, "命中片段来自死行 ✗：%s" % blob[:300]


def test_overwrite_updates_live_row_and_keeps_decoy(mcp, test_kb, session_no):
    r1 = _write(mcp, test_kb, session_no, "# 软删隔离用例\n\n## ① 范围与依据\n- LIVE-v1\n")
    assert r1.get("applied") is True, json.dumps(r1, ensure_ascii=False)[:300]
    _plant_decoy(test_kb)

    r2 = _write(mcp, test_kb, session_no, "# 软删隔离用例\n\n## ① 范围与依据\n- LIVE-v2\n")
    assert r2.get("applied") is True, json.dumps(r2, ensure_ascii=False)[:300]
    pv = (r2.get("page_versions") or [{}])[0]
    assert int(pv.get("before") or 0) == 1, "覆盖写没认活行（before=%s）✗：%s" % (pv.get("before"), pv)

    import ke_db
    rows = ke_db.psql_csv(
        "SELECT version, deleted_at IS NOT NULL AS del, left(COALESCE(content,''),20) AS head "
        "FROM wiki_pages WHERE knowledge_base_id = %s AND slug = %s ORDER BY del"
        % (ke_db.sql_str(test_kb), ke_db.sql_str(SLUG)))
    def _dead(r):
        return str(r.get("del")).strip().lower() in ("t", "true", "1")
    live = [r for r in rows if not _dead(r)]
    dead = [r for r in rows if _dead(r)]
    assert len(live) == 1, "活行不是唯一 ✗：%s" % rows
    assert int(live[0]["version"] or 0) == 2, "活行版本没 +1 ✗：%s" % rows
    assert dead and int(dead[0]["version"] or 0) == 9, "死行被改动/被删了 ✗：%s" % rows
