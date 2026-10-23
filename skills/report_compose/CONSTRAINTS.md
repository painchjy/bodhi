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
- [ ] 清单类小节是**表格** ✓（不是分段罗列 ✗）
- [ ] `verdicts` 等属性是"机算结论"✓，与正文结论**一致** ✓
- [ ] 回执带 `page_type` / `rule 数` / `hash` / 规则来源 `source` ✓

## 四、可复现（2026-10-17 用户口径 ✓）：格式只能来自本体，不许自定 ✗

| 格式要素 | **唯一来源** | 不许 ✗ |
|---|---|---|
| 章节（①-⑤） | 报告类的 `rdfs:comment`「章节规范」✓ | 自拟章节 ✗ |
| **清单表格的列与顺序** | 该清单对应 `bpmn:DataObject` 页的 **`skos:definition`** ✓（列名/顺序逐字照抄） | 自己加/删列 ✗ |
| 每行的**可信度** | `ke_score.score_rule(facts, checks)` → `score` + `level` ✓ | 拍脑袋写"高/中" ✗ |
| **无法判定的说明** | 表后补充块 ✓（① 原因 ② 影响的问题 ③ 需要反馈或核实的内容）；**仅当存在无法判定时输出** ✓ | 塞进表格列 ✗ |
| 判定枚举 | 符合 / 不符合 / 不适用 / **无法判定** ✓ | 自造状态 ✗ |
| 目录可见 | 写库后 `ke_pages.sync_folders(kb)` ✓（否则索引能查、目录看不到 ✗） | 只写页不重建目录 ✗ |

**复现校验（跑完必看 ✓）**：给定同一文档 + 同一 spec ⇒ 章节数、④ 的列名与顺序、`verdicts`/`credibility`
分布应**逐字一致** ✓；变的只应是事实值与结论 ✓。

## 五、④「文档位置」列口径（2026-10-17 ✓）

- **唯一来源** = 该清单对应 `bpmn:DataObject` 页的 `skos:definition` ✓（本技能不另立规则 ✗）；
- 列值必须写**章节标题**（+ 可选二级限定）✓，例 `2.3 <小节名> · <对象>`；
- **禁止** `chunk#N` 这类内部锚点 ✗（锚点放 `page_metadata` / 证据链 ✓）；
- 章节标题用**源文档原样文字** ✓（不改写 ✗）；无章节时写 `全文` ✓。
