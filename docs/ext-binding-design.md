# 外部接口绑定：真源 → 投射 → 面板 → 闸门（设计）

> 2026-10-25 ｜ 口径来源：用户 A 案（本体加第 11 列「专家反馈」+ 表体逐字用工具回执 + 反馈 `update/if_rev`）
> 本文是**设计真源**；实现进度见 §6。

## 1 问题（为什么不能各写一份）

同一件事被写了三遍 ⇒ 必然漂移：

| 资产 | 现在写的 | 谁维护 |
|---|---|---|
| DataObject 页 `skos:definition` | "11 列 / 枚举 / 指纹口径"（手写文本） | 人 |
| it-platform 容器 `schema.json` | `issue_order / issue_labels / verdict_enum / severity_enum` | 人 |
| 工具回执 `schema` | 由上一行派生 | 代码 |
| 技能 / 提示词 / 前端面板 | 又各引用一次 | 人 |

**结论**：列名 / 列序 / 枚举 / 主键 / 模式 / 版本策略 —— **只能有一个真源**。

## 2 L0 真源：MCP `tools/list` 的 `x-ext`

`it-platform` 每个工具在 `tools/list` 里附带数据面契约（`deploy/mock-ext/it-platform/schema.json` 的 `x_ext` 段）：

```jsonc
"x-ext": {
  "objectKind": "issue_list",
  "keyFields": ["review_no", "issue_no"],
  "modes": ["query", "maintain"],
  "revScope": "document",                     // if_rev 是台账级
  "views": ["rows", "table"],
  "columns": [
    {"field": "issue_no", "label": "编号", "key": true, "generated": true},
    {"field": "verdict",  "label": "判定", "enum": ["符合","不符合","不适用","无法判定"]},
    {"field": "expert_feedback", "label": "专家反馈", "editableBy": "expert"}
  ],
  "schemaHash": "sha256:fb9b71cd8b67f376"     // 契约指纹（判漂移）
}
```

- `enumRef` 在 `schema.json` 内解析成 `enum`（枚举只定义一次）；
- `schemaHash` = 规范化 JSON 的 sha256 前 16 位 ⇒ **任何客户端**都能一句话判断"我手里的契约是否过期"；
- 契约文件随**服务版本**发布（`schemas._contract()` 优先读 seed，容器里的副本只作兜底）。

## 2.1 绑定解析：`kb_id` 可省 + 跨库兜底（2026-10-25 实测补的口径）

智能体调用时传的 `kb_id` 是**工作库**（数据所在），而 DataObject（绑定）写在**领域库** ——
旧解析只看传入库 ⇒ 直接 `no_binding`，报告里就出现"台账不可用（待澄清）"。

现在 `ke_ext_store.resolve_binding`：

1. `kb_id` 省了但给了 `object` ⇒ 按 slug **全库反查**库；
2. `kb_id + object` 都给了但本库取不到绑定 ⇒ **按 slug 跨库找**，命中即用（回执带 `binding_kb`）；
3. `object` 也省了 ⇒ 按接口名全库反查候选（每条候选带 `kb_id`，多条才要求指定）。

实测：传工作库 → `ok=True`（`binding_kb` 指向领域库）；不传 `kb_id` → 直接命中。

## 3 L1 投射：页面只留"绑定 + projection"

| 页面元素 | 真源 | 谁写 | 手改 |
|---|---|---|---|
| `bpmn:dataObjectInterface / AccessMode / KeyFields / InterfaceRef` | 人工（绑定意图） | `page_attributes_set`（**ASCII 白名单校验**） | ✅ |
| `page_metadata.ontology.projection` | MCP `tools/list` | `ext_describe`（订阅 + 落投影 + hash + fetched_at） | ❌ |
| 正文「接口投影」段 | projection 渲染 | `ke_pages`（标 `generated`） | ❌ |
| `skos:definition / note` | —— | —— | 有绑定时**不再写列规范**（回 `warnings`） |

绑定串格式（**ASCII**，一次定死）：`,` 分接口；`;` 按序对齐；`:` 服务名前缀（`it_platform:`）。

## 4 L2 报告 + 前端面板

