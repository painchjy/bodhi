# server.py 拆分计划（P2 ✓ 用户口径 2026-10-22）

## 0. 约束（每次切片都必须满足）
1. **行为不变**：`server.py` 里对外的**名字不变**（`from srv.x import 名字`），下游脚本/工具零改动 ✓
2. **三重验证**：`py_compile` → 重启服务 → `active / Result=success / tools=31` → 回归用例 ✓
3. **禁止宽正则批量替换**（历史事故：曾误改 47 处导致服务崩溃循环 ✗）——只用**内容锚点**做精确替换
4. 切片只搬**内聚**的东西；跨模块依赖用 `import` 单向（`srv/*` 不得反向 import `server`）✓
5. **每片回归必须包含 `tests/test_coverage_gaps.py`**（它依赖 `tests/_helpers.py` 解析 schema/dispatcher，
   结构搬家最容易把它打穿 ✗ —— 2026-10-22 实测：S8a 把 schema 搬到 `srv/schemas.py` 后，锚点 `\n]\n`
   失配 ⇒ 5 个用例红，**只有全量回归才照到** ✗）；改完立刻回跑，别等全量。

## 1. 现状
| 项 | 数值 |
|---|---|
| `tools/ontology-mcp/server.py` | **5645 行**（拆分前） |
| 顶层函数 | 100+ 个（协议、工具实现、解析、渲染混在一起） |
| 已有可复用内核 | `tools/ke-core/*`（写库/查库/图谱/巡检/报告…），`server.py` 只应做**协议 + 编排** |

## 2. 切片清单（按风险从低到高）
| # | 切片 | 内容 | 依赖 | 风险 | 状态 |
|---|---|---|---|---|---|
| **S1** | `srv/text.py` | 相似度（`normalize_name`/`_ngrams`/`lexical_similarity`/`embed_texts`/`cosine`）+ `page_id_for` | 仅标准库 | 极低 | **✅ 完成** |
| S2 | `srv/kbresolve.py` | `resolve_kb_id` / `kb_choices` / `resolve_write_kb` / `resolve_knowledge_id` / `get_kb_tenant` | `ke_db` | 低（纯解析） | **✅ 完成** |
| S3 | `srv/state.py` + `srv/constants.py` | `_state_path` / `_empty_state` / `load_domain_state` / `save_domain_state` / `extract_state` / `_iter_states`；常量 `DOMAIN_STATE_DIR`/`DEFAULT_ROUND_BUDGET`/`CHARS_PER_TOKEN` | `srv.kbresolve` | 低 | **✅ 完成** |
| S4 | `srv/outline.py` | `_est_tokens` / `_chunk_units` / `_split_unit` / `doc_outline` | `srv.text`/`srv.constants`/`srv.kbresolve` | 中 | **✅ 完成** |
| S5 | `srv/session.py` | `_IdentCtx`/`_IDENT`/`_session_ident`/`_attach_session`/`_resolve_session_identity`（`_pick_session_no`/`_resolve_session_for`/`adopt_session` 依赖 `IDENTITY_CLASS`+`ke_pages`，留到 S6） | `ke_db` | 中 | **✅ 完成** |
| S6 | `srv/links.py` | `_page_row_by_slug` / `link_candidates` / `list_link_candidates` / `resolve_link_candidate` | `srv.kbresolve`/`srv.state`/`ke_pages` | 中 | **✅ 完成** |
| S7 | `srv/render.py` | `_design_sections` + `SKILL_CONTEXTS` + `_type_label_of` + `context_meta`（纯渲染；`build_new_page` 留 S8） | `srv.text`/`ke_pages`/`ke_ontology` | 低 | **✅ 完成** |
| S8 | `srv/schemas.py`（**S8a ✅**）+ `srv/tools_read.py`（**S8b-1 ✅**）+ `srv/tools_ops.py`（**S8b-2a ✅**）+ 危险工具/`save_knowledge`（S8b-2b 待做） | schema 559 行；只读 5 个；运维 `kb_export`/`kb_import` | `ke_audit`/`ke_context`/`kb_transfer` | 中 | **S8a ✅ S8b-1 ✅ S8b-2a ✅** |
| S9 | `srv/http.py` | HTTP/JSON-RPC 层（`ThreadingHTTPServer` + handler）与 `main()` | 协议 | 中 | 待做 |
| **A1** | `srv/handlers/*` | **工具处理注册表**（`@handle` 登记 + 注册表优先分发，旧 if 链兜底） | `srv.tools_*` | 中 | **A1-0 ✅ A1-1 ✅（7 个只读/运维工具）；A1-2…A1-8 待做** |

