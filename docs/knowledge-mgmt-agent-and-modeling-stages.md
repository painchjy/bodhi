# 企业知识管理智能体 · 建模工艺（草案 v0.3｜2026-10-11｜待讨论）

> v0.3 按你 2026-10-11 的两条裁定修订：**① D2 继承法已实测验通**（§1.6）；**② 报告类型不再新增**，
> 全部复用现有类型（§1.2）。其余（5 段 A–E、会话四律、流程不固化、阶段声明落「企业知识管理流程」）不变。
> 上一版 v0.2 与 v0.1 见 git（`98547ac` / `f1c531a`）。

---

## 0 一屏

**v0.3 的净改动（相对 v0.2）**

| # | v0.2 的说法 | **v0.3（本版）** |
|---|---|---|
| 1 | 新增 6 个阶段报告类（`ScopingReport` 等） | ❌ **不新增报告类**：阶段报告统一用**报告基类 `bmm:Report`**（唯一新类），段的性质写在**标题 + 元数据**里 —— 现有技能**零改动**即可复用 |
| 2 | 报告类靠继承获得 `wiki_content`（只是设想） | ✅ **已实测**（§1.6）：编译器**接受跨模块 `rdfs:subClassOf`**；产物里 `parents(agent:Advice) = ['bmm:Report']`；复算 → 4 个报告类 = `document`、`bmm:Assessment` = `entity` |
| 3 | 上线动作 = 改 TTL → 编译 → 重投影 | ⚠️ **实测补强**：`render_mode` 的 domain 判定**只读 Neo4j 投影**（`data_properties()` 没有 JSON 兜底）→ **不重投影就不生效**；上线后必须**抽查 `render_mode`** 才算完（§1.6 末） |
| 4 | 5 段 A–E / 会话四律 / 流程不固化 / 声明落「企业知识管理流程」 | **不变**（§2–§6） |

**五段 ↔ 报告（类型只有一个：`bmm:Report`）**

| 段 | 报告（过程件，document） | 资产（entity） |
|---|---|---|
| A 立项定界 | **报告**（`bmm:Report`；标题"立项范围报告"） | 目标/范围个体（现成类） |
| B 概念与本体对齐 | **报告**（标题"概念与本体对齐报告"） | 术语/概念个体 + 受控词表（+ 本体变更 ticket） |
| C 资产化 | **报告**（标题"落库核对报告"） | 类/关系/实例/台账 —— **资产主体** |
| D 综合与判断 | **报告**（标题"判断/评阅报告"）；设计开发产出沿用 `agent:DesignSpec` / `InstallGuide` / `Evaluation` | **`bmm:Assessment`（纯 entity 的判断）** |
| E 结项 | **报告**（标题"结项报告"，**必含人员反馈**） | `agent:Advice`（改进建议） |

