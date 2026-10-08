# 知识写入契约规范（草案 v0.1 · 2026-10-08）

> 目的：把当前**7 条各写各的**知识写入通道，收敛为**一个内核 + 统一契约**。
> 本文件是**规范**（待确认），确认后按 §9 逐入口替换、验证。实施前不改代码。

---

## 0. 背景（为什么需要）

现状：正文有 7 条生成路径（`build_new_page` / `merge_content` / `build_pending_page` /
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

### 4.2 渲染长度上限 = **150 字**
- 渲染进正文的值 > 150 字 → **截断**：`<前 150 字>……（完整内容见「数据属性」面板）`；
- 完整值仍在 `attributes`（→ 图节点属性 + `page_metadata`），**由前端「数据属性」面板打开查看**（面板是后续前端项）。

### 4.3 解析（双读兼容）
渲染格式改了，**同步改 3 个解析器**，且兼容旧 `- 名称 = 值`：
- `ke_graph._attrs_from_content`
- `server.detail_attributes`
- `ke_audit.data_attrs`
新格式按「`### 名称` 起、到下一个 `###`/`## ` 止」为一条属性（值可多行/含 markdown）。

### 4.4 定义不重复
`definition_key` 指定的键**只出现在正文首段**，**属性段里跳过它**（消除"首段一份、属性段又一份"）。

---

## 5. 溯源模型（4 层，统一 `source` 参数）

| 层 | 载体 | 类型 | 说明 |
|---|---|---|---|
| L1 会话 | `bmm:sourceSession` | **对象属性（图边）** | → `bmm:KnowledgeSession` 页；**有会话身份自动补**（幂等）；报告类无会话身份 → 回执 `warn` |
| L2 片段 | `bmm:sourceLocator` | **数据属性** | 来源的**会话片段定位**（如 `session/S-20261008-01/p1#轮3/段5`）；进 `attributes` → 图属性 + 属性面板 |
| L3 文档 | `source_refs` / `chunk_refs` + `## 原文依据` | 列 + 正文小节 | 逐字原文 + 定位（文档级/片段级） |
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

`write_knowledge` 内部固定：**① 建/更新图节点（属性）→ ② 写图边 → ③ 写 PG 页（content 渲染）→ ④ 补 L1~L4 溯源**。
图写失败不阻断 PG（巡检/回填兜底），但回执必须说清（`graph.ok=false`）。

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
- **W4 定义重复**：首段与属性段出现同一 `definition_key` → low。

---

## 11. 待办 / 未决

- [ ] 前端「数据属性面板」（打开看完整属性值）——前端项；
- [ ] `## 属性（数据属性）` 格式切换的**存量双读**灰度；
- [ ] RAW 入口逐个评估迁移；
- [ ] `strict_source`（无溯源即拒写）是否默认开启。
