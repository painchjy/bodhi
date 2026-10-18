---
id: report_compose
name: 报告组装（按规范渲染，表格优先）
description: 把"问题清单（含逐条判定）+ 规则清单"组装成**技术方案评审报告**：章节按报告实例页的 `rdfs:comment`（章节规范）✓，问题清单按 DataObject「问题清单」的 `skos:definition`（列规格）渲染为**Markdown 表格** ✓；禁止自拼正文 ✗。
when: 任务步骤点名"生成评审报告"（不经判定规则触发 ✓）；或用户要求"出报告/汇总/写评审结论"时。
models: [bmm, pbmn]
default_model: bmm
stages: [compose]
tools: [knowledge_lookup, wiki_read_page, save_knowledge, knowledge_lookup]
version: 0.1.0
guard: 章节/列规格一律从库内页读（不改代码 ✗）；问题清单必须是表格；枚举逐字；每条带规则出处与逐字证据；写入走 save_knowledge(stage="report")。
---

# 报告组装（规范驱动）

## 0. 先读两份"规范"（**唯一来源** ✓）
1. **报告章节规范**：`wiki_read_page(评审报告实例页)` → 取其 **`rdfs:comment`**
   （现为：**① 范围与依据 ② 规则清单 ③ 问题清单（表格）④ 结论与建议** ✓；**没有「逐条结论」** ✗ —— ③ 已合并进问题清单 ✓）；
2. **问题清单列规格**：`wiki_read_page(DataObject「问题清单」)` → 取其 **`skos:definition`**
   （现为：序号｜规则出处(rule_slug 逐字)｜问题描述｜严重度(高\|中\|低 逐字)｜原文证据(逐字)｜定位｜处置建议）；
3. 规则清单与口径：`knowledge_lookup(kb_id, mode="rules", spec="task:…")` ✓（`rules_hash` 一起带进元数据 ✓）。

> ⚠️ 规范**只读页**、不写死在技能里 ✗ —— 改规范 = 改那两页，不改代码 ✓。

## 1. 组装（严格按规范顺序/列序 ✓）
```
# 技术方案评审报告：<文档名>（评审流程编号 <编号>）
> 生成：<时间>｜任务：<task>｜策略：<policies>｜rules_hash：<hash>

## 1 评审范围与依据        ← 文档、章节范围、策略/规则清单与版本
## 2 适用规则清单          ← 表格：规则 | 级别 | 适用范围 | 实现方式（来自 knowledge_lookup ✓）
## 3 问题清单（9 列，列名/顺序以 bpmn/dataobject/问题清单 定义为准 ✓）
## 3 问题清单              ← **表格**，列 = 问题清单 DataObject 的 definition（逐字/逐序 ✓）
## 4 结论与建议            ← 结论概览（违规数/待澄清数）+ 建议动作
```
**硬规则**：
1. **每个"清单/结论"必须是 Markdown 表格** ✓（`|---|` 表头 = 规格列 ✓）；**禁止**"分段罗列"✗；
2. 表头文字 = 规范里的列名**逐字** ✓（含顺序 ✓）；
3. 枚举值只取规范里的受控取值 ✓（`合规|违规|不适用|待澄清`、`高|中|低` ✓）；
4. 每条问题必带 **规则出处 + 逐字证据 + 定位** ✓（无证据不得进"问题清单" ✗，只能进"待澄清" ✓）；
5. **③ 表必须覆盖全部受判规则** ✓：行数 **≥** `knowledge_lookup(mode="rules")` 规则数 ✓（每规则至少一行；同一规则在不同文档位置/子系统结论不同 ⇒ **拆多行** ✓）；符合项也**至少出现一次** ✓；**只列不符合 = 失败** ✗；表后写汇总行 ✓。

## 2. 写库
```
save_knowledge(kb_id=work_kb, stage="report", context="review",
               title="技术方案评审报告：<文档名>", content_md=<上面的 Markdown>,
               page_metadata.review={task, policies, rules_hash, counts:{合规,违规,不适用,待澄清}})
```
- 正文 = 上面整篇 Markdown ✓（`document` 模式：**正文是事实源** ✓）；
- 报告页 `page_type` = `bmm:TechnicalReviewReport` ✓（**不用 DataObject** ✗ —— 报告是系统内部产物 ✓）；
- 幂等：同 `(kb, slug)` 覆盖、版本+1 ✓。

## 3. 验收断言（回归必过 ✓）
1. 报告正文含规范里**每一节**标题 ✓（缺一即失败 ✓）；
   1b. **③ 数据行数 ≥ 规则条数** ✓（少一条即失败 ✗ —— 2026-10-18 实测：5 条规则只输出 2 条不符合，3 条符合丢失 ✗）；多行须是「同规则不同位置」✓，不得重复凑数 ✗；
2. **④ 问题清单**是表格 ✓：`|` 行数 ≥ 问题数 + 2（表头/分隔）✓；列名逐字等于 DataObject definition ✓；
3. ③/④ 里所有枚举值 ∈ 受控集合 ✓；
4. 每条问题可回指：`rule_slug` 存在于 `knowledge_lookup` 的规则里 ✓；证据是逐字片段 ✓；
5. `page_metadata.review.rules_hash` 存在 ✓（规则改动后可感知 ✓）；
6. 同输入重跑内容稳定（除时间戳 ✓）。

## 4. 纪律
- **不改规范** ✗（只读报告页/清单页 ✓）；需要改 → 改那两页 ✓；
- **不编造证据** ✗：取不到逐字证据的条目进"待澄清" ✓；
- 报告**只作证据** ✓（机器判定读的是知识/规则事实 ✓）。

## 工具口径（2026-10-17 统一，`skills/_deprecated.md` §四）

- **取规则**：`knowledge_lookup(kb_id, mode="rules", spec=…)`
  - `spec` 五种：`direct:<slug[,slug]>` / `policy:<策略>` / `ask` / `task:<任务>` / `path:`（预留）
  - 旧调用 `rules_of_task(kb, task)` ≡ `spec="task:<任务>"` ✓（行为保留，**新写一律用本入口**）
- **找知识**：`knowledge_lookup(kb_id, mode="keyword", query=…)` —— 图优先 → PG 收窄融合 ✓
  每条命中带 `source`（`graph` / `graph+pg` / `pg`）；`pg` 占比高 ⇒ 图缺数据 ✗
- **多跳**：`knowledge_lookup(kb_id, mode="path", start=<slug>, depth=2)` ✓
- **写库**：`write_knowledge`（**图 → PG** ✓）；`document` 类（`bmm:Report` 子类）**必须有正文** ✓
