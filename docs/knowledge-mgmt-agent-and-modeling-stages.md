# 企业知识管理智能体 · 建模工艺（草案 v0.2｜2026-10-11｜待讨论）

> 本版按 2026-10-11 你的口径**重写**，六处与 v0.1 不同（见 §0）。
> 上一版（v0.1）在 git 里可查（提交 `f1c531a`）。

---

## 0 一屏：本版改了什么

| # | v0.1（上一版） | **v0.2（本版，按你的口径）** |
|---|---|---|
| 1 | 过程件/资产只是"纪律"（R1–R6） | **进本体**：`bmm:Report` 报告**基类**带 `wiki_content`，报告类**继承即获得**；`assessment` **去掉** `wiki_content` → 纯 entity |
| 2 | 阶段 = `S0–S8 + G`（9 段 + 横切）**过于详细** | **合并为 5 段**（A 立项 / B 概念与本体对齐 / C 资产化 / D 综合与判断 / E 结项），每段**有明确的启动与结束条件** |
| 3 | 没写"会话级"规范 | 加 **基础规范（会话四律）**：**从一份文档或报告开始 → 以一份报告结束 → 结束报告必须含人员反馈 → 阶段报告是该阶段的过程证据** |
| 4 | 设计了两套流程视图（`Flow Profile`）+ 本地化三层 | **不固化流程**：建模要弹性 —— 只要求"每段满足自身启动/结束条件 + 会话四律"，顺序由会话决定 |
| 5 | 阶段声明放"仓库 yaml + 领域库只读快照" | **落「企业知识管理领域」库**：建一个**业务流程实体「企业知识管理流程」**来声明 |
| 6 | `S0 存量盘点` 单列一段 | **取消该段**：存量建模**用结构化建模导入**（本身就有文件来源，天然满足会话律一） |

**五段与五份报告一一对应**（这也是"报告类型到底几个"的答案来源，见 §1.5、§8-D1）：

| 段 | 报告（过程件，document 模式） | 资产（entity 模式） |
|---|---|---|
| A 立项定界 | **立项范围报告** | 目标/范围相关个体（现成类） |
| B 概念与本体对齐 | **概念与本体对齐报告** | 术语/概念个体 + 受控词表（+ 本体变更 ticket） |
| C 资产化 | **落库核对报告** | 类/关系/实例/台账（**资产主体**） |
| D 综合与判断 | **判断/评阅报告** | **`assessment`（明确结论，纯 entity）** + `Evaluation/Metric` |
| E 结项 | **结项报告（必须含人员反馈）** | `Advice`（改进建议） |

---

## 1 本体调整：报告类型体系（只改 TTL，不改代码）

### 1.1 现状（2026-10-11 实测探查）

| 事实 | 证据 |
|---|---|
| `render_mode` 的判定**已经是**"类或祖先声明了带 `rdfs:domain` 的 `wikiContent` → `document`，其余 `entity`" | `tools/ke-core/ke_ontology.py:302 render_mode()`（2026-10-09 口径，**明确写了"不引入任何新注解"**） |
| `bmm:wikiContent` 目前**只声明了 domain = `Assessment`** | `ontology/BMM完整版.ttl:654` |
| `agent` 侧把 `:Advice` **追加**进 `bmm:wikiContent` 的 domain（跨模块同名属性被编译器禁止，只能追加 domain） | `ontology/智能体开发完整版.ttl:351-364` |
| 因此**现在只有 2 个文档类**：`bmm:Assessment`、`agent:Advice` | 上面两处 |
| 四个"报告"（`DesignSpec` 设计单 / `ToolContract` 工具契约 / `InstallGuide` 安装指引 / `Evaluation` 评测）**现在都不是文档类**，它们能整篇渲染是靠 `save_knowledge(stage="report")` **显式** `mode="document"`（"报告分支不依赖本体判定"） | `tools/ontology-mcp/server.py:2097`（`"mode": "document"` + 注释） |
| BMM 现有 **29 类**；`agent` 现有 **12 类** | 探查输出（`tools/diag/_ttl.log`） |

