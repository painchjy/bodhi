#!/usr/bin/env bash
# =====================================================================
# 切到自建前端镜像 weknora-ui:bodhi2 并做端到端验收
# ---------------------------------------------------------------------
#   bash deploy/weknora-fork/deploy_frontend.sh          # 切换 + 验收
#   bash deploy/weknora-fork/deploy_frontend.sh check    # 只验收
#
# 前置：镜像已存在（bash deploy/weknora-fork/build_frontend.sh <src> 构建）
#       overlay 里 frontend.image 已指向该 tag、并挂载 default.conf.template
#       （见 deploy/docker-compose.weknora.yml）
# 坑：frontend 只有在一个 compose 文件里出现 image+build 时才用 image；
#     重建后 app 容器 IP 会变，nginx 启动时只解析一次 → 必须 docker restart。
# =====================================================================
set -uo pipefail

WK=${WK_DEPLOY_DIR:-/mnt/c/Users/PHJY/source/WeKnora}
BODHI=${BODHI_REPO_DIR:-/mnt/c/Users/PHJY/source/bodhi2}
OVL="$BODHI/deploy/docker-compose.weknora.yml"
export BODHI_DEPLOY_DIR="$BODHI/deploy"
export BODHI_NEO4J_CYPHER_DIR="$BODHI/artifacts/neo4j"
KB_BIZ=dbc2528f-611b-48da-9a71-d7c93975adb4
TAG=weknora-ui:bodhi2
step() { echo; echo "== $* =="; }

do_switch() {
  step "1/3 镜像检查"
  docker image inspect "$TAG" --format '  {{.RepoTags}} {{.Size}} bytes {{.Created}}' || {
    echo "!! 缺镜像 $TAG，先跑 build_frontend.sh"; exit 1; }

  step "2/3 重建 frontend（overlay：image + nginx 模板 + extra_hosts）"
  cd "$WK" || exit 1
  docker compose -f docker-compose.yml -f "$OVL" --profile neo4j up -d --no-build frontend 2>&1 | tail -6
  docker restart WeKnora-frontend >/dev/null && sleep 6 && echo "  frontend restarted（避免 nginx 缓存旧 app IP）"
}

do_check() {
  step "A. 容器里的 nginx 配置"
  docker exec WeKnora-frontend sh -c \
    "grep -n -A3 'location /bodhi/' /etc/nginx/conf.d/default.conf | head -8" 2>&1 | sed 's/^/  /'

  step "B. HTTP 端点"
  for u in / /health "/bodhi/graph?kb_id=$KB_BIZ&model=bmm" "/bodhi/view?kb_id=$KB_BIZ&model=bmm" "/bodhi/pending?kb_id=$KB_BIZ" ; do
    printf '  %-52s -> ' "$u"
    curl -s -o /dev/null -m 20 -w '%{http_code} %{size_download}B\n' "http://localhost$u"
  done

  step "C. 前端资源里是否有补丁（BodhiGraphTab / 本体 tab / 三级折叠）"
  docker exec WeKnora-frontend sh -c \
    "grep -o 'BodhiGraphTab' /usr/share/nginx/html/assets/*.js | head -2; \
     grep -o 'ontology-graph-tab' /usr/share/nginx/html/assets/*.js | head -2; \
     grep -o 'pending-merges' /usr/share/nginx/html/assets/*.js | head -1" 2>&1 | sed 's/^/  /'

  step "D. 经前端反代取本体图数据（证明 nginx → WSL:8765 通了）"
  curl -s -m 25 "http://localhost/bodhi/graph?kb_id=$KB_BIZ&model=bmm" \
    | python3 -c 'import json,sys; d=json.load(sys.stdin); print("  nodes=%s edges=%s" % (len(d.get("nodes",[])), len(d.get("edges",[]))))' 2>&1

  step "E. 待确认合并队列"
  curl -s -m 20 "http://localhost/bodhi/pending?kb_id=$KB_BIZ" \
    | python3 -c 'import json,sys; d=json.load(sys.stdin); print("  pending=%s" % len(d.get("pending",d.get("items",[]))))' 2>&1

  echo
  echo "浏览器验收："
  echo "  1) http://localhost/  → 知识库「企业知识」→ wiki 列表：应见【本体】【待确认合并】tab，"
  echo "     树为三级：模型 → 大类 → 类（如 BMM 业务动机模型 → 手段 → 操作性业务规则）"
  echo "  2) 知识库「企业知识」→ 顶部 tab 应有【本体图谱】（内嵌 /bodhi/view 的力导向图）"
  echo "  3) http://localhost:8765/graph?kb_id=$KB_BIZ&model=bmm （直连 MCP 服务的同一张图）"
  echo
  echo "MARKER_DEPLOY_FE_DONE"
}

case "${1:-deploy}" in
  deploy) do_switch; do_check ;;
  switch) do_switch ;;
  check)  do_check ;;
  *)      echo "用法: $0 [deploy|switch|check]"; exit 1 ;;
esac
