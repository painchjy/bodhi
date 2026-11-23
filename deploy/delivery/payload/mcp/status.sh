#!/usr/bin/env bash
# 查看 02 包服务状态
set -euo pipefail
cd "$(dirname "$0")"
docker compose -f docker-compose.mcp.yml --profile mcp --profile diag --profile admin ps
