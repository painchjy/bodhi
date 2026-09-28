# 跨库上下文映射（DDD Context Map）设计方案

> 面向 `docs/knowledge-governance.md` 分割线之后的诉求（2026-09-28）：
> **跨库同名同义 / 同名异义**（同名 = **slug 相同**）能否用 DDD 的**上下文映射**解决，
> **自动产生**、并**作为一类知识**管理跨库的知识依赖。
> 本文是**方案 + 改动清单 + 决策点**；不含实现（用户拍板后再动写路径）。
>
> 与 `knowledge-governance.md` §A（类型迁移）/ §B（权威·副本）/ §C（溯源）的关系：
> 三个方案共用同一套「元数据 + 巡检 + 两段式确认」骨架，**元数据族**分别是
> `page_metadata.ontology.*` / `authority` / `same_as`（本方案新增），互不覆盖。

## 1. 现状盘点（已有什么、缺什么）

| 已有的 | 位置 | 口径 / 行为 |
|---|---|---|
| 跨库**同实例候选** | 巡检 **F1**（`ke_audit.check_governance`） | `跨库 + page_type 相同 + title 归一化相同`；**报候选、要人指认权威**（只读） |
| 权威/副本**漂移检测** | 巡检 **F2** + `page_metadata.authority` | 读 `authority{role,master,master_version,master_hash,replica_hash}`；副本本地改写 → `local_drift`（只读） |
| 跨库同名**回报** | `save_knowledge` → `cross_kb_same_name` | 目标只存在于别库 → **不合并**、只在回执里提示 |
| 跨库**关系候选** | `link_candidates` → `resolve_link_candidate` | 先登记候选、用户确认后写本库关系（**不跨库建边**） |
| 类型迁移**两段式** | `ke_pages.retag_preview/apply/rollback`（HTTP/CLI/MCP 三入口） | ticket=影响面指纹；缺确认即拒；可回滚 |
| 页面引用**改写引擎** | `ke_pages.find_slug_refs` / `_rewrite_refs` / `_rewrite_session_state` / `rebuild_in_links_sql` | 迁移/改名时复用 |

| 缺的（本方案要补的） | 说明 |
|---|---|
| **slug 字面同名**的全库扫描 | F1 用的是「同类 + 同标题」，**不是** slug 同名 → 用户口径需要一个新检查（G1） |
| **同义 / 异义判定**与记录 | 现在只有「候选」，没有结论；也没有"同义就把它们连起来"的机制 |
| **共享内核（全局概念）** | 无概念注册表、无跨库概念 id、无 `same_as` 元数据 |
| **ACL 翻译映射表** | 无「源库/slug → 目标库/slug + 映射类型 + 说明」的落点 |
| **映射的自动化与依赖追踪** | 无扫描任务、无改名/删除后的依赖预警、无映射一致性校验 |
| 检索期**归一化** | 现在跨库检索（`kb_ids` 多库）结果**不做**同名归一与上下文标注 |

> 注意两套口径**并存**，不要互相替代：
> **G 系列（本方案）**：`slug` 字面同名（跨库）；**F 系列（§B）**：`page_type + 标题归一` 的同实例。
> 同一页可能同时命中 G1 与 F1，回执里必须分别标注（G1=名字冲突风险，F1=权威裁决线索）。

## 2. 目标口径（先定义清楚，再谈实现）

- **上下文（Context）** = **一个知识库**（WeKnora 的 KB 就是天然的限界上下文）；`context key` 稳定、与库名解耦。
- **同名** = 归一化后 `slug` **字面相同**（跨库）。归一化：NFC、去首尾空白、内外空格合并、
  英文小写、全角转半角；**不做**同义词替换（那是"同义"判定的事）。
- **同义（equivalent）**：两页指**同一真实世界概念**（可跨库合并**检索结果**、共享一个概念 id）。
- **异义（distinct）**：`slug` 相同但**含义不同**（如"账户"在财务库=银行账户、在用户库=用户账户）
  → **必须**有 ACL 映射（翻译/窄化/拆分）才能在跨库引用中被正确解释。
- **映射（Mapping）**：`源{库,slug} → 目标{库,slug} + 类型 + 说明 + 证据 + 决定人/时间`。
  类型域：`equivalent`（同义，指向共享概念）/ `rename`（同概念不同名）/ `narrower` / `broader`
  / `split`（一拆多）/ `merge`（多合一）/ `unrelated`（显式声明"同名但无关"，用于抑制误报）。
- **上下文映射（Context Map）**：所有上下文两两之间的映射集合 + 依赖关系（谁引用了谁）。
- **红线（延续既有纪律，不变）**：
  1. **不跨库合并页面**、**不跨库写关系边**（`## 本体关系` 仍只在本库解析）；
  2. 判定结论**不自动生效**：算法只产出"建议 + ticket"，**必须**两段式确认；
  3. 所有写操作**幂等 + 可回滚**，并留迁移/裁决记录（照 `retag` 的模板）。

## 3. DDD 概念 → 本系统落点（一一对应）

