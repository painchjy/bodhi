# 测试待办 / 已发现的真问题（P0.5 期间记录，2026-10-21）

> 口径（用户 2026-10-22 指示）：**非功能性问题只记录、重构计划完成后再解决** ✓
> 分类：`[功能]` = 影响正确性/阻塞流程，必须当期修；`[非功能]` = 一致性/健壮性/性能/可解释性，
> **记录 + 用例标注（xfail/skip）即可**，重构（P1→P6）结束后统一处理。

## 0. 分类总览
| 类别 | 条目 | 处置 |
|---|---|---|
| `[功能]` 已修 | B5 `before_version` 漏传、B6 kb 清单缓存阻塞新库写入、D1-D4 技能清单、C1-C4 脚手架、B3 导出回执断言 | 已修 ✓ |
| `[非功能]` **后置** | **B4**（幂等回执判 created）、**B7**（部分索引语义）、**A1/A2**（流程骨架数据缺口）、**B1/B2**（调用契约需按实际传参） | 记录 + xfail/skip ✓ **重构后再解决** |
| `[功能]` 进行中 | P2 server.py 拆分（见 `docs/refactor-server-split.md`） | 现在做 ✓ |
| `[功能]` **P5 内网问题** | **4.5 解绑**（`context_authority_apply{action:"detach"}`）｜**4.3 参考规范**（`reference_lookup` 代理/超时/白名单 + 结构化 `reason`） | **已落地 ✓**（2026-10-22，用例：`test_mcp_tools_context_detach.py` / `test_reference_lookup.py`） |
| `[功能]` P5 待做 | 4.1+4.4 检索策略 v2（类模糊匹配 / `route` / `strategy`）、4.6 会话分页判定、4.2 报告模板化 | 见 `docs/p5-internal-issues.md` |

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
| B4 | **`save_knowledge` 幂等被判 `created`**（同库同 slug 二次写） | **⚠️ 上一轮的"并发竞态"结论作废**：诊断字段打在 `return`（在 INSERT **之后**）⇒ 看到的 1 行可能正是本次插入的 ✗。仍然成立：① 调用前测试进程查 PG 有 1 行（`version=1, del=f`）② 服务端 `existing` 查到 0 行 ③ 该次走 create 分支。⇒ 只剩两种可能：**(a) 服务端与测试进程看到的不是同一行/同一库** **(b) 调用前该行已被服务端软删** | 用例 xfail 带证据；下一步：一次诊断同时回传**行 id + deleted_at + psql 目标容器名**，与测试侧对比即可定案 |
| B5 | `ke_pages.write_knowledge` 最终返回**漏了 `before_version`** ⇒ `page_versions[].before` 恒为 null | 实测修前后：`before: None` → `before: 0` | **已修** ✓（`ke_pages.py:1392` 起加字段） |
| B7 | `wiki_pages` 的唯一索引是 **部分索引**：`UNIQUE (knowledge_base_id, slug) WHERE deleted_at IS NULL` | 实测 `pg_indexes`（2026-10-21） | ⇒ **软删后可再插同名页**（旧行留作历史，这是设计 ✓），但任何"按 slug 取最新"的逻辑**必须显式** `deleted_at IS NULL`，否则会拿到死行 ✗。当前 `(kb,slug)` 重复组 = **0** ✓，软删 57 / 总 427 ✓ |
| B6 | `ke_db` kb 清单**60s 进程缓存** ⇒ 刚建的库写不进去（`kb_unresolved: not_found`，但 `candidates` 里就有它 ✗） | 实测（本轮多次复现） | **已修** ✓：`_kb_rows(force=True)` + `resolve_kb_id`/`resolve_kb_candidate` 未命中时**强制刷新一次**（不放宽口径 ✓） |

## C. 测试脚手架踩坑（已修 ✓）
| # | 坑 | 修法 |
|---|---|---|
| C1 | `knowledge_bases` 的 `embedding_model_id` / `summary_model_id` 是 **NOT NULL**，且 `tenant_id` 必须与模板库一致（否则解析器看不到该库 ✗） | 克隆模板库行（含 `tenant_id`）✓ |
| C2 | 库名只用秒级时间戳会**撞名** | 名字加 `uuid4().hex[:6]` ✓ |
| C3 | 属性写入/关系用例**没有前置建页** ⇒ "页面不存在" | 统一 `_mk_page()` 前置 ✓ |
| C4 | 图节点类型属性是 **`page_type`**（58/58），**没有 `pt`** | 用例已改 ✓ |

## F. 历史覆盖（P1 起点 ✓）
| 项 | 内容 |
|---|---|
| 工具 | `tests/test_coverage_gaps.py`：注册表 − 日志历史 = **无历史工具**，与 `tests/golden/tools_no_history.json` 对齐 |
| 首次基线 | **18 个工具无真实调用历史**（`audit_kb`/`import_*`×5 + 退役 6 个 + `job_status`/`retag_*`/`service_overview`…） |
| 用法 | 新工具"悄悄上线且无历史" ⇒ 用例立刻红 ✓（逼着造例或显式写进 golden 承认 ✓） |
| 覆盖率 | 只打印不设阈值（`test_no_history_ratio_reported`）✓ |

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

## D. 技能/工具清单（已修 ✓）
| # | 问题 | 修法 |
|---|---|---|
| D1 | `rule_judge/SKILL.md` 缺 `id/when/tools`（`skills()` 靠 front-matter ✓） | 补 3 个键 ✓ |
| D2 | `domain_modeling` 工具名误拼 `list_image_extract` | 改 `image_extract` ✓ |
| D3 | `session_provenance` 工具名误拼 `list_audit_scan` | 改 `audit_scan` ✓ |
| D4 | `skill_development` 引用**已退役** `rules_of_policy` | 删（取规则统一 `knowledge_lookup(mode="rules")`）✓ |
