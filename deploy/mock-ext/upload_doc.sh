#!/usr/bin/env bash
# =====================================================================
# 上传一份评审文档给**模拟**的 IT 工作台（内网工作台未接通前的补位）
# ---------------------------------------------------------------------
#   bash deploy/mock-ext/upload_doc.sh <评审编号> <本地文件> [文档标题]
#
# 做的事：POST /files/<doc_id>?review_no=…&name=…&title=…  → 存盘 + docx 切节
#        → ext_review_flow(action=attach) 把文档绑到该评审编号的档案
# 依赖：curl；服务默认 http://127.0.0.1:8770（env IT_MCP_PUBLIC_BASE 可覆盖）
# =====================================================================
set -uo pipefail

REVIEW_NO="${1:-}"
FILE="${2:-}"
TITLE="${3:-}"
BASE="${IT_MCP_PUBLIC_BASE:-http://127.0.0.1:8770}"

if [ -z "$REVIEW_NO" ] || [ -z "$FILE" ]; then
  echo "用法：bash $0 <评审编号> <本地文件> [文档标题]" >&2
  exit 2
fi
[ -f "$FILE" ] || { echo "！文件不存在：$FILE" >&2; exit 2; }

NAME="$(basename "$FILE")"
STEM="${NAME%.*}"
DOC_ID="doc-$(printf '%s' "${REVIEW_NO}-${STEM}" | tr -c 'A-Za-z0-9._-' '-')"

echo "· 上传 $NAME → doc_id=$DOC_ID（评审编号 $REVIEW_NO）"
RESP="$(curl -sS -X POST "$BASE/files/$DOC_ID?review_no=$(printf '%s' "$REVIEW_NO" | sed 's/ /%20/g')&name=$(printf '%s' "$NAME" | sed 's/ /%20/g')&title=$(printf '%s' "${TITLE:-$STEM}" | sed 's/ /%20/g')&actor=upload-script" \
  --data-binary "@$FILE" -H 'Content-Type: application/octet-stream')" || {
  echo "！上传失败（服务没起？）" >&2; exit 1; }
echo "$RESP"

echo "· 绑定到评审编号档案"
curl -sS -X POST "$BASE/mcp" -H 'Content-Type: application/json' \
  -H 'Accept: application/json, text/event-stream' \
  -d "{\"jsonrpc\":\"2.0\",\"id\":1,\"method\":\"tools/call\",\"params\":{\"name\":\"ext_review_flow\",\"arguments\":{\"action\":\"attach\",\"review_no\":\"$REVIEW_NO\",\"doc_id\":\"$DOC_ID\",\"actor\":\"upload-script\"}}}"
echo