> 结论：**你要的效果不需要改代码** —— 因为判定规则本来就是"看有没有 `wikiContent`（含继承）"。
> 需要改的只有 **TTL**：把 `wiki_content` 从"挂在 `Assessment` 上"改成"挂在报告基类上"。

### 1.2 目标形态

```turtle
# ontology/BMM完整版.ttl（新增）
:Report a owl:Class ;  rdfs:label "报告"@zh ;
    rdfs:comment "过程件基类：带整篇正文的报告/方案/评阅/结项等，供人讨论·评审·反馈·溯源·取证；"
                 "**不参与机器判定**（机器判定只读 entity 资产）。"@zh .
:wikiContent a owl:DatatypeProperty ;
    rdfs:label "报告正文"@zh ;
    rdfs:domain :Report .        # ← 关键：domain 从 Assessment 改到 Report
```

| 报告类型 | 归属 | 继承 | 说明 |
|---|---|---|---|
| `bmm:Report`（**报告·基类**） | bmm | `owl:Thing` | **新增**；`wikiContent` 的 domain 就是它 |
| `bmm:ScopingReport` 立项范围报告 | bmm | `bmm:Report` | **新增**（阶段 A） |
| `bmm:AlignmentReport` 概念与本体对齐报告 | bmm | `bmm:Report` | **新增**（阶段 B） |
| `bmm:LandingReport` 落库核对报告 | bmm | `bmm:Report` | **新增**（阶段 C） |
| `bmm:AssessmentReport` 判断/评阅报告 | bmm | `bmm:Report` | **新增**（阶段 D） |
| `bmm:ClosureReport` 结项报告 | bmm | `bmm:Report` | **新增**（阶段 E）；**必须含人员反馈**（见 §2） |
| `bmm:RequirementAnalysisReport` 需求分析报告 | bmm | `bmm:Report` | **新增**（你举的例子：BMM 领域建模也会产报告） |
| `agent:DesignSpec` 设计单 | agent | **改**为 `bmm:Report` | 从"靠 report 分支显式指定"升级为"本体声明" |
| `agent:ToolContract` 工具契约 | agent | **改**为 `bmm:Report` | 同上 |
| `agent:InstallGuide` 安装指引 | agent | **改**为 `bmm:Report` | 同上 |
| `agent:Evaluation` 评测 | agent | **改**为 `bmm:Report` | 同上 |
| `agent:Advice` 改进建议 | agent | `bmm:Report`（建议） | 现在**已经是**文档类（domain 追加）；改继承后**去掉那句 domain 追加**，更干净 |
| `bmm:Assessment` **评估/判断** | bmm | **不继承** `bmm:Report` | **保持纯 entity**：一条 assessment = **一个明确结论的判断**（可判定、可聚合、可比较），不是综合报告 |

### 1.3 两份实现路径（先说清风险，再动手）

| | 主方案（你的想法，推荐） | 兜底方案（现成模式，零风险） |
|---|---|---|
| 做法 | 新增 `bmm:Report` + `wikiContent` domain 指它；各报告类 `rdfs:subClassOf bmm:Report` | 不新增基类，直接把报告类**追加**进 `bmm:wikiContent` 的 domain（agent 侧给 `Advice` 就是这么做的） |
| 优点 | **一处声明、全链继承**；以后新增报告类不会再漏 | 与现状同一套路，**已验证可用**，零编译器风险 |
| 风险 | **需先验证编译器/投影支持"跨模块 `subClassOf`"**（`agent:* subClassOf bmm:Report`）—— 编译器已知会禁"跨模块同名属性"（E3），跨模块父类**未验证** | 每个新报告类都要记得追加 domain（**易漏**）；类多时 domain 会很脏 |
| 建议 | **先做主方案**；若编译器报错 → 立刻切兜底（两者**可并存**：基类 + domain 双保险） | |

> 顺带：`render_mode` 的判定**只看 domain 显式命中**（`ke_ontology.py:313` 注释），所以——**兜底方案里忽略任何一处 domain 追加，那个报告类就变回 entity**，正文也就落不进去（会被 `skipped_document_nodes` 拦下）。这是"易漏"的具体后果。

