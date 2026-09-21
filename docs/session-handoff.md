# bodhi2 × WeKnora 会话交接单（2026-09-19 当晚）

> 目的：让**新开的会话**能在 5 分钟内接手，不必重新侦察。所有结论都已实测。
> 配套阅读：`docs/weknora-fork.md`（策略与 §11 前端）、`docs/bodhi-reasoning.md`（推理规格）。

## 0. 本轮"上下文杀手"排名（供下次会话避坑）

| 排名 | 消耗源 | 规避办法 |
|---|---|---|
| 1 | `docker logs WeKnora-app \| grep …`：日志里带完整 `response_body={…}`（单条上千字符 ✗） | **用 psql 精确取字段**；必须看日志时先 `grep -o` 出短模式，或 `cut -c1-200` 截断 |
| 2 | 整段读取大文件（`server.py` 2600+ 行、`WikiBrowser.vue` 235KB、`patch_frontend.py`） | 先 `grep -n` 定位行号，再 `sed -n 'a,bp'` 只取 20~40 行 |
| 3 | dump 长文本（页面正文、`page_metadata`、提示词 1 万字符、图谱 JSON） | SQL 里就 `left(content, 1200)`；验证只看**计数与样例各 1 条** |
| 4 | 在 PowerShell 里嵌中文/引号跑 wsl 命令（反复解析失败 ✗ 重试） | **写成 `%TEMP%\*.sh` → `bash /mnt/c/.../*.sh`**，输出重定向到文件再读需要的行 |
| 5 | 前端镜像重建（npm/pnpm 冲突、截断文件、OOM；单次 5~10 分钟 + 大量日志） | 用已固化脚本一键跑，**后台 systemd-run**，只看结尾 20 行 |

## 1. 当前可用状态（全部实测）

- **前端**：`weknora-ui:bodhi2`（v4 已上线，v5/v6 待本次重建）；wiki / 图谱 / 本体图谱 三 tab **常显**（`isWiki` 恒真）；
  类型图标 + 悬停中文类名、名称与徽标顶端左对齐、列表视图也有徽标。
  **v5/v6 新增**（2026-09-19 晚，一次重建）：① 类型改**彩色圆点**（§5.1）；
  ② 知识库头部**「上传自动生成 wiki」开关**（§5.2）；③ 树/列表**多选批量删除**（§5.3）；
  ④ 编辑页**本体类型下拉**（需求 1）；⑤ 阅读区**本体关系维护面板**（需求 2，出边可改/入边只读）。
- **MCP/本体服务**：`bodhi-mcp.service`（WSL，`--host 0.0.0.0 --port 8765`），**10 个工具**：
  `extract_and_save`（**默认异步**，秒回 `job_id`）、`extract_status`、`list_pending_merges`、
  `resolve_pending_merge`、`ontology_types`、`skills`（技能目录/全文，2026-09-21）、
  `audit_scan` / `audit_plan`（巡检，含 `scope=coupling` E1-E4）、`save_knowledge`（设计落库，支持 `retract`）、
  `service_overview`（服务详细设计总览，`apply=true` 异步落库）。
  每次调用落一行到 `logs/mcp_calls_YYYYMMDD.log`（时间/工具/耗时/入参/结果摘要）。
  另有一组给前端用的 **JSON 接口 `/bodhi/*`**（与 `/mcp` 同端口，经 nginx `/bodhi/` 反代）：
  见 §7 速查表。
- **ke-core 拆分**（需求 3）：新增 `tools/ke-core/`（`ke_db` / `ke_neo4j` / `ke_ontology` / `ke_pages`，
  纯标准库）；`server.py` 只留 MCP 传输 + 抽取流水线 + 只读图接口 + 路由。
  下游脚本（`backfill_paths.py` / `relink_pages.py` / `sync_folders.py`）继续用
  `server.psql_*` / `server.rel_line` 等名字，**无需改动**。
- **Neo4j 本体投影已灌库**（2026-09-19 晚）：`WeKnora-neo4j` 原先**是空的**（181 节点为 0），
  已按 `artifacts/neo4j/00_constraints.cypher` → `10_ontology.cypher` 幂等灌入：
  模块 5 / 类 47 / 对象属性 70 / 数据属性 20 / 限制 26 / 枚举值 7；
  边 `BODHI_SUBCLASS_OF 24`、`BODHI_DOMAIN 90`、`BODHI_RANGE 92`、`BODHI_INVERSE_OF 5`。
