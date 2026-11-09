# Bodhi2 架构演示（6 页 · 原生可编辑 PPT）

> 一句话：**智能体 → 技能/工具 → 领域知识 → 本体模型**。  
> 交付：**纯 PPTX**，文字全部是可编辑文本，图形是独立形状（无整页图片）。  
> 范围：**生产链路**（不含测试知识库、`（测试）`智能体、已退役工具与已归档技能）。

## 文件

| 文件 | 用途 |
|---|---|
| `bodhi2-architecture.pptx` | **交付物**：6 页、16:9（13.333×7.5 in）、原生形状 + 可编辑文字 |
| `preview/slide-1.png` … `slide-6.png` | 快速预览图（Pillow 按同一版式绘制，便于不开 PowerPoint 时查看） |
| `build_deck.py` | 生成 PPTX + 预览图 |
| `slides.py` | 6 页内容与坐标（设计稿，1600×900 设计单位） |
| `deck_lib.py` | 形状/文字 DSL + PPTX/Pillow 渲染器 |
| `README.md` | 本文件 |

> `preview/*.png` 只是预览；**以 PPTX 为准**。PPTX 里没有一张整页图片，所有文字都能在 PowerPoint / WPS 里直接改。

## 六页结构

1. **四层架构**：智能体 → 技能（概率性）/ 工具（确定性）→ 领域知识（作为业务技能使用）→ 本体模型（知识的基础规范）
2. **四层如何协作**：三层智能体、6 技能 + 43 工具、3 个领域库（技术方案评审 / 重点产品智能问数 / 企业知识管理领域）、4 个本体模块 + 编译产物
3. **本体 × 领域知识**：TBox 只说类型，ABox 只说实例；四道校验闸门 + 四类知识库角色
4. **本体的作用**：
   - 消歧：同名「技术方案评审」横跨活动 / 流程 / 策略 / 模板 / 规则
   - 约束：产品、业务流程、活动、任务之间的交付/包含关系，不能越级
   - 判定：技术方案评审报告模板 vs 产品手册模板（章节 + 选用规则不同）
5. **智能体 × 技能**：企业知识管理建模 / 技术方案评审 / 知识运维；6 个现役技能；L1 7 任务链 + 评审 5 步链
6. **工具 × 执行**：43 个工具分六组（3 / 6 / 8 / 8 / 11 / 7）+ 执行系统 + 先预览后执行

> 页面 1/2/5 已按新口径对齐：**设计开发、domain_modeling、需求分析已废弃**，由「建模智能体 + 企业知识管理领域」承接。

## 重新生成

```powershell
# PPTX + 预览图
python docs/deck/build_deck.py

# 只生成 PPTX
python docs/deck/build_deck.py --no-preview

# 只生成预览图
python docs/deck/build_deck.py --preview
```

依赖：Python 3.10+、`python-pptx`、`Pillow`。  
改内容/版式：编辑 `slides.py`（每页一个函数 `slide1()`…`slide6()`），再运行上面的命令。  
颜色、字体、圆角等常量在 `deck_lib.py` 顶部。

## 数据口径

| 数字 | 来源 |
|---|---|
| 4 个本体模块 / 54 类 / 71 对象属性 / 55 数据属性 / 4 条限制 | `artifacts/manifest.json`、`artifacts/json_schema/index.json` |
| 6 个现役技能（PPT 所列） | 运行中 MCP `skills()` 实测，已剔除设计开发 / domain_modeling / 需求分析 |
| 43 个工具 | 运行中 MCP `tools/list` 实测（`http://127.0.0.1:8765/mcp`） |
| 4 活动 / 7 任务 | `docs/l1-knowledge-mgmt-process.md` |
| 规则/策略、工具写库、报告渲染 | `ontology/sources/bmm.ttl`、`ontology/sources/agent.ttl`、`docs/ontology/domain-logic-migration.md` |
| 同名「技术方案评审」跨类 | `wiki_pages` 生产库实测（Activity / BusinessProcess / BusinessPolicy / DocumentTemplate / Rule） |
| 产品 / 流程 / 活动 / 任务约束 | `bmm:deliversOffering`、`bpmn:processHasActivity`、`bpmn:activityContainsTask` |
| 评审模板 | 生产库 `schema.org/documenttemplate/技术方案评审报告模板` |

## 排除项

- 测试知识库与 `（测试）` 智能体
- 16 个退役 MCP 工具（`rules_of_task`、`graph_query`、`service_overview`、`link_candidates` 等）
- 已归档技能（`document_review`、`tech_review_extraction`、`structured_modeling`、`model_recommendation`）
- 旧异步抽取 `extract_and_save` / `extract_status` 与旧候选关联三段式
- 设计开发、`domain_modeling`、需求分析（已由建模智能体 + 企业知识管理领域替代）