### 1.4 迁移影响（改完要跑什么）

| 影响 | 动作 |
|---|---|
| 新增 6 个报告类 + 改 4~5 个已有类 | `ontology/sources/*.ttl` → **编译**（`tools/ontology-compiler`）→ `refresh_ontology_kb.sh` 重投影本体库页 → 巡检应 0 新告警 |
| `bmm:Assessment` 去掉 `wiki_content` | 存量 assessment 页**已写入的正文不回滚**（历史页保持原样）；**新写**的 assessment 走 entity 渲染（正文由定义/属性/关系生成）→ 这与你的口径一致 |
| 报告类改归属（如既往用 `page_type=summary` 的报告页） | 用 **两段式 `retag_preview → retag_apply`** 迁移；受影响页先 `audit_scan(scope=similar)` 与 `context_scan` 看影响面 |
| 报告类 vs entity 类的落库路径 | 报告类：`save_knowledge(stage="report")` **必须带正文**；entity 类：`stage="graph"`（**不要**带正文，带了也没用） |

### 1.5 报告类型清单 = **待你确认**（见 §8-D1）

本版提议：**阶段报告 5 类（A–E）+ BMM 域报告 1 类（需求分析报告）+ agent 报告 5 类（设计单/工具契约/安装指引/评测/改进建议）= 11 个报告类**，另加 1 个基类。
你说"是不是就这 5 个，要看阶段定义的需要" —— **5 就是阶段报告数（A–E）**；BMM 域与 agent 侧的 6 个是**既有产出**改继承，不算"新阶段"。

---

## 2 基础规范（**会话四律**）—— 不管有多少阶段都必须遵守

| 律 | 规范 | 落地机制（用现成能力，不新造） |
|---|---|---|
| **律一 · 起点** | 每次知识管理会话**必须从一份文档或一份报告开始**（有来源：文档 id / 报告页 slug / 结构化文件），**不允许空口建模** | 会话起始页记来源 + 各产物带 `source_refs`；**存量建模 = 结构化建模导入**（`import_probe/plan/apply`），它也有文件来源，天然满足 |
| **律二 · 终点** | 每次会话**必须以一份报告结束**（`bmm:ClosureReport` 结项报告），不允许无声结束 | 结项报告落库即"会话完成"；`audit_scan` 可查"有建模会话却没有结项报告" |
| **律三 · 人员反馈** | **结束报告必须含人员反馈**：谁、何时、意见、是否接受（不接受要写理由） | 结项报告固定小节 `## 人员反馈`（字段固定）；**没有人员反馈的结项报告 = 未完成** |
| **律四 · 证据** | **阶段报告是该阶段过程的证据**（每段一份）；报告里的每条主张要能回溯；**报告不作为机器判定依据** | 报告引用只指**资产**或**L3 指针/坐标**（`source_refs`）；机器判定只读 entity 资产 |

**报告写作规范**（借 IEEE 29148 的四条 + 我们自己的两条）：

| # | 规范 | 说明 |
|---|---|---|
| P1 | **单一** | 一份报告只讲一件事（一段一事），不写大杂烩 |
| P2 | **无歧义** | 术语用 B 段定稿的术语表；本地叫法写别名 |
| P3 | **可验证** | 每条结论带证据引用；不确定的显式写"未知/受限"（宁 unknown 勿瞎判） |
| P4 | **可追溯** | 结论 ↔ 依据（`source_refs`）；谁写的（会话编号）、谁看的（审阅人） |
| P5 | **结论与依据分离** | 报告正文给"结论"，依据在引用里（不混写） |
| P6 | **受限项显式** | 因"不改本体/缺知识/工具是挡板"做不到的，逐条列 + 挂建议编号 |

**资产规范（与报告对称）**：

| # | 规范 |
|---|---|
| A1 | **只有报告类（`bmm:Report` 及子类）带 `wiki_content`**；其余一律 entity（正文由定义/属性/关系生成） |
| A2 | **`assessment` 是"一条明确结论的判断"**，不是报告：一条 assessment = 一个可判定/可聚合的结论（对象 + 依据 + 结论 + 置信度 + 未知项） |
| A3 | 每个资产**必须有来源**（无 C1）与**唯一标识**（如 `systemNo`/`sessionNo`/`adviceId`） |
| A4 | 资产之间可以有关联，但**不跨库**（跨库概念走「企业共享概念模型」，只读） |

