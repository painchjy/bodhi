# 交接说明 · 本体上传即加载（2026-09-20）

> 新会话请**先读本文**。自包含：状态 / 接口 / 口径 / 坑 / 剩余任务 / 复现命令。
> 相关：`docs/session-handoff.md` §3.4bis（语义与操作恢复配方）、`docs/ontology-service-neo4j.md`（历史迁移记录，src 已删）、`docs/weknora-fork.md`（fork 相关）。

## 0. 一句话状态

**后端全通**：上传 `.ttl` → 级联删下游 + 依赖校验 → 复用编译器解析入库 → 图谱 / 接口（含继承的关系类型）/ wiki 页四者同步生成 → `cascade` 可精确撤销；**编译产物零写入**。
**前端按钮已补并上线**（§6.1 / §6.1bis，2026-09-20 晚）：本体模型知识库页 →「上传本体文件」→ 选 .ttl → 确认级联 → 报告面板；四处同步实测通过。
**随后用户实测又暴露并修掉 3 个后端缺口**（§6.4）：① 导入的语句归属判据用 key 子串 → 级联删掉的子模块被插回图库；② 依赖模块的**模块节点**语句带 `affects: '<上游 key>'` → 还漏一个空模块；③ wiki 重投影按编译产物 json（只加不减）→ 已删模块的页面复活。
当前基线：类 47 / 属性 90 / 边 SUB24-DOMAIN90-RANGE92 / model-graph 47 节点 96 边 悬空 0 / wiki 216 页（Class 47·Property 90·Relation 70·Module 6·LightDoc 2·index 1）。

## 1. 环境与硬约束

| 项 | 值 |
|---|---|
| 仓库 | `c:\Users\PHJY\source\bodhi2` ↔ WSL `/mnt/c/Users/PHJY/source/bodhi2` |
| venv | `/opt/bodhi-venv`（rdflib 7.6 / PyYAML / openai） |
| MCP 服务 | `bodhi-mcp.service` → `/usr/local/bin/bodhi-mcp` → `exec /opt/bodhi-venv/bin/python3 …/tools/ontology-mcp/server.py --host 0.0.0.0 --port 8765`（原件备份 `bodhi-mcp.bak-<ts>`） |
| 端口 | 8765 直连；前端反代 `http://localhost/bodhi/*`（nginx **60s 超时会掐长任务** → 长任务走 8765） |
| 本体知识库 | `08810cbd-af86-48d1-bd25-3b2c338e3d68`（`ONTOLOGY_KB_ID` 可覆盖） |
| Neo4j / PG | 容器 `WeKnora-neo4j`（HTTP 7474、bolt 7687、neo4j/password）；PG 走 `docker exec psql`（`ke_db.py`） |
| 上传目录 | `ontology/uploads/`（`.gitignore`：`*` + `!.gitignore` + `!*.md`；被拒件改名 `*.rejected`） |
| **上传 TTL 必须在仓库内** | `ModuleSpec.rel_files()` 需相对仓库根；放 `/tmp` → `ValueError: … is not in the subpath of …` |
| 终端坑 | PowerShell 里**别用 `$`、别嵌套 `"`** → 把脚本写文件再 `tr -d '\r' < x.sh > /tmp/x.sh; setsid bash /tmp/x.sh &`；刚重启服务立刻 curl 可能空响应（等 6~8s）；WSL 命令用 `wsl -d Ubuntu -u root --` |

## 2. 架构与单一真源

