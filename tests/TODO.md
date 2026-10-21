# 测试待办 / 已发现的真问题（P0.5 期间记录，2026-10-21）

> 口径：**测试驱动**。下面每条都来自**实测回执**，不是猜测。
> 修完一条划掉并补断言（避免回归）。

## A. 数据缺口（不是代码错，需要补数据）
| # | 缺口 | 证据 | 处理 |
|---|---|---|---|
| A1 | 领域库 3 个流程里 **2 个没有活动边**（`bpmn:processHasActivity`） | `test_flow_skeleton::test_processes_have_activities` → `['bmm/businessprocess/评审问题管理', 'bmm/businessprocess/评审规则维护']` | 用例已 `xfail` 并列出清单；补边后自动转绿 ✓ |
| A2 | `knowledge_lookup(mode="path", start="bpmn/activity/规则工程")` 返回 0 路径 | 早前手工调用回执 | 先确认该活动是否真有 `activityContainsTask` 出边；无 ⇒ 属 A1 同类 |

## B. 契约/文档不一致（代码行为对，调用方要按实际传参）
| # | 事实 | 证据 | 处理 |
|---|---|---|---|
| B1 | `save_knowledge` **必须**有真实会话身份，否则 `need_session=true` 拒写 | 回执 `reason/hint`（server.py:2081-2086） | 测试需 `session` fixture：从 `sessions`/会话页取**真实** `session_no` ✓（**不许自编** ✗） |
| B2 | 写库目标库解析走 `resolve_write_kb` → `ke_db.resolve_kb_candidate`（**完整 uuid 或精确库名**；模糊要 `confirm_kb_match=true`） | server.py:713-746 | 测试库必须能被该解析器看见（见 C1） |
| B3 | `kb_export` 回执**没有** `out_dir`/`saved` 键 | 实测回执 | 断言改为"任一含 dir/export/path 的字段" ✓（已修） |
| B4 | **`save_knowledge` 幂等被判 `created`**（同库同 slug 二次写） | **已定位到并发竞态**（2026-10-21）：同一次调用内 `existing` 查询=0 行，**几微秒后**同样的 SQL=1 行（`diag: {preloaded_is_none:true, existing_len:0, same_sql_len:1, c_alive:"1"}`）⇒ 有**并发写入**（疑似 `save_knowledge` 后台任务/job 与主路径同时写同一 `(kb,slug)`）✗ | 用例已 `xfail` 并附证据（诊断字段已撤）；下一步：查 `save_knowledge` 是否派生后台 job（`job_status` 工具存在 ⇒ 有 job 机制），改成**同 slug 串行**或让后台任务走幂等 upsert |
| B5 | `ke_pages.write_knowledge` 最终返回**漏了 `before_version`** ⇒ `page_versions[].before` 恒为 null | 实测修前后：`before: None` → `before: 0` | **已修** ✓（`ke_pages.py:1392` 起加字段） |
| B6 | `ke_db` kb 清单**60s 进程缓存** ⇒ 刚建的库写不进去（`kb_unresolved: not_found`，但 `candidates` 里就有它 ✗） | 实测（本轮多次复现） | **已修** ✓：`_kb_rows(force=True)` + `resolve_kb_id`/`resolve_kb_candidate` 未命中时**强制刷新一次**（不放宽口径 ✓） |

## C. 测试脚手架踩坑（已修 ✓）
| # | 坑 | 修法 |
|---|---|---|
| C1 | `knowledge_bases` 的 `embedding_model_id` / `summary_model_id` 是 **NOT NULL**，且 `tenant_id` 必须与模板库一致（否则解析器看不到该库 ✗） | 克隆模板库行（含 `tenant_id`）✓ |
| C2 | 库名只用秒级时间戳会**撞名** | 名字加 `uuid4().hex[:6]` ✓ |
| C3 | 属性写入/关系用例**没有前置建页** ⇒ "页面不存在" | 统一 `_mk_page()` 前置 ✓ |
| C4 | 图节点类型属性是 **`page_type`**（58/58），**没有 `pt`** | 用例已改 ✓ |

## E. 日志还原管线（P0.5-3 ✓ 已落地）
| 项 | 内容 |
|---|---|
| 生成器 | `tests/mine_logs.py`（`BEGIN/DONE` 用 `args_file=` 配对 → 去重 → 切会话 → 按回执**字段**标形态） |
| 实测规模 | 13 个日志 / **1054 次调用** → **625 条去重用例** / **152 段会话** |
| 形态分布 | `ok` 78 ｜ `ok_brief` 392 ｜ `silent` 477 ｜ `timeout` 63 ｜ `dry` 10 ｜ `error` 34（`guard` 0 = 日志里没记这类回执） |
| 产物 | `tests/cases/mined/*.json`（+ `COVERAGE.md` 矩阵）、`tests/sessions/*.json`、`tests/golden/mined_index.json` |
| 回放用例 | `tests/test_replay_mined.py`：**全量静态**（工具仍在注册表、args 为对象）+ 每 (tool×形态) **抽 1 条实调**（`kb_id` 一律换 `_test_*` 库 ✓） |
| 会话用例 | `tests/test_sessions_replay.py`：seq ≥2、工具已注册、超时占比 ≤50% |
| 缺口造例 | 看 `tests/cases/mined/COVERAGE.md`：某工具缺 `guard`/`error`/`dry` 形态 ⇒ 按形态补例 |

| # | 问题 | 修法 |
|---|---|---|
| D1 | `rule_judge/SKILL.md` 缺 `id/when/tools`（MCP `skills()` 靠 front-matter ✓） | 补 3 个键 ✓ |
| D2 | `domain_modeling` 工具名误拼 `list_image_extract` | 改 `image_extract` ✓ |
| D3 | `session_provenance` 工具名误拼 `list_audit_scan` | 改 `audit_scan` ✓ |
| D4 | `skill_development` 引用**已退役** `rules_of_policy` | 删（取规则统一 `knowledge_lookup(mode="rules")`）✓ |
