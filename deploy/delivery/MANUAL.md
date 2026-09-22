# bodhi2 交付手册（安装部署总指引）

> 版本：`1.0`（生成时间见 `MANIFEST.json`）
> 适用：**你们已有可用的 WeKnora 镜像部署**（app + 前端 + Postgres 已跑起来），要在此基础上接入 bodhi2 的本体建模与设计能力。
> 本手册是**总入口**；每个包还有自己的详细手册（见下表）。

## 0. 包里有什么

> 手册位置：**根目录只放本文件（`MANUAL.md`）**；其余手册（`FRONTEND.md` / `MCP-SERVER.md` / `ONTOLOGY-KB.md` /
> `KB-CONFIG.md` / `TROUBLESHOOTING.md` / `AGENTS-SQL.md`）与 `docs/`、`ontology/`、`skills/`、`sql/`、在线小工具
> 都在 **`bodhi2-04-manual.tar.gz`** 里 —— 交接/归档以包内为准（单一来源是仓库 `deploy/delivery/*.md`，
> `MANIFEST.json` 的 `manual_sha256` 记录了每份哈希）。

| 包 | 内容 | 详细手册 |
|---|---|---|
| `bodhi2-01-frontend.tar.gz` | 补丁后的 UI 镜像（`weknora-ui:bodhi2`）+ nginx 模板 + compose overlay + 验收脚本 + 打补丁脚本（若你们要自己构建）| `FRONTEND.md` |
| `bodhi2-02-mcp-server.tar.gz` | MCP 服务源码（`tools/ontology-mcp` + `tools/ke-core` + `skills` + `artifacts/weknora`）+ Dockerfile + compose 片段 + systemd + 自检脚本 | `MCP-SERVER.md` |
| `bodhi2-03-ontology-kb.tar.gz` | 本体 TTL 真源 + 编译器 + 投影工具 + 编译产物 + **可直接导入的种子**（248 页本体模型库）| `ONTOLOGY-KB.md` |
| `bodhi2-04-manual.tar.gz` | 配置手册成册：`docs/`（全部设计文档）+ `ontology/`（本体规范）+ 本手册与四份子手册 + 智能体注册 SQL | 本文件 + `KB-CONFIG.md` + `TROUBLESHOOTING.md` |
| `MANIFEST.json` / `SHA256SUMS` | 版本、文件清单、校验和 | — |

**四张图看懂彼此关系**

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
                        本体模型知识库（248 页，类型真源，由 03 包导入）
                        业务知识库（你们的文档 → 实例页，由智能体写入）
```

## 1. 部署顺序（照这个顺序做，每步都有验收）

| 步 | 动作 | 验收（不通过就别往下走）|
|---|---|---|
| **1** | 部署 MCP 服务（`bodhi2-02`）—— 容器方式最省事 | `selfcheck.py` 输出 `tools/list OK（10 个）` |
| **2** | 在 WeKnora 里注册 MCP 服务（UI 或 SQL），URL 用容器 DNS `http://bodhi-mcp:8765/mcp` | 平台 → MCP 服务里能看到 `bodhi_ontology`，工具 10 个 |
| **3** | 导入本体模型知识库（`bodhi2-03`：编译投影 or 种子导入）| 页数 **248**（类 52 / 关系 80 / 属性 107 / 模块 6 / 轻量版 2 / 索引 1）|
| **4** | 建业务库 + 配 `wiki_config`（`KB-CONFIG.md` §3）| 上传一篇文档能出 wiki 页 |
| **5** | 替换前端（`bodhi2-01`）+ 挂载 nginx 模板 | `deploy_frontend.sh check` 全绿；类型下拉能看到 `easvc:*` |
| **6** | 注册智能体（`AGENTS-SQL.md`：提示词 + 两个库 + MCP + 15 个工具）| 让智能体跑一轮"先 `skills()` 看目录"的任务，能正常列出 3 个技能 |
| **7** | 端到端验证 | 见 §4「验收清单」|

> **只想要本体建模/设计能力、暂时不动前端**也可以：1→2→3→4→6 就能跑（前端替换只影响"类型下拉/本体图谱 tab/关系面板"这些可视化）。

## 2. 环境要求

| 项 | 要求 |
|---|---|
| WeKnora | 你们内部网已部署可用（app + 前端 + Postgres）；app 版本与我们的 UI 补丁**尽量一致**，不一致走 `FRONTEND.md` §3 自行构建 |
| 容器运行时 | Docker ≥ 20.10 + compose v2 |
| MCP 服务 | Python ≥ 3.10（容器方式已内置）、`postgresql-client`；**零第三方 Python 依赖** |
| 数据库 | 能连 WeKnora 的 Postgres（同库同表，MCP 直连，不另建库）|
| 网络 | app 能访问 MCP 的 `:8765`；MCP 能访问 Postgres `:5432`（两者用容器 DNS 最省心）|
| 可选 | Neo4j（本体投影）——**本交付不需要**，不部署也不影响主流程 |