---

## 3 阶段（5 段）：每段的**启动条件 / 结束条件**

> 段可跳过、可回环（§4），但**每段"结束条件"是可检查断言**；`启动条件` 缺一条就不许开始。
> 报告列 = 该段**产出的过程件**（这是"阶段报告"一词的含义，也是"报告类型有几个"的来源）。

| 段 | **启动条件**（缺一不可） | **结束条件**（可检查） | 产出报告 | 产出资产 | 门禁工具 |
|---|---|---|---|---|---|
| **A 立项定界** | ① 有一份**文档或报告**，且**带来源 id**；② 用户意图明确（哪个领域 / 哪份材料 / 想得到什么）；③ 目标**领域知识库**已确定 | ①《立项范围报告》已落库（report 模式、带整篇正文）；② 写明**范围 / 目标 / 边界 / 不做什么 / 干系人**；③ **人已确认**（审阅人 + 时间） | `bmm:ScopingReport` 立项范围报告 | 可选：`bmm:Goal`/`Objective` 等目标个体 | `save_knowledge(dry_run)` 类型校验；`skipped_document_nodes` 为空 |
| **B 概念与本体对齐** | A 的《立项范围报告》在库；材料可取（`doc_outline`/`grep_chunks`/原文件） | ①《概念与本体对齐报告》；② **术语表**（每术语：定义 / 别名 / 来源 + **一问三答**：谁消费 · 做什么推理 · 要多确定）；③ 逐条给"**要不要本体化**"结论（J1–J7 + 保鲜成本决策卡）；④ 表达不了的 → **改进建议**（`adviceCategory=本体类`），改本体走两段式并**人批准**；⑤ **人已裁决术语** | `bmm:AlignmentReport` 概念与本体对齐报告 | 术语/概念个体 + 受控词表（`owl:oneOf`）+ `agent:Advice`（本体类） | `ontology_types` 现查；`audit_scan(scope=similar)` 无未裁决近重复/域外同名 |
| **C 资产化** | B 的《对齐报告》在库；**或**直接是一份**结构化文件**（存量建模走这条，也是"有文件来源"）；本体类型可用（`ontology_types` 查得到） | ① 资产已落库（entity 模式：类/关系/实例/台账）；② 每实例**有唯一标识 + 有来源**；③《落库核对报告》（逐条核对 + 例外清单）；④ 巡检**无 C1（无来源）/ B1（类型不在本体）**；⑤ **人抽检** | `bmm:LandingReport` 落库核对报告 | **资产主体**：类/关系/实例、台账页 | `save_knowledge(stage="graph")`；`import_*`（结构化）；`audit_scan`；`skipped_document_nodes` 为空 |
| **D 综合与判断** | C 的资产齐（判定所需输入都查得到）；判断规则/口径明确（来自 B 的术语与规则） | ① 每条**判断**落成**一条 `assessment`**：对象 + 依据 + 结论 + 置信度 + **未知项**；②《判断/评阅报告》（人可读、带证据引用）；③ 反例与受限项显式；④ **人已复核** | `bmm:AssessmentReport` 判断/评阅报告 | **`bmm:Assessment`（纯 entity）** + `agent:Evaluation`/`Metric`（如做评测） | 结论必带证据引用；`graph_query` 可复算；`reference_lookup` 取外部权威 |
| **E 结项** | 本会话各段报告齐全（至少 A、C、D；若跳过 B 必须在对齐报告缺失处**写明理由**） | ①《结项报告》已落库；② **含 `## 人员反馈`**（谁 / 何时 / 意见 / 是否接受 + 理由）；③ 汇总：本次结论 + 受限项 + **改进建议**（`adviceStatus=待受理` 已进总览页）；④ 会话标记完成 | `bmm:ClosureReport` **结项报告（必须含人员反馈）** | `agent:Advice`（技能类/工具类/知识类/本体类） | 结项报告存在 + 人员反馈非空；建议挂编号并进总览页 |

