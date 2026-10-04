# 本体重建分析：以《技术方案评审智能体需求》为起点

> 日期：2026-10-01 ｜ 范围：bodhi2 本体模型结构（不含实现代码） ｜ 状态：**待你确认的草案**（本文档尚未提交 git）

## 0. 结论先行

| 判断 | 依据 |
|---|---|
| **现有 BMM 覆盖了"评审判据（规则侧）"，且质量不错** | `BusinessPolicy` / `BusinessRule`(操作性/结构性) / `EnforcementLevel` / `ruleScope`(适用范围) / `ruleImplementation`(实现方式) / `ruleReference`(参考规范) 齐备，正好对应需求里"规则的 5 列" |
| **但现有本体完全覆盖不了"被评审对象（技术方案侧）"** | 需求明确要识别的：系统等级 A/B/C/D、部署方式（同城/异地 × 云上/云下）、灾备方式（跨云双活/热备、单云双活/热备、多云多活/热备、数据级备份）、云/机房/地区、设备申请（类型/配置/数量/用途）、技术栈+版本+认证清单 —— **bmm/ea 里一个都没有**（新版需求已不提 RTO/RPO → 不建 `AvailabilityTarget`） |
| **`ea`（9 类流程分解）对本需求价值极低** | 需求评的是"技术方案"（部署/架构/技术栈/规则符合性），不是"业务流程分解"；用户口径：只保留 bmm，其它可废弃或重建 |
| **还缺三类"骨架性"概念** | ① 文档/章节 ↔ 规则集绑定（需求第 158 行"逐个章节寻找对应规则"、第 171 行"按文档/流程类型区分适合的规则"）；② 证据与置信度（第 160 行"可信度存疑→人工协助"、图像识别置信度）；③ 评审过程产物（问题/轮次/专家核实/修改溯源，第 157-167 行） |

**建议结构**：`bmm`（保留，小幅增强）+ `tsa`（技术方案资产，**新建，最大缺口**）；`review`（评审过程与溯源）**不进本体** —— 按你的 **D1 裁决**它属**任务库的运行产物**（见 §5 决策记录）。

**D1 裁决后的三库分工（2026-10-01 已确认）**

| 库 | 角色 | 内容 |
|---|---|---|
| **企业本体模型库** | **本体真源**（骨架/词表） | `bmm`、`tsa`、规则的**类**与受控词表（TTL 编译投影） |
| **技术方案评审领域模型** | **需求建模结果**（需求↔实现闭环） | 规则实例 + 智能体目标 + 方法手段（技能/工具/存量 API 整合）+ 影响因素 + 评价 + **本体迭代方案与验证报告** |
| **技术方案评审任务知识库** | **技术方案实例 + 运行结果** | 方案事实（系统/等级/部署/灾备/技术栈/设备/图证据）+ 轮次/问题/专家核实/溯源 + **A/B 效果** |

> 两个闭环：**业务闭环**（任务库跑 A/B → 反推智能体/技能改进）｜**设计闭环**（需求变更 → 是否值得本体化 → 本体迭代，方案与报告入领域模型库、可溯源）。详见 `docs/knowledge-ops-strategy.md` §2.1。

## 1. 需求要素清单（可追踪）

> 行号 = `技术方案评审智能体需求.docx` 提取文本的行号，便于逐句核对。

### 1.1 判据侧：评审规则体系（BMM 已覆盖）

| 需求（原文行） | 需要的概念 | 归属 | 现状 |
|---|---|---|---|
| blocks 17-21「业务策略 / 规则名称 / 规则描述 / **适用范围** / **执行级别** / **参考文档**」 | 规则、策略、适用范围、执行级别、参考文档 | `bmm` | ✅ `BusinessPolicy`→`BusinessRule`、`ruleScope`、`EnforcementLevel`、`ruleReference` |
| blocks 18-21/26-28「建议 / 授权覆盖 / **严格执行** / **总结**」 | 执行级别受控词表 | `bmm` | ⚠️ **新版需求多出第 4 档「总结」**（block 28「合理性-行动方案合理性」，需求 block 101 说它用于"在其他问题基础上总结"）；现有 TTL 只有 `Advisory/Override/Strict` → **P2 补 1 个个体 `bmm:Summary`（总结）** |
| blocks 91-92「一般性规则…LLM 推理（+ 知识库事实核查）/ 重点规则…先知识提取再推理」 | 规则的**实现方式**（LLM软规则/图检索/形式化）+ 规则的**知识需求** | `bmm` | 🔶 `ruleImplementation` 有；"规则需要哪些知识"无概念 → **D2 裁决：只加 1 个数据属性 `knowledgeNeed`，`RuleKnowledgeNeed` 类暂不建**（见 §4.1 修订块） |
| block 104「协助分析需要补充的本体模型…生成图查询语句或形式化规则或 python 代码」 | 规则 ↔ 查询/推理脚本的绑定 | `bmm` | 🔶 建议 `Rule` 增加 `ruleArtifact`（cypher/python/自然语言） |

