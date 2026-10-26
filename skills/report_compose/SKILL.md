---
id: report_compose
name: 报告组装（模板驱动，表格优先）
description: 把「判定结论集合 + 规则集合」组装成**报告类文档**：章节顺序与必填项**一律取自模板页正文的「章节结构」表**（模板 = `schema:DocumentTemplate` 实例，按 `schema:applicableReportType` 选中 ✓）；某节取哪几列由模板的「数据对象字段」列决定，**字段名/枚举以接口契约（`x-ext`）为准**；禁止自拼章节或列 ✗。
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
| **表格列集** | 模板「章节结构」的**「数据对象字段」列**（该节取哪几列、什么顺序） | 工具字段可以多于报告所需；`—` = 该节不取外部数据 |
| **字段名与受控枚举** | 接口契约（`tools/list` 的 `x-ext`：`labels` / 枚举） | 逐字使用，不自造同义词 ✗；`DataObject` 页**只写接口，不写列清单** ✗ |

> ⚠️ 章节**不写死在技能里** ✗ —— 改章节 = 改模板页（`save_knowledge(stage="report")`，同 slug 覆盖）；
> 改列 = 改该模板「章节结构」的**「数据对象字段」列**（DataObject 页只写接口，不写列清单 ✗）。
技能只描述**流程**。

## 1. 组装（严格按模板顺序 ✓）
1. 按模板「章节结构」表逐节产出；**「必填」= 是** 的小节必须有内容（确无内容 ⇒ 按模板「要求」列写「无」）；
2. 清单类小节（模板「要求」列写明"逐条/表格"的）⇒ **Markdown 表格**，表头 = 模板该节「数据对象字段」列映射到的**契约列名**（`labels`）；
   - **该 DataObject 若声明了 `bpmn:dataObjectInterface`** ⇒ 表体**直接嵌该工具 `view=table` 回执的 `markdown`**
     （逐字：列名/顺序/空值都以回执为准，**不改列、不重排** ✗）；工具不可用 ⇒ 该小节写「台账不可用（待澄清）」+ 原因，
     **不编行** ✗；写完核对「表内行数 = 回执 `counts.items`」✓
   - **字段子集由模板定**：模板「章节结构」表的**「数据对象字段」列**（逗号分隔，**顺序即展示顺序** ✓）
     就是要展示/编辑的列 —— 调工具时原样传 `columns=` ⇒ 回执 `markdown` 只会出这几列（缺列不补、顺序不动 ✗）；
     模板没写（`—` 或没有该列）⇒ 该节不取外部数据 / 用工具全量契约。**模板选的是"这份报告用哪些字段"，不是改契约** ✓
     （同一 DataObject 的不同报告可以取不同子集）；
   - **写作口径也只认模板**（覆盖要求 / 表后补充块 / 三态与阈值 / 图证据 / 汇总）：都在模板正文的**口径节**里
     （标题含「口径」的小节）；**不要**去 DataObject 页的 `skos:definition` 抄列清单或口径 ✗
     —— 那页只写接口（绑定 + 契约来源 + 读写口径）。
   - **该小节末尾追加一张"面板引用块"**（前端据此渲染可编辑表格）：
     **优先让工具补** —— `save_knowledge(stage="report")` 发现缺块会自动补（编号/rev/hash/columns 全来自台账回执，
     回执里 `ext_ref_block` 会告诉你补了什么）✓；只有工具报 `object_ambiguous` 时才需要你手工补：
     ```` 
     ```bodhi-ext
     object: <DataObject slug>
     review_no: <外层主键值>
     basis: <服务名> / <工具名>
     rev: <回执 rev>
     schema_hash: <回执 schemaHash>
     ```
     ````
     —— 这是**机器可读的锚点**：页面/写入闸门都按它比对台账版本与契约 hash；**不要改写字段名** ✗
     ⚠️ 块里的 `review_no` 必须是**本次报告 ① 表里的真实编号**（示例值只是占位，**照抄示例 = 错** ✗）；
     拿不到就不写块 —— 让工具在写库时自动补（它会从正文里认编号，比手写可靠 ✓）
3. 每条条目的**证据/定位/汇总口径**一律按模板的口径节执行（本技能不复述）—— 取不到证据的条目
   进"待澄清"说明段，**不进表格** ✗（口径节会写明具体判定与补充块写法）；
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
2. 表格类小节是 Markdown 表格 ✓，表头 = 模板该节「数据对象字段」→ 契约 `labels`（**逐字逐序** ✓）；
3. 其余**领域口径**（枚举取值、证据/定位、指纹、汇总、参考规范状态位）**以模板的口径节为准** —— 本技能不重复；
4. 巡检一致：`audit_scan(kb_id=<报告所在库>, scope="template", template_kb_id=<模板所在库>)` **无 T1 命中** ✓；
5. 同输入重跑内容稳定（除时间戳）✓。

## 5. 纪律
- **不改规范** ✗（模板页 / DataObject 页只读；要改 → 改那页并把影响面告诉用户 ✓）；
- **不编造证据** ✗：取不到逐字证据的条目进"待澄清" ✓；
- 报告**只作证据** ✓（机器判定读的是知识/规则事实 ✓）。

## 工具口径（统一入口）
- 找知识：`knowledge_lookup(kb_id, mode="keyword"|"instances"|"rules"|"path", …)`
- 写库：`save_knowledge(stage="report")`（文档类必须有正文）
- 体检：`audit_scan(scope="template", template_kb_id=…)`（模板章节完整性）
