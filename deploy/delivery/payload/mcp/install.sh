#!/usr/bin/env bash
# bodhi2 02 运行时完整包安装器（同一入口安装三个服务 + 初始化脚本）
#   bash install.sh                      # 安装全部组件（容器）
#   bash install.sh --components mcp,diag,admin
#   bash install.sh --no-build           # 镜像已存在
#   bash install.sh --init-db            # 安装后执行 diag+analytics 初始化
#
# 组件：mcp=8765 / diag=8771 / admin=8781；diag-init 由 init-db.sh 单独执行。
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE"

COMPONENTS="mcp,diag,admin"
NO_BUILD=0
INIT_DB=0
while [ $# -gt 0 ]; do
  case "$1" in
    --components) COMPONENTS="$2"; shift 2 ;;
    --components=*) COMPONENTS="${1#*=}"; shift ;;
    --no-build) NO_BUILD=1; shift ;;
    --init-db) INIT_DB=1; shift ;;
    -h|--help) sed -n '2,12p' "$0"; exit 0 ;;
    *) echo "未知参数：$1" >&2; exit 2 ;;
  esac
done

echo "== 1/6 环境检查"
command -v docker >/dev/null || { echo "!! 需要 docker"; exit 1; }
docker compose version >/dev/null 2>&1 || { echo "!! 需要 docker compose v2"; exit 1; }
[ -d tools ] && [ -d artifacts ] && [ -d sql ] || {
  echo "!! 包不完整：应含 tools/ artifacts/ sql/"; exit 1; }
echo "   docker $(docker version --format '{{.Server.Version}}')"

echo "== 2/6 生成独立组件配置"
CREATED=0
for name in common mcp agent-diag analytics-admin diag-init; do
  if [ ! -f "config/$name.env" ]; then
    cp "config/$name.env.example" "config/$name.env"
    CREATED=1
  fi
done
if [ "$CREATED" = 1 ]; then
  echo "   已生成 config/*.env；请先改 config/common.env 的 BODHI_DB_*，再重跑本脚本"
  exit 0
fi
grep -q "CHANGE_ME" config/common.env && {
  echo "!! config/common.env 里还有 CHANGE_ME，请先改掉"; exit 1; }
set -a; . config/common.env; set +a
echo "   目标库：${BODHI_DB_USER}@${BODHI_DB_HOST}:${BODHI_DB_PORT}/${BODHI_DB_NAME}"

echo "== 3/6 网络确认"
NET=${WEKNORA_NETWORK:-weknora-network}
if ! docker network inspect "$NET" >/dev/null 2>&1; then
  echo "!! 找不到网络 $NET：先 docker network ls 看实际网络名，再改 config/common.env 的 WEKNORA_NETWORK"
  exit 1
fi
echo "   网络 $NET OK"

if [ "$NO_BUILD" = 0 ]; then
  echo "== 4/6 构建镜像"
  docker compose -f docker-compose.mcp.yml build
else
  echo "== 4/6 跳过构建"
fi

if [ "$INIT_DB" = 1 ]; then
  echo "== 5/6 初始化 diag + analytics"
  bash init-db.sh all
else
  echo "== 5/6 跳过 DB 初始化（建议：./init-db.sh all）"
fi

echo "== 6/6 启动选中组件 + 自检"
PROFILES=()
OLD_IFS="$IFS"; IFS=','; for c in $COMPONENTS; do
  case "$c" in
    mcp|diag|admin) PROFILES+=(--profile "$c") ;;
    *) echo "!! 未知组件：$c（可选 mcp,diag,admin）" >&2; exit 2 ;;
  esac
done; IFS="$OLD_IFS"
docker compose -f docker-compose.mcp.yml "${PROFILES[@]}" up -d
sleep 6

if [[ ",$COMPONENTS," == *",mcp,"* ]]; then
  python3 selfcheck.py --url http://127.0.0.1:8765/mcp || {
    echo "!! bodhi-mcp 自检未通过：看 docker logs bodhi-mcp"; exit 1; }
fi
if [[ ",$COMPONENTS," == *",diag,"* ]]; then
  curl -fsS http://127.0.0.1:8771/ >/dev/null || {
    echo "!! agent_diag 未通过：看 docker logs bodhi-agent-diag"; exit 1; }
  echo "   agent_diag OK（http://127.0.0.1:8771/mcp）"
fi
if [[ ",$COMPONENTS," == *",admin,"* ]]; then
  curl -fsS http://127.0.0.1:8781/ >/dev/null || {
    echo "!! analytics-admin 未通过：看 docker logs bodhi-analytics-admin"; exit 1; }
  echo "   analytics-admin OK（http://<host>:8781/）"
fi

cat <<'EOD'

== 下一步 ==
1) WeKnora → 平台 → MCP 服务：
   - bodhi_ontology：http://bodhi-mcp:8765/mcp
   - agent_diag：http://bodhi-agent-diag:8771/mcp
2) 把 agent_diag 挂到智能体的 mcp_services；analytics 内部数据服务行仍用 svc_postgresql。
3) 读包内手册：
   - MCP-SERVER.md     组件矩阵/安装/启停
   - DIAG-CONFIG.md    diag schema、agent_diag 8771、只读账号
   - ANALYTICS-CONFIG.md analytics schema、语义层、数据初始化
   - ANALYTICS-ADMIN.md  8781 管理台/映射维护
EOD
