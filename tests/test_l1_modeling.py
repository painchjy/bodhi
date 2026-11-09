"""回归：**L1 建模四活动 / 七任务**（P2，2026-11-08 收敛）。

断言：
1. L1（企业知识管理领域）有 4 个活动：意图工程 / 规则工程 / 业务流程建模 / 验收工程；
2. 每个活动至少 1 个任务（图关系 `bpmn:activityContainsTask`）；
3. 7 个任务页都有确定性步骤 note（≥200 字），且**不引用技能**（流程来自任务页）；
4. 关键工具/关系名出现在对应任务里（规则门禁、绑定验收、角色关系…）。

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
    "意图工程": ["意图建模"],
    "规则工程": ["规则化"],
    "业务流程建模": ["流程建模", "对象建模", "绑定就绪验收"],
    "验收工程": ["意图评估设计", "验收执行"],
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


def test_four_activities_exist(kb):
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


def test_activity_task_edges_are_exactly_seven(kb):
    """4 活动 / 7 任务，且没有旧任务仍挂活动。"""
    import ke_neo4j
    rows = ke_neo4j.query(
        "MATCH (a:BodhiInstance {kb_id:$kb})-[:`bpmn:activityContainsTask`]->(t:BodhiInstance) "
        "WHERE a.slug STARTS WITH 'bpmn/activity/' "
        "RETURN last(split(a.slug,'/')) AS act, last(split(t.slug,'/')) AS task",
        {"kb": kb}, timeout=10) or []
    got = {(str(r.get("act") or ""), str(r.get("task") or "")) for r in rows}
    expected = {(act, task) for act, tasks in ACTS.items() for task in tasks}
    assert got == expected, "活动-任务边不符：got=%r expected=%r" % (sorted(got), sorted(expected))


def test_only_two_roles(kb):
    import ke_db
    rows = ke_db.psql_csv(
        "SELECT title FROM wiki_pages WHERE knowledge_base_id = %s "
        "AND slug LIKE %s AND deleted_at IS NULL"
        % (ke_db.sql_str(kb), ke_db.sql_str("bpmn/role/%")))
    got = {r["title"] for r in rows}
    assert got == {"业务专家", "质量验收"}, "角色应只有业务专家/质量验收，实际 %r" % sorted(got)


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
    assert "ke_rule_check" in joined and "ke_rule_publish" in joined, "规则工程缺门禁/发布工具名"
    assert "bpmn:dataObjectBindingTool" in notes.get("绑定就绪验收", ""), \
        "绑定就绪验收页未写明 UI 绑定关系"
    assert "UI" in notes.get("绑定就绪验收", ""), "绑定就绪验收页未说明实际绑定由 UI 执行"
    assert "bpmn:taskHasRole" in notes.get("流程建模", ""), "流程建模页未写角色关系"
    assert "bmm:DesiredResult" in notes.get("意图建模", ""), "意图建模页缺 BMM 结构"
