# 自定义知识库配置指引（把你们自己的业务建进来）

> 适用：你们已在内部网跑起 WeKnora（app + 前端 + Postgres）。本指引讲**建哪些库、每个字段怎么填、怎么验收**。
> 前置：`02-mcp-server` 已部署并在 WeKnora 里注册为 MCP 服务（见 `MCP-SERVER.md`）。

## 1. 一共需要两个知识库

| 知识库名称（建议） | 内容 | 谁写 | 怎么来 |
|---|---|---|---|
| **企业本体模型** | 类 / 关系 / 数据属性 / 模块（248 页）| 只有本项目的脚本（人跑）| 导入 `03-manual/seed/` 或按 TTL 编译投影（见 `ONTOLOGY-KB.md`）|
| **企业知识**（名字随意） | 你们的流程、活动、任务、服务、实体等**实例页** | 智能体（抽取 + 设计落库）| 上传文档后由智能体抽取；设计页由智能体落库 |

> 两个库都要**绑定给智能体**（智能体配置 `knowledge_bases` 两项都填）：前者用来**查类型**，后者用来**读业务内容/落库**。

## 2. 建库（UI 三步）

1. 知识库 → 新建 → 名称 `企业本体模型` → 其他保持默认 → 创建（建好后**先不要上传文档**）；
2. 再建一个业务库，名称如 `企业知识`；
3. 记下两个库的 **uuid**（库设置里能看到，或 `SELECT id,name FROM knowledge_bases WHERE deleted_at IS NULL;`）。

模型选择（两库一致即可）：

| 字段 | 说明 | 我们这版的取值（示例）|
|---|---|---|
| `embedding_model_id` | 向量化模型（检索用）| `<embedding 模型 id>` |
| `summary_model_id` | 生成/摘要模型（wiki 生成、抽取都用它）| `<KnowledgeQA 模型 id>`，例如 deepseek-flash |

取 id：`SELECT id,name,type FROM models WHERE deleted_at IS NULL;`

## 3. 关键配置：`wiki_config`（决定"文档 → wiki"的行为）

UI 里在库设置中对应"内容指令 / 抽取粒度 / 抽取指令"。**我们推荐值**（直接改库也行，见下）：

```json
{
  "synthesis_model_id": "<summary_model_id>",
  "content_instructions": "尽量遵循源文，采用链接标注与其他知识的关联",
  "max_pages_per_ingest": 0,
  "extraction_granularity": "standard",
  "extraction_instructions": "重点识别组织机构、角色、业务规则、业务产品、业务实体、业务属性、业务活动、任务、步骤、目标、方法手段、行动计划、影响因素、评估"
}
```

| 键 | 作用 | 建议 |
|---|---|---|
| `synthesis_model_id` | 生成 wiki 页用的模型 | 填业务库的生成模型 id |
| `content_instructions` | 页面写作口径 | 保持"遵循源文 + 链接标注" |
| `extraction_granularity` | 抽取粒度 | `standard`（`fine` 会爆页面数）|
| `extraction_instructions` | **要识别哪些类型** | 按你们的领域改：至少覆盖 EA 的 服务/活动/任务/步骤/实体/角色；BMM 的目标/行动/影响因素（若要跑领域建模技能）|
| `max_pages_per_ingest` | 单次上传最多生成多少页 | `0` = 不限（生产建议设 50~200 防爆）|

**本体模型库**额外要关掉"文档自动抽取"（它的页面是编译生成的）：

```json
{ "enabled": false }        -- knowledge_bases.extract_config
```

用 SQL 套用（把 `<kb>` 换成业务库 uuid）：

```sql
UPDATE knowledge_bases
   SET wiki_config = jsonb_build_object(
         'synthesis_model_id', summary_model_id,
         'content_instructions', '尽量遵循源文，采用链接标注与其他知识的关联',
         'max_pages_per_ingest', 0,
         'extraction_granularity', 'standard',
         'extraction_instructions', '重点识别组织机构、角色、业务规则、业务产品、业务实体、业务属性、业务活动、任务、步骤、目标、方法手段、行动计划、影响因素、评估'),
       updated_at = now()
 WHERE id = '<kb>';

UPDATE knowledge_bases SET extract_config = '{"enabled": false}'::jsonb, updated_at = now()
 WHERE id = '<本体模型库 uuid>';
```

