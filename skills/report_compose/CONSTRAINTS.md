# 报告（document 模式）的统一约束 —— 详见 skills/_deprecated.md §四

## 三条硬约束（工具层只保证规范 ✓，不猜场景 ✗）

1. **报告类型由「用户 / 智能体」按任务步骤确定** ✓
   - 必须是 `bmm:Report` 的**子类**（`bmm:TechnicalReviewReport` / `bmm:RequirementAnalysisReport` /
     `agent:Advice` / `agent:DesignSpec` / `agent:Evaluation` / `agent:InstallGuide` … ✓）
   - **不限定**只能写 `TechnicalReviewReport` ✓ —— 本技能不决定类型 ✗，只校验"是不是 Report 子类" ✓
2. **章节按该类的 `rdfs:comment` 生成** ✓
   - 先从本体读该类的 comment（里面有"**章节规范** ①…②…③…"✓）→ 按它组织章节 ✓
   - 类没有写章节规范 ⇒ 按该类的语义**自行最小化组织**，并在回执里标注"类未声明章节规范" ✓
3. **正文（`wiki_content`）由本技能生成** ✓；**数据属性不产正文** ✓
   - 数据属性只作属性面板（`skos:definition` / `verdicts` / `bmm:ruleSet` … ✓）
   - 正文不重复属性值 ✓（与 entity 类相反 ✓）

## 写入口（唯一）
```python
ke_pages.write_knowledge(kb_id, {
    "title": …, "page_type": <Report 子类>, "wiki_content": <按章节的正文>,
    "attributes": {…}, "source": {"session_no": …/ "doc_refs": …},
})
```
- `mode` **不用传** ✓（`ke_ontology.render_mode(page_type)` 自动判 `document` ✓）
- 缺溯源 ⇒ `strict_source` 拒写 ✓
- `upsert_page` 也可用，但会校验"document 类必须有正文" ✓（同一护栏 ✓）

## 反假断言（自检）
- [ ] `page_type` 是 `bmm:Report` 子类 ✓
- [ ] 章节与该类 comment 的章节规范**一一对应** ✓（顺序一致 ✓，不多不少 ✓）
- [ ] ④ 问题清单是**表格** ✓（不是分段罗列 ✗）
- [ ] `verdicts` 等属性是"机算结论"✓，与正文结论**一致** ✓
- [ ] 回执带 `page_type` / `rule 数` / `hash` / 规则来源 `source` ✓
