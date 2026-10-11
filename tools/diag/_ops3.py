"""Neo4j 实例形态 + knowledge_bases 约束 + wiki_page_issues（kb 迁移工具开发前置）。"""
import sys

sys.path.insert(0, "tools/ke-core")
import ke_db  # noqa: E402
import ke_neo4j  # noqa: E402

KB = "7efad3eb-762a-4981-9164-cd3b3a226bd4"        # 技术方案评审领域模型
KB2 = "030e2021-0ed7-4346-a435-47f8f0a311e7"       # 技术方案评审领域

print("=== PG 计数 ===")
for k in (KB, KB2):
    line = []
    for t in ("wiki_pages", "wiki_folders", "wiki_page_revisions", "wiki_page_issues"):
        try:
            c = ke_db.psql_csv("SELECT count(*) AS c FROM %s WHERE knowledge_base_id='%s'" % (t, k))[0]["c"]
        except Exception as exc:  # noqa: BLE001
            c = "ERR:" + str(exc)[:30]
        line.append("%s=%s" % (t, c))
    print("  %s  %s" % (k[:8], "  ".join(line)))

print("=== Neo4j: kb vs kb_id ===")
for prop in ("kb", "kb_id"):
    try:
        r = ke_neo4j.query("MATCH (n:BodhiInstance) WHERE n.%s = $kb RETURN count(*) AS c" % prop, {"kb": KB})
        print("  %-6s -> %s" % (prop, r))
    except Exception as exc:  # noqa: BLE001
        print("  %-6s ERR %s" % (prop, str(exc)[:60]))

print("=== 该库节点（标签 → 数）===")
for r in ke_neo4j.query("MATCH (n) WHERE n.kb_id = $kb RETURN labels(n) AS l, count(*) AS c ORDER BY c DESC",
                        {"kb": KB}):
    print("  ", r)
print("=== 库内关系类型 ===")
for r in ke_neo4j.query("MATCH (a)-[r]->(b) WHERE a.kb_id = $kb AND b.kb_id = $kb "
                        "RETURN type(r) AS t, count(*) AS c ORDER BY c DESC LIMIT 20", {"kb": KB}):
    print("  ", r)
print("=== 跨库边（该库→别库）===")
for r in ke_neo4j.query("MATCH (a)-[r]->(b) WHERE a.kb_id = $kb AND b.kb_id <> $kb "
                        "RETURN a.slug AS a, type(r) AS t, b.slug AS b, b.kb_id AS bkb LIMIT 5", {"kb": KB}):
    print("  ", r)

print("=== knowledge_bases 约束（NOT NULL / 默认）===")
for r in ke_db.psql_csv(
        "SELECT column_name, is_nullable, coalesce(column_default,'') AS dft "
        "FROM information_schema.columns WHERE table_name='knowledge_bases' "
        "AND (is_nullable='NO' OR column_default IS NOT NULL) ORDER BY ordinal_position"):
    print("  %-28s nullable=%-3s default=%s" % (r["column_name"], r["is_nullable"], r["dft"][:40]))

print("=== wiki_page_issues 列 ===")
rows = ke_db.psql_csv("SELECT column_name FROM information_schema.columns "
                      "WHERE table_name='wiki_page_issues' ORDER BY ordinal_position")
print("  ", ", ".join(r["column_name"] for r in rows))

print("=== wiki_pages 唯一约束 ===")
for r in ke_db.psql_csv("SELECT indexname, indexdef FROM pg_indexes WHERE tablename='wiki_pages'"):
    print("  ", r["indexname"], "|", r["indexdef"][:120])
