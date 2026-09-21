---
id: domain_modeling
name: 领域知识建模
description: 把某一篇文档/资料按本体（EA / BMM）抽取成 wiki 知识页；可用 scope 收窄只抽某几类节点或某几条关系。
version: 0.1.0
when: 用户要求把**某一篇文档/资料**按某个本体抽取成 wiki 知识页（说法如：领域知识建模、知识提取、按 EA/BMM 建模、把《X》抽成知识）；也可用于"只抽某几类/某几条关系"的收窄建模。
models: [ea, bmm]
default_model: bmm
sources: [document]
stages: [extract]
tools: [ontology_types, extract_and_save, extract_status, list_pending_merges, resolve_pending_merge, wiki_search, wiki_read_page]
inputs:
  knowledge_id: 必填。本次要处理的那一篇（文档名或 id；<pinned_documents> 里的最准）
  scope: 可选。{classes:[...], relations:[...]}，从 ontology_types 收窄结果里挑
guard: 只处理用户明确点名的那一篇；不自己读全文、不自己判重复
---

# 领域知识建模

把**一篇文档**编译成知识：本体约束、类型/关系校验、相似度合并、幂等与目录重建**全部由 MCP 服务完成**，
你只负责"选对模型与范围 → 发起一次 → 如实汇报"。

## 步骤
1. **确认输入**：本次要处理的文档（用户点名的那一篇；别顺带处理别的文件）、目标知识库、本体模型。
2. **看类型面（可收窄）**：`ontology_types(model)`；若用户在对话里限定了范围（如"只抽任务与步骤"、
   "只要 taskHasStep 这类关系"），用 `ontology_types(model, focus="步骤")` 或
   `ontology_types(model, classes=[...], relations=[...])` 拿到收窄后的类/关系清单，并**把它作为 scope 传下去**。
3. **发起抽取（只发一次）**：`extract_and_save(model, kb_id, knowledge_id, scope=…)`。
   返回 `{"status":"started","job_id":…}`（后台跑 1–2 分钟）；**看到 started 不要重复调用**。
4. **轮询**：`extract_status(job_id=…)` 直到 `done` / `failed`。
5. **汇报**：`created / merged / pending / violations / unmatched / folders_synced` 数字 +
   被拒项名称与原因；`scope.filtered`（收窄过滤掉多少）也要说。

## 纪律
- **不要**自己读全文、不要自己做去重判断、不要自己列关系 —— 这些在服务端。
- 用户在对话里给了范围就必须传 `scope`：服务端**会在结果上再过一遍**，范围外的要素会进 `unmatched`
  （这是"说得清为什么没抽"的依据，不是错误）。
- `unmatched` 与 `violations` 必须原样列出（用户靠它判断是"本体缺件"还是"文档没写"）。
- 想只看已有知识（不新增）时用 `wiki_search` / `wiki_read_page`，不要用抽取工具。

## 输出格式
一句话说明：哪篇文档、哪个模型、范围（全量 or 收窄到哪些类/关系）；然后给数字；最后列被拒项与原因。
