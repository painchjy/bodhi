# 自助取数分析（analytics）· 本体 + 配置层

2026-11-21 用户确认口径。本文是落地说明；真源分别是：

- 本体：`ontology/sources/analytics.ttl`
- analytics 配置：`sql/analytics/001_schema.sql` + `002` + `005`…`011`
- diag 事实/维度：`sql/diag/001_schema.sql` + `002_tool_dimension_seed.sql` + `003_examples.sql`
- 页面投影：`tools/ke-core/ke_analytics.py`
- 管理端：`tools/analytics-admin/`

## 1. 本体（4 个类 + 7 个对象属性 + 6 个数据属性）

```text
bpmn:DataObject
├─ analytics:Dimension          维度页
└─ analytics:Fact               事实页

analytics:StandardQueryScenario  标准取数场景页（溯源/建模起点）
```

| 属性 | 类型 | domain → range | 用途 |
|---|---|---|---|
| `analytics:factUsesDimension` | ObjectProperty | `Fact → Dimension` | 事实→维度**类级通路**，供 `ontology_paths` 枚举 |
| `analytics:objectUsesMcpService` | ObjectProperty | `DataObject → MCPService` | 对象→active MCP 服务（实例来自 `analytics.binding`） |
| `analytics:grain` | DatatypeProperty | `Fact → xsd:string` | 事实粒度（写在事实正文） |
| `analytics:mappingObjectType` | DatatypeProperty | `bpmn:DataObject → xsd:string` | `fact｜dimension`，mapping 类型判别 |
| `analytics:mappingObjectId` | DatatypeProperty | `bpmn:DataObject → xsd:string` | **唯一** mapping 对象 ID（内部 fact_id/dimension_id） |
| `analytics:mappingServiceId` | DatatypeProperty | `agent:MCPService → xsd:string` | analytics 服务 ID（`svc_*`），mapping 使用 |
| `analytics:platformMcpServiceId` | DatatypeProperty | `agent:MCPService → xsd:string` | 平台 `mcp_services.id`，可选，不用于 mapping |
| `analytics:databaseType` | DatatypeProperty | `agent:MCPService → xsd:string` | SQL 方言；取值 `oceanbase｜gaussdb｜hetu｜postgresql｜mysql` |

`databaseType` 不新增 `SelfServiceMCPService` 子类；`agent:MCPService` 页 + 该属性即可。

## 2. 配置表（18 张 + 8 个视图）

**维度**：`dimension`（`member_key`） / `dimension_level`（`formula_kind` / `formula_params` / `data_type`） / `dimension_attribute` / `dimension_table`（维度物理表/视图来源）

**事实**：`fact` / `fact_dimension`（`fact_key` / `dimension_key` / `join_type`；PK=`fact_id + dimension_id`） / `fact_table`（事实对象的物理事实表组成；一个 Fact 可由多张事实表 JOIN 组成）

**指标/字段**：`base_metric`（聚合、可加性、过滤表达式） /
`derived_metric`（`expression_language` = pseudo/sql + `expression`） /
`fact_field`

**MCP 服务**：`mcp_service`（`database_type` 五类控制取值）

**映射（只供管理界面，不进 wiki）**：`binding` / `field_mapping` /
`enum_mapping`。`field_mapping` 只映射事实对象自己的事实表列；维度表由
Dimension 的 binding/field_mapping 表达，事实字段不得映射到维表。

**场景**：`scenario` / `scenario_join` / `scenario_filter` / `scenario_fact`（来源事实，不设 role）

**视图**：

- `v_fact_spec`：事实 + 基础指标 + 派生指标 + 非指标字段 + 维度键
- `v_dimension_spec`：维度 + 成员键 + 层级公式 + 属性 + 使用它的事实
- `v_binding_detail`：binding + 字段/枚举映射
- `v_scenario_detail`：场景 + 来源事实 + 公共 MCP 服务 + join + filter
- `v_scenario_fact`：Scenario 来源 Fact（只表达来源）
- `v_fact_dimension_path`：事实→维度语义通路（含 `fact_key`/`dimension_key`/`join_type`）
- `v_metric_catalog`：基础 + 派生指标目录
- `v_mapping_interface`：知识页 ↔ analytics 的唯一只读映射接口（fact/dimension + binding + service；公式/直接列维度标记 `formula_only`）