- **已验证的端到端链路**：智能体只处理**用户指定的那篇**文档 → 调一次 `extract_and_save`
  → 后台抽 1~2 分钟 → 写库（`last_edit_source=bodhi-onto-mcp`）→ **自动重建两级目录** → 回报明细。
  真实结果示例：29 要素 / 19 关系 / 5 新增 / 19 合并 / 0 违规 / 7 未匹配（含名称与理由）。
- **数据**：企业知识库（`dbc2528f-611b-48da-9a71-d7c93975adb4`）我们的页 **100**；
  本体模型库（`08810cbd-af86-48d1-bd25-3b2c338e3d68`）TTL 编译页 125。


## 2. 必须记住的三条"上游约束"（踩过）

1. **KB 能力位不能关**：`indexing_strategy.wiki_enabled=false` 会让后端 `/wiki/pages`、
   `/wiki/folders` 直接 400（`error code 1000, Wiki feature is not enabled for this knowledge base`）
   → 界面看似"树空了"（其实是接口被拒）。要"上传不生成 wiki"只能用事后清理：
   `bash deploy/weknora-fork/cleanup_auto_wiki.sh --apply`（软删除 `pipeline`/`agent` 页，**保留 index**，并兜底重建目录）。
2. **app 侧 MCP 客户端 60s 硬超时且无配置项** → 抽取必须异步（已实现），别改回同步。
3. **写入侧主键是确定性 UUIDv5**（slug 派生）→ 必须用 `ON CONFLICT DO UPDATE`，
   否则撞上**软删除的旧页**会整批回滚（已修）。

## 3. 剩余任务（按优先级，均已定位到文件）

| # | 任务 | 入口 | 规模 |
|---|---|---|---|
| 1 | **知识推理引擎**（需求主线下一步）：把本体自带 SHACL 编译成规则 JSON（`--emit-rules`）→ `tools/ke-core/reason.py` 判定内核 → `derive` 幂等落库 → MCP 工具 `reason_validate`/`reason_derive`。**前置条件本轮已备齐**：Neo4j 投影已灌库、`ke_ontology` 可按类筛属性/闭包、关系维护能造出规则需要的实例 | 规格：`docs/bodhi-reasoning.md`（S1–S9 / O1–O5 已抽好）；规则源：`artifacts/shacl/*`、`ontology/shapes/*` | **大**（单独开一轮） |
| 2 | `server.py` **继续拆分**（需求 3 的下一步）：抽取合并流水线（`build_new_page` / `merge_content` / `extract_and_save` / 相似度）整体搬到 `ke-core/extract_pipeline.py`，`server.py` 只留 MCP 壳；`graph_page.py` 的内嵌 HTML/JS 抽成模板文件 | `tools/ontology-mcp/server.py`（本轮已把 DB/本体/页面维护拆到 `tools/ke-core/`） | 中 |
| 3 | index 索引页与 wiki 同步（上游 `pipeline` 维护的 105 字短文，版本在涨但**不覆盖我们的 SQL 写入**） | 方案 A/B/C 见上轮讨论；若走 B 需先查 app 触发 wiki ingest 的接口/队列键 | 中 |
| 4 | 前端 v5/v6 视觉复核（用户侧看一眼：圆点颜色、开关位置、多选删除、类型下拉、关系面板） | — | 微 |
| 5 | **本体文件加载功能**（用户 2026-09-19 提出的待办）：在「企业本体模型」知识库里**选择本体文件 → 加载**；除了更新 Neo4j 图谱，还要**把每个 class 生成/更新 wiki 页写进 PG**，这样点图谱节点就能直接看到该类的 wiki（当前模型图的节点 slug 是 `bmm:Goal` 这类类型名，取不到 wiki 页） | 后端：`POST /api/ontology/load`（`src/api/ontology.py`）+ `ontology_wiki.py`；前端：模型图右侧详情面板 | 中 |

### 3.4bis 本体上传与模块替换（2026-09-20 用户拍板，取代 §3.5 的原设计）

**语义（最简版）**：本体模型与知识模型**松耦合** —— 本体变了只需重新审核知识模型的符合程度，不做联动。
所以本体更新就是「**上传同名模块 = 整体删掉重建**」。

