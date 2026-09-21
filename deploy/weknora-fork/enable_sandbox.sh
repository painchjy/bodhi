#!/usr/bin/env bash
# 开/关 WeKnora 的 **Docker 沙箱后端**（原生技能 / 沙箱会话的前提）。
#
# 为什么需要：WeKnora「原生技能」= 装进**快照镜像**（`tenant_skills` 绑 sandbox_config，
# 上传 zip → 安装时构建镜像），而镜像只能由沙箱后端构建。本部署默认
# `WEKNORA_SANDBOX_DOCKER_ENABLED=false` 且**没挂 docker.sock**，所以原生技能装不上。
#
# ⚠️ 安全：把宿主 docker.sock 挂进 app 容器 ≈ 给容器**宿主机 root**。
#    上游 docker-compose.yml 也这么注解（"仅私有化单机"）。本机是私有单机开发环境，
#    所以可用；对外部署请勿照抄。
#
# 用法
# ----
#   bash deploy/weknora-fork/enable_sandbox.sh            # 干跑：只打印将要做的改动
#   bash deploy/weknora-fork/enable_sandbox.sh --apply    # 开：改 .env + compose，重建 app
#   bash deploy/weknora-fork/enable_sandbox.sh --revert   # 关：恢复注释与 false，重建 app
#
# 开完之后的注册步骤见 docs/agent-design-flow.md §11.7（建 sandbox config → 上传 bundle → 安装）。
set -euo pipefail

WEKNORA_DIR="${WEKNORA_DIR:-/mnt/c/Users/PHJY/source/WeKnora}"
ENV_FILE="$WEKNORA_DIR/.env"
COMPOSE="$WEKNORA_DIR/docker-compose.yml"
SOCK_LINE='      - /var/run/docker.sock:/var/run/docker.sock'
LOG="${TMPDIR:-/tmp}/bodhi_enable_sandbox.log"

mode="dry"
case "${1:-}" in
  --apply) mode="apply" ;;
  --revert) mode="revert" ;;
  "") mode="dry" ;;
  *) echo "未知参数：$1（用 --apply / --revert）" >&2; exit 2 ;;
esac

echo "== WeKnora 目录：$WEKNORA_DIR（mode=$mode）"
[ -f "$ENV_FILE" ] || { echo "找不到 $ENV_FILE" >&2; exit 1; }
[ -f "$COMPOSE" ] || { echo "找不到 $COMPOSE" >&2; exit 1; }
echo "   当前 .env：$(grep -n '^WEKNORA_SANDBOX_DOCKER_ENABLED' "$ENV_FILE" || echo '（无该行）')"
echo "   当前 socket 挂载：$(grep -n 'docker.sock' "$COMPOSE" | head -3 || echo '（无）')"

if [ "$mode" = "dry" ]; then
  cat <<'EOD'
（干跑）将会做：
  1) .env: WEKNORA_SANDBOX_DOCKER_ENABLED=false → true
  2) docker-compose.yml: 取消注释 `- /var/run/docker.sock:/var/run/docker.sock`
  3) docker compose up -d app（重建容器；入口脚本会按 socket GID 把 appuser 加入组）
  4) 健康检查 GET /health
回滚：bash deploy/weknora-fork/enable_sandbox.sh --revert
EOD
  exit 0
fi

want_enabled=true
[ "$mode" = "revert" ] && want_enabled=false

cp -n "$ENV_FILE" "$ENV_FILE.bak-$(date +%Y%m%d%H%M%S)" 2>/dev/null || true
cp -n "$COMPOSE" "$COMPOSE.bak-$(date +%Y%m%d%H%M%S)" 2>/dev/null || true

