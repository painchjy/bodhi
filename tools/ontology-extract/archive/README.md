# tools/ontology-extract/archive —— 已退役的一次性脚本

归档时间：2026-09-20。**归档 ≠ 删除**：保留算法与调用示例，便于回溯"当年端到端是怎么跑的"。
回退方式：`git mv archive/weknora_sync.py ../weknora_sync.py`。

| 文件 | 当时干什么 | 为什么退役 | 现在的对应能力 |
|---|---|---|---|
| `weknora_sync.py`（33KB） | 端到端预演：真库片段 → LLM 抽取 → 校验 → 写 Neo4j/图 + 生成 wiki 页 SQL | 全仓已无 `import weknora_sync`（仅文档 §10.9 与一条历史输出 `logs/ontology_wiki_20260919_062303.sql` 提到）；能力已被下面两条链路取代 | ① 抽取入库：`tools/ontology-mcp/server.py`（`/bodhi/extract` 等，抽取引擎仍是同目录 `extract.py`）② 本体驱动 wiki：`ontology_wiki.py build/project`，由 `ke_admin.load()` 编排 |

需要它当年的 `pages_to_sql()` 输出形态时，看 `logs/ontology_wiki_*.sql` 即可（同格式历史产物）。
