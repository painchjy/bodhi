# 本体模型知识库（交付与导入指引）

> 交付物：**`bodhi2-03-manual.tar.gz`**（文档 + 本体真源）与 **`bodhi2-02-mcp-server.tar.gz`**（工具 + 编译产物）
> - 03 包：`ontology/` —— TTL 单一真源：`EA完整版.ttl`、`BMM完整版.ttl`、`extensions/*.ttl`（服务详设/归属/FD 扩展）、`shapes/*`、`lexicon/*`；
>   `seed/` —— **可直接导入的种子**（无需 Python/编译器）：`ontology_kb_pages.sql`、`ontology_kb.json`、`kb_row.sql`；
>   `refresh_ontology_kb.sh` —— 一键：编译 → 生成清单 → 投影 → 重启容器；
> - 02 包：`tools/ontology-compiler/` —— 编译器（TTL → artifacts，需 `rdflib`+`PyYAML`）；
>   `tools/ontology-extract/ontology_wiki.py` —— 把编译产物投影成「本体模型」知识库页面；
>   `artifacts/` —— 编译产物：`weknora/ontology_index.json`（类型/关系枚举/颜色）、`weknora/ontology_wiki.jsonl`（页面清单）、
>   `weknora/extract_config.*.json`、`prompts/`、`json_schema/`、`neo4j/`（投影脚本）。
>
> **两个包解到同一父目录**即得上文与下文命令假设的仓库布局（`ontology/` 与 `tools/`、`artifacts/` 同级）。 本包**不含**编译产物 `artifacts/shacl/`（与 `ontology/shapes/*.ttl` 逐字节相同，只留真源；编译器会重新生成）。

## 0. 「本体模型知识库」是什么、为什么必须有

它是**类型系统的运行真源**：每个本体类一页、关系在页面里表达。作用有三：

1. **智能体查类型**：MCP 的 `ontology_types(model, focus?)` 返回可用类/关系/每个类允许的数据属性 —— 抽取和设计落库都按它校验；
2. **前端下拉与图例**：编辑页的「本体类型」下拉、图谱配色都取这类清单（颜色/中文名编在 UI 产物里，见 `FRONTEND.md`）；
3. **巡检基准**：`audit_scan` 的 B1（类型不在模型）/B2（关系不在模型）/B3（range 违反）都以它为准。

实测规模（我们这版，2026-09-29 起 EA 瘦身 + IT 资产迁 BMM 后）：类 **50** ｜ 关系 **81** ｜ 数据属性 **114** ｜ 模块 **6** ｜ 轻量版提示词 **2** ｜ 索引页 **1** ＝ **254 页**。

## 1. 方案 A（推荐）：按 TTL 重新生成（可随本体演进）

```bash
# ① 环境：python3（标准库即可）；数据库访问用 BODHI_DB_* 环境变量（见 MCP-SERVER.md §2）
#    另外建议设 BODHI_ONTOLOGY_KB_ID=<本体库 uuid>（或先给库打标记，见 §2 末尾）——两者之一即可
cd /opt/bodhi2
export BODHI_DB_HOST=127.0.0.1 BODHI_DB_USER=postgres BODHI_DB_PASSWORD=你的口令 BODHI_DB_NAME=WeKnora

# ② 编译 TTL → artifacts（幂等；只读写仓库内文件）—— 编译器在 02 包 `tools/ontology-compiler/`
python3 tools/ontology-compiler/compile.py compile --diff

# ③ 生成页面清单（投影工具在 02 包 `tools/ontology-extract/`）
python3 tools/ontology-extract/ontology_wiki.py build

# ④ 投影进知识库（**幂等**：先删 last_edit_source='ontology-wiki' 的旧页，再写）
python3 tools/ontology-extract/ontology_wiki.py project --kb-id <本体模型知识库的 uuid>
#    若还没有这个知识库：先在 UI 建一个空知识库「企业本体模型」，再把它的 id 填到这里

# ⑤（可选）一键版：编译+投影+重启容器
ONTOLOGY_KB_ID=<uuid> bash deploy/weknora-fork/refresh_ontology_kb.sh
```

## 2. 方案 B：直接导入种子（部署机没有 Python / 不想编译）

```bash
# ① 建一个空知识库（UI），记下它的 uuid：ONT_KB=<uuid>
# ② 用它的 id 替换种子里的占位符，然后导入
sed "s/__ONTOLOGY_KB_ID__/$ONT_KB/g" seed/ontology_kb_pages.sql > /tmp/ont.sql
psql "postgresql://postgres:口令@127.0.0.1:5432/WeKnora" -v ON_ERROR_STOP=1 -f /tmp/ont.sql
# ③ 或：直接用 JSON 清单导入（seed/ontology_kb.json，逐条 slug/title/page_type/content）
```

导入的是 `wiki_pages` 行（含 `slug / title / page_type / content / page_metadata / out_links / in_links / version`），
**不需要** `knowledges`（文档）表 —— 本体页面是"生成型"页面，`source_refs` 为空是**正常的**（巡检对 `ontology:*` 类型豁免 C1）。
导入后如前端树不显示，跑一次目录重建（或重启 app）：`docker restart WeKnora-app && docker restart WeKnora-frontend`。

**给库打「本体库」标记（推荐做一次）**：前端「上传本体文件」按钮与 MCP 的默认操作库都靠它认库
（判定顺序见 `FRONTEND.md` §7：env → 标记 → 库名 → `ontology:*` 页数）：

```sql
UPDATE knowledge_bases
   SET wiki_config = COALESCE(wiki_config, '{}'::jsonb) || '{"bodhi_ontology_kb": true}'::jsonb
 WHERE id = '<本体模型知识库 uuid>';
```

