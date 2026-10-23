---
id: report_compose
name: 报告组装（模板驱动，表格优先）
description: 把「判定结论集合 + 规则集合」组装成**报告类文档**：章节顺序与必填项**一律取自模板页正文的「章节结构」表**（模板 = `schema:DocumentTemplate` 实例，按 `schema:applicableReportType` 选中 ✓）；表格列规格取自相应 `bpmn:DataObject` 页的 `skos:definition`（逐字/逐序 ✓）；禁止自拼章节或列 ✗。
when: 任务步骤点名"出报告"；或用户要求"生成/汇总/写结论"时。
models: [bmm, pbmn]
default_model: bmm
stages: [compose]
tools: [knowledge_lookup, save_knowledge, audit_scan]
version: 0.2.0
guard: 章节/列规格一律从库内页读（不改代码 ✗）；清单类内容必须是表格；枚举逐字；每条结论带规则出处与逐字证据；写入走 save_knowledge(stage="report")，回执里校 `missing_required_sections`。
---

# 报告组装（模板驱动）

## 0. 先取三样"规范"（**唯一来源** ✓）

| 规范 | 取法 | 备注 |
|---|---|---|
| **章节结构（顺序 + 必填 + 要求）** | 模板页 `schema:DocumentTemplate` 的**正文**「章节结构」表 | 选模板：按 `schema:applicableReportType` = 报告类名（`knowledge_lookup(mode="instances", cls="schema:DocumentTemplate")` 看候选；多份 ⇒ 看 `schema:templateSelector`，仍多 ⇒ **问用户**，不猜 ✗） |
| **表格列规格** | 对应 `bpmn:DataObject` 页的 **`skos:definition` 原文**（全量 ✓） | 列名/顺序**逐字逐序**照抄；不要用 `summary`（截断 ✗） |
| **受控枚举** | 同上 `skos:definition` 里声明的取值集合 | 逐字使用，不自造同义词 ✗ |

> ⚠️ 章节**不写死在技能里** ✗ —— 改章节 = 改模板页（`save_knowledge(stage="report")`，同 slug 覆盖）；
> 改列 = 改该 DataObject 页。技能只描述**流程**。

## 1. 组装（严格按模板顺序 ✓）
1. 按模板「章节结构」表逐节产出；**「必填」= 是** 的小节必须有内容（确无内容 ⇒ 按模板「要求」列写「无」）；
2. 清单类小节（模板「要求」列写明"逐条/表格"的）⇒ **Markdown 表格**，表头 = 该 DataObject 的 `definition` 列名；
3. 每条判定/条目必带：**规则出处 + 逐字证据 + 定位**（无证据 ⇒ 进"待澄清"说明段，不进表格 ✗）；
4. 汇总口径（条数/比例）必须能由表内行**重算**（不许自报数字 ✗）。

## 2. 写库
```
save_knowledge(kb_id=<工作库>, stage="report", mode="apply", context=<上下文>,
               session_no=<会话>,
               report={title=<标题>, page_type=<报告类名>,
                       content_md=<上面整篇 Markdown>,
                       attributes={<该报告类的数据属性>},
                       template=<可选：指定模板 slug>})
```
- `page_type` **必须是本体里的报告类**（`render_mode=document`，即 `schema:CreativeWork` 谱系）；
- 正文 = 整篇 Markdown ✓（document 模式：**正文是事实源**）；
- 幂等：同 `(kb, slug)` 覆盖、版本 +1 ✓。

## 3. 回执即校验（**不要只看 applied** ✓）
| 回执字段 | 含义 | 不合格怎么做 |
|---|---|---|
| `template` | 用了哪份模板（slug/version/章节数/必填数；`ambiguous=true` = 多候选） | 多候选 ⇒ 让用户定，或给候选补 `schema:templateSelector` |
| `missing_required_sections` | 缺的**必填**小节 | 补齐后**同 slug 重写**（不要新建页 ✗） |
| `template_fallback_reason` | 没用上模板的原因 | 告知用户"本次按内置结构生成"，并给出补模板的动作 |
| `report_warnings` | 其它正文告警 | 逐条核对 |

## 4. 验收（回归必过 ✓）
1. 正文含模板里**每个必填小节**（`missing_required_sections` 为空 ✓）；
2. 表格类小节是 Markdown 表格 ✓，表头 = DataObject `definition` 逐字逐序 ✓；
3. 枚举值 ∈ 该页声明的受控取值 ✓；
4. 每条结论可回指（规则页存在 + 证据为逐字片段）✓；
5. 巡检一致：`audit_scan(kb_id=<报告所在库>, scope="template", template_kb_id=<模板所在库>)` **无 T1 命中** ✓；
6. 同输入重跑内容稳定（除时间戳）✓。

## 5. 纪律
- **不改规范** ✗（模板页 / DataObject 页只读；要改 → 改那页并把影响面告诉用户 ✓）；
- **不编造证据** ✗：取不到逐字证据的条目进"待澄清" ✓；
- 报告**只作证据** ✓（机器判定读的是知识/规则事实 ✓）。

## 工具口径（统一入口）
- 找知识：`knowledge_lookup(kb_id, mode="keyword"|"instances"|"rules"|"path", …)`
- 写库：`save_knowledge(stage="report")`（文档类必须有正文）
- 体检：`audit_scan(scope="template", template_kb_id=…)`（模板章节完整性）
