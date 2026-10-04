# 领域概念的建模方法推荐（DDD × BMM 扩展 × 智能体本体）

> 日期：2026-10-01 ｜ 状态：**建议稿（未提交 git）** ｜ 触发：用户反馈"领域建模智能体没有 `knowledge_triage` 技能；P1 只做了重点规则所需知识的 triage，缺**其他领域概念该用哪种方法建模**的推荐"
> 面向环境：`skills/<id>/SKILL.md`（技能单一来源）+ `mcp_bodhi_ontology_*`（33 个工具）+ 本体仅保留 `bmm`

## 0. 一屏结论

1. **我此前文档里的 `S1 knowledge_triage` / `S2 ontology_modeling` / `S3 rule_engineering` 是自造名字**，环境里**不存在**；真实技能只有 3 个：`domain_modeling`（领域知识建模）、`structured_modeling`（结构化数据批量建模）、`document_review`（文档评审）。→ **需要"改口径"**（§2）。
2. **"针对材料推荐本体模型"的能力确实缺失**：现在每个技能第 0 步都是"**和用户单选模型**"（人拍），没有任何"看材料 → 给建议 + 依据"的环节。→ **建议新增技能 `model_recommendation`**（§5，含可直接落地的 `SKILL.md` 草案）。
3. **你列的那批概念（智能体/技能/MCP 服务/评审问题/任务/轮次/附件版本/角色/人员）不该都进业务本体**：按 DDD 分层后只有"角色/人员"进 `bmm`（且复用现成类与词表），智能体/技能/MCP 属**实现运行时层** → 建议**独立小模型 `agent`**，以 **`bmm:Means` 为锚**桥接（BMM 的 `Means` 定义本就含"**代理人、工具、能力、方法**"，桥接有本体依据）；任务/轮次/附件版本/问题按 D1 **落任务库**（§3、§4）。

## 1. 环境事实（我核准过的，作为建议的地基）

| 事实 | 证据 |
|---|---|
| **技能真源 = `skills/<id>/SKILL.md`**（front-matter：`id/name/when/models/stages/tools/version` + 正文）；**加目录+文件、MCP 重启后 `skills()` 自动列出，无需改代码** | `skills/README.md`；`tools/ontology-mcp/server.py` `SKILLS_DIR.glob("*/SKILL.md")` |
| 真实技能 **3 个**：`domain_modeling`(v0.2.0, models ea/bmm, default bmm)｜`structured_modeling`(v2, models bmm/ea)｜`document_review`(v2, **models: [bmm]**) | 各 `SKILL.md` front-matter |
| **设计类技能 `ea_overview_design` / `service_detailed_design` 已移除待重构**（2026-09-30）；`service_overview` 工具仍在 | `skills/README.md` §"已移除待重构" |
| **`document_review` 已实现需求 block 90-92 的流程**：取规则清单 → 按 `ruleImplementation` 分流（LLM软规则 / 图检索生成只读 Cypher）→ 参考规范 `reference_lookup` → `review_apply` 落"结论页"（版本化）→ `audit_scan` | `skills/document_review/SKILL.md` |
| MCP 工具（约 33 个）：`ontology_types` `skills` `save_knowledge` `doc_outline` `extract_state` `link_candidates`/`resolve_link_candidate` `import_probe/plan/apply/refresh/state` `audit_scan`/`audit_plan`/`audit_purge` `rules_of_policy` `graph_query` `reference_lookup` `review_apply` `retag_*` `context_*` `service_overview` `job_status` … | `server.py: tool_definitions()` |
| **本体只剩 `bmm`**（30 类 / 37 关系 / 15 属性）；`bmm:SubSystem` 注释即"**原 EA 的「应用系统」**"→ 系统层已并入 bmm；`ea` 及其"IT 服务层"（`Service/APIService/MCPService/SkillService`）**已下线/暂缓**（prompt 明写"随 IT 服务层一起暂缓下线"） | `artifacts/prompts/bmm_extraction.md`；`deploy/weknora-fork/config/agent_system_prompt.yaml` |
| `bmm:EnforcementLevel` 现有枚举 **只有** `Advisory`/`Override`/`Strict`（`rules_of_policy` 回执也只认这 3 档） | TTL + `extract_config.bmm.json` + `document_review` 回执注释 |
| 有 `retag_preview/apply/rollback` 与 `state/retag/*.json` 记录：**`客户` 曾在 `ea:Customer` ↔ `bmm:Resource` 之间来回搬** | `state/retag/3173…json`、`f258…json` |

