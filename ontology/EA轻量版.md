# EA 企业架构本体 - 轻量级摘要（用于 LLM 文档提取）
版本: 1.0 | 命名空间: <http://example.org/ea#> | 前缀: ea:

> 本模块依赖 BMM：`bmm:*` 术语（预期成果/手段/资源/规则等）不在本文件重复定义，
> 抽取时必须回到 BMM 轻量版取类名与关系名。

## 一、核心类（按语义分组）

### 业务编排层（流程 → 活动 → 任务 → 步骤）
- Activity（活动）：对任务的编排，完成业务上特定的目标
- Task（任务）：某个岗位或角色连续执行的操作
- Step（步骤）：对单个或一组紧耦合业务实体的操作

### 角色与业务实体
- BusinessRole（业务角色）：业务角色或岗位
- BusinessEntity（业务实体）：如客户、合同、账户、申请单等
> 2026-09-29：本模块**不再有** `Customer` 子类（测试残留已删）——业务实体统统抽 `BusinessEntity`。

### IT 资产层（2026-09-29 **已迁至 BMM**）
> `ITAsset` / `MainSystem`（主系统）/ `SubSystem`（子系统，原本模块的 `Application`）/
> `HardwareAsset`（硬件资产）现定义在 **BMM**：`bmm:ITAsset` 及其子类。
> 本模块只保留「步骤由 IT 资产支撑」这条连接关系（range 指向 `bmm:ITAsset`）。
> 见 `BMM轻量版.md`（含 系统编号/英文简称/重要性等级/状态/核心功能/硬件资产分类 等数据属性）。

### 派生筛选类
> 原 `CustomerWithExpiringProduct` / `CustomerWithoutExpiringProduct` **已随客户类一并删除**（2026-09-29）。

### 从 BMM 借用的跨层术语（本模块关系会用到，定义见 BMM 轻量版）
- bmm:BusinessProcess（业务流程）、bmm:CourseOfAction（行动方案）、bmm:DesiredResult（预期成果）
- bmm:Offering（对外交付物）→ bmm:Product（产品）、bmm:ExpiringProduct（即将到期的产品）
- bmm:BusinessRule（业务规则）、bmm:Resource（资源）、bmm:ITAsset（IT 资产：主系统/子系统/硬件资产）

## 二、枚举类（固定取值）

本模块**没有枚举类**。需要枚举时（如执行级别、评估类型）用 BMM 的
`bmm:EnforcementLevel` / `bmm:AssessmentType`。

## 三、关键对象属性（按功能分组）

### 流程编排（自顶向下包含）
- activityHasTask（活动 → 任务）：活动包含任务
- taskHasStep（任务 → 步骤）：任务包含步骤
- businessProcessHasActivity（bmm:业务流程 → 活动）：业务流程包含活动

### 执行与操作
- taskPerformedByRole（任务 → 业务角色）：任务由业务角色执行
- stepOperatesOnEntity（步骤 → 业务实体）：步骤操作业务实体
- stepSupportedByAsset（步骤 → bmm:IT资产）：步骤由 IT 资产支撑（IT 资产类在 BMM）

### 实现与达成（跨层到 BMM）
- activityAchievesDesiredResult（活动 → bmm:预期成果）：活动实现某个预期成果
- activityRealizesActionPlan（活动 → bmm:行动方案）：活动实现某个行动方案
- businessProcessRealizesActionPlan（bmm:业务流程 → bmm:行动方案）：业务流程实现某个行动方案

### 交付
- activityDelivers（活动 → bmm:对外交付物）：活动交付某个产品或业务服务
- taskDelivers（任务 → bmm:对外交付物）：任务交付某个产品或业务服务

### 治理
- taskGovernedByRule（任务 → bmm:业务规则）：任务受业务规则约束

### 客户与产品
> 2026-09-29：客户类与 `holdsProduct` 已删除（客户不再作为 EA 抽取目标）。若文档表达"客户持有产品"，
> 请在 BMM 侧用 `bmm:Product` 建模。

## 四、数据属性（常用）

本模块**暂无数据属性**：原先唯一的 `ai_skill`（IT 服务技能定义）随 IT 服务层一起暂缓下线。
需要名称/定义等通用字段时，用 BMM 的 `name` / `englishName` / `definition` / `description`。

