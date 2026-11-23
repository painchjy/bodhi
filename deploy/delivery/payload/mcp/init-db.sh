#!/usr/bin/env bash
# diag / analytics 初始化（幂等）：./init-db.sh [diag|analytics|all]
set -euo pipefail
cd "$(dirname "$0")"
COMPONENT="${1:-all}"
case "$COMPONENT" in diag|analytics|all) ;; *) echo "用法：$0 [diag|analytics|all]" >&2; exit 2 ;; esac

if [ -f config/common.env ]; then set -a; . config/common.env; set +a; fi
if [ -f config/diag-init.env ]; then set -a; . config/diag-init.env; set +a; fi
: "${BODHI_DB_HOST:?config/common.env 缺少 BODHI_DB_HOST}"
: "${BODHI_DB_USER:?config/common.env 缺少 BODHI_DB_USER}"
: "${BODHI_DB_NAME:?config/common.env 缺少 BODHI_DB_NAME}"
: "${BODHI_DB_PASSWORD:?config/common.env 缺少 BODHI_DB_PASSWORD}"
BODHI_DB_PORT="${BODHI_DB_PORT:-5432}"

DIAG_SQL=(
  sql/diag/001_schema.sql
  sql/diag/002_tool_dimension_seed.sql
)
ANALYTICS_SQL=(
  sql/analytics/001_schema.sql
  sql/analytics/002_seed.sql
  sql/analytics/005_scenario_diag_seed.sql
  sql/analytics/006_diag_fact_mapping.sql
  sql/analytics/007_diag_metric_seed.sql
  sql/analytics/008_semantic_refactor.sql
  sql/analytics/009_semantic_views.sql
  sql/analytics/010_scenario_fact.sql
  sql/analytics/011_mapping_interface.sql
  sql/analytics/012_fact_table_join.sql
  sql/analytics/013_dimension_table.sql
)

run_sql() {
  local path="$1"
  echo "== apply $path"
  PGPASSWORD="$BODHI_DB_PASSWORD" psql \
    -h "$BODHI_DB_HOST" -p "$BODHI_DB_PORT" -U "$BODHI_DB_USER" -d "$BODHI_DB_NAME" \
    -v ON_ERROR_STOP=1 -f "$path"
}

apply() {
  local component="$1"
  if [ "$component" = "diag" ] || [ "$component" = "all" ]; then
    for f in "${DIAG_SQL[@]}"; do run_sql "$f"; done
  fi
  if [ "$component" = "analytics" ] || [ "$component" = "all" ]; then
    for f in "${ANALYTICS_SQL[@]}"; do run_sql "$f"; done
  fi
}

if command -v psql >/dev/null 2>&1; then
  apply "$COMPONENT"
else
  # 没有宿主机 psql：借 02 镜像执行（镜像内已装 postgresql-client）
  docker compose -f docker-compose.mcp.yml --profile mcp \
    run --rm --no-deps bodhi-mcp bash /app/init-db.sh "$COMPONENT"
fi
