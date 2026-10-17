# 取数口径：**图本优先（graph-first）** — 读知识走 Neo4j，PG 只作降级

> 2026-10-16 定稿。适用于 `tools/ke-core/*` 与 `tools/ontology-mcp/server.py` 的所有**读**路径。
> 写路径不受影响（写仍是"PG + 图"双写，见 `docs/knowledge-write-contract.md`）。

## 1. 为什么（实测，不是推测）
| 取数方式 | 一次往返 | 备注 |
|---|---:|---|
| **Neo4j 一次 Cypher（含节点属性 + 出边）** | **5–15 ms** | `ke_neo4j.query(cypher, params, timeout=8)` |
| PG 一次 `docker exec psql`（含子进程启动） | **150–250 ms** | `ke_db.psql_csv(...)` |
| 差距 | **≈ 20×** | 同一台机、同一份数据 |

因此：**能在图上一次拿全的，绝不查 PG** ✗。

## 2. 图上有哪些字段（`BodhiInstance` 节点）
```
身份：  slug / name / page_type / module / kb_id / tenant_id / wiki_content / definition
属性：  **去前缀存**（`bmm:ruleImplementation` → `ruleImplementation`）
        ruleImplementation / knowledgeNeed / ruleScope / enforcementLevel / ruleReference
        skos:note → note ；其余数据属性同理（见 ke_graph.upsert_nodes_batch 的 `split(":")[-1]`）
边：    `bmm:isDerivedFrom` / `bmm:isBasisFor` / `bmm:includesBusinessPolicy` /
        `bpmn:taskExecutesRule` / `bpmn:taskUsesDataObject` / `bpmn:taskHasRole` …
```
> ⚠️ 图里**没有** `version` / `deleted_at`（软删与版本以 PG 为正本 ✓）—— 要它们才查 PG。

## 3. 标准写法（单次 Cypher，属性 + 出边一起拿）
```cypher
MATCH (n:BodhiInstance {kb_id:$kb}) WHERE n.slug IN $slugs
OPTIONAL MATCH (n)-[rel]->(m:BodhiInstance {kb_id:$kb})
RETURN n.slug AS slug, n.name AS title, n.definition AS dfn,
       n.ruleImplementation AS impl, n.knowledgeNeed AS need,
       n.enforcementLevel AS lvl, n.ruleScope AS scope, n.ruleReference AS ref,
       n.note AS note,
       collect(DISTINCT {t: type(rel), s: m.slug}) AS outs
```
- 参照实现：`ke_review._rule_contracts()`（实测 **9 ms** / 2 条规则 ✓）
- 契约里必须带 **`source`**：`graph:BodhiInstance` ←→ `pg:page_metadata`（可审计 ✓）

## 4. 降级规则（必须保留 ✓）
```python
try:                       # ① 图不可用（available=False / 超时）
    ...Cypher...
except Exception:
    g = {}                 # ② 全量降级 PG
miss = [s for s in want if not (g.get(s) and (g[s].get("impl") or g[s].get("need") or g[s].get("lvl")))]
if miss:                   # ③ 图上有节点但属性全空（历史页面没走 upsert_node）
    ...只对这些 slug 查 PG...
```
**绝不**因为"图上有节点"就假定属性齐全 ✗ —— 历史数据可能缺（例：领域库里带
`ruleImplementation` 的节点 5 个、带 `knowledgeNeed` 的 2 个 ✓ 与 `needs_extract=2` 完全对上 ✓）。

## 5. 两个必须知道的坑
### 5.1 `docker exec -i` 必须关 stdin ✗→✓
```python
subprocess.run(cmd, ..., stdin=subprocess.DEVNULL)   # 必须 ✗ 否则非交互环境永久阻塞
```
`docker exec -i` 会**读 stdin 直到 EOF**；脚本/服务/cron 里 stdin 是未关闭的管道 ⇒ **挂死**
（现象：python 无输出、`timeout` 都杀不掉、`ps` 显示 D 状态）。已在 `ke_db.psql()` 修 ✓。

### 5.2 kb 清单要缓存
`resolve_kb_id()` 每次都要一份库清单（PG 230 ms ✗）⇒ 已在 `ke_db._kb_rows()` 加
**进程内 TTL 60s 缓存** ✓（`BODHI_KB_CACHE_TTL=0` 可关 ✓，写路径"立刻可见"场景用）。

