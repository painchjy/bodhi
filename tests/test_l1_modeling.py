"""回归：**L1 建模三活动任务页**（P2，2026-10-30）。

断言：
1. L1（企业知识管理领域）有 3 个活动：意图工程 / 规则工程 / 业务流程建模；
2. 每个活动至少 1 个任务（图关系 `bpmn:activityContainsTask`）；
3. 11 个任务页都有确定性步骤 note（≥200 字），且**不引用技能**（流程来自任务页）；
4. 关键工具/关系名出现在对应任务里（规则门禁、工具契约同步、角色关系…）。

DB/图不可用 ⇒ skip。
"""
import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
for p in (HERE, os.path.join(REPO, "tools", "ke-core")):
    if p not in sys.path:
        sys.path.insert(0, p)

ACTS = {
    "意图工程": ["意图建模", "意图评估设计"],
    "规则工程": ["规则化", "规则编辑", "规则发布", "规则评测", "评测设计", "规则溯源"],
    "业务流程建模": ["流程建模", "对象建模", "工具绑定"],
}


@pytest.fixture(scope="module")
def kb():
    try:
        import ke_db
        ke_db.psql_csv("SELECT 1 AS ok")
        kb_id = ke_db.resolve_kb_id("企业知识管理领域")[0]
    except Exception as exc:                       # noqa: BLE001
        pytest.skip("DB 不可用：%r" % (str(exc)[:80],))
    if not kb_id:
        pytest.skip("找不到企业知识管理领域库")
    return kb_id


@pytest.fixture(scope="module")
def notes(kb):
    import ke_db
    rows = ke_db.psql_csv(
        "SELECT slug, title, "
        "coalesce(page_metadata->'ontology'->'attributes'->>'skos:note','') AS note "
        "FROM wiki_pages WHERE knowledge_base_id = %s AND slug LIKE %s AND deleted_at IS NULL"
        % (ke_db.sql_str(kb), ke_db.sql_str("bpmn/task/%")))
    return {r["title"]: (r.get("note") or "") for r in rows}


def test_three_activities_exist(kb):
    import ke_db
    rows = ke_db.psql_csv(
        "SELECT title FROM wiki_pages WHERE knowledge_base_id = %s "
        "AND slug LIKE %s AND deleted_at IS NULL"
        % (ke_db.sql_str(kb), ke_db.sql_str("bpmn/activity/%")))
    got = {r["title"] for r in rows}
    for name in ACTS:
        assert name in got, "缺活动：%s（现有 %r）" % (name, sorted(got))


def test_every_activity_contains_tasks(kb):
    import ke_neo4j
    rows = ke_neo4j.query(
        "MATCH (a:BodhiInstance {kb_id:$kb})-[:`bpmn:activityContainsTask`]->(t:BodhiInstance) "
        "WHERE a.slug STARTS WITH 'bpmn/activity/' "
        "RETURN last(split(a.slug,'/')) AS act, last(split(t.slug,'/')) AS task "
        "ORDER BY act, task",
        {"kb": kb}, timeout=10) or []
    by_act = {}
    for r in rows:
        by_act.setdefault(str(r.get("act") or ""), []).append(str(r.get("task") or ""))
    for name in ACTS:
        assert by_act.get(name), "活动没有任务：%s" % name


def test_task_notes_are_deterministic(notes):
    for act, tasks in ACTS.items():
        for t in tasks:
            assert t in notes, "缺任务页：%s（活动 %s）" % (t, act)
            note = notes[t]
            assert len(note) >= 200, "%s 的步骤过短（%d 字）" % (t, len(note))
            assert "步骤" in note or "按序" in note, "%s 未写明执行步骤" % t


def test_task_notes_reference_tools_not_skills(notes):
    joined = " ".join(notes.values())
    assert "domain_modeling" not in joined, "任务页仍在引用已退役技能"
    assert "ke_rule_check" in joined and "ke_rule_runtime_eval" in joined, "规则工程缺门禁工具名"
    assert "ext_sync_tools" in notes.get("工具绑定", ""), "工具绑定页缺契约同步工具"
    assert "bpmn:dataObjectBindingTool" in notes.get("工具绑定", ""), "工具绑定页未写明绑定=关系"
    assert "bpmn:taskHasRole" in notes.get("流程建模", ""), "流程建模页未写角色关系"
    assert "bmm:DesiredResult" in notes.get("意图建模", ""), "意图建模页缺 BMM 结构"
