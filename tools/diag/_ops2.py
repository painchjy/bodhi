"""开发 kb 迁移工具前的勘查：PG 表结构 + Neo4j 实例形态 + KB 候选。"""
import sys

sys.path.insert(0, "tools/ke-core")
import ke_db  # noqa: E402
import ke_neo4j  # noqa: E402


def cols(table):
    rows = ke_db.psql_csv(
        "SELECT column_name, data_type FROM information_schema.columns "
        "WHERE table_name='%s' ORDER BY ordinal_position" % table)
    print("--- %s（%d 列）---" % (table, len(rows)))
    print("   " + ", ".join(r["column_name"] for r in rows))


print("=== wiki* / knowledge* 表 ===")
for r in ke_db.psql_csv(
        "SELECT table_name FROM information_schema.tables "
        "WHERE table_name LIKE 'wiki%' OR table_name LIKE 'knowledge%' OR table_name='folders' "
        "ORDER BY 1"):
    print("  ", r["table_name"])

for t in ("knowledge_bases", "wiki_pages", "wiki_folders", "wiki_page_revisions",
          "wiki_configs", "wiki_page_tags"):
    try:
        cols(t)
    except Exception as exc:  # noqa: BLE001
        print("--- %s 不可读：%s" % (t, str(exc)[:80]))

print("\n=== KB 候选（含'技术方案'/'领域'）===")
for r in ke_db.psql_csv(
        "SELECT id, name, tenant_id, created_at FROM knowledge_bases "
        "WHERE name LIKE '%技术方案%' OR name LIKE '%领域%' ORDER BY created_at"):
    print("  ", r)

print("\n=== Neo4j 实例节点（按 kb）===")
try:
    for r in ke_neo4j.query(
            "MATCH (n:BodhiInstance) RETURN n.kb AS kb, count(*) AS c ORDER BY c DESC LIMIT 10"):
        print("  ", r)
    print("--- 关系类型 ---")
    for r in ke_neo4j.query(
            "MATCH (a:BodhiInstance)-[r]->(b:BodhiInstance) WHERE a.kb = b.kb "
            "RETURN type(r) AS t, count(*) AS c ORDER BY c DESC LIMIT 15"):
        print("  ", r)
    print("--- 单节点属性样例 ---")
    for r in ke_neo4j.query(
            "MATCH (n:BodhiInstance) RETURN keys(n) AS k, n.kb AS kb, n.slug AS slug LIMIT 3"):
        print("  ", r)
except Exception as exc:  # noqa: BLE001
    print("  Neo4j 读失败：%s" % str(exc)[:160])
