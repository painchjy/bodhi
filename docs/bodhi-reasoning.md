# Bodhi2 知识推理规格（规则源 / 判定 / 幂等落库）

> 2026-09-19 决策（方案 A）：**上游 WeKnora 继续用公共镜像干重活**（文件切片 / 向量存储 /
> 图谱存储 / 会话），**自研能力一律只写 Python**，不再构建上游镜像、不再抓 Go 代码。
> 本文是自研「推理插件」的规格：规则从哪来、怎么判定、结论怎么幂等落库。
>
> 目标（摘自 `bodhi2项目需求.md` §应用案例验证）：
> 1. **利用函数依赖实现服务设计的高内聚原则和幂等设计的规则判断**（本体 `ea-service` 模块）；
> 2. **企业控制关系追踪 / 反洗钱穿透推断**（本体 `ea-ownership` 模块，需要进入第三层执行层）。
> 推理引擎（agent scope + deepseek flash + 多轮交互）只负责「选知识范围、选规则、编排调用」，
> 真正的规则判定必须由确定性代码完成——否则幂等与可追溯无从谈起。

## 1. 先确定「规则从哪来」：本体即规则，不另造

关键事实（已核对仓库）：**内聚、幂等这些规则早就写在你自己的本体里了**，而且有两种形态：

| 规则源 | 位置 | 形态 | 说明 |
|---|---|---|---|
| 结构规则（生成） | `artifacts/shacl/generated.shapes.ttl`（68,625B） | SHACL | 编译器从 TBox 生成：基数、值域、枚举。共 **47 个 shape**（bmm 26 / ea 11 / ea-service 4 / ea-ownership 4 / bmm-fd 2） |
| 设计规则（人工） | `ontology/shapes/service-design.shapes.ttl` | SHACL + SPARQL 约束 | S1–S5：幂等键、副作用、服务边界（高内聚）、幂等键↔函数依赖一致性、规则属性覆盖度 |
| 设计规则（人工） | `ontology/shapes/ownership.shapes.ttl` | SHACL + SPARQL 约束 | O1–O5：持股区间、多数持股推定控制（**可由规则推导补全**）、最终控制人结论、时间口径、自持股禁止 |
| 机器可读索引 | `artifacts/shacl/index.json` | JSON | `load_order` + `severity_contract` + 每模块 shape 数与目标类清单 |
| 编译期发射器 | `tools/ontology-compiler/ontology_compiler/emitters/shacl.py` | Python | 生成上表第一条的发射器（改规则源头要动这里） |

**严重级别契约（`artifacts/shacl/index.json` 原文，必须遵守）**：

```json
"sh:Violation": "违反本体 -> 落库判 pending_human",
"sh:Warning":  "仍判 validated -> 打标进入人工抽检"
```

> 这意味着推理结果**不另起一套状态**，而是复用已有的 `pending_human / validated` 语义，
> 前端 wiki 列表与「待确认合并」tab 立刻能看见（见 `docs/weknora-fork.md` §11）。

## 2. 规则清单（从 SHACL 逐条抽出，S/O 编号即稳定规则 ID）

### 2.1 `ea-service`：幂等与高内聚（需求案例 1）

| ID | 规则 | 判定（数据条件） | 级别 | 出处原文 |
|---|---|---|---|---|
| `S1` | 写操作契约必须有幂等键 | `isReadOnly=false` ∧ 无 `idempotencyKey` | Violation | 「写操作契约（isReadOnly=false）必须声明 idempotencyKey，否则重试会产生重复业务动作」 |
| `S2` | 只读契约不得声明副作用 | `isReadOnly=true` ∧ 存在 `contractProducesEffect` | Violation | 「只读契约（isReadOnly=true）不得声明 contractProducesEffect 副作用」 |
| `S3` | **高内聚（服务边界）** | `contractOperatesOn` 取值数 `> 1` | Violation | 「一个服务契约只应操作一个业务实体（聚合根）；跨多个实体说明服务边界过宽，应拆分或改为编排」 |
| `S4` | **幂等键必须有函数依赖支撑** | 有 `idempotencyKey` ∧ 无任何 `contractDependsOn` | Violation | 「声明了 idempotencyKey，但未通过 contractDependsOn 关联任何函数依赖，无法证明幂等键的决定性」 |
| `S5` | 契约必须覆盖其遵循规则所约束的属性 | 规则约束的业务属性 ∉（`contractHasInput` ∪ `contractHasOutput`） | Violation | 「契约遵循的业务规则所约束的业务属性，必须出现在契约的输入或输出属性中」 |
| `S6` | 每个契约必须且只能声明一次 `isReadOnly` | 计数 ≠ 1 | Violation | 生成 shape：「每个服务契约必须恰好声明一次 isReadOnly」 |
| `S7` | 契约必须作用于至少一个业务实体 | 计数 = 0 | Violation | 生成 shape |
| `S8` | 函数依赖必须有决定方与被决定方 | `hasDeterminant` = 0 ∨ `hasDependent` = 0 | Violation | 生成 shape |
| `S9` | 业务属性必须归属于某个业务实体 | 无反向归属 | Violation | 生成 shape |