```
ontology/*.ttl ──(tools/ontology-compiler：唯一 TTL 解析器/发射器)──► artifacts/**（可选导出，不是加载必需）
      │
      └──(tools/ke-core/ke_admin.import_ttl / load_model)──► Neo4j 本体投影 ──► ke_ontology / server.py 接口
                                                        └──► ontology_wiki.py ──► PG wiki_pages
```
- **`src/` 已删除**（12 个跟踪文件已 `git rm`）→ rdflib 解析只剩编译器一套。
- `tools/ke-core/`：`ke_db` / `ke_neo4j`（零依赖 HTTP 客户端）/ `ke_ontology`（类清单、按类筛关系类型、range 闭包、目录路径）/ `ke_pages`（页面写库纪律）/ `ke_admin`（本体维护编排）。
- `artifacts/` 全部未跟踪、可重建；**加载链路不写它**。
- 外部契约产物（`extract_config.*.json` / `prompts/*` / `ontologyTypes.ts`）只在"给上游/前端/agent 用"时按需导出，后续由"运维智能体 + MCP 工具"按需同步，**不在加载链路里**。`ontologyTypes.ts` 是**构建期**产物（改后端不会自动生效，但只需重建镜像、不涉及发布；只影响配色/分组标签）。

## 3. 已完成并实测（后端）

| 能力 | 实测证据 |
|---|---|
| `POST /bodhi/ontology/load` | compile=true → `compile.ok=true`；`apply 603 条 / 47 类`；wiki 47/90/70/6 |
| `purge` / `apply` / `wiki` | 幂等；`apply` = 从 artifacts 回放，**可恢复被 cascade 删掉的模块** |
| **级联删除** `dependent_modules` / `cascade_purge`（CLI `deps`/`cascade`） | `deps bmm → [ea-ownership, ea-service, bmm-fd, ea]`；`deps ea → [ea-ownership, ea-service, bmm-fd]`（**bmm 不在其中**）。判定 = Neo4j 里 `BODHI_SUBCLASS_OF/DOMAIN/RANGE/INVERSE_OF` 指向该命名空间的边 + 传递展开，最下游在前 |
| `cascade bmm` 后 | 库里与 wiki 只剩 `index`（硬保护）；`load {compile:false}`（model_id 留空）= 一条命令回放恢复 47/90 |
| **上传即加载** `import_ttl(path, module, project_wiki, kb_id, allow_dangling)` | 模块名/IRI/命名空间/前缀**从 TTL 自身**读出；级联删下游；复用 `loader.load_ontology` + `Neo4jEmitter`（输出到 `tempfile.mkdtemp()`）；**只执行 19/611 条**（跳过 592 条非本模块语句 + 6 条 `xsd:*`/`owl:Thing` 占位）；`artifacts/` 写入 0；其它模块零变化（翻转 external = 0）、孤立节点不增（10→10） |
| **依赖前置检查** | 从 TTL 文本判定 `subClassOf/domain/range` 的 prefixed 引用是否"已定义"；未就绪 → **拒绝 + 自动回滚** + `missing_defs`。实测 `zzz:Missing` → HTTP 400 + "依赖未就绪：引用了未定义的类 zzz:Missing —— 已自动回滚本次导入（删除 N 个节点）…" |
| **加载后提示** | `cross_refs:[{module,iri,name,kind,defined}]` / `missing_defs` / `placeholders_skipped` / `hint` |
| **上传模块在接口可用** | `ke_ontology.class_meta()` 补录 → `relation-types?page_type=probe:ProbeThing` = `source=neo4j / 5 条`（4 条 `inherited_from=ea:Activity` + 1 条自有）；`classes=48` |
| **上传模块有 wiki 页** | `ontology_wiki.load_index()` 补录 → `Class 48 / Module 7 / Property 91 / Relation 71`；生成 4 页 `ontology/probe`(Module) / `probething`(Class) / `prop/probelinks`(Property) / `rel/probelinks`(Relation)；`cascade probe` 精确删这 4 页 |
| 可撤销 | `cascade probe` → 48→47、91→90、wiki −4，回基线 |

## 4. 接口契约