| DDD | 本系统落点（第一阶段，零本体改动） | 第二阶段（可选增强） |
|---|---|---|
| **限界上下文 Bounded Context** | 一个 **KB** = 一个 context；`state/context_map/contexts.json` 存 `{key,label,kb_id,owner,notes}`（默认从 KB 自动派生，可覆盖） | 在共享内核库投影一页 `context/<key>`（人读的"上下文档案"） |
| **共享内核 Shared Kernel** | **一个专门的"概念库"KB**（`concept` 库；可新建，或指定现有库）—— 每个概念一页 `concept/<归一化名>`；各库页写 `page_metadata.same_as={concept_kb,concept_slug,confidence,decided_by,decided_at,evidence}` | 本体模块 `bodhicx`（`Concept`/`SameAs`/`Mapping` 类）→ 投影概念节点与 `sameAs` 边（图谱可推理） |
| **防腐层 ACL / 翻译映射** | `state/context_map/mappings.json` + **映射页**（共享内核库里 `mapping/<srcCtx>-to-<dstCtx>`，正文渲染映射表，每行 `[[slug]]` 可点） | 同上，本体里表达 `Mapping` 实例与 `mapsTo` 边 |
| **上下文映射 Context Map** | `get /bodhi/contexts` + `scan` 报告 + 映射页索引（共享内核库里一页 `mapping/index`） | 本体图谱里"上下文节点 + 映射边"总览 |
| **依赖追踪** | 扫描/巡检按 `mappings.json` + 页面引用（`out_links`/正文/`## 溯源`）反查依赖；改名/删除/`retag` 后触发复核（G3/G4） | 图谱可达性查询 |
| **检索期 ACL** | MCP 只读 `context_lookup(kb_ids, slug|keyword)` → 同义合并建议 + 异义警告 + 映射跳转；智能体按纪律先查后引 | 前端"跨库上下文"面板 |

## 4. 是否需要 DDD **本体模型**？—— 结论：**第一阶段不需要**，第二阶段按需引入

> **2026-09-28 已拍板（见 §13）**：本体模块**先不做**（决策 7）；概念"是什么类"由**既有本体类**承担
> （决策 2：概念页 `page_type` 就是本体类），本方案只回答"跨库是不是同一个概念"。

**结论**：不要为了这个功能先建一套 DDD 本体（`Context`/`Concept`/`ContextMapping` 类 + 五级同步）。
理由（都是本仓库的既有约束）：

1. **语义层级不对**：现有 `ontology/extensions/*.ttl` 是**领域术语的元模型**（`bmm:*`/`ea:*`/`easvc:*`…），
   而"上下文 / 概念实例 / 映射"是**跨库的治理数据**。把治理语义塞进业务本体，会让"本体库=全局概念库"与
   "本体库=本体模型投影"两种身份混淆。
2. **代价高**：新增模块要走 **TTL → 编译 → 产物 → Neo4j 投影 → wiki 页（五级同步）**，
   每次改识别口径都要重建；而映射是**高频变化**的数据，不该走这条路。
3. **投影是单库的**：本体图谱与本体页都只渲染**本体库**内的节点/边；而本方案的映射天然是**跨库**的，
   塞进单库本体会立刻撞上"不跨库建边"的红线。
4. **不划算**：第一阶段要的是"可读、可巡、可回滚、能指导检索"；
   用「概念库 KB + 映射页 + 元数据 + 索引」就能拿到 DDD 三件套的**全部业务价值**，且零本体风险。

**什么时候需要第二阶段（本体模块 `bodhicx`）**：当出现下面任一诉求时再上——
① 要在**知识图谱里**直接看到跨库概念与映射边；② 要**推理**（如"同义的传递闭包"）；
③ 要把映射作为**本体约束**参与写库校验（如禁止给 `unrelated` 的同名页建跨库引用）。
届时新增模块 `ontology/extensions/bodhicx-ext.ttl`（前缀 `bodhicx`，类 `Concept`/`SameAs`/`ContextMap`，
属性 `conceptId`/`contextKey`/`mappingType`/`conceptSource`），把 `state/context_map/*.json` **投影**成节点/边即可
（`concepts.json` 是唯一事实源，本体只是视图 → 不反向依赖）。

> 一句话：**本体管"是什么类/什么关系"，本方案管"跨库这个词指同一个东西吗"**；
> 后者用"库 + 页 + 元数据 + 索引"表达，比塞进本体更贴合 WeKnora 现有能力（也不动 Go、不重建 app）。

## 5. 数据模型（4 类落点，全部可交付、可回滚）

### L0 上下文注册 —— `state/context_map/contexts.json`

```json
{"version": 1, "updated_at": "2026-09-28T12:00:00+08:00",
 "contexts": {
   "kb:dbc2528f": {"key": "kb:dbc2528f", "label": "企业知识", "kb_id": "dbc2528f-…",
                   "owner": "phjy", "kind": "biz|concept|ontology", "notes": "业务上下文"},
   "concept":     {"key": "concept", "label": "共享概念库", "kb_id": "…", "kind": "concept"}
 }}
```
- **默认零配置**：扫描时按 `knowledge_bases` 自动派生（`key = kb:<短 id>`、`label = 库名`、
  本体模型库标 `kind=ontology`）；想给上下文起业务名/指定 owner 时才覆盖（CLI/HTTP 写入）。
- **共享内核库**：`kind=concept`，认库口径同 `ke_ontology.resolve_ontology_kb`
  （env `BODHI_CONCEPT_KB` → `wiki_config` 标记 → 库名/内容探测），换库不用重建前端。

### L1 共享内核（概念）—— 概念库页 + `page_metadata.same_as`

概念库页（普通 wiki 页，便于人读 + 复用现有能力）：

```markdown
slug: concept/客户
## 概念定义
<各上下文共同认可的定义（人/智能体在 apply 时填写）>

## 各上下文中的表述
| 上下文 | 页 | 类 | 结论 | 置信度 | 决定人/时间 |
|---|---|---|---|---|---|
| 企业知识 | [[ea/businessentity/客户信息]] | ea:BusinessEntity | equivalent | high | user:phjy 2026-09-28 |
| 用户中心 | [[uc/customer/客户]] | uc:Customer | equivalent | medium | user:phjy 2026-09-28 |
```

