---
id: report_compose
name: 报告组装（模板驱动 · 只做「数据 → 按模板渲染 → 落库校验」）
description: 报告类文档的**组装与落库**：① 数据由任务步骤取齐（本技能不复述取数动作 ✗）② 版式一律来自模板页正文「章节结构」表（章节顺序/必填/每节格式/表格列与列序）③ `save_knowledge(stage="report")` 落库并校回执。禁止自拼章节或列 ✗。
when: 任务步骤点名"出报告"；或用户要求"生成/汇总/写结论"时。
models: [bmm, pbmn]
default_model: bmm
stages: [compose]
tools: [knowledge_lookup, save_knowledge, audit_scan]
version: 0.3.0
guard: 取数只按任务步骤（本技能不重复 ✗）；版式只按模板（本技能不重复 ✗）；清单类内容必须是表格、枚举逐字、每条结论带规则出处与逐字证据；写入走 save_knowledge(stage="report")，回执里校 `missing_required_sections`。
---

# 报告组装（模板驱动）

## 0. 职责边界（先读这张表；冲突时按它判 ✓）

| 谁 | 管什么 | 不管什么 |
|---|---|---|
| **任务页 `skos:note`** | **取数**：用哪个工具、什么参数、什么顺序、何时问用户 | 报告的版式 ✗ |
| **模板页 `schema:DocumentTemplate`** | **版式**：章节与顺序、必填、每节内容格式、表格列与列序、表后补充块写法 | 怎么取数、怎么落库 ✗ |
| **本技能** | **组装四步**：取数 → 定模板 → 渲染 → 落库校验 | 不复述取数动作 ✗、不复述版式细节 ✗ |

**固定顺序**：① 按任务步骤把数据取齐（缺数据先问，别先动笔）→ ② 按模板逐节渲染 → ③ 落库 → ④ 校回执。
两处都没写的 ⇒ **问用户**（不猜 ✗）。本技能与任务/模板冲突 ⇒ **以任务/模板为准**，并在回执里点一句冲突点。

> 自检一句话：**"数从任务步骤来，样子从模板来，本技能只负责把两者对上并落库。"**

## 1. 定模板（选哪一份）

1. 按 `schema:applicableReportType` = 报告类名选（`knowledge_lookup(mode="instances", cls="schema:DocumentTemplate")`）；
2. 多候选 ⇒ 看 `schema:templateSelector`；仍多 ⇒ **问用户**（不静默选 ✗）；
3. 任务步骤点名了模板 ⇒ 用它（步骤优先 ✓）。

> 改章节 / 改列 = 改**模板页**（`save_knowledge(stage="report")`，同 slug 覆盖）；**不要在报告里自拼** ✗。
> `DataObject` 页只写接口，不写列清单 ✗（列集归模板；字段名与枚举归契约 `x-ext`）。

## 2. 渲染（严格按模板）

- 按模板「章节结构」表**逐节、按序**产出；「必填=是」的小节必须有内容
  （确无内容 ⇒ 按模板「内容格式要求」列写「无」）；
- 清单类小节 ⇒ **Markdown 表格**；表头 = 模板该节「数据对象字段」格映射到的**契约列名**（`labels` 逐字，不自造同义词 ✗）；
- 该 DataObject 声明了接口 ⇒ 表体**直接嵌工具 `view=table` 回执的 `markdown`**（不重排 / 不改列 / 不编行 ✗）；
  写完核对「表内行数 = 回执 `counts.items`」✓；工具不可用 ⇒ 该节写「台账不可用（待澄清）」+ 原因，**不编行** ✗；
- 调工具时把模板那格**原样传 `columns=`**（模板没写 ⇒ 用工具全量契约）。**模板选字段 ≠ 改契约** ✓
  （同一对象在不同报告里可各取不同子集）；
- 单元格格式（文档位置怎么写、规则指纹怎么填、四态、三态与阈值、表后三块补充块、汇总句式）
  **一律按模板** ✓ —— 本技能不重复，也不要自己去 DataObject 页抄口径 ✗；
- 小节末尾的**面板引用块**（```` ```bodhi-ext ```` 块）交给工具补：
  `save_knowledge(stage="report")` 发现缺块会自动补（回执 `ext_ref_block` 说明补了什么）；
  只有工具报 `object_ambiguous` 才需手工补 —— 此时 `review_no` 必须是本次报告表里的**真实编号**
  （照抄示例 = 错 ✗），拿不到就不写块。

## 3. 写库

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

## 4. 回执即校验（**不要只看 applied** ✓）

| 回执字段 | 含义 | 不合格怎么做 |
|---|---|---|
| `template` | 用了哪份模板（slug/version/章节数/必填数；`ambiguous=true` = 多候选） | 多候选 ⇒ 让用户定，或给候选补 `schema:templateSelector` |
| `missing_required_sections` | 缺的**必填**小节 | 补齐后**同 slug 重写**（不要新建页 ✗） |
| `template_fallback_reason` | 没用上模板的原因 | 告知用户"本次按内置结构生成"，并给出补模板的动作 |
| `report_warnings` | 其它正文告警 | 逐条核对 |

## 5. 验收（回归必过 ✓）

1. 正文含模板里**每个必填小节**（`missing_required_sections` 为空 ✓）；
2. 表格类小节是 Markdown 表格 ✓，表头 = 模板该节「数据对象字段」→ 契约 `labels`（**逐字逐序** ✓）；
3. 版式细节（文档位置格式 / 指纹 / 四态 / 阈值 / 补充块 / 汇总）与**模板**一致 ——
   逐条对模板，**不是对本技能** ✓；
4. 巡检一致：`audit_scan(kb_id=<报告所在库>, scope="template", template_kb_id=<模板所在库>)` **无 T1 命中**；
   `audit_scan(scope="ext")` 无 G4（落后）/G5（列集不符）✓；
5. 同输入重跑内容稳定（除时间戳）✓。

## 6. 纪律

- **不改规范** ✗（模板页 / DataObject 页只读；要改 → 改那页并把影响面告诉用户 ✓）；
- **不编造证据** ✗：取不到逐字证据的条目进"待澄清" ✓（不进表格）；
- 报告**只作证据** ✓（机器判定读的是知识/规则事实 ✓）。

## 工具口径（统一入口）

- 找页 / 找知识：`knowledge_lookup(kb_id, mode="page"|"keyword"|"instances"|"rules"|"path", …)`
- 写库：`save_knowledge(stage="report")`（文档类必须有正文）
- 体检：`audit_scan(scope="template"|"ext")`
