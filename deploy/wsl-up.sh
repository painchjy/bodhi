#!/usr/bin/env bash
# =====================================================================
# WSL 一键环境：WeKnora 全栈 + 本体提取/MCP 保存工具
# ---------------------------------------------------------------------
#   bash /mnt/c/Users/PHJY/source/bodhi2/deploy/wsl-up.sh up       # 启动全部（默认）
#   bash /mnt/c/Users/PHJY/source/bodhi2/deploy/wsl-up.sh status   # 体检
#   bash /mnt/c/Users/PHJY/source/bodhi2/deploy/wsl-up.sh logs     # 跟随服务日志
#   bash /mnt/c/Users/PHJY/source/bodhi2/deploy/wsl-up.sh fe       # 切到自建前端镜像并验收
#   bash /mnt/c/Users/PHJY/source/bodhi2/deploy/wsl-up.sh fe-check # 只验收前端（含 /bodhi/ 反代）
#   bash /mnt/c/Users/PHJY/source/bodhi2/deploy/wsl-up.sh down     # 停止（数据卷保留）
#
# 组成：
#   docker.service            引擎
#   WeKnora（compose profile neo4j）：app / frontend / docreader / postgres / redis / neo4j
#   bodhi-mcp.service         本体知识提取 + MCP 保存工具（tools/ontology-mcp/server.py，端口 8765）
#
# 策略提醒（2026-09-19 方案 A，见 docs/weknora-fork.md §11）：上游镜像只当「重活供应商」
# （切片/向量/图谱存储/会话），**不再构建上游、不再抓 Go 代码**；自研一律 Python。
# 唯一自建过的镜像是 frontend（Vue 补丁，weknora-ui:bodhi2），只有在改 Vue 时才需重建，
# 重建走 deploy/weknora-fork/build_frontend.sh（本脚本不碰 npm/node）。
#
# 两条必须记住的坑（都已在本脚本里处理）：
#   1) app 容器重建后 IP 会变，而前端 nginx 只在启动时解析一次 → 必须重启 frontend，否则全 502；
#   2) bodhi-mcp 依赖 docker exec psql 读写库，所以要先等 postgres healthy 再启动服务。
# =====================================================================
set -uo pipefail

WK=${WK_DEPLOY_DIR:-/mnt/c/Users/PHJY/source/WeKnora}
BODHI=${BODHI_REPO_DIR:-/mnt/c/Users/PHJY/source/bodhi2}
COMPOSE=(-f docker-compose.yml)
[ -f "$BODHI/deploy/docker-compose.weknora.yml" ] && COMPOSE+=(-f "$BODHI/deploy/docker-compose.weknora.yml")
export BODHI_DEPLOY_DIR="$BODHI/deploy"
export BODHI_NEO4J_CYPHER_DIR="$BODHI/artifacts/neo4j"
KB_BIZ=dbc2528f-611b-48da-9a71-d7c93975adb4
KB_ONT=08810cbd-af86-48d1-bd25-3b2c338e3d68

