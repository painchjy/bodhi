# 检索 A/B：本体提示开关与遥测（2026-10-24）

> 目标：**用数据回答**"给智能体本体提示（类范围 / 建议下一步）到底有没有用"，
> 而不是靠感觉。开关在服务端一处实现，两种模式共享同一条检索链路 ⇒ 差异只来自"提示面"。

## 1. 机制

`knowledge_lookup` 新增 3 个可选参数（**向后兼容**，旧调用方不用改）：

| 参数 | 类型 | 作用 |
|---|---|---|
| `ontology_hint` | `bool \| null` | `true` = 提示面开；`false` = 对照组；`null`/不传 ⇒ 读 env `BODHI_ONTOLOGY_HINT`（默认 **on**） |
| `session_no` | `string` | **只进遥测**，用于按会话统计（不参与检索） |
| `question` | `string` | 用户**原始问题**，只进遥测（检索仍用 `query`；用于同类问题聚簇） |

回执差异（**这就是 A/B 的自变量**）：

- `ontology_hint=true` ⇒ 带 `plan`（scope_classes / text_terms / relaxed_by）、`strategy`、
  `suggestions`（每条含可直接调用的 `args`）、`next`、`cross_kb_hint`；
- `ontology_hint=false` ⇒ **只回** `hits / counts / kb / query / mode / ok / note`
  （`plan/strategy/suggestions/next/cross_kb_hint` 全部剥离）；
- **两种模式命中结果相同**（检索本身不变 ✓）——所以差异只能来自"智能体是否照着提示换招"。

遥测（两种模式都回）：

```json
"telemetry": {"variant": "hint|plain", "ontology_hint": true, "session_no": "…",
              "question": "…", "question_key": "…", "query": "…", "mode": "keyword",
              "hits": 2, "top_slug": "bmm/businessprocess/…", "relaxed_by": "",
              "ambiguity": false, "scope_by": "fuzzy_label", "ms": 176,
              "assignment": "manual"}
```

落盘两处：
1. `logs/mcp_calls_*.log` —— 原有单行格式**不变**，`result=` 里多了 `telemetry` 字段（白名单 `srv/obs.py`）；
2. `logs/lookup_ab_YYYYMMDD.jsonl` —— **A/B 专用侧车**（一行一 JSON：ts / tool / ms / error / telemetry），
   分析只读这个，`ke_eval`、`test_replay_mined` 等解析主日志的逻辑**不受影响** ✓。

## 2. 指标

| 指标 | 定义 | 说明 |
|---|---|---|
| 命中率 | `hits > 0` 的调用占比 | 最粗的"有没有东西" |
| 解决率 | 按问题集 `expect_hits_min` 判定（>0 要 `hits≥该值`；=0 要 `hits==0`） | 负样例考"能不能判对没有" |
| 平均调用次数 / 问题 | 同一 `(session_no, question_key)` 的调用次数 | 真 LLM 场景的"绕了几轮" |
| ≤3 次收敛率 | 调用次数 ≤3 的问题占比 | 智能体是否在乱试 |
| 有出路率 | 回执里出现 `suggestions/next`（模拟）或 `relaxed_by/ambiguity`（真实） | 本体提示的直接价值面 |
| 中位 / P90 耗时 | `ms` 分位 | 提示面带来的开销 |

**判定方式**：配对（同题两组），只比差值与区间；**不预设"hint 一定更好"**——
若数据不支持，就照实写（可能结论是"提示对简单问题无差异、对多跳/不存在问题才有用"）。

## 3. 怎么跑

### 3.1 确定性模拟（CI 友好，不调 LLM）

```bash
python3 tests/ab_runner.py                       # 全量 14 题 × 2 组，写 logs/ab/lookup_ab_<时间>.json
python3 -m pytest tests/test_lookup_ab.py -q     # 回归：开关生效 / 数据成对 / 报告落盘
python3 tools/ke-core/ke_ab_report.py --no-telemetry --out docs/lookup-ab-report.md
```

模拟器口径（`tests/ab_runner.py::simulate`）：
- `hint` 组 0 命中时**读回执建议再查一次**（最多 2 次）；
- `plain` 组没有建议 ⇒ 只拿原始结果（1 次）。
⇒ 这正是两种策略的差别，**且完全确定**（可复现、可进 CI）。

### 3.2 真实 LLM 端到端（结论以此为准 · **C1 口径**）

#### 3.2.1 样本准入（用户口径 2026-10-24）

> 用户问题提出后，**第一步就调 `knowledge_lookup`** 的才纳入对比；若首步是 wiki 检索（`wiki_search`），
> 那不是我们要比的问题 ⇒ **不纳入**。

