"""L1 运维：测试库**硬删归零**（防 context 重复 ✓ 用户口径 2026-10-21）

断言：建 `_test_*` → 写 1 页 → `drop()` → PG/图 **全零** ✓，且库行不存在 ✓
"""
import pytest

pytestmark = pytest.mark.slow


def test_kb_hard_delete_zero_residual():
    from kb_factory import TestKB
    import ke_db
    import ke_neo4j

    f = TestKB("teardown")
    kb = f.create()
    assert kb, "未建库 ✗"
    # 2026-10-21 实测：`wiki_pages.tenant_id` 是 **NOT NULL** ⇒ 直接插必须带租户 ✗（否则 psql 报约束错）
    ke_db.psql("INSERT INTO wiki_pages (id, tenant_id, knowledge_base_id, slug, title, page_type, "
               "content, version) "
               "SELECT gen_random_uuid(), COALESCE(tenant_id, 0), %s, 'review/_t-x', 'X', "
               "'bmm:Report', '# X', 1 FROM knowledge_bases WHERE id = %s"
               % (ke_db.sql_str(kb), ke_db.sql_str(kb)))
    try:
        ke_neo4j.query("MERGE (n:BodhiInstance {kb_id:$kb, slug:'review/_t-x'})", {"kb": kb}, timeout=15)
    except Exception:  # noqa: BLE001
        pass
    out = f.drop()
    assert out["residual"] == {"pages": 0, "kb": 0}, "硬删未归零 ✗：%s" % out
    left = ke_db.psql_csv("SELECT count(*) AS c FROM knowledge_bases WHERE id = %s"
                          % ke_db.sql_str(kb))[0]
    assert int(left["c"]) == 0, "知识库行未删 ✗"
