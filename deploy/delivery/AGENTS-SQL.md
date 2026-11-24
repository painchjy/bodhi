# 智能体与 MCP 登记 SQL（可脚本化、可回滚）

> 位置：`/opt/bodhi2/sql/agent_registration/`（由 `tools/delivery/export_db.py --agents` 生成；03 包只放本说明）
> - `mcp_service.sql` —— 登记 `bodhi_ontology`（8765）
> - `agent_diag.sql` —— 登记 `agent_diag`（8771）；替换 `__AGENT_DIAG_URL__`
> - `agents.sql` —— 登记智能体（`bodhi-ea-modeler` / `bodhi-kb-ops`；语义提取智能体按需导入）
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
| `__MCP_URL__` | `http://<宿主机IP>:8765/mcp`（systemd 宿主机，推荐） | 你部署 MCP 时用的地址 |
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
# 一键替换 + 落库（在 02 包根目录）
MCP_URL='http://<宿主机IP>:8765/mcp'
MCP_ID='a7c1f0d2-1b2e-4f3a-9c4d-b0d100000001'
AGENT_DIAG_URL='http://<宿主机IP>:8771/mcp'
PSQL_URL='postgresql://postgres:口令@127.0.0.1:5432/WeKnora'

cd sql/agent_registration
for f in mcp_service.sql agent_diag.sql agents.sql; do
  sed -e "s|__MCP_URL__|$MCP_URL|g" \
      -e "s|__MCP_SERVICE_ID__|$MCP_ID|g" \
      -e "s|__AGENT_DIAG_URL__|$AGENT_DIAG_URL|g" "$f" > "/tmp/$(basename "$f")"
  psql "$PSQL_URL" -v ON_ERROR_STOP=1 -f "/tmp/$(basename "$f")"
done
```

> SQL 的写法是"克隆一个内置智能体的 `config` 再覆盖我们的字段"，所以能自动带上该版本的默认项
> （`agent_mode`、`max_iterations`、`temperature`、`fallback_prompt` 等），你们不用逐字段造。

## 2. 两个智能体各自被赋予什么

| 智能体 | 提示词 | 内置工具（`allowed_tools`）｜MCP 来自 `mcp_services` 挂载 | 备注 |
|---|---|---|---|
| `bodhi-ea-modeler` | 「本体建模与设计（技能驱动）」——按任务页步骤执行；MCP `skills()` 仅迁移期兼容 | 5 个 wiki 工具 + `mcp_bodhi_ontology_*`（含 `ontology_types`/**`retag_preview`+`retag_apply`**/**`context_scan`+`context_lookup`+`context_page`+`context_concept_apply`/`_rollback`**/**`context_authority`+`context_authority_apply`**/**`import_probe`+`import_plan`+`import_apply`+`import_state`+`import_refresh`**/**`audit_purge`**/**`rules_of_policy`+`graph_query`+`reference_lookup`+`review_apply`**/**`image_extract`（文档内嵌图识别）**；**无**抽取类）—— 不要写进 allowed_tools（写了也不生效） | **不给** `wiki_write_page`：写库只走 MCP；改类型必须两段式（preview → 用户确认 → apply）；跨库引用前先 `context_lookup`；**同义知识先认定权威**（`context_authority` → `context_authority_apply{action:"decide"}`），副本只能 `action:"pull"` 从权威复制；**结构化导入**一次只建一个类/一条关系（`plan` 出 ticket → `apply`）；**巡检清理**先 `audit_purge(dry_run=true)` 念清单再执行 |
| `bodhi-kb-ops` | 「知识运维」——体检 + 清理（清理**先 dry_run 念清单**） | 5 个 wiki 工具 + `audit_scan`/`audit_plan`/`retag_preview`/`context_scan`/`context_lookup`/`context_page`/**`context_authority`**/**`audit_purge`**/**`rules_of_policy`+`graph_query`+`reference_lookup`**/**`image_extract`** —— 实际能用到哪些由挂载的服务决定 | `audit_purge` 是**一步硬删**（只要对该库有写权限；用户口径 2026-09-30），所以纪律是：**先 `dry_run=true` 把 `per_kind` 清单念给用户 → 再执行**；结构化导入页/评审页在清理计划里**豁免**（来源记在元数据） |

> **2026-10-27 口径修正（库表实测）**：`allowed_tools` **只管 WeKnora 内置工具**；MCP 工具由
> `config.mcp_services` 挂载决定，**工具清单不落库**、会话时实时 `tools/list` —— 所以**不要**把 `mcp_*`
> 写进 `allowed_tools`（**写了也不生效** ✗，见 `CONFIG-MATRIX.md` §1）。验收口径：内置工具数看
> `jsonb_array_length(config->'allowed_tools')`，**MCP 能不能用看会话 `tools/list`**，不看数组长度。
> 工具级人工确认只在 `mcp_tool_approvals` 有行且 `require_approval=true` 时才拦（新工具默认不拦，不必补记录）。

active 技能（**3 个**）是跨领域能力，按沙箱 bundle 安装：`rule_judge`（规则判定）/
`knowledge_extract`（按 `bmm:knowledgeNeed` 提取本体实例事实）/ `kb_transfer`（导出/导入/比对，运维技能）。
源在 `/opt/bodhi2/skills/<id>/SKILL.md`，bundle 见 `skills/dist/`；安装用
`deploy/weknora-fork/register_native_skills.py --upload`。领域任务步骤负责“做什么”，技能只负责通用算法与输出契约。

> **技能只限「模型」、不限「本体类型」**（用户口径 2026-09-30）：front-matter 只写 `models:`（可加
> `default_model:`），**不写 `scope.classes/relations`**。每个技能的第 0 步先与用户**明确模型范围**：
> 一般**单选模型**；**评审 / 设计可能跨模型 → 单选底层模型**，再按**依赖**把需要的关联本体类型引进来；
> 类/关系/数据属性一律 `ontology_types("<模型>")` 现查（关联线索见回执 `requires`/`affects`）。
> 设计类技能（`ea_overview_design` 企架概要设计、`service_detailed_design` 服务详细设计）**已移除、待重构**。

## 3. 落库后立即验收

```bash
# ① 智能体在位
psql "$PSQL_URL" -At -F' | ' -c "SELECT id, name, deleted_at IS NULL AS live FROM custom_agents WHERE id LIKE 'bodhi-%'"

