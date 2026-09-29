# 排错手册（我们实际踩过的坑，按现象查）

> 都是 2026-09 这轮真实故障，含"现象 / 日志特征 / 根因 / 修法 / 验证"。

## 1. 智能体说"这些工具在我的环境里不存在"（MCP 工具全丢）
**日志特征（WeKnora app 侧）**
```
ERROR Failed to create MCP client for service bodhi_ontology:
      MCP service URL failed SSRF validation: hostname <X> is restricted
WARN  registerMCPTools | No MCP tools registered from 1 enabled service(s)
INFO  stage=Agent action=tools_ready tool_count=5     # 只剩 5 个 wiki 工具（正常应是 15）
```
**根因**：WeKnora 对**出站 URL** 做 SSRF 校验，`utils/security.go` 的 `restrictedHostnames` 含 `host.docker.internal`、
`localhost`、`127.0.0.1` 等；且 `mcp/security.go ValidateServiceOutboundURLs` 会在**每次建 MCP 客户端前**再校验一次。
**修法（二选一）**
1. **MCP 与 app 同网络**：MCP URL 用容器 DNS（`http://bodhi-mcp:8765/mcp`）——私网 IP 字面量都不出现，天然通过；
2. 必须用宿主机 IP/主机名时：WeKnora 的 `.env` 加白名单后重建 app：
   ```bash
   SSRF_WHITELIST_EXTRA=<原默认值>,host.docker.internal,172.18.0.1   # 逗号分隔；**别覆盖**原默认值
   docker compose up -d app && docker restart WeKnora-frontend
   ```
**验证**：`docker logs WeKnora-app | grep tools_ready` → `tool_count=15`。

## 2. 前端能打开但**登录/所有 API 报错**（502）
**日志特征（前端 nginx）**
```
connect() failed (111: Connection refused) while connecting to upstream:
  http://172.18.0.6:8080/api/v1/auth/login  → 502
```
**根因**：nginx `proxy_pass` 里的上游主机名**只在 nginx 启动时解析一次**。重建 app 容器后 IP 变了，nginx 仍连旧 IP。
**修法**
1. 立刻：`docker restart WeKnora-frontend`；
2. 根治（交付的模板已含）：`resolver 127.0.0.11 valid=10s ipv6=off;` + `set $app_backend "${APP_SCHEME}://${APP_HOST}:${APP_PORT}";`
   + 各 location 用 `proxy_pass $app_backend;`（变量式 → 每次请求重新解析；变量式**不能带 URI**，这些路径本就是原样透传）。
   模板是 bind-mount 进容器的 → 改完 `docker restart WeKnora-frontend` 即生效，**不用重建镜像**。
**验证**：首页 200、`/api/v1/auth/config` 200、错密码登录 400/401（**不是 502**）。

## 3. app 报 `Post http://…/mcp: EOF`（每次调工具都 EOF，MCP 侧无日志）
**根因**：MCP 服务端 handler 抛异常会**直接关闭连接**，客户端只看到 EOF（没有响应体）。
我们遇到的具体原因：新加的日志函数用了 `time.time()` 但文件里**没 `import time`** → 每次 `tools/call` `NameError` → 连接被关。
**排查**：`journalctl -u bodhi2-mcp -f`（或 `docker logs bodhi2-mcp -f`）—— 一定能看到 traceback。**不要**只看 app 侧日志。
**修法**：修异常；并把"附属动作"（日志/统计）放进 try 里，别让它影响工具结果。

## 4. 页面写了却"改不动"（关系/正文没更新）
**根因**（我们踩过的四个）：
1. **关系行只对"载荷里出现过的节点"生效** —— 想改某页的边，该页必须出现在 `nodes` 里；
2. 摘要类小节（`## CRUD 矩阵` 等**派生小节**）由服务端渲染，"已存在就跳过"的合并策略会让新设计落不下去 → 已改为**整体替换**；
3. `out_links` 只在**新建页**时写 → 更新路径漏更 → 关系面板/巡检对不上（已修）；
4. 关系**减不掉** → 用 `save_knowledge(..., retract: true)` 撤（会删关系行 + 同键的关系限定行，并留版本快照）。

## 5. 巡检报 C1「无来源实例页」（high）
**根因**：设计/生成出来的页没有 `source_refs`。设计页的来源按方案 C **继承自"服务页 + 报告页"**，
所以 `save_knowledge(stage="graph")` 的 `report.upstream` **必须**带这两类页；新建的应用/系统页要带 `source_document_id`。
**修法**：把上游页 slug 填进 `report.upstream` 重跑；或用运维脚本修（`docs/bodhi-ops-audit.md` P2 的 `plan → apply --confirm`）。

