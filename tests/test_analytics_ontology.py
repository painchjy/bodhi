#!/usr/bin/env python3
"""自助取数分析本体 · 事实→维度通路回归（静态，不连库）。

验证：
1. 本体声明 Dimension/Fact/StandardQueryScenario/AnalysisCase 4 个类；
2. Scenario→Fact 是来源关系；案例通过 caseUsesFact/Dimension/McpService 关联对象；
3. ABox 夹具里工具调用事实能走到两个工具分类维度；轮次事实不挂工具分类。

跑法：`python3 tests/test_analytics_ontology.py`
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _helpers as H  # noqa: E402

ONTO = os.path.join(H.REPO, "ontology", "sources")
FIXTURE = os.path.join(H.TESTS, "fixtures", "analytics_instances.ttl")


def _graph():
    from rdflib import Graph, Namespace  # noqa: PLC0415
    from rdflib.namespace import RDF, RDFS  # noqa: PLC0415

    g = Graph()
    for name in ("analytics.ttl", "bpmn.ttl", "agent.ttl"):
        g.parse(os.path.join(ONTO, name), format="turtle")
    g.parse(FIXTURE, format="turtle")
    return g, Namespace("http://example.org/analytics#"), RDF, RDFS


def main() -> int:
    try:
        g, A, RDF, RDFS = _graph()
    except Exception as exc:  # noqa: BLE001
        print("跳过：rdflib/本体不可用：%r" % (str(exc)[:120],))
        return 0

    from rdflib import URIRef  # noqa: PLC0415
    from rdflib.namespace import OWL  # noqa: PLC0415

    classes = {str(s).rsplit("#", 1)[-1] for s in g.subjects(RDF.type, OWL.Class)
               if str(s).startswith(str(A))}
    expected_classes = {"Dimension", "Fact", "StandardQueryScenario", "AnalysisCase"}
    missing_classes = sorted(expected_classes - classes)

    obj_props = {str(s).rsplit("#", 1)[-1] for s in g.subjects(RDF.type, OWL.ObjectProperty)
                 if str(s).startswith(str(A))}
    data_props = {str(s).rsplit("#", 1)[-1] for s in g.subjects(RDF.type, OWL.DatatypeProperty)
                  if str(s).startswith(str(A))}

    # 事实→维度：每个事实必须能走到 2 个工具分类维度
    q = """
    SELECT ?fact ?factLabel ?dim ?dimLabel WHERE {
      ?fact a analytics:Fact ;
            analytics:factUsesDimension ?dim .
      ?dim a analytics:Dimension .
      OPTIONAL { ?fact rdfs:label ?factLabel }
      OPTIONAL { ?dim rdfs:label ?dimLabel }
    } ORDER BY ?factLabel ?dimLabel
    """
    ns = {"analytics": URIRef(str(A)), "rdfs": URIRef(str(RDFS))}
    rows = list(g.query(q, initNs=ns))
    by_fact = {}
    for row in rows:
        by_fact.setdefault(str(row.factLabel or row.fact), []).append(str(row.dimLabel or row.dim))
    tool_dims = {"工具分类（2层）", "工具分类（1层）"}
    turn_dims = {"时间", "Agent", "模型", "会话"}
    path_ok = (len(by_fact) == 2
               and set(by_fact.get("AgentToolCallFact") or []) == tool_dims | turn_dims
               and set(by_fact.get("AgentTurnFact") or []) == turn_dims)

    rel = URIRef(str(A) + "factUsesDimension")
    domain_ok = (rel, RDFS.domain, URIRef(str(A) + "Fact")) in g
    range_ok = (rel, RDFS.range, URIRef(str(A) + "Dimension")) in g
    svc_rel = URIRef(str(A) + "objectUsesMcpService")
    svc_domain_ok = (svc_rel, RDFS.domain, URIRef("http://example.org/bpmn#DataObject")) in g
    svc_range_ok = (svc_rel, RDFS.range, URIRef("http://example.org/agent#MCPService")) in g
    scenario_fact = URIRef(str(A) + "scenarioBasedOnFact")
    scenario_service = URIRef(str(A) + "scenarioUsesMcpService")
    case_fact = URIRef(str(A) + "caseUsesFact")
    case_dim = URIRef(str(A) + "caseUsesDimension")
    case_svc = URIRef(str(A) + "caseUsesMcpService")
    case_class = URIRef(str(A) + "AnalysisCase")
    case_domain_ok = ((scenario_fact, RDFS.domain, URIRef(str(A) + "StandardQueryScenario")) in g
                      and (scenario_fact, RDFS.range, URIRef(str(A) + "Fact")) in g
                      and (scenario_service, RDFS.range, URIRef("http://example.org/agent#MCPService")) in g)
    case_ok = ((case_fact, RDFS.domain, case_class) in g
               and (case_fact, RDFS.range, URIRef(str(A) + "Fact")) in g
               and (case_dim, RDFS.range, URIRef(str(A) + "Dimension")) in g
               and (case_svc, RDFS.range, URIRef("http://example.org/agent#MCPService")) in g
               and (case_class, RDFS.subClassOf, URIRef("http://example.org/bmm#Report")) in g)

    checks = {
        "类 = Dimension/Fact/StandardQueryScenario/AnalysisCase（缺：%s）" % (missing_classes or "无"): not missing_classes,
        "对象属性（实际 %s）" % sorted(obj_props): obj_props == {
            "factUsesDimension", "objectUsesMcpService", "scenarioBasedOnFact",
            "scenarioUsesMcpService", "caseUsesFact", "caseUsesDimension",
            "caseUsesMcpService"},
        "数据属性 = grain/databaseType/mapping*（实际 %s）" % sorted(data_props):
            data_props == {"grain", "databaseType", "mappingObjectType",
                           "mappingObjectId", "mappingServiceId", "platformMcpServiceId"},
        "工具调用事实挂 2 个工具分类维度；轮次事实不挂（实际 %s）" % by_fact: path_ok,
        "factUsesDimension domain=Fact": domain_ok,
        "factUsesDimension range=Dimension": range_ok,
        "objectUsesMcpService domain=DataObject": svc_domain_ok,
        "objectUsesMcpService range=MCPService": svc_range_ok,
        "Scenario 来源 Fact + 使用 MCP 服务": case_domain_ok,
        "AnalysisCase 是 Report 子类且三条使用关系正确": case_ok,
    }
    return H.report(checks, "analytics 事实→维度通路")


if __name__ == "__main__":
    sys.exit(main())