## 6. 性能基线（回归用）
| 场景 | 预算 | 实测 |
|---|---:|---:|
| `rules_resolve("direct:…")`（2 条） | < 50 ms | **9 ms** ✓ |
| `rules_resolve("policy:…")`（6 条，递归） | < 100 ms | **32 ms** ✓ |
| `rules_resolve("task:逐项评审")`（6 条） | < 100 ms | **21 ms** ✓ |
| `_rule_contracts`（2 条，属性+出边） | < 50 ms | **9 ms** ✓ |
| `ke_db.psql_csv("SELECT 1")`（仅 PG 路径） | < 300 ms | **214 ms** ✓ |
（WSL 原生、`docker exec` 形态；Windows 直跑约 2–3×，属跨边界开销 ✓）

## 8. 一致性：属性只在写核里改（禁 raw SQL ✗）
**现象（2026-10-17 实测）**：领域库 6 条规则的 `knowledgeNeed` / `ruleImplementation`
**图 PG 全不一致** ✗ ——

| 规则 | PG | 图（改前） |
|---|---|---|
| 技术架构-开源软件适用规则 | 725 字 JSON ✓ | 19 字旧文本 ✗ |
| 部署架构-最低部署和灾备要求 | 1037 字 JSON ✓ | 82 字旧文本 ✗ |

原因：这些属性曾用 `ke_db.psql("UPDATE wiki_pages SET page_metadata=…")` **直接改 PG** ✗，
绕过了写核（`ke_pages.upsert_page` → `upsert_node(attributes=…)`）⇒ 图停在旧值 ✗。

**规则**：
1. **改页面属性一律走写核**（`ke_pages.upsert_page` / `ke_graph.upsert_node`）✓；raw SQL 只允许读 ✓
   （例外：`ke_graph.rebuild_kb_graph` 这类"从 PG 反向重建图"的运维动作 ✓）
2. **发现漂移**：跑一次 `ke_graph.rebuild_kb_graph(kb_id)` ✓（只重建节点/属性 ✓ **保留边** ✓）
   —— 本次修复后 `不一致 0 / 6` ✓
3. **回归检查**（10 行，建议纳入巡检）：
   ```python
   pg  = {slug: pg_attr("bmm:knowledgeNeed")}    # SELECT page_metadata->…
   gr  = {slug: node_attr("knowledgeNeed")}     # MATCH (n:BodhiInstance) RETURN n.knowledgeNeed
   assert all((pg[s] or "") == (gr.get(s) or "") for s in pg)   # 必须 0 差异 ✓
   ```
   > 这条检查正是"图为主、PG 可重建"的**验收断言** ✓：图 PG 一致 ⇒ 任一来源都能重建另一份 ✓。

4. **属性值必须是标量/字符串** ✗→✓：`ke_graph._attrs_of()` 只搬**字符串**属性 ⇒
   `verdicts={"符合":3,…}` 这类 **dict 值会被丢弃** ✗（重建也搬不回来 ✗）。
   ⇒ 写核前把对象 `json.dumps` 成字符串 ✓（或改 `_attrs_of` 支持标量+序列化 ✓，待办）。
5. **W10 的假阳性陷阱**（已修 ✓）：PG 侧对象属性经 `psql_csv` 回来的是 **Python 对象** ✗，
   `str(dict)` 得到单引号 repr ✗，与图上的 JSON 字符串 `{"a":1}` ✗ 永不相等 ⇒
   `_norm()` 必须先按对象 `json.dumps(sort_keys)` ✓ 再比 ✓。

所有契约/计划都必须带 `source`（`graph:BodhiInstance` / `pg:page_metadata` ✓）与 `hash` ✓：
- 一旦某条走了 PG 降级 ✓ ⇒ 说明图缺数据 ✗ ⇒ 触发 `rebuild_kb_graph` ✓ 而不是长期靠降级 ✓。

1. 需要 `version` / `deleted_at` / 软删过滤 ✓
2. 需要 `page_metadata` 全量（含非属性段 ✓）
3. 图服务不可用（降级 ✓）
4. **写**路径的"已存在判定"与版本回执（`page_info_map` ✓）
