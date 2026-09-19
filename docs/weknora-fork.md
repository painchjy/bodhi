# WeKnora 二次开发方案：用本体模型驱动 Wiki 生成与关系构建

> 状态：方案已立项（2026-09-18）。证据来自本机实测与上游源码；未实测处明确标注"待确认"。

## 1. 目标（用户口径）

保留 WeKnora 原有**文档处理能力**（上传、解析、分块、向量化、检索、对话），
**替换其中的 Wiki 生成**，改为本体模型驱动：

1. `.env` 里定义**可用的本体模型**与每个模型对应的**专家角色**（已有，见 §3）；
2. 抽取提示词**针对文档类型**，由用户在界面上**选择本体模型 + 专家**后执行；
3. 抽取返回 **JSON**，用于①创建/更新 Wiki 页面 ②创建图关系；
4. 抽取时**把存量 Wiki 内容填进提示词**，使新知识与存量知识建立关联；
5. 关系必须**既符合 Wiki 语义、又符合本体定义**（domain/range），而非宽泛的通用实体-关系。

## 2. 上游现状（证据）

### 2.1 页面类型是硬编码的，不是配置

`internal/agent/prompts_wiki.go`（36.9KB，已取回）里的提示词模板写死了两类知识页：

| 模板 | 作用 | 硬编码内容 |
| --- | --- | --- |
| `WikiTaxonomyPlanPrompt` | 给每个 entity / concept 页面分配目录分类 | 分类体系只有 entity / concept 两支 |
| `WikiCandidateSlugPrompt` | 从文档列出所有 significant entities AND key concepts | 输出 `{"entities":[...],"concepts":[...]}` |
| slug 规则 | URL slug 格式 | `entity/<lowercase-hyphenated>` / `concept/<...>` |

前端对应四个固定筛选：`filterEntity:"实体"` / `filterConcept:"概念"` /
`filterSynthesis:"综合"` / `filterComparison:"对比"`（取自线上前端 bundle，实测）。

数据库层面**不限制**取值：`knowledge_bases.wiki_config` 是 `jsonb`、
`wiki_pages.page_type` 是 `varchar`；但**服务端与提示词层写死** →
**结论：要让页面类型来自本体 class，必须改代码。**

### 2.2 上游 wiki 生成链路（文件级）

二进制内嵌路径给出 838 个 app 自有 `.go` 完整清单，其中与本改造相关的：

```
internal/application/service/wiki_ingest.go            # 入库主流程
internal/application/service/wiki_ingest_taxonomy.go   # 分类/目录 ← 页面类型相关
internal/application/service/wiki_ingest_dedup.go      # 去重
internal/application/service/wiki_ingest_batch.go      # 批量
internal/application/service/wiki_ingest_cite.go       # 引用
internal/application/service/wiki_page.go              # 页面服务
internal/application/service/wiki_linkify.go           # 页内链接
internal/application/service/wiki_lint.go              # 巡检
internal/application/repository/wiki_page.go           # 持久化（48KB）
internal/application/service/extract.go                # 图谱抽取（33.7KB，LLM+写图）
internal/application/service/chat_pipeline/extract_entity.go
internal/application/repository/retriever/neo4j/       # 图检索（GraphRAG）
internal/types/wiki_page.go / extract_graph.go / indexing_strategy.go / prompt_instructions.go
internal/handler/wiki_page.go                          # 对外 API
internal/agent/tools/wiki_write_page.go 等             # Agent 工具（可写 Wiki）
```

`extract.go` 已确认：抽取配置从知识库 `extract_config` 读取，并把
`custom_instructions` 追加进提示词（`types.AppendCustomPromptInstructions(..., "graph_extraction")`）。

## 3. 可复用资产（本仓库内，已实现）

| 文件 | 能力 | 复用方式 |
| --- | --- | --- |
| `.env` → `ONTOLOGY_MODELS` | `{name,label,full_file,light_file,expert_role}` 本体模型 + 专家角色 | 直接作为选型清单下发到前端 |
| `src/services/ontology_service.py` (46.9KB) | 本体装载、class/property/层次、`get_rel_types_for_source`、`expand_node_type` | 移植为 Go 侧本体服务，或沿用 Python sidecar |
| `src/services/extraction_service.py` (33.7KB) | `_build_instruction(expert_role)`、`_build_existing_context()`、`_filter_result()`、`_parse_extraction_result()`、`commit_extraction()` | **核心**：抽取提示词与 JSON 契约 |
| `src/services/graph_service.py` (24.1KB) | 节点/关系/来源落库（当前后端 **KùzuDB**） | 需改为 **Neo4j**（与 WeKnora 同库） |
| `src/services/sim_review_service.py` (10.8KB) | 相似度判重 + merge 审核 | 对应"与存量知识建立关系" |
| `artifacts/weknora/extract_config.*.json` | 5 个模块的 6 键抽取配置 | 注入上游 `extract_config` 的来源 |
| `artifacts/neo4j/*.cypher` | 本体投影（已实测 181 节点 / 407 关系） | 实例与本体同库，关系校验基础 |

### 3.1 既有 JSON 契约（来自 `extraction_service.py`，实测读取）

System prompt 由 `_build_instruction(model_label, expert_role)` 生成，要求：

```json
{
  "elements": [{
    "action": "create | merge",
    "existing_id": "合并时填已有要素id，新建时为 null",
    "type": "要素类型（本体 class）",
    "name": "要素名称",
    "definition": "简要定义",
    "description": "从原文识别该要素的理由",
    "excerpt_id": "来源摘录 id（必须填）",
    "source_text": "支持该要素的原文片段（逐字引用）",
    "ai_skill": "仅 Service 类型的 AI 技能 JSON",
    "extra": {}
  }],
  "relationships": [{
    "type": "关系类型（从本体定义中选取）",
    "source_element_index": 0,
    "target_element_index": 1,
    "excerpt_id": "...",
    "source_text": "..."
  }]
}
```

