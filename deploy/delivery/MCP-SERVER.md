# MCP 服务部署（bodhi2 ontology-mcp）

> 交付物：`bodhi2-02-mcp-server.tar.gz`（**包内就是「仓库根」**：`tools/ artifacts/ skills/ logs/` —— 不再套内层 tar，
> 解包即可 `docker build .`；**零第三方 Python 依赖**：只用标准库 + `psql` 客户端）
> 作用：给 WeKnora 提供 **20 个 MCP 工具**（领域建模分批 / 设计落库 / 巡检 / 技能目录 / 总览页 / 候选关联 / 任务回执 / **类型迁移两段式** / **跨库上下文映射与渲染（只读）** …）
> 依赖：Python ≥ 3.10、`postgresql-client`（提供 `psql`）、可读 WeKnora 的 Postgres；Neo4j **可选**。
>
> **不需要 PyYAML**：技能的 front-matter 优先用 PyYAML 解析，取不到时走 `tools/ke-core/ke_yamlmini.py`
> 的零依赖子集解析（我们逐键比对过，3 个技能结果一致）。**实测**：在只有 Python + psql 的干净镜像里
> `selfcheck.py` 全绿（initialize / tools/list 14 个 / skills() 3 个）。

---

## 0. 它到底怎么工作（先理解，再部署）

**本体类型迁移是两段式（2026-09-27 新增）**：改本体类型 = **迁移 slug（`模块/类/名称`）+ 联动引用**
（关系行 / `out_links` / 正文引用 / `## 溯源` / `page_metadata` / **建模会话状态**），
必须"先 preview、用户确认后 apply"，缺确认一律拒绝：

```bash
# ① 预览（只读）：新 slug、引用清单、会话命中、预计 violations、ticket
curl -s -X POST http://127.0.0.1:8765/bodhi/page/retag/preview -H 'Content-Type: application/json' \
     -d '{"kb_id":"<kb>","slug":"ea/step/某步骤","new_type":"ea:Activity"}'
# ② 确认后执行（带 ticket + acknowledge_risks）
curl -s -X POST http://127.0.0.1:8765/bodhi/page/retag/apply -H 'Content-Type: application/json' \
     -d '{"kb_id":"<kb>","slug":"ea/step/某步骤","new_type":"ea:Activity",
          "ticket":"<preview 的 ticket>","acknowledge_risks":["url_break","refs_rewrite","agent_session"]}'
# ③ 回滚（按 apply 留下的迁移记录，幂等）
curl -s -X POST http://127.0.0.1:8765/bodhi/page/retag/rollback -H 'Content-Type: application/json' \
     -d '{"kb_id":"<kb>","ticket":"<ticket>"}'
```
CLI 等价：`ke_admin.py retag-preview|retag-apply|retag-rollback`。
**智能体也能用**（2026-09-27 新增 3 个工具，总数 14 → **17**）：`retag_preview`（只读）→ 把影响面念给用户 →
用户同意后 `retag_apply`（带 `ticket` + `acknowledge_risks`，缺一即拒）→ 运维可用 `retag_rollback`。
巡检 **A7** 报"slug 段与 `page_type` 错位"（存量体检；迁移后应清零）。
`save_knowledge` 的 `retag` 参数**不再静默改类型**，改为回执 `retag_required`（含 ticket/风险/引用数）。