服务端**看不到** WeKnora 原生检索（`wiki_search`/`grep_chunks`/`list_knowledge_chunks`/`get_document_info`/
`wiki_read_page` 都不经过 bodhi-mcp ✗）⇒ 用 **C1 口径**：*轮次边界 + 服务端裁决 + 自报*。

| 步骤 | 谁做 | 内容 |
|---|---|---|
| ① 开轮 | 智能体（提示词纪律） | **每问第一个工具调用**：`lookup_turn_begin(session_no, question)` → 拿 `turn_id`（**不做检索**） |
| ② 检索 | 智能体 | `knowledge_lookup(..., turn_id=<回执>, first_step=true/false, session_no=…, question=…)` |
| ③ 裁决 | 服务端 | 检索类白名单（`knowledge_lookup`/`context_lookup`/`doc_outline`/`extract_candidates`/`reference_lookup`）**首个被认领者**记入 `telemetry.turn_first_retrieval`（**不覆盖** ✓） |
| ④ 过滤 | 报告器 | `--cohort c1`（默认）：`turn_first_retrieval == knowledge_lookup` 且 `first_step != false` 才纳入 |

**排除项逐条计数**（写进报告「数据质量」，不悄悄丢样本 ✗）：
`out_of_range`（时间窗外）｜`version_mismatch`（版本不符）｜`turn_missing`（没传 turn_id ⇒ 无法证明首步）｜
`first_retrieval_other`（首检索是别的工具）｜`self_report_false`（自报非首步）。

**诚实边界**：`first_step` 仍是**智能体自报**，所以报告同时给 **首步一致率**（自报 vs 服务端裁决）——
一致率低就说明纪律没执行到位，结论要打折 ✓。想彻底摆脱自报，需要接 WeKnora 侧 tool_calls 序列（C2，未做）。

#### 3.2.2 版本维度（区分"改了检索程序"）

- `ke_lookup.py`：`ALGO_VERSION`（**人工语义位**，检索口径/排序/放宽变了才 bump）+ `CODE_REV`
  （本文件 sha1 前 8 位，**任何改动即变**）⇒ `telemetry.lookup_version = "v2-<8位>"`；
- 报告按 `lookup_version` **分表**（跨版本不混比 ✓）；历史 jsonl 没有该字段 ⇒ 归 `unknown` 桶；
- 模拟报告（`tests/ab_runner.py`）也写同一字段 ⇒ 模拟/真实**同构可比**。

#### 3.2.3 问题种类维度

`telemetry.q_category` —— **只看查询形态、不看结果**（避免循环论证 ✗）：
`instances→实例`｜`route/path→路径`｜`rules→规则`｜`keyword: 单词/多词/空查询`。
已知题优先用问题集里的 `category`（报告侧 `by_category` 会先取它 ✓）。

#### 3.2.4 怎么跑

1. WeKnora 里用同一批问题各跑 2 轮（每组 20 问 × 2 轮）：
   - A 组：提示词要求 `ontology_hint=true`（默认）；
   - B 组：同参数 `ontology_hint=false`；
   - **两组都要遵守 3.2.1 的开轮纪律**（否则样本会被判 `turn_missing` 排除）。
2. 出报告（四维：版本 × A/B × 类目 × 时间窗）：
   ```bash
   python3 tools/ke-core/ke_ab_report.py --out docs/lookup-ab-report.md            # 默认 C1、当日窗口
   python3 tools/ke-core/ke_ab_report.py --since 2026-10-24 --until 2026-10-30 \
            --version 'v2-*' --cohort c1 --out docs/lookup-ab-report.md
   python3 tools/ke-core/ke_ab_report.py --cohort c3 --out /tmp/ab-side.md         # 旁证：不判首步
   ```
   联调噪声大时加 `--no-telemetry`（只看模拟）；样本不足 `--min-sample` 的格子只报 n、不解读 ✓。


## 4. 看报告

`docs/lookup-ab-report.md` 由脚本生成，含：总览表（含差值）、按问题类目、数据质量。
数据质量只核**成对齐全**（两组问题集合是否一致、问题集覆盖率），**不对结论下判断** ✓。

## 5. 约束

- 开关只影响**回执提示面**，不改检索逻辑（命中数必须一致，测试里有断言）；
- `session_no`/`question` **不参与检索**，只入日志（避免"为了统计而改变行为"）；
- 遥测写入失败**绝不影响**工具返回（`srv/obs.py` 里 `try/except` 吞掉）；
- 本仓库不保存用户原始问题到业务库（只落日志文件，按天滚动）。

## 6. 口径 v2（2026-10-25，四条修正）