关系显式支持三种连接：**新↔新**（`*_element_index`）、**新↔存量**（`*_existing_id`）、
**存量↔存量**；存量清单由 `_build_existing_context()` 渲染成
`- [类型] id=xx | 名称 | 定义`（最多 180 条）注入用户提示词。

Wiki 标注侧（`_build_annotate_instruction`）产出页面正文：

- 知识标注优先：`[词汇](./kn/类型/名称)` → 指向**存量知识节点**
- 本体标注兜底：`[概念](./onto/bmm:BusinessProcess)` → 只能取自提示词提供的「可用本体概念」清单，
  禁止编造；且要求"尽量选择完整短语/句子表达概念定义语义，不要标注简单术语"
  —— 这正是"不宽泛"的约束。

## 4. 改造设计

### 4.1 页面类型：硬编码枚举 → 本体 class

- `internal/types/wiki_page.go`：`page_type` 改为 `模块:Class` 形式（沿用本体投影的命名空间约定）。
- `internal/agent/prompts_wiki.go`：新增"本体驱动"候选清单/分类提示词，注入
  ①本体模型定义（TTL 摘要）②专家角色 ③本体 class 清单 ④允许的关系类型。
- `wiki_ingest_taxonomy.go`：目录由**本体 class** 层次决定，而非 entity/concept 二分。
- 前端：四个固定筛选改为按当前知识库所选模型的 class 动态生成（i18n 键保留兜底）。

### 4.2 抽取引擎：两条路线（待确认）

| 路线 | 做法 | 优点 | 代价 |
| --- | --- | --- | --- |
| A. Port to Go | 把 extraction/ontology/graph 三块移植进 fork | 单进程、最贴合上游链路 | 移植量与回归成本大（Python ~105KB） |
| B. Python sidecar | fork 只改"调用点"，调用我们已实现的 Python 服务，结果回写 DB/Neo4j | 复用现有实现、风险小、见效快 | 多一个容器；需定义内部 API 契约 |

无论哪条路线，**图写入统一到 Neo4j**（WeKnora 同库），以便 Wiki 的 `[词汇](./kn/...)`
能指向真实节点、关系能按本体 domain/range 校验（已有 `artifacts/shacl/generated.shapes.ttl`）。

### 4.3 `.env` 扩展（草案，沿用已有键）

```dotenv
ONTOLOGY_DIR=./ontology
ONTOLOGY_MODELS=[{"name":"BMM","label":"BMM 业务动机模型","full_file":"BMM完整版.ttl","light_file":"BMM轻量版.md","expert_role":"企业架构分析专家，精通 BMM 业务动机模型"}]

# 新增
WEKNORA_WIKI_ENGINE=ontology      # 取代上游内置 wiki 生成
WEKNORA_WIKI_PAGE_TYPES=ontology  # 页面类型来自本体 class
WEKNORA_EXISTING_LIMIT=180        # 存量注入条数上限（与现有实现一致）
```

### 4.4 构建与部署

- 本机 `github.com` 大传输会被掐断 → 源码经 **jsDelivr 逐文件重建**（838 文件清单已得）。
- Go 依赖 `goproxy.cn` **实测 200** ✓；npm `registry.npmmirror.com` **实测 200** ✓。
- 构建在容器内进行（`docker build` 的 base image 走 daocloud 加速器）；产物打本地 tag
  （如 `wechatopenai/weknora-app:ontov1`）覆盖 compose 里的 `app.image`。
- 运行时仍 `--profile neo4j`；本体投影灌库沿用 `deploy/docker-compose.weknora.yml`。

## 5. 分阶段实施与验收

| 阶段 | 内容 | 验收 |
| --- | --- | --- |
| P0 | 源码重建 + 通读 wiki 链路 | 容器内 `go build ./...` 通过 |
| P1 | 只读改造：新增本体模型/专家清单接口，前端可选 | 界面能选到 BMM / EA 等模型与专家角色 |
| P2 | 抽取引擎接入（路线 A 或 B），结果落 Neo4j | 导入文档后 Neo4j 出现本体类型节点与合法关系 |
| P3 | Wiki 生成替换：页面类型来自本体 class | 页面分类与筛选按本体 class 呈现 |
| P4 | 存量注入 + 关系校验（SHACL） | 新页面与存量节点建立符合本体的关系，违规项进人工确认 |

## 6. 待确认（阻塞项）

1. 抽取引擎路线：**A（Port to Go）** 还是 **B（Python sidecar）**？
2. 页面类型粒度：一个 class 一个页面类型（`bmm:Goal`），还是按模块聚合后再分 class？
3. 「文档类型 → 默认本体模型/专家」的映射表由 `.env` 配还是界面每次选？

## 7. 本次不做什么

- 不改动上游文档解析/分块/向量化/检索/对话（用户明确要求保留）。
- 不动认证、租户、组织、共享等与知识生成无关的模块。
- 不改 Neo4j 之外的其他检索后端（Milvus/Qdrant/Weaviate 等 profile 门控组件）。

## 8. 设计确认（2026-09-18，用户口径）与源码依据

### 8.1 「上传后自动生成 wiki，那在哪个点选本体？」→ 有两级选择点

上游**本来就支持两级配置**（源码证据）：

| 级别 | 落点 | 证据 |
| --- | --- | --- |
| **知识库默认** | KB 的 `WikiConfig` / `ExtractConfig` | `internal/types/wiki_page.go:524` `WikiConfig{ExtractionGranularity, ContentInstructions, ExtractionInstructions, Ingest*}`；`internal/types/knowledgebase.go:120` `WikiConfig *WikiConfig`（json 列） |
| **单文档覆盖** | 上传时传 `process_config`（`KnowledgeProcessOverrides`），存进该文档的 metadata（键 `process_overrides`） | `internal/handler/knowledge.go:430`（上传请求体 `process_config`）、`internal/types/knowledge.go:433` `metadataKeyProcessOverrides = "process_overrides"`、`internal/types/knowledge.go:436` `Knowledge.ProcessOverrides()`、`internal/application/service/knowledge_process.go:2552` `ValidateProcessOverrides(...)`、`ResolveProcessConfig(kb, overrides)` |

