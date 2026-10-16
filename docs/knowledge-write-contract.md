# 知识写入契约规范（草案 v0.1 · 2026-10-08）

> 目的：把当前**7 条各写各的**知识写入通道，收敛为**一个内核 + 统一契约**。
> 本文件是**规范**（待确认），确认后按 §9 逐入口替换、验证。实施前不改代码。

---

## 0. 背景（为什么需要）

现状：正文有 5 条生成路径（`build_new_page` /
`review_apply` / `write_page` / `upsert_page` / `ke_import`）；「定义」有 2 套并存
（`element["definition"]` 与 `bmm:definition`/`description` 数据属性）；关系有 2 条技术并存
（图边直写 vs 正文关系行投影）；会话溯源只有 1/7 通道有。→ 同一知识"放哪、怎么渲、怎么溯源"随渠道而变。

目标：**一个内核、一份输入契约、三种渲染模式、统一溯源、图本优先**。

---

## 1. 统一内核

所有写入器（7 条通道 + 未来的批量/导入）**只准**调用：

```
ke_pages.write_knowledge(kb_id, spec, *, dry_run=False) -> {applied, slug, version, graph, provenance, warnings}
ke_pages.write_knowledge_batch(kb_id, specs, *, dry_run=False) -> {applied, pages[], graph, warnings}
```

现有入口（`save_elements` / `save_knowledge` / `review_apply` / `write_page` / `upsert_page` / `import_apply`）
改为它的**薄封装**，不再各自拼正文。

---

## 2. 输入契约（`spec`）

| 字段 | 必填 | 说明 |
|---|---|---|
| `kb_id` | ✔ | 目标库（唯一，写路径 fail-closed） |
| `title` | ✔ | 页标题 |
| `page_type` | ✔ | 本体类（`模块:类`）；非本体页用 `summary` 等 |
| `slug` |  | 缺省由 `(kb_id,page_type,title)` 确定性派生 |
| `mode` | ✔ | `"document"` \| `"entity"` \| `"raw"`（见 §3） |
| `wiki_content` |  | 正文 md。**document/raw 必填**；entity 由内核生成（传了则忽略并 warn） |
| `attributes` |  | 数据属性 `{key: value}`（key 可 prefixed） |
| `definition_key` |  | **唯一的"定义属性"**；缺省 `definition`。document 模式可 `null` |
| `first_paragraph_from` |  | 正文首段取自哪个属性；缺省= `definition_key`；`""`=不派生（正文自带首段） |
| `relations` |  | `[{type, target_slug, label?, properties?}]`（图边，见 §6） |
| `render_attributes` |  | 数据属性是否渲进正文；缺省按模式（§3） |
| `source` | ✔ | 统一溯源，见 §5（**会话或文档至少给一个**，否则 warn/拒） |

---

## 3. 三种展示 / 渲染模式

| 模式 | 谁用 | `wiki_content` | `attributes` | 首段/定义 | `render_attributes` 默认 |
|---|---|---|---|---|---|
| **M-document** | 评审报告、评测结果、问题反馈单、设计报告 | **调用方给（原文）** | **必填**（结构化摘要） | 正文自带首段（`definition_key=null`） | **false**（报告正文自足） |
| **M-entity** | 领域建模节点等一般本体知识 | **内核生成** | **只填 attributes** | 首段=定义属性；**属性段不重复该键** | **true** |
| **M-raw** | 概念页 / 设计页 / 结构化模板（存量兼容） | **调用方给（原文）** | 可选 | 调用方自理 | **false** |

> **M-raw 先保持现状**（过渡用）；后续逐入口评估能否迁移为 M-document / M-entity（见 §9）。

---

## 4. 数据属性渲染（正文里的 `## 属性（数据属性）`）

### 4.1 格式（`M-entity` 用；`document` 默认不渲）
```
## 属性（数据属性）

### 适用范围（bmm:ruleScope）

§6 容灾

### 实现方式（bmm:ruleImplementation）

图检索
```
- 标题行：`### <中文标签>（<prefixed>）`（标签/range 由本体元数据渲染）；
- 值：**原样 markdown**（可多行），紧随其后，块之间空一行。