业务库里的每一页（**只加元数据、不动正文**，避免污染页内容与既有巡检口径）：

```json
{"same_as": {"concept_kb": "…uuid…", "concept_slug": "concept/客户",
             "context_key": "kb:dbc2528f", "relation": "equivalent",
             "confidence": 0.92, "method": "rule|similarity|llm|human",
             "decided_by": "user:phjy", "decided_at": "…", "evidence": ["title 完全一致"]}}
```
> 与 §B `authority` **正交**：`authority` 管"谁是权威副本"，`same_as` 管"跨库是不是同一个概念"；
> 一页可以既是 `replica` 又有 `same_as`。

### L2 ACL 翻译映射 —— `state/context_map/mappings.json` + 映射页

```json
{"version": 1, "updated_at": "…",
 "pairs": [
   {"id": "m:kb:fin→kb:usr:账户",
    "source": {"context": "kb:fin", "slug": "fi/account/账户", "page_id": "…", "type": "ea:BusinessEntity"},
    "target": {"context": "kb:usr", "slug": "uc/useraccount/用户账户", "page_id": "…", "type": "uc:UserAccount"},
    "mapping": "rename",
    "note": "财务的『账户』= 银行账户；用户库的『账户』= 用户账号，非同物",
    "evidence": ["定义差异：…", "关系签名差异：…"],
    "method": "rule|similarity|llm|human",
    "decided_by": "user:phjy", "decided_at": "…",
    "state": "active|stale|detached",
    "source_hash": "sha1:…", "target_hash": "sha1:…",
    "history": [{"mapping": "unrelated", "at": "…", "by": "…"}]}
 ]}
```

映射页（共享内核库 `mapping/<srcCtx>--<dstCtx>`）：把该 context pair 的映射表渲染成 markdown
（每行 `[[slug]]` + 类型 + 说明 + 状态），另加 `mapping/index` 总览（各 pair 计数 + 待复核数）。
**这样"映射"本身就是可读、可巡检、可版本化的知识**（对应"作为一类知识自动化管理"）。

### L3 扫描报告 —— `state/context_map/scan/<scan_id>.json`

```json
{"scan_id": "cs-20260928-1200", "ticket": "9f3c…", "created_at": "…",
 "same_name_groups": [
   {"slug_key": "ea/businessentity/账户",
    "pages": [{"kb": "…", "kb_name": "财务库", "slug": "…", "title": "账户",
               "page_type": "ea:BusinessEntity", "version": 3, "hash": "sha1:…"}],
    "verdict_suggestion": "distinct", "confidence": 0.41,
    "signals": {"title_equal": false, "same_page_type": true,
                "definition_sim": 0.38, "rel_signature_sim": 0.12},
    "required_risks": ["search_merge_semantics"], "applied": null}],
 "stats": {"groups": 3, "equivalent": 1, "distinct": 1, "unknown": 1}}
```

## 6. 分类算法（同义 vs 异义）：**规则优先 → 相似度 → LLM 仅建议 → 人工确认**

| 层 | 输入 | 输出 | 说明 |
|---|---|---|---|
| **R0 完全一致** | `page_type` + 归一化 `title` + 归一化 `summary` | `equivalent`（0.99） | 与 F1 口径一致的"铁证"场景 |
| **R1 结构同构** | 类的模块/前缀、类名、`## 本体关系` 的**关系签名**（出边类型多重集）、属性键集合 | 相似度分 | 结构像 ≠ 同义，但"处处都像"时强烈提示同义 |
| **R2 文本相似** | 定义/摘要/正文前 N 字 → 复用现有 lexical 3-gram 余弦（配了嵌入服务则用向量） | 相似度分 | 零依赖离线可用（可从 `server.py` 抽出 `ke_similarity.py`） |
| **R3 LLM 判定（可选）** | 同名组的两页（摘要+定义+类+关系）→ "是否同一概念？" | `equivalent/distinct/unknown` + 理由 + 置信度 | **只作建议**；依赖用户模型配置；结果写进 `signals.llm` |
| **R4 人/智能体裁决** | 报告 + 用户口径 | 结论 | **唯一**能落库的路径（两段式） |

- **阈值**（默认可配）：`≥0.90` → 建议 `equivalent`；`≤0.55` → 建议 `distinct`；之间 → `unknown`（必须人判）。
- **禁止自动落库**：自动层只产出建议（防误判污染跨库引用，与 §B"需人指认权威"同口径）。
- **抑制误报**：`unrelated` 是**一等结论**（显式声明"同名但无关"），后续扫描不再报（进 `suppressed`）。
- **来源均可解释**：每条结论都带 `method` + `signals` + `evidence`，回执里必须能说清"凭什么"。

## 7. 两段式协议（照抄 `retag` 的模板）

```
POST /bodhi/context/scan                     # 只读：全库同名组 + 分类建议 + ticket
POST /bodhi/context/concept/preview          # 只读：登记"共享概念"会改哪些页（概念页 + 各库元数据）
POST /bodhi/context/concept/apply            # 写：ticket + acknowledge_risks
POST /bodhi/context/mapping/preview          # 只读：写/改一条 ACL 映射的影响面（含受影响引用页）
POST /bodhi/context/mapping/apply            # 写：ticket + acknowledge_risks → 映射表 + 映射页
POST /bodhi/context/mapping/rollback         # 写：按记录回滚（幂等）
GET  /bodhi/contexts                         # 只读：上下文注册表 + 各 pair 映射计数
GET  /bodhi/context/lookup?kb_ids=&slug=|q=  # 只读：检索前查"同义/异义/映射/依赖"
```
- **ticket** = 影响面指纹（组内各页 `(kb,slug,version,hash)` + 目标概念/映射内容的 sha1）；
  影响面变了 → ticket 不匹配 → 拒（`need_rescan=true`）。