报告正文该节写**引用块 + 快照**（既可导出、又可查漂移）：

```bodhi-ext
object: bpmn/dataobject/问题清单
review_no: YYYYMM-11111111
basis: it_platform / ext_issue_list
rev: 7
schema_hash: sha256:fb9b71cd8b67f376
```

- 前端遇该块 ⇒ `GET /bodhi/ext/list?...`（nginx → bodhi 代理到 it-platform；浏览器不直连 8770）⇒ 渲染**可编辑表格**；
- 列 / 列序 / 标签 / 枚举 / 只读列**全部来自 projection**；不给加列；
- 行内编辑 ⇒ `POST /bodhi/ext/update`（`actor` / `session_no` / `if_rev`）；`conflict` ⇒ 提示并重取，**不覆盖**；
- 成功后：刷新面板 → **回写快照**（`view=table` 的 markdown 节级替换）+ `rev/hash` 同步 + 报告 `version+1`。

### 4.1 报告只用一部分列 ⇒ 由**模板**选字段与顺序（2026-10-25 用户口径）

工具契约的字段**可以多于**某一份报告所需（例：`计划解决时间` / `实际解决时间` / `项目组反馈`），
所以\"这份报告要展示/编辑哪几列\"不能由工具定、也不该写死在面板里 ⇒ **由报告模板配置**：

1. 模板（`schema:DocumentTemplate` 实例）该小节的章节结构里给出**字段子集** —— 表头加一列
   **「数据对象字段（逗号分隔，顺序即展示顺序）」**，`—` = 该节不取外部数据；
2. 组文时调 `ext_issue_list(view="table", columns="<模板那格>")` —— 台账表只出这几列；
   非法字段 ⇒ `bad_columns` + `allowed`（**不静默丢列** ✗）；
3. 引用块带上同一份子集（`columns:` 行，`sync-report` 会自动写回），面板按它**只渲染这几列**（顺序同模板）；
4. 回写快照时按同一 `columns` 重出表 ⇒ 正文列集与模板永远一致（列集真源 = 模板；契约真源 = `x-ext`）。

模板只负责\"**筛哪些字段 + 先后顺序**\"，不改契约本身；同一 DataObject 的不同报告可以有**不同的字段子集** ✓。

### 4.2 DataObject 实例页：契约卡（只读 + 刷新）

`ext_describe` 回执带 `ref_block`（```bodhi-ext-contract / `kind: contract`），工具可**幂等**写进
DataObject 页正文（`write_anchor=true`，已有同类块就整块替换、没有就追加）⇒ 页面就有\"契约卡\"：

- 卡片展示：接口名 / 模式 / 主键 / `schemaHash` / `fetched_at` / **列清单（字段·标签·枚举）**；
- 列/枚举只认 `tools/list`（服务端单一真源）⇒ 卡片**只读**，没有\"加列\"按钮；
- 「刷新契约」= `ext_describe(write_projection=true, write_anchor=true)` ⇒ 重取契约 → 落投影 → 重写锚点 → 页面重载；
- 打开页面只读契约（`write_projection=false`）⇒ 看一眼不会改库 ✓。

### 4.3 锚点与编号：**工具补块、编号取自上文**（2026-10-25 用户实测修正）

问题：模型漏写引用块 ⇒ 页面**没有锚点** ⇒ 面板整块不出现；而"编号"本来就在报告 ① 表里，
不该由模型手写（实测出现过照抄示例编号的情形 ✗）。

现在的口径：

| 环节 | 做法 |
|---|---|
| **写库时补锚点** | `save_knowledge(stage="report")` 检测到"正文有条目表但**没有** `bodhi-ext` 块" ⇒ `ke_ext_gate.auto_ref_block` **自动补**（插在匹配到的表之后）。回执给 `ext_ref_block{object, review_no, rev, columns, review_no_source}` |
| **编号从哪来** | ① 正文里出现的**台账分片名**（= 外层主键值，唯一命中才算）→ ② 台账分片名不在正文时退回"条目编号重叠"。**不再依赖引用块、也不写死** ✓ |
| **object 从哪来** | 该库（本库没有 ⇒ 全库）里**契约标签与该表表头对得上**的 DataObject（`labels[order[0]]` 必须在表头，且命中 ≥3 列）；**多候选不猜**（回 `object_ambiguous` + candidates）✓ |
| **面板不硬要编号** | 引用块缺 `review_no` / 缺 `schema_hash` 也照样挂载（`isRefBlock` 只认 `object:`）；编号由后端解析：`GET /bodhi/ext/list?object=…&title=<页面标题>`（或 `&slug=`）⇒ 回执带 `review_no` + `review_no_source`；面板标题显示「编号取自报告上下文」 |
| **契约先问再找** | `GET /bodhi/ext/schema?object=…` ⇒ `{key, key_label, keys, labels, order}`（**不需要编号**），面板据此知道"编号那一列叫什么" |

实测（666555 那份报告，原本完全没有引用块）：
```
save_knowledge(dry_run) → warnings=[]  ext_ref_block={object: 问题清单,
        review_no: YYYYMM-666555, rev: 11, review_no_source: report_context, columns: 11 项}
