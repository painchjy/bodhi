# 知识运维：一致性巡检与异常清理（wiki ↔ 本体图谱 ↔ 本体模型）

> 用户 2026-09-20 口径：发布一个**知识运维智能体**，能 ① 分析现有 wiki 与本体图谱的一致性；
> ② 分析本体图谱与本体模型的不一致；③ 把**无来源文档**的 wiki 页与图节点/关系判为**异常数据**，
> 排查后提供清理。
> 架构按用户决定走**方案 A：同进程加模块**（不新起服务）。
> 相关：`tools/ke-core/README.md`、`docs/bodhi-doc-cleanup.md`（按来源文档清理）、
> `docs/handoff-ontology-upload.md`（本体模型加载链路）、`docs/bodhi-reasoning.md`（语义规则，另做）。

## 0. 状态

| 期 | 内容 | 状态 |
|---|---|---|
| **P1** | 只读审计内核 `ke_audit.py` + MCP 工具 `audit_scan` + `GET /bodhi/audit` | ✅ 已实现并实测（**不写任何数据**） |
| **P2** | 计划→确认→执行（**硬删**）+ **初始化知识库**：`plan`/`apply` + CLI + `POST /bodhi/audit/{plan,apply}` + MCP `audit_plan`（只出计划） | ✅ 已实现并沙箱自检（见 §5） |
| **P3** | 「知识运维」智能体注册（`gen_agents.py --only ops` + yaml 模板） | ✅ 已注册：`bodhi-kb-ops`（只读工具 + `audit_scan`/`audit_plan`，**无写页/无抽取**） |
| 不做 | `/mcp-ops` 端点隔离（用户口径：**先不隔离**）；任何**自动**修复/定时清理（用户口径：**不得自动修**） | — |

## 0.1 用户已拍板的四条口径（2026-09-20）

1. **硬删除**，必须清理干净；并且要有**初始化知识库**功能（把 wiki 与图谱都清掉，便于反复测试）。
2. **不要自动修** —— 一切修改都必须由人给出指令。
3. **执行前必须确认**（工具只出计划；`apply` 必须 `confirm=true` / `--confirm`）。
4. **先不隔离**（不加 `/mcp-ops` 端点；智能体的工具面靠 `allowed_tools` 收窄）。
   > 因此之前装的 `bodhi-doc-gc.timer`（定时自动清残留）**已卸载**；`deploy/weknora-fork/doc_gc_install.sh` 仍保留为**可选**脚本（要用时显式 `--enable`）。

## 1. 架构（方案 A）

- 逻辑全部进 **`tools/ke-core/ke_audit.py`**（纯标准库，只读），与 `ke_db/ke_neo4j/ke_ontology/ke_pages/ke_docs` 同级；
- 暴露面三处：
  - **MCP 工具** `audit_scan`（服务 `bodhi_ontology` / `POST /mcp`，工具名带 `audit_` 前缀）；
  - **HTTP** `GET /bodhi/audit`（给前端/运维/巡检脚本）；
  - **CLI** `ke_audit.py scan <kb_id>`。
- 不新增进程/端口/systemd 单元/`mcp_services` 行；智能体的工具面靠 `custom_agents.config.allowed_tools` 收窄。
- 若以后需要**强隔离**（运维智能体看不到写页/抽取工具）：只需在 `do_POST` 放行第二个路径并把 `tool_definitions()` 按 scope 过滤，
  再在 `mcp_services` 加一行指向 `/mcp-ops`（改动约 3~5 行，见 §6 待确认项）。

## 2. 检查项与判定口径（P1 已实现）

数据源：PG `wiki_pages`（`page_type` / `page_metadata.ontology` / `source_refs` / `deleted_at`）、PG `knowledges`、Neo4j 图（`BodhiInstance` 节点/边）与本体投影（经 `ke_ontology`）。

> **2026-10-05 M3**：关系只走 Neo4j 图，wiki `out_links` / `in_links` 两列已**废弃恒空**，巡检不再读它们；原 **A2「反向边 in_links 不一致」已移除**（下表不再列 A2）。

