---
name: rule_judge
id: rule_judge
when: 需要把规则逐条判成 符合/不符合/无法判定/不适用 并留字段级依据时（成批判定、入库前复核、出报告前）
tools: [knowledge_lookup, evidence_score, ext_issue_list, save_knowledge]
description: 规则判定——先判**适用性**（规则页 `knowledgeSpec.applies_when`），再读「规则契约」+「知识计划(ke_plan) 的 derived/items」，对每条规则给出 符合/不符合/无法判定/不适用 与依据（字段级），固定输出「结论集合」供报告引用。判定只看规则声明与已提取事实，不做自由发挥。
---

# rule_judge（规则判定）

## 何时用
- 成批判定的最后一步（规则 → 结论）；
- 入库前的复核（结论必须可追溯）；
- `report_compose` 前的最后一道（报告只允许引用**判定结论集合** ✓）。

## 输入（三样，缺一不可）
1. **规则契约**：`rules_resolve(kb_id, spec)` 的 `rules[]`
   - 关键字段：`applies_when` / `evidence_required` / `required_fields` / `needs_extract` / `enforcement` / `ruleHash`;
   - **枚举以回执为准**：`verdict_enum`（符合/不符合/不适用/无法判定）、`severity_enum`（严重/一般/建议）——**不要自己换词** ✗
2. **事实**：`ke_plan`（`knowledge_plan`）的 `items[]`（`required` / `found` / `value` / `quote` / `locator`）
   与 `derived`（**已由规则 spec 推导好的结论**，如 `verdict` / `missing_deploy_fig` / `city_remote` / `dr_ok`）；
3. **判定口径**：规则契约里的 `knowledge_spec`（`applies_when` / `check` / `pass_when` / `fail_when`）——
   **优先读它，不读散文** ✓（读不到 `knowledge_spec` ⇒ 该规则没有条件，按 `applies_when=always` 处理）

## 判定顺序（先适用、再机械、后推理）
```
⓪  applies_when 是否成立？（拿文档标题 + 方案目标与表达式比对）
      不成立 ⇒ 结论 = 「不适用」，**不进 ③ 表**，只在 ⑤ 说明里写一句"本方案不含 X ⇒ 规则不适用" ✓
      成立 / always ⇒ 继续
①  required 字段是否齐全？
      缺 且 needs_extract=true ⇒ 无法判定（缺证据，不是不符合 ✗）
      缺 且 needs_extract=false ⇒ 无法判定（规则自带口径，说明提取阶段漏了）
②  规则若有 derived 结论（spec 里 check 可机算）⇒ **直接采用**，并注明 "derived: <key>" ✓
③  否则按 pass_when / fail_when 逐条比对，给出 field → (期望, 实际) ✓
④  强制级别：
      enforcement=严格执行 且 不符合 ⇒ 结论=不通过（触发整改；是否**一票否决**看 severity）
      enforcement=建议/授权覆盖 且 不符合 ⇒ 结论=整改项（不否决）
```
> ⚠️ **禁止**在证据不足时给 `不符合` ✗ —— 证据不足只能 `无法判定`（否则就是"假断言" ✗）。

## 输出（固定结构，供报告直接引用 ✓）
```json
{"rule": {"slug": "...", "title": "...", "enforcement": "严格执行", "source": "policy:…", "ruleHash": "…"},
 "applies_when": "always|标题或目标含搬迁/迁移",
 "verdict": "符合|不符合|无法判定|不适用",
 "basis": [{"field": "部署图", "expect": "存在", "actual": "缺失", "quote": "", "locator": ""}],
 "derived_used": ["missing_deploy_fig"],
 "note": ""}
```
回执汇总：`{n} 条 ｜ 符合=a 不符合=b 无法判定=c 不适用=d ｜ 否决项(=严格执行∧不符合∧severity∈{严重})=k`，
并**逐条**列出 `不符合` 与 `无法判定` 的字段级依据 ✓。

## 反假断言（自检）
- [ ] 每条 `不符合` 都有 `basis` 且 `actual` 可回访 ✓；
- [ ] 每条 `无法判定` 都注明**缺哪个字段**（不许写"信息不足"了事 ✗）；
- [ ] 每条 `不适用` 都写出**哪一条不满足 `applies_when`**（拿标题/目标原文指认 ✓）；
- [ ] `符合` 的规则其 `required` 全部 `found=true` ✓；
- [ ] 用了 `derived` 的规则写清 key ✓；
- [ ] `a+b+c+d == n` ✓，且"否决项"数 = `严格执行` 中 `不符合` 且 severity=严重 的条数 ✓。

## 清单类产物：一律走对象声明的接口（2026-10-25 用户口径 ✓）

