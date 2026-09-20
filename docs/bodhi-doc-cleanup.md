# 按来源文档清理本体实例（删文档 → 清「Bodhi 本体图」）

> 用户 2026-09-20 口径：删掉企业知识库里的文档后，WeKnora 只清它自己产的 wiki 与 wiki 图，
> **Bodhi 本体图里那份实例数据没清**。问：能否联动清理？不能的话，要能**按文档统计 + 清理**。
> 相关：`docs/weknora-fork.md`（fork 策略）、`docs/handoff-ontology-upload.md`（本体模型加载链路）、
> `tools/ke-core/README.md`（ke-core 模块表）。

## 0. 结论

- **不能「事件驱动」联动**：删文档是上游 Go 做的事，它只处理自己那套 wiki 页/图；我们的实例层是
  **另一份数据**（PG `wiki_pages.source_refs` 标记的实例页 + Neo4j `BodhiInstance`），上游不知道它存在。
  要真联动只有两条路：① 改 Go 并重建 app 镜像（本项目明确不做，见 `docs/weknora-fork.md` §11 的
  「上游只当重活供应商，自研一律 Python」）；② 在上游 schema 上挂「删除触发器 + 队列」（改三方表结构，
  方案见 §5，**未实现**）。
- **已实现「按文档统计 + 清理」**（零 Go 改动）：
  - `tools/ke-core/ke_docs.py`（唯一实现）+ CLI；
  - HTTP：`GET /bodhi/docs`、`GET /bodhi/docs?orphans=1`、`GET /bodhi/docs/pages`、
    `POST /bodhi/docs/purge`、`POST /bodhi/docs/sweep`；
  - 可选定时巡检（≈联动兜底）：`bash deploy/weknora-fork/doc_gc_install.sh --enable`
    → `bodhi-doc-gc.timer` 每 15 分钟跑一次 `sweep --all --apply`。
- **兜底巡检的边界**：**只清「来源文档已删/不存在」的实例页**；文档还在的页一律不动；
  `index` 页与无 `source_refs` 的页永不碰。

## 1. 实例层的实际存法（实测）

| 位置 | 内容 | 删文档时被上游联动吗 |
|---|---|---|
| PG `wiki_pages`（我们的实例页） | `page_type = 模块:类`（如 `bmm:Goal`）、`source_refs` = 来源知识 id **数组**、`chunk_refs` = 片段 id 数组、`last_edit_source='bodhi-onto-mcp'`、正文含 `（来源：《文档名》 片段 #N）` | ✗ |
| PG `wiki_pages`（上游产的页） | pipeline / agent 的页 | ✓（WeKnora 自己清） |
| Neo4j `BodhiInstance` | 只有 CLI `tools/ontology-extract/extract.py` 那条路径会写（属性 `knowledge_id` / `source_doc` / `kb`）；**当前部署为 0** | ✗ |
| PG `knowledges` | 文档行；**删文档 = 软删**（`deleted_at` 置位，行仍在）→ 我们据此判「已删」 | — |

前端「本体图谱」、`/bodhi/graph`、`graph_page.py` 读的都是 PG 这些页 → **清页就等于清图**。

## 2. 接口与命令

> `kb_id` 既可用 **UUID**，也可用**知识库名称**（服务端经 `ke_db.resolve_kb_id()` 解析）；**不存在的库会直接报错**，
> 不会返回空结果（否则会得到"0 页 0 残留"的假清白结论）。
>

| 方式 | 调用 | 说明 |
|---|---|---|
| HTTP | `GET /bodhi/docs?kb_id=<kb>` | 每个来源文档：页数 / **独占页数** / 类型数 / `status`（`live`/`deleted`/`missing`） |
| HTTP | `GET /bodhi/docs?kb_id=<kb>&orphans=1` | 只看「来源文档已删/不存在」的残留（+ `pages_without_source` 报数） |
| HTTP | `GET /bodhi/docs/pages?kb_id=<kb>&knowledge_id=<id>` | 某文档的实例页清单（带 refs/chunks） |
| HTTP | `POST /bodhi/docs/purge` | `{kb_id, knowledge_id | title, apply, delete_exclusive, sync_folders}`（默认 dry-run） |
| HTTP | `POST /bodhi/docs/sweep` | `{kb_id, apply, delete_exclusive, include_missing}`（默认 dry-run） |
| CLI | `ke_docs.py stats\|orphans\|pages <kb_id>` | 只读 |
| CLI | `ke_docs.py purge <kb_id> <knowledge_id\|标题> [--apply] [--strip-only]` | 按文档清理（默认 dry-run） |
| CLI | `ke_docs.py sweep <kb_id>\|--all [--apply] [--strip-only]` | 巡检清理（默认 dry-run；`--all` = 所有有来源引用的 KB） |
| 定时 | `bash deploy/weknora-fork/doc_gc_install.sh [--enable\|--dry-run\|--uninstall]` | 装/卸 `bodhi-doc-gc.timer`（`DOC_GC_INTERVAL` 可改，默认 15min） |

```bash
# 看现状 / 会删什么（只读，先跑这两条）
/opt/bodhi-venv/bin/python3 tools/ke-core/ke_docs.py stats dbc2528f-611b-48da-9a71-d7c93975adb4
/opt/bodhi-venv/bin/python3 tools/ke-core/ke_docs.py sweep --all
# 真清（不可逆：页 + 版本快照一起删）
/opt/bodhi-venv/bin/python3 tools/ke-core/ke_docs.py sweep --all --apply
```

