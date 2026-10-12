#!/usr/bin/env bash
# 只读快检（秒级）：活库 + 跨库同名 slug（= context scan 的 R0 信号，不含 R1/R2 相似度）
PGP=$(grep -hoP '^(?:DB|POSTGRES)_PASSWORD=\K.*' /mnt/c/Users/PHJY/source/WeKnora/.env 2>/dev/null | head -1)
PSQL() { docker exec -e PGPASSWORD="$PGP" WeKnora-postgres psql -U postgres -d WeKnora -t -A -F' | ' -c "$1" 2>&1; }
echo "== 活库 =="
PSQL "SELECT id, name FROM knowledge_bases WHERE deleted_at IS NULL ORDER BY created_at;"
echo "== 跨库同名 slug（活库；排除 本体库/概念库）=="
PSQL "SELECT lower(regexp_replace(p.slug,'\s+','','g')) AS k, count(DISTINCT p.knowledge_base_id) AS ctxs, string_agg(DISTINCT kb.name, ' + ') FROM wiki_pages p JOIN knowledge_bases kb ON kb.id = p.knowledge_base_id AND kb.deleted_at IS NULL WHERE p.deleted_at IS NULL AND p.knowledge_base_id NOT IN ('08810cbd-af86-48d1-bd25-3b2c338e3d68','afcd1c2e-0ff3-41c9-8dde-01f136b8a072') GROUP BY 1 HAVING count(DISTINCT p.knowledge_base_id) >= 2 ORDER BY 2 DESC, 1 LIMIT 40;"
echo "== 同名词组总数 =="
PSQL "SELECT count(*) FROM (SELECT 1 FROM wiki_pages p JOIN knowledge_bases kb ON kb.id = p.knowledge_base_id AND kb.deleted_at IS NULL WHERE p.deleted_at IS NULL AND p.knowledge_base_id NOT IN ('08810cbd-af86-48d1-bd25-3b2c338e3d68','afcd1c2e-0ff3-41c9-8dde-01f136b8a072') GROUP BY lower(regexp_replace(p.slug,'\s+','','g')) HAVING count(DISTINCT p.knowledge_base_id) >= 2) t;"
echo "== 概念库页 =="
PSQL "SELECT slug || ' v' || version FROM wiki_pages WHERE knowledge_base_id = 'afcd1c2e-0ff3-41c9-8dde-01f136b8a072' AND deleted_at IS NULL ORDER BY slug;"
