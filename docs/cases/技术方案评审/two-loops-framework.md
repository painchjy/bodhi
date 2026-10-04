# 两个闭环的框架（以《技术方案评审》需求验证）｜重构版

> 日期：2026-10-01 ｜ 状态：**框架稿（未提交 git）** ｜ 上游口径：`docs/knowledge-ops-strategy.md`（§2.1 三库/两闭环、§4 技能群）、`docs/ontology-modeling-recommendation.md`（建模方法）、`docs/cases/技术方案评审/P1-knowledge-inventory.md`（知识清单）
> 本文件把"两个闭环"**落到角色 / 起点 / 技能 / 工具 / 产物 / 门禁 / 交接物**，作为后续实施与验收的总纲。

## 0. 一屏（两句话）

- **闭环一（业务闭环）**：以**领域建模角色（＝开发设计团队角色）**，**在现有本体范围内**（现只有 `bmm`）从领域建模开始，做出技术方案评审的**智能体 + 技能 + 工具**，跑通"方案 → 判定 → 问题"并做**效果评估**。**预期必然暴露：重点规则的判定存在高不确定性** → 产出**改进建议**（含证据）→ 这就是闭环二的**入口**。
- **闭环二（设计闭环）**：以**本体建模师角色**（用**本体建模智能体**）拿改进建议 → **改进本体模型** + 优化领域建模/评审的**技能与工具** → **回到闭环一**，由项目团队**补充提取相关知识**、更新智能体/技能/工具 → **重新评估效果**、产出新建议。

```
┌─ 闭环一 · 业务闭环（领域建模角色 / 开发设计团队）─────────────────────────┐
│  需求 ──► model_recommendation（判种类·概念分级）                          │
│        ──► domain_modeling / structured_modeling（在现有 bmm 范围内建模）  │
│        ──► document_review（规则逐条判定；bmm-only）                      │
│        ──► 技能/工具开发（skills/*/SKILL.md + MCP 工具）                  │
│        ──► 效果评估（A/B：正确率 / 证据可回溯率 / unknown 正确率 / 外部依赖）│
│        ──► ✉ 改进建议（重点规则判定高不确定性 + 证据 + 影响面）            │
└──────────────────────────────┬────────────────────────────────────────┘
                               │ 改进建议（挂在会话知识页上，可溯源）
┌─ 闭环二 · 设计闭环（本体建模师角色 / 本体建模智能体）─────────▼─────────┐
│  改进建议分类 ──► 本体迭代方案（TTL → 编译 → 投影 → 页 → audit）          │
│              ──► 优化领域建模/评审智能体与技能、工具                      │
│              ──► 验证报告（落领域模型库）                                 │
└──────────────────────────────┬────────────────────────────────────────┘
                               │ 回到闭环一：项目团队补充提取知识 → 重新评估
```

## 1. 闭环一：领域建模 → 评审落地 → 效果评估

| # | 步骤 | 角色 | 技能（现状） | 主要工具 | 产物 | 门禁 |
|---|---|---|---|---|---|---|
| 1 | 判需求种类 + 概念分级 | 领域建模师 | **`model_recommendation`**（✅ 已物化） | `skills` `ontology_types` `doc_outline` `wiki_search` `save_knowledge` | **建议单**（概念→模型/路线 + 消费关系 + 保鲜成本） | 类型现查、每条带 `source_text`、推荐≠决定 |
| 2 | 领域建模（文本） | 领域建模师 | **`domain_modeling`**（✅ 现有） | `doc_outline` `extract_state` `save_knowledge` `link_candidates`/`resolve_link_candidate` | wiki 知识页（版本化） | 分批交互、`dry_run`→`apply`、跨上下文先登记 |
| 3 | 结构化台账建模 | 领域建模师 | **`structured_modeling`**（✅ 现有） | `import_probe/plan/apply/state` `ontology_types` | 类/属性/关系 + 页面 | 先探表再 plan；字段对不上不落 |
| 4 | ~~图/多模态提取~~ | —（⛔ 移出闭环一） | — | — | — | 你 2026-10-01：多模态是**第二闭环的优化规则**；闭环一规则全走 **LLM 软规则** → 不需要图提取 |
| 5 | 规则实例化 + 判定 | 评审开发团队 | **`document_review`**（✅ 现有，`models:[bmm]`） | `rules_of_policy` `reference_lookup` `review_apply` `doc_outline` `audit_scan`（**闭环一全部按 LLM 软规则**，不用 `graph_query` 出结论） | 结论页（`review/<文档>-<策略>`，版本化） | 逐条闭环、每条带 `evidence`、范围优先、不编造 |
| 6 | 技能/工具开发 | 开发设计团队 | 按 `skills/README.md` 约定新增 | `skills()`（目录）+ MCP `tool_definitions()` | `skills/<id>/SKILL.md`（+ 必要时 MCP 工具） | front-matter 必填 `id/name/when`；**只限模型、不限类型** |
| 7 | **效果评估（A/B）** | 开发设计团队 | ⚠️ **待建**（评估技能） | `graph_query` `audit_scan` `wiki_search` `service_overview` | **效果报告**（指标表 + 反例 + 结论） | 指标固定、结论可复现、反例必列 |

**效果评估的固定指标**（沿用 D4 实验产出）：`结论正确率`｜`证据可回溯率`｜`未知/待澄清的正确率（宁 unknown 勿瞎判）`｜`外部知识依赖数`｜`单份方案耗时/token`（成本项）。
**闭环一的出口物（必须写清、且可溯源）**：**改进建议** —— 每条建议要写：`建议编号｜现象｜证据（结论页/slug+原文）｜涉及规则或概念｜不确定性类型（知识缺/知识过期/判定不可判/本体缺类）｜影响面｜期望改动｜优先级`。

## 2. 闭环二：改进建议 → 本体迭代 + 技能优化 → 回到闭环一

| # | 步骤 | 角色 | 技能 | 主要工具 | 产物 | 门禁 |
|---|---|---|---|---|---|---|
| 1 | **改进建议受理与分类** | 本体建模师 | **`model_recommendation`**（判"这是本体问题还是技能/知识问题"） | `wiki_search` `rules_of_policy` `ontology_types` `audit_scan` | 建议分拣表（本体类 / 技能类 / 工具类 / 知识补充类） | 每条建议**必须能被分类**，且挂回原会话知识页 |
| 2 | **本体迭代方案** | 本体建模师 | `domain_modeling`（演进而非重建）+ 编译链 | `ontology_types` `context_scan` `context_lookup` `retag_preview` + TTL 编译（`tools/ontology-compiler`） | **TTL 变更 + 影响面 + 回滚点** | `shortName` ≤16、编译通过、三层一致、`audit_scan` 无 C1/B1 |
| 3 | 技能/智能体优化 | 本体建模师 + 开发设计团队 | 改 `skills/<id>/SKILL.md`；必要时加 MCP 工具 | `skills()`、`tool_definitions()` | 新版本 SKILL.md（版本号 +1） | **只限模型、不限类型**；改了不用注册智能体 |
| 4 | 知识补充（回到闭环一） | **项目团队** | `domain_modeling` / `structured_modeling` / 图提取 | `doc_outline` `save_knowledge` `import_*` | 补充后的 L1 实例 + 证据 | 无证据不落库；低置信进人工 |
| 5 | **验证报告** | 本体建模师 | 评估技能（待建） | `audit_scan` `graph_query` | **本体迭代方案 + 验证报告**（落**领域模型库**，D1 设计闭环） | 改进前后**同一指标口径**对比；可回滚 |
| 6 | 回到闭环一重新评估 | 开发设计团队 | 评估技能（待建） | 同闭环一第 7 步 | 新效果报告 + 新建议 | 指标固定、可复现 |

