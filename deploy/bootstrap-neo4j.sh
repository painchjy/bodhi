#!/bin/sh
# bodhi2 · Neo4j 本体投影灌库（幂等，可重复执行）
# =====================================================================
# 由 deploy/docker-compose.yml 或 deploy/docker-compose.weknora.yml 以
# 「一次性容器 + 覆盖 entrypoint」的方式调用：
#     entrypoint: ["/bin/sh", "/bodhi-deploy/bootstrap-neo4j.sh"]
# 因为覆盖了 entrypoint，neo4j 镜像的入口脚本不会执行，所以 NEO4J_* 变量
# 不会污染 neo4j.conf —— 这里可以安全接收连接串/账号/密码。
#
# 环境变量：
#   NEO4J_URI          默认 bolt://neo4j:7687
#   NEO4J_USERNAME     默认 neo4j
#   NEO4J_PASSWORD     必填（缺失直接失败，不猜默认值）
#   BODHI_CYPHER_DIR   默认 /bootstrap（挂载 artifacts/neo4j）
#   BODHI_RETRY_TRIES  默认 60（连接重试次数）
#   BODHI_RETRY_SLEEP  默认 2（每次重试间隔秒数）
#
# 执行顺序（顺序错了约束就晚生效）：
#   00_constraints.cypher -> 10_ontology.cypher
#   （20_cross_layer_queries.cypher 是查询模板，不是灌库脚本，不在这里执行）
#
# 退出码：0 成功 / 1 参数或文件缺失 / 2 连接成功但执行失败（重试耗尽）
#
# 注意：本文件必须保持 LF 换行（见仓库根 .gitattributes），
#       CRLF 会让容器里的 sh 把 \r 当成命令的一部分而报错。
# =====================================================================
set -eu

URI="${NEO4J_URI:-bolt://neo4j:7687}"
DB_USER="${NEO4J_USERNAME:-neo4j}"
DB_PASS="${NEO4J_PASSWORD:-}"
CYPHER_DIR="${BODHI_CYPHER_DIR:-/bootstrap}"
TRIES="${BODHI_RETRY_TRIES:-60}"
SLEEP_SECS="${BODHI_RETRY_SLEEP:-2}"

FILES="00_constraints.cypher 10_ontology.cypher"

log() {
    printf '[bodhi2] %s\n' "$*"
}

if [ -z "$DB_PASS" ]; then
    log "错误：未设置 NEO4J_PASSWORD（不要用空密码连图库）"
    exit 1
fi

# 官方镜像里 cypher-shell 一般在 PATH 上；取不到时退回绝对路径。
CS="$(command -v cypher-shell 2>/dev/null || true)"
if [ -z "$CS" ] && [ -x /var/lib/neo4j/bin/cypher-shell ]; then
    CS=/var/lib/neo4j/bin/cypher-shell
fi
if [ -z "$CS" ]; then
    log "错误：镜像内找不到 cypher-shell（NEO4J_IMAGE 是否为官方 neo4j 镜像？）"
    exit 1
fi

for f in $FILES; do
    if [ ! -f "$CYPHER_DIR/$f" ]; then
        log "错误：缺少 $CYPHER_DIR/$f"
        log "请先在仓库根目录生成产物：python tools/ontology-compiler/compile.py compile"
        exit 1
    fi
done

apply_file() {
    file="$1"
    n=0
    while :; do
        n=$((n + 1))
        if "$CS" -a "$URI" -u "$DB_USER" -p "$DB_PASS" -f "$file"; then
            log "已应用 $(basename "$file")"
            return 0
        fi
        if [ "$n" -ge "$TRIES" ]; then
            log "失败：$file（已重试 $n 次，$URI 仍不可用）"
            return 2
        fi
        log "等待 $URI 就绪（$n/$TRIES）…"
        sleep "$SLEEP_SECS"
    done
}

log "目标图库：$URI（database 用驱动默认库）"
log "Cypher 源：$CYPHER_DIR"

for f in $FILES; do
    apply_file "$CYPHER_DIR/$f" || exit 2
done

log "本体投影完成（MERGE + IF NOT EXISTS，重复执行安全）"
log "核对投影规模："
log "  MATCH (n) WHERE n.bodhi_projection = 'ontology' RETURN labels(n)[0] AS kind, count(*) AS count ORDER BY kind;"
log "预期：BodhiModule 5 / BodhiOntClass 53（含 6 个 external 占位）/ BodhiEnumValue 7 / BodhiRestriction 26 / BodhiOntProperty 90（object 70 + datatype 20）"
log "跨层查询模板（不灌库，供人工执行）：$CYPHER_DIR/20_cross_layer_queries.cypher"
