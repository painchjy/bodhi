"""v0.5 TTL 变更（D1–D3 已裁决）—— 默认 dry-run 打印 diff，`--apply` 才写。

bmm.ttl：
  · 加 `@prefix skos:`；`bmm:wikiContent` 的 `rdfs:domain` 由 `:Assessment` 改为 **`:Report`**
  · 新增 `:Report`（报告·基类）与 `:RequirementAnalysisReport`（需求分析报告｜别名 需求规格说明书）
agent.ttl：
  · 4 个既有报告类（Advice/DesignSpec/Evaluation/InstallGuide）的 `rdfs:subClassOf owl:Thing` → `bmm:Report`
  · 删掉 4 行 `bmm:wikiContent rdfs:domain …`（由"domain 追加"改为"继承"）
  · 新增 `:EvaluationPlan`（评测方案，子类 of `bmm:Report`）

用法：python3 tools/diag/_ttl_patch_v05.py [--apply]
"""
import difflib
import pathlib
import re
import sys

REPO = pathlib.Path(__file__).resolve().parents[2]
SRC = REPO / "ontology" / "sources"
APPLY = "--apply" in sys.argv

SKOS = '@prefix skos: <http://www.w3.org/2004/02/skos/core#> .\n'
NEW_BMM_CLASSES = '''
# ============================================================
### 36. 报告基类 Report（2026-10-11 新增）
# **过程件基类**：报告/方案/评审/结项等**整篇正文**的载体，给人讨论·评审·反馈·溯源·取证；
# **不参与机器判定**（机器判定只读 entity 资产）。谁继承它，谁就按 `document` 渲染
# （`ke_ontology.render_mode` 的判据：类或祖先声明了带 domain 的 `wikiContent`）。
:Report rdf:type owl:Class ;
    rdfs:label "报告"@zh ;
    rdfs:comment "过程件基类：承载整篇正文，供人讨论·评审·反馈·溯源·取证；不参与机器判定。继承它的类按 document 渲染。"@zh ;
    rdfs:subClassOf owl:Thing .

### 37. 需求分析报告 RequirementAnalysisReport（2026-10-11 新增；别名：需求规格说明书）
:RequirementAnalysisReport rdf:type owl:Class ;
    rdfs:label "需求分析报告"@zh ;
    skos:altLabel "需求规格说明书"@zh ;
    rdfs:comment "「立项与需求分析」活动的产出：范围/目标/边界/需求项与验收口径；是后续设计（设计单）的依据。"@zh ;
    rdfs:subClassOf :Report .
'''
NEW_AG_CLASS = '''
### 报告·评测方案 EvaluationPlan（2026-10-11 新增）
:EvaluationPlan rdf:type owl:Class ;
    rdfs:label "评测方案"@zh ;
    rdfs:comment "「开发交付」活动的产出、「评测」活动的输入：指标/用例/样本/通过门槛（对齐 agent:Metric）。"@zh ;
    rdfs:subClassOf bmm:Report .
'''


def patch_bmm(t: str) -> str:
    if "@prefix skos:" not in t:
        t = t.replace("@prefix bodhi: <http://example.org/bodhi#> .\n",
                      "@prefix bodhi: <http://example.org/bodhi#> .\n" + SKOS, 1)
    old_dom = (':wikiContent rdf:type owl:DatatypeProperty ;\n'
               '    rdfs:label "正文"@zh ;\n'
               '    rdfs:domain :Assessment ;')
    new_dom = (':wikiContent rdf:type owl:DatatypeProperty ;\n'
               '    rdfs:label "正文"@zh ;\n'
               '    rdfs:domain :Report ;')
    assert t.count(old_dom) == 1, "wikiContent domain 块不唯一：%d" % t.count(old_dom)
    t = t.replace(old_dom, new_dom)
    t = t.replace('rdfs:comment "文档类的整篇正文（Markdown）。**有它的类按 document 渲染**：评估/评审报告这类半结构化知识——正文是主体，数据属性只做结构化摘要；其余类不要用本属性（它们是 entity，正文由数据属性生成）。"@zh .',
                  'rdfs:comment "报告类的整篇正文（Markdown）。**声明它的类（或祖先）按 document 渲染**：报告是正文主体、数据属性只做结构化摘要；entity 类不要用本属性。"@zh .')
    assert ":Report rdf:type owl:Class" not in t, "bmm.ttl 已有 :Report"
    t = t.rstrip("\n") + "\n" + NEW_BMM_CLASSES
    return t


def patch_agent(t: str) -> str:
    # ① 4 个报告类：owl:Thing → bmm:Report
    for name in ("Advice", "DesignSpec", "Evaluation", "InstallGuide"):
        pat = re.compile(r"(^:%s rdf:type owl:Class ;(?:.*?\n)*?    rdfs:subClassOf )owl:Thing \." % name,
                         re.MULTILINE)
        t2, n = pat.subn(r"\1bmm:Report .", t)
        assert n == 1, "%s 的 subClassOf 未唯一命中（%d）" % (name, n)
        t = t2
    # ② 删掉 4 行 domain 追加
    old = ("bmm:wikiContent rdfs:domain :Advice ;\n"
           "    rdfs:domain :DesignSpec ;\n"
           "    rdfs:domain :Evaluation ;\n"
           "    rdfs:domain :InstallGuide .")
    assert t.count(old) == 1, "domain 追加块不唯一：%d" % t.count(old)
    t = t.replace(old, "# 2026-10-11：报告类改为**继承** `bmm:Report`（见上方 4 个类的 subClassOf），不再逐个追加 domain。")
    t = t.rstrip("\n") + "\n" + NEW_AG_CLASS
    return t


PAIRS = [
    (SRC / "bmm.ttl", patch_bmm),
    (SRC / "agent.ttl", patch_agent),
    # 「完整版」是 sources 的**同名副本**（交付包按内容去重），必须一起改，否则两份不一致
    (REPO / "ontology" / "BMM完整版.ttl", patch_bmm),
    (REPO / "ontology" / "智能体开发完整版.ttl", patch_agent),
]

for p, fn in PAIRS:
    before = p.read_text(encoding="utf-8")
    after = fn(before)
    print("\n===== %s =====" % p.relative_to(REPO).as_posix())
    d = list(difflib.unified_diff(before.splitlines(True), after.splitlines(True),
                                  "before", "after", n=2))
    print("".join(d[:60]) if d else "（无变化）")
    if APPLY and before != after:
        p.write_text(after, encoding="utf-8")
        print("→ 已写入 %s" % p.relative_to(REPO).as_posix())
print("\n%s" % ("已应用。" if APPLY else "**dry-run**（未写文件）；确认后加 --apply。"))