> **闭环二的实质**：把闭环一的"**高不确定性**"变成**可判定的确定性**（补知识 / 补本体类 / 改判定实现），或**明确承认"这条规则不该本体化"**（改走 L2/L3，并登记决策依据）。

## 3. 两闭环的技能与工具矩阵（合并视图）

| 技能 | 现状 | 闭环一 | 闭环二 | 关键工具 |
|---|---|---|---|---|
| `model_recommendation` | ✅ **已物化** | 判需求种类 + 概念分级 + 消费关系 | 改进建议受理与分类 | `skills` `ontology_types` `doc_outline` `wiki_search` `save_knowledge` `audit_scan` |
| `domain_modeling` | ✅ 现有 | 文本建模 | **本体迭代方案** | `doc_outline` `save_knowledge` `extract_state` `link_candidates` |
| `structured_modeling` | ✅ 现有 | 台账建模 | 台账类迭代 | `import_probe/plan/apply/refresh/state` |
| `document_review` | ✅ 现有（bmm-only） | **规则逐条判定** | 判定式/实现方式优化 | `rules_of_policy` `graph_query` `reference_lookup` `review_apply` |
| 图/多模态提取 | ⚠️ **待建** | 部署图/图例/坐标证据 | 抽取契约迭代 | 待建视觉管道 + `save_knowledge` |
| `rule_effect_evaluation`（A/B 评估） | ⚠️ **待建** | **效果评估** | 改进后复评 | `graph_query` `audit_scan` `service_overview` |
| `knowledge_ops`（巡检/升级降级/存档） | ⚠️ **待建**（工具齐） | — | **本体与知识版本治理** | `audit_scan/plan/purge` `retag_*` `context_*` |
| **会话知识溯源**（见 §4） | ⚠️ **待建**（方案已定） | 记录来源 | 记录本体/技能改动来源 | `save_knowledge` + 本体新增字段 |
| `agent` 模型（Agent/Skill/MCP/Tool） | ⏸ **推迟到 P6**（M1） | 先落任务库字段 | 正式建模方案 | — |

## 4. 会话知识溯源（**知识来源＝智能体会话**）—— 两闭环共用的地基

> 这是你第 1 点的物化设计：**知识的生产与变化都发生在与智能体的会话里**，所以**把"智能体会话"作为统一的知识来源**；会话在 WeKnora 里虽有保存，但**被删就断链** → 必须**作为知识写进知识库**。

### 4.1 页面结构（三种页）

| 页 | 内容 | 版本/可变性 | 同库约束 |
|---|---|---|---|
| **会话起始页**（每会话一页） | **只放基本信息**：`会话编号`｜`名称`｜`智能体名称`｜`租户名称`｜`初始问题`｜`开始时间`｜`权威库` | 基本不变；**跨库时每库一份副本**，**以初始库为权威** | 每库各一份 |
| **会话分页**（上下文过长时新增） | 该段的**会话内容摘要 + 该段产出的知识页清单**；**关联同一会话编号/名称**，带 `分页序号` | append-only | **与该会话生成的知识同库** |
| **知识页**（领域知识/结论页/建议页） | 正文；**"原文依据"节必须引用"会话分页 + 上下文定位"** | 版本化（`version+1` + 快照） | 与会话页同库 |

**生成新会话分页的触发**：上下文过长 **且** 一组高相关的知识**已确认完成更新**（避免半成品被切段）。

### 4.2 跨库规则（你已定，落地要点）
- 每个会话分页与其产出的知识**必须同库**；
- **切换目标库 → 必须新建会话分页**（不能复用别库的会话页）；
- 跨库时**会话起始页多库副本**，**权威副本在初始库**（副本页标 `isAuthoritative=false` 并指向权威页）。

### 4.3 存档（运维智能体）
运维智能体按**该会话各页关联的知识是否全部过期/废弃**判断 → 会话分页可**存档**（`sessionStatus=archived`）；存档只改状态、不删页（溯源链保留）。

### 4.4 与 bmm 的衔接（**需补进本体**，P2 一起做）

| bmm 增补 | 类型 | 说明 | 必做? |
|---|---|---|---|
| `bmm:KnowledgeSession` | 类 | 知识会话（**知识来源的统一载体**）。属性：`sessionNo`(会话编号) `sessionName` `agentName` `tenantName` `initialQuestion` `startedAt` `partNo`(分页序号) `isAuthoritative` `sessionStatus`(词表：active/archived) | **必做** |
| `bmm:SessionStatus` | 受控词表 | `active` / `archived`（后续可扩） | **必做** |
| `bmm:sourceSession` | 对象属性（**domain=owl:Thing → range=`KnowledgeSession`**） | **任何知识页/知识项都能指回会话**（"来源都指向会话知识页的相关上下文"） | **必做** |
| `bmm:sourceLocator` | 数据属性（owl:Thing） | 会话内定位：`分页序号 + 轮次/消息/段落`（可复核） | **必做** |
| `bmm:sessionFor` | 对象属性（`KnowledgeSession → CourseOfAction`） | 该会话服务于哪个方案/行动（**弱桥**，可选） | 可选 |

> 加上此前两项（`bmm:Summary` 总结档、`knowledgeNeed`），**P2 对 bmm 的增补合计 = 6 项（5 必做 + 1 可选）**。

## 5. 决策点：改进建议的会话知识页要不要**独立管理 / 分类**？

| 选项 | 做法 | 优点 | 缺点 |
|---|---|---|---|
| **A 不独立** | 改进建议就是普通会话分页，靠**标签/属性**区分 | 零新增结构 | 建议散落，闭环二"取件"要人工捞 |
| **B 独立库** | 新建"改进建议"知识库，一号一卷 | 隔离清晰 | **违背你的跨库规则**（会话页与知识必须同库）→ 必然产生副本与权威争议；运维/存档规则要再来一套 |
| **C 同库 + 双层分类（我的建议）** | ①会话页加 `sessionKind`（`需求澄清｜领域建模｜效果评估｜改进建议`）②建议条目加 `adviceCategory`（`本体类｜技能类｜工具类｜知识类`）+ `targetRef`（目标模型/slug）+ `adviceStatus`（`待受理｜已受理｜已实施｜驳回`）③在**领域模型库**建**一个"改进建议总览页"**（滚动汇总待受理建议）作为闭环二的**取件入口** | 不新增库、不破坏同库规则；闭环二有明确入口；分类即路由 | 需要 1 个总览页的维护约定（可由运维技能顺带刷新） |

**✅ 已裁决：选 C**（2026-10-01）。落地形态：
1. **会话页**加 `sessionKind`：`需求澄清｜领域建模｜效果评估｜改进建议`；
2. **建议条目**加 `adviceCategory`（`本体类｜技能类｜工具类｜知识类`）+ `targetRef`（目标模型/slug）+ `adviceStatus`（`待受理｜已受理｜已实施｜驳回`）；
3. **领域模型库建"改进建议总览页"**（滚动汇总 `adviceStatus=待受理` 的条目）——**闭环二的唯一取件入口**；
4. **不新建库**：会话页与知识页仍**同库**（遵守 §4.2 跨库规则）。

