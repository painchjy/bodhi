# 企业知识管理领域 · L1 标准闭环（4 活动 / 7 任务）

> 2026-11-08 落地。库：`6effde80-3ad0-46a1-92b2-76d695e0b58f`（企业知识管理领域）。
> 本文是该领域任务页的**人类可读摘要**；步骤正本仍是各任务页 `skos:note`，I/O 正本是 `activityEntry/Exit`。

## 1. 流程

```text
意图工程          T1 意图建模
规则工程          T3 规则化（含规则编辑 / 规则发布门禁）
业务流程建模      T5 流程建模 → T6 对象建模 → T7 绑定就绪验收
验收工程          T2 意图评估设计 → T4 验收执行
```

依赖不固化顺序，由 I/O 对象匹配推导；T2 是**汇合任务**，依赖 T1/T3/T5/T6/T7 的输出。

## 2. 任务 I/O 契约

| 任务 | entry（输入 + 下游验收规则） | exit（输出类型） |
|---|---|---|
| T1 意图建模 | `ontology:bpmn:TextualDataObject rules=policy:意图建模输入验收` | `ontology:bmm:Goal` / `DesiredResult` / `Means` / `Influencer` |
| T2 意图评估设计 | T1/T3/T5/T6/T7 输出类型 + 案例 `TextualDataObject`，`rules=policy:意图评估输入验收` | `ontology:agent:EvaluationPlan` |
| T3 规则化 | `ontology:bpmn:TextualDataObject rules=policy:规则化输入验收` | `ontology:bmm:BusinessPolicy` / `BusinessRule` |
| T4 验收执行 | `EvaluationPlan` + 全部建模输出 + 案例 `TextualDataObject`，`rules=policy:验收执行门禁` | `ontology:agent:Evaluation` / `Advice` |
| T5 流程建模 | `ontology:bpmn:TextualDataObject rules=policy:流程建模输入验收` | `ontology:bmm:BusinessProcess` / `bpmn:Activity` / `bpmn:Task` |
| T6 对象建模 | `ontology:bpmn:TextualDataObject rules=policy:对象建模输入验收` | `ontology:bpmn:DataObject` |
| T7 绑定就绪验收 | `ontology:bpmn:DataObject` + `agent:Tool` + `agent:MCPService`，`rules=policy:工具绑定验收` | `ontology:bpmn:DataObject` / `agent:Tool` / `agent:MCPService` |

**硬规则**：

- `entry` 写输入类型 + 验收规则；`exit` 只写输出类型，**不写验收规则**；
- 输出对象的规范/规则由**下游 entry** 判定；
- 实际输入与声明不一致 ⇒ 回 `io_contract_conflict`（expected / actual / rule / how_to_fix），不得静默换源；
- 输出 slug 清单、版本/hash、ruleHash、验收结论写任务页 `skos:note` 的“交接回执”；
- 下游不通过 ⇒ 上游返工，`bpmn:activityRound` +1；连续 3 轮升级人工。

## 3. T2：验收测试方案（`agent:EvaluationPlan`）

必须含 9 节：

1. 评估目标与范围
2. 智能体初始问题
3. 案例输入 `TextualDataObject`（sourceLocator；ext-doc 才建实例页）
4. 上下文信息
5. 正向案例（应解析出的知识：类/slug/字段/值/关系）
6. 负向案例（不应识别出的知识：干扰项/原因）
7. 评分规则（precision / recall / F1 / 误识别率 / 漏识别率、按类加权、通过门槛）
8. 执行步骤（目标库、专用测试工作库、执行任务、预期输出、停止条件）
9. 返工映射（失败项 → T1/T3/T5/T6/T7）

T2 不允许在模型残缺时设计测试方案；缺必要输出 ⇒ 给对应上游任务 `io_contract_conflict`。

## 4. T4：验收执行

1. 校验计划 9 节齐全；
2. 在**专用测试工作库**执行，不写生产库；
3. 用初始问题 + 案例输入跑流程，收集工作库输出；
4. 对比正/负案例，按评分规则算分；
5. 输出 `agent:Evaluation` + `agent:Advice`；
6. 不达标 ⇒ 按返工映射回上游，`activityRound+1`；
7. **单规则评测取消**：只做端到端验收，不再按单规则回放。

## 5. T7：绑定就绪验收（不是实际绑定）

- T7 只验收对象建模完整性：字段表、主键、枚举、必填、`dataObjectAccessMode` / `dataObjectInterface` / `dataObjectInterfaceRef` / `dataObjectKeyFields`；
- 不需要外部对接 ⇒ 软定义表格即通过；
- 需要外部对接但接口意图属性缺失 ⇒ `io_contract_conflict` 退回 T6；
- **实际绑定由 UI 执行**：UI 写 `bpmn:dataObjectBindingTool`；T7 不写绑定边、不替 UI 做映射，只读核对。

## 6. 首批策略（7 个，各 1 条清单规则）

`意图建模输入验收` / `意图评估输入验收` / `规则化输入验收` / `验收执行门禁` /
`流程建模输入验收` / `对象建模输入验收` / `工具绑定验收`。

- 规则类型：`bmm:TextLogicOperationalRule`；
- 四要素 + `bmm:knowledgeNeed`（`kn/text-logic-v1`）+ `language:` 行；
- `ke_rule_check` 0 错误；`ke_rule_publish` 写 `bmm:ruleHash`；
- 评估中发现某类缺陷反复出现，再拆成独立规则，不预先造大而全的规则集。

## 7. 角色

只设 2 个：**业务专家**（T1/T3/T5/T6/T7）、**质量验收**（T2/T4/T7）。不设机器岗位；智能体只作辅助执行工具。

## 8. 给其他领域复用的步骤

1. 复制 4 活动 / 7 任务骨架到目标领域库（任务页 `skos:note` 保留，I/O 换成目标领域对象）；
2. 先建 T1/T3/T5/T6 的输入 `TextualDataObject` 与输出类型；
3. 按 7 个交接点写验收策略（可先 1 条清单规则）；
4. 跑 `audit_scan(scope="taskio", stage="handoff")`，修到 0 high/medium；
5. 用 T2/T4 跑一次带正/负例的端到端验收，记录返工闭环；
6. 再扩到其他领域，不直接把领域名词写进 tools/skills。

## 9. 验证命令

```bash
python3 -m pytest tests/test_l1_modeling.py tests/test_l1_taskio_contract.py tests/test_rule_engineering.py -q
# MCP
audit_scan(kb_id="6effde80-3ad0-46a1-92b2-76d695e0b58f", scope="taskio", stage="handoff")
```
