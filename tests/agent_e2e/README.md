# Agent E2E acceptance

这个目录只放**真实 WeKnora 智能体验收**；默认 pytest 只跑确定性单测，真实调用必须显式开启。

## 分层

- `harness.py`：WeKnora API client + 评审会话执行 + 确定性 evaluator。
- `cases/plan.json`：测试方案（只给评审智能体 case/question/输入文档引用）。
- `cases/oracle.json`：预期结果（只给评测侧，不能给评审智能体）。
- `test_review_causal.py`：plan/oracle 分离、evaluator 单测、live E2E。
- `test_analytics_mapping_flow.py`：analytics 取数步骤断言（canonical `mapping_object_id`、首次 mapping 成功、mapping 全部早于 scenario）；设置 `BODHI_RUN_ANALYTICS_E2E=1` 跑真实智能体。

## 防抄答案

- 评审智能体只收到 `plan.json` 的 `review_query` 和输入文档，不读 `oracle.json`。
- 评测侧独立读取 `oracle.json`，对比评审报告；两边不要共用同一会话。
- 生产环境建议把 Oracle 放独立 Eval KB，并禁止评审智能体 ACL 读取。

## 跑法

默认：

```bash
pytest -q tests/agent_e2e
```

真实 E2E：

```bash
export BODHI_RUN_AGENT_E2E=1
export BODHI_WEKNORA_BASE_URL=http://localhost:8080
export BODHI_REVIEW_AGENT_ID=<review-agent-uuid>
pytest -q -m agent_e2e tests/agent_e2e
```

`run_case()` 默认读取 `plan.json` 的 `source_file`，通过 `POST /files/<doc_id>?review_no=…`
把文档上传到 `it_platform` 文档服务并绑定评审编号；`BODHI_CASE_*_DOC` 只用于覆盖已有文档。
文档服务地址取 `BODHI_IT_MCP_BASE`，未设置时回退到 `.env` 的 `IT_MCP_PUBLIC_BASE`，再回退
`http://127.0.0.1:8770`。

`BODHI_WEKNORA_TOKEN` 可显式提供；未提供时从 `auth_tokens` 读取最新未撤销 access_token。

## 第一批 mock case

| case | 类型 | 预期 |
|---|---|---|
| CASE-CB-01 | Circuit Breaker 正例 | 符合 / V-CB-01 |
| CASE-CACHE-01 | Cache-Aside 正例 | 符合 / V-CA-01 |
| CASE-SCOPE-01 | 全局变量改局部变量 | 符合 / V-SCOPE-01 |
| CASE-UNCERTAIN-01 | 缺前提 | 无法判定 |
| CASE-NEG-CACHE-01 | 缓存宣称强一致 | 不符合 / I-CACHE-CONSISTENCY |
| CASE-NEG-RETRY-01 | 重试宣称解决容量 | 不符合 / I-RETRY-CAPACITY |
| CASE-NO-PATTERN-01 | 无 active pattern | 无法判定 / 人工确认 |

当前 JSON 是测试方案快照；正式环境可由 Eval KB 的 EvaluationPlan/Oracle 页面导出为同结构。

## 判定范围

oracle 可以设置 `"verdict_scope": "causal"`：`score_case` 只取报告里带“因果/专项”标题的章节判定 verdict，
避免把规则表中的单个“符合”误当成总体结论；总体结论由报告其他章节承载（例如 `总体结论：不拟通过`）。

## 多文档回归

每个 case 使用独立 WeKnora 会话和独立输入文档；一次 `test_live_review_cases` 会顺序跑完所有已配置 `BODHI_CASE_*` 的 case，互不污染。

可用 `BODHI_AGENT_E2E_CASE=CASE-CB-01` 只跑单个 case。

| case | 可上传文档 |
|---|---|
| CASE-CB-01 | `docs/cases/技术方案评审/e2e-CASE-CB-01.md` |
| CASE-CACHE-01 | `docs/cases/技术方案评审/e2e-CASE-CACHE-01.md` |
| CASE-SCOPE-01 | `docs/cases/技术方案评审/e2e-CASE-SCOPE-01.md` |
| CASE-UNCERTAIN-01 | `docs/cases/技术方案评审/e2e-CASE-UNCERTAIN-01.md` |
| CASE-NEG-CACHE-01 | `docs/cases/技术方案评审/e2e-CASE-NEG-CACHE-01.md` |
| CASE-NEG-RETRY-01 | `docs/cases/技术方案评审/e2e-CASE-NEG-RETRY-01.md` |
| CASE-NOPATTERN-01 | `docs/cases/技术方案评审/e2e-CASE-NOPATTERN-01.md` |

当前 harness 按“一个 case 一个文档 + 一个会话”执行；同一会话多附件不属于本轮范围，避免文档 ID 和轮次混淆。


## 库范围

`WeKnoraClient.from_env()` 从智能体的 `system_prompt` 解析 `【库范围】domain_kb: … | work_kb: …`，再按库名解析成 id；不再硬编码默认知识库名。


## 建模智能体：任务选择 E2E（2026-11-18）

`test_modeling_task_select.py` 验证 domain 只返回按活动分组的任务（goal / io / roles），
智能体选 1 个任务后才 page，不允许 page 全部任务。

```bash
BODHI_RUN_AGENT_E2E=1 \
BODHI_MODELING_AGENT_NAME=企业知识管理建模智能体 \
pytest -q -m agent_e2e tests/agent_e2e/test_modeling_task_select.py
```

case 输入在 `cases/modeling_task_select_plan.json`，预期任务在 oracle；两者分离。