### A1 记录（2026-10-24，**结构拆分，工具面不变**）
- 新增 `srv/handlers/__init__.py`：`REGISTRY` + `@handle("<工具名>")` + `dispatch(name, args)`（未登记 ⇒ 返回 `None`）
- `server._call_tool_impl` 在 `_reset_request_cache()` 之后**先查注册表**，未命中继续走原 `if name == …` 链 ⇒ **双轨、零行为变化** ✓
- **A1-1 `srv/handlers/read.py`**：迁移 7 个"纯参数搬运"工具 —— `audit_scan` / `kb_export` / `kb_import` /
  `context_scan` / `context_lookup` / `context_page` / `context_authority`（实现仍在 `srv/tools_read|tools_ops`，
  参数解析**逐字照搬**原分支）；`server.py` 对应分支已删
- `tests/_helpers.py::dispatcher_names()` 改为 **旧 if 链 ∪ `@handle`**（否则结构搬家会让"分发器覆盖"少名字 ✗；
  同时**禁止在 `srv/handlers/**` docstring 里写真实工具名**，否则会被当成已注册工具 ✗）
- 验证：`py_compile` ✓ / 导入冒烟（`tools=36 handlers=7`）✓ / `active+Result=success+8765` ✓ /
  7 个工具**实调用**回执正常（`audit_scan` 仍 0 high/0 medium/0 low；`kb_export` ok；`kb_import` 非法源结构化失败）✓ /
  `test_coverage_gaps` + `test_mcp_tools_read` + `test_lookup_ab` **13 项全绿** ✓
- 后续切片（按域）：`meta`（`ontology_types`/`skills`/`job_status`，依赖 server 内函数，需先抽出到 `srv/`）、
  `extract`（`image_extract`/`extract_*`）、`audit`（`audit_plan`/`audit_purge`）、`context`（`context_concept_*`/
  `context_authority_apply`）、`import_`（`import_plan`/`import_run`）、`ops`（`retag_*`/`clean_alias_noise`/`overview`）、
  `write`（`save_knowledge`/`page_copy`，**最后做**）

#### A1-2（2026-10-24，✅ 完成）
| 模块 | 工具 | 实现来源 |
|---|---|---|
| `srv/handlers/ops.py` | `audit_plan` / `audit_purge` / `audit_kb` / `migrate_article_body` / `clean_alias_noise` | `ke_audit` / `ke_graph` / `ke_pages` |
| `srv/handlers/import_.py` | `import_probe` / `import_plan` / `import_apply` / `import_refresh` / `import_state` / `reference_lookup` | `ke_sheet` / `ke_import` / `ke_review` |

- `server.py` 对应 **11 个 `if name == …` 分支已删**（注册表 7 → **18**，if 链剩 34 个名字）
- 三处"容易搬错"的点已按原样保留：`import_apply.prune` **三态**（缺省 `None` = 沿用票据）、
  `migrate_article_body` / `clean_alias_noise` 的 `kb_id` **空 ⇒ 全部库**、`audit_kb`/`import_state`
  **仍先被 `_RETIRED_TOOLS` 拦截**（回执 `deprecated` 不变 ✓）
- 验证：`py_compile` ✓ / 重启 `active+success+8765` ✓ / 实调用 `audit_plan`（出 plan_id）、`clean_alias_noise`
  （dry_run 扫 2/命中 0）、`migrate_article_body`（dry_run pending 54）、`reference_lookup`（found=false 结构化）✓；
  `test_coverage_gaps` 绿（`tools_without_dispatcher=[]`、`nonretired_no_dispatch=[]`）✓

## 3. 完成判据
- `server.py` ≤ **800 行**（协议 + 注册 + 分发三段）✓
- 所有切片有对应**单测或回归用例**（`tests/` 现有 126 项即为验收基线 ✓）
- `tools/ontology-mcp/srv/` 内**没有** `import server`（无环）✓
- 域词扫描（`tests/test_no_business_semantics.py`）命中数**不增加** ✓

## 4. S1 记录（已完成）
- 抽出 `srv/text.py`（6 个函数，实现逐字未改）
- `server.py` 对应位置改为 `from srv.text import (_ngrams, cosine, embed_texts, lexical_similarity, normalize_name, page_id_for)`
- 验证：`py_compile` ✓ / `active` ✓ / `Result=success` ✓ / `tools=31` ✓ / 回归 `10 pass + 1 xfail(B4)` ✓