---

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
| **`bmm:Report`（报告·基类）** | bmm | **新增（唯一新增）** | `bmm:wikiContent` 的 domain 由 `Assessment` **改指它**；阶段报告统一用它 |
| `agent:Advice` 改进建议 / `agent:DesignSpec` 设计单 / `agent:Evaluation` 评测 / `agent:InstallGuide` 安装指引 | agent | **改继承**（`rdfs:subClassOf bmm:Report`），删掉那 4 行 "domain 追加" | 4 个既有报告类，**类型名不变** → **现有技能零改动** |
| `bmm:Assessment` **评估/判断** | bmm | **摘掉** `wiki_content`（不再进 domain） | **纯 entity**：一条 assessment = **一个明确结论的判断**（可判定/可聚合/可比较），不是综合报告 |
| 阶段报告（A 立项 / B 对齐 / C 核对 / D 判断 / E 结项） | — | **不新增类型** | 统一用 `bmm:Report`，段的性质写**标题 + 元数据**（例：标题"立项范围报告"、`## 人员反馈` 小节） |

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
| **A 立项定界** | ① 有一份**文档或报告**，且**带来源 id**；② 用户意图明确（哪个领域 / 哪份材料 / 想得到什么）；③ 目标**领域知识库**已确定 | ①《立项范围报告》已落库（report 模式、带整篇正文）；② 写明**范围 / 目标 / 边界 / 不做什么 / 干系人**；③ **人已确认**（审阅人 + 时间） | `bmm:Report`（标题"立项范围报告"） | 可选：`bmm:Goal`/`Objective` 等目标个体 | `save_knowledge(dry_run)` 类型校验；`skipped_document_nodes` 为空 |
| **B 概念与本体对齐** | A 的《立项范围报告》在库；材料可取（`doc_outline`/`grep_chunks`/原文件） | ①《概念与本体对齐报告》；② **术语表**（每术语：定义 / 别名 / 来源 + **一问三答**：谁消费 · 做什么推理 · 要多确定）；③ 逐条给"**要不要本体化**"结论（J1–J7 + 保鲜成本决策卡）；④ 表达不了的 → **改进建议**（`adviceCategory=本体类`），改本体走两段式并**人批准**；⑤ **人已裁决术语** | `bmm:Report`（标题"概念与本体对齐报告"） | 术语/概念个体 + 受控词表（`owl:oneOf`）+ `agent:Advice`（本体类） | `ontology_types` 现查；`audit_scan(scope=similar)` 无未裁决近重复/域外同名 |
| **C 资产化** | B 的《对齐报告》在库；**或**直接是一份**结构化文件**（存量建模走这条，也是"有文件来源"）；本体类型可用（`ontology_types` 查得到） | ① 资产已落库（entity 模式：类/关系/实例/台账）；② 每实例**有唯一标识 + 有来源**；③《落库核对报告》（逐条核对 + 例外清单）；④ 巡检**无 C1（无来源）/ B1（类型不在本体）**；⑤ **人抽检** | `bmm:Report`（标题"落库核对报告"） | **资产主体**：类/关系/实例、台账页 | `save_knowledge(stage="graph")`；`import_*`（结构化）；`audit_scan`；`skipped_document_nodes` 为空 |
| **D 综合与判断** | C 的资产齐（判定所需输入都查得到）；判断规则/口径明确（来自 B 的术语与规则） | ① 每条**判断**落成**一条 `assessment`**：对象 + 依据 + 结论 + 置信度 + **未知项**；②《判断/评阅报告》（人可读、带证据引用）；③ 反例与受限项显式；④ **人已复核** | `bmm:Report`（标题"判断/评阅报告"） | **`bmm:Assessment`（纯 entity）** + `agent:Evaluation`/`Metric`（如做评测） | 结论必带证据引用；`graph_query` 可复算；`reference_lookup` 取外部权威 |
| **E 结项** | 本会话各段报告齐全（至少 A、C、D；若跳过 B 必须在对齐报告缺失处**写明理由**） | ①《结项报告》已落库；② **含 `## 人员反馈`**（谁 / 何时 / 意见 / 是否接受 + 理由）；③ 汇总：本次结论 + 受限项 + **改进建议**（`adviceStatus=待受理` 已进总览页）；④ 会话标记完成 | `bmm:Report`（标题"结项报告"，**必含 `## 人员反馈`**） | `agent:Advice`（技能类/工具类/知识类/本体类） | 结项报告存在 + 人员反馈非空；建议挂编号并进总览页 |

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
| **报告类型怎么引用** | 报告类统一是 **`bmm:Report`（基类）**：段页里写"本段产出报告 = `bmm:Report` + 标题写法"，并链到本体模型库的类页（跨库只读，不复制定义） |
| **谁读/谁写** | **知识管理智能体只读**（读阶段声明来指导会话）；**运维/人**维护该库（与"智能体只写领域库"的边界一致） |
| **为什么不用报告页** | 声明是**资产**（要被工具读、可聚合、稳定），不是过程件；一旦写成报告页，就违反 A1（只有报告类带 `wiki_content`） |

### 5.2 备选（语义最准，但**要改本体**，走闭环二）

新增：`bmm:Stage`（阶段）类 + 关系 `bmm:processHasStage`（domain `BusinessProcess` → range `Stage`）+ `bmm:stageProducesReport`（domain `Stage` → range `Report`）+ 数据属性 `stageId` / `stageEntry` / `stageExit`。

- 优点：阶段是**一等公民**，`graph_query` 直接可查；"每段的报告/条件"是结构化字段而不是文本。
- 代价：**动本体**（新增 1 类 + 2 关系 + 3 数据属性）→ 必须人批准 + 编译 + 重投影 + 巡检过。
- 我的建议：**v0.2 先用 5.1**（零本体改动就能跑，且完全满足"在库里声明"）；**等阶段真的稳定**（跑过 2–3 个领域）再按 J1–J7 判据决定是否升到这里。

### 5.3 声明页的字段（v0.3：用页面约定承载，示例）

