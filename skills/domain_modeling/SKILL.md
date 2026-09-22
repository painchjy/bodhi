---
id: domain_modeling
name: 领域知识建模
description: 按切片分批、可交互续跑地把一篇文档建模成 wiki 知识页（每轮落库并回执页面名称+编号；跨上下文关联先登记候选、确认后才写入）。
version: 0.2.0
when: 用户要求把**某一篇文档/资料**按某个本体抽取成 wiki 知识页（说法如：领域知识建模、知识提取、按 EA/BMM 建模、把《X》抽成知识）。
models: [ea, bmm]
default_model: bmm
sources: [document]
stages: [extract]
tools: [doc_outline, extract_state, save_knowledge, link_candidates, list_link_candidates, resolve_link_candidate, ontology_types, skills, wiki_search, wiki_read_page, get_document_info, grep_chunks, list_knowledge_chunks]
inputs:
  knowledge_id: 必填。本次要处理的那一篇（文档名或 id；<pinned_documents> 里的最准）
  budget_tokens: 可选。本轮上下文的 token 上限（会话参数；默认取 BODHI_ROUND_BUDGET_TOKENS=8000）
guard: 一轮一批；只抽本批文本支撑的内容；跨批/跨库目标先用向量召回再登记候选，用户确认后才写关系；不再用异步一次性抽取
---

# 领域知识建模（分批交互版）

把一篇文档建模成 wiki 知识页。**不再一次性异步抽取**：按切片大小与父子关系把文档切成
"大小合适的上下文"，**一轮读一批、落一批**，每轮回执都带**页面名称 + 会话编号**，
所以整个会话始终握着这篇文档的索引（不必复述全文）。

> 内网算力有限：**一次交互的输入 token 上限就是会话参数**（`budget_tokens`）。
> 某轮超时/被截断 → **把阈值调小**重跑，父块会自动按子块细分。

## 固定动作（每轮）

1. **对齐进度**：`extract_state(kb_id, knowledge_id)`。
   - `pages` 非空 = 这篇文档做过几轮 → 用它的 `cursor` 续跑，**不要从头重抽**；
   - `pending_links` 非空 = 上一轮还有候选关联没裁决 → 先问用户确认/驳回。
2. **取本批上下文**：`doc_outline(kb_id, knowledge_id, budget_tokens, cursor)`。
   - 回执 `batches[0].text` 就是**本批正文**（父块正文；父块超预算时自动拆成子块，`split_parents` 可见）；
   - `total_units / est_total_tokens / est_total_batches` 让你先告诉用户"这篇大概要做几批"。
3. **只从本批抽知识**：节点（类）与关系（对象属性）必须能由本批文本支撑；
   每条节点给 `source_text`（**原句**，用于定位切片）。
4. **落库**：`save_knowledge(stage="graph", mode="dry_run")` 先预览 → 用户确认后 `mode="apply"`，
   并带上 **`session`**：
   ```json
   {"session": {"knowledge_id": "<id>", "round_no": 1, "budget_tokens": 600,
                "cursor": 0, "next_cursor": 8, "doc_title": "<标题>"}}
   ```
   - 回执 `created[].no` = 本会话**页面编号**；`session.next_cursor` = 下一批游标；
     `session.pages_total` = 累计页数（这些就是"整个文档不缺失"的索引）。
5. **跨上下文/跨库的关联**（本批里出现、但目标不在本批/本库可见范围）：
   - 先用向量检索（`wiki_search`，必要时 `wiki_read_page` 确认语义）找候选页；
   - `link_candidates(kb_id, source_slug, relation, candidates=[{target_slug, similarity, reason}],
     knowledge_id, round_no)` 登记候选 —— **只登记，不写库**；
   - **把候选清单交给用户逐条确认**，再 `resolve_link_candidate(candidate_id, action="confirm"|"reject")`；
     `target_exists=false` 的候选**不能确认**（先补页或换目标）。
6. **循环**：重复 2→5（`cursor = session.next_cursor`），直到 `doc_outline.done=true`。

## 纪律

- **不要指望「整篇一次性抽取」**：该工具已于 2026-09-22 从 MCP 服务端**移除**（原文留痕
  `tools/ontology-mcp/archive/async_extract_retired_2026-09-22.py.txt`）—— 它把整篇塞进一次调用，内网必然超时；
  请按本技能**分批**走 `doc_outline` → `save_knowledge(stage="graph", session=…)` → `extract_state`。
- **不编 slug、不猜目标**：引用的页一律用检索结果里的 `slug`；找不到就登记候选或先建页。
- `nodes[].attributes` / `edges[].properties` 的键必须是 `skills(...)` 给的本体面里**声明过的数据属性**
  （未声明的会被回报为 `violations`，不拦写入，但要告诉用户"需要补本体"）。
- `violations` / `unmatched` / `pending` 一律原样列出（用户靠它判断是本体缺件还是资料缺失）。
- **同一篇文档尽量固定 `budget_tokens` 跑完**；中途改预算则 `cursor` 失效 → 从 `cursor=0` 重跑
  （落库幂等：同 slug 命中即合并更新，不会重复建页）。
- 一轮一批：**不要**在一次回答里连读多批再拼大表 —— 用户看不见进度、也没法中途改阈值。

## 汇报（每轮）

一句话：第几轮 / 本批范围（单元数＋字数＋估 token）/ 用了哪几个本体类；
然后给数字：`created`（含 `no`）/`merged`/`pending`/`violations`/`unmatched`；
最后：`session.next_cursor` 与"还剩几批"，以及**待确认候选关联**清单（如有）。
全部批次跑完时汇总：轮数、页面总数、按类型分布、待确认候选数、以及仍未闭合的缺口。