## 4. 「上传自动生成 wiki」开关

知识库头部有这个开关（我们打的补丁之一）。打开后：上传文档 → 切片 → **自动生成 wiki 页**（走上面的 `wiki_config`）。
不开也能用：让智能体按 **`domain_modeling` 技能分批抽**（`doc_outline` 取本批上下文 → 智能体自己比对 →
`save_knowledge` 落库 → `extract_state` 续跑），比「整篇一次性抽取」更可控，也能指定只抽某几类/某几条关系。

> 抽取结果进入"待确认合并队列"时：`list_pending_merges` 查看，`resolve_pending_merge` 决定合并/新建；
> 阈值在工具参数里（`high`/`low`）。

## 5. 页面与类型的约定（决定巡检是否干净）

| 约定 | 说明 |
|---|---|
| `page_type` 用**前缀:类名** | 如 `ea:Service`、`ea:Step`、`easvc:ServiceOperation`；必须是本体模型库里有声明的类（否则巡检 B1）|
| slug 规则 | `<模块>/<类小写>/<标题>`，例如 `ea/mcpservice/签约账户选择服务`；同标题重复会导致 D1 |
| 关系写进正文 `## 本体关系` | MCP 落库时自动写；人工不要手改（`## CRUD 矩阵` 等派生小节会被服务端重算覆盖）|
| `source_refs` 溯源 | 实例页必须有来源文档；设计生成的页从"服务页/报告页"继承。**空来源**会被巡检判 C1（high）|
| 类型/关系只能取本体面 | `ontology_types(model, focus?)`；`nodes[].attributes` / `edges[].properties` 的键必须是该类声明过的数据属性 |

## 6. 智能体与工具面（配置在哪）

在 `custom_agents` 里（UI：智能体 → 编辑），一次配好四件事：

1. `config.system_prompt` —— 提示词（我们给的「本体建模与设计」提示词，见 `03-manual/AGENTS-SQL.md`）；
2. `config.knowledge_bases` —— **两个库的 uuid**；
3. `config.mcp_services` —— `['<bodhi_ontology 的 mcp_services.id>']` + `config.mcp_selection_mode='all'`；
4. `config.allowed_tools` —— 18 个：5 个 wiki 工具（`grep_chunks`/`list_knowledge_chunks`/`get_document_info`/
   `wiki_search`/`wiki_read_page`）+ 13 个 `mcp_bodhi_ontology_*`（**不要**给 `wiki_write_page`，写库只走 MCP；
   抽取类工具已于 2026-09-22 退役，建模改用 `doc_outline` / `save_knowledge` / `extract_state`）。

一条 SQL 落库的完整示例在 `03-manual/AGENTS-SQL.md`（含可回滚的 `DELETE/INSERT`）。

## 7. 验收清单

| # | 检查 | 期望 |
|---|---|---|
| 1 | 智能体跑一轮"只读"任务（`skills()` → 读一页）| `tool_count=15`；`logs/mcp_calls_*.log` 里能看到 `skills` |
| 2 | 智能体说"某工具不存在" | **十有八九是 MCP 没注册上**（SSRF/URL/网络），见 `TROUBLESHOOTING.md` §1 |
| 3 | 上传一篇文档并抽取 | 页面类型都在本体里；无 B1/B2 报错；来源(`source_refs`)非空 |
| 4 | `curl "http://<mcp>:8765/bodhi/audit?kb_id=<kb>"` | findings 里**没有** C1/C3（无来源）；A1/A2 若出现属于历史页，按提示修 |
| 5 | 前端类型下拉 | 能看到业务用到的类型（说明本体模型库 + UI 产物都到位）|
