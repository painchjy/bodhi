"""产物：Neo4j 本体投影与跨层查询（`artifacts/neo4j/`）。

投影模型（**单向**：本体 -> 图，图上不做任何回写）
------------------------------------------------
    (BodhiModule)        -[:BODHI_DECLARES]->  (BodhiOntClass | BodhiOntProperty | BodhiEnumValue)
    (BodhiOntClass)      -[:BODHI_SUBCLASS_OF]-> (BodhiOntClass)
    (BodhiOntClass)      -[:BODHI_HAS_RESTRICTION]-> (BodhiRestriction) -[:BODHI_ON_PROPERTY]-> (BodhiOntProperty)
    (BodhiOntClass)      -[:BODHI_ENUM_MEMBER]-> (BodhiEnumValue)
    (BodhiOntProperty)   -[:BODHI_DOMAIN]-> (BodhiOntClass)
    (BodhiOntProperty)   -[:BODHI_RANGE]->  (BodhiOntClass)
    (BodhiOntProperty)   -[:BODHI_INVERSE_OF | :BODHI_SUB_PROPERTY_OF]-> (BodhiOntProperty)

边类型统一加 `BODHI_` 前缀，和 ontology/queries/*.cypher 里人工查询使用的名字一致（那是下游契约）。

为什么要投影到图里
------------------
执行层的跨层推理（「这条业务规则约束了哪些契约」「这个契约的操作对象是否越界」）本质是图遍历。
把 TBox 投影成节点与边后，约束可以用纯 Cypher 查询表达，**不需要把 OWL 推理机塞进执行路径**；
实例层（WeKnora 抽取的知识点）schema 由 WeKnora 管理，不在这里硬编码。

产物清单
--------
    00_constraints.cypher        约束与索引（幂等，可重复执行）
    10_ontology.cypher           类 / 属性 / 枚举 / 限制 / 继承与 domain-range 投影
    20_cross_layer_queries.cypher 人工查询模板 + 占位符替换（来自 ontology/queries/*.cypher）
    README.md                    投影模型与执行顺序说明
"""

from __future__ import annotations

import re
from pathlib import Path

from ontology_compiler.config import ARTIFACTS_DIR, QUERIES_DIR, cypher_tokens
from ontology_compiler.emitters._common import banner, write_text
from ontology_compiler.model import OntologyBundleView

LABELS = {
    "module": "BodhiModule",
    "class": "BodhiOntClass",
    "property": "BodhiOntProperty",
    "enum_value": "BodhiEnumValue",
    "restriction": "BodhiRestriction",
}

# 边类型词表：与 ontology/queries/*.cypher 里人工查询使用的名字**完全一致**（那是下游契约）。
REL = {
    "subclass": "BODHI_SUBCLASS_OF",
    "declares": "BODHI_DECLARES",
    "domain": "BODHI_DOMAIN",
    "range": "BODHI_RANGE",
    "inverse": "BODHI_INVERSE_OF",
    "sub_property": "BODHI_SUB_PROPERTY_OF",
    "restriction": "BODHI_HAS_RESTRICTION",
    "on_property": "BODHI_ON_PROPERTY",
    "enum_member": "BODHI_ENUM_MEMBER",
}


class Neo4jEmitter:
    name = "neo4j"

    def emit(self, bundle: OntologyBundleView, out_dir: Path | None = None) -> list[str]:
        root = Path(out_dir) if out_dir is not None else Path(ARTIFACTS_DIR)
        target = root / "neo4j"
        written = [
            write_text(target / "00_constraints.cypher", render_constraints(bundle)),
            write_text(target / "10_ontology.cypher", render_projection(bundle)),
            write_text(target / "20_cross_layer_queries.cypher", render_queries(bundle)),
            write_text(target / "README.md", render_readme(bundle)),
        ]
        return written


# --------------------------------------------------------------------------
# 字面量与头部
# --------------------------------------------------------------------------
def cypher_str(value) -> str:
    """Cypher 字符串字面量（单引号包裹并转义；None 输出 null）。"""
    if value is None:
        return "null"
    return "'%s'" % str(value).replace("\\", "\\\\").replace("'", "\\'")


def cypher_list(values) -> str:
    return "[" + ", ".join(cypher_str(value) for value in values) + "]"


