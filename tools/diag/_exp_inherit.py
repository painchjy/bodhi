"""D2 实验：**报告基类 + 跨模块 subClassOf 继承** 能否让 render_mode 生效。

做四件事（**全在沙箱，不碰仓库本体/产物**）：
  1. 复制 ontology/sources → /tmp/ont-exp（沙箱）
  2. 改沙箱 TTL：`bmm:Report`（基类）+ `wikiContent` domain 改指 Report +
     agent 侧 4 个报告类由「domain 追加」改为 `rdfs:subClassOf bmm:Report`；`Assessment` 不继承
  3. 用**真编译器**（compile_all，out_dir=沙箱产物）编译：基线 + 改动后各一次
  4. 用**真 `ke_ontology.render_mode`**（INDEX_PATH 指向沙箱产物）逐类验证

用法：BODHI_NEO4J_HTTP=http://127.0.0.1:1 /opt/bodhi-venv/bin/python3 tools/diag/_exp_inherit.py
"""
import importlib
import pathlib
import shutil
import sys

REPO = pathlib.Path(__file__).resolve().parents[2]
# ⚠️ 沙箱必须**在仓库内**：编译器的 `ModuleSpec.rel_files()` 会做 `relative_to(REPO_ROOT)`
SB_ROOT = REPO / "tools" / "diag" / ".exp"          # 用完即删（git 里不留）
SB_SRC = SB_ROOT / "src"                           # 沙箱 TTL
SB_ART = SB_ROOT / "artifacts"                     # 沙箱编译产物
WATCH = ["bmm:Assessment", "agent:Advice", "agent:DesignSpec", "agent:Evaluation",
         "agent:InstallGuide", "agent:ToolContract", "agent:Agent", "agent:Skill",
         "bmm:BusinessProcess", "bmm:Report"]

BMM_OLD = (':wikiContent rdf:type owl:DatatypeProperty ;\n'
           '    rdfs:label "正文"@zh ;\n'
           '    rdfs:domain :Assessment ;')
BMM_NEW = (':wikiContent rdf:type owl:DatatypeProperty ;\n'
           '    rdfs:label "正文"@zh ;\n'
           '    rdfs:domain :Report ;')
REPORT_CLS = '''

### 36. 报告基类（D2 实验：过程件基类，带整篇正文）（2026-10-11）
:Report rdf:type owl:Class ;
    rdfs:label "报告"@zh ;
    rdfs:comment "过程件基类：报告/方案/评阅/结项等，带整篇正文，供人讨论·评审·反馈·溯源·取证；不参与机器判定。"@zh .
'''
AG_OLD = ('bmm:wikiContent rdfs:domain :Advice ;\n'
          '    rdfs:domain :DesignSpec ;\n'
          '    rdfs:domain :Evaluation ;\n'
          '    rdfs:domain :InstallGuide .')
AG_NEW = (':Advice rdfs:subClassOf bmm:Report .\n'
          ':DesignSpec rdfs:subClassOf bmm:Report .\n'
          ':Evaluation rdfs:subClassOf bmm:Report .\n'
          ':InstallGuide rdfs:subClassOf bmm:Report .')


def stage_sources(patched: bool) -> pathlib.Path:
    """准备沙箱 TTL：patched=False → 原样（基线）；True → 按 D2 方案改。"""
    if SB_SRC.exists():
        shutil.rmtree(SB_SRC, ignore_errors=True)
    shutil.copytree(REPO / "ontology" / "sources", SB_SRC)
    if patched:
        b = SB_SRC / "bmm.ttl"
        t = b.read_text(encoding="utf-8")
        assert t.count(BMM_OLD) == 1, "bmm.ttl 里 wikiContent 块不唯一：%d" % t.count(BMM_OLD)
        b.write_text(t.replace(BMM_OLD, BMM_NEW) + REPORT_CLS, encoding="utf-8")
        a = SB_SRC / "agent.ttl"
        t2 = a.read_text(encoding="utf-8")
        assert t2.count(AG_OLD) == 1, "agent.ttl 里 domain 追加块不唯一：%d" % t2.count(AG_OLD)
        a.write_text(t2.replace(AG_OLD, AG_NEW), encoding="utf-8")
    return SB_SRC


