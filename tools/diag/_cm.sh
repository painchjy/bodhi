#!/usr/bin/env bash
# 只读：dump 概念库（afcd1c2e）页面清单与正文，用于把悬空映射行标 detached
PGP=$(grep -hoP '^(?:DB|POSTGRES)_PASSWORD=\K.*' /mnt/c/Users/PHJY/source/WeKnora/.env 2>/dev/null | head -1)
PSQL() { docker exec -e PGPASSWORD="$PGP" WeKnora-postgres psql -U postgres -d WeKnora -t -A -F'|' -c "$1" 2>&1; }
KBC=afcd1c2e-0ff3-41c9-8dde-01f136b8a072
echo "== 概念库页清单 =="
PSQL "SELECT slug || ' v' || version || ' len=' || coalesce(length(content),0) || ' title=' || coalesce(title,'') FROM wiki_pages WHERE knowledge_base_id='$KBC' AND deleted_at IS NULL ORDER BY slug;"
echo "== 正文（前 2600 字）=="
PSQL "SELECT '### ' || slug || ' (v' || version || ')' || chr(10) || substring(content,1,2600) FROM wiki_pages WHERE knowledge_base_id='$KBC' AND deleted_at IS NULL ORDER BY slug;"