**结论**：本体模型 + 专家角色的选择放在 **①知识库设置（默认，供自动流水线用）**，
并在 **②上传/重解析时用 `process_config` 覆盖（单文档）**。自动生成 wiki 的链路不变，
只是链路内部读的是"该文档生效的本体模型"。

### 8.2 抽取引擎：Python 组织提示词，输出对齐 WeKnora

- **路线定为 B**：Python 侧组织 system/user 提示词并调用 LLM，产出的 JSON **按 WeKnora 的形态**返回。
- **不让 LLM 判断 create/merge**：LLM 只产出候选要素/关系；**由 WeKnora 的向量/身份去重决定新增页面还是合并**
  （证据：`internal/application/service/wiki_ingest_dedup.go` 的 `selectDedupCandidatePages`、
  `normalizeWikiIdentityTitle`、`stabilizeExtractedIdentities`、`claimWikiIdentitySlug`）。
  → 我方 JSON **去掉 `action` / `existing_id`**。
- **与源文的关联**：改用 WeKnora 的引用机制（`wiki_ingest_cite.go` 的 chunk 引用、
  `wiki_page_revisions`），即输出里带上 **chunk_id / knowledge_id**，而不是我们自己的 excerpt_id。
- **与存量 wiki 的关联**：存量喂给提示词的应是 **既有 Wiki 页面的 slug/标题/摘要**（`wiki_page` 表 + `wiki_linkify.go` 的链接规则），
  输出里的链接也用 WeKnora 的页面引用形式，而不是我们的 `./kn/类型/名称`。

### 8.3 多源文档与「唯一权威定义」

`src` 侧已支持"一个知识由多份文档的多个片段提及或定义"，并要求**只允许一个权威定义**
（`SourceInfo.is_authoritative`、`graph_service.set_authoritative_source`）。
在 WeKnora 里对应：**一个 slug 一个页面**（多源汇入同一页面的 citations），
权威定义标记需要落在页面上（本轮新增字段），这样"文档更新 → 驱动知识更新"有明确入口。

### 8.4 页面类型 = 一个本体 class 一个类型，前缀取模块简称

- 现状枚举是 6 个常量：`summary / entity / concept / index / synthesis / comparison`
  （`internal/types/wiki_page.go:138-152`），`IsValidWikiPageType`（同文件 168 行）做白名单。
- 改为：页面类型取值形如 **`bmm:Goal`、`ea:Activity`、`easvc:ServiceContract`**
  （前缀 = 模块简称：bmm / ea / easvc / eaown / bmmfd）。

### 8.5 提示词用「轻量版」，不加载完整 TTL

- 上游/我方提示词**不注入完整 TTL**，用轻量版内容；**专家角色移入 TTL**（不再写死在 `config.py`，已实施）。
- **轻量版现状（2026-09-18 决定：先只做 bmm + ea）**：

| 模块 | 轻量版真源 | 产物 | 界面可选 |
| --- | --- | --- | --- |
| `bmm` | `ontology/BMM轻量版.md`（已有） | `artifacts/prompts/bmm_light.md` | ✅ |
| `ea` | `ontology/EA轻量版.md`（本轮新建，结构对齐 BMM 轻量版） | `artifacts/prompts/ea_light.md` | ✅ |
| `ea-service` / `ea-ownership` / `bmm-fd` | 暂无 | 无（emitter 跳过，不产出空文件） | ❌ |

- 由 `emitters/light_prompts.py` 把真源**复制**到产物目录并标注来源；`ontology_index.json`
  每个模型带 `light_available` / `light_source` / `light_prompt`，**fork 界面只应把
  `light_available=true` 的模型列为可选**（当前即 bmm / ea）。
- `.env` 里的 `ONTOLOGY_MODELS`（BMM / EA）与此一致。

### 8.6 `extract_config.*.json` 的角色变更

用户口径：**不再需要**该 JSON——fork 只需要
**「所有本体模型的 class」**（知识分类枚举）与 **「relationType」**（关系类型枚举）。
→ 保留产物（仍有 SHACL/Neo4j/JSON Schema 等消费者），但**不再作为与 fork 的接口**；
新的接口产物是**枚举目录**（class + relationType + 模块前缀），即 `artifacts/weknora/ontology_index.json`。

## 9. v0.1 发布范围（用户口径，2026-09-18）

目标：**先发布一个可用版本** —— 验证「用本体提取」的能力，并能在界面上**看到提取出的知识图谱**。

### 9.1 范围

- 抽取模型只提供 **bmm / ea**（其余 3 个扩展模块留给后续"推理验证"，暂不出现在界面）。
- 提取提示词用**轻量版 md**（§8.5），不注入完整 TTL。
- 发布判据（缺一不算可用）：
  1. 上传文档后能触发**本体驱动**的抽取（替换上游通用实体-关系抽取）；
  2. 抽取产物写入图谱：**节点是 wiki 页面**，类型取本体 class；
  3. 界面有**知识图谱视图**：节点按本体类型着色、边 = 关系（带 label + 方向箭头）。

### 9.2 与上游「页面链接图谱」的区别（不要混）

| | 上游 wiki 链接图 | 我们的本体图谱（v0.1） |
| --- | --- | --- |
| 接口 | `GET /knowledgebase/:kb_id/wiki/graph`（`WikiPageHandler.GetGraph`，`routes_knowledge.go:326`） | **新增** `GET /knowledgebase/:kb_id/ontology-graph` |
| 节点 | wiki 页面（`slug/title/page_type/link_count`） | wiki 页面 + **本体类型**（`bmm:Goal`…） |
| 边 | `{source,target}`，**无 label、无方向语义** | `{source,target,type,label}`，**必须渲染箭头** |
| 用途 | 页面互相引用的导航图 | 本体驱动的知识结构（后续推理的载体） |

