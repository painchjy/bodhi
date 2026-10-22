# P3 kernel generalization: read-only analysis (2026-10-22)
﻿
> 口径：**工具层只做协议与编排**；凡是可复用、可单测的能力一律下沉 `tools/ke-core/*`；下沉后**回执不变**、**回归全绿**、**server.py 行数下降** 三条同时满足才收。

## 1. 现状（S9 之后）
| 项 | 值 |
|---|---|
| server.py | **3510 行 / 81 个顶层 def** |
| srv/ | 13 模块（含 http.py 849 行） |
| 测试 | 全量 126 项 RC=0（xfail 2 / skip 3 / 失败 0） |

## 2. 下沉候选（按收益/风险排序）
| # | 能力 | 现在位置（行） | 建议目标 | 理由 | 风险 |
|---|---|---|---|---|---|
| A | 图谱视图（bodhi_graph / bodhi_model_graph / bodhi_page / _section_edge / _class_meta / all_class_meta / class_group / class_category_path） | 3227-3510 | `ke-core/ke_graph_view.py` | 纯读、无编排；前端与图谱页共用 | 低（只读） |
| B | 目录/分类（refresh_folders / _realign_wiki_paths / recategorize / _class_chain_labels / _category_path_for） | 3032-3210 | `ke-core/ke_category.py`（已存在） | 已是同域，工具层重复封装 | 中（写目录） |
| C | 服务总览聚合（service_design_summary / service_overview_lines / crud_model / crud_matrix_lines / start_overview_job） | 2400-2600 区 | `ke-core/ke_overview.py` | 聚合逻辑可复用、可单测；异步 job 只是外壳 | 中 |
| D | 观测（_log_tool_begin / _log_tool_call / _log_brief / _log_line / _dump_args） | 2894-3030 | `srv/obs.py` | 协议侧设施，与业务无关 | 低 |
| E | 重入保护（_reentry_key / _prune_reentry / call_tool 外壳） | 2592-2664 | `srv/reentry.py` | 同上 | 低 |
| F | 分发表（_call_tool_impl 约 228 行 + call_tool） | 2608-2893 | `srv/dispatch.py`（=原 S10） | 工具层最后收口 | 高（分支多） |

## 3. P3.5 能力前移（紧随 P3）
| 能力 | 现状 | 目标 | 验收口径 |
|---|---|---|---|
| page_copy | 智能体手动重建页 | `ke_pages.copy_page(kb, slug, new_slug)` | 副本页属性/关系/来源齐全，原页零改动 |
| rule_publish | 手工改规则页再回读 | `ke_rule_publish.publish(...)` 负责 hash 生成 | **hash 变化 = 发布成功**（回执给 before/after hash） |
| metric_compute | 报告里现算 | `ke_metrics.compute(kb, spec)` | 指标可复算、回执带明细与口径 |

## 4. 建议执行顺序
1. **A（图谱视图，只读）**：收益最大、风险最低，搬完 server.py 约 −280 行；
2. **D + E（观测/重入）**：纯设施，−150 行左右，顺带把 call_tool 外壳瘦下来；
3. **B（目录/分类）**：与 ke_category 合并去重；
4. **C（总览聚合）**：把 job 外壳留在协议层，聚合下沉；
5. **F（分发）**：最后做，靠 126 项回归兜底；
6. 每项都走：AST 依赖清单 → 搬 → 真实请求 + 定向回归 → 四项健康 → 文档 → 提交。

## 5. A 已落地（2026-10-22）
- 新增 `tools/ke-core/ke_graph_view.py`（289 行）：_class_meta / all_class_meta / class_group / class_category_path / _section_edge / bodhi_model_graph / bodhi_graph / bodhi_page + _WIKI_LINK_RE/_SECTION_EDGE
- server.py **3510 → 3242**（−268），改为 from ke_graph_view import ...（名字不变，HTTP 层经 bind 继续可用）
- 验收：SMOKE_OK / COMPILE_OK / active / success / PORT_OK / tools=31；**HTTP 探针**：/bodhi/graph 5 节点 1 边、/bodhi/model-graph 31 节点 61 边、/bodhi/page 正常
- 全量回归：126 项 **0 失败**（xfail 2 / skip 3）
- 备份：logs/server.pre_p3a.py

## 6. D+E 已落地（2026-10-22）
- `srv/reentry.py`（31 行）：_REENTRY / _REENTRY_LOCK / REENTRY_TOOLS / REENTRY_WINDOW_S / _reentry_key / _prune_reentry
- `srv/obs.py`（154 行）：_log_brief / _log_line / _dump_args / _log_tool_begin / _log_tool_call（两段式日志不变）
- LOG_DIR / ARGS_DIR 归位 `srv/constants.py`（server 已无残留引用）
- server.py **3242 → 3097**（−145）
- 验收：SMOKE_OK / COMPILE_OK / active / success / PORT_OK / tools=31；HTTP 探针（graph 5/1、model-graph 31/61、page OK）；read+write+coverage **14 pass + 1 xfail(B4)**

## 7. B 已落地（2026-10-22）
- `ke_category.py` 新增：常量 MAX_DIR_DEPTH / WEKNORA_MODULE_SHORT / WEKNORA_TYPE_LABELS；函数 _realign_wiki_paths / refresh_folders / _class_chain_labels / _category_path_for / recategorize（逐字搬；resolve_kb_id 用内核侧适配器 _resolve_kb_id 保持 (id, note) 语义）
- server.py **3097 → 2916**（−181）；ke_category.py 205 → 412 行
- 踩坑：ke_category 原本不 import ke_db ⇒ 冒烟 SMOKE_FAIL 直接拦住（服务未重启 ✓）→ 补 import 后通过
- 验收：SMOKE_OK / COMPILE_OK / active / success / PORT_OK / tools=31；read+write+coverage 14 pass + 1 xfail