```
页：企业知识管理流程            page_type=bmm:BusinessProcess   kb=企业知识管理领域
  ├─ 定义：企业知识管理（领域建模 + 业务智能体设计开发）的**通用阶段骨架**；
  │        每段有独立启动/结束条件；会话遵守"四律"（从文档/报告开始 → 以报告结束 → 含人员反馈 → 报告即证据）。
  ├─ 子页（关系：realizedBy）：A 立项定界 / B 概念与本体对齐 / C 资产化 / D 综合与判断 / E 结项
  └─ 字段：stageSetVersion=1 ｜ 适用领域=（按 L1 profile 可覆写子集/顺序/门槛/别名）

页：A 立项定界（段）              page_type=bmm:CourseOfAction
  ├─ 定义：启动条件 = ①有文档或报告(带来源) ②意图明确 ③目标领域库确定；
  │       结束条件 = 《立项范围报告》落库 + 范围/目标/边界/不做什么/干系人齐 + 人确认
  ├─ 产出报告：bmm:Report（标题"立项范围报告"）
  └─ 门禁：save_knowledge(dry_run) 类型校验；skipped_document_nodes 为空
（B/C/D/E 同构：产出报告**都用** `bmm:Report`，仅标题区分 —— 概念与本体对齐报告 / 落库核对报告 / 判断评阅报告 / 结项报告）
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
| **A 立项定界** | `model_recommendation`（判需求种类→选路线）+ `session_provenance`（会话起始页） | `doc_outline` `get_document_info` `list_knowledge_chunks` `grep_chunks` `wiki_search` `wiki_read_page` `save_knowledge`(report) | `bmm:Report`（标题"立项范围报告"） |
| **B 概念与本体对齐** | `model_recommendation`（分级/决策卡）+ `domain_modeling`（概念抽取）+ `multimodal_extraction`（图/表里的概念） | `ontology_types` `graph_query` `audit_scan(scope=similar)` `context_scan/lookup` `link_candidates` `save_knowledge` | `bmm:Report`（标题"概念与本体对齐报告"） |
| **C 资产化** | `domain_modeling`｜**`structured_modeling`（存量导入走这条）**｜`multimodal_extraction`｜`tech_review_extraction` | `save_knowledge(stage="graph")` `import_probe/plan/apply/state` `image_extract` `audit_scan` `reference_lookup` | `bmm:Report`（标题"落库核对报告"） |
| **D 综合与判断** | `document_review`（按策略逐条判定）｜`revision_trace`（轮次比对/溯源）｜`skill_development`（设计单/工具契约/安装指引/评测） | `rules_of_policy` `graph_query` `review_apply` `ontology_types` `save_knowledge` `audit_scan` | `bmm:Report`（标题"判断/评阅报告"） |
| **E 结项** | `session_provenance`（会话页 + 改进建议总览页） | `save_knowledge` `wiki_read_page` `audit_scan` | `bmm:Report`（标题"结项报告"，**含人员反馈**） |

**合并后的智能体（`bodhi-knowledge-mgmt`）提示词结构**（一个入口，三步）：

```
① 读阶段声明：从「企业知识管理领域」库读「企业知识管理流程」+ A–E 段页（启动/结束条件、产出报告类型）
② 路由到技能：按当前要做的事选技能（§6 表）；技能仍"只限模型、不限类型"，类/关系一律 ontology_types 现查
③ 按"四律"收尾：确认起点文件 → 每段落一份报告 → 结项报告必含人员反馈 → 报告只作证据、判定只读资产
```

---

## 7 落地清单（v0.3，等 §8 裁决后开工）

| # | 动作 | 产出 | 粗估 |
|---|---|---|---|
| **1** | **TTL 调整（不新增报告类型）**：新增基类 `bmm:Report`（`wikiContent` 的 domain 改指它）+ 4 个既有报告类由"domain 追加"改**继承** + **`bmm:Assessment` 摘掉 `wiki_content`** | `ontology/sources/bmm.ttl` / `agent.ttl` diff | 0.5 天 |
| **2** | ✅ **继承法已验证**（§1.6）；上线按 **§1.4 五步**：改 TTL → 编译 → **重投影 Neo4j（必做）** → **抽查 `render_mode`**（4 类 document、Assessment entity）→ 巡检 | 编译日志 + `render_mode` 抽查表 | 0.5 天 |
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
| **D1** | 报告类型清单 | ✅ **不新增报告类型**（按你的裁定）：唯一新增是**基类 `bmm:Report`**；阶段报告统一用它（段性质写标题/元数据）；4 个既有报告类（`Advice`/`DesignSpec`/`Evaluation`/`InstallGuide`）只改继承；`Assessment` 摘掉 `wiki_content` → 报告类合计 **5 个** |
| **D2** | 报告类怎么获得 `wiki_content`：继承 vs domain 追加 | ✅ **已实测验通，采用继承**（§1.6：编译器接受跨模块 `subClassOf`，产物 `parents=…bmm:Report`）；domain 追加保留为**兜底** |
| **D3** | **5 段**是否合适（A 立项 / B 对齐 / C 资产化 / D 判断 / E 结项） | ✅ 5 段；若嫌多，**D+E 可合成"综合判断与结项"**（4 段，报告也合 1 类）——但那样"结束报告含人员反馈"的分量会变轻，我倾向保持 5 段 |
| **D4** | 会话四律的强度 | 律一/律二/律四＝**纪律**（技能与提示词约束）；律三（人员反馈）＝**硬门禁**（可被巡检查、无反馈的结项报告不算完成） |
| **D5** | 阶段声明的形态 | **5.1**（零本体改动：`bmm:BusinessProcess`/`CourseOfAction` 实例 + 页面承载） → 稳定后再考虑 5.2（新增 `bmm:Stage`） |
| **D6** | 是否新增巡检 3 个 kind | ✅ 建议加（否则"律二/律三"只是口号）；先做 `session_without_closure` + `report_without_feedback` |
| **D7** | `agent:Advice`（改进建议） | 现在靠 domain 追加成文档类 → **改继承 `bmm:Report`**，去掉那句 domain 追加（更干净） |
| **D8** | 本次先做哪几步 | **§7-1（改 TTL）→ §7-2（按 §1.4 五步上线：编译 + 重投影 + 抽查 `render_mode`）→ §7-4（建「企业知识管理流程」+ A–E 段页）**；跑通再 5→6→7→8 |

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