## 3. 语义（关键口径）

- **独占页**（`source_refs` 只有这份文档）→ **删**：复用 `ke_pages.delete_pages`
  （硬删页 + 版本快照 + 重算 `in_links` + 重算目录树 + Neo4j slug 钩子）；
- **多源页**（同一实例被多份文档提到）→ **只摘引用**：`source_refs` 去掉该 id、
  `page_metadata.ontology.doc` 指向它时一并摘掉；摘光后它就成了独占页，**下一趟**会被删；
  `sweep` 已做「一趟去重」：先算净效果（引用集全在已删文档里的页直接删），不会漏也不会重复删；
- **`index` 页永不删**（沿用 `delete_pages` 的保护）；**无 `source_refs` 的页不动**，只报
  `pages_without_source`（老数据/上游页，归不了档）；
- **正文不改**：正文来源行只写标题（`（来源：《系统定义.docx》 片段 #3）`），同名文档多版本时
  定位不到具体 id，改写反而可能误伤别的来源 → **`source_refs` 是唯一权威的来源标记**；
- 默认 **dry-run**；`delete_exclusive=false`（CLI `--strip-only`）= 只摘引用、不删页（保守模式）；
- 按 `title` 给时会把**同名多版本**的文档一起清（这正是删文档场景想要的）；只想清一个就传 `knowledge_id`。

## 4. 实测（2026-09-20，业务库 `dbc2528f-611b-48da-9a71-d7c93975adb4`）

`GET /bodhi/docs`（只读）：

| knowledge_id | 标题 | status | 实例页 | 独占页 | 类型数 |
|---|---|---|---|---|---|
| `6d7dfcbf…` | 系统定义.docx | deleted（2026-09-19 08:25） | 50 | 36 | 12 |
| `8f265dd3…` | 系统定义.docx | deleted（2026-09-19 14:40） | 16 | 2 | 3 |

合计 66 条「页-来源」引用，**52 个不同页**（14 页同时引用这两个版本）；
`pages_without_source = 1`（`index` 页）。

`sweep --all`（dry-run）净效果：`pages_distinct=52 / will_delete=52 / will_strip=0`
→ 52 页的来源**全部**已删，执行后会被删掉（两版本的共享页也在其中）；`index` 与无来源页不动。

> apply 是**不可逆**（连版本快照一起删）→ 默认只出计划，由使用者确认后加 `--apply` / `apply=true`。

## 5. 可选的「真联动」（未实现，需要时再上）

`knowledges` 上挂触发器 + 队列，做到**秒级事件驱动**：

```sql
-- 上游表结构上新增（不改 Go；但与 WeKnora 迁移共存需评估）
CREATE TABLE IF NOT EXISTS bodhi_doc_events (
  id bigserial PRIMARY KEY, knowledge_id text NOT NULL, event text NOT NULL,
  at timestamptz NOT NULL DEFAULT now(), handled_at timestamptz);
CREATE OR REPLACE FUNCTION bodhi_doc_event() RETURNS trigger AS $$
BEGIN
  INSERT INTO bodhi_doc_events(knowledge_id, event)
  VALUES (COALESCE(NEW.id, OLD.id),
          CASE WHEN TG_OP = 'DELETE' THEN 'delete' ELSE 'soft-delete' END);
  RETURN COALESCE(NEW, OLD);
END $$ LANGUAGE plpgsql;
CREATE TRIGGER bodhi_doc_gc AFTER UPDATE OF deleted_at OR DELETE ON knowledges
  FOR EACH ROW WHEN (TG_OP = 'DELETE' OR (OLD.deleted_at IS DISTINCT FROM NEW.deleted_at))
  EXECUTE FUNCTION bodhi_doc_event();
```

再由一个 1 分钟的消费者（systemd timer 或 `bodhi-mcp` 内的轻量线程）取未处理事件 →
`ke_docs.purge_document(kb_id, knowledge_id, apply=True)`。
**为什么默认不做**：要在三方库里加表+触发器（升级/迁移要盯），收益只是把延迟从「一个巡检周期」
降到秒级 —— 当前用「按需 API + 定时巡检」已够。

## 6. 排障

| 症状 | 处置 |
|---|---|
| `stats` 里 `live=0` 但图里还有页 | 那些页没有 `source_refs`（老数据/上游页）→ 只能按 slug 人工删：`bash deploy/weknora-fork/delete_wiki_pages.sh`（或前端多选删除） |
| 删完图里没变化 | `/bodhi/graph` 每次读库、不缓存 → 前端点「刷新」即可；仍不对看 `GET /bodhi/docs?kb_id=…&orphans=1` 是否还有 |
| 想把实例页保留、只断来源 | `purge … --strip-only` / `delete_exclusive=false` |
| 定时器没跑 | `systemctl list-timers bodhi-doc-gc.timer`；`journalctl -u bodhi-doc-gc.service -n 50`；确认装了 `/usr/local/bin/bodhi-doc-gc` |
| Neo4j 实例（CLI 那条路径） | `neo4j_purge()` 会按 `knowledge_id`/`source_doc`/`source_docs` 清点与边；当前部署 `BodhiInstance` 为 0，属正常 |