## 3. 种子中的两个工具分类维度

`002_seed.sql` + `008_semantic_refactor.sql` 里只有 **2 个工具分类维度对象**：

| 维度实例 | 层数 | 层级 | 物理值来源 |
|---|---|---|---|
| `dim_tool_category_2level` | 2 | 内容安全 / 知识规范 / 运行控制 → 工具分类 | `diag.tool_problem_dimension` |
| `dim_tool_category_1level` | 1 | 系统自己的 6 组工具分类 | `diag.tool_dimension` |

只有 `fact_agent_tool_call`（AgentToolCallFact）通过 `fact_dimension` 挂这两个维度
（`fact_key=tool_code`，`join_type=left`）；`fact_agent_turn`（AgentTurnFact）挂
时间 / Agent / 模型 / 会话 4 个维度，**不挂工具分类**。

物理映射不再写在 `fact_dimension` / 页面里：工具分类维度的 `binding` 指向
`svc_postgresql`，`field_mapping` 分别映射 `diag.tool_dimension` /
`diag.tool_problem_dimension`；一个对象只映射一次，不按事实重复。

实际数据来自 deck P6：

- 系统 1 层分类：执行边界 8 / 知识规范 6 / 知识完整 8 / 运行控制 8+4 /
  跨库迁移 9 / 外部工具连接 4；
- 2 层分类第一层：内容安全 / 知识规范 / 运行控制；
- 第二层：外部内容访问 / 知识结构规范 / 知识完整性 / 跨库知识治理 /
  执行边界 / 运行审计与治理 / 权限与审批；
- 共 52 个工具成员（含新增 mapping_lookup / scenario_lookup / list_tables / describe_table / run_select）；待建成员 `is_pending=true`。

`012_fact_table_join.sql` 额外登记事实表组成，并给出一个多事实表事实
`fact_agent_tool_context`：主表 `agent_tool_call_fact` + 轮次上下文
`agent_turn_fact`，只通过 `scenario_join` 的 `message_id` 关联；它的事实字段
只映射这两张事实表，不映射维表。

> 实际查询源是：
> - `diag.agent_turn_fact`（来自 `public.messages` + `usage`）
> - `diag.agent_tool_call_fact`（展开 `agent_steps[].tool_calls[]`）
> - `diag.tool_dimension` / `diag.tool_problem_dimension`（工具分类值）
>
> `analytics.dimension_value` / `dimension_mapping` 已于 2026-11-22 **硬删除**；
> 智能体应使用 `diag` 维度表，物理映射通过 `mapping_lookup` 查
> `binding + field_mapping`，不要从知识页猜表名/列名。

## 4. 事实→维度路径验证

### 4.1 类级（MCP）

新增通用工具 `ontology_paths`（无业务名词）：

```text
ontology_paths(from_class="analytics:Fact", to_class="analytics:Dimension")
→ 1 跳：analytics:Fact —[analytics:factUsesDimension]→ analytics:Dimension
```

### 4.2 实例级（PG 视图）

```sql
SELECT fact_name, dimension_name, level_count, level_names
  FROM analytics.v_fact_dimension_path
 WHERE fact_id = 'fact_agent_tool_call'
 ORDER BY sort_order;
```

期望至少两行：

| fact_name | dimension_name | level_count | level_names |
|---|---|---|---|
| AgentToolCallFact | 工具分类（2层） | 2 | 内容安全 / 知识规范 / 运行控制 → 工具分类 |
| AgentToolCallFact | 工具分类（1层） | 1 | 系统 6 组工具分类 |

Python 读取：

```python
import ke_analytics
ke_analytics.fact_dimension_paths("fact_agent_tool_call")
```

## 5. 应用与页面同步

### 5.1 应用 schema / 种子