```
POST /bodhi/ontology/upload  {filename, content, module_id?}
  ① 模块名：优先取显式 module_id，否则从 TTL 的 owl:Ontology IRI / 主 @prefix 推断
  ② 依赖闭包：谁引用该模块（owl:imports + 子类/domain/range 指向它的命名空间）
  ③ 级联删除：**下游先删**（删 bmm ⇒ ea / ea-service / bmm-fd 一并删除），再删自身；
     Neo4j DETACH DELETE（含其它模块指向它的边），PG 硬删该模块的 ontology:* 页
  ④ 解析：rdflib 走 `ontology_compiler.loader`（**复用唯一解析器**，不跑编译器 CLI）
  ⑤ 入库：内存模型直接 MERGE 进 Neo4j（类/属性/继承/domain/range/inverse/模块节点）
  ⑥ 出 wiki：从 **Neo4j** 生成页面并投影（不再依赖 ontology_index.json）
  ⑦ 返回：{module, deleted_modules[], classes, properties, wiki_pages, warnings[]}
```

- **只重建上传的那个模块**（用户 2026-09-20 选定 B 案）：上传 bmm 会连带删掉 ea 等下游，
  ea 必须**再上传一次**才回来；**不做**"用 uploads 目录整套重建"，也**不做**"从仓库 `ontology/` 兜底重建"。
- 上传文件落 `ontology/uploads/`（**gitignore**；工作区不是版本库，同名覆盖 + uuid 防冲突）。
- **不引入**"提升为正式模块"流程、**不管理**本体版本、**不做**增量 patch。
- **加载链路不需要编译**：产物只服务"外部消费者"——上游 WeKnora 的 `extract_config`
  （可改为 MCP 直接写 DB 字段）、前端 `ontologyTypes.ts`（**构建期**）、agent 提示词、SHACL/JSON Schema
  （目前无消费者）→ 需要时用 `--emit` 白名单**按需导出**，不再每次加载都刷。

**依赖判定与校验（实现要点）**
- 依赖 = 显式 `owl:imports` **或**实际引用（`rdfs:subClassOf` / `domain` / `range` / `owl:inverseOf`
  指向目标模块命名空间）——后者**以 Neo4j 的边为准**（`BODHI_SUBCLASS_OF` / `BODHI_DOMAIN` / `BODHI_RANGE`），
  比只读 TTL 更可靠（历史遗留的跨模块引用也查得到）。
- 校验闸门（不通过则整体回滚）：命名空间自洽（只能声明自家前缀）、前缀与已存模块不冲突、
  文件大小/三元组数上限、range 指向的类存在。
- 前端对"动态模块"已能降级显示（`module_label`=模块 key、颜色灰）；专属配色需构建期生成
  `ontologyTypes.ts`，属以后的事。

**操作与恢复配方（2026-09-20 实测）**
```bash
# 看会连带删掉谁（只读，先跑这条）
python3 tools/ke-core/ke_admin.py deps bmm      # → ea-ownership, ea-service, bmm-fd, ea（最下游在前）
# 真删（不可逆：B 案不重建下游）—— 也可用 HTTP：POST /bodhi/ontology/purge {"model_id":"bmm"}
python3 tools/ke-core/ke_admin.py cascade bmm
# 恢复（artifacts 未动即可原样回放；model_id 留空 = 不清理任何模块）
curl -s -X POST -H 'Content-Type: application/json' -d '{"compile":false}' \
     http://127.0.0.1:8765/bodhi/ontology/load
#   → apply 603 条 / 47 类；wiki 47 类 / 90 属性 / 70 关系 / 6 模块；model-graph 47 节点 / 96 边 / 悬空 0
```
实测结论：`cascade bmm` 后 Neo4j 与 wiki 只剩 `index`（硬保护的索引页），
`load {compile:false}` 一步恢复全部 5 个模块（幂等 MERGE + 整体重投影）。
注意 `cache` 字段位于 `compile.cache`（仅 `compile=true` 时出现）。

**待做（按序，每步跑通再下一步）**
1. `ke_admin.import_ttl(path, module_id)`：解析 → 级联删 → 入库 → 出 wiki（**零产物**）；
2. `ontology_wiki.py` 的"类/关系"数据源改为 Neo4j（属性已改）→ wiki 生成不依赖任何 json；
3. `POST /bodhi/ontology/upload` 路由（`server.py` 的 `_ROUTES` 表）+ `ontology/uploads/` 与 .gitignore；
4. 前端「上传本体文件」按钮：选文件 → 自动推断模块名 → 确认框列出"将级联删除的下游模块" → 结果面板；
5. 编译器 `--emit` 白名单（只在导出契约时产文件）。