**为什么是这 5 段**（对应你"要整合"的要求）：

| 从 v0.1 的 9 段 + G | 合并到 v0.2 |
|---|---|
| `S1 范围目标` + `S2 场景能力` | → **A 立项定界**（一份报告说清范围/目标/边界） |
| `S3 概念术语` + `S4 本体对齐` | → **B 概念与本体对齐**（术语与"要不要本体化"一次谈完；缺类→建议） |
| `S5 实体化落库` + `S0 存量盘点`（改结构化导入） | → **C 资产化**（存量也走这里，因为**有文件来源**） |
| `S6 过程件报告` + `S7 智能体·技能·工具开发` + `S8 评估` 的"判断"部分 | → **D 综合与判断**（报告 + `assessment` 判断；设计单/契约/安装指引/评测按需在这一段产出） |
| `S8 验收` + `S9/G 治理` 的收尾 | → **E 结项**（含人员反馈；建议进总览页） |
| `G 治理`（巡检/升级降级/版本） | → **不是阶段**：`audit_scan`/`audit_purge`/`retag_*` 是**随时可用的门禁与治理动作**（横切） |

---

## 4 弹性衔接：**不固化流程**

**原则（就一句话）**：只约束"**每段自己的启动/结束条件**" + "**会话四律**"；**不规定全局顺序**，也不搞两套流程。

| 常见走法（**示例，不是规范**） | 序列 | 适用 |
|---|---|---|
| **自上而下** | `A → B → C → D → E` | 有新需求/新方案要建模（目标驱动） |
| **自下而上** | `C → B → D → E`（A 在 C 后补，或并入 D 之后的报告里补范围） | 从存量/现状出发，先落资产再归纳概念 |
| **存量导入** | `C（结构化文件导入）→ D → E` | 台账/清单/表格批量入库（**有文件来源**，律一满足） |
| **专项推翻重做** | 直接 `C'`（同段第二轮回环） | 上一轮资产有问题，重落一版 |

**允许 / 不允许**：

| 行为 | 允许？ | 约束 |
|---|---|---|
| 跳过某段（如无新概念 → 跳过 B） | ✅ | 必须在**结项报告**里写明"为何跳过"（律四：证据链不能断） |
| 回环、迭代（同一段多轮） | ✅ | 同一段的报告**版本+1**（`wiki_page_revisions` 留痕）；不得覆盖不改版 |
| 并行做不同子系统 | ✅ | 会话仍要**一份**结项报告（可含多个子系统结论） |
| 没有任何起点文件的"聊天建模" | ❌ | 违反律一（必须先有文档或报告） |
| 忙完不写结项报告 | ❌ | 违反律二 |
| 结项报告没有人员反馈 | ❌ | 违反律三（**这是硬门禁**） |
| 用报告当"事实来源"去下机器判定结论 | ❌ | 违反律四 + 资产规范 A1 |

> **与技术方案评审流程的区别**：评审是**制度化流程**（固定轮次、有流程状态与责任人，将来还要对接 `review_flow_status`）——所以它必须固化；
> 建模是**探索性工作**，顺序会随发现变 —— 所以**给弹性，但把"报告 + 人员反馈"这两条纪律收紧**（建模产物比评审结论更容易失控）。

---

## 5 阶段声明：落「企业知识管理领域」库的**「企业知识管理流程」实体**

### 5.1 落点与形态（推荐：**零本体新增**）