def header(title: str, source: str, extra: list[str]) -> str:
    """Cypher 文件头：必须用 `//`（`banner()` 用的是 `#`，在 Cypher 里不是注释）。"""
    return cypher_header(title, source, extra)


def render_constraints(bundle: OntologyBundleView) -> str:
    lines = [
        header(
            "Neo4j 本体投影 · 约束与索引",
            "ontology/*.ttl（由 compiler 生成）",
            ["执行顺序：本文件 -> 10_ontology.cypher -> 20_cross_layer_queries.cypher", "幂等：全部使用 IF NOT EXISTS"],
        )
    ]
    lines.append("// 术语节点唯一键：本体名（iri）是唯一身份")
    for label in (LABELS["module"], LABELS["class"], LABELS["property"], LABELS["enum_value"], LABELS["restriction"]):
        lines.append("CREATE CONSTRAINT %s_iri IF NOT EXISTS FOR (n:%s) REQUIRE n.iri IS UNIQUE;" % (label.lower(), label))
    lines.append("")
    lines.append("// 常用检索索引")
    lines.append("CREATE INDEX bodhiontclass_prefixed IF NOT EXISTS FOR (n:%s) ON (n.prefixed);" % LABELS["class"])
    lines.append("CREATE INDEX bodhiontproperty_prefixed IF NOT EXISTS FOR (n:%s) ON (n.prefixed);" % LABELS["property"])
    lines.append("CREATE INDEX bodhiontclass_module IF NOT EXISTS FOR (n:%s) ON (n.module);" % LABELS["class"])
    lines.append("CREATE INDEX bodhiontproperty_module IF NOT EXISTS FOR (n:%s) ON (n.module);" % LABELS["property"])
    lines.append("CREATE INDEX bodhiontclass_label IF NOT EXISTS FOR (n:%s) ON (n.label);" % LABELS["class"])
    lines.append("")
    lines.append("// 投影统计（执行后核对：类 %d / 对象属性 %d / 数据属性 %d / 限制 %d）" % (len(bundle.classes), len(bundle.object_properties), len(bundle.data_properties), len(bundle.restrictions)))
    lines.append("MATCH (n) WHERE n.bodhi_projection = 'ontology' RETURN labels(n)[0] AS kind, count(*) AS count ORDER BY kind;")
    return "\n".join(lines)


def node_map(pairs: list[tuple[str, object]]) -> str:
    """`SET n += {..}` 里用的 Cypher map（值已转义）。"""
    return "{" + ", ".join("%s: %s" % (key, cypher_str(value)) for key, value in pairs) + "}"


def map_set(variable: str, pairs: list[tuple[str, object]]) -> str:
    """`SET n += {..}`（显式写入 null，覆盖上一次投影可能残留的值）。"""
    return "SET %s += %s;" % (variable, node_map(pairs))


def class_pairs(cls) -> list[tuple[str, object]]:
    return [
        ("prefixed", cls.prefixed),
        ("local_name", cls.local),
        ("prefix", cls.prefix),
        ("module", cls.module),
        ("label", cls.label),
        ("comment", cls.comment),
        ("is_enum", cls.is_enum),
        ("enum_style", cls.enum_style),
        ("bodhi_projection", "ontology"),
    ]


def property_pairs(prop) -> list[tuple[str, object]]:
    return [
        ("prefixed", prop.prefixed),
        ("local_name", prop.local),
        ("prefix", prop.prefix),
        ("module", prop.module),
        ("label", prop.label),
        ("comment", prop.comment),
        ("property_kind", prop.kind),
        ("is_functional", prop.is_functional),
        ("characteristics", ",".join(prop.characteristics)),
        # 数据属性的 range 是**字面量类型**（xsd:string 等），不是类：单独记在属性上，
        # 不再建"外部占位类"（否则界面会显示「未定义 … range · external」，见用户 2026-09-21 反馈）。
        ("range_literal", ",".join(prop.range_iris) if getattr(prop, "is_datatype", False) else ""),
        ("bodhi_projection", "ontology"),
    ]


# 标准词汇命名空间：出现 range/domain 里都**不算"未定义类"**（是字面量类型或内置词汇）。
STANDARD_VOCAB_PREFIXES = (
    "http://www.w3.org/2001/XMLSchema#",
    "http://www.w3.org/1999/02/22-rdf-syntax-ns#",
    "http://www.w3.org/2000/01/rdf-schema#",
    "http://www.w3.org/2002/07/owl#",
    "http://www.w3.org/ns/shacl#",
)


