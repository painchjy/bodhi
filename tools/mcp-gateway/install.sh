#!/usr/bin/env bash
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
DEST="${MCP_GATEWAY_DIR:-/opt/bodhi-mcp-gateway}"
CONF="${MCP_GATEWAY_CONFIG:-/etc/bodhi-mcp-gateway.json}"
UNIT="/etc/systemd/system/bodhi-mcp-gateway.service"

if [ "$(id -u)" -ne 0 ]; then
  echo "请用 root 运行：sudo bash install.sh" >&2
  exit 1
fi

install -d -m 0755 "$DEST"
install -m 0644 "$HERE/server.py" "$HERE/gateway.py" "$HERE/README.md" "$HERE/VERSION" "$DEST/"
install -m 0644 "$HERE/bodhi-mcp-gateway.service" "$UNIT"

if [ ! -f "$CONF" ]; then
  install -m 0600 "$HERE/gateway.json.example" "$CONF"
  echo "已生成配置文件：$CONF"
  echo "请先编辑 upstream_url / inject_args，再执行：systemctl restart bodhi-mcp-gateway"
else
  echo "保留已有配置：$CONF"
fi

systemctl daemon-reload
systemctl enable --now bodhi-mcp-gateway

PORT="$(python3 - "$CONF" <<'PY'
import json, sys
try:
    print(int(json.load(open(sys.argv[1], encoding="utf-8")).get("port", 8772)))
except Exception:
    print(8772)
PY
)"

for _ in $(seq 1 20); do
  if curl -fsS "http://127.0.0.1:${PORT}/health" >/dev/null 2>&1; then
    echo "OK: http://127.0.0.1:${PORT}/health"
    exit 0
  fi
  sleep 1
done

echo "WARN: 健康检查未通过，查看：journalctl -u bodhi-mcp-gateway -n 80" >&2
exit 1
