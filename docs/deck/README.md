# Bodhi2 架构演示（8 页 · 原生可编辑 PPT）

> 一句话：**智能体 → 技能/工具 → 领域知识 → 本体模型**。  
> 交付：**纯 PPTX**，文字全部是可编辑文本，图形是独立形状（无整页图片）。  
> 范围：**生产链路**（不含测试知识库、`（测试）`智能体、已退役工具与已归档技能）。

## 文件

| 文件 | 用途 |
|---|---|
| `bodhi2-architecture-v4.pptx` | **最新版**：8 页、16:9、原生形状 + 可编辑文字（主文件/v2/v3 被 WPS 占用时自动另存） |
| `bodhi2-architecture.pptx` | 主文件名（未被占用时生成器直接写这里） |
| `preview/slide-1.png` … `slide-8.png` | 快速预览图（Pillow 按同一版式绘制，便于不开 PowerPoint 时查看） |
| `build_deck.py` | 生成 PPTX + 预览图 |
| `slides.py` | 8 页内容与坐标（设计稿，1600×900 设计单位） |
| `deck_lib.py` | 形状/文字 DSL + PPTX/Pillow 渲染器 |
| `README.md` | 本文件 |

> `preview/*.png` 只是预览；**以 PPTX 为准**。PPTX 里没有一张整页图片，所有文字都能在 PowerPoint / WPS 里直接改。

## 八页结构

1. **三类问题**：内容安全（成熟护栏）/ 知识规范性与完整性（结构成熟、语义缺口）/ 执行控制（快速演进、需运行时）
2. **Bodhi 的补位与缺口**：结构完整性用本体 + SHACL；语义完整性用业务规则 + 建模智能体；执行边界用领域智能体；还缺智能体角色认证、任务级权限、运行时网关、IFC、隔离、预算熔断与审计
3. **四层架构**：智能体 → 技能（概率性）/ 工具（确定性）→ 领域知识（作为业务技能使用）→ 本体模型（知识的基础规范）；右侧用「核心连接」标出 **企业知识管理领域 + 建模智能体** 是连接本体与领域库的核心
4. **四层如何协作**：三层智能体、6 技能 + 43 工具、3 个领域库（技术方案评审 / 重点产品智能问数 / 企业知识管理领域）、4 个本体模块 + 编译产物；同样用右侧「核心连接」强调建模智能体与企业知识管理领域
5. **本体 × 领域知识**：TBox 只说类型，ABox 只说实例；四道校验闸门 + 四类知识库角色
6. **本体解决三种歧义**：用「资金可管可控」在集团司库 / 跨境出海 / 供应链金融的差异，说明消歧、约束、判定：
   - 消歧：企业概念层给 3 个不同名称；下层 3 个领域各 3 个同名概念（同名 3×3）
   - 约束：驱动来源 → 约束机制 → 管控对象，三领域各一条有向路线；不能跨域套用
   - 判定：同一句“资金可管可控” → 判定领域 → 司库/跨境/供应链三套判定路线
7. **智能体 × 技能**：企业知识管理建模 / 技术方案评审 / 知识运维；6 个现役技能；L1 7 任务链 + 评审 5 步链
8. **工具 × 执行**：43 个现役工具全部列出，按问题—解决方案分六组（执行边界 8 / 知识规范 6 / 知识完整 8 / 运行控制 8+4 / 跨库迁移 9 / 外部工具连接 4）；运行控制新增 4 个**待建**工具（`agent_auth`、`tool_authorize`、`ifc_guard`、`budget_breaker`），用红色区分；执行边界（领域建模）标为「核心」

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
颜色、字体、圆角等常量在 `deck_lib.py` 顶部。

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
