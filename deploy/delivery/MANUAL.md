# bodhi2 交付手册（安装部署总指引）

> 版本：`1.0`（生成时间见 `MANIFEST.json`）
> 适用：**你们已有可用的 WeKnora 镜像部署**（app + 前端 + Postgres 已跑起来），要在此基础上接入 bodhi2 的本体建模与设计能力。
> 本手册是**总入口**；每个包还有自己的详细手册（见下表）。

## 0. 包里有什么

> 手册位置：**根目录只放本文件（`MANUAL.md`）**；各包自带自己的部署手册（`FRONTEND.md` 在 01、`MCP-SERVER.md` 在 02），
> `ONTOLOGY-KB.md` / `KB-CONFIG.md` / `TROUBLESHOOTING.md` / `AGENTS-SQL.md` 与 `docs/`、`ontology/`（**本体真源 TTL**）、
> 维护工具都在 **`bodhi2-03-manual.tar.gz`** 里；运行 SQL / seed 在 **02 包**（`sql/` + `init-db.sh`）。
> 交接/归档以包内为准（单一来源是仓库 `deploy/delivery/*.md`，
> `MANIFEST.json` 的 `manual_sha256` 记录了每份哈希）。

| 包 | 内容 | 详细手册 |
|---|---|---|
| `bodhi2-01-frontend.tar.gz` | 补丁后的 UI 镜像（`weknora-ui:bodhi2`）+ nginx 模板 + compose overlay + 验收脚本 + 打补丁脚本（若你们要自己构建）| `FRONTEND.md` |
| `bodhi2-02-mcp-server.tar.gz` | **bodhi 运行时完整包（systemd-only）**：`bodhi-mcp` 8765 + `agent_diag` 8771 + `analytics-admin` 8781 + `diag-init`（diag/analytics 建表/视图/种子）+ systemd 安装/启停（不含 Docker） | `MCP-SERVER.md` / `DIAG-CONFIG.md` / `ANALYTICS-CONFIG.md` / `ANALYTICS-ADMIN.md` |
| `bodhi2-03-manual.tar.gz` | **文档 + 本体真源**：`docs/` + `ontology/`（TTL/词表/shapes）+ 维护工具；**不含 SQL/seed**（运行 SQL 在 02） | 本文件 + `ONTOLOGY-KB.md` + `KB-CONFIG.md` + `TROUBLESHOOTING.md` |
| `bodhi2-04-it-platform-mcp.tar.gz` | **it_platform 外部服务**（8770，独立包） | `04-it-platform-mcp/README.md` |
| `bodhi2-mcp-gateway-<ver>.tar.gz` | **dbbi_data gateway**（8772，独立包） | `tools/mcp-gateway/README.md` |
| `MANIFEST.json` / `SHA256SUMS` | 版本、文件清单、校验和 | — |

**一张图看懂彼此关系**

```
                 ┌─────────────── 你们已有的 WeKnora 部署 ───────────────┐
   用户 ──► 前端(替换为 bodhi2 UI) ──► app ──► Postgres ◄──┐            │
                 └───────────────────────────────────────┼────────────┘
                                                         │ psql
                            MCP 服务(bodhi2-mcp:8765) ───┘
                                    ▲
                                    │ MCP(HTTP)
                                  app/智能体 ── 按 skills/<id>/SKILL.md 干活
                                    │
                        本体模型知识库（类型真源；由用户导出/导入，或按 TTL 编译/投影生成）
                        业务知识库（你们的文档 → 实例页，由智能体写入）
```

## 1. 部署顺序（照这个顺序做，每步都有验收）