### 3.5 待办 5 的实现要点（设计已定，照做即可）

**用户 2026-09-20 补充口径**：选择一个本体文件加载；**若本体模型知识库已有该模型，先清理该模型的 wiki 与图谱、再加载**（避免旧类残留）。

1. **加载链路**（沿用 rdflib 解析，不重写本体处理）：
   `POST /bodhi/ontology/load {file, model_id?, purge:true}` →
   `purge_model(model_id)`（见第 4 点）→ `load_ttl()`（rdflib → `upsert_ttl` 写 Neo4j 投影）→
   重投影该模型的 wiki 页（`ontology_wiki.py build/project`，可按模型过滤）→ `sync_folders`。
2. **图谱节点 ↔ wiki 页映射**（已做）：`bodhi_model_graph()` 把节点 slug 换成真实 wiki slug
   （`ontology/<模块 key>/<本地名小写>`），类名保留在 `type_name`；**边端点也要跟着重映射**
   （否则只剩节点没边 —— 2026-09-20 已踩并修）。
3. **增量 vs 全量**：`load_ttl` 是「按模块 upsert + 重建该模块出边」；页面重投影先全量最省事（215 页几秒）。
4. **purge 语义（要清理什么）**：
   - Neo4j：`MATCH (n {module:$m}) DETACH DELETE n`（类/属性）+ 模块节点 + 该模块的
     `BODHI_SUBCLASS_OF/BODHI_DOMAIN/BODHI_RANGE` 入边（`ke_ontology`/`neo4j_store` 已有 `delete_model`）；
     注意**其它模块指向它的边**要一并清（否则留断边）。
   - PG：`wiki_pages WHERE page_metadata->'ontology'->>'model' = $m AND last_edit_source='ontology-wiki'`
     硬删（含 revisions），再重建目录树。
5. **src/ 的去留（用户建议：能删就删）**：唯一独有能力是「用 rdflib 把 TTL 解析成结构化数据」。
   方案：把它搬进 `tools/ke-core/`（例如 `ke_ttl.py`，只保留 `parse_ttl` + `upsert_ttl` 的写入映射），
   并在 WSL 里 `pip install rdflib`（bodhi-mcp 由 `bodhi-mcp.service` 启动，加依赖要写进部署说明）；
   之后 `src/` 可整体删除，Neo4j 本体读写只保留 ke-core 一份实现。




## 4. 常用命令（复制即用）

```bash
# 体检（容器 / 服务 / 前端补丁 / 图数据 / 库内计数）
bash /mnt/c/Users/PHJY/source/bodhi2/deploy/wsl-up.sh status

# 前端镜像重建（改过 Vue 才需要）
bash /mnt/c/Users/PHJY/source/bodhi2/deploy/weknora-fork/build_frontend.sh /root/fe-build
bash /mnt/c/Users/PHJY/source/bodhi2/deploy/weknora-fork/deploy_frontend.sh deploy

# 只改了本体类型表（ontologyTypes.ts）时的最小路径（CLEAN_SRC 干净副本已不在，别加 CLEAN_SRC）
cp /mnt/c/Users/PHJY/source/bodhi2/deploy/weknora-fork/frontend/ontologyTypes.ts /root/fe-build/src/utils/ontologyTypes.ts
cd /root/fe-build && NODE_OPTIONS=--max-old-space-size=4096 npm run build && docker build -f Dockerfile -t weknora-ui:bodhi2 .
bash /mnt/c/Users/PHJY/source/bodhi2/deploy/weknora-fork/deploy_frontend.sh deploy

# 离线复放（不烧 token、不受 60s 超时影响；验证写入路径）
cd /mnt/c/Users/PHJY/source/bodhi2/tools/ontology-mcp && python3 replay_extraction.py --dry-run

# 清理上游自动页 + 兜底重建目录
bash /mnt/c/Users/PHJY/source/bodhi2/deploy/weknora-fork/cleanup_auto_wiki.sh --apply
```


## 5. 前端 v5/v6（2026-09-19 晚**已实现**，一次重建打包五件事）