| # | 检查 | 判定 | 严重度 | P2 可自动修 |
|---|---|---|---|---|
| A1 | 悬空出边 | 正文关系行指向的 slug 在本 KB 活页里不存在 | high | ✅ 删该关系行 |
| A3 | 类型自相矛盾 | `page_type` ≠ `page_metadata.ontology.class/type`（补前缀后比较） | medium | ❌ 需人确认以谁为准 |
| A4 | 关系 domain 非法 | 关系类型不在 `ke_ontology.relation_type_map(page_type)`（**含父类继承**）里 | high | ❌ 需人/模型层决定 |
| A5 | 重复/自环关系行 | 同页出现相同 `(关系, 目标)` 多次；或目标 = 自己 | low | ✅ 去重该行 |
| A6 | 元数据漂移 | 实例页缺 `page_metadata.ontology`；或 `last_edit_source` 不在已知生成器里 | low | ❌ 仅提示 |
| B1 | 类型不在模型 | 实例页 `page_type`（`模块:类`）不在 `ke_ontology.classes()` | high | ❌ 补模型或改数据 |
| B2 | 关系不在模型 | 关系类型不是模型里的对象属性 | high | ❌ 同上 |
| B3 | range 违反 | 用 `ke_ontology.target_closure(rel_type)` 判：目标页类型不在允许范围内 | high | ❌ |
| B4 | 模型库页 vs 投影 | 本体模型库：Neo4j 的模块/类 与 `ontology:Class`/`ontology:Module` 页一一对应（`ontology/index` 总览页豁免） | medium | ✅ 重投影（`/bodhi/ontology/wiki`） |
| B5 | **本体投影 ↔ 编译产物不一致** | 投影（运行真源，含上传导入的模块）的类/属性 与 `artifacts/weknora/ontology_index.json` 对比；两侧差集都报（投影独有 = 上传模块未编进产物；产物独有 = 投影加载不全） | medium | ✅ **直接命令，不走 plan_id**：① 重编产物 `/opt/bodhi-venv/bin/python3 tools/ontology-compiler/compile.py compile --diff`；② 重载投影 `bash deploy/bootstrap-neo4j.sh` |
| C1 | **无来源实例页** | 实例页 `source_refs` 为空（`index`/`summary`/`ontology:*` 豁免） | high | ✅ 清理（P2） |
| C2 | **来源文档已删/不存在** | `source_refs` 指向 `knowledges.deleted_at` 非空或行不存在（复用 `ke_docs.doc_index`） | high | ✅ 清理（复用 `ke_docs`） |
| C3 | 正文称有来源但无溯源 | 非实例页正文含 `（来源：`/`<sources>` 而 `source_refs` 空 | low | ❌ 上游页，不动 |
| C4 | 图侧实例无溯源 | Neo4j `BodhiInstance` 的 `knowledge_id`/`source_doc` 全空（当前部署为 0） | medium | ❌ 仅报数 |
| D1 | 同语义多页 | 同 `page_type` + 归一化标题重复 | medium | ❌ 人工合并 |
| D2 | 软删残留并存 | 同 slug 既有软删旧行又有活页（历史撞主键根因） | medium | ✅ 硬删旧行（P2） |
| D3 | 孤儿版本快照 | `wiki_page_revisions` 有、`wiki_pages` 无 | low | ✅ 清理（P2） |

> 边界：B3/A4 与 `docs/bodhi-reasoning.md` 的 SHACL 规则判定**同源**（都读 `ke_ontology` 闭包）。
> 本工具只做**数据一致性/异常**；业务规则推导归 `reason.py`（下一话题），避免两套规则打架。

## 3. 契约

```jsonc
// MCP 工具（服务 bodhi_ontology）
{"name":"audit_scan","arguments":{"kb_id":"<uuid>","scope":"all|wiki|model|source|dupes","max_findings":50}}
// → { kb_id, scope, generated_at, summary{pages,instance_pages,findings,by_severity,checks,model,…},
//     totals{每个检查的完整计数}, findings[{check,severity,subject,detail,fix_hint,fixable}], data{}, note }

// HTTP（与 /bodhi/* 同端口，经 nginx 反代）
GET /bodhi/audit?kb_id=<uuid>&scope=all&max_findings=50

// CLI
/opt/bodhi-venv/bin/python3 tools/ke-core/ke_audit.py scan <kb_id> [--scope all] [--compact]
```

