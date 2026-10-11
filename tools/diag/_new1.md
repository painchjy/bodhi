## 1 本体调整：报告类型体系（**只改 TTL，不改代码；且不新增报告类型**）

### 1.1 现状（2026-10-11 **live Neo4j 实测**）

| 事实 | 证据 |
|---|---|
| `render_mode` 的判定**就是**"类或祖先声明了带 `rdfs:domain` 的 `wikiContent` → `document`，否则 `entity`" | `tools/ke-core/ke_ontology.py:302 render_mode()`（2026-10-09 口径，注释写明"不引入任何新注解"） |
| **现有文档类（报告类）恰好 5 个**：`bmm:Assessment`、`agent:Advice`、`agent:DesignSpec`、`agent:Evaluation`、`agent:InstallGuide` | live 实测：`bmm:wikiContent domains = ['agent:InstallGuide','agent:DesignSpec','agent:Advice','agent:Evaluation','bmm:Assessment']`，且这 5 个 `render_mode=document`、其余（`ToolContract/Agent/Skill/BusinessProcess`）`=entity` |
| 这 5 个是怎么来的：`bmm.ttl` 里 `wikiContent` 的 domain 写了 `Assessment`；`agent.ttl` 用**"domain 追加"**（不许跨模块同名属性）把 `Advice/DesignSpec/Evaluation/InstallGuide` 加进来 | `ontology/sources/bmm.ttl:656-658`、`ontology/sources/agent.ttl:94-99` |
| 报告分支 `save_knowledge(stage="report")` 对**非本体类型**（如 WeKnora 内建 `summary`）会**改写为 `bmm:Assessment`** | `tools/ontology-mcp/server.py:2073-2092`（回执 `page_type_adjusted`） |
| ⚠️ `render_mode` 的 domain 判定**只读 Neo4j 投影**：`data_properties()` 明确"JSON 产物只有计数、没有名单 → 只能空表兜底" | `ke_ontology.py:622-654`（`source="unavailable"` 时 `properties={}`） |
| BMM 现有 **29 类** / agent **12 类** | 探查（`tools/diag/_ttl.log`） |

> 结论：**你要的效果不需要改代码**；需要改的只有 **TTL**（把 `wiki_content` 从"逐个追加 domain"改成"挂在报告基类上"）。
> 另外**上线必须重投影**（见 §1.6 末），这是本轮实测新发现。

### 1.2 目标形态：**不新增报告类型，只加 1 个基类**

| 类型 | 归属 | 变化 | 说明 |
|---|---|---|---|
| **`bmm:Report`（报告·基类）** | bmm | **新增（唯一新增）** | `bmm:wikiContent` 的 domain 由 `Assessment` **改指它**；阶段报告统一用它 |
| `agent:Advice` 改进建议 / `agent:DesignSpec` 设计单 / `agent:Evaluation` 评测 / `agent:InstallGuide` 安装指引 | agent | **改继承**（`rdfs:subClassOf bmm:Report`），删掉那 4 行 "domain 追加" | 4 个既有报告类，**类型名不变** → **现有技能零改动** |
| `bmm:Assessment` **评估/判断** | bmm | **摘掉** `wiki_content`（不再进 domain） | **纯 entity**：一条 assessment = **一个明确结论的判断**（可判定/可聚合/可比较），不是综合报告 |
| 阶段报告（A 立项 / B 对齐 / C 核对 / D 判断 / E 结项） | — | **不新增类型** | 统一用 `bmm:Report`，段的性质写**标题 + 元数据**（例：标题"立项范围报告"、`## 人员反馈` 小节） |

> **为什么这样最省**：报告类只多 1 个（基类），4 个既有报告类只是"换个继承方式"（名字/技能/工具全不动），
> `Assessment` 摘掉一个属性就完成"报告与判断分家"。**没有一处需要改技能或工具代码。**

### 1.3 D2 结论：继承法**可用**（实测），兜底方案**保留**

