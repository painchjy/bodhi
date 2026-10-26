#!/usr/bin/env bash
# 04-it-platform-mcp 安装/升级（**幂等**：重复跑不会重复建库/覆盖数据）
# ---------------------------------------------------------------------------
#   sudo bash install.sh [--prefix /opt/bodhi-ext] [--data /var/lib/bodhi-ext]
#
# 做的事：
#   1) 拷 `tools/`（it-platform-mcp + ke-core）与种子 `deploy/mock-ext/` 到 --prefix；
#   2) 建数据根 --data（业务数据；**不覆盖**已有内容）；
#   3) 装 systemd 单元（若系统有 systemd）并启用；
#   4) 跑一次 selfcheck.py（失败即返回非 0）。
#
# 边界（用户裁定 2026-10-26）：
#   - 本包**不含** `deploy/mock-ext/{docs,files,images}`（二进制/抓取产物）；
#   - 契约 `schema.json` 随本服务走（`tools/it-platform-mcp/schema.json`），种子目录只留副本供 bootstrap；
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
rsync -a --delete "$SRC/tools/it-platform-mcp/" "$PREFIX/tools/it-platform-mcp/"
rsync -a --delete "$SRC/tools/ke-core/" "$PREFIX/tools/ke-core/"
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
  echo "（无 systemd）请手动：cd $PREFIX && set -a && . ./.env && set +a && python3 tools/it-platform-mcp/server.py --http &"
fi

echo "== selfcheck"
python3 "$SRC/deploy/delivery/04-it-platform-mcp/selfcheck.py" --base "$PREFIX" --repo "$SRC"
echo "完成：契约 $(python3 -c "import json;print(len(json.load(open('$PREFIX/tools/it-platform-mcp/schema.json'))['x_ext']))") 个工具"
