# 批量建模测试案例（`docs/cases/`）

> 用途：验证「结构化数据批量建模」技能与工具（设计见 `../structured-modeling-skill.md`）。
> 两个文件都是**真实的表形态**（横表、重复表头、一行多实体、空值），正好当回归样本。
>
> **分工（用户 2026-09-29 强调）**：**技能/智能体负责"理解"** —— 从**用户的自然语言描述** + 表头/抽样，
> 得出「这份表里有哪些**类**、哪些**关系**、哪列是键、哪列映射到哪个**数据属性**」；
> **工具只负责"确定性执行与校验"** —— 智能体把这些结论作为**工具参数**传入，工具据此读表、校验、落库，
> **不做语义推断**。所以同一个工具能服务任意表：换表只改参数（技能教智能体怎么给参数）。

| 文件 | 内容 | 状态 |
|---|---|---|
| `主子系统批量建模案例.xlsx` | 原表：**只有表头**（17 列），需造数据 | 已生成样例：`主子系统批量建模案例-样例数据.xlsx`（17 行：8 主系统 / 16 子系统 / 5 部门，含空子系统行、重复表头列、部门不一致等边界）|
| `业务规则批量建模案例.xlsx` | 原表：7 列 + **2 行真实样例** | 直接用 |

零依赖读表：`python3 tools/ke-core/ke_sheet.py probe <file>`（本机无 openpyxl/pandas，xlsx 走标准库 `zipfile+xml`）。

---

## 案例 1：主子系统清单（**一张横表 → 3 个类 + 3 条关系**）

表头（17 列）：`主系统系统编号 | 主系统英文简称 | 主系统英文名称 | 主系统功能简介 | 重要性等级 | 主系统业务部门 |
子系统系统编号 | 子系统英文简称 | 子系统英文名称 | 子系统功能简介 | 子系统重要性等级 | 子系统业务部门 |
核心功能 | 子系统业务部门 ⚠️重复 | 调整内容 | 业务域 | 应用域`

**列前缀分组**（工具按"前缀 → 实体类"切，`probe.prefixes` 会自动报出 `["主系统","子系统"]`）：

| 实体 | 类 | 键列（slug 用） | 数据属性映射 |
|---|---|---|---|
| 主系统 | `bmm:MainSystem` | `主系统系统编号` | `systemNo`←系统编号、`systemAbbr`←英文简称、`englishName`←英文名称、`systemCriticality`←重要性等级、`description`←功能简介 |
| 子系统 | `bmm:SubSystem` | `子系统系统编号` | 同上 5 个（用「子系统」前缀列）+ `coreFunction`←核心功能 |
| 业务部门 | `bmm:OrganizationUnit` | 部门名（**建议补编号列**）| `name`←部门名（`主系统业务部门` / `子系统业务部门` 两列的取值都建实例，同名去重）|

**关系（3 条）**：

| 关系 | domain → range | 本体状态 |
|---|---|---|
| 主系统包含子系统 | `bmm:MainSystem` → `bmm:SubSystem` | ✅ 已有 `bmm:mainSystemContainsSubSystem`（2026-09-29 加）|
| 主系统归属业务部门 | `bmm:MainSystem` → `bmm:OrganizationUnit` | ❌ **缺** → 建议 `bmm:mainSystemBelongsToOrganizationUnit` |
| 子系统归属业务部门 | `bmm:SubSystem` → `bmm:OrganizationUnit` | ❌ **缺** → 建议 `bmm:subSystemBelongsToOrganizationUnit` |

> 也可用**一条** `bmm:systemBelongsToOrganizationUnit`（domain `bmm:ITAsset`）覆盖主/子两种——
> 但用户口径是"三种关系"，故默认按两条实现（见待拍板项）。

**不能映射的列 → 按口径聚合进 `description`**：`调整内容`、`业务域`、`应用域`、重复的 `子系统业务部门`（第 2 列，工具报 `duplicate_headers`）。
聚合格式：`<功能简介>\n\n【调整内容】…\n【业务域】…\n【应用域】…`（列名标注，便于回溯）。