# ② 内置工具数 + MCP 挂载（MCP 工具清单不落库 ⇒ 数数组没有意义）
psql "$PSQL_URL" -At -c "SELECT id || ' 内置工具=' || jsonb_array_length(COALESCE(config->'allowed_tools','[]'::jsonb)) || ' MCP=' || COALESCE((config->'mcp_services')::text,'[]') FROM custom_agents WHERE id LIKE 'bodhi-%'"

# ②b 脏配置检查：应回 **0 行**（有行 = 旧口径把 mcp_* 写进了 allowed_tools，跑 apply_config.py --apply 清掉）
psql "$PSQL_URL" -At -c "SELECT id FROM custom_agents WHERE EXISTS (SELECT 1 FROM jsonb_array_elements_text(COALESCE(config->'allowed_tools','[]'::jsonb)) t WHERE t LIKE 'mcp\_%')"

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

期望：`custom_agents` 里能看到两个 `bodhi-*` 为 live；② 的内置工具数 = 导出 SQL 里写的那几个（`bodhi-ea-modeler` 5、
`bodhi-kb-ops` 5），②b 回 0 行；**MCP 工具不在库里**，要看就在 App 里新开一个会话，让它 `skills()` / 调一个
`mcp_bodhi_ontology_*` 工具，能调通即挂载成功。App 里用 `bodhi-ea-modeler` 问一句
"先看技能目录再告诉我有哪些技能"，它应调用 `skills()` 并列出 3 个技能；
`logs/mcp_calls_YYYYMMDD.log`（MCP 侧）里能看到对应记录。

## 4. 回滚

```bash
psql "$PSQL_URL" -f sql/ROLLBACK.sql        # 停用智能体（软删，可再改回 deleted_at=NULL）+ 删 MCP 服务行
```
