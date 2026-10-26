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
#   bodhi-it-mcp.service      内网工作台接口（参考实现；tools/it-platform-mcp/server.py，端口 8770）
#                             —— **与 bodhi-mcp 并列**的第二个 MCP 服务（WeKnora 里 name=it_platform）
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

# --- 参考规范域名（内网直连；hosts 或 DNS 二选一）---------------------------
# 规则里的「参考规范」常是内网 URL（如 http://techdoc.sdc.bocomm.com/...）。
# `reference_lookup(allow_fetch=true)` 走**直连**（不假设代理）；WSL 每次启动会重生成
# /etc/hosts，所以映射必须在这里幂等维护 ✓（IP 可用 env `BODHI_REF_HOST_IP` 覆盖）。
BODHI_REF_HOST="${BODHI_REF_HOST:-techdoc.sdc.bocomm.com}"
BODHI_REF_HOST_IP="${BODHI_REF_HOST_IP:-}"
ensure_ref_host() {
  [ -n "$BODHI_REF_HOST" ] || return 0
  if getent hosts "$BODHI_REF_HOST" >/dev/null 2>&1; then return 0; fi
  [ -n "$BODHI_REF_HOST_IP" ] || return 0
  if ! grep -q "[[:space:]]$BODHI_REF_HOST\$" /etc/hosts 2>/dev/null; then
    echo "$BODHI_REF_HOST_IP $BODHI_REF_HOST" >> /etc/hosts 2>/dev/null \
      && echo "· 已补 /etc/hosts：$BODHI_REF_HOST_IP $BODHI_REF_HOST"
  fi
}
ensure_ref_host

# --- 前置自检（2026-10-12 新增）-------------------------------------------
# 现象：Windows/WSL 重启后跑本脚本报 `permission denied ... /var/run/docker.sock`。
# 原因：docker 组没有成员（套接字是 root:docker 660）。修法与判定都放这里，
#       宁可**明确失败**，也不要点着 root-only 命令静默失败后面还报"成功"。
SUDO=""
if [ "$(id -u)" -ne 0 ]; then
  # 探测必须用**白名单内的命令**（deploy/bodhi-ops.sudoers），否则 NOPASSWD 不匹配
  if sudo -n systemctl is-active bodhi-mcp.service >/dev/null 2>&1 \
     || sudo -n systemctl is-active bodhi-it-mcp.service >/dev/null 2>&1 \
     || sudo -n systemctl is-active docker >/dev/null 2>&1; then SUDO="sudo"
  else echo "！非 root 且无免密 sudo：root-only 步骤会失败 —— 建议 sudo bash $0 $*"; fi
fi
if docker info >/dev/null 2>&1; then
  :
else
  cat >&2 <<'EOT'
!! 连不上 docker（permission denied 或 daemon 未起）。修复：
   1) 加入 docker 组： sudo usermod -aG docker $USER      # 本机已给 painchjy 配好
   2) **重开 WSL 会话**（关终端重进）—— 组变更只对新登录会话生效
   3) daemon 未起： sudo systemctl start docker
   自检：docker info
EOT
  exit 1
fi

WK=${WK_DEPLOY_DIR:-/mnt/c/Users/PHJY/source/WeKnora}
BODHI=${BODHI_REPO_DIR:-/mnt/c/Users/PHJY/source/bodhi2}
COMPOSE=(-f docker-compose.yml)
[ -f "$BODHI/deploy/docker-compose.weknora.yml" ] && COMPOSE+=(-f "$BODHI/deploy/docker-compose.weknora.yml")
export BODHI_DEPLOY_DIR="$BODHI/deploy"
export BODHI_NEO4J_CYPHER_DIR="$BODHI/artifacts/neo4j"
# 知识库 id：允许 env 覆盖；默认**动态解析**（清理/重建后 id 会变，写死会误报，见 2026-10-12）
KB_BIZ="${BODHI_KB_BIZ:-}"
KB_ONT="${BODHI_KB_ONT:-}"

step() { echo; echo "== $* =="; }
# 数据库口令：env → WeKnora `.env`（**脚本里不写死口令**）
BODHI_DB_PASSWORD="${BODHI_DB_PASSWORD:-$(grep -hoP '^(?:DB|POSTGRES)_PASSWORD=\K.*' "/mnt/c/Users/PHJY/source/WeKnora/.env" 2>/dev/null | head -1)}"
PG() { docker exec -e PGPASSWORD="$BODHI_DB_PASSWORD" WeKnora-postgres psql -U postgres -d WeKnora -t -A -c "$1" 2>&1; }
MCP() { curl -s -m 15 -X POST http://localhost:8765/mcp -H 'Content-Type: application/json' \
        -H 'Accept: application/json' -d "$1" 2>&1; }
# 第二个 MCP 服务（内网工作台接口的参考实现；端口 8770）
MCP_IT() { curl -s -m 15 -X POST http://localhost:8770/mcp -H 'Content-Type: application/json' \
        -H 'Accept: application/json' -d "$1" 2>&1; }
# systemctl 包装：非 root 一律带 `-n`（免密），**绝不弹密码提示** —— 脚本是非交互的，
# 一旦 `sudo` 需要密码就会挂住；不在白名单里的服务会立刻失败并给出提示。
SCTL() { if [ -n "$SUDO" ]; then $SUDO -n systemctl "$@"; else systemctl "$@"; fi; }

