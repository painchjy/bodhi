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
| `ea_overview_design` | 企架概要设计 | 基于**业务模型**做 IT 服务层设计（报告页 + 服务页） | ea | `wiki_search`/`wiki_read_page`、`save_knowledge` |
| `service_detailed_design` | 服务详细设计 | 对**已入库服务**做接口/操作/主外键/CRUD 详细设计 | ea + ea-service | `ontology_types`、`save_knowledge`、`audit_scan`、`service_overview` |

> **领域建模已改为分批交互**（v0.2.0，2026-09-21）：`doc_outline` 按切片父子关系组织"合适的上下文"，
> `budget_tokens` 是**会话参数**（超时就调小，父块会自动按子块细分）；每轮 `save_knowledge(..., session=…)`
> 回执带**页面编号**（`created[].no`），进度用 `extract_state` 对齐；跨上下文关联先 `link_candidates` 登记、
> **用户确认后** `resolve_link_candidate(confirm)` 才写入。原异步一次性抽取（`extract_and_save`）已于 **2026-09-22 从服务端移除**（不是「兼容保留」）。

> 服务详细设计收尾：`service_overview(kb_id, apply=true)` 刷「IT 服务详细设计总览」评审页
> （服务一览 / 键 / 跨服务读依赖 / 操作明细 + 巡检结论）—— 评审看这一页，不要人在回答里拼表。

新增技能：建目录 + 写 `SKILL.md`（front-matter 必填 `id`/`name`/`when`），MCP 重启后
`skills()` 自动列出（无需改代码）。
