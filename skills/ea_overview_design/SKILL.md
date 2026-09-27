---
id: ea_overview_design
name: 企架概要设计
description: 基于已建好的业务模型（wiki 知识）做 IT 服务层概要设计：先出报告页，再细分出服务/应用节点页。
version: 0.1.1
when: 用户要求基于**已建好的业务模型（wiki 知识）**做 IT 服务层设计（说法如：概要设计、IT 服务设计、服务清单、按流程设计服务、服务与系统定位）。
models: [ea]
default_model: ea
scope:
  classes: [ea:Service, ea:APIService, ea:MCPService, ea:SkillService, ea:Application, ea:Step, ea:Task, ea:BusinessEntity, ea:ITAsset]
  relations: [ea:stepUsesService, ea:applicationProvidesService, ea:stepOperatesOnEntity, ea:stepSupportedByAsset, ea:taskHasStep]
sources: [graph, document]
stages: [report, graph]
tools: [ontology_types, save_knowledge, skills, wiki_search, wiki_read_page, list_pending_merges]
inputs:
  activity_or_process: 必填。从哪个流程/活动分支出发（如「手机银行注册、实名验证、签约流程」）
  upstream: 可选。你读过的需求页 slug（写进报告页溯源）
guard: 两段都要人工确认；落库必须 mode="apply"；新建应用/系统节点需用户确认
---

# 企架概要设计

从**业务模型**（流程 → 活动 → 任务 → 步骤 + 实体/角色/规则）归纳出 **IT 服务清单**，
落成「报告页 + 每个服务一页」的两层结构。**校验与渲染在服务端**（类型/关系合规、页面小节、CRUD 矩阵），
你负责内容判断与 JSON 结构。

## 步骤
1. **读业务模型**：`wiki_search` / `wiki_read_page` 读该流程相关页（流程、活动、任务、步骤、实体、角色、规则）；
   沿 `## 本体关系` 展开到步骤。
2. **归纳 IT 服务**：把「任务/步骤」归并为若干内聚服务；**类型一次定好**
   （`ea:APIService` / `ea:MCPService` / `ea:SkillService` 三选一，之后重跑不要改类型 —— 改类型会产生另一套页）。
   每个服务写清：`purpose`（用途）、`inputs` / `outputs`（业务对象级）、归属任务与步骤、
   **正常/异常案例 ASSERTION**（`assertions[{id, kind:"N|E", assertion}]`）；有可执行技能定义时才填
   `attributes`（数据属性，如 `ea:ai_skill`）。
3. **定位实现系统**：判断服务由哪个现有应用实现；找不到合适才建议新建（写清职责与边界）。
4. **第一段（报告）**：形成报告 md（下面结构），调
- 落库时带 `context="ea_overview_design"`（报告页类型/分类=「概要设计报告」）。
   `save_knowledge(stage="report", mode="dry_run", context="ea_overview_design")`：
   `report={title, content_md, upstream=[你读过的页 slug], source_document_id/title}`。
   把将要新建/更新的清单给用户看；**用户确认后同载荷 `mode="apply"` 重跑**，记下返回的报告页 **slug**。
   （重跑**同一标题** = 更新该报告页：slug 不变、正文整体替换 —— 不要为"改版"改标题。）
5. **第二段（细分）**：调 `save_knowledge(stage="graph", mode="dry_run")`：
   - 节点：IT 服务 → `ea:Service` 或子类；应用系统 → `ea:Application`；
     可引用已有页时**节点名用库里已有标题**（如步骤页）以便连边；
   - 关系：`ea:stepUsesService`（步骤 → 服务）、`ea:applicationProvidesService`（应用 → 服务）；
   - `report.slug` 传第一段拿到的 slug（细分页挂到报告页下）；
   - **新建「应用/系统」节点必须人工确认**：把 `pending_confirmation` 念给用户，同意后带
     `confirmed_new_applications=[…]` 再 `mode="apply"`。
6. **汇报**：报告页、各服务页（新建/更新）、系统页、关系条数、被拒违规项；不要贴 JSON。

## 报告 md 结构（固定）
```
## 1. 范围与依据        # 哪个流程/活动分支；读了哪些页（列 slug）
## 2. IT 服务清单       # 表格：服务名 | 类型(API/MCP/Skill) | 用途 | 新建/修改
## 3. 与业务模型的关系   # 每个服务 ← 归属任务/步骤（用页标题）
## 4. 系统与资源        # 归属应用（现有/新建）；需新建的资源及理由
## 5. 设计规范（正常 / 异常案例 · ASSERTION）
## 6. 待确认事项        # 边界、命名、是否新建系统
```

## 落库纪律（必须遵守）

## 写库前置（硬规则，2026-09-22）

**写操作必须唯一确定目标知识库**：会话可能同时绑定多个库（查询/检索可以多库，**保存只能落一个**）。

1. 把会话绑定的库清单（`<bound_knowledge_bases>` 里的 `id`）原样传给 `kb_ids`；
2. `kb_ids` > 1 且用户没点名写哪个库 → 服务端**拒绝写**并回 `need_kb_selection: true` + 候选清单：
   **必须问用户"写进哪一个库"**，拿到答复后带 `kb_id=<完整 uuid 或精确库名>` 重跑；
3. `kb_id` 只认**完整 uuid**或**精确库名**；uuid 前缀 / 名称包含（模糊）会回 `need_kb_confirm: true`
   → 改传完整 uuid，或在用户确认后带 `confirm_kb_match=true` 重跑；
4. 库名不要含糊（只写"企业"这种）——命中多个会被拒，命中一个也要确认。

> **相似度与关系解析都只在本库内**：目标节点若只存在于别的知识库，回执会出现
> `cross_kb_same_name` 并把它计入 `violations`（**绝不跨库合并**——同名不代表同义）。
> 要连到那个节点，先在本库建立它，或改用本库内的等价节点。

- **`mode` 必须显式写**：`dry_run` 只是预览（回执 `applied=false` + `write_note`）；要落库必须 `apply`。
  **只以回执 `applied=true` 与 `page_versions` 为准**；`applied=false` 时绝不说"已写入/已更新"。
- **重跑同一份内容 = 更新同一页**（报告页正文整体替换；节点页 slug 命中原页即合并），不要靠改标题绕开旧页。
- **slug 规则**：`<类型所属模块>/<类小写>/<名称>`，故服务页在 `ea/mcpservice/<名称>`（或 `ea/apiservice/…`、
  `ea/skillservice/…`）。历史模块 `bmm-ea-ext/…` 已废弃 —— 不要按该前缀检索（0 条属正常）；
  找既有页按**类型**（`ea:MCPService`）或**名称关键词**搜（关键词别带 `/`）。
- **不要手写引用/入边**：`## 被引用（入边）` 由系统按本体关系生成；关系只在 `edges` 里给。