```jsonc
POST /bodhi/ontology/upload     // JSON（不用 multipart）：前端 FileReader.readAsText 后发文本
  { "filename":"ea-ownership.ttl", "content":"<TTL 文本>", "module_id":"ea-ownership", "project_wiki":false, "kb_id":"" }
→ 200 { module, ttl, ontology_iri, namespace, prefix,
        purge:{model,kb_id,neo4j_nodes_deleted,wiki_pages_deleted,pages},
        cypher_statements, statements_skipped, placeholders_skipped:[iri…],
        classes, module_classes, module_properties,
        cross_refs:[{module,iri,name,kind,defined}], missing_defs:[…], hint }
→ 400 { "error": "依赖未就绪：…" }      // 校验类错误，前端直接显示

GET  /bodhi/ontology/deps?model_id=<key>
→ 200 { model_id, dependent_modules:[…], purge_order:[…,model_id] }

POST /bodhi/ontology/load  {model_id?, compile?, purge?, project_wiki?, kb_id?}   // model_id 留空 = 只 apply + wiki
POST /bodhi/ontology/purge {model_id, kb_id?}
POST /bodhi/ontology/apply {}
POST /bodhi/ontology/wiki  {kb_id?}
```
CLI（服务不可用时等价，必须 venv python）：
```bash
/opt/bodhi-venv/bin/python3 tools/ke-core/ke_admin.py import <ttl> [模块名] | deps <模块名> | cascade <模块名> \
  | purge <模块名> | apply | wiki | compile | load <模块名>
```

## 5. 关键口径（改代码前必读）

1. **真源 = TTL；加载链路只认 Neo4j，json 只兜底/展示。** 凡"只认 json"处都要加 **Neo4j 补录**（已做：`ke_ontology.class_meta()`、`ontology_wiki.load_index()`）。补录字段**必须与产物同构**：类 `name/iri/label/definition/parents/is_enum/restriction_count`；关系 `name/iri/label/definition/domain/range/domain_display/range_display/inverse_of/functional`。
2. `ontology_wiki.py` **模块级没有 `import ke_neo4j`** → 补录函数内按需 import（加 `ke-core` 到 `sys.path`）；失败会 `print` 警告，**别改回静默**。
3. **导入只碰自己**：只执行"提到本模块命名空间/key"的语句；**不造外部占位**（`external=true` 且 IRI 非本模块的语句跳过）。
4. **`MERGE … ON CREATE SET` 只增不改**：属性写进已有节点后**再也改不动**（`apply` 回放也救不回）。踩过：导入 probe 时 `bmm:Offering`/`ea:Activity` 被写成 `external=true` 且**粘住** → 类数 47→45。修法：口径 3 不写它们；真要改属性 → **purge 后重建**（`load {model_id}`）。
5. **依赖顺序** bmm → ea → ea-service/ea-ownership/bmm-fd，由 `missing_defs` 硬校验保证（不通过拒绝+回滚），不靠人记。
6. **级联删除 = B 案**：删下游、**不重建下游**（依赖者需重新上传）；顺序由 `dependent_modules()` 给出（最下游在前）。
7. **零产物**：加载/上传链路不写 `artifacts/`；emitter 只当"翻译器"，输出落临时目录、用完即删。
8. 前缀永远**由 TTL 的 `@prefix` 决定**（`build_prefix_map` TTL 优先、config 兜底）；历史反例：`ea:Activity` 被写成 `bmm-EA-ext:Activity`。

## 6. 剩余任务

**6.1 前端「上传本体文件」按钮 —— ✅ 已完成并上线（2026-09-20 晚）**

位置：本体模型知识库页的面包屑（「上传自动生成 wiki」开关右侧；`isOntologyKb` 限定只在本体模型库渲染）。
流程（= 实现，与后端契约一一对应）：
```
[上传本体文件] → <input type=file accept=".ttl,.turtle"> → FileReader.readAsText
 → 推断模块名（可改）：与 owl:Ontology IRI **同命名空间的前缀**，否则 IRI 末段
 → 点「上传并加载」：先 GET /bodhi/ontology/deps?model_id=<名字>
     → 有下游才弹确认框：级联删除名单 + 顺序（purge_order）「…确认替换？」
 → POST /bodhi/ontology/upload {filename, content, module_id, project_wiki}
     → 200：报告面板 module_classes / module_properties / cypher_statements /
            statements_skipped / placeholders_skipped / cross_refs(defined=false 警示色) / hint
     → 400：对话内原文显示（如「依赖未就绪…已自动回滚」），可改模块名后重试
```

