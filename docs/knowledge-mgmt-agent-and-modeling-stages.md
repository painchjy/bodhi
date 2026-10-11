# 企业知识管理智能体 · 建模工艺（草案 v0.5｜2026-10-11｜待讨论）

> v0.5 按你的口径收敛为 **4 个活动**：**A1 立项与需求分析**（A1+A2 合并，产出**需求分析报告**=别称"需求规格说明书"）、
> **A2 设计与规划**（可产出**多份设计单**，顺序或并行开发）、**A3 开发交付**（安装指引 + **评测方案**）、
> **A4 评测**（按评测方案评测 → **评测报告 + 改进建议**，并反馈评测结果）。**A5 文档评审不在本闭环、A7 巡检先不要**。
> 方法不变：报告是活动的产物；活动启动必声明**本体模型范围**；结束必做**范围质检 + 问题项反馈**。
> v0.4 及以前见 git（`fc64598` / `5a3ee89` / `98547ac` / `f1c531a`）。§1（报告类型体系与 D2 实测）除"加 1 子类"外沿用。

---

## 0 一屏

### 0.1 从"阶段"到"活动"（BPMN）—— 为什么换

| v0.3 的"阶段" | **v0.5 的"活动"** | 说明 |
|---|---|---|
| A 立项定界 + B 概念与本体对齐 + C 资产化 | **A1 立项与需求分析** | 合成一个活动：定范围 → 需求分析 → 领域建模（概念对齐与资产落库都在这里） |
| D 的"设计开发"部分 | **A2 设计与规划** | 可产出**多份设计单**（一个对象一份），并规划**顺序 / 并行**开发 |
| D 的"交付"部分 | **A3 开发交付** | 产物：**安装指引** + **评测方案** |
| D 的"判断/评测"部分 | **A4 评测** | 按 A3 的**评测方案**跑评测 → **评测报告 + 改进建议**（并反馈评测结果） |
| A5 文档评审 | ❌ **不在本闭环** | 属**技术方案评审业务闭环**（业务智能体 + `document_review` 负责） |
| A7 巡检治理 | ❌ **先不要** | 由运维智能体单独负责，不在本流程声明 |
| E 结项 | **会话终点（不是活动）** | 结项报告 + 人员反馈 |

> 好处：**报告与活动一一对应**；启动/结束条件挂在活动上（不依赖全局顺序）；与 **BPMN** 词汇（Process / Activity / Sequence Flow / Data Object / Gateway）直接对齐。

### 0.2 活动清单（**4 个** + 会话终点）

| # | 活动 | 主办技能 | **本体模型范围**（启动时必须声明） | 产物：**报告** | 产物：资产（entity） |
|---|---|---|---|---|---|
| **A1 立项与需求分析** | `model_recommendation` + `domain_modeling`（存量走 `structured_modeling`）/ `multimodal_extraction` / `tech_review_extraction` | **`bmm`**（目标/方案/计划/需求项；理由写进报告） | **需求分析报告**（**新增子类** `bmm:RequirementAnalysisReport`，**别名：需求规格说明书**） | `bmm:Goal`/`Objective`/`DesiredResult`/`CourseOfAction`/`OrganizationUnit`… + 类/关系/实例/受控词表 |
| **A2 设计与规划** | `skill_development`（设计段） | **`agent`**（`Agent`/`Skill`/`Tool`/`WorkKnowledgeBase`） | **设计单 × N**（`agent:DesignSpec`，**一个设计对象一份**）+ **开发规划**（顺序/并行、批次） | `Agent`/`Skill`/`Tool`/`WorkKnowledgeBase` 个体 |
| **A3 开发交付** | `skill_development`（脚手架/安装段） | **`agent`**（`InstallGuide`/`ToolContract`/`Stub`） | **安装指引**（`agent:InstallGuide`）+ **评测方案**（`bmm:Report`，标题"评测方案"） | `InstallGuide`/`ToolContract`/`Stub` 个体 |
| **A4 评测** | 评估技能（待物化） | **`agent`**（`Evaluation`/`Metric`） | **评测报告**（`agent:Evaluation`）+ **改进建议**（`agent:Advice`） | `Evaluation`/`Metric`/`Advice` 个体 |
| *会话终点（非活动）* | `session_provenance` | — | **结项报告**（`bmm:Report`，**必含 `## 人员反馈`**） | — |

**报告类型总账（v0.5）**

