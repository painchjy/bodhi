# 技能目录（repo 单一来源）

每个技能 = 一个目录里的 `SKILL.md`：**YAML front-matter**（机器读：id / when / model / inputs / stages / tools）
+ **正文**（人/模型读：步骤、输出契约、纪律）。

- 智能体**不把技能全文塞进提示词**：先看目录，用到哪个再用 MCP 工具 `skills(skill="<id>")` 取全文
  （省 token，且技能改了不用重新注册智能体）。
- 未来若开 WeKnora 沙箱技能（`tenant_skills` / bundle），**同一份 SKILL.md 可直接打成 bundle 注册**，
  一份源两种呈现。

| id | 名称 | 何时用 | 模型 | 主要工具 |
|---|---|---|---|---|
| `domain_modeling` | 领域知识建模 | 把**某篇文档**按本体抽成 wiki 知识（**分批交互**：一轮一批、可续跑） | ea / bmm | `doc_outline`、`extract_state`、`save_knowledge`、`link_candidates`/`resolve_link_candidate` |
| `structured_modeling` | 结构化数据批量建模 | 读用户对 **Excel/CSV 表结构**的描述 → 把列映射到本体的**类/数据属性/关系/枚举**，再一次一个类或一条关系地批量建页（**技能理解 → 工具执行**） | bmm / ea | `import_probe`、`import_plan`、`import_apply`、`import_state`、`ontology_types`、`audit_scan` |
| `document_review` | 文档评审 | 选**文档** + 选**业务策略** → 按策略下的**业务规则逐条**评（LLM软规则交给大模型；图检索生成只读 Cypher；参考规范用 `reference_lookup` 取），**每条只在它的适用范围（一般=章节）内判断** | bmm | `rules_of_policy`、`graph_query`、`reference_lookup`、`review_apply`、`doc_outline`、`grep_chunks`、`wiki_search`、`ontology_types`、`audit_scan` |

| **`skill_development`** | 设计开发 | 由**设计开发智能体**使用：把"领域建模产物 + 知识需求"变成**可运行的业务智能体与业务技能**，并交付**配置与安装指引**（依赖/安装/库与模型/工具状态表/验证步骤）、**挡板计划**（契约先行·打标·不污染·可切换）与**验证清单**；**先出设计单再落文件**；**不改本体** | bmm | `skills`、`ontology_types`、`doc_outline`、`grep_chunks`、`wiki_search`、`rules_of_policy`、`graph_query`、`review_apply`、`save_knowledge`、`audit_scan` |
| **`session_provenance`** | 会话知识溯源 | **知识来源＝智能体会话**：把会话记成可溯源知识页（**会话起始页**只放会话编号/名称/智能体/租户/初始问题/时间；**分页**按"上下文过长且一组知识已确认更新完"切段，关联同一会话编号）；每条知识在「原文依据」里写 **分页 slug + 定位**；跨库每库一份起始页副本、**以初始库为权威**，**换库必须新建分页**；知识全过期/废弃才 `archived`；含**改进建议**分类（`adviceCategory/adviceStatus`）与总览页 `advice/board`。**不改本体** | bmm | `save_knowledge`、`wiki_search`、`wiki_read_page`、`grep_chunks`、`list_knowledge_chunks`、`link_candidates`、`resolve_link_candidate`、`audit_scan` |
| `model_recommendation` | 建模方案推荐 | 用户问"这份材料该怎么建模""这些概念要不要建本体""该建到哪个模型/哪一层" → 判**需求种类** → 选**建模路线**（流程·EA ／ BMM ／ DDD+智能体 ／ 方案资产 ／ 结构化）→ 抽概念候选 → 判**消费关系**（被哪个技能/智能体消费）→ 出**建议单**（**推荐≠决定**） | bmm | `skills`、`ontology_types`、`doc_outline`、`wiki_search`、`link_candidates`、`save_knowledge`、`audit_scan` |

> **技能只限「模型」，不限「本体类型」**（用户口径 2026-09-30）——
> front-matter 里只写 `models:`（可加 `default_model:`），**不写 `scope.classes/relations`**：
> 类型清单会随本体演进漂移，写死就等于额外维护一份配置。
>
> **每个技能的第 0 步都是"和用户明确模型范围"**：
> - **一般单选模型**（一次只用一个模型做事）；
> - **评审 / 设计可能跨模型** → 仍**单选底层模型**，再**按依赖**把需要的关联本体类型引进来
>   （`ontology_types("<模型>")` 回执里带 `requires`/`affects` 依赖线索）；
> - 具体类/关系/数据属性一律 `ontology_types("<模型>")` **现查**，不存在就换，别猜。
>
> 设计类技能（`ea_overview_design` 企架概要设计、`service_detailed_design` 服务详细设计）
> **已移除，待重构**（2026-09-30）；`service_overview` 等工具仍在。

> **领域建模已改为分批交互**（v0.2.0，2026-09-21）：`doc_outline` 按切片父子关系组织"合适的上下文"，
> `budget_tokens` 是**会话参数**（超时就调小，父块会自动按子块细分）；每轮 `save_knowledge(..., session=…)`
> 回执带**页面编号**（`created[].no`），进度用 `extract_state` 对齐；跨上下文关联先 `link_candidates` 登记、
> **用户确认后** `resolve_link_candidate(confirm)` 才写入。原异步一次性抽取（`extract_and_save`）已于 **2026-09-22 从服务端移除**（不是「兼容保留」）。

> 服务详细设计收尾：`service_overview(kb_id, apply=true)` 刷「IT 服务详细设计总览」评审页
> （服务一览 / 键 / 跨服务读依赖 / 操作明细 + 巡检结论）—— 评审看这一页，不要人在回答里拼表。

新增技能：建目录 + 写 `SKILL.md`（front-matter 必填 `id`/`name`/`when`），MCP 重启后
`skills()` 自动列出（无需改代码）。
