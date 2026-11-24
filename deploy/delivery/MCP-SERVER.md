# MCP 服务部署（bodhi2 ontology-mcp）

> 交付物：`bodhi2-02-mcp-server.tar.gz`（**包内就是「仓库根」**：`tools/ artifacts/ skills/ logs/` —— 不再套内层 tar，
> 解包到 `/opt/bodhi2` 后用 systemd 启动；**零第三方 Python 依赖**：只用标准库 + `psql` 客户端）
> 作用：给 WeKnora 提供 **51 个 MCP 工具（2026-11-23）**（领域建模分批 / 设计落库 / 巡检 / 技能目录 / 总览页 / 候选关联 / 任务回执 / **类型迁移两段式** / **跨库上下文映射·渲染·概念页写路径·权威副本治理** / **结构化数据批量建模（探表·计划·执行·刷新）** / **巡检一步硬删** / **文档评审（规则清单·图检索·参考规范·结论落页）** / **数据属性写口（`page_attributes_set`，方案 D）** / **多模态识别（`image_extract` 取文档内嵌图）** / **运维·图→正文重建（`rebuild_wiki_from_graph`）** / **运维·清别名噪音（`clean_alias_noise`）** / **运维·知识库导出/导入（`kb_export` + `kb_import`，含 PG+Neo4j 六项一致性比对）** …）
> 依赖：Python ≥ 3.10、`postgresql-client`（提供 `psql`）、可读 WeKnora 的 Postgres；Neo4j **可选**。
>
> **不需要 PyYAML**：技能的 front-matter 优先用 PyYAML 解析，取不到时走 `tools/ke-core/ke_yamlmini.py`
> 的零依赖子集解析（我们逐键比对过，5 个技能结果一致）。**实测**：在只有 Python + psql 的干净镜像里
> `selfcheck.py` 全绿（initialize / tools/list **51 个** / skills() **3 个**）。

## 0. 02 包组件矩阵（2026-11-23）

`bodhi2-02-mcp-server.tar.gz` 是 **bodhi 运行时完整包**，一个安装器、三个常驻服务 + 一个初始化组件：

| 组件 | 端口 | 作用 | 配置 | 选择性启动 |
|---|---:|---|---|---|
| `bodhi-mcp` | 8765 | 本体/知识/语义提取工具 | `config/common.env` + `config/mcp.env` | `./start.sh mcp` |
| `agent_diag` | 8771 | 只读 SQL MCP（`analytics_sql`） | `config/common.env` + `config/agent-diag.env` | `./start.sh diag` |
| `analytics-admin` | 8781 | 业务对象映射/语义层管理台 | `config/common.env` + `config/analytics-admin.env` | `./start.sh admin` |
| `diag-init` | — | diag/analytics 建表、视图、种子（一次性） | `config/common.env`（可选 `diag-init.env`） | `./init-db.sh all` |

安装/初始化/启动：

