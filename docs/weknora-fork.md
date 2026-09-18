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

