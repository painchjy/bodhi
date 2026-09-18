# ontology-extract —— 本体驱动的知识提取（v0.1 捷径验证版）

> 定位：**先让你能验证"用本体提取"**。不依赖 fork 镜像，直接
> `文档 → 轻量版本体提示词 → LLM → 本体约束校验 → 写 Neo4j`，
> 然后在 `http://localhost:7474` 看结果（节点按 label 着色、关系显示类型名并带方向箭头）。
>
> 最终形态（wiki 页面 + 自定义类型颜色 + 独立图谱页）见 `docs/weknora-fork.md` §9。

## 依赖

**无需新增任何依赖**：

| 用途 | 用什么 | 本机状态 |
| --- | --- | --- |
| 调 LLM（OpenAI 兼容） | `openai` | 已装 2.48.0 ✓ |
| 写图 / 读存量 | **Neo4j HTTP API**（`POST /db/neo4j/tx/commit`）+ 标准库 `urllib` | 端口 7474 已发布 ✓ |
| 读本体目录 | 标准库 `json` | ✓ |

LLM 配置读仓库根的 `.env`：`LLM_API_KEY` / `LLM_BASE_URL` / `LLM_MODEL` /
`LLM_TEMPERATURE` / `LLM_MAX_TOKENS`。图库连接默认
`http://localhost:7474`、`neo4j` / `password`（与 `deploy/.env` 一致）。

## 用法

```powershell
# 1) 只看提示词（不花额度）：导出 system+user 到 logs/last_prompt_preview.md
python tools/ontology-extract/extract.py --doc docs/samples/ea-对公开户流程.md --model ea --dry-run

# 2) 真跑：调 LLM -> 本体校验 -> 写 Neo4j
python tools/ontology-extract/extract.py --doc <你的文档.md> --model ea

# 3) 调 LLM 但先不写图（看抽取出什么、有多少违规）
python tools/ontology-extract/extract.py --doc <你的文档.md> --model bmm --no-write
```

| 参数 | 说明 |
| --- | --- |
| `--doc` | 待抽取文档（.md/.txt） |
| `--model` | 本体模型 key，当前可选 **`bmm` / `ea`**（其余 3 个扩展模块没有轻量版，见 §8.5） |
| `--kb` | 写入图里的知识库标记（默认 `bodhi-poc`），便于区分批次 |
| `--dry-run` / `--no-write` | 不调 LLM / 不写图 |
| `--max-chars` | 文档截断长度（默认 24000；正式版按 chunk 分片） |

## 提示词怎么组装（三段）

1. **专家角色**——来自 TTL 的 `bodhi:expertRole`（已随本体目录下发，不再是代码里的常量）；
2. **本体轻量版**——`artifacts/prompts/<模型>_light.md`（真源 `ontology/<模型>轻量版.md`），
   **不注入完整 TTL**；
3. **可用类型 / 可用关系枚举**——取自 `artifacts/weknora/ontology_index.json`
   （`classes[]` / `relations[]` / `cross_module_bridges[]`，关系带 domain→range）。

输出契约（按新口径裁剪，与 `src/services/extraction_service.py` 对齐）：

- `elements[]`：`type`（本体 class）+ `name` + `definition` + `source_text`（**逐字引用**）+ `source_span`
- `relationships[]`：`type`（本体关系）+ `source_element` / `target_element` + `source_text`
- `unmatched[]`：文档里有但归不进本体的内容（不丢弃）
- **没有 `action` / `existing_id`**：不让 LLM 判新增还是合并（由 WeKnora 的向量匹配决定，§8.2）

## 日志（与旧实现同格式）

每次调用写 `logs/ontology_<method>_<yyyymmdd_HHMMSS>.log`：

```
=== 提取调试日志 <ts> ===
finish_reason: …
usage: …
--- Message[0] role=system ---  （含专家角色 + 轻量版 + 枚举 + 契约）
--- Message[1] role=user ---    （存量要素清单 + 文档正文）
--- LLM 原始返回 ---            （原文，便于排查）
```

并在控制台打印 `[Bodhi]   📝 LLM调试日志已保存: logs/…`。

## 写到图里长什么样

| 元素 | 约定 |
| --- | --- |
| 节点 | 共享 label `BodhiInstance` + **每类一个 label** `模块前缀__类名`（如 `ea__Activity`），属性含 `name` / `type`（`ea:Activity`）/ `type_label`（活动）/ `module` / `definition` / `source_doc` / `source_text` / `authoritative` |
| 关系 | 类型 = 关系本地名大写（如 `ACTIVITY_HASTASK`，Neo4j 关系类型不能带 `:`），属性含 `type`（`ea:activityHasTask`）/ `label`（包含任务）/ `source_text` |
| 合并 | `MERGE` on `key = type|name`（同一实体在多份文档多次提及汇到同一节点 = 多源）；`authoritative` 只在首次创建时置 true |

在 `http://localhost:7474` 里看：

```cypher
MATCH (n:BodhiInstance) WHERE n.module = 'ea' RETURN n LIMIT 100
MATCH (a:BodhiInstance)-[r]->(b:BodhiInstance) RETURN a, r, b LIMIT 200
MATCH (n:BodhiInstance) RETURN n.type AS 类型, count(*) AS 数量 ORDER BY 数量 DESC
```

## 与最终形态的差距（本版有意为之）

- 节点是**知识实体**，不是 wiki 页面；颜色由 Neo4j Browser 按 label 自动分配
  （正式版由前端用 `ontology_index.json` 的 `classes[].color` / `legend[]` 渲染）；
- 不做向量去重（正式版交 WeKnora 的 `selectDedupCandidatePages`）；
- 不做 chunk 级引用（正式版写 `chunk_id`/`knowledge_id`，本版记 `source_doc` + 逐字 `source_text`）；
- 单文档截断，不分片（正式版按 batch/chunk 走）。
