---
id: knowledge_extract
name: 知识提取（按规则的知识需要取事实）
description: 由**规则的知识需要**（`bmm:knowledgeNeed`）驱动：先判定"要哪些知识 + 属于哪些本体模型（**可能多个**）"，再从文档/图/外部接口（挡板）取**逐字事实**并落库；工具只执行、智能体负责理解。
when: 规则实现方式含"知识提取"，或规则声明了 knowledgeNeed；或任务步骤要求"抽表格/抽条款/抽清单"时。
models: [bmm, bpmn, agent]
default_model: bmm
stages: [extract]
tools: [ontology_types, knowledge_lookup, doc_outline, list_knowledge_chunks, grep_chunks, image_extract, save_knowledge, wiki_read_page, extract_state]
version: 0.1.0
guard: 先定模型/类型再抽；只抽本批文本支撑的事实；每条事实带 source_text（原句）+ sourceLocator（会话定位）；外部系统已管理的数据**不本体化**（查挡板/接口）；多库未点名 ⇒ 拒写；跨库同名不合并。
---

# 知识提取（规则驱动）

**输入**：`domain_kb`（领域库，规则/类型在这里）· `work_kb`（工作库，文档/页在这里）· 事实需求（来自规则的 `knowledgeNeed`）。
**分工（第一原则，沿用 `structured_modeling`）**：**你（智能体）负责"理解与判定"**（要什么知识、属于哪个类/属性）；
**工具只做确定性的事**（读表/校验/建页/写关系）——**工具不做语义推断** ✓。

## 0. 先定"知识需要 + 本体模型范围"（**本技能第一步，可能有多个模型**）
1. 从规则取需求：`knowledge_lookup(kb_id, mode="rules", spec="task:…")` → 该规则的 `knowledgeNeed`（如"技术栈及其版本（文档中以表格形式勾选）"）。
2. 把它拆成**知识项**（名称/版本、状态等级、部署方式、外部依赖、清单类数据…）。
3. **判定每项属于哪个本体模型/类**：`ontology_types("<模型>")` **现查** ✓，**不写死类型清单** ✓；
   - 典型：系统/资源类 → `bmm`（`bmm:ITAsset`/`bmm:SubSystem`）；规则/策略 → `bmm`；文档产物 → `bpmn:DataObject`/`bmm:Report` 系。
   - **一个知识需要可能跨多个模型** ✓ ⇒ 先定**主模型**，再按依赖引入（`ontology_types` 回执的 `requires`/`affects` ✓）。
4. **类不明确 ⇒ 不要猜** ✗：要么先在 `ontology_types` 里找最近类并**向用户确认**，要么用 `knowledge_lookup` 返回候选类让用户选（澄清后才筛 ✓，与检索工具口径一致）。
5. **系统已管理的数据不本体化** ✗：如"外部清单对象"由 IT 工作平台维护 ⇒ 它**不是**本体类/实例集合，而是 **DataObject**，查询走**挡板/接口**（挡板输出=该 DataObject；样例在其 `skos:note`）✓。

## 1. 取事实（按来源分层，全部要留定位）
1. **文档**：`doc_outline(work_kb, knowledge_id, budget_tokens, cursor)` 取本批 → 只抽**本批文本支撑**的内容 ✓；
   表格：逐行逐格取值（行/列定位）；条款：原句 + 章节；
2. **图**：``（只读 Cypher）取实例/关系事实；
3. **外部系统**：查**挡板/接口**（见 0.5）；返回值即"事实"，与文档抽取值同权 ✓；
4. **图像**：`image_extract`（部署图/架构图/技术栈截图）→ 结构化后再判定（可信度低 ⇒ 待澄清 ✓，不硬判 ✗）。

## 2. 落库（沿用 `domain_modeling` 的硬规则）
1. **写库唯一**：`kb_ids` > 1 且用户没点名 ⇒ 服务端拒写（`need_kb_selection`）✓；`kb_id` 只认**完整 uuid/精确库名**（模糊 ⇒ `need_kb_confirm`）✓；
2. **会话身份必带**：`save_knowledge(..., session_no=…, session={knowledge_id, round_no, cursor, next_cursor, doc_title})` ✓；
   每节点给 **`source_text`（原句）** + **`attributes.bmm:sourceLocator`**（如 `session/S-…/p2#轮3/段5`）✓ —— 只写原句不写定位 = **溯源链断** ✗；
3. **先预览后写**：`mode="dry_run"` → 用户确认 → `mode="apply"` ✓；
4. **未映射的列不要硬塞** ✓（默认聚合进 `description`，无关则关掉 ✓）；
5. **两侧先存在** ✓：先建/确认类实例与目标页，再建关系；`target_exists=false` 时**不能**确认 ✓；
6. **跨库同名不合并** ✗（`cross_kb_same_name`）；跨库引用前先 `context_lookup`，请用户裁决 ✓。

## 3. 分批与续跑
- `extract_state(work_kb, knowledge_id)` 对齐进度（`pages` 非空 ⇒ 用 `cursor` 续跑，**不从头重抽** ✓；`pending_links` 非空 ⇒ 先让用户裁决 ✓）；
- 循环"取批 → 抽 → 落 → `cursor=next_cursor`"直到 `done=true` ✓；内网算力有限，超时就把 `budget_tokens` 调小 ✓。