## 2. 口径纠正：我文档里的 S1-S6 → 真实技能 / 缺口

| 我此前的叫法 | 环境真实状态 | 处置建议 |
|---|---|---|
| **S1 `knowledge_triage`**（知识分级） | **不存在**。最接近的是各技能的"第 0 步单选模型" + `ontology_types` 现查 | ❌ 删掉这个名字；→ **新增技能 `model_recommendation`**（§5）。我原来的 P1 产出应降级为该技能在"材料=规则清单"场景下的**一个子产物** |
| **S2 `ontology_modeling`** | `domain_modeling` **已存在**（分批交互、`dry_run`→`apply`、`link_candidates` 先登记后确认） ✅ | 改名对齐；**缺口**：没有"**本体迭代方案 + 验证报告**"的收尾产物（设计闭环用）→ 可作 `domain_modeling` 的收尾段，或并入 `model_recommendation` 的"演进建议" |
| **S3 `rule_engineering`** | `document_review` **已实现大半**（规则清单、按实现方式分流、图检索 Cypher、参考规范、结论页、`audit_scan`）✅ | 改名对齐；**缺口**：① 判定式**没有持久化载体**（目前塞在 `ruleImplementation` 字段/结论页）② 无"用例集/回归" ③ 无 `bmm:Summary`（总结级会丢档） |
| **S4 提取（图文）** | 正文 → `domain_modeling`；**表格 → `structured_modeling`**（我此前**漏了**这个技能，K08 设备表 / K09 技术栈表正对口）✅ | 改名对齐；**缺口**：图/多模态抽取没有契约（无坐标/置信度/`ambiguity`），现在只有 `source_text` 原句证据 |
| **S5 A/B 评估** | **不存在** | 新建（P5 用） |
| **S6 知识运维** | 部分：`audit_scan` 工具 ✅ + `retag_*`/`context_*` 治理工具 ✅ | **缺口**：没有"升级/降级/退役 + 留依据"的技能 |


## 3. 领域概念的建模方法推荐（DDD 分层 × BMM 复用/扩展）

### 3.0 三条护栏（先立规矩，再逐概念定）

- **R1｜owning model 唯一**：一个概念只能有**一个真源**，其他地方只能是**引用/投影**。理由：环境里已有教训——`客户` 被 `retag` 在 `ea:Customer` ↔ `bmm:Resource` 之间来回搬（`state/retag/*.json`），**双源必然打架**。
- **R2｜分层**：`业务动机层(bmm)` ／ `方案资产层(tsa 待建)` ／ `过程作业层(任务库表)` ／ `实现运行时层(agent 待建)` —— **跨层只做桥接，不做继承**。
- **R3｜判据**：沿用策略 §2.2 决策卡（J1-J7 + `年成本≈f×h` + 消费者 + Δ 由 A/B 实测）；**够不上 L1 的，就落 L2/L3/任务库**。

### 3.1 概念 → 方法对照表（你列出的全部概念）

