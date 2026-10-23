# P5 · 内网问题清单（2026-10-22 用户口径 4.1–4.6）

> 定位：这些是**内网真实使用**发现的问题，用来验证新架构（图本 / 工具面 / 技能面）是否合理。
> 排序原则：**收益 ÷ 成本**，P4（报告模板化）最后。

| # | 问题 | 状态 | 落点 |
|---|---|---|---|
| **4.5** | 上下文**解绑**失败（`action 只能是 decide / pull`） | ✅ **已落地**（W1） | `ke_context.authority_detach` + `context_authority_apply{action:"detach"}` |
| **4.3** | `reference_lookup` 内网 URL 超时（`urlopen error timed out`） | ✅ **已落地**（W1） | `ke_review._ref_fetch`：代理/超时/白名单 + 结构化 `reason` |
| **4.1** | `knowledge_lookup` 不支持组合关键字，智能体反复试错 | ✅ **已落地**（W2） | `ke_lookup` v2：多词 AND/OR/短语/排除 + `plan`/`suggestions`/`strategy` + AND→OR 自动降级 |
| **4.4** | 智能体只认领域库、不认本体；"流程→活动→任务"被当关键字 | ✅ **已落地**（W2） | 类模糊匹配（`ke_ontology.class_meta()`，强/弱证据）+ `mode="route"` 类型路径 + `ontology_types` 回 `parents/children` |
| **4.6** | 会话分页判定过简、溯源缺环 | ✅ **已落地**（W3） | `ke_pages.session_part_plan`（四信号 + 证据）+ `write_knowledge` 门禁 + `/pN` 定位 + 分页反列产出 |
| **4.2** | report 基类 → `schema.org/CreativeWork` + 文档模板子类 | ✅ **已落地**（W4a/W4b） | 图属性 `articleBody` + `schema:DocumentTemplate` + `applicableReportType`；`stage="report"` 直写模板页；`ke_report` 按模板校验章节（`tests/test_report_template.py`） |

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

## 4.1 + 4.4 检索策略 v2（已落地）

**判定顺序（确定性，零 LLM）**：切词 → 每词查本体类（`ke_ontology.class_meta()` 的 label / 本地名）：

| 证据 | 判据 | 处置 |
|---|---|---|
| **强** | 完全相等 ｜ 词是标签的**词尾**（"流程"⊂"业务流程"）｜ 占比 ≥75% ｜ 命中本地名 | 圈**范围类**（+全部子类） |
| **弱** | 只是标签的**前缀修饰**（"技术方案"⊂"技术方案评审报告"） | **不圈范围**，只进 `plan.class_candidates`，仍当文本词 |
| 多候选互不同族 | top1−top2 < 10 且无祖先关系 | `ambiguity=true` + `class_candidates`，**不静默选错** |

**组合语义**：多类且有通路 ⇒ `mode=route` 出**类型路径**（不做 AND ✗）｜单类无文本词 ⇒ `mode=instances` 列实例（含子类）｜单类+文本词 ⇒ 范围内 AND（`name` / 图 `articleBody`）｜无类+文本词 ⇒ 图全文检索。

**最少来回三条硬规则**：
1. **AND 空 ⇒ 一次调用内降级 OR**，并标 `plan.relaxed_by="and→or"`；
2. 每条 `suggestions[]` 都带 `tool` + **完整 args**（父类扩大 / 列实例 / 换库 / `rules` 入口 / 类型分布）；
3. **空结果必带 `next`** + ≥2 条不同方向建议。

**模式**：`keyword`（默认搜索）｜`instances`｜`route`｜`path`｜`rules`｜`semantic`（W2.5 未配置时结构化说明）。

**用例**：`tests/cases/lookup_cases.json` + `tests/test_lookup_strategy.py`（C1–C9，走 MCP）。

## 4.6 会话分页（已落地，2026-10-22 · 1-C）

**分页规则（有判据、留证据）**：判定在 `ke_pages.session_part_plan()`（只读），执行在 `ensure_session_page()`。

| 信号 | 类型 | 判据 | 处置 |
|---|---|---|---|
| 智能体变化 | **强** | 分页证据里的 `agent` ≠ 本次 | 自动开新分页 `session/<no>/p<n>` |
| 用户角色变化 | **强** | `role` 不同 | 同上 |
| **文档变化** | **强** | `doc_id`（缺省取 `doc_refs[0]`）不同 | 同上 |
| **意图变化** | **弱** | `intent` 不同 | **拒写** + `need_session_part=true` + `ask_user`（问用户是否进入新任务）；带 `acknowledge_intent_change=true` 才放行，并**把新意图写回该分页证据**（否则每次都再问） |