def is_standard_vocab(iri: str) -> bool:
    return any((iri or "").startswith(prefix) for prefix in STANDARD_VOCAB_PREFIXES)


def external_iris(bundle: OntologyBundleView) -> list[str]:
    """被引用但不在本体系声明里的 IRI（外部词汇）：建占位节点，避免投影出现断边。

    2026-09-21：**跳过标准词汇命名空间**（xsd / rdf / rdfs / owl / sh）——
    它们多出现在**数据属性的 range**（xsd:string 等）里，属于字面量类型/内置词汇，
    建占位类会被界面标成「未定义 … range · external」（用户实测反馈：bmm 也有）。
    数据属性的 range 现在记在属性节点的 `range_literal` 上。
    """
    declared = set(bundle.classes) | set(bundle.object_properties) | set(bundle.data_properties)
    found: list[str] = []

    def note(iri: str) -> None:
        if iri and iri not in declared and iri not in found and not is_standard_vocab(iri):
            found.append(iri)

    for cls in bundle.classes.values():
        for iri in cls.parents + cls.disjoints:
            note(iri)
        for restriction in cls.restrictions:
            if restriction.value_kind == "class" and isinstance(restriction.value, str):
                note(restriction.value)
    for prop in bundle.all_properties():
        for iri in prop.domain_iris + prop.range_iris:
            note(iri)
        if prop.inverse_of:
            note(prop.inverse_of)
        if prop.sub_property_of:
            note(prop.sub_property_of)
    return found


def edge(source_label: str, source_iri: str, rel: str, target_label: str, target_iri: str) -> list[str]:
    """一条关系边：先 MATCH 两端（缺节点即静默跳过），再 MERGE 边（可重复执行）。"""
    return [
        "MATCH (a:%s {iri: %s}), (b:%s {iri: %s})" % (source_label, cypher_str(source_iri), target_label, cypher_str(target_iri)),
        "MERGE (a)-[:%s]->(b);" % rel,
    ]


def relationship_pairs(bundle: OntologyBundleView) -> list[str]:
    """全部关系边的可读摘要（供 README 统计复用）。"""
    edges = iter_edges(bundle)
    counts: dict[str, int] = {}
    for item in edges:
        counts[item["rel"]] = counts.get(item["rel"], 0) + 1
    return ["%s x%d" % (rel, counts[rel]) for rel in sorted(counts)]


def iter_edges(bundle: OntologyBundleView) -> list[dict]:
    items: list[dict] = []

    def add(source_label: str, source_iri: str, rel: str, target_label: str, target_iri: str) -> None:
        items.append({"source_label": source_label, "source": source_iri, "rel": rel, "target_label": target_label, "target": target_iri})

    for key in bundle.module_order():
        spec = bundle.modules[key]
        for cls in bundle.sorted_classes(key):
            add(LABELS["module"], spec.ontology_iri, REL["declares"], LABELS["class"], cls.iri)
            for parent in cls.parents:
                add(LABELS["class"], cls.iri, REL["subclass"], LABELS["class"], parent)
            for member in cls.members:
                add(LABELS["class"], cls.iri, REL["enum_member"], LABELS["enum_value"], member.iri)
        for prop in bundle.sorted_properties(key):
            add(LABELS["module"], spec.ontology_iri, REL["declares"], LABELS["property"], prop.iri)
    for restriction in bundle.restrictions:
        add(LABELS["class"], restriction.owner_iri, REL["restriction"], LABELS["restriction"], restriction.key)
        add(LABELS["restriction"], restriction.key, REL["on_property"], LABELS["property"], restriction.on_property)
    for prop in bundle.all_properties():
        for iri in prop.domain_iris:
            add(LABELS["property"], prop.iri, REL["domain"], LABELS["class"], iri)
        for iri in prop.range_iris:
            add(LABELS["property"], prop.iri, REL["range"], LABELS["class"], iri)
        if prop.inverse_of:
            add(LABELS["property"], prop.iri, REL["inverse"], LABELS["property"], prop.inverse_of)
        if prop.sub_property_of:
            add(LABELS["property"], prop.iri, REL["sub_property"], LABELS["property"], prop.sub_property_of)
    return items


