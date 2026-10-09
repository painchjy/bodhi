# knowledge_save 性能改造 + 阶段化建模（方案 → P0 实施）

> 2026-10-09。触发：内网「技术方案评审智能体」需求建模调一次 `knowledge_save`
> **超过 2 分钟被客户端超时切断，且没有任何调用记录**。本文 = 诊断 + 方案 + P0 实施记录。
> 相关：`docs/knowledge-write-contract.md`（写入契约）、`docs/context-mapping-plan.md`。

---

## 1. 实测基线（本机，只读原语，2026-10-09）

| 原语 | 实测 | 说明 |
|---|---|---|
| `ke_db.psql()` 1 次往返 | **271.7 ms** | 每次都是**起子进程**（`docker exec` / `psql`）|
| `ke_neo4j.query()` 1 次往返 | **74.4 ms** | HTTP 事务端点 |

**结论：瓶颈不是计算，是往返次数。** 内网 RTT 更大、没有本地 docker exec 优化 → 只会更贵。

---

## 2. 诊断（都带证据位置）

| # | 现象 | 根因 | 位置 |
|---|---|---|---|
| 1 | 一次 save > 2 分钟 | 40 节点 + 40 关系的往返量 **~150-250 次** | 见 §3 拆解 |
| 2 | **最大的块**：目录树 | `upsert_page` 在**每一页**末尾调 `_sync_folders(kb_id)` → **全量重建整库目录树**，还要**起子进程**（`sync_folders.py`）。40 页 = 40 次全库重建 + 40 次 Python 启动 | `ke_pages.py:295/321`（改前）|
| 3 | 每条关系 1.46 s | `add_edge` = 2×`_page_type` + 2×`_ensure_instance`(各含 1 PG + 1 Neo4j) + dup 检查 + MERGE + `relations_of` = **4 PG + 5 Neo4j** | `ke_graph.py:316-337`（改前）|
| 4 | **整个 payload 一个事务** | `save_elements` 把全部 INSERT/UPDATE 拼成**一个** `BEGIN;…COMMIT;`：任何一条抖动 → **全批回滚、无部分成功** | `server.py:1497-1498` |
| 5 | **索引列表先出现、图谱滞后** | `save_knowledge` 走的 `save_elements` **根本不写图**：关系只被渲染进页面正文；Neo4j 实例边只由**事后**的 `link_source_session` + 投影补 | `save_elements` 全篇无 `add_edge`；`server.py:2294-2302`、`2330-2339` |
| 6 | 超时=**零记录** | `_log_tool_call` 在 handler **返回后**才写日志，且 args 被 `[:300]` 截断 | `server.py:4193-4234` |
| 7 | 每页会话边 2 往返 | `link_source_session` 逐页 `_ensure_instance`×2 + MERGE；40 页 ≈ 160 次往返 | `server.py:2056-2071`（改前）|
| 8 | Neo4j 没索引 | `_ensure_index()` 只在 `rebuild_kb_graph` 里调 → 日常 `MERGE` 走**标签全扫**（实测 `ensure_instances` 4 行要 **3.2 s**）| `ke_graph.py:20`（改前）|

---

## 3. 开销拆解（40 节点 + 40 关系，改前）

| 阶段 | 往返量级 | 估算 |
|---|---|---|
| 会话身份 + engine/index | 5-10 | 1-3 s |
| `fetch_existing_pages`（拉全库页） | 1（大） | 0.3-1 s |
| 相似度匹配（本地 CPU，载荷×存量） | 0 | 0.1-0.5 s |
| 一次大事务（~80 条语句） | 1 | 0.5-3 s |
| `write_check` 对账 | 1 | 0.3 s |
| **`sync_folders` × 40 页** | 数十~上百 + 40 子进程 | **10-60 s** ⚠️ |
| `refresh_crud_matrix`（每服务页） | 5-8 × N | 5-30 s |
| `link_source_session`（每页 2 往返） | ~80 | 20-30 s |
| retag/retract/回执 | 若干 | 2-10 s |
| **合计** | **~150-250** | **≈1.5-3 分钟** |

→ 与内网「>2 分钟超时」吻合。

---

## 4. 方案（用户 2026-10-09 确认）

