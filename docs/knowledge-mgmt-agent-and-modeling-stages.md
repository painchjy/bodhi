# 企业知识管理智能体 · 建模工艺（草案 v0.4｜2026-10-11｜待讨论）

> v0.4 按你的口径**去掉"阶段"，全部改成"活动"（BPMN 概念）**：**报告是活动的产物**（需求分析报告←需求分析活动、
> 设计单←设计活动、改进建议←评测活动…）；**每个活动的启动条件之一 = 明确本体模型范围**（并说明为什么选这个模型，
> 如立项要明确目标/方案/计划 → 选 `bmm`）；**结束条件之一 = 对该模型范围做质检，并对问题项给出明确反馈**。
> v0.3 及以前见 git（`5a3ee89` / `98547ac` / `f1c531a`）。§1（报告类型体系与 D2 实测）沿用，未变。

---

## 0 一屏

### 0.1 从"阶段"到"活动"（为什么换、换掉什么）

| v0.3 的"阶段" | v0.4 的"活动" | 说明 |
|---|---|---|
| A 立项定界 | **立项与需求分析活动** | 一次活动：先定范围（目标/方案/计划），需求分析随之；产物=**需求分析报告** |
| B 概念与本体对齐 | **领域建模活动**（含概念与本体对齐） | 对齐是**建模活动的内部步骤**，不再单列 |
| C 资产化 | 并入**领域建模活动**（存量走结构化导入） | 台账/清单导入仍是"有文件来源"的建模活动 |
| D 综合与判断 | **文档评审活动** / **评测活动** 分开 | 报告不同：评审结论 vs 评测/改进建议 |
| E 结项 | **不是活动** → 是**会话的终点事件** | 结项报告 + 人员反馈（会话级，见 §2 律二/律三） |
| —— | **设计活动** / **开发交付活动** | 本来就存在（设计单 / 安装指引），v0.3 挤在"D 段"里，现在独立成活动 |

> 换词的好处：**报告与活动一一对应**（谁产谁负责）、**启动/结束条件挂在活动上**（不依赖全局顺序）、
> 与 **BPMN** 的词汇（Process / Activity / Sequence Flow / Data Object / Gateway）直接对齐，业务方一看就懂。

### 0.2 活动清单（6 个 + 1 个可选；会话终点单独算）

| # | 活动 | 主办技能 | **本体模型范围**（启动时必须声明） | 产物：**报告** | 产物：资产（entity） |
|---|---|---|---|---|---|
| A1 | **立项与需求分析** | `model_recommendation` + `domain_modeling` | **`bmm`**（目标/方案/计划/干系人；`agent` 只读） | **需求分析报告**（`bmm:Report`） | 目标/预期成果/行动方案等 `bmm` 个体 |
| A2 | **领域建模**（含概念与本体对齐；存量走 `structured_modeling` 导入） | `domain_modeling` / `structured_modeling` / `multimodal_extraction` / `tech_review_extraction` | 按材料定：**`bmm`** 或 **`ea`**（声明哪个 + 为什么） | **领域建模报告**（`bmm:Report`） | 类/关系/实例/受控词表（本体约束的知识） |
| A3 | **设计** | `skill_development`（设计段） | **`agent`**（业务智能体/技能/工具/工作知识库） | **设计单**（`agent:DesignSpec`） | `Agent`/`Skill`/`Tool`/`WorkKnowledgeBase` 个体 |
| A4 | **开发交付** | `skill_development`（脚手架/安装段） | **`agent`** | **安装指引**（`agent:InstallGuide`）+ 工具契约 | `InstallGuide`/`ToolContract`/`Stub` 个体 |
| A5 | **文档评审** | `document_review`（+`revision_trace`） | **`bmm`**（规则/判断口径；本活动**产判断不产报告结论为事实**） | **评审结论报告**（`bmm:Report`） | **`bmm:Assessment`（纯 entity 的判断）** |
| A6 | **评测** | 评估技能（`skill_development` 评测段 / 待物化） | **`agent`**（`Evaluation`/`Metric`） | **评测报告**（`agent:Evaluation`）+ **改进建议**（`agent:Advice`） | `Evaluation`/`Metric`/`Advice` 个体 |
| A7 | *（可选）**巡检治理*** | 运维智能体 | 全模型（声明要查哪些） | 巡检报告（`bmm:Report`） | `Advice`（本体类/知识类） |
| — | *会话终点（不是活动）* | `session_provenance` | — | **结项报告**（`bmm:Report`，**必含 `## 人员反馈`**） | — |