第一轮真实数据暴露了四个口径问题，已全部改掉（`docs/lookup-ab-report.md` 由脚本重生成）：

### 6.1 查询次数 = **真实库访问**，不是 MCP 调用次数

`knowledge_lookup` 内部会自动换招：AND 空 ⇒ 降级 OR；图不可用 ⇒ 退 PG。
所以**一次工具调用可能打 1–5 次库/图**，用"调用次数"衡量"查了多少次"是错的 ✗。

现在 `ke_stats` 在 **`ke_db.psql()` / `ke_neo4j.query()`** 里逐次回填**返回行数**：

| 字段 | 含义 |
|---|---|
| `telemetry.io` | `{queries, rows, hit, empty, unknown, ms, empty_rate, ok}`（跨 PG/图汇总） |
| `telemetry.retrieval` | `{queries, pg, neo4j, empty_queries, hit_queries, unknown_queries, io_ms, degraded, ok}` |

**成功/失败口径（用户原话：空记录算失败）**：
`rows>0` ⇒ 这次往返算**成功**；`rows==0` ⇒ 算**失败**（`empty`）；脚本执行等无法判行的 ⇒ `unknown`（**不冒充成功**）。
一次调用是否"查到了"看 `retrieval.ok`（= 最终 `hits>0`）。

### 6.2 总耗时分三层

| 层级 | 字段 | 说明 |
|---|---|---|
| 单次检索 | `median_ms` / `p90_ms` | 一次 `knowledge_lookup` 的墙钟 |
| **每问检索总耗时** | `group_ms_median` / `group_ms_p90` | 同一问题**多轮合计**（"两轮评审"就是这个口径） |
| **端到端** | `agent_duration_ms`（C2） | 含 LLM 思考与全部工具，**真值** |

### 6.3 「hint 35 / plain 0；集合不一致（23 vs 0）」是什么意思

`23` = hint 侧**不同 `question_key` 的个数**（问题文本归一化去重后的键数），`0` = plain 侧个数。
一侧为 0 时"两个集合相等"必然不成立 ⇒ 旧报告会打 ⚠️，但**真实含义是"对照臂为空、不可比"**，
不是数据脏。现在报告**先判对照臂**（`pairing().arm_missing`），再判配对（组交集），并逐来源写明。

### 6.4 问题归组：不按"问题集"，按**语义**

实际跑法常是「选文件 + 给编号 + 要求评审」，不是按题库逐题：同一编号评审两次是**同问题的两轮**，
不同编号但语义相近也**可以**当同一问题比；而**关键字数量/层次不同**应算**不同类问题**。落法：

| 维度 | 来源 | 规则 |
|---|---|---|
| `qid` | `ke_lookup._extract_qid` | 抽业务编号（`评审编号 YYYYMM-889999`）⇒ **同编号必同组** |
| `question_core` | `ke_lookup._question_core` | 去编号/版本/文件名/轮次/礼貌语 ⇒ 归一语义核 |
| `q_shape` | `ke_lookup._q_shape` | **词数档**（0/1/2/3+）× **层次**（类／类+文本／文本／实例） |
| `question_group` | `ke_ab_report.group_rows` | 同 `qid` ⇒ 同组；否则语义核 **bigram 相似度 ≥ 0.62** ⇒ 同组（`--group-threshold` 可调） |

报告第 5 节按组列出「轮次 / 命中 / 查询 / 空查询 / 每问总耗时」，形态差异并列显示（不拆组 ✓）。

### 6.5 C2 真值：直接读 WeKnora 的工具轨迹

不需要再靠智能体自报 `first_step`：

```bash
python3 tools/ke-core/ke_ab_trace.py --since-days 3     # → logs/ab/trace_<日期>.jsonl
python3 tools/ke-core/ke_ab_report.py --out docs/lookup-ab-report.md
```

数据源：`messages.agent_steps`（jsonb 数组，每轮 `thought/iteration/tool_calls`）+
`messages.agent_duration_ms`；抽成一行一问：`tool_seq / first_tool / first_retrieval /
lookup_calls / native_retrieval_calls / agent_ms`。
报告第 3 节给「端到端耗时 / 真·首步=knowledge_lookup / 原生检索先行 / 平均工具调用」，
数据质量里给**自报 vs 真值一致率**（旧版"首步一致率"只是自报 vs 服务端裁决）。

**诚实边界**：C2 与遥测的配对键是 `(session_no, question_key)`；旧日志没传 `session_no`/`question`
时退化为「同会话 + 时间最近（±10min）」，再不行就记 `unknown`（只作耗时旁证，不参与 A/B 差异）。