1. **`knowledge_save` 彻底改走新内核**（`ke_pages.write_knowledge_batch`）；`save_elements` 降级为仅供旧抽取路径。
2. **`sync_folders` 增量/按需**：不再每次落库重建整库目录树；交给前端 `/bodhi/folders/refresh`（接口已存在）。
3. **建模阶段表**（S0-S4 × 允许的类 × 范围建议）由 `artifacts/ontology_index.json` **生成草案**再人工过一遍。
4. **续作语义**先**模拟验证可行性**再定稿。
5. **属性别名表**：从现有本体模型自动挖「别名 → 规范名」初稿；
   **并且取消正文里数据属性的 150 字截断**（已完成，见 P0）。

---

## 5. P0 实施记录（已完成 · 本次提交）

### 5.1 `ke_graph.py`
| 改动 | 效果 |
|---|---|
| 新增 `page_info_map(kb_id, slugs)` | **一次** SQL 取多 slug 的 `{pt,title,tenant}` → 替代每条边 2 次 `_page_type` |
| 新增 `ensure_instances(kb_id, rows)` | **一次 UNWIND MERGE** 批量确保节点 → 替代逐节点 `_ensure_instance` |
| `_ensure_index()` 加**进程级缓存** `_INDEX_READY`，并在 `ensure_instances` 前调用 | 修掉「日常 MERGE 标签全扫」 |
| `add_edge(..., ctx=None)` | `ctx={"info","nodes_ensured","skip_relations"}` → 每条边从 **4 PG + 5 Neo4j** 降到 **1-2 Neo4j**；不传 `ctx` 行为与旧版逐字一致（兼容） |

**实测（本机）**：
```
page_info_map    4 slug → 4 条    267 ms   (旧: 8×_page_type ≈ 2.2 s)
ensure_instances 冷(含建索引)     106 ms   (改前 3169 ms —— 建索引后 30×)
ensure_instances 热                 8 ms   (旧: 8×_ensure_instance ≈ 2.8 s → 350×)
add_edge(ctx) 目标不存在 → ValueError（ctx 校验路径正确，0 写入）
```

### 5.2 `ke_pages.py`
| 改动 | 说明 |
|---|---|
| `upsert_page(..., sync_folders=True)` | `False` 时**不再逐页全量重建目录树**（改前的最大开销）；两个调用点都加了守卫 |
| `write_knowledge(..., sync_folders=True)` | 透传给 `upsert_page`；批量路径传 `False` |
| `write_knowledge_batch(...)` **重写** | ①批量预读 → ②逐节点小事务（`sync_folders=False`）→ ③`ensure_instances` 一次 + 逐边 `add_edge(ctx)` → ④**整批一次**目录；返回新增 `applied_count / failed / edge_errors / folders / resume_hint` |
| `ATTR_RENDER_LIMIT = 0` | **取消 150 字截断**（用户口径）；`0` = 不截断 |

### 5.3 `server.py`
| 改动 | 说明 |
|---|---|
| `link_source_session` **批量化** | 一次 `page_info_map` + 一次 `ensure_instances` + **一条 `UNWIND` 建全部 `bmm:sourceSession` 边**（40 页 ~160 次往返 → **3 次**）|

### 5.4 验收（已跑的冒烟）
```
SYNTAX_OK（ke_pages / ke_graph / server 三文件）
batch dry_run -> {"applied": false, "count": 1, "edges_written": 0, "failed": [], "folders": null}
属性正文长度 = 445  (>150 证明截断已取消)
```
**预期**：40 节点 + 40 关系 ≈ 40 次节点往返 + 40~80 次边往返 ≈ **15-25 s（本机）**，内网 < 90 s；
失败只丢**当前那一条**，同 payload 重跑即续作。

---

## 5.5 P0-b 实施记录（已完成 · 本次提交 `d254950`）

> 重要修正：`save_elements` **不走 `upsert_page`**（它自己拼一个大 `BEGIN…COMMIT`），
> 所以 §5.2 的「逐页目录重建」修复**没有**惠及 `save_knowledge`；
> 它的目录同步是**每次调用一次**（但仍是大库上 10-60 s 的一块）。
> 因此 P0-b 针对 `save_knowledge` 的真实瓶颈下手。

