---
name: rule_judge
id: rule_judge
when: 需要把规则逐条判成 passed/failed/insufficient 并留字段级依据时（逐项评审、入库前复核、报告撰写前）
tools: [knowledge_lookup, wiki_read_page, grep_chunks, save_knowledge]
description: 规则判定——读「规则契约」+「知识计划(ke_plan) 的 derived/items」，对每条规则给出 passed/failed/insufficient 与依据（字段级），固定输出「结论集合」供报告引用。判定只看规则声明与已提取事实，不做自由发挥。
---

# rule_judge（规则判定）

## 何时用
- `逐项评审` 第 1 步末尾（规则 → 结论）；
- `知识入库` 前的复核（结论必须可追溯）；
- `report_compose` 前的最后一道（报告只允许引用**判定结论集合** ✓）。

## 输入（三样，缺一不可）
1. **规则契约**：`rules_resolve(kb_id, spec)` 的 `rules[]`
   - 关键字段：`required_fields` / `needs_extract` / `enforcement` / `source` / `hash`；
2. **事实**：`ke_plan`（`knowledge_plan`）的 `items[]`（`required` / `found` / `value` / `quote` / `locator`）
   与 `derived`（**已由规则 spec 推导好的结论**，如 `verdict` / `missing_deploy_fig` / `city_remote` / `dr_ok`）；
3. **判定口径**：规则自身的 `knowledgeSpec`（`check` / `pass_when` / `fail_when`）——**优先读它，不读散文** ✓。

## 判定顺序（先机械、后推理）
```
①  required 字段是否齐全？
      缺 且 needs_extract=true ⇒ insufficient（缺证据，不是 failed ✗）
      缺 且 needs_extract=false ⇒ insufficient（规则自带口径，说明提取阶段漏了）
②  规则若有 derived 结论（spec 里 check 可机算）⇒ **直接采用**，并注明 "derived: <key>" ✓
③  否则按 pass_when / fail_when 逐条比对，给出 field → (期望, 实际) ✓
④  强制级别：
      enforcement=严格执行 且 failed ⇒ 结论=不通过（一票否决，触发整改）
      enforcement=建议/授权覆盖 且 failed ⇒ 结论=整改项（不否决）
```
> ⚠️ **禁止**在证据不足时给 `failed` ✗ —— 证据不足只能 `insufficient`（否则就是"假断言" ✗）。

## 输出（固定结构，供报告直接引用 ✓）
```json
{"rule": {"slug": "...", "title": "...", "enforcement": "严格执行", "source": "policy:…", "hash": "…"},
 "verdict": "passed|failed|insufficient",
 "basis": [{"field": "部署图", "expect": "存在", "actual": "缺失", "quote": "", "locator": ""}],
 "derived_used": ["missing_deploy_fig"],
 "note": ""}
```
回执汇总：`{n} 条 ｜ passed=a failed=b insufficient=c ｜ 否决项(=严格执行&failed)=k`，并**逐条**列出 `failed` 与 `insufficient` 的字段级依据 ✓。

## 反假断言（自检）
- [ ] 每条 `failed` 都有 `basis` 且 `actual` 可回访 ✓；
- [ ] 每条 `insufficient` 都注明**缺哪个字段**（不许写"信息不足"了事 ✗）；
- [ ] `passed` 的规则其 `required` 全部 `found=true` ✓；
- [ ] 用了 `derived` 的规则写清 key ✓；
- [ ] `a+b+c == n` ✓，且"否决项"数 = `严格执行` 中 `failed` 的条数 ✓。

## 与其它技能的关系
- 上游：`rules_resolve`（规则）✓ + `knowledge_plan`/`knowledge_extract`（事实）✓；
- 下游：`report_compose`（报告「评审结论」与「问题清单」都只引这里 ✓）；
- 不做：文章改写 ✗、知识入库决策 ✗（那是 `知识入库` 任务的 `ask` 环节 ✓）。

## 工具口径（2026-10-17 统一，`skills/_deprecated.md` §四）

- **取规则**：`knowledge_lookup(kb_id, mode="rules", spec=…)`
  - `spec` 五种：`direct:<slug[,slug]>` / `policy:<策略>` / `ask` / `task:<任务>` / `path:`（预留）
  - 旧调用 `rules_of_task(kb, task)` ≡ `spec="task:<任务>"` ✓（行为保留，**新写一律用本入口**）
- **找知识**：`knowledge_lookup(kb_id, mode="keyword", query=…)` —— 图优先 → PG 收窄融合 ✓
  每条命中带 `source`（`graph` / `graph+pg` / `pg`）；`pg` 占比高 ⇒ 图缺数据 ✗
- **多跳**：`knowledge_lookup(kb_id, mode="path", start=<slug>, depth=2)` ✓
- **写库**：`write_knowledge`（**图 → PG** ✓）；`document` 类（`bmm:Report` 子类）**必须有正文** ✓

## 判定口径修正（2026-10-17 · 样例3 回归发现 ✓）

1. **否决 ≠ 严格执行 + failed** ✗
   - 正确口径：**否决 = 严格执行 ∧ failed ∧ severity ∈ {高, 严重}** ✓
   - 依据：样例3 有 2 条"严格执行且不符合"，但专家严重度=**一般** ⇒ 整改项（**不否决** ✓），
     反馈单结论是"符合 5 / 不符合 3 / 无严重项" ✓；
   - 所以 `严格执行` 只决定**是否必须整改** ✓，**是否一票否决看 severity** ✓。
2. **"缺文档要素" 是 failed，不是 insufficient** ✗→✓
   - 样例3 F3-02：文档**未给技术栈勾选表** ⇒ 属**文档缺陷** ⇒ `failed` ✓（不是"证据不足" ✗）；
   - 只有"**外部**清单/系统不可得"（如样例1 中心认证清单不可得 ✓）才判 `insufficient` ✓；
   - 判据一句话：**缺的是"受评文档里该有的东西" ⇒ failed ✓；缺的是"受评文档之外的数据源" ⇒ insufficient ✓**。

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
sc = ke_score.score_rule(facts=<该规则 required 的事实>, checks=<fact_check 结果>, threshold=0.60)
if sc["gate"] == "insufficient":
    verdict = "无法判定"      # ✓ 不得写 failed ✗
else:
    verdict = "符合" if 满足 pass_when else "不符合"
```
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
- **一规则可多结论**：同一规则在不同文档位置/子系统结论不同时，**按位置各出一条** ✓（例：部署方式对网关=符合、对结算=不符合 ✓）；结论必须带各自的定位与原文证据 ✓。
- **取规则/策略正文**：在**领域库**，用 `knowledge_lookup(mode="rules")` 或 `wiki_read_page(领域库, slug)` ✓；
- **不要用 `context_page`** ✗ —— 它是「跨库同名概念视图」，不是取正文的入口；误用会报「本库没有这一页」（库名/副本不含该页 ✓）。
- **取页面属性**：`knowledge_lookup(mode="keyword")` 的 **`definition` / `attributes`** ✓；`summary` 仅展示 ✗（截断）

## 检索（类型优先，2026-10-22）

- 先 `ontology_types(model)` 看类与 `parents/children`；
- `knowledge_lookup` 会把**类名**识别为检索范围（不是 AND 关键词）；
- 组合词为空时它会**自动降级 OR** 并回 `suggestions`（父类扩大 / 列实例 / 换库 / `mode=rules` 入口）——**照 args 直接调用，不要自己换词试错**；
- 若多个词是**关系链**（类A 类B 类C），用 `mode="route"` 拿类型路径，别用 AND。
