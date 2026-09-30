# 智能体辅助设计流水线（需求 → 概要设计 → 服务详设 → FD 交叉验证）

> 2026-09-20 起。本文是**流程规格**：最少步骤、预期中间产物、每步的产物形态与校验点、测试流程。
> 验证案例（需求与设计文档分离）：
> - `docs/samples/mb-手机银行开户签约需求.md`（个人客户 注册/实名/签约）
> - `docs/samples/sp-个体工商户经营圈签约需求.md`（**增量需求**：个体工商户经营圈签约）
> - 背景 / 用途 / 验证过程：`docs/cases/sp-个体工商户经营圈签约-验证说明.md`
> 现状：**确定性内核已实现并沙箱跑通**（`tools/ke-core/ke_design.py`），LLM 侧的智能体提示词/MCP 暴露是下一步。
>
> **2026-09-22 变更（重要）**：整篇异步抽取工具（`extract_and_save` / `extract_status`）已从 MCP 服务**移除**
> （不是「兼容保留」；原文留痕 `tools/ontology-mcp/archive/async_extract_retired_2026-09-22.py.txt`），
> 状态查询工具改名为 **`job_status`**（只服务 `service_overview` 异步刷新）。
> 下文历史表格/章节里出现的 `extract_and_save` 一律按「**分批建模**」理解：
> `doc_outline` 取本批上下文 → 智能体比对 → `save_knowledge(stage="graph", session={...})` → `extract_state`。
> 服务端**不再调 LLM**（因此不再需要 `openai` 依赖），范围收窄（旧 `scope` 参数）改由技能里用
> `ontology_types(model, focus=…)` 选类后分批实现。

## 0. 设计口径（四条硬规则）

1. **需求与设计分离**：需求文档只写业务（不写实体属性表、不写本体术语）；设计产物一律落在 wiki 页里。
2. **只到实体粒度做概要设计**：概要设计只拆「任务 / 步骤 + 关联实体」，**不到属性粒度**；属性级只在服务详设里出现。
3. **一切可溯源**：需求文档 → 需求 wiki 页（片段级溯源 `chunk_refs`）→ 概要设计页 → 服务详设页（**页面级溯源**
   `page_metadata.design.derived_from` + 正文 `## 溯源`）。溯源目标不存在则写页失败（不允许指向空气）。
4. **FD 独立识别、事后交叉验证**：函数依赖**只从需求侧识别**（不参考详设），再与各服务详设做交叉验证，
   判定「属性归属/双维护/幂等可证明性」。判定必须由**确定性代码**完成（`docs/bodhi-reasoning.md` 口径）。

## 1. 最少步骤（7 步）与每步产物

| # | 步骤 | 执行者 | 输入 | 产物（落库形态） | 校验/门禁 |
|---|---|---|---|---|---|
| 1 | **需求入库**：上传需求 md 到知识库 | 人 / 运维 | `docs/samples/mb-…需求.md` | 文档 + 切片（溯源锚点） | 文档可读、切片非空 |
| 2 | **需求解析**：整篇抽取到 wiki（模型 `ea`） | MCP `extract_and_save` | 步骤 1 的 `knowledge_id` | 需求 wiki 页：任务/步骤/实体/角色（`ea:Task` / `ea:Step` / `ea:BusinessEntity`…），每页带 `chunk_refs` | 页数与文档内容量匹配；`source_refs` 非空 |
| 3 | **概要设计** | 设计智能体 | 需求 wiki 页（`wiki_search` / `wiki_read_page`） | 概要设计页（`design/overview/*`）：任务/步骤清单 + **关联实体**（不到属性）；溯源=需求页 slug | 每页 `derived_from` 非空；不出现属性名（人审） |
| 4 | **服务分配** | 设计智能体 | 概要设计页 | 每个系统/IT 服务一页：负责的任务/步骤 + 操作实体；溯源=概要设计页 | 每个步骤恰好落到一个主责服务；无孤儿步骤 |
| 5 | **服务详细设计**（逐服务） | 详设智能体 | 该服务的溯源信息（需求页 + 概要设计页） | 服务详设页（`design/service/*`，`page_type=easvc:ServiceContract`）：`## 服务契约`（操作实体/幂等键/只读）+ `## 属性操作`（R/W/V）+ **`## 维护责任`（属性 → 自维护 / 引用外部 / 只读缓存）** | 每个属性都要有维护方式；跨服务引用要写权威服务名 |
| 6 | **FD 独立识别**（只读需求，不看详设） | MCP `extract_and_save`（模型 `ea-service`） | 步骤 1 的文档 | 函数依赖页（`easvc:FunctionalDependency`，关系行 `hasDeterminant` / `hasDependent`）+ 业务属性页（`easvc:BusinessAttribute`） | 每条 FD 至少 1 个决定方 + 1 个被决定方 |
| 7 | **FD × 详设交叉验证** | 确定性代码 `ke_design.check_couplings` | 步骤 5 的详设页 + 步骤 6 的 FD 页 | findings（耦合审查报告，可落 `design/report/*` 页） | `findings=0` 才算设计收敛 |

> 步骤 2 与 6 是**同一份文档、两个模型、两次独立抽取** —— 这就是「函数依赖独立从需求识别」的落地方式，
> 也是"概要设计不掺 FD、FD 不掺详设"这一独立性的保证。

## 2. 中间产物清单

| 产物 | 存放 | 关键字段 | 被谁消费 |
|---|---|---|---|
| 需求文档 + 切片 | KB `knowledge` / `chunks` | 文档 id、切片序号 | 步骤 2、6 的抽取；溯源兜底 |
| 需求 wiki 页 | `wiki_pages`（`page_type` = 本体类） | `chunk_refs` / `source_refs`（片段级溯源） | 概要设计智能体、人审 |
| 概要设计页 | `design/overview/<hash>` | `page_metadata.design.derived_from`（需求页 slug） | 服务分配、详设智能体 |
| 服务详设页 | `design/service/<hash>`，`page_type=easvc:ServiceContract` | `## 服务契约` / `## 属性操作` / `## 维护责任` / `## 溯源` | 交叉验证（步骤 7）、图谱 |
| FD 页 | `design/fd/<hash>`（或抽取产物），`page_type=easvc:FunctionalDependency` | `## 本体关系` 里的 `hasDeterminant` / `hasDependent` | 交叉验证、图谱 |
| 耦合审查报告 | `design/report/<hash>` | findings（rule/severity/subject/evidence/suggestion） | 设计返工、人工抽检 |
| 版本快照 | `wiki_page_revisions` | 每次写页前快照，`version+1` | 可回退、可审计 |

## 3. 页式约定（解析靠这些小节，不能改名）

