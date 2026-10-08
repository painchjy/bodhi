---
id: domain_modeling
name: 领域知识建模
description: 按切片分批、可交互续跑地把一篇文档建模成 wiki 知识页（每轮落库并回执页面名称+编号；跨上下文关联先登记候选、确认后才写入）。
version: 0.2.0
when: 用户要求把**某一篇文档/资料**按某个本体抽取成 wiki 知识页（说法如：领域知识建模、知识提取、按 EA/BMM 建模、把《X》抽成知识）。
models: [bmm, agent]
default_model: bmm
sources: [document]
stages: [extract]
tools: [doc_outline, extract_state, save_knowledge, link_candidates, list_link_candidates, resolve_link_candidate, image_extract, ontology_types, skills, wiki_search, wiki_read_page, get_document_info, grep_chunks, list_knowledge_chunks]
inputs:
  knowledge_id: 必填。本次要处理的那一篇（文档名或 id；<pinned_documents> 里的最准）
  budget_tokens: 可选。本轮上下文的 token 上限（会话参数；默认取 BODHI_ROUND_BUDGET_TOKENS=8000）
guard: 一轮一批；只抽本批文本支撑的内容；跨批/跨库目标先用向量召回再登记候选，用户确认后才写关系；不再用异步一次性抽取；**知识来源＝智能体会话** → 落库带会话身份、每条依据带会话定位（见「来源登记」）
---

# 领域知识建模（分批交互版）

把一篇文档建模成 wiki 知识页。**不再一次性异步抽取**：按切片大小与父子关系把文档切成
"大小合适的上下文"，**一轮读一批、落一批**，每轮回执都带**页面名称 + 会话编号**，
所以整个会话始终握着这篇文档的索引（不必复述全文）。

> 内网算力有限：**一次交互的输入 token 上限就是会话参数**（`budget_tokens`）。
> 某轮超时/被截断 → **把阈值调小**重跑，父块会自动按子块细分。

## 写库前置（硬规则，2026-09-22）

**写操作必须唯一确定目标知识库**：会话可能同时绑定多个库（查询/检索可以多库，**保存只能落一个**）。

1. 把会话绑定的库清单（`<bound_knowledge_bases>` 里的 `id`）原样传给 `kb_ids`；
2. `kb_ids` > 1 且用户没点名写哪个库 → 服务端**拒绝写**并回 `need_kb_selection: true` + 候选清单：
   **必须问用户"写进哪一个库"**，拿到答复后带 `kb_id=<完整 uuid 或精确库名>` 重跑；
3. `kb_id` 只认**完整 uuid**或**精确库名**；uuid 前缀 / 名称包含（模糊）会回 `need_kb_confirm: true`
   → 改传完整 uuid，或在用户确认后带 `confirm_kb_match=true` 重跑；
4. 库名不要含糊（只写"企业"这种）——命中多个会被拒，命中一个也要确认。

> **相似度与关系解析都只在本库内**：目标节点若只存在于别的知识库，回执会出现
> `cross_kb_same_name` 并把它计入 `violations`（**绝不跨库合并**——同名不代表同义）。
> 要连到那个节点，先在本库建立它，或改用本库内的等价节点。
> **跨库引用前必须先查（2026-09-28 新增只读工具）**：`context_lookup(slug|q)` ——
> 它会返回该页在**各库的同名页**、已挂的企业标准概念（`page_metadata.same_as`）、ACL 映射、
> 以及引用它的页；若回执里有 `same_name_no_decision`（跨库同名但**没有任何裁决**），
> **停下来先请用户裁决**（同义→挂「企业共享概念模型」里的概念页；异义→登记 ACL 映射），
> 不要凭 slug 相同就假定同义。全库体检用 `context_scan`（只读，出建议 + ticket）。

## 来源登记（**知识来源＝智能体会话**｜2026-10-01）

本技能产出的**每一条知识都必须能回到会话上下文**（会话页由 `session_provenance` 技能负责建）：