## 6. 智能体说"已落库"但其实没写（`applied=false`）
**根因**：`save_knowledge` 默认是 **dry_run**（只出清单）。回执里 `dry_run=true` / `applied=false`。
**修法**：要真写必须显式 `mode="apply"` 重跑同一份载荷；提示词里已明确规定"`applied=false` 不得说已落库"。
**核对**：`logs/mcp_calls_*.log` 有没有 `applied=true`；或看页面 `version` 是否 +1。

## 7. 落库报 violations（键不在本体里）
**根因**：`nodes[].attributes` / `edges[].properties` 的键**必须是该类在本体里声明过的数据属性**
（`ontology_types(model, focus?)` 返回的 `class_attributes`）。带前缀写（`easvc:crudKind`），未声明的会被回报为 violations（**不拦写入**）。
**修法**：补本体（TTL → 编译 → 投影），或改用已声明的键。常见正确写法：
- `easvc:ServiceOperation` → `operationMethod / isIdempotent / transactionBoundary / operationRetryPolicy / crudKind`；
- 边限定：`operationOperatesOnAttribute` 的 `properties.easvc:crudKind`（多值用**逗号**：`"C,U"`，别用数组）。

## 8. A5「重复/自环关系行」
**根因**：同一操作对同一属性同时读写时给了**两条边**（一条 C、一条 R）→ 页面出现两行完全相同的 `## 本体关系`。
**修法**：**写一条边**，`crudKind` 给多值：`properties={"easvc:crudKind": "C,R"}`。

## 9. 页面类型/关系"不在模型"（B1/B2/B3）
**根因**：本体模型库没投影全（或 TTL 改了没重编译），或类型前缀拼错。
**修法**：按 `ONTOLOGY-KB.md` 重新编译+投影；`curl "<mcp>/bodhi/ontology/models"` 看模型是否齐全。

## 10. 智能体传了不存在/编造的 kb_id
**现象**：回执 `知识库不存在：b1；**请把可选清单里的 id 原样传给 kb_id**，可选：企业知识（08810cbd…）、…`
**说明**：这是**服务端有意的容错**（错误信息里带可选清单，模型会自己纠正）。不用改配置；看到这类回执属正常纠错过程。

## 11. 技能目录取不到 / 技能改了不生效
**说明**：技能由 MCP 承载（`skills/<id>/SKILL.md`，**mtime 缓存**）：改完文件**无需重启**，下一次调用即生效；
新增技能目录后 MCP 会自动出现在目录里（无需改代码）。若取不到：确认 `skills/` 目录在**仓库根**（与 `tools/` 平级）且 MCP 进程能读。

**若 `skills()` 回执是 `{"error": "No module named 'yaml'"}`**：这是 MCP 在**没有 PyYAML** 的环境里跑
（干净容器/精简宿主机）。代码已带零依赖兜底（`tools/ke-core/ke_yamlmini.py`），只要源码是最新版即可；
升级方式：把 02 包里的 `tools/ke-core/ke_yamlmini.py` 与 `tools/ontology-mcp/server.py` 一起替换后重启 MCP。
我们实测：在只有 Python + psql 的镜像里 `skills()` 正常返回 3 个技能。

## 12. 打 MCP 镜像时卡在 `apt-get update`
**根因**：容器里要装 `postgresql-client`（提供 `psql`），而**公网 Debian 源在内网/受限网络下常常不通**。
**修法**：① `docker build --build-arg APT_MIRROR=<内网 debian 源> ...`；② 或改用**裸机 systemd** 方式（§MCP-SERVER 方式 B）。

## 14. 改了配置/代码但"没生效"（我们踩过，2026-09-29）

**现象**：接口少字段（例如 `/bodhi/context/page` 回执里没有 `state`）→ 前端「跨库上下文」面板的**治理状态徽标不显示**；或改了 `.env` 但认库/租户行为没变。

**根因**：`bodhi-mcp` 是**长驻进程**：
- 改**代码**（`tools/ke-core/*.py`、`tools/ontology-mcp/server.py`）→ 进程里还是旧模块；
- 改 **`.env`** → systemd 只在**启动时**读 `EnvironmentFile`（`daemon-reload` 只重载 unit 定义，不改已运行进程的环境）。

**修法**：
```bash
systemctl restart bodhi-mcp && sleep 3
systemctl show -p EnvironmentFiles --value bodhi-mcp      # 确认读的是哪个 .env
tr '\0' '\n' < /proc/$(systemctl show -p MainPID --value bodhi-mcp)/environ | grep '^BODHI_'
# 容器部署：docker compose up -d --force-recreate bodhi-mcp
```
**自检一行**（服务与 CLI 应看到同一份配置）：
```bash
python3 -c "import sys;sys.path.insert(0,'tools/ke-core');import ke_db,ke_ontology,ke_context;\
print('tenant=',ke_db.caller_tenant(),'ontology=',ke_ontology.resolve_ontology_kb()['source'],\
'concept=',ke_context.concept_kb()['source'],'db_pwd_len=',len(ke_db.DB_PASSWORD or ''))"
```