```bash
# 在 02 包根目录；推荐用 init-db.sh（psql，幂等）
cd 02-mcp-server
./init-db.sh all
```

对应顺序：

```text
sql/diag/001_schema.sql
sql/diag/002_tool_dimension_seed.sql
sql/analytics/001_schema.sql
sql/analytics/002_seed.sql
sql/analytics/005_scenario_diag_seed.sql
sql/analytics/006_diag_fact_mapping.sql
sql/analytics/007_diag_metric_seed.sql
sql/analytics/008_semantic_refactor.sql
sql/analytics/009_semantic_views.sql
sql/analytics/010_scenario_fact.sql
sql/analytics/011_mapping_interface.sql
sql/analytics/012_fact_table_join.sql
sql/analytics/013_dimension_table.sql
sql/analytics/014_semantic_test_scenarios.sql
```

> 012/013 不能漏；`013` 负责 `dimension_table`（维度视图登记）。

### 5.2 同步 wiki 页

```python
import sys; sys.path.insert(0, "tools/ke-core")
import ke_analytics
ke_analytics.sync_pages("<kb_id>", kind="all", dry_run=False)
```

只生成四类页：

- `analytics/fact/<code>` → `analytics:Fact`
- `analytics/dimension/<code>` → `analytics:Dimension`
- `analytics/scenario/<code>` → `analytics:StandardQueryScenario`
- `agent/mcpservice/<service_name>` → `agent:MCPService`（只 active；按本体页 slug 规则）

**不生成** binding / field_mapping / enum_mapping / scenario_join /
scenario_filter 页。事实页：基础指标 / 派生指标 / 非指标字段 / 维度键（语义） /
公共提示；维度页：成员键 / 层级公式 / 属性 / 使用它的事实 / 公共提示。
所有同步统一走 `ke_pages.write_knowledge`（PG 正文与图 `articleBody` 同源），
不再使用只改 PG 的 `_update_existing_page`。Fact/Dimension 的 `mapping_object_type/id`
从只读视图 `analytics.v_mapping_interface` 投影，服务页的 `mappingServiceId/platformMcpServiceId/databaseType`
从 `mcp_service` + 平台 `mcp_services` 投影；同步不覆盖 binding。

> 已同步到指定知识库：**bodhi运行监控分析**
>（`af70952f-aea6-4b46-89d8-6c4158cfd0aa`），共 16 页（6 维度 + 3 事实 + 6 场景 + 1 MCP 服务）。
> `dim_tool` 旧页/旧节点已硬删除；MCP 服务页按 `agent/mcpservice/<名称>` 生成；图边 10 条 `factUsesDimension`（工具调用 6 + 轮次 4）
> 与 5 条 `objectUsesMcpService`（新增多事实表事实）。同步后 PG/图正文差异 = 0。

### 5.3 页面映射接口（正文 + 图属性）

Fact / Dimension 页顶部固定生成：

```markdown
## 自助取数映射接口
- mapping_object_type: `fact`
- mapping_object_id: `fact_agent_tool_call`
- 可用 MCP 服务：`agent_diag`（mappingServiceId=`svc_postgresql`，postgresql）
```

公式/直接列维度没有独立 binding 时：`mapping_object_type: dimension`、
`mapping_object_id: dim_time`，并注明按 `formula_only` 使用
`levels[].formula_sql`。

MCP 服务页固定生成 `mapping_service_id`、`platform_mcp_service_id`（可选）、
`database_type`。这些值同时写入页面图属性：`analytics:mappingObjectType` /
`analytics:mappingObjectId` / `analytics:mappingServiceId` /
`analytics:platformMcpServiceId` / `analytics:databaseType`。

### 5.3.1 事实选择（fact_match）

智能体先提取：

- `metrics`：指标/计算清单；
- `fact_tables`：用户明确提到的事实表/事实对象；
- `filters`：筛选条件；
- `group_by`：分组字段；
- `classification_fields`：分类统计字段/维度；
- `intent`：目的/场景。