```markdown
## 服务契约
- 操作实体：客户（`ea:BusinessEntity`）        ← 多个实体 = 服务边界过宽（F6）
- 幂等键：客户.证件种类 + 客户.证件号码   ← 必须取自某条 FD 的决定属性（F4）
- 只读：否

## 属性操作
| 属性 | 操作 | 说明 |            ← 操作：R 读 / W 写 / V 校验 / RW

## 维护责任
| 属性 | 维护方式 | 依据 |          ← 维护方式：自维护 / 引用外部 / 只读缓存

## 溯源
- 来源：[需求 R1…](wiki:design/requirement/<hash>)

## 本体关系
- 实现步骤（`easvc:contractRealizesStep`）→ [[design/overview/<hash>|概要设计…]]
```


## 4. 确定性内核：`tools/ke-core/ke_design.py`

| 能力 | 函数 / CLI | 说明 |
|---|---|---|
| 写设计页（+页面级溯源） | `write_page(kb_id, kind, title, content=…, page_type=…, derived_from=[…], relations=[…])` / `write` | 确定性 slug（`uuid5`）、版本快照、重建 `in_links`、`## 溯源` 自动落、溯源目标必须存在 |
| 写本体关系 | `apply_relation(...)` | 优先 `ke_pages.add_relation`（严格校验 domain/range）；**本体投影缺该模块属性节点时**按 `target_closure` 校验后直接落行 |
| 读 FD | `load_fds(kb_id)` / `fds` | 从 FD 页的 `## 本体关系` 解析决定 / 被决定属性 |
| 读详设 | `load_services(kb_id)` / `services` | 解析 `## 服务契约` / `## 属性操作` / `## 维护责任` / `derived_from` |
| 交叉验证 | `check_couplings(kb_id)` / `check` | 规则 `F1`–`F6`，只读，输出 findings |

**规则表（`check` 的判定口径）**

| 规则 | 判定 | 级别 | 依据 |
|---|---|---|---|
| `F1` | 同一属性被 ≥2 个服务**自维护** | 决定属性 → critical；其他 → high | 属性必须有唯一权威维护者 |
| `F2` | 某 FD 的决定属性**没有任何服务**自维护 | medium | 归属未定，没人负责 |
| `F3` | 服务的自维护属性是某 FD 的**被决定方**，而该 FD 的决定属性由**别的**服务维护 | high | 副本会被写脏（姓名 / 手机号码的典型场景） |
| `F4` | 契约声明的幂等键属性不在任何 FD 的决定集合里 | high | 对应 `S4`（幂等可证明性） |
| `F5` | 设计页缺 `derived_from`（不可溯源） | high | §0 口径 3 |
| `F6` | 契约声明 >1 个操作实体 | medium | 对应 `S3`（高内聚 / 服务边界） |

## 5. 测试流程与沙箱实测（2026-09-20 已跑通）

**沙箱库**：`FD案例沙箱-手机银行`（`c7426a6e-6285-46f5-9c5a-fe7111136acb`），克隆「企业知识」的向量/wiki 配置。
页面（13 个）：2 需求页、5 业务属性页、3 FD 页、1 概要设计页、2 服务详设页。

**脚本**：`%TEMP%\fd_sandbox.py`（可重跑；同标题幂等更新）。
用 `/opt/bodhi-venv/bin/python3` 跑；psql 调用较多，建议 `systemd-run --unit=…` 后台跑再取日志。

**实测结果**

1. 写页：需求页 / 属性页 / FD 页 / 概要设计页 / 详设页全部落库；`wiki_page_revisions` 有快照（v3–v11，revs 对齐）✓
2. 溯源：`derived_from` 链完整（需求页 ← 概要设计页 ← 服务详设页）✓
3. FD 关系：9 条关系（3 个 FD 页 × 决定/被决定）全部落库，`in_links` 反向可见 ✓
4. **第一次 check（故意让 ECIF 与手机银行都自维护「姓名」「手机号码」）** → 5 条 findings：
   - `F1/critical 姓名`（三要素之一被两个服务自维护）
   - `F1/high 手机号码`
   - `F3/high 姓名`：被决定属性（姓名）由手机银行自维护，而决定属性（证件种类+证件号码）的维护者是 ECIF → **副本会被写脏**
   - `F3/high 手机号码`（决定方 = ECIF 的「客户编号」）
   - `F4/high 手机银行`：幂等键用了「客户.手机号码」，不是任何 FD 的决定属性 → 幂等不可证明
5. **第二次 check（手机银行的 手机号码/姓名 改为「引用外部」、契约收敛到单实体、幂等键改用三要素）** → **findings = 0** ✓
6. 图谱：`GET /bodhi/graph?kb_id=<沙箱>` → `nodes=10 / edges=9`，边形如
   `{"source":"design/fd/…","target":"design/fd/…","type":"easvc:hasDeterminant"}`；
   加 `&types=easvc:FunctionalDependency` → 3 个 FD 节点 ✓（设计页在 UI 图谱里可见）

## 6. 沙箱发现的问题与处置

| # | 现象 | 根因 | 处置 |
|---|---|---|---|
| 1 | `ke_pages.add_relation` 拒绝 `easvc:hasDeterminant`：「该类型可用：无」 | **Neo4j 本体投影里没有 ea-service 模块的属性节点**（`MATCH (p:BodhiOntProperty {prefixed:'easvc:hasDeterminant'})` 为空；而 `artifacts/neo4j/10_ontology.cypher` 里其实有 23 处 easvc） | 已加受控回退（`apply_relation`：按 `target_closure` 校验后直接落行）。**待办**：把 `10_ontology.cypher` 重新载入 Neo4j，严格路径与 UI 关系下拉即可覆盖 ea-service |
| 2 | FD 的决定/被决定属性解析成了 slug | `_rel_line` 实参顺序写反（应 `[[slug|标题]]`） | 已修；重跑后 F2/F4 判定恢复正常 |
| 3 | 新建页 INSERT 报 SQL 语法错（kb_id / wiki_path 重复引号） | 拼字面量时把已带引号的值又包了一层 | 已修 |
| 4 | `module_label` 显示成类名（"业务属性"）而非模块名（"EA 服务契约扩展"） | 模块标签回退逻辑 | 仅展示层面，未修（低优先） |

## 7. 手工验证清单

```bash
cd /mnt/c/Users/PHJY/source/bodhi2
P=/opt/bodhi-venv/bin/python3
SB='FD案例沙箱-手机银行'

# ① 只读：FD 清单 / 服务详设与维护责任
$P tools/ke-core/ke_design.py fds "$SB"
$P tools/ke-core/ke_design.py services "$SB"

# ② 交叉验证（当前应为 0 findings；把某服务的「引用外部」改回「自维护」即可复现 F1/F3）
$P tools/ke-core/ke_design.py check "$SB" --compact

# ③ UI：知识库「FD案例沙箱-手机银行」→ wiki 列表可见 需求/概要设计/服务详设/FD 四类页；
#    Bodhi 本体图谱按类型筛选 easvc:FunctionalDependency / easvc:BusinessAttribute 应能看到 9 条 FD 边

# ④ 用完清理（硬删，保留 index）
$P tools/ke-core/ke_audit.py plan "$SB" --kinds all
$P tools/ke-core/ke_audit.py apply "$SB" --plan-id <id> --confirm
# 整个沙箱库不要了：UI 里删库，或 SQL：UPDATE knowledge_bases SET deleted_at=now() WHERE id=…
```