> 报告类型**不新增**（沿用 v0.3 口径）：A1/A2/A5/A7/结项用**基类 `bmm:Report`**（标题标明是哪份报告）；
> A3/A4/A6 沿用既有报告类 `agent:DesignSpec` / `InstallGuide` / `Evaluation`(+`Advice`)。
> ⇒ **现有技能零改动**。

### 0.3 每个活动的三件套（一句话记住）

```
① 启动条件：有输入的文档或报告  ｜  **明确本体模型范围（选哪个模型 + 为什么）**  ｜  产出物与"反馈人"明确
② 产出：一份**报告**（过程件，给人看/评审/取证）  +  一批**资产**（entity，给机器判定）
③ 结束条件：报告已落库  ｜  **对该模型范围做质检**（类型在册·无越界·无 C1/S1 问题）  ｜  **问题项有明确反馈**（谁·处理·时间）
```
## 1 本体调整：报告类型体系（**只改 TTL，不改代码；且不新增报告类型**）

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

### 1.2 目标形态：**不新增报告类型，只加 1 个基类**

| 类型 | 归属 | 变化 | 说明 |
|---|---|---|---|
| **`bmm:Report`（报告·基类）** | bmm | **新增（唯一新增）** | `bmm:wikiContent` 的 domain 由 `Assessment` **改指它**；活动报告统一用它 |
| `agent:Advice` 改进建议 / `agent:DesignSpec` 设计单 / `agent:Evaluation` 评测 / `agent:InstallGuide` 安装指引 | agent | **改继承**（`rdfs:subClassOf bmm:Report`），删掉那 4 行 "domain 追加" | 4 个既有报告类，**类型名不变** → **现有技能零改动** |
| `bmm:Assessment` **评估/判断** | bmm | **摘掉** `wiki_content`（不再进 domain） | **纯 entity**：一条 assessment = **一个明确结论的判断**（可判定/可聚合/可比较），不是综合报告 |
| 活动报告（A1 需求分析 / A2 领域建模 / A5 评审结论 / A7 巡检 / 结项） | — | **不新增类型** | 统一用 `bmm:Report`，**活动与报告的名称写在标题 + 元数据**（例：标题"需求分析报告"、`## 人员反馈` 小节） |

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

## 3 活动定义（6 个活动 + 1 个可选 + 会话终点）

> 每个活动三件套：**① 启动条件（含模型范围）② 产物（报告 + 资产）③ 结束条件（报告落库 + 模型范围质检 + 问题项反馈）**。