1. **先有会话页**：落库前确认本会话已有 `bmm:KnowledgeSession` 页（起始页 `session/<会话编号>`；上下文过长**且**一组高相关知识已确认更新完成时开分页 `session/<会话编号>/p<序号>`）。没有 → 按 `session_provenance` 建；
2. **落库带会话身份**：`save_knowledge(..., session_no="<运行时给的会话编号>", session={knowledge_id, round_no, cursor, next_cursor, doc_title})` —— `session_no` **必传**（服务端据此取「会话选择的智能体」注 `agentName`、并把 `bmm:sourceSession` 自动挂到本次所有页）；并**把会话编号/分页序号写进页面「原文依据」小节**；
3. **每条依据 = `source_text`（原句）+ 会话定位**（分页序号 + 轮次/段落）—— 只写原句不写定位 → `audit_scan` 的 F3 可能仍绿，但**溯源链是断的**（评审问题将追不到出处）。**定位必须落成两个地方**：
   - **节点属性 `bmm:sourceLocator`**（`nodes[].attributes.sourceLocator`，如 `"session/S-20261003-01/p2#轮3/段5"`）→ 这样**来源是可查询的数据属性**；
   - 页面「## 原文依据」的来源行（工具会把 locator 渲染进去）。
   > 示例：`attributes: {"sourceLocator": "session/S-20261003-01/p2#轮3/段5"}`；`source_text` 仍必填（原句）。
4. **同库约束**：会话分页与其产出的知识**必须在同一个知识库**；**换库必须新建分页**（不是复制）；会话起始页各库各一份、**以初始库为权威**。

## 固定动作（每轮）

1. **对齐进度**：`extract_state(kb_id, knowledge_id)`。
   - `pages` 非空 = 这篇文档做过几轮 → 用它的 `cursor` 续跑，**不要从头重抽**；
   - `pending_links` 非空 = 上一轮还有候选关联没裁决 → 先问用户确认/驳回。
2. **取本批上下文**：`doc_outline(kb_id, knowledge_id, budget_tokens, cursor)`。
   - 回执 `batches[0].text` 就是**本批正文**（父块正文；父块超预算时自动拆成子块，`split_parents` 可见）；
   - `total_units / est_total_tokens / est_total_batches` 让你先告诉用户"这篇大概要做几批"。
3. **只从本批抽知识**：节点（类）与关系（对象属性）必须能由本批文本支撑；
   每条节点给 `source_text`（**原句**，用于定位切片）。
4. **落库**：`save_knowledge(stage="graph", mode="dry_run",
- 落库时带 `context="domain_modeling"`（决定页面文案：`## 原文依据` 会写「领域建模：无原文片段」而不是「概要设计…」；报告页标题/分类也按技能走）。
   kb_ids=[…会话绑定的库 id…])` 先预览 → 用户确认后 `mode="apply"`，
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
- **关系只走图谱边（2026-10-05）**：`save_knowledge(stage="graph")` 的 `edges` 直落 Neo4j 图边；
  wiki 的 `in_links`/`out_links` 已废弃恒空，**不要**重算、不要建议「重算入边索引」（巡检 A2 已移除）。
- **图里才有信息 → 先 `image_extract`（2026-10-08）**：`doc_outline` 的「图/表位置」里若是**图**（部署图/拓扑/截图），
  先 `image_extract(kb_id, knowledge_id, figure_no?)` 取文档内嵌图，由你的**多模态能力**按 `multimodal_extraction`
  技能契约读图（契约 v0 + 门禁 1–4）；图里才有的知识**不得凭文字臆断**。

## 汇报（每轮）

一句话：第几轮 / 本批范围（单元数＋字数＋估 token）/ 用了哪几个本体类；
然后给数字：`created`（含 `no`）/`merged`/`pending`/`violations`/`unmatched`；
最后：`session.next_cursor` 与"还剩几批"，以及**待确认候选关联**清单（如有）。
全部批次跑完时汇总：轮数、页面总数、按类型分布、待确认候选数、以及仍未闭合的缺口。