上游 i18n 自己也承认两者不同：`tabGraphTip`「Wiki 页面之间的引用关系图…与『知识库设置 → 知识图谱』
中基于 LLM 抽取的实体-关系图谱不是同一个概念」；而抽取出来的实体-关系图**在本版本里没有可视化入口**
（只进 `RetrieveGraphRepository` 供 GraphRAG 检索）。

### 9.3 新接口契约

`GET /api/v1/knowledgebase/{kb_id}/ontology-graph?models=bmm,ea&limit=…`

```json
{
  "nodes": [{"id": "<wiki slug>", "title": "…", "page_type": "bmm:Goal", "type_label": "目标",
             "module": "bmm", "color": "#3b82f6", "degree": 3}],
  "edges": [{"source": "…", "target": "…", "type": "bmm:realizes", "label": "实现",
             "directed": true, "evidence": [{"knowledge_id": "…", "chunk_id": "…"}]}],
  "meta": {"total": 0, "returned": 0, "truncated": false,
           "legend": [{"page_type": "bmm:Goal", "label": "目标", "color": "#3b82f6"}]}
}
```

- `color` 与 `legend` 由**编译产物**下发（`ontology_index.json` 的 `classes[].color` / `legend[]`），
  前端**不硬编码颜色**、也不再维护类型清单。
- 页面类型取值形如 `模块前缀:Class`（§8.4）；`IsValidWikiPageType` 必须改为读本体目录。

### 9.4 前端渲染要求（用户口径）

- 图库：**AntV G6**（镜像里已打包：`graphManager` / `getItemGraphicEl` 等符号；无需新增依赖）。
- 节点：wiki 页面；**填充色 = 该页面本体类型的颜色**；标签显示页面标题。
- 连线：**关系类型 label**（如「实现行动方案」）；**按方向画箭头**（G6 `endArrow: true`）。
- 图例：由 `meta.legend` 渲染（可点选高亮/过滤）。
- 入口与"页面链接图"**分开**（不同 tab/菜单），避免混淆。

### 9.5 v0.1 明确不做

- 不做跨库推理；不做 `ea-service` / `ea-ownership` / `bmm-fd` 的抽取（后续阶段）。
- 不改上游的 wiki 页面链接图，以及 Wiki 生成之外的流水线。

## 10. 交互设计：把「知识提取」变成对话能力（用户口径，2026-09-18）

### 10.1 用户的设计意图

- 提取是**单模型**的：让模型集中注意力，每次只提一类我们关心的知识；
- **同一文档的片段可以多次提取**，每次换模型 → 可能产出新知识；
- WeKnora 目前没有这个前端功能 → 希望**在对话里增加知识提取能力**，或**新增一类"知识提取"对话**：
  意图识别判断是提取任务时，**要求用户明确**：①本体模型 ②源文档 ③源文片段
  （可按关键词筛选，或直接选章节/切片）。

### 10.2 上游已有的基础设施（都不是空白）

| 需要的能力 | 上游现状（证据） |
| --- | --- |
| 意图识别 | `chat_pipeline/query_understand.go`：「performs query rewriting and **intent classification**」，输出 `{"rewrite_query":…,"intent":"kb_search"}`；枚举 9 个（`types/chat_manage.go:87-95`） |
| 意图→提示词 | `ConversationConfig.IntentSystemPrompts map[string]string`；`config.go:1036-1045` 用 `IntentPrompts` 模板回填，**模板 ID 必须等于意图值**（模板文件 `config/prompt_templates/intent_prompts.yaml`，可挂载覆盖） |
| 管线插件化 | `chat_pipeline/` 29 个插件（search / rerank / merge / query_understand / extract_entity / wiki_boost / query_knowledge_graph …） |
| @提及文档 | 对话请求 `mentioned_items:[{id,name,type:kb|file}]`；Agent 配置 `kb_selection_mode: all/selected/disabled`、`retrieve_kb_only_when_mentioned` |
| 片段级访问 | Agent 工具：`knowledge_search`、**`grep_chunks`**（关键词筛）、**`list_knowledge_chunks`**（列切片）、`get_document_info`；chunk 管理 API 亦存在 |
| 新增一类 Agent | **纯配置**：`builtin_agents.yaml` / `agent_type_presets.yaml`（i18n + system_prompt_id + allowed_tools + kb_filter），前端经 `GET /agents/type-presets` 自动展示 |
| 工具扩展 | `internal/agent/tools/registry.go` 注册表；已有 `wiki_*`、`query_knowledge_graph` 等先例 |

### 10.3 方案 A（推荐，先做）：配置驱动一个「本体知识提取」Agent

**改动量最小、当天可演示**：

1. 新增 agent type preset（YAML）：
   ```yaml
   agent_type_presets:
     - id: "ontology-extract"
       i18n:
         zh-CN: { label: "本体知识提取", description: "按指定本体模型，从选定文档/片段提取要素与关系并写入知识图谱" }
       config:
         system_prompt_id: "ontology_extract_agent"
         allowed_tools: ["knowledge_search", "grep_chunks", "list_knowledge_chunks",
                         "get_document_info", "extract_ontology_knowledge"]
         kb_selection_mode: "selected"        # 必须先选知识库
         max_iterations: 20
   ```
2. 新增系统提示词 `ontology_extract_agent`（内容要点）：
   - 你要执行的是**本体抽取**，不是问答；
   - **必须**向用户确认三个参数：本体模型（当前 `bmm` / `ea`）、源文档、源片段范围
     （未给全就问，不要自己假设）；
   - 片段范围三种给法：`knowledge_id` + 关键词（走 `grep_chunks`）、`knowledge_id` + 章节、
     显式 `chunk_ids`（走 `list_knowledge_chunks`）；
   - 调用 `extract_ontology_knowledge` 后，把"要素 N / 关系 M / 违规 X / 未归类 Y"和
     未归类清单回报给用户，并给出可点击的图谱入口；
   - 一次只用一个模型；用户换模型再提一遍是**预期用法**。
