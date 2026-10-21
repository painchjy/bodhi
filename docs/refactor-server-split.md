# server.py 拆分计划（P2 ✓ 用户口径 2026-10-22）

## 0. 约束（每次切片都必须满足）
1. **行为不变**：`server.py` 里对外的**名字不变**（`from srv.x import 名字`），下游脚本/工具零改动 ✓
2. **三重验证**：`py_compile` → 重启服务 → `active / Result=success / tools=31` → 回归用例 ✓
3. **禁止宽正则批量替换**（历史事故：曾误改 47 处导致服务崩溃循环 ✗）——只用**内容锚点**做精确替换
4. 切片只搬**内聚**的东西；跨模块依赖用 `import` 单向（`srv/*` 不得反向 import `server`）✓

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
| S8 | `srv/tools/*.py` | 把 `_call_tool_impl` 的分支按**读写/运维/本体**分组搬走，`server.py` 只留 `if name == ...: return srv.tools.x.y(...)` | 全部 | 高（分支多） | 待做 |
| S9 | `srv/http.py` | HTTP/JSON-RPC 层（`ThreadingHTTPServer` + handler）与 `main()` | 协议 | 中 | 待做 |

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
