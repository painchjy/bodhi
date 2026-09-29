---
id: structured_modeling
name: 结构化数据批量建模（Excel / CSV → 本体类与关系）
description: 读用户对表格结构的中文描述，把列对应到本体的类/数据属性、把外键列对应到关系，然后一次一个类或一条关系地批量建页
when: 用户给出「结构化文件（Excel/CSV）」并说明「这些列是什么意思」「要建到哪个知识库」时
models: [bmm, ea]
stages: [probe, plan, apply, verify]
scope:
  classes: [bmm:MainSystem, bmm:SubSystem, bmm:HardwareAsset, bmm:ITAsset, bmm:OrganizationUnit,
            bmm:BusinessPolicy, bmm:BusinessRule, bmm:OperativeBusinessRule, bmm:StructuralBusinessRule]
  relations: [bmm:mainSystemContainsSubSystem, bmm:mainSystemBelongsToOrganizationUnit,
              bmm:subSystemBelongsToOrganizationUnit, bmm:hardwareAssetBelongsToSubSystem,
              bmm:isDerivedFrom, bmm:isBasisFor, bmm:guides, bmm:hasEnforcementLevel]
tools: [import_probe, import_plan, import_apply, import_state, ontology_types, audit_scan]
version: 1
---

# 结构化数据批量建模（技能）

## 0. 分工（**第一原则**）

- **你（智能体）负责"理解"**：从**用户对表的中文描述** + 表头/抽样，判断这份表里有哪些**类**、哪些**关系**、
  哪列是键、哪列对应哪个**数据属性**、哪列是**外键**（指向另一个类）。
- **工具负责"执行"**：你把这些结论作为**参数**传给工具；工具只做**确定性的事**——读表、校验（类/属性是否已声明、
  键是否唯一、外键能否命中已有页）、按模板建页、写关系、记账。**工具不做语义推断**。
- 所以：换一份表不用换工具，**只换参数**。你不确定某类/某属性是否存在时，先 `ontology_types` 查（或看本技能 front-matter 的 scope）。

## 1. 铁律（用户口径）

1. **一次只建一个类（含它的数据属性）或一条关系** —— 工具会拒绝别的形态（`need_single_target`）。
   一份表通常要**跑很多遍**（每个类一遍、每条关系一遍），每遍都能单独重跑。
2. **两段式**：`import_plan`（只读，出 `ticket` + 影响面 + `questions`）→ 给用户看 → 用户同意 → `import_apply(ticket)`。
3. **不逐行确认**：一遍里成百上千行都由工具批量写（默认 500 行/事务）。不要一行一行问。
4. **未映射的列不要硬塞**：默认聚合进 `description`（`【列名】值`）；如果该实体与那些列无关
   （例如"业务部门"只有名称），用 `unknown_to_description=false` 关掉聚合。
5. **关系的两侧必须先存在**：先跑完所有**类**批次，再跑**关系**批次；关系批次会报 `dangling`（找不到的键）。

## 2. 三步走

### 第 1 步：探表（只读）
```
import_probe(file="…xlsx")            # 或 sheet="主系统"
```
拿着回执里的 `header`（列名）、`rows`、`sample`（抽样 3 行）、`duplicate_headers`、`prefixes`（列前缀）、
`unique_columns`（疑似主键）**和用户对齐一次**：

- **一张表可能包含多个实体**：看**列前缀**（如 `主系统系统编号` / `子系统系统编号` → 两个实体：
  主系统、子系统）；也可能是行内不同列段（`业务策略 | 级别 | 业务规则名称 …`）。
- 对每个实体问自己三件事：**哪个类**（`bmm:MainSystem`…）、**哪个键列**（进 slug）、**哪些列→哪些数据属性**。
- 外键列 → 关系（如 `主系统系统编号`+`子系统系统编号` → `bmm:mainSystemContainsSubSystem`）。