## 8. 下一步（还没做的部分）

1. **把设计页读写暴露成 MCP 工具**：`design_write_page`（包 `write_page`）与 `fd_check`（包 `check_couplings`），
   供设计智能体调用（沿用 `server.py` 的 `tool_definitions()` 注册方式）。
2. **两个设计智能体**：概要设计智能体（只读需求 wiki，产出任务/步骤+实体，**禁止出现属性名**）；
   详设智能体（读该服务的溯源页，产出属性级操作 + 维护责任，禁止自维护他人的决定属性）。
3. **规则外移**：把 `F1`–`F6` 与 `S1`–`S9` 统一进 `ontology/shapes/*.shapes.ttl` 并给 `rule_id`，
   由 `docs/bodhi-reasoning.md` 的 `validate` 模式执行（现为 Python 内联实现，编号已对齐）。
4. **修 Neo4j 投影**（问题 1 的待办），让严格关系校验与 UI 关系下拉覆盖 `ea-service` / `bmm-fd` / `ea-ownership`。
5. **FD 精化规则**：需求里 `证件种类 + 证件号码 → 姓名` 成立时，`U1` 的决定集合可最小化 ——
   在报告里给「最小覆盖 + 冗余决定属性」建议（当前 F 规则未覆盖）。

## 9. 落库自证与三个已修坑（2026-09-21 实测）

用户在「手机银行注册、实名验证、签约流程」上连测 4 次，报「工具回执写成功，但库里报告页正文一直是首版」。
定位到的三个**独立**原因（都已修 + 已用真库验证）：

1. **`fetch_existing_pages()` 排除 `summary`/`index` 页** → 报告页永远进不了合并候选，
   `stage=report` 永远走「新建」分支；而 slug 由标题推导，**与既有报告页完全相同 → 撞主键**；
   恰好 `sql_insert_page` 的护栏当时是「活页一律跳过（`WHERE deleted_at IS NOT NULL`）」→
   **语句静默无操作，回执仍报成功**。这是"报告页改不动"的直接原因。
   - 修：护栏改为「**活页只更新内容字段，不改 title/page_type/status**」（软删行仍是复活+全覆盖）；
     报告段按 `doc → upstream → 同标题` 三级复用既有页 slug；回执 `page_versions[].action` 如实区分
     `created` / `updated（同 slug 既有活页）` / `revived（复活软删行）`。
2. **合并时"取更长正文"的启发式**：报告改版后正文更短 → 静默保留旧正文。
   - 修：报告载荷带 `replace_body=True` → **正文整体替换**。
3. **`out_links` 只在新建页时写**：第二次跑（合并路径）追加的关系行不会进 `out_links`，
   而 `in_links` 是按 `out_links` 反推的 → 服务页「入边（引入的本体关系）」整片为空。
   - 修：`sql_update_page` 同步重算 `out_links`（`ke_pages.out_links_of`，与关系行解析同源）；
     现网已全量重算（企业知识 60 页 / 本体模型 225 页）→ 12 个 `ea/mcpservice/*` 的 `in_links` 全部有值。

**回执怎么读**（智能体与人都适用）：
- `applied=false` / `dry_run=true` / `write_note` 出现 ⇒ **没写库**，不要报告"已落库"；
  必须用**同一份载荷** `mode="apply"` 重跑。
- `page_versions[].before` = 写入前版本；写完后 `before+1`，`last_edit_source=bodhi-onto-mcp`。
- 报告页 slug 形如 `ea/summary/<标题>`；节点页 slug 形如 `ea/mcpservice/<名称>`。
  历史模块 `bmm-ea-ext/…` 已废弃（检索 0 条属正常）。

**同一轮修掉的另外四处**（都与上面这轮实测同源）：

4. **报告页来源文档**：报告段只给了 `source_document_title` 时也要**解析出 `knowledges.id`** 写进
   `source_refs`（否则正文写「来源：《…需求.md》」而 `source_refs` 空 → 巡检 **C3**，
   实测报告页命中；设计页的 `source_refs` 还靠它继承）。解析不到的，正文改写成「生成方式：设计智能体」
   （不再留"来源"字样）。现网已把报告页 `source_refs` 补成 `4747a82d…`（`mb-手机银行开户签约需求.md`）→ C3 归零。
5. **服务页渲染去重**：正文首段与 `## 用途` 是同一段时不再重复输出；
   `## 被引用（入边）` 明确标注「由系统按本体关系自动生成」（正文里不要手写引用链接）。
6. **关系面板数据源**：前端 `BodhiRelationsPanel.vue` 用 **GET** `/bodhi/relations?kb_id=&slug=` 取
   「出边 + 入边」（写边才用 POST `/bodhi/relations/{add,update,delete}`）。该分支必须在 `do_GET` 链里，
   否则 GET 落到 `http.server` 兜底 404（HTML）→ **面板永远空白**（用户报"查不到引入的本体关系"）。
   自检：`curl -sG --data-urlencode kb_id=<kb> --data-urlencode slug=<slug> http://127.0.0.1:8765/bodhi/relations`
   应返回 `{"out":[…],"in":[…]}`（键是 `in`，不是 `inbound`）。
7. **`sql_insert_page` 的 `out_links`**：活页更新分支也要写 `out_links`（否则第二次跑追加的关系行
   不进反向边，`in_links` 依旧空）。

**自检命令**（真库，含 dry_run → apply → 还原）：
```bash
/opt/bodhi-venv/bin/python3 - <<'PY'
import sys; sys.path.insert(0, '/mnt/c/Users/PHJY/source/bodhi2/tools/ontology-mcp')
import server
KB = 'dbc2528f-611b-48da-9a71-d7c93975adb4'
r = {"title": "手机银行注册、实名验证、签约流程 — IT 服务概要设计报告",
     "content_md": "# 自检\n\n正文替换自检。\n"}
print(server.save_knowledge(KB, stage='report', model='ea', report=r, mode='dry_run')['applied'])   # False
print(server.save_knowledge(KB, stage='report', model='ea', report=r, mode='apply')['page_versions'])  # before→+1
PY
```

## 10. 服务详细设计（2026-09-21）

**口径：详设不新开保存工具** —— 用同一份「通用保存」（`save_knowledge` 的 nodes/edges），
校验与渲染都由**本体**决定（见 §11）。详设 = 本体里多几个件 + 两个派生（都不新增工具）。