### 改了什么
| 位置 | 改动 | 效果 |
|---|---|---|
| `save_elements(..., skip_folders, skip_crud)` | 新增两个开关；目录树全量重建、CRUD 矩阵刷新**可跳过** | 落库不再同步等 10-60 s 的重型维护 |
| `save_knowledge` | `defer_maintenance`（默认开，`BODHI_DEFER_MAINTENANCE=0` 可关）→ 两条分支都传 `skip_folders/skip_crud=defer_maintenance`；回执加 `maintenance: deferred/inline` 与 `folders_synced: deferred（按需刷新）` | 前端进 wiki 会调 `/bodhi/folders/refresh` 按需刷新 |
| **新增 `ke_pages.write_relations_batch(kb_id, pairs)`** | 一次 `page_info_map` + 一次 `ensure_instances` + 逐条 `add_edge(ctx)`，**幂等可重跑** | **修掉「本体图谱滞后」**：旧实现只把关系渲染进正文、**不写图** |
| `save_knowledge(stage="graph")` | `mode=apply` 时用 `created/merged` 的 **name→真实 slug** 映射批量写边，回执 `graph_edges: {ok,written,total,errors}` | 关系**当场**进图，不必等事后投影 |
| `_log_tool_call` keys | 补 `graph_edges / edge_errors / folders_synced / maintenance / resume_hint` | 内网可自证「边有没有真写进图」 |

### 验收（已跑）
```
SYNTAX_OK（ke_pages / server）
write_relations_batch([])                 -> {'ok': True, 'written': 0, 'total': 0, 'errors': []}
write_relations_batch(不存在的节点)        -> ok=True written=0 total=1 errors=[{source,type,target_slug,...}]  ← 不抛、不写
save_elements(skip_folders/skip_crud)      -> True True
save_knowledge 形参未变                    -> ['kb_id','stage','model','report','nodes']（向后兼容）
```

### 用户决策
- **接受**「按 slug 幂等 upsert」，**去掉 similarity 相似度合并 / `pending` 待确认页**（用户 2026-10-09 选 1）。

---

## 6. 未完项（按序）

| 阶段 | 内容 | 验收 |
|---|---|---|
| ~~P0-b~~ ✅ | **已完成**（见 §5.5）：热路径瘦身（目录/CRUD 可延迟）+ 关系**批量写进图** | §5.5 验收已过 |
| **P0-c** | `save_knowledge` **写入段接新内核**：payload → spec 适配器 + **设计页正文渲染对齐**（服务页 `purpose/inputs/outputs/assertions/## 溯源`）+ 回执形状兼容 | 页面内容**不回归**；40 节点 < 90 s（内网）|
| **P1** | 观测性：`_log_tool_call` 改**两段写**（进入即写工具+完整 args 落 `logs/args/*.json`，返回补 result）+ 写库进度行 | ✅ **已完成**（见 §8）|
| **P2** | 阶段表共享知识页（S0-S4 × 允许类 × 范围）+ `knowledge_save` **阶段白名单硬门禁** | 传越界类 → `violation` + 可选类清单 |
| **P3** | 属性名规范化（去前缀/别名表/下划线-斜杠归一）+ 未知键进 `violations` + 回执「已纠正」清单 | 传 `under_score`/`a/b` → 自动规范 |
| **P4** | **合并** `ops/design/modeler` 三个自建智能体 → 一个「企业建模智能体」+ 技能提示改写 | 一轮任务一个智能体、阶段推进可见 |
| **P5** | `BODHI_DB_PASSWORD` 统一（已提交 `5a82a17`）随下次发布包生效 | 内网 `.env` 写 `BODHI_DB_PASSWORD` 即生效 |

### 6.1 待确认
- ~~相似度合并 / `pending`~~ → **已定（用户 2026-10-09 选 1）**：接受「按 slug 幂等 upsert」，去掉 similarity 合并与 `pending` 页。
- **续作语义**：先做**模拟验证**（造 N 条、中途 kill、重跑看是否只补缺）再定稿提示词约定。
- **P0-c 的正文渲染**：设计页正文当前由 `save_elements` 的 `sql_insert_page/sql_update_page` 渲染
  （含 `purpose/inputs/outputs/assertions/## 溯源` 等），新内核 `mode="entity"` 渲染的是
  「定义段 + 属性段」，**两者不等价** → P0-c 需要新增 `mode="design"` 渲染器保持内容不回归。

---

## 7. 一句话总结

> 2 分钟超时**不是模型慢、也不是数据库慢**，是**往返次数**：40 页 × 每页一次全量目录重建（还起子进程）
> + 每条边 4 次 psql 往返。P0 把这些压成「批量预读 + 逐条小事务 + 每条边 1 次 Neo4j + 整批一次目录」。

---

## 8. P1 实施记录（已完成 · 提交 `c199675`）

### 8.1 新增 `tools/ke-core/ke_stats.py`
- `ContextVar` 承载「当前这次工具调用」的计数桶 → `ThreadingHTTPServer` **多线程天然隔离**；
- `begin() / record() / mark(阶段) / snapshot() / reset()`；**未开启统计时零开销**（一次 `get()`）；
- 预留 `marks`（阶段标记）→ 超时时能看出卡在哪个阶段。

