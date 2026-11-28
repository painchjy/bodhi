#!/usr/bin/env bash
set -euo pipefail

SRC="$(cd "$(dirname "$0")/../../.." && pwd)"
PREFIX="/opt/dsdf-mcp"
CONF="/etc/dsdf-mcp.env"
PYTHON="/opt/bodhi-venv/bin/python3"

if [ "$(id -u)" -ne 0 ]; then
  echo "请用 root 运行：sudo bash install.sh" >&2
  exit 1
fi
command -v rsync >/dev/null 2>&1 || { echo "缺少 rsync" >&2; exit 1; }
command -v python3 >/dev/null 2>&1 || { echo "缺少 python3" >&2; exit 1; }
command -v curl >/dev/null 2>&1 || { echo "缺少 curl" >&2; exit 1; }
if [ ! -x "$PYTHON" ]; then
  echo "缺少服务 Python 环境：$PYTHON" >&2
  exit 1
fi

if ! getent passwd dsdf-mcp >/dev/null; then
  useradd --system --home-dir /nonexistent --shell /usr/sbin/nologin dsdf-mcp
fi
install -d -m 0755 "$PREFIX"
install -d -m 0750 -o root -g dsdf-mcp "$PREFIX/lib"
install -d -m 0755 "$PREFIX/tools/dsdf-mcp"
rsync -a --delete "$SRC/tools/dsdf-mcp/" "$PREFIX/tools/dsdf-mcp/"

"$PYTHON" -m pip install --requirement "$PREFIX/tools/dsdf-mcp/requirements.txt"

if [ ! -f "$CONF" ]; then
  install -m 0600 "$SRC/deploy/delivery/dsdf-mcp/dsdf-mcp.env.example" "$CONF"
  echo "已生成 $CONF，请先填写 DWS 连接信息并确认 JDBC JAR 路径，再重新运行安装。"
  exit 2
fi
chmod 0600 "$CONF"

"$PYTHON" "$SRC/deploy/delivery/dsdf-mcp/selfcheck.py" --env-file "$CONF" \
  --python "$PYTHON"
install -m 0644 "$SRC/deploy/delivery/dsdf-mcp/dsdf-mcp.service" \
  /etc/systemd/system/dsdf-mcp.service
systemctl daemon-reload
systemctl enable --now dsdf-mcp.service

for _ in $(seq 1 20); do
  if curl -fsS "http://127.0.0.1:$(awk -F= '/^DSDF_MCP_PORT=/{print $2}' "$CONF")/health" >/dev/null 2>&1; then
    echo "OK: dsdf-mcp healthy"
    exit 0
  fi
  sleep 1
done

echo "WARN: health check failed; run journalctl -u dsdf-mcp -n 80" >&2
exit 1
