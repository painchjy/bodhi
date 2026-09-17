# ontology-compiler · BODHI2 本体编译器

把 `ontology/*.ttl`（唯一真源）编译成下游可直接消费的产物，落在 `artifacts/`：

| 产物目录 | 消费方 | 内容 |
| --- | --- | --- |
| `weknora/` | WeKnora | `extract_config.{module}.json`（上游 6 键：`enabled` / `text` / `tags` / `nodes` / `relations` / `custom_instructions`）、`provenance.json`（逐字段依据 + 回溯到 TTL 的 file/line/iri） |
| `shacl/` | Neo4j / 校验服务 | `generated.shapes.ttl`（由本体生成）、`authored/*.shapes.ttl`（人工编写，原样收录）、`index.json`（generated / authored 分区） |
| `neo4j/` | Neo4j | `00_constraints.cypher`、`10_ontology.cypher`、`20_cross_layer_queries.cypher`、README |
| `json_schema/` | 抽取管道 / LLM 结构化输出 | `extraction_result.{module}.schema.json`、聚合 `extraction_result.schema.json`、`knowledge_point.schema.json`、`index.json` |
| `prompts/` | LLM 抽取 | `{module}_extraction.md`（自包含，含术语取值域与 SHACL 关键约束） |
| `mapping/` | 文本 → IRI 对齐 | `label_map.json`（`terms` / `alias_index` / `conflicts`） |
| （根） | CI / 对比 | `manifest.json`：`artifact_schema_version`、`compiler_version`、`generated_at`、`module_keys`、`modules`、`stats`、`artifacts[]`（path / sha256 / bytes / emitter） |

当前规模：5 个模块 / 47 类 / 70 对象属性 / 20 数据属性 / 26 限制 / 2 枚举（7 取值）→ 32 个产物文件（weknora 已下线 5 个与上游不兼容的 `.flat.json`）。

## 真源与可重建物

* **唯一真源**：`ontology/*.ttl`。`artifacts/` 整目录是**可重建物**，已列入仓库根 `.gitignore`，不入库。
* 因此 `manifest.json` 也只在**工作区**存在；`--diff` 比较的是「本地产物相对上次本地编译」的差异，换台机器首跑必然是「首次编译」。
* 部署 / CI 的正确姿势：拉取仓库 → 跑一次 `compile` 生成产物 → 再把 `artifacts/` 挂给 WeKnora / Neo4j。

## 快速开始

在仓库根目录执行：

```powershell
# 模块清单（不解析 TTL）；--stats 才解析并给出术语计数与跨模块引用
python tools/ontology-compiler/compile.py list --stats

# 只体检，绝不写产物
python tools/ontology-compiler/compile.py validate --summary

# 体检 + 写产物
python tools/ontology-compiler/compile.py compile

# 版本
python tools/ontology-compiler/compile.py --version
```

任何子命令都支持 `--json` 输出机器可读结果（CI 用）：

* `list --json`：`{"compiler_version", "modules": [...]}`，每项含 `key/prefix/kind/label/short_label/namespace/ontology_iri/affects/files/files_present/lexicon/lexicon_present`，`--stats` 时追加 `stats`（`classes/object_properties/data_properties/enums/external_refs/referenced_modules`）；
* `validate --json`：`{"compiler_version", "ok", "generated_at", "stats", "counts", "problems": [...]}`；
* `compile --json`：上面的基础上再加 `wrote_artifacts`、`module_keys`、`out_dir`、`artifacts`、`removed`、`diff`。

## 退出码契约

| 码 | 含义 | 说明 |
| --- | --- | --- |
| `0` | 成功 | 体检通过（`validate`）/ 产物已写完（`compile`） |
| `1` | 校验未通过 | 存在 ERROR，或 `--fail-on-warn` 且存在 WARN。`compile` 在此码下**默认不写任何产物** |
| `2` | 参数错误 | argparse 用法错误（未知子命令 / 未知 flag） |
| `3` | 输入或环境错误 | 未知模块 key、本体文件缺失、TTL 语法错、缺 `PyYAML`、输出目录不可写 |

CI 里推荐：`validate --fail-on-warn` 卡质量门，`compile --diff` 卡产物漂移。

## 可复现（逐字节）

产物里带 `generated_at`，所以时间戳来源必须可控。优先级：

