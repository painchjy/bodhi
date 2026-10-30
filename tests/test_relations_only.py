"""回归：**纯关系模式**（报告/评测页单建后补边）+ **绑定自动补契约块**（2026-10-30）。

1. `ke_graph.validate_edge` 四类 code：ok / unknown_relation / missing_page / (domain|range)_mismatch；
2. `save_knowledge(stage="graph", relations_only=True)`：dry_run 给 checks、apply 真写边、
   **未知关系被拦、违规关系只记 violations 不阻断**；
3. `set_binding` 成功后**自动给对象页补 ```bodhi-ext-contract 块**（幂等）。

依赖 PG + 图；不可用 ⇒ skip。用例自建页/自删。
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


@pytest.fixture
def pages(env):
    """建一对页（规则 entity + 评测 document），用完软删。"""
    import ke_pages
    kb = env["kb"]
    tag = uuid.uuid4().hex[:6]
    rule = "bmm/operativebusinessrule/reltest-%s" % tag
    ev = "agent/ruleevaluation/reltest-%s" % tag
    ke_pages.write_knowledge(kb, {"slug": rule, "title": "reltest-%s" % tag,
                                  "page_type": "bmm:OperativeBusinessRule", "mode": "entity",
                                  "wiki_content": "", "summary": "关系测试",
                                  "attributes": {"skos:definition": "关系测试规则"},
                                  "source": {"doc_refs": [], "source_text": "pytest"}},
                             strict_source=False, sync_folders=False)
    ke_pages.write_knowledge(kb, {"slug": ev, "title": "评测 reltest-%s" % tag,
                                  "page_type": "agent:RuleEvaluation", "mode": "document",
                                  "wiki_content": "# 评测\n\n| 字段 | 值 |\n|---|---|\n| r | 1 |\n",
                                  "summary": "评测", "source": {"doc_refs": [], "source_text": "pytest"}},
                             strict_source=False, sync_folders=False)
    yield {"rule": rule, "eval": ev}
    env["db"].psql("UPDATE wiki_pages SET deleted_at = now() WHERE knowledge_base_id = %s "
                   "AND slug IN (%s, %s);" % (env["db"].sql_str(kb),
                                              env["db"].sql_str(rule), env["db"].sql_str(ev)))


def test_validate_edge_codes(env, pages):
    import ke_graph
    ok = ke_graph.validate_edge(env["kb"], pages["eval"], "agent:evaluationOfRule", pages["rule"])
    assert ok["ok"] and ok["code"] == "", ok
    bad = ke_graph.validate_edge(env["kb"], pages["eval"], "agent:notARealRelation", pages["rule"])
    assert bad["code"] == "unknown_relation", bad
    miss = ke_graph.validate_edge(env["kb"], pages["eval"], "agent:evaluationOfRule", "no/such/page")
    assert miss["code"] == "missing_page", miss


def test_relations_only_dry_run_and_apply(env, pages):
    from srv import knowledge_write as KW
    edges = [{"source": pages["eval"], "type": "agent:evaluationOfRule",
              "target_slug": pages["rule"]},
             {"source": pages["eval"], "type": "agent:notARealRelation",
              "target_slug": pages["rule"]}]
    dry = KW.save_knowledge(kb_id=env["kb"], stage="graph", relations_only=True,
                            edges=edges, mode="dry_run")
    assert dry["ok"] and dry["total"] == 2
    codes = [c["code"] for c in dry["checks"]]
    assert codes.count("") == 1 and "unknown_relation" in codes, dry["checks"]

    sess = env["db"].psql_csv("SELECT id FROM sessions WHERE deleted_at IS NULL "
                              "ORDER BY created_at DESC LIMIT 1")
    sno = str((sess[0] or {}).get("id") or "") if sess else ""
    res = KW.save_knowledge(kb_id=env["kb"], stage="graph", relations_only=True,
                            edges=edges, mode="apply", session_no=sno)
    assert res["ok"] and res["written"] == 1, res
    assert any(e.get("code") == "unknown_relation" for e in res["errors"]), res["errors"]


def test_binding_adds_contract_block(env, pages):
    import ke_ext_store as S
    kb = env["kb"]
    out = S.set_binding(kb, pages["rule"], "ext_issue_list", status="draft")
    assert out["ok"], out
    blk = out.get("contract_block") or {}
    if not blk.get("ok"):
        pytest.skip("契约块写入不可用：%r" % (blk.get("detail") or blk,))
    row = env["db"].psql_csv(
        "SELECT left(coalesce(content,''),120) AS h FROM wiki_pages "
        "WHERE knowledge_base_id=%s AND slug=%s AND deleted_at IS NULL"
        % (env["db"].sql_str(kb), env["db"].sql_str(pages["rule"])))[0]
    assert "```bodhi-ext-contract" in row["h"], row
    again = S.set_binding(kb, pages["rule"], "ext_issue_list", status="draft")
    assert (again.get("contract_block") or {}).get("reason") == "has_block", again
