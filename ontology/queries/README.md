# ontology/queries —— 可复用的 Cypher 查询（人工编写，编译器替换占位符）

## 用途
本目录存放**人工编写**的 Cypher 查询模板，由 `tools/ontology-compiler` 复制到
`artifacts/neo4j/` 下（默认文件 `20_cross_layer_queries.cypher`），并在复制时替换占位符。

- 这些查询作用于**本体投影**（`BodhiOntClass` / `BodhiOntProperty` / `BodhiEnumValue` / `BodhiRestriction`），
  用来回答「本体允许/要求什么」，是跨层推理的约束来源。
- **实例层**（WeKnora 抽取出的知识点、Chunk 溯源）不在这里硬编码：其 schema 由 WeKnora 管理，
  由 `ke-core` 经 WeKnora 检索接口读取后在 Python 侧统一处理，避免与上游版本耦合。

## 占位符
| 占位符 | 替换值（含引号，直接拼接） |
| --- | --- |
| `{{BMM_NS}}` | `'http://example.org/bmm#'` |
| `{{EA_NS}}` | `'http://example.org/bmm-EA-ext#'` |
| `{{EXT_EA_SERVICE_NS}}` | `'http://example.org/bodhi/ext/ea-service#'` |
| `{{EXT_EA_OWNERSHIP_NS}}` | `'http://example.org/bodhi/ext/ea-ownership#'` |
| `{{EXT_BMM_FD_NS}}` | `'http://example.org/bodhi/ext/bmm-fd#'` |
| `{{GENERATED_AT}}` | 生成时间（UTC ISO8601，不带引号） |

新增查询文件时，命名建议 `<用途>.<层>.cypher`（例：`poc.cross_layer.cypher`），
编译器会按文件名字典序输出，生成物头部会自动补上「自动生成，请勿手改」的说明。
