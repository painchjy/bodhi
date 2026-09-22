#!/usr/bin/env bash
# =====================================================================
# 清理 WeKnora「自己生成」的 wiki 页，并重建目录树（bodhi2 运维脚本）
# ---------------------------------------------------------------------
# 为什么需要它：
#   知识库的 `indexing_strategy.wiki_enabled` **必须保持 true** —— 关掉它，后端的
#   `/wiki/pages`、`/wiki/folders` 会直接 400（error code 1000,
#   "Wiki feature is not enabled for this knowledge base"），界面就再也看不到 wiki 与图谱。
#   但保持 true 就意味着「上传文档后它会自动生成一批 wiki 页」，其中有些是它自己
#   理解出的页（last_edit_source = pipeline / agent），会与我们的本体提取结果混淆。
#   所以做法是：**事后清理** —— 只删它写的，不碰我们的（bodhi-onto-mcp）。
#
# 同时会重跑目录同步：这是一道**保险**（幂等）。
#   ⚠️ 更正（2026-09-19，用户实测）：目录**不会**因为删除文档而消失——之前"树空了"的真正
#   原因是**把 wiki 能力位关掉**了（后端 /wiki/pages、/wiki/folders 直接 400，
#   error code 1000 "Wiki feature is not enabled for this knowledge base"），
#   目录行其实一直在库里。所以这里的同步只是兜底，不是必需步骤。
#
# 索引页（slug=index）：**保留**，不参与清理（它是知识库的入口页，由上游流水线维护）。
#
# 用法：
#   bash deploy/weknora-fork/cleanup_auto_wiki.sh                  # 只看（默认，不改动）
#   bash deploy/weknora-fork/cleanup_auto_wiki.sh --apply          # 执行删除 + 重建目录
#   KB=企业知识 bash deploy/weknora-fork/cleanup_auto_wiki.sh --apply   # 指定知识库（名称或 UUID）
# =====================================================================
set -uo pipefail

BODHI=${BODHI_REPO_DIR:-/mnt/c/Users/PHJY/source/bodhi2}
KB=${KB:-dbc2528f-611b-48da-9a71-d7c93975adb4}
APPLY=0
[ "${1:-}" = "--apply" ] && APPLY=1
# 清理范围：它自己写的页 **除了** 索引页（slug=index，保留）
COND="deleted_at IS NULL AND last_edit_source IN ('pipeline','agent') AND slug <> 'index'"

Q() { docker exec -e PGPASSWORD="${BODHI_DB_PASSWORD:-$(grep -hoP '^(?:DB|POSTGRES)_PASSWORD=\K.*' "${BODHI_WEKNORA_DIR:-/mnt/c/Users/PHJY/source/WeKnora}/.env" 2>/dev/null | head -1)}" WeKnora-postgres psql -U postgres -d WeKnora -t -A -F' | ' -c "$1" 2>&1; }

# --- 0) 知识库名称 → UUID（支持直接给 UUID） -------------------------------
if [[ "$KB" =~ ^[0-9a-fA-F-]{36}$ ]]; then
  KB_ID="$KB"
else
  KB_ID=$(Q "SELECT id FROM knowledge_bases WHERE deleted_at IS NULL AND name = '$KB' LIMIT 1;" | head -1)
  [ -z "$KB_ID" ] && KB_ID=$(Q "SELECT id FROM knowledge_bases WHERE deleted_at IS NULL AND name ILIKE '%$KB%' LIMIT 1;" | head -1)
fi
[ -z "${KB_ID:-}" ] && { echo "!! 找不到知识库：$KB"; exit 1; }
echo "== 目标知识库：$KB → $KB_ID =="

echo
echo "== 1) 当前页数（按写作来源） =="
Q "SELECT coalesce(last_edit_source,'(null)') AS src, count(*) FROM wiki_pages WHERE knowledge_base_id='$KB_ID' AND deleted_at IS NULL GROUP BY 1 ORDER BY 2 DESC;"

echo
echo "== 2) 待清理的页（WeKnora 自己的：pipeline / agent；索引页保留） =="
Q "SELECT slug, coalesce(last_edit_source,'?'), created_at::timestamp(0) FROM wiki_pages WHERE knowledge_base_id='$KB_ID' AND $COND ORDER BY created_at DESC LIMIT 15;"
CNT=$(Q "SELECT count(*) FROM wiki_pages WHERE knowledge_base_id='$KB_ID' AND $COND;" | head -1)
echo "  合计：${CNT:-0} 条（我们的页不受影响：last_edit_source='bodhi-onto-mcp'）"

if [ "$APPLY" != "1" ]; then
  echo
  echo "== 默认只列出，未做任何改动。确认后加 --apply 执行 =="
  exit 0
fi

echo
echo "== 3) 执行清理（软删除，可回溯；索引页保留） =="
Q "UPDATE wiki_pages SET deleted_at = now(), updated_at = now() WHERE knowledge_base_id='$KB_ID' AND $COND;"
Q "SELECT coalesce(last_edit_source,'(null)') AS src, count(*) FROM wiki_pages WHERE knowledge_base_id='$KB_ID' AND deleted_at IS NULL GROUP BY 1 ORDER BY 2 DESC;"

echo
echo "== 4) 重建目录树（复用既有目录，逻辑幂等） =="
cd "$BODHI/tools/ontology-mcp" || exit 1
python3 sync_folders.py --kb-id "$KB_ID" --link-pages 2>&1 | tail -4

echo
echo "== 5) 完成。刷新前端即可看到：只有本体提取的页 + 两级目录 =="
