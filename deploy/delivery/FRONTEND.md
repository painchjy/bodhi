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
| 7 | 平台 → MCP 服务 | 能看到 `bodhi_ontology`，且**启用的工具里有 10 个** |

## 5. 两个必须知道的坑

1. **nginx 只在启动时解析上游**：任何重建 app 容器（`docker compose up -d app`）之后，前端 `/api/*` 会 502，
   表现为**"登录报错"**。两个措施都要有：
   - 交付的 `default.conf.template` 已加 `resolver 127.0.0.11 valid=10s ipv6=off;` + `set $app_backend …` + 变量式 `proxy_pass`；
   - 发生重建时仍建议 `docker restart WeKnora-frontend` 一把（`deploy_frontend.sh` / `enable_sandbox.sh` 里都自动做了）。
2. **`/bodhi/` 的上游地址**：模板里默认 `http://host.docker.internal:8765`（MCP 跑在宿主机时）。
   若 MCP 也进了 compose 网络，把该行改成 `proxy_pass http://bodhi-mcp:8765;`（同网络 DNS），更稳。
