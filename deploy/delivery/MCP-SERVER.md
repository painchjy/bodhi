# MCP 服务部署（bodhi2 ontology-mcp）

> 交付物：`02-mcp-server/bodhi2-mcp.tar.gz`（源码包，**零第三方依赖**：只用 Python 标准库 + `psql` 客户端）
> 作用：给 WeKnora 提供 **10 个 MCP 工具**（抽取落库 / 设计落库 / 巡检 / 技能目录 / 总览页 …）
> 依赖：Python ≥ 3.10、`postgresql-client`（提供 `psql`）、可读 WeKnora 的 Postgres；Neo4j **可选**。

---

## 0. 它到底怎么工作（先理解，再部署）

```
WeKnora-app ──(MCP over HTTP, POST /mcp)──► bodhi2-mcp (:8765)
     │                                            │
     │ allowed_tools 里带 mcp_bodhi_ontology_*    ├─ psql ──► WeKnora Postgres（wiki_pages / knowledges / …）
     └─ 读知识库片段（WeKnora 原生工具）           └─（可选）HTTP ──► Neo4j（本体投影；本交付不需要）
```

- MCP 服务**直接读写 Postgres**（`wiki_pages` 等表），不调 WeKnora 私有 API → 对上游版本升级不敏感；
- 它**不调 LLM**：抽取/设计由 WeKnora 侧的智能体做，MCP 只负责"规范化落库 + 本体校验 + 巡检 + 技能下发"；
- 技能（`skills/<id>/SKILL.md`）也由它提供：`skills()` 返回目录、`skills(skill="…")` 返回全文 + 本体面。

## 1. 目录布局（必须保持这个形状）

`server.py` 用 `Path(__file__).parents[2]` 定位"仓库根"，所以包内结构必须原样：

```
/app/                          ← 容器里的"仓库根"
├── tools/ontology-mcp/        ← server.py / refresh_design.py / mdview.py / graph_page.py
├── tools/ke-core/             ← ke_db / ke_pages / ke_ontology / ke_audit / ke_docs / ke_admin / ke_neo4j
├── tools/ontology-extract/    ← ontology_wiki.py（本体知识库投影；server 会 import）
├── skills/<id>/SKILL.md       ← 技能库（单一来源）
├── artifacts/weknora/         ← ontology_index.json（类型/关系枚举、颜色、图例）
├── ontology/                  ← TTL 源（编译产出用；服务运行时不强制）
├── logs/                      ← 工具调用日志 mcp_calls_YYYYMMDD.log（**容器里必须可写**）
└── .env                       ← 可选（配置见下；`engine.load_env` 会读它）
```

> 这些都在 `bodhi2-mcp.tar.gz` 里；解包即得到上述结构：
> `sudo mkdir -p /opt/bodhi2 && sudo tar -xzf bodhi2-mcp.tar.gz -C /opt/bodhi2`

## 2. 环境变量（唯一配置面）

| 变量 | 默认 | 说明 |
|---|---|---|
| `BODHI_DB_HOST` | 空 | **设了就直连 TCP**（容器/远端部署推荐）；不设则 `docker exec <容器> psql`（本机开发）|
| `BODHI_DB_PORT` | `5432` | |
| `BODHI_DB_USER` | `postgres` | |
| `BODHI_DB_PASSWORD` | `postgres123!@#` | **改掉** |
| `BODHI_DB_NAME` | `WeKnora` | |
| `BODHI_DB_CONTAINER` | `WeKnora-postgres` | 仅 docker-exec 模式用 |
| `BODHI_NEO4J_HTTP` | `http://127.0.0.1:7474` | **可选**；不部署 Neo4j 就保持默认 |
| `NEO4J_USERNAME` / `NEO4J_PASSWORD` | `neo4j` / `password` | 同上（仅投影类巡检项用到）|

`.env.example` 已给出容器部署的推荐值（`BODHI_DB_HOST=postgres`）。

## 3. 部署方式 A：容器（推荐；与 WeKnora 同一 compose 网络）

```bash
sudo mkdir -p /opt/bodhi2 && sudo tar -xzf bodhi2-mcp.tar.gz -C /opt/bodhi2
cd /opt/bodhi2/02-mcp-server
docker build -t bodhi2-mcp:1.0 .        # 包内 Dockerfile：python:3.12-slim + postgresql-client
cp .env.example .env && vi .env         # BODHI_DB_HOST=postgres / BODHI_DB_PASSWORD=…
docker compose -f docker-compose.mcp.yml up -d
docker logs -f bodhi2-mcp               # 应看到 0.0.0.0:8765
```

`docker-compose.mcp.yml`（包内已给，要点三行）：

```yaml
services:
  bodhi-mcp:
    image: bodhi2-mcp:1.0
    container_name: bodhi2-mcp
    env_file: [.env]
    networks: [weknora-network]      # ← 与 WeKnora-app 同一网络（名字按你们 compose 实际）
    restart: unless-stopped
    ports: ["8765:8765"]             # 可选，便于本机 curl 自检
```

> **MCP URL 用容器内 DNS（`http://bodhi-mcp:8765/mcp`）可天然绕开 SSRF 白名单问题**（见 §6）。

