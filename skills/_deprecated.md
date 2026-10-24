# 技能/智能体收敛清单（R6）

> 2026-10-17 起，该领域的做事方式统一为**规则驱动**：
> `rules_resolve` → `knowledge_plan` → `knowledge_extract` → `rule_judge` → `report_compose`
> （`fact_check` 在 `rule_judge` 之前把关证据）。
>
> 本文是**下架清单**（先标 deprecated、不删文件 ✓；确认后再物理收敛 ✓）。

## 一、保留（6 个工具 + 6 个技能）
| 层 | 名字 | 职责 |
|---|---|---|
| 工具 | `rules_resolve` | 按步骤解析规则（direct / policy 递归 / ask / task 兼容 / path 预留）✓ |
| 工具 | `ontology_paths` | 多跳路径（跨层噪音可过滤 ✓） |
| 工具 | `page_get` / `page_search` 类读 | 读页正文（图 `wiki_content` 优先 ✓） |
| 工具 | `write_knowledge`（写核） | 图 → PG，fail-closed ✓ |
| 工具 | `ke_audit`（W10 等巡检） | 图↔PG 漂移、摘要/别名一致性 ✓ |
| 工具 | `assertion` 类（会话/结论） | 溯源与留痕 ✓ |
| 技能 | `knowledge_plan` | 把 k 条规则的 knowledgeSpec **合并**成一次提取计划 ✓ |
| 技能 | `knowledge_extract` | 按计划取事实（一次并集 ✓，不逐规则重复读 ✗） |
| 技能 | `fact_check` | 逐字证据 + 可回访定位 + 三类区分（fact/compliance/opinion）✓ |
| 技能 | `rule_judge` | 规则契约 + 已抽事实 → passed/failed/**insufficient**（证据不足不得判 failed ✓） |
| 技能 | `report_compose` | 模板驱动的报告（清单类小节用表格 ✓） |
| 技能 | `rule_execute` | 执行单条规则的判定脚本（A/B 分流 ✓） |

## 二、已归档（2026-10-17 ✓ 移到 `skills/_deprecated/`，`git mv` 保留历史 ✓）
| 名字 | 现状 | 为什么收敛 |
|---|---|---|
| `document_review` | **已归档** ✓ | 被 `rule_judge` + `report_compose` 取代 ✓（它按"看文档复原规则"✗，正是要消除的 ✗） |
| `tech_review_extraction` | **已归档** ✓ | 被 `knowledge_plan` + `knowledge_extract` 取代 ✓（旧版逐规则重复提取 ✗） |
| `structured_modeling` | **已归档** ✓ | 与 `domain_modeling` 重叠 ✓；本体改动走 TTL 编译 ✓ |
| `model_recommendation` | **已归档** ✓ | 与主链无关 ✓ |
| `multimodal_extraction` | **保留**（工具能力 ✓） | `knowledge_extract` 调用它取图/表 ✓，不再作独立技能入口 ✓ |
| `kb_transfer` / `session_provenance` / `revision_trace` / `skill_development` | **保留**（平台类 ✓） | 不作技能入口 ✓ |

## 四、报告生成能力盘点与统一（2026-10-17）

### 现状：**3 个技能 + 2 条写路径**（口径不一致 ✗）
| 类型 | 名字 | 现状 | 问题 |
|---|---|---|---|
| 技能 | `report_compose` | 新（规则驱动 ✓） | 未声明"报告类由本体定 ✓ / 章节按类 comment ✓" |
| 技能 | `document_review` | 旧 ✗ | 按"看文档复原规则"✗，且自定章节 ✗ |
| 技能 | `tech_review_extraction` | 旧 ✗ | 逐规则重复提取 ✗ |
| 函数 | `ke_review.review_apply` | 旧 ✗ | 直接产 `bmm:Assessment` 报告 ✗（**entity 类型** ✗，章节自定 ✗） |
| 函数 | `server.save_knowledge(stage="report")` | 旧 ✗ | 报告阶段用 `bmm:Assessment` ✗ 同上 |

### 统一口径（唯一正确写法 ✓）
```python
ke_pages.write_knowledge(kb_id, {
    "title": "报告：{文档}（{流程号}·R{n}）",
    "page_type": "bmm:TechnicalReviewReport",   # ← 必须用本体里的报告子类 ✓
    "mode": "document",                          # ← 由 render_mode(Report 子类) 决定 ✓
    "wiki_content": <五节正文>,                   # ← 正文由智能体/技能生成 ✓（数据属性不产正文 ✓）
    "attributes": {"skos:definition": … , "verdicts": …},   # 只作属性面板 ✓
    "source": {"session_no": …, "locator": …},   # 严格溯源 ✓（缺则拒写 ✓）
})
```
**判定链（不需要 `bodhi:renderMode` ✓ —— 已确认多余 ✗）**：
```
report/composite 模式 = `page_type` 是 `bmm:Report` 的子类
  ⇒ ke_ontology.render_mode() 返回 "document" ✓（ke_ontology.py:433-436 ✓）
  ⇒ 数据属性**不渲正文** ✓，正文只来自 wiki_content ✓
其余类 ⇒ "entity" ✓，正文由数据属性渲染 ✓
```
> 2026-10-17 回滚说明：我曾往 TTL 加 `bodhi:renderMode "document"` ✗ —— **不需要** ✗，
> 因为「`Report` 子类 ⇒ document」这条判定 2026-10-15 就已经定稿并实现 ✓。

### 报告类的章节规范从哪来 ✓
`rdfs:comment` 里写"**章节规范** ①…②…③…" ✓（如 `bmm:TechnicalReviewReport` ✓）⇒
`report_compose` **必须先读该类的 comment** ✓ 再生成章节 ✓，不得自定章节 ✗。

### 收敛动作（待办）
1. `report_compose` 技能补三条硬约束 ✓：报告类由本体定 ✓ / 章节按 comment ✓ / 数据属性不产正文 ✓；
2. `ke_review.review_apply` 标 deprecated ✓（保留函数 ✓，但不再产 `bmm:Assessment` 报告 ✗）；
3. `server.save_knowledge(stage="report")` 的报告阶段改用 `bmm:TechnicalReviewReport` ✓；
4. 存量 4 份旧报告（`bmm:Assessment` ✗）标注 **legacy** ✓，不参与新口径统计 ✓；
5. 任务步骤 note 明确报告类型 ✓：`3) 生成报告（类型=<报告类>，章节=模板页「章节结构」）。技能：report_compose` ✓。

1. `systemCriticality` 的 TTL 注释补"**主系统定义、子模块继承；子模块可独立认定并覆盖**" ✓，
   然后 `upload_ttl`（顺序 bmm → bpmn → agent ✓）；
2. `rules_of_task` 在 MCP 描述里标 `[deprecated → rules_resolve]` ✓（行为保留 ✓）；
3. 上述 deprecated 技能经你确认后移入 `skills/_deprecated/` ✓（git 历史保留 ✓）。