| 步 | 动作 | 验收（不通过就别往下走）|
|---|---|---|
| **1** | 部署 02 运行时完整包（`bodhi2-02`）：解到 `/opt/bodhi2` 后 `sudo bash install.sh --components mcp,diag,admin --init-db` | `bodhi2-mcp` 8765、`bodhi2-agent-diag` 8771、`bodhi2-analytics-admin` 8781 全部 healthy；selfcheck 51 个工具 |
| **2** | 在 WeKnora 里注册 MCP 服务（UI 或 SQL）：`bodhi_ontology` → 8765；`agent_diag` → 8771 | 平台 → MCP 服务里能看到两条，工具可列出 |
| **3** | 导入本体模型知识库（`bodhi2-03`：**TTL + 用户导出/导入**；不使用 seed）| 页数/类型符合你们的 TTL 版本 |
| **4** | 建业务库 + 配 `wiki_config`（`KB-CONFIG.md` §3）| 上传一篇文档能出 wiki 页 |
| **5** | 替换前端（`bodhi2-01`）+ 挂载 nginx 模板 | `deploy_frontend.sh check` 全绿；类型下拉能看到 `easvc:*` |
| **6** | 注册智能体（`AGENTS-SQL.md`：提示词 + 两个库 + MCP + 工具清单）| 让智能体跑一轮，能正常列技能/调 MCP |
| **7** | 端到端验证 | 见 §4「验收清单」 |

> **只想要本体建模/设计能力、暂时不动前端**也可以：1→2→3→4→6 就能跑（前端替换只影响"类型下拉/本体图谱 tab/关系面板"这些可视化）。

## 1.1 配置放在哪（**只有一处**，2026-09-29 起）

**02 systemd 服务的配置 = `/opt/bodhi2/config/*.env`**（安装器从 `config/*.env.example` 生成；不再用 `.env` 驱动服务）：

```bash
cd /opt/bodhi2
vi config/common.env      # BODHI_DB_HOST=127.0.0.1 / 可达 IP；数据库口令
```

| 谁 | 怎么读配置 |
|---|---|
| MCP 服务（systemd） | `EnvironmentFile=/opt/bodhi2/config/common.env` + `config/<service>.env` |
| CLI / 运维脚本 | `cd /opt/bodhi2 && set -a; . config/common.env; . config/mcp.env; set +a` |

- **凭据**：放在 `/opt/bodhi2/config/common.env` 的 `BODHI_DB_PASSWORD`；服务由 systemd 直接注入，CLI 先 source 同一文件。
  Neo4j 用 `config/mcp.env` 的 `BODHI_NEO4J_HTTP`/`BODHI_NEO4J_DB`（默认 `127.0.0.1:7474`/`neo4j`）。
- **不需要**配 LLM / 嵌入 / 本体编译参数：MCP **不调用大模型**（模型与向量能力都由 WeKnora 提供）。
- **两条硬规矩**：改 `config/*.env` → `systemctl restart bodhi2-mcp bodhi2-agent-diag bodhi2-analytics-admin`；
  **改代码 → 也要 restart**（否则服务跑旧代码）。
- 详见 `MCP-SERVER.md` §2 / §2.1（含逐项变量表与自检命令）。

## 2. 环境要求

| 项 | 要求 |
|---|---|
| WeKnora | 你们内部网已部署可用（app + 前端 + Postgres）；app 版本与我们的 UI 补丁**尽量一致**，不一致走 `FRONTEND.md` §3 自行构建 |
| 容器运行时 | WeKnora app / 前端 / Postgres 仍可用 Docker；**02 三个服务不用 Docker** |
| MCP 服务 | systemd + Python ≥ 3.10（宿主 `/usr/bin/python3`）+ `postgresql-client`；**零第三方 Python 依赖** |
| 数据库 | 能连 WeKnora 的 Postgres（同库同表，MCP 直连，不另建库）|
| 网络 | WeKnora app 容器能访问宿主机 `:8765/:8771`；宿主机能访问 Postgres `:5432`（必要时映射 127.0.0.1:5432）|
| 可选 | Neo4j（本体投影）——**本交付不需要**，不部署也不影响主流程 |

## 3. 一次性配置清单（要改的东西全在这）

> **发布包不含任何密钥**：没有 LLM API key、没有访问令牌、没有数据库口令（打包前跑过
> `tools/delivery/scan_secrets.py` 审计）。下面这些**都要在内网重新配置**：