实现（照既有约定：**自包含组件** + 补丁只插「一行标签 + 一个回调」）：
- 新组件 `deploy/weknora-fork/frontend/BodhiOntologyUpload.vue`（Vue3 `<script setup lang="ts">`，自带
  `api()/post()/qs()` + tdesign；**不下发 `kb_id`** → 让服务端用它自己的 `ONTOLOGY_KB_ID`，比构建期常量权威）。
- 模块名预填口径（比本节原文更准）：**与本体 IRI 同命名空间的那个前缀**（`probe.ttl` → `probe`；
  `BMM完整版.ttl` / `ea-ownership-ext.ttl` 只有默认 `:` → 走 IRI 末段 → `bmm` / `ea-ownership`）；
  改过模块名时表单里红字提示（**改模块名不改 TTL 的命名空间**，库里该命名空间的节点会归到新模块名下）。
- 「同步 wiki」勾选框**默认开**（= `project_wiki:true`；关掉只更新图谱与接口）——「四处同步」必须有它。
- 补丁：`patch_frontend.py` 新增 `patch_knowledgebase_v6()`（`main()` 里的「5) v6 批次」，排在 v5 开关之后；
  注意这里的 v6 是 **KnowledgeBase.vue 的第 6 版补丁**，与 `docs/session-handoff.md` §5 里 2026-09-19 的
  WikiBrowser v6「编辑页类型下拉」不是一回事。它做四件事：拷贝组件 + import +
  `const BODHI_ONTOLOGY_KB_ID = '<uuid>'`（**构建期**常量，env `ONTOLOGY_KB_ID` 与 `ke_admin.ONTOLOGY_KB`
  同源）+ `isOntologyKb` + 回调 `onOntologyUploaded()`（刷新一次 wiki 状态）+ 面包屑里一行标签。
- 重建/上线（**CLEAN_SRC 必给**，见 `build_frontend.sh` 的幂等约定）：
  `CLEAN_SRC=/root/wk080/frontend/src bash deploy/weknora-fork/build_frontend.sh /root/fe-build`
  → `bash deploy/weknora-fork/deploy_frontend.sh deploy`（镜像内自检应见 `bodhi-onto-upload`）。
- ⚠️ 2026-09-21 实测：`/root/wk080/frontend/src`（干净源码副本）**已被清掉**，带 `CLEAN_SRC` 的整链会
  在 `cp` 处直接失败（`cannot stat`）。此时若**只改本体类型表**（`gen_frontend_types.py` 产出的
  `frontend/ontologyTypes.ts`，例如本体从 `bmm-ea-ext` 迁到 `ea`），不必动 Vue 补丁，走最小路径：
  ```bash
  cp deploy/weknora-fork/frontend/ontologyTypes.ts /root/fe-build/src/utils/ontologyTypes.ts
  cd /root/fe-build && NODE_OPTIONS=--max-old-space-size=4096 npm run build   # ~1m50s
  docker build -f Dockerfile -t weknora-ui:bodhi2 .
  bash deploy/weknora-fork/deploy_frontend.sh deploy                            # 切换 + 验收
  # 验收：docker exec WeKnora-frontend grep -o 'ea:MCPService' /usr/share/nginx/html/assets/*.js | head -1
  ```
  （`/root/fe-build` 的 `node_modules` 已在，无需 `npm install`；`ontologyTypes.ts` 会被 `patch_frontend.py`
  拷到 `src/utils/`，所以直接覆盖同名文件即可。）

**6.1bis 验收实测（2026-09-20 晚，经 `http://localhost/bodhi/*` 反代，与按钮完全同形）**

