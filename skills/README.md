# 技能目录（repo 单一来源）

每个技能 = 一个目录里的 `SKILL.md`：**YAML front-matter**（机器读：id / when / model / inputs / stages / tools）
+ **正文**（人/模型读：步骤、输出契约、纪律）。

- 智能体**不把技能全文塞进提示词**：先看目录，用到哪个再用 MCP 工具 `skills(skill="<id>")` 取全文
  （省 token，且技能改了不用重新注册智能体）。
- 未来若开 WeKnora 沙箱技能（`tenant_skills` / bundle），**同一份 SKILL.md 可直接打成 bundle 注册**，
  一份源两种呈现。

| id | 名称 | 何时用 | 模型 | 主要工具 |
|---|---|---|---|---|
| `domain_modeling` | 领域知识建模 | 把**某篇文档**按本体抽成 wiki 知识（可收窄范围） | ea / bmm | `ontology_types`、`extract_and_save`、`extract_status` |
| `ea_overview_design` | 企架概要设计 | 基于**业务模型**做 IT 服务层设计（报告页 + 服务页） | ea | `wiki_search`/`wiki_read_page`、`save_knowledge` |
| `service_detailed_design` | 服务详细设计 | 对**已入库服务**做接口/操作/主外键/CRUD 详细设计 | ea + ea-service | `ontology_types`、`save_knowledge`、`audit_scan`、`service_overview` |

> 服务详细设计收尾：`service_overview(kb_id, apply=true)` 刷「IT 服务详细设计总览」评审页
> （服务一览 / 键 / 跨服务读依赖 / 操作明细 + 巡检结论）—— 评审看这一页，不要人在回答里拼表。

新增技能：建目录 + 写 `SKILL.md`（front-matter 必填 `id`/`name`/`when`），MCP 重启后
`skills()` 自动列出（无需改代码）。
