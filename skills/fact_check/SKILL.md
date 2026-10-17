---
name: fact_check
description: 事实核查——把「问题清单」里的每一条问题核对到原文证据：逐字引用 + 可定位（章节/段落/表格行列），区分「客观事实/规范符合性」与「主观意见」，回写核查状态。用于评审组长确认前的把关，避免把「意见」当「事实」、把「转述」当「证据」。
---

# fact_check（事实核查）

## 何时用
- 评审智能体产出一批问题后、**交组长确认前**（`逐项评审` 第 2 步）；
- 专家驳回/澄清后需要复核（`问题核实` 第 1 步）；
- 任何"拿问题去问人"之前——**先确认这条问题站得住**。

## 铁律（违反即视为无效核查）
1. **证据必须逐字**：`quote` 从原文**复制**，不得改写、不得拼接两处、不得省略号省略关键限定词；
2. **定位必须可回访**：`locator` 到**章节标题**或**表格名+行号**（如「3.2 技术架构 · 表格『设备清单』第 4 行」）；
   - 找不到 → `locator="未定位"` 且 `verdict="证据不足"`（**不得**猜一个）；
3. **区分三类**（写进 `claim_type`）：
   - `fact`：可证伪的事实（"未提供部署图"、"开源组件 A 的许可证是 GPL-3.0"）；
   - `compliance`：规范符合性（"最低部署要求 §2 要求同城双活，方案未写"）；
   - `opinion`：主观判断（"架构过于复杂"）→ **不得**计入问题清单的"事实问题"，须转"建议"并标注；
4. **一条问题一个结论**：`supported` / `not_supported` / `insufficient`（三选一，不许"大致符合"）。

## 步骤
1. **取待核查清单**
   - 输入：问题清单（slug 列表 或 题目数组）；若来自报告 → 取报告里「问题清单」节；
2. **逐条取原文**
   - 按 `source_slug` + `locator` 读页正文；**只读不写**；
   - 无 `source_slug` → 视为 `insufficient`（缺溯源 ✗）；
3. **逐条比对**（写 `verdict` + `reason`）
   - `supported`：原文**直接**支持（quote 即证据）；
   - `not_supported`：原文**直接**反驳（给出反驳 quote）；
   - `insufficient`：原文缺失/模糊/未定位；
4. **判 claim_type**（`fact` / `compliance` / `opinion`）
   - `opinion` ⇒ 标记"建议类"，**不改**问题状态，交组长决定是否保留；
5. **回写**（幂等：同 `question_slug` 重跑覆盖，不追加重复）
   - 写回问题页：`verdict` / `claim_type` / `quote` / `locator` / `checked_at`；
   - 状态迁移：`待核查` → `待组长确认`（supported/compliance）｜`证据不足` 或 `已驳回`（not_supported/insufficient）；
6. **回执**（给组长/专家看，必须能一眼判"这条该不该发"）
   ```
   核查：{n} 条 ｜ supported={a} not_supported={b} insufficient={c} ｜ 建议类(opinion)={d}
   · {question_slug} ｜ {claim_type} ｜ {verdict} ｜ quote=「…」 ｜ locator=…
   ```

## 判定标准从哪来（不靠模型记忆 ✓）
- 规范符合性类问题 → 先 `rules_resolve(kb_id, "direct:<该规则 slug>")` 取规则的 `required_fields` / `needs_extract`，
  **按规则定义的字段**核对（而不是凭印象）；
- 涉及策略范围 → `rules_resolve(kb_id, "policy:<策略>")` 取该策略下全部规则，逐条看是否覆盖。

## 反假断言（自检，全过才算完成）
- [ ] 每条 `quote` 都能在原文**全文检索**到（逐字 ✓）；
- [ ] 每条 `locator` 都能回访（章节存在 / 表格行列存在 ✓）；
- [ ] 没有把 `opinion` 写成 `fact` ✓；
- [ ] `insufficient` 的条数 ≤ 总数（不是"全都不足"蒙过去 ✗）；
- [ ] 回执 `a+b+c+d == n` ✓。

## 与其它技能的关系
- 产出 → `逐项评审` 第 2 步的输入（组长确认）→ `问题核实`（专家裁决）；
- 消费者 → `report_compose`（报告「问题清单」表格的 `verdict`/`quote` 列）；
- **不做**判定（那是 `rule_judge`）✗、**不做**入库（`knowledge_extract` 写入阶段）✗。