| 步骤 | 结果 |
|---|---|
| 上传前基线 | 类 47；wiki `Class 47 / Module 6 / Property 90 / Relation 70`；model-graph 47 节点 96 边 悬空 0 |
| `GET /bodhi/ontology/deps?model_id=probe` | `{"dependent_modules":[],"purge_order":["probe"]}` → 不弹级联确认（对） |
| `POST /bodhi/ontology/upload`（probe.ttl，project_wiki=true） | `HTTP 200`；`prefix=probe`、`namespace=…/probe#`、`module_classes=1 / module_properties=1`、`cypher_statements=19 / statements_skipped=592`、占位 6 条、`cross_refs=[bmm:Offering(RANGE,defined), ea:Activity(SUBCLASS_OF,defined)]`、`wiki.project.ok=true` |
| ① 类清单 | `/bodhi/ontology/classes` → **48** |
| ② 关系类型（含继承） | `relation-types?page_type=probe:ProbeThing` → `source=neo4j`，**5 条**（4 条 `inherited_from=ea:Activity` + 1 条自有 `probe:probeLinks`） |
| ③ 图谱 | `/bodhi/model-graph?model=` → **48 节点 / 98 边 / 悬空 0** |
| ④ wiki 页 | `Class 48 / Module 7 / Property 91 / Relation 71` + 4 页 `ontology/probe{,/probething,/prop/probelinks,/rel/probelinks}` |
| 400 闸门 | 独立命名空间的坏 TTL（`badtest:BadThing subClassOf ea:MissingAct`）→ `HTTP 400 依赖未就绪…已自动回滚本次导入（删除 1 个节点）`；回滚后类数仍 48（不伤已导入模块） |
| 撤销 | `cascade probe` → 删 2 个图节点 + 4 个 wiki 页 → **精确回基线**（47 / 90 / 70 / 6；model-graph 47 节点 96 边） |

> 本轮新发现的坑（已加前端提示）：**上传时改模块名 ≠ 改命名空间** —— 命名空间永远由 TTL 的
> `@prefix` / owl:Ontology IRI 决定，模块名只是标签；拿同一个 TTL 换个模块名上传，`MERGE` 会把库里
> 该命名空间的节点属性 `module` 改成新名字（原模块名被「掏空」）。所以改名只在确实要改时做。


**6.2 文档与 git 收尾** —— `docs/session-handoff.md` §3.4bis 补 upload/deps 契约与三条纪律；`docs/ontology-service-neo4j.md` 加头注（"本文是 2026-09-19 迁移记录；`src/` 已于 2026-09-20 删除，现役见 `tools/ke-core/`"）；`docs/weknora-fork.md` §3 加同注；`git add docs/handoff-ontology-upload.md ontology/uploads/.gitignore tools/ke-core tools/ontology-mcp/server.py tools/ontology-extract/ontology_wiki.py`。

**6.3 可选加固（非阻塞）** —— emitter 外部占位语句改 `MERGE … ON CREATE SET`（只对"模块集与库不一致的 apply"有意义）；编译器加 `--emit` 白名单（仅导出外部契约时产 `artifacts/**`）。

**6.4 用户实测暴露的 3 个后端缺口 —— ✅ 已修（2026-09-20 第二轮）**

用户操作：上传 ea →「清理成功，但 ea 的 2 个子模型好像也加载了」；再上传 bmm →「清理干净，但 bmm 扩展也被加载了」。