| 项 | 设计 |
|---|---|
| **落在哪个库** | **企业知识管理领域**（`.env` 的 `BODHI_KNOWLEDGE_MGMT_KB_ID`，实测 `6effde80-…`）—— 与"技能/工具定义也归这个库"一致 |
| **声明主体** | 一个页：**「企业知识管理流程」**，`page_type = bmm:BusinessProcess`（**已存在**的类，**无需改本体**），entity 模式（不是报告类） |
| **各阶段** | **A–E 每段一页**：`page_type = bmm:BusinessProcess`（子流程）或 `bmm:CourseOfAction`（行动方案）；用 BMM 现有关系与主流程页相连：`bmm:realizesProcess` / `bmm:realizedBy`（若觉得语义不准，退一步用正文小节列出，关系只作弱关联） |
| **每段页承载** | 该段的 **`stageId` · 启动条件 · 结束条件 · 产出报告类型 · 门禁 · 谁做（人/智能体）** —— 写在 `definition` 与 data 属性里（entity 渲染即可读） |
| **报告类型怎么引用** | 报告是**本体的类**（`bmm:ScopingReport` 等），段页里以**类名 + 链接到本体模型库的类页**引用（跨库只读，不复制定义） |
| **谁读/谁写** | **知识管理智能体只读**（读阶段声明来指导会话）；**运维/人**维护该库（与"智能体只写领域库"的边界一致） |
| **为什么不用报告页** | 声明是**资产**（要被工具读、可聚合、稳定），不是过程件；一旦写成报告页，就违反 A1（只有报告类带 `wiki_content`） |

### 5.2 备选（语义最准，但**要改本体**，走闭环二）

新增：`bmm:Stage`（阶段）类 + 关系 `bmm:processHasStage`（domain `BusinessProcess` → range `Stage`）+ `bmm:stageProducesReport`（domain `Stage` → range `Report`）+ 数据属性 `stageId` / `stageEntry` / `stageExit`。

- 优点：阶段是**一等公民**，`graph_query` 直接可查；"每段的报告/条件"是结构化字段而不是文本。
- 代价：**动本体**（新增 1 类 + 2 关系 + 3 数据属性）→ 必须人批准 + 编译 + 重投影 + 巡检过。
- 我的建议：**v0.2 先用 5.1**（零本体改动就能跑，且完全满足"在库里声明"）；**等阶段真的稳定**（跑过 2–3 个领域）再按 J1–J7 判据决定是否升到这里。

### 5.3 声明页的字段（v0.2 用页面约定承载，示例）

```
页：企业知识管理流程            page_type=bmm:BusinessProcess   kb=企业知识管理领域
  ├─ 定义：企业知识管理（领域建模 + 业务智能体设计开发）的**通用阶段骨架**；
  │        每段有独立启动/结束条件；会话遵守"四律"（从文档/报告开始 → 以报告结束 → 含人员反馈 → 报告即证据）。
  ├─ 子页（关系：realizedBy）：A 立项定界 / B 概念与本体对齐 / C 资产化 / D 综合与判断 / E 结项
  └─ 字段：stageSetVersion=1 ｜ 适用领域=（按 L1 profile 可覆写子集/顺序/门槛/别名）

页：A 立项定界（段）              page_type=bmm:CourseOfAction
  ├─ 定义：启动条件 = ①有文档或报告(带来源) ②意图明确 ③目标领域库确定；
  │       结束条件 = 《立项范围报告》落库 + 范围/目标/边界/不做什么/干系人齐 + 人确认
  ├─ 产出报告：bmm:ScopingReport（立项范围报告）
  └─ 门禁：save_knowledge(dry_run) 类型校验；skipped_document_nodes 为空
（B/C/D/E 同构：B→bmm:AlignmentReport｜C→bmm:LandingReport｜D→bmm:AssessmentReport｜E→bmm:ClosureReport）
```

### 5.4 声明自身的治理（同样守纪律）

| 事项 | 规则 |
|---|---|
| 变更 | **版本+1**（`stageSetVersion`）+ 变更记录页；改语义要人批准 |
| 影响面 | 变更前用 `context_scan` / `audit_scan(scope=similar)` / `retag_preview` 出影响面 |
| 领域本地化 | 领域可覆写：**段的子集 / 顺序 / 门槛 / 术语别名**；**不可改**段的语义与结束条件（结束条件是硬门禁） |
| 引用完整性 | 段页引用的报告类必须在本体里存在（本体缺类 → 先走 B 段的"缺类→建议"路径） |

---

## 6 与现有技能 / 工具 / 智能体的对接（谁干哪段）