**由 S1–S4 联合推出的两条「设计判据」**（这才是「利用函数依赖实现内聚与幂等判断」的落点）：

- **幂等可证明性**：`有 idempotencyKey` ∧ `∃ contractDependsOn → FunctionalDependency` ∧
  `idempotencyKey` 取值属性 ⊆ `hasDeterminant` 属性集合 ⇒ **幂等可证明**；否则判「不可证明」（S4 加强版，
  依据本体注释「幂等键应取自 determinant 属性集合」）。
- **高内聚评分**：`|contractOperatesOn| = 1` ⇒ 高内聚；`> 1` ⇒ 服务边界告警（S3），并给拆分建议：
  按被操作实体把契约拆成「主体契约 + 编排」。

### 2.2 `ea-ownership`：控制 / 穿透（需求案例 2）

| ID | 规则 | 判定 | 级别 | 备注 |
|---|---|---|---|---|
| `O1` | 持股比例区间 | `ownershipPercent ∉ [0,100]` | Violation | 百分数口径 |
| `O2` | 多数持股推定控制 | `ownershipPercent ≥ 50` ∧ 无 `controls` 控制关系 | Violation | **可由规则推导后补全** ← 「推导」而非仅校验 |
| `O3` | 最终控制人结论 | 法人主体无穿透结论 | **Warning** | 进人工抽检队列 |
| `O4` | 时间口径 | 股权关系无 `asOfDate` | **Warning** | 穿透结论必须能对齐时间口径 |
| `O5` | 不得自持股 | 持股方 = 客体 | Violation | — |


## 3. 两种工作模式：`validate` 与 `derive`（同一套规则）

| 模式 | 做什么 | 输出 | 幂等要求 |
|---|---|---|---|
| `validate` | 按规则判「结构对不对 / 设计好不好」 | findings：`{rule_id, severity, subject, message, evidence[]}` | 纯函数：同输入同输出，不写库 |
| `derive` | 由规则**推导补全**事实（O2 的控制关系、O3 的最终控制人、`inverseOf` 反向边、子类传递闭包） | 推导事实 `{pred, subject, object, rule_id, basis[]}` | **写入按 (rule_id, subject, pred, object) 去重**；重跑不新增、只更新 `version` |

推导必须带 **依据链（basis）**：每条推导事实都要能指回「哪条规则 + 哪些实例页面 + 本体哪句声明」，
否则穿透结论无法审计（延伸自已踩的坑：写库前一律先做类型守卫与断言，
如 `wiki_pages.out_links` 是标量那次必须 `jsonb_typeof` 守卫）。

## 4. 幂等落库设计（关键：同输入 ⇒ 同一页面）

- **确定性 slug**：`reason/<rule-id>/<subject-slug>`（例如 `reason/o2/某控股方`）。
  重跑同一批数据 ⇒ 命中同一 slug ⇒ 走「更新 + 版本快照」而非新建页面。
  与 `tools/ontology-mcp/server.py` 的合并语义一致（`version+1` + `wiki_page_revisions` 快照，可回退）。
- **`page_type`** 用 `reason:Finding`（写入侧不校验 `page_type`，实测见 `docs/weknora-fork.md` §10.11），
  与 `ontology:Class` / `ontology:Relation` 并列，前端「本体」tab 会按 `category_path` 自动归类。
- **`category_path`**：`[规则模块, 规则编号+名称, 级别]`，例如
  `["服务设计规则", "S3 服务边界（高内聚）", "Violation"]` → 前端三级折叠天然可读。
- **`last_edit_source`**：`bodhi-reason`（注意 `varchar(16)` 上限——这个坑已经踩过一次）。
- **`page_metadata.reason`**：`{rule_id, severity, mode, basis[], generated_at, generator}` 便于机器回溯。

## 5. 接口（自研、Python、零第三方依赖）

落地位置：`tools/ke-core/`（与 `tools/ontology-*` 同级）。命名沿用既有文档：
`deploy/README.md:160`「实例层的关联推理在 Python（ke-core）侧做」、SHACL 注释「由 ke-core 在 Python 侧统一校验」。

