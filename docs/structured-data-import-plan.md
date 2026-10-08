# 结构化数据批量初始化（系统台账 / 业务规则 / 规范）—— 方案（2026-09-29）

> 目标：把**已经整理好**的结构化资料（Excel 主/子系统 ~4k 行、Word/MD 业务规则 ~500 条、架构规范文档）
> **一次性**初始化为 wiki 知识页，**快速、可重跑、不逐条确认**，且**巡检干净**（不制造 C1/F3/B1 脏数据）。
>
> 一句话口径：**结构化数据走"确定性导入"，不走"对话式抽取"**（后者要 LLM、要逐批确认、要处理"待确认合并队列"）。

## 1. 为什么不是"上传文档让智能体抽"

| 维度 | 上传 + 智能体抽取 | **本方案的批量导入** |
|---|---|---|
| 4k 行 Excel | 切片后逐批 LLM 抽取，耗时长、token 贵、结果不稳定 | 读表 → 建页 → 一条事务落库（秒级）|
| 确认成本 | 每轮都要看回执、"待确认合并"要人裁决 | **一次预览 + 一次确认**（或 `--yes` 直接跑）|
| 幂等 | 重跑会产生重复页/合并噪音 | `last_edit_source` 标记 + 整批 delete/insert，**可重跑可回滚** |
| 字段完整性 | 靠模型理解，可能漏列 | **列到页字段一一映射**，缺列直接报错 |
| 溯源 | 有 | 每条带「## 原文依据」逐字摘录（Excel 行 / 规则原文）|

> 智能体抽取仍然保留给**非结构化**材料（方案文档、纪要）；本方案专治"本来就整齐"的数据。

## 2. 数据 → 页面映射（默认方案，零本体变更）

### 2.1 主系统 / 子系统（Excel）

| Excel 列 | 落点 | 说明 |
|---|---|---|
| 系统编号 | `slug` 的一段 + 正文表 + `page_metadata.ontology.attributes.systemNo` | 稳定主键（英文简称可能重名）|
| 英文简称 | `aliases` + 正文表 | 检索用（`wiki_search` 命中）|
| 英文名称 | 正文表 + `aliases` | |
| 功能简介 | `summary`（列表页摘要）+ 正文「## 定义」| |
| 重要性等级 / 状态 | 正文表 + `page_metadata`（**不造新枚举类**）| 以后要治理再进 TTL |
| 核心功能（子系统） | 正文「## 核心功能」| |
| 主系统 / 子系统（Excel） | 页 `bmm/mainsystem/<系统编号>` / `bmm/subsystem/<系统编号>`，`page_type=bmm:MainSystem` / `bmm:SubSystem`（2026-09-29 起 IT 资产在 BMM；原 `ea:Application` = `bmm:SubSystem`）|
| 主→子关系 | 子系统页 `## 本体关系`：`- 包含子系统（bmm:mainSystemContainsSubSystem）→ [[bmm/mainsystem/xxx\|…]]`（**本体已提供该关系**，图谱可见）|
| 硬件资产 | 页 `bmm/hardwareasset/<编号>`，`page_type=bmm:HardwareAsset`；`bmm:hardwareAssetBelongsToSubSystem` 指向子系统；属性 `bmm:hardwareAssetCategory`（硬件资产分类）|
| 6 列数据属性 | `bmm:systemNo`（系统编号）/ `bmm:systemAbbr`（英文简称）/ `bmm:englishName`（英文名称，复用）/ `bmm:description`（功能简介，复用）/ `bmm:systemCriticality`（重要性等级）/ `bmm:systemStatus`（状态）；子系统另加 `bmm:coreFunction`（核心功能）|

```markdown
# <英文名称>（<系统编号>）

> **本体类型**：应用系统（`ea:Application`）
> **生成方式**：结构化导入（`bodhi-import-systems`，2026-09-29 12:00:00 +0800）
> **来源**：`系统台账.xlsx`（sha256:1a2b…）sheet=`主系统` 第 12 行

## 定义

<功能简介>

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
```

- `slug`：`ea/application/<系统编号>`（编号稳定唯一；英文简称放 `aliases`，避免重名撞 slug）。**实例页 slug 三段式**：模块/类小写/名称（否则 D1/B1）。
- `page_type`：`ea:Application`（现成本体类，前端类型下拉/图谱/关系面板都认它）。
- `source_refs`：登记来源文件（见 §5），否则巡检 **C1（high：无来源）**。

### 2.2 业务规则（Word / MD，条目式）