| 段 | 主力技能（单一真源＝`skills/<id>/SKILL.md`） | 主要工具 | 产出报告 |
|---|---|---|---|
| **A 立项定界** | `model_recommendation`（判需求种类→选路线）+ `session_provenance`（会话起始页） | `doc_outline` `get_document_info` `list_knowledge_chunks` `grep_chunks` `wiki_search` `wiki_read_page` `save_knowledge`(report) | `bmm:ScopingReport` |
| **B 概念与本体对齐** | `model_recommendation`（分级/决策卡）+ `domain_modeling`（概念抽取）+ `multimodal_extraction`（图/表里的概念） | `ontology_types` `graph_query` `audit_scan(scope=similar)` `context_scan/lookup` `link_candidates` `save_knowledge` | `bmm:AlignmentReport` |
| **C 资产化** | `domain_modeling`｜**`structured_modeling`（存量导入走这条）**｜`multimodal_extraction`｜`tech_review_extraction` | `save_knowledge(stage="graph")` `import_probe/plan/apply/state` `image_extract` `audit_scan` `reference_lookup` | `bmm:LandingReport` |
| **D 综合与判断** | `document_review`（按策略逐条判定）｜`revision_trace`（轮次比对/溯源）｜`skill_development`（设计单/工具契约/安装指引/评测） | `rules_of_policy` `graph_query` `review_apply` `ontology_types` `save_knowledge` `audit_scan` | `bmm:AssessmentReport` |
| **E 结项** | `session_provenance`（会话页 + 改进建议总览页） | `save_knowledge` `wiki_read_page` `audit_scan` | `bmm:ClosureReport`（**含人员反馈**） |

**合并后的智能体（`bodhi-knowledge-mgmt`）提示词结构**（一个入口，三步）：

```
① 读阶段声明：从「企业知识管理领域」库读「企业知识管理流程」+ A–E 段页（启动/结束条件、产出报告类型）
② 路由到技能：按当前要做的事选技能（§6 表）；技能仍"只限模型、不限类型"，类/关系一律 ontology_types 现查
③ 按"四律"收尾：确认起点文件 → 每段落一份报告 → 结项报告必含人员反馈 → 报告只作证据、判定只读资产
```

---

## 7 落地清单（v0.2，等 §8 裁决后开工）

| # | 动作 | 产出 | 粗估 |
|---|---|---|---|
| **1** | **TTL 调整**：新增 `bmm:Report`（带 `wikiContent`，domain 指它）+ 6 个报告类；4~5 个已有报告类改继承；**`bmm:Assessment` 摘掉 `wiki_content`** | `ontology/sources/*.ttl` diff | 0.5 天 |
| **2** | **验证跨模块 `subClassOf`**（`agent:* ⊂ bmm:Report`）；不行 → 切 §1.3 兜底（domain 追加） | 编译日志 + `render_mode` 抽查（4 类应为 document、assessment 应为 entity） | 0.5 天 |
| **3** | **编译 + 重投影 + 巡检**（本体库页重生成；`audit_scan` 过） | 编译产物 + 无新告警 | 0.5 天 |
| **4** | **建声明**：企业知识管理领域库建「企业知识管理流程」+ A–E 段页（§5.1 形态） | 该库 6 个 entity 页 | 1 天 |
| **5** | **技能标注"归属段"** + 5 类**报告模板**（固定小节：结论/依据/受限项/人员反馈） | 各 SKILL.md front-matter `stage: [A..E]` + 模板小节 | 1 天 |
| **6** | **技能修订**：`session_provenance`（会话页 + 结项报告 + 人员反馈字段）、`domain_modeling`/`structured_modeling`/`skill_development`（报告与四律） | 4 份 SKILL.md | 1 天 |
| **7** | **巡检新增 kind（可选）**：`session_without_closure`（有建模会话无结项报告）、`report_without_feedback`（结项报告无人员反馈）、`artifact_mode_mismatch` | `ke_audit` + scope + 文档 | 1 天 |
| **8** | **合并智能体** `bodhi-knowledge-mgmt` + **`allowed_tools` 收口**（顺带清掉两个已删工具名）+ 交付文档同步 | 提示词 + `gen_agents.py`/`agents.sql` | 2 天 |
| **9** | **案例回归**：技术方案评审案例跑 A–E，产出"阶段验收记录"；**收口重打包** | 记录页 + `bodhi2-delivery-20261010-5` | 1 天 |

