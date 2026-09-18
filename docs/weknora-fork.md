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

### 10.7 方案 A1 实施细节（2026-09-19，源码已核实到文件/行）

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