### 10.1 本体件（`ontology/extensions/ea-service-ext.ttl`）
| 件 | 形态 | 说明 |
|---|---|---|
| `easvc:ServiceOperation` | 类 | 服务操作/接口（一服务多操作；与 `ServiceContract` 刻意分开） |
| `easvc:crudKind` | 数据属性（domain=`ServiceOperation`） | `C/R/U/D`，**作为 `operationOperatesOnAttribute` 边的限定属性**填写 |
| `easvc:operationMethod` / `isIdempotent` / `transactionBoundary` | 数据属性 | 实现形态 / 幂等 / 事务边界 |
| `easvc:keyRole` | 数据属性（domain=`BusinessAttribute`） | `PK/FK/UNIQUE/NONE`（**不建 Key 类**，避免为建模而建模） |
| `easvc:serviceHasOperation` / `operationOfService` | 对象属性 | 服务 ↔ 操作（互逆） |
| `easvc:operationOperatesOnAttribute` | 对象属性（边带 `crudKind`） | CRUD 本体，耦合分析的数据基础 |
| `easvc:operationAccepts` / `operationReturns` | 对象属性 | 操作级输入/输出属性 |
| `easvc:referencesAttribute` | 对象属性 | 外键 → 被引用属性（应指向 PK/UNIQUE） |
| `easvc:serviceOwnsEntity` | 对象属性 | 服务负责的实体（判边界内聚） |

TBox：`ServiceOperation` 至少 1 个 `operationOperatesOnAttribute` + 至少 1 个 `operationMethod`；
OWL 表达不了的（FK 配引用、写操作非幂等需说明、同属性被 ≥2 服务写）由巡检 **E 类**兜底。

### 10.2 落库（通用保存，无新工具）
```jsonc
{"kb_id": "...", "model": "ea", "stage": "graph", "mode": "apply",
 "nodes": [
   {"name": "开户服务", "type": "ea:MCPService", "definition": "...", "purpose": "..."},
   {"name": "创建客户", "type": "easvc:ServiceOperation", "definition": "...",
    "attributes": {"easvc:operationMethod": "MCP tool: createCustomer",
                   "easvc:isIdempotent": "false", "easvc:transactionBoundary": "single"}},
   {"name": "客户号", "type": "easvc:BusinessAttribute", "definition": "主键",
    "attributes": {"easvc:keyRole": "PK"}}],
 "edges": [
   {"source": "开户服务", "type": "easvc:serviceHasOperation", "target": "创建客户"},
   {"source": "创建客户", "type": "easvc:operationOperatesOnAttribute", "target": "客户号",
    "properties": {"easvc:crudKind": "C,U"}}]}
```
`edges[].properties` 渲染进页面独立的 **`## 关系限定（边属性）`** 小节（刻意**不动** `## 本体关系` 行语法，
那行被 `ke_pages` 解析成 `out_links`/`in_links`）。解析器把 `，` 当分隔符**仅当**其后是 `前缀:名=`，
所以 `crudKind=C,U` 这种"值里带逗号"不会被拆坏。

> **落库必须带 `report.upstream`（服务页 + 报告页）**：新属性/操作页的 `source_refs` 从上游页继承
> （`save_knowledge` 里 `report.slug` 或 `report.upstream` 任一有值即可），否则新页是"实例页无来源" → 巡检 **C1**（high）。
> 完整样例（**已随技能移除、待重构重建**）：原路径 `skills/service_detailed_design/EXAMPLE.json`（真实数据「身份三要素采集服务」：3 操作 / 5 属性 / 24 条边）。

### 10.3 派生一：`## CRUD 矩阵`（服务页，确定性渲染）
落库后自动刷新（`server.refresh_crud_matrix`；跨页聚合：服务 → 操作 → 属性 + `crudKind` + `keyRole`）。
历史页/手工改过关系行的页用 CLI 重刷：
```bash
/opt/bodhi-venv/bin/python3 tools/ontology-mcp/refresh_design.py --kb 企业知识 --all-services [--dry-run]
```
只读视图（前端/运维）：`GET /bodhi/crud?kb_id=&slug=` → `{service, operations[], attributes{}, matrix_md}`。

### 10.4 派生二：耦合与完整性巡检（E 类，只读）
| 检查 | 判定 | 严重度 |
|---|---|---|
| E1 写耦合 | 同一业务属性被 **≥2 个服务**以 C/U/D 操作 | medium |
| E2 读耦合·**经接口** | 读方服务**已声明** `easvc:operationDependsOnOperation`（本操作 → 对方查询操作） | low（合理耦合） |
| E2 读耦合·**疑似直读** | 读了别写的属性但**没声明**操作依赖 | medium |
| E3 详设完整性 | 服务无操作 / 操作无被操作属性 / 缺 `operationMethod` / 写操作 `isIdempotent=false` 无幂等说明 | low–medium |
| E4 键一致性 | `keyRole=FK` 无 `referencesAttribute`；引用目标非 PK/UNIQUE；目标页不存在 | medium |

**改设计要能减边**：`save_knowledge` 的关系项支持 `"retract": true` —— 撤回该关系（删掉源页里指向
target 的 `## 本体关系` 行 + 同键的 `## 关系限定（边属性）` 行，带版本快照与反向边重算），
回执列在 `retract`（dry_run 时列在 `retract_planned`）。另注意：**要改哪个页的边，那个页必须出现在 `nodes` 里**
（关系行只对载荷里出现过的节点生效），否则只有"减"生效、"加"落不下去。

**设计收口实测（同一天，企业知识库）**：E1 那条「客户姓名」两个写方 → 把注册服务的写收口到
`待认证的注册用户.注册姓名`（`手机号码`/`注册状态` 一并挂到该实体），并 `retract` 旧边 →
**E1 归零**；核查服务补 `operationDependsOnOperation → 查询待核查三要素` 声明 →
**E2 三条从「疑似直读」(medium) 降为「经接口」(low)**（合理耦合）。

```bash
/opt/bodhi-venv/bin/python3 tools/ke-core/ke_audit.py scan <kb> --scope coupling
```
> E 类并入 `scope=all` ⇒ **尚未做详设的服务会各报一条 E3(low)**（"还没有详细设计"）；
> 这是**待办信号**不是数据错误，只看详设问题时用 `--scope coupling`。

**E3 是可消解的（2026-09-21）**：非幂等写操作补 `easvc:operationRetryPolicy`（重试退避/补偿与
幂等键来源）后，E3 不再提醒 —— 把"红点"变成**可消解的设计字段**。
**派生小节在合并时会整体替换**（`## 用途 / 输入 / 输出 / 设计规范 / 属性（数据属性）/ 关系限定 / 被引用（入边）`
由本次载荷推导）：否则"已存在就跳过"会让**改设计落不下去**（补属性/改 CRUD 无效）。

**真实数据实测（2026-09-21，3 个服务已详设）**：
- 矩阵：`身份三要素采集服务`（3 操作/5 属性，客户编号 PK、证件号码 UNIQUE）、
  `实名联网核查服务`（3 操作/7 属性，写核查结论·流水号·时间，读三要素与证件有效期）、
  `注册信息录入服务`（2 操作/3 属性，写客户姓名·手机号码(UNIQUE)·注册状态）。