### 1.2 被测侧：技术方案里必须识别的知识（**最大缺口**）

| 需求（原文行） | 需要的概念 | 现状 |
|---|---|---|
| blocks 32-41「系统等级 A/B/C/D」「部署方式 同城/异地（云上/云下）」「灾备方式：跨云双活、跨云热备、单云双活、单云热备、多云多活、多云热备、数据级备份、双活/热备」 | 系统等级、部署方式、灾备方式（受控词表） | ❌ 全无 |
| blocks 53/55「浦江云+张江云=同城，内蒙云=异地 → 同城+异地；同城跨云双活，异地多云热备」 | 云、地区（同城/异地）、组合判定规则 | ❌ 全无 |
| block 43「现状部署 vs 本方案要求部署 → 有变化就涉及设备申请」 | 现状/目标部署、**变更判定** | ❌ 全无 |
| blocks 44-45「设备申请：设备类型、部署位置、配置规格、数量、用途；区分云上/云下、地区、机房、云名称」 | 设备申请单、资源规格、位置 | ❌ 全无 |
| block 49「技术栈（表格勾选 + 版本）」block 27「开源软件必须在**中心认证清单**范围内，**包括版本号**」 | 技术栈项、版本、认证清单 | ❌ 全无 |
| block 46/51-55「部署信息主要在**部署图**里，需要**图像识别**」 | 图像证据 + 置信度 | ❌ 全无 |

### 1.3 过程侧：评审运作（问题 / 溯源 / 轮次）

| 需求（原文行） | 需要的概念 | 现状 |
|---|---|---|
| block 87「任务：流程编号、名称、评审轮次、方案附件（不同轮次版本不同）」 | 评审任务、轮次、附件版本 | ❌ |
| block 90「问题内容：文档位置、原文摘录、触发的评审规则、违规说明、修改建议、问题级别（严重/一般/建议）」 | 评审问题（6 要素） | ❌（`bmm` 无；现在只有 wiki 页 + `source_refs`） |
| block 95「后续轮次：比对修改部分 → 溯源到对应问题；无法溯源的要重新评审」 | 修改↔问题溯源 | ❌ |
| block 98「每个问题需要 ≥1 位专家核实；专家可要求针对上下文重新评审」 | 专家核实、复审 | ❌ |
| block 99「增补文档/对话/邮件澄清可作为问题更新依据」 | 问题依据（多来源证据） | ❌ |
| block 100-167「问题聚类、**问题总结**（供组织级评审）」 | 聚类、总结 | ❌ |

### 1.4 通用性与元数据

| 需求（原文行） | 需要的概念 | 现状 |
|---|---|---|
| block 108「同一套智能体/技能/MCP 要能评需求、概设、详设文档，只要『文档/流程类型』能区分出适合的规则」 | **文档类型 ↔ 规则集**绑定 | ❌ |
| block 92「提取知识可信度存疑 → 人工协助子任务」「图像识别需反馈可信度」 | 证据 / 来源(human/agent/import) / 置信度 / 人工确认 | ⚠️ 仅 wiki `source_refs`（巡检 C1 用），**本体级无** |
| block 98-165「专家核实/澄清」 | 人工确认状态 | ❌ |


## 2. 覆盖度矩阵