| # | 概念 | DDD 定位 | **建议方法** | owning 载体 | 依据（为什么复用 / 为什么不硬建） | ❌ 不要怎么做 |
|---|---|---|---|---|---|---|
| 1 | **智能体** | 支撑域·**运行时** | **独立小模型 `agent`（~8 类）**，以 `bmm:Means` 为**锚**桥接（`agent:Agent → bmm:Means`） | 新模型 `agent` | BMM `Means` 定义**已含**"设备、能力、制度、技术、限制、**代理人**、工具或方法"→"智能体=手段"有本体依据；但它属实现层，混进 BMM 会污染业务动机层 | ❌ 塞进 `bmm` 当 `Resource`（就是 `客户` 那个坑） |
| 2 | **技能 Skill** | 支撑域·运行时 | **本体只建"引用个体"**：`agent:Skill{id, path, version, status}` | **真源 = `skills/<id>/SKILL.md`** | 技能真源已是**文件**（repo 单一来源、可打包 bundle）；本体里再写正文 = 双源 | ❌ 把 SKILL.md 正文抄进本体页 |
| 3 | **MCP 服务/工具** | 支撑域·运行时 | `agent:MCPService` + `agent:Tool{name, kind, transport}`，**可由 `tool_definitions()` 自动同步校验** | 真源 = MCP server 代码 | 工具清单机器可读（约 33 个）→ 一致性可自动检查 | ❌ 人工维护工具清单 |
| 4 | **评审问题 Issue** | 核心域·过程 | **任务库表**（D1）；要图关系就用 `review_apply` 的**结论页**（`slug=review/<文档>-<策略>`，版本化、带逐字证据） | 任务库 + wiki 结论页 | 过程数据不是本体；结论页已有稳定身份/版本/证据 | ❌ 为 Issue 建本体类（与结论页双源） |
| 5 | **评审任务** | 核心域·过程 | 任务库表；**弱桥**：用 `bmm:CourseOfAction` 作方案/项目锚（CoA 定义"可体现为一个项目或一份需求"），任务表外键引用 | 任务库 + 属性引用 | 任务不是业务动机对象 | ❌ 建 `ReviewTask` 本体类 |
| 6 | **轮次** | 过程 | 任务库字段 `roundNo` | 任务库 | 纯状态字段 | ❌ 同上 |
| 7 | **附件版本** | 过程 + 载体 | 任务库字段 + **L3 指针**（`doc_id`/版本/定位） | 任务库 | 版本是文件属性 | ❌ 复制附件内容进本体 |
| 8 | **角色 Role**（组长/专家） | 通用域·组织 | **受控词表**（如 `bmm:ReviewRole`，或并入既有角色词表） | `bmm` 词表（`owl:oneOf` + 个体） | 角色是**枚举型**，不是对象 | ❌ 每个角色建一个类 |
| 9 | **人员 Person**（组长/专家） | 通用域·组织 | **直接用 `bmm:OrganizationUnit` 实例** | `bmm`（复用） | 现有 `bmm:assessedBy`/`createdBy` 的 range **已经就是** `OrganizationUnit`；**"专家核实"天然落在 `bmm:Assessment`**（`assessedBy`→OrganizationUnit，`minCardinality=1`） | ❌ 新建 `Person`/`Employee` 类 |
| 10 | （附）**技术方案本身** | 核心域·对象 | 复用 `bmm:CourseOfAction` + 待建 `技术方案(tsa)` 资产层 | `bmm` + `tsa` | CoA/需求/项目的既有语义正好 | ❌ 新建与 CoA 平行的"方案"类 |

> **这张表最大的价值**：把"要建模"从 9 个概念**收敛为 2 个新增动作**（`agent` 模型；`bmm:ReviewRole` 词表）+ **复用 3 个现成概念**（`OrganizationUnit` / `Assessment` / `CourseOfAction`）+ **4 个概念明确不建本体**（问题/任务/轮次/附件版本 → 任务库）。

### 3.2 关于 DDD 与 BMM 结合（正面回答你的问题）

- **BMM 不是 DDD 的替代品，它天然对应 DDD 的"问题空间"**：`DesiredResult`(目的/目标) / `Means`(手段) / `Influencer`(影响因素) / `Assessment`(评估) 就是"为什么做、做什么"。
- **DDD 负责切限界上下文，BMM 负责在业务动机上下文里表达动机与约束**。落到本需求：
  - **核心域**＝技术方案评审（`BusinessPolicy → OperativeBusinessRule` 判定；`CourseOfAction` 是被评审的方案）；
  - **支撑域**＝知识资产运维（L1/L2/L3 + 保鲜成本）；
  - **通用/实现域**＝智能体运行时（`agent` 模型：Agent/Skill/MCPService/Tool/Task/Run/Artifact）。