| # | 症状 | 根因 | 修法 |
|---|---|---|---|
| ① | 上传 ea 后 `ea-service`/`ea-ownership` 的**图节点**回来了 | `import_ttl` 的语句归属判据是 `any(tok in stmt for tok in keep)`，`keep` 里有**裸模块 key** → 子串匹配：`'ea'` 命中 332 条（含两个子模块的全部语句 + 所有带 "ea" 的英文串）、`'bmm'` 命中 320 条（含 bmm-fd 的 33 条）。级联刚删掉，导入又插回去 | `tools/ke-core/ke_admin.py·import_ttl`：改成**结构判据** = 语句**首个 `iri:`** 在本模块命名空间内（或 = 本模块 ontology IRI）；没有 `iri:` 的诊断语句才退化为 `key: '<本模块 key>'` 精确匹配（新增 `_STMT_IRI_RE`）。实测命中 bmm 261 / ea 109 / ea-service 101 / ea-ownership 78 / bmm-fd 33（旧：320 / 332 / 103 / 78 / 33）；5 个模块并集覆盖 582/592 条，漏掉的 10 条正是 `xsd:*`/`owl:Thing` 外部占位（本就该跳过） |
| ② | 修了①后，子模块的**空模块节点**还在（wiki 多出"只有模块页、没有类与关系"的 1 页） | 依赖模块的模块节点语句里写着 `key: 'bmm-fd'` / `affects: 'bmm'`，按 key 匹配仍命中 | 同①的结构判据；wiki 侧再加护栏：**"现存模块"只认有内容的模块**（≥1 类或 ≥1 属性），不看 `BodhiModule` 节点（`ontology_wiki._neo4j_live_sets`） |
| ③ | 级联删除后 `ea-service`/`ea-ownership`/`bmm-fd` 的 **wiki 页**又出现 | `ontology_wiki.load_index()` 是「json 全量 + Neo4j **只加不减**」；json 是构建期快照、永远列着 5 个已登记模块，cascade 不会改它 | `tools/ontology-extract/ontology_wiki.py`：`load_index()` 先按 Neo4j 实况**做减法**（新增 `_prune_against_neo4j`：不在 Neo4j 的模块/类/关系全剔、重算 totals），再补录 Neo4j 独有的（上传模块）。剔除会打印 `[wiki] 按 Neo4j 实况剔除 json 过期内容：…`；Neo4j 不可用则回退"json 原样"（不静默清空） |

**同一条链路实测（经 `http://localhost/bodhi/*` 反代 = 按钮同形）**
```
上传 ea  → 200；purge.deleted = ea-ownership(18 节点/28 页) + ea-service(23/36) + bmm-fd(7/11) + ea(24/39)
          图库 37 类（bmm 26 + ea 11）；wiki 只剩 bmm=100 / ea=39 / index=2   ← 子模型**没有**回来
上传 bmm → 200；cypher 272 条（旧 320）；purge.deleted = ea(24/39) + bmm(72/100)
          图库 26 类（只有 bmm）；wiki 只剩 bmm=100 / index=2                ← bmm-fd **没有**回来
cascade bmm            → 图库清空、wiki 只剩总览
load {compile:false}   → apply 603 条 / 47 类；wiki 216 页
                         （Class 47·Property 90·Relation 70·Module 6·LightDoc 2·index 1）
                         图库 47 类、model-graph 47 节点 96 边 悬空 0      ← 精确回基线
```

**口径补充（§5 之外，改代码前必读）**
- **语句归属 = 首个 `iri:`**：emitter 一贯先 MERGE/MATCH 主体再连边，所以"首个 iri"就是这条语句的属主。
  跨模块**出边**（本模块属性 → 别模块类）主体在本模块 → 会执行（probe 的 `cross_refs` 正是这么来的）；
  别模块指向本模块的边主体在别模块 → 跳过（那个模块在库里时这些边早就有了）。
- **wiki 严格、接口兜底**：wiki 不做 json 兜底（否则复活已删模块）；`/bodhi/ontology/classes` 仍是
  "Neo4j 优先 + json 兜底"（Neo4j 全空时回 `source: "json: …"`）。两者在"图库被清空但 artifacts 还在"时
  可能不一致，这是**有意**的取舍。
- **`deps` 只能从 Neo4j 现有边推断**：某模块当前不在图库里（例如上一次导入失败），这次上传就算不出
  "它是下游"，也就不会删它。要彻底干净：先 `load {compile:false}` 把 artifacts 全量回放，再上传要替换的模块。