def compile_sandbox(sb: pathlib.Path, out: pathlib.Path):
    sys.path.insert(0, str(REPO / "tools" / "ontology-compiler"))
    import ontology_compiler.config as cfg
    from ontology_compiler.compiler import compile_all
    cfg.SOURCES_DIR = sb                       # ← 沙箱 TTL
    mods = cfg.build_modules()
    if out.exists():
        shutil.rmtree(out, ignore_errors=True)
    res = compile_all(modules=mods, out_dir=out, prune=False,
                      generated_at="2026-01-01T00:00:00Z")
    return res


def render_table(index_path: pathlib.Path):
    sys.path.insert(0, str(REPO / "tools" / "ke-core"))
    import ke_ontology as ko
    ko.INDEX_PATH = index_path
    ko = importlib.reload(ko)                 # 清缓存（index + memo）
    ko.INDEX_PATH = index_path
    rows = []
    for t in WATCH:
        try:
            rows.append((t, ko.render_mode(t)))
        except Exception as exc:              # noqa: BLE001
            rows.append((t, "ERR:%s" % exc))
    # 机制证据：wikiContent 的 domain + 若干类的父链
    info = {}
    try:
        props = ko.data_properties().get("properties") or {}
        for name, v in props.items():
            if str(name).split(":")[-1].replace("_", "").lower() == "wikicontent":
                info["wikiContent.domain"] = (v or {}).get("domains")
        for t in ("agent:Advice", "agent:DesignSpec", "bmm:Assessment"):
            info["parents(%s)" % t] = (ko.class_meta().get(t) or {}).get("parents")
    except Exception as exc:                  # noqa: BLE001
        info["ERR"] = str(exc)
    return rows, info


def expected_from_ttl(sb: pathlib.Path):
    """按 `render_mode` 文档化规则，从**合并 TTL** 复算期望值（链 ∩ wikiContent.domains，只看显式 domain）。

    为什么不用编译产物：`data_properties()` 只读 Neo4j（JSON 里数据属性只有计数、没有名单），
    所以沙箱里只能从 TTL 复算「期望值」，运行时抽查留到重投影之后。
    """
    import rdflib
    OWL = rdflib.Namespace("http://www.w3.org/2002/07/owl#")
    RDFS = rdflib.RDFS
    g = rdflib.Graph()
    for f in sorted(sb.glob("*.ttl")):
        g.parse(str(f), format="turtle")
    doms_raw = []
    for p in g.subjects(rdflib.RDF.type, OWL.DatatypeProperty):
        if str(p).rsplit("#", 1)[-1].replace("_", "").lower() == "wikicontent":
            doms_raw = list(g.objects(p, RDFS.domain))
    BASES = {"bmm": "http://example.org/bmm#", "agent": "http://example.org/agent#"}

    def to_pref(u):
        s = str(u)
        for m, b in BASES.items():
            if s.startswith(b):
                return "%s:%s" % (m, s[len(b):])
        return s

    doms_pref = sorted({to_pref(d) for d in doms_raw})

    def parents(c):
        out = []
        for sup in g.objects(c, RDFS.subClassOf):
            if isinstance(sup, rdflib.URIRef):
                out.append(str(sup).rsplit("#", 1)[-1])
        return out

    pfx = {"bmm": "http://example.org/bmm#", "agent": "http://example.org/agent#"}

    def chain(t):
        mod, loc = t.split(":")
        cur = rdflib.URIRef(pfx.get(mod, "") + loc)
        out, seen = [], set()
        while cur and cur not in seen:
            out.append("%s:%s" % (mod, str(cur).rsplit("#", 1)[-1]))
            ps = [x for x in parents(cur)]
            cur = rdflib.URIRef(pfx.get(mod, "") + ps[0]) if ps else None
        return out

    rows = []
    for t in WATCH:
        try:
            ch = set(chain(t))
            hit = bool(ch & set(doms_pref))
            rows.append((t, "document" if hit else "entity",
                         "链=%s ; 命中 domain=%s" % (",".join(ch), sorted(ch & set(doms_pref)) or "-")))
        except Exception as exc:                     # noqa: BLE001
            rows.append((t, "ERR", str(exc)[:80]))
    return doms_pref, rows


