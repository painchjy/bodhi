#!/usr/bin/env bash
# 04-it-platform-mcp 安装/升级（**幂等**：重复跑不会重复建库/覆盖数据）
# ---------------------------------------------------------------------------
#   sudo bash install.sh [--prefix /opt/bodhi-ext] [--data /var/lib/bodhi-ext]
#
# 做的事：
#   1) 拷 `tools/domain-mcp/`（服务 + `lib/` 自带运行库，6 个 ke_domain_*/ke_fs）到 --prefix；
#   2) 建数据根 --data（业务数据；**不覆盖**已有内容）；
#   3) 装 systemd 单元（若系统有 systemd）并启用；
#   4) 跑一次 selfcheck.py（失败即返回非 0）。
#
# 边界（用户裁定 2026-10-28：**完全独立**）：
#   - 本服务**零第三方依赖、零数据库、零 docker**，不需要 02 包、不需要 WeKnora；
#   - 文档正文**不解析**（PDF/DOCX 由 bodhi-mcp 的 `ext_doc_parse` 拉原件再解析）；
#   - 本包**不含** `deploy/mock-ext/{docs,files,images}`（二进制/抓取产物）；
#   - 契约 `schema.json` 随本服务走（`tools/domain-mcp/it_platform/schema.json`）；
#   - 智能体的工具面/提示词**不由本包写**（用 `tools/delivery/apply_config.py`）。

set -euo pipefail
SRC="$(cd "$(dirname "$0")/../.." && pwd)"     # 仓库/包根
PREFIX="/opt/bodhi-ext"
DATA="/var/lib/bodhi-ext"
while [ $# -gt 0 ]; do
  case "$1" in
    --prefix) PREFIX="$2"; shift 2 ;;
    --data) DATA="$2"; shift 2 ;;
    *) echo "未知参数：$1"; exit 2 ;;
  esac
done
echo "源=$SRC 前缀=$PREFIX 数据=$DATA"

mkdir -p "$PREFIX" "$DATA"
rsync -a --delete "$SRC/tools/domain-mcp/" "$PREFIX/tools/domain-mcp/"
# **自带运行库**（`tools/domain-mcp/lib/`）——不依赖 02、不依赖 WeKnora
for f in ke_domain_store.py ke_domain_items.py ke_domain_docs.py ke_domain_flow.py \
         ke_domain_catalog.py ke_fs.py; do
  if [ ! -f "$PREFIX/tools/domain-mcp/lib/$f" ]; then
    echo "✗ 缺运行库 $PREFIX/tools/domain-mcp/lib/$f（04 包应自带这 6 个文件，请确认包完整）"
    exit 3
  fi
done
echo "运行库=自带（ke_domain_* + ke_fs，零 PG / 零 docker）"

mkdir -p "$PREFIX/deploy/mock-ext"
rsync -a --delete \
  --exclude 'docs/' --exclude 'files/' --exclude 'images/' --exclude 'state/' \
  "$SRC/deploy/mock-ext/" "$PREFIX/deploy/mock-ext/"
[ -f "$PREFIX/.env" ] || cp "$SRC/deploy/delivery/04-it-platform-mcp/.env.sample" "$PREFIX/.env"
sed -i "s#^BODHI_EXT_ROOT=.*#BODHI_EXT_ROOT=$DATA#" "$PREFIX/.env" || true

if command -v systemctl >/dev/null 2>&1; then
  install -m 0644 "$SRC/deploy/delivery/04-it-platform-mcp/it-platform-mcp.service" \
    /etc/systemd/system/it-platform-mcp.service
  systemctl daemon-reload
  systemctl enable --now it-platform-mcp.service
  sleep 2
  systemctl --no-pager --lines=5 status it-platform-mcp.service || true
else
  echo "（无 systemd）请手动：cd $PREFIX && set -a && . ./.env && set +a && python3 tools/domain-mcp/it_platform/server.py --http &"
fi

echo "== selfcheck"
python3 "$SRC/deploy/delivery/04-it-platform-mcp/selfcheck.py" --base "$PREFIX" --repo "$SRC"
echo "完成：契约 $(python3 -c "import json;print(len(json.load(open('$PREFIX/tools/domain-mcp/it_platform/schema.json'))['x_ext']))") 个工具"