> 落地方式：**闭环一内用页面约定实现**（`save_knowledge` 写页 + 固定小节/字段），**不改本体**（见 §6）。

## 6. 越界规则：**闭环一 ≠ 闭环二**（你 2026-10-01 的硬约束）

> **"第一闭环如果不是本体建模的角色，则不应该自动进入第二闭环；只能在第一闭环完成领域建模、智能体、技能、工具设计开发，完成一轮评估后才能进入第二闭环。"**

据此，本轮的**铁律**：

| # | 规则 | 含义 |
|---|---|---|
| 1 | **闭环一的本体改动须逐项批准**（2026-10-01 修订） | **已批准并在本轮实施**：① **会话知识页来源**（`bmm:KnowledgeSession` / `SessionStatus` / `sessionNo…isAuthoritative` / `sourceSession` / `sourceLocator` / `sessionFor` + `Summary` 总结档 + `knowledgeNeed`）② **设计开发本体**（**新模型 `agent`**：DDD + 智能体 + 技能/工具开发 + 评估）。**其余本体诉求（如"技术方案资产"`tsa`）仍只登记不动手** —— 因为**闭环一的规则全部按 LLM 软规则实现**，不需要那类结构化资产模型 |
| 2 | **闭环一"在现有本体范围内"建模** | 只用 **`bmm` 基线**（30 类 / 37 关系 / 15 数据属性 / 2 枚举）能表达的部分；表达不了的**宁可留白**，也不要硬塞语义 |
| 3 | **需要本体 → 只登记，不动手** | 任何"缺类/缺属性/级别丢档/语义不匹配"的诉求，一律写成**改进建议**（`adviceCategory=本体类`，进 §5 的总览页） |
| 4 | **不自动进入闭环二** | 闭环一交付物 = 领域建模结果 + 智能体/技能/工具 + **一轮评估报告** + 改进建议；**只有评估完成并经你确认**，才由**本体建模师角色**启动闭环二 |
| 5 | **受限项要显式标注** | 因"不改本体"而做不到的判定，必须在结论页/评估报告里写**"受限（原因 + 建议编号）"**——这本身就是闭环一最有价值的产出 |

**闭环一的本体处理方式**：把 `ontology/sources/bmm.ttl` 现状冻结为**「闭环一本体基线」**，并产出一份**基线缺口清单**（只登记、不实施，见 §7-D）。

## 7. 第一闭环重构清单（本轮交付，供你验证）

### 7-A 智能体（Agent）

| 智能体 | 角色 | 现状 | 本轮动作 |
|---|---|---|---|
| **「本体建模与设计（技能驱动）」**（`agent_system_prompt.yaml` 唯一入口） | 领域建模师 / 开发设计团队 | ✅ 现有（"一个入口、按技能做事"：先 `skills()` 看目录 → 取技能全文 → 照做） | **不改提示词**；闭环一的行为**全部由技能面约束**（技能=单一来源，改技能不用改智能体） |
| **技术方案评审智能体**（业务智能体） | 评审执行 | ⚠️ 由 `document_review` 技能 + 评审会话承载（**不新增系统对象**） | **可配置**：技能面（`document_review` / `model_recommendation` / `session_provenance` / `structured_modeling`）+ 工具面（现有 33 个 + §7-C 新增 3 个，**先挡板**）；**内置模拟验证模式**（见 §7-F） |
| **设计开发智能体**（**本轮新增**） | 开发设计团队 | ⚠️ **本轮新增** | **职责**：把"领域建模产物 + 知识需求"变成**可运行的业务智能体与业务技能**，并交付**配置与安装指引**、**挡板计划**、**验证清单**；**技能 = `skill_development`**（见 §7-B）；**不改本体**（越界规则） |
| 运维智能体 | 巡检/存档 | ⏸ | **不在本轮**（属闭环二及以后） |

### 7-B 技能（闭环一）

| # | 技能 | 状态 | 闭环一职责 |
|---|---|---|---|
| 1 | `model_recommendation` | ✅ **已物化** | 判需求种类 + 概念分级 + 消费关系 + **建议单**（含受限项） |
| 2 | `domain_modeling` | ✅ 现有 | 需求/方案正文建模（**限 bmm**；表达不了就留白） |
| 3 | `structured_modeling` | ✅ 现有 | 设备申请表 / 技术栈表（**限 bmm**） |
| 4 | `document_review` | ✅ 现有（bmm-only） | **评审主技能**：规则逐条判定 → 结论页（带证据/cypher） |
| 5 | **`session_provenance`（会话知识溯源）** | ⚠️ **待建（本轮物化）** | 会话起始页/分页 + 知识页"原文依据"指向会话上下文（**页面约定，不改本体**） |
| 6 | ~~图/多模态提取~~ | ⛔ **移出闭环一**（你 2026-10-01：多模态是第二闭环的优化规则；闭环一规则全走 LLM 软规则 → 不需要图提取） | — |
| 7 | **效果评估（A/B）** | ⚠️ 待建（本轮物化） | 一轮评估：指标 + 反例 + **受限项清单** + 改进建议 |
| 8 | **`skill_development`（设计开发）** | ⚠️ **本轮物化** | 由**设计开发智能体**使用：设计/编写**业务技能**（`skills/<id>/SKILL.md`）、产出**业务智能体配置**、**安装指引**、**挡板计划**与**验证清单** |
| 9 | `session_provenance`（会话溯源） | ✅ **已物化** | 会话起始页/分页 + 知识来源定位 + 改进建议分类与总览页 |

### 7-C 工具（MCP）——够用 vs 缺口

| 用途 | 工具 | 状态 |
|---|---|---|
| 技能/本体面 | `skills` `ontology_types` | ✅ |
| 文档与切片 | `doc_outline` `extract_state` `list_knowledge_chunks` `grep_chunks` `get_document_info` | ✅ |
| 落库与关联 | `save_knowledge` `link_candidates` `list_link_candidates` `resolve_link_candidate` | ✅ |
| 台账批量 | `import_probe/plan/apply/refresh/state` | ✅ |
| 评审 | `rules_of_policy` `graph_query` `reference_lookup` `review_apply` | ✅ |
| 巡检/治理 | `audit_scan/plan/purge` `retag_*` `context_*` `service_overview` | ✅ |
| **图识别管道** | ⛔ **移出闭环一**（第二闭环再做：启用 WeKnora `vlm_config` 或外接多模态 + 复用 `save_knowledge` 落证据） | — |
| **会话页/建议总览** | 先用 `save_knowledge` 页面约定；后续可加 `session_page_upsert` / `advice_board_refresh` | ⚠️ 缺口（可无代码先跑） |
| **A/B 评估** | 可用 `graph_query` + `audit_scan` + `wiki_search` 拼；如需固化再加 `eval_run` | ⚠️ 缺口（可无代码先跑） |
| **`open_source_catalog`**（**新增·挡板先行**） | **从 IT 工作平台查"授权使用的开源软件清单（产品/允许版本范围/有效期）"**——需求 block 27 明确此法；R2（开源认证）判定的**关键外部知识** | ⚠️ **缺口**（当前只能靠人工/猜测 → 正是"高不确定性"来源） |
| **`review_flow_status`**（**新增·挡板先行**） | 取**技术方案评审流程状态**（流程编号/名称/阶段/轮次/责任人）——需求 block 87"任务信息包括流程编号、名称、评审轮次、方案附件" | ⚠️ 缺口（当前无流程状态获取） |
| **`issue_publish`**（**新增·挡板先行**） | **把评审问题发布到流程/问题台账**（含问题级别/位置/摘录/规则/建议）——需求 block 97-101"问题管理、与规则关联、聚类、溯源" | ⚠️ 缺口（现在只有 `review_apply` 落"结论页"，不能发布到流程） |