def chains_from_index(idx: pathlib.Path) -> dict:
    """从编译产物读 parents（**权威**：已跨模块解析、已滤掉 owl:Thing）→ {类: 父链}。"""
    import json
    data = json.loads(idx.read_text(encoding="utf-8"))
    parents: dict[str, list] = {}
    for m in data.get("models") or []:
        for c in m.get("classes") or []:
            parents[c["name"]] = list(c.get("parents") or [])
    chains = {}
    for t in WATCH:
        out, seen, cur = [], set(), t
        while cur and cur not in seen:
            out.append(cur)
            seen.add(cur)
            ps = parents.get(cur) or []
            cur = ps[0] if ps else ""
        chains[t] = out
    return chains


def expected(doms_pref, chains):
    rows = []
    for t in WATCH:
        ch = chains.get(t) or [t]
        hit = sorted(set(ch) & set(doms_pref))
        rows.append((t, "document" if hit else "entity",
                     "链=%s ; 命中=%s" % (",".join(ch), hit or "-")))
    return rows


def live_check():
    """生产环境（**真 Neo4j**）现状：wikiContent 的 domain + 逐类 render_mode。"""
    sys.path.insert(0, str(REPO / "tools" / "ke-core"))
    import ke_ontology as ko
    props = ko.data_properties()
    print("data_properties source =", props.get("source"))
    for name, v in (props.get("properties") or {}).items():
        if str(name).split(":")[-1].replace("_", "").lower() == "wikicontent":
            print("  %s domains = %s" % (name, (v or {}).get("domains")))
    for t in WATCH:
        try:
            print("  %-22s render_mode = %-9s ancestors=%s"
                  % (t, ko.render_mode(t), ko.ancestors(t)))
        except Exception as exc:                     # noqa: BLE001
            print("  %-22s ERR %s" % (t, exc))


def main():
    if "--live" in sys.argv:
        print("========== 生产现状（真 Neo4j，只读） ==========")
        live_check()
        return
    out = {}
    for tag, patched in (("基线（现状：domain 逐个追加）", False),
                         ("改动后（继承 bmm:Report）", True)):
        sb = stage_sources(patched)
        art = SB_ART / ("patched" if patched else "base")
        res = compile_sandbox(sb, art)
        print("\n========== %s ==========" % tag)
        print("compile ok=%s  wrote_artifacts=%s"
              % (getattr(res, "ok", None), getattr(res, "wrote_artifacts", None)))
        errs = list(getattr(res, "errors", []) or [])[:6]
        for e in errs:
            print("   ! ", str(e)[:220])
        idx = art / "weknora" / "ontology_index.json"
        if idx.exists():
            _, info = render_table(idx)
            for k, v in info.items():
                print("   · %s = %s" % (k, v))
        doms, _ = expected_from_ttl(sb)
        rows = expected(doms, chains_from_index(idx) if idx.exists() else {})
        print("   wikiContent.domains = %s" % doms)
        w = max(len(t) for t, _, _ in rows)
        for t, mode, why in rows:
            print("   %-*s  期望=%-8s  %s" % (w, t, mode, why))
        out[tag] = (getattr(res, "ok", None), idx.exists(), doms)
    print("\n========== 结论速览 ==========")
    for k, v in out.items():
        print("  %s → 编译 ok=%s / 产物=%s / domains=%s" % (k, v[0], v[1], v[2]))
    print("\n注：`render_mode` 运行时只读 Neo4j 投影（data_properties 无 JSON 兜底）→"
          " 上线必须先**重投影**（refresh_ontology_kb.sh / /bodhi/ontology/load），再抽查 render_mode。")


if __name__ == "__main__":
    main()

