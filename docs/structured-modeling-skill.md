# 结构化数据批量建模 · 技能与工具设计（2026-09-29）

> **用户口径**：设计一个给智能体用的**结构化数据批量建模技能** —— 接受**自然语言描述的 Excel 结构**，
> 对应到本体模型的**类 / 关系 / 数据属性**，然后组织**批量建模**；
> **约定：一次批量建模只包含一种关系、或一个类（含其数据属性）** —— 这样对同一份文件**多次扫描**即可完成全部创建。

## 0. 为什么这样切（与"对话式抽取"的分工）

| | 非结构化（方案/纪要） | **结构化（本设计）** |
|---|---|---|
| 入口 | `doc_outline` → 智能体读片段 → `save_knowledge` 分批 | `import_probe` → 智能体出**映射方案** → `import_apply` 逐目标 |
| 谁决定"抽什么" | LLM 理解语义 | **人给的自然语言列描述 + 本体校验**（确定性） |
| 量级 | 一篇文档几十条 | 一份表几千行 |
| 确认成本 | 每批回执 | **一次映射确认**，之后逐目标批量跑 |
| 失败模式 | 幻觉类型/漏关系 | 列对不上/取值不在枚举（工具直接报，不写库） |

## 1. 不变量（写进工具，不靠提示词）

1. **一次一个目标**：`kind=class` 时 `target` 是**一个类**（顺带它声明的数据属性）；`kind=relation` 时 `target` 是**一条关系**。
   其它一律拒（`need_single_target`）——这样"多扫几遍文件"就是天然的分批机制。
2. **两段式**：`import_plan`（只读，出 `ticket` + 影响面 + 风险）→ 用户确认 → `import_apply`（带同一 `ticket` 才写）。
3. **幂等可重跑**：页/关系带 `last_edit_source='bodhi-import:<batch>:<target>'`；内容哈希未变的行**零写入**；
   重跑只更新变化行（快照 + `version+1`，可回退）；`prune=true` 时软删"源里已消失"的行（只限本批）。
4. **零第三方依赖**：xlsx = 标准库 `zipfile + xml.etree` 读 `xl/worksheets/sheetN.xml` + `sharedStrings`；
   csv/tsv = 内置；**老式 `.xls`/`.doc` 不支持**（要求另存为 `.xlsx/.csv/.docx/.md`）。
5. **巡检干净**：每页必有 `page_type`（本体类）+ `## 原文依据`（该行**逐字**原文）+ `source_refs`（来源文件 sha256 + sheet + 行号）。
6. **写权限**：`ke_db.assert_can_write(kb_id, tenant)`（与既有写路径同一守门）。
7. **账本**：`state/import/<batch>.json` 记每个目标的 plan/ticket/结果/时间 → `import_state` 可查"还差哪些"。

## 2. 技能（`skills/structured_modeling/SKILL.md`）

front-matter（MCP 的 `skills(skill=…)` 会据此**收窄本体面**，智能体拿到的就是"这个技能能碰的类/关系"）：

```yaml
---
id: structured_modeling
name: 结构化数据批量建模（Excel/CSV）
when: 用户给出「结构化文件（Excel/CSV）」+「列的含义」+「要建到哪个库」，需要按本体类/关系批量建页
models: [bmm, ea]
stages: [probe, plan, apply, audit]
scope:
  classes: [bmm:MainSystem, bmm:SubSystem, bmm:HardwareAsset, bmm:ITAsset]   # 示例；可按需扩展
  relations: [bmm:mainSystemContainsSubSystem, bmm:hardwareAssetBelongsToSubSystem]
tools: [import_probe, import_plan, import_apply, import_state, ontology_types, audit_scan, wiki_read_page]
version: 1
---
```

正文（要点）：
1. **先问全三件事**（一次问清，不来回）：文件路径 / 目标知识库 / **每个 sheet 的列 → 语义**（自然语言即可）。
2. `import_probe` 把文件结构（sheet、列名、行数、抽样 3 行、疑似主键）摆出来 → **和用户对齐映射**（这一步只做一次）。
3. 逐目标：`import_plan(kind=…, target=…)` → 把回执里的**影响面 + 风险 + 样例页**念给用户 → 用户同意 → `import_apply`。
4. `import_state(batch)` 看进度 → 继续下一个目标，直到 `remaining=[]`。
5. 末尾 `audit_scan(kb, scope=all)`：**C1/F3/B1 必须全绿**才算完成，否则照 fix 修。

## 3. 工具契约（新增 4 个 MCP 工具 + HTTP 对应端点）