## 7. 复现/验证命令

```bash
B=wsl -d Ubuntu -u root -- bash -lc 'cd /mnt/c/Users/PHJY/source/bodhi2 && '
$B /opt/bodhi-venv/bin/python3 tools/ke-core/ke_admin.py deps bmm        # 会级联删谁
$B /opt/bodhi-venv/bin/python3 tools/ke-core/ke_admin.py import ontology/uploads/probe.ttl probe
curl -s 'http://127.0.0.1:8765/bodhi/ontology/classes' | grep -o prefixed | wc -l          # 48
curl -s 'http://127.0.0.1:8765/bodhi/ontology/relation-types?page_type=probe:ProbeThing'    # source=neo4j, 5 条
$B /opt/bodhi-venv/bin/python3 tools/ke-core/ke_admin.py cascade probe   # 撤销
curl -s -X POST -H 'Content-Type: application/json' -d '{"compile":false}' http://127.0.0.1:8765/bodhi/ontology/load
```

前端（「上传本体文件」按钮）——重建/上线与同形验证：
```bash
# 1) 重建 + 上线（CLEAN_SRC 必给：补丁脚本拒绝二次打补丁，见 §6.1）
CLEAN_SRC=/root/wk080/frontend/src bash deploy/weknora-fork/build_frontend.sh /root/fe-build
bash deploy/weknora-fork/deploy_frontend.sh deploy
docker exec WeKnora-frontend sh -c \
  "grep -o 'bodhi-onto-upload' /usr/share/nginx/html/assets/*.js | head -2"   # 新组件在 bundle 里

# 2) 按钮实际走的两条接口（经 nginx 反代，与组件同形）
curl -s 'http://localhost/bodhi/ontology/deps?model_id=probe'
/opt/bodhi-venv/bin/python3 -c "
import json, urllib.request
ttl = open('ontology/uploads/probe.ttl', encoding='utf-8').read()
payload = json.dumps({'filename': 'probe.ttl', 'content': ttl,
                      'module_id': 'probe', 'project_wiki': True}).encode()
req = urllib.request.Request('http://localhost/bodhi/ontology/upload', data=payload,
                             headers={'Content-Type': 'application/json'})
rep = json.load(urllib.request.urlopen(req, timeout=900))
print(rep['module_classes'], rep['classes'], rep['hint'])
"
# 3) 四处同步 / 回基线（期望 48 → cascade 后 47）
/opt/bodhi-venv/bin/python3 tools/ke-core/ke_admin.py cascade probe
```

## 8. 排障速查

| 症状 | 处置 |
|---|---|
| `relation-types` 回 `unknown-class` | 改过 `ke_ontology.py` **没重启服务**；或补录失败（日志 `[wiki] Neo4j 补录失败`） |
| 类数莫名少几个 | `external` 被翻转粘住 → `MATCH (c:BodhiOntClass {iri:'…'}) REMOVE c.external`，或 `load {model_id}` 重建 |
| `… is not in the subpath of …` | TTL 必须在仓库内（放 `ontology/uploads/`） |
| `依赖未就绪…` | 先导入上游模块；确实放行用 `allow_dangling=True` |
| 上传后**已删的子模块/扩展又出现** | §6.4 的三个缺口（已修）。改过 `ke_admin.py`/`ontology_wiki.py` **必须重启 bodhi-mcp**；正常应看到 `[wiki] 按 Neo4j 实况剔除 json 过期内容…`。若还残留旧数据：先 `load {compile:false}` 全量回放再上传 |
| wiki 多出"只有模块页、没有类"的空模块 | 旧泄漏留下的空 `BodhiModule` 节点 → `MATCH (m:BodhiModule {key:'x'}) DETACH DELETE m`（新代码不会再造，也不会给它出页） |
| 长任务 504 | nginx 60s 超时 → 直连 8765 |
| 服务空响应 | 重启后等 6~8s；`systemctl is-active bodhi-mcp`；`journalctl -u bodhi-mcp -f` |

