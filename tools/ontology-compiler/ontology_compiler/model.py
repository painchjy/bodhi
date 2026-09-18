"""编译期本体模型（IR：与 rdflib 解耦的中间表示）。

为什么要有这一层：直接在三元组上写发射器会让每个产物充满 `graph.objects(...)` 噪音，
且「模块归属 / 继承闭包 / 跨模块引用」这些概念每次都重复实现。这里把 TBox 归一化成
dataclass，发射器只依赖这些结构，可离线单测。

对应关系（TBox -> IR）：
    owl:Class                  -> OntClass
    owl:ObjectProperty         -> OntProperty(kind="object")
    owl:DatatypeProperty       -> OntProperty(kind="datatype")
    X rdfs:subClassOf [ owl:onProperty P ; owl:minCardinality n ]  -> Restriction
    rdfs:subClassOf / rdfs:domain / rdfs:range 里的联合类等          -> ClassExpr(kind="union"/...)
    owl:oneOf 或「类实例」形式的取值集合                            -> OntClass.members(EnumMember)
"""

from __future__ import annotations

from dataclasses import dataclass, field

# ---- ClassExpr.kind 取值 -------------------------------------------------
KIND_NAMED = "named"
KIND_UNION = "union"
KIND_INTERSECTION = "intersection"
KIND_COMPLEMENT = "complement"
KIND_RESTRICTION = "restriction"
KIND_ONEOF = "oneOf"
KIND_UNKNOWN = "unknown"

# ---- Restriction.kind 取值（与 OWL 限制词一一对应）-----------------------
RESTRICTION_KINDS = (
    "minCardinality",
    "maxCardinality",
    "cardinality",
    "someValuesFrom",
    "allValuesFrom",
)


@dataclass(frozen=True)
class ClassExpr:
    """类的表达式：具名类，或由 unionOf/intersectionOf/complementOf/oneOf 构成的匿名类。"""

    kind: str
    display: str
    iri: str | None = None
    members: tuple["ClassExpr", ...] = ()

    @property
    def is_named(self) -> bool:
        return self.kind == KIND_NAMED and self.iri is not None

    @property
    def is_anonymous(self) -> bool:
        return not self.is_named

    def named_iris(self) -> list[str]:
        """表达式里出现的所有具名类 IRI（递归展开，用于跨模块引用检查与 SHACL 生成）。"""
        if self.is_named:
            return [self.iri]  # type: ignore[list-item]
        found: list[str] = []
        for member in self.members:
            for iri in member.named_iris():
                if iri not in found:
                    found.append(iri)
        return found


@dataclass
class Term:
    """本体术语的公共部分。"""

    iri: str
    local: str
    module: str
    prefix: str
    labels: dict[str, str] = field(default_factory=dict)
    comment: str | None = None
    source_files: list[str] = field(default_factory=list)

    @property
    def label(self) -> str | None:
        """优先中文标签，其次无语言标签，最后任意标签。"""
        for key in ("zh", "zh-Hans", ""):
            if key in self.labels:
                return self.labels[key]
        if self.labels:
            return next(iter(self.labels.values()))
        return None

    @property
    def prefixed(self) -> str:
        return "%s:%s" % (self.prefix, self.local)

    def display(self) -> str:
        return "%s（%s）" % (self.label, self.prefixed) if self.label else self.prefixed


@dataclass
class EnumMember(Term):
    """枚举类的取值（owl:oneOf 成员，或以个体形式声明的取值）。"""

    owner_iri: str = ""


@dataclass
class Restriction:
    """一条 OWL 限制：owner 类上对 on_property 的基数/值域要求。"""

    owner_iri: str
    on_property: str
    kind: str
    value: int | str | None
    value_kind: str  # "int" | "class"
    value_display: str
    comment: str | None = None
    source_files: list[str] = field(default_factory=list)

    @property
    def key(self) -> str:
        return "%s|%s|%s|%s" % (self.owner_iri, self.on_property, self.kind, self.value)


@dataclass
class OntClass(Term):
    """一个类。枚举类（取值集合）复用同一结构，用 `members` 承载取值。"""

    parents: list[str] = field(default_factory=list)
    equivalent: list[ClassExpr] = field(default_factory=list)
    disjoints: list[str] = field(default_factory=list)
    anonymous_parents: list[ClassExpr] = field(default_factory=list)
    restrictions: list[Restriction] = field(default_factory=list)
    members: list[EnumMember] = field(default_factory=list)
    enum_style: str | None = None  # "oneOf" | "individuals" | None

    @property
    def is_enum(self) -> bool:
        return self.enum_style is not None or bool(self.members)

    @property
    def parent_locals(self) -> list[str]:
        return [iri.split("#")[-1] for iri in self.parents]


@dataclass
class OntProperty(Term):
    """一个对象属性或数据属性。"""

    kind: str = "object"  # "object" | "datatype"
    domain: list[ClassExpr] = field(default_factory=list)
    range: list[ClassExpr] = field(default_factory=list)
    inverse_of: str | None = None
    sub_property_of: str | None = None
    characteristics: list[str] = field(default_factory=list)
    restrictions: list[Restriction] = field(default_factory=list)

    @property
    def is_object(self) -> bool:
        return self.kind == "object"

    @property
    def is_datatype(self) -> bool:
        return self.kind == "datatype"

    @property
    def is_functional(self) -> bool:
        return "functional" in self.characteristics

    @property
    def domain_iris(self) -> list[str]:
        return [iri for expr in self.domain for iri in expr.named_iris()]

    @property
    def range_iris(self) -> list[str]:
        return [iri for expr in self.range for iri in expr.named_iris()]

    def domain_display(self) -> str:
        return "、".join(expr.display for expr in self.domain) or "（未声明 domain）"

    def range_display(self) -> str:
        return "、".join(expr.display for expr in self.range) or "（未声明 range）"


