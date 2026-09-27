# 知识治理方案（2026-09-24 提案 · 待确认）

面向三件事：**A** 类型变更要联动 slug 与全部引用（且必须风险确认后才保存）；**B** 跨库同实例知识的
**权威/副本 + 单向版本绑定**机制；**C** 溯源纪律（`source_text` 必填、原文依据统一模板、多源综合怎么管）。

> 本文只描述**方案与决策点**；各节末列出「落地改动」，未确认前不动写路径。
> 已配套**只读**巡检（`ke_audit`）：**F1** 跨库同实例候选 / **F2** 权威·副本绑定漂移 / **F3** 原文依据不达标；
> F4（比对不中）/F5（综合声明缺失）待本节确认后实施。

## A. 类型变更 = slug 迁移（联动引用 + 风险确认）

现状：`ke_pages.set_page_type()` 只改 `page_type / category_path / page_metadata`，**slug 不动** →
`ea/businessentity/客户信息` 改成 `ea:Service` 之后 slug 仍是 `businessentity/…`，与
「slug = 模块/类/名称」（`docs/agent-design-flow.md` §11.11）的唯一性口径不一致。
正确的新 slug 由**新类**推导：`<新类模块>/<新类名小写>/<normalize_name(原名)>`；**页 id 保持稳定不重算**
（`wiki_page_revisions.page_id` 外键与 `_resolve_page_id` 按 (kb, slug) 找现有行天然兼容）。