| 类型 | 状态 |
|---|---|
| `bmm:Report`（报告·基类） | **新增**；`bmm:wikiContent` 的 domain 由 `Assessment` **改指它** |
| `bmm:RequirementAnalysisReport` **需求分析报告**（别名：**需求规格说明书**） | **新增子类**（继承 `bmm:Report`）—— A1 的产出 |
| `agent:DesignSpec` 设计单 ／ `agent:InstallGuide` 安装指引 ／ `agent:Evaluation` 评测报告 ／ `agent:Advice` 改进建议 | **既有**（只改继承 `bmm:Report`，名字不变 → 技能零改动） |
| 评测方案 | 暂用 `bmm:Report`（标题"评测方案"）；要不要建子类见 **§8-D2** |
| `bmm:Assessment`（评估/判断） | **不在本闭环产出**（保留给业务闭环的"明确结论判定"）；且**摘掉 `wiki_content`** → 纯 entity |

### 0.3 每个活动的三件套

```
① 启动条件：有输入的文档或报告  ｜  **明确本体模型范围（选哪个模型 + 为什么）**  ｜  产出物与"反馈人"明确
② 产出：一份**报告**（过程件，给人看/评审/取证）  +  一批**资产**（entity，给机器判定）
③ 结束条件：报告已落库  ｜  **对该模型范围做质检**（类型在册·无越界·无 C1/S1 问题）  ｜  **问题项有明确反馈**（谁·处理·时间）
```

---

## 1 本体调整：报告类型体系（**只改 TTL，不改代码；1 基类 + 1 子类**）

### 1.1 现状（2026-10-11 **live Neo4j 实测**）

| 事实 | 证据 |
|---|---|
| `render_mode` 的判定**就是**"类或祖先声明了带 `rdfs:domain` 的 `wikiContent` → `document`，否则 `entity`" | `tools/ke-core/ke_ontology.py:302 render_mode()`（2026-10-09 口径，注释写明"不引入任何新注解"） |
| **现有文档类（报告类）恰好 5 个**：`bmm:Assessment`、`agent:Advice`、`agent:DesignSpec`、`agent:Evaluation`、`agent:InstallGuide` | live 实测：`bmm:wikiContent domains = ['agent:InstallGuide','agent:DesignSpec','agent:Advice','agent:Evaluation','bmm:Assessment']`，且这 5 个 `render_mode=document`、其余（`ToolContract/Agent/Skill/BusinessProcess`）`=entity` |
| 这 5 个是怎么来的：`bmm.ttl` 里 `wikiContent` 的 domain 写了 `Assessment`；`agent.ttl` 用**"domain 追加"**（不许跨模块同名属性）把 `Advice/DesignSpec/Evaluation/InstallGuide` 加进来 | `ontology/sources/bmm.ttl:656-658`、`ontology/sources/agent.ttl:94-99` |
| 报告分支 `save_knowledge(stage="report")` 对**非本体类型**（如 WeKnora 内建 `summary`）会**改写为 `bmm:Assessment`** | `tools/ontology-mcp/server.py:2073-2092`（回执 `page_type_adjusted`） |
| ⚠️ `render_mode` 的 domain 判定**只读 Neo4j 投影**：`data_properties()` 明确"JSON 产物只有计数、没有名单 → 只能空表兜底" | `ke_ontology.py:622-654`（`source="unavailable"` 时 `properties={}`） |
| BMM 现有 **29 类** / agent **12 类** | 探查（`tools/diag/_ttl.log`） |

> 结论：**你要的效果不需要改代码**；需要改的只有 **TTL**（把 `wiki_content` 从"逐个追加 domain"改成"挂在报告基类上"）。
> 另外**上线必须重投影**（见 §1.6 末），这是本轮实测新发现。

### 1.2 目标形态：**1 个基类 + 1 个子类（需求分析报告）**

| 类型 | 归属 | 变化 | 说明 |
|---|---|---|---|
| **`bmm:Report`（报告·基类）** | bmm | **新增** | `bmm:wikiContent` 的 domain 由 `Assessment` **改指它** |
| **`bmm:RequirementAnalysisReport` 需求分析报告**（别名：**需求规格说明书**） | bmm | **新增子类**（`rdfs:subClassOf bmm:Report`） | A1 的标准产出；别名用 `skos:altLabel`（编译器若不抽取，则写进 `rdfs:comment`） |
| `agent:Advice` 改进建议 / `agent:DesignSpec` 设计单 / `agent:Evaluation` 评测 / `agent:InstallGuide` 安装指引 | agent | **改继承**（`rdfs:subClassOf bmm:Report`），删掉那 4 行 "domain 追加" | 4 个既有报告类，**类型名不变** → **现有技能零改动** |
| `bmm:Assessment` **评估/判断** | bmm | **摘掉** `wiki_content`（不再进 domain） | **纯 entity**：一条 assessment = **一个明确结论的判断**（不在本闭环产出，保留给业务闭环） |
| 其余活动报告（设计单/安装指引/评测方案/评测报告/改进建议/结项报告） | — | **不再新增类型** | 用既有类型 + **标题规范**（"评测方案"、"结项报告" 等） |

