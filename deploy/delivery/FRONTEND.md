# 前端（WeKnora UI）包替换指引

> 交付物：`01-frontend/`
> - `weknora-ui-bodhi2.tar.gz` —— 我们打补丁后构建的 UI 镜像（`docker save` 产物），tag `weknora-ui:bodhi2`
> - `frontend/default.conf.template` —— nginx 模板（含 `/bodhi/` 反代 + **上游运行期解析**修复）
> - `frontend/docker-compose.weknora.yml` —— compose overlay 片段（image + 模板挂载 + extra_hosts）
> - `frontend/deploy_frontend.sh` —— 一键切换 + 端到端验收脚本
> - `frontend/patches/` —— 若你们 UI 版本不同，可自行打补丁：`patch_frontend.py`、`gen_frontend_types.py`、`ontologyTypes.ts`

## 1. 我们的前端补丁都改了什么（便于评估是否要整体替换）

| 能力 | 位置 | 说明 |
|---|---|---|
| **本体图谱 tab** | `components/bodhi/BodhiGraphTab.vue` 等 | 知识库里第三个 tab：按本体模型渲染图谱（节点=wiki 页、边=本体关系），支持按类型过滤 |
| **类型可视化** | `utils/ontologyTypes.ts`（**57 个类型**） | 类型彩色圆点 + 悬停中文类名；列表视图同样显示徽标；新增 `easvc:*`（服务详设）类型 |
| **本体关系维护面板** | `components/bodhi/BodhiRelationsPanel.vue` | 阅读页右侧：出边可改/可删、入边只读；调 MCP 的 `/bodhi/relations*` |
| **版本徽标位置** | `views/knowledge/wiki/WikiBrowser.vue`（补丁 v10） | 列表行 `v3` 徽标放在**标题之后**（放前面会让各行标题起始位置不齐，用户 2026-09-21 反馈）；`tools/delivery/patch_version_badge.py` 可对已有源码单独归一化（幂等）|
| **类型下拉（编辑页）** | `AgentEditorModal`/知识页编辑 | 页面 `page_type` 用下拉选取（来自本体模型库编译产物），避免手打前缀出错 |
| **上传自动生成 wiki 开关** | 知识库头部 | 打开后上传文档自动进 wiki 抽取流程 |
| **树/列表多选批量删除** | wiki 列表页 | |
| **`/bodhi/` 反向代理** | `default.conf.template` | 前端把 `/bodhi/*` 反代到 MCP 服务（图谱/待确认/巡检等只读数据） |
| **上游运行期解析修复** | `default.conf.template` | `resolver 127.0.0.11` + 变量式 `proxy_pass` → **app 容器重建后不用再重启前端**（见 §5） |

> 类型清单（`ontologyTypes.ts`）是**编进产物**的：新增本体类型（如 `easvc:*`）必须**重新构建 UI 镜像**才能在下拉/图例里看到。

## 2. 方案 A：直接用我们的镜像（推荐，你们的 UI 版本与我们一致时）

```bash
# ① 载入镜像
gunzip -c weknora-ui-bodhi2.tar.gz | docker load          # → weknora-ui:bodhi2
# ② 覆盖 compose：把 frontend 服务的 image 换成它，并挂载 nginx 模板
cd /path/to/weknora
cp /path/to/01-frontend/frontend/default.conf.template ./bodhi-default.conf.template
# 在你们的 docker-compose.yml（或 overlay）里对 frontend 服务加：
#   image: weknora-ui:bodhi2
#   volumes:
#     - ./bodhi-default.conf.template:/etc/nginx/templates/default.conf.template:ro
#   extra_hosts: ["host.docker.internal:host-gateway"]   # 仅当 /bodhi/ 反代走宿主机时需要
# ③ 起容器 + 验收
docker compose up -d --no-build frontend
docker restart WeKnora-frontend
bash /path/to/01-frontend/frontend/deploy_frontend.sh check
```