| 需求能力 | `bmm`（32类/37关系/15属性） | `ea`（9类/14关系/1属性） | 判定 |
|---|---|---|---|
| 业务策略 → 业务规则（含派生） | ✅ `BusinessPolicy`/`BusinessRule`/`isDerivedFrom`/`guides`/`manages` | — | **保留** |
| 规则执行级别 | ✅ `EnforcementLevel`（Strict/Override/Advisory，与需求 blocks 18-21/26-28 完全一致） | — | **保留** |
| 规则适用范围 / 实现方式 / 参考规范 | ✅ `ruleScope`/`ruleImplementation`/`ruleReference` | — | **保留** |
| 规则"需要哪些知识" | ❌ | — | **补**（§4.1） |
| 系统 / 子系统 / 硬件资产 / 组织 | 🔶 `MainSystem`/`SubSystem`/`HardwareAsset`/`ITAsset`/`OrganizationUnit`（够用但**缺等级/云/机房/位置**） | — | **增强** |
| **系统等级 A/B/C/D** | ❌ | ❌ | **新建** |
| **部署方式（同城/异地 × 云上/云下）** | ❌ | ❌ | **新建** |
| **灾备方式（9 种模式）** | ❌ | ❌ | **新建** |
| **云 / 机房 / 地区** | ❌ | ❌ | **新建** |
| **设备申请（类型/规格/数量/用途/位置）** | ❌ | ❌ | **新建** |
| **技术栈 / 版本 / 认证清单** | ❌ | ❌ | **新建** |
| **云上/云下（部署载体）** | ❌ | ❌ | **新建**（需求 block 37/45 明确可区分） |
| 文档 / 章节 / 评审要点 | ❌ | ❌ | **新建** |
| 文档类型 ↔ 规则集 | ❌ | ❌ | **新建** |
| 证据 / 来源 / 置信度 / 人工确认 | ⚠️ 仅 wiki `source_refs`（巡检用） | — | **新建（通用元数据）** |
| 评审问题（6 要素）/ 轮次 / 专家核实 / 修改溯源 / 聚类 | ❌ | ❌ | **落业务层**（D1：进**任务库**，**不建本体类**） |
| 流程分解（活动/任务/步骤/IT 服务/CRUD） | — | ✅ 9 类 | **废弃**（本需求用不到；将来要用再重建） |

## 3. 差距清单（按优先级）

**P0｜不建就做不成需求的（技术方案侧）**
1. 系统等级 `SystemLevel`（A/B/C/D）+ 等级与最低部署/灾备要求的**绑定**
2. 部署 `Deployment`（同城/异地、云上/云下）+ `Cloud`/`DataCenter`
3. 灾备 `AvailabilityPattern`（9 模式）+ `DRStrategy`
4. 设备 `DeviceRequest`（类型/规格/数量/用途/位置）
5. 技术栈 `TechStackItem`（组件/版本）+ `CertifiedProductVersion`（认证清单）
6. ~~可用性指标 `AvailabilityTarget`（RTO/RPO）~~ → **不建**：新版需求 block 30-49「需要识别的信息」已无 RTO/RPO，规则 block 26 也只判"部署方式 × 灾备方式"（避免为不判定项建模）

**P1｜决定"能不能自动评审"的（判据-对象-文档的串联）**
7. `SolutionDoc` / `Section` / `ReviewPoint`（文档与章节）
8. `DocType` → 规则适用范围绑定（block 108 通用性）
9. 规则 → 所需知识（block 92"按规则需要的知识内容提取"）→ **D2 裁决：暂不建类**，先以规则数据属性 `knowledgeNeed` 承载（见 §4.1 修订块）

**P2｜决定"评审能不能闭环"（过程与溯源）**
10. `Evidence`（文档/章节/摘录/坐标/来源/置信度/人工确认）
11. `ReviewIssue`（位置/摘录/规则/说明/建议/级别）+ 与规则/章节/证据的关系
12. `ReviewTask`/`ReviewRound`、`ExpertVerification`、`IssueResolution`（修改↔问题）、`IssueCluster`

## 4. 建议的模型结构（3 个模型）

> 沿用现有工程约定：**一个模型 = 一个 TTL 文件**；身份从 TTL 自读（`rdfs:label` = 完整名、`bodhi:shortName` ≤16 = 目录名）；受控词表用 `owl:equivalentClass [ owl:oneOf (...) ]` + 个体（与 `bmm:EnforcementLevel` 同款写法）；对象属性给 domain/range。

### 4.1 `bmm`（保留 + 小幅增强，不动存量类）