## 5. S2 记录（已完成）
- 抽出 `srv/kbresolve.py`（5 个函数：读路径宽松 / 写路径严格，同一处实现 ✓）
- **不搬** `fetch_existing_pages`：它依赖模块常量 `TYPE_PENDING`，搬走会引入反向 import（环）✗ ⇒ 留待 S3 与常量一起搬
- 验证：`py_compile` ✓ / `active` ✓ / `success` ✓ / `tools=31` ✓ / 读写回归 **9 pass + 1 xfail** ✓
- `server.py` 行数变化：**5645 → 5570**（S1 −75，S2 −75；后续 S3-S9 目标 ≤ 800 行）

## 13. S8b-2a 记录（已完成 · 含一次自查修复）
- `EXPORT_DIR` 收进 `srv/constants.py`（HTTP 上传/下载两处仍用同一路径 ✓ 单一来源）
- 抽出 `srv/tools_ops.py`：`kb_export` / `kb_import`（真逻辑在 `kb_transfer`：`cmd_export`/`cmd_import`/`cmd_verify`）
- **自查修复**：替换 `kb_export`/`kb_import` 时把 `def rebuild_wiki_from_graph(...)` 的**函数头一并吃掉** ✗
  ⇒ 立刻贴回（`SMOKE_OK` 与 `py_compile` 双确认后才重启 ✓）。教训：**长块删除时，old_text 的尾部锚点要选"函数体最后一行"，不要选"下一个 def 行"**
- 验证：`SMOKE_OK → COMPILE_OK → active/success/PORT_OK/tools=31` ✓

## 12. S8b-1 记录（已完成）
- 抽出 `srv/tools_read.py`：`audit_scan` + `context_scan` / `context_lookup` / `context_page` / `context_authority`
  （都是**薄封装**：真逻辑在 `ke_audit` / `ke_context` ✓）
- 有意不搬：`job_status`（依赖模块级 `JOBS`，与 `service_overview` 异步刷新共享）、`skills`（依赖 `load_skills()` 缓存）、
  `kb_export`（属运维写侧）⇒ 归 S8b-2
- 新增两条 MCP 验收：`test_audit_scan_smoke` / `test_context_lookup_smoke`（否则只是"搬了没验" ✗）
- 验证：`SMOKE_OK → COMPILE_OK → active/success/PORT_OK/tools=31` ✓

## 11. S8a 记录（已完成）
- 机械搬运（**脚本 + 行号锚点断言**，不是手工贴 559 行）：`tool_definitions()` 的 schema 列表 → `srv/schemas.py`
- `server.py` 只剩 `def tool_definitions(): return TOOL_SCHEMAS`
- **冒烟在重启前抓到一处真问题** ✓：schema 里有 2 处**运行期引用**（`ke_audit.SCOPES`、`ke_plan`）
  ⇒ 补 import（枚举必须与实现**同源**，不能写死 ✗）；冒烟失败 ⇒ 服务**没有被重启**（旧版本继续跑 ✓）
- `tests/_helpers.py` 的 schema 来源同步改为解析 `srv/schemas.py`（`TOOL_SCHEMAS = [` 锚点）
- 验证：`SMOKE_OK → COMPILE_OK → active/success/PORT_OK/tools=31` ✓
- 规模：`server.py` **4944 → 4446**（S8a −498）｜`srv/schemas.py` 579 行

## 10. S7 记录（已完成）
- 抽出 `srv/render.py`：`_design_sections`（wiki 小节渲染）+ `SKILL_CONTEXTS`（技能上下文文案）+ `_type_label_of` + `context_meta`
- 纯渲染、无 IO；`build_new_page`（需要 engine/`element_page_slug`）留到 S8
- 验证：`SMOKE_OK → COMPILE_OK → active/success/PORT_OK/tools=31` ✓
- 回归：read + write + **report_structure**（覆盖 `_type_label_of`/`context_meta` 渲染路径）
- 规模：`server.py` **5092 → 4944**（S7 −148）

## 9. S6 记录（已完成）
- 抽出 `srv/links.py`：`_page_row_by_slug` / `link_candidates` / `list_link_candidates` / `resolve_link_candidate`
- `now_text` 改为直接用 `ke_db.now_text`（不再依赖 `server` 的同名兼容别名 ✓ 无环）
- 验证（新流程）：`SMOKE_OK → COMPILE_OK → active/success/PORT_OK/tools=31` ✓
- 规模：`server.py` **5192 → 5092**（S6 −100）

## 8. S5 记录（已完成）
- 抽出 `srv/session.py`：会话身份解析（`_IdentCtx`/`_IDENT`/`_session_ident`/`_attach_session`/`_resolve_session_identity`）
- 只搬**身份解析**：`_pick_session_no`（依赖 `IDENTITY_CLASS`）、`_resolve_session_for` / `adopt_session`（依赖 `ke_pages` + 状态写入）留到 S6/S7 ✓
- 线程局部 `_IDENT` 仍由 `save_knowledge` 直接赋值（`from srv.session import _IDENT` ⇒ 同一对象 ✓）
- **新流程首次生效**：先导入冒烟 → 通过才重启 ⇒ 本次**零崩溃**、四项健康一次通过
  （`SMOKE_OK → COMPILE_OK → active/success/PORT_OK/tools=31` ✓）
