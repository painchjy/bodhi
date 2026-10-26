# it-platform-mcp · 内网工作台 MCP 服务（参考实现 / 打样）

> **定位**：这些接口的**正本在内网 IT 工作台**，所以它是 **WeKnora 里与 `bodhi_ontology` 并列的第二个
> MCP 服务**（不是 bodhi-mcp 的一部分）。bodhi-mcp 只管本体知识；本服务只管"外部系统的库表"。

## 1. 启动

```bash
python3 tools/it-platform-mcp/server.py --host 0.0.0.0 --port 8770
# systemd: bodhi-it-mcp.service（EnvironmentFile 复用仓库 .env）
curl -s http://127.0.0.1:8770/health     # {"ok": true, "server": "it-platform-mcp", "tools": 3}
```

协议：MCP Streamable HTTP（JSON-RPC 2.0，`POST /mcp`）。
**按 `Accept` 协商**：含 `text/event-stream` 回 SSE（WeKnora 走这条），否则回纯 JSON（脚本/自检）。

## 2. WeKnora 侧注册（与 bodhi 并列）

`mcp_services` 加一行：

| 列 | 值 |
|---|---|
| `id` | `a7c1f0d2-1b2e-4f3a-9c4d-b0d100000002` |
| `name` | `it_platform` ← **工具前缀来源**：智能体看到 `mcp_it_platform_<tool>` |
| `transport_type` | `http-streamable` |
| `url` | `http://host.docker.internal:8770/mcp` |
| `enabled` | `true` |

`custom_agents.config`：
- `mcp_services` 里**同时**保留 bodhi 与 it_platform 两个 id（`mcp_selection_mode=all|selected` 都别只剩一个）；
- `allowed_tools` 追加 **4** 个：`mcp_it_platform_ext_catalog_query` / `_ext_issue_list` /
  `_ext_doc_access` / `_ext_review_flow`。

## 3. 工具与绑定

| 服务内工具名 | 智能体看到的名字 | 用途 |
|---|---|---|
| `ext_catalog_query` | `mcp_it_platform_ext_catalog_query` | 白名单/黑名单查询（版本**逐字**；也支持 `versionRange` 允许范围与 `expires_at` 有效期，黑名单优先） |
| `ext_issue_list` | `mcp_it_platform_ext_issue_list` | 条目清单查询/维护（含 `expert_feedback`；`columns=` 取模板子集；**`items=[…]` 批量写入 ⇒ `rev` 只 +1**） |
| `ext_doc_access` | `mcp_it_platform_ext_doc_access` | 文档/附件只读；`action=url` 给**可下载地址**；`image`+`inline_media` 直出 `data_url` |
| `ext_review_flow` | `mcp_it_platform_ext_review_flow` | **流程上下文**：`get/docs/rounds/open/create/attach`（编号不存在⇒**直接建档**）+ `maintain`；文档清单带 `url` |

> 只给「**流程编号 + 文件名称**」即可开工（`ext_review_flow(action=open)` 建档并按名称绑文档）
> ⇒ 不必把方案文档上传进知识库 ✓。附件下载走 `GET /files/<doc_id>`（流式 + `Content-Disposition`），
> 基地址由 `IT_MCP_PUBLIC_BASE`（默认 `http://127.0.0.1:8770`）决定，内网换成域名/IP 即可。

> **内网工作台没接通时怎么"上传文档"**（2026-10-26）：
> ① `POST /files/<doc_id>?review_no=<编号>&name=<文件名>&title=<标题>&round=1`，body = 文件二进制
> （docx 会用标准库切节）→ 回 `doc_id/size/sections/url`；
> ② `ext_review_flow(action="attach", review_no=<编号>, doc_id=<doc_id>)` 把文档绑到该编号；
> ③ 一条命令搞定：`bash deploy/mock-ext/upload_doc.sh <编号> <本地文件> [标题]`。
> 新建流程：`ext_review_flow(action="create", review_no=<新编号>, title=…)`。

> **台账指纹与版本口径**（2026-10-26）：`rule_fp` 由**调用方显式给**（= 规则页 `bmm:ruleHash`，16 hex）；
> 工具只在缺省时才自算 `r-xxxxxx` 短哈希（`human_input=true` 的人工条目留空）。
> `rev` = **该编号台账的写入版本**（一次批量写 +1，不是业务轮次）。

> **版本指纹**：`create/update` 时工具按 `hash(规则 + 文档位置 + 判定 + 原文摘录)` 自动生成 `r-xxxxxx`
> （人工提交的条目带 `human_input=true` 才留空）—— 调用方**不要自己拼**，也不要写"未取得（待澄清）"。

