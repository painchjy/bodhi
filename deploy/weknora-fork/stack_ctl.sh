#!/usr/bin/env bash
# =====================================================================
# WeKnora（bodhi2 版）整栈：起 / 停 / 体检 / 盯盘
# ---------------------------------------------------------------------
#   bash deploy/weknora-fork/stack_ctl.sh up       # 拉起整栈（含 neo4j + 等健康 + 重启前端 nginx）
#   bash deploy/weknora-fork/stack_ctl.sh status   # 一次完整体检（人可读；异常会给出下一步建议）
#   bash deploy/weknora-fork/stack_ctl.sh watch    # 每 15 秒一行，用来看"是不是在反复重启"
#   bash deploy/weknora-fork/stack_ctl.sh stop     # 停容器（保留数据卷）
#
# 为什么要这个脚本（2026-09-28 的坑）
# -----------------------------------
# 1) dockerd 反复被重启 → 全栈容器反复被杀 → 浏览器在"重启窗口"里访问 `http://localhost/`
#    会连不上，而稍后 `http://localhost/platform/creatChat` 又能用（同一个 origin，只是时机不同）。
#    本脚本的 status 会直接报出「近 10 分钟 dockerd 启动次数」来暴露这个循环。
#    实测根因：WeKnora-app 的**技能沙箱 Docker 后端**（system_settings.sandbox.docker_enabled=true
#    + 挂宿主 /var/run/docker.sock）在启动时"应用 docker 后端设置"会 systemctl restart docker。
#    缓解：/etc/docker/daemon.json 里 `"live-restore": true`（dockerd 重启不再杀容器 → 循环被打断）。
# 2) 重建 app 容器后它的 IP 会变，而 nginx 只在启动时解析一次 → 必须 `docker restart WeKnora-frontend`
#    （本脚本 up 之后会自动做；更彻底的办法见 deploy/docker-compose.weknora.yml 的 resolver 变量式 proxy_pass）
# 3) WSL 空闲会被回收（.wslconfig 未设 vmIdleTimeout）→ 回到机器时"整栈刚重启"是正常现象。
# =====================================================================
set -uo pipefail

BODHI=${BODHI_REPO_DIR:-/mnt/c/Users/PHJY/source/bodhi2}
WK=${WK_DEPLOY_DIR:-/mnt/c/Users/PHJY/source/WeKnora}
OVL="$BODHI/deploy/docker-compose.weknora.yml"
export BODHI_DEPLOY_DIR="$BODHI/deploy"
export BODHI_NEO4J_CYPHER_DIR="$BODHI/artifacts/neo4j"
KB_BIZ=dbc2528f-611b-48da-9a71-d7c93975adb4
SELFCHECK="$BODHI/deploy/delivery/payload/mcp/selfcheck.py"
PY=${BODHI_PY:-/opt/bodhi-venv/bin/python3}
[ -x "$PY" ] || PY=python3

h() { echo; echo "== $* =="; }

compose() { ( cd "$WK" && docker compose -f docker-compose.yml -f "$OVL" --profile neo4j "$@" ); }

health_of() { docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}-{{end}}' "$1" 2>/dev/null || echo missing; }

