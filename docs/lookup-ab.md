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

### 3.2 真实 LLM 端到端（结论以此为准）

1. 在 WeKnora 里用同一批问题（`tests/cases/lookup_ab_questions.json`）各跑 2 轮：
   - A 组：提示词里要求 `knowledge_lookup(..., ontology_hint=true, session_no=<会话编号>, question=<原问题>)`；
   - B 组：同参数但 `ontology_hint=false`；
   每组 **20 问 × 2 轮**（共 ~80 次调用）。
2. 跑报告：
   ```bash
   python3 tools/ke-core/ke_ab_report.py --out docs/lookup-ab-report.md
   ```
   （默认读当日 `logs/lookup_ab_*.jsonl` + 最新模拟快照；联调噪声大时用 `--no-telemetry`）

## 4. 看报告

`docs/lookup-ab-report.md` 由脚本生成，含：总览表（含差值）、按问题类目、数据质量。
数据质量只核**成对齐全**（两组问题集合是否一致、问题集覆盖率），**不对结论下判断** ✓。

## 5. 约束

- 开关只影响**回执提示面**，不改检索逻辑（命中数必须一致，测试里有断言）；
- `session_no`/`question` **不参与检索**，只入日志（避免"为了统计而改变行为"）；
- 遥测写入失败**绝不影响**工具返回（`srv/obs.py` 里 `try/except` 吞掉）；
- 本仓库不保存用户原始问题到业务库（只落日志文件，按天滚动）。