调用只读 `fact_match`，它把词条与 `base_metric` / `derived_metric` /
`fact_field` / `fact_table` / `dimension` / `scenario` 做相似度匹配并汇总到
Fact。`decision=confident` 才继续；`ambiguous` 让用户选择或补充信息后重算；
`no_match` 不得猜事实。算法不写死任何事实对象名称。

### 5.4 公共提示（事实页/维度页内置）

页面末尾统一带：

```text
本页只描述语义（对象、指标、字段、维度、层次）；物理映射不在知识页里。
- 选事实：fact_match(metrics=..., fact_tables=..., filters=..., group_by=..., classification_fields=..., intent=...)；decision=confident 才继续
- 事实/维度：mapping_object_type（fact｜dimension）+ mapping_object_id（内部对象 ID）
- MCP 服务：mapping_service_id + platform_mcp_service_id（可选）+ database_type
- 物理映射：mapping_lookup(mapping_object_type=..., mapping_object_id=..., mapping_service_id=...)
- 先确认 mapping，再调用 scenario_lookup(mapping_object_id=..., mapping_service_id=..., intent=...)
- status=mapped 继续；formula_only 用 levels[].formula_sql 继续（bindings 为空不等于缺映射）；missing 停止并检查 ID/服务
- 事实字段若映射到维表/未登记表，返回 `missing` + `fact_source_not_fact_table`；不得把维表当事实表
- 管理台：analytics-admin 的 binding / field_mapping / enum_mapping；v_mapping_interface 只读
不要从本页猜物理表名或列名；不要用页面 slug、标题、fact_id、dimension_id、object_id 等旧叫法调用 mapping_lookup。
```

### 5.5 维度值为什么必须在 diag

智能体最终查的是 `diag` 事实表。只读 analytics 的维度语义（层级/属性）只能知道
“怎么分组”，**不知道 JOIN 哪张表、用哪个键、日期/小时怎么算**，生成不了可执行 SQL。

所以拆成两层：

| 层 | 内容 | 作用 |
|---|---|---|
| `analytics.fact_dimension` | 事实键 / 维度键 / JOIN 类型（只语义，不存物理表列） | **事实→维度语义契约** |
| `analytics.fact_table` | Fact 的物理事实表组成 / 主表 / 粒度 | **多事实表组成，不存列映射** |
| `analytics.dimension_level` | `formula_kind` / `formula_params`（如 `date` / `hour` + 时区） | **派生公式只定义一次** |
| `analytics.binding` + `field_mapping` | 对象×MCP 服务的物理表/列/枚举 | 物理映射唯一来源；事实字段只能落在 `fact_table` |
| `diag.tool_dimension` / `tool_problem_dimension` | 52 个工具的实际分类值 | 工具维度 **LEFT JOIN** 的数据 |
| `diag.agent_tool_call_fact` | 工具调用事实（展开 `agent_steps`） | 工具使用频次/未使用工具/返回体量 |
| `diag.agent_turn_fact` | 轮次事实（`messages.usage`） | token 消耗 |
| `diag.tool_dimension_all` | 已登记工具 + 事实中未登记工具（未分类） | 保证实际出现的工具不被漏掉 |

映射规则：

- 工具分类：维度 `binding` 指向 `svc_postgresql`，`field_mapping` 的
  `semantic_kind=dimension_key` 映射 `tool_code`；事实键 `fact_key=tool_code`、
  维度键 `dimension_key=tool_code`、`join_type=left`；
- Agent / 模型 / 会话：事实键直取，`join_type=none`，不建维度表；
- 日期 / 小时：`dimension_level.formula_kind=date|hour`，公式渲染按
  `database_type` 生成 SQL，不按 MCP 服务重复写；
- 工具使用统计用 `tool_dimension_all`，避免事实里出现但未登记的工具被漏掉；
- `scenario_join` 只记录事实表↔事实表路径；事实↔维度路径不写入，
  由 `fact_dimension` + 维度 binding/field_mapping 生成。

### 5.6 可直接改写的 SQL

工具使用频次 + Agent/模型对比 + 未使用工具：