> **为什么这样最省**：报告类只多 1 个（基类），4 个既有报告类只是"换个继承方式"（名字/技能/工具全不动），
> `Assessment` 摘掉一个属性就完成"报告与判断分家"。**没有一处需要改技能或工具代码。**

### 1.3 D2 结论：继承法**可用**（实测），兜底方案**保留**

| | 主方案（你提的，**已验通**） | 兜底方案（现状模式，零风险） |
|---|---|---|
| 做法 | `bmm:Report` 带 `wiki_content`；报告类 `rdfs:subClassOf bmm:Report` | 不建基类，把报告类**逐个追加**进 `bmm:wikiContent` 的 domain |
| 实测 | 编译器 **接受跨模块 `subClassOf`**：产物里 `parents(agent:Advice)=['bmm:Report']`；复算 4 类 `document`、`Assessment` `entity`（§1.6） | 生产上**正在用**（5 个 domain） |
| 取舍 | ✅ 采用（一处声明、以后新增报告类不再漏） | 保留为**兼容兜底**：若某次编译器/重投影出问题，可临时改回 domain 追加 |

### 1.4 上线动作（**五步，第 3 步最容易被漏**）

```
① 改 TTL：ontology/sources/bmm.ttl 加 :Report + wikiContent domain 改指 :Report（+ 摘掉 Assessment）
            ontology/sources/agent.ttl 把 4 行 domain 追加换成 4 行 rdfs:subClassOf bmm:Report
② 编译：python tools/ontology-compiler/compile.py validate && ... compile
③ 【必做】重新投影到 Neo4j（否则 render_mode 不生效 —— 实测结论）
         ：deploy/weknora-fork/refresh_ontology_kb.sh 或 /bodhi/ontology/load
④ 抽查：对 5 个类型问 render_mode（工具 `ontology_types` / 直接跑 ke_ontology.render_mode）
         → 期望：DesignSpec/InstallGuide/Evaluation/Advice/Report = document；Assessment/ToolContract = entity
⑤ 巡检：audit_scan 过（无 B1 新告警）；本体库页按新类重生成
```

### 1.5 迁移影响

| 影响 | 动作 |
|---|---|
| `bmm:Assessment` 由 document 变 entity | 存量 assessment 页**已写正文不回滚**（历史页保持原样）；**新写**走 entity 渲染（正文由定义/属性/关系生成）—— 与你的口径一致 |
| 4 个既有报告类只改继承 | 页面 `page_type` 不变（`agent:DesignSpec` 等），**无需 retag** |
| 若历史上有 `page_type=summary` 的报告页 | 用两段式 `retag_preview → retag_apply` 迁到 `bmm:Report`（或按内容选既有报告类） |
| 新增 `bmm:Report` | 本体库重投影后多一页类页；`bodhi:shortName`/label/color 按既有约定 |

### 1.6 实测记录（D2 验证，2026-10-11）

**环境**：沙箱 = `ontology/sources` 复制到仓库内临时目录 → 按 §1.2 改 TTL → 用**真编译器** `compile_all(out_dir=沙箱)`；
复算用**编译产物的 `parents`**（已跨模块解析）+ **TTL 里 `wikiContent` 的 domain**；另跑一次**生产 Neo4j 只读**对照。
脚本：`tools/diag/_exp_inherit.py`（日志 `tools/diag/_exp_inherit.log`、`tools/diag/_live.log`）。

| 检查点 | 基线（现状） | **改动后（继承）** |
|---|---|---|
| 编译器接受？ | ✅ `ok=True`，产物已写 | ✅ **`ok=True`**，产物已写 —— **跨模块 `subClassOf` 不被拒** |
| `wikiContent.domains` | `['agent:Advice','agent:DesignSpec','agent:Evaluation','agent:InstallGuide','bmm:Assessment']` | **`['bmm:Report']`**（一处声明） |
| `parents(agent:Advice)` | `[]` | **`['bmm:Report']`**（继承进产物 ✓） |
| `agent:DesignSpec / Evaluation / InstallGuide` | document（靠 domain 追加） | **document（靠继承）** ✓ |
| `bmm:Assessment` | document | **entity** ✓（按你的口径分家） |
| `bmm:Report` | （不存在） | **document** ✓ |
| `agent:ToolContract` / `Agent` / `Skill` / `bmm:BusinessProcess` | entity | **entity**（不受影响）✓ |
| 生产 Neo4j（live） | 5 个类 = document（与基线**逐条一致** → 复算忠实） | （重投影后再抽查，见 §1.4-④） |
| ⚠️ 运行时取数 | `data_properties source = neo4j`；沙箱禁用 Neo4j 时 `render_mode` 全回 `entity` | **证明**：domain 判定只认 Neo4j 投影 → **必须重投影** |