- 耦合结论：**E1×1**（「客户姓名」被 注册信息录入服务 与 身份三要素采集服务 同时写）、
  **E2×3**（实名联网核查服务 读由采集服务写的 客户姓名/证件种类/证件号码）、
  **E3×1**（`核验三要素一致性` 非幂等且无幂等/重试说明）+ 其余 9 条 E3-low（待详设）。
- 踩到的坑（已修）：同一操作对同一属性给了**两条边**（一条 C、一条 R）→ `## 本体关系` 出现
  **两行完全相同** → 巡检 **A5**。修法：渲染层按 `(类型, 目标)` 去重、`## 关系限定` 按同键合并
  （`crudKind=C,R`），并在技能里明确"读写都用时写**一条边**"。
  样例（**已随技能移除**）：原 `skills/service_detailed_design/examples/coupling_example.json`（14 节点 / 37 边）。

## 11. 技能驱动与合并智能体（2026-09-21）

### 11.1 为什么由 MCP 承载技能
本部署的 WeKnora「技能」是**沙箱安装型**（`tenant_skills.sandbox_config_id` 非空、`bundle_ref/sha256`、
`read_skill` 工具、`SkillSettings.vue`），而本机 `tenant_sandbox_configs` **0 行**、
app env `WEKNORA_SANDBOX_DOCKER_ENABLED=false` —— 原生技能不可用。

所以技能放在**仓库单一来源** `skills/<id>/SKILL.md`（YAML front-matter + 正文），由 MCP 的一个工具提供：

| 调用 | 返回 |
|---|---|
| `skills()` | **目录**：id / name / when / models / stages / tools |
| `skills(skill="<id>")` | 该技能**完整指令** + `front_matter` + **按技能声明的 `scope` 收窄好的本体面**（类 / 关系 / 每个类的数据属性） |

好处：① 提示词只留"目录 + 纪律"，省 token；② 改技能**不用重新注册智能体**（mtime 缓存自动重读）；
③ 将来开沙箱后，**同一份 SKILL.md 可直接打成 bundle** 注册成原生技能（一份源两种呈现）。

### 11.2 技能（现有 3 个）
| id | 名称 | 源 | 模型（front-matter） | 阶段 |
|---|---|---|---|---|
| `domain_modeling` | 领域知识建模 | 文档 | `models: [ea, bmm]`，default `bmm` | `extract` |
| `structured_modeling` | 结构化数据批量建模 | Excel/CSV + 中文说明 | `models: [bmm, ea]`，default `bmm` | `model` → `probe` → `plan` → `apply` → `verify` |
| `document_review` | 文档评审 | 文档 + 业务策略 | `models: [bmm]` | `model` → `pick` → `rules` → `judge` → `report` |

> **技能只限「模型」、不限「本体类型」**（用户口径 2026-09-30）：front-matter **不写** `scope.classes/relations`；
> 每个技能第 0 步先与用户**单选模型**（评审/设计可能跨模型 → 单选底层模型），类型一律
> `ontology_types("<模型>")` 现查、按依赖引入关联模型。
> 设计类技能 `ea_overview_design` / `service_detailed_design` **已移除、待重构**（下方正文保留作设计记录）。

新增技能 = 建目录写 `SKILL.md`（front-matter 必填 `id`/`name`/`when`），MCP 重启后自动出现在目录里，**无需改代码**。

### 11.3 合并智能体 `bodhi-ea-modeler`
- 绑定：企业知识 + 企业本体模型；MCP = bodhi 本体服务；工具 = 读 + `skills` + `ontology_types`
  + `doc_outline` / `save_knowledge` / `extract_state` + 候选关联三件套 + 待确认裁决 + `audit_scan/audit_plan`
  + `service_overview` / `job_status`（**无原生写页工具**；抽取类工具 2026-09-22 已移除）。
- 提示词很薄（≈1.3k 字符）：**先看技能目录 → 取技能全文 → 照做**；把"回执 `applied=false` 不得说已落库"
  与"只处理点名对象"写成硬规则。
- 注册/更新：
```bash
/opt/bodhi-venv/bin/python3 deploy/weknora-fork/gen_agents.py --only modeler      # 生成 agents.sql
# 落库（免嵌套 wsl）
/opt/bodhi-venv/bin/python3 -c "
import pathlib,sys; sys.path.insert(0,'tools/ke-core'); import ke_db
ke_db.psql('BEGIN;\n'+pathlib.Path('deploy/weknora-fork/config/agents.sql').read_text(encoding='utf-8')+'\nCOMMIT;\n', stdin=True)"
```
- 旧智能体（`bodhi-ontology-bmm` / `bodhi-ontology-ea` / `bodhi-ea-design`）**已于 2026-09-22 全部停用**
  （软删，可回滚）；`bodhi-ontology-bmm` 是最后停用的那个（它绑的是已退役的整篇抽取工具面）：
```sql
UPDATE custom_agents SET deleted_at = now() WHERE id IN
  ('bodhi-ontology-bmm','bodhi-ontology-ea','bodhi-ea-design');
```

### 11.4 按对话收窄范围（`scope`，**已随整篇抽取一并移除**）
旧实现：`extract_and_save(..., scope={"classes":[...], "relations":[...]})` —— 服务端把范围写进抽取提示词，
再在结果上过一遍（`apply_extract_scope`）。**2026-09-22 起**：抽取侧不再由服务端调 LLM，收窄改为
「先 `ontology_types(model, focus="步骤")` 选类 → 只把这些类写进本批的 `save_knowledge` 候选」；
服务端仍会做**确定性合规校验**（类型白名单 + domain→range），范围外的要素进 `unmatched` 并附原因。

### 11.5 端到端验收（技能驱动是否真的发生）
```bash
# 驱动 bodhi-ea-modeler 跑一轮**只读**任务，并留证据
/opt/bodhi-venv/bin/python3 deploy/weknora-fork/try_agent_chat.py
#   ① 脚本日志：/mnt/c/Users/PHJY/AppData/Local/Temp/agent_run.log（SSE 原文 + 会话消息）
#   ② 工具调用日志：logs/mcp_calls_YYYYMMDD.log（时间/工具/耗时/入参/结果摘要）
```
**期望证据（2026-09-21 实测）**：
```
22:06:41 skills          78ms  args={}                                  result={"count": 3, ...}
22:06:44 skills        1120ms  args={"skill": "service_detailed_design"} result={"id": ..., "source": "skills/service_detailed_design/SKILL.md"}
22:06:48 audit_scan     137ms  args={"kb_id": "b1", "scope": "coupling"} result={"error": "知识库不存在：b1；请把可选清单里的 id 原样传…"}
22:06:52 audit_scan     935ms  args={"kb_id": "企业知识", ...}            result={...}
```
- **先目录 → 再取全文**（这正是技能驱动的目标行为）；取到全文后它复述了 `scope.classes/relations`、
  `class_attributes`（含 `operationRetryPolicy`）与纪律（E1/E2/A5）；
- `kb_id` 传错被服务端纠正后自己改正（参数容错在起作用）；
- **零写库**：该轮没有任何 `save_knowledge` 调用，wiki 页 `updated_at` 无变化。