## 3. 一次性配置清单（要改的东西全在这）

> **发布包不含任何密钥**：没有 LLM API key、没有访问令牌、没有数据库口令（打包前跑过
> `tools/delivery/scan_secrets.py` 审计）。下面这些**都要在内网重新配置**：

| 需要重配的东西 | 配在哪 | 说明 |
|---|---|---|
| **LLM 模型的 API Key** | **内网 WeKnora 自己的 UI/数据库**（平台 → 模型）| 只落在你们的库里，**从不进交付包**；MCP 侧只用到 `model_id`（UUID）|
| **数据库口令** | `02-mcp-server/.env` 的 `BODHI_DB_PASSWORD`，或把 `BODHI_WEKNORA_DIR` 指向含 `.env` 的 WeKnora 目录 | 代码里**不再内置任何默认口令**：env → WeKnora `.env` 的 `DB_PASSWORD`/`POSTGRES_PASSWORD` → 都没有则报错退出 |
| MCP 服务 URL | WeKnora 平台 → MCP 服务（`http://bodhi-mcp:8765/mcp`）| 用容器 DNS 可免 SSRF 白名单 |
| `SSRF_WHITELIST_EXTRA`（仅当 MCP URL 用宿主 IP/域名时）| WeKnora `.env` | 见 `TROUBLESHOOTING.md` §1 |
| Neo4j 口令（可选，本交付不需要）| `.env` 的 `NEO4J_PASSWORD` | 不部署 Neo4j 就不用管 |

1. `02-mcp-server/.env`：`BODHI_DB_HOST/PORT/USER/PASSWORD/NAME`（改成你们的 Postgres）；
2. WeKnora `.env`（仅当 MCP URL 不是容器 DNS 时）：`SSRF_WHITELIST_EXTRA` 加上 MCP 的主机名/IP；
3. `mcp_services` 一行：URL 指向 `http://bodhi-mcp:8765/mcp`（SQL 在 `MCP-SERVER.md` §6）；
4. `custom_agents` 一行：提示词 + `knowledge_bases`（两个库）+ `mcp_services` + `allowed_tools`（18 个）；
   **SQL 在 `04-manual/AGENTS-SQL.md`**（可回滚）；
5. `knowledge_bases.wiki_config`：抽取指令按你们领域改（`KB-CONFIG.md` §3）；
6. 前端 nginx 模板挂载 + `image: weknora-ui:bodhi2`（`FRONTEND.md`）。

## 4. 验收清单（交付意义的"通过标准"）

| # | 检查 | 期望 |
|---|---|---|
| 1 | `selfcheck.py --url http://…:8765/mcp` | `tools/list OK（10 个）` + `skills() OK（3 个）` |
| 2 | 智能体一轮只读任务 | `tool_count=15`；`logs/mcp_calls_*.log` 有 `skills`/`audit_scan` 记录 |
| 3 | 本体模型库 | 248 页；`curl <mcp>/bodhi/ontology/models` 返回 5 个模型 |
| 4 | 业务库上传+抽取 | 页面类型都在本体里，`source_refs` 非空（无 C1）|
| 5 | `curl <mcp>/bodhi/audit?kb_id=<业务库>` | 无 **C1/C3** 类"无来源"发现；A1/A2 若有，按提示修 |
| 6 | 前端 | 登录正常；本体图谱 tab 出图；关系面板出边可改；类型下拉含 `easvc:*` |
| 7 | 设计流程 | 让智能体按 `service_detailed_design` 技能做一个服务，末尾调 `service_overview(apply=true)` → 生成/刷新「IT 服务详细设计总览」页 |

## 5. 日常运维

| 场景 | 命令 |
|---|---|
| 看智能体到底调了什么 | `tail -f logs/mcp_calls_YYYYMMDD.log`（时间/工具/耗时/入参/结果摘要）|
| 一致性巡检 | `curl "<mcp>/bodhi/audit?kb_id=<kb>"`；清理走 `plan → apply --confirm`（**不自动修**）|
| 本体演进 | 改 TTL → `refresh_ontology_kb.sh`（编译+投影）；类清单变了记得重建前端 |
| 技能演进 | 直接改 `skills/<id>/SKILL.md`（MCP 按 mtime 热读，**不用重启**）|
| 重建过 app 容器 | `docker restart WeKnora-frontend`（交付模板已含运行期解析，仍建议一把）|
| 备份 | Postgres 常规备份即可（`wiki_pages` / `knowledges` / `mcp_services` / `custom_agents`）；MCP 服务本身无状态 |

## 6. 排错

先看 `TROUBLESHOOTING.md`（12 条，都是我们真实踩过的：SSRF 白名单、nginx 上游解析、工具 EOF、`applied=false`、
C1 无来源、A5 重复关系行、violations、沙箱等）。若还没解决，把三样东西发我们：
① `docker logs <mcp 容器> --tail 200`；② WeKnora app 日志里 `tools_ready` 那一行；③ `logs/mcp_calls_*.log` 最后几行。