| 活动 | **① 启动条件**（缺一不可） | **② 产物**：报告 ｜ 资产 | **③ 结束条件** | 技能 |
|---|---|---|---|---|
| **A1 立项与需求分析** | ① 有一份**文档或报告**（带来源）；② **明确本体模型范围**：**`bmm`**（要表达目标/方案/计划/干系人；理由写进报告）；③ 产出物与**反馈人**明确 | **需求分析报告**（`bmm:Report`） ｜ `bmm:Goal`/`Objective`/`DesiredResult`/`CourseOfAction`/`OrganizationUnit` 等个体 | ① 报告落库（带 `source_refs`）；② **`bmm` 范围质检**：本次页 `page_type` 全在 `bmm:` 内、`ontology_types("bmm")` 通过、`audit_scan` 无 B1/C1；③ **问题项反馈**（现象·证据·处理·责任人·时间） | `model_recommendation`、`domain_modeling`、`session_provenance` |
| **A2 领域建模**（概念与本体对齐在此活动内；存量走结构化导入） | ① A1 的报告，**或**一份材料/**结构化文件**；② **声明模型范围**：`bmm` **或** `ea`（+ 依据）；③ 目标领域库确定 | **领域建模报告**（`bmm:Report`） ｜ 类/关系/实例/受控词表（本体约束的知识） | ① 报告落库；② **范围质检**：`page_type` 前缀 ∈ 声明范围；每实例有**唯一标识 + 来源**；`audit_scan` 无 B1/C1，`scope=similar` 无未裁决近重复/域外同名；③ **问题项反馈**（含"表达不了→本体建议"） | `domain_modeling`、`structured_modeling`、`multimodal_extraction`、`tech_review_extraction` |
| **A3 设计** | ① A1/A2 的产物在库（需求 + 领域资产）；② **声明 `agent` 模型范围**（要建哪些：`Agent`/`Skill`/`Tool`/`WorkKnowledgeBase`）；③ 设计对象与反馈人明确 | **设计单**（`agent:DesignSpec`） ｜ `Agent`/`Skill`/`Tool`/`WorkKnowledgeBase` 个体 | ① 设计单落库（report 模式、带整篇正文）；② **`agent` 范围质检**：能力对象类型都在声明范围内、`ontology_types("agent")` 通过、巡检无 B1；③ **问题项反馈**（如"缺工具契约"→ 转 A4 或建议） | `skill_development`（设计段） |
| **A4 开发交付** | ① A3 设计单在库；② **声明 `agent` 范围**（`InstallGuide`/`ToolContract`/`Stub`）；③ 交付目标与反馈人明确 | **安装指引**（`agent:InstallGuide`）+ **工具契约** ｜ `InstallGuide`/`ToolContract`/`Stub` 个体 | ① 安装指引落库；② **范围质检**：**工具状态表**（挡板/已投产）与契约字段一致、巡检无 B1；③ **问题项反馈**（挡板转正计划、缺口清单） | `skill_development`（脚手架/安装段） |
| **A5 文档评审** | ① 有被评文档（或上一轮报告）；② **声明 `bmm` 范围**（评审策略/规则 + 判定所需知识）；③ 评审范围与反馈人明确 | **评审结论报告**（`bmm:Report`） ｜ **`bmm:Assessment`（纯 entity 判断，一条结论一条）** | ① 报告落库；② **范围质检**：每条结论**带证据引用**、规则在册、`unknown` 显式、巡检无 B1；③ **问题项反馈**（规则冲突/知识缺口 → 建议） | `document_review`、`revision_trace` |
| **A6 评测** | ① 被评对象可运行或有运行记录；② **声明 `agent` 范围**（`Evaluation`/`Metric`）；③ 指标与反馈人明确 | **评测**（`agent:Evaluation`）+ **改进建议**（`agent:Advice`） ｜ `Evaluation`/`Metric`/`Advice` 个体 | ① 评测与建议落库；② **范围质检**：指标齐（结论正确率/证据可回溯率/unknown 正确率/外部知识依赖数/耗时）、**反例必列**、受限项挂编号；③ **问题项反馈**（建议分拣 + `adviceStatus`） | 评估技能（待物化；可先用 `graph_query`+`audit_scan` 拼） |
| **A7 巡检治理**（可选） | ① 声明**要查的模型范围与 kind**；② 触发依据（上次巡检报告/用户要求）；③ 反馈人明确 | **巡检报告**（`bmm:Report`） ｜ `agent:Advice`（本体类/知识类） | ① 报告落库；② **范围质检**：巡检 kind 覆盖声明范围（含 A1/A2/B*/C*/S1/S2/孤儿图节点）；③ **问题项反馈 + 处理计划**（谁在何时清） | 运维智能体（`audit_*`） |
| *会话终点（非活动）* | 各活动报告齐（跳过某活动要**写明理由**）+ **人员反馈已收** | **结项报告**（`bmm:Report`，**含 `## 人员反馈`**） | 落库即会话完成 | `session_provenance` |

---

## 4 BPMN 对照 + 弹性（**不设固定顺序**）

### 4.1 与我们术语的对照

| BPMN 概念 | 我们 | 说明 |
|---|---|---|
| **Process（业务流程）** | **「企业知识管理流程」**（`bmm:BusinessProcess` 实例，落在**企业知识管理领域**库） | 一个流程，装 6 个活动 |
| **Activity / Task** | **活动**（A1–A7） | 有自己的启动/结束条件；**发起由人/会话决定** |
| **Sequence Flow（顺序流）** | **不固定**（不画死顺序） | 建模是探索性的：只守"每个活动自己的启动/结束条件 + 纪律" |
| **Data Object / Artifact** | **报告**（过程件，document） | 活动的产物、过程的证据 |
| （数据本体） | **资产**（entity） | 活动的产物，**机器判定的唯一依据** |
| **Gateway（网关/判断）** | **质检 + 问题项反馈** | 挂在活动结束条件里：不合格 → 整改后再结束 |
| **Data Store** | 知识库（领域库 / 工作库 / `.env` 三库） | 资产落领域库；运行产物落工作库 |
| **Start / End Event** | **会话起点** = 一份文档或报告；**会话终点** = 结项报告（含人员反馈） | 律一 / 律二 / 律三 |

### 4.2 弹性（允许 / 不允许）