### 第 2 步：逐目标 plan（只读）
```
import_plan(kind="class", target="bmm:MainSystem", file="…xlsx", kb_id="系统与规则台账",
            key_column="主系统系统编号",
            mapping={"主系统英文简称":"systemAbbr","主系统英文名称":"englishName",
                     "主系统功能简介":"description","重要性等级":"systemCriticality",
                     "主系统状态":"systemStatus"},
            title="{主系统英文名称}（{主系统系统编号}）", aliases=["主系统英文简称"])
# 关系批次：
import_plan(kind="relation", target="bmm:mainSystemContainsSubSystem", file="…xlsx", kb_id="…",
            key_column="主系统系统编号",
            source_key_column="主系统系统编号", target_key_column="子系统系统编号",
            source_class="bmm:MainSystem", target_class="bmm:SubSystem")
```
把回执里的 **`counts`（entities/create/update/dangling）+ `samples`（即将生成的页样例）+ `questions`**
**用一两句话**念给用户确认（一次问完，别来回）。

### 第 3 步：apply + 记账
```
import_apply(ticket="<上一步的 ticket>")     # 只写这一个目标
import_state(batch="<batch>")                # remaining 为空 = 这张表建完了
```
- 建完所有目标后跑 `audit_scan(kb_id=…, scope="all")`：**C1（无来源）/F3（无原文依据）/B1（类型不在本体）必须全绿**，
  有 findings 就照它的 fix 处理。

## 3. 每个类/关系的参数怎么给（速查）

| 情况 | 怎么给参数 |
|---|---|
| 单实体宽表 | `key_column` = 唯一键列；`mapping` = 属性列 → 数据属性 |
| 一张表**多个实体**（前缀分组） | 每个实体**单独一遍**：前缀列映射到该类的属性，`key_column` 用该实体的编号列 |
| 只有名称的实体（部门/组织机构） | `mapping={"…业务部门":"name"}` + `unknown_to_description=false` |
| 关系（主子、归属） | `source_key_column`/`target_key_column` + `source_class`/`target_class`；**两侧键列都必须是该类建页时用的 slug 键列**（规则页键列是 `规则编号`——拿 `业务规则名称` 当 `source_key_column` 会报 `dangling`，回执会回显拼出的 slug） |
| 枚举型列（强制/推荐） | **用 `enums` 建成关系**（不是数据属性）：`enums={"级别":{"relation":"bmm:hasEnforcementLevel","values":{"强制":"bmm:Strict","推荐":"bmm:Advisory","可覆盖":"bmm:Override"}}}` → 页面写 `- 具有执行级别（`bmm:hasEnforcementLevel`）→ bmm:Advisory（推荐）`（**无链接**：目标是枚举值不是页），元数据落 `ontology.enum_relations` |
| 列里是 URL/规范名 | 映射到 `bmm:ruleReference` 之类；**它的内容不进正文**（正文原文依据用**该行原始值**） |
| 列名对不上任何已声明属性 | 别硬塞 → 让工具报 `attribute_not_declared`（它会给出可用清单），或先跟用户确认是否改本体 |

## 4. 边界与自纠

- **重复键**（同一实体多行，如一个主系统带多个子系统）：工具**自动去重成一条**（`duplicate_keys` 会报数）；
  这是正常现象，不用报错。
- **空键行**：进 `empty_keys` 计数并跳过（例如"主系统无子系统"的空行）。
- **`dangling`（关系批次）**：说明两侧有页还没建 → 先把对应**类**批次跑完，再回来跑关系；
  也可能是**键列选错**（关系批次把 `source_key_column` 的值直接拼源页 slug）——回执里 `source_slug`/`target_slug`
  会给出拼出来的 slug，对照页 slug 一眼能看出。
- **`## 本体关系` 归谁维护**：**关系批次**（和手工编辑）。类批次只维护「定义/属性/原文依据」，
  重跑时会**原样保留**已有关系小节（只把本批新增的枚举关系行并进去）→ 不用担心类批次擦掉关系线。
- **`attribute_not_declared` / 类不存在**：改 `mapping`/`target`，或跟用户确认是否要给本体加类/属性（那是 TTL 变更）。
- **`need_write_permission`**：目标库的写权限（属主 / `kb_shares` 里 editor|writer|admin）；让用户换库或授权。
