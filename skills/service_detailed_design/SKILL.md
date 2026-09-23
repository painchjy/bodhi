---
id: service_detailed_design
name: 服务详细设计
description: 对已入库的 IT 服务做详细设计（操作/接口、属性与主外键、边上的 CRUD、跨服务读依赖声明），并刷出评审总览页。
version: 0.1.0
when: 用户要求对**已入库的 IT 服务**做详细设计（说法如：详细设计、接口设计、服务操作、主外键、CRUD、服务耦合分析、服务合理性）。
models: [ea-service, ea]
default_model: ea-service
scope:
  classes: [easvc:ServiceOperation, easvc:BusinessAttribute, easvc:FunctionalDependency, easvc:ServiceContract, ea:BusinessEntity, ea:Service]
  relations: [easvc:serviceHasOperation, easvc:operationOperatesOnAttribute, easvc:operationAccepts, easvc:operationReturns, easvc:referencesAttribute, easvc:hasBusinessAttribute, easvc:attributeOf]
sources: [graph, document]
stages: [detail]
tools: [ontology_types, save_knowledge, service_overview, skills, wiki_search, wiki_read_page]
inputs:
  service: 必填。目标服务页 slug 或标题（先用 wiki_search 按类型 ea:MCPService 找）
  entity_attributes: 可选。已知的业务实体属性（没有时从实体页/文档里取）
guard: 键角色不建 Key 类（用 easvc:keyRole）；CRUD 只在边上给（properties.crudKind）
---

# 服务详细设计

把概要设计里的一个 IT 服务，细化到**接口/操作级**：操作、输入输出属性、主外键、
以及对业务属性的 **C/R/U/D** —— 这些正是**服务耦合性与设计合理性分析**的输入。

## 本体件（用之前先看一眼）
`ontology_types("ea-service")` 会给出：类 `easvc:ServiceOperation` / `easvc:BusinessAttribute`；
关系 `serviceHasOperation`、`operationOperatesOnAttribute`（**边限定 `crudKind`**）、
`operationAccepts/Returns`、`referencesAttribute`、`serviceOwnsEntity`；
数据属性 `crudKind` / `operationMethod` / `isIdempotent` / `transactionBoundary` / `keyRole`。
> 同一个模型里 `class_attributes` 会告诉你"这个类允许哪些数据属性" —— `nodes[].attributes` 的键就按它填。

## 步骤
1. **定位服务**：`wiki_search` 找目标服务页（类型 `ea:MCPService` / `ea:APIService` / `ea:SkillService`），
   `wiki_read_page` 看它的用途、输入输出、ASSERTION 与所属系统。
2. **定操作**：一个服务拆成若干**可独立调用**的操作（如 createCustomer / getCustomer / updateCustomer）。
   每个操作给：`definition`、`attributes={easvc:operationMethod, easvc:isIdempotent, easvc:transactionBoundary}`。
   `operationMethod` 建议写实现形态（`HTTP PUT /customers/{id}` / `MCP tool: xxx` / 函数名）。
3. **定属性与键**：为涉及的**业务实体**列属性页（`easvc:BusinessAttribute`），
   `attributes={easvc:keyRole: "PK|FK|UNIQUE|NONE"}`；外键再加关系 `easvc:referencesAttribute` → 被引用属性
   （目标应为 PK/UNIQUE）。
4. **定 CRUD**：用 `easvc:operationOperatesOnAttribute` 边把「操作 → 属性」连起来，
   **在读写在边上给**：`properties={"easvc:crudKind": "C,U"}`（可多值，逗号分隔）。
   读操作 `R`，写操作 `C/U/D`。
5. **落库**：`save_knowledge(stage="graph", mode="dry_run",
- 落库时带 `context="service_detailed_design"`（文案按服务详细设计渲染）。
   kb_ids=[…会话绑定的库 id…])` 先看清单，用户确认后 `mode="apply"`；
   服务页会自动生成 `## CRUD 矩阵`（系统渲染，别自己写表）。
   **`report.upstream` 必须带上「服务页 + 报告页」** —— 设计页的 `source_refs` 从这里继承，
   不带的话新页会被巡检判 C1（"实例页无来源"，high）。
