#!/usr/bin/env bash
# 选择性停止 02 包服务
set -euo pipefail
cd "$(dirname "$0")"
COMPONENT="${1:-all}"
COMPOSE=(docker compose -f docker-compose.mcp.yml)
case "$COMPONENT" in
  mcp) "${COMPOSE[@]}" stop bodhi-mcp ;;
  diag) "${COMPOSE[@]}" stop bodhi-agent-diag ;;
  admin) "${COMPOSE[@]}" stop bodhi-analytics-admin ;;
  all) "${COMPOSE[@]}" --profile mcp --profile diag --profile admin stop ;;
  *) echo "用法：$0 [mcp|diag|admin|all]" >&2; exit 2 ;;
esac
