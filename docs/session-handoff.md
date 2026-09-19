# bodhi2 × WeKnora 会话交接单（2026-09-19 当晚）

> 目的：让**新开的会话**能在 5 分钟内接手，不必重新侦察。所有结论都已实测。
> 配套阅读：`docs/weknora-fork.md`（策略与 §11 前端）、`docs/bodhi-reasoning.md`（推理规格）。

## 0. 本轮"上下文杀手"排名（供下次会话避坑）

| 排名 | 消耗源 | 规避办法 |
|---|---|---|
| 1 | `docker logs WeKnora-app \| grep …`：日志里带完整 `response_body={…}`（单条上千字符 ✗） | **用 psql 精确取字段**；必须看日志时先 `grep -o` 出短模式，或 `cut -c1-200` 截断 |
| 2 | 整段读取大文件（`server.py` 2600+ 行、`WikiBrowser.vue` 235KB、`patch_frontend.py`） | 先 `grep -n` 定位行号，再 `sed -n 'a,bp'` 只取 20~40 行 |
| 3 | dump 长文本（页面正文、`page_metadata`、提示词 1 万字符、图谱 JSON） | SQL 里就 `left(content, 1200)`；验证只看**计数与样例各 1 条** |
| 4 | 在 PowerShell 里嵌中文/引号跑 wsl 命令（反复解析失败 ✗ 重试） | **写成 `%TEMP%\*.sh` → `bash /mnt/c/.../*.sh`**，输出重定向到文件再读需要的行 |
| 5 | 前端镜像重建（npm/pnpm 冲突、截断文件、OOM；单次 5~10 分钟 + 大量日志） | 用已固化脚本一键跑，**后台 systemd-run**，只看结尾 20 行 |

## 1. 当前可用状态（全部实测）

- **前端**：`weknora-ui:bodhi2`（v4 已上线）；wiki / 图谱 / 本体图谱 三 tab **常显**（`isWiki` 恒真）；
  类型图标 + 悬停中文类名、名称与徽标顶端左对齐、列表视图也有徽标。
- **MCP/本体服务**：`bodhi-mcp.service`（WSL，`--host 0.0.0.0 --port 8765`），5 个工具：
  `extract_and_save`（**默认异步**，秒回 `job_id`）、`extract_status`、`list_pending_merges`、
  `resolve_pending_merge`、`ontology_types`。
- **已验证的端到端链路**：智能体只处理**用户指定的那篇**文档 → 调一次 `extract_and_save`
  → 后台抽 1~2 分钟 → 写库（`last_edit_source=bodhi-onto-mcp`）→ **自动重建两级目录** → 回报明细。
  真实结果示例：29 要素 / 19 关系 / 5 新增 / 19 合并 / 0 违规 / 7 未匹配（含名称与理由）。
- **数据**：企业知识库（`dbc2528f-611b-48da-9a71-d7c93975adb4`）我们的页 **100**；
  本体模型库（`08810cbd-af86-48d1-bd25-3b2c338e3d68`）TTL 编译页 125。

## 2. 必须记住的三条"上游约束"（踩过）

1. **KB 能力位不能关**：`indexing_strategy.wiki_enabled=false` 会让后端 `/wiki/pages`、
   `/wiki/folders` 直接 400（`error code 1000, Wiki feature is not enabled for this knowledge base`）
   → 界面看似"树空了"（其实是接口被拒）。要"上传不生成 wiki"只能用事后清理：
   `bash deploy/weknora-fork/cleanup_auto_wiki.sh --apply`（软删除 `pipeline`/`agent` 页，**保留 index**，并兜底重建目录）。
2. **app 侧 MCP 客户端 60s 硬超时且无配置项** → 抽取必须异步（已实现），别改回同步。
3. **写入侧主键是确定性 UUIDv5**（slug 派生）→ 必须用 `ON CONFLICT DO UPDATE`，
   否则撞上**软删除的旧页**会整批回滚（已修）。

## 3. 剩余任务（按优先级，均已定位到文件）

| # | 任务 | 入口 | 规模 |
|---|---|---|---|
| 1 | **知识推理引擎**（需求主线的下一步）：把本体自带 SHACL 编译成规则 JSON（`--emit-rules`）→ `tools/ke-core/reason.py` 判定内核 → `derive` 幂等落库 → MCP 工具 `reason_validate`/`reason_derive` | 规格：`docs/bodhi-reasoning.md`（规则清单 S1–S9 / O1–O5 已抽好）；规则源：`artifacts/shacl/*`、`ontology/shapes/*` | **大**（建议单独开一轮） |
| 2 | 智能体收敛为一个入口（两个 bodhi 智能体提示词已一致 1152 字符；差异只剩默认模型） | `deploy/weknora-fork/set_agent_prompt_lean.py` + `custom_agents` | 小 |
| 3 | index 索引页与 wiki 同步（上游 `pipeline` 维护的 105 字短文，版本在涨但**不覆盖我们的 SQL 写入**） | 方案 A/B/C 见上轮讨论；若走 B 需先查 app 触发 wiki ingest 的接口/队列键 | 中 |
| 4 | `gen_agent_config.py` 标注停用（保留追溯） | 同目录 | 微 |
| 5 | 前端 v3 细节视觉复核（用户侧看一眼即可） | — | 微 |