## 4. 部署方式 B：裸机（systemd）

```bash
sudo tar -xzf bodhi2-mcp.tar.gz -C /opt/bodhi2
sudo cp /opt/bodhi2/02-mcp-server/bodhi2-mcp.service /etc/systemd/system/
sudo vi /etc/systemd/system/bodhi2-mcp.service     # 改 WorkingDirectory / Environment
sudo systemctl daemon-reload && sudo systemctl enable --now bodhi2-mcp
journalctl -u bodhi2-mcp -f
```

`bodhi2-mcp.service` 要点：

```ini
[Service]
WorkingDirectory=/opt/bodhi2
Environment=BODHI_DB_HOST=127.0.0.1
Environment=BODHI_DB_PASSWORD=你的口令
ExecStart=/usr/bin/python3 /opt/bodhi2/tools/ontology-mcp/server.py --host 0.0.0.0 --port 8765
```

> 裸机 `BODHI_DB_HOST=127.0.0.1` 需要 Postgres 允许 TCP（不是只监听容器网络）。

## 5. 自检（三步全绿才算通）

```bash
# 1) 进程/端口
curl -s -o /dev/null -w 'bodhi/version %{http_code}\n' http://127.0.0.1:8765/bodhi/version
# 2) 数据库连通（把 BODHI_DB_HOST 换成实际值）
cd /opt/bodhi2 && BODHI_DB_HOST=127.0.0.1 python3 - <<'PY'
import sys; sys.path.insert(0, 'tools/ke-core'); import ke_db
print('知识库：', [r['name'] for r in ke_db.psql_csv(
    "SELECT name FROM knowledge_bases WHERE deleted_at IS NULL ORDER BY created_at")])
PY
# 3) MCP 协议自检（tools/list 必须 10 个工具 + skills() 返回 3 个技能）
python3 /opt/bodhi2/02-mcp-server/selfcheck.py --url http://127.0.0.1:8765/mcp
```

`selfcheck.py` 期望输出：

```
initialize  OK（session=…）
tools/list  OK（10 个）：extract_and_save, extract_status, list_pending_merges, resolve_pending_merge,
                        ontology_types, skills, service_overview, audit_scan, audit_plan, save_knowledge
skills()    OK（3 个：domain_modeling / ea_overview_design / service_detailed_design）
```

## 6. 在 WeKnora 里注册这个 MCP 服务

**UI**：平台 → MCP 服务 → 新建 → 传输 `streamable-http` → URL `http://bodhi-mcp:8765/mcp`。

**SQL**（可脚本化；`04-manual/AGENTS-SQL.md` 里有配套的智能体注册）：

```sql
INSERT INTO mcp_services (id, tenant_id, name, description, enabled, transport_type, url, created_at, updated_at)
SELECT 'a7c1f0d2-1b2e-4f3a-9c4d-b0d100000001', t.tenant_id, 'bodhi_ontology',
       '本体知识保存工具（抽取/设计落库、巡检、技能）', true, 'streamable_http',
       'http://bodhi-mcp:8765/mcp', now(), now()
FROM tenants t ORDER BY t.id LIMIT 1
ON CONFLICT (id) DO UPDATE SET url = EXCLUDED.url, enabled = true, updated_at = now();
```

> ⚠️ 若 MCP URL 必须写 `http://<宿主机IP>:8765/mcp`（跨主机），WeKnora 会做 **SSRF 校验**：
> 私网地址 / `host.docker.internal` 会被拒（app 日志：`MCP service URL failed SSRF validation`），
> 现象是**智能体的 MCP 工具全部消失**（tool_count 从 15 掉到 5，只剩 wiki 工具）。
> 修法：WeKnora 的 `.env` 里给 `SSRF_WHITELIST_EXTRA` 加上该主机名 / IP / CIDR，然后重建 app。
> 详见 `04-manual/TROUBLESHOOTING.md` §1。

## 7. 升级 / 回滚

```bash
# 升级
tar -xzf bodhi2-mcp-<新版>.tar.gz -C /opt/bodhi2 && cd /opt/bodhi2/02-mcp-server
docker build -t bodhi2-mcp:<新版> . && docker compose -f docker-compose.mcp.yml up -d
# 回滚：把 current 指向旧 tag（数据都在 Postgres，服务本身无状态）
docker tag bodhi2-mcp:<旧版> bodhi2-mcp:current && docker compose -f docker-compose.mcp.yml up -d
```

## 8. 日常观测

- **工具调用日志**：`logs/mcp_calls_YYYYMMDD.log` —— 一行一次调用（时间 / 工具 / 耗时 ms / 入参摘要 / 结果摘要）。
  智能体说"我调了 X 工具"，先在这里核对是否真调了、参数对不对；
- 容器日志：`docker logs bodhi2-mcp --tail 100`（handler 异常会有 `[mcp] … 失败`）；
- 只读自检接口（同一端口）：
  `GET /bodhi/overview?kb_id=<kb>`（服务详设总览）、`GET /bodhi/crud?kb_id=&slug=`（CRUD 矩阵）、
  `GET /bodhi/audit?kb_id=`（一致性巡检）、`GET /bodhi/relations?kb_id=&slug=`（出边/入边）。
