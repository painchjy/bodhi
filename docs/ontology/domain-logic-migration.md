# 域逻辑迁移（域词不是"直接删"，而是搬进领域知识库）

> 2026-10-23 用户口径：
> **域词相关逻辑要转移到领域知识库**（例：报告章节要求 → **模板实例页**），并**完善模型/技能/工具**，
> 让智能体**依赖领域知识找到正确的模板**——**不能自己猜**，**不应反查需求文档**。
> 代码/技能里剩下的域词，只有在"逻辑已在领域库 + 有用例证明智能体找得到"之后才允许消失。

## 1. 三层分工（谁是事实源）

| 层 | 位置 | 放什么 | 不放什么 |
|---|---|---|---|
| **本体（模型）** | `docs/ontology/SCHEMA完整版.ttl` + `BMM/BPMN/智能体开发完整版.ttl` → 上传编译 | 类/关系/**属性的语义**（`schema:DocumentTemplate`、`applicableReportType`、`templateSelector`） | 任何具体报告类型名、章节清单、业务口径 |
| **领域知识库（数据）** | 领域库实例页 | **模板实例页**（`page_type=schema:DocumentTemplate`）：章节表写在**正文**、`applicableReportType` 写"能出哪类报告"、`templateSelector` 指规则页 | 通用规则（那些该进本体或技能） |
| **工具/技能（代码）** | `ke_report` / `knowledge_lookup` / `skills/**` | **只认类型与属性**：怎么写、怎么找、怎么校验 | 具体章节名/报告名/策略名等域词 |

## 2. 智能体的正确决策链（禁止猜 / 禁止反查需求文档）

```
① 需要出「某类报告」
   └─ knowledge_lookup(mode="instances", cls="schema:DocumentTemplate")
      → 用 applicableReportType 过滤（值 = bmm:Report 的子类名，如 bmm:TechnicalReviewReport）
② 多条候选 → 用 templateSelector 指向的**规则页**判定（裁剪前/裁剪后、适用条件）
   └─ knowledge_lookup(mode="rules", spec="policy:<策略名>") 或直接读规则页
③ 读模板**正文**的章节表 → 得到"必填章节 / 顺序 / 每节要求"
④ ke_report(template=<模板 slug>) 按模板渲染；缺必需章节 ⇒ 报缺（不静默补）
⑤ 落库：报告页 articleBody（正文）+ sourceLocator（会话分页）
```

**禁止行为**：靠标题里出现"评审""方案"等词猜模板；为凑章节去反查需求文档；把模板内容复制进代码。

## 3. 工具改造（按依赖顺序）

| # | 工具 | 改动 | 验收 |
|---|---|---|---|
| T1 | `knowledge_lookup` | 已支持 `page_type` / `cls` / `instances`；补：`applicableReportType` 作为**属性过滤**（`attrs={"schema:applicableReportType": "<类名>"}`） | 用类名能精确拿到 1~N 个模板 |
| T2 | `ke_report` | 新增 `template`（slug）与 `report_type`；未给 template ⇒ **自动按 applicableReportType 选**（多候选按 templateSelector 规则，仍多则报歧义）；渲染时以模板章节表为骨架；缺模板 ⇒ 回落内置结构 + `template_fallback_reason` | 同一份内容，换模板 ⇒ 章节结构随之变 |
| T3 | 巡检 `audit_kb` | 新增"模板章节完整性"：模板声明的必填章节 vs 生成报告实际章节；缺 ⇒ medium | 故意缺一节 ⇒ 巡检命中 |
| T4 | `page_attributes_set` | 模板页属性写入放开这两个 schema 属性（属于 `schema:DocumentTemplate`，已在 `class_attributes` ✓） | 模板页可写 `applicableReportType`/`templateSelector` |

## 3.1 模板页怎么维护（2026-10-23 口径）

| 做法 | 说明 |
|---|---|
| ✅ **`save_knowledge(stage="report")` + `report.page_type`** | 报告与模板**同属 `schema:CreativeWork`** ⇒ 直接用 report 通道写：`report.page_type="schema:DocumentTemplate"`、`report.content_md=<章节表>`、`report.attributes={"schema:applicableReportType": "bmm:TechnicalReviewReport"}`。**不需要 retag**（实测 v3→v4 直接更新成功） |
| ❌ `stage="graph"` 写文档类 | **不支持**：graph 阶段只处理**非 CreativeWork**（entity 类）；文档类节点仍会被 `skipped_document_nodes` 拒绝（这是期望行为，不要放宽） |
| 判定依据 | `ke_ontology.render_mode()` = **是否属于 `schema:CreativeWork` 谱系**（含自身）；`bmm:Report`、`schema:DocumentTemplate` 自动覆盖 |

> 「首个版本生成」等页头值按内核规则只在新建时写；同 slug 覆盖不会重置。


| 技能 | 现状 | 改成 |
|---|---|---|
| `report_compose` | 把章节清单、报告类型、口径写在 SKILL/CONSTRAINTS 里（域词 47 处） | 只讲**流程**：先按 §2 找模板 → 读章节表 → 逐节产出 → 自检缺节；具体章节名全部来自模板页 |
| `rule_judge` | 部分评审口径写死 | 评审口径来自**规则页**与**模板的 templateSelector**；技能只讲"按规则/模板判定"的流程 |
| 其它 | 见 `tests/golden/biz_terms_baseline.json` | 逐项按同一原则处理（逻辑搬领域库，代码只留类型/属性/流程） |

**域词退场判据（三条同时满足才删）**：① 逻辑已在领域库可查；② 工具有用例证明能自动定位；③ 技能改写为流程化且回归通过。

## 5. `taskExecutesRule`：保留，作为**多路径检索**案例

- 两条路径并存：`Task --taskExecutesRule--> BusinessRule`（直接）与
  `Task --taskExecutesPolicy--> BusinessPolicy --isBasisFor--> BusinessRule`（经策略）；
- `ke_ontology.ontology_paths(from, to)` 返回**全部 ≤N 跳通路**（含反向），
  `knowledge_lookup(mode="route")` 据此给"检索配方"；
- 优化方向：**按步骤描述选路径**（步骤写"按策略"⇒ 走间接路径；写"某条规则"⇒ 走直接路径），
  而不是两条都查、也不是只用一条。用例放 `tests/cases/lookup_cases.json` 扩展。

## 6. 落地顺序

1. **建模板实例页**（领域库）：每类报告先建 1 个（章节表在正文、`applicableReportType` 指报告类）
2. **T2 `ke_report` 读模板** + 用例（换模板 ⇒ 章节变；缺模板 ⇒ 回落并给原因）
3. **T1 属性过滤** + **T3 巡检** + 用例
4. **技能改写**（`report_compose` → 流程化）
5. **最后**才删代码/技能里的域词（按 §4 判据逐条），基线归零后把 `test_no_business_semantics` 改为硬断言 0
