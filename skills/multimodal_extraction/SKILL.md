---
id: multimodal_extraction
name: 多模态识别（文档内嵌图 → 结构/语义 + 证据 + 置信度）
description: 把文档内嵌的图（部署架构图、拓扑图、表格截图等）识别成结构化知识——节点/关系/结论/待澄清/知识缺口，每条必带 bbox 或 evidence 与置信度；低置信进 ambiguity/todo，缺知识显式报 knowledgeGaps，落库前过门禁。
when: 用户（或评审流程）需要从**文档里的图**取数/判读（如"部署架构图判最低部署与灾备要求""拓扑里的系统边界"），且文字提取不到。
models: [bmm, ea]
default_model: bmm
stages: [pick, extract, gate, land]
tools: [doc_outline, image_extract, wiki_search, wiki_read_page, save_knowledge, ontology_types]
version: 0.1.0
---

# 多模态识别（技能）

> 设计依据：`docs/cases/技术方案评审/image-extraction-experiment.md`（D4 实验）——**§5 提取契约 v0** 与 **门禁 1–4** 就是本技能的输入输出约定。
> 视觉通道（2026-10-08 用户口径）：**复用会话同一个多模态模型**（不另配 .env、不启用 WeKnora `vlm_config`）；素材走 **WeKnora 文档内嵌图**。即：**`image_extract` 负责取图并交给会话模型看，识别由你（会话模型）按本技能契约完成**。

## 0. 边界

- 本技能**只读文档** → 产物是"图上的知识"（节点/关系/结论/证据/置信度/待澄清），**不臆造图上看不见的事实**；
- 识别**结论**（如"同城+异地部署""跨云双活"）必须能回到**节点/标签/图例**（`derivedFrom`）；
- 图上看不见、需要外部知识才能判的（如"云名→城市"）→ 允许**名称推测**并**打置信度**，同时**显式报 `knowledgeGaps`**（不要悄悄脑补）。

## 1. 流程（四步）

### 第 1 步：定位图（只读）
`doc_outline(kb_id, knowledge_id, …)` 的骨架里带**图/表位置**；挑出要识别的图（记 `figure_no` / 页 / 尺寸）。文字章节能替代的优先文字，**只在文字不足以判定时才走图**。

### 第 2 步：取图（`image_extract`）
`image_extract(kb_id=…, knowledge_id=…, figure_no=…)` → 返回**该内嵌图**（交给会话模型查看）。可加 `question` 收窄（如"这张图的部署方式与灾备方式是什么？"）。

### 第 3 步：识别并**过门禁**（本技能核心）
按 §2 契约产出 JSON，**逐条门禁自检**（见 §3），不合规的条目**剔除或降级**，不要带病输出。

### 第 4 步：落库（`save_knowledge`）
- L1 实例（云/AZ/机房/单元/分片/组件/路径）→ **领域模型库**（按本体类；先 `ontology_types` 现查）；
- L3 指针（原图 + `bbox`）→ 证据字段（`source_text`/`source_locator` 里带 `figure_no` + 坐标）；
- 结论（annotation）→ 作为**规则判定的输入**（不直接当结论页结论）。
- **务必带 `session_no`**（契约 §5 会话溯源）。

## 2. 提取契约 v0（= 实验报告 §5，逐字对齐）

```json
{
  "source": {"doc_id": "…", "file": "…", "figure_no": 1, "page": 12, "size": [1712, 1347]},
  "nodes": [{"id": "cloud.pujiang", "type": "Cloud", "label": "浦江云",
             "bbox": [0.28, 0.20, 0.47, 0.95], "confidence": 0.98}],
  "edges": [{"from": "cloud.pujiang.oss", "to": "cloud.zhangjiang.oss", "type": "sync",
             "evidence": "虚线+『同步』", "bbox": [0.42, 0.72, 0.72, 0.78], "confidence": 0.6}],
  "annotations": [{"key": "dr.pattern.homocity", "value": "跨云双活",
                   "derivedFrom": ["cloud.pujiang", "cloud.zhangjiang"], "confidence": 0.85}],
  "ambiguity": [{"topic": "shard.80-99",
                 "candidates": [{"value": "内蒙主机房", "confidence": 0.5},
                                {"value": "贵州同步副本", "confidence": 0.5}],
                 "needHuman": true, "reason": "同区间双现，图上未写主备"}],
  "knowledgeGaps": [{"need": "云名→城市", "missing": ["浦江云", "张江云"],
                     "impact": "无法判定『同城』", "confidenceWithout": 0.0}],
  "todo": [{"action": "确认跨云同步方向", "assignee": "human", "blocks": ["dr.pattern.homocity"]}]
}
```

## 3. 门禁（硬规则，落库前逐条自检）

1. 每个 `node/edge/annotation` **必须有 `bbox` 或 `evidence`**，否则**不落库**（可复核性硬门禁）；
2. `confidence < 0.8` **必须**进 `ambiguity[]`/`todo[]`，且**不允许**把不确定项写成结论；
3. 结论类 `annotation` 必须写 `derivedFrom`（可追溯到节点或知识项）；
4. **缺知识**显式输出 `knowledgeGaps`（由上层补知识清单），**不让模型脑补**。

## 4. 纪律

- **宁缺勿滥**：判不出就 `unknown` + 挂 `todo`，**不硬判**（对应需求 block 92「知识不足 ⇒ unknown」的语义）；
- **证据可回溯**：任何落库条目都要能回到"原图哪一块"（`bbox`）或"图上哪句话"（`evidence`）；
- **不把图上读不到的当事实**：跨云同步方向/主备归属这类图上没写的 → 一律进 `todo`；
- **结论只在有 `derivedFrom` 时才写**；低置信默认降级为 `ambiguity`；
- 挡板（`_mock`）数据必须标「模拟数据」；本技能落地后**不再需要图像识别挡板**。