3. 新增工具 `extract_ontology_knowledge`（Go）：
   - 入参：`model`（bmm/ea）、`knowledge_ids[]`、`chunk_ids[]` 或 `keyword`、`write`（默认 true）；
   - 行为：取指定片段（或关键词命中的片段）→ 组装轻量版提示词（与 CLI 同一套）→ 调 LLM
     → 本体校验 → 写图（节点=知识实体 + `page_type`，边=关系 + label + 方向）；
   - 出参：`{elements, relationships, violations, unmatched, graph_hint}`。
   > 实现上先**复用 `tools/ontology-extract/extract.py`**（Python sidecar / 子进程即可），
   > 后续再决定是否移植进 Go（§4.2 的 A/B 路线在此收口）。
4. 意图识别增强（可选，第二步）：加一个 `ontology_extract` 意图值 + 对应
   `intent_prompts.yaml` 模板（模板 ID = 意图值），命中时切到提取提示词。
   注意：枚举是 Go 常量（`types/chat_manage.go`），新增值需同步 `NeedsKBRetrieval()` 语义。

### 10.4 方案 B（后续）：专用「知识提取」界面

当流程稳定后，做一个独立视图，把"选模型 / 选文档 / 选片段"做成控件：

- 顶部：**本体模型单选**（当前 bmm / ea，来自 `ontology_index.json` 的 `light_available=true` 项）；
- 左侧：源文档（`GET /knowledge-bases/:id/knowledge`）＋**章节树**（chunk 的父子结构）；
- 中间：**片段多选**（按 `grep_chunks` 关键词筛选，或直接勾选切片；显示命中高亮）；
- 右侧：运行 → 预览（要素/关系/违规/未归类）→ 确认写入；
- 底栏：本次写了哪些节点/关系（点击跳图谱页）。

### 10.5 多模型多次提取带来的两个设计问题（需要正视）

1. **同一实体会在不同模型下成为不同类型**：节点 key 含类型（`bmm:Goal` vs `ea:Activity`），
   因此同名实体可能出现在两个类型下。正式版应引入**实体身份层**（identity/alias），
   把"同一实体在不同模型视角下的实例"聚合，这也正好承接 §8.3 的**多源唯一权威定义**。
2. **一次提取的产物归属**：写入要带 `knowledge_id` / `chunk_id`（本版 CLI 暂用
   `source_doc` + 逐字 `source_text`），否则做不到"文档更新 → 驱动知识更新"。

### 10.6 与最终形态的关系

方案 A/B 都是**在 WeKnora 内**提供提取交互（对话式 / 专用页面），
它们调用的是同一套抽取引擎（轻量版提示词 + 本体枚举 + 校验 + 写图），
与 §9 的图谱展示、§8.2 的抽取契约完全一致 —— 只是入口不同。

### 10.7 关键约束：`wiki_enabled` 是总闸门，不能关（2026-09-19 实测）

上游 `KnowledgeBase.IsWikiEnabled()`（`types/knowledgebase.go:822`）是 wiki 的唯一开关，
它的分布**不止"生成"一处**：

| 位置 | 被闸住的东西 |
| --- | --- |
| `service/knowledge_post_process.go:179` | **自动生成触发点**：`willSpawnWiki := WikiEnabled && len(textChunks)>0` |
| `service/wiki_ingest_batch.go:293,1041` | 批次 ingest 入口直接 return |
| `handler/wiki_page.go:63` | **对外 wiki API 全拒**（403） |
| `service/agent_service.go:861,1300` | **wiki 工具不注册给 agent** |
| `types/knowledgebase.go:799` → `Capabilities().Wiki` | KB 按能力过滤（会话选库会跳过） |
| `chat_pipeline/wiki_boost.go:73` / `service/wiki_lint.go:100` | 对话 wiki 增强检索 / 巡检 |
| `service/knowledge_delete.go:209,668` | 文档删除时的 wiki 清理 |

**因此不能靠 `wiki_enabled=false` 来"停掉上游通用生成"**（会把 API/工具/能力一起关掉）。
正确做法是加**本体模式开关**（KB 级），只改"生成端"：

- 本体模式为真时，`knowledge_post_process.go` 不 spawn 上游候选生成（`willSpawnWiki=false`），
  `wiki_ingest_batch` 的入口同样跳过；
- `wiki_enabled` 保持 `true` → wiki API / `wiki_write_page` 等工具 / KB 能力**全部照旧可用**；
- 页面的**产生**改由本体抽取负责（Python 抽取 → Go 经 `wikiPageService.CreatePage` 写入）。

**写页面的两条硬约束（A1 工具必须处理）**：

1. `wiki_pages` 的唯一约束是**部分唯一**：
   `CREATE UNIQUE INDEX idx_wiki_pages_kb_slug ON (knowledge_base_id, slug) WHERE deleted_at IS NULL`
   → 软删的旧页不阻塞新页；但**同名 slug 已存在（未删）时必须 upsert**（`GetPageBySlug` → 有则更新，无则创建），
   否则 `CreatePage` 撞唯一约束失败。
2. `CreatePage` **要求 slug 必填**（`service/wiki_page.go:75`），没有自动 slug 兜底
   → 工具侧需自己按 slug 规则生成（参考 `wiki_write_page.go` 的 `normalizeAndValidateWikiSlug`）。

### 10.8 本体模式的页面类型如何绕过白名单

`IsValidWikiPageType`（`types/wiki_page.go:168`）是硬编码枚举（entity/concept/index/summary…），
本体类型 `ea:Activity` 会被拒。改法：改为"先查本体目录（`artifacts/ontology_index.json` 的合法类型集合），
命中即合法"，保留原枚举作为兼容分支。这样**不破坏上游既有页面类型**，同时放行本体类型。


### 10.9 预演路径已跑通：真库片段 → BMM 抽取 → wiki + 图谱（2026-09-19 实测）

**脚本**：`tools/ontology-extract/weknora_sync.py`（666 行，零新依赖；`extract.py` 作为引擎被导入复用）