```sql
SELECT d.tool_code, d.tool_name, d.category_name,
       f.agent_name, f.model_id,
       count(f.tool_code) AS usage_count
  FROM diag.tool_dimension_all d
  LEFT JOIN diag.agent_tool_call_fact f
    ON f.tool_code = d.tool_code
 GROUP BY d.tool_code, d.tool_name, d.category_name, f.agent_name, f.model_id
 ORDER BY usage_count DESC, d.tool_name, f.agent_name NULLS FIRST;
```

只要未使用工具：

```sql
SELECT d.tool_code, d.tool_name, d.category_name, d.is_pending
  FROM diag.tool_dimension d
  LEFT JOIN diag.agent_tool_call_fact f ON f.tool_code = d.tool_code
 WHERE f.tool_code IS NULL
 ORDER BY d.is_pending, d.category_name, d.tool_code;
```

日期/小时（无需时间维度表）：

```sql
SELECT f.event_date, f.event_hour, count(*) AS tool_calls
  FROM diag.agent_tool_call_fact f
 GROUP BY 1,2 ORDER BY 1,2;
```

token 消耗（按会话/Agent/模型）：

```sql
SELECT f.session_id, f.agent_name, f.model_id,
       count(*) AS turns,
       sum(f.prompt_tokens) AS prompt_tokens,
       sum(f.completion_tokens) AS completion_tokens,
       sum(f.total_tokens) AS total_tokens,
       sum(f.cached_tokens) AS cached_tokens
  FROM diag.agent_turn_fact f
 GROUP BY 1,2,3 ORDER BY total_tokens DESC;
```

工具返回体量/成功率：

```sql
SELECT f.tool_code,
       count(*) AS calls,
       sum(f.result_chars) AS result_chars,
       avg(f.result_chars)::numeric(18,1) AS avg_result_chars,
       count(*) FILTER (WHERE f.tool_success) AS success_calls,
       count(*) FILTER (WHERE NOT f.tool_success) AS failed_calls
  FROM diag.agent_tool_call_fact f
 GROUP BY 1 ORDER BY calls DESC;
```

> **token 口径**：`agent_tool_call_fact` 的 `turn_*_tokens` 是所属轮次的 usage，
> 一次工具调用重复一行；按工具聚合时**不要** sum token，token 只在
> `agent_turn_fact` 按轮次/会话求和。

完整示例在 `sql/diag/003_examples.sql`。

### 5.7 管理台乱码根因

`dim_tool_category_2level` 的 name/description 乱码不是 SQL seed 或浏览器管理台造成的：
验证阶段用 **Windows PowerShell 5 `Invoke-WebRequest -Body <中文字符串>`** 发过一次
`POST /api/row`，PowerShell 不以 UTF-8 编码 body，覆盖了这一行（只覆盖了这一行）。

现在已：

- 用 UTF-8 seed 恢复正确中文；
- `diag.tool_dimension` / `tool_problem_dimension` 直接使用 UTF-8 数据；管理台拒绝 `U+FFFD` 污染字段；
- 管理台拒绝含 UTF-8 替换字符 `U+FFFD` 的字段；
- 管理台浏览器 `fetch(JSON.stringify(...))` 按 UTF-8 发送，正常使用不会复现。

> 命令行验证请用浏览器管理台、Python `urllib`/`requests`（显式 `.encode('utf-8')`）
> 或 curl；不要用 Windows PowerShell 5 的中文字符串 body。

### 5.8 MCP 侧权限

自助取数 MCP 的只读角色需要对 `diag` 有：

```sql
GRANT USAGE ON SCHEMA diag TO <mcp_readonly_role>;
GRANT SELECT ON ALL TABLES IN SCHEMA diag TO <mcp_readonly_role>;
ALTER DEFAULT PRIVILEGES IN SCHEMA diag
  GRANT SELECT ON TABLES TO <mcp_readonly_role>;
```