---

## 2 活动纪律（**会话四律 + 活动两律**）

| 律 | 规范 | 落地机制（现成能力） |
|---|---|---|
| **律一 · 起点** | 每次知识管理会话**必须从一份文档或一份报告开始**（带来源：文档 id / 报告页 slug / 结构化文件） | 会话起始页记来源 + 各产物带 `source_refs` |
| **律二 · 终点** | 每次会话**必须以一份报告结束**（`bmm:Report`，标题"结项报告"） | 结项报告落库即"会话完成" |
| **律三 · 人员反馈** | 结束报告**必须含 `## 人员反馈`**：谁 / 何时 / 意见 / 是否接受（不接受写理由） | 固定小节；**无反馈的结项报告 = 未完成**（硬门禁） |
| **律四 · 证据** | **报告是该活动过程的证据**；报告只作证据，**机器判定只读资产** | 报告引用只指资产或 L3 指针/坐标 |
| **律五 · 模型范围**（新） | **每个活动启动时必须声明本体模型范围**（选哪个模型 + 为什么），并在活动中**只在该范围内建模** | 写进会话页 + 该活动的声明页；结束时按范围质检（律六） |
| **律六 · 质检与反馈**（新） | **每个活动结束时必须对该模型范围做质检**；**发现的问题项必须有明确反馈**（现象 · 证据 · 处理：整改/驳回/接受 · 责任人 · 时间） | `ontology_types`（类型在册）+ `audit_scan`（B1 类型不在本体 / C1 无来源 / S1 近重复 / S2 域外同名 / D* 等）+ 越界检查（本次落库页 `page_type` 前缀 ∈ 声明范围）；问题项进该活动报告与结项报告 |

**报告写作规范**（P1–P6，不变）：**单一** · **无歧义**（用 B 步定稿的术语表） · **可验证**（每条结论带证据） · **可追溯**（结论↔依据 `source_refs`；谁写=会话编号、谁看=审阅人） · **结论与依据分离** · **受限项显式**（挂建议编号）。

**资产规范**（A1–A4，不变）：**只有报告类带 `wiki_content`**；**`assessment` 是"一条明确结论的判断"**（不是报告）；资产**必有来源与唯一标识**；资产**不跨库**（跨库概念走「企业共享概念模型」，只读）。

---

## 3 活动定义（4 个活动 + 会话终点）

> 每个活动三件套：**① 启动条件（含模型范围）② 产物（报告 + 资产）③ 结束条件（报告落库 + 模型范围质检 + 问题项反馈）**。

| 活动 | **① 启动条件**（缺一不可） | **② 产物**：报告 ｜ 资产 | **③ 结束条件** | 技能 |
|---|---|---|---|---|
| **A1 立项与需求分析** | ① 有一份**文档或报告**（带来源）；② **声明模型范围 = `bmm`**（要表达目标/方案/计划/需求项；理由写进报告）；③ 产出物与**反馈人**明确 | **需求分析报告 / 需求规格说明书**（`bmm:RequirementAnalysisReport`） ｜ 需求与领域资产：`bmm:Goal`/`Objective`/`DesiredResult`/`CourseOfAction`/`OrganizationUnit` + 类/关系/实例/受控词表（**存量走结构化导入**） | ① 报告落库（带 `source_refs`）；② **`bmm` 范围质检**：本次页 `page_type` 全在 `bmm:` 内、`ontology_types("bmm")` 通过、`audit_scan` 无 B1/C1、`scope=similar` 无未裁决近重复/域外同名；③ **问题项反馈**（现象·证据·处理：整改/驳回/接受·责任人·时间；"表达不了 → 本体建议"） | `model_recommendation`、`domain_modeling`、`structured_modeling`、`multimodal_extraction`、`tech_review_extraction`、`session_provenance` |
| **A2 设计与规划** | ① A1 的需求报告与领域资产在库；② **声明模型范围 = `agent`**（本批要建哪些：`Agent`/`Skill`/`Tool`/`WorkKnowledgeBase`）；③ 设计对象清单与反馈人明确 | **设计单 × N**（`agent:DesignSpec`，**一个设计对象一份**：业务智能体/业务技能/工具各一份） ｜ **开发规划**（顺序 或 并行、批次、依赖）写进设计单或规划小节 ｜ `Agent`/`Skill`/`Tool`/`WorkKnowledgeBase` 个体 | ① 每份设计单落库（report 模式、带整篇正文）；② **`agent` 范围质检**：能力对象类型都在声明范围内、`ontology_types("agent")` 通过、巡检无 B1；③ **问题项反馈**（如"缺工具契约/缺工作知识库"→ 转 A3 或写建议） | `skill_development`（设计段） |
| **A3 开发交付** | ① A2 的设计单在库；② **声明 `agent` 范围**（`InstallGuide`/`ToolContract`/`Stub`）；③ 交付目标环境与反馈人明确 | **安装指引**（`agent:InstallGuide`）+ **评测方案**（`bmm:Report`，标题"评测方案"） ｜ `InstallGuide`/`ToolContract`/`Stub` 个体 | ① 安装指引与评测方案落库；② **范围质检**：**工具状态表**（挡板/已投产）与契约字段一致、**评测方案含指标与用例**（结论正确率/证据可回溯率/unknown 正确率/外部知识依赖数/耗时）、巡检无 B1；③ **问题项反馈**（挡板转正计划、缺口清单） | `skill_development`（脚手架/安装段） |
| **A4 评测** | ① 被评对象可运行/有运行记录；② **A3 的评测方案在库**（指标与用例来自它）；③ **声明 `agent` 范围**（`Evaluation`/`Metric`）+ 反馈人明确 | **评测报告**（`agent:Evaluation`）+ **改进建议**（`agent:Advice`） ｜ `Evaluation`/`Metric`/`Advice` 个体 | ① 评测报告与建议落库；② **范围质检**：按**评测方案**逐指标给结果（**含反例**）、受限项挂编号、巡检无 B1；③ **反馈评测结果**（结论 + 涉及的智能体/技能/工具 + 处置：**改进建议** 或 回 A2/A3 迭代）+ 逐条问题项反馈 | 评估技能（待物化；可先用 `graph_query`+`audit_scan` 拼） |
| *会话终点（非活动）* | 各活动报告齐（跳过某活动要**写明理由**）+ **人员反馈已收** | **结项报告**（`bmm:Report`，**含 `## 人员反馈`**） | 落库即会话完成 | `session_provenance` |

