"""回归：L1 任务 I/O 契约（4 活动 / 7 任务 / 2 角色）。

断言：
- 7 个任务的 activityEntry/Exit 都非空，行前缀只能是 dataobject:/text:/ontology:；
- entry 的 rules=policy:... 都能解析；dataobject 页存在；ontology 类在本体模型里；
- T2 依赖 T1/T3/T5/T6/T7 输出；T4 依赖 EvaluationPlan + 全部建模输出；
- 6 个废弃任务页已清理且不挂活动；
- 只有 2 个角色（业务专家/质量验收）。
"""
import os
import re
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
for p in (HERE, os.path.join(REPO, "tools", "ke-core")):
    if p not in sys.path:
        sys.path.insert(0, p)

KB_NAME = "企业知识管理领域"
TASKS = {
    "意图建模": "意图工程",
    "规则化": "规则工程",
    "流程建模": "业务流程建模",
    "对象建模": "业务流程建模",
    "绑定就绪验收": "业务流程建模",
    "意图评估设计": "验收工程",
    "验收执行": "验收工程",
}
ARCHIVED = ["规则编辑", "评测设计", "规则发布", "规则溯源", "规则评测", "接口变更重映射"]


@pytest.fixture(scope="module")
def kb():
    try:
        import ke_db
        ke_db.psql_csv("SELECT 1 AS ok")
        kb_id = ke_db.resolve_kb_id(KB_NAME)[0]
    except Exception as exc:  # noqa: BLE001
        pytest.skip("DB 不可用：%r" % (str(exc)[:80],))
    if not kb_id:
        pytest.skip("找不到企业知识管理领域库")
    return kb_id


def _rows(kb):
    import ke_db
    return ke_db.psql_csv(
        "SELECT slug, title, page_type, "
        "coalesce(page_metadata->'ontology'->'attributes'->>'bpmn:activityEntry','') AS entry, "
        "coalesce(page_metadata->'ontology'->'attributes'->>'bpmn:activityExit','') AS exit "
        "FROM wiki_pages WHERE knowledge_base_id = %s AND slug LIKE 'bpmn/task/%%' "
        "AND deleted_at IS NULL" % ke_db.sql_str(kb))


def test_task_io_lines_and_policy_refs(kb):
    import ke_db
    import ke_ontology
    import ke_rule_expr
    classes = set((ke_ontology.class_meta() or {}).keys())
    rows = {r["title"]: r for r in _rows(kb)}
    for name in TASKS:
        assert name in rows, "缺任务页：%s" % name
        r = rows[name]
        assert (r.get("entry") or "").strip(), "%s entry 为空" % name
        assert (r.get("exit") or "").strip(), "%s exit 为空" % name
        for field in ("entry", "exit"):
            for line in re.split(r"[;\n]+", r.get(field) or ""):
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                m = re.search(r"(?:^|\s)rules=([^\s]+)", line)
                head = (line[:m.start()] if m else line).strip()
                assert head.startswith(("dataobject:", "text:", "ontology:")), \
                    "%s %s 行前缀非法：%s" % (name, field, head[:80])
                if head.startswith("dataobject:"):
                    slug = head[len("dataobject:"):].strip()
                    hit = ke_db.psql_csv(
                        "SELECT 1 AS ok FROM wiki_pages WHERE knowledge_base_id=%s "
                        "AND slug=%s AND deleted_at IS NULL LIMIT 1"
                        % (ke_db.sql_str(kb), ke_db.sql_str(slug)))
                    assert hit, "%s 引用的 DataObject 不存在：%s" % (name, slug)
                elif head.startswith("ontology:"):
                    cls = head[len("ontology:"):].split(":closure=")[0].strip()
                    assert cls in classes, "%s 引用的本体类不存在：%s" % (name, cls)
                if m:
                    got = ke_rule_expr.expand(kb, m.group(1), limit=100)
                    assert got.get("ok"), "%s 的 rules 解析失败：%r" % (name, got)


def test_t2_t4_dependency_coverage(kb):
    rows = {r["title"]: r for r in _rows(kb)}
    t2 = rows["意图评估设计"]["entry"]
    for token in ("bmm:Goal", "bmm:DesiredResult", "bmm:BusinessPolicy",
                  "bmm:BusinessRule", "bmm:BusinessProcess", "bpmn:Activity",
                  "bpmn:Task", "bpmn:DataObject", "agent:Tool", "agent:MCPService",
                  "bpmn:TextualDataObject"):
        assert token in t2, "T2 entry 缺依赖：%s" % token
    t4 = rows["验收执行"]["entry"]
    for token in ("agent:EvaluationPlan", "bmm:Goal", "bmm:DesiredResult",
                  "bmm:BusinessPolicy", "bmm:BusinessRule", "bmm:BusinessProcess",
                  "bpmn:Activity", "bpmn:Task", "bpmn:DataObject",
                  "agent:Tool", "agent:MCPService", "bpmn:TextualDataObject"):
        assert token in t4, "T4 entry 缺依赖：%s" % token


def test_archived_pages_not_in_activities(kb):
    import ke_db
    import ke_neo4j
    rows = ke_neo4j.query(
        "MATCH (a:BodhiInstance {kb_id:$kb})-[:`bpmn:activityContainsTask`]->(t:BodhiInstance) "
        "WHERE a.slug STARTS WITH 'bpmn/activity/' RETURN last(split(t.slug,'/')) AS task",
        {"kb": kb}, timeout=10) or []
    linked = {str(r.get("task") or "") for r in rows}
    assert len(linked) == 7, "活动下应恰好 7 个任务，实际 %r" % sorted(linked)
    for name in ARCHIVED:
        assert name not in linked, "废弃页仍挂活动：%s" % name
        got = ke_db.psql_csv(
            "SELECT 1 AS ok FROM wiki_pages WHERE knowledge_base_id=%s AND slug=%s "
            "AND deleted_at IS NULL LIMIT 1"
            % (ke_db.sql_str(kb), ke_db.sql_str("bpmn/task/%s" % name)))
        assert not got, "废弃页应已清理：%s" % name


def test_only_two_roles(kb):
    import ke_db
    rows = ke_db.psql_csv(
        "SELECT title FROM wiki_pages WHERE knowledge_base_id=%s "
        "AND slug LIKE 'bpmn/role/%%' AND deleted_at IS NULL" % ke_db.sql_str(kb))
    assert {r["title"] for r in rows} == {"业务专家", "质量验收"}