```bash
```bash
sudo mkdir -p /opt/bodhi2
sudo tar -xzf bodhi2-02-mcp-server.tar.gz -C /opt/bodhi2 --strip-components=1
cd /opt/bodhi2
sudo bash install.sh --components mcp,diag,admin --init-db
./status.sh
```

对外部服务：`it_platform` 8770 在 04 包；`dbbi_data` gateway 8772 在独立 gateway 包；02 不含它们。
详见 `DIAG-CONFIG.md` / `ANALYTICS-CONFIG.md` / `ANALYTICS-ADMIN.md`。

**结构化数据批量建模（2026-09-29/30 新增，5 个工具）**：技能负责「理解」（从用户对表结构的中文描述得出
有哪些类/关系/键列/列→属性映射），工具负责「确定性执行」，**一次只处理一个类（含数据属性）或一条关系**：
`import_probe`（只读探表：表头/抽样/列前缀/疑似主键）→ `import_plan`（只读：校验 + 影响面 + `ticket`；
`mapping` 列→属性、`enums` 枚举列→关系、`unknown_to_description` 未知列口径）→ `import_apply`（按 `ticket` 写，
500 行/事务，**幂等**：内容未变零写入）→ `import_state`（账本 + `remaining`，循环到收敛）→
`import_refresh`（存量页元数据/目录对齐）。页模板：`定义 + 属性 + 原文依据`（**逐字**该行原始值，满足巡检 F3），
`id = uuid5(bodhi-element:<kb>|<slug>)`，`wiki_path = 目录路径/标题` + `folder_id`（**前端目录树靠它**）。
HTTP：`GET /bodhi/import/{probe,state}`、`POST /bodhi/import/{plan,apply,refresh}`。

**巡检一步硬删（2026-09-30）**：`audit_purge` —— 只要对该库**有写权限**即可执行（不再要求后台
`plan → apply --confirm`），支持 `slugs`（精确）或 `kinds`（按种类）；**先 `dry_run` 看清单**。
结构化导入页与评审页在清理计划里**豁免**（来源记在元数据），不会被误删。
HTTP：`POST /bodhi/audit/purge`。

**文档评审（2026-09-30 新增，4 个工具）**：对选定文档按一条业务策略**逐条**评业务规则 ——
`rules_of_policy`（策略下规则：级别/适用范围/实现方式/参考规范）→ 规则「实现方式」为 **LLM软规则**则交给大模型判定、
为**图检索**则用 `graph_query`（**只读** Cypher 白名单）出结论；有参考规范先 `reference_lookup`
（知识库文档/页 → 可选抓 URL；取不到就标「不可得」不编造）；结论用 `review_apply` 写成**一页**评审报告
（逐条结论 + 逐字证据 + `bmm:promotesDirective → 策略页`）。
HTTP：`GET /bodhi/review/{rules,graph,reference}`、`POST /bodhi/review/apply`。

**权威 / 副本（2026-09-29 新增，2 个工具）**：同义知识**认定一个领域为权威**，其它领域**只读**、
**只能从权威复制** —— 工具面 `context_authority`（只读：谁是权威、副本漂移、ticket）+
`context_authority_apply`（`action=decide|pull|detach`，两段式写；HTTP 对应
`GET /bodhi/context/authority`、`POST /bodhi/context/authority/{decide,pull}`）。
副本页的本地写会被服务端**拒绝**（`ke_pages.replica_guard`），错误里直接给「从权威复制」的命令。
**解绑（2026-10-22）**：`action=detach`（`kb=<副本库>`，`apply:false` 先预览）把该库从
「## 权威与副本」表摘掉 + 副本页自述改 `role=detached` ⇒ 该库**恢复可本地编辑**；
**不删副本页**、幂等（重复调回 `already_detached`）；概念页「权威与副本」表是**事实源**
（缓存 `replica_protect` 与只读视图都按表派生）。

---

## 0. 它到底怎么工作（先理解，再部署）

**本体类型迁移是两段式（2026-09-27 新增）**：改本体类型 = **迁移 slug（`模块/类/名称`）+ 联动引用**
（正文引用 / `## 溯源` / `page_metadata` / **建模会话状态**；关系走 Neo4j 图，`out_links`/`in_links` 已废弃），
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
- 它**不调 LLM**：抽取/设计由 WeKnora 侧的智能体做，MCP 只负责"规范化落库 + 本体校验 + 巡检"；
- 技能包源在 `skills/<id>/SKILL.md`，由 `tools/skills/bundle.py` 打成 `skills/dist/*.zip`，
  再由 `deploy/weknora-fork/register_native_skills.py` 安装到 WeKnora 沙箱；MCP `skills()` 仅为迁移期兼容入口。

## 1. 目录布局（必须保持这个形状）

`server.py` 用 `Path(__file__).parents[2]` 定位"仓库根"，所以包内结构必须原样：

```
/opt/bodhi2/                   ← systemd 的"仓库根"
├── tools/ontology-mcp/        ← server.py / refresh_design.py / mdview.py / graph_page.py
├── tools/ke-core/             ← ke_db / ke_pages / ke_ontology / ke_audit / ke_docs / ke_admin / ke_neo4j
├── tools/ontology-extract/    ← ontology_wiki.py（本体知识库投影；server 会 import）
├── skills/<id>/SKILL.md       ← active 技能包源（沙箱 bundle 的源）
├── skills/dist/*.zip          ← 可直接安装到 WeKnora sandbox 的技能 bundle
├── artifacts/                 ← 编译产物（ontology_index.json / prompts / json_schema / neo4j；**运行时读**）
├── ontology/uploads/          ← 仅**上传暂存**目录（本体真源 TTL 在 03-manual 包的 `ontology/`）
├── logs/                      ← 工具调用日志 mcp_calls_YYYYMMDD.log（**宿主机必须可写**）
└── .env                       ← 可选（配置见下；`engine.load_env` 会读它）
```