判定结论常常要落成一张**清单**（多行多列）。这类产物若对应某个 `bpmn:DataObject`，且该页声明了
`bpmn:dataObjectInterface`，**数据只能经它声明的工具读写** ✓ —— 本技能**不存数据、不自编号、不改列** ✗：

| 步骤 | 怎么做 | 依据 |
|---|---|---|
| ① 取表头 | `mode=query, action=list, <主键>=…, view=table`；**回执的 `schema.order` / `schema.labels` 就是列名与顺序**（台账 0 条时同法当模板 ✓） | 该对象页的列规范 + `bpmn:dataObjectKeyFields` |
| ② 写条目 | `mode=maintain, action=create`；主键取 `bpmn:dataObjectKeyFields`；**必带 `actor`（执行者）+ `session_no`（会话编号，审计用）**；**编号由台账分配，禁止自编号** ✗ | 同上 |
| ③ 先预览 | 写前 `dry_run=true` 把 `diff` 给用户看；确认后才 `dry_run=false`（`bpmn:dataObjectAccessMode` 含 `maintain` 才可写） | 写库纪律 |
| ④ 回写反馈 | `action=update` + `fields={…}` + `if_rev=<台账 rev>`（台账级）+ `actor`/`session_no`；回执 `conflict` ⇒ 重新 `list` 再改（**不静默覆盖** ✗） | 乐观并发 |
| ⑤ 报告呈现 | 报告里那张表**逐字嵌入** `view=table` 回执的 `markdown`（不改列名、不加列、不重排 ✓） | 列口径真源 = 回执 + 对象页 |
| ⑥ 不可用 | 工具不可用 ⇒ 相关判定记「无法判定」并注明「台账不可用（待澄清）」；**不得自造清单** ✗ | 反假断言 |

> 工具名 / 模式 / 主键**一律从对象页读**（`bpmn:dataObjectInterface` / `bpmn:dataObjectAccessMode` /
> `bpmn:dataObjectKeyFields` / `bpmn:dataObjectInterfaceRef`），技能里**不写死** ✓；
> 回执 `schema` 与对象页列规范不一致 ⇒ **以回执为准**并把差异报给用户 ✓。

## 与其它技能的关系

- 上游：`rules_resolve`（规则）✓ + `knowledge_plan`/`knowledge_extract`（事实）✓；
- 下游：`report_compose`（报告「判定结论」与清单类小节都只引这里 ✓）；
- 不做：文章改写 ✗、入库决策 ✗（那是入库任务的 `ask` 环节 ✓）。

## 工具口径（2026-10-17 统一，`skills/_deprecated.md` §四）

- **取规则**：`knowledge_lookup(kb_id, mode="rules", spec=…)`
  - `spec`：`direct:<slug[,slug]>` / `policy:<策略>` / `influencer:<影响因素>`（链路口径）/ `ask`（**只回策略清单、不含规则**）/ `path:`（预留）；`task:` 已废弃
  - **影响类问题用 `influencer:`**（影响因素→影响手段→策略→规则）；**确需全量策略清单才用 `ask`**
- **找知识**：`knowledge_lookup(kb_id, mode="keyword", query=…)` —— 图优先 → PG 收窄融合 ✓
  每条命中带 `source`（`graph` / `graph+pg` / `pg`）；`pg` 占比高 ⇒ 图缺数据 ✗
- **多跳**：`knowledge_lookup(kb_id, mode="path", start=<slug>, depth=2)` ✓
- **写库**：`write_knowledge`（**图 → PG** ✓）；`document` 类（`bmm:Report` 子类）**必须有正文** ✓

## 判定口径修正（回归样例乙发现 ✓）

1. **否决 ≠ 严格执行 + 不符合** ✗
   - 正确口径：**否决 = 严格执行 ∧ 不符合 ∧ severity ∈ {严重}** ✓
   - 依据：回归样例乙里有 2 条"严格执行且不符合"，但专家严重度=**一般** ⇒ 整改项（**不否决** ✓），
     反馈单结论是"符合 5 / 不符合 3 / 无严重项" ✓；
   - 所以 `严格执行` 只决定**是否必须整改** ✓，**是否一票否决看 severity** ✓。
2. **"缺文档要素" 是不符合，不是无法判定** ✗→✓
   - 回归样例乙 F3-02：文档**未给技术栈勾选表** ⇒ 属**文档缺陷** ⇒ `不符合` ✓（不是"证据不足" ✗）；
   - 只有"**外部**清单/系统不可得"（如外部数据源拿不到 ✓）才判 `无法判定` ✓；
   - 判据一句话：**缺的是"受评文档里该有的东西" ⇒ 不符合 ✓；缺的是"受评文档之外的数据源" ⇒ 无法判定 ✓**。

## ke_score 必经（2026-10-17 用户口径 ✓）：结论必须带"可信度"