6. **刷总览（评审页）**：`service_overview(kb_id)` 先预览（服务数/操作数/属性数/依赖数 + 前若干行），
   再 `service_overview(kb_id, apply=true)` **异步**刷新「IT 服务详细设计总览」页
   （**服务一览 / 业务属性与键 / 跨服务读依赖 / 逐操作明细 + 巡检结论**；用 `job_status(job_id)` 查回执）。
7. **汇报**：服务 → 操作数 → 属性数与键角色 → 读写分布；然后**念一遍巡检结论**
   （`audit_scan(scope="coupling")`：E1 写耦合 / E2 读耦合 / E3 完整性 / E4 键一致性）。

> 完整样例：同目录 `EXAMPLE.json`（真实数据「身份三要素采集服务」：3 操作 / 5 属性 / 24 条边），
> 照着改成目标服务即可。

## 纪律

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

- **主外键不建类**：`keyRole` 是数据属性（PK/FK/UNIQUE/NONE），外键用 `referencesAttribute` 指过去。
- **CRUD 只在边上给**（`edges[].properties.easvc:crudKind`），不要在正文里手写表格 ——
  `## CRUD 矩阵` 由服务端确定性渲染；`## 关系限定（边属性）` 也是服务端写的。
- **同一操作对同一属性读写都用时，写一条边**：`properties={"easvc:crudKind": "C,R"}`。
  给两条边（一条 C 一条 R）会在页面产生两行**完全相同**的 `## 本体关系` 行 → 巡检 **A5（重复关系行）**。
- **读别人写的属性 = 声明「经接口」**：本操作读的属性若由别的服务写，必须给一条
  `{"source": <本操作>, "type": "easvc:operationDependsOnOperation", "target": <对方提供的查询操作>}`。
  没有这条声明，巡检 E2 会报 **「疑似直读」**（medium）；声明后降为 **「经接口（合理耦合）」**（low）。
- **改设计要能减边**：给要撤掉的关系加 `"retract": true`（删掉该关系行 + 同键的关系限定行，带版本快照）。
  只加不减会让旧边留在页面上，E1/E2/E4 的结论跟着失真。
- **要改哪个页的边，那个页必须作为 `nodes` 出现在载荷里**（关系行只对载荷里出现过的节点生效）。
- **评审看总览页**：别在回答里自己拼大表 —— `service_overview(apply=true)` 生成的那一页就是评审物
  （服务一览 / 业务属性与键 / 跨服务读依赖 / 逐操作明细 + 巡检结论），服务改过就重刷一次。
- **一次只做一个服务**（做完落库、跑巡检、再下一个），避免一批 JSON 出错难定位。
- 写操作 `isIdempotent=false` 时，要么给幂等键，要么在契约里说明重试策略（否则巡检 E3 会提醒）。

## 耦合结论怎么读（`audit_scan(scope="coupling")`）
| 检查 | 含义 | 设计动作 |
|---|---|---|
| **E1 写耦合** | 同一业务属性被**≥2 个服务**以 C/U/D 操作 | 评审服务边界：写方应收敛到一个服务（把该属性挪到某个服务的实体上并 `retract` 旧边），或明确主从 |
| **E2 读耦合·经接口** | 读方服务**已声明** `operationDependsOnOperation` | 合理耦合；确认依赖的是稳定对外接口 |
| **E2 读耦合·疑似直读** | 读了别写的属性但**没声明**操作依赖 | 补 `operationDependsOnOperation` 走接口，或把读收敛到写方 |
| **E3 完整性** | 服务无操作 / 操作无被操作属性 / 缺 `operationMethod` / 写操作非幂等无说明 | 补齐设计 |
| **E4 键一致性** | FK 无 `referencesAttribute` / 引用目标非 PK·UNIQUE / 目标页不存在 | 修正键设计 |

> 真实样例（本目录 `examples/coupling_example.json`，14 节点 / 37 边）产出：
> **E1**「客户姓名」被 注册信息录入服务 与 身份三要素采集服务 同时写；
> **E2** 实名联网核查服务 读采集服务写的 客户姓名/证件种类/证件号码；
> **E3** 核验三要素一致性 `isIdempotent=false` 且无幂等/重试说明。
