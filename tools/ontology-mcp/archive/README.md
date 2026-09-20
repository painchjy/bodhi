# tools/ontology-mcp/archive —— 已退役的一次性脚本

归档时间：2026-09-20。**归档 ≠ 删除**：文件还在（历史/算法/文档都保留），只是不再属于日常运维面。
回退方式：`git mv archive/<文件名> ../<文件名>` 即可恢复原位置（`git log --follow` 可追全历史）。

| 文件 | 当时干什么 | 为什么退役 | 现在的对应能力 |
|---|---|---|---|
| `backfill_paths.py` | 把已有页面的 `category_path` 从三级改成两级，并回填 `wiki_path` | 一次性数据迁移，已全库执行完毕 | `ke_ontology.category_path()`（写入侧同源）+ `sync_folders.py` 重建目录 |
| `relink_pages.py` | 重建 `wiki_pages.in_links` 与父目录链 | 一次性修复；现在创建/删除/改类型的写库路径已内建目录与反链重建 | `ke_pages.py`（写入纪律统一在此）+ `sync_folders.py` |

注意：`server.py` / `ke_pages.py` 里仍留有提到 `relink_pages.py` 的**注释**（说明行格式沿用），不是代码依赖。
