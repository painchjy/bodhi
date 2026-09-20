"""从 TTL 载入编译期模型（ontology/*.ttl -> OntologyBundleView）。

载入流程
--------
1. 按模块的**显式文件清单**逐文件解析 Turtle，每模块得到一个 Graph，同时合并进总 Graph；
2. 「模块归属」以**声明位置**为准：某个类/属性在哪个模块的文件里被声明，就属于哪个模块；
3. 跨模块引用（rdfs:subClassOf / rdfs:domain / rdfs:range / owl:onProperty 指向别的模块）
   是被显式允许的——这正是「跨层桥」的来源，因此解析总在总 Graph 上进行；
4. 枚举取值既支持 `owl:oneOf` 列表，也支持「个体声明 + rdf:type 枚举类」两种写法。

失败策略：文件缺失 / Turtle 语法错误 -> 直接抛异常（编译必须停在明确的错误上，
不允许产出半成品产物）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from os import environ
from pathlib import Path

import rdflib
from rdflib import BNode, Graph, Literal, URIRef

from ontology_compiler.config import (
    EXPERT_ROLE,
    GENERATED_AT_ENV,
    NS,
    OWL,
    OWL_THING,
    RDF,
    RDF_TYPE,
    RDFS,
    RESERVED_CLASSES,
    XSD,
    ModuleSpec,
    build_modules,
    namespace_of,
)
from ontology_compiler.model import (
    KIND_COMPLEMENT,
    KIND_INTERSECTION,
    KIND_NAMED,
    KIND_ONEOF,
    KIND_RESTRICTION,
    KIND_UNKNOWN,
    KIND_UNION,
    RESTRICTION_KINDS,
    ClassExpr,
    EnumMember,
    EnumMember as _EnumMember,  # noqa: F401  （保持导出语义清晰）
    OntClass,
    OntProperty,
    OntologyBundleView,
    Restriction,
)

# ---- rdflib 谓词常量 -----------------------------------------------------
# 注意：`config.RDF_TYPE` 是**字符串**（供 IRI 拼接），而 rdflib 7.x 不会把 str 谓词
# 隐式转成 URIRef —— 直接拿字符串去查询会静默返回空集（曾经导致「类 0 / 属性 0」）。
# 因此这里必须重新包成 URIRef 再用于所有三元组查询。
RDF_TYPE = URIRef(RDF_TYPE)
RDFS_LABEL = URIRef(RDFS + "label")
RDFS_COMMENT = URIRef(RDFS + "comment")
RDFS_SUBCLASS = URIRef(RDFS + "subClassOf")
RDFS_SUBPROPERTY = URIRef(RDFS + "subPropertyOf")
RDFS_DOMAIN = URIRef(RDFS + "domain")
RDFS_RANGE = URIRef(RDFS + "range")

OWL_CLASS = URIRef(OWL + "Class")
OWL_OBJECT_PROPERTY = URIRef(OWL + "ObjectProperty")
OWL_DATATYPE_PROPERTY = URIRef(OWL + "DatatypeProperty")
OWL_ANNOTATION_PROPERTY = URIRef(OWL + "AnnotationProperty")
OWL_RESTRICTION = URIRef(OWL + "Restriction")
OWL_ON_PROPERTY = URIRef(OWL + "onProperty")
OWL_INVERSE_OF = URIRef(OWL + "inverseOf")
OWL_EQUIVALENT_CLASS = URIRef(OWL + "equivalentClass")
OWL_DISJOINT_WITH = URIRef(OWL + "disjointWith")
OWL_UNION_OF = URIRef(OWL + "unionOf")
OWL_INTERSECTION_OF = URIRef(OWL + "intersectionOf")
OWL_COMPLEMENT_OF = URIRef(OWL + "complementOf")
OWL_ONEOF = URIRef(OWL + "oneOf")
OWL_NAMED_INDIVIDUAL = URIRef(OWL + "NamedIndividual")

CHARACTERISTIC_PREDICATES = {
    OWL + "FunctionalProperty": "functional",
    OWL + "InverseFunctionalProperty": "inverse-functional",
    OWL + "TransitiveProperty": "transitive",
    OWL + "SymmetricProperty": "symmetric",
    OWL + "AsymmetricProperty": "asymmetric",
    OWL + "ReflexiveProperty": "reflexive",
    OWL + "IrreflexiveProperty": "irreflexive",
}

RESTRICTION_PREDICATES: dict[str, str] = {
    OWL + kind: kind for kind in RESTRICTION_KINDS
}


# --------------------------------------------------------------------------
# 前缀与显示
# --------------------------------------------------------------------------
def build_prefix_map(modules: dict[str, ModuleSpec]) -> dict[str, str]:
    """命名空间 -> 前缀。

    用户口径（2026-09-20）：**前缀以本体定义为准** —— 先读各模块 TTL 里的 `@prefix` 声明，
    再用 config 里的 `spec.prefix` 兜底/补缺。这样 TTL 与编译产物不会出现「本体叫 `bmm-EA-ext:`、
    编译产物叫 `ea:`」这类不一致（曾经因此把 Neo4j 里的 `ea:Activity` 写成 `bmm-EA-ext:Activity`，
    前端按类查关系类型全空）。空前缀（`@prefix :`）忽略——它只是文件内部的默认前缀，不适合做展示前缀。
    """
    prefix_map = {spec.namespace: spec.prefix for spec in modules.values()}
    for spec in modules.values():
        for path in spec.files:
            try:
                text = pathlib.Path(path).read_text(encoding="utf-8")
            except Exception:  # noqa: BLE001
                continue
            for line in text.splitlines()[:40]:      # 前缀声明都在文件头
                match = re.match(r"\s*@prefix\s+([A-Za-z0-9_.-]*)\s*:\s*<([^>]+)>", line)
                if not match:
                    continue
                name, ns = match.group(1), match.group(2)
                if not name:
                    continue
                declared = prefix_map.get(ns)
                if declared and declared != name:
                    print("[compiler] 注意：%s 在 TTL 里声明为 `%s:`，config 里是 `%s`（以 TTL 为准）"
                          % (ns, name, declared), flush=True)
                prefix_map[ns] = name
    for prefix in ("owl", "rdf", "rdfs", "xsd", "sh"):
        prefix_map.setdefault(NS[prefix], prefix)
    return prefix_map


def display_iri(iri: str, prefix_map: dict[str, str]) -> str:
    namespace = namespace_of(iri)
    if namespace in prefix_map:
        return "%s:%s" % (prefix_map[namespace], iri[len(namespace) :])
    return "<%s>" % iri


def local_name(iri: str) -> str:
    """取 IRI 的最后一段作为本地名（与 rdflib 的 split_uri 相比更宽松、可预测）。"""
    namespace = namespace_of(iri)
    tail = iri[len(namespace) :]
    return tail or iri.rsplit("/", 1)[-1]


# rdf:List 展开所需的谓词（只在 rdf:List 辅助函数里使用）
RDF_FIRST = URIRef(RDF + "first")
RDF_REST = URIRef(RDF + "rest")
RDF_NIL = URIRef(RDF + "nil")


# --------------------------------------------------------------------------
# 解析：TTL -> Graph
# --------------------------------------------------------------------------
def parse_module_graphs(modules: dict[str, ModuleSpec]) -> tuple[dict[str, Graph], Graph, dict[str, list[str]]]:
    """逐模块解析 TTL，返回 (模块 Graph 表、合并 Graph、模块 -> 相对文件路径)。"""
    graphs: dict[str, Graph] = {}
    combined = Graph()
    source_files: dict[str, list[str]] = {}
    for key, spec in modules.items():
        missing = spec.missing_files()
        if missing:
            raise FileNotFoundError("模块 %s 缺少本体文件：%s" % (key, "、".join(missing)))
        graph = Graph()
        for path in spec.files:
            graph.parse(
                data=path.read_text(encoding="utf-8"),
                format="turtle",
                publicID=path.resolve().as_uri(),
            )
        graphs[key] = graph
        source_files[key] = spec.rel_files()
        for triple in graph:
            combined.add(triple)
    return graphs, combined, source_files


# --------------------------------------------------------------------------
# 字面量与类表达式
# --------------------------------------------------------------------------
def pick_labels(graph: Graph, subject) -> dict[str, str]:
    """收集 rdfs:label，key 为语言标签（无语言标签时为 ""）。"""
    labels: dict[str, str] = {}
    for value in graph.objects(subject, RDFS_LABEL):
        if isinstance(value, Literal):
            labels[str(value.language or "")] = str(value)
    return labels


def pick_comment(graph: Graph, subject) -> str | None:
    for value in graph.objects(subject, RDFS_COMMENT):
        if isinstance(value, Literal):
            return str(value)
    return None


def combine_display(kind: str, members: tuple[ClassExpr, ...]) -> str:
    return "%s(%s)" % (kind, "、".join(member.display for member in members))


def one_of_display(node, prefix_map: dict[str, str]) -> str:
    return display_iri(str(node), prefix_map) if isinstance(node, URIRef) else str(node)


def rdf_list_values(graph: Graph, head) -> list:
    """展开 rdf:List（owl:oneOf / owl:unionOf 的取值列表），带环路保护。"""
    values: list = []
    seen: set = set()
    node = head
    while node is not None and node != RDF_NIL and node not in seen:
        seen.add(node)
        first = graph.value(node, RDF_FIRST)
        if first is not None:
            values.append(first)
        node = graph.value(node, RDF_REST)
    return values


def restriction_hint(graph: Graph, node) -> str:
    """把限制节点的基数/值域渲染成简短提示，用于 display 文本。"""
    parts: list[str] = []
    for predicate, kind in RESTRICTION_PREDICATES.items():
        for value in graph.objects(node, URIRef(predicate)):
            parts.append("%s=%s" % (kind, value))
    return (": " + ", ".join(parts)) if parts else ""


def class_expression(graph: Graph, node, prefix_map: dict[str, str]) -> ClassExpr:
    """把 rdfs:subClassOf / rdfs:domain / rdfs:range 的取值归一化成 ClassExpr。"""
    if isinstance(node, URIRef):
        return ClassExpr(kind=KIND_NAMED, iri=str(node), display=display_iri(str(node), prefix_map))
    if not isinstance(node, BNode):
        return ClassExpr(kind=KIND_UNKNOWN, display=str(node))
    for predicate, kind in ((OWL_UNION_OF, KIND_UNION), (OWL_INTERSECTION_OF, KIND_INTERSECTION)):
        for rdf_list in graph.objects(node, predicate):
            members = tuple(class_expression(graph, item, prefix_map) for item in rdf_list_values(graph, rdf_list))
            if members:
                return ClassExpr(kind=kind, display=combine_display(kind, members), members=members)
    for inner in graph.objects(node, OWL_COMPLEMENT_OF):
        member = class_expression(graph, inner, prefix_map)
        return ClassExpr(kind=KIND_COMPLEMENT, display="complementOf(%s)" % member.display, members=(member,))
    if (node, OWL_ONEOF, None) in graph:
        values: list[str] = []
        for rdf_list in graph.objects(node, OWL_ONEOF):
            values.extend(one_of_display(item, prefix_map) for item in rdf_list_values(graph, rdf_list))
        return ClassExpr(kind=KIND_ONEOF, display="oneOf(%s)" % "、".join(values))
    if (node, OWL_ON_PROPERTY, None) in graph:
        prop = next(iter(graph.objects(node, OWL_ON_PROPERTY)))
        return ClassExpr(
            kind=KIND_RESTRICTION,
            display="restriction(%s%s)" % (display_iri(str(prop), prefix_map), restriction_hint(graph, node)),
        )
    return ClassExpr(kind=KIND_UNKNOWN, display="<匿名类>")


# --------------------------------------------------------------------------
# 限制（Restriction）
# --------------------------------------------------------------------------
def extract_restrictions(
    graph: Graph,
    owner_iri: str,
    prefix_map: dict[str, str],
    source_files: list[str],
) -> list[Restriction]:
    """抽取某个类通过 rdfs:subClassOf 挂载的限制节点（匿名 BNode + owl:onProperty）。"""
    result: list[Restriction] = []
    for node in graph.objects(URIRef(owner_iri), RDFS_SUBCLASS):
        if not isinstance(node, BNode) or (node, OWL_ON_PROPERTY, None) not in graph:
            continue
        prop = graph.value(node, OWL_ON_PROPERTY)
        comment = pick_comment(graph, node)
        for predicate, kind in RESTRICTION_PREDICATES.items():
            for value in graph.objects(node, URIRef(predicate)):
                if kind in ("someValuesFrom", "allValuesFrom"):
                    expr = class_expression(graph, value, prefix_map)
                    result.append(
                        Restriction(
                            owner_iri=owner_iri,
                            on_property=str(prop),
                            kind=kind,
                            value=expr.iri or expr.display,
                            value_kind="class",
                            value_display=expr.display,
                            comment=comment,
                            source_files=list(source_files),
                        )
                    )
                    continue
                try:
                    number = int(value)
                except (TypeError, ValueError):
                    continue
                result.append(
                    Restriction(
                        owner_iri=owner_iri,
                        on_property=str(prop),
                        kind=kind,
                        value=number,
                        value_kind="int",
                        value_display=str(number),
                        comment=comment,
                        source_files=list(source_files),
                    )
                )
    return sorted(result, key=lambda r: (r.on_property, r.kind))


# --------------------------------------------------------------------------
# 枚举取值
# --------------------------------------------------------------------------
def build_enum_member(
    combined: Graph,
    iri: str,
    owner_iri: str,
    module: str,
    prefix_map: dict[str, str],
    source_files: dict[str, list[str]],
) -> EnumMember:
    node = URIRef(iri)
    return EnumMember(
        iri=iri,
        local=local_name(iri),
        module=module,
        prefix=prefix_map.get(namespace_of(iri), "ext"),
        labels=pick_labels(combined, node),
        comment=pick_comment(combined, node),
        source_files=list(source_files.get(module, [])),
        owner_iri=owner_iri,
    )


def extract_enum_members(
    graphs: dict[str, Graph],
    combined: Graph,
    cls_iri: str,
    class_module: str,
    prefix_map: dict[str, str],
    source_files: dict[str, list[str]],
) -> tuple[list[EnumMember], str | None]:
    """抽取枚举取值，支持两种写法：`owl:oneOf` 列表，或「个体 rdf:type 枚举类」。

    为降低把**示例数据**误判成枚举取值的风险，个体形式只承认「与枚举类声明在同一模块文件中」
    的个体；`validate` 另外对取值个数异常多的"枚举"给出告警。
    """
    members: list[EnumMember] = []
    seen: set[str] = set()
    style: str | None = None
    cls_node = URIRef(cls_iri)

    for rdf_list in combined.objects(cls_node, OWL_ONEOF):
        style = "oneOf"
        for item in rdf_list_values(combined, rdf_list):
            iri = str(item)
            if iri in seen:
                continue
            seen.add(iri)
            members.append(build_enum_member(combined, iri, cls_iri, class_module, prefix_map, source_files))

    declaring_graphs = [
        (key, graph) for key, graph in graphs.items() if (cls_node, RDF_TYPE, OWL_CLASS) in graph
    ]
    for subject in combined.subjects(RDF_TYPE, cls_node):
        if not isinstance(subject, URIRef) or str(subject) == cls_iri:
            continue
        iri = str(subject)
        if iri in seen:
            continue
        owner_module = next((key for key, graph in declaring_graphs if (subject, RDF_TYPE, cls_node) in graph), None)
        if owner_module is None:
            continue
        seen.add(iri)
        style = style or "individuals"
        members.append(build_enum_member(combined, iri, cls_iri, owner_module, prefix_map, source_files))

    members.sort(key=lambda m: (m.module, m.local))
    return members, style


# --------------------------------------------------------------------------
# 类与属性
# --------------------------------------------------------------------------
def extract_classes(
    graphs: dict[str, Graph],
    combined: Graph,
    module_key: str,
    prefix_map: dict[str, str],
    source_files: dict[str, list[str]],
) -> list[OntClass]:
    """抽取某模块**声明**的类（以声明位置决定模块归属）。"""
    module_graph = graphs[module_key]
    spec_files = source_files.get(module_key, [])
    classes: list[OntClass] = []
    for subject in sorted(set(module_graph.subjects(RDF_TYPE, OWL_CLASS)), key=str):
        if not isinstance(subject, URIRef):
            continue
        iri = str(subject)
        if iri in RESERVED_CLASSES:
            continue
        parents: list[str] = []
        anonymous_parents: list[ClassExpr] = []
        for parent in combined.objects(subject, RDFS_SUBCLASS):
            if isinstance(parent, URIRef):
                if str(parent) not in RESERVED_CLASSES:
                    parents.append(str(parent))
                continue
            expr = class_expression(combined, parent, prefix_map)
            if expr.kind != KIND_RESTRICTION:  # 限制单独进 restrictions，不重复记录
                anonymous_parents.append(expr)
        members, enum_style = extract_enum_members(graphs, combined, iri, module_key, prefix_map, source_files)
        classes.append(
            OntClass(
                iri=iri,
                local=local_name(iri),
                module=module_key,
                prefix=prefix_map.get(namespace_of(iri), "ext"),
                labels=pick_labels(combined, subject),
                comment=pick_comment(combined, subject),
                source_files=list(spec_files),
                parents=sorted(set(parents)),
                anonymous_parents=anonymous_parents,
                equivalent=[
                    class_expression(combined, node, prefix_map)
                    for node in combined.objects(subject, OWL_EQUIVALENT_CLASS)
                ],
                disjoints=[
                    str(node) for node in combined.objects(subject, OWL_DISJOINT_WITH) if isinstance(node, URIRef)
                ],
                restrictions=extract_restrictions(combined, iri, prefix_map, spec_files),
                members=members,
                enum_style=enum_style,
            )
        )
    return classes


PROPERTY_KINDS: dict[str, str] = {
    str(OWL_OBJECT_PROPERTY): "object",
    str(OWL_DATATYPE_PROPERTY): "datatype",
}


def extract_properties(
    graphs: dict[str, Graph],
    combined: Graph,
    module_key: str,
    prefix_map: dict[str, str],
    source_files: dict[str, list[str]],
) -> list[OntProperty]:
    """抽取某模块声明的对象属性与数据属性（owl:AnnotationProperty 不参与编译）。"""
    module_graph = graphs[module_key]
    spec_files = source_files.get(module_key, [])
    properties: list[OntProperty] = []
    for rdf_type, kind in PROPERTY_KINDS.items():
        for subject in sorted(set(module_graph.subjects(RDF_TYPE, URIRef(rdf_type))), key=str):
            if not isinstance(subject, URIRef):
                continue
            characteristics = sorted(
                name
                for predicate, name in CHARACTERISTIC_PREDICATES.items()
                if (subject, RDF_TYPE, URIRef(predicate)) in combined
            )
            inverse = combined.value(subject, OWL_INVERSE_OF)
            sub_property = combined.value(subject, RDFS_SUBPROPERTY)
            properties.append(
                OntProperty(
                    iri=str(subject),
                    local=local_name(str(subject)),
                    module=module_key,
                    prefix=prefix_map.get(namespace_of(str(subject)), "ext"),
                    labels=pick_labels(combined, subject),
                    comment=pick_comment(combined, subject),
                    source_files=list(spec_files),
                    kind=kind,
                    domain=[
                        class_expression(combined, node, prefix_map)
                        for node in combined.objects(subject, RDFS_DOMAIN)
                    ],
                    range=[
                        class_expression(combined, node, prefix_map)
                        for node in combined.objects(subject, RDFS_RANGE)
                    ],
                    inverse_of=str(inverse) if isinstance(inverse, URIRef) else None,
                    sub_property_of=str(sub_property) if isinstance(sub_property, URIRef) else None,
                    characteristics=characteristics,
                )
            )
    return properties


# --------------------------------------------------------------------------
# 顶层装配
# --------------------------------------------------------------------------
def utcnow_iso() -> str:
    """生成物时间戳（UTC，秒级，带 Z），用于 manifest 与可复现性说明。"""
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def resolved_generated_at(explicit: str | None = None) -> str:
    """决定本次编译写进产物的 `generated_at`：显式参数 > 环境变量 > 当前 UTC 时间。

    固定时间戳（`BODHI_GENERATED_AT=2026-01-01T00:00:00Z`）只在两类场景使用：
    测试快照比对、CI 里判断「产物是否与本体同步」——否则每次编译的产物 sha256 都会变，
    `--diff` 会永远显示 changed。
    """
    if explicit and explicit.strip():
        return explicit.strip()
    pinned = environ.get(GENERATED_AT_ENV)
    return pinned.strip() if pinned and pinned.strip() else utcnow_iso()


def extract_expert_roles(graphs: dict[str, Graph], mods: dict[str, ModuleSpec]) -> dict[str, str]:
    """读每个模块 `owl:Ontology` 上的 `bodhi:expertRole`（专家角色）。

    角色属于本体的语义，因此定义在 TTL 而不是代码里（docs/weknora-fork.md §8.5）：
    换角色、加模块都不需要改编译器。缺注解的模块不出现在返回值里，由消费方决定是否报错。
    """
    predicate = URIRef(EXPERT_ROLE)
    roles: dict[str, str] = {}
    for key, spec in mods.items():
        graph = graphs.get(key)
        if graph is None:
            continue
        value = graph.value(URIRef(spec.ontology_iri), predicate)
        if value is None:
            continue
        text = str(value).strip()
        if text:
            roles[key] = text
    return roles


def load_ontology(
    modules: dict[str, ModuleSpec] | None = None,
    generated_at: str | None = None,
) -> OntologyBundleView:
    """载入全部模块并组装合并视图（编译的唯一入口）。

    `generated_at` 不传时按 `resolved_generated_at()` 的顺序取值
    （环境变量 `BODHI_GENERATED_AT` > 当前 UTC 时间）。
    """
    mods = modules if modules is not None else build_modules()
    prefix_map = build_prefix_map(mods)
    graphs, combined, source_files = parse_module_graphs(mods)

    classes: dict[str, OntClass] = {}
    object_properties: dict[str, OntProperty] = {}
    data_properties: dict[str, OntProperty] = {}
    for key in mods:
        for cls in extract_classes(graphs, combined, key, prefix_map, source_files):
            classes.setdefault(cls.iri, cls)
        for prop in extract_properties(graphs, combined, key, prefix_map, source_files):
            target = object_properties if prop.is_object else data_properties
            target.setdefault(prop.iri, prop)

    restrictions = [r for cls in classes.values() for r in cls.restrictions]
    for prop in list(object_properties.values()) + list(data_properties.values()):
        prop.restrictions = [r for r in restrictions if r.on_property == prop.iri]

    return OntologyBundleView(
        modules=mods,
        prefix_map=prefix_map,
        classes=classes,
        object_properties=object_properties,
        data_properties=data_properties,
        restrictions=restrictions,
        generated_at=resolved_generated_at(generated_at),
        source_files=source_files,
        expert_roles=extract_expert_roles(graphs, mods),
    )


def external_references(bundle: OntologyBundleView, module_key: str) -> dict[str, list[str]]:
    """某模块引用了哪些「不属于自己」的术语，按被引用模块分组（跨模块桥的显式清单）。"""
    collected: dict[str, set[str]] = {}

    def note(iri: str) -> None:
        owner = bundle.module_of(iri)
        if owner != module_key:
            collected.setdefault(owner, set()).add(iri)

    for cls in bundle.sorted_classes(module_key):
        for parent in cls.parents:
            note(parent)
        for expr in cls.anonymous_parents:
            for iri in expr.named_iris():
                note(iri)
        for restriction in cls.restrictions:
            note(restriction.on_property)
            if restriction.value_kind == "class" and isinstance(restriction.value, str):
                note(restriction.value)

    for prop in bundle.sorted_properties(module_key):
        for iri in prop.domain_iris + prop.range_iris:
            note(iri)
        if prop.inverse_of:
            note(prop.inverse_of)

    return {key: sorted(values) for key, values in sorted(collected.items())}


@dataclass
class ModuleView:
    """单模块视角：本模块声明的术语 + 被引用到的外部术语（供提示词/抽取配置/JSON Schema 共用）。"""

    key: str
    spec: ModuleSpec
    prefix_map: dict[str, str]
    classes: list[OntClass]
    object_properties: list[OntProperty]
    data_properties: list[OntProperty]
    referenced_classes: list[OntClass]
    referenced_properties: list[OntProperty]
    external_by_module: dict[str, list[str]]
    lexicon: dict = field(default_factory=dict)


def build_module_view(
    bundle: OntologyBundleView,
    module_key: str,
    lexicon: dict | None = None,
) -> ModuleView:
    """构造单模块视角。外部术语作为「上下文」一并给出，避免 LLM 在跨层桥上瞎猜。"""
    external = external_references(bundle, module_key)
    ref_class_iris: list[str] = []
    ref_property_iris: list[str] = []
    for iri in [item for values in external.values() for item in values]:
        if iri in bundle.classes and iri not in ref_class_iris:
            ref_class_iris.append(iri)
        elif iri in bundle.object_properties and iri not in ref_property_iris:
            ref_property_iris.append(iri)
        elif iri in bundle.data_properties and iri not in ref_property_iris:
            ref_property_iris.append(iri)
    return ModuleView(
        key=module_key,
        spec=bundle.modules[module_key],
        prefix_map=dict(bundle.prefix_map),
        classes=bundle.sorted_classes(module_key),
        object_properties=[p for p in bundle.sorted_properties(module_key) if p.is_object],
        data_properties=[p for p in bundle.sorted_properties(module_key) if p.is_datatype],
        referenced_classes=[bundle.classes[i] for i in ref_class_iris],
        referenced_properties=[
            bundle.object_properties.get(i) or bundle.data_properties[i] for i in ref_property_iris
        ],
        external_by_module=external,
        lexicon=dict(lexicon or {}),
    )


def build_module_views(bundle: OntologyBundleView, lexicons: dict[str, dict] | None = None) -> dict[str, ModuleView]:
    lex = lexicons or {}
    return {key: build_module_view(bundle, key, lex.get(key)) for key in bundle.module_order()}


# 兼容别名：包对外导出的模型名（`ontology_compiler.OntologyBundle`）
OntologyBundle = OntologyBundleView