**跨库上下文映射（2026-09-28 一期，只读）**：`context_scan`（全库同名/同实例候选 + 建议 + ticket）、
`context_lookup`（检索前查同义/异义/依赖）、`context_page`（**渲染视图**：领域页 ←同名 slug→ 概念页）。
只读、只写 `state/context_map/`（不碰任何 wiki 页）。
**口径（用户 2026-09-28）**：领域库**不写 uuid、不互相引用**；关联靠**按 slug 同名查询**「企业共享概念模型」
（该库已建，认库 `wiki_config.bodhi_concept_kb=true`）；跨域关系**必须经企业共享概念页转换**。
HTTP：`GET /bodhi/contexts`、`GET /bodhi/context/scan?kb_ids=&limit=&write=0`、
`GET /bodhi/context/lookup?slug=|q=`、`GET /bodhi/context/page?slug=|q=[&kb_id=]`、
`GET|POST /bodhi/context/concept/preview`（dry-run，需 URL 编码参数）；
CLI：`ke_admin.py ctx-contexts|ctx-scan|ctx-lookup`（+ `ke_context.py page|concept-preview`）。
巡检新增 **G2–G7**（映射悬空/过期/异义未映射/矛盾/**G7 跨库直接引用=high**），并把 **F1 合并**为
"L1 `slug` 字面同名 + L2 类+标题同实例"（detail 标 `matched_by`）。设计见 `docs/context-mapping-plan.md`。


```
WeKnora-app ──(MCP over HTTP, POST /mcp)──► bodhi2-mcp (:8765)
     │                                            │
     │ allowed_tools 里带 mcp_bodhi_ontology_*    ├─ psql ──► WeKnora Postgres（wiki_pages / knowledges / …）
     └─ 读知识库片段（WeKnora 原生工具）           └─（可选）HTTP ──► Neo4j（本体投影；本交付不需要）
```

- MCP 服务**直接读写 Postgres**（`wiki_pages` 等表），不调 WeKnora 私有 API → 对上游版本升级不敏感；
- 它**不调 LLM**：抽取/设计由 WeKnora 侧的智能体做，MCP 只负责"规范化落库 + 本体校验 + 巡检 + 技能下发"；
- 技能（`skills/<id>/SKILL.md`）也由它提供：`skills()` 返回目录、`skills(skill="…")` 返回全文 + 本体面。

## 1. 目录布局（必须保持这个形状）

`server.py` 用 `Path(__file__).parents[2]` 定位"仓库根"，所以包内结构必须原样：

```
/app/                          ← 容器里的"仓库根"
├── tools/ontology-mcp/        ← server.py / refresh_design.py / mdview.py / graph_page.py
├── tools/ke-core/             ← ke_db / ke_pages / ke_ontology / ke_audit / ke_docs / ke_admin / ke_neo4j
├── tools/ontology-extract/    ← ontology_wiki.py（本体知识库投影；server 会 import）
├── skills/<id>/SKILL.md       ← 技能库（单一来源）
├── artifacts/                 ← 编译产物（ontology_index.json / prompts / json_schema / neo4j；**运行时读**）
├── ontology/uploads/          ← 仅**上传暂存**目录（本体真源 TTL 在 03-manual 包的 `ontology/`）
├── logs/                      ← 工具调用日志 mcp_calls_YYYYMMDD.log（**容器里必须可写**）
└── .env                       ← 可选（配置见下；`engine.load_env` 会读它）
```

> 包内**就是**上面的结构（2026-09-22 起不再套内层 `bodhi2-mcp.tar.gz`）：
> `tar -xzf bodhi2-02-mcp-server.tar.gz` → `cd 02-mcp-server` 即「仓库根」；
> 想放到 `/opt/bodhi2` 用：`sudo mkdir -p /opt/bodhi2 && sudo tar -xzf bodhi2-02-mcp-server.tar.gz -C /opt/bodhi2 --strip-components=1`

## 2. 环境变量（唯一配置面）

| 变量 | 默认 | 说明 |
|---|---|---|
| `BODHI_DB_HOST` | 空 | **设了就直连 TCP**（容器/远端部署推荐）；不设则 `docker exec <容器> psql`（本机开发）|
| `BODHI_DB_PORT` | `5432` | |
| `BODHI_DB_USER` | `postgres` | |
| `BODHI_DB_PASSWORD` | 空 | **必填**（或让 `BODHI_WEKNORA_DIR` 指向含 `.env` 的 WeKnora 目录）—— 代码里**不再内置任何默认口令**：env → WeKnora `.env` 的 `DB_PASSWORD`/`POSTGRES_PASSWORD` → 都取不到则报错退出 |
| `BODHI_WEKNORA_DIR` | 空 | WeKnora 部署目录（读它的 `.env` 取口令）；容器部署不需要，直接用 `BODHI_DB_PASSWORD` |
| `BODHI_DB_NAME` | `WeKnora` | |
| `BODHI_DB_CONTAINER` | `WeKnora-postgres` | 仅 docker-exec 模式用 |
| `BODHI_NEO4J_HTTP` | `http://127.0.0.1:7474` | **可选**；不部署 Neo4j 就保持默认 |
| `NEO4J_USERNAME` / `NEO4J_PASSWORD` | `neo4j` / `password` | 同上（仅投影类巡检项用到）|

`.env.example` 已给出容器部署的推荐值（`BODHI_DB_HOST=postgres`）。

## 3. 部署方式 A：容器（推荐；与 WeKnora 同一 compose 网络）

```bash
tar -xzf bodhi2-02-mcp-server.tar.gz     # → 02-mcp-server/（就是仓库根形状）
cd 02-mcp-server
cp .env.example .env && vi .env          # BODHI_DB_HOST=postgres / BODHI_DB_PASSWORD=…
docker build -t bodhi2-mcp:1.0 .         # 构建上下文含 tools/ artifacts/ skills/（包内 Dockerfile）
docker compose -f docker-compose.mcp.yml up -d
docker logs -f bodhi2-mcp                # 应看到 0.0.0.0:8765
```

> **构建卡在 `apt-get update`？（内网常见）** 公网 Debian 源可能不通/极慢。两个办法：
> ① 用内网源重建：`docker build --build-arg APT_MIRROR=<你们的 debian 镜像> -t bodhi2-mcp:1.0 .`
> ② 干脆走**裸机方式 B**（宿主机一定有 psql）：只把源码包解到 `/opt/bodhi2`，用 systemd 起（见 §4）。

`docker-compose.mcp.yml`（包内已给，要点三行）：

```yaml
services:
  bodhi-mcp:
    image: bodhi2-mcp:1.0
    container_name: bodhi2-mcp
    env_file: [.env]
    networks: [weknora-network]      # ← 与 WeKnora-app 同一网络（名字按你们 compose 实际）
    restart: unless-stopped
    ports: ["8765:8765"]             # 可选，便于本机 curl 自检
```

> **MCP URL 用容器内 DNS（`http://bodhi-mcp:8765/mcp`）可天然绕开 SSRF 白名单问题**（见 §6）。

## 4. 部署方式 B：裸机（systemd）

```bash
sudo mkdir -p /opt/bodhi2 && sudo tar -xzf bodhi2-02-mcp-server.tar.gz -C /opt/bodhi2 --strip-components=1
sudo cp /opt/bodhi2/bodhi2-mcp.service /etc/systemd/system/
sudo vi /etc/systemd/system/bodhi2-mcp.service     # 改 WorkingDirectory / Environment
sudo systemctl daemon-reload && sudo systemctl enable --now bodhi2-mcp
journalctl -u bodhi2-mcp -f
```

`bodhi2-mcp.service` 要点：

```ini
[Service]
WorkingDirectory=/opt/bodhi2
Environment=BODHI_DB_HOST=127.0.0.1
Environment=BODHI_DB_PASSWORD=你的口令
ExecStart=/usr/bin/python3 /opt/bodhi2/tools/ontology-mcp/server.py --host 0.0.0.0 --port 8765
```

> 裸机 `BODHI_DB_HOST=127.0.0.1` 需要 Postgres 允许 TCP（不是只监听容器网络）。

## 5. 自检（三步全绿才算通）

```bash
# 1) 进程/端口
curl -s -o /dev/null -w 'bodhi/version %{http_code}\n' http://127.0.0.1:8765/bodhi/version
# 2) 数据库连通（把 BODHI_DB_HOST 换成实际值）
cd /opt/bodhi2 && BODHI_DB_HOST=127.0.0.1 python3 - <<'PY'
import sys; sys.path.insert(0, 'tools/ke-core'); import ke_db
print('知识库：', [r['name'] for r in ke_db.psql_csv(
    "SELECT name FROM knowledge_bases WHERE deleted_at IS NULL ORDER BY created_at")])
PY
# 3) MCP 协议自检（tools/list 必须 14 个工具 + skills() 返回 3 个技能）
#
# 本体维护（前端「上传本体文件 / 加载本体」用的就是这个端口）：
#   POST /bodhi/ontology/upload  {filename, content, module_id, project_wiki,
#                                 write_source(默认 true), compile_after(默认 true),
#                                 apply_after(默认 false)}   ← 默认落真源+编译并生效
#   POST /bodhi/ontology/repair  {kb_id, compile(默认 true), project_wiki(默认 true)}
#   POST /bodhi/ontology/load    {model_id, kb_id, compile, purge, project_wiki}
python3 /opt/bodhi2/02-mcp-server/selfcheck.py --url http://127.0.0.1:8765/mcp
```

`selfcheck.py` 期望输出（14 个工具）：

```
initialize  OK（session=…）
tools/list  OK（14 个）：list_pending_merges, resolve_pending_merge, ontology_types, skills, job_status,
                        service_overview, audit_scan, audit_plan, save_knowledge, doc_outline, extract_state,
                        link_candidates, list_link_candidates, resolve_link_candidate
skills()    OK（3 个：domain_modeling / ea_overview_design / service_detailed_design）
```

> **2026-09-22 变更（写库目标库唯一化）**：`save_knowledge` / `link_candidates` /
> `resolve_link_candidate` / `resolve_pending_merge` / `service_overview(apply=true)` 新增
> `kb_ids`（会话绑定的库清单）与 `confirm_kb_match`；`save_knowledge` 另加 `context`（技能上下文）。
> 多库会话下不指定 `kb_id` → **拒绝写**（`need_kb_selection`）；`kb_id` 模糊命中（uuid 前缀/名称包含）
> → 需 `confirm_kb_match=true`。读工具 `audit_scan` / `list_pending_merges` 也支持 `kb_ids`（多库）。
>
> **2026-09-22 变更（写库目标库唯一化）**：`save_knowledge` / `link_candidates` /
> `resolve_link_candidate` / `resolve_pending_merge` / `service_overview(apply=true)` 新增
> `kb_ids`（会话绑定的库清单）与 `confirm_kb_match`；`save_knowledge` 另加 `context`（技能上下文）。
> 多库会话下不指定 `kb_id` → **拒绝写**（`need_kb_selection`）；`kb_id` 模糊命中（uuid 前缀/名称包含）
> → 需 `confirm_kb_match=true`。读工具 `audit_scan` / `list_pending_merges` 也支持 `kb_ids`（多库）。
>
> **2026-09-22 变更（写库目标库唯一化）**：`save_knowledge` / `link_candidates` /
> `resolve_link_candidate` / `resolve_pending_merge` / `service_overview(apply=true)` 新增
> `kb_ids`（会话绑定的库清单）与 `confirm_kb_match`；`save_knowledge` 另加 `context`（技能上下文）。
> 多库会话下不指定 `kb_id` → **拒绝写**（`need_kb_selection`）；`kb_id` 模糊命中（uuid 前缀/名称包含）
> → 需 `confirm_kb_match=true`。读工具 `audit_scan` / `list_pending_merges` 也支持 `kb_ids`（多库）。
>
> **2026-09-22 变更**：整篇异步抽取工具（`extract_and_save` / `extract_status`）**已移除**（不是"兼容保留"）；
> 领域建模走 `doc_outline` + `extract_state` + `save_knowledge(session=…)` + 候选关联三件套
> （见 `03-manual/docs/agent-design-flow.md` §11.8）。
> 原来的状态查询工具改名为 **`job_status`** —— 现在只服务 `service_overview(apply=true)` 的异步刷新回执。
> 原文留痕：`tools/ontology-mcp/archive/async_extract_retired_2026-09-22.py.txt`。
> 依赖也随之简化：**服务端不调 LLM → 不需要 `openai`**；`rdflib`/`PyYAML` 只有编译本体时才要。

## 6. 在 WeKnora 里注册这个 MCP 服务

**UI**：平台 → MCP 服务 → 新建 → 传输 `streamable-http` → URL `http://bodhi-mcp:8765/mcp`。

**SQL**（可脚本化；`03-manual/AGENTS-SQL.md` 里有配套的智能体注册）：

```sql
INSERT INTO mcp_services (id, tenant_id, name, description, enabled, transport_type, url, created_at, updated_at)
SELECT 'a7c1f0d2-1b2e-4f3a-9c4d-b0d100000001', t.tenant_id, 'bodhi_ontology',
       '本体知识保存工具（抽取/设计落库、巡检、技能）', true, 'streamable_http',
       'http://bodhi-mcp:8765/mcp', now(), now()
FROM tenants t ORDER BY t.id LIMIT 1
ON CONFLICT (id) DO UPDATE SET url = EXCLUDED.url, enabled = true, updated_at = now();
```

> ⚠️ 若 MCP URL 必须写 `http://<宿主机IP>:8765/mcp`（跨主机），WeKnora 会做 **SSRF 校验**：
> 私网地址 / `host.docker.internal` 会被拒（app 日志：`MCP service URL failed SSRF validation`），
> 现象是**智能体的 MCP 工具全部消失**（tool_count 从 15 掉到 5，只剩 wiki 工具）。
> 修法：WeKnora 的 `.env` 里给 `SSRF_WHITELIST_EXTRA` 加上该主机名 / IP / CIDR，然后重建 app。
> 详见 `03-manual/TROUBLESHOOTING.md` §1。

## 7. 升级 / 回滚

```bash
# 升级
tar -xzf bodhi2-mcp-<新版>.tar.gz -C /opt/bodhi2 && cd /opt/bodhi2/02-mcp-server
docker build -t bodhi2-mcp:<新版> . && docker compose -f docker-compose.mcp.yml up -d
# 回滚：把 current 指向旧 tag（数据都在 Postgres，服务本身无状态）
docker tag bodhi2-mcp:<旧版> bodhi2-mcp:current && docker compose -f docker-compose.mcp.yml up -d
```

## 8. 日常观测

- **工具调用日志**：`logs/mcp_calls_YYYYMMDD.log` —— 一行一次调用（时间 / 工具 / 耗时 ms / 入参摘要 / 结果摘要）。
  智能体说"我调了 X 工具"，先在这里核对是否真调了、参数对不对；
- 容器日志：`docker logs bodhi2-mcp --tail 100`（handler 异常会有 `[mcp] … 失败`）；
- 只读自检接口（同一端口）：
  `GET /bodhi/overview?kb_id=<kb>`（服务详设总览）、`GET /bodhi/crud?kb_id=&slug=`（CRUD 矩阵）、
  `GET /bodhi/audit?kb_id=`（一致性巡检）、`GET /bodhi/relations?kb_id=&slug=`（出边/入边）。
