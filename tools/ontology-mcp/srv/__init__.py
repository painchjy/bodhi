"""MCP server 拆分包（P2 ✓）

原则（用户口径 2026-10-22）：
- `server.py` 只留**协议/HTTP/工具注册与分发**（`tool_definitions` / `_call_tool_impl` / `main`）；
- 与领域无关的机制（相似度、页 id 派生、解析…）按**内聚**切成子模块；
- 每个切片保持 `server.py` 里的**名字不变**（`from srv.x import 名字`）⇒ 下游零改动 ✓；
- 每次切片后必须：`py_compile` → 重启 → `active / Result=success / tools=31` → 回归 ✓
"""