## 9. 本轮（2026-09-20）改动文件

```
tools/ke-core/ke_admin.py     + inspect_ttl/_cypher_statements/import_ttl/upload_ttl/_registered_modules
                              + module_namespace/dependent_modules/cascade_purge/_invalidate_ontology_cache
                              + 输入闸门(空/2MB)、失败件改名 .rejected、CLI import|deps|cascade
                              + 语句归属判据（首个 iri，§6.4①②；替换掉被 key 子串污染的旧判据）
tools/ke-core/ke_ontology.py  + invalidate_cache/index_stamp + class_meta() 的 Neo4j 补录
tools/ontology-extract/ontology_wiki.py  + _neo4j_live_sets()/_prune_against_neo4j()（先减后加，§6.4②③）
                              + _supplement_from_neo4j()；load_index() = 按 Neo4j 实况剔除 json 过期项 + 补录
tools/ontology-mcp/server.py  + /bodhi/ontology/{load,purge,apply,wiki,upload} + GET /bodhi/ontology/deps
ontology/uploads/.gitignore   上传目录不进版本库
deploy/weknora-fork/frontend/BodhiOntologyUpload.vue  【新】「上传本体文件」按钮（自包含组件）
deploy/weknora-fork/frontend/patch_frontend.py        + patch_knowledgebase_v6 / BODHI_ONTOLOGY_KB / 拷贝组件
docs/session-handoff.md       §3.4bis 语义/依赖判定/操作与恢复配方
docs/handoff-ontology-upload.md  本文
（另：archive/{backfill_paths,relink_pages}.py、weknora_sync.py、{cleanup_auto_wiki.sh,fix_agent_mcp.py} 归档；删除 src/；均 git mv 保留历史）
```

## 10. 话题收尾（2026-09-20 第三轮 · 已提交）

- **状态**：本体上传即加载**完结**（后端 + 前端 + 文档随同一个提交）；详见 `git log` 的
  `feat(ontology): 本体上传即加载 …`。
- **前端已冻结**：`BodhiOntologyUpload.vue` 与 `patch_frontend.py`（`patch_knowledgebase_v6`）不再改动；
  镜像 `weknora-ui:bodhi2` 已上线，自检
  `docker exec WeKnora-frontend sh -c "grep -o bodhi-onto-upload /usr/share/nginx/html/assets/*.js"`。
  以后**只有再动 Vue** 时才需要重建：
  `CLEAN_SRC=/root/wk080/frontend/src bash deploy/weknora-fork/build_frontend.sh /root/fe-build`
  → `bash deploy/weknora-fork/deploy_frontend.sh deploy`（补丁脚本拒绝二次打补丁 → **CLEAN_SRC 必给**）。
- **清理**：`ontology/uploads/` 只留 `probe.ttl`（测试靶子）+ `.gitignore`；WSL `/tmp` 里本轮临时脚本与日志、
  一次性 systemd 单元（`bodhi-fe-*` / `bodhi-verify*` / `bodhi-fixtest*` / `bodhi-all`）已清。
  `/root/fe-build`（1.6G 构建树 + node_modules）**保留**（重建要用）；要腾空间：`rm -rf /root/fe-build`
  （下次重建会重新 npm install，约 5~10 分钟）。
- **下一个话题**：**函数依赖 + 幂等规则验证**（具体范围下一轮现场确认）。现成落脚点：
  - 依赖判定唯一实现：`tools/ke-core/ke_admin.py` 的 `dependent_modules()` / `cascade_purge()`（口径见 §4、§6.4）；
  - 幂等回放基线：`POST /bodhi/ontology/load {"compile":false}`（apply 603 条 → 47 类 → wiki 216 页）；
  - 既有材料：`docs/session-handoff.md` §3.4bis（依赖判定与校验）、`docs/bodhi-reasoning.md`（规则规格）。