- **BMM 扩展的正确姿势（不是"往 bmm 里塞类"）**：
  1. **优先复用**：`OrganizationUnit`（谁）/`Assessment`（评估核实）/`CourseOfAction`（项目·需求）/`Means`（手段）/`Resource`（资源）；
  2. **要挂实现层，就只加"锚关系"**：`agent:X -[:bmm:realizes | :bmm:influencesMeans]-> …`，让实现层**单向引用**业务层；
  3. **确实要加实体类时，加在独立模型里**（`agent`），**不要**动 `bmm` 的核心类；
  4. **枚举/词表可以进 bmm**（`EnforcementLevel`、`ReviewRole`）——它们不携带实体身份，不会造成双源。
- **要不要恢复 `ea`？建议：不恢复。** ① 系统层（`MainSystem/SubSystem/ITAsset`）已在 bmm；② `ea` 的 IT 服务层（Service/APIService/MCPService/SkillService）本质就是"智能体运行时"，**用一个聚焦的 `agent` 模型比复用大而全的 ea 更稳**；③ 恢复 ea = 把"只留 bmm"推倒重来，还会重启 `客户` 那类 retag 拉锯。

## 4. 对 `bmm` 的扩展建议（"必做 6 + 可选 3 + 不做 5"）

| 类型 | 项 | 为什么 | 改动 |
|---|---|---|---|
| **必做** | ① `bmm:Summary`（总结） | 新版需求第 4 档执行级别；`rules_of_policy` 回执与 `document_review` 现在只认 `Strict/Advisory/Override` → **不加会丢档**（R3 合理性规则就是"总结"） | 加 1 个枚举个体 |
| **必做** | ② `knowledgeNeed`（数据属性，`OperativeBusinessRule`） | 需求 block 92"按规则需要的知识内容提取"的落点；也是抽取技能的输入契约 | 加 1 个 data property（D2 已定） |
| **必做** | ③ `bmm:KnowledgeSession`（类） | **知识来源＝智能体会话**：会话被删也不能断溯源 → 必须作为知识入库。属性：`sessionNo`/`sessionName`/`agentName`/`tenantName`/`initialQuestion`/`startedAt`/`partNo`/`isAuthoritative`/`sessionStatus` | 1 个类 + 属性 |
| **必做** | ④ `bmm:SessionStatus`（词表） | `active` / `archived`（分页存档用） | 1 个词表 + 2 个体 |
| **必做** | ⑤ `bmm:sourceSession`（对象属性，**`owl:Thing → KnowledgeSession`**） | 让**任何知识页/知识项**都能指回会话上下文 | 1 个对象属性 |
| **必做** | ⑥ `bmm:sourceLocator`（数据属性，`owl:Thing`） | 会话内定位：`分页序号 + 轮次/段落`（可复核） | 1 个 data property |
| 可选 | ⑦ `bmm:sessionFor`（`KnowledgeSession → CourseOfAction`） | 该会话服务于哪个方案/行动（弱桥） | 1 个对象属性 |
| 可选 | ⑧ `bmm:ReviewRole` 词表（组长/专家） | 角色是枚举；人员仍用 `OrganizationUnit` | 1 个词表（`owl:oneOf` + 个体） |
| 可选 | ⑨ `DocType → 规则适用范围` 绑定 | 需求 block 108"按文档/流程类型区分适用规则"（可推广性） | 视是否本体化决定 |
| **不做** | `ReviewIssue` / `ReviewTask` / `ReviewRound` / `AttachmentVersion` / `Person` | 分别是过程数据（任务库）/ 已有 `OrganizationUnit` 承接 | **0 改动** |

## 5. 建议新增技能：`model_recommendation`（按材料特点推荐本体模型与建模方法）

> 这是你指出的**真实缺口**：现在所有技能的第 0 步都是"**和用户单选模型**"（人拍），**没有"看材料 → 给建议 + 依据"的环节**。下面是可直接落地的草案（放 `skills/model_recommendation/SKILL.md`，MCP 重启后 `skills()` 自动列出；**未确认前我不落地**）。