`deploy_frontend.sh check` 会打印：
```
A. 容器里的 nginx 配置（location /bodhi/ 是否在）
B. HTTP 端点：/ →200  /health →200  /bodhi/graph?… →200  /bodhi/pending?… →200
C. 产物里是否有补丁标记：BodhiGraphTab / ontology-graph-tab / pending-merges
D. 经前端反代取本体图数据：nodes=N edges=M
E. 待确认合并队列：pending=0
```

## 3. 方案 B：在自己的上游前端源码上打补丁（UI 版本不同、或你们要自己构建）

```bash
# 需要：Node ≥ 20、能访问 npm 源
cp -r /path/to/01-frontend/frontend/patches /tmp/fe-patches
python3 /tmp/fe-patches/patch_frontend.py --fe /path/to/weknora/frontend      # 打补丁（幂等）
python3 /tmp/fe-patches/gen_frontend_types.py --fe /path/to/weknora/frontend   # 生成 ontologyTypes.ts（57 类型）
cd /path/to/weknora/frontend && npm install && npm run build                   # 产物 dist/
# 用上游 Dockerfile 重建 UI 镜像并替换
docker compose build frontend && docker compose up -d --no-build frontend
```

> 补丁脚本会做：插入本体图谱 tab 与路由、类型常量与配色、关系面板组件、编辑页类型下拉、上传自动生成开关。
> 打补丁前请确认 `frontend/src` 是**干净的上游源码**（重复插入会让 vite 报语法错）。

## 4. 验收清单（替换后逐条勾）

| # | 检查 | 期望 |
|---|---|---|
| 1 | 登录页能登录 | 正常进入工作台（若报错先看 §5 的 nginx 上游问题）|
| 2 | 知识库 → wiki | 页面列表/树正常，页面类型显示**彩色圆点 + 中文类名** |
| 3 | 知识库 → 本体图谱 tab | 能出图（节点=页面，边=关系），按类型过滤可用 |
| 4 | 打开任一实例页 → 右侧关系面板 | 出边可编辑、入边只读 |
| 5 | 编辑页 → 本体类型下拉 | 能看到 `easvc:ServiceOperation` 等类型（说明 `ontologyTypes.ts` 生效）|
| 6 | `curl -s -o /dev/null -w '%{http_code}' http://<host>/bodhi/graph?kb_id=<kb>&model=ea` | `200`（说明 `/bodhi/` → MCP 通了）|
| 7 | 平台 → MCP 服务 | 能看到 `bodhi_ontology`，且**启用的工具里有 14 个** |

## 5. 两个必须知道的坑

1. **nginx 只在启动时解析上游**：任何重建 app 容器（`docker compose up -d app`）之后，前端 `/api/*` 会 502，
   表现为**"登录报错"**。两个措施都要有：
   - 交付的 `default.conf.template` 已加 `resolver 127.0.0.11 valid=10s ipv6=off;` + `set $app_backend …` + 变量式 `proxy_pass`；
   - 发生重建时仍建议 `docker restart WeKnora-frontend` 一把（`deploy_frontend.sh` / `enable_sandbox.sh` 里都自动做了）。
2. **`/bodhi/` 的上游地址**：模板里默认 `http://host.docker.internal:8765`（MCP 跑在宿主机时）。
   若 MCP 也进了 compose 网络，把该行改成 `proxy_pass http://bodhi-mcp:8765;`（同网络 DNS），更稳。

## 6. 外部资源依赖清单（内网部署参考；**不改代码**）

> 口径（用户 2026-09-21）：原生页面内网已可正常使用，所以只看**我们新开发/改动的前端页面**；
> 若有外网 CDN，列出版本 + 域名 + 部署建议即可 —— 因此我们**没有**修改产物里的任何外链。

### 6.1 我们新开发/改动的页面：**零外网依赖** ✅

