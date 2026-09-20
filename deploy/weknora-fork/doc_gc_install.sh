#!/usr/bin/env bash
# =====================================================================
# bodhi2 · 本体实例「按来源文档」定时巡检清理（doc GC）—— 可选安装
# ---------------------------------------------------------------------
# 背景：WeKnora 删文档只清它自己产的那部分（上游 wiki 页 + 它的 wiki 图），
#       不会碰我们的实例层：PG `wiki_pages.source_refs` 标记的实例页、
#       Neo4j `BodhiInstance`。这个 timer 定期跑
#       `ke_docs.py sweep --all --apply`，**只**清理「来源文档已删/不存在」的
#       实例页（独占页删掉、多源页摘引用），index 页与无来源页永不碰 ——
#       相当于把「删文档」的联动补上（最长延迟一个巡检周期）。
#
# 用法（WSL 里 root）：
#   bash deploy/weknora-fork/doc_gc_install.sh              # 只安装单元（不启用）
#   bash deploy/weknora-fork/doc_gc_install.sh --enable     # 安装并启用（默认每 15 分钟）
#   bash deploy/weknora-fork/doc_gc_install.sh --dry-run    # 立刻 dry-run 看会删什么
#   bash deploy/weknora-fork/doc_gc_install.sh --uninstall  # 卸载
# 可用 DOC_GC_INTERVAL=5min 改周期。
# =====================================================================
set -euo pipefail
BODHI_REPO=${BODHI_REPO_DIR:-/mnt/c/Users/PHJY/source/bodhi2}
PY=/opt/bodhi-venv/bin/python3
UNIT=/etc/systemd/system/bodhi-doc-gc.service
TIMER=/etc/systemd/system/bodhi-doc-gc.timer
WRAP=/usr/local/bin/bodhi-doc-gc
INTERVAL=${DOC_GC_INTERVAL:-15min}

case "${1:-install}" in
  --dry-run)
    exec "$PY" "$BODHI_REPO/tools/ke-core/ke_docs.py" sweep --all
    ;;
  --uninstall)
    systemctl disable --now bodhi-doc-gc.timer 2>/dev/null || true
    rm -f "$UNIT" "$TIMER" "$WRAP"
    systemctl daemon-reload
    echo "已卸载 bodhi-doc-gc（单元文件与包装脚本已删）"
    exit 0
    ;;
esac

cat > "$WRAP" <<EOF
#!/usr/bin/env bash
# bodhi2 · 本体实例按文档巡检清理（只清「来源文档已删」的残留）
exec $PY "$BODHI_REPO/tools/ke-core/ke_docs.py" sweep --all --apply "\$@"
EOF
chmod +x "$WRAP"

cat > "$UNIT" <<EOF
[Unit]
Description=Bodhi ontology instance GC (purge residue of deleted source documents)
After=network-online.target docker.service
Wants=docker.service

[Service]
Type=oneshot
WorkingDirectory=$BODHI_REPO
ExecStart=$WRAP
EOF

cat > "$TIMER" <<EOF
[Unit]
Description=Periodic sweep: purge ontology instances of deleted source documents

[Timer]
OnBootSec=5min
OnUnitActiveSec=$INTERVAL
Persistent=true

[Install]
WantedBy=timers.target
EOF

systemctl daemon-reload
echo "已安装：$UNIT"
echo "        $TIMER（间隔 $INTERVAL）"
echo "        $WRAP"
if [ "${1:-}" = "--enable" ]; then
  systemctl enable --now bodhi-doc-gc.timer
  systemctl list-timers bodhi-doc-gc.timer --no-pager || true
  echo "已启用：每 $INTERVAL 自动清理「来源文档已删」的本体实例"
else
  echo "（未启用）启用：systemctl enable --now bodhi-doc-gc.timer"
  echo "先看会删什么：bash deploy/weknora-fork/doc_gc_install.sh --dry-run"
fi
