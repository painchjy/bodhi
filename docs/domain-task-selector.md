# domain：任务选择视图（2026-11-18）

`knowledge_lookup(mode="domain")` 是**任务选择器**，不是任务页浏览器。

## 输出

```text
processes[]
  slug/title
  activities[]
    slug/title
    tasks[]
      slug/title
      activity_id
      goal                  # bpmn:taskGoal
      io.entry / io.exit    # bpmn:activityEntry / bpmn:activityExit
      roles[]               # bpmn:taskHasRole -> bpmn:Role
```

- 活动/流程信息只在所属层级出现一次；
- 任务不返回 `content` / `content_md` / `skos:note` 正文；
- `entry/exit` 单字段超 1200 字时截断并标记；
- 没有活动/角色的任务进入 `unassigned` 或 `role_missing`，不静默丢失；
- `selection.max_task_pages = 1`，智能体选一个任务后才用 page。

## 调用顺序

1. `domain`：比较 `tasks[].goal + io.entry/exit + roles`，选一个任务；
2. `page`：`query=<选中 task.slug>` 读取 `skos:note` 步骤；
3. 需要下一个任务时，完成当前任务后重新 `domain` 选择，不在开头 page 全部任务。

## 测试

- `tests/test_lookup_domain_tasks.py`：MCP 契约（分组/I-O/角色/紧凑性/父组保留）；
- `tests/test_l1_taskio_contract.py`：7 个 L1 任务 I/O 语法与依赖；
- `tests/agent_e2e/test_modeling_task_select.py`：真实建模智能体 E2E，断言只 page 一个任务。