### 8.2 埋点（唯一的两个 IO 出口）
| 出口 | 埋点 | 说明 |
|---|---|---|
| `ke_db.psql()` | `kind="pg"` | **子进程启动时间计入**（272 ms 主要就是它）|
| `ke_neo4j.query()` | `kind="neo4j"` | 含 HTTP 往返与解析 |
两者都在"未开启统计"时**立即返回**，不影响既有路径。

### 8.3 两段写日志（修「超时零记录」）
| 时机 | 落什么 |
|---|---|
| **进入工具前** | `logs/args/<时间>-<工具>.json`（**完整入参**，不再 `[:300]` 截断）+ 主日志一行 `BEGIN args_file=…` |
| **返回后** | 主日志一行 `DONE`（或 `ERR`）：耗时 / **`io=pg:12次/3264ms，neo4j:40次/2976ms \|合计… \|墙钟… \|阶段:…`** / 结果摘要 |

### 8.4 验收（已跑）
```
SYNTAX_OK（ke_stats / ke_db / ke_neo4j / server）
snapshot = {"calls":2,"ms":917.3,"detail":{"pg":{"n":1,"ms":512.6},"neo4j":{"n":1,"ms":404.7}},"wall_ms":918.3}
reset 后 = None
日志 BEGIN 行： … selftest  BEGIN  args_file=args/20261009-213422-815899-selftest.json  args={…}
日志 DONE  行： … selftest  1234ms  DONE  io=pg:2次/778ms，neo4j:0次/0ms |合计2次/778ms |墙钟778ms |阶段:326ms:nodes 3/3  args_file=…
args 落盘 = True
```

### 8.5 用法（内网排错）
```bash
tail -f logs/mcp_calls_$(date +%Y%m%d).log        # 看 BEGIN/DONE + io= 统计
ls -t logs/args/ | head                            # 拿完整入参（可重放/续作）
```
**从此"超时"不再是一个黑盒**：至少能看到"调了哪个工具、传了什么、PG/Neo4j 各打了多少次、卡在第几阶段"。

---

## 9. 用户提议评估：**图写完 → PG 异步派生**（"PG 是派生的历史版本记录，Neo4j 是最新版"）

> 用户 2026-10-09 提议：既然 PG 是派生的，图写完就让 **PG 异步派生**（和目录一样），
> 不一致可通过运维同步；PG 幂等即使重放也不产生新版本；Neo4j 是最新版本，PG 是派生 + 历史版本记录。
> 并问：**异步之后，能否事后查询 PG 的操作情况和时长？**

### 9.1 结论：**方向正确，且 P1 已经为"事后可查"打好地基**；但落地前必须先解 4 个问题
**"能否事后查询"→ 能，而且有两种粒度**：
1. **日志粒度（现在就可用 P1）**：派生消费者自己 `ke_stats.begin()/snapshot()` → 同一套 `io=pg:n次/xms`
   落 `logs/derive_*.log`（或主日志 `phase=DERIVE` 行）；`logs/args/` 里留派生的输入清单。
2. **数据库粒度（更推荐）**：建 PG 表 **`bodhi_derive_jobs`**（`id, kb_id, batch_id, slug, phase,
   attempt, pg_n, pg_ms, neo4j_n, neo4j_ms, ok, error, version_before, version_after, started_at, ended_at`）
   → **运维可直接 SQL 查"某批派生打了多少次 PG/花了多久/哪些失败"**，并天然支持**重试/对账/断点续作**。
   （比纯日志更耐重放，也是你问的"事后查询 PG 操作情况和时长"的最佳答案。）

### 9.2 必须先解决的 4 个问题（否则引入新坑）
| # | 问题 | 说明 / 解法 |
|---|---|---|
| 1 | **"重放不涨版本"需要新判据** | 现在 `upsert_page` 是"存在就 `version+1`"。要支持幂等重放，得改成**内容哈希比对**：渲染结果 hash == 已存 hash → **跳过**（不写、不 +1、不写 revision）；hash 变了才写新版本。这样才区分得开「同一份图再派生」（跳过）与「图变了」（新版本）。|
| 2 | **哪一层能异步** | `wiki_pages`（+`wiki_page_revisions`）是我们的；**WeKnora 的 `chunks`/向量索引不由我们写** → 异步只覆盖前者。要明确边界，别指望"全链路异步"。|
| 3 | **读一致性（最关键）** | 异步期会变成**"图有、页无"**（与你现在观察到的"页先出现、图滞后"**正好反过来**）。影响：`page_get`、`add_edge` 的 `_page_type`、`link_source_session` 的 slugs 校验都按 PG 读 → 必须加**图优先回落**：`_page_type` 读不到 PG 时回落读 Neo4j 的 `n.page_type`。同时前端要接受"图谱立即可见、页面稍后"（图谱直接读 Neo4j，天然满足）。|
| 4 | **队列持久化 + 对账** | 队列必须落库（进程重启不丢）；并加巡检项 **`derive_lag`**（比较 Neo4j 实例 vs `wiki_pages`：列出"图有页无/页有图无"）→ 复用 `audit_scan` 框架，可一键重放。|

