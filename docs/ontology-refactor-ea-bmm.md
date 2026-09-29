# EA / BMM 本体重构方案（2026-09-29 用户口径）

> **用户口径**：EA 模型**以后只用于流程建模**；去除之前测试的**客户类及子类**并清理相关属性；
> **IT 资产相关内容迁回 BMM**；BMM 里 IT 资产**新增子类「主系统」**；主/子系统增加数据属性；
> 新增「**主系统包含子系统**」关系；新增「**硬件资产归属于子系统**」关系；硬件资产增加数据属性「**硬件资产分类**」。

## 1. 改动清单（精确到 TTL 行）

### 1.1 `ontology/EA完整版.ttl`（瘦身 = 只留流程建模）

| 处理 | 对象 | 位置 | 说明 |
|---|---|---|---|
| **删除** | `:Customer` | 40–43 | 测试残留 |
| **删除** | `:CustomerWithExpiringProduct` | 47–57 | 含 `owl:equivalentClass` 交叉定义 |
| **删除** | `:CustomerWithoutExpiringProduct` | 59–73 | 含 `owl:complementOf` |
| **删除** | `:holdsProduct` | 206–211 | domain 是 `:Customer`，随之失效 |
| **迁移** | `:ITAsset` → `bmm:ITAsset` | 78–81 | 迁 BMM（原注释已写"是 BMM 资源子类"）|
| **迁移** | `:Application` → **`bmm:SubSystem`** | 83–86 | 定义原文"应用（或称为子系统）"→ 更名"子系统" |
| **迁移** | `:HardwareAsset` → `bmm:HardwareAsset` | 88–91 | 迁 BMM |
| **改 range** | `:stepSupportedByAsset` | 156–161 | `range :ITAsset` → `range bmm:ITAsset`（保留在 EA：流程建模要用"步骤由 IT 资产支撑"）|
| **改 domain** | `:applicationProvidesService` | 163–167 | `domain :Application` → `domain bmm:SubSystem`（保留在 EA：EA 拥有 IT 服务层）|
| **删公理** | `:Application 至少提供一个服务` | 294–299 | 强约束对 4k 系统台账不友好（会刷 violation）|
| **改 range** | `:Step 至少由 IT 资产支撑` | 273–278 | `:ITAsset` → `bmm:ITAsset` |
| **改注释** | 头部说明 | 342–343 | 改成"IT 资产类已迁至 BMM（bmm:ITAsset 及子类）" |

**EA 保留**：Activity / Task / Step / BusinessRole / BusinessEntity / Service + APIService / MCPService / SkillService /
流程关系（hasActivity/hasTask/hasStep/performedByRole/operatesOnEntity/governedByRule/delivers/achieves…）、
IT 服务关系（stepUsesService / stepSupportedByAsset / applicationProvidesService）、`ai_skill` 数据属性、其余公理。

### 1.2 `ontology/BMM完整版.ttl`（接收 IT 资产 + 新增系统建模）

**新增类**（放在 §14.2 固定资产之后，作为 `bmm:Resource → bmm:Asset` 家族的一员）：

```
### 14.2.1 IT 资产 ITAsset（自 EA 迁入）
:ITAsset rdf:type owl:Class ; rdfs:subClassOf :Asset ;
    rdfs:label "IT资产"@zh ; rdfs:comment "IT 资产，如硬件、软件系统等"@zh .

### 14.2.2 主系统 MainSystem
:MainSystem rdf:type owl:Class ; rdfs:subClassOf :ITAsset ;
    rdfs:label "主系统"@zh ; rdfs:comment "承载核心业务能力的应用系统（主系统）"@zh .

### 14.2.3 子系统 SubSystem
:SubSystem rdf:type owl:Class ; rdfs:subClassOf :ITAsset ;
    rdfs:label "子系统"@zh ; rdfs:comment "主系统下的子系统/模块（原 EA 应用系统 ea:Application）"@zh .

### 14.2.4 硬件资产 HardwareAsset
:HardwareAsset rdf:type owl:Class ; rdfs:subClassOf :ITAsset ;
    rdfs:label "硬件资产"@zh ; rdfs:comment "硬件资产"@zh .
```