**活动之间的关系（不固化顺序，只写依赖事实）**：
- `A1 → A2`：设计单要引用需求与领域资产（**依赖**，但一个会话可以只做 A1）。
- `A2 → A3`：开发交付按设计单执行；**多份设计单可顺序或并行交付**（规划写在 A2）。
- `A3 → A4`：评测**必须依据 A3 的评测方案**（否则回到 A3 补方案）。
- `A4 → A2/A3`：改进建议受理后回到设计与开发（**迭代**，不是"下一阶段"）。

---

## 4 BPMN 对照 + 弹性（**不设固定顺序**）

### 4.1 与我们术语的对照

| BPMN 概念 | 我们 | 说明 |
|---|---|---|
| **Process（业务流程）** | **「企业知识管理流程」**（`bmm:BusinessProcess` 实例，落在**企业知识管理领域**库） | 一个流程，装 6 个活动 |
| **Activity / Task** | **活动**（A1–A4） | 有自己的启动/结束条件；**发起由人/会话决定** |
| **Sequence Flow（顺序流）** | **不固定**（不画死顺序） | 建模是探索性的：只守"每个活动自己的启动/结束条件 + 纪律" |
| **Data Object / Artifact** | **报告**（过程件，document） | 活动的产物、过程的证据 |
| （数据本体） | **资产**（entity） | 活动的产物，**机器判定的唯一依据** |
| **Gateway（网关/判断）** | **质检 + 问题项反馈** | 挂在活动结束条件里：不合格 → 整改后再结束 |
| **Data Store** | 知识库（领域库 / 工作库 / `.env` 三库） | 资产落领域库；运行产物落工作库 |
| **Start / End Event** | **会话起点** = 一份文档或报告；**会话终点** = 结项报告（含人员反馈） | 律一 / 律二 / 律三 |

### 4.2 弹性（允许 / 不允许）

| 行为 | 允许？ | 约束 |
|---|---|---|
| 只做 A1（只做立项与需求分析），不做 A2/A3/A4 | ✅ | 结项报告里写明"本次不涉及设计/交付/评测及理由" |
| 同一活动多轮（回环） | ✅ | 同活动的报告**版本+1**（`wiki_page_revisions`） |
| 并行做多个活动 / 并行交付多份设计单 | ✅ | 每个活动各自满足"启动/结束"；会话仍要**一份**结项报告 |
| A4 之后回到 A2/A3（评测发现要改设计/开发） | ✅ | 由**改进建议**驱动迭代；建议进总览页 |
| 跳过 A3 直接做 A4 | ❌ | A4 的启动条件含"**A3 的评测方案在库**"（没有方案不许评测） |
| 不声明模型范围就开工 | ❌ | 违反**律五** |
| 结束时不做范围质检 / 问题项没有反馈 | ❌ | 违反**律六** |
| 无起点文件 / 无结项报告 / 结项无人员反馈 | ❌ | 违反律一/二/三 |
| 用报告当"事实来源"下机器判定 | ❌ | 违反律四 + 资产规范 A1 |