> 排查提示：若 app 侧报 `failed to call tool: ... EOF`，先看 `bodhi-mcp` 的 journal 有没有 `tools/call`
> 与异常 —— 服务端 handler 抛异常会直接关连接（2026-09-21 实测：日志函数里漏 `import time` 就是这个症状）。

### 11.6 服务详细设计总览（评审页，2026-09-21）
12 个服务铺开后，评审要看的是**一页**而不是 12×N 个页。所以新增**跨页聚合渲染**（确定性，不调 LLM）：

| 入口 | 用途 |
|---|---|
| 工具 `service_overview(kb_id)` | 只读预览（服务/操作/属性/依赖计数 + 前 N 行）|
| 工具 `service_overview(kb_id, apply=true)` | **异步**渲染并写入/刷新总览页（`job_status(job_id)` 查回执）|
| `refresh_design.py --kb <kb> --overview` | 运维/命令行同一份渲染（`--dry-run` 只预览）|
| `GET /bodhi/overview?kb_id=&format=json` | 前端/curl 取同一份 markdown（或 `format=json` 取结构）|
| 页面 | `ea/summary/it服务详细设计总览`（`summary`，同 slug 复用 + 正文整体替换，可反复跑）|

页面内容（5 节）：**① 规模与巡检结论**（服务/操作/属性/键计数 + E1/E2/E3/E4）→ **② 服务一览**
（每服务：操作数 / 作为写方的属性 / 只读属性 / 涉及键）→ **③ 业务属性与键**（键角色 + 所属实体 + 唯一写方；
写方 `—` = 本流程之外的既有系统，只读）→ **④ 跨服务读依赖**（读方操作 → 被依赖的写方操作 → 写方服务）→
**⑤ 逐操作明细**（实现方式 / 幂等 / 事务边界 / 写 / 读 + 非幂等写的重试口径）。

实测（2026-09-21，企业知识）：12 服务 / 29 操作 / 32 属性 / 20 条跨服务依赖，总览页 7814 字；
巡检 `pages=122 findings=12 {E2:12}`（新增总览页**没有**引入 C1/C3 等发现）。

性能口径（重要，避免 MCP 60s 硬超时）：一次聚合 ≈30s（12 次 `crud_model` + 1 次批量操作页查询 + 1 次属性页查询），
巡检 `scope=coupling` ≈0.8s，`save_knowledge(report)` ≈6s → **预览可同步**，渲染+落库走**异步 job**。

### 11.8 领域建模 v2：分批交互（2026-09-21，替代异步一次性抽取）
用户口径：内网算力有限，**一次交互的输入 token 上限当会话参数**；按切片大小与父子关系组织"合适的上下文"，
一轮读一批、落一批；每轮回执带**页面名称 + 编号**，所以会话里始终握着整篇文档的索引；跨上下文的关联先向量
召回候选、**单独确认后**才写入；**原来的异步提取退役**。

**工具面（新增 5 个，`save_knowledge` 新增 `session` 参数）**

| 工具 | 作用 |
|---|---|
| `doc_outline(kb_id, knowledge_id, budget_tokens, cursor, batches, with_text)` | 按**父子切片**把文档组织成上下文批次；回执含本批正文、`next_cursor`、`done`、`est_total_batches`、`split_parents` |
| `extract_state(kb_id, knowledge_id, action)` | **会话页索引**：`pages[{no,title,slug,type,round_no}]`、轮次、游标、待确认候选；`reset` 清状态 |
| `link_candidates(kb_id, source_slug, relation, candidates, knowledge_id, round_no)` | 登记跨批/跨库的**候选关联**（**不写库**），返回 `candidate_id` |
| `list_link_candidates(kb_id, knowledge_id, status)` | 列候选（按 id 去重；`pending/confirmed/rejected/all`）|
| `resolve_link_candidate(kb_id, candidate_id, action)` | 裁决：`confirm` = 真写关系边（页面版本 +1，可回退）；`reject` = 丢弃；驳回后可**复活**再确认 |
| `save_knowledge(..., session={...})` | 落库时带上会话：回执 `created[].no` = **会话编号**，并回写状态（dry_run 只预览编号）|

**切片怎么组织（`_chunk_units` / `_split_unit`）**
- WeKnora 的层级切片是 `parent_text`（父）+ `text`（子，`parent_chunk_id` 指父）；**父块已含子块正文**
  → 上下文用**父块正文**，子块只用于定位（`chunk_refs` / `source_text` 匹配），避免重复喂模型；
- **父块本身超过预算时按子块细分**（子块成为独立上下文原子，首个原子带父块开头 120 字作归属提示）
  → 所以"把阈值调小"永远能继续缩小；无子块可拆时在 `oversized_units` 里明示；
- `budget_tokens` 下限 500（避免荒谬值）；估算用 `chars/1.6`（可用 `BODHI_CHARS_PER_TOKEN` 调）。

**会话状态**：`state/domain_sessions/<kb_id>/<knowledge_id>.json`（页面索引/轮次/游标/候选关联；已 gitignore）。
**cursor 语义**：按"当前预算"展开后的原子序号 → **中途改预算请从 `cursor=0` 重跑**（落库幂等，同 slug 命中即合并）。

**实测（沙箱库）**：`mb-手机银行开户签约需求.md` 在预算 8000/1500 下 1 批；预算 600 时父块拆成 **8 个子块原子**，
连取 3 批覆盖 8 个 chunk、**不重不漏**、`done=true`；落库两页拿到编号 **1、2**（dry_run 只预览不落状态）；
候选关联：登记 → 重复登记被 skip → 驳回 → 再登记**复活** → 确认后页面出边出现真实关系（版本 +1）→ 删除还原。

> **退役 → 移除（2026-09-22）**：`extract_and_save` / `extract_status` 已**从服务端删掉**（含 `run_extraction` /
> 任务池里的抽取键等死角代码），原文留痕 `tools/ontology-mcp/archive/async_extract_retired_2026-09-22.py.txt`；
> `tools/ontology-extract/extract.py` 里的 LLM 调用链与 CLI 入口同样回收（见该包 `archive/`）。
> 服务端工具面因此从 15 → **14**（`extract_status` 改名为 `job_status`）；智能体 `bodhi-ea-modeler` 仍为 18 个工具。
> 详见技能 `skills/domain_modeling/SKILL.md`（v0.2.0）与 `docs/handoff-domain-modeling-v2.md`。

### 11.7 原生技能（沙箱）路线：现状与开法（2026-09-21）
本部署的技能**由 MCP 承载**（§11.1）—— 因为 WeKnora 原生技能是**沙箱安装型**：
`tenant_skills` 绑 `sandbox_config_id`，上传物是 **zip bundle**，安装时**在沙箱后端构建快照镜像**。
本机默认 `WEKNORA_SANDBOX_DOCKER_ENABLED=false` 且 app 未挂 `docker.sock`，所以原生路线此前不可用。

**同一份 SKILL.md 两种呈现**已打通到"只差上传"这一步：