`analytics` 是配置库，不需要暴露给数据查询角色；MCP 的 `list_tables` /
`describe_table` 应能看到 `diag.tool_dimension`、`diag.tool_problem_dimension`、
`diag.agent_turn_fact`、`diag.agent_tool_call_fact`。

## 6. 管理端（可视化配置）

在 **02 包**根目录：

```bash
./start.sh admin
# 打开 http://<host>:8781/
```

服务固定监听 `0.0.0.0:8781`（配置 `config/analytics-admin.env`）；当前不启用 token，
必须用 nginx/VPN/ACL 限制来源。Windows 开发机可直接
`python tools/analytics-admin/server.py --host 127.0.0.1 --port 8781`。

- 左表浏览 18 张配置表；点行编辑 JSON；保存/新建/删除按主键 upsert；
- 右侧「同步 wiki 页面」填 KB ID + kind + dry_run；
- 映射、枚举、join、filter 只在此配置，不进知识库检索。

## 7. 旧固定工具映射统一

旧工具契约绑定迁移为：

```text
analytics.binding.binding_type = 'tool_contract'
analytics.binding.tool_name = <旧工具名>
analytics.field_mapping.source_type = 'tool_contract'
```

`field_mapping` 的 `semantic_kind` 只用
`base_metric｜non_metric_field｜dimension_key｜dimension_attribute`；
维度物理取数方式在 `field_mapping` 的 `source_type=mcp_table|tool_contract` + 来源表/列，
事实/维度只保留 `fact_key` / `dimension_key` / `join_type` 与层级公式。
派生指标不映射物理字段，它的计算逻辑在 `derived_metric.expression`。

## 8. 验证清单

```bash
python tests/test_analytics_ontology.py   # 4 类 + 7 对象属性 + 工具调用事实挂 2 个工具维度
python tests/test_analytics_config.py     # 18 表 / 8 视图 / 5 类 database_type / 页面投影
python tests/test_analytics_fact_match.py # 指标/事实表/场景/筛选/分组/分类字段选事实
python tests/test_analytics_semantic.py   # 场景 SQL 扫描 / 表角色 / 口径匹配
python tests/test_tools_contract.py       # fact_match / mapping_lookup / scenario_lookup / scenario_semantic_* 已进工具契约
python tools/ontology-compiler/compile.py validate --summary
```

图关系同步：`sync_pages` 还会重建 `analytics:factUsesDimension`、
`analytics:objectUsesMcpService`、`analytics:scenarioBasedOnFact` 与
`analytics:scenarioUsesMcpService`；当前 `bodhi运行监控分析` 已确认
**10 条事实→维度**、**5 条对象→MCP 服务**、**6 条场景来源事实**、
**6 条场景→MCP 服务**，PG 页与图 `articleBody` 正文差异 **0**。

## 9. 分析案例（AnalysisCase）

`analytics:AnalysisCase` 是 `bmm:Report` 子类；SQL、参数、JOIN、FILTER 和口径
全部在正文，**不落 analytics 表**。图关系：

- `caseUsesFact`：案例使用的事实；
- `caseUsesDimension`：案例使用的维度；
- `caseUsesMcpService`：案例执行使用的 MCP 服务。

页面 slug 使用 `analytics/analysiscase/<案例标识>`；保存前必须先通过只读
`analytics-sql` 的 `run_select` 验证。执行案例时读正文 `## SQL` 下的 ```sql
代码块，校验对象/服务 active 后再执行。

## 10. 只读 analytics-sql MCP（agent_diag，8771）

服务：`tools/domain-mcp/analytics_sql/server.py`，在 **02 包**，端口 **8771**；
容器名 `bodhi-agent-diag`（URL `http://bodhi-agent-diag:8771/mcp`）。工具固定为：

- `list_tables`
- `describe_table`
- `run_select`

启动：`./start.sh diag`；配置：`config/common.env` + `config/agent-diag.env`。
WeKnora `mcp_services.name=agent_diag`；`analytics.mcp_service` 里的 `svc_postgresql`
是内部数据服务配置，两者不是同一张表。



## 11. 标准取数场景 → 语义层提取