P2（计划 → 人工确认 → 执行；**只出计划的是工具，执行必须人给**）：

```jsonc
// MCP 工具（只出计划，绝不执行）
{"name":"audit_plan","arguments":{"kb_id":"<uuid>","kinds":"init|all|逗号分隔的 kind","scope":"all"}}
// → { plan_id, kb_id, kinds, actions{...}, edits{...}, current{...}, execute_hint, plan_file }

// HTTP
POST /bodhi/audit/plan   {kb_id, kinds, scope}            // 只读：出计划（落 logs/audit/*.json）
POST /bodhi/audit/apply  {kb_id, plan_id, confirm:true}   // 执行（硬删）；缺 confirm → 400 拒绝

// CLI
ke_audit.py plan  <kb_id> --kinds all|init|...      # 出计划
ke_audit.py init  <kb_id>                           # = plan --kinds init（初始化知识库）
ke_audit.py apply <kb_id> --plan-id <id> --confirm  # 执行（缺 --confirm 直接拒绝）
```

**防漂移**：`plan_id` = 对「kb + kinds + 动作清单」的稳定哈希；**执行时会重新算一遍**，与 `plan_id` 不一致就拒绝
（"数据已变化——请重新 plan 并再次确认"）。计划落 `logs/audit/<kb前8位>-<plan_id>.json`，执行后把 `applied` 写回同一文件留痕。

**kind 一览**（`KIND_HELP`，plan 的 `actions` 里带计数与 slug 清单）：

| kind | 动作 |
|---|---|
| `init_wiki` | **初始化**：硬删该 KB 全部 wiki 页 + 版本快照 + 目录 + 问题项，**保留索引页 `index`**（用户口径：初始化后只留文档与索引页） |
| `init_graph` | **初始化**：删 Neo4j 里该 KB 的本体实例节点/边（`DETACH DELETE`，**连边一起删**；当前部署为 0） |
| `no_source_pages` | 无来源的实例页（C1）→ 删；**并级联删掉其它页里指向它的关系行** |
| `deleted_source_pages` | 来源文档已删/不存在的页（C2，引用全删的）→ 删；同样级联删边 |
| `mixed_source_refs` | 多源页摘掉已删文档的引用（页保留） |
| `soft_deleted_rows` | 软删旧行（D2）→ 硬删（含其快照） |
| `orphan_revisions` | 孤儿版本快照（D3）→ 删 |
| `dangling_edges` | 悬空关系行（A1）→ 删该行（快照 + 版本+1） |
| `dup_edges` | 重复/自环关系行（A5）→ 去重（同上） |

**删节点 = 连关系一起删（级联）** —— 计划里以 `actions.cascade_edges` 提前报出「会顺带删掉多少页上的多少行关系」：
- 被删页**自己的出边**随页消失（页没了，正文也没了）；
- **别的页指向它的关系行**由 `cascade_edges` 在同一趟里删掉（否则会留下悬空边 = 用户实测会看到的"剩余节点/断链"）；
- 同一页同时命中「悬空行」和「级联行」时**只重写一次**（快照 + 版本+1 + 重算 links）。

**初始化知识库（`init`）的边界**：清 **wiki + 图谱**、**保留索引页 `index`**；**不动文档**（`knowledges`/`chunks`/`embeddings`）。
若 WeKnora 的定时索引任务之后又写了页（用户观察到的现象），**再跑一次 `init` 计划并确认**即可 —— 不做任何自动清理。

`findings` 最多返回 `max_findings` 条（按严重度排序），**`totals` 始终是完整计数** —— 便于"先看规模，再拉明细"。

## 4. 实测（2026-09-20，只读）

**业务库 `dbc2528f-611b-48da-9a71-d7c93975adb4`**（53 页 / 实例页 36）：

| 检查 | 计数 | 典型样例 |
|---|---|---|
| A1 | 14 | `bmm/assessment/对标质量不高` → `bmm/goal/维护完整的企业数据资产统一数据标准`（该页不存在；同义页 slugs 不一致） |
| A2 | 8 | `bmm/goal/逐步提升落标率`：in_links 缺 2（`…/存量系统多全部落标的工程量大`、`…/对标率达到97%以上`）多 1 |
| B3 | 2 | `bmm:definedBy` 从 `bmm:Goal`/`bmm:Objective` 连到 `bmm:DesiredResult`，模型允许 `bmm:OrganizationUnit` |
| C2 | 2 | 《系统定义.docx》两个已删版本，仍被 52 页引用（独占 38） |
| D2 | 3 | `bmm/courseofaction/形成闭环管控` 等 3 页有软删旧行 + 活页并存 |

