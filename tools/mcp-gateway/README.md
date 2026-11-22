# MCP Gateway（notifications 补丁 + 固定参数注入）

> 独立部署的小网关，放在 WeKnora 与内网 MCP 服务之间。
> **只补 `notifications/*` 这一处协议差异，其他请求原样转发**；`tools/call` 可注入固定参数。
> 不需要改 WeKnora Go 代码，不需要重建 `WeKnora-app`，也不需要改内网 MCP 服务。

## 1. 为什么需要

部分内网 MCP 服务没有实现 `notifications/initialized`，收到后返回：

```json
{"jsonrpc":"2.0","id":null,"error":{"code":-32000,"message":"unknown method"}}
```

WeKnora 的 mcp-go 在 `Initialize()` 里发完 `initialize` 后会发该通知，收到错误就把整个 MCP 客户端初始化判失败，日志表现为：

```text
Failed to create MCP client for service <name>: ... failed to initialize
```

网关把该通知**本地吃掉**，返回标准响应，WeKnora 就能继续 `tools/list` / `tools/call`。

## 2. 行为

| 客户端请求 | 网关行为 |
|---|---|
| `initialize` | `local_initialize=true`（默认）→ **本地返回标准 initialize**，不转发；`false` → 原样转发 |
| `notifications/initialized` | **本地 202 + 空 body**，不转发上游 |
| 其他 `notifications/*` | 默认同样 202 + 空 body |
| `tools/list` | 解析上游响应并**重打包成标准 JSON**（id 用 WeKnora 原 id；`inputSchema` sanitize；`x-ext` 有就保留、没有就不加） |
| `tools/call` | 合并 `inject_args` 后转发（固定参数优先）；SSE 补 `event: message` |
| 其他方法 | 原样转发 |

> 为什么要本地 `initialize`：内网服务的 initialize 可能返回非标准 result、缺少
> `event: message`、或只接受某个协议版本；而它本身只是连通性测试、没有会话/验证。
> 网关本地返回标准结果后，WeKnora 的 mcp-go 一定初始化成功，`tools/list` / `tools/call`
> 仍然转发到真实服务。

`notifications/*` 的响应：

```http
HTTP/1.1 202 Accepted
Content-Type: application/json
Content-Length: 0

（空 body）
```

JSON-RPC 通知按规范**不带 `id`、不返回 JSON-RPC body**；不要返回 `-32000`。

## 3. 目录内容

```text
server.py                  HTTP 服务
gateway.py                 纯逻辑（通知识别 / 参数注入 / schema 兜底 / JSON-SSE）
gateway.json.example       配置模板（不含真实值）
bodhi-mcp-gateway.service  systemd 单元
install.sh                 安装脚本
VERSION                    网关版本
```

## 4. 安装

```bash
tar -xzf bodhi2-mcp-gateway-<version>.tar.gz -C /opt
cd /opt/mcp-gateway
sudo bash install.sh
```

`install.sh` 会：

1. 复制到 `/opt/bodhi-mcp-gateway`；
2. 首次安装生成 `/etc/bodhi-mcp-gateway.json`（权限 600）；
3. 安装并启动 `bodhi-mcp-gateway.service`；
4. 检查 `http://127.0.0.1:8771/health`。

编辑配置后重启：

```bash
sudo vi /etc/bodhi-mcp-gateway.json
sudo systemctl restart bodhi-mcp-gateway
curl -s http://127.0.0.1:8771/health
```

## 5. 配置

`/etc/bodhi-mcp-gateway.json`：

```json
{
  "host": "0.0.0.0",
  "port": 8771,
  "upstream_url": "http://dbbiuat.xxx.xxx.xxx:8080/mcp",
  "timeout_seconds": 30,
  "intercept_notifications": true,
  "local_initialize": true,
  "protocol_version": "2024-11-05",
  "server_info": {
    "name": "mcp-gateway",
    "version": "1.0.0"
  },
  "instructions": "",
  "normalize_sse": true,
  "canonicalize_tools": true,
  "fill_schema": false,
  "inject_args": {
    "_loginName": "<按上游要求填>",
    "_tenant": "<按上游要求填>"
  },
  "inject_tools": ["*"],
  "upstream_headers": {}
}
```

说明：