### 7-D 本体（**第一闭环基线：冻结，不改**）+ 基线缺口清单（→ 闭环二候选，只登记）

**基线（2026-10-01 修订：本轮已批准的 2 项改动已入库）**
- `ontology/sources/bmm.ttl`：**32 类 / 40 对象属性 / 25 数据属性 / 3 枚举（10 值）**（新增 `KnowledgeSession`、`SessionStatus`、`sessionNo…isAuthoritative`、`sourceSession`、`sourceLocator`、`sessionFor`、`Summary` 总结档、`knowledgeNeed`）
- **新模型 `ontology/sources/agent.ttl`**：**20 类 / 26 对象属性 / 15 数据属性 / 4 枚举（14 值）**（DDD：`BoundedContext`/`Aggregate`/`Entity`/`ValueObject`/`DomainEvent`；智能体：`Agent`/`Skill`/`Tool`/`MCPService`；开发：`DesignSpec`/`InstallGuide`/`ToolContract`/`Stub`；评估：`Evaluation`/`Metric`/`Advice` + 4 词表）
- **编译实测**：`manifest.module_keys = ['bmm','agent']`｜合计 **52 类 / 66 关系 / 40 数据属性 / 7 枚举 / 24 值**；新增产物 `extract_config.agent.json`、`agent_extraction.md`、`extraction_result.agent.schema.json`
- **仍待你点头**：`bash deploy/weknora-fork/refresh_ontology_kb.sh`（把新类/属性**重投影**成「本体模型库」的 wiki 页 —— 会重写 `ontology-wiki` 页面）

| # | 缺口（闭环一遇到就登记，不动手） | 建议分类 | 闭环一临时处理 |
|---|---|---|---|
| G1 | **技术方案资产无类**（方案/系统等级/部署方式·载体/云·机房/灾备方式/设备申请/技术栈+版本/认证清单） | 本体类 | 用 `bmm:CourseOfAction`/`ITAsset`/`SubSystem` **部分承载** + 文本/证据；其余**留白** |
| G2 | **`bmm:Summary`（总结档）缺失** | 本体类（词表） | R3 级别**按文本承载**，评估时标 **受限** |
| G3 | **会话来源无类**（`KnowledgeSession`/`SessionStatus`/`sourceSession`/`sourceLocator`） | 本体类 | **页面约定**：会话页 slug/标题 + `sessionLocator` 写在证据串里 |
| G4 | 角色（组长/专家）无词表 | 本体类（词表） | 用 `bmm:OrganizationUnit` 实例 + 文本 |
| G5 | `DocType → 规则适用范围` 绑定 | 本体类 | 用 `ruleScope` 文本（需求 block 108 可推广性，先不本体化） |

### 7-E 你要验证什么（闭环一验收口径）

1. `skills()` 目录能看到 **4 个已有 + 本轮物化的技能**，front-matter 合规；
2. 用**本需求**跑通一条链：概念分级 → 建模（**限 bmm**）→ 台账建模 → 评审（R1/R2 出结论；**R3 标"受限"**）；产出**结论页** + **会话知识页**（可溯源：`sourceLocator` 可定位到会话上下文）；
3. **一轮评估报告**：`结论正确率`｜`证据可回溯率`｜`unknown 正确率`｜`外部知识依赖数` + **受限项清单**（每条挂建议编号）；
4. **改进建议总览页**存在，条目按 `adviceCategory`/`adviceStatus` 分类（**闭环二的唯一入口**）；
5. **全程零本体改动**：`git status` 中**不应出现任何 TTL/本体产物变更**（这是"未越界"的硬证据）；
6. **模拟验证模式可跑完整一轮**（见 §7-F）：`open_source_catalog` / `review_flow_status` / `issue_publish` **用挡板数据** → 结论页与问题必须带 **"模拟数据"** 标记 → A/B **一轮评估完成**（**不依赖**这三个工具真实投产）；
7. **安装与配置可复现**：按 `skill_development` 的**安装指引**装完，业务方能**自行验证**（含"哪些工具是挡板、哪些已投产"的清单）。

### 7-G 知识库分类与归属（2026-10-04 修订口径：**只有「工作知识库」进本体**）

> **口径（用户裁定 2026-10-04）**：**只有工作知识库需要建模**（`agent:WorkKnowledgeBase`，**一库一页**）。
> 其它知识库**都不进本体**：`.env` 注册的三个特殊库、以及**领域知识库**，分别是
> **第二闭环 / 第一闭环的闭包载体**，不存在"多种知识库角色"这种要建模的分类。
> **业务智能体能指定的目标库 = 工作知识库**，其范围 = **建模时（设计单）指定的知识库名称**。
> （原 `agent:KbRole` 5 值词表与 `workKbRole`/`skillTargetsKbRole`/`toolTargetsKbRole` 已**废止并清理**，由
> `agent:WorkKnowledgeBase` + `agentUsesWorkKb`/`skillTargetsWorkKb`/`toolTargetsWorkKb` 承接。）

| 类 | 知识库 | 库 id（实测） | 谁写 | 说明 |
|---|---|---|---|---|
| ① 特殊库（`.env` 注册） | **企业本体模型** | `08810cbd-…` | **手工上传维护**（TTL → 编译 → 投影） | 本体真源（`BODHI_ONTOLOGY_KB_ID/NAME`） |
| ① 特殊库 | **企业共享概念模型** | `afcd1c2e-…` | **运维智能体**（**其他智能体只读**） | 治理产物：跨上下文的共享概念与映射 = **DDD 上下文映射的载体**（`BODHI_CONCEPT_KB_ID/NAME`） |
| ① 特殊库 | **企业知识管理领域**（新） | `6effde80-…` | 我们自己按需处理 | 承载**知识提取/管理**的技能与工具定义（按需补充、不全集暴露）；**暂不纳入两闭环**（已纳入 `.env`：`BODHI_KNOWLEDGE_MGMT_KB_ID/NAME`） |
| ② **领域知识库** | **技术方案评审领域模型** | `7efad3eb-…` | 领域建模 / 设计开发 | 领域建模的**目标库**，也是业务智能体设计/开发使用的库：**业务智能体、技能、工具、评估、改进建议都在这里定义** → 与业务领域知识/需求天然形成溯源；**业务智能体不得把它当成自己的工作知识库** |
| ③ **工作知识库** | **技术方案评审任务知识库** | `12ccea38-…` | **业务智能体运行时** | 运行时新增/留存的知识（方案实例、部署/技术栈识别结果、结论、问题、A/B 数据）；**范围由所在领域的领域知识库指定**；技能/工具需能识别与管控 |

**DDD 的处理（你的裁定）**：**限界上下文 = 领域知识库**（知识库之间不能跨库引用、本地领域方言独立，天然满足）；**上下文映射 = 企业共享概念模型** → **本体里不建 DDD 概念**（`BoundedContext/Aggregate/Entity/ValueObject/DomainEvent` 已从 `agent.ttl` **移除**）。

