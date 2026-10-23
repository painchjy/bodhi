# 本体上传回归（级联删除 / 备份 / 报错恢复）

> 用户口径（2026-10-22）：前端上传本体文件 → **级联备份 + 删除下游** → 新模型文件复制到
> `ontology/sources/` → **编译并生成产物**。编译源头 = `sources/*.ttl`，所以**先确认这里的文件正确**，
> 再确认产物完整性。**不许自己跑编译器**，一律调用现有 `upload_ttl`。

## 链路（`ke_admin.upload_ttl`，实现已具备，本片只补回归）

| 步 | 动作 | 失败时 |
|---|---|---|
| ① | 留痕 `ontology/uploads/<时间戳>-<文件名>` | — |
| ② | **预检（只读）**：依赖模块齐 + TTL 可解析 | **直接中止**，不动真源/图/wiki |
| ③ | 写真源 `ontology/sources/<模块>.ttl`（失败回滚） | 回滚 |
| ④ | **级联删除**下游模块（真源文件 + 图 + wiki）前，**备份全部真源**到 `sources/.backup/<ts>/`（只留最近 5 份） | 异常 ⇒ 按备份**整体恢复 + 重编译** |
| ⑤ | 编译 `artifacts/weknora/ontology_index.json` | 同上（回滚） |
| ⑥ | 灌图（按模块 DETACH + 重放投影）、重投影 wiki | 同上 |

## 用例（`tests/test_ontology_upload.py`）

默认 **skip**（会真重编本体、改 `artifacts/`、重灌 Neo4j 图）：

```bash
BODHI_TEST_ONTOLOGY_UPLOAD=1 python3 -m pytest tests/test_ontology_upload.py -q
```

| # | 步骤 | 期望 |
|---|---|---|
| ① | 上传 `BMM完整版.ttl` | `sources/` **只剩 `bmm.ttl`**（bpmn/agent 属下游被级联删）；回执含 `cascade_purge` + `sources_backup`；产物含 `bmm` |
| ② | 上传 `BPMN完整版.ttl` | `sources/` = `bmm.ttl + bpmn.ttl`；产物含 `bmm/bpmn` |
| ③ | 上传 `智能体开发完整版.ttl` | `sources/` = **3 个 ttl**；产物含 `bmm/bpmn/agent` |
| ④ | 产物完整性 | `ontology_index.json` 的**全量总数回到上传前**（模块集合一致 ⇒ 数字应一致） |
| ⑤ | 报错恢复 | 传坏 TTL ⇒ `ttl_unparsable`，且 `sources/` 与产物**零改动** |

实测（2026-10-23）：① `sources` 变为 `['bmm.ttl']`；② `['bmm.ttl','bpmn.ttl']`；
③ `['agent.ttl','bmm.ttl','bpmn.ttl']`；⑤ 坏 TTL 返回
`上传导入 被拒绝：ttl_unparsable …（**没有改动真源、没有删除任何数据**）`，状态不变。

## 发现（记录，待你决定）

级联删除会连**下游模块的随包文件**一起删（本次 `ontology/EA完整版.ttl`、`ontology/EA轻量版.md`
被删，已从 git 恢复）。这些文件在"文件即模块"（2026-09-30）之后**已不是编译源**（编译器只扫
`sources/*.ttl`），删掉不影响运行；若仍要保留，需要把 `cascade_delete_modules` 的 keep 范围
扩到 `ontology/*`（或把它们从 git 移除，统一以 `sources/` 为唯一真源）。

## 注意

- 编译在**服务进程**里跑（`/usr/local/bin/bodhi-mcp` → `/opt/bodhi-venv/bin/python3`，含 `rdflib`），
  所以测试机不需要自己装 rdflib；本机系统 `python3` 没有 rdflib，**不要**用它直连 `ke_admin.compile_artifacts`。
- 用例终态会回到 3 个模块（顺序即自愈）；若中途失败，`upload_ttl` 自带"备份恢复 + 重编译"，
  必要时用 `logs/_snap_sources/` 快照重传。