## 五、提取模式速查表

### 识别关键词 → 映射类型
| 关键词 | → 类型 |
|--------|--------|
| 业务流程、主流程、端到端流程 | bmm:BusinessProcess |
| 活动、环节、编排 | Activity |
| 任务、作业、日常操作、岗位职责 | Task |
| 步骤、操作步骤、动作 | Step |
| 岗位、角色、经办人、责任人 | BusinessRole |
| 客户、合同、账户、申请单、工单 | BusinessEntity |
| 系统、平台、应用、子系统、软件 | **bmm:SubSystem**（子系统；EA 已迁出该类） |
| 主系统、核心系统 | **bmm:MainSystem** |
| 服务器、网络设备、终端、硬件 | **bmm:HardwareAsset** |
| IT 资产、信息化资产（不区分软硬件） | **bmm:ITAsset** |
| 包含子系统、下属子系统 | **bmm:mainSystemContainsSubSystem**（主系统 → 子系统） |
| 归属于子系统、部署在子系统 | **bmm:hardwareAssetBelongsToSubSystem**（硬件资产 → 子系统） |
| 支撑、依托于…系统、在…系统里操作 | stepSupportedByAsset |
| 由…执行、由…负责、岗位是… | taskPerformedByRole |
| 处理、录入、修改、查询（对业务对象的动作） | stepOperatesOnEntity |
| 必须遵守、受…约束、按…规则 | taskGovernedByRule |
| 交付、输出、产出（活动/任务的产物） | activityDelivers / taskDelivers |
| 达成、实现、服务于（目标/方案） | activityAchievesDesiredResult / activityRealizesActionPlan |

### 抽取粒度提示
- 一份流程文档通常应抽出：1 个 `bmm:BusinessProcess` + N 个 `Activity` + 每个活动下的 `Task`/`Step`。
- 只在文档明确写出时抽 `bmm:ITAsset`/`bmm:MainSystem`/`bmm:SubSystem`/`bmm:HardwareAsset`，不要凭常识补系统名。

## 六、基数约束简记（本模块 TBox 公理）

| 主体 | 约束 | 说明 |
| --- | --- | --- |
| bmm:BusinessProcess | `businessProcessHasActivity` some `Activity` | 业务流程至少包含一个活动 |
| bmm:BusinessProcess | `businessProcessRealizesActionPlan` some `bmm:CourseOfAction` | 业务流程必须实现至少一个行动方案 |
| Activity | `activityHasTask` some `Task` | 活动至少包含一个任务 |
| Activity | `activityDelivers` some `bmm:Offering` | 活动必须交付至少一个产品或业务服务 |
| Activity | `activityAchievesDesiredResult` some `bmm:DesiredResult` | 活动必须实现至少一个预期成果 |
| Activity | `activityRealizesActionPlan` some `bmm:CourseOfAction` | 活动必须实现至少一个行动方案 |
| Task | `taskHasStep` some `Step` | 任务至少包含一个步骤 |
| Task | `taskPerformedByRole` some `BusinessRole` | 任务必须由至少一个角色执行 |
| Task | `taskDelivers` some `bmm:Offering` | 任务必须交付至少一个产品或业务服务 |
| Task | `taskGovernedByRule` some `bmm:BusinessRule` | 任务必须至少受一条业务规则约束 |
| Step | `stepOperatesOnEntity` some `BusinessEntity` | 步骤必须至少操作一个业务实体 |
| Step | `stepSupportedByAsset` some `bmm:ITAsset` | 步骤必须至少由一个 IT 资产支撑（IT 资产类在 BMM） |

## 七、跨层协作提示（与 BMM 一起用时）

- 本模块 13 条对象属性里 **8 条是跨模块桥**（domain 与 range 分属 EA / BMM），
  这是"流程 → 目标/方案 → 规则"的推理接缝：`ea:Activity` 到 `bmm:DesiredResult`
  **只能**通过 `ea:activityAchievesDesiredResult` 连接，不许自造关系。
- IT 服务层（服务契约 / 业务属性 / 函数依赖）在扩展模块 `ea-service` 中定义，
  本轻量版**不含**这些术语；文档涉及服务契约时需同时启用该模块。