def render_properties(bundle: OntologyBundleView) -> list[str]:
    lines: list[str] = []
    for key in bundle.module_order():
        lines.append("// 模块 %s（%s）" % (key, bundle.modules[key].label))
        for prop in bundle.sorted_properties(key):
            lines.append("MERGE (p:%s {iri: %s})" % (LABELS["property"], cypher_str(prop.iri)))
            lines.append(map_set("p", property_pairs(prop)))
    lines.append("")
    return lines


def render_restrictions(bundle: OntologyBundleView) -> list[str]:
    lines: list[str] = []
    for restriction in bundle.restrictions:
        lines.append("MERGE (r:%s {iri: %s})" % (LABELS["restriction"], cypher_str(restriction.key)))
        lines.append(
            map_set(
                "r",
                [
                    ("key", restriction.key),
                    ("kind", restriction.kind),
                    ("value", restriction.value if isinstance(restriction.value, int) else None),
                    ("value_iri", restriction.value if isinstance(restriction.value, str) else None),
                    ("value_kind", restriction.value_kind),
                    ("value_display", restriction.value_display),
                    ("owner", restriction.owner_iri),
                    ("on_property", restriction.on_property),
                    ("comment", restriction.comment),
                    ("bodhi_projection", "ontology"),
                ],
            )
        )
    lines.append("")
    return lines


def render_relationships(bundle: OntologyBundleView) -> list[str]:
    """三类关系边的分组渲染（继承 / 枚举 / 限制 / domain-range / 属性特征 / 模块声明）。"""
    groups = [
        ("7.1 继承（子类 -> 父类）", {REL["subclass"]}),
        ("7.2 枚举归属（类 -> 取值）", {REL["enum_member"]}),
        ("7.3 限制挂载（类 -> 限制 -> 属性）", {REL["restriction"], REL["on_property"]}),
        ("7.4 domain / range（属性 -> 类）", {REL["domain"], REL["range"]}),
        ("7.5 属性之间的关系", {REL["inverse"], REL["sub_property"]}),
        ("7.6 模块声明（模块 -> 术语）", {REL["declares"]}),
    ]
    edges = iter_edges(bundle)
    lines: list[str] = []
    for title, rels in groups:
        lines.append("// %s" % title)
        selected = [item for item in edges if item["rel"] in rels]
        if not selected:
            lines.append("// （无）")
        for item in selected:
            lines.extend(edge(item["source_label"], item["source"], item["rel"], item["target_label"], item["target"]))
        lines.append("")
    return lines