| 步骤 | 命令 / 证据 |
|---|---|
| ① 打包 bundle（zip + sha256，SKILL.md 在根） | `python3 tools/skills/bundle.py` → `skills/dist/<id>-<version>.zip` + `manifest.json` |
| ② 开沙箱（**可逆**，⚠️ socket ≈ 宿主 root） | `bash deploy/weknora-fork/enable_sandbox.sh [--apply/--revert]`（改 `.env` + compose 挂载 + 重建 app）|
| ③ 基础镜像在 daemon 上 | `docker pull wechatopenai/weknora-sandbox:latest`（= compose `sandbox` 服务的镜像）|
| ④ 建 sandbox config + 上传安装 | `python3 deploy/weknora-fork/register_native_skills.py --list / --ensure-config / --upload` |
| ⑤ 校验 | `GET /api/v1/skills`（智能体可用清单）、`GET /api/v1/sandbox-configs/{id}/skills`（安装状态）|

Front-matter 要求（Go 侧 `ParseSkillFile` + `parseSkillBundleVersion`）：YAML 合法，且有 `name` / `description` / `version`
→ 因此给三份 SKILL.md 补了 `description:` 与 `version: 0.1.0`（**原生**要用；MCP 侧照旧读 `when` 等，不受影响）。

> 口径：**MCP 承载仍是主路**（不需要沙箱、改技能免重启、改完即生效）；开沙箱只是把同一份技能再挂到原生技能面，
> 便于在 UI 的「技能」页里看到/勾选。开与不开都不影响 `skills()` 工具的行为。

#### ⚠️ 开沙箱的硬前提：SSRF 白名单（2026-09-21 实测踩到）
沙箱一开，app 对**出站 URL** 走严格 SSRF：`internal/utils/security.go` 的 `restrictedHostnames` 含
`host.docker.internal`，且 `internal/mcp/security.go ValidateServiceOutboundURLs` 在**每次建 MCP 客户端前**再校验一次。
于是 MCP 服务 URL（`http://host.docker.internal:8765/mcp`）被拒：

```
ERROR Failed to create MCP client for service bodhi_ontology:
      MCP service URL failed SSRF validation: hostname host.docker.internal is restricted
WARN  registerMCPTools | No MCP tools registered from 1 enabled service(s)
INFO  stage=Agent action=tools_ready tool_count=5      # 只剩 5 个 wiki 工具（技能面整体失效）
```

症状很有迷惑性：智能体说"这些工具在我的环境里不存在"（因为它**真的**没拿到）。
修法：`.env` 里给 `SSRF_WHITELIST_EXTRA` 补 `host.docker.internal`；**注意**该变量会**覆盖** compose 的默认值
（`searxng,qdrant,milvus,weaviate,doris-fe,doris-be,minio`），所以要把默认一起写上。改完重建 app →
`tool_count` 从 5 回到 **15**，`skills/audit_scan/save_knowledge/service_overview` 全部恢复。
`enable_sandbox.sh` 已内置这一步（`--revert` 时摘除）。

#### ⚠️ 重建 app 之后：必须让 nginx 重新解析上游（否则"登录报错"）
症状（2026-09-21 实测）：前端能打开，但**登录/任何 API 都报错**；前端 nginx 日志刷
`connect() failed (111: Connection refused) while connecting to upstream: http://172.18.0.6:8080/api/...` → 502。

根因：`proxy_pass` 里的上游主机名 **nginx 只在启动时解析一次**。`docker compose up -d app` 重建 app 后容器 IP 变了，
nginx 仍连旧 IP。（`deploy_frontend.sh` 里早就写着这个坑："重建后 app 容器 IP 会变，nginx 启动时只解析一次 → 必须 docker restart"。）

两手都要有：
1. **运维动作**：任何重建 app 之后 `docker restart WeKnora-frontend`
   （`enable_sandbox.sh` 已内置这一步）。
2. **根治（已落地）**：`deploy/weknora-fork/frontend/default.conf.template` 里
   ```nginx
   resolver 127.0.0.11 valid=10s ipv6=off;          # Docker 内嵌 DNS，按 TTL 重解析
   set $app_backend "${APP_SCHEME}://${APP_HOST}:${APP_PORT}";
   ...
   location /api/ { proxy_pass $app_backend; ... }   # 变量式 proxy_pass → 每次请求重新解析
   ```
   模板是**从仓库 bind-mount** 进容器的（`overlay: …/default.conf.template:/etc/nginx/templates/default.conf.template:ro`），
   所以改完只需 `docker restart WeKnora-frontend`（entrypoint 会重跑 envsubst），**不用重建镜像**。
   注意：变量式 `proxy_pass` 不能带 URI，以上各 location 的路径都是原样透传（identity，与改前等价）。

验收：`curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1/api/v1/auth/config` → 200；
错密码登录 → 400/401（**不是 502**）；`curl http://127.0.0.1/` → 200；`/bodhi/graph?...` → 200。

- 安装是**LLM 驱动**的沙箱会话（`WeKnora Skill Installer`，工具 `edit_skill_file` / `shell_exec` / `write_skill_file`），
  会建 venv、装依赖、最后打**快照镜像**（`tenant_skill_snapshots`：building → active → superseded）。
- **app 重启会杀掉正在安装的会话** → 行会停在 `installing`。恢复：`POST /api/v1/sandbox-configs/{cfg}/skills/{id}/reinstall`
  （幂等；已 ready 的重跑不影响）。
- 观察：`docker ps | grep sandbox`（安装容器在不在）、`tenant_skills.status`、`GET /sandbox-configs/{cfg}/skills`。
- `GET /api/v1/skills` 只在**技能可用**时返回条目（`skills_available` 字段）；安装中途会是 `false`。

### 11.9 写库目标库唯一化 · 跨库隔离 · 文案按技能（2026-09-22）

用户实测两件事：① 领域建模会话可绑多个知识库，写库时含糊的 `kb_id` 会**静默写进别的库**（回执成功、
自己的库里没有记录）；② 领域建模的页面/报告写着「概要设计…/无原文片段」（三技能共用一份落库代码，
文案写死）。修复：

| 问题 | 根因（改前） | 现在 |
|---|---|---|
| 写库目标库不唯一 | `ke_db.resolve_kb_id` 支持"名称包含匹配"，写路径也用它；会话绑了几个库服务端不知道 | 新增 `ke_db.resolve_kb_candidate`（exact/fuzzy）+ `server.resolve_write_kb`：多库未指定 → 拒（`need_kb_selection`）；模糊命中=1 → 需 `confirm_kb_match=true`；精确（完整 uuid / 精确库名）直通 |
| 跨库"合并"假象 | `_graph_target_slug/_type` 按标题在**全库**找页（少了 `knowledge_base_id` 过滤）→ 把别库的 slug 写进本库页的 `## 本体关系`/`out_links` | 两处加库过滤；`save_elements` 落库前**再断言** relation.target_slug 属于本库，越界的进 `dropped_relations`；目标只存在于别库时回 `cross_kb_same_name`（**不跨库合并**） |
| 文案串技能 | `design_elements` 写死 `（概要设计，无原文片段）`；报告页 `type_label/category_path/source_text` 写死「概要设计报告」 | 新增 `context`（`domain_modeling` / `ea_overview_design` / `service_detailed_design`，缺省=旧口径）→ `SKILL_CONTEXTS` 决定占位文案与报告页文案；三个技能文档已写明要显式传 `context` |
| 读范围 | 我们 MCP 的读工具都是单库 | `audit_scan` / `list_pending_merges` 支持 `kb_ids`（多库逐库，不跨库合并计数）；**全库检索**仍是 app 原生工具（`grep_chunks`/`wiki_search`）按会话绑定库决定 |