apply          → 页面 version 2→3，正文新增 ```bodhi-ext 块
GET /bodhi/ext/list?object=…&title=…（不给编号） ⇒ ok=True review_no=YYYYMM-666555 source=ref_block rows=11
巡检 scope=ext ⇒ findings=0（G4 blocks=2/ledgers=2/stale=0；G5 template_checked=2/header_mismatch=0）
```

## 5 L3 闸门（分级，用户 2026-10-25 确认）

| 闸 | 位置 | 触发 | 强度 |
|---|---|---|---|
| **G3 自造编号** | 报告写入 | 条目编号 ⊄ 台账主键 | **硬失败** `ext_unknown_items` |
| G1 schema 漂移 | 报告写入 | 正文 `schema_hash` ≠ 当前 `x-ext.schemaHash` | 软：`warnings:["ext_schema_stale"]` |
| G2 行数不符 | 报告写入 | 表行数 ≠ 台账 `counts.items`（**只数第一张带编号列的表**） | 软：`warnings:["ext_count_mismatch"]` |
| G4 报告落后台账 | `audit_scan(scope="ext")` | 报告 `rev` < 台账 `rev` | low 告警 + 面板"同步" |
| **G5 三方不一致** | `audit_scan(scope="ext")` | 正文表头 ≠ 模板「数据对象字段」/ ≠ 契约 `columns` 标签；`columns:` 含契约外字段；DataObject 定义又写列清单（职责越界） | medium（前三条）/ low（越界） |
| G5 调用证据 | it-mcp 访问日志 | 只记 HTTP 层（无法区分 list/call） | 已修：记 `rpc=<method>/<tool>` |

## 5.0 职责边界（2026-10-25 用户口径：**各管一段，互不越界**）

| 角色 | 唯一职责 | 不做什么 |
|---|---|---|
| **DataObject 实例页** | **接口定义**：4 个数据属性绑定（接口/模式/主键/提供方）+ 契约投射（`x-ext` → `page_metadata.ontology.projection`）+ 读写口径（怎么调、谁分配编号、指纹由工具算） | 不写**列名/列序/列数**、不写"报告怎么写"（表后补充/覆盖/阈值/图证据）⇒ 这些归模板 ✗ |
| **报告模板（`schema:DocumentTemplate`）** | **报告口径真源**：章节结构 + 每节的「数据对象字段」（要哪几列、什么顺序）+ 写作口径（覆盖要求/表后补充/事实三态/阈值/图证据/汇总） | 不定义接口、不存数据 |
| **外部台账** | **数据正本** | —— |
| **面板 / 巡检** | 读写入口 / 一致性核对（G1·G2·G4·G5） | 不产生口径 |

**报告生成只读模板**（`ke_report`）：章节/列集/写作口径都来自模板页；DataObject 页的 `skos:definition`
**不参与**报告生成（只作人读的接口说明）⇒ 改 DataObject 不会改变报告长什么样，改模板才会 ✓。
实测（2026-10-25）：把 DataObject 的 6 段写作口径搬到模板后，`save_knowledge(stage="report")` 的
`template` 仍命中（v9）、`missing_required_sections` 为空 ✓。