1. 命令行 `--generated-at 2026-01-01T00:00:00Z`（最高）
2. 环境变量 `BODHI_GENERATED_AT`
3. 当前 UTC 时间（默认，写真实时间，便于追溯）

**契约**：同一份 `ontology/*.ttl` + 同一个时间戳 ⇒ 产物**逐字节相同**（sha256 一致）。
验证方式：固定时间戳连跑两次，第二次 `--diff` 必须全部 `unchanged`（`tests/test_compiler.py::test_second_compile_is_byte_identical` 与 `CliTests::test_compile_json_then_diff_is_stable` 都在守这条）。

不固定时间戳时，每次编译都会让**恰好这 13 个**内嵌时间戳的产物变化（`json_schema/` 下 6 个 `extraction_result.*.schema.json` + `index.json` + `knowledge_point.schema.json`、`mapping/label_map.json`、`neo4j/README.md`、`neo4j/20_cross_layer_queries.cypher`、`shacl/index.json`、`weknora/provenance.json`），这是**预期行为**而非不稳定。CI 想卡漂移就固定 `BODHI_GENERATED_AT`：

```powershell
$env:BODHI_GENERATED_AT = '2026-01-01T00:00:00Z'
python tools/ontology-compiler/compile.py compile --diff --summary
```

## WeKnora 抽取契约（2026-09-16 对齐上游源码）

`artifacts/weknora/extract_config.{module}.json` 的字段名**逐一取自 WeKnora 源码**，不是自拟：

| 键 | 本产物填什么 | 上游依据 / 消费点 |
| --- | --- | --- |
| `enabled` | 恒 `true` | `KnowledgeBase.extract_config`（JSON 列）；`IsGraphEnabled()` 要求它与 `indexing_strategy.graph_enabled` 同时为真 |
| `text` | 术语白名单 + 表述线索 | few-shot 的 Q 行（`extract.go` 塞进 `GraphData.Text`） |
| `tags` | 本模块全部对象属性名 | 基础模板里的 `%s`（原文 “Allowed relationship types are: %s.”）；`RemoveUnknownRelation` 按它过滤关系类型 |
| `nodes` | `[{name, attributes[]}]` | `GraphNode{name, chunks, attributes}`；`attributes` 渲染成示例答案的 `entity_attributes` |
| `relations` | `[{node1, node2, type}]` | `GraphRelation{node1, node2, type}`；值来自对象属性的 domain / range + 属性名 |
| `custom_instructions` | 硬约束（domain/range 匹配、枚举白名单、evidence、unmatched 出口） | 追加到上游系统提示词（`AppendCustomPromptInstructions`） |

三条硬性结论：

1. **键名写错 = 静默失效**：`extract_config` 以 JSON 列存储、用 `json.Unmarshal` 读回结构体，
   未知键不报错、直接消失。所以只写上游认得的这 6 个键，`tests/test_compiler.py` 有用例锁死键集合。
2. **`nodes[].chunks` 不写**：它由 WeKnora 运行时回填当前 chunk id；写了会把示例 chunk 混进真实图谱。
3. **不兼容形态已下线**：早期为「`nodes` 可能是字符串数组」而额外产出的 `.flat.json` 已删除——
   上游 `Nodes []*GraphNode` 收到 `["bmm:Resource"]` 会直接 unmarshal 失败，整份配置作废。

接入四个闸门（`artifacts/weknora/README.md` 内有同版说明，随产物一起生成）：

1. 服务侧：`.env` 里 `NEO4J_ENABLE=true`，并 `docker compose --profile neo4j up -d`；
2. 知识库开关：`indexing_strategy.graph_enabled=true`；
3. 本体注入：把 `extract_config.{module}.json` 内容整体写进该知识库的 `extract_config`（一个知识库一份配置）；
4. 验证：导入文档后在 Neo4j 里核对节点名与关系 `type` 是否命中配置。

闸门 1 的落库细节（起图库 / 把 `artifacts/neo4j/*.cypher` 灌进图库 / 与上游 compose
的叠加方式）见 `deploy/README.md`。

字段名的逐条证据（`源码文件:行号`）见 `artifacts/weknora/provenance.json` 的 `upstream.evidence`；
上游升级后按该清单重新核对，再动 `weknora_config.py`。


## 命令与选项

三子命令：`list` / `validate` / `compile`。