```
:RuleKnowledgeNeed   rdf:type owl:Class ;            # 【草案·D2 之后不建】规则需要的知识（需求 block 92/104）
    rdfs:label "规则知识需求"@zh ;
    rdfs:comment "为判定某条规则必须从文档提取的知识项（可人工协助补齐）"@zh .
:RuleImplementation  rdf:type owl:Class ;            # 受控词表：实现方式
    owl:equivalentClass [ owl:oneOf ( :ImplLlm :ImplGraphQuery :ImplFormal ) ] .
#   个体：ImplLlm=LLM软规则 ｜ ImplGraphQuery=图检索 ｜ ImplFormal=形式化(python/规则引擎)
:needsKnowledge   (OperativeBusinessRule → RuleKnowledgeNeed)   # 规则需要什么知识
:ruleArtifact     (OperativeBusinessRule, datatype)              # 现成 cypher/python 片段（block 104）
```

> **⚠️ D2 裁决后的修订（2026-10-01 你已拍板）—— 上面的新类先不建**
> - 规则侧**只补少量数据属性**，且**只补本 POC 需求本身要求**的。P2 实际动作 = **只加 1 个**：`:knowledgeNeed`（datatype，`OperativeBusinessRule`，注释"判定该规则前必须先提取的知识项"）。
> - 其余用**已有**能力：`ruleScope` / `ruleImplementation`（可写"图查询/规则表/py"）/ `ruleReference` / `hasEnforcementLevel`（`Strict`=严格执行、`Override`=授权覆盖、`Advisory`=建议，**已与需求 blocks 18-21/26-28 逐字一致**）。
> - **不做**：`RuleKnowledgeNeed` / `RuleImplementation`(类) / `ruleArtifact` / `RuleCondition` / `RuleCheck` / `CheckResult` / `RuleStatus` —— **等双闭环技能完成后**，用本 POC 需求的领域建模来判定"是否真的需要新增本体"。
> - 判定式本体与验证报告 → 落**领域模型库**（不新增类；设计闭环证据）。

### 4.2 `tsa` —— 技术方案资产（**新建，最大缺口**）

短名建议 `技术方案`（目录名直观）或 `tsa`；命名空间 `http://example.org/tsa#`。

| 类 | 中文 | 关键数据属性 | 对应需求 |
|---|---|---|---|
| `SolutionDoc` | 技术方案文档 | `docNo`(流程编号) `docName` `docVersion` `docType` | block 87（轮次/版本） |
| `Section` | 章节 | `sectionNo` `title` `text` | block 90"逐个章节" |
| `System` | 系统 | `systemNo` `systemAbbr` `deployScope`(总行/分行子公司) | blocks 32-33/44-45（或复用 `bmm:MainSystem`，见 D4） |
| `SystemLevel` | 系统等级 | 受控词表：`LevelA`/`LevelB`/`LevelC`/`LevelD` | blocks 32-33 |
| `Deployment` | 部署方案 | `deployMode`(同城/异地) `hostingEnv`(云上/云下) `isCurrent`(现状/目标) | blocks 34-43 |
| `Cloud` | 云 | `cloudName`(浦江云/张江云/内蒙云/贵州云) `cloudVendor` | blocks 53/55 |
| `DataCenter` | 机房 | `dcName` `region` | block 45 |
| `AvailabilityPattern` | 灾备方式 | 受控词表 9 种（跨云双活/跨云热备/单云双活/单云热备/多云多活/多云热备/数据级备份/双活/热备） | blocks 38-41 |
| `DRStrategy` | 灾备策略/等级最低要求 | `isMinimum`(是否最低要求) `requirementText` | block 26 |
| `DeviceRequest` | 设备申请 | `deviceType` `spec` `quantity` `purpose` | blocks 44-45 |
| `TechStackItem` | 技术栈项 | `component` `version` `isOpenSource` | block 49 |
| `CertifiedProductVersion` | 认证清单条目 | `product` `version` `certNo` `validUntil` | block 27 |

