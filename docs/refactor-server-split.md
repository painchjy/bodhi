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
| S4 | `srv/outline.py` | `doc_outline` / `_chunk_units` / `_split_unit` / `_est_tokens` | `ke_*` | 中（拆词/游标） | 待做 |
| S5 | `srv/session.py` | `adopt_session` / `_resolve_session_identity` / `_resolve_session_for` / 会话页确保 | `ke_pages`, `ke_db` | 中 | 待做 |
| S6 | `srv/links.py` | `link_candidates` / `list_link_candidates` / `resolve_link_candidate` / `_page_row_by_slug` | `ke_pages` | 中（多已退役） | 待做 |
| S7 | `srv/render.py` | `_design_sections` / `_type_label_of` / `build_new_page` / `_contract_head` 等**纯渲染** | 本体 | 中 | 待做 |
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

## 6. S3 记录（已完成）
- 抽出 `srv/constants.py`（路径/阈值常量，**取值表达式逐字一致**；`REPO` 用 `parents[3]` 对齐 server 的 `parents[2]`）
- 抽出 `srv/state.py`（状态读写 + 归零，无判定逻辑）
- `server.py` 改为 `from srv.constants import ...` / `from srv.state import ...`，并删掉本地实现（避免同名 shadow ✗）
- 新增验收用例：`tests/test_mcp_tools_read.py::test_extract_state_get`（经 MCP ✓ 走 resolve + 读状态）
- 验证：`py_compile` ✓ / `active` ✓ / `success` ✓ / `tools=31` ✓ / 读写回归 **全绿（含新用例）** ✓
- **未搬**：`fetch_existing_pages`（依赖 `TYPE_PENDING`，与 S7/S8 的常量收敛一起搬 ✓）
- `server.py` 行数：**5570 → 5495**（S3 −75）