---

## 5 活动声明：落「企业知识管理领域」库的**「企业知识管理流程」实体**

### 5.1 落点与形态（**零本体新增**，推荐）

| 项 | 设计 |
|---|---|
| **落在哪个库** | **企业知识管理领域**（`BODHI_KNOWLEDGE_MGMT_KB_ID`，实测 `6effde80-…`）—— 与本智能体的技能/工具定义同库 |
| **流程主体** | 一页：**「企业知识管理流程」**，`page_type = bmm:BusinessProcess`（**已有类**），**entity 模式**（不是报告类） |
| **每个活动** | **A1–A4 各一页**：`page_type = bmm:CourseOfAction`（行动方案，最贴 BPMN 的 Activity）或 `bmm:BusinessProcess`（子流程）；与主流程页用 `bmm:realizesProcess` / `bmm:realizedBy` 相连（语义不合就用正文小节列，关系只作弱关联） |
| **每活动页承载** | `activityId` · **本体模型范围** · 启动条件 · 产出报告（类型 + 标题规范） · 产出资产 · 结束条件（**质检项 + 问题项反馈要求**） · 技能/工具 · 谁做（人/智能体） —— 写进 `definition` 与 data 属性（entity 渲染即可读） |
| **报告怎么引用** | 报告类 = `bmm:Report`（或既有 `agent:DesignSpec`/`InstallGuide`/`Evaluation`），活动页写"产出报告 = 某类型 + 标题写法"，并链到本体模型库的类页（跨库只读） |
| **谁读/谁写** | **知识管理智能体只读**（读活动声明来指导会话）；**运维/人**维护该库 |

### 5.2 备选（把"活动"也本体化，**以后再说**）

若将来要严格 BPMN 化：新增 `bmm:Activity`（活动）+ `bmm:activityProducesReport`（Activity→Report）+ `bmm:activityScopeModel`（Activity→模型）+ `bmm:sequenceFlow`（Activity→Activity）等，参照 BPMN 的 `Task / SubProcess / SequenceFlow / Gateway / DataObject` 建类。

- 优点：活动/产物/顺序都是可 `graph_query` 的结构化字段。
- 代价：**动本体**（1 类 + 3~4 关系/属性）+ 编译 + 重投影 + 巡检 —— 且**顺序我们刻意不固化**（SequenceFlow 多半用不上）。
- 结论：**v0.5 先用 §5.1**（零本体改动就能跑），等跑过 2–3 个领域再按 **J1–J7 判据**复核。

### 5.3 声明页字段示例

```
页：企业知识管理流程            page_type=bmm:BusinessProcess   kb=企业知识管理领域
  ├─ 定义：企业知识管理（领域建模 + 业务智能体设计开发）的**活动集合**（BPMN 视角）；
  │        活动之间**不固定顺序**；会话遵守"四律"，每个活动遵守"模型范围（律五）"与"质检+反馈（律六）"。
  ├─ 子页（realizedBy）：A1 立项与需求分析 / A2 设计与规划 / A3 开发交付 / A4 评测
  └─ 字段：flowVersion=1 ｜ 适用领域=（可覆写：活动子集/顺序/门槛/术语别名）

页：A1 立项与需求分析            page_type=bmm:CourseOfAction
  ├─ 启动条件：①一份文档或报告（带来源） ②**声明模型范围 = bmm**（理由：要表达目标/方案/计划/需求项） ③产出物与反馈人明确
  ├─ 产出：报告 = bmm:RequirementAnalysisReport（需求分析报告｜别名 需求规格说明书）；
  │        资产 = 需求与领域资产（bmm:Goal/Objective/CourseOfAction… + 类/关系/实例/受控词表）
  ├─ 结束条件：①报告落库 ②**范围质检**（page_type 前缀 ∈ {bmm}；每实例有唯一标识+来源；audit_scan 无 B1/C1；similar 无未裁决）
  │            ③**问题项反馈**（现象·证据·处理·责任人·时间；表达不了 → 本体建议）
  └─ 技能：model_recommendation / domain_modeling / structured_modeling / multimodal_extraction
  └─ 技能：domain_modeling / structured_modeling / multimodal_extraction / tech_review_extraction
```

### 5.4 声明自身的治理

| 事项 | 规则 |
|---|---|
| 变更 | `flowVersion` **+1** + 变更记录页；改活动语义要人批准 |
| 影响面 | 变更前用 `context_scan` / `audit_scan(scope=similar)` / `retag_preview` 出影响面 |
| 领域本地化 | 领域可覆写：**活动子集 / 顺序 / 门槛 / 术语别名**；**不可改**活动的语义与结束条件（结束条件是硬门禁） |
| 引用完整性 | 活动页引用的报告类必须在本体里存在（缺类 → 走 A2 的"缺类→改进建议"） |

