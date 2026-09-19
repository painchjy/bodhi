#!/usr/bin/env bash
# =====================================================================
# 改 TTL → 重新编译 → 更新「企业本体模型」知识库（一键）
#   bash deploy/weknora-fork/refresh_ontology_kb.sh
#
# 步骤：
#   1) 编译 TTL -> artifacts（ontology_index.json / light 提示词 / cypher）
#   2) 生成 wiki 页面清单（artifacts/weknora/ontology_wiki.jsonl）
#   3) 幂等投影进本体知识库（先删 last_edit_source='ontology-wiki' 的页面）
#   4) 打开 app 与 frontend（frontend 必须重启：nginx 缓存了 app 的 IP）
# =====================================================================
set -uo pipefail

BODHI=$(cd "$(dirname "$0")/../.." && pwd)
KB_ID=${ONTOLOGY_KB_ID:-08810cbd-af86-48d1-bd25-3b2c338e3d68}
PY=$(command -v python3 || command -v python || true)
[ -n "$PY" ] || { echo "需要 python3"; exit 1; }

echo "== 1/4 编译本体（TTL -> artifacts） =="
"$PY" "$BODHI/tools/ontology-compiler/compile.py" compile 2>&1 | tail -8

echo
echo "== 2/4 生成页面清单 =="
"$PY" "$BODHI/tools/ontology-extract/ontology_wiki.py" build

echo
echo "== 3/4 投影进知识库 $KB_ID =="
"$PY" "$BODHI/tools/ontology-extract/ontology_wiki.py" project --kb-id "$KB_ID"

echo
echo "== 4/4 重启 app + frontend =="
docker restart WeKnora-app >/dev/null && echo "  app restarted"
sleep 8
docker restart WeKnora-frontend >/dev/null && echo "  frontend restarted"
sleep 6
curl -s -o /dev/null -m 8 -w "  /health -> %{http_code}\n" http://localhost/health
echo "MARKER_ONTOLOGY_KB_REFRESH_DONE"
