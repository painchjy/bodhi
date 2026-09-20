# 本体层迁移到 Neo4j（`src` Web 应用 · 2026-09-19）

> 用户口径：**用 `src/services/ontology_service.py` 重构「本体模型知识库」的图谱与服务**；
> 原来本体存在本地 **Kùzu**，现在改为 **Neo4j**（`load_ttl` 与「类的初始化」都要适配），
> **尽量减少本体处理工作量**，并把**本体处理从 service 层剥离出来**；RESTful 接口已存在，
> 目标是把「前端要的本体类型查询、关系类型查询」直接跑到 Neo4j 上。

## 1. 一句话结论

`src/api/ontology.py` 的既有端点**一行没改**，但数据来源已从 Kùzu 换成 Neo4j：

| 端点 | 用途 | 迁移后数据来源 |
|---|---|---|
| `GET /api/ontology/node-types?model=bmm` | **本体类型清单**（类型下拉） | Neo4j `BodhiOntClass`（47 类，排除 `external` 占位） |
| `GET /api/ontology/rel-types?model=bmm&source_type=bmm:BusinessRule` | **按类筛关系类型**（含父类继承） | Neo4j `BodhiOntProperty` + `BODHI_DOMAIN` × `BODHI_SUBCLASS_OF` 闭包 |
| `GET /api/ontology/rel-target-types?model=bmm&rel_type=bmm:guides` | 关系可指向的**目标类型**（range 子类闭包） | Neo4j `BODHI_RANGE` + 子类闭包 |
| `GET /api/ontology/tree` `/schema` `/labels` `/models` `/models/{id}` `/models/{id}/graph` | 概念树 / 概览 / 标签 / 模型 / 结构图 | 同上（快照读） |
| `POST /api/ontology/load` `POST /api/ontology/upload` | 上传/加载 `.ttl` | rdflib 解析**不变** → 写 Neo4j（`MERGE` 到同一套词汇） |

## 2. 文件与职责（本体处理集中在这里）

| 文件 | 状态 | 职责 |
|---|---|---|
| `src/ontology/neo4j_store.py` | **新增** | 唯一与 Neo4j 打交道的地方：HTTP 事务端点客户端（纯标准库）、图词汇常量、读（类/属性/边/模型）、写（TTL 解析结果 MERGE）、删、投影自举 `ensure_projection()` |
| `src/services/ontology_service.py` | 改 | 保留全部对外方法与返回字段；**删掉了 Kùzu 连接与建表**，数据访问统一委托 `store.*`；TTL 解析沿用原 rdflib 实现（仅 `parse_ttl` 使用，缺 rdflib 时查询路径仍可用） |
| `src/services/graph_service.py` | **未动** | 实例知识图谱（Kùzu）继续由它负责；本体层与它**不再有任何耦合** |

数据流：

```
tools/ontology-compiler  ──(ontology/*.ttl)──►  artifacts/neo4j/10_ontology.cypher  ──► Neo4j 本体投影
        （仓库既有能力，不重写）                                     ↑
src/api/ontology.py ──► ontology_service ──► neo4j_store ──────────┘
        （上传 .ttl 时：rdflib 解析 ──► MERGE 进同一张图）
```

## 3. 图词汇（与编译器产物完全一致，同一张图两个写入方）

- 节点：`BodhiModule`（= 模型，`key/label/namespace/files`）、`BodhiOntClass`、`BodhiOntProperty`
- 关系：`BODHI_DECLARES` / `BODHI_SUBCLASS_OF` / `BODHI_DOMAIN` / `BODHI_RANGE`
- 兼容写入（仅 TTL 上传路径）：`BODHI_EQUIVALENT_CLASS`（等价类）、`BODHI_IMPORTS`（模型引用）
- 标记：本体节点带 `bodhi_projection='ontology'`；未声明的引用（`owl:Thing`、`xsd:string`、外部模块）
  建成 `external='True'` 占位节点，**查询时排除** —— 这样「range 落在外建命名空间 ⇒ 任意类型」
  这条既有判定仍然成立。

## 4. `load_ttl` / `init_tables` 的新行为

- `init_tables()`：不再 `CREATE NODE TABLE`，改为 `store.ensure_projection()` ——
  先数一下 `BodhiOntClass`；**为空**时执行 `artifacts/neo4j/00_constraints.cypher` + `10_ontology.cypher`
  （幂等 MERGE，分批 200 条），已装载时只花一次计数查询。