---

## 6 活动 ↔ 技能 ↔ 工具（谁干哪件事）

| 活动 | 主力技能 | 主要工具 |
|---|---|---|
| **A1 立项与需求分析** | `model_recommendation` + `domain_modeling`（存量 `structured_modeling`）/ `multimodal_extraction` / `tech_review_extraction` + `session_provenance` | `doc_outline` `get_document_info` `list_knowledge_chunks` `grep_chunks` `wiki_search` `wiki_read_page` `ontology_types` `save_knowledge`（报告用 `stage="report"`，资产用 `stage="graph"`）`import_probe/plan/apply/state` `image_extract` `link_candidates` `context_scan/lookup` `audit_scan` `reference_lookup` |
| **A2 设计与规划** | `skill_development`（设计段） | `skills` `ontology_types` `wiki_search` `wiki_read_page` `graph_query` `save_knowledge`(report) |
| **A3 开发交付** | `skill_development`（脚手架/安装段） | `skills` `ontology_types` `doc_outline` `grep_chunks` `save_knowledge`(report)——安装指引 + 评测方案 |
| **A4 评测** | 评估技能（待物化） | `graph_query` `audit_scan` `wiki_search` `wiki_read_page` `service_overview` `save_knowledge`(report)——评测报告 + 改进建议 |
| 会话终点 | `session_provenance` | `save_knowledge` `wiki_read_page`（结项报告 + 人员反馈） |

**合并后的智能体（`bodhi-knowledge-mgmt`）提示词结构**：

```
① 读声明：从「企业知识管理领域」库读「企业知识管理流程」+ A1–A4 活动页（模型范围/启动/产出/结束条件）
② 选活动：按用户意图选活动 → 取该活动的技能（上表）→ 技能"只限模型、不限类型"，类/关系一律 ontology_types 现查
③ 守纪律：律五（先声明模型范围）→ 律六（结束前做范围质检 + 问题项反馈）→ 律一/二/三（起点、结项、人员反馈）→ 律四（报告只作证据）
```

---


---

## 7 落地清单（v0.5，等 §8 裁决后开工）

| # | 动作 | 产出 | 粗估 |
|---|---|---|---|
| **1** | **TTL 调整**：新增基类 `bmm:Report`（`wikiContent` domain 改指它）+ **新增子类 `bmm:RequirementAnalysisReport`（需求分析报告 / 别名 需求规格说明书）** + 4 个既有报告类改继承 + `bmm:Assessment` 摘掉 `wiki_content` | `ontology/sources/{bmm,agent}.ttl` diff | 0.5 天 |
| **2** | **按 §1.4 五步上线**：改 TTL → 编译 → **重投影 Neo4j（必做）** → **抽查 `render_mode`**（`Report`/`RequirementAnalysisReport`/`DesignSpec`/`InstallGuide`/`Evaluation`/`Advice` = document；`Assessment`/`ToolContract` = entity）→ 巡检 | 编译日志 + 抽查表 | 0.5 天 |
| **3** | **建声明**：企业知识管理领域库建「企业知识管理流程」+ **A1–A4 活动页**（含**模型范围**字段） | 该库 5 个 entity 页 | 1 天 |
| **4** | **技能标注所属活动**（front-matter `activity: [a1]`…）+ **报告标题规范**（"需求分析报告（需求规格说明书）/ 设计单-<对象> / 安装指引 / 评测方案 / 评测报告 / 改进建议 / 结项报告" + `## 人员反馈` 小节） | 各 SKILL.md + 模板小节 | 1 天 |
| **5** | **会话页补字段**：**声明的本体模型范围**（供律六质检与巡检比对） | `session_provenance` 技能 + 页面约定 | 0.5 天 |
| **6** | **巡检新增 kind**：`activity_scope_violation`（本次落库页类型越出声明范围）、`session_without_closure`、`report_without_feedback` | `ke_audit` + scope + 文档 | 1 天 |
| **7** | **技能修订**：`domain_modeling`/`structured_modeling`/`skill_development`/`session_provenance` 写入"律五/律六"与报告规范 | 4 份 SKILL.md | 1 天 |
| **8** | **合并智能体** `bodhi-knowledge-mgmt` + **`allowed_tools` 收口**（顺带清掉两个已删工具名）+ 交付文档同步 | 提示词 + `gen_agents.py`/`agents.sql` | 2 天 |
| **9** | **案例回归**：用技术方案评审案例按活动跑一遍（A1→A2→A3→A4→结项），产出"活动验收记录"；**重打包** | 记录页 + `bodhi2-delivery-20261010-5` | 1 天 |