step() { echo; echo "== $* =="; }
PG() { docker exec -e PGPASSWORD=postgres123!@# WeKnora-postgres psql -U postgres -d WeKnora -t -A -c "$1" 2>&1; }
MCP() { curl -s -m 15 -X POST http://localhost:8765/mcp -H 'Content-Type: application/json' \
        -H 'Accept: application/json' -d "$1" 2>&1; }

do_up() {
  step "1/6 docker 引擎"
  systemctl is-active docker >/dev/null 2>&1 || systemctl start docker
  sleep 2
  docker version --format 'engine {{.Server.Version}}' || { echo "!! docker 起不来"; exit 1; }

  step "2/6 WeKnora 全栈（profile neo4j）"
  cd "$WK" || exit 1
  docker compose "${COMPOSE[@]}" --profile neo4j up -d --no-build 2>&1 | tail -8

  step "3/6 等 postgres / app 就绪"
  for i in $(seq 1 30); do
    st_app=$(docker inspect -f '{{.State.Health.Status}}' WeKnora-app 2>/dev/null || echo none)
    st_pg=$(docker inspect -f '{{.State.Health.Status}}' WeKnora-postgres 2>/dev/null || echo none)
    [ "$st_pg" = healthy ] && [ "$st_app" = healthy ] && { echo "  app=$st_app postgres=$st_pg"; break; }
    echo "  ... app=$st_app postgres=$st_pg"; sleep 4
  done

  step "4/6 本体提取 / MCP 服务（systemd）"
  systemctl enable --now bodhi-mcp.service >/dev/null 2>&1
  sleep 3
  echo "  $(systemctl is-active bodhi-mcp.service) / $(systemctl show bodhi-mcp.service -p MainPID --value)"

  step "5/6 重启 frontend（nginx 缓存 app IP，必做）"
  docker restart WeKnora-frontend >/dev/null && sleep 6 && echo "  frontend restarted"

  do_status
}

do_status() {
  step "容器"
  docker compose -f "$WK/docker-compose.yml" ps --format '{{.Name}} | {{.State}} | {{.Status}}' 2>/dev/null | head -10

  step "systemd 服务"
  printf '  docker     : %s\n' "$(systemctl is-active docker)"
  printf '  bodhi-mcp  : %s (enabled=%s)\n' "$(systemctl is-active bodhi-mcp.service)" \
    "$(systemctl is-enabled bodhi-mcp.service 2>/dev/null)"

  step "HTTP"
  for u in /health /api/v1/agents; do
    printf '  %-22s -> ' "$u"
    curl -s -o /dev/null -m 8 -w '%{http_code}\n' "http://localhost$u"
  done
  printf '  MCP tools/list         -> '
  MCP '{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}' \
    | python3 -c 'import json,sys; d=json.load(sys.stdin); print(", ".join(t["name"] for t in d["result"]["tools"]))' 2>/dev/null || echo "(不可用)"

  step "前端（自建镜像，见 docs/weknora-fork.md §11）"
  printf '  image                    : %s\n' "$(docker inspect -f '{{.Config.Image}}' WeKnora-frontend 2>/dev/null || echo '(无容器)')"
  printf '  nginx location /bodhi/   : %s\n' "$(docker exec WeKnora-frontend sh -c "grep -c 'location /bodhi/' /etc/nginx/conf.d/default.conf" 2>/dev/null || echo '?')"
  printf '  补丁 chunk(BodhiGraphTab): %s\n' "$(docker exec WeKnora-frontend sh -c "grep -l 'BodhiGraphTab' /usr/share/nginx/html/assets/*.js 2>/dev/null | wc -l" 2>/dev/null || echo '?')"
  printf '  经前端反代 /bodhi/graph  : '
  curl -s -o /dev/null -m 25 -w '%{http_code} (%{size_download}B)\n' "http://localhost/bodhi/graph?kb_id=$KB_BIZ&model=bmm"
  printf '  经前端反代 /bodhi/pending: '
  curl -s -o /dev/null -m 20 -w '%{http_code} (%{size_download}B)\n' "http://localhost/bodhi/pending?kb_id=$KB_BIZ"

  step "知识库内容"
  printf '  企业知识     : pages=%s revisions=%s\n' \
    "$(PG "SELECT count(*) FROM wiki_pages WHERE knowledge_base_id='$KB_BIZ' AND deleted_at IS NULL;")" \
    "$(PG "SELECT count(*) FROM wiki_page_revisions WHERE knowledge_base_id='$KB_BIZ';")"
  printf '  企业本体模型 : pages=%s\n' \
    "$(PG "SELECT count(*) FROM wiki_pages WHERE knowledge_base_id='$KB_ONT' AND deleted_at IS NULL;")"
  echo
  echo "MARKER_WSL_UP_DONE"
}

case "${1:-up}" in
  up)     do_up ;;
  status) do_status ;;
  logs)   echo "== bodhi-mcp（Ctrl-C 退出） =="; journalctl -u bodhi-mcp -f -n 40 ;;
  # 前端自建镜像的切换与验收（只在改了 Vue 补丁后才需要 build_frontend.sh 重建）
  fe)     bash "$BODHI/deploy/weknora-fork/deploy_frontend.sh" deploy ;;
  fe-check) bash "$BODHI/deploy/weknora-fork/deploy_frontend.sh" check ;;
  down)   step "停止"; systemctl stop bodhi-mcp.service 2>/dev/null; \
          cd "$WK" && docker compose "${COMPOSE[@]}" --profile neo4j stop 2>&1 | tail -8; \
          echo "  （数据卷保留；再次 up 即可）" ;;
  *)      echo "用法: $0 [up|status|logs|fe|fe-check|down]"; exit 1 ;;
esac