```powershell
python tools/ontology-extract/weknora_sync.py `
  --kb-id dbc2528f-611b-48da-9a71-d7c93975adb4 `
  --knowledge-id 815b301f-703e-4cf8-8bfe-f85e32848f3d `
  --model bmm
# 可选：--dry-run（只导提示词）/ --no-write / --no-wiki / --no-graph / --from-log
```

**实测结果**（源文档《增量数据落标与枚举值管控方案.docx》，13 片段 / 2706 字正文）：

| 环节 | 结果 |
| --- | --- |
| 片段选取 | 13 个片段 → **正文取 1 个 `parent_text` 父块**（2706 字）+ **溯源池 11 个 `text` 子块** |
| LLM | 42.4s 返回 10254 字符，`finish_reason=stop` |
| 本体校验 | **要素 27 / 关系 20 / 违规 0** / unmatched 6（不丢弃，进索引页） |
| 溯源定位 | **exact 47 / span 0 / miss 0**（要素+关系全部精确命中子片段） |
| 图谱 | **节点 27 / 关系 20**，12 个本体类、16 种关系类型（带中文 label），节点与关系都写了 `knowledge_id` + `chunk_id` + `chunk_index` |
| wiki | **28 页**：27 个要素页（`page_type = bmm:<Class>`）+ 1 个索引页；每页带 `category_path`、`source_refs`、`chunk_refs`、`in_links`/`out_links` |

wiki 页面类型分布：`bmm:CourseOfAction 5`、`bmm:OrganizationUnit 5`、`bmm:OperativeBusinessRule 4`、
`bmm:BusinessProcess 2`、`bmm:ExternalInfluencer 2`、`bmm:Goal 2`、`bmm:InternalInfluencer 2`、
`bmm:Assessment 1`、`bmm:Asset 1`、`bmm:BusinessPolicy 1`、`bmm:BusinessService 1`、`bmm:Objective 1`。

**三条已知差异（都是 A1 要收尾的）**：

1. 页面是**直写 Postgres**（字段与 `CreatePage` 对齐，含 links 复算），**未走上游 service**；
   正式版改为经 `wikiPageService` 写入（顺带拿到拼音 slug、linkify、revision）。
2. slug 是 `<模型>/<类小写>/<name sha1[:12]>`（本机无拼音库），正式版由 Go 生成可读 slug。
3. **上游会在打开 wiki 时自动补一个根索引页**（`slug='index'`, `page_type='index'`, `last_edit_source` 空）——
   本次实测就多出这样一页，属上游正常行为，不要误判为脚本重复写。

**幂等**：重跑时先 `DELETE ... WHERE last_edit_source='ontology-extract'` 再整批重建；
页面 id 用 `uuid5(slug)` 稳定生成；SQL 留档 `logs/ontology_wiki_*.sql`，报告 `logs/ontology_sync_*.json`。


### 10.10 方案 A1 落地（零重建，2026-09-19 实测）

**关键结论：原本计划的 Go 工具 `extract_ontology_knowledge` 不需要写。** 两条源码/实测依据：

1. `IsValidWikiPageType` **只被 HTTP handler 调用**（`handler/wiki_page.go:372`、`:514`）；
   `wiki_write_page` 工具（只校验 slug 与非空）与 `wikiPageService.CreatePage`
   **都不校验 `page_type`**，repository 层也没有 → **agent 的工具路径本来就能写本体类型页面**。
2. app 容器启动时从 `/app/config/` **读文件**加载 preset 与 prompt 模板（无 `go:embed`；
   `loadPromptTemplates` 只认固定文件名，所以新模板必须追加进 `agent_system_prompt.yaml`），
   而 `config.yaml` 原本就是挂载的 → **加两个挂载即可生效，无需重建镜像**。

落地物（本仓库）：

| 文件 | 作用 |
| --- | --- |
| `deploy/weknora-fork/gen_agent_config.py` | 由本体产物生成下面两个文件（幂等；`--check` 校验是否最新） |
| `deploy/weknora-fork/baseline/*.yaml` | 从运行中的容器 `docker cp` 出来的基线（升级版本时替换） |
| `deploy/weknora-fork/config/agent_type_presets.yaml` | 基线 + 预设 `ontology-extract-bmm` / `ontology-extract-ea` |
| `deploy/weknora-fork/config/agent_system_prompt.yaml` | 基线 + 模板 `ontology_extract_agent_bmm` / `_ea` |
| `deploy/docker-compose.weknora.yml` | app 服务新增两个 `:ro` 挂载（overlay，不动上游） |

生成与生效：

```bash
python deploy/weknora-fork/gen_agent_config.py           # 生成（读 artifacts/weknora/ontology_index.json）
python deploy/weknora-fork/gen_agent_config.py --check    # 与产物是否一致
cd <上游 WeKnora 部署目录>                                  # 本机：c:\Users\PHJY\source\WeKnora
export BODHI_DEPLOY_DIR=<本仓库>/deploy
docker compose -f docker-compose.yml \
  -f $BODHI_DEPLOY_DIR/docker-compose.weknora.yml \
  --profile neo4j --profile bodhi up -d --no-build        # 重建 app 容器
```

**实测**：app 容器 `Recreated → Healthy`；容器内 `agent_type_presets.yaml` 12230B（preset 命中 2）、
`agent_system_prompt.yaml` 92259B（模板命中 2）✓；启动日志无错误。

**预设内容**：`temperature 0.1`、`max_iterations 40`、
`allowed_tools = [grep_chunks, list_knowledge_chunks, get_document_info, wiki_search, wiki_read_page,
wiki_write_page, todo_write, thinking]`、`kb_selection_mode = selected`；
系统提示词 = 专家角色 + 本体轻量版 + 可用类型（BMM 26 / EA 11）+ 可用关系（BMM 33 / EA 21）+
五步工作流（先读片段 → 抽取 → 写页 → **关系双向落页** → 汇报）+ 硬约束（禁止自造类型/逐字引用/未归类不硬塞）。

两个细节：

- **slug 可以带中文**：`normalizeAndValidateWikiSlug` 放行 CJK（`U+4E00–U+9FFF`），
  所以提示词里直接要求 `bmm/目标/逐步提升落标覆盖率` 这种可读 slug，**不需要拼音库**；
- 页面类型用 `bmm:Class` 形式写进 `page_type`（**不要用 entity/concept**），上游 UI 之外的消费方（图谱、检索）都能用。

**仍未做（下一步）**：让 wiki 列表页展示本体分类页面 —— 前端那 4 个固定筛选（§4.1 ④）
需要重建 UI 镜像（`frontend/Dockerfile` 只 `COPY dist`，必须先 `pnpm build`）。


### 10.11 踩坑记录：重建 app 后前端会全 502（2026-09-19 实测）

**症状**（用户报）：知识库管理为空、创建智能体页为空且有报错、会话建立失败，
但对话里还能看到"企业知识库"（前端已加载的旧状态）。

**根因**：不是配置改动，而是 **app 容器重建后 IP 变了，而前端 nginx 缓存了旧 IP**。

```
frontend nginx: proxy_pass http://app:8080;   # 服务名，但 nginx 只在启动时解析一次
日志: connect() failed (113: Host is unreachable) while connecting to upstream,
      request: "POST /api/v1/sessions HTTP/1.1", upstream: "http://172.18.0.5:8080/api/v1/sessions" → 502
实测: app 重建后 IP 172.18.0.5 → 172.18.0.8，前端仍打 .5
```

**排查要点**：`docker logs WeKnora-frontend 2>&1 | grep 'Host is unreachable'` 一眼可辨；
**数据层没丢**（`knowledge_bases=1 / custom_agents=1 / sessions=1 / wiki_pages=29`）。

**修复**：`docker restart WeKnora-frontend` → 重新解析 → `/health=200`、
`/api/v1/*=401`（通了，只是未登录）、新 502 计数为 0 ✓。

**预防**：`deploy/weknora-fork/apply.sh` 把「重建 app」和「重启 frontend」绑在一起，
以后应用配置一律走这个脚本，不要单独 `up -d app`。

> 备选（更彻底）：给前端挂一个带 `resolver 127.0.0.11 valid=10s;` 的 nginx 配置，
> 让 `proxy_pass` 用变量、每次请求重解析。代价是 nginx 在 `proxy_pass` 带变量时
> **不再自动透传原始 URI**，需要显式 `$request_uri`，容易改错 —— 当前先用"重启前端"这个简单方案。


### 10.12 智能体 vs 类型预设（重要区别）+ 预置本体提取智能体（2026-09-19）

**踩的坑**：只加 `agent_type_presets.yaml` 后，UI 里"找不到新智能体，只有内置 4 个"。

**真相（源码级）**：

| 东西 | 来源 | 出现在 UI 哪里 |
| --- | --- | --- |
| **智能体（agents）** | `builtin_agents.yaml`（内置 4 个）+ `custom_agents` 表（自定义） | 智能体列表 / 新建对话选智能体 |
| **智能体类型预设（type-presets）** | `agent_type_presets.yaml` | **只在「创建/编辑智能体」的类型选择器**里 —— 前端 `stores/editorResources.ts` → `AgentEditorModal.vue` 调用 `getAgentTypePresets()` |

而且**运行时不用预设**，用的是 `custom_agents.config.system_prompt` 的**全文**
（`session_agent_qa.go:360`：`if config.SystemPrompt != "" { UseCustomSystemPrompt = true }`），
预设里的 `system_prompt_id` 只是编辑器 prefill 用。

⇒ 要"开箱可用"，必须写一行 `custom_agents`。已由 `deploy/weknora-fork/gen_agents.py` 完成
（克隆已有智能体 config → 覆盖本体字段；`--apply` 写入）：

| id | 名称 | agent_mode | prompt | tools | kb_selection |
| --- | --- | --- | --- | --- | --- |
| `bodhi-ontology-bmm` | 本体知识提取 · BMM 业务动机模型 | smart-reasoning | ontology_extract_agent_bmm（10171 字符） | 8 | selected |
| `bodhi-ontology-ea` | 本体知识提取 · EA 企业架构 | smart-reasoning | ontology_extract_agent_ea（7578 字符） | 8 | selected |

要点：
- `agent_mode = "smart-reasoning"`（ReAct，多步 + 工具；Go 端常量只有 `quick-answer` / `smart-reasoning` 两个）；
- `agent_type = "custom"`（Go 端只认 `rag-qa` / `wiki-qa` / `hybrid-rag-wiki` / `data-analysis` / `custom` 五个常量，
  我们自己的 preset id 放进去没有消费方，用 `custom` 最安全）；
- 工具集固定 8 个（读片段 3 + wiki 4 + 计划 1）；`temperature 0.1`、`max_iterations 40`；
- 两条都是 `is_builtin = false` → 用户在 UI 里可自由改名/改配置/删除。

### 10.13 本体模型知识库（规划，待确认）

需求：把「企业本体模型」单独做成一个知识库，**每个本体类一个 wiki 页面**，关系在页面中表达；
其他知识库/智能体需要理解页面类型时去那里查；内容**由 TTL 编译生成**，改 TTL 就更新该知识库。

建议的数据流（单一真源 = TTL，全部产物由编译器生成）：

```
ontology/*.ttl ──(tools/ontology-compiler)──┬─→ artifacts/weknora/ontology_index.json   （类型/关系枚举、颜色、图例 → 提示词）
                                            ├─→ artifacts/prompts/<key>_light.md        （轻量版正文 → 提示词）
                                            ├─→ artifacts/weknora/ontology_wiki/…       （新：每个本体类一页 → 本体模型 KB）★
                                            └─→ artifacts/neo4j/*.cypher                （图谱投影；本机已按用户要求清空，可停用）
```

- **投影程序**：`tools/ontology-extract/project_ontology_wiki.py`（复用 `weknora_sync.py` 的 SQL 写入器；
  幂等：先删 `last_edit_source='ontology-wiki'` 的页面再整批写入）。
- **页面类型建议**（自描述、不与抽取页混淆）：
  `ontology:Module`（模块索引页）、`ontology:Class`（每个本体类一页，含属主模块/父类/属性/关系 domain-range/示例）、
  `ontology:Relation`（每个对象属性一页，便于按关系名查）、`ontology:LightDoc`（轻量版全文，供 agent 直接读）。
- **其他知识库怎么用**：把该 KB 一并选进对话 → agent 用 `wiki_search` / `wiki_read_page` 查定义；
  提示词里仍内嵌类型枚举（现状），KB 页是"权威定义 + 人查"的落点。
- **一致性**：轻量版 md 建议**继续由 TTL 生成**（避免 TTL→KB→light 形成环），
  但可以把 light 文档**也投影成 KB 里的一页**（`ontology:LightDoc`），满足"从知识库查"的诉求；
  另提供 `--check` 比对 KB 页面与 TTL 是否一致。

**待用户确认**：① 新 KB 名称与范围（是否所有 5 个模块都建页）；② 页面类型命名；
③ 是否彻底放弃 Neo4j 本体投影（据此把 overlay 里的 `bodhi-ontology` 服务停掉，避免下次 `up` 又灌回去）。


### 10.14 原始实施细节（已被 §10.10 取代，仅留档）

> 下面这版计划（新增 Go 工具 + 注册 + 重建镜像）在 2026-09-19 被证伪：
> 工具路径本来就能写本体类型页面、preset/提示词可从挂载文件加载，
> 所以 **不需要动 Go、不需要重建 app 镜像**。此处留档备查。

**分工原则**：Go 侧负责"平台内的事"（拿片段、写 wiki 页面），Python 侧负责"本体抽取"（组提示词、调 LLM、校验）。

| 改动 | 文件 | 依据 |
| --- | --- | --- |
| 工具名常量 | `internal/agent/tools/definitions.go`（+`ToolExtractOntologyKnowledge = "extract_ontology_knowledge"`） | 现有常量块 `definitions.go:9-76` |
| **新工具** | `internal/agent/tools/extract_ontology_knowledge.go`（新建） | 模仿 `grep_chunks.go`（取片段）/`wiki_write_page.go`（写页面） |
| 注册 | `internal/application/service/agent_service.go`（+1 行 `toolRegistry.RegisterTool(...)`） | 注册点 `agent_service.go:424-806` |
| 预置 Agent | `config/agent_type_presets.yaml`（+`id: ontology-extract`） | 结构见 `types/agent_type_preset.go:59` |
| 系统提示词 | `config/prompt_templates/*.yaml`（+`ontology_extract_agent`） | 模板注册见 `config.go` 的 `PromptTemplateStructured` 装配 |
| 抽取服务 | `tools/ontology-extract/server.py`（新建，标准库 HTTP，复用 `extract.py` 的引擎） | 本仓库已跑通 |
| 编排 | `deploy/docker-compose.weknora-fork.yml`（新增：抽取服务容器 + 挂载 `artifacts/`、`ontology/`、注入 `BODHI_EXTRACT_URL`） | 与现有叠加层同风格 |

**新工具契约**（`extract_ontology_knowledge`）：

```jsonc
// 入参
{
  "model": "ea",                       // 必填：bmm | ea（= 本体模型，界面只列 light_available=true）
  "knowledge_ids": ["..."],            // 源文档（不填则用本轮 @提及/会话选中的库范围）
  "chunk_ids": ["..."],                // 源文片段（显式选择时）
  "keyword": "开户|审核",              // 或关键词筛选（走 grep_chunks 同款检索）
  "limit": 40,                         // 片段上限
  "write_wiki": true,                  // 是否把结果写成 wiki 页面（默认 true）
  "write_graph": true                  // 是否写图谱（默认 true）
}
// 出参（供 LLM 回报给用户）
{
  "model": "ea", "model_label": "EA 企业架构",
  "chunks_used": 12, "elements": 42, "relationships": 57,
  "violations": [], "unmatched": [{ "name": "...", "reason": "..." }],
  "wiki_pages_written": 42, "graph_hint": "MATCH (n:BodhiInstance) WHERE n.kb='<kb>' RETURN n"
}
```

**执行流程**：
1. Go：解析参数 → 取片段（`knowledge_ids`/`chunk_ids`/`keyword`，复用既有检索与 chunk 仓储）；
2. Go → Python：`POST {BODHI_EXTRACT_URL}/extract`，body = `{model, doc_name, chunks:[{chunk_id, knowledge_id, title, text}]}`；
3. Python：组装轻量版提示词（专家角色取自 TTL、本体枚举取自 `ontology_index.json`）→ 调 LLM →
   本体校验（类型白名单 + 关系 domain/range）→ **写 Neo4j**（节点 label = `模块__类`、边 = 关系 +
   中文 label + 方向）→ 返回 `{elements, relationships, violations, unmatched}`（附 `chunk_id` 归属）；
4. Go：把 `elements` 写成 **wiki 页面**（`wikiPageService.CreatePage`，`page_type = 模块:Class`，
   `source_refs`/`chunk_refs` 指向本次片段）→ 汇总回报给用户。

**前置依赖（都已具备）**：源码树 `/root/wk080`（v0.8.0，go 833/838，含 `go.mod`/`Makefile`/`Dockerfile.app`）、
运行中的栈、已验证的 Python 引擎（42 节点/57 关系）。

**仍需的小改动**：`IsValidWikiPageType` 目前是硬编码白名单（§4.1），
不放开的话 `page_type = ea:Activity` 会被拒 → 该函数改为"接受本体目录里的合法类型"。

**验证方式**：A1 完成后，在对话里说「把《增量数据落标与枚举值管控方案.docx》按 EA 模型提取一遍」，
Agent 应先确认模型/文档/片段，再调用工具；随后 wiki 里应出现**本体类型**的页面
（`SELECT DISTINCT page_type FROM wiki_pages` 里出现 `ea:*`），图谱里出现对应的节点与关系。