### 4.2 渲染长度：**不截断**（2026-10-13 用户口径）
- 数据属性值**原样渲染进正文**（可多行 markdown）；`ATTR_RENDER_LIMIT = 0` ⇒ 早前的
  「> 150 字截断 + 请到『数据属性』面板看完整值」**已取消**（值与正文一致，模型/人不再看到半句话）；
- 事实源仍是 `attributes`（→ 图节点属性 + `page_metadata`）；正文是它的**只读渲染**。

### 4.2.1 编辑入口（**方案 D**，2026-10-13 用户口径）
| 渲染模式 | 谁说了算 | 怎么改 |
|---|---|---|
| **entity**（一般本体知识：规则/服务/实体…） | **数据属性是事实源**；正文**只读渲染** | 「数据属性」面板 → `POST /bodhi/page/attributes`（或 MCP `page_attributes_set`、CLI `ke_pages.set_page_attributes`）；**三处同写**（PG `page_metadata.ontology.attributes` + 图节点属性 + 正文属性段重渲染），幂等、写前快照、`version+1`。**改正文不生效**（读路径不解析正文） |
| **document / report**（评审报告/需求分析/评测结果…） | **正文是事实源** | 直接改正文；数据属性（若有）**同样可改**并三处同写（2026-10-15 起不再按模式拒改），但**判定只读正文** |
| **本体模型库**（`ontology:*` 页） | **只读**（由加载编译生成） | 任何写口（`save_knowledge` / `set_page_attributes` / 设计页写入）**一律拒绝**，回执提示改 `ontology/*.ttl` 后走**加载编译**（`ke_admin.load_model` / 前端「上传本体文件」） |
- 只读：`GET /bodhi/page/attributes?kb_id=&slug=` → `attributes`（现值）+ `source`（meta/graph/content）
  + `editable`（2026-10-15 起**恒为真**，是否可改由写口按本体/键校验）+ `allowed`（该类可编辑属性）+ `drift`。