```markdown
---
id: model_recommendation
name: 建模方案推荐（按材料特点推荐本体模型与建模方法）
description: 读一份材料（需求/概设/技术方案/Excel/图）+ 用户意图 → 抽"概念候选" → 与现有模型比覆盖度
             → 结合本库既有约定 + LLM 知识 + 网络搜索 + 知识检索，给出"每个概念用哪个模型、建什么类、为什么、代价多大"的建议单；人工确认后才进入建模
when: 用户问"这份材料该怎么建模""这些概念要不要建本体""该建到哪个模型/哪一层"时
models: [bmm, agent, tsa]      # 仅作默认面；实际一律 skills()+ontology_types() 现查
default_model: bmm
stages: [profile, concepts, fit, advise, confirm]
tools: [get_document_info, doc_outline, list_knowledge_chunks, grep_chunks, skills, ontology_types,
        wiki_search, wiki_read_page, service_overview, audit_scan]
version: 0
---

# 建模方案推荐（技能）

## 0. 第 0 步：先问清三件事（不要先动工具）
1. **材料**：哪份（知识库文档 / 附件 / 表格 / 图）？
2. **意图**：要"建知识页"、"写规则判定"、还是"先判断该不该建本体"？
3. **目标库**：建议单写到哪里（默认"领域模型库"，见 D1：本体迭代方案的载体）。

## 1. 材料画像（只读）
- `get_document_info` → 文档类型/大小/切片数；
- `doc_outline(kb_id, knowledge_id, budget_tokens)` → 章节骨架 + **图/表位置**（表 → 记下来给 `structured_modeling`，图 → 记下来给多模态）；
- `list_knowledge_chunks` / `grep_chunks` → 抽样正文。

## 2. 概念候选抽取（每条必须带原文依据）
按 6 类抽：**对象 / 规则 / 过程 / 组织·角色 / 资源 / 实现**。
输出候选表：`概念 | 类别 | 出现位置 | source_text（原句） | 频次`。
> 抽不到原句的概念**不要报**（宁缺毋滥）；拿不准进 `待确认`。

## 3. 覆盖度比对（**类型一律现查，禁止猜**）
- `skills()` 看**技能面**（有哪些技能已经在做这件事）；
- `ontology_types("bmm")` 等**逐个模型**取类/关系/数据属性（含 `requires`/`affects` 依赖线索），对照候选概念标：
  **命中已有类** / **需扩展** / **需新建模型** / **不该建本体**。

## 4. 外部参考（你提的"LLM 知识 + 网络搜索 + 知识检索"）
- 先 `wiki_search`/`wiki_read_page` 查**本库既有约定**（最优先，避免与存量冲突）；
- 再用 **LLM 知识**对标框架（BMM / DDD 分层 / TOGAF / ArchiMate / UDM）里同类概念的标准处理；
- 若开网：`web_search` 查行业做法；**内网不可达就标"未获取"**，不要编。

## 5. 建议单（唯一产物）
| 概念 | DDD 定位 | 建议方法 | owning 载体 | 判据(J1-J7) | 保鲜成本(f×h) | 消费者 | 反例 | 置信度 | 原文依据 |
|---|---|---|---|---|---|---|---|---|---|
（一行一个概念；**推荐 ≠ 决定**）

**建议单还要写 3 段**：
1. **不建清单**：哪些概念明确不进本体（+ 理由 + 落到哪个库/表）；
2. **新增清单**：要新建的模型/类/词表（含最小闭集与影响面）；
3. **待确认清单**：证据不足或需业务拍板的项。

## 6. 收尾
- 建议单**不是本体**：只写"需求建模结果"（D1）——用 `save_knowledge`/结论页写进**领域模型库**；
- 用户确认后，**才**转给 `domain_modeling`（正文）/`structured_modeling`（表格）/`document_review`（规则判定）执行；
- 收尾 `audit_scan`。

## 纪律
1. **类型现查**：类/关系/属性一律 `ontology_types` 现查；查不到就换，**不要猜、不要自造**。
2. **每条建议必须有原文依据**（`source_text`），无依据的进"待确认"。
3. **推荐 ≠ 决定**：模型选择与"是否承担保鲜成本"由**人**拍板（策略 §2.2 决策卡）。
4. **不写实现**：不写代码/SQL/表结构；只写概念→模型的映射与理由。
5. **一次只推一个材料**，跨材料要分开出建议单（可对比）。
```

