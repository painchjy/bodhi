# 技能/智能体收敛清单（R6）

> 2026-10-17 起，技术方案评审域的做事方式统一为**规则驱动**：
> `rules_resolve` → `knowledge_plan` → `knowledge_extract` → `rule_judge` → `report_compose`
> （`fact_check` 在 `rule_judge` 之前把关证据）。
>
> 本文是**下架清单**（先标 deprecated、不删文件 ✓；确认后再物理收敛 ✓）。

## 一、保留（6 个工具 + 6 个技能）
| 层 | 名字 | 职责 |
|---|---|---|
| 工具 | `rules_resolve` | 按步骤解析规则（direct / policy 递归 / ask / task 兼容 / path 预留）✓ |
| 工具 | `ontology_paths` | 多跳路径（跨层噪音可过滤 ✓） |
| 工具 | `page_get` / `page_search` 类读 | 读页正文（图 `wiki_content` 优先 ✓） |
| 工具 | `write_knowledge`（写核） | 图 → PG，fail-closed ✓ |
| 工具 | `ke_audit`（W10 等巡检） | 图↔PG 漂移、摘要/别名一致性 ✓ |
| 工具 | `assertion` 类（会话/结论） | 溯源与留痕 ✓ |
| 技能 | `knowledge_plan` | 把 k 条规则的 knowledgeSpec **合并**成一次提取计划 ✓ |
| 技能 | `knowledge_extract` | 按计划取事实（一次并集 ✓，不逐规则重复读 ✗） |
| 技能 | `fact_check` | 逐字证据 + 可回访定位 + 三类区分（fact/compliance/opinion）✓ |
| 技能 | `rule_judge` | 规则契约 + 已抽事实 → passed/failed/**insufficient**（证据不足不得判 failed ✓） |
| 技能 | `report_compose` | 五节报告（④ 问题清单 7 列表格 ✓） |
| 技能 | `rule_execute` | 执行单条规则的判定脚本（A/B 分流 ✓） |

## 二、标 deprecated（待用户确认后归档到 `skills/_deprecated/`）
| 名字 | 现状 | 为什么收敛 |
|---|---|---|
| `document_review` | 旧"文档评审"技能 | 被 `rule_judge` + `report_compose` 取代 ✓（它按"看文档复原规则"✗，正是要消除的 ✗） |
| `tech_review_extraction` | 旧"技术方案提取" | 被 `knowledge_plan` + `knowledge_extract` 取代 ✓（旧版逐规则重复提取 ✗） |
| `structured_modeling` | 结构化建模 | 与 `domain_modeling` 重叠 ✓；本体改动走 TTL 编译 ✓ |
| `multimodal_extraction` | 图像/表格提取 | 保留为**工具能力**（`knowledge_extract` 调用它 ✓），不再作为独立技能入口 ✓ |
| `model_recommendation` | 模型推荐 | 与评审主链无关 ✓ |
| `kb_transfer` / `session_provenance` / `revision_trace` / `skill_development` | 平台类 | 保留工具，不作为评审技能入口 ✓ |

## 三、待办（本轮未做）
1. `systemCriticality` 的 TTL 注释补"**主系统定义、子系统继承；子系统可独立认定并覆盖**" ✓，
   然后 `upload_ttl`（顺序 bmm → bpmn → agent ✓）；
2. `rules_of_task` 在 MCP 描述里标 `[deprecated → rules_resolve]` ✓（行为保留 ✓）；
3. 上述 deprecated 技能经你确认后移入 `skills/_deprecated/` ✓（git 历史保留 ✓）。