回执里新增/常用的字段：`need_kb_selection` / `need_kb_confirm` / `matched` / `bound_kb_ids` /
`cross_kb_same_name` / `dropped_relations` / `context`。

### 11.10 上传 TTL 自动编译 + 运维修复（2026-09-24）

**背景**：上传 TTL 以前只更新 Neo4j 图库（"零产物"），而编译产物 `artifacts/weknora/ontology_index.json`
才是**类型校验**（`ontology_types` / `save_knowledge` 的 domain→range）、前端类型下拉、以及
`apply_projection()` 回放的依据 —— 于是"上传成功但新类被判『本体里没有这个类』"，看起来像没生效。

**现在**（默认行为，开关默认开）：

```
前端「上传本体文件」
  ├─ write_source=true    → TTL 落真源 ontology/extensions/<key>-ext.ttl + 登记 _registry.json
  │                          （编译器 config.build_modules() 会读注册表 → 认得上传来的模块；
  │                            缺 bodhi:expertRole 时自动补一条默认值，否则编译会被拒）
  └─ compile_after=true   → compile.py compile（artifacts 更新）→ 回执报 compiled.delta
                            （totals before→after：modules/classes/object_properties/…）
                          + project_wiki=true 时再重投影本体库 wiki
                          （apply_after 默认 false：导入步骤已写本模块语句，全量回放交给 repair）
```

**运维修复**（崩溃/手工改动/换机器后的四层对齐；幂等）：

```bash
python3 tools/ke-core/ke_admin.py repair [kb_id]
# 等价 HTTP：POST /bodhi/ontology/repair {compile:true, project_wiki:true}
# 做四件事：编译 artifacts → 全量回放 Neo4j 投影 → 重投影本体库 wiki → 一致性体检（含 C5）
```

> 已知经验：**投影会按 Neo4j 实况做减法** —— Neo4j 缺对象属性/模块节点时，投影页数会少于产物规模
> （实测 248→237）；先 `apply_projection()` 再投影即恢复。`repair` 已包含这一步。

### 11.11 页唯一性语义 · id 方案加固（2026-09-24）

用户提问：「企业知识和领域知识都有『客户信息』、slug 相同 → 保存/更新到了别的库」这个判断成立吗？为它做的
修改（id 并入 kb + 撞车时加后缀重试）合理吗？将来会不会出问题？—— 逐条核对后的结论与口径：

**① 诊断成立**（证据）：修复前那批 11 页的 id **全部等于旧口径** `uuid5("bodhi-element:"+slug)`，却落在
`dbc2528f`(5 页)/`c7426a6e`(6 页)；目标库 `522d5f81` 当时 0 页；而目标库的会话状态文件
（`state/domain_sessions/<kb>/`）记着这 11 页 → `save_knowledge` 解析出的目标库确实是它。即
`ON CONFLICT (id) DO UPDATE` 撞上**别库那一行**并把它更新了，回执仍报成功。表侧佐证：`wiki_pages_pkey`
只在 `id` 上、`knowledge_base_id` 不在唯一键里 → "id 只按 slug"时同名页跨库**物理上不可能**各有一行。

**② 唯一性键 = `(kb_id, slug)`**，而 slug 自带模块与类：

```
slug = <类所属模块>/<类名（冒号后部分，小写）>/<normalize_name(名称)>
       例：ea/activity/手机银行注册、ea/businessentity/客户信息
```
- 模块取自**类自身声明的模块**（`class_meta()[cls].module`），不是调用方传的 model → 同名同类但在不同模块 = 不同页；
- `normalize_name` 抹掉空白/全角空格与 `·、，,。.（）()【】[]:：;；-—_/` 并转小写 → 「客户信息」「客户 信息」
  「客户-信息」「（客户信息）」视为**同名**；括号也被抹掉 → 「客户信息（个人）」≠「客户信息」→
  **同名不同义想各自成页，在名称里加限定词即可**（零代码改动）。

| 情形 | 结果 |
|---|---|
| 同库 + **同类** + 同名（归一化相等） | ✅ **同一页**（自动合并/更新，幂等）|
| 同库 + **同名但不同类** | ✅ 各自成页（slug 含模块 + 类）|
| 同库 + 同类 + 名称归一化**不等** | ✅ 各自成页 |
| **跨库** + 同 slug | ✅ 各自成页（id 并入 kb，见 11.9 + 本轮）|
| 同库 + **同根兄弟类** + 同名（`ea:APIService` vs `ea:MCPService`）| ⚠️ 算**同类型族** → 会合并（2026-09-21 特意放宽，为修"2 套服务"；载荷带 `retag` 时改成改类型而非另建）|

分数档：名称归一化相等 → 相似度 1.0 ≥ `high`(0.90) → **自动合并**（设计路径还先按同类型族过滤候选）；
`low`(0.75)~`high` 之间 → 建「待确认合并」页、人工裁决；≤ `low` → 新建页。

**③ id 方案与兜底分支**：
- 规范派生 `uuid5("bodhi-element:<kb>|<slug>")`；本库已有同 slug 页 → **沿用其现有 id**（老数据零迁移、幂等不变）；
- 兜底后缀（`|<n>`，n=1…49）只在"规范 id 已被**别的库**占着"时触发 —— 新方案下这需要 uuid5 碰撞或有人直接写库，
  **实测从未触发**：库里 `bodhi-onto-mcp` 写的活页 = 旧派生 144 / 新派生 37 / **其它 0**；同库同 slug 多行 **0**；
- 所有读/更新一律按 `(kb, slug)` 定位，**没有"按重算 id 反查页"的代码** → 即便 id 偏离规范也不影响功能；
- 本轮加固（把"静默"变成"可见"）：
  - 回执 `id_strategy{existing,new}`（异常时附 `id_notes` 与 `odd[]` 清单）；兜底触发会打日志
    `[mcp] ⚠ 页 id 走了兜底分支…`（由 `sql_insert_page(strategies=…)` 出参驱动）；
  - 巡检新增 **D4**（同库同 slug 多行 = 影子页）与 **D5**（页 id 非规范派生；旧/新/后缀三种口径都算"规范"，
    第三种才算异常）；
  - 口径写进交付手册 `ONTOLOGY-KB.md` §6 的体检清单。