### 4.2.2 定义（`skos:definition`）与 PG `summary` 同步（**2026-10-15 用户口径**）
| 规则 | 说明 |
|---|---|
| 键 | 定义统一用 **`skos:definition`**（SKOS 标准注释属性，编译白名单 `PLATFORM_ANNOTATION_IRIS`；**无 domain ⇒ 全类可用**，不给类逐个声明、不 import skos）；备注用 `skos:note` |
| 三处 | **PG `summary`（WeKnora 原生「定义」框）↔ `page_metadata.ontology.attributes` ↔ 图节点属性**；`summary` 恒为定义值 |
| 正文 | **不渲染定义**（`SUMMARY_SYNC_LOCALS` 让 `render_attributes_section()` 跳过定义键）——正文只渲染**其它**数据属性的取值 |
| 写口 | 面板「编辑」（预演 → 确认写入）改 `summary + 元数据 + 图`；面板「**以摘要为准同步**」= `POST /bodhi/page/attributes {sync_from_summary: true}` → 把 `summary` 写进元数据 + 图（**不含正文**） |
| 漂移 | `drift` = 元数据/图/正文三处不一致的键 **∪** `summary` 非空却与定义键不一致（含"三处都没有"）；本体页不参与该比对（其 `summary` 就是类 `rdfs:comment`） |
| 报告判定 | 渲染模式 = **是否 `bmm:Report` 子类**（不再看 `wikiContent` 的 domain）；`document` → 正文为主，`entity` → 数据属性为主 |
| 本体页展示 | 本体类页正文的「属性定义」小节**分「自身 / 继承自 X」×「数据属性 / 对象属性」**（祖先闭包**跨模块**）；本体页**不展示实例属性** |
| **单一写核**（2026-10-15/16） | 实例页 `summary` **只由 `ke_pages.derive_summary()` 派生**（定义 → 备注 → 回落），并在写口**强制**：`ke_pages.upsert_page()` 内置护栏（属性里有定义键 ⇒ 忽略调用方传的 summary），`write_knowledge*` / `_bulk_write_pages()` / 设计页 / 面板 / 以摘要为准同步**同此一处**；`ke_import` 的批量导入亦走该核心（其更新路径曾用 title 覆盖 ✗ 已修）。**本体模型库的 `summary`（= `rdfs:comment`）不在其列** —— 由"加载页"（`ontology_wiki.py` + `ke_admin.load_model`）独立生成，两条链**严格隔离**、互不复用 |
| **别名规则**（2026-10-16） | 前端「标题下方的别名标签」（上游 `WikiBrowser.vue` 的 `selectedPage.aliases`）：实例页 `aliases` **只由 `ke_pages.derive_aliases()` 派生** —— ① 属性里的 `rdfs:label` / `skos:altLabel`（注释属性，**谁用谁算别名**，不分本体/实例）；② 本体标记 **`bodhi:aliasKey true`** 的属性值（如 `bmm:systemAbbr` 主/子系统简称）；③ 页面**已有**值（并集保留）；**排除与标题同名**的值；上限 8。写口同上（`upsert_page` / `_bulk_write_pages`）。**不引入 `bodhi:alias`**：别名一律「标记 + 已有属性」；待办 = 编译器把标记带进模型 + `ke_ontology.alias_keys()` |
| **建模口径：系统已管理的数据不本体化**（2026-10-16） | 由**外部系统维护/查询**的数据（如「开源软件清单」由 **IT 工作平台**维护）**不进本体** ✗（既不建"类+实例集合"，也不建专用数据属性）：改为 **`bpmn:DataObject` 实例** ✓ —— **属性清单**写在页的 `skos:definition`、**测试样例数据**写在 `skos:note`；查询由**挡板**（`agent:Stub`）提供，**挡板的输出就是该 DataObject** ✓，与外部系统对接后**换正式接口、挡板下线** ✓。判定类规则（如"开源软件必须在认证清单内且版本一致"）因此变成"**抽取值 vs 接口/清单的集合比对**" ✓（不依赖大模型猜 ✗）。权威性由结构推导：`DataObject --dataObjectBelongsToTask--> Task --taskImplementedBySubSystem--> SubSystem` ✓ |

### 4.3 解析（双读兼容）
渲染格式改了，**同步改 3 个解析器**，且兼容旧 `- 名称 = 值`：
- `ke_graph._attrs_from_content`
- `server.detail_attributes`
- `ke_audit.data_attrs`
新格式按「`### 名称` 起、到下一个 `###`/`## ` 止」为一条属性（值可多行/含 markdown）。
**读侧**（`ke_review.rules_of_policy` / 文档评审 / 巡检）**不解析正文**，只读**元数据 `attributes` +
图节点属性**（entity 的事实源，见 §4.2.1）；正文解析器（下面 3 个）只服务**展示/落图/巡检比对**。

### 4.4 定义不重复
`definition_key` 指定的键**只出现在正文首段**，**属性段里跳过它**（消除"首段一份、属性段又一份"）。

---

## 5. 溯源模型（4 层，统一 `source` 参数）

| 层 | 载体 | 类型 | 说明 |
|---|---|---|---|
| L1 会话 | `bmm:sourceSession` | **对象属性（图边）** | → `bmm:KnowledgeSession` 页；**有会话身份自动补**（幂等）；报告类无会话身份 → 回执 `warn` |
| L2 片段 | `bmm:sourceLocator` | **数据属性** | 来源的**会话片段定位**（如 `session/S-20261008-01/p1#轮3/段5`）；进 `attributes` → 图属性 + 属性面板 |
| L3 文档 | `source_refs` / `chunk_refs` + `## 原文依据` | 列 + 正文小节 | 逐字原文 + 定位（文档级/片段级）。**2026-10-09 起降级为「有就写」的兼容字段**（WeKnora 内建列）：溯源**主口径 = L1 会话边 + 正文依据**，巡检 C1/C3 判「**会话边 或 source_refs 任一**」 |
| L4 页面 | `page_metadata.design.derived_from` + `## 溯源` | 元数据 + 正文小节 | 上游 page slug 链 |