> **有意的偏离**：SHACL 注释里写的是 pyshacl，但方案 A 要求最少依赖。人工规则只有 S1–S5 / O1–O5
> 共 10 条且形态固定，**手工编译成 JSON 谓词**比引入 pyshacl 更轻、更可审计；生成的 47 条结构规则本来
> 就是基数/值域判断，同样可编译成 JSON。原始 SPARQL 文本继续作为权威文档留在 `.ttl` 里，由编译器在
> 第 1 步做「JSON 谓词 ↔ SPARQL 原文」一致性自检，避免两份真相漂移。

| 入口 | 形态 | 用途 |
|---|---|---|
| `reason.py validate --kb <id> [--model easvc]` | CLI（stdlib argparse） | 出 findings，**不写库**；本地/CI 用 |
| `reason.py derive --kb <id> --apply` | CLI | 推导并**幂等**写结论页；加 `--dry-run` 只打印将写什么 |
| `reason_validate` / `reason_derive` | MCP 工具（并入 `tools/ontology-mcp/server.py`，与 `extract_and_save` 并列） | 供 agent（agent scope / deepseek flash，**多轮交互**）选范围、选规则后触发 |

取数复用现有通道（`docker exec psql` 读 `wiki_pages`，见 `tools/ontology-mcp/server.py`），不引入 ORM；
规则读 `artifacts/shacl/index.json` + 编译产物 `artifacts/rules/rules.json`（JSON，stdlib 可解析）。

## 6. 实施步骤（每步可单独验收）

1. **规则编译**：`tools/ontology-compiler` 加 `--emit-rules`，把 SHACL（含人工 SPARQL）编译为
   `artifacts/rules/rules.json`，并做「JSON 谓词 ↔ SPARQL 原文」一致性自检。
2. **判定内核**：`tools/ke-core/reason.py` 实现 §2 判定（纯函数、无 IO）；先在**当前 53 页实例**上跑通——
   现有数据来自 bmm/ea 模型抽取，`easvc:` 实例可能为 0，此时必须输出「无适用实例」而非空报告（可验证）。
3. **推导器**：O2（≥50% ⇒ 控制关系）、O3（传递闭包 ⇒ 最终控制人）、`inverseOf` 反向边、子类传递闭包。
4. **幂等落库** + 结论页模板（三级 `category_path`、`page_metadata.reason`）。
5. **MCP 接线 + 前端验收**：「本体」tab 下出现「服务设计规则」「控制穿透规则」两个大类，下挂 finding 页。
6. **真实演练**：同一要素出现在第二篇文档 → 走合并/待确认路径；再跑一次 `derive` 验证重跑不新增页。

## 7. 验收口径（先写死，避免自我感觉良好）

| # | 验收项 | 通过标准 |
|---|---|---|
| A1 | 幂等可证明性 | 找到/构造 1 个契约：有幂等键但无函数依赖 → S4 命中；补上 `contractDependsOn` 后同输入重跑 → 不再命中 |
| A2 | 高内聚判定 | 1 个契约操作 2 个实体 → S3 命中并给出拆分建议 |
| A3 | 穿透推导 | ≥50% 持股 → 推导出 `controls`；两跳穿透 → 得出最终控制人结论（Warning 页） |
| A4 | 幂等性 | 同一 KB 连续跑两次 `derive --apply`：页面数不增、`version` 只增 1、结论逐字相同 |
| A5 | 可追溯 | 每个 finding / 推导事实的 `basis` 能指回具体 wiki 页面与规则 ID |
| A6 | 零依赖 | 仅用 Python 标准库；在无第三方包的 WSL 里 `python3 -c "import reason"` 可用（与 `bodhi-mcp` 同标准） |

## 8. 与三层知识库架构的对应（`bodhi2项目需求.md`）

| 层 | 承载 | 本规格的角色 |
|---|---|---|
| L1 本体语义网络 | `ontology/*.ttl`（BMM / EA / 扩展模块） | **规则源**：TBox 声明与 SHACL 直接决定判定口径，推理不改本体 |
| L2 知识网络（受本体约束） | 「企业知识」KB 的 wiki 页（53 页；easvc 实例待抽取） | **判定对象**：页面 `page_metadata.ontology` + 关系页构成实例图 |
| L3 执行层（API/MCP 网关） | 第三层企业应用接口、DB 元数据 / SQL 查询 | 穿透类推理（O2–O4）需进入此层取实际数据后回填 L2 结论 |