| 文件（我们的补丁） | `http(s)://` 出现次数 |
|---|---|
| `views/knowledge/wiki/BodhiGraphTab.vue`（本体图谱 tab） | **0** |
| `views/knowledge/wiki/BodhiRelationsPanel.vue`（关系维护面板） | **0** |
| `views/knowledge/wiki/BodhiOntologyUpload.vue`（本体上传） | **0** |
| `utils/ontologyTypes.ts`（57 类型配色/中文名） | **0** |
| `WikiBrowser.vue` 补丁（类型圆点、**版本徽标**、待确认裁决、多选删、编辑下拉） | **0** |
| `KnowledgeBase.vue` 补丁（本体图谱 tab、上传自动生成 wiki 开关） | **0** |

- 这些页面只调用**同源**接口：`/bodhi/*`（nginx 反代到 MCP 服务）与 `/api/v1/*`（WeKnora app）；
- 用到的图标来自产品自带的**本地离线 sprite**（`/tdesign-icons/0.4.1/fonts/index.js`），不走 CDN；
- 无外部字体、无外部图片、无外部脚本。

### 6.2 整包唯一的外网域名（`tdesign-vue-next` 自带，非我们引入）

| 项 | 值 |
|---|---|
| **域名** | `tdesign.gtimg.com` |
| **路径** | `/icon/<版本>/fonts/index.js`、`/icon/<版本>/fonts/index.css` |
| **版本** | 组件内置版本表：**0.4.0 / 0.4.1 / 0.4.2 / 0.4.3 / 0.4.4**（默认常量指向 **0.4.2**） |
| 出现在 | `assets/tdesign-icon-offline-*.js`（打包后的 tdesign-vue-next `Icon` 组件） |
| 触发条件 | **仅当运行时没找到已加载的图标 sprite 才会去取**。上游 `index.html` 已提前加载本地 sprite（注释点名 tdesign issue #867/#897），所以正常使用**不会请求**该域名 |
| 我们是否改动 | **没有**（与上游完全一致，产物里仍是 3 处常量） |

### 6.3 部署建议（按你们内网策略三选一）

1. **直接阻断（推荐）**：默认运行不需要它；最坏情况只是个别图标缺失/回退，不影响功能与数据。
   验证方法：浏览器 F12 → Network 过滤 `tdesign.gtimg.com`（正常应为 **0 条**请求）。
2. **放行白名单**：若要求图标绝不缺失，在出口放行 `tdesign.gtimg.com`（HTTPS/443）。
3. **要"零外网 URL"（可选，需重新打包）**：
   `python3 tools/delivery/offline_harden.py --dist <dist> --rewrite` —— 把该常量改成本地
   `/tdesign-icons/<版本>/fonts/index.js`，并把 sprite 补齐到 0.4.0–0.4.4 目录，然后重建镜像。
   **默认不启用**（本次按"不改代码"口径未做）。

### 6.4 其余扫到的主机名都是"不会被请求"的

XML 命名空间（`w3.org` / `openxmlformats.org` / `purl.org` …）、文档链接（`github.com`、`vuejs.org` …）、
示例占位（`*.example.com`、`YOUR_IP`、`your-*`）、以及**可选渠道/模型集成端点**
（`api.openai.com`、`open.feishu.cn`、`open.larksuite.com`、`dashscope.aliyuncs.com` …）——
只有你们在设置里配了对应渠道/模型才会调用，不配置就没人访问。

体检命令（**只报告，不改产物**）：

```bash
python3 tools/delivery/offline_harden.py --dist /path/to/frontend/dist
# 也可直接查镜像：
docker run --rm --entrypoint sh weknora-ui:bodhi2 -c '
  grep -cE "src=\"https?://|href=\"https?://" /usr/share/nginx/html/index.html   # → 0
  grep -o "https://tdesign.gtimg.com/icon/" /usr/share/nginx/html/assets/tdesign-icon-offline-*.js | wc -l   # → 3（自带的兜底常量）
  ls /usr/share/nginx/html/tdesign-icons/'                                       # → 0.4.1（本地 sprite）
```
