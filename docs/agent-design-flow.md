# 智能体辅助设计流水线（需求 → 概要设计 → 服务详设 → FD 交叉验证）

> 2026-09-20 起。本文是**流程规格**：最少步骤、预期中间产物、每步的产物形态与校验点、测试流程。
> 验证案例（需求与设计文档分离）：
> - `docs/samples/mb-手机银行开户签约需求.md`（个人客户 注册/实名/签约）
> - `docs/samples/sp-个体工商户经营圈签约需求.md`（**增量需求**：个体工商户经营圈签约）
> - 背景 / 用途 / 验证过程：`docs/cases/sp-个体工商户经营圈签约-验证说明.md`
> 现状：**确定性内核已实现并沙箱跑通**（`tools/ke-core/ke_design.py`），LLM 侧的智能体提示词/MCP 暴露是下一步。

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
| 2 | **需求解析**：整篇抽取到 wiki（模型 `ea`） | MCP `extract_and_save` | 步骤 1 的 `knowledge_id` | 需求 wiki 页：任务/步骤/实体/角色（`ea:Task` / `ea:Step` / `ea:Customer`…），每页带 `chunk_refs` | 页数与文档内容量匹配；`source_refs` 非空 |
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
- 操作实体：客户（`ea:Customer`）        ← 多个实体 = 服务边界过宽（F6）
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
> 完整样例见 `skills/service_detailed_design/EXAMPLE.json`（真实数据「身份三要素采集服务」：3 操作 / 5 属性 / 24 条边）。

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
  样例：`skills/service_detailed_design/examples/coupling_example.json`（14 节点 / 37 边）。

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

### 11.2 三个技能
| id | 名称 | 源 | 模型（front-matter） | 阶段 |
|---|---|---|---|---|
| `domain_modeling` | 领域知识建模 | 文档 | `models: [ea, bmm]`，default `bmm` | `extract` |
| `ea_overview_design` | 企架概要设计 | 知识图谱 + 文档 | `models: [ea]` | `report` → `graph` |
| `service_detailed_design` | 服务详细设计 | 服务页 + 图谱 | `models: [ea-service, ea]` | `detail` |

新增技能 = 建目录写 `SKILL.md`（front-matter 必填 `id`/`name`/`when`），MCP 重启后自动出现在目录里，**无需改代码**。

### 11.3 合并智能体 `bodhi-ea-modeler`
- 绑定：企业知识 + 企业本体模型；MCP = bodhi 本体服务；工具 = 读 + `skills` + `ontology_types`
  + `extract_and_save/extract_status` + `save_knowledge` + 待确认裁决 + `audit_scan/audit_plan`（**无原生写页工具**）。
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
- 旧智能体（`bodhi-ontology-bmm` / `bodhi-ontology-ea` / `bodhi-ea-design`）**保留但可停用**（软删即可回滚）：
```sql
UPDATE custom_agents SET deleted_at = now() WHERE id IN
  ('bodhi-ontology-bmm','bodhi-ontology-ea','bodhi-ea-design');
```

### 11.4 按对话收窄范围（`scope`）
`extract_and_save(..., scope={"classes":[...], "relations":[...]})`：
服务端把范围写进抽取提示词，**并在结果上再过一遍**（`apply_extract_scope`）——
范围外的节点/关系进 `unmatched` 并附原因（`scope.filtered` 给出条数），**不静默丢**。
选范围用 `ontology_types(model, focus="步骤")` 或 `classes=[...]`。

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
- **零写库**：该轮没有任何 `save_knowledge/extract_and_save` 调用，wiki 页 `updated_at` 无变化。

> 排查提示：若 app 侧报 `failed to call tool: ... EOF`，先看 `bodhi-mcp` 的 journal 有没有 `tools/call`
> 与异常 —— 服务端 handler 抛异常会直接关连接（2026-09-21 实测：日志函数里漏 `import time` 就是这个症状）。