- `load_ttl(file, model_id, label)`：
  1. 路径解析：相对路径按 `ONTOLOGY_DIR`（缺省 `<repo>/ontology`）拼；
  2. `parse_ttl()`（rdflib，**原实现不动**）→ 类/属性/父类/域/值域/等价/import 的结构化结果；
  3. `store.upsert_ttl(...)`：模型节点 upsert → 清掉本模型的旧出边 → 逐条 `MERGE` 类/属性/边
     （含跨模型引用与 external 占位）→ 重建 `BODHI_IMPORTS`；
  4. 返回结构与原来一致（多一个 `"storage": "neo4j"`）。
- 语义收敛（有意为之）：`/rel-types` 现在**只返回对象属性**。数据属性没有 `range`，
  被当成「关系类型」会让前端建出没有目标的边（旧版会列出 `bmmfd:ruleExpression` 这类数据属性）。

## 5. 依赖与环境变量

- 运行期：**零新增 Python 依赖**（`urllib` + `json`）。rdflib 仍只被 `/load|/upload` 需要。
- 环境变量（默认对准本机 compose 的 `--profile neo4j`）：

  | 变量 | 默认 | 说明 |
  |---|---|---|
  | `NEO4J_HTTP_URL` | `http://127.0.0.1:7474` | Neo4j HTTP 端口（不是 bolt 7687） |
  | `NEO4J_USERNAME` / `NEO4J_PASSWORD` | `neo4j` / `password` | 与上游 `NEO4J_AUTH` 一致 |
  | `NEO4J_DATABASE` | `neo4j` | 数据库名 |
  | `BODHI_ONTOLOGY_PROJECTION_DIR` | `<repo>/artifacts/neo4j` | 投影产物目录（自举用） |
  | `ONTOLOGY_DIR` | `<repo>/ontology` | `.ttl` 目录（**仅在没有 `src/config.py` 时**用作兜底，真实工程以 `settings.ontology_dir` 为准） |

- ⚠️ 本仓库里**没有** `src/config.py`（`settings`）与 `src/main.py`（FastAPI 入口）——
  它们在你的 Web 工程里。迁移对 `settings` 做了**容错导入**：拿不到时退回 `ONTOLOGY_DIR` 环境变量、
  `.env` 模型元信息为空，因此 `ontology_service` 在本仓库也能独立跑（见下节自测）。

## 6. 自测（无需 FastAPI/rdflib/kuzu，直接打真库）

```bash
cd /mnt/c/Users/PHJY/source/bodhi2
python3 - <<'PY'
import sys; sys.path.insert(0, '/mnt/c/Users/PHJY/source/bodhi2')
from src.services.ontology_service import ontology_service as svc
print([(m["model_id"], m["class_count"], m["property_count"]) for m in svc.list_models()])
print([x["name"] for x in svc.get_node_types("bmm")][:6])
print([(r["name"], r["ranges"]) for r in svc.get_rel_types_for_source("bmm", "bmm:BusinessRule")])
print(svc.get_rel_target_types("bmm", "easvc:contractEnforcesRule"))
PY
```

2026-09-19 实测结果（本机 Neo4j 投影 181 节点 / 47 类 / 70 对象属性）：

```
models           : bmm(26/39) ea(11/13) ea-service(4/19) ea-ownership(4/14) bmm-fd(2/5)
node-types(bmm)  : 20 个可实例化类型（剔除抽象类与枚举）
rel-types(bmm:BusinessRule) : 9 条，含继承来的 bmm:guides（domain=bmm:Directive，range=bmm:BusinessProcess）
rel-target-types(bmm:guides)             -> ['bmm:BusinessProcess']
rel-target-types(easvc:contractEnforcesRule)
   -> ['bmm:BusinessRule','bmm:OperativeBusinessRule','bmm:StructuralBusinessRule',
       'bmmfd:AccessControlRule','bmmfd:DataQualityRule']      # range 子类闭包，含跨模块扩展
tree / labels / class_info / search / expand / resolve / model_graph(26 节点 50 边) 均正常
```

## 7. 本次没做 / 建议下一步

1. `src/api/review.py::_build_ontology_prompt` 仍用 `src/ontology/loader.py::OntologyLoader`
   **直接解析 TTL** 生成智能体提示词 —— 这是仓库里第二条本体解析路径（与 `ontology_service.parse_ttl` 重复）。
   建议下一步把 `OntologyLoader` 改成 `ontology_service` 的薄适配器（Neo4j 读），彻底消掉重复。
2. 实例层（知识节点/关系）仍在 Kùzu（`graph_service`）。若也要迁 Neo4j，建议同样先抽
   `store` 适配层，再逐个替换 `graph_service` 的 Cypher，避免一次性重写。
3. `src/config.py` 的 Neo4j 配置项（若你的工程已有 `Settings` 类）建议加上上表三个变量；
   本次迁移**刻意不新建** `config.py`，避免与真实工程里的配置冲突。
