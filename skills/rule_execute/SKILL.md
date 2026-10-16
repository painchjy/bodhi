---
id: rule_execute
name: 规则执行（逐条评审）
description: 给一个**任务**（bpmn:Task）→ 用 rules_of_task 取到该任务**真正绑定的规则及其判定口径** → 逐条出结论（合规｜违规｜不适用｜待澄清），每条带**逐字证据**、**严重度**与**规则出处**。规则分两型：**抽取型**（实现方式含"知识提取"或有 knowledgeNeed ⇒ 先取事实再判）与**判定型**（直接按规则与事实判）。事实来源分层：文档（doc_outline/grep_chunks）→ 图（graph_query）→ **外部系统已管理的数据（查挡板/接口，不本体化）**。
when: 用户要"按某任务/策略评审这份方案/文档""逐条评审""按规则检查"；或作为活动编排（activity_drive）里"逐项评审"任务的执行体。
version: 0.1.0
tools: [rules_of_task, doc_outline, list_knowledge_chunks, grep_chunks, graph_query, wiki_read_page, save_knowledge]
---

# 规则执行（逐条评审）

**一句话**：**规则是正本（图边绑定 + 页面属性）**，技能只负责"取规则 → 取事实 → 判 → 出结论"，
**不允许自己发明规则**（历史事故：`rules_of_policy` 只回清单 ⇒ 智能体回去"看需求文档复原规则" ✗）。

## 0. 前置：取任务与规则（唯一入口）
```
rules_of_task(kb_id=<库>, task=<任务 slug/标题片段>)
→ { task:{slug,title,definition,dataobjects,roles}, rules:[{slug,title,implementation,knowledgeNeed,
     scope,enforcement,reference,needs_extract,strategies,required_fields,source}], counts, hash }
```
- 逐条按 `rules` 处理；**回执必须原样带上 `source` 与规则 `slug`**（可复核 ✓）；
- `hash` 记进报告页（`page_metadata.review.rules_hash`），下次重跑如 `hash` 变 ⇒ 规则被改过 ⇒ 需重评 ✓。

## 1. 逐条规则：两种模式（同一结论契约）
| 模式 | 触发 | 步骤 |
|---|---|---|
| **A 抽取型** | `needs_extract = true`（实现方式含"知识提取"或 `knowledgeNeed` 非空） | ① 按 `knowledgeNeed` 定位事实来源；② 抽事实（**逐字**，带定位）；③ 判 |
| **B 判定型** | `needs_extract = false` | 直接在 `scope` 范围内按规则文本判（可先做一次**知识库事实核查**：`grep_chunks`/`graph_query`） |

**事实来源分层**（按优先级，全部要留定位）：
1. **文档**：`doc_outline` 定章节 → `grep_chunks` 取**逐字片段**（表格类：整行/整格取值）；
2. **图**：`graph_query`（只读 Cypher）取实例/关系事实；
3. **外部系统已管理的数据**：**不本体化** ✗（口径）⇒ 查**挡板/接口**：
   - 例：规则「技术架构-开源软件适用规则」的清单 = **DataObject「开源软件清单」**（`bpmn/dataobject/开源软件清单` ✓）；
   - 接口未就绪时：**挡板**（`agent/stub/开源软件清单查询挡板`）返回清单；样例数据在其 `skos:note`（MySQL 8.0.32 等 ✓）；
   - 判定 = **抽取值集合 vs 清单集合的比对**（缺失 / 版本不符 ⇒ 违规 + 逐字证据）；
   - **不得**为了比对而在本体里建"软件产品类 + 实例集合" ✗。

## 2. 结论契约（每条规则输出，字段名固定）
```json
{"rule_slug":"…","rule_title":"…","scope":"…","enforcement":"严格执行|建议|授权覆盖",
 "conclusion":"合规|违规|不适用|待澄清",        // 逐字枚举，不得自造
 "severity":"高|中|低",                          // conclusion=违规 时必填，按 enforcement 与影响面
 "evidence":[{"quote":"逐字原文","locator":"章节/表格行/图查询/接口返回"}],
 "reason":"为什么得出该结论（≤200字）",
 "source":"graph:taskExecutesRule","rules_hash":"…"}
```
- **适用规则必须全部有结论**（含"不适用/待澄清"✓）；`不适用` 必须写清**为什么不在 scope 内** ✓；
- `待澄清` = 事实取不到且无法可靠推断（**不要猜** ✗）→ 进「待确认清单」。

## 3. 汇总（交给 task_compose）
- 结论集合 → 报告页（字段/枚举按任务页输出规范 ✓）；`违规` 项逐条进「问题清单」（`severity` 排序 ✓）；
- 报告 `page_metadata.review = {task, rules_hash, rule_count, counts:{合规,违规,不适用,待澄清}}` ✓。

## 4. 验收断言（回归用，必须全过）
1. `rules_of_task` 返回的规则**条数**与图边一致（逐项评审 = 6，**不含总结规则** ✓）；
2. 每条规则都有结论；`违规` 必有 `severity` + **逐字证据**；
3. **A 型样例**（规则 5，技术栈表格）：清单内的 (名称,版本) ⇒ 合规 ✓；**版本不在清单**（如 `PostgreSQL 15.6`）⇒ **违规** ✓；
4. 同输入重跑：`rules_hash` 不变时结论应稳定（幂等 ✓）；改规则属性 ⇒ `hash` 变 ⇒ 允许结论变化 ✓；
5. 报告里 `source`/`rule_slug` 可回指到图边与规则页 ✓（抽查 1 条）。
