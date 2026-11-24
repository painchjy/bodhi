"""Golden 语义场景 · 真实智能体会话 apply E2E。

与 roundtrip 测试的区别：这里不直接调用 MCP 工具，而是把任务交给「智能问数」
智能体；只把 scenario_id 交给它，语义名称/单位/字段类型来自场景
semantic_json.answers，验证它真的走 validate → plan → apply(confirm=true,
dry_run=false)，并且写回 analytics 语义层与场景 wiki 页。
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
for path in (str(HERE), str(REPO / "tests"), str(REPO / "tools" / "ke-core")):
    if path not in sys.path:
        sys.path.insert(0, path)

import ke_analytics  # noqa: E402
import ke_db  # noqa: E402
from harness import WeKnoraClient, run_query, tool_calls  # noqa: E402
from test_analytics_semantic_extract import (  # noqa: E402
    APPLY, PLAN, VALIDATE, _kb_id, restore_fact, snapshot_fact)
from test_analytics_semantic_roundtrip import (  # noqa: E402
    CASES, _delete_semantics, _golden_snapshot, _sem)

E2E = os.environ.get("BODHI_RUN_ANALYTICS_E2E") == "1"


@pytest.mark.agent_e2e
@pytest.mark.skipif(not E2E, reason="set BODHI_RUN_ANALYTICS_E2E=1 to run live agent tests")
@pytest.mark.parametrize("fact,scenario,golden_name", CASES)
def test_agent_session_golden_apply(fact, scenario, golden_name):
    kb = _kb_id()
    client = WeKnoraClient.from_env(agent_name="智能问数（智能体运行状态监控）")
    snap = snapshot_fact(fact)
    golden = _golden_snapshot(golden_name)
    try:
        _delete_semantics(fact)
        query = f"""任务：从标准取数场景提取语义层并**写库**（golden apply）。
场景：{scenario}；目标事实：{fact}。
请按任务页 `bpmn/task/从标准取数场景提取语义层` 的 skos:note 执行：
选任务并读任务页 → {VALIDATE} → 用场景绑定服务执行原 SQL 验证 →
`{PLAN}`(scenario_id="{scenario}", fact_id="{fact}")。
场景 `semantic_json.answers` 已包含完整中文名称/单位/字段类型/枚举，**不要**
再向用户追问，也不要在 plan 参数里重复传 answers。
用户已确认：拿到 plan_id 后先 dry_run=true 展示 diff，再立即调用
`{APPLY}`(scenario_id="{scenario}", kb_id="{kb}", plan_id=同一次 plan_id,
confirm=true, dry_run=false) 真正写库；写后核对
validation.mapping.status、validation.scenario.ok、validation.converged。
最终只报告：调用过的工具名、plan_id、applied、validation 三个状态。"""
        run = run_query(client, query, title="semantic-golden-apply-" + fact)
        session_id = run["session_id"]
        rows = ke_db.psql_csv(
            "SELECT id FROM sessions WHERE id=%s AND deleted_at IS NULL"
            % ke_db.sql_str(session_id))
        assert rows, "session not persisted: %s" % session_id
        assert run["messages"], "session has no messages"
        calls = tool_calls(run["messages"])
        names = [c["name"] for c in calls]
        assert VALIDATE in names, names
        assert PLAN in names, names
        assert APPLY in names, names
        plans = [c for c in calls if c["name"] == PLAN]
        assert any(not (c["args"].get("answers") or {}) for c in plans), plans
        applies = [c for c in calls if c["name"] == APPLY]
        assert any(c["args"].get("confirm") is True and c["args"].get("dry_run") is False
                   for c in applies), applies
        assert _sem(snapshot_fact(fact), scenario) == _sem(golden, scenario)

        page = ke_db.psql_csv(
            "SELECT content, page_metadata::text AS meta FROM wiki_pages "
            "WHERE knowledge_base_id=%s AND slug=%s AND deleted_at IS NULL"
            % (ke_db.sql_str(kb),
               ke_db.sql_str("analytics/scenario/" + scenario)))
        assert page, "scenario page missing"
        content = page[0]["content"] or ""
        meta = json.loads(page[0]["meta"] or "{}")
        attrs = (meta.get("ontology") or {}).get("attributes") or meta.get("attributes") or {}
        assert "## 固定筛选" in content and "## 关联路径" in content, content[:500]
        assert attrs.get("analytics:sqlStatement"), attrs.keys()
        semantic = json.loads(attrs.get("analytics:semanticJson") or "{}")
        assert semantic.get("version") == 1 and semantic.get("definitions"), semantic.keys()
        assert "skos:note" in attrs, attrs.keys()
    finally:
        restore_fact(kb, fact, snap, sync_pages=False)
        ke_analytics.sync_pages_affected(
            kb, {"fact": [fact], "scenario": [scenario]}, dry_run=False)