- 规模：`server.py` **5288 → 5192**（S5 −96）

## 7. S4 记录（已完成 · 含一次事故与教训）
- 抽出 `srv/outline.py`（`_est_tokens` / `_chunk_units` / `_split_unit` / `doc_outline`）
- 顺带把 `squash` 收进 `srv/text.py`（S1 漏了它；`outline` 依赖它）
- **⚠️ 事故**：首版 `srv/outline.py` 写了 `from srv.text import squash`，但 `squash` 当时还在 `server.py`
  ⇒ **`yaml` 无关的纯 ImportError** ⇒ 服务 `exit status=1` **崩溃循环 ~35s**（`activating (auto-restart)`）
- **教训（已写入流程）**：切片后**先做导入冒烟** `cd tools/ontology-mcp && python3 -c 'import server'`，
  再执行 `systemctl restart`；冒烟失败就**不要重启**（否则把服务打进重启风暴 ✗）
- 恢复：修 import → 冒烟通过 → `reset-failed` + `restart` → `active` / 端口 8765 LISTEN / **tools=31** ✓
- 新增验收：`tests/test_mcp_tools_read.py::test_doc_outline_smoke`（**取库里真实文档**，不写死库名/slug ✓）
- 回归：`tests/test_mcp_tools_read.py` **7/7 通过** ✓
- 规模：`server.py` **5495 → 5288**（S4 −207）｜`srv/` 合计 **485 行**

## 6. S3 记录（已完成）
- 抽出 `srv/constants.py`（路径/阈值常量，**取值表达式逐字一致**；`REPO` 用 `parents[3]` 对齐 server 的 `parents[2]`）
- 抽出 `srv/state.py`（状态读写 + 归零，无判定逻辑）
- `server.py` 改为 `from srv.constants import ...` / `from srv.state import ...`，并删掉本地实现（避免同名 shadow ✗）
- 新增验收用例：`tests/test_mcp_tools_read.py::test_extract_state_get`（经 MCP ✓ 走 resolve + 读状态）
- 验证：`py_compile` ✓ / `active` ✓ / `success` ✓ / `tools=31` ✓ / 读写回归 **全绿（含新用例）** ✓
- **未搬**：`fetch_existing_pages`（依赖 `TYPE_PENDING`，与 S7/S8 的常量收敛一起搬 ✓）
- `server.py` 行数：**5570 → 5495**（S3 −75）

## 14. S9 第一次尝试：失败并回滚（2026-10-22）
- 手法：行号锚点脚本把 MCPHandler 803 行 + main 22 行搬到 srv/http.py，server.py 留薄壳 main（注入 _CTX）
- 结果：import server OK、import srv.http OK、active/success/PORT_OK/tools=31 全绿，但**每个请求都 RemoteDisconnected** => handler 运行期 NameError
- 根因：grep 候选表只覆盖 5 个外部符号，实际 handler 还引用更多 server 级名字（我的候选正则漏项）
- 处置：cp logs/server.pre_s9.py server.py + rm srv/http.py + 重启 => 服务恢复（active/success/PORT_OK/tools=31），未提交任何东西
- 教训（下次必须）：① 搬前用 **AST 求自由变量**（不是 grep 候选表）枚举全部外部依赖；② 验收必须发**真实请求**（tools/list + 一次 tools/call），只做 import 冒烟不够 ✗
- 备份：logs/server.pre_s9.py（回滚用，勿删）

## 15. S9 第二轮：成功（2026-10-22）
- 方案：**bind(server globals) 注入整个命名空间**（AST 实测外部依赖 **51 个**，逐名注入不可行）；搬移代码**逐字不改**
- 踩坑 1：类体在 **import 时**就用到 SERVER_NAME/SERVER_VERSION ⇒ 必须从 srv.constants 直接 import（bind 来不及）
- 踩坑 2：bind 把 server 的 main 覆盖本模块 main ⇒ RecursionError；改为**只补缺失名**（k not in globals()）
- 验收升级：真实请求探针（initialize / tools-list / tools-call）+ 部署后 read 9/9、write 3 pass+1 xfail(B4)、coverage 2/2
- 规模：server.py **4329 → 3510**；srv/http.py **847 行**；备份 logs/server.pre_s9b.py