- **`acknowledge_risks`** 必须与 preview 的 `required_risks` **完全一致**；风险域：
  `same_as_conflict`（该页已挂别的概念）、`mapping_stale`（决定后源/目标改过）、
  `cross_kb_reference_break`（有页/会话引用将被解释为另一概念的 slug）、
  `search_merge_semantics`（检索期将按同义合并结果）、`concept_page_rewrite`（概念页正文将被更新）。
- **回滚**：概念页/业务页的正文改动走 `ke_pages.rewrite_page_content`（自动 `version+1`，可退回旧版本）；
  `mappings.json`/元数据按 `state/context_map/history/<ticket>.json` 还原。

## 8. 依赖追踪与巡检（新增 **G 系列**，只读）

| 检查 | 内容 | 严重度 | 修复路径 |
|---|---|---|---|
| **G1** | 跨库 **slug 字面同名**（本方案口径）—— 与 F1（同类+同标题）**分别**报 | low | `context_scan` → 裁决 |
| **G2** | 同义组**未指定概念** / 一页挂了**两个概念**（冲突） | medium | `concept/preview → apply` |
| **G3** | 映射**悬空**：源/目标页不存在或已删（`detached`） | high | 重建/删除映射（两段式） |
| **G4** | 映射**过期**：源/目标版本或 hash 变了（`stale`）→ 复核候选 | medium | 复核后重签（重走 preview→apply） |
| **G5** | **同名异义未映射**：`slug` 同名、判定 `distinct`、却无 `mapping` 记录 | high | 立即补 ACL 映射（否则跨库引用会误读） |
| **G6** | 映射**不可达**：指向的概念页/上下文不存在；或 pair 映射互相矛盾 | medium | 修正映射 |

- **触发时机**：① 手动/定时 `context_scan`；② **事件驱动**（写路径回调）——`retag`（slug 迁移）、
  `delete_pages`、`save_knowledge` 改写受影响页、库删除 → 命中 `mappings.json` 的 `source/target`
  → 自动标 `stale`/`detached` 并把 G3/G4 报进巡检（**只标记，不改写别人的页**）。
- **依赖反查**：`context_lookup(slug|q)` 返回"哪些库/页依赖我"，并在回执里提示（人工决定是否处理）。

## 9. 检索期使用（MCP + 前端 + 智能体纪律）

1. **检索前**：智能体先 `context_lookup(kb_ids, q|slug)` → `{same_as, mappings, warnings}`。
2. **检索中**：仍用 WeKnora 原生跨库检索（`kb_ids`）→ **不改 app、不动 Go**。
3. **检索后归一化**：同义组结果**合并展示**（标注来源上下文 + 概念 id）；
   异义组**分开陈列**并显式标注"各上下文含义不同 + 映射说明"（`rename` 给出目标 slug 跳转）。
4. **前端（二期可选）**：wiki 阅读页右侧加"跨库上下文"块（同义概念 / 映射 / 依赖 / 待复核），
   做成自包含组件（`BodhiRelationsPanel.vue` 同款），挂载靠 `patch_frontend.py` 新补丁。
5. **智能体纪律（写进 3 个 SKILL.md + 系统提示词）**：
   - 跨库引用**前**必须 `context_lookup`；命中"同名异义未映射"→ **停**，先请用户裁决；
   - 不得因 slug 相同就假定同义；`cross_kb_same_name` 从"仅提示"升级为"有下一步可执行"。

## 10. 需要哪些修改（逐文件清单）

| 层 | 文件 | 改动 | 规模/风险 |
|---|---|---|---|
| 核心 | **`tools/ke-core/ke_context.py`（新）** | 上下文注册 + 同名扫描 + 分类 + 概念/映射读写 + 两段式 + 依赖反查 | ~450 行，低（新文件） |
| 核心 | `tools/ke-core/ke_similarity.py`（新，可选） | 把 `server.py` 的 lexical 3-gram 相似度抽出复用（避免复制） | ~80 行 |
| 核心 | `tools/ke-core/ke_pages.py` | 加 `set_page_metadata_keys(kb, slug, patch)`（**只改 jsonb 指定键**） | ~40 行，低 |
| 巡检 | `tools/ke-core/ke_audit.py` | 新增 `check_context_map()`（G1–G6）+ 进 `SCOPES`/summary | ~150 行，低 |
| HTTP/MCP | `tools/ontology-mcp/server.py` | 8 个 `/bodhi/context*` 端点 + 3 个工具（`context_scan`/`context_decide`/`context_lookup`）**17→20** | ~250 行，中（同步 selfcheck） |
| CLI | `tools/ke-core/ke_admin.py` | `ctx-scan` / `ctx-link` / `ctx-map` / `ctx-rollback` / `ctx-contexts` | ~60 行 |
| 智能体 | `deploy/weknora-fork/gen_agents.py` | `MODELER_TOOLS` +3、`OPS_TOOLS` +2（只读） | 小 |
| 提示词/技能 | `config/agent_system_prompt.yaml`、`skills/*/SKILL.md`×3 | 跨库引用纪律 + `cross_kb_same_name` 处置 | 中（文案） |
| 交付 | `MCP-SERVER.md` / `AGENTS-SQL.md` / `ONTOLOGY-KB.md` / `selfcheck.py` | 工具数 20、端点清单、概念库认库、G 系列判据 | 小 |
| 前端（二期） | `frontend/bodhi_context_panel.ts` + `patch_frontend.py`（新补丁 v11） | 阅读页"跨库上下文"面板 + 映射页表格 | 中（重建镜像） |
| 二期可选 | `ontology/extensions/bodhicx-ext.ttl` + `_registry.json` | `Concept`/`SameAs`/`Mapping` 类 + `state/context_map/*.json` → 图谱投影 | 中（五级同步） |