def render_projection(bundle: OntologyBundleView) -> str:
    lines = [
        header(
            "Neo4j 本体投影 · 类 / 属性 / 枚举 / 限制 / 关系",
            "ontology/*.ttl（由 compiler 生成）",
            [
                "所有本体节点带 bodhi_projection='ontology'，用于与实例层数据区分",
                "顺序：模块 -> 外部占位 -> 类 -> 枚举取值 -> 属性 -> 限制 -> 关系边 -> 自检",
            ],
        ),
        "// ---- 1. 模块 ----",
    ]
    for key, spec in bundle.modules.items():
        lines.append("MERGE (m:%s {iri: %s})" % (LABELS["module"], cypher_str(spec.ontology_iri)))
        lines.append(
            map_set(
                "m",
                [
                    ("key", spec.key),
                    ("label", spec.label),
                    ("shortLabel", spec.short_label),
                    ("kind", spec.kind),
                    ("namespace", spec.namespace),
                    ("affects", ",".join(spec.affects)),
                    ("files", ",".join(spec.rel_files())),
                    ("bodhi_projection", "ontology"),
                ],
            )
        )
    lines.append("")
    lines.append("// ---- 2. 外部词汇占位节点（被引用但未在本体系声明）----")
    externals = external_iris(bundle)
    if not externals:
        lines.append("// （无）")
    for iri in externals:
        local = iri.rsplit("#", 1)[-1] if "#" in iri else iri.rsplit("/", 1)[-1]
        lines.append("MERGE (e:%s {iri: %s})" % (LABELS["class"], cypher_str(iri)))
        lines.append(
            map_set(
                "e",
                [
                    ("prefixed", iri),
                    ("local_name", local),
                    ("module", "external"),
                    ("external", True),
                    ("bodhi_projection", "ontology"),
                ],
            )
        )
    lines.append("")
    lines.append("// ---- 3. 类（含枚举类）----")
    for key in bundle.module_order():
        lines.append("// 模块 %s（%s）" % (key, bundle.modules[key].label))
        for cls in bundle.sorted_classes(key):
            lines.append("MERGE (c:%s {iri: %s})" % (LABELS["class"], cypher_str(cls.iri)))
            lines.append(map_set("c", class_pairs(cls)))
    lines.append("")
    lines.append("// ---- 4. 枚举取值 ----")
    for key in bundle.module_order():
        for cls in bundle.sorted_classes(key):
            for member in cls.members:
                lines.append("MERGE (v:%s {iri: %s})" % (LABELS["enum_value"], cypher_str(member.iri)))
                lines.append(
                    map_set(
                        "v",
                        [
                            ("prefixed", member.prefixed),
                            ("local_name", member.local),
                            ("module", member.module),
                            ("label", member.label),
                            ("owner", cls.prefixed),
                            ("bodhi_projection", "ontology"),
                        ],
                    )
                )
    lines.append("")
    lines.append("// ---- 5. 属性（对象属性 = 边模板；数据属性 = 节点属性模板）----")
    lines.extend(render_properties(bundle))
    lines.append("// ---- 6. OWL 限制（基数 / 值域）----")
    lines.extend(render_restrictions(bundle))
    lines.append("// ---- 7. 关系边 ----")
    lines.extend(render_relationships(bundle))
    lines.append("// ---- 8. 投影自检（执行后应返回 0 行）----")
    lines.append("MATCH (n) WHERE n.bodhi_projection IS NULL AND (n:BodhiOntClass OR n:BodhiOntProperty) RETURN n.iri;")
    lines.append("MATCH (p:BodhiOntProperty)-[:%s|%s]->(x) WHERE NOT x:BodhiOntClass RETURN p.iri, x.iri;" % (REL["domain"], REL["range"]))
    lines.append("MATCH (r:BodhiRestriction) WHERE NOT ()-[:%s]->(r) OR NOT (r)-[:%s]->() RETURN r.key;" % (REL["restriction"], REL["on_property"]))
    lines.append("MATCH (m:BodhiModule)-[:%s]->(t) WHERE t.module <> m.key RETURN m.key, t.iri;" % REL["declares"])
    return "\n".join(lines)


CYPHER_PLACEHOLDER = re.compile(r"\{\{[A-Z0-9_]+\}\}")


def cypher_header(title: str, source: str, extra: list[str] | None = None) -> str:
    """Cypher 注释形式（`//`）的文件头：`banner()` 用的是 `#`，在 Cypher 里不是注释。"""
    lines = [
        "// %s" % title,
        "//",
        "// 由 tools/ontology-compiler 自动生成 —— 请勿手改。",
        "// 真源：%s" % source,
        "// 重新生成：python tools/ontology-compiler/compile.py compile",
    ]
    for line in extra or []:
        lines.append("// %s" % line)
    return "\n".join(lines) + "\n"


def query_files() -> list[Path]:
    if not QUERIES_DIR.is_dir():
        return []
    return sorted(path for path in QUERIES_DIR.rglob("*.cypher") if path.is_file())


def query_tokens(bundle: OntologyBundleView) -> dict[str, str]:
    """占位符替换表：命名空间（自带引号）+ 生成时间（不带引号）。"""
    tokens = dict(cypher_tokens())
    tokens["{{GENERATED_AT}}"] = bundle.generated_at
    return tokens


def apply_tokens(text: str, tokens: dict[str, str]) -> tuple[str, list[str]]:
    """替换占位符并回报**未解析**的占位符（漏替换必须显式暴露，不能悄悄放行）。"""
    for key, value in tokens.items():
        text = text.replace(key, value)
    unresolved: list[str] = []
    for match in CYPHER_PLACEHOLDER.finditer(text):
        if match.group(0) not in unresolved:
            unresolved.append(match.group(0))
    return text, unresolved


