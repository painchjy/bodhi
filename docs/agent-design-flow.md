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