**本体里的表达（现行，2026-10-04）**：`Agent / Skill / Tool / MCPService / DesignSpec / InstallGuide / ToolContract / Stub / StubStatus / Evaluation / Metric / Advice / AdviceCategory / AdviceStatus / **WorkKnowledgeBase**`（+ 词表 `StubStatus / AdviceCategory / AdviceStatus`）。**领域建模智能体、设计开发智能体、运维智能体不进本体**（属能力，由技能/工具面表达）。
`WorkKnowledgeBase`（**只有这一类知识库进本体**）供**技能（`skillTargetsWorkKb`）与工具（`toolTargetsWorkKb`）识别与管控**；业务智能体用 **`agentUsesWorkKb`** 声明它使用的工作知识库；三个数据属性 `workKbName`/`workKbId`/`workKbUsage` 描述库名、库 id 与用途范围。
**不进本体的知识库**（无需建模、不要在知识库里建"角色页"）：`.env` 三库（企业本体模型 / 企业共享概念模型 / 企业知识管理领域 —— **第二闭环**使用）与**领域知识库**（领域建模/设计开发的目标库 —— **第一闭环的闭包**，由部署提示词上下文表达）。

> （闭环一交付清单的另一项 —— **挡板与模拟验证** —— 见**下一节 §7-F**。）

### 7-F 挡板与模拟验证（stub-first）——投产后换真，**契约不变**

| 原则 | 说明 |
|---|---|
| **契约先行** | 先定工具**接口契约**（入参/出参/错误码/字段含义/分页/权限），**挡板与真实实现共用同一契约**；调用方（技能/智能体）不因换实现而改 |
| **挡板打标** | 挡板返回值**必须带 `_mock: true` + `_source: "stub:<工具名>"`**；引用它的**结论页/问题/建议必须显示"模拟数据"**（否则业务会把模拟当真实） |
| **不污染** | **写库/发布类挡板默认 `dry_run`**（只回执、不真写）；确需真写时**只写测试库/测试实例**，并在页上标注 |
| **可切换** | 用**配置**切换（MCP 侧 `MOCK_TOOLS=1`，或每工具 `mock=true` 参数）；**切换不改代码、不改技能** |
| **可追溯** | "挡板 → 真实"的替换要**留痕**：一条会话知识页 + 一条改进建议（`adviceCategory=工具类`，写清替换时间与验证结论） |
| **业务可自验** | 安装指引必须列清**当前工具状态表**（`挡板 / 已投产`）与**该工具影响的结论类型**，业务方据此判断哪些结论是模拟的 |

**三个待建外部工具**（§7-C 末三行）＝ **需求点名的两处缺口**：`open_source_catalog`（IT 工作平台·授权开源清单）、`review_flow_status`（评审流程状态）、`issue_publish`（问题发布）。
**挡板数据从哪来**：`open_source_catalog` 直接用 `docs/cases/技术方案评审/P3-materials-draft.md` §2 已拟的**认证清单样例**；`review_flow_status` 用一张**状态机样例表**；`issue_publish` 用**本地队列（只回执）**。
**为什么先挡板**：这三个都与**存量系统**交互（投产周期长），但**不挡也测不出"外部知识依赖数/unknown 正确率"**；挡板先行 → 闭环一**能完整跑一轮评估**，并把"真实接口需求"作为**工具类改进建议**交给下一步。


### 7-H DDD 概念的去向 + 过程对象（评审问题 / 修订追踪）的建模（2026-10-01）

#### （1）DDD 那 5 个类**保持删除** —— 但它们的职责要有明确承接人（不是"凭空删掉"）

| DDD 概念 | **谁承担（本工程里的替代机制）** | 依据 |
|---|---|---|
| 限界上下文 BoundedContext | **领域知识库**（库间不能跨库引用 + 本地领域方言独立） | 你的裁定 2026-10-01 |
| 上下文映射 ContextMap | **企业共享概念模型**（运维智能体维护的治理产物，其他智能体只读） | 同上 |
| 实体 Entity | **本体类 + 唯一标识数据属性**（惯例：`systemNo` / `sessionNo` / `adviceId`…）；**页面身份 = slug** | bmm 现有数据属性里 `systemNo/systemAbbr/systemStatus` 即此用法 |
| 值对象 ValueObject | **受控词表**：`owl:equivalentClass [ owl:oneOf (...) ]` + 个体（bmm 3 张、agent 4 张） | 既有惯例 |
| 聚合 Aggregate / 一致性边界 | **知识库边界（同库）+ 单页幂等写入 + 版本快照**（`save_knowledge`） | §4.2 同库规则、落库工具行为 |
| 领域事件 DomainEvent | **会话知识页里的"决定/事件"记录**（`bmm:KnowledgeSession` + `sessionKind`），或工作知识库的日志页 | §4 会话溯源 |

> 结论：**本体里不需要 DDD 元概念**；需要表达"实体/值对象/边界/事件"时，一律走上表右列。

#### （2）评审问题 / 修订追踪：归属**③ 工作知识库**，用**页面契约**，闭环一**不进本体**

**为什么**：它们是**业务智能体运行时新增/留存的产物**（需求 block 90-101 的过程数据）→ 按 **D1 裁决**（过程数据不进本体）落**工作知识库**；本体只放定义。

**页面契约（写进技能，不写本体）**

| 页 | slug 约定 | 必填小节/字段 | 追溯机制 |
|---|---|---|---|
| **评审问题页** | `issue/<方案>-<序号>` | 问题信息：**级别**（严重/一般/建议）、**位置**（章节）、**原句摘录**、**触发规则**（slug/名称）、违规说明、**修改建议**、状态 | 每条问题**必须**挂 `bmm:sourceSession` + `sourceLocator`（会话分页+定位）；与规则页/章节页用页内引用关联 |
| **修订记录页**（修订追踪） | `rev/<方案>-<vN>` | 本版修改清单：每条 =（章节 + 修改前/后文字 + **对应问题编号** + 是否可溯源）+ 附件版本号 | **不可溯源**的修改 → 标记 **"需重新评审"**（需求 block 95） |
| **方案版本页** | `sol/<方案>-<vN>` | 流程编号/名称/**轮次**/附件（需求 block 87） | 与修订记录页互链 |

**修订追踪的实现机制**（都用现成能力）：① **页面版本快照**（`wiki_pages.version` + revision，落库工具已负责）② **问题↔修改↔章节** 三元关联用 `link_candidates` → `resolve_link_candidate`（先登记、确认后写）③ **可追溯口径**：任何修改必须能指回一条问题；指不回就打"待重评"。

**专家核实**：复用 `bmm:Assessment`（`assessedBy`→`OrganizationUnit`，`minCardinality=1`）的语义；闭环一先写成问题页里的"核实记录"字段（≥1 专家，需求 block 98）。

#### （3）什么时候才把"问题/修订"本体化？→ **闭环二**（触发条件写清楚）

**触发条件**：出现**跨方案的聚类/统计/规则覆盖分析**需求时（需求 block 100"问题聚类、与规则关联"、block 11-12"评审质量指标"）→ 才值得为它们建类（如独立的**工作层模型** `review`：`ReviewIssue` + `IssueRevision` + 关联 `bmm:OperativeBusinessRule`），并作为 `adviceCategory=本体类` 的改进建议交闭环二。
**闭环一不建**：因为闭环一规则全走 **LLM 软规则**，不需要这些结构化字段，建了也没有消费者（违反"每个类必须有消费者"的门槛）。

#### （4）裁决（2026-10-01）

**✅ 已裁决：选 A** —— 闭环一按上表**页面契约**做（问题页/修订页/方案版本页，落工作知识库），**本体化留闭环二**（由"聚类/统计/规则覆盖"需求驱动，届时作为 `adviceCategory=本体类` 的建议受理）。

