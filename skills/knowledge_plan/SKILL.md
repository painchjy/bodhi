---
id: knowledge_plan
name: 知识需求聚合（多规则 → 知识计划）
description: 把 N 条规则的 knowledgeNeed 聚合成**可执行的知识计划**：候选类 → 去重 → 父子类整合（同链收敛到最近公共祖先）→ 归属本体模型 → 建模专家角色 → 歧义清单（questions）+ 稳定 hash。**只读、不写库**；确定性部分全在这里，语义歧义交智能体问用户。
when: 任务的一步要处理**多条规则**且其中 ≥1 条有知识提取需求时（`rules_of_task` 里 needs_extract=true 的条数 ≥1）；单条规则时本技能同样适用（N=1，自动退化，不特判）。
models: [bmm, bpmn, agent]
default_model: bmm
stages: [plan]
tools: [rules_of_task, ontology_types, knowledge_lookup, wiki_read_page]
version: 0.1.0
guard: 只读；不猜类（歧义进 questions）；同链才整合（不同链不硬合）；外部系统已管理的数据不当本体类（走挡板/接口）；模型与专家角色一律从编译产物读取。
---

# 知识需求聚合（知识计划）

**输入**：`domain_kb` · `task`（或直接给 `rules` 列表）· 可选 `policies`（多选）
**输出**：`knowledge_plan`（**只读产物**，智能体据此逐项调 `knowledge_extract`）
**分工（方案 C）**：**确定性的事全在本技能**（分类候选/去重/父子整合/模型/角色/hash）✓；
**语义裁决归智能体**（把 `questions` 变成用户能答的选择题 ✓）；**写入归 `knowledge_extract`** ✓。

## 0. 取输入
1. `rules_of_task(domain_kb, task, policy?)` → 取 `needs_extract=true` 的规则（含 `knowledgeNeed`）；
2. 若 `needs_extract` 条数 = 0 → 返回 `{empty:true}`（本步无需提取）；
3. **N=1 时照走本流程**（同一路径、无特判 ✓）。

## 1. 每条 need → 候选类（**现查，不写死**）
1. 先把 `knowledgeNeed` 拆成**知识项**（如"技术栈及其版本"→（软件名称, 版本））；
2. `ontology_types("<模型>")` **现查**（多模型逐个查；`requires`/`affects` 提示依赖）；
3. 每个知识项映射**候选类**（可多个）：词面/别名匹配（`knowledge_lookup` 类模式），
   并把匹配理由写进 `why`（可复核 ✓）；
4. **外部系统已管理的数据**（如"开源软件清单"由 IT 工作平台维护）⇒ **不是本体类** ✗：
   该项标 `source:"external"` + `dataobject:"<DataObject 页>"` + `via:"stub|api"` ✓。

## 2. 去重
- 同一 `(class)` 合并；`from_rules[]` 记录来源规则（多规则合并时可回溯 ✓）；
- 同一类但"知识项"不同 ⇒ 保留 `needs[]` 列表（一个类采多种事实 ✓）。

## 3. 父子类整合（**只合并同链** ✓）
1. 取每个候选类的**祖先闭包**（`ontology_types` 的 `parents`/`ancestors` ✓）；
2. **同一 `rdfs:subClassOf` 链**上的多个类 ⇒ 收敛到**最近公共祖先(LCA)**，并给 `collect_at` 标注采集口径：
   - `collect_at: "parent"`（父类采：一次采集覆盖多子类 ✓ 适合"清单类/同质项"）；
   - `collect_at: "each"`（各自采：子类语义不同，逐类采 ✓）；
3. **不在同链** ⇒ **不硬合** ✗，各自保留；把"是否合并"作为选择题写进 `questions` ✓。

## 4. 归属模型 + 专家角色（**从产物读，不猜** ✓）
1. 类 → `module`（`ontology_types` 回执带）✓；
2. 模块 → **专家角色**（编译产物 `models[].expert_role`，来自 TTL 的 `bodhi:expertRole` ✓）；
3. 多模型 ⇒ `groups` 分组，并给每个模型一个角色；**多角色时说明各自负责哪些知识项** ✓；
4. 角色缺失 ⇒ 进 `questions`（"该模块没有专家角色，是否用默认？"✓）。

## 5. 输出（稳定结构，**字段顺序固定**，便于断言）
```jsonc
{
  "task": "<slug>", "rules":[{"slug","title","knowledgeNeed","needs_extract"}],
  "items": [{
     "need": "技术栈及其版本", "class": "bmm:SoftwareProduct",   // 或 null（歧义/外部）
     "candidates": ["bmm:SoftwareProduct","…"],                   // 多候选时非空
     "source": "ontology|external", "dataobject": "bpmn/dataobject/开源软件清单",
     "via": "stub", "collect_at": "each|parent", "module": "bmm",
     "expert_role": "企业架构分析专家…", "from_rules": ["…"], "why": "…"
  }],
  "groups": {"bmm": ["…"], "bpmn": ["…"]},
  "questions": [{"kind":"class_ambiguous|model_ambiguous|merge_parent_child|role_missing",
                 "ask":"…", "options":["…"]}],
  "counts": {"rules":6,"items":4,"external":1,"ambiguous":1},
  "hash": "…"
}
```
- **`hash`** = 对 `{task, items(排序后), rules(排序后)}` 的稳定 sha256 前 16 位 ✓（同输入必同 ✓）。

## 6. 与下游的衔接
1. 智能体把 `questions` 逐条问用户（**每题给选项** ✓），拿到答复后回填 `items`（只改被问的字段 ✓）；
2. 按 `items` 逐项调 **`knowledge_extract`**：
   - `source=ontology` ⇒ 按类抽事实（`ontology_types` 校验类 ✓、`source_text`+`sourceLocator` 必带 ✓）；
   - `source=external` ⇒ 走**挡板/接口**（不建本体类 ✗，样例取自 DataObject 的 `skos:note` ✓）；
3. 多个 `items` 同一技能 ⇒ **合并批量调用** ✓；有依赖（先父类后子类）⇒ 串行 ✓。

## 7. 纪律（从"领域建模/结构化建模"沿用 ✓）
- **不猜类/模型/角色** ✗：歧义一律进 `questions`（人裁决 ✓）；
- **只合并同链** ✓，跨链不硬合 ✗；
- **外部系统数据不本体化** ✗（DataObject + 挡板/接口 ✓）；
- 本技能**只读** ✓（不写库 ✗）；写入库由 `knowledge_extract` 执行 ✓。

## 8. 验收 / 评测（3 组固定输入，对比 A/B/C 用）
| 输入 | 期望 |
|---|---|
| 1 条规则（技术栈及版本） | 1 项；`source=external`（DataObject「开源软件清单」）✓；**不产生**本体类 ✗ |
| 2 条同类规则（技术栈 + 设备申请） | 父子整合：`bmm:HardwareAsset` ⊂ `bmm:ITAsset` ⇒ LCA=`bmm:ITAsset`，`collect_at` 明确 ✓ |
| 2 条跨模型规则（系统等级 ⊂ bmm；报告字段 ⊂ bpmn） | `groups` 两组 ✓ + **两个专家角色** ✓ + `questions` 中 model 歧义（若未指定 ✓） |
断言：① 分类命中 ✓ ② 整合正确 ✓ ③ 模型/角色正确 ✓ ④ 同输入 `hash` 稳定 ✓ ⑤ `questions` 只含真歧义（不滥问 ✓）。