| 规则文档中的东西 | 落点 |
|---|---|
| 文档本身（"业务策略"）| 一页 `bmm/businesspolicy/<文档名>`，`page_type=bmm:BusinessPolicy` |
| 每条规则 | 一页 `bmm/operativebusinessrule/<规则编号>`，`page_type=bmm:OperativeBusinessRule` |
| 「强制 / 推荐」标签 | **`bmm:hasEnforcementLevel` → `bmm:Strict`（强制）/ `bmm:Advisory`（推荐）**（现成枚举，取值还有 `bmm:Override`）|
| 规则出自该文档 | 规则页 `## 本体关系`：`- 派生自（bmm:isDerivedFrom）→ [[bmm/businesspolicy/<文档>|…]]`；策略页反向靠 `in_links` 自动生成 |
| 规则逐条原文 | `## 原文依据` 里**逐字复制**该条 → 巡检 F3 通过、可追溯 |

```markdown
# BR-0132 手机银行开户须核验三要素

> **本体类型**：操作性业务规则（`bmm:OperativeBusinessRule`）
> **执行级别**：强制（`bmm:Strict`）
> **来源**：`应用架构规范v3.docx`（sha256:9f8e…）第 4.2 节 第 132 条

## 规则

<规则正文（保留原文措辞）>

## 原文依据

> 4.2.3 客户在手机银行渠道开户时，必须先完成三要素核验，核验不通过不得进入下一步。

## 本体关系

- 派生自（`bmm:isDerivedFrom`）→ [[bmm/businesspolicy/应用架构规范v3|应用架构规范 v3]]
- 具有执行级别（`bmm:hasEnforcementLevel`）→ [[ontology/bmm/enforcementlevel|执行级别（bmm:EnforcementLevel）]]：strict
```

- 枚举类（`bmm:EnforcementLevel`）**不建实例页**（本体口径：枚举取值只作属性值，不抽成知识页）→ 只在**边属性/正文**里出现。
- 解析口径：优先按 `第N条 / N. / N) / - / •` 切条；切不出来的片段**整段进"待人工清单"CSV**，**不阻塞**其它条目（你一次过目即可）。

### 2.3 主系统 → 子系统：关系 vs 目录树（**唯一需要你拍板的模型问题**）

现成本体里**没有**"系统包含子系统 / 应用分解为应用"这条关系（EA 里与 `ea:Application` 相关的是
`ea:applicationProvidesService`（应用→服务）、`ea:stepSupportedByAsset`（步骤→IT 资产））。
三种处理：

| 方案 | 做法 | 代价 |
|---|---|---|
| **A（默认，推荐）** | 用**目录树 + `parent_slug`** 表达归属（主系统为父目录，子系统挂其下），不建本体边 | 零本体变更；图谱里看不到这条边 |
| B | 在 EA 扩展 TTL 里**新增关系** `ea:applicationPartOf`（子系统 → 主系统），编译 + 重投影 + 重建前端类型清单 | 改本体（要跑编译/投影/前端重建），图谱能看见 |
| C | 子系统页 `## 本体关系` 用 `ea:stepSupportedByAsset` 之类**借道** | 语义不准，不推荐 |

## 3. 工具形态（一次预览 → 一次确认 → 秒级导入）

新增 `tools/ke-import/`（**零第三方依赖**，只用标准库，与仓库"零依赖"口径一致）：

