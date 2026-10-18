---
name: rule_judge
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