> **跨库兜底**：报告在**工作库**、模板在**领域库** ⇒ `ke_report.resolve_template` 本库找不到时**全库找**
> （命中回执带 `template_scope=global`）；本库自带多候选则**不跨库、不静默选**（`ambiguous`）✓。
> 不跨库会"静默跳过校验"，等于报告根本没按模板生成 ✗（本轮实测踩到并修复）。

## 5.1 规则指纹：由**工具**算，不由模型拼（2026-10-25 实测补）

实测发现报告里 `规则指纹` 列是空的，模型给的理由是"规则解析回执未返回 `ruleHash`，未自行计算" ——
但本体口径要求"**机器生成的条目必须有指纹**"。靠"要求模型自己拼 hash"必然多端漂移 ✗，所以：

- `ke_ext_issues._fingerprint(rule, doc_loc, verdict, quote)` = `r-` + sha256 前 6 位（四要素缺一 ⇒ 空，宁缺不编）；
- **写路径**：`create/update` 时 `rule_fp` 为空则自动补（人工条目带 `human_input` 时留空 ✓）；
- **读路径**：`_load` 时就地补空值 ⇒ 老条目也能在 `rows` / `view=table` 回执里显示指纹，下次写该分片时**自愈落盘**；
- 本体页口径也补了一句"指纹由工具算，不要写「未取得（待澄清）」"。

实测：`YYYYMM-2222222` 的 8 条全部得到 `r-xxxxxx`，并已落盘。

## 5.2 规则清单的「规则指纹」：`bmm:ruleHash` 有值，但工具没带出来（2026-10-25 修）

现象：报告 ② 规则清单的 `规则指纹` 列从 YYYYMM-2222222 起变成"未取得（待澄清）"，
更早的 YYYYMM-11111111 有 16 位 hex；三份报告的 **② 表头完全一致**（7 列含指纹）⇒ 不是格式/技能改坏。

根因：`bmm:ruleHash`（规则级版本哈希，本体里由发布流程写入）**在规则页上是有值的** ——
6 条规则全都存在（`16c4fde43637ed6c` / `acd26930b9eb2542` / `81cfa4c4b6b8d508` /
`22e44ac2f97e874c` / `297f84bc47c18327` / `fa7004fd7b4054c6`，正是 11111111 里那 6 个值），
但 `rules_resolve` 的 Cypher/PG 查询**没取这一列** ⇒ 回执里没有 `ruleHash` ⇒ 模型只能写"未取得"。

修法（`ke_review._rule_contracts`）：

| 回执字段 | 含义 |
|---|---|
| `ruleHash` | 规则页的 `bmm:ruleHash`（有就用它，**报告直接引用** ✓） |
| `ruleHashSource` | `page`（页上有值）/ `computed`（未发布 ⇒ 工具兜底算） |
| `fingerprint` | **统一的取值入口** = `ruleHash`，缺省时 `hash(slug+标题+定义)` 短哈希兜底 |

TTL 的章节规范也补了：`② 规则清单` 的 `规则指纹` **取 `fingerprint`，不得留空、不得自行计算**。

实测（修复后 `rules_resolve` 回执）：6/6 条全是页上原值（16 位 hex），与 YYYYMM-11111111 完全一致。

## 5.3 报告写入闸门（P3，分级；`ke_ext_gate.py`）

在 `save_knowledge(stage="report")` 外层执行（**写库前**；闸门自身异常不挡写）：

| 闸 | 判定 | 强度 | 回执 |
|---|---|---|---|
| **G3** 编号不来自台账 | 报告表里的 `编号` ⊄ 台账主键 | **硬失败**（仅 `mode=apply`） | `ok=false, error=ext_unknown_items, unknown_items[], ledger, ledger_rev` |
| G2 行数不符 | 表行数 ≠ 台账条目数 | 软 | `warnings:["ext_count_mismatch"], counts{report,ledger}` |
| G1 契约漂移 | 正文 `schema_hash` ≠ 当前 `x-ext.schemaHash` | 软 | `ext_schema_stale`；无引用块 ⇒ `ext_no_ref_block` |