| 命令 | 作用 |
|---|---|
| `ke_import.py probe --file 系统台账.xlsx` | 只读文件：sheet 列表、列名、行数、抽样 3 行（**先确认列名映射**）|
| `ke_import.py preview --spec systems.yaml --file … --kb <kb>` | 出**预检报告**：将新建/将更新/重复 slug/缺列/超长字段/预计关系数/**ticket** |
| `ke_import.py apply --spec … --ticket <t> --yes` | 落库（默认 500 行/事务分批）；回执给页数、耗时、版本、跳过行 |
| `ke_import.py apply … --sql out.sql` | 只生成 SQL（可审计、可离线给客户 DBA 跑）|
| `ke_import.py rollback --batch <id>` | 按台账 JSON 回滚（删本批新建 + 恢复更新前快照）|

- 列名映射写在 **`spec.yaml`**（客户列名不同只改配置，不改代码）：
  ```yaml
  kind: systems            # systems | rules
  kb: <kb 名或 uuid>
  source: {file: 系统台账.xlsx, sheet: 主系统, row_range: "2-2001"}
  columns: {系统编号: system_no, 英文简称: abbr, 英文名称: en_name,
            功能简介: intro, 重要性等级: criticality, 状态: status}
  page: {type: ea:Application, slug: "ea/application/{system_no}", title: "{en_name}（{system_no}）"}
  ```
- 通道（可选，按需加）：HTTP `POST /bodhi/import/{preview,apply}`（便于前端"上传 xlsx → 预览表 → 一键导入"）
  与 MCP 工具 `import_preview`/`import_apply`（智能体也能把"初始化"干完）。**默认先只做 CLI**（最可控）。

## 4. 幂等 / 性能 / 回滚（"无需反复确认"的机制保证）

- 每页带 `last_edit_source='bodhi-import-systems-<batch>'`：
  - **重跑**：内容哈希未变的行**零写入**（不产生噪音版本）；变化的行走"快照 `wiki_page_revisions` + `version+1`"（可回退）；
  - **源里删掉的行**：`--prune` 时本批软删（不删别的批次/别人手工建的页）；
  - **回滚**：`state/import/<batch>.json` 台账 + 快照，一条命令回到导入前。
- **性能**：4.5k 页按 500 行/事务 → **单事务多值 `INSERT`**，总耗时秒级~十几秒；
  关系直写 Neo4j 图边（`out_links`/`in_links` 已废弃恒空，不再派生/重算）。
- **不走 `save_knowledge`**：那条路有"相似度匹配 → 合并/待确认"逻辑，4k 行会产生大量"待确认"，
  正好违背"不要反复确认"。
- 导入后固定三件事：① 重算 `in_links`；② `sync_folders.py --kb-id <kb> --link-pages`（目录树）；
  ③ 巡检一次 `ke_audit.py scan <kb> --scope all`（要 C1/F3/B1 **全绿**）。

## 5. 来源登记（决定巡检是否干净）

巡检 **C1** 要求实例页有 `source_refs`。两种做法：

1. **推荐**：把 `系统台账.xlsx` / `应用架构规范v3.docx` **上传到该知识库一次**（当来源凭证）→
   页面的 `source_refs` 指向该 `knowledge_id`；好处：原文件可被检索、`ke_docs` 统计/清理都正常；
2. 不上传（纯离线导入）：`source_refs` 存 `文件名 + sha256`（外部来源标记）；
   需要给巡检加一条"外部来源豁免"口径（我会在实现里加，避免误报 C1）。

## 6. 环境事实（已实测，决定了实现方式）

| 能力 | 实测 |
|---|---|
| `openpyxl` / `pandas` / `python-docx` / `lxml` | **没有** → 用标准库 `zipfile + xml.etree` 直接读 xlsx/docx（xlsx=zip 内 `xl/worksheets/sheetN.xml` + `sharedStrings`；docx=zip 内 `word/document.xml`）|
| `pandoc` / `libreoffice` / `unzip` / `7z` | **没有** → **老式 `.doc` / `.xls`（二进制）不支持**，请另存为 `.docx`/`.xlsx`/`.md`/`.csv` |
| 已有知识库 | 企业知识(123 页)、企业本体模型(248)、FD案例沙箱(37)、领域知识库-测试1(51)、领域知识0924(54)、鞍山初级(1)、企业共享概念模型(4) |

## 7. 分批与验收（我按这个顺序做）

| 批 | 内容 | 验收断言 |
|---|---|---|
| **P0** | `probe` + `preview` + Excel→`systems` 导入（4k 行，含目录树/`source_refs`）| 页数=数据行数；0 重复 slug；缺列=0；每页有本体类型/属性表/原文依据；`in_links` 重算成功；巡检 C1/B1/F3 全绿；耗时 < 60s |
| **P1** | Word/MD→`rules`（500 条 + 策略页 + 执行级别边）| 规则页 500；"待人工清单"只含解析不出的片段（可 0）；`bmm:hasEnforcementLevel` 边数=500；规范页 `in_links` 指向各规则 |
| **P2** | `rollback` + `--sql` 导出 + 前端"上传→预览→一键导入"面板（可选）| 回滚后页数回到导入前；导出的 SQL 能在空库跑通 |

## 8. 待你拍板（实现前只问这一次）

1. **落点库**：新建「系统与架构知识」库，还是落进现有「企业知识」库？
2. **主→子系统**：方案 A（目录树，零本体变更，推荐）/ B（扩展 EA 加关系，图谱可见）/ C 借道？
3. **来源**：Excel/Word **上传一次**当来源凭证（推荐）/ 纯离线（要加豁免口径）？
4. **规范文档**：整篇规范只做「策略页 + 规则条目」，还是**同时**保留整篇文档页（便于全文检索）？

