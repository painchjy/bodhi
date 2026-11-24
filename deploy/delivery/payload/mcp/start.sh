#!/usr/bin/env bash
# 选择性启动 02 包的三个常驻服务：mcp(8765) / diag(8771) / admin(8781)
set -euo pipefail
UNIT="${1:-all}"
case "$UNIT" in
  mcp) UNITS="bodhi2-mcp" ;;
  diag) UNITS="bodhi2-agent-diag" ;;
  admin) UNITS="bodhi2-analytics-admin" ;;
  all) UNITS="bodhi2-mcp bodhi2-agent-diag bodhi2-analytics-admin" ;;
  *) echo "用法：$0 [mcp|diag|admin|all]" >&2; exit 2 ;;
esac
for u in $UNITS; do systemctl start "$u"; done
systemctl --no-pager --full status $UNITS || true