> 合计 29 findings（high 18 / medium 11 / low 0）。**没有** B1/B2/A4/C1 → 现有实例的类型/关系都在模型内、且都有来源 ✓

**本体模型库 `08810cbd-af86-48d1-bd25-3b2c338e3d68`**（141 页）：`scope=model` → **clean**
（首轮把总览页 `ontology/index` 误报为"多余页"，已修：总览页豁免。）

## 5. 误报/边界记录

- `ontology/index`（总览页，`page_type=ontology:Module`）不算 B4 的"多余页" → 已豁免；
- A2（反向边 `in_links` 不一致）已于 **2026-10-05 M3 移除**：关系只走 Neo4j 图，`in_links`/`out_links` 废弃恒空，不再校验、也不再重算；
- A1 的两种成因（目标页从未创建 / 目标页被删）在报告里都表现为"页不存在"，需人看 `detail` 里的 slug 决定删边还是补页；
- 页面数量超过 `page_limit`（默认 5000）时 `summary.truncated=true`，报告只覆盖前 N 页。

## 6. P2 自检证据（2026-09-20，**一次性沙箱 KB**，未碰业务库）

沙箱做法：克隆一份 KB 元数据 + 造样本（活页 / 软删页 / 快照 / 孤儿快照 / 目录 / 带悬空关系行的页）→ 走完整
`plan → apply(confirm=True)` → 核对 → 删沙箱。**业务库始终 53 页未受影响。**

| 用例 | 结果 |
|---|---|
| 拒绝：不带 `confirm` 就 apply | `拒绝执行：必须显式确认（confirm=True / CLI --confirm）` ✅ |
| 拒绝：`plan_id` 不存在 | `找不到计划 deadbeef…` ✅ |
| 拒绝：数据变了再 apply 旧 plan（防漂移） | 出计划 → 人为再加一页 → `apply(旧 plan_id)` → `数据已变化（plan_id 不匹配）——请重新 plan 并再次确认`；沙箱数据**未被改动** ✅ |
| `init_wiki` | 沙箱 2 页/2 快照/1 目录 → **全删**，四张表归 0；之后 `audit` findings=0 ✅ |
| `dangling_edges` | 悬空关系行被删；`version 5→6`、`last_edit_source=bodhi-ops-edit`、**生成新版本快照** ✅；重跑 `audit` 该 A1 消失 |
| ~~`in_links`~~（已移除） | 2026-10-05 M3：关系走图，无此修复项 ✅ |
| `soft_deleted_rows` / `orphan_revisions` | 计划计数正确（在 `init` 之后被执行时已为 0，属预期：init 已一并清掉） ✅ |
| `cascade_edges`（删页连带删边） | 沙箱：B 页有 `[[A]]` 出边、A 被判为"无来源"→ 计划报 `cascade_edges{pages:1, lines:1}`；执行后 **A 已删、B 正文里的指向行消失**、B `version+1` 且 `last_edit_source=bodhi-ops-edit` ✅ |
| `init_wiki` 保留索引页 | 沙箱 init 执行后**只剩 `('index','index')`**；目录已重建、复检 findings=0 ✅ |
| 业务库未被波及 | 全程 `pages=53`（沙箱用独立 KB id，用完即删） ✅ |
| MCP `audit_plan` | `tools/list` 有该工具；调用返回 `plan_id` 与 actions（**不执行**） ✅ |
| HTTP `/bodhi/audit/apply` 无 confirm | `400 {"error":"拒绝执行：必须显式确认…"}` ✅ |

> 结论：**改数据的路径（整库初始化 / 删页级联删边 / 正文改写）都端到端验证过**，且"没有人工确认就动不了"。

## 6.1 「知识运维」智能体（P3，已注册）