**不做**（明确排除）：不动 Go/app 镜像；不加数据库表（元数据 + `state/*.json` 足够，符合既有交付口径）；
不跨库建关系边；**不自动改写任何业务页正文**（只有概念页/映射页——我们自己建的页——允许写）。

## 11. 分期与验收

| 期 | 内容 | 验收（可测断言） |
|---|---|---|
| **一期（MVP，建议先做）** | 上下文注册（自动派生）+ `context_scan`（G1 同名分组 + R0/R1/R2 建议 + ticket）+ `context_lookup`（只读）+ 巡检 G1/G2/G5 + CLI/HTTP/MCP 入口 + 文档 | ① 全库扫描能把同名 slug 正确分组（用"企业知识 / FD案例沙箱 / 领域知识库-测试1"三库实测）；② 任一"同名异义未映射"被 G5 报出；③ `context_lookup` 对已知同义组返回概念/映射；④ **只读路径零副作用**（跑完 git 状态 / 页面版本不变） |
| **二期** | 概念库落地（`concept/<名>` 页 + `same_as` 元数据）+ ACL 映射表 + 映射页 + 两段式 apply/rollback + G3/G4/G6 + 前端面板 | ① apply 后概念页与各库元数据同时生效、可回滚（旧版本可退回）；② 映射页表格可点跳到各库页；③ 改名/删除/retag 后 G3/G4 命中；④ 用户取消 → 零改动 |
| **三期（可选）** | LLM 判定（R3）+ `bodhicx` 本体模块 + 图谱投影 + 检索期归一化话术进回执 | ① LLM 结论与人工判定一致率抽样 ≥80%（不达标不上线）；② 图谱里能看到概念节点与映射边 |

## 12. 决策点（请拍板后我再动实现）

1. **共享内核落点**：新建一个专门的「共享概念库 KB」，还是复用**本体模型库**？（本方案强烈建议前者）
2. **概念 id 形态**：`concept/<归一化名>`（人读友好）还是 `concept/<sha1 前 8 位>`（改名不漂移）+ 别名机制？
3. **元数据写入是否 `version+1`**：只改 `page_metadata.same_as`（最轻、但版本号不再表示"知识变了"）
   还是走 `rewrite_page_content`（version+1、可回退，但会动版本史）？
4. **同名口径并存**：G1（slug 字面同名）+ F1（同类同标题）**并存**分别报，还是把 F1 收敛进 G1？
5. **自动程度**：R3（LLM）是否允许**自动**写 `distinct`（低风险方向），而 `equivalent` 永远要人工？
6. **权限与租户**：谁能裁决（`decided_by` 记什么）？多租户下"同名"是**全租户可见**还是**同租户内**才比较？
7. **二期本体模块**：现在就要"图谱里看得到跨库概念"，还是先页面 + 索引（推荐）？

---
*本文为设计稿；实施顺序建议：决策点 1–3 拍板 → 一期（只读，无风险）→ 用真实库跑一遍出报告 → 再上二期写路径。*

## 13. 决策记录（2026-09-28 用户拍板）—— **以本节为准**（覆盖前文相应口径）

### 13.1 决策 1：**新建"企业共享概念模型"知识库**（不再另设 GUID 概念库）

- 这个库 = **各领域模型公共概念的集合**；跨领域的**同名同义 / 同名异义**都在这里做映射与转换；
- 企业的**标准名称与标准定义**也在这里明确（人读的一等知识）；
- 因此它与其它知识库一样是**普通 wiki 库**（可检索、可投影、可巡检），**kind=concept**，认库口径同
  `ke_ontology.resolve_ontology_kb`（env `BODHI_CONCEPT_KB` → `wiki_config` 标记 → 库名/内容探测）。

### 13.2 决策 2：**概念标识 = 该库中的页 slug**（不造 GUID）；概念归类**仍遵本体约束**

- 问题"概念 id 是什么"：我原稿写的是"另发一个全局 GUID"。按你的答复**改掉**——
  **概念 id 就是「企业共享概念模型」库里的那一页**：`page_id` 稳定 + `slug` 可读，便于人用、便于 `[[slug]]` 引用；
- **归类仍用本体的类名**（`page_type = ea:BusinessEntity / bmm:Goal …`），即概念页**同样受本体约束**：
  ```text
  企业共享概念模型库
    页 slug = <模块>/<类名小写>/<企业标准名称>       ← 与领域模型完全同构（同一 slug 构造规则）
    页 page_type = <本体类>（如 ea:BusinessEntity）  ← 遵循本体约束，由 ontology_types 校验
    正文 = ## 标准定义 + ## 各领域映射（下表）
  ```
- 例：`ea/businessentity/客户`（企业标准）← 领域库里的 `ea/businessentity/客户信息`、`uc/customer/客户` 等都映射到它；
- 因此**不需要新本体模块**（与决策 7 一致）：概念"是什么类"由既有本体类回答，"是不是同一个概念"由本方案回答。