## 4. 常用命令（复制即用）

```bash
# 体检（容器 / 服务 / 前端补丁 / 图数据 / 库内计数）
bash /mnt/c/Users/PHJY/source/bodhi2/deploy/wsl-up.sh status

# 前端镜像重建（改过 Vue 才需要）
bash /mnt/c/Users/PHJY/source/bodhi2/deploy/weknora-fork/build_frontend.sh /root/fe-build
bash /mnt/c/Users/PHJY/source/bodhi2/deploy/weknora-fork/deploy_frontend.sh deploy

# 离线复放（不烧 token、不受 60s 超时影响；验证写入路径）
cd /mnt/c/Users/PHJY/source/bodhi2/tools/ontology-mcp && python3 replay_extraction.py --dry-run

# 清理上游自动页 + 兜底重建目录
bash /mnt/c/Users/PHJY/source/bodhi2/deploy/weknora-fork/cleanup_auto_wiki.sh --apply
```


## 5. 待做的前端 v5（用户 2026-09-19 明确要求，一次重建即可打包两件）

两件都在 `deploy/weknora-fork/frontend/patch_frontend.py` 里加 **v5 段**，然后
`build_frontend.sh /root/fe-build` → `deploy_frontend.sh deploy`（一次重建 ✓）。

### 5.1 类型标签显示为空 → 改成**彩色圆点**（用户口径：「改成有颜色的圆点，简单一些」）

- **根因**：v3 用 `<t-icon :name="getPageIcon(page)">`，而 `getPageIcon()` 对本类类型统一返回
  `'hierarchy'` —— 该图标名在打包后的 tdesign 图标集里**渲染为空** ✗（悬停 tooltip 正常 ✓，
  所以只是图标本身没画出来）。
- **改法**：把树/列表两处的 `t-icon` 换成
  `<span class="wiki-page-item-type-dot" :style="{ background: ontologyColor(page.page_type) }"></span>`，
  tooltip 内容不变（`中文类名（bmm:XXX）`）。
- 颜色来源：`gen_frontend_types.py` 生成的 `ontologyTypes.ts` 里**本来就有每个类型的 color**
  → 让生成器多导出一个 `ontologyColor(type)`（读同一张表）✓，别在 Vue 里硬编码。
- CSS 追加：`.wiki-page-item-type-dot { display:inline-block; width:8px; height:8px;
  border-radius:50%; margin-right:6px; flex:0 0 auto; }`

### 5.2 知识库页加「启用/禁用 wiki 自动生成」开关（用户口径：上传时自己控制是否自动提取 wiki）

- **背景**：能力位 `indexing_strategy.wiki_enabled` 关掉后，上传文档**不再自动生成 wiki**
  （省时省 token ✓），但后端 `/wiki/pages`、`/wiki/folders` 会 400 → 界面暂时看不到 wiki ✗。
  所以开关必须**带明确提示**：关闭后"wiki/图谱界面临时不可用，需要时再打开"。
- **实现步骤**（下轮直接照做）：
  1. 在 `KnowledgeBase.vue` 头部（面包屑右侧）加 `<t-switch>`，绑定
     `kbInfo.indexing_strategy.wiki_enabled`；
  2. 变更时调用知识库更新接口 —— **先确认方法名**：`src/api/knowledge-base.ts` 里找
     `updateKnowledgeBase` / `patchKnowledgeBase`（若上游没有对应封装，就用 `src/utils/request`
     直接 `put('/api/v1/knowledge-bases/'+kbId, { indexing_strategy: {...} })`，
     以 app 日志里该端点的 `method` 为准）；
  3. 成功后 `MessagePlugin.success` 提示，并局部刷新 `kbInfo`；失败回滚开关状态；
  4. 提示文案：「关闭 = 上传文档不再自动生成 wiki（省时省 token）；注意：关闭期间 wiki 列表
     接口会被后端拒绝，界面暂时看不到 wiki 与图谱，需要时请重新打开」。
- 后端不需要改 ✓（`indexing_strategy` 是 KB 的普通字段 ✓，我们已多次直接 SQL 改过 ✓）。

## 6. 本轮收尾状态（2026-09-19 末）

- 智能体已**收敛为一个入口**：`bodhi-ontology-bmm` 更名为「本体知识提取（BMM / EA）」，
  提示词为精简版（1152 字符，默认 bmm、必须回显调用参数）；`bodhi-ontology-ea` 已软删保留追溯。
- `deploy/weknora-fork/gen_agent_config.py` 已按用户要求**删除**
  （`config/agents.sql`、`config/agent_system_prompt.yaml` 仍在，作历史追溯）。
- `set_agent_prompt_lean.py` 是现在**唯一**的智能体提示词入口（改提示词就改它并重跑）。
