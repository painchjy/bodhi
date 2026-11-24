#!/usr/bin/env bash
# 选择性停止 02 包的三个常驻服务
set -euo pipefail
UNIT="${1:-all}"
case "$UNIT" in
  mcp) systemctl stop bodhi2-mcp ;;
  diag) systemctl stop bodhi2-agent-diag ;;
  admin) systemctl stop bodhi2-analytics-admin ;;
  all) systemctl stop bodhi2-mcp bodhi2-agent-diag bodhi2-analytics-admin ;;
  *) echo "用法：$0 [mcp|diag|admin|all]" >&2; exit 2 ;;
esac
