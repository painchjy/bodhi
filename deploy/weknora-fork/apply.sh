#!/usr/bin/env bash
# =====================================================================
# 一键应用「本体知识提取 preset / 提示词」到运行中的 WeKnora（零重建）
#   bash /mnt/c/Users/PHJY/source/bodhi2/deploy/weknora-fork/apply.sh
#
# 步骤：
#   1) 生成配置（本体产物 -> deploy/weknora-fork/config/）
#   2) compose 重建 app 容器（挂载生效）
#   3) **重启 frontend** —— 关键！nginx 在启动时解析 `app` 并缓存 IP，
#      app 容器一重建 IP 就变，前端会全部 502（知识库管理空 / 智能体页空 /
#      建会话失败）。这一步不做就会踩上面那个坑（2026-09-19 实测）。
#   4) 自检：/health=200、/api/v1/*=401（通了但需登录）、容器内文件就位
# =====================================================================
set -uo pipefail

WK=${WK_DEPLOY_DIR:-/mnt/c/Users/PHJY/source/WeKnora}
BODHI=$(cd "$(dirname "$0")/../.." && pwd)
export BODHI_DEPLOY_DIR="$BODHI/deploy"
export BODHI_NEO4J_CYPHER_DIR="$BODHI/artifacts/neo4j"

echo "== 1/4 生成配置 =="
PY=$(command -v python3 || command -v python || true)
if [ -n "$PY" ]; then
  "$PY" "$BODHI/deploy/weknora-fork/gen_agent_config.py" || exit 1
else
  echo "!! WSL 里没有 python；请在 Windows 侧先运行："
  echo "   python deploy/weknora-fork/gen_agent_config.py"
fi

echo
echo "== 2/4 重建 app（挂载 preset + 提示词模板） =="
cd "$WK" || exit 1
docker compose -f docker-compose.yml \
  -f "$BODHI_DEPLOY_DIR/docker-compose.weknora.yml" \
  --profile neo4j --profile bodhi up -d --no-build 2>&1 | tail -8

echo
echo "== 3/4 重启 frontend（重新解析 app 的 IP；不重启会全 502） =="
docker restart WeKnora-frontend >/dev/null && echo "  frontend restarted"
sleep 8

echo
echo "== 4/4 自检 =="
for u in /health /api/v1/knowledge-bases /api/v1/agents/type-presets; do
  printf '  %-34s -> ' "$u"
  curl -s -o /dev/null -m 8 -w '%{http_code}\n' "http://localhost$u"
done
echo "  容器内 preset 数（应为 2）："
docker exec WeKnora-app sh -lc 'grep -c "ontology-extract-" /app/config/agent_type_presets.yaml' 2>&1 | sed 's/^/    /'
echo "  容器内模板数（应为 2）："
docker exec WeKnora-app sh -lc 'grep -c "ontology_extract_agent_" /app/config/prompt_templates/agent_system_prompt.yaml' 2>&1 | sed 's/^/    /'
echo
echo "MARKER_APPLY_DONE"