## 8. 本轮重构做了什么 / 下一步

| 事项 | 状态 |
|---|---|
| 本框架文档（两闭环 + **越界规则** + **第一闭环重构清单** + 会话溯源 + 决策点 C） | ✅ 本轮 |
| `skills/model_recommendation/SKILL.md` | ✅ 已物化（上轮；技能=单一来源，改它不动智能体） |
| 策略文档 §4.2 改为**会话溯源版** | ✅ 本轮 |
| **P2 原计划（`技术方案` TTL + bmm 6 项增补）** | ⛔ **按越界规则整体移出闭环一** → 降级为**闭环二候选**（见 §7-D 的 G1/G2/G3） |
| 本轮待物化技能（§7-B） | ⏳ 剩 2 个：**图/多模态提取** → **效果评估（A/B）** |
| **`skills/skill_development/SKILL.md`**（**设计开发智能体**用） | ✅ **本轮物化**（设计单 → 脚手架 → 安装指引 → 挡板计划 → 验证） |
| **`skills/session_provenance/SKILL.md`**（会话知识溯源） | ✅ **本轮物化** |
| **3 个外部集成工具契约（挡板版）**：`open_source_catalog` / `review_flow_status` / `issue_publish` | ✅ **本文 §9 冻结契约**；⏳ 实现待定（**先挡板**） |
| D-K1~D-K4（前置知识决策卡） | ⏸ **属第二闭环，暂缓**（你 2026-10-01 裁决） |
| P1 完善与验证 | ⏳ 按 §7 口径完善（**不含任何本体改动**） |
| **部署（2026-10-03）** | ✅ 本体：`bmm`+`agent` 编译通过、Neo4j **47 类**、「企业本体模型」库 **153 页**（新增 68 / 删 0）｜✅ 智能体：**`bodhi-skill-dev`「设计开发（技能驱动）」已入库**（`agent_mode=smart-reasoning`、tools 37、prompt 930 字符）｜✅ app/frontend 已重启（`/health 200`、MCP 在监听）｜✅ 编译器：移除 `ea-service`/`ea-ownership`/`bmmfd` **三个无源扩展**并移出 `ea`（消 E3 同名冲突）→ **整包 `compile` 不再中止**、全量体检 **error 0**｜⏸ 未做：前端镜像重建（类型下拉要看到 `agent:*` 需 `gen_frontend_types.py` + `build_frontend.sh` + `deploy_frontend.sh`） |
| **修复（2026-10-01 手工验证发现）** | ① **租户/身份可伪造** → 工具侧 `server.py` 新增 `_attrs_with_identity`：`bmm:KnowledgeSession` 的 `tenantName`/`agentName` **服务端强制注入、客户端值一律丢弃**（实测：伪造"伪造租户/假智能体"被丢弃，注入 `tenantName=10000`、`agentName=BODHI_AGENT_NAME`），回执给 `injected_identity`；技能 `session_provenance` 新增 **§0b 身份与租户只能由系统注入**（禁止询问/接受/编造）。② **来源定位没落成数据属性** → payload 新增 `source_locator` 通道（显式 `source_locator`/`locator` 优先，`chunk_index` 兜底为 `片段 #n`），技能 `domain_modeling` 明确要写 `attributes.sourceLocator`；本体三条属性注释更新（`tenantName`/`agentName`=系统注入只读、`sourceLocator`=每条知识必填）。已重编译+投影（47 类 / 153 页）+ 重启 MCP |
| **待办（诚实项）** | ① 页面正文「## 原文依据」里补渲染「来源定位」行（当前只落**节点属性**，正文拼装处未改）；③ 前端镜像重建（类型下拉见 `agent:*`）｜~~② `agentName` 需配 `BODHI_AGENT_NAME`~~ **已作废（2026-10-04 改为“只认会话”）** |
| **修复·会话身份门禁（2026-10-04 用户口径）** | **① `agentName` 只认会话**：真源 = `session_no`（WeKnora 会话 uuid）→ `sessions.agent_config->>'agent_id'` → `custom_agents.name`（**会话选择的智能体**）；**删除 `BODHI_AGENT_NAME`**（env 兜底作废）、客户端自报值一律丢弃/覆盖。**② fail-closed**：**取不到会话的智能体名称 → 拒绝触碰知识库** —— `mode="apply"` 直接返回 `ok=false`+`need_session=true`+`reason`；`session_no` 必须**真实存在于 `sessions` 表且为 uuid 形态**（自编号如 `S-20261004-01` 被拒 → 防编造）。**③ 来源会话自动挂**：服务端给本次**所有页**补 `bmm:sourceSession`（= `session/<会话编号>`）与 `bmm:sourceLocator` 兜底。**④ 回执**：`session_identity={session_no,agent_id,agent_name,session_title}`（`report`/`graph` 两条路径都带）。**⑤ 顺带修真 bug**：`save_knowledge`/分派/总览页的 `model` 默认值 `"ea"`（模块已删 → 必报错）改为 `"bmm"`。实测（4/4 绿）：apply 无会话 → 拒；apply 自编号 → 拒；dry_run + 真实会话 → `agent_name=设计开发（技能驱动）`；会话页伪造 `sessionNo/agentName` → 被权威值覆盖，知识页自动得 `sourceSession`。已重启 `bodhi-mcp`（active） |
| **修复·目录树不显示「智能体开发本体」（2026-10-04 用户实测）** | **根因 = 前端容器跑的是 9-29 旧产物**（镜像 `weknora-ui:bodhi2` 里其实已是新 bundle，但 `WeKnora-frontend` 容器仍是旧 imageId → 产物里 `agent:MCPService` 命中 0、`ea:APIService` 命中 1）。前端树的类型集合来自 `isOntologyType()` + `stats.pages_by_type`：**旧 bundle 不认识 `agent:*`** → 「本体」tab 的 `page_types` 不含 agent → 后端按类型过滤目录时**不返回** `智能体开发本体`（其子树只有 agent 页）→ 目录与 11 页都看不到（`bmm:*` 认得 → BMM 正常）。**修法**：`docker build -f /root/fe-build/Dockerfile -t weknora-ui:bodhi2 .`（dist 已是新的，秒级）→ `bash deploy/weknora-fork/deploy_frontend.sh`（重建容器 + restart）→ 实测容器产物 `agent:MCPService`=1、`ea:APIService`=0 ✅。**教训**：重镜像后**必须重建容器**（`compose up -d --no-build frontend`），否则跑的还是旧层 |
| **数据·目录口径收口（2026-10-04）** | 该库 47 页 `category_path[0]` 全部归一到**模块短名**（`智能体开发本体` / `BMM业务动机模型`）；`wiki_path` 重算 47 页；`sync_folders --link-pages --prune` 清掉 **11 个**长名/空格变体残留目录；顶层目录只剩 4 个（`BMM业务动机模型`/`智能体开发本体`/`概要设计报告`/`系统与规则台账`）；**活页未挂目录 = 0** |
| **提示词·落库铁律（2026-10-04）** | `agent_system_prompt.yaml` 给 `skill_modeler_agent`/`skill_development_agent` 各加一条**落库必传会话编号**（`session_no` 必传；`agentName`/`tenantName` 由服务端按会话注入，不要自己写；取不到 → 工具拒写，不要自编编号/换库）；经 `gen_agents.py --only modeler/dev` 生成 SQL 后**我们自己执行**（`--apply` 的 stdin psql 在本机未生效）→ 实测 `bodhi-ea-modeler` 3622 字符、`bodhi-skill-dev` 1303 字符，均含 `session_no`+铁律；app 已重启（`/health 200`） |
| **修复·知识库口径（2026-10-04 用户口径：删「知识库角色」词表）** | **① 本体**：`agent:KbRole`（5 值词表：企业本体模型库/共享概念/知识管理领域/**领域知识库**/工作知识库）**删除** → 改为普通类 **`agent:WorkKnowledgeBase`（工作知识库，一库一页）**；关系改名+重定向：`agentWorkKbRole→agentUsesWorkKb`、`skillTargetsKbRole→skillTargetsWorkKb`、`toolTargetsKbRole→toolTargetsWorkKb`（range=WorkKnowledgeBase）；新增 3 个数据属性 `workKbName/workKbId/workKbUsage`；注释写明「**领域知识库（=本库）不进本体**：由业务智能体的**部署提示词上下文**表达，与设计/部署一致、不需要额外配置管理」「特殊知识库由 .env 注册，不建业务实例」。**② 关键坑**：`apply_projection()` 是 MERGE **只加不减** → 改本体后必须**手工删 Neo4j 里的废弃节点**（实测 9 个：KbRole 类 + 5 个枚举值 + 3 条旧关系），否则 wiki 投影仍从 Neo4j 生成旧页（第一轮就踩了：投影完旧页还在）。**③ 链路已验证**：编译→灌 Neo4j(607 语句/48 类)→清 9 个废弃节点→重投影 = **47 类 / 59 关系 / 43 属性 / 156 页**；本体库 `ontology/agent/workknowledgebase`=1、`KbRole` 残留 **0**。**④ 技能/提示词**：`skill_development` 设计单「知识库绑定」→「**工作知识库（清单）**」（只列运行时要读写的库；不列领域库）；`skill_modeler_agent`(+1e)/`skill_development_agent`(+口径段) 提示词已更新（3940 / 1709 字符）。**⑤ 存量数据**：撤回 4 条旧关系（`retract_relations` 各自 removed_lines=1）、**软删 4 个角色页**（2 库×2）、在「技术方案评审领域模型」新建实例页 **`agent/workknowledgebase/技术方案评审任务知识库`**（带 workKbName/Id/Usage + 自动 `sourceSession`）并与智能体连 `agentUsesWorkKb`；两库 `in_links` 重建、「知识库角色」空目录软删、**未挂目录=0** |
| **事故记录（诚实项，2026-10-04）** | 我中途用 shell 拼 SQL 追加铁律时**反引号被 bash 执行**，把 `bodhi-ea-modeler`/`bodhi-skill-dev` 的提示词**截断到首行 + 半截块**（378/397 字符）。**已从真源恢复**：`gen_agents.py --only <key>` 生成 SQL → 手工执行 → 3622/1303 字符；**全库扫查无其它智能体受影响**（`has_bad_block=false`） |
| **待办（2026-10-04 收口后）** | ① 设计单页 §5 正文仍是旧文字（「## 5 知识库绑定 —— 读：…」）→ 下次设计智能体重跑会按新口径输出；如要我直接改这一页文字，说一声。② `3f538bd9`（系统与规则台账）的智能体**未声明工作知识库**（旧关系已撤回 → 现在是"无"）→ 需要时由设计智能体补 `agentUsesWorkKb`。③ 新建 WorkKnowledgeBase 实例页时回执报了 3 条**非阻断** `violations(kind=attribute)`（属性实际已写入并渲染）→ 疑似 `class_attributes` 归属索引未即时刷新，下次重编译/重启后消失；不影响使用。 |
| **修复·新增本体类自动编目（2026-10-04 用户实测 + 口径）** | **症状**：闭环二新增的类（`agent:WorkKnowledgeBase`）的页**建好且挂了目录**，界面**看不到、只能搜索**。**根因**：目录按类型过滤，而「本体」tab 的类型集合 = `stats.pages_by_type ∩ isOntologyType()`，`isOntologyType` **只认构建期白名单**（随镜像烘焙）→ 新类不在白名单 ⇒ 前端不带该类型查 ⇒ 目录不返回。**修法（A1+A2+A3+A4 全做）**：**① 结构判据** `isOntologyType(t)=白名单(t)‖t.includes(':')`（改 `frontend/ontologyTypes.ts` + 生成器 `gen_frontend_types.py`）；**② 子类闭包检索**（A2①）——目录第二级=**直接 owl:Thing 子类**（`ke_ontology.category_path` = `[模块短名, top_group]`，`top_group` 走 `ancestors` 闭包）→ **同顶层类的子类页同目录**，点开即列全部子类（实测 `工作知识库` 目录 2 页 ✅，故不加 ② 的显式展开保险）；**③ 目录按需全量重建**：MCP 新增 **`GET /bodhi/folders/refresh?kb_id=<kb>[&force=1]`**（判 `max(页.updated_at)>max(目录.updated_at)` → 才全量重建 upsert+挂页+prune，幂等），前端在**进入 wiki tab/切库首次加载根目录前**调它并在 `refreshed` 时**强制重载本层**（补丁写进 `patch_frontend.py`（可重放）+ 构建树 Vue）；**④ 重建一次前端**（vite 1m31s → 镜像 `55e6d3e91b32` → 容器重建）。**验证**：产物 `includes(":")`=1；本体 tab 类型集合 **17 个（含新类）**；顶层目录 `BMM业务动机模型(2)/智能体开发本体(11)/系统与规则台账(33)`，`智能体开发本体/工作知识库`=**2 页** ✅；`/bodhi/folders/refresh` 直连与经 nginx 反代均通、幂等 no-op 正确。**结论：以后闭环二新增本体类，不再需要重建前端。** |
| **修复·三项（2026-10-04 用户实测）** | **① 非本体库出现「上传本体文件」**：`系统与规则台账` 被**误标** `wiki_config.bodhi_ontology_kb=true`（`ontology:*` 页数=0），且判定把「env 权威库/标记/内容探测」**并列 OR** ⇒ 改为**优先级**（有 env 权威库时只有它算本体库，标记/探测仅兜底）+ 清误标 → 实测只有「企业本体模型」为 true ✅。**② `sourceSession` 落错层**：本体里它是**对象属性**，我上轮当**数据属性**写 ⇒ 正文裸键、关系里没有边。改为：**服务端自动补关系边**（`design_elements` 里 `bmm:sourceSession` → 会话页，分页优先；回执新增 `source_session_edges`），`attributes` 只留 `sourceLocator`（带分页序号）；新增 `_session_page_ref()` **按 `bmm:sessionNo` 属性匹配**会话页（实测智能体把 slug 命名成 `bmm/knowledgesession/会话-XXX`，**不是** `session/<编号>`，旧匹配必然失败）；存量 5 页已清理（4 页补边、5 页删错属性行，残留 0）✅。**③ 编目随本体变化**：`category_path` 是**写时物化**，改本体/手工改类型都不重算 ⇒ 新增**显式** `recategorize`（MCP 工具 + `GET /bodhi/folders/recategorize?kb_id=&dry_run=`，**不自动跑**）；**并修了真 bug** `top_group`（原用 `ancestors()[-1]`，那条 Cypher **无 ORDER BY** ⇒ 顶层类乱；改 `top_ancestor()` 确定性取根）。口径按用户裁定 **C＝根类**；`系统与规则台账` 已 apply（**35 页**重编目：`BMM业务动机模型/{影响因素24,手段4,组织机构5,知识会话1,评估1}`、`智能体开发本体/{工具4,…}`，**「挡板」目录消失**，未挂目录=0） |
| **新功能·目录全量重刷按钮 + 层级参数（2026-10-04 用户口径）** | **UI**：wiki 侧栏工具栏加 **[刷新目录]** 按钮 + **[层级 0–5]** 选择（缺省 **1**）；**本体模型库自动隐藏**（前端读 `/bodhi/ontology/kb` 判定）；点击 = 调 `GET /bodhi/folders/recategorize?kb_id=&depth=N&dry_run=0` → 全量重算 `category_path`/`wiki_path` + 重建目录（挂页+prune）→ 就地重载当前 tab 目录。**口径**：**第一层永远是模块短名**；`depth`=模块下再展开几层类（**1**＝直接 Thing 子类，界面共 2 层；**0**＝第一层模块下直接到**最底层的类**；上限 5，链不足则到本类为止）；**weknora 自带类型**（`concept`/`entity`/`summary`…）固定第一层 **`weknora`**、第二层是类型中文名（**与 depth 无关**）。**后端**：`_category_path_for()` + `_class_chain_labels()`（确定性沿 `parents[0]` 上溯）+ `recategorize(kb_id, dry_run, depth)` + 工具 `recategorize`（新增 `depth`）+ 路由 `depth` 参数；**本体模型库直接拒绝**（`reason`：它另有 `ontology:*` 目录）。**实测**：`agent:Stubtest` depth 0/1/2/3 → `测试挡板` / `工具` / `工具/挡板` / `工具/挡板/测试挡板`；`bmm:MainSystem` → `主系统` / `影响因素` / `影响因素/内部影响因素`；`summary`→`weknora/摘要`；本体库 `ok=false` ✅。前端补丁已写入 `patch_frontend.py`（**可重放**）+ 构建树 → vite 1m31s → 镜像 `e35f070709d6` → 容器重建；产物含 `folders/recategorize`、`层级 `、`刷新目录` ✅ |
| **修复·本体模块替换改为「硬替换」（2026-10-04 用户口径）** | **症状**：把 `agent:Stub` 从 `bmm:Means` 子类改成 `owl:Thing` 子类并重新加载 TTL 后，**本体图谱仍认为 Stub 是 Means 的子类**。**根因**：上传链路的第⑥步用的是 `apply_projection()`（`00/10` 投影 cypher 全是 **MERGE，只加不减**）⇒ 改继承后**旧边 `Stub→Means` 不会被删**（实测替换前 `agent:Stub` 的父类 = `['bmm:Means','agent:Tool']` —— 两次改动的残留）。**修法（用户裁定：不 merge，删整个模块再新增；本体与知识库松耦合，知识库依赖交巡检治理）**：新增 `ke_admin.replace_module_in_neo4j(module)` = ① 按**命名空间**（`http://example.org/<key>#`，取自编译产物/TTL `@prefix`）`MATCH (n) WHERE n.iri STARTS WITH $ns DETACH DELETE n`（**含所有边**）→ ② 把 `10_ontology.cypher` 里**首 `iri:` 在本命名空间**的语句灌回；并把 `upload_ttl` 第⑥步从"只 MERGE 投影"改成 **按模块硬替换（本模块 + scope 各一次）+ 全量 MERGE 投影**，回执给 `apply.replace[]`（每模块 deleted_nodes/inserted_statements/nodes_after/classes）。**实测**：`agent` 模块 → 删 **66** 节点、灌 **146** 语句、62 节点 / **15 类**；`agent:Stub` 父类 `['bmm:Means','agent:Tool']` → **`[]`** ✅；重投影本体库（153 页）后 **图谱 API 里 Stub 已无 Means 父边** ✅、类页「相关关系」只剩 `stubHasStatus` ✅ |