> 包内**就是**上面的结构（2026-09-22 起不再套内层 `bodhi2-mcp.tar.gz`）：
> `sudo tar -xzf bodhi2-02-mcp-server.tar.gz -C /opt/bodhi2 --strip-components=1` 即得到「仓库根」；
> 想放到 `/opt/bodhi2` 用：`sudo mkdir -p /opt/bodhi2 && sudo tar -xzf bodhi2-02-mcp-server.tar.gz -C /opt/bodhi2 --strip-components=1`

## 2. 环境变量（唯一配置面）

| 变量 | 默认 | 说明 |
|---|---|---|
| `BODHI_DB_HOST` | `127.0.0.1` | **systemd 模式必填**；指向宿主机可达的 Postgres（不要写容器 DNS）|
| `BODHI_DB_PORT` | `5432` | |
| `BODHI_DB_USER` | `postgres` | |
| `BODHI_DB_PASSWORD` | 空 | **必填**。放在 `/opt/bodhi2/config/common.env`，由 systemd `EnvironmentFile` 注入；代码里不再内置默认口令，取不到则 DB 类工具全部失败 |
| `BODHI_WEKNORA_DIR` | 空 | 可选；systemd 直接用 `BODHI_DB_PASSWORD`，通常不需要 |
| `BODHI_DB_NAME` | `WeKnora` | |
| `BODHI_DB_CONTAINER` | `WeKnora-postgres` | 仅旧 docker-exec 兜底；systemd 不依赖 |
| `BODHI_NEO4J_HTTP` | `http://127.0.0.1:7474` | **可选**；不部署 Neo4j 就保持默认 |
| `NEO4J_USERNAME` / `NEO4J_PASSWORD` | `neo4j` / `password` | 同上（仅投影类巡检项用到）|
| `BODHI_ONTOLOGY_KB_ID` | 空 | **本体模型知识库 id**（`env` 是唯一权威；不配则按 `wiki_config.bodhi_ontology_kb` → 库名 → 内容探测兜底）|
| `BODHI_ONTOLOGY_KB_NAME` | `企业本体模型` | 同上（库名匹配用）|
| `BODHI_CONCEPT_KB_ID` | 空 | **企业共享概念模型**知识库 id（跨库上下文映射的落点，见 `KB-CONFIG.md`）|
| `BODHI_CONCEPT_KB_NAME` | `企业共享概念模型` | 同上（库名匹配用）|
| `BODHI_TENANT_ID` | 空 | **可选**（2026-10-28 起权限控制已停用：`assert_can_write` 一律放行、`mode="open"`）；保留它只用于回执/审计里的"调用者租户"记录，不再阻断写 |
| `BODHI_OPEN_WRITE_ACL` | `1` | **写权限总开关**：`1`=停用权限判定（内网口径，默认）；`0`=恢复严格判定（属主 / 组织共享 editor·writer·admin，身份缺失 fail-closed）|
| `BODHI_EXT_SERVICE_URLS` | 空 | 外部 MCP 服务的地址覆盖（`ext_describe` 取契约用）：`{"it_platform":"http://127.0.0.1:8770/mcp"}`；不配则读 PG `mcp_services`（`name='it_platform'`）|
| ~~`BODHI_SIMILARITY_MERGE`~~ | — | **已删除**（2026-10-10 相似度合并永久取消）：一律按 `(kb,slug)` 幂等 upsert（`merged` = "同 slug 覆盖"）；相似知识治理走巡检 `audit_scan`（S1/S2）。**落库只走契约内核一条路**（`BODHI_SAVE_ENGINE` 已删除）|
| `BODHI_VALIDATE_FROM_GRAPH` | `1` | 关系 domain/range 校验的**类型读**：`1`=图优先（1 次 Neo4j 替掉每条边 2 次 psql，图里没有才回落 PG）；`0`=只用 PG |
| `BODHI_DEFER_MAINTENANCE` | `1` | 重型维护（全库目录树重建 / CRUD 矩阵）延迟到前端按需刷新（`/bodhi/folders/refresh`）；`0`=落库时同步做 |
| `BODHI_DEBUG_ENGINE` | `0` | 内核路径内部调试打印（排障用）|