| 需要重配的东西 | 配在哪 | 说明 |
|---|---|---|
| **LLM 模型的 API Key** | **内网 WeKnora 自己的 UI/数据库**（平台 → 模型）| 只落在你们的库里，**从不进交付包**；MCP 侧只用到 `model_id`（UUID）|
| **数据库口令** | `/opt/bodhi2/config/common.env` 的 `BODHI_DB_PASSWORD` | 代码里**不再内置任何默认口令**：env → WeKnora `.env` 的 `DB_PASSWORD`/`POSTGRES_PASSWORD` → 都没有则报错退出 |
| MCP 服务 URL | WeKnora 平台 → MCP 服务（`http://<宿主机IP>:8765/mcp`）| systemd 服务在宿主机，不能再用容器 DNS `bodhi-mcp` |
| `SSRF_WHITELIST_EXTRA` | WeKnora `.env` | **必须**加宿主机 IP；见 `TROUBLESHOOTING.md` §1 |
| **本体模型知识库的识别**（可选但推荐）| `/opt/bodhi2/config/mcp.env` 的 `BODHI_ONTOLOGY_KB_ID`**或**给库打标记 | 见 `ONTOLOGY-KB.md` §2 末与 `FRONTEND.md` §7：不配也能按库名/内容自动认 |
| Neo4j 口令（可选，本交付不需要）| `config/mcp.env` 的 `NEO4J_PASSWORD` | 不部署 Neo4j 就不用管 |

1. `/opt/bodhi2/config/common.env`：`BODHI_DB_HOST/PORT/USER/PASSWORD/NAME`；Host 用 `127.0.0.1` 或可达 IP；
2. WeKnora `.env`：`SSRF_WHITELIST_EXTRA` 加上宿主机 IP；
3. `mcp_services` 一行：URL 指向 `http://<宿主机IP>:8765/mcp`（SQL 在 `MCP-SERVER.md` §6）；
4. `custom_agents` 一行：提示词 + `knowledge_bases`（两个库）+ `mcp_services` + `allowed_tools`（modeler **27** / kb-ops **12**，见 `AGENTS-SQL.md` §2）；
   **SQL 在 `/opt/bodhi2/sql/agent_registration/`**（`AGENTS-SQL.md` 是说明；可回滚）；
5. `knowledge_bases.wiki_config`：抽取指令按你们领域改（`KB-CONFIG.md` §3）；
6. 前端 nginx 模板挂载 + `image: weknora-ui:bodhi2`（`FRONTEND.md`）。
7. **ext 侧（04 包，独立服务 8770）**：`sudo bash 04-it-platform-mcp/install.sh` → `python3 04-it-platform-mcp/selfcheck.py`；
   智能体工具面/提示词**不手改 SQL**，用 `tools/delivery/apply_config.py --check|--apply`（真源
   `deploy/weknora-fork/config/agent_tools.yaml` + `agent_review_prompt.md`）。
   **哪些能改、改哪、怎么生效、怎么校验 ⇒ 一律看 `CONFIG-MATRIX.md`** ✓

## 4. 验收清单（交付意义的"通过标准"）