实现位置：`deploy/weknora-fork/frontend/patch_frontend.py` 的 **v4→v7 链**
（`patch_wikibrowser_v4` 圆点 → `_v5` 多选删 → `_v6` 类型下拉 → `_v7` 关系面板 →
`patch_knowledgebase_v5` 开关），外加新组件 `frontend/BodhiRelationsPanel.vue`
（自包含，补丁只插一行标签 + 一个回调）。重建命令见 §5.4。
本轮顺手修好了 `build_frontend.sh` 的两处旧伤：**patch 脚本路径/参数写错**（原来指向
`$HERE/patch_frontend.py` 且用位置参数，必然报错）与**缺 `CLEAN_SRC` 还原**（见下）。

### 5.1 类型标签显示为空 → **彩色圆点**（用户口径：「有颜色的圆点，简单一些」）

- 根因：v3 的 `<t-icon :name="getPageIcon(page)">` 对本类类型统一返回 `'hierarchy'`，
  该名字在打包后的 tdesign 图标集里**不渲染**（tooltip 正常，只是图标空白）。
- 已实现：树视图 / 列表视图 / 阅读区类型徽标三处的 `t-icon` 换成
  `<span class="wiki-page-item-type-dot" :style="{ background: ontologyColor(...) }">`，
  tooltip 文案不变（`中文类名（bmm:XXX）`）。
  颜色来自 `ontologyTypes.ts` 的 `ontologyColor()`（`gen_frontend_types.py` 生成，本来就有 color 字段）。

### 5.2 知识库页加「启用/禁用 wiki 自动生成」开关

- 背景：能力位 `indexing_strategy.wiki_enabled` 关掉后上传**不再自动生成 wiki**（省时省 token），
  但后端 `/wiki/pages`、`/wiki/folders` 会 400（error code 1000）→ 界面暂时看不到 wiki，
  所以开关必须**带明确提示**。
- 已实现：`KnowledgeBase.vue` 面包屑右侧加 `<t-switch>`（在「本体图谱」之后），
  tooltip 写明「关闭 = 上传不再自动生成 wiki；关闭期间 wiki 列表接口会被后端拒绝，
  界面暂时看不到 wiki 与图谱，需要时请重新打开」；失败自动回滚开关状态。
- **落点选择**：写库不走上游 `PUT /knowledge-bases/:id`（它要求整份 `config`，
  `chunking_config` 等是值类型，缺字段会被清空），而是走自研
  **`POST /bodhi/kb/wiki-flag`** → `UPDATE knowledge_bases SET indexing_strategy =
  jsonb_set(..., '{wiki_enabled}', …)`（与我们以前直接 SQL 改的口径一致）。

## 6. 本轮收尾状态（2026-09-19 末）

- 智能体已**收敛为一个入口**：`bodhi-ontology-bmm` 更名为「本体知识提取（BMM / EA）」，
  提示词为精简版（1152 字符，默认 bmm、必须回显调用参数）；`bodhi-ontology-ea` 已软删保留追溯。
- `deploy/weknora-fork/gen_agent_config.py` 已按用户要求**删除**

### 5.3 列表/树**多选批量删除**（用户 2026-09-19 新增要求）

- 后端：`POST /bodhi/delete`（`ke_pages.soft_delete_pages`）—— 软删除、可回溯、
  **永不动 `slug='index'`**、删完自动重建目录树；`dry_run` 可先看命中。
  （运维脚本 `deploy/weknora-fork/delete_wiki_pages.sh` 的四种模式仍在，二者共用同一套写库口径。）
- 前端：树/列表每行加 `<t-checkbox>`（绑定 `Set<string>`）；有选中时出现工具条
  「已选 N 项 / 清空选择 / 删除（N）」；删除前 `<t-dialog>` 二次确认（文案写明软删除、
  可回溯、index 不受影响）；分片每批 ≤200；失败**保留选择**并回显后端原文。
- 验收口径：多选 3 页删除 → 树上立刻少 3 页、目录计数更新；`index` 与上游页不受影响；
  库里 `deleted_at` 有值（可用 `UPDATE … SET deleted_at=NULL` 复原）。

### 5.4 一次重建即可打包五件事