> **落库引擎切换（2026-10-10）**：`BODHI_SAVE_ENGINE=kernel` 与 legacy **行为等价已验**（同批回执逐条一致、
> 正文 strip 后逐字相同）；切换只需在 `.env` 改一行 + **`systemctl restart bodhi2-mcp`**（改了不重启=跑旧字节码，
> 这是本仓最容易踩的坑）。回退：改回 `legacy` 再重启。**巡检清理**（2026-11-02 起**只做单库**）：
> `kinds=graph` = 该库在图上的孤儿节点（PG 里该库已软删/不存在）；`kinds=all` 只含
> `pages/rows/edges/graph`，**不含 `init`**；跨库动作 `deleted_kb_residue` **已取消**
> （清理软删库用 `kinds=init, kb_id=<该库>`）。WeKnora 删库只删 PG 记录、不删图，
> 所以按库跑 `graph` 即可把该库的图与 PG 对齐。

`config/common.env.example` 已给出宿主机推荐值（`BODHI_DB_HOST=127.0.0.1`）。

### 2.1 配置只有一组（`config/*.env`）

02 三个服务都由 systemd 的 `EnvironmentFile` 读取：
- `/opt/bodhi2/config/common.env`：数据库连接（三服务共用）
- `/opt/bodhi2/config/mcp.env`：本体库/概念库/租户/Neo4j
- `/opt/bodhi2/config/agent-diag.env` / `analytics-admin.env`：端口等组件变量

```bash
# CLI / 一次性脚本：与 service 同源
cd /opt/bodhi2
set -a; . config/common.env; . config/mcp.env; set +a
python3 tools/ke-core/ke_context.py contexts --print 400
```

安装器首次运行会生成 `config/*.env` 并退出，改完数据库地址/口令后重跑 `sudo bash install.sh`。

更改配置后：

```bash
sudo systemctl restart bodhi2-mcp bodhi2-agent-diag bodhi2-analytics-admin
```

**所有 `BODHI_*` 变量口径统一（2026-10-04）**：**进程环境变量优先，其次 `.env` 文件**
（`BODHI_WEKNORA_DIR/.env` → `…/WeKnora/.env` → 服务目录 `.env`）——
所以「`config/common.env` 里只配三个库参数 + `BODHI_DB_PASSWORD`」也能跑起来；systemd 会原样注入。

| 要连的 | 读取顺序 |
|---|---|
| Postgres 口令 | `/opt/bodhi2/config/common.env` 的 `BODHI_DB_PASSWORD`（systemd 注入）|
| Postgres 位置 | `/opt/bodhi2/config/common.env` 的 `BODHI_DB_HOST/PORT/USER/NAME`；systemd 模式必须显式设置 host |
| Neo4j | `BODHI_NEO4J_HTTP` / `BODHI_NEO4J_DB`（默认 `http://127.0.0.1:7474` / `neo4j`）+ `NEO4J_USERNAME` / `NEO4J_PASSWORD`（**env 或 `.env` 均可**）|

> **不需要**在本服务里配任何 LLM / 嵌入 / 本体编译参数：MCP **不调用大模型**，模型与向量能力都由 WeKnora 提供。

**两条硬规矩**：
1. **改 `config/*.env` 后**：`systemctl restart bodhi2-mcp bodhi2-agent-diag bodhi2-analytics-admin`；
2. **改代码后**：也要 `systemctl restart bodhi2-mcp`（否则服务仍跑旧代码——我们踩过：接口少了字段，前端看不到状态徽标）。

自检一行：
```bash
python3 -c "import sys;sys.path.insert(0,'tools/ke-core');import ke_db,ke_ontology,ke_context;\
print('tenant=',ke_db.caller_tenant(),'ontology=',ke_ontology.resolve_ontology_kb()['source'],\
'concept=',ke_context.concept_kb()['source'],'db_pwd_len=',len(ke_db.DB_PASSWORD or ''))"
# 期望类似：tenant= 10000 ontology= env concept= env db_pwd_len= 14
```

## 3. 部署（systemd-only）

r6 起 02 包**只用 systemd**，不再提供 Dockerfile / compose。三个常驻服务共享同一份 `/opt/bodhi2/tools/ke-core`：

```bash
sudo mkdir -p /opt/bodhi2
sudo tar -xzf bodhi2-02-mcp-server.tar.gz -C /opt/bodhi2 --strip-components=1
cd /opt/bodhi2
sudo bash install.sh --components mcp,diag,admin --init-db
```

安装器会：
- 检查 `tools/ke-core/ke_db.py`、`tools/analytics-admin/server.py`、`tools/domain-mcp/analytics_sql/server.py`、51 个工具定义；
- 生成 `config/*.env`；改好 `config/common.env` 的 `BODHI_DB_HOST`（宿主机可达地址）后重跑；
- 安装 `bodhi2-*.service` 到 `/etc/systemd/system` 并 `enable --now`；
- 检查 8765/8771/8781 端口，避免旧容器/旧进程继续占用；
- 自检 `selfcheck.py --expect-tools 51` 和 8771/8781 HTTP。