- `upstream_url`：内网 MCP 服务的真实 `/mcp` 地址（必须是 http/https，**端口必须是数字**，不能写 `PORT`）；
- `local_initialize`：默认 **true**。网关本地返回标准 `initialize`，不调用上游；dbbi 的 initialize 只是连通性测试，建议保持 true；
- `protocol_version`：本地 initialize 返回的协议版本，默认 `2024-11-05`（WeKnora v0.8.0 实测接受）；
- `server_info`：本地 initialize 返回的 `serverInfo`；
- `instructions`：可选。dbbi 的 initialize 如果带 `instructions`，把原文填到这里；网关会放进本地 initialize 结果，WeKnora 会把它作为服务级说明。留空则不返回该字段；
- `normalize_sse`：默认 **true**。上游 SSE 只有 `data:`、没有 `event: message` 时，网关重发为标准 SSE；`tools/call` 需要；
- `canonicalize_tools`：默认 **true**。`tools/list` 解析上游（JSON/SSE/`result.tools`/`data.tools` 等外层），用 WeKnora 原 id 重打包成纯 JSON；`inputSchema` 统一 sanitize；`x-ext` 有就保留、没有就不加。动态结果集工具不需要 `x-ext`；
- `inject_args`：`tools/call` 要固定的参数；**固定值优先于模型传入值**；
- `inject_tools`：`["*"]` 表示全部工具注入；也可以只列需要取数的工具名；
- `fill_schema`：默认 **false**。`canonicalize_tools=true` 时规范器已经保证 `inputSchema` 合法（缺失/非法统一补最小 schema）；此开关只对 `canonicalize_tools=false` 的旧路径有意义。
- `upstream_headers`：上游需要的额外请求头（如认证头）；不要在这里写无意义的头。
- 配置文件含账号信息，保持 `chmod 600`，不要提交仓库/打进包。

环境变量可覆盖非敏感项（`MCP_GATEWAY_PORT` / `MCP_GATEWAY_UPSTREAM_URL` 等）；固定参数建议只放 JSON 文件。

## 6. WeKnora 侧登记

把现有服务的 URL 指到网关（其余字段不变）：

```sql
UPDATE mcp_services
SET url = 'http://host.docker.internal:8771/mcp',
    transport_type = 'http-streamable',
    enabled = true,
    deleted_at = NULL,
    updated_at = now()
WHERE name = 'dbbi_data';
```

- `name` 不变 ⇒ 智能体看到的工具前缀仍是 `mcp_dbbi_data_*`；
- `host.docker.internal` 已在 SSRF 白名单（其他本机 MCP 服务同款）；
- 如果容器解析不了该域名，改用宿主机 IP，并把 IP 加进 WeKnora 的 `SSRF_WHITELIST_EXTRA`；
- 改完新开智能体会话验证。

## 7. 验证

### 7.1 网关健康

```bash
curl -s http://127.0.0.1:8771/health | python3 -m json.tool
```

应返回 `ok=true`，且 `inject_keys` 里能看到配置的固定参数名（不显示值）。

### 7.2 MCP 三步

```bash
MCP='http://127.0.0.1:8771/mcp'

# 1) initialize
curl -sS -X POST "$MCP" \
  -H 'Content-Type: application/json' \
  -H 'Accept: application/json, text/event-stream' \
  --data-binary '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"WeKnora","version":"1.0.0"}}}'

# 2) notifications/initialized（期望 202 空 body，网关日志显示 local）
curl -sS -i -X POST "$MCP" \
  -H 'Content-Type: application/json' \
  -H 'Accept: application/json, text/event-stream' \
  --data-binary '{"jsonrpc":"2.0","method":"notifications/initialized"}'

# 3) tools/list
curl -sS -X POST "$MCP" \
  -H 'Content-Type: application/json' \
  -H 'Accept: application/json, text/event-stream' \
  --data-binary '{"jsonrpc":"2.0","id":2,"method":"tools/list","params":{}}'
```

### 7.3 WeKnora 自己的客户端

用内网 token：

```bash
TOKEN='...'
curl -s -X POST -H "Authorization: Bearer $TOKEN" \
  'http://<app>:8080/api/v1/mcp-services/<dbbi_id>/test' | python3 -m json.tool
```

`success=true` 且 `tools` 非空即可；新会话 `tools_ready` 应出现 `mcp_dbbi_data_*`。

### 7.4 固定参数

网关日志会打印：

```text
forward tools/call/<tool> status=200 123ms inject=_loginName,_tenant
```

只打印 key，不打印值。上游 `tools/call` 的返回就是真实取数结果。

## 8. 回滚

```sql
UPDATE mcp_services
SET url = 'http://dbbiuat.xxx.xxx.xxx:<port>/mcp',
    updated_at = now()
WHERE name = 'dbbi_data';
```

停用网关：

```bash
sudo systemctl disable --now bodhi-mcp-gateway
```

WeKnora 无需改代码/重建容器。

## 9. 日志

```bash
journalctl -u bodhi-mcp-gateway -f
```

关键日志：

- `notification notifications/initialized -> 202 (local)`：通知已本地吃掉；
- `forward tools/list status=200 ...`：列表转发成功；
- `forward tools/call/<tool> ... inject=...`：调用了哪个工具、注入了哪些 key；
- `tools/list filled schema=N`：`fill_schema=true` 时补了多少个 schema。

## 10. 打包

```bash
python3 tools/delivery/pack_gateway.py --version <YYYYMMDD>
```

生成独立包：

```text
dist-delivery/bodhi2-mcp-gateway-<version>.tar.gz
dist-delivery/bodhi2-mcp-gateway-<version>/SHA256SUMS
```

包内不含任何真实地址/账号；`gateway.json.example` 只有占位符。
