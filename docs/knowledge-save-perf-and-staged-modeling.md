# 落库（knowledge_save）写入链路与性能 —— 当前实现（2026-10-10）

> 本文只写**当前版本的行为与实测**。历史中间态、方案权衡、排期与未采用的提议（如"图写完→PG 异步派生"）
> 已删除 —— 需要考古请看 `git log` 与提交信息（每个阶段都有实测数字）。
> 契约见 `docs/knowledge-write-contract.md`；KPI/开关见 `deploy/delivery/MCP-SERVER.md`。

## 1 写入链路（唯一路径）

`save_knowledge` → `save_elements`（构建页）→ `ke_pages.write_knowledge_batch`：

| 步 | 动作 | 实现 |
|---|---|---|
| ① | 图节点 | `ke_graph.upsert_nodes_batch`（一条 `UNWIND`） |
| ② | PG 页 | `_bulk_write_pages`（新建：一条多行 `INSERT`；更新：一条事务 = 快照 `INSERT..SELECT` + `UPDATE..FROM(VALUES)`） |
| ③ | 图边 + 节点兜底 | `ke_graph.add_edges_batch`（按关系类型分组 `UNWIND`）+ `ensure_instances` |
| ④ | 目录树 | 默认**延迟**（`BODHI_DEFER_MAINTENANCE=1`；前端 `/bodhi/folders/refresh` 按需刷新） |
| ⑤ | 正文 strip | `ke_graph.strip_wiki_pages`（整批一次读 + 一条 `UPDATE..FROM(VALUES)`） |

**不要回退的设计**：

- **无 wiki→图 投影**：关系只由图边承载（正文不再有「本体关系」小节），图一律由写路径直写；
  存量重建只用 `rebuild_kb_graph`（**保留边**、只重建节点/属性）。
- **无相似度合并**（`BODHI_SIMILARITY_MERGE` 开关**已删除**，2026-10-10 永久取消）：一律按 `(kb, slug)`
  幂等 upsert；`merged` 含义 = "同 slug 覆盖"。相似知识（库内近重复 / 跨库同名）的裁决只在**巡检**做
  （`audit_scan` 的 S1/S2）；`pick_match` / `merge_content` / 待确认合并页与两个裁决工具均已删除。
- **文档类必须有正文**：本体 `render_mode(page_type)=document` 的节点（设计单/建议/评测/安装指引/报告…）
  在 graph 阶段**未带 `wiki_content` 一律跳过**（回执 `skipped_document_nodes` + note），
  应改用 `stage="report"` 落整篇正文（同 slug 幂等覆盖）。
- **校验类读走图**（`BODHI_VALIDATE_FROM_GRAPH=1`）：关系 domain/range 用图上的 `page_type`
  （1 次 Neo4j 替掉每条边 2 次 psql），图里没有才回落 PG。
- **本体元数据进程级 memo**（`class_meta` / `ancestors` / `descendants` / `relation_type_map` /
  `target_closure` / `top_ancestor`，按 `index_stamp()` 失效）→ 热进程本体读 0 次 Neo4j。

## 2 观测：怎么看"这次调用花了什么"

- `logs/mcp_calls_YYYYMMDD.log` 的 `DONE` 行：`耗时` / `io=pg:N次/Xms，neo4j:N次/Xms |合计… |墙钟…`
  / `|pg调用者:…|neo4j调用者:…`（**按类型归因**，谁在打库/打图一眼可见）/ `|阶段:…`。
  **`result=` 只保留 500 字摘要**（想看全量回执：让调用方打印，或复测工具直接读返回值）。
- 入参全文：`logs/args/<时间戳>-<工具名>.json`（与 DONE 行同序，便于配对）。
- 复测：`python3 tools/diag/_replay_sk.py <args.json>`（**默认强制 `dry_run`**，加 `--apply` 才写）。

## 3 实测（同参 33 nodes / 27 edges；本机）

| 阶段 | 墙钟 | pg | neo4j |
|---|---|---|---|
| 改造前（旧进程，apply） | 31351 ms | 190 次 | 221 次 |
| dry_run（当前，热进程） | **2.2 s** | **11 次** | **0 次** |
| apply（当前，批量写后） | **3 s 档** | ~15 次 | ~30 次 |

其它实测：本体校验读搬图后 dry_run `neo4j 64 → 0`（热）；孤儿图节点清理 **186/378**（378 → 192）。

## 4 开关（`deploy/delivery/payload/mcp/.env.example` 同步）

| 变量 | 默认 | 说明 |
|---|---|---|
| ~~`BODHI_SIMILARITY_MERGE`~~ | — | **已删除**（相似度合并永久取消）：一律 `(kb, slug)` 幂等 upsert |
| `BODHI_ONTOLOGY_KB_ID` / `BODHI_CONCEPT_KB_ID` | env | 治理库（唯一权威；工具回执回 `kb_note`） |
| `BODHI_VALIDATE_FROM_GRAPH` | `1` | 关系 domain/range 的类型读：图优先（PG 兜底） |
| `BODHI_DEFER_MAINTENANCE` | `1` | 目录树/CRUD 矩阵延迟到前端按需刷新 |
| `BODHI_DEBUG_ENGINE` | `0` | 内核 spec 构建的内部打印（排障用） |

## 5 已删除 / 已废弃（考古索引）

- `BODHI_SAVE_ENGINE` 与 legacy 单事务写（拼 `statements` + 一个事务）—— 2026-10-10 删；
  落库只有内核一条路，`id_strategy` 由内核回执回填。
- wiki→图 投影：`ke_graph.project_page` / `project_page_from_content` / `strip_wiki_page` /
  `delete_kb_graph` / `_edges_of` 及 `ke_pages` 两处调用。
- 逐页 psql 家族：`sql_insert_page` / `sql_update_page` / `_resolve_page_id` / `_page_id_of_slug` /
  `_page_id_owner` / `_prior_version` / `_prime_page_ids` / `_prime_prior_versions`。
- 相似度合并分支（并页 / 待确认页）默认停用（`pending` 恒空）。
- 判「新建 / 覆盖」改用**循环前的库里快照**（`fetch_existing_pages` 那次预读）；副作用：只剩
  "软删旧行"的 slug 从 `revived` 改判 `created`（内核 upsert 会复活它）。

## 6 排障（当前版本）

| 现象 | 先看什么 |
|---|---|
| 改了没生效 | `systemctl show <unit> -p ExecMainStartTimestamp` 必须**晚于**源文件 mtime（不重启=跑旧字节码） |
| 回执 `failed>0` | 用**同一份载荷重跑**即续作（节点按 `(kb,slug)`、边按 `(kb,源,类型,目标)` 幂等） |
| 图上有幽灵节点（库已删） | `audit_scan` 看 `graph_orphans`；`audit_purge(kinds="orphan_graph_nodes")` 一步清 |
| 设计单/报告类"没落库" | 看回执 `skipped_document_nodes`：该类必须在 `stage="report"` 带 `wiki_content` |
| 边没进图 | 回执 `graph:{batched,edges_written,edge_errors}`；`edge_errors` 会给出拒绝原因 |
