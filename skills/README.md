# 技能目录（repo 单一来源 / 沙箱包源）

每个技能 = 一个目录里的 `SKILL.md`：**YAML front-matter**（机器读：id / when / models / stages / tools）
+ **正文**（人/模型读：步骤、输出契约、纪律）。

> **2026-11-15 收敛口径**：active 技能只保留三个；`domain_modeling`、`skill_development`、
> `knowledge_plan`、`report_compose`、`session_provenance` 已移入 `_deprecated/`，不再进入
> active catalog。后续运行时以 WeKnora **沙箱安装技能**为准，MCP `skills()` 仅作为迁移期兼容入口。

| id | 名称 | 何时用 | 主要工具 |
|---|---|---|---|
| `rule_judge` | 规则判定 | 按规则契约和已提取事实给出符合/不符合/无法判定/不适用 | `knowledge_lookup`、`evidence_score`、`ext_table`、`save_knowledge` |
| `knowledge_extract` | 知识提取（按规则的知识需要取事实） | 由 `bmm:knowledgeNeed` 驱动，按本体类提取逐字事实并按 `bmm:sourceLocator` 落库 | `ontology_types`、`knowledge_lookup`、`doc_outline`、`image_read`、`save_knowledge` |
| `kb_transfer` | 知识库迁移（导出/导入/比对） | 导出、导入、PG+Neo4j 六项一致性比对；作为运维技能保留 | `kb_export`、`kb_import`、`audit_scan` |

> `kb_transfer` 属于跨环境运维技能，保留；但它不是业务作业高频技能，实际高频使用的是
> `kb_export` / `kb_import` 工具。

> **技能只限「模型」，不限「本体类型」**：front-matter 只写 `models:`（可加 `default_model:`），
> 类/关系/数据属性一律用 `ontology_types("<模型>")` 现查，不写死。

## 运行机制

- 当前迁移期仍可由 MCP `skills()` 提供 active 技能指令；
- 目标机制：`tools/skills/bundle.py` 将本目录打包为 zip，`register_native_skills.py` 上传到
  WeKnora sandbox config，通过 `/api/v1/skills` 安装和管理；
- 不再向智能体下发 MCP 技能目录工具；领域任务步骤负责“做什么”，沙箱技能只负责跨领域的
  通用算法与输出契约。

## 新增/退役

- 新增：建目录 + 写 `SKILL.md`，front-matter 必填 `id`/`name`/`description`/`version`；
- 退役：移入 `_deprecated/`，不得继续出现在智能体工具面或沙箱 active bundle 中。