# ── SSRF 白名单（**开沙箱后的硬前提**）──────────────────────────────
# 一旦沙箱启用，app 会对「出站 URL」执行严格 SSRF 校验：
#   - `internal/utils/security.go` 的 restrictedHostnames 含 `host.docker.internal`；
#   - `internal/mcp/security.go ValidateServiceOutboundURLs` 在**每次建 MCP 客户端前**再校验一次。
# 于是 MCP 服务 URL（`http://host.docker.internal:8765/mcp`）会被拒 →
# `Failed to create MCP client … hostname host.docker.internal is restricted`
# → `No MCP tools registered` → 智能体只剩 wiki 工具（**技能面整体失效**）。
# 白名单是部署方维护的 `SSRF_WHITELIST_EXTRA`（compose 已注入 searxng 等）。
SSRF_MIN="host.docker.internal"
# compose 里 `SSRF_WHITELIST_EXTRA` 的默认值（docker 网络内的 sidecar 主机名）：
# .env 一旦设了这个变量就会**覆盖**该默认，所以这里必须把默认一起带上，否则会丢。
SSRF_DEFAULT="searxng,qdrant,milvus,weaviate,doris-fe,doris-be,minio"
ensure_ssrf() {
  if [ "$want_enabled" = true ]; then
    base="$SSRF_DEFAULT"
    if grep -q "^SSRF_WHITELIST_EXTRA=" "$ENV_FILE"; then
      cur=$(grep "^SSRF_WHITELIST_EXTRA=" "$ENV_FILE" | head -1 | cut -d= -f2-)
      case ",$cur," in *",$SSRF_MIN,"*) base="$cur" ;; *) base="${cur:+$cur,}$SSRF_MIN" ;; esac
      # 默认里的 sidecar 主机名补回来（幂等）
      for h in $(printf '%s' "$SSRF_DEFAULT" | tr ',' ' '); do
        case ",$base," in *",$h,"*) : ;; *) base="$base,$h" ;; esac
      done
      sed -i -E "s|^SSRF_WHITELIST_EXTRA=.*|SSRF_WHITELIST_EXTRA=${base}|" "$ENV_FILE"
    else
      echo "SSRF_WHITELIST_EXTRA=${base},${SSRF_MIN}" >> "$ENV_FILE"
    fi
  elif grep -q "^SSRF_WHITELIST_EXTRA=" "$ENV_FILE"; then
    cur=$(grep "^SSRF_WHITELIST_EXTRA=" "$ENV_FILE" | head -1 | cut -d= -f2-)
    new=$(printf '%s' "$cur" | tr ',' '\n' | grep -v "^${SSRF_MIN}$" | paste -sd, -)
    sed -i -E "s|^SSRF_WHITELIST_EXTRA=.*|SSRF_WHITELIST_EXTRA=${new}|" "$ENV_FILE"
  fi
}
ensure_ssrf

if [ "$want_enabled" = true ]; then
  sed -i -E 's/^WEKNORA_SANDBOX_DOCKER_ENABLED=.*/WEKNORA_SANDBOX_DOCKER_ENABLED=true/' "$ENV_FILE"
  grep -q '^WEKNORA_SANDBOX_DOCKER_ENABLED=true' "$ENV_FILE" || echo 'WEKNORA_SANDBOX_DOCKER_ENABLED=true' >> "$ENV_FILE"
  # 取消注释（只改那一行，幂等；注意行首有缩进）
  sed -i -E "s|^([[:space:]]*)#[[:space:]]*- /var/run/docker.sock:/var/run/docker.sock.*$|\1- /var/run/docker.sock:/var/run/docker.sock|" "$COMPOSE"
else
  sed -i -E 's/^WEKNORA_SANDBOX_DOCKER_ENABLED=.*/WEKNORA_SANDBOX_DOCKER_ENABLED=false/' "$ENV_FILE"
  sed -i -E "s|^[[:space:]]*- /var/run/docker.sock:/var/run/docker.sock[[:space:]]*$|      # - /var/run/docker.sock:/var/run/docker.sock|" "$COMPOSE"
fi

echo "== 改动后"
echo "   .env：$(grep -n '^WEKNORA_SANDBOX_DOCKER_ENABLED' "$ENV_FILE")"
echo "   SSRF：$(grep -n '^SSRF_WHITELIST' "$ENV_FILE" || echo '（未设置 SSRF_WHITELIST_EXTRA）')"
echo "   挂载：$(grep -n 'docker.sock' "$COMPOSE" | head -3)"

( cd "$WEKNORA_DIR" && docker compose up -d app ) > "$LOG" 2>&1 || { tail -20 "$LOG"; exit 1; }
# ⚠️ nginx 只在**启动时**解析一次上游主机名（`proxy_pass http://app:8080`）：
#    app 容器重建后 IP 会变 → nginx 仍连旧 IP → `/api/*` 全部 502 →
#    **前端表现为"登录报错"**（2026-09-21 实测踩到）。所以每次重建 app 后必须重启 frontend。
if docker ps --format '{{.Names}}' | grep -q '^WeKnora-frontend$'; then
  docker restart WeKnora-frontend > /dev/null && echo "== 已重启 WeKnora-frontend（避免 nginx 缓存旧 app IP）"
fi
echo "== 重建完成（日志 $LOG）；等健康检查"
for i in $(seq 1 30); do
  if curl -fsS -m 3 http://127.0.0.1:8080/health > /dev/null 2>&1; then echo "   /health OK（${i}0s 内）"; break; fi
  sleep 10
done
echo "== 容器内确认"
docker exec WeKnora-app sh -c 'env | grep -i WEKNORA_SANDBOX_DOCKER_ENABLED; ls -l /var/run/docker.sock 2>&1 | head -1'