启停：

```bash
sudo systemctl status bodhi2-mcp bodhi2-agent-diag bodhi2-analytics-admin
sudo systemctl restart bodhi2-mcp
/opt/bodhi2/status.sh
```

日志固定写入 `/opt/bodhi2/logs/`：

```text
bodhi2-mcp.log / bodhi2-mcp.err.log
bodhi2-agent-diag.log / bodhi2-agent-diag.err.log
bodhi2-analytics-admin.log / bodhi2-analytics-admin.err.log
```

`logrotate.bodhi2` 会按天轮转；`journalctl -u bodhi2-mcp -f` 仍可用。

## 4. 配置与目录

```text
/opt/bodhi2/
├── tools/              # 三服务共享（ontology-mcp / ke-core / analytics-admin / domain-mcp/analytics_sql）
├── sql/                # diag + analytics 初始化
├── config/             # common.env + 每服务 env（systemd EnvironmentFile）
├── artifacts/          # 本体编译产物（运行时读）
├── skills/             # active 技能源 + dist bundle
├── logs/               # 服务 stdout/stderr + mcp_calls_*.log
└── bodhi2-*.service
```

配置真源是 `/opt/bodhi2/config/common.env` + `config/<service>.env`；`.env.example` 只作 CLI/兼容参考。

> 若 WeKnora Postgres 只在 Docker 内，先把 `127.0.0.1:5432` 映射到宿主机；systemd 服务不会用 Docker DNS。

## 5. 自检（三步全绿才算通）

```bash
# 1) 进程/端口
curl -s -o /dev/null -w 'bodhi/version %{http_code}\n' http://127.0.0.1:8765/bodhi/version
# 2) MCP 协议（必须是 51 个工具）
python3 /opt/bodhi2/selfcheck.py --url http://127.0.0.1:8765/mcp --expect-tools 51
# 3) 管理台 / diag
curl -fsS http://127.0.0.1:8771/
curl -fsS http://127.0.0.1:8781/ | head
```

`selfcheck.py` 期望：

```text
initialize  OK
tools/list  OK（51 个）
skills()    OK（3 个）
```

旧文档里的 38/44 个工具是历史口径；r6 发布以 **51** 为准。

## 6. 在 WeKnora 里注册这个 MCP 服务

**UI**：平台 → MCP 服务 → 新建 → 传输 `streamable-http` → URL `http://<宿主机IP>:8765/mcp`。

**SQL**（可脚本化；`03-manual/AGENTS-SQL.md` 里有配套的智能体注册）：

```sql
INSERT INTO mcp_services (id, tenant_id, name, description, enabled, transport_type, url, created_at, updated_at)
SELECT 'a7c1f0d2-1b2e-4f3a-9c4d-b0d100000001', t.tenant_id, 'bodhi_ontology',
       '本体知识保存工具（抽取/设计落库、巡检、技能）', true, 'streamable_http',
       'http://<宿主机IP>:8765/mcp', now(), now()
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
systemctl stop bodhi2-mcp bodhi2-agent-diag bodhi2-analytics-admin
tar -xzf bodhi2-mcp-<新版>.tar.gz -C /opt/bodhi2 --strip-components=1
cd /opt/bodhi2 && bash install.sh --no-start
systemctl start bodhi2-mcp bodhi2-agent-diag bodhi2-analytics-admin
# 回滚：恢复旧版源码目录后重复 stop/解包/install/start（数据都在 Postgres）
```

## 8. 日常观测

- **工具调用日志**：`logs/mcp_calls_YYYYMMDD.log` —— 一行一次调用（时间 / 工具 / 耗时 ms / 入参摘要 / 结果摘要）。
  智能体说"我调了 X 工具"，先在这里核对是否真调了、参数对不对；
- 错误日志：`tail -n 100 /opt/bodhi2/logs/bodhi2-mcp.err.log`（handler 异常会有 `[mcp] … 失败`）；
- 只读自检接口（同一端口）：
  `GET /bodhi/overview?kb_id=<kb>`（服务详设总览）、`GET /bodhi/crud?kb_id=&slug=`（CRUD 矩阵）、
  `GET /bodhi/audit?kb_id=`（一致性巡检）、`GET /bodhi/relations?kb_id=&slug=`（出边/入边）。