**对象属性**：`docHasSection`、`sectionDescribes`(→System/Deployment/TechStackItem)、`systemHasLevel`、`systemDeployedVia`(→Deployment)、`deploymentOnCloud`(→Cloud)、`deploymentInDC`(→DataCenter)、`deploymentUsesPattern`(→AvailabilityPattern)、`systemRequiresDR`(→DRStrategy)、`deviceRequestedFor`(→System)、`deviceHostedIn`(→Cloud/DataCenter)、`docUsesTechStack`(→TechStackItem)、`techStackCertifiedBy`(→CertifiedProductVersion)。
> **等级 → 最低要求 的绑定**（block 26 的核心）：建议用 `systemHasLevel`(→`SystemLevel`) + `levelRequiresDR`(`SystemLevel`→`DRStrategy`) + `levelRequiresDeployMode`(`SystemLevel`→`DeploymentMode`) 表达，判定式再比对"实际 ⊇ 最低"。
### 4.3 ~~`review`~~ —— 评审过程与溯源（**D1 裁决：不进本体**）

> **2026-10-01 你已拍板**：这些是**任务运行产物**，落**技术方案评审任务知识库**（页面/表单结构化字段即可），**不建本体类**。
> 下表保留为**任务库的字段清单参考**（若将来发现"问题↔规则↔章节"的**跨库图查询**确有价值，再按 F 步骤升级为本体 —— 这正是**设计闭环**的判断点）。

短名建议 `评审`；命名空间 `http://example.org/review#`。

| 类 | 中文 | 关键属性 | 来源 |
|---|---|---|---|
| `ReviewTask` | 评审任务 | `flowNo` `name` | block 87 |
| `ReviewRound` | 评审轮次 | `roundNo` `attachmentVersion` | block 87/95 |
| `ReviewIssue` | 评审问题 | `location` `excerpt` `description` `suggestion` | block 90（6 要素） |
| `IssueSeverity` | 问题级别 | 受控词表：`Severe`严重/`General`一般/`Suggestion`建议 | block 90 |
| `IssueCluster` | 问题聚类 | `clusterName` | block 100 |
| `IssueResolution` | 修改记录 | `changedSection` `changedText` `wordCount` | block 95/12 |
| `ExpertVerification` | 专家核实 | `expert` `confirmed` `at` | block 98 |
| `ReviewSummary` | 评审总结 | `text` | block 101 |

**对象属性**：`roundRaisesIssue`、`issueTriggeredByRule`(→`bmm:BusinessRule`)、`issueLocatedInSection`(→`tsa:Section`)、`issueVerifiedBy`、`issueResolvedBy`、`changeTracedToIssue`、`issueClusteredInto`、`issueEvidencedBy`(→`Evidence`)。

### 4.4 通用元数据：`Evidence`（跨模型复用）

```
:Evidence  rdf:type owl:Class ;        # 证据
    #  数据属性：docTitle / docType / section / excerpt(原文摘录) / locator(页码|URL|图坐标)
    #            origin(human|agent|import) / confidence(0-1) / humanConfirmed(bool) / capturedAt
:EvidenceOrigin 受控词表
```
> 与 `OntologyKBAgent` 仓库里 UDOM 的 `Evidence`（`doc_title/doc_type/fragment/location/kb` + `provenance{origin,confidence}`）**概念一致** → 建议直接对齐，便于两套本体互操作。
> **D4 实验（2026-10-01）已证明它的必要性**：两张部署图的结论必须能回看原图坐标 + 置信度 + 人工确认 → 建议字段再加 `bbox`(图坐标) / `ambiguity[]` / `knowledgeGaps[]`（见 `docs/cases/技术方案评审/image-extraction-experiment.md` §5）。

### 4.5 依赖与目录

```
tsa     ：独立
bmm     ：独立（保持现状）
# review：不进本体（D1）→ 作为「任务库」的页面/表单结构化字段
```
目录名（`bodhi:shortName`）：`BMM业务动机模型`（不动）｜`技术方案`（或 `tsa`，二选一）→ 见 D6。

## 5. 关键决策点（需要你拍板）