| 项 | 值 |
|---|---|
| id / 名称 | `bodhi-kb-ops` / 知识运维 · 一致性巡检与清理（`is_builtin=false`，用户可改可删） |
| 模式 | `agent_mode=smart-reasoning`、`agent_type=custom`、`temperature=0.1`、`max_iterations=12` |
| 工具（7） | `grep_chunks` / `list_knowledge_chunks` / `get_document_info` / `wiki_search` / `wiki_read_page` + **`mcp_bodhi_ontology_audit_scan`** / **`mcp_bodhi_ontology_audit_plan`**（**无** `wiki_write_page`、**无** `extract_and_save`） |
| MCP | `mcp_services=[a7c1f0d2-…0001]`、`mcp_selection_mode=all` |
| 知识库 | 业务库 + 本体模型库（两个都能体检） |
| 提示词 | 单一真源：`deploy/weknora-fork/config/agent_system_prompt.yaml` 的 `templates[].id="knowledge_ops_agent"`（2050 字符） |

**为什么提示词里放 `{{knowledge_bases}}`（用户建议，已采纳）**：上游 Go 会把系统提示词里的该变量渲染成
「请看用户消息 `<runtime_context>` 里的 `<bound_knowledge_bases>`」；而那块的每个库都带
`id="<uuid>" name="企业知识" doc_count="N" capabilities="…"` —— 所以 **kb_id 一律取 `id`**，
不会再把名称当 id。同一函数还支持 `{{current_time}}` / `{{language}}` / `{{web_search_status}}`（本提示词都用了）。

**代码侧兜底（防止"假清白报告"）**：`ke_db.resolve_kb_id()` 支持「UUID / 名称（精确或包含）」，
且**未知知识库直接抛错**（`知识库不存在：…；可选：…`）。2026-09-20 用户实测：智能体传了名称，而旧代码
把不认识的值当"空库"→ 给出 `0 页 / 0 findings / 0 源文档` 的**假健康报告**；现在 `audit_scan`/`audit_plan`
都会先解析并在报告里回带 `kb_id`/`kb_name`（供核对），传错会得到明确错误。

重新注册/更新（改提示词后）：
```bash
/opt/bodhi-venv/bin/python3 deploy/weknora-fork/gen_agents.py --only ops        # 生成 SQL
# 落库（免嵌套 wsl：用 ke_db 直接喂 stdin）
/opt/bodhi-venv/bin/python3 - <<'PY'
import pathlib, sys; sys.path.insert(0, 'tools/ke-core'); import ke_db
ke_db.psql(pathlib.Path('deploy/weknora-fork/config/agents.sql').read_text(encoding='utf-8'), stdin=True)
PY
```
> ⚠️ **必须带 `--only ops`**：不带会把 bmm/ea 的**精简提示词**覆盖回 yaml 里的长版（`set_agent_prompt_lean.py` 的成果）。
> 运维智能体 id 特意用 `bodhi-kb-ops`（不在 `bodhi-ontology-%` 前缀里），免得被那个精简脚本误伤。
> 📌 `config/agents.sql` 只是**本次生成**的产物（带 `--only ops` 时只含 ops 行，不会动 bmm/ea）；
> bmm/ea 的提示词归 `set_agent_prompt_lean.py` 管，别拿全量重跑去覆盖它。

## 7. 排障

| 症状 | 处置 |
|---|---|
| 体检报告**全 0**但界面上明明有页/节点 | `kb_id` 传错（最常见：把**名称**当 id）。现在会直接回 `知识库不存在：…；可选：…`，且报告里带 `kb_id`/`kb_name` 可核对；正确 id 见 `<bound_knowledge_bases>` |
| `audit_scan` 回 `unknown tool` | 服务没重启（改过 `server.py`/`ke_audit.py` 必须 `systemctl restart bodhi-mcp`） |
| `model.source` 是 `json: …` | Neo4j 投影不可用 → B1/B2/B3 用 json 兜底判断（会标 `source`） |
| 报告与手工 SQL 不一致 | 先看 `summary.truncated`；再看 `summary.model.classes/relations` 是否与 Neo4j 一致（模型侧变了要 `load`） |
| 数量很大 | 用 `scope` 分片（`wiki`/`model`/`source`/`dupes`）+ `max_findings` 只取明细 |