> **字段子集**：`ext_issue_list` 的 `columns=`（逗号分隔，顺序即展示顺序）用于\"**某一份报告只取其中几列**\"——
> 服务端字段集 ⊋ 报告字段集，取哪些由**报告模板**配置；非法字段回 `bad_columns` + `allowed`（不静默丢列 ✗）。

绑定写在**业务对象页的 4 个数据属性**（`bpmn:DataObject`，内网同样适用）：

```
bpmn:dataObjectInterface      ext_catalog_query[,ext_issue_list,…]     # 逗号分隔
bpmn:dataObjectAccessMode     query,maintain[;query]                   # 分号对齐接口顺序
bpmn:dataObjectKeyFields      software,version[;review_no,issue_no]
bpmn:dataObjectInterfaceRef   it_platform:tools/ke-core/ke_ext_catalog.py
```

## 3.1 契约下发：`tools/list` 的 `x-ext`（2026-10-25）

列的**名称/顺序/枚举/主键/模式/版本策略**只在这里定义一次（`deploy/mock-ext/it-platform/schema.json` 的 `x_ext` 段），
由 `tools/list` 随工具一起下发；**页面只做投射**（`page_metadata.ontology.projection`，带 `fetched_at` + `schemaHash`），
不再手写列规范 ⇒ 单一真源、可判漂移。

```bash
curl -s -X POST http://127.0.0.1:8770/mcp -H 'content-type: application/json' \
     -H 'Accept: application/json' \
     -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}' \
  | python3 -c 'import json,sys; t=json.load(sys.stdin)["result"]["tools"]; print([(x["name"], len(x.get("x-ext",{}).get("columns",[])), x.get("x-ext",{}).get("schemaHash")) for x in t])'
# 期望：ext_issue_list 11 列 + sha256:xxxxxxxxxxxxxxxx
```

访问日志现在带 JSON-RPC 方法，能区分"只是列工具"还是"真的调用了"：

```
2026-10-25 19:46:11 POST /mcp -> 200  rpc=tools/list   accept='application/json' ua='Python-urllib/3.14'
2026-10-25 19:46:11 POST /mcp -> 200  rpc=tools/call/ext_issue_list  accept='application/json' ua='Python-urllib/3.14'
```


## 4. 内网落地（只改存储层）

1. 保留 `server.py`（协议层）与 `schemas.py`（工具面）不变；
2. 把 `tools/ke-core/ke_ext_store.py` 的"容器文件"换成**你们库表的读写**（SQL/ORM 均可）：
   - 字段与幂等键照上面 4 个属性；
   - 写操作保持 `dry_run` 默认 true + 审计 + 版本号（`rev`）语义；
3. `ke_ext_catalog.py` / `ke_ext_issues.py` / `ke_ext_docs.py` 里的口径（黑名单优先、分页、图转 `data_url`）
   可直接复用。

> 本目录下的 `ke_ext_*` 只做**逻辑参考**：真源仍在 `tools/ke-core/`（bodhi 与本服务共用同一套实现，
> 这样"打样"与"正式"只有存储层差异）。接入正式库表后可把 `ke_ext_*` 复制进本目录再改。

## 5. 与 bodhi-mcp 的边界

- bodhi-mcp（:8765，37 个工具）：本体/知识/检索/落库；
- it-platform-mcp（:8770，3 个工具）：外部系统库表（清单、条目、文档附件）；
- **两边不互相调用**；领域库里的 DataObject 通过 4 个数据属性指向本服务的工具。

## 6. 排障（必读：WeKnora 页面上的 `404 page not found` 不是本服务的问题）

WeKnora「设置 → MCP 服务 → 重新拉取」报 `404 page not found` 时，**先看 app 日志**：

```bash
docker logs WeKnora-app 2>&1 | grep -E 'mcp-services/.*/metadata'
# GET  /api/v1/mcp-services/<id>/metadata          404   ← 前端要的路由，app 没有
# POST /api/v1/mcp-services/<id>/metadata/refresh  404
```

- 该按钮调的是**新版前端**的 `/metadata(/refresh)`，当前 app 镜像里不存在 ⇒ 必 404，
  **与 MCP 服务无关**（请求根本没发到 :8770）；`GET /api/v1/mcp-services` 返回 200 就说明配置已生效。
- **不影响智能体**：app 在会话运行时会自己向 MCP 服务 `tools/list` 拉工具。
- 看谁真的打进来了：

```bash
tail -f /var/log/bodhi-it-mcp.log      # 每行: 时间 方法 路径 -> 状态 accept=... ua=...
# IT_MCP_QUIET=1 可关（放 .env）
```

- 手工验协议（不依赖任何前端）：

```bash
curl -s http://127.0.0.1:8770/health
curl -s -X POST http://127.0.0.1:8770/mcp -H 'content-type: application/json' \
     -H 'Accept: application/json' -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}'
```
