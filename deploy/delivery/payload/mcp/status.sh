#!/usr/bin/env bash
# 查看 02 包三个 systemd 服务与端口状态
set -euo pipefail
UNITS="bodhi2-mcp bodhi2-agent-diag bodhi2-analytics-admin"
systemctl --no-pager --full status $UNITS || true
echo
echo "== 端口"
for p in 8765 8771 8781; do
  if command -v ss >/dev/null 2>&1; then ss -ltnp "sport = :$p" 2>/dev/null || true; fi
done
echo
echo "== 日志"
ls -l /opt/bodhi2/logs/*.log 2>/dev/null || true