```bash
# src 必须先还原成干净源码（CLEAN_SRC 由脚本负责 rm+cp；也可手动 rm -rf src）
export CLEAN_SRC=/root/wk080/frontend/src
bash /mnt/c/Users/PHJY/source/bodhi2/deploy/weknora-fork/build_frontend.sh /root/fe-build
bash /mnt/c/Users/PHJY/source/bodhi2/deploy/weknora-fork/deploy_frontend.sh deploy
```
（`patch_frontend.py` 一定会拒绝对已打补丁的树二次打补丁：重复插入 import 会让 vite 报
babel `parseImportSpecifier` 语法错。所以 `CLEAN_SRC` 别漏。）

### 5.5 需求 1：wiki 编辑页可改**本体类型**（下拉 = 全部本体 class）

- 编辑态在标题输入框上方出现「本体类型」行：彩色圆点 + `<t-select>`（filterable，
  按模块分组，label 形如 `目标（bmm:Goal）`），数据来自 `GET /bodhi/ontology/classes`（Neo4j）。
- 保存链路：先用上游 `PUT /wiki/pages/<slug>` 存正文（乐观锁照旧），**若类型变了**再调
  `POST /bodhi/page/type`（上游 `PUT` 只接受 6 个内建 `page_type`，传 `bmm:Goal` 会 400）。
  后端顺带重算 `category_path`、更新 `page_metadata.ontology`、写版本快照、**重建目录树**。
- 前端随后 `getWikiPage` 拉回新版本（保持乐观锁基线最新）并刷新侧栏。

### 5.6 需求 2：Bodhi 图谱的**关系维护**（新增/修改/删除）

面板：`frontend/BodhiRelationsPanel.vue`，嵌在 wiki 阅读区（正文下方、footer 上方）。
- **出边**（本页 → 别的要素）：可新增 / 修改 / 删除；
- **入边**（别的要素 → 本页）：**只读**，提示「反向关系需在对方页面里修改」——
  因为它是对方页面的出边（`in_links` 语义）；
- 编辑正文时面板隐藏（避免本地未保存正文与服务端写关系互相覆盖）。
- 关系类型的候选 = 该页 `page_type` 的**对象属性 domain 闭包（含父类继承）**：
  `GET /bodhi/ontology/relation-types?page_type=…`，选项文案带「继承自 `bmm:DesiredResult`」。
- 选定关系类型后，目标页候选 = 该对象属性 `range` 的**子类闭包**内的 wiki 页：
  `GET /bodhi/ontology/targets?kb_id=&slug=&rel_type=…`（例：`bmm:guides` → `bmm:BusinessProcess`；
  `easvc:contractEnforcesRule` → `bmm:BusinessRule` + 4 个子类）。
- 写库：`POST /bodhi/relations/{add,update,delete}` → 改正文 `## 本体关系` 小节 + 重算
  `out_links` / `in_links` + 版本快照（`last_edit_source=bodhi-rel-edit`）。
- 服务端**双向校验**（前端只做提示，判定以服务端为准）：关系类型必须在该类可用集合内；
  目标页类型必须在 range 闭包内；不允许指向自己。

### 5.7 需求 3：把「占用大量上下文的程序」拆小

已建 `tools/ke-core/`（纯标准库，README 见该目录）：

| 文件 | 职责 |
|---|---|
| `ke_db.py` | psql / SQL 字面量 / 时间戳（原先散在 `server.py` 顶部） |
| `ke_neo4j.py` | Neo4j 本体投影查询（**HTTP + Basic Auth**，官方镜像自带事务端点，免驱动，零依赖） |
| `ke_ontology.py` | 类清单 / **按类（含父类继承）筛对象属性** / range 闭包 / 按 range 找目标页（Neo4j 优先，`ontology_index.json` 兜底，返回值带 `source`） |
| `ke_pages.py` | 关系增删改 / 类型修改 / 批量软删除 / KB wiki 开关（写库纪律统一在此） |

`server.py` 现在只留：MCP 传输 + 抽取合并流水线 + 只读图谱接口 + `/bodhi/*` 路由；
`psql` / `psql_csv` / `sql_str` / `sql_json` / `now_text` / `rel_line` / `parse_rel_line` /
`class_category_path` **名字全部保留**（内部委托 ke-core），所以
`backfill_paths.py` / `relink_pages.py` / `sync_folders.py` 一行都不用改 ✓。
下一步拆分建议见 §3 第 2 项。

## 6. 本轮收尾状态（2026-09-19 末）

- 智能体已**收敛为一个入口**：`bodhi-ontology-bmm` 更名为「本体知识提取（BMM / EA）」，
  提示词为精简版（1152 字符，默认 bmm、必须回显调用参数）；`bodhi-ontology-ea` 已软删保留追溯。