任务页：`bpmn/task/从标准取数场景提取语义层`（步骤与 `answers` 形状以该页 **`skos:note`** 为准）；
智能体提示词只负责任务分流（选到该任务时不走问数流程），不重复任务步骤。工具在 **bodhi MCP**（不新增 MCP 服务）：

- `scenario_semantic_validate`：只读前置校验（场景/服务/来源事实 binding/SQL 可解析），**不执行 SQL**；
- `scenario_semantic_plan`：只读差异计划，扫描场景 SQL 的表/列/聚合/FILTER/GROUP BY，
  和现有 Fact 的 `base_metric` / `derived_metric` / `fact_field` / `fact_dimension` /
  `field_mapping` 比对，输出 `missing` 与 `questions`（中文含义/枚举值/事实选择）；
- `scenario_semantic_apply`：唯一写入口，必须 `confirm=true` + 匹配的 `plan_id` + `dry_run=false`；
  只写 analytics 配置表，写后调用 `sync_pages` 并复核 `mapping_lookup` / `scenario_lookup`。

一次性语义（优先，避免多轮追问）：

1. 场景 SQL 用中文列别名直接给语义：`count(*) AS "调用次数"`、`sum(tokens) AS "总Token"`；
   PostgreSQL 用双引号或裸中文（`AS "调用次数"` / `AS 调用次数`），单引号别名在 PG 不合法；
   MySQL/Trino 可用 `AS '调用次数'`。扫描器三种都识别，别名作为名称，不再追问 label。
2. 布尔枚举写进列别名：`tool_success AS "执行结果（true=成功，false=失败）"`，
   或在**同一次** plan 的 `answers.enums` 给全 `{"true":"成功","false":"失败"}`。
3. 用户首条消息已给出事实名称/粒度/枚举/单位时，第一次 `scenario_semantic_plan` 就带 `answers`
   一次算完；只有 SQL 与用户输入都没有、且现有语义层未覆盖时，才一次性列出全部问题。
4. 已有事实默认不修改名称/描述/粒度（防止把智能体臆造的名称写回）；确需修改时传
   `answers.fact.update=true`，并在提问中明确说明。新建事实传 `answers.fact.create=true`，
   可同时给 `fact_id/code/name/grain`；缺 `fact_id` 时按 code 生成。
5. 场景粒度优先取 SQL 首行注释 `-- grain: col1, col2`；没有注释时用 GROUP BY 列，
   并排除已登记的非指标字段。只有 `exact` / `fact_contains` 才允许挂接已有事实；
   `scenario_contains` / `partial` 必须阻断并询问是否新建事实（`fact_creation_required`）；
   没有匹配事实时询问 `fact_creation`。

约束：

1. 场景 SQL 必须由**场景绑定的 MCP 服务**执行（`run_select(service_id=scenario.service_id)`），
   失败就停止，不进入语义分析；
2. 字段名、中文含义、枚举标签一律来自配置、SQL 列别名/枚举提示或用户回答，**不猜**；
3. 事实字段只映射 `fact_table` 登记的事实表；维表由 Dimension 的
   `dimension_table + binding + field_mapping` 表达；
4. `scenario_join` 只记录事实表↔事实表路径；
5. 写操作默认开启，可用 `BODHI_ANALYTICS_WRITE=0` 关闭（kill switch）。

E2E：`tests/agent_e2e/test_analytics_semantic_extract.py`（`BODHI_RUN_ANALYTICS_E2E=1`）：
任务页/提示词注册、SQL 中文别名一次性语义、案例 1 删除部分指标/字段后按场景补全、
案例 2 清空一个可由场景重建的事实再重建、3 个语义样例场景
（全量轮次 / 工具上下文 / 缺粒度键新建事实）、6 个标准场景只读计划回归；
测试结束恢复快照并重新同步页面。

`run_select` 只允许单条 SELECT/WITH，拒绝 DML/DDL，行数上限默认 200、最大 1000。
服务通过 `analytics.mcp_service` 选择目标；当前 `agent_diag` 使用 PostgreSQL。
