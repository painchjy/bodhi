#!/usr/bin/env bash
# =====================================================================
# 构建 Bodhi2 的 WeKnora 前端镜像（weknora-ui:bodhi2）
# ---------------------------------------------------------------------
# 为什么需要自己构建：上游前端源码的 WikiBrowser.vue / KnowledgeBase.vue 要加
# 「本体 / 待确认合并 / 本体图谱」三个 tab 与三级折叠；本仓库不改 Go、不重建 app
# 镜像（见 docs/weknora-fork.md §10.11 零重建路径），只重建 frontend 镜像。
#
# 用法（WSL 里，src 目录需含 package.json；会在该目录里改文件、生成 dist）：
#   bash deploy/weknora-fork/build_frontend.sh /root/fe-build [镜像tag]
#
# 实测环境（2026-09-19）：WSL Ubuntu / node v22.22.1 / npm 9.2.0 / registry=npmmirror
# 踩坑记录：
#   1) 上游是 **pnpm** 工程：package.json 的 resolutions.lightningcss="none" 会让
#      npm 报 `Invalid comparator: none`。脚本会先摘掉该覆盖项再 npm install。
#   2) 上游源码若来自 CDN 逐文件补拉，可能有**截断**文件（构建时报
#      "Element is missing end tag"）→ 先跑 check_sfc.mjs 闸门定位。
#   3) 上游 package.json 的 build 脚本就是 `vite build`，产物 dist/ 由镜像 Dockerfile 拷进 nginx。
# =====================================================================
set -euo pipefail

SRC=${1:?用法: build_frontend.sh <上游 frontend 目录> [镜像tag]}
TAG=${2:-weknora-ui:bodhi2}
HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
FEDIR="$HERE/frontend"

cd "$SRC"
echo "== 0) 目录 =="; pwd; node -v; npm -v

echo
echo "== 1) 应用前端补丁（幂等） =="
python3 "$HERE/patch_frontend.py" "$SRC"

echo
echo "== 2) 摘掉 npm 不认的 lightningcss 覆盖项 =="
python3 - <<'PY'
import json, pathlib
p = pathlib.Path('package.json')
d = json.loads(p.read_text(encoding='utf-8'))
for key in ('overrides', 'resolutions'):
    if isinstance(d.get(key), dict):
        d[key].pop('lightningcss', None)
        if not d[key]:
            d.pop(key)
p.write_text(json.dumps(d, indent=2, ensure_ascii=False) + "\n", encoding='utf-8')
print("  overrides:", d.get('overrides'))
print("  resolutions:", d.get('resolutions'))
PY

echo
echo "== 3) npm install =="
npm config set registry https://registry.npmmirror.com
npm install --no-audit --no-fund

echo
echo "== 4) .vue 完整性闸门 =="
node "$FEDIR/check_sfc.mjs" src

echo
echo "== 5) vite build =="
npm run build
echo "  dist: $(du -sh dist | cut -f1)  files=$(find dist -type f | wc -l)"

echo
echo "== 6) docker build -> $TAG =="
docker build -f Dockerfile -t "$TAG" .
docker images --format '{{.Repository}}:{{.Tag}}  {{.Size}}' | grep -F "$TAG" || true

echo
echo "== 7) 镜像内补丁自检（应看到 BodhiGraphTab / 本体图谱） =="
docker run --rm --entrypoint sh "$TAG" -c \
  "grep -o 'BodhiGraphTab' /usr/share/nginx/html/assets/*.js | head -2; \
   grep -c 'ontology-graph-tab' /usr/share/nginx/html/assets/*.js | head -4" || true

echo
echo "完成。overlay 里把 frontend.image 指到 $TAG 即可（见 deploy/docker-compose.weknora.yml）。"