---

## 8 待你裁决

| # | 问题 | 我的建议 |
|---|---|---|
| **D1** | **报告类的最终清单**：阶段报告 5 类（A–E） + BMM 域 1 类（需求分析报告） + agent 侧 5 类（设计单/工具契约/安装指引/评测/改进建议）改继承 = **11 个报告类 + 1 基类** | ✅ 就按这 11 个；**再多的报告类等阶段跑过一轮再提**（避免先造一堆没人用的类） |
| **D2** | 报告类怎么获得 `wiki_content`：**基类继承** vs **domain 追加** | 主方案＝**基类继承**（`bmm:Report`）；若编译器不支持跨模块 `subClassOf` → **立即切 domain 追加兜底**（零风险）；两者可并存 |
| **D3** | **5 段**是否合适（A 立项 / B 对齐 / C 资产化 / D 判断 / E 结项） | ✅ 5 段；若嫌多，**D+E 可合成"综合判断与结项"**（4 段，报告也合 1 类）——但那样"结束报告含人员反馈"的分量会变轻，我倾向保持 5 段 |
| **D4** | 会话四律的强度 | 律一/律二/律四＝**纪律**（技能与提示词约束）；律三（人员反馈）＝**硬门禁**（可被巡检查、无反馈的结项报告不算完成） |
| **D5** | 阶段声明的形态 | **5.1**（零本体改动：`bmm:BusinessProcess`/`CourseOfAction` 实例 + 页面承载） → 稳定后再考虑 5.2（新增 `bmm:Stage`） |
| **D6** | 是否新增巡检 3 个 kind | ✅ 建议加（否则"律二/律三"只是口号）；先做 `session_without_closure` + `report_without_feedback` |
| **D7** | `agent:Advice`（改进建议） | 现在靠 domain 追加成文档类 → **改继承 `bmm:Report`**，去掉那句 domain 追加（更干净） |
| **D8** | 本次先做哪几步 | **1→2→3→4**（本体调整 + 验证 + 声明落地），跑通再 5→6→7→8 |

---

## 9 附

### 9.1 本文**不做**的事

- ❌ 不新增知识库（用「企业知识管理领域」+ 领域库）。
- ❌ 不改 `render_mode` 的**代码**（判定机制本就要"看有没有 `wikiContent`"，只改 TTL）。
- ❌ 不给 `assessment` 保留 `wiki_content`（它必须是纯 entity）。
- ❌ 不固化流程顺序（只约束每段启动/结束 + 四律）。
- ❌ 不在本阶段处理"已删工具名在 DB 注册里残留"（并入 §7-8 一次收口）。
- ❌ 不重开 DDD 概念（库边界=限界上下文、共享概念模型=上下文映射，已裁决）。

### 9.2 术语对照

| 本文 | 仓库既有 | 说明 |
|---|---|---|
| **报告**（过程件） | 文档类 / `render_mode=document` / 报告页 | **只有报告类带 `wiki_content`** |
| **资产** | entity 知识（类/关系/实例/受控词表） | 参与机器判定 |
| **`assessment`** | `bmm:Assessment` | **一个明确结论的判断**（纯 entity，不是报告） |
| 阶段（A–E） | ❌ 无（本文首创） | 每段有启动/结束条件 + 一份报告 |
| 会话四律 | ❌ 无 | 起点·终点·人员反馈·报告即证据 |
| 企业知识管理流程 | — | 企业知识管理领域库里的 `bmm:BusinessProcess` 实例（阶段声明的载体） |
| 工序阶段 | SKILL.md 的 `stages:` | 技能内部步骤（与 A–E 不是一回事） |
| 写入阶段 | `save_knowledge(stage=…)`＝`graph`/`report` | 只管写入模式 |

> 版本：**v0.2**（2026-10-11 重设计）｜上一版 v0.1 见 git `f1c531a`｜待 §8 裁决后升 v0.3 并开工。