- `deploy/weknora-fork/gen_agent_config.py` 已按用户要求**删除**
  （`config/agents.sql`、`config/agent_system_prompt.yaml` 仍在，作历史追溯）。
- `set_agent_prompt_lean.py` 是现在**唯一**的智能体提示词入口（改提示词就改它并重跑）。

## 7. `/bodhi/*` JSON 接口速查（与 `/mcp` 同端口 8765，经 nginx `/bodhi/` 反代）

| 方法 | 路径 | 参数 / body | 用途 |
|---|---|---|---|
| GET | `/bodhi/ontology/classes` | — | 全部本体类（47，带模块前缀 + 中文 label + 颜色）→ 类型下拉 |
| GET | `/bodhi/ontology/relation-types` | `page_type` | 该（含父类）可用的对象属性 + range |
| GET | `/bodhi/ontology/targets` | `kb_id` `rel_type` `slug` `q` `limit` | range 子类闭包内的候选目标页 |
| GET | `/bodhi/relations` | `kb_id` `slug` | 本页出边（可改）+ 入边（只读） |
| POST | `/bodhi/relations/add` | `{kb_id, slug, rel_type, target_slug, label?}` | 建边 |
| POST | `/bodhi/relations/update` | `{kb_id, slug, target_slug, new_rel_type?, new_target_slug?}` | 改边（类型/目标） |
| POST | `/bodhi/relations/delete` | `{kb_id, slug, target_slug, rel_type?}` | 删本页出边 |
| POST | `/bodhi/page/type` | `{kb_id, slug, page_type}` | 改本体类型（重算 category_path + 重建目录） |
| POST | `/bodhi/delete` | `{kb_id, slugs:[…], dry_run?}` | 多选软删除（`index` 受保护） |
| POST | `/bodhi/kb/wiki-flag` | `{kb_id, enabled}` | 上传是否自动生成 wiki |
| （原有） | `/bodhi/graph` `/bodhi/page` `/bodhi/view` `/bodhi/pending` `/bodhi/resolve` | — | 只读图 / 单页 / 图页面 / 待确认 / 裁决 |

校验类错误一律 `400 {"error": "…原文…"}`（前端直接展示），其余 `500`。

## 8. 本轮验证记录（2026-09-19 晚，全部实测）

- **Neo4j 灌库**：灌前 `count(n)=0` → 灌后 181 节点；`BODHI_SUBCLASS_OF 24`、
  `BODHI_DOMAIN 90`、`BODHI_RANGE 92`、`BODHI_INVERSE_OF 5`、`BODHI_HAS_RESTRICTION 26`。
- **本体查询**：`classes()` → `source=neo4j`、47 类；`ancestors('bmm:Goal') = [bmm:Goal, bmm:DesiredResult]`；
  `relation_types_for('bmm:Goal')` → 2 条（`bmm:containsResult` / `bmm:definedBy`，均标 `inherited_from=bmm:DesiredResult`）；
  `relation_types_for('easvc:ServiceContract')` → 7 条；`target_closure('easvc:contractEnforcesRule')`
  → `bmm:BusinessRule` + `OperativeBusinessRule` / `StructuralBusinessRule` / `bmmfd:AccessControlRule` / `bmmfd:DataQualityRule`。
- **关系往返**（受控测试，测完已清理到原状：`version=1`、`md5` 一致、测试快照已删）：
  读（出边 1 条 + 入边 0）→ 加（`bmm:guides` → 影响评估流程，v1→v2）→ 改（→ `bmm:isDerivedFrom` / 业务政策页）
  → 删（回到原正文，v4）；非法输入被拦：越界类型（`easvc:contractEnforcesRule` 用于 `bmm:BusinessRule`）、
  指向自己、`index` 删除、未知类型（400 带原文）。
- **接口自测**：`GET classes / relation-types / targets / relations`、`POST kb/wiki-flag`
  均 200；`POST delete{slugs:["index"]}` → 400「index 受保护」。
- **前端补丁**：干净源码 → v1…v7 全链打补丁成功（WikiBrowser.vue 238,040 → 248,001 字节；
  KnowledgeBase.vue 146,818 字节），`check_sfc.mjs` **扫描 191 个 .vue、损坏 0**。
- **服务**：`py_compile` 全绿；`systemctl restart bodhi-mcp.service` → `active`。

