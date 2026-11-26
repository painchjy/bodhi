# Bodhi2 架构演示（8 页 · 原生可编辑 PPT）

> 一句话：**智能体 → 技能/工具 → 领域知识 → 本体模型**。  
> 交付：**纯 PPTX**，文字全部是可编辑文本，图形是独立形状（无整页图片）。  
> 范围：**生产链路**（不含测试知识库、`（测试）`智能体、已退役工具与已归档技能）。

## 文件

| 文件 | 用途 |
|---|---|
| `bodhi2-architecture.pptx` | **最新版**：8 页、16:9、原生形状 + 可编辑文字（已反向同步 v2 的文字/布局，并重做第 4 页；第 8 页已更新为 51 个工具） |
| `bodhi2-architecture-v3.pptx` | 备用版本（主文件被 WPS/PowerPoint 占用时生成） |
| `preview/slide-1.png` … `slide-8.png` | 快速预览图（Pillow 按同一版式绘制，便于不开 PowerPoint 时查看） |
| `assets/wing.png` / `assets/headband.png` | 第 2 页翅膀 / 紧箍咒图标（唯一的两张图片） |
| `make_icons.py` | 重新生成上述两个图标 |
| `build_deck.py` | 生成 PPTX + 预览图 |
| `slides.py` | 8 页内容与坐标（设计稿，1600×900 设计单位） |
| `deck_lib.py` | 形状/文字 DSL + PPTX/Pillow 渲染器 |
| `README.md` | 本文件 |

> `preview/*.png` 只是预览；**以 PPTX 为准**。PPTX 只有翅膀/紧箍咒两个图标是图片，其余为独立形状；所有文字都能在 PowerPoint / WPS 里直接改。

## 八页结构

1. **三类问题**：内容安全（成熟护栏）/ 知识规范性与完整性（结构成熟、语义缺口）/ 执行控制（快速演进、需运行时）
2. **本体库 MCP 工具包（Bodhi）**：左侧「本体库 MCP 工具包（Bodhi）核心补位」+ 翅膀图片；右侧外框「企业智能体」包住「领域智能体（业务流程建模 → 精准意图识别）」和「运行时控制（紧箍咒图片）」；左右两个大框上下对齐
3. **分层架构**：智能体（顶）→ 领域本体实例知识库 → 工具/技能 → 本体模型（底）；顶层为技术方案评审智能体 / 重点产品知识问答智能体 / 其他领域智能体；**虚拟列只在上两层**，仅含「企业知识建模智能体 + 企业知识管理领域」，不贯穿工具/本体层；本页不画闭环
4. **两个闭环**：
   - 上层 E2E 测试与定位：接受领域 BAD CASE，定位「领域知识 / 工具返回 / 技能 / 大模型推理」四类根因，并做同一 case 回归验证
   - 中间通道：**建模智能体**串接左右两个闭环；它读取右侧「企业知识管理领域」，优化左侧「领域知识」；**建模智能体自身不更新**
   - 领域知识闭环（全自动）：领域智能体 → BAD CASE → 建模智能体 → 更新领域知识 → 回归；领域智能体不变，只读知识并执行，是 harness 入口；只改领域知识
   - 能力建设闭环：E2E 根因 → 建模智能体 → **开发智能体** → 更新工具/技能/本体/企业知识管理领域 → 回归；智能体不变，开发智能体驱动能力变更；**企业知识管理领域在右侧闭环，由开发智能体更新**
5. **本体 × 领域知识**：TBox 只说类型，ABox 只说实例；四道校验闸门 + 四类知识库角色
6. **本体能解决哪些业务问题**：以「资金可管可控」为例：
   - 消歧：企业概念层给 3 个不同名称；下层 3 个领域各 3 个同名概念（同名 3×3）
   - 规范性约束：规则“每个领域必须提供管控对象”，跨境出海缺失 → 阻断
   - 隐性知识和完整性判定：核心目标缺量化（补资金归集率/审批时长）；跨域机制重叠（资金池/逐级审批）→ 标记并拆分
7. **智能体 × 技能**：企业知识管理建模 / 技术方案评审 / 知识运维；6 个现役技能；L1 7 任务链 + 评审 5 步链
8. **工具 × 执行**：51 个现役工具全部列出，按问题—解决方案分六组（执行边界 8 / 知识规范 7 / 知识完整 8 / 运行控制 8+4 / 跨库迁移 9 / 外部工具+自助取数 11）；运行控制新增 4 个**待建**工具（`agent_auth`、`tool_authorize`、`ifc_guard`、`budget_breaker`），用红色区分；外部工具+自助取数中外部连接 4 + 自助取数 7（`scenario_semantic_*`、`mapping_lookup`、`ontology_paths`、`fact_match`、`scenario_lookup`）；执行边界（领域建模）标为「核心」

> 页面 3/4/7 已按新口径对齐：**设计开发、domain_modeling、需求分析已废弃**，由「建模智能体 + 企业知识管理领域」承接。

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
改内容/版式：编辑 `slides.py`（每页一个函数 `slide1()`…`slide8()`），再运行上面的命令。  
颜色、字体、圆角等常量在 `deck_lib.py` 顶部；当前 `FONT_SCALE=1.2`（全局字号 +20%，适配 1600×900 设计稿）。

> 如果 `bodhi2-architecture.pptx` 正在 WPS / PowerPoint 里打开，生成器会**自动另存为 `bodhi2-architecture-v2/v3/v4.pptx`**（不会打断你正在看的文件）；关掉旧文件后把最新版重命名回主文件名即可。

## 数据口径

| 数字 | 来源 |
|---|---|
| 4 个本体模块 / 54 类 / 71 对象属性 / 55 数据属性 / 4 条限制 | `artifacts/manifest.json`、`artifacts/json_schema/index.json` |
| 6 个现役技能（PPT 所列） | 运行中 MCP `skills()` 实测，已剔除设计开发 / domain_modeling / 需求分析 |
| 43 个工具 | 运行中 MCP `tools/list` 实测（`http://127.0.0.1:8765/mcp`） |
| 4 个待建运行控制工具 | `agent_auth`、`tool_authorize`、`ifc_guard`、`budget_breaker`（预设计，未计入 43） |
| 4 活动 / 7 任务 | `docs/l1-knowledge-mgmt-process.md` |
| 规则/策略、工具写库、报告渲染 | `ontology/sources/bmm.ttl`、`ontology/sources/agent.ttl`、`docs/ontology/domain-logic-migration.md` |
| 资金可管可控案例 | 集团司库 / 跨境出海 / 供应链金融（用户提供业务口径） |

## 排除项

- 测试知识库与 `（测试）` 智能体
- 16 个退役 MCP 工具（`rules_of_task`、`graph_query`、`service_overview`、`link_candidates` 等）
- 已归档技能（`document_review`、`tech_review_extraction`、`structured_modeling`、`model_recommendation`）
- 旧异步抽取 `extract_and_save` / `extract_status` 与旧候选关联三段式
- 设计开发、`domain_modeling`、需求分析（已由建模智能体 + 企业知识管理领域替代）
