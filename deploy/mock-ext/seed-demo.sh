#!/usr/bin/env bash
# 演示/联调数据灌入（**不进发布包**；正常交付不需要跑）
# ---------------------------------------------------------------------------
#   bash deploy/mock-ext/seed-demo.sh [--with-docs]
#
# 做的事：
#   1) 校验种子必需文件（allowlist/denylist）；
#   2) 建空台账/流程样例（缺才建，**不覆盖**现场数据 ✓）；
#   3) --with-docs：把样例附件按「演示编号」灌进外部工作台（调 upload_doc.sh）。
#
# 口径：**只补缺失**（幂等）；业务数据一律走工具接口，脚本不直接写库。
set -euo pipefail
cd "$(dirname "$0")/it-platform"

WITH_DOCS=0
[ "${1:-}" = "--with-docs" ] && WITH_DOCS=1

for f in allowlist.json denylist.json; do
  [ -s "$f" ] || { echo "✗ 缺种子：$f"; exit 1; }
done
echo "✓ 种子文件齐：allowlist.json / denylist.json"

mkdir -p issues flows
DEMO_NO="202610-000001"
if [ ! -f "flows/${DEMO_NO}.json" ]; then
  printf '{\n  "review_no": "%s",\n  "title": "演示评审",\n  "round": 1,\n  "status": "open",\n  "docs": [],\n  "rev": 1\n}\n' "$DEMO_NO" > "flows/${DEMO_NO}.json"
  echo "✓ 建流程样例 flows/${DEMO_NO}.json"
else
  echo "· 流程样例已存在（不覆盖）"
fi
if [ ! -f "issues/${DEMO_NO}.json" ]; then
  printf '{\n  "review_no": "%s",\n  "rev": 1,\n  "items": []\n}\n' "$DEMO_NO" > "issues/${DEMO_NO}.json"
  echo "✓ 建台账样例 issues/${DEMO_NO}.json（0 条）"
else
  echo "· 台账样例已存在（不覆盖）"
fi

if [ "$WITH_DOCS" = "1" ]; then
  for f in files/*.pdf files/*.docx; do
    [ -e "$f" ] || continue
    bash ../upload_doc.sh "$DEMO_NO" "$f" "$(basename "$f")"
  done
fi
echo "完成（容器根：${BODHI_EXT_ROOT:-默认 state/}）"