| 行为 | 允许？ | 约束 |
|---|---|---|
| 只做 A2（纯建模），不做 A3/A4 | ✅ | 结项报告里写明"本次不涉及设计/交付及理由" |
| 同一活动多轮（回环） | ✅ | 同活动的报告**版本+1**（`wiki_page_revisions`） |
| 并行做多个活动 | ✅ | 每个活动各自满足"启动/结束"；会话仍要**一份**结项报告 |
| A5 之后回到 A2（评审发现知识缺口） | ✅ | 缺口的处理进 A2 的问题项反馈 |
| 不声明模型范围就开工 | ❌ | 违反**律五** |
| 结束时不做范围质检 / 问题项没有反馈 | ❌ | 违反**律六** |
| 无起点文件 / 无结项报告 / 结项无人员反馈 | ❌ | 违反律一/二/三 |
| 用报告当"事实来源"下机器判定 | ❌ | 违反律四 + 资产规范 A1 |
## 5 活动声明：落「企业知识管理领域」库的**「企业知识管理流程」实体**

### 5.1 落点与形态（**零本体新增**，推荐）

| 项 | 设计 |
|---|---|
| **落在哪个库** | **企业知识管理领域**（`BODHI_KNOWLEDGE_MGMT_KB_ID`，实测 `6effde80-…`）—— 与本智能体的技能/工具定义同库 |
| **流程主体** | 一页：**「企业知识管理流程」**，`page_type = bmm:BusinessProcess`（**已有类**），**entity 模式**（不是报告类） |
| **每个活动** | **A1–A7 各一页**：`page_type = bmm:CourseOfAction`（行动方案，最贴 BPMN 的 Activity）或 `bmm:BusinessProcess`（子流程）；与主流程页用 `bmm:realizesProcess` / `bmm:realizedBy` 相连（语义不合就用正文小节列，关系只作弱关联） |
| **每活动页承载** | `activityId` · **本体模型范围** · 启动条件 · 产出报告（类型 + 标题规范） · 产出资产 · 结束条件（**质检项 + 问题项反馈要求**） · 技能/工具 · 谁做（人/智能体） —— 写进 `definition` 与 data 属性（entity 渲染即可读） |
| **报告怎么引用** | 报告类 = `bmm:Report`（或既有 `agent:DesignSpec`/`InstallGuide`/`Evaluation`），活动页写"产出报告 = 某类型 + 标题写法"，并链到本体模型库的类页（跨库只读） |
| **谁读/谁写** | **知识管理智能体只读**（读活动声明来指导会话）；**运维/人**维护该库 |

### 5.2 备选（把"活动"也本体化，**以后再说**）

若将来要严格 BPMN 化：新增 `bmm:Activity`（活动）+ `bmm:activityProducesReport`（Activity→Report）+ `bmm:activityScopeModel`（Activity→模型）+ `bmm:sequenceFlow`（Activity→Activity）等，参照 BPMN 的 `Task / SubProcess / SequenceFlow / Gateway / DataObject` 建类。

- 优点：活动/产物/顺序都是可 `graph_query` 的结构化字段。
- 代价：**动本体**（1 类 + 3~4 关系/属性）+ 编译 + 重投影 + 巡检 —— 且**顺序我们刻意不固化**（SequenceFlow 多半用不上）。
- 结论：**v0.4 先用 §5.1**（零本体改动就能跑），等跑过 2–3 个领域再按 **J1–J7 判据**复核。

### 5.3 声明页字段示例

