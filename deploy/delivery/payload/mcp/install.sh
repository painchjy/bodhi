#!/usr/bin/env bash
# bodhi2 MCP 服务一键部署（容器方式）
#   bash install.sh                # 检查环境 → 建 .env → 建镜像 → 起服务 → 自检
#   bash install.sh --no-build     # 跳过构建（镜像已存在）
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE"

echo "== 1/5 环境检查"
command -v docker >/dev/null || { echo "!! 需要 docker"; exit 1; }
docker compose version >/dev/null 2>&1 || { echo "!! 需要 docker compose v2"; exit 1; }
[ -f ../bodhi2-mcp.tar.gz ] || true
echo "   docker $(docker version --format '{{.Server.Version}}')"

echo "== 2/5 配置 .env"
if [ ! -f .env ]; then
  cp .env.example .env
  echo "   已生成 .env —— **请先改 BODHI_DB_PASSWORD / BODHI_DB_HOST**，然后重新执行本脚本"
  exit 0
fi
grep -q "CHANGE_ME" .env && { echo "!! .env 里还有 CHANGE_ME，请先改掉"; exit 1; }
set -a; . ./.env; set +a
echo "   目标库：${BODHI_DB_USER}@${BODHI_DB_HOST}:${BODHI_DB_PORT}/${BODHI_DB_NAME}"

echo "== 3/5 网络确认（MCP 必须与 WeKnora app 同网络，才能用容器 DNS 作为 MCP URL）"
NET=${WEKNORA_NETWORK:-weknora-network}
if ! docker network inspect "$NET" >/dev/null 2>&1; then
  echo "!! 找不到网络 $NET：先 docker network ls 看你们的实际网络名，再改 .env 的 WEKNORA_NETWORK"
  exit 1
fi
echo "   网络 $NET OK"

if [ "${1:-}" != "--no-build" ]; then
  echo "== 4/5 构建镜像"
  docker compose -f docker-compose.mcp.yml build
else
  echo "== 4/5 跳过构建"
fi

echo "== 5/5 启动 + 自检"
docker compose -f docker-compose.mcp.yml up -d
sleep 6
docker logs --tail 5 bodhi2-mcp || true
python3 selfcheck.py --url http://127.0.0.1:8765/mcp || {
  echo "!! 自检未通过：看 docker logs bodhi2-mcp，并对照 ../TROUBLESHOOTING.md"
  exit 1; }

cat <<'EOD'

== 下一步 ==
1) 打开 WeKnora → 平台 → MCP 服务 → 新建，URL 填： http://bodhi2-mcp:8765/mcp
   （容器名是 bodhi2-mcp；同网络 DNS 可直接解析，且能绕开 SSRF 白名单）
2) 按 ../MCP-SERVER.md §6 注册（UI 或 SQL 都行）
3) 继续交付手册的主流程：本体模型知识库导入 → 业务库配置 → 前端替换 → 智能体注册
EOD