## 4. 纪律（来自原领域建模/结构化建模，保留有用项 ✓）
1. 不指望"整篇一次性抽取" ✗（已退役）；一轮一批 ✓；
2. 跨批/跨库关联：先 `knowledge_lookup`（模式一，可带 `classes` 收窄 ✓）召回 → **登记候选不写库** → 用户逐条确认 → 再写 ✓；
3. 类/属性一律**现查**（`ontology_types`）✓，工具会拒未声明类型 ✓；
4. **一次只建一个目标**（一个类批 / 一条关系）✓，不逐行确认 ✓；
5. 抽不到就**待澄清**（写清"缺什么/需要谁提供"）✓——**不要编** ✗；
6. 所有事实都要能回答："**哪个规则需要它 + 出自哪句原文/哪个接口返回**" ✓。

## 5. 验收断言
1. 每条事实都有 `source_text` + `sourceLocator` ✓（抽查 3 条）；
2. 知识项落在**已确认的模型/类**里 ✓（`ontology_types` 可查 ✓，无 `B1` 违规 ✓）；
3. 表格类抽取**逐行逐格**、覆盖完整（行数 = 表行数 ✓）；
4. 外部系统数据走挡板/接口 ✓，**没有**为它新建本体类/实例集合 ✗；
5. 同输入重跑幂等（同 slug 版本+1 或不变 ✓）。

## 工具口径（2026-10-17 统一，`skills/_deprecated.md` §四）

- **取规则**：`knowledge_lookup(kb_id, mode="rules", spec=…)`
  - `spec`：`direct:<slug[,slug]>` / `policy:<策略>` / `influencer:<影响因素>`（链路口径）/ `ask`（**只回策略清单、不含规则**）/ `path:`（预留）；`task:` 已废弃
  - **影响类问题用 `influencer:`**（影响因素→影响手段→策略→规则）；**确需全量策略清单才用 `ask`**
- **找知识**：`knowledge_lookup(kb_id, mode="keyword", query=…)` —— 图优先 → PG 收窄融合 ✓
  每条命中带 `source`（`graph` / `graph+pg` / `pg`）；`pg` 占比高 ⇒ 图缺数据 ✗
- **多跳**：`knowledge_lookup(kb_id, mode="path", start=<slug>, depth=2)` ✓
- **写库**：`write_knowledge`（**图 → PG** ✓）；`document` 类（`bmm:Report` 子类）**必须有正文** ✓

## 抽取产出契约（2026-10-17 用户口径 ✓）：三态 + `found_in`

每条事实**必须**产出下面 7 个字段 ✓（`ke_plan.items[]` 逐项填 ✓），否则判定层无法算可信度 ✗：

```json
{"id": "deploy_fig",
 "status": "present" | "absent_verified" | "unavailable",   // 三态 ✓
 "confidence": "high" | "medium" | "low",                   // 你对该取值的把握 ✓
 "source_type": "doc_text" | "doc_table" | "image" | "graph" | "external_query",
 "found_in": "doc" | "other_kb:<库名/slug>" | "none",       // **从哪来** ✓
 "quote": "逐字摘录（可全文检索到 ✓）",
 "locator": "章节/表格行列/图号（可回访 ✓）"}
```

### 判定三态的**硬规则**（这是"文档遗漏 vs 外部不可得"的分界 ✓）
| 情况 | status | 是否算问题 |
|---|---|---|
| 文档里有 ✓ | `present` | — |
| 文档里没有 ✗，**但其他知识库/系统能拿到** ✓ | **`absent_verified`** ✓（`found_in="other_kb:…"` ✓，quote 写**其他库的来源** ✓） | **是问题** ✓ ⇒ 判 `failed`（文档遗漏 ✓） |
| **任何地方都拿不到** ✗ | `unavailable` ✓（`found_in="none"` ✓） | **不算问题** ✗ ⇒ 只能 `无法判定` ✓ |

### 禁止 ✗
- **不许**在文档里没有、但没去其他库查过就写 `unavailable` ✗ —— 那是把"我们没查"当成"查不到" ✗；
- **不许**自己判 `不符合` ✗（判定是 `rule_judge` 的事 ✓）；
- **不许**写 `present` 但 `quote` 是转述 ✗（必须逐字 ✓，且 `locator` 可回访 ✓）。

### 自检
- [ ] 7 个字段齐全 ✓；`status` 只用三态 ✓
- [ ] `absent_verified` 的条目 `found_in` 必须是 `other_kb:` ✓ 或明确的"文档确认无"证据 ✓
- [ ] `unavailable` 的条目必须有"已查过哪些来源"的说明 ✓（写在 quote 或 note ✓）
- [ ] `present` 的 `quote` 能全文检索到 ✓、`locator` 能回访 ✓

## 检索（类型优先，2026-10-22）

- 先 `ontology_types(model)` 看类与 `parents/children`；
- `knowledge_lookup` 会把**类名**识别为检索范围（不是 AND 关键词）；
- 组合词为空时它会**自动降级 OR** 并回 `suggestions`（父类扩大 / 列实例 / 换库 / `mode=rules` 入口）——**照 args 直接调用，不要自己换词试错**；
- 若多个词是**关系链**（类A 类B 类C），用 `mode="route"` 拿类型路径，别用 AND。
