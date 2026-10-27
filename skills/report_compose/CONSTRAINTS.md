# 报告（document 模式）的边界备忘 —— 详见 `skills/_deprecated.md` §四

> 2026-10-27 收敛：**版式只认模板页**（`schema:DocumentTemplate` 正文），**取数只认任务页 `skos:note`**。
> 本文只保留工具层的三条硬约束与自检，不再复述领域口径（避免第四处真相 ✗）。

## 三条硬约束（工具层只保证规范 ✓，不猜场景 ✗）

1. **报告类型由「用户 / 任务步骤」确定** ✓
   - 必须是 `bmm:Report` 的**子类**（`bmm:TechnicalReviewReport` / `bmm:RequirementAnalysisReport` /
     `agent:Advice` / `agent:DesignSpec` / `agent:Evaluation` / `agent:InstallGuide` … ✓）
   - 本技能不决定类型 ✗，只校验"是不是 Report 子类" ✓
2. **章节 / 每节格式 / 表格列与列序一律来自模板页正文** ✓
   - 解析入口：模板正文「章节结构」表（`序号 | 章节 | 必填 | 内容格式要求 | 数据对象字段`）
   - 模板没有 ⇒ `ke_report` **不内置任何章节**（回 `template_fallback_reason`，不猜 ✗）
3. **正文由本技能生成** ✓；**数据属性不产正文** ✓
   - 数据属性只作属性面板（`skos:definition` / `verdicts` / `bmm:ruleSet` … ✓）
   - 正文不重复属性值 ✓（与 entity 类相反 ✓）

## 写入口（唯一）

```
save_knowledge(kb_id=<库>, stage="report", mode="apply", session_no=<会话>,
               report={title, slug, page_type=<Report 子类>, content_md, attributes, template?})
```
- `mode`（`render_mode`）由 `ke_ontology.render_mode(page_type)` 自动判 `document` ✓，不用传；
- 缺会话溯源 ⇒ `need_session` 拒写 ✓（`session_no` 必须是真实 WeKnora 会话编号）；
- 模板页同样走这个口（`page_type: schema:DocumentTemplate`，无需 retag ✓）。

## 反假断言（自检）

- [ ] `page_type` 是 `bmm:Report` 子类 ✓
- [ ] 章节与模板「章节结构」表**一一对应**（顺序一致、不多不少）✓
- [ ] 清单类小节是**表格** ✓（不是分段罗列 ✗）
- [ ] 表格列与**模板「数据对象字段」格**一致（逐字逐序）✓
- [ ] `verdicts` 等属性是"机算结论"✓，与正文结论**一致** ✓
- [ ] 回执带 `template` / `missing_required_sections` / `template_fallback_reason` ✓

## 可复现（2026-10-17 用户口径 ✓）：格式只能来自模板，不许自定 ✗

| 格式要素 | **唯一来源** | 不许 ✗ |
|---|---|---|
| 章节与顺序 | 模板正文「章节结构」表 ✓ | 自拟章节 ✗ |
| **清单表格的列与顺序** | 模板「章节结构」的**「数据对象字段」列** ✓ | 自己加/删列 ✗；去 DataObject 页抄列清单 ✗ |
| 每行**可信度** | `ke_score.score_rule(facts, checks)` → `score` + `level` ✓ | 拍脑袋写"高/中" ✗ |
| 判定枚举 | 模板（四态：符合 / 不符合 / 不适用 / 无法判定）✓ | 自造状态 ✗ |
| 表后补充块句式 | 模板的《③ 格式规格》✓ | 自创写法 ✗ |
| 目录可见 | 写库后 `ke_pages.sync_folders(kb)` ✓ | 只写页不重建目录 ✗ |

**复现校验（跑完必看 ✓）**：同一文档 + 同一规则集 ⇒ 章节数、③ 的列名与顺序、`verdicts`/`credibility`
分布应**逐字一致** ✓；变的只应是事实值与结论 ✓。
