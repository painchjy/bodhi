# 测试待办 / 已发现的真问题（P0.5 期间记录，2026-10-21）

> 口径（用户 2026-10-22 指示）：**非功能性问题只记录、重构计划完成后再解决** ✓
> 分类：`[功能]` = 影响正确性/阻塞流程，必须当期修；`[非功能]` = 一致性/健壮性/性能/可解释性，
> **记录 + 用例标注（xfail/skip）即可**，重构（P1→P6）结束后统一处理。

## 0. 分类总览
| 类别 | 条目 | 处置 |
|---|---|---|
| `[功能]` 已修 | B5 `before_version` 漏传、B6 kb 清单缓存阻塞新库写入、D1-D4 技能清单、C1-C4 脚手架、B3 导出回执断言 | 已修 ✓ |
| `[非功能]` **后置** | **B7**（部分索引语义）、**A1/A2**（流程骨架数据缺口）、**B1/B2**（调用契约需按实际传参） | 记录 + xfail/skip ✓ **重构后再解决** |
| `[功能]` 进行中 | P2 server.py 拆分（见 `docs/refactor-server-split.md`） | 现在做 ✓ |
| `[功能]` **P5 内网问题** | **4.5 解绑**（`context_authority_apply{action:"detach"}`）｜**4.3 参考规范**（`reference_lookup` 代理/超时/白名单 + 结构化 `reason`） | **已落地 ✓**（2026-10-22，用例：`test_mcp_tools_context_detach.py` / `test_reference_lookup.py`） |
| `[功能]` P5 已完成 | 4.5 解绑 ｜ 4.3 参考规范 ｜ **4.1+4.4 检索策略 v2** ｜ **4.6 会话分页** | 已落地 ✓（用例与口径见 `docs/p5-internal-issues.md`） |
| `[功能]` P5 待做 | —（4.1/4.2/4.3/4.4/4.5/4.6 全部落地 ✓） | 见 `docs/p5-internal-issues.md`；后续：域词退场（工具/技能只认类型与属性） |
| `[口径]` 域词退场 | ✅ **已清零**（2026-10-23）：`tools/**`+`skills/**` 活跃面 **394 → 0**；`test_no_business_semantics` 改为**硬断言 0**（golden=`{}`）；规则链 4 模块归档到 `tools/ke-core/_deprecated/` | `docs/ontology/domain-logic-migration.md` §7 |

## A. 数据缺口（不是代码错，需要补数据）
| # | 缺口 | 证据 | 处理 |
|---|---|---|---|
| A1 | 领域库 3 个流程里 **2 个没有活动边**（`bpmn:processHasActivity`） | ✅ **已由领域建模智能体修复（2026-10-24）**：现 2 个流程各 ≥1 活动（`技术方案评审流程→评审执行→逐项评审`；`评审问题管理→问题跟踪→{知识入库,问题核实}`），`test_flow_skeleton` **4 项全绿**（不再 xfail） | 验证：`pytest tests/test_flow_skeleton.py tests/test_mcp_tools_read.py` = 13 项通过 ✓ |
| A2 | `knowledge_lookup(mode="path", start=…)` 起点写死导致 0 路径 | 起点已改**动态取**（有出边的活动）；A1 修好后 path 用例同时转绿 | ✅ 已关闭 |

## B. 契约/文档不一致（代码行为对，调用方要按实际传参）
| # | 事实 | 证据 | 处理 |
|---|---|---|---|
| B1 | `save_knowledge` **必须**有真实会话身份，否则 `need_session=true` 拒写 | 回执 `reason/hint`（server.py:2081-2086） | ✅ **已按实际传参**：`tests/conftest.py::session_no` fixture 从 `sessions` 取**真实**会话编号（**不许自编** ✗），全部写类用例已用它 |
| B2 | 写库目标库解析走 `resolve_write_kb` → `ke_db.resolve_kb_candidate`（**完整 uuid 或精确库名**；模糊要 `confirm_kb_match=true`） | server.py:713-746 | ✅ **已按实际传参**：`test_kb` fixture 用 UUID + 轮询到服务端可见（配合 B6 的强制刷新），无需 `confirm_kb_match` |
| B3 | `kb_export` 回执**没有** `out_dir`/`saved` 键 | 实测回执 | 断言改为"任一含 dir/export/path 的字段" ✓（已修） |
| B4 | **`save_knowledge` 幂等被判 `created`**（同库同 slug 二次写） | ✅ **已修（2026-10-24）**：真因是 **同载荷重入保护**（`P3-0i3`）—— 第二次**没有写库**（正确），但把第一次的回执原样返回 ⇒ 看起来像"又建了一页" ✗。现在去重回执按库中现值刷新：`action="unchanged"` / `before=<当前版本>` / `created=[]`（`server._reentry_receipt`）。用例改为断言这些（不再是 xfail） | `tests/test_mcp_tools_write.py::test_save_knowledge_document_idempotent` |
| B5 | `ke_pages.write_knowledge` 最终返回**漏了 `before_version`** ⇒ `page_versions[].before` 恒为 null | 实测修前后：`before: None` → `before: 0` | **已修** ✓（`ke_pages.py:1392` 起加字段） |
| B7 | `wiki_pages` 的唯一索引是 **部分索引**：`UNIQUE (knowledge_base_id, slug) WHERE deleted_at IS NULL` | 实测 `pg_indexes`（2026-10-21）：软删后可再插同名页（**设计 ✓**），但"按 slug 取最新"必须显式 `deleted_at IS NULL`，否则拿到死行 ✗ | ✅ **已加行为级用例（2026-10-24）**：`tests/test_soft_delete_isolation.py` —— 放一条软删"诱饵行"（version=9、内容带 DECOY），验证 ① 面板读 ② 检索 ③ 覆盖写 三条路径都**只认活行**，且死行不被改动（3 例全绿） |
| B6 | `ke_db` kb 清单**60s 进程缓存** ⇒ 刚建的库写不进去（`kb_unresolved: not_found`，但 `candidates` 里就有它 ✗） | 实测（本轮多次复现） | **已修** ✓：`_kb_rows(force=True)` + `resolve_kb_id`/`resolve_kb_candidate` 未命中时**强制刷新一次**（不放宽口径 ✓） |
| W7 | 别名里含与标题同名的标签（旧写入路径把标题塞进 `aliases`） | 领域库 34 条（2026-10-24 实测） | ✅ **已提供清理工具并清零**：`clean_alias_noise(kb_id, dry_run=true)` —— 只删**与标题完全相同**的别名（真别名保留），执行前写 `wiki_page_revisions` 快照、`version+1`（可回退）。领域库实跑：dry-run 34 → apply cleaned=34 → 巡检 **0 high/0 medium/0 low**，二次 dry-run `hits=0`（幂等）。用例：`tests/test_clean_alias_noise.py`（3） |
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