```
页：企业知识管理流程            page_type=bmm:BusinessProcess   kb=企业知识管理领域
  ├─ 定义：企业知识管理（领域建模 + 业务智能体设计开发）的**活动集合**（BPMN 视角）；
  │        活动之间**不固定顺序**；会话遵守"四律"，每个活动遵守"模型范围（律五）"与"质检+反馈（律六）"。
  ├─ 子页（realizedBy）：A1 立项与需求分析 / A2 领域建模 / A3 设计 / A4 开发交付 / A5 文档评审 / A6 评测 / A7 巡检治理
  └─ 字段：flowVersion=1 ｜ 适用领域=（可覆写：活动子集/顺序/门槛/术语别名）

页：A2 领域建模                  page_type=bmm:CourseOfAction
  ├─ 启动条件：①A1 报告或材料/结构化文件（带来源） ②**声明模型范围**（bmm 或 ea + 依据） ③目标领域库确定
  ├─ 产出：报告 = bmm:Report（标题"领域建模报告"）；资产 = 类/关系/实例/受控词表
  ├─ 结束条件：①报告落库 ②**范围质检**（page_type 前缀 ∈ 声明范围；每实例有唯一标识+来源；audit_scan 无 B1/C1；similar 无未裁决）
  │            ③**问题项反馈**（现象·证据·处理·责任人·时间；表达不了 → 本体建议）
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
| **A1 立项与需求分析** | `model_recommendation` + `session_provenance` | `doc_outline` `get_document_info` `list_knowledge_chunks` `grep_chunks` `wiki_search` `wiki_read_page` `save_knowledge`(report) |
| **A2 领域建模** | `domain_modeling`（存量走 `structured_modeling`） / `multimodal_extraction` / `tech_review_extraction` | `ontology_types` `save_knowledge(stage="graph")` `import_probe/plan/apply/state` `image_extract` `link_candidates` `context_scan/lookup` `audit_scan` `reference_lookup` |
| **A3 设计** | `skill_development`（设计段） | `skills` `ontology_types` `wiki_search` `wiki_read_page` `save_knowledge`(report) `graph_query` |
| **A4 开发交付** | `skill_development`（脚手架/安装段） | `skills` `ontology_types` `save_knowledge`(report) `doc_outline` `grep_chunks` |
| **A5 文档评审** | `document_review` / `revision_trace` | `rules_of_policy` `graph_query` `reference_lookup` `review_apply` `image_extract` `save_knowledge` `audit_scan` |
| **A6 评测** | 评估技能（待物化） | `graph_query` `audit_scan` `wiki_search` `service_overview` `save_knowledge` |
| **A7 巡检治理** | 运维智能体 | `audit_scan` `audit_plan` `audit_purge` `context_scan` `retag_preview` |
| 会话终点 | `session_provenance` | `save_knowledge` `wiki_read_page` |

**合并后的智能体（`bodhi-knowledge-mgmt`）提示词结构**：

```
① 读声明：从「企业知识管理领域」库读「企业知识管理流程」+ A1–A7 活动页（模型范围/启动/产出/结束条件）
② 选活动：按用户意图选活动 → 取该活动的技能（§6）→ 技能"只限模型、不限类型"，类/关系一律 ontology_types 现查
③ 守纪律：律五（先声明模型范围）→ 律六（结束前做范围质检 + 问题项反馈）→ 律一/二/三（起点、结项、人员反馈）→ 律四（报告只作证据）
```

---

## 7 落地清单（v0.4，等 §8 裁决后开工）

| # | 动作 | 产出 | 粗估 |
|---|---|---|---|
| **1** | **TTL 调整（不新增报告类型）**：新增基类 `bmm:Report`（`wikiContent` domain 改指它）+ 4 个既有报告类改继承 + `bmm:Assessment` 摘掉 `wiki_content` | `ontology/sources/{bmm,agent}.ttl` diff | 0.5 天 |
| **2** | **按 §1.4 五步上线**：改 TTL → 编译 → **重投影 Neo4j（必做）** → **抽查 `render_mode`** → 巡检 | 编译日志 + 抽查表 | 0.5 天 |
| **3** | **建声明**：企业知识管理领域库建「企业知识管理流程」+ **A1–A7 活动页**（含**模型范围**字段） | 该库 8 个 entity 页 | 1 天 |
| **4** | **技能标注所属活动**（front-matter `activity: [a2]`）+ **报告标题规范**（"需求分析报告/领域建模报告…" + `## 人员反馈` 小节） | 各 SKILL.md + 模板小节 | 1 天 |
| **5** | **会话页补字段**：**声明的本体模型范围**（供律六质检与巡检比对） | `session_provenance` 技能 + 页面约定 | 0.5 天 |
| **6** | **巡检新增 kind**：`activity_scope_violation`（本次落库页类型越出声明范围）、`session_without_closure`、`report_without_feedback` | `ke_audit` + scope + 文档 | 1 天 |
| **7** | **技能修订**：`domain_modeling`/`structured_modeling`/`skill_development`/`session_provenance` 写入"律五/律六"与报告规范 | 4 份 SKILL.md | 1 天 |
| **8** | **合并智能体** `bodhi-knowledge-mgmt` + **`allowed_tools` 收口**（顺带清掉两个已删工具名）+ 交付文档同步 | 提示词 + `gen_agents.py`/`agents.sql` | 2 天 |
| **9** | **案例回归**：用技术方案评审案例按活动跑一遍（A1→A2→A5→A6→结项），产出"活动验收记录"；**重打包** | 记录页 + `bodhi2-delivery-20261010-5` | 1 天 |

