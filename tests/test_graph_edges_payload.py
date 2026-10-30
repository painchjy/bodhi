"""回归（真实 payload 形状）：`stage=graph` 的**顶层 edges**（slug → 标题）必须落图。

来源：2026-10-30 实测 payload（`logs/args/20261030-152405-143399-save_knowledge.json`）：
    edges=[{source:"agent/ruleevaluation/规则评测｜<规则>｜<编号>", type:"agent:evaluationOfRule",
            target:"<规则标题>"}]
当时 7 条全部静默丢弃（`relationships:0 / edges_planned:0`）——因为 `stage=graph` 只从
`nodes[].relations` 建边，顶层 `edges` 被忽略；且 `target` 写的是**标题**。
本用例复现同形状，断言：标题被解析成 slug、边真写入、`external_edges.planned>0`。
"""
import os
import sys
import uuid

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
for p in (HERE, os.path.join(REPO, "tools", "ke-core"),
          os.path.join(REPO, "tools", "ontology-mcp")):
    if p not in sys.path:
        sys.path.insert(0, p)


@pytest.fixture(scope="module")
def env():
    try:
        import ke_db
        import ke_neo4j
        ke_db.psql_csv("SELECT 1 AS ok")
        kb = ke_db.resolve_kb_id("技术方案评审领域模型（测试）")[0]
    except Exception as exc:                       # noqa: BLE001
        pytest.skip("依赖不可用：%r" % (str(exc)[:80],))
    if not kb:
        pytest.skip("找不到测试领域库")
    return {"kb": kb, "db": ke_db, "g": ke_neo4j}


def test_top_level_edges_with_title_target_are_written(env):
    import ke_pages
    from srv import knowledge_write as KW
    kb = env["kb"]
    tag = uuid.uuid4().hex[:6]
    rule_title = "规则-%s" % tag
    rule = "bmm/operativebusinessrule/%s" % tag
    ev = "agent/ruleevaluation/规则评测｜%s｜test%s" % (tag, tag)

    ke_pages.write_knowledge(kb, {"slug": rule, "title": rule_title,
                                  "page_type": "bmm:OperativeBusinessRule", "mode": "entity",
                                  "wiki_content": "", "summary": "回归用规则",
                                  "attributes": {"skos:definition": "回归用规则（payload 形状）"},
                                  "source": {"doc_refs": [], "source_text": "pytest"}},
                             strict_source=False, sync_folders=False)
    ke_pages.write_knowledge(kb, {"slug": ev, "title": "规则评测｜%s" % tag,
                                  "page_type": "agent:RuleEvaluation", "mode": "document",
                                  "wiki_content": "# 评测\n\n| 字段 | 值 |\n|---|---|\n| 规则 | %s |\n"
                                                  % rule_title,
                                  "summary": "评测", "source": {"doc_refs": [], "source_text": "pytest"}},
                             strict_source=False, sync_folders=False)

    sess = env["db"].psql_csv("SELECT id FROM sessions WHERE deleted_at IS NULL "
                              "ORDER BY created_at DESC LIMIT 1")
    sno = str((sess[0] or {}).get("id") or "") if sess else ""
    out = KW.save_knowledge(kb_id=kb, stage="graph", mode="apply", session_no=sno,
                            nodes=[], edges=[{"source": ev,
                                              "type": "agent:evaluationOfRule",
                                              "target": rule_title}])
    g = out.get("graph") or {}
    ext = g.get("external_edges") or {}
    assert out.get("stage") == "graph"
    assert ext.get("planned") == 1, out
    assert not ext.get("unresolved"), ext
    assert (g.get("edges_written") or 0) >= 1, g

    rows = env["g"].query(
        "MATCH (a:BodhiInstance {kb_id:$kb, slug:$s})-[r:`agent:evaluationOfRule`]->"
        "(b:BodhiInstance {kb_id:$kb, slug:$t}) RETURN count(r) AS n",
        {"kb": kb, "s": ev, "t": rule}, timeout=10)
    assert rows and rows[0]["n"] == 1, rows

    env["db"].psql("UPDATE wiki_pages SET deleted_at = now() WHERE knowledge_base_id = %s "
                   "AND slug IN (%s, %s);" % (env["db"].sql_str(kb), env["db"].sql_str(rule),
                                              env["db"].sql_str(ev)))
    env["g"].query("MATCH (n:BodhiInstance {kb_id:$kb}) WHERE n.slug IN [$a,$b] DETACH DELETE n",
                   {"kb": kb, "a": rule, "b": ev}, timeout=20)