## 9. 外部集成工具契约（**挡板版**，投产后换真、契约不变）

> 三个工具都**先出挡板**。约定：**错误码统一** `unauthorized | not_found | timeout | bad_request`；**超时/不可达一律返回 `unknown`**（不猜）；**`_mock` 必须透传到结论页/问题**。

### 9.1 `open_source_catalog`（IT 工作平台 · 授权使用的开源软件清单）
```json
// 入参：product 必填；version 选填
{ "product": "Redis", "version": "6.2.7" }
// 出参（挡板 = 直接返回打标样例）
{ "found": true, "product": "Redis", "allowRanges": ["6.2.5-6.2.14"],
  "validUntil": "2026-09-30",
  "verdictHint": "in_range",              // in_range | out_of_range | not_in_catalog | unknown
  "source": "IT工作平台 · 可用开源软件版本清单",
  "_mock": true, "_source": "stub:open_source_catalog" }
```
- **挡板数据**：`docs/cases/技术方案评审/P3-materials-draft.md` §2 的**认证清单样例**（MySQL/Redis/Nginx/JDK/Kafka/PostgreSQL/Struts/Log4j，含"不收录"项）。
- **规则**：`validUntil` 已过期 → `verdictHint=unknown`（**不许按过期清单下结论**）；`not_in_catalog` → 由 R2 判"不符合"。
- **真实实现**：IT 工作平台**只读**查询，字段不变。