---

## 8 待你裁决

| # | 问题 | 我的建议 |
|---|---|---|
| **D1** | **活动清单**：A1 立项与需求分析 / A2 领域建模 / A3 设计 / A4 开发交付 / A5 文档评审 / A6 评测 / A7 巡检治理（+ 会话终点） | ✅ 就这 7 个（A7 可先不建页）；**报告↔活动一一对应**：需求分析报告←A1、设计单←A3、安装指引←A4、评审结论←A5、评测+改进建议←A6 |
| **D2** | 报告类型 | ✅ **不新增**（唯一新增是基类 `bmm:Report`）；A1/A2/A5/A7/结项用它，A3/A4/A6 用既有 `agent:*` 报告类 |
| **D3** | 一个活动能否声明**多个**模型？ | ✅ 允许（如 A2 可声明 `{bmm, ea}`），但**必须逐个说明理由**；质检按声明集合判越界 |
| **D4** | 律五/律六的强度 | 律五=**纪律**（不声明不能开工）；律六的"**问题项反馈**"=**硬门禁**（没有反馈的活动不算结束） |
| **D5** | 活动声明形态 | **§5.1 零本体新增**（`bmm:BusinessProcess`/`CourseOfAction` 实例 + 页面字段）；§5.2 的 `bmm:Activity` 以后再说 |
| **D6** | 巡检新增 kind | 先做 **`activity_scope_violation`**（律五/律六可机器查）+ `session_without_closure` + `report_without_feedback` |
| **D7** | "越界"怎么判 | 按**模型前缀集合**：本次落库页的 `page_type` 前缀（`bmm:` / `agent:` / `ea:`）须 ∈ 活动声明的范围；跨库**只读**引用不算越界 |
| **D8** | 本次先做哪几步 | **§7-1 → §7-2 → §7-3**（TTL 调整 + 上线五步 + 建「企业知识管理流程」与活动页）；再 4→5→6 |

---

## 9 附

### 9.1 本文**不做**的事

- ❌ 不新增知识库（用「企业知识管理领域」+ 领域库）。
- ❌ 不改 `render_mode` 的**代码**（判定就是"看有没有 `wikiContent`（含继承）"，只改 TTL）。
- ❌ **不新增报告类型**（只加 1 个基类 `bmm:Report`）。
- ❌ 不给 `assessment` 保留 `wiki_content`（纯 entity）。
- ❌ **不固化活动顺序**（BPMN 的 Sequence Flow 刻意不画）。
- ❌ 不在本阶段处理"已删工具名在 DB 注册里残留"（并入 §7-8 一次收口）。
- ❌ 不重开 DDD 概念（库边界=限界上下文、共享概念模型=上下文映射）。

### 9.2 术语对照

| 本文（v0.4） | 仓库既有 / BPMN | 说明 |
|---|---|---|
| **活动**（A1–A7） | BPMN **Activity / Task**；声明页用 `bmm:CourseOfAction` | 有启动/结束条件；**报告与活动一一对应** |
| ~~阶段（A–E）~~ | v0.3 用语，**已废弃** | 见 §0.1 的替换表 |
| **报告**（过程件） | 文档类 / `render_mode=document` / `bmm:Report` + 既有 `agent:*` 报告类 | 活动的产物；给人讨论·评审·反馈·溯源·取证 |
| **资产** | entity 知识（类/关系/实例/受控词表） | 活动的产物；**机器判定的唯一依据** |
| **模型范围** | 本体模块（`bmm` / `ea` / `agent`） | 律五：活动启动时必须声明 |
| **质检 / 问题项反馈** | BPMN **Gateway**；`ontology_types` + `audit_scan` | 律六：活动结束前必做，问题项必须明确反馈 |
| **企业知识管理流程** | `bmm:BusinessProcess` 实例（企业知识管理领域库） | BPMN Process：装活动 |
| 会话四律 | ❌ 无（本文首创） | 起点·终点·人员反馈·证据 |
| 工序阶段 | SKILL.md 的 `stages:` | 技能**内部**步骤，与"活动"不是一回事 |
| 写入阶段 | `save_knowledge(stage=…)`＝`graph`/`report` | 只管写入模式 |

> 版本：**v0.4**（2026-10-11 改为"活动"视角）｜v0.1–v0.3 见 git（`f1c531a` / `98547ac` / `5a3ee89`）｜待 §8 裁决后升 v0.5 并开工。