台账分片按"与报告编号重叠最多"选择（同一编号不同轮次不混）。实测：真实报告放行（11/11）；
伪造 `P-99` ⇒ dry_run 只给软警告、`apply` **硬失败且未落库**。

## 5.4 前端面板（P4，2026-10-25）

**不重建 Vue 镜像**：报告正文里的 ```` ```bodhi-ext ```` 块，由一段 DOM 级脚本在渲染后替换成可编辑表格。

| 组成 | 位置 |
|---|---|
| 面板脚本 | `deploy/weknora-fork/frontend/ext-panel.js`（10.5 KB，vanilla JS） |
| 注入 | nginx `sub_filter '</body>' '<script src="/ext-panel.js" defer>'`（只作用于 text/html） |
| 挂载 | compose overlay：`ext-panel.js → /usr/share/nginx/html/ext-panel.js:ro` |
| 后端 | `/bodhi/ext/list`（GET，`review_no` 可省 ⇒ 按 `slug`/`title` 从页面上下文解析）、`/bodhi/ext/schema`（GET，列标签/主键标签，**不需要编号**）、`/bodhi/ext/update`、`/bodhi/ext/describe`、`/bodhi/ext/sync-report` → 与 MCP 工具**同一实现** |

面板行为：列/枚举**只认回执**（来自 `x-ext`/投射）；key/generated/rule_fp 只读；改完点「保存」⇒
`POST /bodhi/ext/update`（带 `if_rev`，冲突提示重取）；顶部显示 `台账 rev` 与 `契约 hash`，
与引用块不一致时给黄条提示。

实测：`/ext-panel.js` = 200 / 10546B；`index.html` 已注入；`nginx -t` 通过；
最新报告已插入引用块（version 4→5），闸门 `warnings=[]`、`unknown=[]`、`11/11`。

**待补**：DataObject 实例页上的「接口契约卡（刷新契约）」—— 需要页面渲染器在"有绑定"的实例页输出
```` ```bodhi-ext-describe ```` 块（面板侧渲染逻辑下一步一起加）。数据面板里的「刷新契约」按钮已移除 ✓。

### 快照回写（2026-10-25 已实现）

`POST /bodhi/ext/sync-report {object, review_no}`：找引用该编号的报告页（**全库找**，因为报告在工作库、
绑定在领域库）→ 取台账 `view=table` 的最新 markdown → **按表头标签**（`schema.labels.issue_no`）定位并替换那张表
→ 同步引用块 `rev`/`schema_hash` → 走 `save_knowledge(stage="report", mode=apply)`（**闸门生效**，version+1）。

面板保存一行后**自动调用**它。实测：报告 version 5→6，引用块 rev 18→19，11 行**逐列与台账一致**（判定/严重度/指纹全对）。

## 6 实施状态

| 阶段 | 内容 | 状态 |
|---|---|---|
| **P0** | 24 处域词清零（4 文件，含一处冗余正则分支）+ 基线 | ✅ 域词 24 → **0**，`test_no_business_semantics` 全绿 |
| **P1** | it-platform `tools/list` 下发 `x-ext`；日志加 `rpc` | ✅ 11 列 / 2 枚举 / `schemaHash`；日志见 `rpc=tools/call/ext_issue_list` |
| **P2** | bodhi `ext_describe`（订阅 + projection + hash + drift）；写口规则（projection 拒写、绑定 ASCII 校验） | ✅ 38 工具；投射已落库并回读；三条写口规则实测（见 §5 备注） |
| **P3** | 报告 `bodhi-ext` 引用块 + G3 硬 / G1·G2 软 | ✅ 闸门已挂 `save_knowledge`（§5.3 实测）；引用块随 P4 面板一起加 |
| **P4** | 前端面板 `BodhiExtTable` + `/bodhi/ext/*` + 快照回写 | ✅ 面板 v4.5（按块内容识别、`object:` 即可挂载、契约卡）、接口（`list`/`schema`/`update`/`describe`/`sync-report`）、快照回写（含 `columns:` 写回 + 自动刷新）、**缺块时工具自动补锚点**（§4.3）；实测 666555 报告从"无块无面板"→ 补块后 11 行可编辑 |
| P5 | `audit_scan(scope="ext")`（G4）+ 回归用例 | ✅ `ke_audit.check_ext_ledger`（G4 low；绑定/台账不可读**也从实报**）+ `tests/test_ext_tools.py` 6 例（块解析/rev 纯函数/落后/持平/不可读/门禁只数第一张表）；实测：改台账 → 巡检报「差 1 版」→ 同步 → 归零 |
| P6 | 迁移收口：删 DataObject definition 列清单、任务页 note 去"11 列"；模板加「数据对象字段」列 | ✅ definition 只留口径（列名/列序/列数一律不写）、note 与任务页 note 去列数、列号引用改列名；模板「章节结构」加**第 5 列「数据对象字段」**（`③ 问题清单` = 11 字段有序），`ke_report.parse_template_sections` 回 `fields`、`sync-report` 把 `columns:` 写回引用块 |