**不再是"可选加分项" ✗ —— 每条结论都要出 `{score, level, gate, reasons}` ✓，否则本条判定视为不合格 ✗。**

### 输入：`ke_plan.items[]` 每条事实必须带三键 ✓
```python
{"id": "deploy_fig",
 "status": "present" | "absent_verified" | "unavailable",   # 三态 ✓（缺省视为 present）
 "confidence": "high" | "medium" | "low",                   # 提取可信度 ✓
 "source_type": "doc_table" | "doc_text" | "graph" | "image" | "external_query" | "external_stub",
 "required": True,                                          # 是否"必需事实"（决定硬闸门 ✓）
 "quote": "逐字摘录", "locator": "章节/表格行列/图号"}      # 证据（可回访 ✓）
```

### 判定流程（固定顺序 ✓）
```python
sc = ke_score.score_rule(facts=<该规则 required 的事实>, checks=<fact_check 结果>)  # 阈值见下
if sc["gate"] == "insufficient":
    verdict = "无法判定"      # ✓ 不得写 不符合 ✗
else:
    verdict = "符合" if 满足 pass_when else "不符合"
```
- **阈值/等级从哪来（2026-10-27）**：`agent/metric/规则评测指标` 页（`threshold=0.60`、`level 高≥0.80 / 中≥0.60 / 低<0.60`）；
  工具 `evidence_score` 已按该页取值 ⇒ **技能里不写死数字** ✗（读不到页面时才回退 0.60）；
- `gate` 的两条来源 ✓：**必需事实 `unavailable` ⇒ 硬闸门** ✓；`score < threshold` ⇒ 阈值闸门 ✓；
- `level`：≥0.80 高 ｜ 0.60–0.80 中 ｜ <0.60 低 ✓；
- `reasons` 必须逐条可回访 ✓（缺哪条事实 / 哪个外部源不可得 / 哪条事实缺 quote 或 locator ✓）。

### 输出（报告 ④ 的「可信度」列就取这里 ✓）
```json
{"rule": "...", "verdict": "不符合",
 "credibility": {"score": 0.91, "level": "high", "gate": "ok",
                 "parts": {"coverage": 1.0, "confidence": 0.95, "evidence": 1.0, "check": 1.0},
                 "reasons": ["文档要素确认缺失：… ⇒ 支持不符合 ✓（不是证据不足 ✗）"]},
 "basis": [...]}
```

### 与"无法判定说明"的衔接 ✓
`gate=insufficient` 的条目 ⇒ **不进表格列** ✗，而是进 ④ 表后的「无法判定说明」✓：
① 原因（= `reasons` ✓）② 影响的问题（编号 ✓）③ 需要反馈或核实的内容（**具体可执行** ✓）。

### 自检
- [ ] 每条结论都有 `credibility` ✓（缺 = 不合格 ✗）
- [ ] `gate=insufficient` ⇒ 判定一定是「无法判定」✓（写成不符合 = 假断言 ✗）
- [ ] `unavailable` 的事实 `required=True` ✓（否则硬闸门不触发 ✗）
- [ ] `reasons` 里没有"信息不足"这种空话 ✗（必须指出具体事实/数据源 ✓）

## 判定输出（硬 ✓）
- **全量输出**：每条规则**至少一个**结论（`符合｜不符合｜无法判定｜不适用`）✓；只报不符合 ✗；
- **一规则可多结论**：同一规则在不同文档位置/模块结论不同时，**按位置各出一条** ✓（例：同一规则在不同模块/位置的结论不同 ✓）；结论必须带各自的定位与原文证据 ✓。
- **取规则/策略正文**：在**领域库**，用 `knowledge_lookup(mode="rules")` 取契约，或用
  `knowledge_lookup(mode="page", query="<规则页 slug 或标题>")` 取整页（正文 + 属性）；
  **不用**内置 `wiki_read_page` / `grep_chunks`（已从评审智能体白名单移除，且读页无口径追溯 ✗）
- **不要用 `context_page`** ✗ —— 它是「跨库同名概念视图」，不是取正文的入口；误用会报「本库没有这一页」（库名/副本不含该页 ✓）。
- **取页面属性**：`knowledge_lookup(mode="keyword")` 的 **`definition` / `attributes`** ✓；`summary` 仅展示 ✗（截断）

## 检索（类型优先，2026-10-22）

- 先 `ontology_types(model)` 看类与 `parents/children`；
- `knowledge_lookup` 会把**类名**识别为检索范围（不是 AND 关键词）；
- 组合词为空时它会**自动降级 OR** 并回 `suggestions`（父类扩大 / 列实例 / 换库 / `mode=rules` 入口）——**照 args 直接调用，不要自己换词试错**；
- 若多个词是**关系链**（类A 类B 类C），用 `mode="route"` 拿类型路径，别用 AND。
