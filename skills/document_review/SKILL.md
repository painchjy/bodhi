---
id: document_review
name: 文档评审（按业务策略下的业务规则逐条评）
description: 选定文档（知识库文档或附件）→ 选业务策略 → 按该策略下的业务规则逐条评审：LLM软规则交大模型判定，图检索规则生成图检索语句分析，有参考规范先取规范；每条规则只在它的适用范围内（一般=文档章节）判断
when: 用户要"按某策略/规范评审这份文档（方案、需求、设计）"时
models: [bmm]
stages: [pick, rules, scope, judge, report]
scope:
  classes: [bmm:BusinessPolicy, bmm:OperativeBusinessRule, bmm:StructuralBusinessRule, bmm:BusinessRule]
  relations: [bmm:isDerivedFrom, bmm:isBasisFor, bmm:guides, bmm:hasEnforcementLevel]
tools: [rules_of_policy, graph_query, review_apply, doc_outline, get_document_info, list_knowledge_chunks,
        grep_chunks, wiki_search, wiki_read_page, audit_scan]
version: 1
---

# 文档评审（技能）

## 0. 输入（先问清三件事）

1. **文档**：要评审的**知识库文档或附件**（`knowledge_id` / 文件名）——用户常说"评审《XXX 方案》"。
2. **策略**：用哪条**业务策略**（`bmm:BusinessPolicy`）评审（如"应用架构设计原则""技术架构设计原则"）。
3. **目标库**：结论页写到哪个知识库（默认与文档同库）。

## 1. 流程（一条规则一轮，**不要全文通读**）

### 第 1 步：取规则清单（只读）
```
rules_of_policy(kb_id="…", policy="应用架构设计原则")
# 回执：rules[{slug, name, 级别(Strict/Advisory/Override), 适用范围(ruleScope), 实现方式(ruleImplementation),
#              参考规范(ruleReference), 原文依据}]
```
把清单（**条数 + 每条的适用范围/实现方式**）报给用户，一次确认要评哪些（默认全评）。

### 第 2 步：定位**每条规则的范围**（一般 = 文档章节）
`ruleScope` 就是范围线索（如 `技术方案/总体方案/应用架构`）。用：
```
doc_outline(kb_id, knowledge_id, budget_tokens=…)      # 按章节/切片取上下文（推荐，可控 token）
# 或 list_knowledge_chunks / grep_chunks 精确找某一节
```
> **只取该范围内的章节**；范围缺失时（`ruleScope` 为空）才退化为全文找关键词，并在结论里注明"范围未标注"。

### 第 3 步：按**实现方式**分流判定

| 实现方式（`ruleImplementation`） | 怎么做 |
|---|---|
| **LLM软规则** | **你自己判定**：读该范围章节原文 → 与规则语义逐条比对 → 给「符合/不符合/不适用/无法判定」+ **逐字证据**（引用章节原句）|
| **图检索** | 先把规则语义翻成**只读 Cypher**，再 `graph_query(cypher=…)` 取结论；结论里要附 **cypher 语句 + 命中行数 + 关键行**（可复核）|
| 其它/未填 | 默认按 **LLM软规则** 处理，并在结论里标注"实现方式未标注" |

**图检索的 Cypher 怎么产生**（要点）：
- 只用 `MATCH / OPTIONAL MATCH / WHERE / WITH / RETURN / ORDER BY / LIMIT`；**禁止写操作**（工具会拦）。
- 从本体出发：节点标签用 `BodhiOntClass/BodhiInstance`（本体投影）或业务页关系（`wiki_pages.slug`）；不确定时先 `ontology_types`。
- 例：*"每个子系统应有独立物理部署，避免共享数据库"* →
  ```cypher
  MATCH (s:BodhiInstance)-[:R]->(d:BodhiInstance) WHERE … RETURN s.name, d.name LIMIT 200
  ```
  （真正写法按你们图谱的实际投影标签来；**拿不准就先跑一条探针查询看标签有哪些**。）
- 图检索**取不到**证据时：结论写「无法判定（图中无相关数据）」，并给"需要在图谱里补什么"的建议。

### 第 4 步：有参考规范就先取规范
`ruleReference` 是规范线索（URL 或规范文档名）：
- **优先在知识库里找同名规范文档/附件**（`wiki_search` / `doc_outline`）→ 取到就用它做判据；
- 只给 URL 且取不到 → 结论里标「参考规范不可得（仅按规则原文判定）」，**不要编造规范内容**。

### 第 5 步：写评审结论（一页报告）
```
review_apply(kb_id="…", doc="<文档名/知识 id>", policy="<策略>",
             findings=[{rule:"<规则slug或名称>", verdict:"符合|不符合|不适用|无法判定",
                        severity:"high|medium|low", scope:"<范围>", evidence:"<逐字原文片段>",
                        how:"LLM软规则|图检索", cypher:"<图检索时附>", suggestion:"<改法>"}])
```
工具会写成**一页**（`slug=review/<文档>-<策略>`，版本化可回退）：正文 = 逐条结论表 + 每条证据 + 汇总（不符合 N 条 / 按严重度）。

## 2. 纪律

1. **逐条闭环**：一条规则一个结论，**不许合并成一段泛泛评价**；每条必须有 `evidence`（逐字，来自文档正文）或 `cypher`（可复核）。
2. **范围优先**：只在 `ruleScope` 指定的章节里判断；不要用全文印象下结论（用户口径）。
3. **不编造**：规范取不到就写"不可得"；图里查不到就写"无法判定"。
4. **结论可执行**：`suggestion` 要给"改成什么样"（一句话），不要只写"不符合"。
5. **不改文档**：评审**只读原文档**；结论写在**新的一页**上（`review_apply`）。
6. 收尾跑 `audit_scan`（结论页也要有原文依据 → F3 才绿）。