### 3.1 `import_probe(file)` — 只读
```
输入: {file: "/path/系统台账.xlsx", sheet?: "主系统", rows?: 3}
输出: {sheets:[{name, columns:[…], rows: 2001, sample:[[…],[…]]}], sha256, encoding?}
```
> 只看结构，不读全文（4k 行也只回抽样 + 统计），避免把上下文撑爆。

### 3.2 `import_plan(kind, target, file, sheet, mapping, kb_id, key_column?, …)` — 只读，出 ticket
```
输入: {kind: "class", target: "bmm:MainSystem", kb_id: "企业知识",
       file: "…xlsx", sheet: "主系统", key_column: "系统编号",   # 行→slug 的唯一键
       mapping: {"英文名称": "englishName", "功能简介": "description",
                 "系统编号": "systemNo", "英文简称": "systemAbbr",
                 "重要性等级": "systemCriticality", "状态": "systemStatus"},
       aliases: ["英文简称"], title: "{英文名称}（{系统编号}）"}
输出: {ok, ticket, target, plan:{pages: 2001, create: 1998, update: 3, prune: 0},
       samples:[{slug, title, content_preview}], unknown_columns:[], violations:[…],
       required_risks:["bulk_create_2001","external_source"], generated_at, permission}
```
校验（**工具做，不靠模型自律**）：
- `target` 必须是本体类（`ke_ontology.classes()`）；`mapping` 的值必须是**该类或其祖先声明的数据属性**（否则报 `attribute_not_declared` 并给出可用清单）；
- `key_column` 必须存在且**取值唯一**（否则 `duplicate_key` 列出重复项样本）；
- 未映射的列进 `unknown_columns`（**不报错**，由用户决定“忽略/另建类/另建关系”）；
- 关系目标（`kind=relation`）：`target` 必须满足 domain/range；外键列取值必须先**在目标库存在对应页**，否则 `dangling_ref`（列出前 10 个缺失值）。

### 3.3 `import_apply(ticket, acknowledge_risks, prune?, actor?)` — 写（**一次一个目标**）
```
输出: {ok, batch: "imp-20260929-01", target, created: 1998, updated: 3, pruned: 0,
       pages: […前 10…], relations_written: 2001,
       ledger: "state/import/imp-20260929-01.json", duration_ms: 4210}
```
实现要点：按 500 行/事务多值 `INSERT`；关系直写 Neo4j 图边（`out_links`/`in_links` 已废弃恒空，不再派生/重算）；
`sync_folders --link-pages`（目录树）；不触发相似度/待确认合并（那是 `save_knowledge` 的活）。

### 3.4 `import_state(batch?)` — 只读
```
不传 batch: {recent:[{batch, kb_id, file, targets_total, targets_done, updated_at}]}
传 batch:   {batch, targets:[{kind, target, status: planned|applied|skipped|failed, pages, at}], remaining:[…]}
```
> 智能体靠它做"循环到收敛"：`remaining` 为空即整个文件扫完。

## 4. 列 → 页面的落点（同一套模板，便于巡检/图谱/前端）

```markdown
# Core Banking System（SYS-0001）

> **本体类型**：主系统（`bmm:MainSystem`）
> **生成方式**：结构化批量建模（`bodhi-import:imp-20260929-01:bmm:MainSystem`，2026-09-29 22:10:00 +0800）
> **来源**：`系统台账.xlsx`（sha256:1a2b…）sheet=`主系统` 第 12 行

## 定义

<功能简介列>

## 属性

| 属性 | 取值 |
|---|---|
| 系统编号 | SYS-0001 |
| 英文简称 | CBS |
| 英文名称 | Core Banking System |
| 重要性等级 | 高 |
| 状态 | 在用 |

## 原文依据

> SYS-0001 | CBS | Core Banking System | 核心账务处理 | 高 | 在用

## 本体关系

- 包含子系统（`bmm:mainSystemContainsSubSystem`）→ [[bmm/subsystem/SUB-01|存款子系统]]    ← 关系批次写这一行
```
- `slug` = `<模块>/<类小写>/<key列值>`（如 `bmm/mainsystem/SYS-0001`）；类小写映射见既有约定（`SubSystem`→`subsystem`）；
- `aliases` = 英文简称/英文名称（让 `wiki_search` 命中）；`summary` = 功能简介；
- `page_metadata.ontology` = `{model, class, name, attributes:{…}, import:{batch, file_sha256, sheet, row}}`；
- 属性列**同时**进 `page_metadata.ontology.attributes`（机器可读）+ 正文属性表（人可读）。

