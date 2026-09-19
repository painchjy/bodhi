#!/usr/bin/env bash
# =====================================================================
# 批量删除 wiki 页（bodhi2 运维脚本）—— 供「列表/树多选后删除」使用
# ---------------------------------------------------------------------
# 用法：
#   # 0) 先列出候选（打印 slug，便于多选后复制）
#   bash deploy/weknora-fork/delete_wiki_pages.sh --list [--type bmm:Goal] [--prefix bmm/goal/]
#
#   # 1) 按 slug 列表删除（UI 多选后把 slug 逗号分隔传进来即可）
#   bash deploy/weknora-fork/delete_wiki_pages.sh --slugs "bmm/goal/a,bmm/goal/b" --apply
#
#   # 2) 按类型 / 前缀批量删除
#   bash deploy/weknora-fork/delete_wiki_pages.sh --type bmm:Goal --apply
#   bash deploy/weknora-fork/delete_wiki_pages.sh --prefix bmm/goal/ --apply
#
#   # 3) 整库清空我们抽取的页（谨慎！index 与上游页始终保留）
#   bash deploy/weknora-fork/delete_wiki_pages.sh --ours --apply
#
# 安全约定：
#   - **默认 dry-run**，只有加 --apply 才写库；
#   - 软删除（deleted_at=now()），可回溯；**永不动 slug='index'**；
#   - 删除后自动同步目录树（复用既有目录、幂等），避免树上留下空目录；
#   - 不带任何选择条件时会拒绝执行（防止误清库）。
# =====================================================================
set -uo pipefail

BODHI=${BODHI_REPO_DIR:-/mnt/c/Users/PHJY/source/bodhi2}
KB=${KB:-dbc2528f-611b-48da-9a71-d7c93975adb4}
MODE=""; SLUGS=""; TYPE=""; PREFIX=""; APPLY=0

while [ $# -gt 0 ]; do
  case "$1" in
    --list)   MODE="list" ;;
    --slugs)  MODE="delete"; SLUGS="${2:-}"; shift ;;
    --type)   MODE="delete"; TYPE="${2:-}"; shift ;;
    --prefix) MODE="delete"; PREFIX="${2:-}"; shift ;;
    --ours)   MODE="delete"; OURS=1 ;;
    --apply)  APPLY=1 ;;
    *) echo "未知参数：$1"; exit 1 ;;
  esac
  shift
done
[ -z "$MODE" ] && { echo "用法见脚本头部注释（--list / --slugs / --type / --prefix / --ours）"; exit 1; }

Q() { docker exec -e PGPASSWORD='postgres123!@#' WeKnora-postgres psql -U postgres -d WeKnora -t -A -F' | ' -c "$1" 2>&1; }

# KB 名称 → UUID
if [[ "$KB" =~ ^[0-9a-fA-F-]{36}$ ]]; then KB_ID="$KB"; else
  KB_ID=$(Q "SELECT id FROM knowledge_bases WHERE deleted_at IS NULL AND name ILIKE '%$KB%' LIMIT 1;" | head -1)
fi
[ -z "${KB_ID:-}" ] && { echo "!! 找不到知识库：$KB"; exit 1; }
echo "== 目标知识库：$KB → $KB_ID =="

# 组装选择条件（永远排除 index）
COND="knowledge_base_id='$KB_ID' AND deleted_at IS NULL AND slug <> 'index'"
if [ -n "${SLUGS:-}" ]; then
  LIST=$(printf "'%s'," $SLUGS | sed "s/,/'/g" | sed "s/'$//")
  # 把 a,b 变成 'a','b'
  LIST=$(echo "$SLUGS" | awk -F',' '{for(i=1;i<=NF;i++){printf "%s'\''%s'\''", (i>1?",":""), $i}}')
  COND="$COND AND slug IN ($LIST)"
elif [ -n "$TYPE" ]; then
  COND="$COND AND page_type = '$TYPE'"
elif [ -n "$PREFIX" ]; then
  COND="$COND AND slug LIKE '$PREFIX%'"
elif [ "${OURS:-0}" = "1" ]; then
  COND="$COND AND last_edit_source = 'bodhi-onto-mcp'"
else
  echo "!! 没有选择条件（用 --list 看候选，再 --slugs/--type/--prefix/--ours）"; exit 1
fi

echo
echo "== 命中页（最多 30 条） =="
Q "SELECT slug, page_type, coalesce(last_edit_source,'?'), version FROM wiki_pages WHERE $COND ORDER BY slug LIMIT 30;"
CNT=$(Q "SELECT count(*) FROM wiki_pages WHERE $COND;" | head -1)
echo "  合计：${CNT:-0} 条"

if [ "$MODE" = "list" ] || [ "$APPLY" != "1" ]; then
  echo
  echo "== 未做改动。确认后加 --apply 执行软删除（会自动同步目录树） =="
  exit 0
fi

echo
echo "== 执行软删除 =="
Q "UPDATE wiki_pages SET deleted_at = now(), updated_at = now() WHERE $COND;"
Q "SELECT '剩余页数（按来源）：' AS x, coalesce(last_edit_source,'?') AS src, count(*) FROM wiki_pages WHERE knowledge_base_id='$KB_ID' AND deleted_at IS NULL GROUP BY 2;"

echo
echo "== 同步目录树（清掉空目录、复用既有目录） =="
cd "$BODHI/tools/ontology-mcp" || exit 1
python3 sync_folders.py --kb-id "$KB_ID" --link-pages 2>&1 | tail -4

echo
echo "== 完成。刷新前端即可看到结果 =="
