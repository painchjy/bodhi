---
id: session_provenance
name: 会话知识溯源（知识来源＝智能体会话）
description: 把智能体会话登记成可溯源的知识页（起始页 + 分页，本体类型 bmm:KnowledgeSession），每条知识的来源用 bmm:sourceSession + bmm:sourceLocator 定位到会话上下文（分页 + 轮次/段落）；同时维护改进建议的分类与总览页。字段一律 ontology_types("bmm") 现查，不自造
when: 用户要"把这次会话的知识/澄清/改进建议记下来""查某结论出自哪次会话""看看待受理的改进建议"时
models: [bmm]
default_model: bmm
stages: [identify, page, bind, board, verify]
tools: [save_knowledge, wiki_search, wiki_read_page, grep_chunks, list_knowledge_chunks, doc_outline, link_candidates, list_link_candidates, resolve_link_candidate, audit_scan]
version: 0
---

# 会话知识溯源（技能）

> **第一闭环的核心能力**：知识的产生与变化都发生在**与智能体的会话**里 → **会话即来源**。
> 会话在 WeKnora 里虽有保存，但**被删就断链** → 必须**作为知识写进知识库**。
> **越界规则**：本技能**只写页面、不改本体**（不新增类/属性/词表）。缺什么本体，一律写成**改进建议**（见 §4）。

## 0. 第 0 步：先定三件事
1. **会话编号**：**取自运行时（WeKnora 会话编号）**，例如运行时给出的 `session_id` / 会话标识。
   ⚠️ **不要自编编号**（尤其**不要按日期编** `S-YYYYMMDD-NN`）：跨天继续同一会话时必须**沿用同一编号**，否则连续性断裂（会新建会话 + 新建 p1）。
   **落库时必带**：`save_knowledge(..., session_no="<运行时给的会话编号>")`（uuid 形态）。
   拿不到运行时编号 → **不要猜、不要编**：`mode="apply"` 会**拒绝写库**（回执 `need_session=true`），
   此时请向用户说明「需要运行时把会话编号传给我」，**不要退回自编号**。
2. **会话种类** `sessionKind`：`需求澄清` ｜ `领域建模` ｜ `效果评估` ｜ `改进建议`（可多段会话各自不同）；
3. **目标库**：本次产出的知识写哪个库（**会话页与它产出的知识必须同库**）。

## 0b. 身份与租户：**只能由系统注入**（2026-10-01，安全口径）

> ⚠️ **禁止询问、禁止接受、禁止编造** `tenantName`（租户名称）与 `agentName`（智能体名称）。
> 这两项是**运行身份**，不是业务知识：

| 字段 | 谁提供 | 规则 |
|---|---|---|
| `bmm:tenantName` | **落库工具**（按**目标知识库所属租户**注入） | 智能体**不要问用户**；用户说了也**不采信**（会被服务端丢弃并覆盖） |
| `bmm:agentName` | **落库工具**（按 `session_no` → `sessions.agent_config.agent_id` → `custom_agents.name`，即**会话选择的智能体**） | 智能体**不要写、不要编、不要问**；服务端一律覆盖 |
| `bmm:sessionNo` | **运行时**（WeKnora 会话编号，uuid） | **必须原样作为工具参数 `session_no` 传入**；**不得自编**（服务端会去 `sessions` 表校验：查不到即拒绝写库） |
| `bmm:sessionName`/`initialQuestion`/`startedAt`/`partNo`/`isAuthoritative`/`sessionStatus` | 智能体（会话事实） | 正常填写 |

**为什么**：租户/智能体身份若由对话内容决定，就能被**伪造**（同一个人可以说自己是任何租户）。
**做法**：落库时**照常带上会话事实字段**，`tenantName`/`agentName` **留空即可** —— 服务端按 `session_no` 解析后注入权威值（回执 `session_identity` 说明用了哪个会话/智能体）。
**fail-closed（2026-10-04 用户口径）**：**取不到会话的智能体名称 → 拒绝触碰知识库** —— `mode="apply"` 会直接返回 `need_session=true` + `reason`；此时**不要重试编造编号**，而是把运行时给的编号原样传入（或请用户确认会话）。
**附带收益**：服务端还会给本次**所有页**自动挂 `bmm:sourceSession`（= `session/<会话编号>`），所以非会话页也天然带来源会话。

