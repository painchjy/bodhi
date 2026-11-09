"""回归：**统一类视图 + 4 消费方一致性**（2026-11-08）。

同一个 `ke_ontology.class_view(类)` 必须同时支撑：
1. `save_knowledge` / `ke_graph.validate_edge`（domain/range/direction）
2. `ontology_types.class_relations`
3. `ontology_wiki` 类页的 out/in
4. `ke_audit` 使用的 `relation_type_map` / `target_closure`

DB/Neo4j 不可用 ⇒ skip。
"""
import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
for p in (os.path.join(REPO, "tools", "ke-core"),
          os.path.join(REPO, "tools", "ontology-mcp"),
          os.path.join(REPO, "tools", "ontology-extract")):
    if p not in sys.path:
        sys.path.insert(0, p)

KB = "7efad3eb-762a-4981-9164-cd3b3a226bd4"
POLICY = "bmm/businesspolicy/技术方案评审策略"
GOAL = "bmm/goal/确保技术方案评审质量"


@pytest.fixture(scope="module")
def onto():
    try:
        import ke_ontology
        if not ke_ontology.class_meta():
            pytest.skip("本体元数据不可用")
        return ke_ontology
    except Exception as exc:  # noqa: BLE001
        pytest.skip("ke_ontology 不可用：%r" % (str(exc)[:80],))


def _names(rows):
    return {r.get("name") for r in (rows or [])}


def test_businesspolicy_out_has_inherited_means_relations(onto):
    view = onto.class_view("bmm:BusinessPolicy")
    own = _names((view.get("out") or {}).get("own"))
    inherited = (view.get("out") or {}).get("inherited") or {}
    means = _names(inherited.get("bmm:Means"))
    directive = _names(inherited.get("bmm:Directive"))
    assert "bmm:realizes" in means, means
    assert "bmm:createdBy" in means, means
    assert "bmm:governs" in directive, directive
    assert "bmm:isSourceOf" in directive, directive
    assert "bmm:isBasisFor" in own, own
    # 同一关系不能在 own/inherited 重复
    all_names = list(own) + list(means) + list(directive)
    assert len(all_names) == len(set(all_names)), all_names


def test_goal_in_has_incoming_realizes(onto):
    view = onto.class_view("bmm:Goal")
    inherited = (view.get("in") or {}).get("inherited") or {}
    rows = inherited.get("bmm:DesiredResult") or []
    hit = [r for r in rows if r.get("name") == "bmm:realizes"]
    assert hit, inherited
    assert "bmm:BusinessPolicy" in (hit[0].get("source_classes") or []), hit[0]
    assert hit[0].get("direction") == "in"


def test_edge_check_reverse_is_canonical_not_write(onto):
    ok = onto.edge_check("bmm:realizes", "bmm:BusinessPolicy", "bmm:Goal")
    assert ok.get("ok") and not ok.get("code"), ok
    rev = onto.edge_check("bmm:realizes", "bmm:Goal", "bmm:BusinessPolicy")
    assert rev.get("code") == "direction_reversed", rev
    assert (rev.get("canonical") or {}).get("source_type") == "bmm:BusinessPolicy"
    assert (rev.get("canonical") or {}).get("target_type") == "bmm:Goal"


def test_validate_edge_consumer_returns_canonical_slugs():
    try:
        import ke_graph
        fwd = ke_graph.validate_edge(KB, POLICY, "bmm:realizes", GOAL)
        rev = ke_graph.validate_edge(KB, GOAL, "bmm:realizes", POLICY)
    except Exception as exc:  # noqa: BLE001
        pytest.skip("ke_graph/实例页不可用：%r" % (str(exc)[:80],))
    assert fwd.get("ok"), fwd
    assert rev.get("code") == "direction_reversed", rev
    canon = rev.get("canonical") or {}
    assert canon.get("source") == POLICY and canon.get("target") == GOAL, canon