## 15. CLI 报 `need_write_permission`、`caller_tenant: null`（2026-09-29 已修）

**现象**：手敲 `ke_context.py concept-state …` / `concept-apply …` 落库失败，回执里 `caller_tenant` 是 `null`：
```json
{"error":"need_write_permission","permission":{"allowed":false,"caller_tenant":null,
 "why":"无法确定调用者租户（身份缺失 → 拒写，只读放行）"}}
```
**根因**：写权限要**调用者租户**（fail-closed 设计）。MCP 服务由 systemd 注入 env，**CLI 进程拿不到**；当时 `.env` 只被用来读 DB 口令，其它键不读 —— 于是 CLI 一律"身份缺失"。

**修法（已内置，v1.0 之后版本）**：`ke_db.env_value()` 统一读配置 —— **进程 env → `BODHI_WEKNORA_DIR/.env` → `…/WeKnora/.env` → 服务目录 `.env`**；
`caller_tenant()`、`resolve_ontology_kb()`、`concept_kb()` 都走它。老版本临时绕过：`BODHI_TENANT_ID=10000 python3 …` 或加 `--tenant 10000`。
**排查顺序**：① `.env` 里有没有 `BODHI_TENANT_ID`；② 该租户是不是目标库属主，或该租户所在组织在 `kb_shares` 上的权限是 `editor/admin`（`viewer` 只读）；
③ 目标库是否是**另一个租户**的库（那就得先共享/给写权限）。

## 16. `docker build` 报 `NotFound: parent snapshot … does not exist`（2026-09-29）

**现象**：打前端/MCP 镜像时失败：
```
NotFound: parent snapshot sha256:… does not exist: not found
```
**根因**：containerd 快照存储里**基础镜像的父层丢了**（常见于 dockerd 被反复重启/被强杀之后，快照索引与内容库不一致）。
**修法**（任选，通常第 1 条就够）：
```bash
docker pull nginx:alpine           # 或目标基础镜像，把缺的层补回来
docker build --no-cache -f Dockerfile -t weknora-ui:bodhi2 .
# 仍失败再退回传统构建器：
DOCKER_BUILDKIT=0 docker build --no-cache -f Dockerfile -t weknora-ui:bodhi2 .
```
前端重建的完整流程见 `FRONTEND.md` §4（本仓 `deploy/weknora-fork/build_frontend.sh` 会自动构建并自检）。

## 17. 整栈"莫名其妙全重启"/ `localhost` 时通时断（2026-09-28 排查结论）

**现象**：容器 `Up` 只有几十秒；`http://localhost/` 有时连不上，稍后 `/platform/creatChat` 又能开。

**两个独立根因**：
1. **WeKnora-app 的"技能沙箱 Docker 后端"**（`system_settings.sandbox.docker_enabled=true` + 挂载宿主 `/var/run/docker.sock`）
   在应用启动时"应用 docker 后端设置"会 **`systemctl restart docker`** → 全栈容器被杀重启 → 应用再启动 → 再重启 dockerd，形成**约 40 秒一轮的循环**。
   - **判别法**：`journalctl -u docker --since "$(uptime -s)" | grep -c 'Started docker.service'` > 1；
     停掉 app 观察 75 秒增量是否归零。
   - **缓解**：`/etc/docker/daemon.json` 加 `"live-restore": true`（dockerd 重启不再杀容器）+ `systemctl restart docker` 一次；
     彻底消除则关掉沙箱 docker 后端（会同时失去沙箱能力）。
2. **WSL 空闲被回收**：`.wslconfig` 未设 `vmIdleTimeout` 时，最后一个 WSL 进程退出约 60 秒后整台 VM 被关 → docker 引擎与容器一起停。
   - **修法**：`%USERPROFILE%\.wslconfig` 加 `[wsl2] vmIdleTimeout=86400000`（并保持一个 WSL 会话/或按时唤醒），
     容器 `restart: unless-stopped` 会在下次进入 WSL 时自动拉起。

> 排查入口：本仓 `deploy/weknora-fork/stack_ctl.sh status|watch|up|stop`（一条命令打出容器/健康/端口/端点/dockerd 重启次数/ MCP 自检）。
## 13. 沙箱相关（本交付**不需要**，仅备查）

WeKnora 原生技能是"沙箱安装型"（装进快照镜像）。要用需同时满足：`WEKNORA_SANDBOX_DOCKER_ENABLED=true`、
app 挂载 `docker.sock`（≈宿主机 root）、沙箱基础镜像可用、以及上面的 §1 SSRF 白名单。
我们已验证可行，但**方案上以 MCP 承载技能为主**（不需要沙箱）；若你们要开，照 `docs/agent-design-flow.md` §11.7。