`source` 入参：`{session_no?, part_no?, doc_refs?, chunk_refs?, derived_from?, source_text?}`。
**校验**：L1 或 L3 至少一个；都没有 → `warn`（可配 `strict_source=true` 则拒写）。

---

## 6. 关系保存（收口到一个内核方法）

- **图边 = 唯一事实源**。`spec.relations` → 内核内 `ke_graph.add_edge`（域/值域校验）；
- **取消"正文关系行 → 投影"这条**：不再生成 `## 本体关系` 小节、`_apply_content_update` 不再从正文抽边；
- 单边增删改仍留维护口：`add_relation` / `update_edge` / `delete_edge`（面板/裁决用）；
- 回执给 `graph.edges_written` / `edge_errors`（沿用已加的回归防护）。

---

## 7. 批量保存（结构化导入用）

`write_knowledge_batch(kb_id, specs)`：**一次事务**写多页 + 多边：
- 供 `import_apply` / 批量建模 / 批量补关系；
- 沿用"先图后 PG"；
- 幂等（同 slug 合并更新；内容未变零写入）。

---

## 8. 落库顺序（图本优先）

`write_knowledge` 内部固定：**① 建/更新图节点（属性）→ ② 写 PG 页（content 渲染）→ ③ 写图边 → ④ 补 L1~L4 溯源**。

> **②③ 的次序**：建边要校验 domain/range 与"目标页存在"，所以**页先落 PG、边后写**（节点仍先入图）。
> 批量路径把三者各自并成一条 `UNWIND`/一条 SQL（图节点 1 次、PG 2 次、边按关系类型数次），
> 详见 `docs/knowledge-save-perf-and-staged-modeling.md`。
> 图写失败不阻断 PG（巡检/回填兜底），但回执必须说清（`graph.ok=false`）。

---

## 9. 各入口 → 内核 映射（迁移计划）

| 入口 | 目标模式 | 备注 |
|---|---|---|
| `save_knowledge(stage="graph")` | M-entity | 主要来源 |
| `save_knowledge(stage="report")` | M-document | 报告正文=`content_md`；**补齐 sourceSession** |
| `review_apply`（评审报告） | M-document | 正文自拼 → 走 M-document；**补齐 sourceSession** |
| `import_apply` / `import_refresh` | M-entity（+批量） | 改走 `write_knowledge_batch` |
| `ke_design.write_page` | M-raw →（评估）M-entity/M-document | 过渡 |
| `ke_pages.upsert_page`（概念/映射） | M-raw | 过渡 |
| `/bodhi/relations/*`、`resolve_link_candidate` | 关系维护口 | 不变 |

---

## 10. 巡检项（新增）

- **W1 缺会话溯源**：非会话/非本体模型页缺 `bmm:sourceSession` 边 → medium；
- **W2 属性段格式不合规**：`## 属性（数据属性）` 非 `### 名称` 格式（旧页豁免/双读）→ low；
- **W3 报告页正文为空**：`mode=document` 类的页 `content` 为空 → high；
- **W4 定义重复**：首段与属性段出现同一 `definition_key` → low；
- **W5 摘要漂移（清单，供人工核查）**：`summary` 与定义不同（medium）／`summary` 空但有定义·备注（low）
  → 用面板 **「编辑」保存一次**（经内核护栏回到定义值）修复，**不批量处理**；
- **W7 别名同名噪音（清单）**：`aliases` 里含与标题同名的标签（旧 `save_elements` 默认 `[name]`）→ low，
  存量**先保留**，重新写入时内核不再产生（W6 属本体编译器的 `owl:inverseOf` 检查，勿混用）。

---

## 11. 待办 / 未决

- [ ] 前端「数据属性面板」（打开看完整属性值）——前端项；
- [ ] `## 属性（数据属性）` 格式切换的**存量双读**灰度；
- [ ] RAW 入口逐个评估迁移；
- [ ] `strict_source`（无溯源即拒写）是否默认开启。
