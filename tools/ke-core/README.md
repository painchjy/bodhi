# ke-core —— 自研能力内核（小模块，零第三方依赖）

> 2026-09-19 建立。目的是满足用户要求：**把占用大量上下文的巨型文件拆开**
> （`tools/ontology-mcp/server.py` 曾 2600+ 行、70KB，改一行要整段读进来）。
>
> 规矩：本目录只用 **Python 标准库**；不 import `tools/ontology-mcp/server.py`
> （方向是单向的：`server.py` → `ke-core`，避免循环依赖）。

## 1. 模块清单（每个都能单独跑、单独读）

| 文件 | 职责 | 关键入口 |
|---|---|---|
| `ke_db.py` | Postgres 访问（`docker exec psql`）、SQL 字面量、时间戳、**kb_id 解析** | `psql` / `psql_csv` / `sql_str` / `sql_json` / `now_text` / `resolve_kb_id(raw)`（名称→UUID；未知库抛错，防"假清白报告"） |
| `ke_neo4j.py` | Neo4j 本体投影查询（**HTTP + Basic Auth**，官方镜像自带事务端点，免驱动） | `query(cypher, params)` / `available()` / `info()` |
| `ke_ontology.py` | 本体查询：类清单、**按类（含父类继承）筛对象属性**、range 闭包、按 range 找目标页 | `classes()` / `relation_types_for(page_type)` / `target_closure(rel_type)` / `target_pages(kb_id, rel_type)` |
| `ke_pages.py` | wiki 页面维护：本体关系增/改/删、类型修改、批量软删除、KB wiki 开关 | `page_relations` / `add_relation` / `update_relation` / `delete_relation` / `set_page_type` / `soft_delete_pages` / `set_wiki_enabled` |
| `ke_docs.py` | **按来源文档**统计/清理本体实例（删文档后的残留）：`source_refs` 聚合、独占页删/多源页摘引用、巡检 sweep、可选清 Neo4j `BodhiInstance` | `doc_index` / `pages_of_doc` / `purge_document` / `orphans` / `sweep` / `sweep_all` + CLI `stats\|pages\|purge\|sweep`（见 `docs/bodhi-doc-cleanup.md`）。kb_id 支持**名称或 UUID**（`ke_db.resolve_kb_id()`，未知库报错） |
| `ke_audit.py` | **知识运维只读体检**（P1）：wiki ↔ 本体图谱 ↔ 本体模型 一致性 + 无来源/来源已删等异常数据；MCP 工具 `audit_scan` / `GET /bodhi/audit` | `audit(kb_id, scope, max_findings)` + CLI `scan`（见 `docs/bodhi-ops-audit.md`） |
| `ke_design.py` | **设计流水线**：写设计页（概要设计 / 服务详设 / FD 报告）并做**页面级溯源**（`derived_from`）；读回 FD 页与服务详设页；**FD × 详设交叉验证**（规则 F1–F6） | `write_page` / `apply_relation` / `load_fds` / `load_services` / `check_couplings` + CLI `write\|fds\|services\|check`（见 `docs/agent-design-flow.md`） |
| `reason.py` | **（下一轮）** SHACL 规则判定与推导 —— 规格见 `docs/bodhi-reasoning.md`，规则源 `artifacts/rules/rules.json` | 待实现 |

`tools/ontology-mcp/server.py` 现在只负责：**MCP 传输 + 抽取合并流水线 + 只读图谱接口
+ `/bodhi/*` HTTP 路由**（路由实现全部委托给上面这些模块）。

## 2. 数据来源与降级

- **Neo4j 优先**：本体投影由 `ontology-compiler` 生成
  （`artifacts/neo4j/00_constraints.cypher` → `10_ontology.cypher`）。
  投影规模（实测）：模块 5 / 类 47 / 对象属性 70 / 数据属性 20 / 限制 26 / 枚举值 7。
  节点标签 `BodhiOntClass` / `BodhiOntProperty` / `BodhiModule` …，
  关系 `BODHI_SUBCLASS_OF` / `BODHI_DOMAIN` / `BODHI_RANGE` / `BODHI_INVERSE_OF`。
- **JSON 兜底**：`artifacts/weknora/ontology_index.json`（同一份 TTL 的编译产物）。
  Neo4j 不可用时自动降级，返回值里的 `source` 字段会标明 `neo4j` 还是 `json: …`。
- 连接参数（默认对准本机 compose）：`BODHI_NEO4J_HTTP`（默认 `http://127.0.0.1:7474`）、
  `NEO4J_USERNAME` / `NEO4J_PASSWORD`（默认 `neo4j` / `password`）、`BODHI_NEO4J_DB`。

## 3. 灌库 / 自测

```bash
# 1) 把本体投影灌进运行中的 Neo4j（幂等，MERGE）
cd /mnt/c/Users/PHJY/source/bodhi2/artifacts/neo4j
docker exec -i WeKnora-neo4j /var/lib/neo4j/bin/cypher-shell \
  -a bolt://localhost:7687 -u neo4j -p password --format plain -f - < 10_ontology.cypher

# 2) 模块自测（不需要前端）
cd /mnt/c/Users/PHJY/source/bodhi2/tools/ke-core
python3 -c "import ke_ontology as k; print(k.classes()['source'], len(k.classes()['classes']))"
python3 -c "import ke_ontology as k; print(k.relation_types_for('bmm:Goal'))"
python3 ke_pages.py relations dbc2528f-611b-48da-9a71-d7c93975adb4 'bmm/businessrule/…'
```

## 4. 写库纪律（照抄 `server.py` 的既有约定，别新发明）

1. 改页面前**先写 `wiki_page_revisions` 快照**，再 `version = version + 1`（可回退）；
2. `last_edit_source` 是 `varchar(16)`：本目录用 `bodhi-rel-edit`(13) /
   `bodhi-type-edit`(15) / `bodhi-page-del`(13)；
3. `out_links` / `in_links` 可能是**标量**脏值 → 一律 `jsonb_typeof` 守卫；
4. 页面主键是 slug 派生的确定性 UUIDv5 → 只 UPDATE / `ON CONFLICT DO UPDATE`，别盲插；
5. **`slug='index'` 永不删除**（批量删除接口里已硬编码保护）；
6. 关系（出边）只能由**本页**维护：反向边是对方页面的出边，本页只读展示。

## 5. 后续拆分建议（未做，已记入交接单）

- `server.py` 的**抽取合并流水线**（`build_new_page` / `merge_content` / `extract_and_save`
  / 相似度）体积最大，可整体搬成 `ke-core/extract_pipeline.py`，`server.py` 只留 MCP 壳；
- `graph_page.py` 的 HTML/JS（内嵌字符串）可拆成模板文件，便于单独改图；
- `reason.py` 落地时把 S1–S9 / O1–O5 判定写成纯函数（无 IO，便于 CI 断言）。