| # | 决策 | 我的建议 | **你的裁决（2026-10-01）** |
|---|---|---|---|
| **D1** | 评审过程（任务/轮次/问题/专家核实/修改溯源）**进本体**，还是作为**业务数据**（表单/台账，不进本体）？ | 进本体 | **不进本体** → 落**任务库**（运行产物）；本体只放定义。已按此改 §0 / §4.3 / §4.5 |
| **D2** | "最低部署和灾备要求"怎么表达？ | 规则 + 结构化条件两层 | **同意（规则驱动）**；但**规则元数据只补少量、只补本 POC 需求要求的**，**新类（`RuleKnowledgeNeed`/`RuleCheck`/`CheckResult`…）暂不建**，等双闭环技能跑完再定 → §4.1 修订块 |
| **D3** | 系统等级/灾备方式/云 用**受控词表个体**还是 datatype 字符串？ | **受控词表个体**（`owl:oneOf`） | *（你的"D3 同意"对应的是判据阈值那条；本条未单独回复 → 我按"受控词表个体"执行，P2 出 TTL 时你可一眼否掉）* |
| **D4** | `tsa:System` 与现有 `bmm:MainSystem`/`SubSystem` 的关系？ | `tsa:System` **复用/指向** `bmm:ITAsset`，避免两套系统清单 | 待确认（P2 前定） |
| **D5** | `Evidence`（doc/section/excerpt/locator/origin/confidence/humanConfirmed）放哪个模型？ | 放 **`bmm`**（通用元数据）——若要保持 `bmm` 纯净则单建 `core` | 待确认（P3 前定；D4 实验已证明"证据+坐标+置信度"必须有地方放） |
| **D6** | 模型与目录命名：`技术方案` 还是 `tsa`？ | **中文短名 `技术方案`**（与 `BMM业务动机模型` 一致、≤16 字符过门禁） | 待确认（我按 `技术方案` 走） |
| **D7** | `ea` / `bmmfd` / `ea-service` / `ea-ownership` 的处置 | **废弃**：先从 `sources/` 清掉、级联删图库/wiki，再删模型文件 | **已定向**（你的口径：**只保留 bmm**） |

## 6. 推进计划（与本仓 PoC 计划对齐；见 `docs/knowledge-ops-strategy.md` §5）

| 阶段 | 本体侧动作 | 产出 | 门禁/验收 |
|---|---|---|---|
| **P0** ✅ | 决策定稿（本文档 D1-D7 + 策略文档 D1'-D6' 均已回） | 决策记录（本文 §5 / 策略 §8） | 你确认 —— **已完成** |
| **P1** | 无本体动作（S1 分级） | 知识清单表（含 J1-J7 打分） | L1 项 ≤12、每条有判据依据 |
| **P2** | 写 `技术方案`（tsa）TTL v0.1（**只覆盖"最低部署与灾备要求"最小闭包**）+ `bmm` **仅补 1 个数据属性** `knowledgeNeed` | `ontology/技术方案.ttl` → 编译产物 + 目录 + 页 | `shortName` 门禁、编译通过、`repair` 幂等、目录正确、`audit_scan` 无 C1/B1 |
| **P3** | 落 L1 实例（图/表提取，含坐标/置信度/待澄清） | 实例 + 证据 + 人工待办 | 低置信项必须进人工清单（D4 实验 §5 门禁） |
| **P4** | 写判定式（图查询/规则表/py）+ ≥14 用例（含 2 个"信息不足→unknown"用例） | 判定式 + 用例集 | 用例全过、结论带证据、宁 unknown 勿瞎判 |
| **P5** | 跑 A/B（3 方案 × 2 规则 × 2 轨 × 5 次） | 对照报告 → **价值入领域模型库** | 指标完整、结论可复现 |
| **P6** | 巡检 + 升级/降级 + **设计闭环演练**（需求变更 → 本体迭代方案与验证报告） | 运维报告 + 本体迭代方案 | 动作有依据、可回滚；报告入领域模型库 |

> 每个阶段都遵循现有纪律：**TTL 是唯一真源**、`bodhi:shortName` 必填、编译=投影=wiki 三层一致、`audit_scan` 全绿。

## 7. 本次边界（明确未做）

- **未改任何 TTL / 未动数据库 / 未跑 repair / 未启用 `vlm_config`**；本次只新增/更新三份文档（`docs/ontology-rebuild-analysis.md`、`docs/knowledge-ops-strategy.md`、`docs/cases/技术方案评审/image-extraction-experiment.md`），**全部未 git add、未提交、未推远端**。
- 待你确认 **D3-D6（分析文档）** 后我再进入 P2（写 TTL）——同样**提交前会先给你看 diff 并等你同意**。