### 9.3 但**先别急着异步** —— 有个更划算的顺序
**图侧还没批量化**：现在每节点 1 次 + 每边 1-2 次 Neo4j 往返 → 40 节点+40 边 ≈ **80-120 次往返**。
如果先做 **P3-0「图侧一条 UNWIND 全批」**（一条 Cypher 建全部节点、一条建全部边）：
- 往返从 ~120 次压到 **~5 次**；
- **无任何一致性代价**（图仍是最新、PG 同步写）；
- 内网按 74 ms/次算：**~9 s → <1 s**。

异步派生只省下 PG 那一段（40-80 次 × 272 ms ≈ 11-22 s）。
所以建议顺序：

| 序 | 动作 | 预期 |
|---|---|---|
| **P3-0** | 图侧 `UNWIND` 全批（节点/边各一条 Cypher）| 往返 ~120 → ~5，**无一致性代价** |
| **P3-a** | 用 P1 的 `io=` 统计**在内网实测**一次真实 payload | 拿到 PG/Neo4j 的真实占比，决定要不要异步 |
| **P3-b** | 若 PG 占比 > 60% → 上异步派生（§9.1 的 `bodhi_derive_jobs` + §9.2 四问）| 落库只写图，PG 派生可查、可重放 |

---

## 10. 渲染模式收敛（用户口径 2026-10-09）

### 10.1 结论
1. **不需要 `mode="design"`** —— 复用已有 `mode="document"`（= 调用方给整篇正文 + 统一页头 + 可选属性段）。
   原 P0-c 设想的"新增 design 渲染器"是多做的，改为：**复用 document + 保内容不回归**。
2. **不存 `summary` 这类"摘要知识"** —— `summary` / `index` 是 WeKnora **内建 wiki 页类型**，
   **不是本体类** → 报告页写成 `type="summary"`（`server.py:2295`）是"不合理的模式"，
   应改为**本体里的文档类**（bmm 已有 **评估类** `bmm:Assesment`；agent 侧有 **评测 / 设计单** 等）。
3. **渲染模式由本体决定，只有两种**：`entity`（知识单元，正文由数据属性渲染）/
   `document`（文档类，调用方给整篇正文）。

### 10.2 已实现（本次提交）
| 位置 | 改动 |
|---|---|
| `ke_ontology.RENDER_MODES` | `("entity", "document")` —— 唯一合法取值 |
| `ke_ontology.render_mode(type_name)` | 读本体声明的渲染模式；**声明来源**：编译产物 `ontology_index.json` 的 `render` 或 Neo4j `BodhiOntClass.render_mode`；都取不到 → 默认 `entity` |
| `ke_ontology.class_meta()` | 每个类新增 `render` 字段（json 来源 + Neo4j 补录来源**两条路径都带上**）|
| `ke_pages.CONTRACT_MODES` | 收敛为 `("entity", "document")`；`raw` 降级为**旧别名**（`MODE_ALIASES={"raw":"document"}`）|
| `ke_pages.write_knowledge` | `spec.mode` **可省略** → 自动取 `ke_ontology.render_mode(page_type)`；调用方不再自己选模式 |

### 10.3 待办（P2 的一部分，需要动本体 TTL + 编译器）
- **TTL 声明**：给文档类加 `bodhi:renderMode "document"`（评估 / 评测 / 设计单 / 报告 …）；
- **编译器**：把 `bodhi:renderMode` 编进 `artifacts/weknora/ontology_index.json` 的 `render` 字段，
  并**投影**成 Neo4j `BodhiOntClass.render_mode`（当前实测：41 个类**均未声明** → 全部默认 `entity`）；
- **清理存量**：把 `page_type='summary'` 的报告页归到本体的文档类（可走 `retag` 两段式）；
- 之后 `write_knowledge` 就能**完全按本体**决定渲染，无需调用方传 `mode`。