do_up() {
  h "1/3 拉起整栈（compose up -d，profile neo4j）"
  compose up -d 2>&1 | tail -8 | sed 's/^/  /'
  h "2/3 重启 frontend（避免 nginx 缓存旧 app IP）"
  docker restart WeKnora-frontend >/dev/null 2>&1 && echo "  restarted"
  h "3/3 等健康（最多 180s）"
  for i in $(seq 1 36); do
    a=$(health_of WeKnora-app); p=$(health_of WeKnora-postgres); d=$(health_of WeKnora-docreader)
    code=$(curl -s -o /dev/null -m 4 -w '%{http_code}' http://localhost/ 2>/dev/null || echo 000)
    echo "  [$(printf '%3ds' $((i*5)))] postgres=$p app=$a docreader=$d  GET / → $code"
    if [ "$a" = healthy ] && [ "$p" = healthy ] && [ "$code" = 200 ]; then echo "  健康收敛 ✅"; return 0; fi
    sleep 5
  done
  echo "  !! 未在 180s 内收敛，请跑：$0 status"
  return 1
}

do_status() {
  h "A. 容器（含健康 / 重启次数）"
  docker ps -a --format '{{.Names}}\t{{.Status}}' | sed 's/^/  /'
  for c in WeKnora-app WeKnora-postgres WeKnora-docreader WeKnora-frontend WeKnora-neo4j WeKnora-redis; do
    docker inspect -f '  {{.Name}} restartCount={{.RestartCount}} startedAt={{.State.StartedAt}} health='"$(health_of $c)" "$c" 2>/dev/null
  done

  h "B. 宿主与引擎"
  echo "  WSL 启动于 $(uptime -s)（uptime：$(uptime | sed 's/.*up //; s/,.*load/ load/')）"
  echo "  dockerd：$(systemctl is-active docker)  启动于 $(systemctl show docker --property=ActiveEnterTimestamp --value)"
  echo "  近 10 分钟 dockerd 启动次数：$(journalctl -u docker --since '10 min ago' --no-pager 2>/dev/null | grep -c 'Started docker.service')（>1 = 有人在反复重启 dockerd）"
  echo "  live-restore：$(grep -o '\"live-restore\"[^,}]*' /etc/docker/daemon.json 2>/dev/null || echo '未设置')"
  ss -ltnp 2>/dev/null | grep -E ':(80|8080|8765|7474|7687)\b' | sed 's/^/  /'

  h "C. HTTP 端点（同一 origin：http://localhost）"
  for u in "/" "/platform/creatChat" "/health" "/api/v1/health" "/bodhi/ontology/classes" \
           "/bodhi/graph?kb_id=$KB_BIZ&model=bmm"; do
    printf '  %-64s -> ' "$u"
    curl -s -o /dev/null -m 15 -w '%{http_code} %{size_download}B\n' "http://localhost$u"
  done
  echo "  （/ → 200 是 SPA 外壳；/api/v1/health → 401 = app 活着但需鉴权；000 = 连不上）"

  h "D. bodhi-mcp（本体 MCP 服务）"
  echo "  systemd：$(systemctl is-active bodhi-mcp)"
  if [ -f "$SELFCHECK" ]; then "$PY" "$SELFCHECK" 2>&1 | tail -5 | sed 's/^/  /'; fi

  h "E. 判据速查"
  echo "  · 容器 Up 但 uptime 只有几十秒，且 dockerd 近 10 分钟启动次数 >1 → **重启循环**：见本文件头注释（沙箱 docker 后端 + live-restore）"
  echo "  · / 与 /platform/creatChat 都 000 → 前端容器没起或 80 端口没监听：$0 up"
  echo "  · / 200 但 /api/v1/* 502/000 → nginx 缓存了旧 app IP：docker restart WeKnora-frontend"
  echo "  · /bodhi/* 502 → bodhi-mcp 没起：systemctl status bodhi-mcp（改后端代码后必须 systemctl restart bodhi-mcp）"
}

do_watch() {
  echo "每 15 秒一行（Ctrl+C 退出）：时间 | dockerd 启动数(近10m) | app 状态 | GET / | GET /bodhi/ontology/classes"
  while true; do
    n=$(journalctl -u docker --since '10 min ago' --no-pager 2>/dev/null | grep -c 'Started docker.service')
    a=$(docker inspect -f '{{.State.Status}}' WeKnora-app 2>/dev/null || echo missing)
    c1=$(curl -s -o /dev/null -m 5 -w '%{http_code}' http://localhost/ 2>/dev/null || echo 000)
    c2=$(curl -s -o /dev/null -m 8 -w '%{http_code}' http://localhost/bodhi/ontology/classes 2>/dev/null || echo 000)
    printf '%s | dockerd启动=%s | app=%s | / → %s | /bodhi/ontology/classes → %s\n' \
      "$(date +%H:%M:%S)" "$n" "$a" "$c1" "$c2"
    sleep 15
  done
}

case "${1:-status}" in
  up)     do_up ;;
  status) do_status ;;
  watch)  do_watch ;;
  stop)   h "停容器（保留数据卷）"; compose stop 2>&1 | tail -8 | sed 's/^/  /' ;;
  *)      echo "用法: $0 [up|status|watch|stop]"; exit 1 ;;
esac