---

## 8 待你裁决

| # | 问题 | 我的建议 |
|---|---|---|
| **D1** | **活动清单**：A1 立项与需求分析 / A2 设计与规划 / A3 开发交付 / A4 评测（+ 会话终点） | ✅ 就这 4 个（A5 文档评审属业务闭环、A7 巡检属运维，**都不进本流程**） |
| **D2** | **"评测方案"要不要也建子类**（如 `agent:EvaluationPlan`）？ | 先**不建**：用 `bmm:Report` + 标题"评测方案"；等 A4 跑顺、方案字段稳定后再定（**A1 的需求分析报告是标准件，独立成子类**） |
| **D3** | 报告类型总账 | 新增 **1 基类 + 1 子类**（`bmm:Report`、`bmm:RequirementAnalysisReport`）；其余沿用既有 `agent:DesignSpec`/`InstallGuide`/`Evaluation`/`Advice` |
| **D4** | 律五/律六强度 | 律五=**纪律**（不声明模型范围不能开工）；律六的"**问题项反馈**"=**硬门禁**（没有反馈的活动不算结束） |
| **D5** | 活动声明形态 | **§5.1 零本体新增**（`bmm:BusinessProcess`/`CourseOfAction` 实例 + 页面字段）；`bmm:Activity` 以后再说 |
| **D6** | 巡检新增 kind | 先做 `activity_scope_violation` + `session_without_closure` + `report_without_feedback` |
| **D7** | "越界"怎么判 | 按**模型前缀集合**：本次落库页 `page_type` 前缀（`bmm:`/`ea:`/`agent:`）须 ∈ 活动声明范围；跨库**只读**引用不算越界 |
| **D8** | 本次先做哪几步 | **§7-1 → §7-2 → §7-3**（TTL 调整 + 上线五步 + 建流程与活动页）；再 4→5→6 |

---

## 9 附

### 9.1 本文**不做**的事

- ❌ 不新增知识库（用「企业知识管理领域」+ 领域库）。
- ❌ 不改 `render_mode` 的**代码**（判定就是"看有没有 `wikiContent`（含继承）"，只改 TTL）。
- ❌ **不为每个活动发明报告类型**（只加 1 基类 `bmm:Report` + 1 子类 `bmm:RequirementAnalysisReport`）。
- ❌ 不给 `assessment` 保留 `wiki_content`（纯 entity）。
- ❌ **不固化活动顺序**（BPMN 的 Sequence Flow 刻意不画）。
- ❌ 不在本阶段处理"已删工具名在 DB 注册里残留"（并入 §7-8 一次收口）。
- ❌ 不重开 DDD 概念（库边界=限界上下文、共享概念模型=上下文映射）。

### 9.2 术语对照

| 本文（v0.5） | 仓库既有 / BPMN | 说明 |
|---|---|---|
| **活动**（A1–A4） | BPMN **Activity / Task**；声明页用 `bmm:CourseOfAction` | 有启动/结束条件；**报告与活动一一对应** |
| **需求分析报告 / 需求规格说明书** | 新增子类 `bmm:RequirementAnalysisReport` | A1 的产出（标准件，故独立成类；别称走 `skos:altLabel`） |
| ~~阶段（A–E）~~ | v0.3 用语，**已废弃** | 见 §0.1 的替换表 |
| **报告**（过程件） | 文档类 / `render_mode=document` / `bmm:Report` + 既有 `agent:*` 报告类 | 活动的产物；给人讨论·评审·反馈·溯源·取证 |
| **资产** | entity 知识（类/关系/实例/受控词表） | 活动的产物；**机器判定的唯一依据** |
| **模型范围** | 本体模块（`bmm` / `ea` / `agent`） | 律五：活动启动时必须声明 |
| **质检 / 问题项反馈** | BPMN **Gateway**；`ontology_types` + `audit_scan` | 律六：活动结束前必做，问题项必须明确反馈 |
| **企业知识管理流程** | `bmm:BusinessProcess` 实例（企业知识管理领域库） | BPMN Process：装活动 |
| 会话四律 | ❌ 无（本文首创） | 起点·终点·人员反馈·证据 |
| 工序阶段 | SKILL.md 的 `stages:` | 技能**内部**步骤，与"活动"不是一回事 |
| 写入阶段 | `save_knowledge(stage=…)`＝`graph`/`report` | 只管写入模式 |

> 版本：**v0.5**（2026-10-11：4 个活动；报告加 1 子类"需求分析报告/需求规格说明书"）｜v0.1–v0.4 见 git（`f1c531a` / `98547ac` / `5a3ee89` / `fc64598`）｜待 §8 裁决后升 v0.6 并开工。