### 13.3 决策 4：**F1 与 G1 合并**成一个治理检查

- 合并为 **F1｜跨库同名/同实例候选**（沿用 F1 编号，便于历史报告对照），内部两个判据：
  - **L1 `slug` 字面相同**（你的口径，跨库）；
  - **L2 `本体类 + 归一化标题` 相同**（原 F1 口径，语义更强的"同实例"线索）。
- 每条候选标 `matched_by: ["slug"] / ["class+title"] / ["slug","class+title"]`，同一张表输出、统一进 `cross_kb_candidates`。
- 映射相关的检查仍用 **G2–G6**（同义未指认概念 / 映射悬空 / 映射过期 / 异义未映射 / 映射矛盾）。

### 13.4 决策 6：权限与"单库写入"（**已加入实现清单**）

**你的理解是对的**，落到实现是这样（依据：`knowledge_bases.tenant_id`/`creator_id`、`kb_shares`、
`organization_tenant_members`、`mcp_services.tenant_id`/`headers`）：

1. **每轮知识修改/新增只允许一个库**：所有写工具（`save_knowledge`、`resolve_link_candidate`、
   以及本方案的 `context_decide`）只接受**单个 `kb_id`**；`kb_ids` 仅用于"选库/多库只读"，
   **禁止一次调用写多个库**。跨库动作（如"本域库 + 企业共享概念模型库"）→ **两次调用、各验权限**。
2. **写权限校验**（新增 `ke_db.assert_can_write(kb_id, caller_tenant)`，在**每个写工具入口**调用）：
   - **属主**：`knowledge_bases.tenant_id == caller_tenant` → 允许（`owner`）；
   - **共享**：`kb_shares`（未删）⋈ `organization_tenant_members(tenant_id = caller_tenant)`
     → `permission` 白名单（`editor` / `writer` / `admin` 可写；`viewer` 只读——本机实测目前只出现 `viewer`）；
   - **都不满足 → 拒**：`{error:"need_write_permission", kb:{id,name,tenant_id}, caller_tenant, why:"该库属租户 10000，你只有 viewer"}`；
   - **身份缺失 → 拒写**（fail-closed，只读放行），并在回执里标 `permission_mode`。
3. **调用者身份来源**：MCP 服务在 WeKnora 里是**按租户注册**的（`mcp_services.tenant_id`），
   所以**给服务配静态头** `X-Bodhi-Tenant: <tenant_id>`（`mcp_services.headers`，每租户一套服务配置）
   → 服务端读该头作为调用租户；未配则回落 env `BODHI_DEFAULT_TENANT`（并在回执注明）。
   （不用智能体自报的租户——那是不可信输入；也不用 app 动态注入——本机无 Go 源码可确认其行为。）
4. **企业共享概念模型库同样受这套约束**：只有**对该库有写权限的租户**才能 apply 概念/映射；
   其他租户**只能读**（`context_scan` / `context_lookup`）→ 回执给"建议清单"，由有权限的一方执行 → 天然满足你的要求。
5. 巡检/回执都显示"权限来源"（`owner` / `share:<org>:<perm>` / `read-only`），便于排查"为什么写不进去"。

### 13.5 决策 3 解释：元数据写入是否 `version+1`

**背景事实**：`wiki_pages.page_metadata`（jsonb）里存系统字段（`ontology` 类/模块、`design.upstream`、
`authority`…）；`version` 是页面版本号，`wiki_page_revisions` 每次变更存快照（用于回退与审计）。

**两种写法**

| 写法 | 做什么 | 好处 | 代价 |
|---|---|---|---|
| **(a) 轻量** | `UPDATE wiki_pages SET page_metadata = page_metadata ‖ '{"same_as":…}'`，**不动正文、不动 version** | 业务内容零改动；不产生无意义版本；不碰乐观锁（用户在编辑态保存不会 409） | 版本史里看不出"这一页被纳入企业标准概念"；审计要看 `state/context_map/history/<ticket>.json` |
| **(b) 版本化** | 走 `ke_pages.rewrite_page_content()`（`version+1` + 快照） | 可回退到"未映射前"；版本号反映"知识状态变了" | 内容没变却多一版；会**碰乐观锁**（编辑中的用户保存时 409 冲突） |

**建议**：**两库两种策略**——
- **领域库里的业务页**：只挂 `same_as` 指针 → **(a) 轻量**（不动你的知识内容与版本）；
- **企业共享概念模型库里的概念页 / 映射页**：这些页的正文本来就会变（标准定义、映射表）→ **(b) 版本化**（可回退、可审计）。

### 13.6 决策 5 解释：LLM 能不能"自动写 distinct"（举例）

**判定只有两个落库方向**：`equivalent`（同义 → 把两页挂到**同一个企业标准概念**上）、
`distinct`（异义 → 记一条"同名但不同物"的映射/`unrelated` 结论，用于**抑制误报**并在跨库引用时警告）。

**风险不对称**：误判 `equivalent` **很危险**（两个不同东西被合并检索、共用标准定义 → 污染知识）；
误判 `distinct` **相对安全**（只是"判成不同"；人工发现错了改回来即可，且不改任何业务页正文）。

**举例**（财务库 vs 用户库都叫"账户"）：
- LLM：`distinct`（置信度 0.97，理由："一类是银行账户 ea:BusinessEntity，一类是用户账号 uc:UserAccount"）
  → 若允许自动：直接写一条 `unrelated` 映射（两次扫描后不再报同一组）→ **省一次人工点击**；