| | 主方案（你提的，**已验通**） | 兜底方案（现状模式，零风险） |
|---|---|---|
| 做法 | `bmm:Report` 带 `wiki_content`；报告类 `rdfs:subClassOf bmm:Report` | 不建基类，把报告类**逐个追加**进 `bmm:wikiContent` 的 domain |
| 实测 | 编译器 **接受跨模块 `subClassOf`**：产物里 `parents(agent:Advice)=['bmm:Report']`；复算 4 类 `document`、`Assessment` `entity`（§1.6） | 生产上**正在用**（5 个 domain） |
| 取舍 | ✅ 采用（一处声明、以后新增报告类不再漏） | 保留为**兼容兜底**：若某次编译器/重投影出问题，可临时改回 domain 追加 |

### 1.4 上线动作（**五步，第 3 步最容易被漏**）

```
① 改 TTL：ontology/sources/bmm.ttl 加 :Report + wikiContent domain 改指 :Report（+ 摘掉 Assessment）
            ontology/sources/agent.ttl 把 4 行 domain 追加换成 4 行 rdfs:subClassOf bmm:Report
② 编译：python tools/ontology-compiler/compile.py validate && ... compile
③ 【必做】重新投影到 Neo4j（否则 render_mode 不生效 —— 实测结论）
         ：deploy/weknora-fork/refresh_ontology_kb.sh 或 /bodhi/ontology/load
④ 抽查：对 5 个类型问 render_mode（工具 `ontology_types` / 直接跑 ke_ontology.render_mode）
         → 期望：DesignSpec/InstallGuide/Evaluation/Advice/Report = document；Assessment/ToolContract = entity
⑤ 巡检：audit_scan 过（无 B1 新告警）；本体库页按新类重生成
```

### 1.5 迁移影响

| 影响 | 动作 |
|---|---|
| `bmm:Assessment` 由 document 变 entity | 存量 assessment 页**已写正文不回滚**（历史页保持原样）；**新写**走 entity 渲染（正文由定义/属性/关系生成）—— 与你的口径一致 |
| 4 个既有报告类只改继承 | 页面 `page_type` 不变（`agent:DesignSpec` 等），**无需 retag** |
| 若历史上有 `page_type=summary` 的报告页 | 用两段式 `retag_preview → retag_apply` 迁到 `bmm:Report`（或按内容选既有报告类） |
| 新增 `bmm:Report` | 本体库重投影后多一页类页；`bodhi:shortName`/label/color 按既有约定 |

### 1.6 实测记录（D2 验证，2026-10-11）

**环境**：沙箱 = `ontology/sources` 复制到仓库内临时目录 → 按 §1.2 改 TTL → 用**真编译器** `compile_all(out_dir=沙箱)`；
复算用**编译产物的 `parents`**（已跨模块解析）+ **TTL 里 `wikiContent` 的 domain**；另跑一次**生产 Neo4j 只读**对照。
脚本：`tools/diag/_exp_inherit.py`（日志 `tools/diag/_exp_inherit.log`、`tools/diag/_live.log`）。

| 检查点 | 基线（现状） | **改动后（继承）** |
|---|---|---|
| 编译器接受？ | ✅ `ok=True`，产物已写 | ✅ **`ok=True`**，产物已写 —— **跨模块 `subClassOf` 不被拒** |
| `wikiContent.domains` | `['agent:Advice','agent:DesignSpec','agent:Evaluation','agent:InstallGuide','bmm:Assessment']` | **`['bmm:Report']`**（一处声明） |
| `parents(agent:Advice)` | `[]` | **`['bmm:Report']`**（继承进产物 ✓） |
| `agent:DesignSpec / Evaluation / InstallGuide` | document（靠 domain 追加） | **document（靠继承）** ✓ |
| `bmm:Assessment` | document | **entity** ✓（按你的口径分家） |
| `bmm:Report` | （不存在） | **document** ✓ |
| `agent:ToolContract` / `Agent` / `Skill` / `bmm:BusinessProcess` | entity | **entity**（不受影响）✓ |
| 生产 Neo4j（live） | 5 个类 = document（与基线**逐条一致** → 复算忠实） | （重投影后再抽查，见 §1.4-④） |
| ⚠️ 运行时取数 | `data_properties source = neo4j`；沙箱禁用 Neo4j 时 `render_mode` 全回 `entity` | **证明**：domain 判定只认 Neo4j 投影 → **必须重投影** |