## 7 回归与回滚

- P1 属于 `tools/list` 的**附加字段** → 标准 JSON 客户端忽略未知字段；若某客户端严格校验，
  回滚只需把 `server.handle_rpc` 的 `tools/list` 改回 `S.TOOLS`（保留 `schemas.x_ext()` 供 `ext_describe` 用）。
- 相关测试：`tests/test_ext_tools.py`（38）、`tests/test_report_template.py`（9）、
  `tests/test_no_business_semantics.py`（0 命中）、`tests/test_tools_contract.py`（38 工具无漂移）。

## 8 已知遗留 / 运维注意（2026-10-25 收尾）

| 项 | 说明 | 处理 |
|---|---|---|
| 面板浏览器验收 | 后端/接口/巡检均已实测；面板 v4.5 的**挂载与自动刷新**需在浏览器点一次确认 | 报告页 `Ctrl+Shift+R`；Console 应只有 `[bodhi-ext] v4.5 loaded` |
| 两页「首个版本生成」被重渲重置 | **已修复（2026-10-26）**：口径改为「= DB 首版时间」，页头**幂等**（不再每次保存多一段）；存量 11 页已用 `ke_admin.py fix-head` 收敛+校准 | 复核：`fix-head <kb> <slug>` 默认 dry_run |
| `666555` 报告已补锚点 | 该页原**无**引用块；工具 `auto_ref_block` 补了一块（version 2→3），内容未变 | 无需处理 |
| 跨库找模板 | 报告在工作库、模板在领域库 ⇒ `resolve_template` **全库唯一命中才用**，回执 `template.scope=global` + `local_reason`（不静默） | 想"只用本库"就显式传 `template=<slug>`，或把模板复制到工作库 |
| `deploy/bodhi-ops.sudoers` / `wsl-up.sh` 免密 | `wsl-up.sh` 里"重启服务/起容器"等步骤需要免密 sudo；当前用 `wsl -u root` 也可 | 待定：是否安装 sudoers（影响运维脚本能否一键跑） |
| 工作区未提交 | 本任务新增/改动 40+ 文件（`tools/ke-core/ke_ext_*.py`、`srv/ext_api.py`、`ext-panel.js`、`docs/ext-binding-design.md`、`tests/test_ext_tools.py`、`deploy/mock-ext/`、`tools/it-platform-mcp/` …） | 建议按主题分批提交（`it-platform-mcp` / ext 绑定与面板 / 巡检 G4·G5 / 文档） |

## 9 评审上下文（`技术方案评审流程`）与附件下载（2026-10-26）

### 9.1 为什么加它

内网口径：**智能体只提交「评审编号 + 文件名称」**，流程编号/阶段/状态/**轮次**/责任人/评审文档都在
IT 工作平台；我们此前没有对应接口 ⇒ 智能体只能"让用户上传文档"，与内网不符。

### 9.2 新增对象与工具