@dataclass
class OntologyBundleView:
    """跨模块合并后的本体视图——发射器唯一需要依赖的模型。"""

    modules: dict[str, ModuleSpec]
    prefix_map: dict[str, str]
    classes: dict[str, OntClass]
    object_properties: dict[str, OntProperty]
    data_properties: dict[str, OntProperty]
    restrictions: list[Restriction]
    generated_at: str
    source_files: dict[str, list[str]]
    # 模块 -> 专家角色（读自各模块 owl:Ontology 上的 bodhi:expertRole，见 config.EXPERT_ROLE）
    expert_roles: dict[str, str] = field(default_factory=dict)

    # ---- 基本集合 --------------------------------------------------------
    def all_properties(self) -> list[OntProperty]:
        return list(self.object_properties.values()) + list(self.data_properties.values())

    @property
    def enums(self) -> dict[str, OntClass]:
        return {iri: cls for iri, cls in self.classes.items() if cls.is_enum}

    def stats(self) -> dict[str, int]:
        return {
            "modules": len(self.modules),
            "classes": len(self.classes),
            "object_properties": len(self.object_properties),
            "data_properties": len(self.data_properties),
            "restrictions": len(self.restrictions),
            "enums": len(self.enums),
            "enum_values": sum(len(c.members) for c in self.enums.values()),
        }

    def module_order(self) -> list[str]:
        return list(self.modules.keys())

    def module_index(self) -> dict[str, int]:
        return {key: index for index, key in enumerate(self.module_order())}

    def module_of(self, iri: str) -> str:
        for key, spec in self.modules.items():
            if iri.startswith(spec.namespace):
                return key
        return "external"

    # ---- 查询 ------------------------------------------------------------
    def class_by_local(self, local: str, module: str | None = None) -> OntClass | None:
        if module is not None:
            return self.classes.get(self.modules[module].namespace + local)
        for cls in self.classes.values():
            if cls.local == local:
                return cls
        return None

    def property_by_local(self, local: str, module: str | None = None) -> OntProperty | None:
        if module is not None:
            iri = self.modules[module].namespace + local
            return self.object_properties.get(iri) or self.data_properties.get(iri)
        for prop in self.all_properties():
            if prop.local == local:
                return prop
        return None

    def children_of(self, iri: str) -> list[str]:
        return sorted(cls.iri for cls in self.classes.values() if iri in cls.parents)

    def subclass_iris(self, iri: str, include_self: bool = True) -> list[str]:
        """继承闭包（广度优先、去重、顺序稳定）。"""
        result: list[str] = [iri] if include_self else []
        queue = [iri]
        while queue:
            current = queue.pop(0)
            for child in self.children_of(current):
                if child not in result:
                    result.append(child)
                    queue.append(child)
        return result

    def superclass_iris(self, iri: str, include_self: bool = False) -> list[str]:
        result: list[str] = [iri] if include_self else []
        queue = list(self.classes[iri].parents) if iri in self.classes else []
        while queue:
            current = queue.pop(0)
            if current in result:
                continue
            result.append(current)
            parent = self.classes.get(current)
            if parent:
                queue.extend(parent.parents)
        return result

    def subclasses(self, iri: str, include_self: bool = False) -> list[OntClass]:
        return [self.classes[i] for i in self.subclass_iris(iri, include_self=include_self) if i in self.classes]

    def superclasses(self, iri: str) -> list[OntClass]:
        return [self.classes[i] for i in self.superclass_iris(iri) if i in self.classes]

    def restrictions_for(self, iri: str, inherited: bool = False) -> list[Restriction]:
        """取某个类上的限制；inherited=True 时沿继承闭包合并（子类继承父类约束）。"""
        owners = set(self.subclass_iris(iri, include_self=True)) if inherited else {iri}
        return [r for r in self.restrictions if r.owner_iri in owners]

    def properties_with_domain(self, iri: str, compatible: bool = False) -> list[OntProperty]:
        """按 domain 反查属性。compatible=True 时把「domain 是 iri 的父类」也算命中。"""
        found: list[OntProperty] = []
        for prop in self.all_properties():
            if iri in prop.domain_iris:
                found.append(prop)
            elif compatible and any(iri in self.subclass_iris(d, include_self=True) for d in prop.domain_iris):
                found.append(prop)
        order = self.module_index()
        return sorted(found, key=lambda p: (order.get(p.module, 999), p.local))

    def sorted_classes(self, module: str | None = None) -> list[OntClass]:
        order = self.module_index()
        items = [c for c in self.classes.values() if module is None or c.module == module]
        return sorted(items, key=lambda c: (order.get(c.module, 999), c.local))

    def sorted_properties(self, module: str | None = None, kind: str | None = None) -> list[OntProperty]:
        order = self.module_index()
        items = [
            p
            for p in self.all_properties()
            if (module is None or p.module == module) and (kind is None or p.kind == kind)
        ]
        return sorted(items, key=lambda p: (order.get(p.module, 999), p.local))

    def cross_module_bridges(self) -> list[OntProperty]:
        """跨模块对象属性：domain 与 range 分属不同模块（跨层推理的接缝，必须显式管理）。"""
        bridges: list[OntProperty] = []
        for prop in self.sorted_properties(kind="object"):
            domains = {self.module_of(iri) for iri in prop.domain_iris}
            ranges = {self.module_of(iri) for iri in prop.range_iris}
            if not domains or not ranges:
                continue
            if domains != ranges or domains - {prop.module} or ranges - {prop.module}:
                bridges.append(prop)
        return bridges