**证据与溯源**：
- 每页 `page_metadata.session_part = {action, fingerprint, agent, role, doc_id, intent, reasons[], at}`；
  开新分页时正文多一节「## 分页理由」（逐条 `signal: from → to`）；
- 知识页 `bmm:sourceLocator` **必带 `/pN`**（起始页也算 `p1`）；
- 分页页正文「## 本轮产出」**反列**本轮写的知识页 slug ⇒ 知识→分页→会话双向可溯。

**门禁口径**：`write_knowledge` 在写之前判定（`dry_run` 不拦）；服务端 `save_knowledge` 遇内核
`applied=False` **不得伪装成功**，原样透出 `need_session_part / session_part / ask_user`。

**用例**：`tests/test_session_paging.py`（5 例：强信号换页 / 弱信号拒写 / ack 通过且不换页 / `/pN` 定位 / 反列产出）。

## 4.2 报告模板化（✅ 已完成：W4a 正文主键 + W4b 模板页/模板读取）

### W4a 正文主键 `articleBody`（图本，已完成）

| 层 | 口径 |
|---|---|
| **Neo4j `BodhiInstance`** | 正文主键 **`articleBody`**（schema.org 对齐）；旧键 `wiki_content` **双写**兼容；读一律 `coalesce(articleBody, wiki_content)` |
| **PG `wiki_pages.content`** | **渲染缓存**（可随时从图重建）；不含第二份"事实" |
| **PG `page_metadata`** | **不再存正文**：写路径剔除 `wikiContent`/`bmm:wikiContent`；历史残留用迁移工具清 |

**迁移工具**（新工具 `migrate_article_body`，默认 `dry_run=true`）：
1. `graph`：把只有 `wiki_content` 的节点回填 `articleBody`（幂等）；
2. `page_metadata`：清掉属性里残留的 `wikiContent` 键（幂等）。
用例：`tests/test_article_body.py`（写路径双写 / 迁移幂等 / 片段取自图正文）。

### W4b 模板（✅ 已完成）

- 本体：`schema:CreativeWork`（本地子集，**不远程抓取**）→ `bmm:Report rdfs:subClassOf schema:CreativeWork`；
  `schema:DocumentTemplate`（模板=实例页，章节**只在正文**）；`schema:articleBody`（DP）
- 模板属性**只 3 个**：`applicableReportType`（多值，报告类型类名）、`templateSelector`（规则页 slug，可空）
- **模板页维护**：`save_knowledge(stage="report")` + `report.page_type="schema:DocumentTemplate"` + `report.attributes`
  —— 一次写入，**不需要 retag**（报告/模板同属 `schema:CreativeWork`）；`stage="graph"` 仍**只写实体类**
- **模板读取**（`ke_report`）：`resolve_template`（按 `applicableReportType` 自动选；多候选按 `templateSelector`；
  仍多 ⇒ `ambiguous` + `candidates`，**不静默选**）→ `parse_template_sections`（解析模板正文「章节结构」表）
  → `check_report_md(md, sections)`（按模板**必填**章节校验，只告警不拒写）
- **回执可追溯**：`template`（slug/version/章节数/必填数/候选）、`missing_required_sections`、
  `template_fallback_reason`（没用上模板的原因）
- 用例：`tests/test_report_template.py`（6 例：模板页直写 / 自动选中 / 缺必填章节 / 换模板 / 无模板回落 / graph 跳过文档类）

## 4.3 参考规范（已落地）

- 环境：`BODHI_HTTP_PROXY` / `BODHI_REF_TIMEOUT`(默认 8s) / `BODHI_REF_HOSTS`(白名单，空=不限)。
- 失败结构化：`reason ∈ timeout｜dns｜tls｜blocked｜conn_refused｜http_4xx｜http_5xx｜empty`。
- 总预算 ≤ 2×timeout（避开反代 60s）；4xx 不重试；`allow_fetch=false` 回 `fetch_disabled` + 提示下一步。
- 用例：`tests/test_reference_lookup.py`（真抓本机 HTTP：200 / 404 / 超时 / 白名单）。