公共选项（`validate`、`compile` 都有）：

| 选项 | 作用 |
| --- | --- |
| `--module bmm,ea` | 只处理指定模块（逗号分隔，默认全部）。非法 key → 退出码 3 |
| `--summary` | 体检输出每行只留 `code + 术语`，不展开详情 |
| `--json` | 输出机器可读 JSON |
| `--generated-at TS` | 固定生成时间戳（优先级最高） |

`list`：`--stats`（解析 TTL，附带术语计数与跨模块引用数）。

`validate`：

| 选项 | 作用 |
| --- | --- |
| `--fail-on-warn` | WARN 也算失败（退出码 1） |
| `--allow-name-collisions` | 允许不同模块出现同名术语 |
| `--report FILE` | 把体检报告写入文件（Markdown/文本均可） |

`compile`：

| 选项 | 作用 |
| --- | --- |
| `--out DIR` | 产物输出目录（默认 `artifacts/`；测试用临时目录） |
| `--diff` | 列出与上次 manifest 的差异（`added` / `changed` / `removed` / `unchanged`） |
| `--show-unchanged` | 差异清单里包含 `unchanged`（隐含开启 `--diff`） |
| `--force` | 即使有 ERROR 也写产物（**仅排查用，禁止合并**） |
| `--fail-on-warn` | WARN 也算失败 |
| `--allow-name-collisions` | 允许不同模块出现同名术语 |
| `--keep-stale` | 不清理上次 manifest 记录、本次不再产出的文件（默认会清理） |

`--diff` 输出形态（`ontology_compiler/compiler.py::format_diff`）：

```text
产物对比（相对上一次 manifest）：
  changed   artifacts/mapping/label_map.json
  added     artifacts/weknora/extract_config.xx.json
  小计：added 1、changed 1、unchanged 30
```

注意**小计只列本次出现过的状态**：全量不变时只有 `unchanged 32`，不会出现 `changed 0`。上次 manifest 不存在时输出「无上一次 manifest，无法对比（首次编译）。」

## 模块清单

本体模块用**显式清单**登记在 `ontology_compiler/config.py::build_modules()`。

| key | 前缀 | 类型 | 本体文件 | 词表 |
| --- | --- | --- | --- | --- |
| `bmm` | `bmm` | base | `ontology/BMM完整版.ttl` | `ontology/lexicon/bmm.keywords.yaml` |
| `ea` | `ea` | base | `ontology/EA完整版.ttl` | `ontology/lexicon/ea.keywords.yaml` |
| `ea-service` | `easvc` | extension（补强 `ea`） | `ontology/extensions/ea-service-ext.ttl` | 无 |
| `ea-ownership` | `eaown` | extension（补强 `ea`） | `ontology/extensions/ea-ownership-ext.ttl` | 无 |
| `bmm-fd` | `bmmfd` | extension（补强 `bmm`、`ea`） | `ontology/extensions/bmm-fd-ext.ttl` | 无 |

`--module` 取值：`all` / `*` / 逗号分隔的模块 key（如 `bmm,ea`）。

## 体检码一览（`ontology_compiler/validate.py`）

| 码 | 级别 | 含义 |
| --- | --- | --- |
| `E1` | error | 引用未声明的术语（domain / range / subClassOf / onProperty / inverseOf / 限制值域） |
| `E2` | error | 悬空限制：`owl:onProperty` 指向的术语未声明 |
| `E3` | error | 跨模块同名冲突：同一 local name 在不同模块被声明（LLM 会出现一义多指） |
| `E4` | error | 继承环 |
| `E5` | error | 枚举类没有任何取值 |
| `E6` | error | 对象属性缺 domain 或 range（数据属性降级为 WARN） |
| `W1` | warn | 类或属性缺 `rdfs:label` |
| `W2` | warn | 类或属性缺 `rdfs:comment` |
| `W3` | warn | 枚举取值个数异常多（>12，疑似把示例数据当枚举） |
| `W4` | warn | 类没有父类（顶层悬空，需人工拍板；会出现在 Neo4j 元查询 Q5） |
| `W5` | warn | 功能属性与基数约束自相矛盾（`functional` 且 `minCardinality > 1`） |
| `W6` | warn | `owl:inverseOf` 未成对声明 |
| `I1` | info | 跨模块桥清单（层间接缝，必须显式管理） |
| `I2` | info | 模块文件与计数 |
| `I3` | info | 引用了外部词汇表术语（未在本体系声明，按外部标准处理） |