def render_queries(bundle: OntologyBundleView) -> str:
    tokens = query_tokens(bundle)
    lines = [
        cypher_header(
            "跨层查询（人工模板 + 占位符替换）",
            "ontology/queries/*.cypher",
            [
                "每个查询前有 // ==== 段落标题，标出它来自哪个源文件",
                "执行前提：10_ontology.cypher 已投影完成",
            ],
        )
    ]
    files = query_files()
    if not files:
        lines.append("// 未找到 ontology/queries/*.cypher（模板为空）")
        return "\n".join(lines)
    for path in files:
        rel = path.relative_to(QUERIES_DIR.parent.parent).as_posix()
        text, unresolved = apply_tokens(path.read_text(encoding="utf-8"), tokens)
        lines.append("")
        lines.append("// %s" % ("=" * 72))
        lines.append("// ==== 来自 %s" % rel)
        if unresolved:
            lines.append("// !! 未替换的占位符（需在 config.cypher_tokens() 补登记）：%s" % ", ".join(unresolved))
        lines.append("// %s" % ("=" * 72))
        lines.append("")
        lines.append(text.rstrip())
    return "\n".join(lines)


def render_readme(bundle: OntologyBundleView) -> str:
    files = query_files()
    tokens = query_tokens(bundle)
    lines = [banner("Neo4j 本体投影说明", source="ontology/*.ttl + ontology/queries/*.cypher"), ""]
    lines.append("# artifacts/neo4j —— 本体投影与跨层查询")
    lines.append("")
    lines.append("## 1. 执行顺序（必须按序）")
    lines.append("")
    lines.append("| 顺序 | 文件 | 作用 |")
    lines.append("| --- | --- | --- |")
    lines.append("| 1 | `00_constraints.cypher` | 唯一约束与索引（`IF NOT EXISTS`，幂等）|")
    lines.append("| 2 | `10_ontology.cypher` | 类 / 属性 / 枚举 / 限制 / 继承与 domain-range 投影 |")
    lines.append("| 3 | `20_cross_layer_queries.cypher` | 跨层查询模板（占位符已替换）|")
    lines.append("")
    lines.append("```bash")
    lines.append("cypher-shell -a bolt://localhost:7687 -u neo4j -p <password> -f 00_constraints.cypher")
    lines.append("cypher-shell -a bolt://localhost:7687 -u neo4j -p <password> -f 10_ontology.cypher")
    lines.append("```")
    lines.append("")
    lines.append("## 2. 投影模型")
    lines.append("")
    lines.append("```")
    lines.append("(BodhiModule)-[:BODHI_DECLARES]->(BodhiOntClass | BodhiOntProperty | BodhiEnumValue)")
    lines.append("(BodhiOntClass)-[:BODHI_SUBCLASS_OF]->(BodhiOntClass)")
    lines.append("(BodhiOntClass)-[:BODHI_HAS_RESTRICTION]->(BodhiRestriction)-[:BODHI_ON_PROPERTY]->(BodhiOntProperty)")
    lines.append("(BodhiOntClass)-[:BODHI_ENUM_MEMBER]->(BodhiEnumValue)")
    lines.append("(BodhiOntProperty)-[:BODHI_DOMAIN|BODHI_RANGE]->(BodhiOntClass)")
    lines.append("(BodhiOntProperty)-[:BODHI_INVERSE_OF|BODHI_SUB_PROPERTY_OF]->(BodhiOntProperty)")
    lines.append("```")
    lines.append("")
    lines.append("- 所有本体节点都带 `bodhi_projection = 'ontology'`，与实例层数据区分；清理投影时按此标记删。")
    lines.append("- 被引用但未在本体系声明的 IRI 会建 `external = true` 占位节点，避免投影出现断边。")
    lines.append("")
    lines.append("## 3. 本次投影规模")
    lines.append("")
    lines.append("| 指标 | 数量 |")
    lines.append("| --- | --- |")
    for key, value in bundle.stats().items():
        lines.append("| %s | %d |" % (key, value))
    lines.append("| 外部占位节点 | %d |" % len(external_iris(bundle)))
    lines.append("")
    lines.append("## 4. 人工查询模板")
    lines.append("")
    if files:
        lines.append("源文件（产出前会替换占位符）：")
        lines.append("")
        for path in files:
            lines.append("- `%s`" % path.relative_to(QUERIES_DIR.parent.parent).as_posix())
    else:
        lines.append("（`ontology/queries/` 下暂无 `*.cypher`）")
    lines.append("")
    lines.append("占位符替换表（来自 `config.cypher_tokens()`，值已带引号，可直接嵌入 Cypher）：")
    lines.append("")
    lines.append("| 占位符 | 替换为 |")
    lines.append("| --- | --- |")
    for key, value in sorted(tokens.items()):
        lines.append("| `%s` | `%s` |" % (key, value))
    return "\n".join(lines)




