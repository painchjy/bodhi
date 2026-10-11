"""本体探查：类清单 + 继承链 + 是否有 wiki_content（含继承）+ 枚举类。

用法：/opt/bodhi-venv/bin/python3 tools/diag/_ttl_probe.py ontology/BMM完整版.ttl ontology/智能体开发完整版.ttl
"""
import sys

import rdflib

OWL = rdflib.Namespace("http://www.w3.org/2002/07/owl#")
RDF = rdflib.RDF
RDFS = rdflib.RDFS
SKOS = rdflib.Namespace("http://www.w3.org/2004/02/skos/core#")

for path in sys.argv[1:]:
    g = rdflib.Graph()
    g.parse(path, format="turtle")
    classes = set(g.subjects(RDF.type, OWL.Class)) | set(g.subjects(RDF.type, RDFS.Class))
    classes = {c for c in classes if isinstance(c, rdflib.URIRef)}

    def label(c):
        for p in (SKOS.prefLabel, RDFS.label):
            for o in g.objects(c, p):
                return str(o)
        return ""

    # 数据属性 → (名, 定义域)
    props = {}
    for p in g.subjects(RDF.type, OWL.DatatypeProperty):
        name = str(p).rsplit("#", 1)[-1]
        doms = [str(d).rsplit("#", 1)[-1] for d in g.objects(p, RDFS.domain)]
        props[name] = doms

    def ancestors(c, seen=None):
        seen = seen or set()
        out = []
        for sup in g.objects(c, RDFS.subClassOf):
            if isinstance(sup, rdflib.URIRef) and sup not in seen:
                seen.add(sup)
                out.append(sup)
                out += ancestors(sup, seen)
        return out

    print("\n===== %s =====" % path)
    print("-- 类（%d）--  [带 * = 本类或其祖先带 wiki_content 数据属性]" % len(classes))
    for c in sorted(classes, key=lambda x: str(x)):
        anc = ancestors(c)
        names = {str(c).rsplit("#", 1)[-1]} | {str(a).rsplit("#", 1)[-1] for a in anc}
        wc = ""
        for pn, doms in props.items():
            if "wiki" in pn.lower() and (names & set(doms)):
                wc = "  ★wiki_content"
        isenum = bool(list(g.objects(c, OWL.oneOf)))
        print("  %-28s %-22s parents=%-42s%s%s"
              % (str(c).rsplit("#", 1)[-1], label(c),
                 ",".join(sorted(str(a).rsplit("#", 1)[-1] for a in anc))[:42],
                 wc, "  [enum]" if isenum else ""))
    print("-- 数据属性里的 wiki/正文 相关 --")
    for pn, doms in sorted(props.items()):
        if any(k in pn.lower() for k in ("wiki", "content", "text", "body")):
            print("  %-24s domain=%s" % (pn, doms))