| 项 | 内容 |
|---|---|
| DataObject | `bpmn/dataobject/技术方案评审流程`（领域库）；绑定 `ext_review_flow` / `query,maintain` / `review_no` / `it_platform:tools/ke-core/ke_ext_flow.py` |
| 工具 | `ext_review_flow`：`get`（状态/轮次/责任人）、`docs`（文档清单 + **url**）、`rounds`、`open`（**建档** + 按文件名绑文档）、`maintain`（`upsert_round`/`close`，默认 `dry_run=true`） |
| 契约 | `deploy/mock-ext/it-platform/schema.json` 的 `x_ext.ext_review_flow`（8 列 + `flow_status_enum` / `round_status_enum`） |

### 9.3 编号 / 轮次口径（模拟侧）

- 智能体给的 `review_no` **一律视为平台已存在**：无档案 ⇒ `get/open` 时**直接建档**
  （`status=评审中`、`round=1`、`authority=agent_requested`）；
- 智能体指定轮次 ⇒ 以它为准（`round_source=agent`）；档案更大 ⇒ `warn=round_behind` 且**以档案为准**（不静默）；
- 真内网把 `authority` 置 `internal` 则一律以平台为准（工具面/入参不变）。

### 9.4 附件下载（不给"上传"，给"URL"）

- 案例文档已复制进 `deploy/mock-ext/it-platform/files/`（样例1 pdf/docx、样例2、样例3、图片1/2）；
- `docs/doc-sample-*.json` 由 docx **标准库**（zipfile + XML）按标题切节生成；
- `GET /files/<doc_id>[?name=]` 流式返回附件（`Content-Type` + `Content-Disposition`）；
  `ext_doc_access(action=url|meta|attachment)` 回可下载地址；`ext_review_flow(docs)` 也直接给 `url`；
- 基地址 `IT_MCP_PUBLIC_BASE`（默认 `http://127.0.0.1:8770`），内网换域名/IP 即可（**不假设代理**）。

### 9.5 参考规范（直连，不新增工具）

- 规则页 `bmm:ruleReference` 若是 URL：用现成 `reference_lookup(allow_fetch=true)` **直连**抓取；
  内网 DNS 不通就配 hosts/IP（`deploy/wsl-up.sh` 已幂等维护 `BODHI_REF_HOST[_IP]`），
  白名单 `BODHI_REF_HOSTS`；
- 模板「规则清单口径」要求把结果写成 `fetched` / `unavailable:<reason>` + 一句摘要（可回指 ✓）；
- 失败仍结构化（`timeout｜dns｜tls｜blocked｜4xx｜empty`），报告照原因写"不可得（待澄清）"。
- **抓取留痕**（`<root>/ref_cache.json`）：`reference_lookup` 每抓一次就落一条（同 URL 覆盖，
  只留最近 300 条）；巡检 **G7（low）** 读这份留痕 —— 规则页写了 URL 但从没抓过 ⇒ 提示抓一次。
  这样既不联网做巡检（避免假告警/慢），又能逼出"跳过抓取直接写不可得"的情况 ✓。

### 9.6 文档上传与新建流程（2026-10-26）

内网工作台没接通时，文档可直接交给模拟服务（**不需要**先进知识库）：

| 动作 | 调用 | 说明 |
|---|---|---|
| 新建流程 | `ext_review_flow(action="create", review_no=…, title=…)` | 状态取 schema `defaults`，轮次 1 |
| 上传文档 | `POST /files/<doc_id>?review_no=&name=&title=&round=` | body=文件二进制；docx 用标准库**切节** |
| 绑定文档 | `ext_review_flow(action="attach", review_no=…, doc_id=…)` | 档案的 `docs` 指向该文档 |
| 一条命令 | `bash deploy/mock-ext/upload_doc.sh <编号> <文件> [标题]` | 上传 + attach |

### 9.7 台账指纹与 rev（2026-10-26）

- `rule_fp` = **规则页 `bmm:ruleHash`**（16 hex），调用方**显式**提交；工具只在缺省时自算 `r-xxxxxx`；
- 台账写入用 **`items=[…]` 批量**（一次调用 ⇒ `rev` 只 +1）；`rev` = 该编号的**写入版本**，不是评审轮次；
- 报告 ③ 的指纹列必须 ⊆ ② 的指纹集合（表后自查），历史 `r-` 值按 `TROUBLESHOOTING` 的批量法重写。