| # | 检查 | 期望 |
|---|---|---|
| 1 | `selfcheck.py --url http://…:8765/mcp` | `tools/list OK（51 个）` + `skills()` 可列出 |
| 2 | 智能体一轮只读任务 | `logs/mcp_calls_*.log` 有 `skills`/`audit_scan` 记录（模型侧工具数见 `AGENTS-SQL.md`） |
| 3 | 本体模型库 | **178 页**（类 50 / 本体关系 83 / **数据属性 36** / 模块 6 / 轻量版 2 / 索引 1）；目录每模块下只有「数据属性」「本体关系」；`curl <mcp>/bodhi/ontology/models` 返回 5 个模型 |
| 4 | 业务库上传+抽取 | 页面类型都在本体里，且**有溯源**（`source_refs` 非空 **或** 图上已挂 `bmm:sourceSession` 会话边）|
| 5 | `curl <mcp>/bodhi/audit?kb_id=<业务库>` | 无 **C1/C3** 类"无来源"发现；A1/A2 若有，按提示修 |
| 6 | 前端 | 登录正常；本体图谱 tab 出图；关系面板出边可改；类型下拉含 `easvc:*` |
| 7 | 设计流程 | **设计类技能（概要/详细设计）重构中，本版不含** —— `service_overview` 等工具仍在；重构完成前由用户在对话里直接说明步骤 |
| 8 | **结构化批量建模**（`structured_modeling` 技能）| 给一张 Excel + 中文说明：`import_probe` → 逐目标 `import_plan`/`import_apply` → `import_state` 的 `remaining` 为空；页有「定义/属性/原文依据」；**前端目录里能看到这批页**（`wiki_path`=目录路径/标题、`folder_id` 非空）|
| 9 | **文档评审**（`document_review` 技能）| 选文档 + 选策略：`rules_of_policy` 出规则清单（级别/范围/实现方式）→ 逐条判（LLM软规则 / `graph_query`）→ `review_apply` 写出 `review/<文档>-<策略>` 页（逐条结论 + 逐字证据 + `promotesDirective → 策略页`）；**图里信息**（部署图/技术栈截图）先 `image_extract` 取文档内嵌图 → 多模态读图（`multimodal_extraction` 技能）|
| 10 | 巡检清理 | `audit_purge(kb_id, dry_run=true)` 先看 `per_kind` 清单（导入页/评审页应被**豁免**）→ 确认后再执行（**一步硬删**，不可逆）|
| 11 | **图像识别**（`multimodal_extraction` 技能 + `image_extract` 工具）| `tools/list` 含 `image_extract`；对**含内嵌图**的文档调 `image_extract(kb_id, knowledge_id)` → `images_found ≥ 1` 且带 `resource://` 引用（样例1 应=4）；识别结果按**契约 v0**（`nodes/edges/annotations/ambiguity/knowledgeGaps/todo`，每条带 `bbox/evidence`+`confidence`）与**门禁 1–4** 输出；图像识别挡板页 `stubStatus=已下线`（工具页无 `toolHasStub` 边）|

## 5. 日常运维

| 场景 | 命令 |
|---|---|
| 看智能体到底调了什么 | `tail -f logs/mcp_calls_YYYYMMDD.log`（时间/工具/耗时/入参/结果摘要）|
| 一致性巡检 | `curl "<mcp>/bodhi/audit?kb_id=<kb>"`；清理用 `audit_purge`（**有库写权限即可一步硬删**，先 `dry_run=true` 看 `per_kind` 清单再执行）|
| 本体演进 | 改 TTL（**03 包** `ontology/`）→ `refresh_ontology_kb.sh`（编译+投影；借用 **02 包** `tools/`，解到同一父目录即可）；类清单变了记得重建前端 |
| 技能演进 | 改 `skills/<id>/SKILL.md` → `python3 tools/skills/bundle.py` → `register_native_skills.py --upload`（沙箱安装；MCP `skills()` 仅迁移期兼容）|
| 重建过 app 容器 | `docker restart WeKnora-frontend`（交付模板已含运行期解析，仍建议一把）|
| 备份 | Postgres 常规备份即可（`wiki_pages` / `knowledges` / `mcp_services` / `custom_agents`）；MCP 服务本身无状态 |

## 6. 排错

先看 `TROUBLESHOOTING.md`（都是我们真实踩过的：SSRF 白名单、nginx 上游解析、工具 EOF、`applied=false`、
C1 无来源、A5 重复关系行、violations、沙箱等）。若还没解决，把三样东西发我们：
① `tail -n 200 /opt/bodhi2/logs/bodhi2-mcp.err.log`；② WeKnora app 日志里 `tools_ready` 那一行；③ `/opt/bodhi2/logs/mcp_calls_*.log` 最后几行。