def test_ontology_types_consumer_has_class_relations(onto):
    try:
        from srv.meta import ontology_types
        out = ontology_types("bmm", classes=["bmm:BusinessPolicy", "bmm:Goal"])
    except Exception as exc:  # noqa: BLE001
        pytest.skip("ontology_types 不可用：%r" % (str(exc)[:80],))
    cr = out.get("class_relations") or {}
    assert "bmm:BusinessPolicy" in cr and "bmm:Goal" in cr, cr
    bp = cr["bmm:BusinessPolicy"].get("out") or {}
    means = _names((bp.get("inherited") or {}).get("bmm:Means"))
    assert "bmm:realizes" in means, means
    goal_in = (cr["bmm:Goal"].get("in") or {}).get("inherited") or {}
    rows = goal_in.get("bmm:DesiredResult") or []
    assert any(r.get("name") == "bmm:realizes" for r in rows), rows


def test_ontology_wiki_consumer_renders_inherited_relations():
    try:
        import ontology_wiki
        pages, _stats = ontology_wiki.build_pages()
    except Exception as exc:  # noqa: BLE001
        pytest.skip("ontology_wiki 不可用：%r" % (str(exc)[:80],))
    content = next((p.get("content") or "" for p in pages
                    if p.get("slug") == "ontology/bmm/businesspolicy"), "")
    assert content, "缺 BusinessPolicy 类页"
    assert "继承自" in content, content[:600]
    assert "bmm:realizes" in content, content[:1200]
    assert "bmm:createdBy" in content, content[:1200]


def test_audit_core_inputs_match_view(onto):
    # ke_audit 的 A4/B3 使用这两个函数；它们必须与 class_view 同源。
    bp = onto.class_view("bmm:BusinessPolicy")
    means = _names(((bp.get("out") or {}).get("inherited") or {}).get("bmm:Means"))
    assert "bmm:realizes" in means
    assert "bmm:realizes" in onto.relation_type_map("bmm:BusinessPolicy")
    assert "bmm:Goal" in onto.target_closure("bmm:realizes")


def test_task_inherits_activity_io_data(onto):
    """数据属性也必须按祖先继承：Task 必须看到 Activity 的 entry/exit。"""
    view = onto.class_view("bpmn:Task")
    inherited = (view.get("data") or {}).get("inherited") or {}
    names = _names(inherited.get("bpmn:Activity") or [])
    assert {"bpmn:activityEntry", "bpmn:activityExit"} <= names, inherited


def test_ontology_types_task_relations_include_inherited_data():
    try:
        from srv.meta import ontology_types
        out = ontology_types("bpmn", classes=["bpmn:Task"])
    except Exception as exc:  # noqa: BLE001
        pytest.skip("ontology_types 不可用：%r" % (str(exc)[:80],))
    cr = (out.get("class_relations") or {}).get("bpmn:Task") or {}
    data = cr.get("data") or {}
    names = _names((data.get("inherited") or {}).get("bpmn:Activity") or [])
    assert {"bpmn:activityEntry", "bpmn:activityExit"} <= names, data
    attrs = (out.get("class_attributes") or {}).get("bpmn:Task") or []
    entry = next((a for a in attrs if a.get("name") == "bpmn:activityEntry"), None)
    assert entry and entry.get("inherited_from") == "bpmn:Activity", entry
    assert entry.get("scope") == "inherited", entry


def test_ontology_wiki_task_page_shows_inherited_data():
    try:
        import ontology_wiki
        pages, _stats = ontology_wiki.build_pages()
    except Exception as exc:  # noqa: BLE001
        pytest.skip("ontology_wiki 不可用：%r" % (str(exc)[:80],))
    content = next((p.get("content") or "" for p in pages
                    if p.get("slug") == "ontology/bpmn/task"), "")
    assert content, "缺 Task 类页"
    assert "继承自" in content and "activityEntry" in content and "activityExit" in content, \
        content[:1200]

