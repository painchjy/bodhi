---
id: service_detailed_design
name: 服务详细设计
when: 用户要求对**已入库的 IT 服务**做详细设计（说法如：详细设计、接口设计、服务操作、主外键、CRUD、服务耦合分析、服务合理性）。
models: [ea-service, ea]
default_model: ea-service
scope:
  classes: [easvc:ServiceOperation, easvc:BusinessAttribute, easvc:FunctionalDependency, easvc:ServiceContract, ea:BusinessEntity, ea:Service]
  relations: [easvc:serviceHasOperation, easvc:operationOperatesOnAttribute, easvc:operationAccepts, easvc:operationReturns, easvc:referencesAttribute, easvc:hasBusinessAttribute, easvc:attributeOf]
sources: [graph, document]
stages: [detail]
tools: [ontology_types, save_knowledge, skills, wiki_search, wiki_read_page]
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
5. **落库**：`save_knowledge(stage="graph", mode="dry_run")` 先看清单，用户确认后 `mode="apply"`；
   服务页会自动生成 `## CRUD 矩阵`（系统渲染，别自己写表）。
   **`report.upstream` 必须带上「服务页 + 报告页」** —— 设计页的 `source_refs` 从这里继承，
   不带的话新页会被巡检判 C1（"实例页无来源"，high）。
6. **汇报**：服务 → 操作数 → 属性数与键角色 → 读写分布；然后**念一遍巡检结论**
   （`audit_scan(scope="coupling")`：E1 写耦合 / E2 读耦合 / E3 完整性 / E4 键一致性）。

> 完整样例：同目录 `EXAMPLE.json`（真实数据「身份三要素采集服务」：3 操作 / 5 属性 / 24 条边），
> 照着改成目标服务即可。

## 纪律
- **主外键不建类**：`keyRole` 是数据属性（PK/FK/UNIQUE/NONE），外键用 `referencesAttribute` 指过去。
- **CRUD 只在边上给**（`edges[].properties.easvc:crudKind`），不要在正文里手写表格 ——
  `## CRUD 矩阵` 由服务端确定性渲染；`## 关系限定（边属性）` 也是服务端写的。
- 同一操作对同一属性可声明多次不同 `crudKind`（如 `C` 与 `U` 分开），最终在矩阵里合并成 √。
- **一次只做一个服务**（做完落库、跑巡检、再下一个），避免一批 JSON 出错难定位。
- 写操作 `isIdempotent=false` 时，要么给幂等键，要么在契约里说明重试策略（否则巡检 E3 会提醒）。
