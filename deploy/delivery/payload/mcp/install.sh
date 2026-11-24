#!/usr/bin/env bash
# bodhi2 02 运行时安装器（systemd-only；三个常驻服务共享 /opt/bodhi2/tools/ke-core）
#   sudo bash install.sh [--components mcp,diag,admin] [--init-db] [--no-start] [--cleanup-docker]
# 组件：mcp=8765 / diag=8771 / admin=8781；diag-init 由 init-db.sh 单独执行。
set -euo pipefail

PREFIX=/opt/bodhi2
COMPONENTS=mcp,diag,admin
INIT_DB=0
NO_START=0
CLEANUP_DOCKER=0
HERE="$(cd "$(dirname "$0")" && pwd)"

usage() { sed -n "2,7p" "$0"; }
fail() { echo "!! $*" >&2; exit 1; }

while [ $# -gt 0 ]; do
  case "$1" in
    --components) COMPONENTS="$2"; shift 2 ;;
    --components=*) COMPONENTS="${1#*=}"; shift ;;
    --init-db) INIT_DB=1; shift ;;
    --no-start) NO_START=1; shift ;;
    --cleanup-docker) CLEANUP_DOCKER=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "未知参数：$1" >&2; exit 2 ;;
  esac
done
[ "$COMPONENTS" = "all" ] && COMPONENTS="mcp,diag,admin"

[ "$(id -u)" = 0 ] || fail "请用 root 执行：sudo bash install.sh"
command -v systemctl >/dev/null || fail "当前主机没有 systemctl；02 只支持 systemd 安装"
command -v python3 >/dev/null || fail "缺少 python3"
command -v psql >/dev/null || fail "缺少 psql（postgresql-client）；systemd 模式不再回退 Docker"
command -v curl >/dev/null || fail "缺少 curl（安装后自检用）"
PYTHON_BIN="${PYTHON_BIN:-/usr/bin/python3}"
[ -x "$PYTHON_BIN" ] || PYTHON_BIN="$(command -v python3)"
"$PYTHON_BIN" -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)" || fail "需要 Python >= 3.10（$PYTHON_BIN）"
[ -d /run/systemd/system ] || fail "当前不是运行中的 systemd 环境（WSL/容器内请用真实主机或开启 systemd）"

SRC="$HERE"
if [ -d "$HERE/02-mcp-server/tools" ]; then SRC="$HERE/02-mcp-server"; fi
[ -d "$SRC/tools/ontology-mcp" ] || fail "找不到 tools/ontology-mcp；请把 02 包解到 $PREFIX（tar --strip-components=1）"

