#!/usr/bin/env bash
# 只读体检：活库 / 已软删库 / 各活库页数（2026-10-12，重启后排障用）
PGP=$(grep -hoP '^(?:DB|POSTGRES)_PASSWORD=\K.*' /mnt/c/Users/PHJY/source/WeKnora/.env 2>/dev/null | head -1)
PSQL() { docker exec -e PGPASSWORD="$PGP" WeKnora-postgres psql -U postgres -d WeKnora -t -A -F' | ' -c "$1" 2>&1; }
echo "== 活库 =="
PSQL "SELECT id, name, to_char(created_at,'MM-DD HH24:MI') FROM knowledge_bases WHERE deleted_at IS NULL ORDER BY created_at;"
echo "== 已软删库数 =="
PSQL "SELECT count(*) FROM knowledge_bases WHERE deleted_at IS NOT NULL;"
echo "== 各活库页数（未软删）=="
PSQL "SELECT k.name, count(p.id) FROM knowledge_bases k LEFT JOIN wiki_pages p ON p.knowledge_base_id = k.id AND p.deleted_at IS NULL WHERE k.deleted_at IS NULL GROUP BY k.name ORDER BY 2 DESC;"
echo "== 概念库(afcd1c2e) 页数 =="
PSQL "SELECT count(*) FROM wiki_pages WHERE knowledge_base_id = 'afcd1c2e-0ff3-41c9-8dde-01f136b8a072' AND deleted_at IS NULL;"
