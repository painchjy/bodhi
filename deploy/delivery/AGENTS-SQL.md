# 智能体与 MCP 登记 SQL（可脚本化、可回滚）

> 位置：`04-manual/sql/`（由 `tools/delivery/export_db.py --agents` 生成）
> - `mcp_service.sql` —— 登记 bodhi2 MCP 服务
> - `agents.sql` —— 登记两个智能体：`bodhi-ea-modeler`（技能驱动：抽取 + 概要设计 + 详细设计）、`bodhi-kb-ops`（只读巡检与清理计划）
> - `ROLLBACK.sql` —— 停用智能体 + 删除 MCP 服务行

## 1. 要先替换三个占位符

| 占位符 | 换成 | 从哪拿 |
|---|---|---|
| `__MCP_URL__` | `http://bodhi2-mcp:8765/mcp`（容器 DNS，推荐）或 `http://<宿主机IP>:8765/mcp` | 你部署 MCP 时用的地址 |
| `__BIZ_KB_ID__` | 业务知识库 uuid | `SELECT id,name FROM knowledge_bases WHERE deleted_at IS NULL;` |
| `__ONTOLOGY_KB_ID__` | 本体模型知识库 uuid | 同上 |
| `__MCP_SERVICE_ID__` | MCP 服务行 id（默认就是 `a7c1f0d2-1b2e-4f3a-9c4d-b0d100000001`）| 与 `mcp_service.sql` 里的 id 一致 |

```bash
# 一键替换 + 落库（把四个变量填好）
MCP_URL='http://bodhi2-mcp:8765/mcp'
BIZ_KB='<业务库 uuid>'; ONT_KB='<本体模型库 uuid>'; MCP_ID='a7c1f0d2-1b2e-4f3a-9c4d-b0d100000001'
PSQL_URL='postgresql://postgres:口令@127.0.0.1:5432/WeKnora'

for f in sql/mcp_service.sql sql/agents.sql; do
  sed -e "s|__MCP_URL__|$MCP_URL|g" -e "s|__BIZ_KB_ID__|$BIZ_KB|g" \
      -e "s|__ONTOLOGY_KB_ID__|$ONT_KB|g" -e "s|__MCP_SERVICE_ID__|$MCP_ID|g" "$f" > "/tmp/$(basename "$f")"
  psql "$PSQL_URL" -v ON_ERROR_STOP=1 -f "/tmp/$(basename "$f")"
done
```

> SQL 的写法是"克隆一个内置智能体的 `config` 再覆盖我们的字段"，所以能自动带上该版本的默认项
> （`agent_mode`、`max_iterations`、`temperature`、`fallback_prompt` 等），你们不用逐字段造。

## 2. 两个智能体各自被赋予什么

| 智能体 | 提示词 | 工具面（`allowed_tools`） | 备注 |
|---|---|---|---|
| `bodhi-ea-modeler` | 「本体建模与设计（技能驱动）」——先 `skills()` 看目录，再取技能全文照做 | 5 个 wiki 工具 + 10 个 `mcp_bodhi_ontology_*` | **不给** `wiki_write_page`：写库只走 MCP |
| `bodhi-kb-ops` | 「知识运维」——只做体检与清理计划，绝不改数据 | 5 个 wiki 工具 + `audit_scan`/`audit_plan` | 执行清理始终由人确认（`plan → apply --confirm`）|

技能（3 个）由 MCP 下发、**不写进提示词**：`domain_modeling` / `ea_overview_design` / `service_detailed_design`
（源在 `04-manual/skills/<id>/SKILL.md`，改完即生效，无需重启/重新注册智能体）。

## 3. 落库后立即验收

```bash
# ① 智能体在位
psql "$PSQL_URL" -At -F' | ' -c "SELECT id, name, deleted_at IS NULL AS live FROM custom_agents WHERE id LIKE 'bodhi-%'"

# ② 工具面 15 个、MCP 已挂
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