**依赖清单**（三选一，按你口味）：
| 方案 | 说明 | 代价 |
|---|---|---|
| A（推荐） | 只用**现有 33 个工具**（如上草案） | 0 代码改动，**新建 skills 目录即可** |
| B | 加 1 个新工具 `models_overview()`：一句话列出**所有模型 + 类/关系计数 + 跨模型桥接** | 小改动 `server.py: tool_definitions()`，省"逐个模型查"的 token |
| C | 加 `model_fit(concepts=[…])`：把候选概念直接与各模型术语做**匹配打分** | 中改动，但推荐更客观 |

## 6. 决策回填（M1-M5：**你已裁决** 2026-10-01）

| # | 决策点 | **你的裁决** | 落地动作 |
|---|---|---|---|
| **M1** | 是否现在新建 `agent` 模型？ | **后者**：先落**任务库字段**；`agent` 模型留到 **P6 设计闭环演练**正式提建模方案 | 写入策略 §5（P6 行）与本文 §3.1 第 1-3 行备注 |
| **M2** | 智能体/技能/MCP 是否只建"引用个体"？ | **是，但还要判"消费关系"**：领域模型里若要技能/智能体，必须判断**最终提取的知识是否会被真实技能与智能体引用/依赖**（作为**需求溯源、测试验收、评测的源头**）；**判据与"规则用知识的 triage"完全一致**；**知识 ≠ 最终落地产物，但两者之间必须有消费关系** | 已写进技能 §4（消费关系判定）+ §9 建议单新增"消费关系"列；P1 决策卡同步口径 |
| **M3** | 角色/人员/核实怎么建？ | **都放到 `model_recommendation` 技能里评估决策**（不预设） | 已写进技能 §6（给默认建议 + 判据，最终由建议单承载、人确认） |
| **M4** | 是否采纳 `model_recommendation`？ | **要开始物化了**；且技能**自身要具备多种建模能力**、并**能判断需求种类**（流程建模 → 可能适合 ea 类模型；智能体/技能/用例 → 可能适合 DDD+智能体模型） | ✅ **已物化** `skills/model_recommendation/SKILL.md`（§1 判需求种类 + 六条路线；§5 DDD 定位与"是否新建/恢复模型"判定） |
| **M5** | 是否恢复 `ea`？ | （你未反对）**不恢复**；但"流程类需求"仍可由技能**建议新建流程模型**（可用 `ontology/EA完整版.ttl` 作底稿） | 已写进技能 §1 路线表 + §5 口径 |

> 你新提的"需求澄清/变更登记"需求 → 已给方案：**`docs/knowledge-ops-strategy.md` §4.2**（三层 + 铁律 + 现成能力映射 + 待物化技能 `requirement_change_log`）。

## 7. 已物化 / 待物化

| 对象 | 状态 |
|---|---|
| `skills/model_recommendation/SKILL.md` | ✅ **已物化**（`skills/README.md` 目录已登记；**MCP 重启后 `skills()` 会列出**） |
| `skills/README.md` 技能目录 | ✅ 已加 `model_recommendation` 行 |
| `docs/knowledge-ops-strategy.md` §4.2 变更登记方案 | ✅ 已写（含 `requirement_change_log` 草案） |
| `skills/requirement_change_log/SKILL.md` | ⏸ **待你点头**（一行话我就物化） |
| `agent` 模型（新） | ⏸ 推迟到 **P6**（M1） |
| `bmm` 的 2 处必做扩展（`bmm:Summary` + `knowledgeNeed`） | ⏸ **P2** 一起做 |
| `技术方案`(tsa) 模型 | ⏸ **P2** |


