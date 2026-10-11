---
id: kb_transfer
name: 知识库迁移（导出 → 转移 → 导入 → 一致性比对）
description: 把一个知识库的内容导出成文件（沙箱工作目录 → 可下载到本地），再导入到另一个知识库（本机或内网），最后用 **PG + Neo4j 双源比对**确认一致；用于"本机 → 内网"迁移、测试库搭建、给客户灌数据。全过程幂等、可重复执行。
when: 用户要"导出/迁移/复制某个知识库""把知识库搬到内网/另一套环境""打个包带走""给测试库灌一份数据""核对迁移前后是否一致"时
models: [bmm]
default_model: bmm
sources: [kb]
stages: [export, transfer, import, verify]
tools: [bash, read_file, write_file, wiki_search, audit_scan]
version: 0.1.0
---

# 知识库迁移（导出 / 导入 / 比对）

> **工具**：`<包根>/tools/ke-core/kb_transfer.py`（随 02-mcp-server 包交付；只用 ke-core 既有能力，无新依赖）
> **覆盖两处真源**：PG（`knowledge_bases` 行 + `wiki_pages` + `wiki_folders` + `wiki_page_revisions` + `wiki_page_issues`）＋ Neo4j（`BodhiInstance` 按 `kb_id` + 库内边）

## 0 什么时候用

| 场景 | 用哪条 |
|---|---|
| 把某个库**搬到内网** | `export` → 把产物带走 → 内网 `import --new-kb` → `verify` |
| 本机**建个测试库**看导入效果 | `import --new-kb "测试库名"`（同名库**复用**，可反复跑） |
| 校验两份数据是否一致 | `verify <源kb_id或导出目录> --to <目标kb_id>` |
| 只想拿一份离线快照 | `export`（产物含 `manifest.json`：计数 + 每文件 sha256） |

## 1 四条命令（在沙箱里执行）

```bash
T="python3 <包根>/tools/ke-core/kb_transfer.py"

# ① 导出（源库 → 目录；产物在沙箱工作目录）
$T export <源kb_id> ./kb-xfer

# ② 打包带走（tar 比散文件好传）
tar czf kb-xfer.tgz kb-xfer            # 再把 kb-xfer.tgz 通过"沙箱文件导出/下载"取到本地

# ③ 导入（内网沙箱：先上传 tar → 解包 → 导入）
tar xzf kb-xfer.tgz
$T import ./kb-xfer --new-kb "库名"          # 新建（同名自动复用）
$T import ./kb-xfer --to <已存在kb_id>        # 或导进既有库（按 slug 覆盖）
$T import ./kb-xfer --new-kb "库名" --keep-ids  # 跨实例迁移建议加：保留原 UUID

# ④ 一致性比对（**必须 ✅ 才算迁移完成**）
$T verify <源kb_id或./kb-xfer> --to <目标kb_id>
```

**参数**
| 参数 | 说明 |
|---|---|
| `--new-kb "名称"` | 建/复用同名库（配置字段从现有库拷贝，避免 NOT NULL 缺默认） |
| `--to <kb_id>` | 导入到已有库（按 `(knowledge_base_id, slug)` **先删后插**，可重复跑） |
| `--keep-ids` | 保留原页/目录 UUID；**不传则重编 id**（并自动修 `folder_id`/`parent_id`/`page_id`） |
| `verify` 的源 | 可以是**源库 kb_id**，也可以是**导出目录**（两侧用同一套归一化，所以能直比） |

## 2 验收口径（`verify` 六项）

| 项 | 比什么 |
|---|---|
| 页内容 | 每 slug 的 `title/page_type/status/content/summary/parent_slug/category_path/wiki_path/depth/sort_order/source_refs/chunk_refs/page_metadata/aliases/version` 归一化哈希 |
| 页链 | `in_links` / `out_links`（排序后） |
| 目录 | 每 `path` 的 `name/depth/sort_order` |
| 版本 | 每 `slug#version` 的 `title/content/summary` |
| 图节点 | 每 slug 的节点属性（不含 `kb_id`） |
| 图边 | `(源slug, 关系类型, 目标slug)` 全量集合 |

**纪律**：① **`verify` 不 ✅ 不许对用户说"迁移完成"**；② 差异要逐条读（缺/多/内容不一致），别只报"不一致"；③ 迁移完成后再对目标库跑一次 `audit_scan`（看 B1/C1/S1 有无新问题）。

## 3 性能与已知坑（**重要，别再踩**）

| 坑 | 现象 | 处理 |
|---|---|---|
| **ARG_MAX** | `docker exec psql -c "<SQL>"` 单条 SQL 超 ~128 KB → `OSError: Argument list too long` | 工具已内置**按字节切批**（默认 60 KB/批）；**不要**把批改回大行数 |
| CSV 字段上限 | 导出报 `field larger than field limit` | 工具已把 `csv.field_size_limit` 抬到 1 GB |
| 逐行写太慢 | 旧版逐行 `psql`：67 页要 >10 分钟 | 现为批量：**11.8 s**（67 页/28 目录/131 版本/66 节点/182 边） |
| 前端目录树看不到新页 | 目录树默认**延迟重建** | 导入后让前端**刷新该库目录**（`POST /bodhi/folders/refresh`）或重进知识库 |
| 同名库重复建 | 反复自测堆库 | 用 `--new-kb`（同名复用）；确认无用后可删测试库 |

## 4 建议的完整话术

1. 导出：`export <kb> ./kb-xfer` → 看 `manifest.json` 的计数（页/目录/版本/节点/边）**对不对得上**；
2. 打包：`tar czf kb-xfer.tgz kb-xfer` → 下载到本地；
3. 内网上传 → `tar xzf` → `import ./kb-xfer --new-kb "技术方案评审领域模型" --keep-ids`；
4. `verify ./kb-xfer --to <新库id>` → **六项全 ✅**；
5. `audit_scan(kb_id=<新库id>)` 体检无新问题 → 再向用户汇报"迁移完成 + 计数 + 一致性结论"。