- LLM：`equivalent`（0.90，"都是指企业在银行开立的账户"）→ **永不自动**，必须你点"确认"才把两页挂到
  `ea/businessentity/账户` 这个企业标准概念下。

**三档可选**（请挑一档）：
1. **严格**：全部人工确认（自动层只出建议）；
2. **推荐**：`distinct` 允许自动（阈值 ≥0.95 且两页 title/类/定义都明显不同）；`equivalent` 永远人工；
3. **激进**：两方向都允许自动（不推荐：`equivalent` 自动=有污染风险）。

### 13.7 决策 7：本体模块（`bodhicx`）**先不做**（三期再说）

### 13.8 本节对前文的覆盖（避免口径打架）

- §4 的结论"不新增本体模块"仍然成立，但**理由补充**：概念归类本就由**既有本体类**承担（决策 2）；
- §5 的 **L1** 改为本节的「企业共享概念模型库 + 概念页（slug 即概念 id）」，**删除"另发 GUID"**；
- §6/§7 不变；§8 检查编号按 13.3 调整（F1 合并、G 系列保留 G2–G6）；§10 清单**新增**：`ke_db.assert_can_write`、
  写入口统一权限校验、`mcp_services.headers` 配 `X-Bodhi-Tenant`。

### 13.9 决策 3 / 5 已定（2026-09-28 第二次确认）

- **决策 3（元数据写入）**：**接受"两库两策略"** —— 领域库业务页只挂 `same_as` 指针（**轻量**，
  不动正文/版本；审计走 `state/context_map/history/<ticket>.json`）；「企业共享概念模型」里的
  概念页/映射页**版本化**（`rewrite_page_content`，可回退可审计）。
- **决策 5（自动程度）**：**选档 2** —— `distinct`（异义）允许在**二期**按阈值 **≥0.95** 自动落
  `unrelated`（抑制误报，省人工点击）；`equivalent`（同义）**永远**要人/有权者确认，不自动。
  一期只读：扫描结果里标 `auto_eligible=true`，让人看到"将来会自动做什么"。

### 13.10 一期实施结果（2026-09-28，**只读**）

已落地并在真实库实测（企业知识 / 本体模型 / FD案例沙箱-手机银行 / 领域知识库-测试1 / 领域知识0924）：

| 交付 | 位置 | 实测 |
|---|---|---|
| 核心只读模块 | `tools/ke-core/ke_context.py`（新） | `contexts()` 自动派生 5 个上下文（本体库标 `kind=ontology`；共享概念库未建 → 回执明确提示） |
| 全库扫描 | `ke_context.scan()` | **258 页 → 24 条候选**（equivalent 1 / distinct 4 / unknown 19）；例：`ea/businessentity/客户身份三要素` 同时在 **3 个库**；`ea/offering/手机银行服务` vs `bmm/offering/手机银行服务` 仅 `class+title` 命中（L1+L2 合并生效） |
| 检索前查询 | `ke_context.lookup()` | `ea/businessentity/手机号码` → 2 个上下文同名 + **5 个引用它的页**（依赖追踪） |
| MCP 工具 | `server.py`（**17 → 19**：`context_scan`/`context_lookup`） | 自检 `tools/list OK（19 个，期望 19）`；走 MCP 协议调 `context_scan` 返回 24 候选 |
| HTTP（浏览器同源） | `GET /bodhi/contexts`、`/bodhi/context/scan`、`/bodhi/context/lookup` | 经 nginx 200（`lookup` 的参数需 URL 编码） |
| CLI | `ke_admin.py ctx-contexts / ctx-scan / ctx-lookup` | 通过 |
| 巡检 | `ke_audit`：**F1 合并**（L1+L2，detail 标 `matched_by`）+ **G2–G6**（新 `--scope context`） | 企业知识：F1 = 27（14 同实例 + 13 slug 同名）；G 系列数据源为空 → 全 0（二期写入后生效） |
| 技能/提示词纪律 | 3 个 `SKILL.md` + `agent_system_prompt.yaml` | "跨库引用前先 `context_lookup`；`same_name_no_decision` → 停下请用户裁决" |
| 状态目录 | `state/context_map/{contexts.json,mappings.json,latest_scan.json,scan/*}` | 只写这里；**不碰任何 wiki 页/元数据**（`git status` 无页改动） |

### 13.11 口径修订（2026-09-28 第三次确认）：**领域库不写 uuid、靠同名 slug 查询；领域库不得互相引用**

用户原话："领域知识库 wiki 不使用 uuid 链接企业共享概念页，通过查询企业共享概念模型的**同名 slug** 页面
渲染与企业概念和其他领域概念的关系；领域知识库**不能互相引用**，相互关系**必须通过企业共享概念进行转换**。"

落地口径（**以此为准**）：

1. **建库已完成**：「企业共享概念模型」= `afcd1c2e-0ff3-41c9-8dde-01f136b8a072`
   （`wiki_config.bodhi_concept_kb=true`；`ke_context` 认库 `source=wiki_config`；上下文表 `kind=concept`）。
2. **关联方式 = 同名 slug 查询**：领域页**不写 uuid**（取消 `same_as.concept_kb` 的 uuid 依赖）。
   概念页 `slug` **与领域页同名**（不再用"标准名重写 slug"；企业标准名称放概念页 `title` 与 `## 标准定义`）。
3. **领域库不得互相引用**：领域页出边/正文**不许**指向别的领域库的 slug；跨域关系**必须经企业共享概念页转换**
   —— A 领域页 →（同名 slug）概念页 → 概念页的「各领域映射」表 → B 领域页。
   **渲染由查询完成**（`context_page`/`page_view`），库里零跨库痕迹（概念库也不写指向领域库的 wiki 链接，
   映射表用 `code span` 表达）。