## 0c. 需求澄清 = **必须留痕的分页**（2026-10-04 用户实测口径）

> **问题**：用户在会话里做的**需求澄清/修正**（如"执行级别不是『总结』、应为『严格执行』"）当时只改进了
> 知识页的属性，**会话页里查不到"用户说过什么"** → 溯源断在会话侧。
> **规则**：出现**用户澄清/修正/否定**时，除了改知识页，还要**把澄清本身记进会话页**：
> 1. **承载页**：优先**当前会话分页**（`session/<会话编号>/p<N>`，`N` = 已有分页数 + 1，从 `p2` 起）；
>    澄清轮次本身就是**开分页的正当理由**（§1「切段原因」已含此项）；起始页只放基本信息，**不写澄清正文**。
> 2. **落库**：把分页页**一起交给 `save_knowledge`**（`bmm:KnowledgeSession` 类型、同一 `bmm:sessionNo`/`bmm:sessionName`，
>    `bmm:partNo=<N>`、`bmm:sessionStatus=bmm:SessionActive`），正文小节：
>
>    ```markdown
>    ## 本段范围与切段原因
>    需求澄清（用户对建模/评审结果的修正）
>
>    ## 需求澄清
>    - 轮次 / 时间：轮2 · 2026-10-05 10:12（+0800）
>    - 用户原话：「合理性评审的执行级别不应该是『总结』……手段不应是具体产物，建议改为构建技术方案评审智能体」
>    - 影响的知识页：[[…]]（改了哪个属性/关系，从什么改成什么）
>
>    ## 本段产出的知识页
>    - [[…]]
>    ```
> 2b. **知识页照常标注**：受影响的知识页在 `bmm:sourceLocator` 里写 `session/<会话编号>/p<N>#轮<m>/<段落>`
>    （分页序号 + 轮次/段落，与 `sourceLocator` 的定义一致）。
> 3. **关联由服务端自动挂**：落库后服务端会给本次所有页补 `- 知识来源会话（`bmm:sourceSession`）→ [[会话页 slug|标题]]`
>    （**分页优先**）→ 会话分页的「被引用（入边）」能看到本条澄清**派生出的全部知识页**。
>    ⚠️ 服务端**只做挂链**：澄清的**内容**必须由你把上面两个小节写出来（不写 = 溯源里没有"用户说过什么"）。

## 1. 三种页（**本体类型 `bmm:KnowledgeSession` + slug 约定**）

> 本体已提供该类型（`bmm` 现 32 类）：字段一律 `ontology_types("bmm")` **现查**，**不要自造**。
> 仅两项属**页级约定（不进本体）**：`sessionKind`（需求澄清｜领域建模｜效果评估｜改进建议）、`authoritativeKb`（权威库 id）。

| 页 | 类型 / slug 约定 | 内容 |
|---|---|---|
| **会话起始页** | `bmm:KnowledgeSession` @ `session/<会话编号>` | 本体属性：`bmm:sessionNo` `bmm:sessionName` `bmm:agentName` `bmm:tenantName` `bmm:initialQuestion` `bmm:startedAt` `bmm:isAuthoritative` `bmm:sessionStatus`；页级：`sessionKind` `authoritativeKb`；小节 `## 分页索引`（分页 slug + 覆盖的知识页 slug） |
| **会话分页** | `bmm:KnowledgeSession` @ `session/<会话编号>/p<序号>`（`p<序号>` 从 `p2` 起；**起始页不带序号**） | 沿用同一 `bmm:sessionNo`/`bmm:sessionName` + `bmm:partNo`；小节 `## 本段范围与切段原因`（**上下文过长 且 一组高相关知识已确认更新完成**才切段；**或** §0c 的"需求澄清"）｜`## 需求澄清`｜`## 本段产出的知识页`｜`## 本段结论摘要` |
| **知识页**（含结论页/建议页） | 各技能自己的约定（如 `review/<文档>-<策略>`） | `## 原文依据` 每条写：`bmm:sourceSession`（分页 slug）+ `bmm:sourceLocator`（轮次/消息序号/段落） |