**建议补齐的表头**（2026-09-29 用户口径已定）：
1. **不加部门编号** —— 业务部门就用**名称**作为键（工具按名称去重；改名会视为新部门，属可接受）；
2. ~~重复的 `子系统业务部门`~~ → **确认是笔误，已从原表删除**（现为 16 列）；
3. **状态两列已加**：`主系统状态` / `子系统状态` → 映射 `bmm:systemStatus`（样例数据现为 18 列）；
4. `调整内容` / `业务域` / `应用域` → 仍按口径**聚合进 `description`**（要当独立概念时再给编号建模）。

---

## 案例 2：业务规则（**一张表 → 2 个类 + 1 条关系**）

表头：`业务策略 | 级别 | 业务规则名称 | 适用范围 | 业务规则描述 | 实现方式 | 参考规范细则`

| 实体 | 类 | 键列 | 映射 |
|---|---|---|---|
| 业务策略 | `bmm:BusinessPolicy` | `业务策略` | `name`←业务策略 |
| 业务规则 | `bmm:OperativeBusinessRule` | `业务规则名称`（**建议补编号**）| `name`←业务规则名称、`description`←业务规则描述 |

| 列 | 落点 | 本体状态 |
|---|---|---|
| `级别`（强制 / 推荐）| 关系 `bmm:hasEnforcementLevel` → `bmm:Strict`（强制）/ `bmm:Advisory`（推荐）/ `bmm:Override`（可覆盖）| ✅ 枚举与关系都现成 |
| `适用范围` | 建议新数据属性 `bmm:ruleScope` | ❌ 缺（默认先聚合进 `description`）|
| `实现方式` | 建议新数据属性 `bmm:ruleImplementation` | ❌ 缺（同上）|
| `参考规范细则`（URL/md）| 建议新数据属性 `bmm:ruleReference` | ❌ 缺（同上）|
| 规则 → 策略 | 关系 `bmm:isDerivedFrom`（`bmm:BusinessRule` → `bmm:BusinessPolicy`）| ✅ 已有 |

**建议补齐的表头**（2026-09-29 用户口径已定）：
1. **`规则编号` 已加**（`BR-001`/`BR-002`，见原表第 1 列）→ 规则页用编号当键，避免名称重名撞 slug；
2. **不加"原文依据"列** —— **Excel 行本身就是原文**：正文「## 原文依据」直接写该行原始单元格值（逐字），满足巡检 F3；
3. `参考规范细则`（URL）只作**参照**：技能里有一条可选步骤 —— 发现 URL 时**去取该明细规范**（上传/检索到的 md）辅助处理规则；
4. `级别` 取值约定 `强制 / 推荐 / 可覆盖` → `bmm:Strict / bmm:Advisory / bmm:Override`（现成枚举）。
   `适用范围`（`bmm:ruleScope`）、`实现方式`（`bmm:ruleImplementation`）、`参考规范细则`（`bmm:ruleReference`）已进本体（2026-09-29）。

---

## 落地顺序（两个案例共用一套协议）

```
probe  →  一张表=多个实体 → 逐个目标：plan（只读，出 ticket/影响面）→ 用户确认 → apply
案例 1 需 5 遍：bmm:MainSystem → bmm:SubSystem → bmm:OrganizationUnit
                → bmm:mainSystemContainsSubSystem → bmm:mainSystemBelongsToOrganizationUnit
                → bmm:subSystemBelongsToOrganizationUnit（关系 3 遍）
案例 2 需 3 遍：bmm:BusinessPolicy → bmm:OperativeBusinessRule → bmm:isDerivedFrom
每遍都能单独重跑（last_edit_source + 内容哈希），import_state 报 remaining，最后 audit_scan 要 C1/F3/B1 全绿
```
