"""工具 schema 单一来源（P2 切片 S8a ✓ 从 `server.py` 机械搬运，**内容逐字未改**）

为什么单独放：schema 是**纯声明数据**（零运行时依赖 ✓），却有 500+ 行；
搬出来后 `server.py` 只留 `def tool_definitions(): return TOOL_SCHEMAS` ✓
"""
from __future__ import annotations

import os
import sys

_CORE = os.path.abspath(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                     "..", "ke-core"))
if _CORE not in sys.path:
    sys.path.insert(0, _CORE)

# schema 里有两处**运行期引用**（枚举清单必须与实现同源 ✓，不能写死字符串 ✗）：
#   · `ke_audit.SCOPES`（audit_scan 的 scope 取值）
#   · `ke_plan`（计划的阶段/动作枚举）
import ke_audit  # noqa: E402
import ke_plan  # noqa: E402

TOOL_SCHEMAS = [
        {
            "name": "ontology_types",
            "description": ("返回某本体模型的可用类与关系（含 domain/range），供核对类型是否合法。"
                            "**可用 `focus` / `classes` / `relations` 收窄**（技能按对话缩范围："
                            "如 focus=\"Service\" 只看服务相关）。返回里 `class_attributes` 给出**每个类允许的"
                            "数据属性**（本体声明，含祖先）—— 落库时 `nodes[].attributes` 与 "
                            "`edges[].properties` 的键就按它填（未声明的键会被回报为 violations，不拦写入）。"),
            "inputSchema": {"type": "object", "properties": {
                "model": {"type": "string", "description": "本体模型 key（bmm / ea / ea-service …）"},
                "focus": {"type": "string",
                          "description": "关键词收窄（匹配类/关系的中文标签或 prefixed，大小写不敏感）"},
                "classes": {"type": "array", "items": {"type": "string"},
                            "description": "显式白名单（prefixed），给了就以它为准"},
                "relations": {"type": "array", "items": {"type": "string"},
                              "description": "显式白名单（prefixed）"},
                "with_attributes": {"type": "boolean",
                                    "description": "是否附带 class_attributes（默认 true）"},
            }, "required": ["model"]},
        },
        {
            "name": "skills",
            "description": ("**技能目录 / 技能指令**（本服务的技能库在 `skills/<id>/SKILL.md`，单一来源）。"
                            "不传 `skill` → 返回目录（id / name / when / models / stages / tools）；"
                            "传 `skill` → 返回该技能**完整指令**，并附带它需要的**本体面**"
                            "（按技能声明的 scope 收窄好的类 / 关系 / 每个类的数据属性）。"
                            "用法：先看目录判断用哪个技能，再取全文照做 —— **不要凭记忆猜步骤**。"),
            "inputSchema": {"type": "object", "properties": {
                "skill": {"type": "string",
                          "description": "技能 id（domain_modeling / ea_overview_design / "
                                         "service_detailed_design …）；留空 = 只看目录"},
                "model": {"type": "string",
                          "description": "可选：本次想用的本体模型（覆盖技能默认值，如 bmm）"},
            }},
        },
        {
            "name": "job_status",
            "description": ("查询**异步任务回执**（当前用于 `service_overview(apply=true)` 的总览页刷新，"
                            "渲染 12 个服务约 1-3 分钟）。不传 `job_id` → 列出最近任务概览。"
                            "整篇抽取工具已于 2026-09-22 退役：建模改用 `domain_modeling` 技能的分批"
                            "流程（`doc_outline` → `save_knowledge(stage=\"graph\", session={...})` "
                            "→ `extract_state`）。"),
            "inputSchema": {"type": "object", "properties": {
                "job_id": {"type": "string", "description": "异步受理回执里的 job_id（留空 = 列出最近任务）"},
            }},
        },
        {
            "name": "kb_export",
            "description": ("**运维 · 导出知识库**：把指定知识库导出成文件（PG 页/目录/版本快照 + Neo4j 节点/边），"
                            "落在服务端 `exports/` 并打成 zip；回执给 **download** 链接（下载到本地）。"
                            "用于**本机 → 内网**迁移、建测试库、离线留档。"),
            "inputSchema": {"type": "object",
                            "properties": {"kb_id": {"type": "string"},
                                           "out_dir": {"type": "string", "description": "可选：导出目录（默认 exports/kb-<id前8位>-时间戳）"},
                                           "make_zip": {"type": "boolean", "description": "默认 true 打 zip"}},
                            "required": ["kb_id"]},
        },
        {
            "name": "audit_scan",
            "description": ("知识运维**只读体检**：比对 wiki ↔ 本体图谱 ↔ 本体模型，并找出异常数据。"
                            "检查项：悬空出边(A1)、in_links 不一致(A2)、类型/元数据矛盾(A3/A6)、"
                            "重复或自环关系行(A5)、类型不在模型(B1)、关系不在模型(B2)、"
                            "range 违反(B3)、模型库页与投影不一致(B4)、"
                            "**无来源文档的实例页(C1)**、**来源文档已删/不存在(C2)**、"
                            "同语义多页(D1)、软删残留(D2)、孤儿快照(D3)、"
                            "**近似重复知识(S1，3-gram 相似度)**、**领域上下文之外的同名页(S2)**。"
                            "**建议每次 `save_knowledge` 落库后跑一次**（`scope=all` 已含 S1/S2）："
                            "落库已**不做相似度合并**，相似知识的裁决统一在巡检做（并入/补别名/跨库映射）。"
                            "返回 summary/totals/findings（每条含 severity、subject、detail、fix_hint）。"
                            "**本工具不写任何数据**；清理需另行确认（P2）。"),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "kb_id": {"type": "string", "description": "知识库 UUID（单库巡检）"},
                    "kb_ids": {"type": "array", "items": {"type": "string"},
                               "description": "多库巡检：会话绑定的库 id 清单（每库一份独立报告）"},
                    "scope": {"type": "string", "enum": list(ke_audit.SCOPES),
                              "description": "all(默认) / wiki(A) / model(B) / source(C) / dupes(D) / similar(S1/S2)"},
                    "max_findings": {"type": "integer",
                                     "description": "最多返回多少条明细，默认 50（计数始终完整）"},
                },
                "required": ["kb_id"],
            },
        },
        {
            "name": "audit_plan",
            "description": ("生成**只读**的清理/修复计划（**不写任何数据**）：列出要删/要改的清单与理由，"
                            "并给出 plan_id 与执行命令。kinds：`init`（初始化＝清空该 KB 的 wiki 与图谱）、"
                            "`all`（清理异常+修一致性问题：悬空关系行、in_links、重复关系行、无来源页、"
                            "来源已删页、软删残留、孤儿快照）、或具体 kind。"
                            "**执行必须由人工确认后走 CLI/HTTP（apply --confirm）** —— 本工具绝不执行。"),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "kb_id": {"type": "string", "description": "知识库 UUID"},
                    "kinds": {"type": "string",
                              "description": "init / all / 逗号分隔的具体 kind（默认 all）"},
                    "scope": {"type": "string", "enum": list(ke_audit.SCOPES),
                              "description": "顺带带出的现状快照范围，默认 all"},
                },
                "required": ["kb_id"],
            },
        },
        {
            "name": "save_knowledge",
            "description": ("**设计落库（不调用大模型）**：把概要设计内容写成 wiki 页与本体图谱。两段式："
                            "`stage=report` 先落**概要设计报告 md**（整篇写成索引页/父页）；"
                            "`stage=graph` 再把报告**细分**出的节点/关系落成各自 wiki 页 + 本体关系"
                            "（每个 IT 服务/应用系统一页，挂在报告页下）。"
                            "**落库必须显式 `mode=\"apply\"`**（默认 dry_run 只算不写，回执 `applied=false`）。"
                            "再跑同一份内容 = **更新同一页**（报告页按标题/上游自动复用 slug，正文整体替换；"
                            "节点页 slug 命中原页即合并，不会重复建页）。"
                            "页面 slug 规则：`<类型所属模块>/<类小写>/<名称>`，故 MCP 服务页在 "
                            "`ea/mcpservice/<名称>`（历史模块 `bmm-ea-ext/…` 已废弃，按旧前缀检索必然 0 结果）。"
                            "落库与抽取路径**共用同一份实现**：相似度两阈值（高→合并现有页、低→新增页、"
                            "中间→生成「待确认合并」页交人工裁决）、版本快照、溯源、目录重建都一致；"
                            "**每次写页都会重算该页出边并重建全库 `in_links`**，所以关系面板/入边即时可见。"
                            "合规校验：类必须在本体里、关系必须满足 domain/range，违规进 violations 不入库。"
                            "**新建「应用/系统」节点必须人工确认**：默认 dry_run 只给清单，"
                            "确认后 mode=apply 且带 confirmed_new_applications 重跑（幂等，同标题只更新）。"),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "kb_id": {"type": "string", "description": "目标知识库 UUID 或精确库名（**写库前必须唯一确定**）"},
                    "kb_ids": {"type": "array", "items": {"type": "string"},
                               "description": ("会话里绑定的知识库 id 清单（`<bound_knowledge_bases>` 原样传）。"
                                               "**写操作**：>1 个且未传 kb_id 会被拒（要求用户指明）；"
                                               "恰好 1 个时等价于显式指定。**读操作**：多库一起查")},
                    "confirm_kb_match": {"type": "boolean",
                                        "description": ("模糊匹配（uuid 前缀 / 名称包含）命中唯一库时的**二次确认**："
                                                        "true 才允许写；精确匹配（完整 uuid / 精确库名）不需要")},
                    "session_no": {
                        "type": "string",
                        "description": ("**本会话编号**（WeKnora 会话 id，uuid 形态）。**落库（apply）必传**："
                                        "服务端拿它去 `sessions` 表取「**会话选择的智能体**」"
                                        "（`agent_config.agent_id` → `custom_agents.name`）作为 `agentName`、"
                                        "取租户名作为 `tenantName`，并给本次**所有**页自动挂 `bmm:sourceSession`。"
                                        "**取不到会话身份 → 拒绝写库**（回执 `need_session=true` + `reason`）——"
                                        "会话编号不由你编造，必须是运行时给你的编号（或写在会话页属性 `bmm:sessionNo`）。")},
                    "context": {"type": "string",
                                "enum": ["domain_modeling", "ea_overview_design", "service_detailed_design"],
                                "description": ("本技能上下文：决定文案（缺 source_text 时写哪句、报告页标题/分类）。"
                                                "不传时按旧口径（概要设计）渲染")},
                    "stage": {"type": "string", "enum": ["report", "graph"],
                              "description": "report=先落报告页（返回 slug）；graph=再落细分节点与关系"},
                    "model": {"type": "string", "description": "本体模块 key：`bmm`（默认，BMM/EA 业务本体）或 `agent`（智能体开发本体：技能/工具/MCP/评估）；`ea` 已退役（模块已删，传它会报错）"},
                    "report": {
                        "type": "object",
                        "description": ("报告页与溯源信息：{title, content_md(报告全文), slug(可选，"
                                        "graph 段要用它挂父页), upstream(上游页 slug 数组), "
                                        "source_document_id(需求文档 id，方案 C 溯源), "
                                        "source_document_title, page_type(默认 summary)}"),
                        "properties": {"title": {"type": "string"}, "content_md": {"type": "string"},
                                       "slug": {"type": "string"},
                                       "upstream": {"type": "array", "items": {"type": "string"}},
                                       "source_document_id": {"type": "string"},
                                       "source_document_title": {"type": "string"}},
                    },
                    "nodes": {
                        "type": "array",
                        "description": ("细分节点：[{name, type(本体类，如 ea:Service / ea:APIService / "
                                        "ea:MCPService / ea:SkillService / bmm:SubSystem / bmm:MainSystem), "
                                        "purpose(用途), inputs[], outputs[], "
                                        "assertions[{id,kind:'N|E',assertion}], attributes{数据属性:值}, "
                                        "definition, description?, aliases?, retag?}]；"
                                        "**`attributes` 的键 = 本体为该类（或祖先）声明的数据属性**"
                                        "（见 `ontology_types(model).class_attributes`；未声明的键会被回报为 "
                                        "violations(kind=attribute)，不拦写入，但要长期用就补本体）。"
                                        "服务页会自动渲染 用途/输入输出/设计规范(ASSERTION)/属性/被引用(入边)。"
                                        "**类型变更（如 API 服务→MCP 服务）用 `retag: true`**：命中同根类的既有页时"
                                        "合并并改类型，不会新建第二份"),
                        "items": {"type": "object"},
                    },
                    "edges": {
                        "type": "array",
                        "description": ("关系：[{source, type(本体对象属性，如 ea:applicationProvidesService / "
                                        "ea:stepUsesService / easvc:operationOperatesOnAttribute), target, "
                                        "label?, properties?(边限定属性，键 = **源类**声明的数据属性)}]；"
                                        "`properties` 会渲染成页面里独立的 `## 关系限定（边属性）` 小节"
                                        "（不改 `## 本体关系` 的行语法，反向边/审计不受影响）；"
                                        "**改设计要减边时给 `retract: true`**（撤回该关系：删掉源页里指向 "
                                        "target 的那一行 + 同键的关系限定行，回执列在 `retract`）；"
                                        "target 可以是本次节点名，也可以是库内已有页标题（会解析成 slug）"),
                        "items": {"type": "object"},
                    },
                    "mode": {"type": "string", "enum": ["dry_run", "apply"],
                             "description": ("**默认 dry_run = 只算不写库**（回执里 `applied=false`、"
                                             "`dry_run=true`、`write_note` 会提示）。**要真正落库必须显式传 "
                                             "`apply`**；写完用回执 `page_versions`（含 before 版本）或巡检复核。")},
                    "confirmed_new_applications": {
                        "type": "array", "items": {"type": "string"},
                        "description": "**人工已确认**可新建的「应用/系统」节点名称清单",
                    },
                    "session": {
                        "type": "object",
                        "description": ("**领域建模 v2 的分批会话**（可选；只在按批次建模时传）："
                                        "{knowledge_id(本篇文档), round_no(第几轮), budget_tokens(本轮 token 上限),"
                                        " cursor(本批起始单元), next_cursor(回执里 doc_outline 给的下一批游标),"
                                        " doc_title}。带上它时：回执会给每个**新建页**编会话编号 "
                                        "`created[].no`，并把页面索引/轮次/游标写进会话状态（`extract_state` 可查）。"
                                        "dry_run 只预览编号，不落状态。"),
                        "properties": {"knowledge_id": {"type": "string"}, "round_no": {"type": "integer"},
                                       "budget_tokens": {"type": "integer"}, "cursor": {"type": "integer"},
                                       "next_cursor": {"type": "integer"}, "doc_title": {"type": "string"}},
                    },
                },
                "required": ["kb_id", "stage"],
            },
        },
        {
            "name": "import_probe",
            "description": ("**结构化批量建模·探表（只读）**：读 Excel/CSV 的**结构**——sheet 名、表头、行数、抽样 3 行、"
                            "**重复表头**、**列前缀**（判断一张表里有几个实体）、疑似主键列。"
                            "用法：先 `import_probe(file=…, sheet=…)`，和用户对齐「哪些列是哪个类/属性/外键」后，"
                            "再逐个目标 `import_plan` → `import_apply`。零依赖（xlsx 走标准库 zip+xml）。"),
            "inputSchema": {"type": "object", "properties": {
                "file": {"type": "string", "description": "文件路径（宿主机可读路径）"},
                "sheet": {"type": "string", "description": "可选：只探某个 sheet"},
                "sample": {"type": "integer", "description": "抽样行数（默认 3）"}},
                "required": ["file"]},
        },
        {
            "name": "import_plan",
            "description": ("**结构化批量建模·计划（只读）**：**一次只处理一个类（含其数据属性）或一条关系**。"
                            "`kind=class` 给 `target`（本体类）+ `key_column`（行→slug 的键列）+ `mapping`（列→数据属性）；"
                            "`kind=relation` 给 `target`（本体关系）+ `source_key_column`/`target_key_column` + "
                            "`source_class`/`target_class`。回执给影响面（create/update/duplicate_keys/empty_keys/dangling）、"
                            "页样例、待确认问题与 `ticket`。工具**只做确定性校验**（类/属性是否已声明、键唯一、外键命中），"
                            "语义由你（智能体）从用户描述得出。"),
            "inputSchema": {"type": "object", "properties": {
                "kind": {"type": "string", "enum": ["class", "relation"]},
                "target": {"type": "string", "description": "类（bmm:MainSystem）或关系（bmm:mainSystemContainsSubSystem）"},
                "file": {"type": "string"},
                "kb_id": {"type": "string", "description": "目标知识库（uuid 或精确库名）"},
                "sheet": {"type": "string"},
                "key_column": {"type": "string"},
                "mapping": {"type": "object", "description": "列名 → 数据属性（systemNo 或 bmm:systemNo）"},
                "title": {"type": "string", "description": "标题模板，如 {主系统英文名称}（{主系统系统编号}）"},
                "aliases": {"type": "array", "items": {"type": "string"}},
                "source_key_column": {"type": "string"},
                "target_key_column": {"type": "string"},
                "source_class": {"type": "string"},
                "target_class": {"type": "string"},
                "unknown_to_description": {"type": "boolean", "description": "未映射列是否聚合进 description（默认 true；部门这类实体建议 false）"},
                "enums": {"type": "object", "description": "枚举列 → 关系（不是数据属性）：{\"级别\":{\"relation\":\"bmm:hasEnforcementLevel\",\"values\":{\"强制\":\"bmm:Strict\",\"推荐\":\"bmm:Advisory\"}}}；工具只校验关系对该类是否合法，取值映射由技能从用户口径给"},
                "prune": {"type": "boolean", "description": "重跑时软删本批多出来的页"},
                "batch_id": {"type": "string", "description": "同一份文件多次目标共用一个 batch（便于 import_state 收敛）"},
                "limit": {"type": "integer"}},
                "required": ["kind", "target", "file", "kb_id", "key_column"]},
        },
        {
            "name": "import_apply",
            "description": ("**结构化批量建模·执行（写）**：按 `import_plan` 给的 `ticket` 落库，**只写那一个目标**；"
                            "内部按 500 行/事务分批；幂等（内容没变的页**零写入**，变化走快照 + version+1）；"
                            "关系批次把关系行写进 **domain 侧页**的「## 本体关系」（关系走 Neo4j 图，出入链已废弃）。"
                            "写权限：对该库有写权限（属主 / kb_shares 的 editor|writer|admin）。"),
            "inputSchema": {"type": "object", "properties": {
                "ticket": {"type": "string"},
                "actor": {"type": "string"},
                "prune": {"type": "boolean"}},
                "required": ["ticket"]},
        },
        {
            "name": "audit_purge",
            "description": ("**巡检清理·一步硬删（写）**：只要调用者对该知识库**有写权限**就执行，不需要后台 plan/confirm。"
                            "两种用法：① 给 `slugs` → 只硬删这些页（含快照/关系行清理）；"
                            "② 不给 → 按 `kinds`（all=清理异常+修问题；也可 no_source_pages/deleted_source_pages/"
                            "mixed_source_refs/soft_deleted_rows/orphan_revisions/**orphan_graph_nodes** 等）生成计划并立即执行。"
                            "`orphan_graph_nodes` = **图库对账**（全库）：删掉「对应知识库已删除/软删」的孤儿实例节点"
                            "（WeKnora 删库只删 PG、不删图）；已含在 `kinds=all` 里。"
                            "`dry_run=true` 只看影响面。**删除不可逆**，删前先 `audit_scan` 把 target 念给用户确认。"),
            "inputSchema": {"type": "object", "properties": {
                "kb_id": {"type": "string"},
                "kinds": {"type": "string", "description": "all（默认）或逗号分隔的具体 kind"},
                "slugs": {"type": "array", "items": {"type": "string"}},
                "dry_run": {"type": "boolean"}},
                "required": ["kb_id"]},
        },
                {
            "name": "rules_resolve",
            "description": ("**按步骤解析规则（只读，推荐用这个）**：任务由**步骤**组成，每步自选解析方式 —— "
                            "`direct:<slug>[,<slug>…]` 直接指定；`policy:<策略名/slug片段>` 经策略**递归解析**"
                            "（`isDerivedFrom` 入边 + `includesBusinessPolicy` 出边，支持策略包含策略）；"
                            "`ask` **只回可选范围**（策略清单）由用户定；`task:<任务>` **已废弃** ✗（任务边 `bpmn:taskExecutesRule` 已删除 ✓，请用 policy/direct/ask ✓）；"
                            "`path:` 预留。回执统一：`resolved_by` + `rules[…]`（含 `needs_extract` / "
                            "`required_fields` / `source` / `strategies`）+ `hash`（可断言）。"
                            "⚠️ 取规则请优先用本工具：它不假设\"任务=一组规则\"，而是**按步骤**解析。"),
            "inputSchema": {"type": "object", "properties": {
                "kb_id": {"type": "string", "description": "领域库（规则/策略/任务都在这里）"},
                "spec": {"type": "string", "description": "direct:<slug,…> | policy:<策略> | ask | task:<任务> | path:…"},
                "limit": {"type": "integer"}},
                "required": ["kb_id", "spec"]},
        },
        {
            "name": "extract_candidates",
            "description": ("**候选抽取（只定位，不判定）**：按 `ke_plan` 的必需事实，从**源文档**（WeKnora `chunks` "
                            "直读 ✓）里定位**逐字行 + 章节位置**，返回 `candidates[]`（每项 `{line, locator}` ✓）。"
                            "⚠️ 返回的 `status` **一律 `unknown`** ✗ —— 判读是你的工作（见技能 `knowledge_extract`）："
                            "文档里有 ⇒ `present` ✓；文档没有但其他库/系统有 ⇒ `absent_verified`（文档遗漏 ✓ 是问题 ✓）；"
                            "哪都没有 ⇒ `unavailable` ✓。**不得**把章节标题命中当成事实命中 ✗（假断言 ✗）。"),
            "inputSchema": {"type": "object", "properties": {
                "kb_id": {"type": "string", "description": "源文档所在库（任务知识库）"},
                "knowledge_id": {"type": "string", "description": "文档 UUID / 标题 / 文件名（如 样例1-…docx）"},
                "domain_kb": {"type": "string", "description": "规则所在库（领域模型）"},
                "task": {"type": "string", "description": "任务（默认 逐项评审）"},
                "other_kbs": {"type": "array", "description": "文档缺时查哪些库（可选）"},
                "limit_items": {"type": "integer"}},
                "required": ["kb_id", "knowledge_id"]},
        },
        {
            "name": "evidence_score",
            "description": ("**证据/结论可信度评分（只读，纯函数）**：输入一条规则的 `facts`（每条事实带 "
                            "`status`(present/absent_verified/unavailable) / `confidence` / `source_type` / "
                            "`required` / `quote` / `locator`）与 `checks`（fact_check 的 verdict），"
                            "输出 `{score, level, gate, parts, reasons}` ✓。"
                            "`gate=insufficient` ⇒ 结论只能是**无法判定** ✓（不得写不符合 ✗）。"
                            "`rule_judge` 必须调用本工具产出可信度 ✓，不要自己估 ✗。"),
            "inputSchema": {"type": "object", "properties": {
                "facts": {"type": "array", "description": "该规则 required 的事实列表（见上）"},
                "checks": {"type": "array", "description": "fact_check 结果：[{verdict: supported|not_supported|insufficient}]"},
                "threshold": {"type": "number", "description": "阈值，默认 0.60"}},
                "required": ["facts"]},
        },
        {
            "name": "knowledge_lookup",
            "description": ("**统一检索入口（只读，三种模式）**："
                            "`mode=\"rules\"` 取规则契约（收 `spec`：direct:/policy:/ask/task:，**按步骤解析** ✓，"
                            "等价 `rules_resolve`）；`mode=\"keyword\"` 按关键词找知识（`query`，"
                            "**图优先 → PG 收窄融合** ✓，每条命中带 `source`：graph / graph+pg / pg）；"
                            "`mode=\"path\"` 沿关系多跳（`start`=slug，`depth`≤4 ✓，回节点链 + 关系链）。"
                            "⚠️ 找知识先用本工具：比\"自己猜库再查\"稳 ✓，且 `source=pg` 占比高即说明图缺数据 ✗。"),
            "inputSchema": {"type": "object", "properties": {
                "kb_id": {"type": "string"},
                "mode": {"type": "string", "description": "rules | keyword | path（默认 rules）"},
                "query": {"type": "string", "description": "keyword 模式的关键词"},
                "spec": {"type": "string", "description": "rules 模式的 spec（direct:/policy:/ask/task:）"},
                "start": {"type": "string", "description": "path 模式的起始 slug"},
                "depth": {"type": "integer", "description": "path 模式深度（1-4，默认 2）"},
                "limit": {"type": "integer"}},
                "required": ["kb_id"]},
        },
        {
            "name": "page_attributes_set",
            "description": ("**改一页的数据属性（方案 D，写）**：entity 模式的知识页 —— 正文是**只读渲染**、"
                            "数据属性是**事实源**。给 `patch`（键=本体数据属性，可带/不带模块前缀；"
                            "空串 = 删键），本工具**三处同写**：`page_metadata.ontology.attributes` + "
                            "图节点属性 + 正文属性段（**标题式**重渲染，不截断）。"
                            "只接受 **entity 渲染模式**的页；报告/文档类（document）**以正文为主**会被拒"
                            "（请直接改正文）。键必须是该类本体面声明过的数据属性，否则拒并列出可用键。"
                            "幂等；写前快照 + `version+1`（可回退）；**默认 `dry_run=true`**（只回 diff）。"),
            "inputSchema": {"type": "object", "properties": {
                "kb_id": {"type": "string"},
                "slug": {"type": "string"},
                "patch": {"type": "object",
                          "description": "要改的数据属性 `{键: 值}`（键可带本体前缀）；值为空串 = 删键"},
                "actor": {"type": "string", "description": "操作者（记进回执；面板/人/智能体）"},
                "dry_run": {"type": "boolean", "description": "默认 true：只回 diff，不写库"}},
                "required": ["kb_id", "slug", "patch"]},
        },
                {
            "name": "reference_lookup",
            "description": ("**文档评审·取参考规范（只读）**：按规则里的「参考规范」（`bmm:ruleReference`，URL 或规范名）"
                            "找**规范内容**：① 知识库文档/附件同名 → 回 `knowledge_id`（正文用 `doc_outline` 取）；"
                            "② 知识库 wiki 页标题/slug 命中 → 直接回正文片段；③ `allow_fetch=true` 且是 URL → 抓取并剥 HTML"
                            "（内网可能不可达，**默认关**）。都找不到 → `found=false` + `note`：按技能口径标"
                            "「参考规范不可得」，**只按规则原文判定，不要编造规范内容**。"),
            "inputSchema": {"type": "object", "properties": {
                "kb_id": {"type": "string"},
                "reference": {"type": "string", "description": "规则里的参考规范（URL / 规范名 / 文件名）"},
                "allow_fetch": {"type": "boolean"},
                "max_chars": {"type": "integer"}},
                "required": ["reference"]},
        },
        {
            "name": "image_extract",
            "description": ("**多模态识别·取图（只读）**：取文档**内嵌图**——WeKnora 把 docx/pptx 的图抽成正文里的 "
                            "markdown 引用 `![alt](resource://id)`（存于 `chunks.content`）。本工具把它取出来交给"
                            "**会话多模态模型**识别；识别按技能 `multimodal_extraction` 的**契约 v0 + 门禁 1–4** 输出"
                            "（nodes/edges/annotations/ambiguity/knowledgeGaps/todo，每条带 bbox/evidence+confidence），"
                            "再经 `save_knowledge` 落库（务必带 session_no）。"),
            "inputSchema": {"type": "object", "properties": {
                "kb_id": {"type": "string"},
                "knowledge_id": {"type": "string", "description": "文档 id 或标题（含匹配）"},
                "figure_no": {"type": "integer", "description": "只要第几张图（缺省全部）"}}},
        },
        {
            "name": "doc_outline",
            "description": ("**领域建模 v2 的入口**：按切片**父子关系**把一个文档组织成"
                            "\"大小合适\"的上下文批次（父块正文 + 子块用于定位），把"
                            "**一次交互的输入 token 上限**（`budget_tokens`）当会话参数。"
                            "用法：先 `doc_outline(kb_id, knowledge_id, budget_tokens)` 拿**本批正文** → "
                            "只从本批抽节点/关系 → `save_knowledge(stage=\"graph\", mode=\"apply\", session={...})` "
                            "落库 → 用回执的 `session.next_cursor` 取下一批，直到 `done=true`。"
                            "**超时/被截断就把 budget_tokens 调小**（8000→4000→2000）重跑本工具。"
                            "`with_text=false` 只要地图（标题/规模）。"),
            "inputSchema": {"type": "object", "properties": {
                "kb_id": {"type": "string", "description": "知识库 UUID 或名称"},
                "knowledge_id": {"type": "string", "description": "文档 id 或标题（本篇要建模的文档）"},
                "budget_tokens": {"type": "integer",
                                  "description": "本批上下文 token 上限（会话参数；默认取 BODHI_ROUND_BUDGET_TOKENS=8000）"},
                "cursor": {"type": "integer", "description": "从第几个上下文单元开始（上一批的 next_cursor）"},
                "batches": {"type": "integer", "description": "本次取几批（默认 1）"},
                "with_text": {"type": "boolean", "description": "是否返回本批正文（默认 true）"},
            }, "required": ["kb_id", "knowledge_id"]},
        },
        {
            "name": "extract_state",
            "description": ("**领域建模会话的页索引与进度**：每轮落库的页面**名称 + 会话编号**"
                            "（`pages: [{no, title, slug, type, round_no}]`）、已做轮次、游标、"
                            "待确认候选关联。用途：上下文里不必复述全文 —— 引用编号/slug 即可；"
                            "跨会话续跑也先调它对齐进度。`action=\"reset\"` 清空该文档的会话状态。"),
            "inputSchema": {"type": "object", "properties": {
                "kb_id": {"type": "string", "description": "知识库 UUID 或名称"},
                "knowledge_id": {"type": "string", "description": "文档 id/标题；留空 = 该库全部文档会话"},
                "action": {"type": "string", "enum": ["get", "reset"], "description": "默认 get"},
            }, "required": ["kb_id"]},
        },
        {
            "name": "retag_preview",
            "description": ("**改本体类型 · 第一步（只读预览）**：算改类型后的影响面 —— "
                            "① 新 slug（`模块/类/名称`）；② 引用了该页的地方（关系行 / out_links / 正文 / "
                            "`## 溯源` / `page_metadata` / **建模会话状态**）；③ 预计 domain-range 违规；"
                            "④ 需要用户确认的风险项 `required_risks`；⑤ **ticket**（影响面指纹）。"
                            "流程：本工具 → **把影响面与风险念给用户**、等用户明确同意 → "
                            "用**同一 ticket** 调 `retag_apply`。**不要跳过预览直接改类型**。"),
            "inputSchema": {"type": "object", "properties": {
                "kb_id": {"type": "string", "description": "目标知识库（会话绑定多库时必须明确，否则被拒）"},
                "kb_ids": {"type": "array", "items": {"type": "string"},
                           "description": "会话绑定的库清单（见 runtime_context 的 bound_knowledge_bases）"},
                "confirm_kb_match": {"type": "boolean", "description": "kb_id 模糊匹配命中唯一库时的二次确认"},
                "slug": {"type": "string", "description": "要改类型的页 slug（如 ea/step/某步骤）"},
                "new_type": {"type": "string", "description": "目标本体类型（prefixed，如 ea:Activity）"},
            }, "required": ["kb_id", "slug", "new_type"]},
        },
        {
            "name": "retag_apply",
            "description": ("**改本体类型 · 第二步（执行）**：迁移 slug + 改写所有引用（关系行 / out_links / "
                            "正文 / 溯源 / 元数据）+ 改写建模会话状态；页 id 不变，可回滚。"
                            "**必须**带 `ticket`（来自同一影响面的 `retag_preview`）与 `acknowledge_risks`"
                            "（与 preview 返回的 `required_risks` 完全一致）—— 缺一即拒；ticket 不匹配"
                            "（例如引用变了）也会被拒，需要重新 preview。调用前必须已获得用户明确同意。"),
            "inputSchema": {"type": "object", "properties": {
                "kb_id": {"type": "string"},
                "kb_ids": {"type": "array", "items": {"type": "string"}},
                "confirm_kb_match": {"type": "boolean"},
                "slug": {"type": "string"},
                "new_type": {"type": "string"},
                "ticket": {"type": "string", "description": "retag_preview 返回的 ticket（必须原样带回）"},
                "acknowledge_risks": {"type": "array", "items": {"type": "string"},
                                      "description": "用户确认过的风险项（= preview 的 required_risks，如 "
                                                     "['url_break','refs_rewrite','agent_session']）"},
            }, "required": ["kb_id", "slug", "new_type", "ticket", "acknowledge_risks"]},
        },
        {
            "name": "context_scan",
            "description": ("**跨库上下文扫描（只读）**：把「跨库同名 / 同实例」的知识页聚成**一张**候选表 —— "
                            "判据 L1 归一化 `slug` 字面同名（用户口径）+ L2 本体类 + 归一化标题相同（原 F1）；"
                            "每组给出 `signals`（标题/类/定义相似度/关系签名）与**建议结论**"
                            "（`equivalent` 同义 / `distinct` 异义 / `unknown` 需人判）、`required_risks`、`ticket`。"
                            "**同名不等于同义**：本工具只出建议，绝不自动改任何页；"
                            "同义 → 让用户指认「企业共享概念模型」里的概念页；异义 → 登记 ACL 映射（二期两段式 apply）。"
                            "写入路径见 docs/context-mapping-plan.md §7/§13。"),
            "inputSchema": {"type": "object", "properties": {
                "kb_ids": {"type": "array", "items": {"type": "string"},
                           "description": "要一起比较的知识库（省略=全部活着的库；本体库与共享概念库不参与分组）"},
                "limit": {"type": "integer", "description": "最多返回多少条候选（默认 200）"},
                "write": {"type": "boolean", "description": "是否落扫描报告到 state/context_map/（默认 true；只写我们自己的状态目录）"},
            }},
        },
        {
            "name": "context_lookup",
            "description": ("**跨库上下文查询（只读，检索前用）**：给定 `slug`（或 `q` 关键词）→ 返回该页在**各库**的"
                            "同名页、已挂的企业标准概念（`page_metadata.same_as`）、ACL 映射、"
                            "引用它的页（`depended_by`），以及 `warnings`（如 `same_name_no_decision` = "
                            "跨库同名但**没有任何裁决** ← 跨库引用最危险的场景）。"
                            "**跨库引用前必须先查这个**：不得因为 slug 相同就假定同义。"),
            "inputSchema": {"type": "object", "properties": {
                "slug": {"type": "string", "description": "精确 slug（如 ea/businessentity/客户信息）"},
                "q": {"type": "string", "description": "关键词（按 slug/标题模糊找，再展开跨库同名组）"},
                "kb_ids": {"type": "array", "items": {"type": "string"},
                           "description": "会话绑定的库（仅回显/限定）"},
            }, "required": []},
        },
        {
            "name": "context_page",
            "description": ("**跨库上下文渲染（只读）**：领域页 ←（**同名 slug**）→「企业共享概念模型」页 的关系视图。"
                            "返回：① 概念页（企业标准名称/定义 + `## 各领域映射` 表）；② 同名领域页清单（含类/版本）；"
                            "③ ACL 映射；④ **经企业概念转换**的中转关系；⑤ `warnings`"
                            "（`concept_page_missing` / `cross_kb_reference_forbidden` / `same_name_no_decision`）。"
                            "口径：**领域库不写 uuid、不互相引用**；关系统一按 slug 同名查概念库，"
                            "跨域关系只能经概念页转换（docs/context-mapping-plan.md §13.11）。"),
            "inputSchema": {"type": "object", "properties": {
                "slug": {"type": "string", "description": "领域页 slug（与概念页同名）"},
                "q": {"type": "string", "description": "关键词（按 slug/标题找页，再渲染）"},
                "kb_id": {"type": "string", "description": "本库 id（给了才能判定「本页是否跨库引用」）"},
            }},
        },
        {
            "name": "context_concept_apply",
            "description": ("**把某同名组落成「企业共享概念模型」里的概念页（写路径）**：概念页 slug 与领域页**同名**、"
                            "`page_type` = 该组本体类；正文含「标准定义 + 各领域映射表」（**映射关系的事实源**）。"
                            "**必须**：先 `context_scan`/人读预览拿 `ticket`、带 `acknowledge_risks`（= preview 的 "
                            "`required_risks`）、且**对企业共享概念模型库有写权限**（服务端按 `X-Bodhi-Tenant` 判）。"
                            "领域库**零写入**（不动领域页/版本）；概念页版本化可回滚（`context_concept_rollback`）。"),
            "inputSchema": {"type": "object", "properties": {
                "slug": {"type": "string", "description": "组内 slug（与概念页同名）"},
                "ticket": {"type": "string", "description": "预览返回的 ticket（原样带回）"},
                "acknowledge_risks": {"type": "array", "items": {"type": "string"},
                                      "description": "用户确认过的风险（= preview 的 required_risks）"},
                "definition": {"type": "string", "description": "企业标准定义（可空 → 用组内首选页摘要）"},
                "standard_name": {"type": "string", "description": "企业标准名称（可空 → 用标题）"},
                "actor": {"type": "string", "description": "操作者标识（记进概念页元数据与记录）"},
            }, "required": ["slug", "ticket", "acknowledge_risks"]},
        },
        {
            "name": "context_authority",
            "description": ("**权威/副本（只读）**：某跨库同名组的「谁是企业权威、谁是只读副本」分工与影响面 —— "
                            "master（唯一可编辑）、replicas（含是否 `local_drift`/`outdated`）、需要的风险确认与 ticket。"
                            "口径：同义知识**认定一个领域为权威**，其它领域**只读**、只能 `authority_pull` 从权威复制。"),
            "inputSchema": {"type": "object", "properties": {
                "slug": {"type": "string", "description": "同名组的 slug（概念页同名）"},
                "master": {"type": "string", "description": "候选权威库（id/前缀/精确库名）；留空=用概念页已登记的"},
            }, "required": ["slug"]},
        },
        {
            "name": "kb_import",
            "description": ("**运维 · 导入知识库**：把导出的**目录/zip** 导入到指定知识库（或 `new_kb` 现建/复用同名库），"
                            "按 (kb, slug) 先删后插（幂等）；`verify=true` 默认**立刻做 PG + Neo4j 六项一致性比对**"
                            "（页内容/页链/目录/版本/图节点/图边），回执给 `verify_ok`。"),
            "inputSchema": {"type": "object",
                            "properties": {"source": {"type": "string", "description": "导出目录或 zip 的路径/文件名（默认在服务端 exports/ 下找）"},
                                           "kb_id": {"type": "string", "description": "导入到该既有库"},
                                           "new_kb": {"type": "string", "description": "或：新建/复用同名库（与 kb_id 二选一）"},
                                           "keep_ids": {"type": "boolean", "description": "保留原 UUID（跨实例迁移建议 true）"},
                                           "verify": {"type": "boolean", "description": "默认 true：导入后比对"}},
                            "required": ["source"]},
        },
        {
            "name": "retag_rollback",
            "description": ("**回滚一次类型迁移**（运维用）：按 apply 留下的迁移记录 "
                            "`state/retag/<ticket>.json` 恢复 slug / 类型 / 正文 / 引用 / 会话状态；幂等。"),
            "inputSchema": {"type": "object", "properties": {
                "kb_id": {"type": "string"},
                "kb_ids": {"type": "array", "items": {"type": "string"}},
                "confirm_kb_match": {"type": "boolean"},
                "ticket": {"type": "string", "description": "要回滚的那次迁移 ticket"},
            }, "required": ["kb_id", "ticket"]},
        },
        {
            "name": "context_authority_apply",
            "description": ("**权威/副本裁决（写，2026-10-19 恢复 ✓）**：`action=decide` 把某跨库同名组的**权威库**写进企业共享概念页（`master`），其余为副本；`action=pull` 从权威**复制正文**到副本页。**必须先跑 `context_authority` 看影响面并拿到用户确认** ✓。参数：`action`(decide|pull) / `slug` / `master` / `kb` / `ticket` / `acknowledge_risks` / `actor`"),
            "inputSchema": {"type": "object", "properties": {}},
        },
        {
            "name": "context_concept_rollback",
            "description": ("**回滚一次概念页 apply（写，2026-10-19 恢复 ✓）**：按 `state/context_map/history/<ticket>.json` 恢复旧正文或删除新建概念页；幂等。参数：`ticket`"),
            "inputSchema": {"type": "object", "properties": {}},
        },
        {
            "name": "page_copy",
            "description": "复制一页（保留本体类型/数据属性/来源与关系；源页只读不改）。new_slug 缺省 <src>-copy。",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "kb_id": {"type": "string", "description": "目标知识库（写库解析口径同 save_knowledge）"},
                    "src_slug": {"type": "string", "description": "源页 slug"},
                    "new_slug": {"type": "string", "description": "副本 slug（缺省 <src>-copy）"},
                    "new_title": {"type": "string", "description": "副本标题（缺省 <原标题>（副本））"},
                    "copy_relations": {"type": "boolean", "description": "是否复制关系边（默认 true）"},
                },
                "required": ["kb_id", "src_slug"],
            },
        },
        {
            "name": "rule_publish",
            "description": "规则发布：体检+评测双门禁通过才写库；回执给规则集 hash_before/hash_after（hash 变化=发布生效）。默认 dry_run。",
            "inputSchema": {"type": "object", "properties": {"kb_id": {"type": "string"}, "spec": {"type": "string", "description": "规则集标识（如 policy:<策略名> 或 spec 片段）"}, "apply": {"type": "boolean", "description": "true 才真正写库（默认 false=dry_run）"}}, "required": ["kb_id", "spec"]},
        },
    ]
