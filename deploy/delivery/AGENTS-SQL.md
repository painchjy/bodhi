# 智能体与 MCP 登记 SQL（可脚本化、可回滚）

> 位置：`03-manual/sql/`（由 `tools/delivery/export_db.py --agents` 生成）
> - `mcp_service.sql` —— 登记 bodhi2 MCP 服务
> - `agents.sql` —— 登记两个智能体：`bodhi-ea-modeler`（技能驱动：抽取 + 概要设计 + 详细设计）、`bodhi-kb-ops`（只读巡检与清理计划）
> - `ROLLBACK.sql` —— 停用智能体 + 删除 MCP 服务行
>
> **2026-09-22 口径（简化 + 去硬编码）**：
> - 租户（`mcp_services.tenant_id` 与智能体的 `tenant_id`）**固定写 10000**（用户口径；你们按需改）；
> - **模型留空、知识库留空** —— 装完在「平台 → MCP 服务 / 智能体」界面里配；
> - 需要替换的只有一个：**`__MCP_URL__`**（`__MCP_SERVICE_ID__` 默认就是 `a7c1f0d2-…`，两边一致）；
> - SQL 里**不含**我们这套环境的其它 UUID（`export_db.py` 有断言保证）。

## 1. 要先替换的占位符

| 占位符 | 换成 | 从哪拿 |
|---|---|---|
| `__MCP_URL__` | `http://bodhi2-mcp:8765/mcp`（容器 DNS，推荐）或 `http://<宿主机IP>:8765/mcp` | 你部署 MCP 时用的地址 |
| `__MCP_SERVICE_ID__` | MCP 服务行 id（默认就是 `a7c1f0d2-1b2e-4f3a-9c4d-b0d100000001`，两边一致，通常不用动）| 与 `mcp_service.sql` 里的 id 一致 |

> **租户固定 10000**（`mcp_services.tenant_id` 与两个智能体的 `tenant_id` 都写 10000，2026-09-22 用户口径）。
> 你们库里租户不是 10000 时，把两个 SQL 里的 `10000` 一起改掉再执行（改一处漏一处会插不进去）。
> 顺带修掉的两个坑：旧版 `mcp_service.sql` 误取 `tenants.tenant_id`（该列**不存在**，只跑出
> `column t.tenant_id does not exist`）、`transport_type` 写成 `streamable_http`
> —— 现在写死 `10000` + `http-streamable`，实测可在库上跑通。

### 关于 `model_id` 与知识库（为什么留空）

`config.model_id` / `config.knowledge_bases` 都是**每个实例自己的**值（模型 uuid、库 uuid），
写进交付 SQL 必然对不上 —— 所以**留空**，装完在界面里选（也可用下面 SQL 指定模型）。
`agents.sql` 的 `INSERT` 仍会「克隆内置智能体 config 再覆盖我们的字段」，所以上游默认项
（`agent_mode`、`max_iterations` 等）自动带上，你们不用逐字段造。

要显式指定（可选）：

```sql
-- 看你们可用的 chat 模型
SELECT id, name, type FROM models WHERE type = 'KnowledgeQA';
-- 指定给某个智能体（示例 id 换成上一行的结果）
UPDATE custom_agents SET config = config || jsonb_build_object('model_id', '<chat 模型 uuid>')
WHERE id = 'bodhi-ea-modeler';
```

> 同理，`rerank_model_id` / `vlm_model_id` / `asr_model_id` 也在导出时被剥掉（我们环境里是空的，客户库里更不该继承）。
> `export_db.py` 有一道断言：**脱敏后 agent 配置里不允许再出现任何 UUID 字面量**，出现就报错 ——
> 防止我们环境的 id 再被带进交付包。

```bash
# 一键替换 + 落库（只有 URL 是必须填的）
MCP_URL='http://bodhi2-mcp:8765/mcp'
MCP_ID='a7c1f0d2-1b2e-4f3a-9c4d-b0d100000001'
PSQL_URL='postgresql://postgres:口令@127.0.0.1:5432/WeKnora'

for f in sql/mcp_service.sql sql/agents.sql; do
  sed -e "s|__MCP_URL__|$MCP_URL|g" -e "s|__MCP_SERVICE_ID__|$MCP_ID|g" "$f" > "/tmp/$(basename "$f")"
  psql "$PSQL_URL" -v ON_ERROR_STOP=1 -f "/tmp/$(basename "$f")"
done
```

> SQL 的写法是"克隆一个内置智能体的 `config` 再覆盖我们的字段"，所以能自动带上该版本的默认项
> （`agent_mode`、`max_iterations`、`temperature`、`fallback_prompt` 等），你们不用逐字段造。

## 2. 两个智能体各自被赋予什么

| 智能体 | 提示词 | 工具面（`allowed_tools`） | 备注 |
|---|---|---|---|
| `bodhi-ea-modeler` | 「本体建模与设计（技能驱动）」——先 `skills()` 看目录，再取技能全文照做 | 5 个 wiki 工具 + 15 个 `mcp_bodhi_ontology_*`（含 `skills`/`ontology_types`/`service_overview`/**`retag_preview`+`retag_apply`**；**无**抽取类） | **不给** `wiki_write_page`：写库只走 MCP；改类型必须两段式（preview → 用户确认 → apply） |
| `bodhi-kb-ops` | 「知识运维」——只做体检与清理计划，绝不改数据 | 5 个 wiki 工具 + `audit_scan`/`audit_plan` | 执行清理始终由人确认（`plan → apply --confirm`）|

技能（3 个）由 MCP 下发、**不写进提示词**：`domain_modeling` / `ea_overview_design` / `service_detailed_design`
（源在 `02-mcp-server/skills/<id>/SKILL.md`，改完即生效，无需重启/重新注册智能体）。

## 3. 落库后立即验收

```bash
# ① 智能体在位
psql "$PSQL_URL" -At -F' | ' -c "SELECT id, name, deleted_at IS NULL AS live FROM custom_agents WHERE id LIKE 'bodhi-%'"

# ② 工具面 18 个（5 wiki + 13 MCP）、MCP 已挂
psql "$PSQL_URL" -At -c "SELECT jsonb_array_length(config->'allowed_tools') || ' 工具 / MCP=' || (config->'mcp_services')::text FROM custom_agents WHERE id='bodhi-ea-modeler'"

# ③ 真跑一轮（让智能体先 skills() 再报目录）——用交付里的驱动脚本或 App 里直接对话
python3 - <<'PY'
import json, subprocess, urllib.request
tok = subprocess.run(['docker','exec','WeKnora-postgres','psql','-U','postgres','-d','WeKnora','-At','-c',
  "SELECT token FROM auth_tokens WHERE is_revoked=false AND token_type='access_token' AND expires_at>now() ORDER BY created_at DESC LIMIT 1"],
  capture_output=True, text=True).stdout.strip()
req = urllib.request.Request('http://127.0.0.1:8080/api/v1/agents', headers={'Authorization':'Bearer '+tok})
print([a['id'] for a in json.loads(urllib.request.urlopen(req, timeout=30).read())['data']])
PY
```

期望：`custom_agents` 里能看到两个 `bodhi-*` 为 live；工具数 **15**；App 里用 `bodhi-ea-modeler` 问一句
"先看技能目录再告诉我有哪些技能"，它应调用 `skills()` 并列出 3 个技能；
`logs/mcp_calls_YYYYMMDD.log`（MCP 侧）里能看到对应记录。

## 4. 回滚

```bash
psql "$PSQL_URL" -f sql/ROLLBACK.sql        # 停用智能体（软删，可再改回 deleted_at=NULL）+ 删 MCP 服务行
```
