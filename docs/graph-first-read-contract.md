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

## 7. 何时**必须**用 PG（例外清单）
1. 需要 `version` / `deleted_at` / 软删过滤 ✓
2. 需要 `page_metadata` 全量（含非属性段 ✓）
3. 图服务不可用（降级 ✓）
4. **写**路径的"已存在判定"与版本回执（`page_info_map` ✓）