### 9.2 `review_flow_status`（评审流程状态）
```json
{ "flowNo": "2026-XXXX" }
→ { "flowNo": "2026-XXXX", "name": "某系统技术方案", "stage": "软件中心内部评审",
    "round": 1, "attachmentVersion": "v3", "assignees": ["评审组长", "专家A"],
    "updatedAt": "2026-10-01T10:00:00+08:00",
    "_mock": true, "_source": "stub:review_flow_status" }
```
- **挡板数据**：一张**状态机样例表**（`软件中心内部评审`/`组织级评审` × `第 1 轮`/`后续轮次`）。
- **用途**：判定"第一轮 → 逐章节推理；后续轮次 → 只比修改部分并溯源"（需求 block 89-95）。

### 9.3 `issue_publish`（问题发布）
```json
{ "flowNo": "2026-XXXX",
  "issues": [ { "rule": "部署架构-最低部署和灾备要求", "level": "严重",
                "location": "3.4.2", "excerpt": "原文摘录…",
                "suggestion": "改成…", "evidence": "session/<编号>/p2#轮3/段5" } ],
  "dry_run": true }
→ { "published": 0, "queued": 2, "issueIds": [], "dryRun": true,
    "_mock": true, "_source": "stub:issue_publish" }
```
- **挡板行为**：**只入本地队列并回执**（`queued`，**不出 `issueIds`**）；`dry_run=false` 才真发，**投产前禁止**。
- **一致性**：发布内容**必须与结论页同源**（同一 `evidence` 定位）。
- **真实实现**：流程/问题台账系统（写操作需权限与人工确认）。

### 9.4 与"一轮完整评估"的关系
| 工具 | 挡板下能验什么 | 投产前测不出的 |
|---|---|---|
| `open_source_catalog` | R2 的**版本比对链路**、`unknown` 处理、外部知识依赖数 | 真实清单的**时效/覆盖度**（清单本身是否最新） |
| `review_flow_status` | 轮次分流的**流程正确性** | 真实流程的**状态同步延迟/字段差异** |
| `issue_publish` | 问题**格式/级别/证据**是否合规、发布链路是否可用 | 真实台账的**去重/权限/回执语义** |