## 5. 自然语言 → 映射：智能体怎么定、工具怎么兜

**智能体负责"理解 + 提议"**（它读列名/抽样/用户描述）：
- 一个 sheet 的列大致分三类 → 「主体列」（构成一行的一个实体）｜「属性列」（该实体的数据属性）｜「外键列」（指向另一个类，用来建关系）；
- 按语义挑类：如「主系统／核心系统／平台」→ `bmm:MainSystem`；「子系统／应用／模块」→ `bmm:SubSystem`；「服务器／网络设备／终端」→ `bmm:HardwareAsset`；
- 挑属性：把中文列名对到本体已声明的数据属性（`系统编号→systemNo`、`重要性等级→systemCriticality`…），**英文名称/功能简介复用 `englishName`/`description`**；
- 认不出的列**不要硬塞**：留在 `unknown_columns`，问一句"这几列要不要新建数据属性（需改本体 TTL）／忽略／当成别名"。

**工具负责"确定性校验"**（不给模型留幻觉空间）：类必须存在、属性必须已声明、关系必须满足 domain→range、键列必须唯一、外键必须能命中目标页、枚举取值（若属性有受控词表）必须合法。**任何一条不过 → 不写库**，回执带可用清单，智能体自纠后重试（**不需要人来判**）。

**歧义只问一次**：`import_plan` 里的 `questions[]`（如"`状态`列取值 3 种，是否需要受控枚举类？"）在**同一次**交互里问完，避免逐行/逐目标打扰。

## 6. 多遍扫描怎么收敛（用户的核心诉求）

```
第 1 遍  import_plan(kind=class,    target=bmm:MainSystem)     → apply   （只建主系统这类，含它的数据属性）
第 2 遍  import_plan(kind=class,    target=bmm:SubSystem)      → apply   （子系统 + coreFunction）
第 3 遍  import_plan(kind=relation, target=bmm:mainSystemContainsSubSystem) → apply（主子归属，读「主系统编号」外键列）
第 4 遍  import_plan(kind=relation, target=bmm:hardwareAssetBelongsToSubSystem) → apply
…        import_state(batch) → remaining 为空 → audit_scan 全绿 → 收工
```
每遍都是**同一次读文件**（probe 结果可复用/缓存）；一遍只写一类东西，**失败/回滚半径 = 一个目标**，
且任何一遍都能单独重跑（幂等）——这就是"不反复确认也能安全大批量"的保证。

## 7. 分期

| 批 | 内容 | 验收断言 |
|---|---|---|
| **P0** | `ke_sheet.py`（零依赖 xlsx/csv 读）+ `import_probe` + `import_plan/apply/state`（`kind=class`）+ 技能 `structured_modeling` | 2001 行主系统一次 apply：页数=行数、0 重复 slug、`## 原文依据` 全有、巡检 C1/F3/B1 全绿、耗时 < 60s、重跑 0 写入 |
| **P1** | `kind=relation`（外键列 → 直写 Neo4j 图边） | 2001 条边；`import_state.remaining=[]`；图谱里能看到"主系统 → 子系统" |
| **P2** | Word/MD 条目式（业务规则）→ `bmm:OperativeBusinessRule` + `hasEnforcementLevel(Strict/Advisory)` + 策略页 | 规则页=条目数；执行级别边=条目数；解析不出的片段进"待人工清单 CSV" |
| **P3** | 前端"上传 xlsx → 预检表 → 一键导入"面板（调 HTTP 端点）+ `import_state` 进度条 | 上传到出页 ≤ 1 次点击；进度可见 |

## 8. 待你拍板的 3 点

1. **文件怎么"进得来"**（决定工具入参形态）：
   a. **宿主机路径**（推荐）：运维/用户把 xlsx 放到机器上（如 `/mnt/c/.../系统台账.xlsx`），MCP 直接读；
   b. **MCP 暂存目录**：约定 `state/import/inbox/`，用户放文件 → `import_probe` 只接受该目录内的文件名（更安全、可审计）；
   c. 上传进知识库再让 MCP 去取 —— WeKnora 把原件交给 MinIO，MCP 侧要额外凭据/HTTP，**不推荐**。
2. **来源口径**：这份 xlsx **上传进目标库一次**当来源凭证（巡检 C1 天然干净、原件可检索）／纯外部来源（则要在 `ke_audit` 给 C1 加"`page_metadata.import.file_sha256` 豁免"）。
3. **技能与工具命名**：技能 id `structured_modeling`，工具 `import_probe / import_plan / import_apply / import_state`（要不要改成 `bulk_*` 前缀以区分？）。