| 需同步改的地方 | 怎么改 |
|---|---|
| 本页 `slug` / `wiki_path` / `category_path` / `page_metadata.ontology.{class,class_label,model}` | 一次 UPDATE（先快照 + `version+1`） |
| 其他页正文 `## 本体关系` 行（`[[slug\|标题]]`、`wiki:slug`） | 按旧 slug → 新 slug 精确替换（逐页快照） |
| 其他页 `out_links` jsonb | 数组元素替换（jsonb 层） |
| 本页/其他页 `in_links` | 落库后 `ke_pages.rebuild_in_links_sql()` 重算（不用手改） |
| 设计页 `## 溯源` 的「上游页面：\`<slug>\`」与 `page_metadata.design.upstream` | 同上替换 |
| 待确认页 `page_metadata.ontology.candidate_slug` | 替换 |
| 会话状态 `state/domain_sessions/<kb>/*.json` 的 `pages[].slug` | 替换（否则智能体续跑按旧 slug 找不到页） |
| 目录树 / `folder_id` | `sync_folders` 重建 |
| 跨库绑定 `page_metadata.authority.master.slug`（见 §B） | 随迁移改写 |

**风险（必须让用户确认）**：① 旧 slug 的 URL 失效（前端书签、外部链接）；② 智能体会话按旧 slug 续跑；
③ 漏改引用 → 巡检 A1 悬空出边；④ 类型语义变化可能让既有关系违反新类的 domain/range（必须重校验，违规进 `violations`）。

**协议（两段式，服务端强制）**
`preview`（返回影响面：新 slug、引用页清单+行号、会话命中、绑定命中、预计 violations、URL 变化）
→ 用户确认（`confirm: "<ticket>"` + `acknowledge_risks: [url_break, agent_session, cross_kb_binding]`）
→ `apply`；**缺 ticket 一律拒绝（HTTP 409）**。幂等（已迁移 → `already=true`）、可回滚（逐页版本快照 + `retag-rollback <ticket>`）。

**待决**：默认迁移 slug，还是允许 `page_metadata.ontology.slug_locked=true` 保留旧 slug（URL 不变、唯一性口径放宽）？
写入入口用**新增 MCP 工具**（14→15，需同步交付文档/自检/手册）还是挂在 `save_knowledge` 的 `retag` 参数 + 新 HTTP 端点？

**落地改动**：`ke_pages.retag_preview()/retag_apply()`（新）、`server` 两个 HTTP 端点 + 可选工具、
`ke_admin.py retag-preview|retag-apply --confirm`、前端类型下拉改「先预览后确认」、巡检新增 A7（迁移后旧 slug 残留引用）。

## B. 跨库同实例：权威（master）/ 副本（replica）+ 单向版本绑定

**识别口径（用户 2026-09-24）**：`跨库 + 同模型 + 同类 + 同名（归一化）` = **同一实例知识**的多个副本候选
→ 巡检 **F1** 报出候选清单，提示租户**指认权威知识**。

**元数据结构**（写在页面 `page_metadata.authority`，不改表结构）：

```json
{"authority": {
  "key": "ea:BusinessEntity|客户信息",
  "role": "master",
  "master": {"kb_id": "...", "slug": "ea/businessentity/客户信息", "page_id": "..."},
  "master_version": 3,
  "master_hash": "sha1:<权威正文指纹>",
  "replica_hash": "sha1:<副本绑定时的正文指纹>",
  "bound_at": "2026-09-24T21:40:00+08:00", "bound_by": "user:phjy",
  "policy": "one_way_upgrade",
  "state": "in_sync|outdated|local_drift|detached",
  "history": [{"master_version": 2, "upgraded_at": "..."}]
}}
```

**单向绑定语义**
- 副本页**禁止本地内容写入**（`save_knowledge` 命中 `role=replica` → 拒绝或转 `local_notes`；需宽松模式可
  配 `policy=allow_local`，此时本地改动标记 `state=local_drift` 并在 F2 报出）；
- 权威**更新不自动推送**副本：权威版本 +1 后，副本进入 `outdated`（F2 报出），由人决定何时升级；
- 副本**永远不会**反向覆盖权威（写路径按 `role` 拦截，单向性由服务端保证，不靠提示词）。

**升级流程（副本 → 权威当前版本）**
1. `upgrade-plan`（只读）：算差异（权威 vs 副本：定义/属性/关系/来源）→ **下游影响评估**（引用本副本的页与边、
   跨库引用、外部系统）→ 出 `plan_id` + 影响清单；
2. 人工确认 → `upgrade-apply --confirm <plan_id>`：写新版本（快照 + `version+1`）、更新
   `master_version/master_hash/state=in_sync`、`history` 追加；副本本地补充（`local_notes`）保留；
3. 冲突：副本正文被本地改过 → 必须显式选择「以权威为准 / 保留本地 / 合并」，不做自动合并；
4. 幂等 + 可回滚（版本快照）。

**跨库关联机制（三条路，建议 2 为主、1 为辅）**

| 选项 | 做法 | 优点 | 代价 |
|---|---|---|---|
| 1 引用式 | 关系行/`out_links` 用限定地址 `kb://<kb_id>/<slug>` 表达跨库边 | 可表达网状复用、零复制 | A1/B3 等巡检需支持跨库解析；跨库边无本库页承接 |
| 2 **镜像订阅**（建议主用） | 副本库保同 slug 页，靠「绑定 + 升级」同步；关系仍在本库内自洽 | 巡检/前端/图谱都不用改 | 有延迟、多一份存储 |
| 3 联邦检索 | 不建副本，多库联合检索（`kb_ids`） | 零重复 | 依赖检索层、无法离线、图谱仍要重复建 |

**落地改动**：巡检 F1/F2（本轮已上线只读版，F3=副本本地改写检测待定）、`/bodhi/authority/decide`（裁决权威）、
`/bodhi/authority/upgrade-plan|apply`、`save_knowledge` 的 replica 写保护、`docs` 与技能文档写明副本纪律。

**待决**：权威裁决是**租户级全局唯一**还是**每库可各自指定**？副本保护强度（严格禁止本地写 / 允许但标漂移）？
跨库关联选哪条路（建议 2 为主 + 1 为辅）？

## C. 溯源纪律：`source_text` 必填 + 原文依据统一模板 + 多源综合

**现状问题**：① 设计路径缺 `source_text` 时服务端**用占位文案**（`server.py:1729` `node.get("source_text") or ctx["no_quote"]`
= 「（概要设计，无原文片段）」）→ 「原文依据」变成"声明无原文"，且**没有强制**；② 「## 原文依据」渲染格式不统一
（抽取路径 `> 引文` 引用块 vs 设计路径 `- 说明（来源）》` 单行）；③ 多源综合知识没有表达方式 → 只能留空或编造。

**统一模板（二选一，不允许"无原文"占位）**

```markdown
## 原文依据                       # 引用型（有逐字摘录）
- 摘录：> <逐字原文，≤300 字>
  来源：《文档标题》 片段 #<chunk_index>（定位：<原文前 12 字>）
  定位校验：exact | normalized | fuzzy | unverified   ← 服务端比对 chunk 文本后写入
```

```markdown
## 原文依据                       # 综合型（多源推导，必须有声明）
- 摘录：> …（来源：《…》片段 #n，定位：…）      # ≥1 条，逐字
- 摘录：> …（来源：《…》片段 #m，定位：…）
### 综合声明
- 综合方式：合并 | 归纳 | 推导；依据：<文档 id / 上游页 slug 列表>
- 置信度：high | medium | low；责任方：<智能体 id / 人>
- 不可回溯部分：<无原文支撑的结论，逐条列出；不得留空>
```

**服务端强校验（把关在 MCP，不靠提示词）**
- `source_text` **必填**且 ≥12 字；命中占位符集合（`（…无原文片段）` 等）→ 进 `violations` 拒绝入库
  （仅**报告页/索引页**允许 `allow_placeholder=true`）；
- `source_span` 或 `chunk_id` 至少一个，服务端比对后回报 `located_by`；`unverified` 不拒绝但记入巡检 F4 人审队列；
- 多源：新增 `sources: [{doc_id, chunk_id, chunk_index, quote, span}]`（≥2 条即综合型），配合
  `synthesis: {mode, confidence, owner, inferred_from[]}`；`synthesis` 与 `sources` 必须成对出现。

**知识分类（把"综合"当一类显式管理）**：`page_metadata.ontology.origin ∈ {quoted, synthesized, designed}` ——
引用型（有摘录）/ 综合型（多源推导，须声明+责任人）/ 设计型（无原文，来源=上游页）。前端按 origin 显示徽标；
综合型必须 `sources[]` 或 `derived_from` 非空才能落库。

**巡检（F 系列）**：F3 原文依据不达标（本轮已上线）｜F4 摘录比对不中/过短（待实施）｜F5 声明综合但
`sources<2` 或缺 `synthesis`（待实施）｜F6 权威变更后综合型页进入"复核候选"（与 §B 下游影响评估合并算）。

**智能体纪律（写进 3 个 SKILL.md + 回执硬约束）**：无法给出逐字摘录的内容**不要落库**（放 `unmatched/needs_review`）；
综合结论必须显式声明综合方式与置信度；占位符只允许出现在报告/索引页。

## 决策点（请拍板后我实施）

1. **A**：类型变更默认迁移 slug，还是允许 `slug_locked` 保留旧 slug？旧 URL 失效是否需要前端跳转表？
2. **A**：写入入口用新增 MCP 工具（14→15）还是复用 `save_knowledge.retag` + 新 HTTP 端点？
3. **B**：权威裁决粒度（租户级全局唯一 / 每库可指定）？跨库关联选哪条路（建议 2 为主 + 1 为辅）？
4. **B**：副本保护强度：严格禁止本地写，还是允许但标 `local_drift`？
5. **C**：`source_text` 必填是**硬拒绝**（写不进去）还是**软提醒**（入库但进人审队列）？设计型页是否也要求声明？

-------------------------------------
#期望的方案
跨库同名同义或跨库同名不同义，同名指slug相同，能否参考DDD的上下文映射解决，自动产生，作为一类知识自动化管理跨库的知识依赖

## 核心思想：将知识库视为限界上下文
在DDD中，一个限界上下文内的术语必须含义唯一。同理，你的每个知识库（如“企业战略库”、“产品研发库”）就是一个独立的“知识上下文”，其中的每个slug在本库内是唯一的、无歧义的。
但跨库时，同名slug就可能产生歧义。DDD的解决方案是：跨上下文同名异义是允许的，但必须明确转换。这恰好就是你需要建立的机制。
## 实现方案：构建“知识上下文映射”层
你可以在WeKnora之上，构建一个独立的知识上下文映射层，它作为一个后台服务，自动化地管理跨库的知识依赖。
1. 自动检测跨库同名Slug
首先，需要自动扫描所有知识库，找出所有同名slug。这可以通过一个定时任务或事件驱动的方式实现，遍历各库的wiki_pages或知识条目表，将slug相同的条目聚为一组。
2. 自动分类：同名同义 vs. 同名异义
对每个同名slug组，需要判断其语义关系。可以结合基于规则和基于LLM的方法：
- 规则匹配：比较条目的title、description、tags等元数据。如果完全一致，则同名同义。
- LLM语义判断：对于元数据不一致的，调用LLM，将各库中该slug对应的页面内容作为上下文，询问：“这两个条目描述的是否是同一个概念？”。
- 上下文相似度：计算条目所在页面其他内容的向量相似度，作为辅助判断依据。
3. 应用DDD上下文映射模式处理
对于“同名同义”的情况，即不同库中的slug指向同一真实世界概念（如“企业战略”），可以应用共享内核（Shared Kernel） 模式。具体做法是，建立一个全局权威概念库（Shared Kernel），为每个同义概念分配一个全局唯一的GUID。各业务库中的slug通过owl:sameAs关系链接到这个GUID。查询时，系统可以通过GUID汇聚所有库中关于该概念的信息。
对于“同名异义”的情况，即slug相同但含义不同（如“账户”在财务库指“银行账户”，在用户库指“用户账户”），则需要建立翻译/映射关系。可以建立一个概念映射表，明确记录：

源库	源Slug	目标库	目标Slug	映射类型	说明
财务库	account	用户库	user-account	重命名	财务的“账户”在用户库中应映射为user-account
财务库	account	风控库	risk-account	重命名	风控库中有自己独特的“账户”概念

这相当于为每个知识库建立了一个翻译字典，当跨库引用发生时，自动进行slug的转换。这种机制在DDD中被称为防腐层（Anti-Corruption Layer, ACL） ，用于隔离不同上下文的概念污染。
4. 自动产生与知识依赖管理
这个映射层可以作为一类知识进行自动化管理：
- 自动生成：通过上述检测和分类流程，自动生成和维护sameAs链接和slug映射表。
- 依赖追踪：当某个知识库的slug被重命名或删除时，映射层可以自动触发检查，识别出所有依赖此slug的其他知识库，并发出预警或自动更新映射关系。
- 一致性校验：定期校验映射表的有效性，确保没有悬空的sameAs链接或过时的映射条目。
## 在WeKnora中的落地建议
WeKnora本身提供了跨库检索能力，通过knowledge_base_ids参数可以同时搜索多个知识库。你可以在此基础上实现上述映射逻辑：
- 检索前：根据用户的查询意图和当前上下文，确定需要检索哪些知识库。
- 检索中：调用WeKnora的跨库检索API，传入多个knowledge_base_ids。
- 检索后：利用你的知识上下文映射层，对返回的结果进行slug归一化。对于sameAs链接的概念，合并结果并标注来源；对于需要翻译的slug，自动替换为当前上下文理解的术语。
- 展示时：向用户清晰展示跨库引用的来源，以及概念在不同上下文中的含义差异。
##  总结
问题	DDD解决方案	你的知识管理实现
跨库同名同义	共享内核（Shared Kernel）	建立全局概念库，用GUID和owl:sameAs关联各库的slug
跨库同名异义	防腐层（ACL）+ 翻译映射	建立概念映射表，自动进行slug转换
知识依赖管理	上下文映射（Context Map）	自动检测、分类、生成映射，并追踪依赖变化

通过借鉴DDD的上下文映射思想，你可以将WeKnora从多个孤立的知识库，升级为一个有机的、语义一致的知识网络。这不仅能解决当前的同名slug问题，更能为未来的知识自动化治理打下坚实的基础。
本回答由 AI 生成，内容仅供参考，请仔细甄别