仅用 `--summary` 时每行只留 `code + 术语`。当前基线：error 0 / warn 33 / info 33。

## 源码结构

```text
tools/ontology-compiler/
├─ compile.py                  # CLI：list / validate / compile，退出码 0/1/2/3
├─ ontology_compiler/
│  ├─ config.py                # 路径、命名空间、模块显式清单
│  ├─ loader.py                # rdflib 载入 + 模块视图 + generated_at 解析
│  ├─ model.py                 # OntologyBundleView / Term / Property 等视图模型
│  ├─ validate.py               # E1-E6 / W1-W6 / I1-I3 体检
│  ├─ lexicon.py               # YAML 词表（别名、关键词）
│  ├─ compiler.py              # compile_all / manifest / diff / prune
│  └─ emitters/                # 6 个发射器，顺序即 EMITTERS：
│     weknora_config → shacl → neo4j → json_schema → label_map → prompts
└─ tests/test_compiler.py      # 32 例回归（纯 stdlib unittest）
```

## 测试

```powershell
# 推荐（仓库根目录）
python -m unittest discover -s tools/ontology-compiler/tests -t tools/ontology-compiler -v

# 或直接跑
python tools/ontology-compiler/tests/test_compiler.py
```

覆盖范围：本体载入（术语/枚举/domain-range/模块归属）、体检（无 ERROR、问题结构稳定、模块 key 合法、info 可跳过）、编译（每层产物齐备、模块筛选、裁剪陈旧产物、无占位符残留、固定时间戳逐字节复现）、产物内容（标签表、SHACL、JSON Schema、提示词自包含、WeKnora 六键契约 / `nodes[].attributes` / `relations[].node1,node2,type` / `tags`=关系类型白名单 / 无 flat 遗留、provenance 回溯与上游证据）、CLI 子进程退出码契约。

测试约定：涉及产物一律落 `tempfile.TemporaryDirectory()`，固定 `PINNED='2026-01-01T00:00:00Z'`，**不碰仓库 `artifacts/`**；断言「契约」而非全文快照（本体仍在演进）。

## 已知约束与坑

1. **rdflib 7.x：用 `str` 当谓词查询会静默返回空集**（`graph.subject_objects("rdf:type")` 得到 0 条，不报错）。所有查询必须用 `URIRef`——曾出现「类 0 / 属性 0」的假象，`tests/test_compiler.py::test_terms_are_really_loaded` 是这条的回归哨兵。
2. **禁止 glob 抓本体**：`ontology/old/BMM_EXTENDED_LIGHT.ttl` 是 Markdown 风格伪 TTL，通配会直接语法错。新增本体必须登记到 `config.py`。
3. **依赖**：编译器本体只依赖 `rdflib`；读 YAML 词表额外依赖 `PyYAML`（缺失时报错，不静默降级）。
4. **Windows 控制台编码**：`compile.py` 启动时会尝试把 stdout/stderr 重配为 UTF-8；管道里若看到中文乱码，是终端解码问题（设 `$env:PYTHONIOENCODING='utf-8'` + `[Console]::OutputEncoding=[Text.Encoding]::UTF8`），不是产物问题。
5. **产物中的 `{{...}}` 占位符**：发射阶段必须全部替换（Neo4j 里的 `{{BMM_NS}}` 之类）；仅 `README.md` 的取值示例表豁免，由 `test_no_leftover_placeholders` 全量扫描把守。

## 扩展方式

* **加本体模块**：在 `config.py::build_modules()` 增加 `ModuleSpec`（key/prefix/label/namespace/files/lexicon/kind/affects），把 TTL 放进 `ontology/`，然后 `compile --module <key>` 验证；`--diff` 会报出新增产物。
* **加产物层**：在 `ontology_compiler/emitters/` 新增实现 `Emitter.emit(bundle, out_dir)` 的类，注册进 `EMITTERS`（顺序即产物写入顺序），并在 `tests/test_compiler.py` 的产物内容测试组补契约断言。
* **改产物结构**：同步升 `config.py::ARTIFACT_SCHEMA_VERSION`，下游（WeKnora / Neo4j 装载脚本）按版本兼容。


