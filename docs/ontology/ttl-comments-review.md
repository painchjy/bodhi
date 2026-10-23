# TTL 注释体检（待人工修正）

> 生成 2026-10-23。**只列清单，未改任何 TTL**。
> 对象：手工维护文件（现已迁到 `docs/ontology/*完整版.ttl`）。
> ⚠️ `ontology/sources/*.ttl` 是**程序产物**（上传生成），不要手改；要改就先改这里的 `*完整版.ttl`，再走上传。

## 0. 结论速览

| # | 文件:行 | 问题性质 | 建议 |
|---|---|---|---|
| A1 | `BPMN完整版.ttl:14` | **事实错误**：说"故本模块 imports bmm"，但文件里 `owl:imports` 为空 | 删掉"故本模块 **imports bmm**"半句 |
| A2 | `BPMN完整版.ttl:16` | **事实错误**：说"`agent.ttl` imports bpmn"，实际 agent 只 `owl:imports bmm` | 整行删除 |
| A3 | `BPMN完整版.ttl:34/141/163-164/173` | 引用 `taskExecutesRule` | 该属性**仍声明**（L81），按你口径**先保留**；注释可不动，或统一标注"（工具路径已下架，属性保留）" |
| B1 | `BMM完整版.ttl:650-655` | **旧口径**：说"声明 `wikiContent` 的类 → document（render_mode 看 domains）" | 现行判据是 **`bmm:Report` 子类 → document**（2026-10-15）；应改写 |
| B2 | `BMM完整版.ttl:682-683` | 同上（`:Report` 注释） | 同上 |
| C1 | `智能体开发完整版.ttl:351-363` | **整块已废**：讲"把 domain 追加到 `bmm:wikiContent`" | L364 自己写着"2026-10-11 改为继承，不再追加 domain" ⇒ 删 L351-363，留一行说明 |

## 1. 逐条原文与证据

### A1 / A2 — `BPMN完整版.ttl`（第 9–18 行块）

原文：
```turtle
# **任务 → 业务规则**（`taskExecutesRule` → bmm:BusinessRule，故本模块 **imports bmm**）、
# `agent.ttl` **imports bpmn**：**智能体协助活动**（`agent:agentAssistsActivity`）。
```
证据：
```text
grep -n 'owl:imports' ontology/sources/bpmn.ttl   → （空）
grep -n 'owl:imports' ontology/sources/agent.ttl  → 12:    owl:imports <http://example.org/bmm> ;
```
⇒ bpmn **没有** imports；agent **只** imports bmm。两处注释都与实现不符。

### A3 — `taskExecutesRule`

证据：`bpmn.ttl:81` 仍有 `:taskExecutesRule rdf:type owl:ObjectProperty`；
2026-10-18 下架的是**工具侧路径**（`rules_of_task` / `spec="task:"`），不是属性本身。
⇒ 按你口径**先保留**；若要统一，可在注释后加"（工具路径已下架，属性保留）"。

### B1 / B2 — `BMM完整版.ttl` 渲染模式口径

原文（L650–655）：
```turtle
# **知识渲染模式由本体决定，且只有两种**（用户口径）：
#   声明了 `wikiContent` 的类 → `document`（半结构化：调用方给整篇正文）；
#   其余类 → `entity`（结构化知识单元：正文由数据属性渲染）。
# 所以本属性**必须带 `rdfs:domain`**（不能是 owl:Thing）：不写 domain 就成了全局属性，
# 会让**所有类**都变 document（ke_ontology.render_mode 只看 domains 显式命中来规避）。
```
原文（L682–683）：
```turtle
# **不参与机器判定**（机器判定只读 entity 资产）。谁继承它，谁就按 `document` 渲染
# （`ke_ontology.render_mode` 的判据：类或祖先声明了带 domain 的 `wikiContent`）。
```
证据：`ke_ontology.py:433-437`
```python
# 2026-10-15 用户口径：**报告判定 = 是否是 `bmm:Report` 的子类**（不再看 wikiContent 的 domain）
if key == "bmm:Report" or "bmm:Report" in chain:
    return "document"
```
⇒ 注释里的判据已过时。另外 **W4a 起正文主键是图属性 `articleBody`**（PG `content` 是渲染缓存），
`wikiContent` 现在只是本体里的属性声明 + 兼容别名。

### C1 — `智能体开发完整版.ttl`（L351–364）

原文（节选）：
```turtle
### 渲染模式：本模块的文档类「追加」到 bmm:wikiContent 的 domain（2026-10-09）
# 为什么不是在本模块另声明一个 `agent:wikiContent`：编译器**禁止跨模块同名属性**
# 为什么这样写能生效：… 所以在 agent 侧"追加 domain"即可，**bmm 模块不用动**。
@prefix bmm: <http://example.org/bmm#> .
# 2026-10-11：报告类改为**继承** `bmm:Report`（见上方 4 个类的 subClassOf），不再逐个追加 domain。
```
⇒ L351–363 描述的做法已被 L364 自身推翻（改为继承），整块可删。

## 2. 建议一并确认的"历史沿革类"注释（可选保留）

| 位置 | 内容 | 说明 |
|---|---|---|
| `BPMN完整版.ttl:130` | `activityContainsTask 活动包含任务（原来缺）` | "（原来缺）"是历史信息，可删 |
| `BMM完整版.ttl:17` | `编译器读入后随本体目录下发，不再写死在 config.py` | 历史说明，可保留 |
| `智能体开发完整版.ttl:91` | `2026-10-04 用户口径（取消原来的「知识库角色」词表）` | 解释性，建议保留 |
| `智能体开发完整版.ttl:364` | `2026-10-11：报告类改为继承 bmm:Report` | 保留（正好替换 C1 的旧块） |

## 3. 修正后的落盘流程（提醒）

1. 只改 `docs/ontology/*完整版.ttl`；
2. 走**前端上传**（或 `POST /bodhi/ontology/upload`）→ 程序把它写到 `ontology/sources/<模块>.ttl` 并编译；
3. 回归：`BODHI_TEST_ONTOLOGY_UPLOAD=1 python3 -m pytest tests/test_ontology_upload.py`（会真重编）。