4. **新增巡检 G7**：领域页出边指向"只存在于别的库"的 slug → **high**（违反上面的红线）。
5. **只读渲染接口**：
   - MCP `context_page`（工具数 **19 → 20**）/ `GET /bodhi/context/page?slug=|q=[&kb_id=]`（参数需 URL 编码）；
   - 概念页生成**只读预览**：`ke_context.concept_preview()`（CLI `concept-preview`；
     `GET|POST /bodhi/context/concept/preview`）—— dry-run，给 ticket 与 `body_md`，不写任何页。
6. **弃用**：§5 L1 里"往领域页写 `same_as={concept_kb(uuid), concept_slug}` 指针"**不再是关联依据**；
   若二期仍保留该字段，只能作离线校验用（可选、非必需）。

### 13.12 二期实施结果（2026-09-28）：B 案迁移 + 概念页写路径 + 缓存

**1. 存量修复（用户口径：`ea/offering` 是旧类名 → 走 B 案，统一 slug）**

| 迁移 | 结果 |
|---|---|
| 企业知识 `ea/offering/手机银行服务` → `bmm/offering/手机银行服务` | ✅ v1→v2，`page_type` 保持 `bmm:Offering`；无冲突、无引用；记录 `state/retag/b45daa01e43f6324.json`（可回滚） |
| 企业知识 `ea/offering/转账类服务` → `bmm/offering/转账类服务` | ✅ v1→v2；记录 `d98ae5ffacd4f64d.json` |

迁移后该组在 **2 个库同名**（企业知识 + 领域知识库-测试1）→ 一期扫描的候选从"仅 class+title"升级为"slug+class+title"，
**新口径下的歧义消失**（这正是 B 案的目的）。

**2. 映射关系的事实源 = 概念页；JSON 只是缓存（用户口径）**

- 概念页正文（企业共享概念模型库）承载 **`## 标准定义` + `### 各领域定义摘录` + `## 各领域映射`表**（事实源）；
  表内用 `code span` 而非 wiki 链接 → 概念库不产生指向领域库的链接/边。
- `state/context_map/mappings.json`（企业级）与 `state/context_map/cache/kb-<短id>.json`（**各领域库局部缓存**）
  都是**程序产物**：由 `ke_context.rebuild_cache()` 从概念页解析生成，带 `generated_from{slug,version,hash}` 指纹；
  `read_cache()` 会报 `stale`（概念页改过/删了）。**缓存不写任何领域页、不动领域版本**。
- 命令：`cache-rebuild`（写缓存）/ `cache-show`（看缓存+失效）；HTTP `GET /bodhi/context/cache`、
  `POST /bodhi/context/cache/rebuild`。

**3. 写路径（两段式 + 权限守门）**

- `concept_preview`（dry-run）→ `concept_apply`（带 `ticket` + `acknowledge_risks` 完全一致）→ `concept_rollback`
  （按 `state/context_map/history/<ticket>.json` 恢复旧正文或删除新建页）。
- **权限**：`ke_db.assert_can_write(kb_id, tenant)` —— 属主 / `kb_shares`（经 `organization_tenant_members`）写权限白名单
  / **身份缺失 fail-closed**。调用者租户来自 **MCP 请求头 `X-Bodhi-Tenant`**（`mcp_services.headers` 已配 `10000`），
  回落 env `BODHI_TENANT_ID`。**只有对企业共享概念模型库有写权限的租户**才能 apply ✓（决策 6 落地）。
- 新增页面写助手 `ke_pages.upsert_page()`（概念页/映射页专用：新建 → INSERT + 重算 in_links + 建目录树；
  已存在 → 快照 + `version+1` + 元数据合并，可回退）。

**4. 实测（2026-09-28 22:xx）**

| 项 | 结果 |
|---|---|
| 概念页 | 生成 3 个：`ea/businessentity/登录凭据`、`bmm/offering/手机银行服务`、`ea/businessentity/手机号码`（均 v1，`last_edit_source=bodhi-cxt-edit`） |
| 领域库 | **版本零变化**（`522d5f81`=3/4、`c7426a6e`=6、`dbc2528f`=2 与 apply 前一致）→ "不影响领域知识的版本" ✓ |
| 渲染 | `page_view`/`context_page`：概念页 `exists=true`、标准定义、映射表逐行、peers（concept/domain 分类）、warnings |
| 缓存 | `pairs=3`、概念页=3、`stale=0`；3 份领域库局部缓存 + 2 条 history |
| 权限 | 无线程头 → `need_write_permission`（fail-closed）；带 `X-Bodhi-Tenant: 10000` → `owner` 通过并写成功 |
| 工具面 | MCP **22 个**（新增 `context_page` + `context_concept_apply` + `context_concept_rollback`）；modeler 系 25 个（含写路径），知识运维保持只读 |

**5. 待办（下一轮）**
- 概念页的**标准定义**目前多为占位（领域页无 `summary`）→ 由人/智能体在概念页里补写（或 `apply --definition`）；
- **异义组的映射语义**（`unrelated`/`rename`/`split`）目前在概念页表格里人工填写 → 缓存解析即生效（G5 会随之清零）；
- 前端"跨库上下文"面板（渲染 `context_page` 结果；可在领域页显示"经企业概念 X 关联到 B 领域页"）；
- `mappings.json` 的巡检口径（G3/G4 现已读缓存并可报 `stale`；下一步把"缓存过期"并入巡检提示）。