## 3. 校验（导入/生成之后必须做）

```bash
# ① 页数与类型分布（期望：Class 52 / Relation 80 / Property 107 / Module 6 / LightDoc 2 / index 1 = 248）
psql "postgresql://postgres:口令@127.0.0.1:5432/WeKnora" -At -F' | ' -c \
 "SELECT page_type, count(*) FROM wiki_pages WHERE knowledge_base_id='<ONT_KB>' AND deleted_at IS NULL GROUP BY 1 ORDER BY 2 DESC"

# ② MCP 侧：类型枚举能取到（应能列出全部本体类，含 5 个模型 bmm / ea / ea-service / ea-ownership / bmm-fd 的类）
curl -s "http://127.0.0.1:8765/bodhi/ontology/classes" | head -c 400

# ③ 智能体侧：让智能体调 ontology_types(model="ea-service")，应返回
#    类 easvc:ServiceOperation / easvc:BusinessAttribute …、关系 operationOperatesOnAttribute（边带 crudKind）…
```

## 4. 加/改本体类型（标准流程）

```bash
# ① 改 TTL（真源只有这里）
vi ontology/EA完整版.ttl              # 或 ontology/extensions/*.ttl 放业务扩展
# ② 编译 + 投影（见 §1 ②③④）
# ③ 若改了「类清单」（新增/删除类）→ 前端类型下拉与配色需要**重建 UI 镜像**
python3 01-frontend/frontend/patches/gen_frontend_types.py --fe /path/to/frontend && （重新构建前端）
# ④ 巡检复检
curl -s "http://127.0.0.1:8765/bodhi/audit?kb_id=<业务库>" | head -c 400
```

> **注意 TTL 语法**：注释里不要出现 ASCII 双引号（`"`）未闭合的情况 —— 会让 Turtle 解析失败（我们踩过）。
> 编译报错时先 `python3 tools/ontology-compiler/compile.py compile` 看具体行号。

## 5. 与业务知识库的关系（别混）

| 知识库 | 内容 | 来源 | 谁写它 |
|---|---|---|---|
| **企业本体模型**（本包） | 类/关系/属性/模块页 | TTL 编译 | 只用本包的脚本（人来跑）|
| 企业知识（业务） | 流程、活动、任务、服务、实体… 实例页 | 文档抽取（分批）+ 设计落库 | 智能体（`save_knowledge`，走 `domain_modeling` 技能）|

业务库的智能体在**查类型**时读前者；写实例时只写后者。两个库可以都给智能体绑定（智能体配置里 `knowledge_bases` 两项都填）。

## 6. 上传 TTL 现在会**自动编译**（2026-09-24 打通）

前端「上传本体文件」对话框里有两个开关，**默认都打开**：

| 开关 | 默认 | 作用 |
|---|---|---|
| **上传后编译并生效** | ✅ 开 | TTL 落进真源 `ontology/extensions/<key>-ext.ttl` + 登记 `ontology/extensions/_registry.json` → 自动 `compile.py compile`（artifacts 更新）→ 回执给出**编译了什么**（`compiled.delta`：模块/类/关系/属性 的 before→after）|
| 重投影本体 wiki 页 | ✅ 开 | 编译完成后再把「本体模型知识库」重投影一遍（页 + 目录）|

- 关闭「上传后编译并生效」= 老行为：**只更新图库**（Neo4j），产物不动 → 智能体校验仍会说
  「本体里没有这个类」、前端类型下拉也不认它（这是以前的坑，现在默认不走这条路了）。
- **缺 `bodhi:expertRole` 会自动补**：编译器要求每个模块声明专家角色，否则整次编译失败；
  上传时若缺，服务端会在**真源副本**里补一条默认值（回执 `source.injected` 会写清楚），原上传件不动。
- **全量投影回放默认不做**（`apply_after=false`）：导入步骤已把本模块语句写进图库；全量回放要逐条执行
  整份投影 cypher（实测几十秒到几分钟），那是**运维修复**的事（见下）。要强制回放可传 `apply_after=true`。

### 运维修复（崩溃/手工改动导致四层不一致时）

```
真源 TTL ──compile──► artifacts/（编译产物）──apply──► Neo4j 投影 ──project──► 本体知识库 wiki
```

任一层被破坏（或换机器重建）都可以用**幂等**的修复入口恢复：

```bash
# 编译 → 全量回放投影 → 重投影 wiki → 一致性体检（含 C5：会话页是否落错库）
/opt/bodhi-venv/bin/python3 tools/ke-core/ke_admin.py repair [kb_id]

# 也可以走 HTTP（MCP 服务）
curl -s -X POST http://127.0.0.1:8765/bodhi/ontology/repair \
     -H 'Content-Type: application/json' -d '{"compile": true, "project_wiki": true}'
```

体检项里与本体相关的：
- **C5**：领域建模会话记着的页**不在本库**（历史跨库同 slug 撞主键的存量，见 `ke_audit`）→ 需要重跑建模或人工处理；
- **D4/D5**（页身份完整性，2026-09-24 加固）：D4 = 同库同 slug 多行（"影子页"，更新只命中其中一行）；D5 = 页 id 非规范派生（走了兜底分支或有人直接写库）。**正常都为 0**，出现即为信号；
- **C1/C3**：实例页无来源 / 正文称有来源但 `source_refs` 空；
- **B4/B5**：模型库页与 Neo4j 投影、编译产物不一致 → 跑一次 `repair` 即可对齐。

> 页数变少的常见原因：投影会按 **Neo4j 实况做减法**。若 Neo4j 里缺对象属性/模块节点，
> 投影出的页就会少于产物规模 → 先 `apply_projection()`（`repair` 已包含）再投影。
