# 配置矩阵（发布期唯一口径：**哪些能改、改哪、怎么生效、怎么校验**）

> 用户裁定（2026-10-26）：**不做 `/admin` 后台**。凡"必须在多处保持一致"的项，一律
> **代码/文件为真源**、发布程序写入、`selfcheck` 校验；UI 只保留"人该改的项"。
> 本表就是**交付验收单**：每行都要能勾 ✓。

## 1. 智能体（`custom_agents`）

| 项 | UI 能改？ | 真源（单一） | 写入命令 | 生效 | 校验 |
|---|---|---|---|---|---|
| 名称/描述 | ✓ 可以 | UI | —（人工） | 立即 | UI 里看一眼 |
| 知识库绑定 `knowledge_bases` | ✓ 可以 | UI（选库） | —（人工） | 立即 | `SELECT config->'knowledge_bases' …` |
| MCP 服务勾选 `mcp_services` | ✓ 可以 | UI；发布包内可用 `agent_tools.yaml` 覆盖 | `apply_config.py --apply` | 新会话 | `apply_config.py --check` |
| **工具面 `allowed_tools`** | ✗ **不要**（会被覆盖） | `deploy/weknora-fork/config/agent_tools.yaml` | `apply_config.py --apply` | 新会话（已开会话仍用旧面） | `apply_config.py --check`（应"无漂移"） |
| **提示词 `system_prompt`** | ✗ **不要**（会被覆盖） | `deploy/weknora-fork/config/agent_review_prompt.md`（首个 ```text 块） | 同上 | 新会话 | 同上 + `selfcheck.py` 输出提示词字数 |
| `temperature` / `max_iterations` | ✓ 可以 | UI | —（人工） | 新会话 | 跑一轮看行为 |

> 判据：**改完必须能"重放"**——`apply_config.py --check` 退出码 0 且输出 `✓ 无漂移`。

## 2. 工具契约（`x-ext`：列名/枚举/主键/模式）

| 项 | UI 能改？ | 真源 | 写入 | 生效 | 校验 |
|---|---|---|---|---|---|
| 契约 `schema.json`（`x_ext`） | ✗ | `tools/it-platform-mcp/schema.json`（服务发布内） | 发新包 | `systemctl restart it-platform-mcp` | `curl tools/list` 看 `x-ext.schemaHash` 变化 |
| 页面投射 | ✗ | 工具 `page_metadata.ontology.projection` | `import_refresh` / 同步工具 | 立即 | 巡检 G1/G2 |
| 报告取哪几列 | ✓ **改模板**（不是改契约） | 模板页「数据对象字段」列 | 编辑模板页 | 下次组文 | 巡检 G2（列集与回执不符） |

## 3. 领域口径（任务步骤 / 模板口径节 / DataObject）

| 项 | UI 能改？ | 真源 | 写入 | 生效 | 校验 |
|---|---|---|---|---|---|
| 任务步骤 | ✓（页属性） | 任务页 `skos:note` | `page_attributes_set(patch={"skos:note":…})` | 立即 | `knowledge_lookup(mode="page")` 复核 |
| 模板口径节 | ✓（页正文） | 模板页正文 | 编辑模板页 | 立即 | 同上 |
| DataObject 绑定/指纹 | ✗ | DataObject 页属性（`bpmn:dataObject*`） | `page_attributes_set` | 立即 | 同上 |
| 巡检规则 | ✗ | `tools/ke-core/ke_audit.py` | 发新包 | 重启 bodhi-mcp | `audit_scan` |

## 4. 发布包边界（哪些进包、哪些不进）

| 目录 | 进包？ | 说明 |
|---|---|---|
| `tools/it-platform-mcp/`（含 `schema.json`） | ✓ **只在 04 包** | 02（bodhi-mcp）**排除**：它是独立服务，跟本体服务解耦 |
| `tools/ke-core/ke_ext_*.py` | ✓ 04 包 | 台账/文档/流程/契约投射 |
| `deploy/mock-ext/{allowlist.json,issues/,flows/}` | ✓ 04 包 | **种子**（演示数据只在 `seed-demo.sh`，默认不跑） |
| `deploy/mock-ext/{docs/,files/,images/}` | ✗ **不进包**（git 也不跟） | 二进制/抓取产物；由 `upload_doc.sh` 生成 |
| `tests/fixtures/mock-ext/` | ✗ | 只给单测（合成 2KB docx，无版权风险） |

## 5. 一次发布的最短路径

```bash
# ① 配置（幂等）
python3 tools/delivery/apply_config.py --check     # 先看漂移
python3 tools/delivery/apply_config.py --apply     # 再写
# ② 工具面自检（数量/名字/必需工具齐不齐）
python3 <包>/04-it-platform-mcp/selfcheck.py
# ③ 口径可读性（page 模式确定性）
python3 tools/ke-core/ke_lookup.py --page "bpmn/task/逐项评审"
# ④ 回归（本仓）
python3 tests/run_all.py --fast
```

## 6. 常见漂移与处置

| 症状 | 原因 | 处置 |
|---|---|---|
| 智能体看不到新工具 | `allowed_tools` 少了它（最常被忘） | `apply_config.py --check` → `--apply` → **新开会话** |
| 提示词改了没生效 | 改的是仓库 md，没写入 DB | 同上（md 是真源，DB 是投影） |
| 报告列对不上模板 | 模板「数据对象字段」列与回执 `schema.order` 不符 | 改模板该格（**不改契约**），巡检 G2 会复报 |
| `knowledge_lookup` 回 `kb_unresolved` | `kb_id` 自造缩写（`b1`/`b2`） | 照抄回执 `one_of`/`retry_args` 重试一次 |
