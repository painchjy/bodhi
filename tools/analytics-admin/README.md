# analytics-admin · 自助取数配置管理端

最小可视化配置端（stdlib HTTP，无第三方依赖）：

```bash
python tools/analytics-admin/server.py --host 127.0.0.1 --port 8781
# http://127.0.0.1:8781/
```

Windows 可直接双击 `start-admin.cmd`（自动起服务并打开浏览器）。管理台是常驻进程；
关掉命令窗口/进程后浏览器会打不开（先确认 8781 有监听）。

- 16 张 `analytics` 配置表的浏览/编辑/新建/删除（含 `scenario_fact`，按 `information_schema` 自动取列与主键）；
- 「同步 wiki 页面」调用 `ke_analytics.sync_pages`：Fact / Dimension / Scenario + active MCP 服务页；
- 「映射接口校验」：`GET /api/mapping-interface?kb_id=...` 读取只读视图 `analytics.v_mapping_interface`，并校验页面属性、binding、服务状态、`objectUsesMcpService` 图边；返回 `page_missing / attr_missing / mapping_object_type_mismatch / mapping_object_id_mismatch / service_id_mismatch / binding_missing / service_inactive / object_service_edge_missing` 等问题；
- UI 将内部 `binding.object_type / object_id / service_id` 显示为「映射对象类型 / 映射对象 ID / 映射服务 ID」；
- 维度成员值以 `diag.tool_dimension` / `diag.tool_problem_dimension` 为准；物理映射只在 `binding` + `field_mapping`；
- 默认只监听本机；`BODHI_ANALYTICS_ADMIN_TOKEN` 设置后需 `X-Admin-Token` 或 `?token=`；
- 不暴露给智能体；映射/枚举/join/filter 只在此配置，不进知识库检索；`v_mapping_interface` 只读，不是第二套映射真源。

页面投影与管理端边界详见 `docs/analytics.md`。