**跨库**：会话起始页**每库一份副本**（`bmm:isAuthoritative=false` + 页级 `authoritativeKb=<初始库>` 指回权威页）；**切换目标库必须新建会话分页**（沿用同一 `bmm:sessionNo`），不得复用别库分页。

**存档**：该会话**所有分页关联的知识页都已过期/废弃** → `bmm:sessionStatus = bmm:SessionArchived`（**只改状态，不删页**）。

## 2. 主流程

1. **identify**（只读）：从当前会话/新文档里识别"需登记的内容"——**知识产出**、**澄清**、**变更**、**改进建议**；每条记 `原句/依据`（`grep_chunks`/`list_knowledge_chunks`/`wiki_read_page` 取原文）。
2. **page**：先 `ontology_types("bmm")` **现查字段** → `save_knowledge(stage=…, nodes=[{type:"bmm:KnowledgeSession", …本体属性…}], …)`（**先 `dry_run` 念清单**：要建/改哪些页、哪些分页、有没有新开分页）→ 用户确认 → `mode="apply"`：
   - **续跑规则（关键）**：先在目标库 `wiki_search` 找**同一 `sessionNo`** 的会话页 ——
     · **找到** → 沿用其 `sessionNo`/`sessionName`，**新分页序号 = 已有最大 `partNo` + 1**（例：已有 `p1` → 建 `p2`），**绝不新建会话页**；
     · **找不到** → 才新建会话页（编号来自运行时；运行时没给就留空标「待系统注入」）。
   - 首次会话 → 建**会话起始页**；有内容 → 建**分页**；
   - 已有会话 → 先 `wiki_search`/`wiki_read_page` 找到起始页，**沿用同一会话编号/名称**。
3. **bind**：给本次产出的每个知识页补 `## 原文依据`，写成 **`分页 slug + 定位`**；跨上下文引用先 `link_candidates` 登记 → `resolve_link_candidate(confirm)`。
4. **board**（仅 `sessionKind=改进建议` 时需要）：把建议条目写进建议页（字段见 §3），并刷新**改进建议总览页** `advice/board`（滚动汇总 `adviceStatus=待受理`）。
5. **verify**：`audit_scan` 确认本次涉及的页**都有原文依据**（无来源即 F3 红），并回报"会话页/分页/知识页"三层的 slug 清单。

## 3. 改进建议条目（`sessionKind=改进建议` 专用，供闭环二取件）

| 字段 | 说明 |
|---|---|
| `adviceId` | `A-YYYYMMDD-NN` |
| `adviceCategory` | **`本体类｜技能类｜工具类｜知识类`**（决定进闭环二后由谁处理） |
| `adviceText` | 一句话建议（"要改成什么"） |
| `targetRef` | 目标模型/slug（如 `bmm:OperativeBusinessRule`、`skills/document_review`） |
| `evidence` | 触发它的一次判定：**知识页 slug + 会话定位**（可复核） |
| `impact` / `expectedChange` / `priority` | 影响面 / 期望改动 / 优先级 |
| `adviceStatus` | **`待受理｜已受理｜已实施｜驳回`**（"驳回"也要留痕） |

> **受限项也要登记**：因"闭环一不改本体"而做不到的判定，写 `adviceCategory=本体类`，并在结论页标注 **"受限（原因 + adviceId）"**。

## 4. 纪律
1. **不自造本体字段**：会话/来源字段一律用 `ontology_types("bmm")` 现查（`KnowledgeSession` / `sourceSession` / `sourceLocator` / `sessionStatus` / `partNo` …）；**字段不够用 → 登记改进建议**（`adviceCategory=本体类`），不要临时造字段。
2. **同库**：会话页与其产出的知识**必须同库**；换库 → 新建分页（不是复制旧页）。
3. **不删历史**：存档=改 `sessionStatus`；被驳回的建议也保留。
4. **来源必须可定位**：每条知识都要能回到"分页 + 定位"；写不出就补，别放过。
5. **先 dry_run 再 apply**：目录/页面清单先给用户看。
6. **收尾必跑** `audit_scan`。