do_up() {
  step "1/6 docker 引擎"
  systemctl is-active docker >/dev/null 2>&1 || $SUDO systemctl start docker
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

  step "4/6 本体 / 外部接口 MCP 服务（systemd，两个并列）"
  for s in bodhi-mcp.service bodhi-it-mcp.service; do
    SCTL enable "$s" >/dev/null 2>&1 || echo "  ! $s enable 需要 root（见 deploy/bodhi-ops.sudoers）"
    if SCTL restart "$s" 2>/dev/null; then
      echo "  $s restarted"
    else
      echo "  ! $s 重启失败：不在免密白名单 → sudo install -m 440 deploy/bodhi-ops.sudoers /etc/sudoers.d/bodhi-ops"
    fi
  done
  sleep 3
  printf '  bodhi-mcp   : %s / %s\n' "$(systemctl is-active bodhi-mcp.service)" \
    "$(systemctl show bodhi-mcp.service -p MainPID --value)"
  printf '  bodhi-it-mcp: %s / %s\n' "$(systemctl is-active bodhi-it-mcp.service)" \
    "$(systemctl show bodhi-it-mcp.service -p MainPID --value)"

  step "5/6 重启 frontend（nginx 缓存 app IP，必做）"
  docker restart WeKnora-frontend >/dev/null && sleep 6 && echo "  frontend restarted"

  do_status
}

do_status() {
  # 动态解析知识库 id（可用环境变量固定：BODHI_KB_BIZ / BODHI_KB_ONT）
  [ -n "$KB_BIZ" ] || KB_BIZ=$(PG "SELECT id FROM knowledge_bases WHERE deleted_at IS NULL AND name LIKE '%企业知识%' ORDER BY created_at LIMIT 1;" | tail -1)
  [ -n "$KB_ONT" ] || KB_ONT=$(PG "SELECT id FROM knowledge_bases WHERE deleted_at IS NULL AND name LIKE '%本体%' ORDER BY created_at LIMIT 1;" | tail -1)
  printf '  kb_biz=%s  kb_ont=%s\n' "${KB_BIZ:-?}" "${KB_ONT:-?}"

  step "容器"
  docker compose -f "$WK/docker-compose.yml" ps --format '{{.Name}} | {{.State}} | {{.Status}}' 2>/dev/null | head -10

  step "systemd 服务"
  printf '  docker     : %s\n' "$(systemctl is-active docker)"
  printf '  bodhi-mcp  : %s (enabled=%s)\n' "$(systemctl is-active bodhi-mcp.service)" \
    "$(systemctl is-enabled bodhi-mcp.service 2>/dev/null)"
  printf '  bodhi-it-mcp: %s (enabled=%s)\n' "$(systemctl is-active bodhi-it-mcp.service)" \
    "$(systemctl is-enabled bodhi-it-mcp.service 2>/dev/null)"

  step "HTTP"
  for u in /health /api/v1/agents; do
    printf '  %-22s -> ' "$u"
    curl -s -o /dev/null -m 8 -w '%{http_code}\n' "http://localhost$u"
  done
  printf '  MCP tools/list         -> '
  MCP '{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}' \
    | python3 -c 'import json,sys; d=json.load(sys.stdin); print(len(d["result"]["tools"]), "tools")' 2>/dev/null || echo "(不可用)"
  printf '  it-mcp tools/list      -> '
  MCP_IT '{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}' \
    | python3 -c 'import json,sys; d=json.load(sys.stdin); print(", ".join(t["name"] for t in d["result"]["tools"]))' 2>/dev/null || echo "(不可用)"

  step "前端（自建镜像，见 docs/weknora-fork.md §11）"
  printf '  image                    : %s\n' "$(docker inspect -f '{{.Config.Image}}' WeKnora-frontend 2>/dev/null || echo '(无容器)')"
  printf '  nginx location /bodhi/   : %s\n' "$(docker exec WeKnora-frontend sh -c "grep -c 'location /bodhi/' /etc/nginx/conf.d/default.conf" 2>/dev/null || echo '?')"
  printf '  补丁 chunk(BodhiGraphTab): %s\n' "$(docker exec WeKnora-frontend sh -c "grep -l 'BodhiGraphTab' /usr/share/nginx/html/assets/*.js 2>/dev/null | wc -l" 2>/dev/null || echo '?')"
  printf '  经前端反代 /bodhi/graph  : '
  curl -s -o /dev/null -m 25 -w '%{http_code} (%{size_download}B)\n' "http://localhost/bodhi/graph?kb_id=$KB_BIZ&model=bmm"
  printf '  经前端反代 /bodhi/pending（**已移除**，期望 410）: '
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
  logs)   echo "== bodhi-mcp / bodhi-it-mcp（Ctrl-C 退出） =="; \
          tail -n 40 -F /var/log/bodhi-mcp.log /var/log/bodhi-it-mcp.log \
            /var/log/bodhi-mcp.err.log /var/log/bodhi-it-mcp.err.log 2>/dev/null ;;
  # 前端自建镜像的切换与验收（只在改了 Vue 补丁后才需要 build_frontend.sh 重建）
  fe)     bash "$BODHI/deploy/weknora-fork/deploy_frontend.sh" deploy ;;
  fe-check) bash "$BODHI/deploy/weknora-fork/deploy_frontend.sh" check ;;
  down)   step "停止"; SCTL stop bodhi-it-mcp.service 2>/dev/null; \
          SCTL stop bodhi-mcp.service 2>/dev/null; \
          cd "$WK" && docker compose "${COMPOSE[@]}" --profile neo4j stop 2>&1 | tail -8; \
          echo "  （数据卷保留；再次 up 即可）" ;;
  *)      echo "用法: $0 [up|status|logs|fe|fe-check|down]"; exit 1 ;;
esac
