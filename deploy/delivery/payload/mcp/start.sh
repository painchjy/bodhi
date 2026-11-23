#!/usr/bin/env bash
# 选择性启动 02 包的三个常驻服务：mcp(8765) / diag(8771) / admin(8781)
set -euo pipefail
cd "$(dirname "$0")"
COMPONENT="${1:-all}"
COMPOSE=(docker compose -f docker-compose.mcp.yml)
case "$COMPONENT" in
  mcp|diag|admin) "${COMPOSE[@]}" --profile "$COMPONENT" up -d ;;
  all) "${COMPOSE[@]}" --profile mcp --profile diag --profile admin up -d ;;
  *) echo "用法：$0 [mcp|diag|admin|all]" >&2; exit 2 ;;
esac
"${COMPOSE[@]}" --profile mcp --profile diag --profile admin ps
