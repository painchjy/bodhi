# P5 · 内网问题清单（2026-10-22 用户口径 4.1–4.6）

> 定位：这些是**内网真实使用**发现的问题，用来验证新架构（图本 / 工具面 / 技能面）是否合理。
> 排序原则：**收益 ÷ 成本**，P4（报告模板化）最后。

| # | 问题 | 状态 | 落点 |
|---|---|---|---|
| **4.5** | 上下文**解绑**失败（`action 只能是 decide / pull`） | ✅ **已落地**（W1） | `ke_context.authority_detach` + `context_authority_apply{action:"detach"}` |
| **4.3** | `reference_lookup` 内网 URL 超时（`urlopen error timed out`） | ✅ **已落地**（W1） | `ke_review._ref_fetch`：代理/超时/白名单 + 结构化 `reason` |
| **4.1** | `knowledge_lookup` 不支持组合关键字，智能体反复试错 | ⏳ W2 | `ke_lookup` v2：多词 AND/OR/短语/排除 + `plan`/`suggestions`/`strategy` |
| **4.4** | 智能体只认领域库、不认本体；"流程→活动→任务"被当关键字 | ⏳ W2（与 4.1 同片） | 类模糊匹配（`ke_ontology.classes()`）+ `mode="route"` 类型路径 |
| **4.6** | 会话分页判定过简、溯源缺环 | ⏳ W3 | 扩展 `ke_pages.ensure_session_page` + 弱信号 ack + 分页反列产出 |
| **4.2** | report 基类 → `schema.org/CreativeWork` + 文档模板子类 | ⏳ W4（最后） | 图属性 `articleBody` + `schema:DocumentTemplate` + `applicableReportType` |

---

## 4.5 解绑（方案 1-A，已落地）

- **诊断**：不是误删 —— 内核一直只有 `authority_preview/decide/pull`（`docs/context-mapping-plan.md` §13.14.2 通道矩阵就是三列）；
  巡检 F2 文案却承诺"解除副本"，属**文案与能力不一致**。
- **语义**：`detach` = 从概念页「## 权威与副本」表**摘掉该库** + 该副本页
  `page_metadata.authority` 改 `role=detached` ⇒ 该库**恢复可本地编辑**。
- **不删副本页**：删页走 `audit_purge{slugs}`/UI；两动作解耦，**任意顺序都安全**（`detach` 容忍页已删）。
- **两步各写单库**：① 概念库（登记表）② 副本库（自述）；`apply=false` 先预览（影响面 + `required_risks` + `ticket`）。
- **幂等**：表里无该行 / 自述已 detached / 页已删 ⇒ `already_detached=true`。
- **配套修正（必须）**：`rebuild_cache` 与 `authority_preview` 都改为**按表派**——
  之前按"同名组全量"派生 ⇒ 解绑后 `replica_protect` 仍在、写保护不解除 ✗。
- 用例：`tests/test_mcp_tools_context_detach.py`（全链路 MCP 实调：decide → 副本写被拒 → detach → 可写 → 幂等）。

## 4.3 参考规范（已落地）

- 环境：`BODHI_HTTP_PROXY` / `BODHI_REF_TIMEOUT`(默认 8s) / `BODHI_REF_HOSTS`(白名单，空=不限)。
- 失败结构化：`reason ∈ timeout｜dns｜tls｜blocked｜conn_refused｜http_4xx｜http_5xx｜empty`。
- 总预算 ≤ 2×timeout（避开反代 60s）；4xx 不重试；`allow_fetch=false` 回 `fetch_disabled` + 提示下一步。
- 用例：`tests/test_reference_lookup.py`（真抓本机 HTTP：200 / 404 / 超时 / 白名单）。