> `:ITAsset` 挂 **`:Asset`**（而不是直接 `:Resource`）——与 `FixedAsset/Offering` 同级；
> 若你更想挂在 `:Resource`，一句话我就改（见 §5 决策点）。

**新增关系**（放在 §三 对象属性区）：

| 关系 | domain → range | 中文名 |
|---|---|---|
| `bmm:mainSystemContainsSubSystem` | `bmm:MainSystem` → `bmm:SubSystem` | 包含子系统 |
| `bmm:hardwareAssetBelongsToSubSystem` | `bmm:HardwareAsset` → `bmm:SubSystem` | 归属于子系统 |

**新增数据属性**（放在 §四 数据属性区；`englishName`/`description` 已存在 → 复用）：

| 属性 | 中文名 | domain | 备注 |
|---|---|---|---|
| `bmm:systemNo` | 系统编号 | `bmm:ITAsset` | Excel「系统编号」；导入时用于 slug |
| `bmm:systemAbbr` | 英文简称 | `bmm:ITAsset` | Excel「英文简称」 |
| `bmm:englishName` | 英文名称 | （已存在，owl:Thing）| **复用**，Excel「英文名称」 |
| `bmm:description` | 描述 | （已存在，owl:Thing）| **复用**，Excel「功能简介」 |
| `bmm:systemCriticality` | 重要性等级 | `bmm:ITAsset` | Excel「重要性等级」 |
| `bmm:systemStatus` | 状态 | `bmm:ITAsset` | Excel「状态」 |
| `bmm:coreFunction` | 核心功能 | `bmm:SubSystem` | 仅子系统 |
| `bmm:hardwareAssetCategory` | 硬件资产分类 | `bmm:HardwareAsset` | 硬件资产新属性 |

**新增公理（可选，建议先不加）**：`bmm:MainSystem ⊑ 至少包含一个子系统`、`bmm:HardwareAsset ⊑ 至少归属一个子系统`
—— 这类"至少一条"约束会让**部分数据违规**（新系统还没填子系统时就报 violation）；导入期建议先不加。

## 2. 编译与投影（本机可闭环，已实测依赖齐）

```bash
# 1) TTL → artifacts（label_map / prompts / neo4j / shacl / json_schema / weknora 抽取配置 / ontology_index）
/opt/bodhi-venv/bin/python3 tools/ontology-compiler/compile.py compile --diff
# 2) 投影本体模型知识库（248 页 → 新页数；删掉的类页消失、新类页出现）
bash deploy/weknora-fork/refresh_ontology_kb.sh
# 3) 前端类型清单（ontologyTypes.ts）→ 重建前端镜像
/opt/bodhi-venv/bin/python3 deploy/weknora-fork/gen_frontend_types.py --fe /root/fe-build
bash deploy/weknora-fork/build_frontend.sh /root/fe-build && bash deploy/weknora-fork/deploy_frontend.sh
```

## 3. 已有数据的迁移（DB）

| 现状 | 处理 |
|---|---|
| `ea:Customer` **3 页** | **软删**（`ke_pages.soft_delete_pages`，可恢复；回执列 slug 供你过目）|
| `ea:Application` **6 页** | **retag → `bmm:SubSystem`**（两段式：`retag_preview` → `retag_apply`，带 ticket/ack；同时改写引用/`## 本体关系`/slug）|
| `ea:BusinessEntity` 37 页 / Step / Task / Activity / Service / MCPService / BusinessRole | **不动**（流程建模保留）|
| 本体库里的 `ontology/ea/{customer,itasset,application,hardwareasset}` 等类页 | 由第 2 步**重新投影**自动处理（旧页删除、新页生成）|
| 扩展 TTL（ea-service / ea-ownership / bmmfd） | 不含这些名字（已查），**不动** |

> retag 会改写 `page_type` + slug + 引用，是**有影响面的写操作**，走它自带的两段式（这也正好符合"不反复确认但要有一次确认"）。