echo "== 1/6 安装运行时到 $PREFIX"
if [ "$SRC" != "$PREFIX" ]; then
  install -d -m 0755 "$PREFIX" "$PREFIX/config" "$PREFIX/logs"
  for d in tools skills artifacts ontology sql; do
    [ -d "$SRC/$d" ] || continue
    install -d -m 0755 "$PREFIX/$d"
    cp -a "$SRC/$d/." "$PREFIX/$d/"
  done
  for f in bodhi2-mcp.service bodhi2-agent-diag.service bodhi2-analytics-admin.service install.sh start.sh stop.sh status.sh init-db.sh selfcheck.py logrotate.bodhi2 .env.example MCP-SERVER.md DIAG-CONFIG.md ANALYTICS-CONFIG.md ANALYTICS-ADMIN.md; do
    [ -f "$SRC/$f" ] && cp -a "$SRC/$f" "$PREFIX/$f"
  done
  if [ -d "$SRC/logs" ]; then cp -a "$SRC/logs/." "$PREFIX/logs/"; fi
  for f in "$SRC"/config/*.env.example; do
    [ -f "$f" ] || continue
    b="$(basename "$f")"
    [ -f "$PREFIX/config/$b" ] || cp -a "$f" "$PREFIX/config/$b"
  done
else
  install -d -m 0755 "$PREFIX/config" "$PREFIX/logs"
fi
chmod 0755 "$PREFIX"/*.sh 2>/dev/null || true

echo "== 2/6 检查配置"
CREATED=0
for name in common mcp agent-diag analytics-admin diag-init; do
  if [ ! -f "$PREFIX/config/$name.env" ]; then
    [ -f "$PREFIX/config/$name.env.example" ] || fail "缺少 config/$name.env.example"
    cp "$PREFIX/config/$name.env.example" "$PREFIX/config/$name.env"
    chmod 0600 "$PREFIX/config/$name.env"
    CREATED=1
  fi
done
if [ "$CREATED" = 1 ]; then
  echo "   已生成 config/*.env；请先改 config/common.env 的 BODHI_DB_*（BODHI_DB_HOST 用 127.0.0.1 或可达 IP），再重跑本脚本"
  exit 0
fi
chmod 0600 "$PREFIX/config/"*.env 2>/dev/null || true
set -a
. "$PREFIX/config/common.env"
set +a
[ -n "${BODHI_DB_HOST:-}" ] || fail "config/common.env 缺少 BODHI_DB_HOST"
case "${BODHI_DB_HOST}" in WeKnora-postgres|postgres) fail "systemd 在宿主机运行，BODHI_DB_HOST 不能写容器名 $BODHI_DB_HOST；改成 127.0.0.1 或宿主机可达 IP" ;; esac
[ "${BODHI_DB_PASSWORD:-}" != "CHANGE_ME" ] || fail "config/common.env 仍是 CHANGE_ME，请先改数据库口令"
[ -n "${BODHI_DB_USER:-}" ] || fail "config/common.env 缺少 BODHI_DB_USER"
[ -n "${BODHI_DB_NAME:-}" ] || fail "config/common.env 缺少 BODHI_DB_NAME"

echo "== 3/6 运行时完整性检查"
for f in tools/ontology-mcp/server.py tools/ke-core/ke_db.py tools/analytics-admin/server.py tools/domain-mcp/analytics_sql/server.py sql/analytics/001_schema.sql; do
  [ -f "$PREFIX/$f" ] || fail "缺少 $PREFIX/$f；02 包不完整，或解包层级不对（应为 /opt/bodhi2/tools）"
done
TOOL_COUNT="$("$PYTHON_BIN" - "$PREFIX/tools/ontology-mcp/srv/schemas.py" <<'PY'
import pathlib
import sys
print(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8").count("\"name\""))
PY
)"
[ "$TOOL_COUNT" = 51 ] || fail "ontology-mcp 工具定义应为 51，实际 $TOOL_COUNT；请用当前 r6 02 包重装，不要复用旧源码"
echo "   共享 core、admin/diag server、51 个工具定义 OK"

echo "== 4/6 安装 systemd unit"
install -d -m 0755 /etc/systemd/system
for u in bodhi2-mcp.service bodhi2-agent-diag.service bodhi2-analytics-admin.service; do
  [ -f "$PREFIX/$u" ] || fail "缺少 $PREFIX/$u"
  install -m 0644 "$PREFIX/$u" "/etc/systemd/system/$u"
done
systemctl daemon-reload
if [ -d /etc/logrotate.d ] && [ -f "$PREFIX/logrotate.bodhi2" ]; then install -m 0644 "$PREFIX/logrotate.bodhi2" /etc/logrotate.d/bodhi2; fi
echo "   unit 已安装，日志目录：$PREFIX/logs"

echo "== 5/6 检查端口与旧容器"
if [ "$CLEANUP_DOCKER" = 1 ]; then
  command -v docker >/dev/null || fail "指定了 --cleanup-docker，但没有 docker"
  docker rm -f bodhi-mcp bodhi-agent-diag bodhi-analytics-admin >/dev/null 2>&1 || true
fi
if command -v ss >/dev/null 2>&1; then
  for p in 8765 8771 8781; do
    if ss -ltn 2>/dev/null | grep -qE "[:.]$p[[:space:]]"; then
      echo "!! 端口 $p 已被占用；先停旧容器/旧进程，或加 --cleanup-docker 后重跑" >&2
      ss -ltnp 2>/dev/null | grep -E "[:.]$p[[:space:]]" >&2 || true
      exit 1
    fi
  done
fi

UNITS=()
case ",$COMPONENTS," in *",mcp,"*) UNITS+=(bodhi2-mcp);; esac
case ",$COMPONENTS," in *",diag,"*) UNITS+=(bodhi2-agent-diag);; esac
case ",$COMPONENTS," in *",admin,"*) UNITS+=(bodhi2-analytics-admin);; esac
[ ${#UNITS[@]} -gt 0 ] || fail "没有选中组件（可选 mcp,diag,admin）"

if [ "$INIT_DB" = 1 ]; then
  echo "== 6/6 初始化 diag + analytics"
  bash "$PREFIX/init-db.sh" all
fi

for u in "${UNITS[@]}"; do systemctl enable "$u" >/dev/null; done
if [ "$NO_START" = 0 ]; then
  for u in "${UNITS[@]}"; do
    if systemctl is-active --quiet "$u"; then systemctl restart "$u"; else systemctl start "$u"; fi
  done
  sleep 3
  if [[ ",$COMPONENTS," == *",mcp,"* ]]; then
    "$PYTHON_BIN" "$PREFIX/selfcheck.py" --url http://127.0.0.1:8765/mcp --expect-tools 51 || { tail -n 40 "$PREFIX/logs/bodhi2-mcp.err.log" 2>/dev/null || true; exit 1; }
  fi
  if [[ ",$COMPONENTS," == *",diag,"* ]]; then
    curl -fsS http://127.0.0.1:8771/ >/dev/null || { tail -n 40 "$PREFIX/logs/bodhi2-agent-diag.err.log" 2>/dev/null || true; exit 1; }
  fi
  if [[ ",$COMPONENTS," == *",admin,"* ]]; then
    curl -fsS http://127.0.0.1:8781/ >/dev/null || { tail -n 40 "$PREFIX/logs/bodhi2-analytics-admin.err.log" 2>/dev/null || true; exit 1; }
  fi
fi

systemctl --no-pager --full status "${UNITS[@]}" || true
cat <<EOD

== 完成（systemd-only） ==
日志目录：/opt/bodhi2/logs/
  tail -f /opt/bodhi2/logs/bodhi2-mcp.err.log
  journalctl -u bodhi2-mcp -f

WeKnora MCP URL（宿主机 IP，不是容器 DNS）：
  bodhi_ontology -> http://<宿主机IP>:8765/mcp
  agent_diag     -> http://<宿主机IP>:8771/mcp
记得把宿主机 IP 加进 WeKnora .env 的 SSRF_WHITELIST_EXTRA。
EOD