## 4. 连带要更新的文件（编译自动生成的除外）

- `ontology/lexicon/ea.keywords.yaml`（去掉客户/IT 资产关键词）、`ontology/lexicon/bmm.keywords.yaml`（加"主系统/子系统/硬件资产"关键词与线索）
- `ontology/EA轻量版.md` / `ontology/BMM轻量版.md`（人读版同步）
- `deploy/weknora-fork/config/agent_system_prompt.yaml` + `baseline/agent_system_prompt.yaml`（提示词里的类清单）
- `skills/ea_overview_design/SKILL.md`、`skills/domain_modeling/SKILL.md`（若提到 `ea:Application`/`ITAsset`）
- `docs/agent-design-flow.md`、`docs/structured-data-import-plan.md`（系统页类型改 `bmm:SubSystem`/`bmm:MainSystem`）
- `deploy/weknora-fork/frontend/ontologyTypes.ts`（第 2 步生成）+ `patch_frontend.py`
- 交付包重打（`pack_release.py`）+ `sha256sum -c`

## 5. 需要你点头的 4 个点（其余按上面执行）

> **已按 D 执行完（2026-09-29，含 C 追加的 ③④⑥⑦）**：
> ① TTL 真源已改 + lexicon/轻量版已同步；② 编译产物已重生成（validate ok、error 0，20 个产物更新）；
> ③ 投影清单 `artifacts/weknora/ontology_wiki.jsonl` 已重生成（253 行）；
> ④ **Neo4j 本体投影已清空重灌**（旧节点 205 → 0，重灌 701 条语句；类 50 / 属性 114 / 限制 29 / 枚举 7 / 模块 5），
>   随后重投影本体库 → **254 页**（类 50 / 关系 81 / 属性 114 / 模块 6 / 轻量版 2 / 索引 1），
>   新类页 4 个 + 新关系页 2 个 + 新数据属性页 6 个，旧类页已消失；
> ⑥ 连带文字：`config/agent_system_prompt.yaml`（ea 段 11→5 类、关系去重并更新 range；BMM 段 26→30 类、33→35 关系）、
>   `frontend/ontologyTypes.ts`（57→**55** 类型）、`frontend/patch_frontend.py`、`docs/agent-design-flow.md`、
>   `skills/ea_overview_design/SKILL.md`、`tools/ontology-mcp/server.py`（`app_types` 加新类并兼容存量）；
>   交付文档页数口径 248 → 254、类型数 57 → 55（MANUAL/KB-CONFIG/ONTOLOGY-KB/FRONTEND/export_db.py）；
> ⑦ 前端镜像已重建（`ontologyTypes.ts` 生成 → 构建树 → vite build 1m58s → docker build → 切换）；
> **未做**（你选择 C 明确排除）：交付包重打、那 9 个存量实例页的处理（6 个 `ea:Application` + 3 个 `ea:Customer`）。
>
> ⚠️ **留意**：那 9 页的 `page_type` 已不在本体里 → 巡检 **B1** 会报它们（这是预期的，等你决定 retag/软删）。
> 备份：`/root/onto-backup-20260929-211245/ontology`（37 个文件）+ git（`git revert` 可回）。

1. **IT 资产挂哪**：`bmm:ITAsset ⊑ bmm:Asset`（我的默认，与"固定资产"同级）还是 `⊑ bmm:Resource`（直接挂资源）？
2. **`ea:Application` 归并**：6 个 `ea:Application` 页 retag 成 `bmm:SubSystem`（默认），还是 `bmm:Application`（保留"应用系统"名、另加"子系统"概念）？
3. **客户类 3 页**：软删（默认，可恢复）还是 retag 成 `ea:BusinessEntity` 留着？
4. **两条 EA 关系**：`stepSupportedByAsset`（range→`bmm:ITAsset`）与 `applicationProvidesService`（domain→`bmm:SubSystem`）**保留在 EA**（默认，流程建模要用）还是整条迁去 BMM？
