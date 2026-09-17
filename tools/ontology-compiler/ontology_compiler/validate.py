"""本体体检（validate）：把「本体结构缺陷」变成可执行的检查项。

严重级别约定
------------
- `error`：结构缺陷，编译**必须**失败（产物不可信）。用 `--fail-on-warn` 可把 warn 也升级为失败。
- `warn` ：不改结构但会削弱下游质量（提示词/校验/推理），提请人工关注。
- `info` ：记录事实与待决事项（跨模块桥清单、无父类的类等），不阻断。

检查项一览
----------
E1 引用未声明的术语（domain / range / subClassOf / onProperty / inverseOf / 限制值域）
E2 悬空限制：owl:onProperty 指向的术语未被声明
E3 跨模块同名冲突：同一 local name 在不同模块被声明（LLM 会出现一义多指）
E4 继承环
E5 枚举类没有任何取值（owl:oneOf 为空或成员未声明）
E6 对象属性缺 domain 或 range（无法生成可靠校验与提示词）；数据属性降级为 WARN
W1 类或属性缺 rdfs:label（中文标签缺失导致标签归一化失败）
W2 类或属性缺 rdfs:comment（提示词与文档会缺少定义）
W3 枚举取值个数异常多（>12，疑似把示例数据当成枚举）
W4 类没有父类（本体顶层悬空，需人工拍板；会原样出现在 Neo4j 元查询 Q5）
W5 功能属性与基数约束自相矛盾（functional 且 minCardinality > 1）
W6 owl:inverseOf 未成对声明
I1 跨模块桥清单（层间接缝，必须显式管理）
I2 模块文件与计数
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ontology_compiler.config import NS, namespace_of
from ontology_compiler.loader import display_iri
from ontology_compiler.model import OntologyBundleView

SEVERITY_ERROR = "error"
SEVERITY_WARN = "warn"
SEVERITY_INFO = "info"
SEVERITY_ORDER = {SEVERITY_ERROR: 0, SEVERITY_WARN: 1, SEVERITY_INFO: 2}


class Severity:
    """严重级别的对外常量集合（`Severity.ERROR / WARN / INFO`），避免调用方手写字符串。"""

    ERROR = SEVERITY_ERROR
    WARN = SEVERITY_WARN
    INFO = SEVERITY_INFO
    ORDER = SEVERITY_ORDER
    ALL = (SEVERITY_ERROR, SEVERITY_WARN, SEVERITY_INFO)


# 外部词汇：引用到这些命名空间时不视为「未声明」（它们由 RDF/OWL/RDFS/XSD 规范定义）
EXTERNAL_NAMESPACES = (NS["owl"], NS["rdf"], NS["rdfs"], NS["xsd"])


@dataclass
class ValidationProblem:
    severity: str
    code: str
    message: str
    subject: str = ""
    module: str = ""
    hint: str = ""

    def as_dict(self) -> dict[str, str]:
        return {
            "severity": self.severity,
            "code": self.code,
            "subject": self.subject,
            "module": self.module,
            "message": self.message,
            "hint": self.hint,
        }

    def format(self) -> str:
        head = "[%s %s] %s" % (self.severity.upper(), self.code, self.subject or "-")
        body = " %s" % self.message
        tail = "\n      提示：%s" % self.hint if self.hint else ""
        return head + body + tail


# --------------------------------------------------------------------------
# 引用收集
# --------------------------------------------------------------------------
@dataclass
class ReferenceIndex:
    """本体里出现的全部「引用」，用于 E1/E2/W6 检查。"""

    declared_terms: set[str] = field(default_factory=set)
    references: list[tuple[str, str, str]] = field(default_factory=list)  # (subject, predicate, iri)

    def note(self, subject: str, predicate: str, iri: str | None) -> None:
        if iri:
            self.references.append((subject, predicate, iri))


def collect_references(bundle: OntologyBundleView) -> ReferenceIndex:
    index = ReferenceIndex()
    index.declared_terms.update(bundle.classes)
    index.declared_terms.update(bundle.object_properties)
    index.declared_terms.update(bundle.data_properties)
    for cls in bundle.classes.values():
        for member in cls.members:
            index.declared_terms.add(member.iri)

    for cls in bundle.classes.values():
        for parent in cls.parents:
            index.note(cls.prefixed, "rdfs:subClassOf", parent)
        for expr in cls.anonymous_parents:
            for iri in expr.named_iris():
                index.note(cls.prefixed, "rdfs:subClassOf(%s)" % expr.kind, iri)
        for disjoint in cls.disjoints:
            index.note(cls.prefixed, "owl:disjointWith", disjoint)
        for restriction in cls.restrictions:
            index.note(cls.prefixed, "owl:onProperty", restriction.on_property)
            if restriction.value_kind == "class" and isinstance(restriction.value, str):
                index.note(cls.prefixed, restriction.kind, restriction.value)

    for prop in bundle.all_properties():
        for expr in prop.domain:
            for iri in expr.named_iris():
                index.note(prop.prefixed, "rdfs:domain", iri)
        for expr in prop.range:
            for iri in expr.named_iris():
                index.note(prop.prefixed, "rdfs:range", iri)
        index.note(prop.prefixed, "owl:inverseOf", prop.inverse_of)
        index.note(prop.prefixed, "rdfs:subPropertyOf", prop.sub_property_of)
    return index


def is_external(iri: str) -> bool:
    return namespace_of(iri) in EXTERNAL_NAMESPACES


# --------------------------------------------------------------------------
# E1/E2 引用完整性
# --------------------------------------------------------------------------
def check_references(bundle: OntologyBundleView, index: ReferenceIndex) -> list[ValidationProblem]:
    problems: list[ValidationProblem] = []
    for subject, predicate, iri in index.references:
        if iri in index.declared_terms:
            continue
        display = display_iri(iri, bundle.prefix_map)
        if is_external(iri):
            problems.append(
                ValidationProblem(
                    severity=SEVERITY_INFO,
                    code="I3",
                    message="引用了外部词汇表术语 %s（未在本体系声明，按外部标准处理）" % display,
                    subject=subject,
                    hint="若该术语应受本体系治理，请在对应模块的 TTL 中补声明",
                )
            )
            continue
        if predicate == "owl:onProperty":
            problems.append(
                ValidationProblem(
                    severity=SEVERITY_ERROR,
                    code="E2",
                    message="悬空限制：%s 上的 owl:onProperty 指向未声明的属性 %s" % (subject, display),
                    subject=subject,
                    module=bundle.module_of(iri),
                    hint="补声明该属性，或删除这条限制",
                )
            )
            continue
        problems.append(
            ValidationProblem(
                severity=SEVERITY_ERROR,
                code="E1",
                message="%s 引用了未声明的术语 %s" % (predicate, display),
                subject=subject,
                module=bundle.module_of(iri),
                hint="在对应模块的 TTL 中声明该术语，或把引用修正为已声明术语",
            )
        )
    return dedupe(problems)


# --------------------------------------------------------------------------
# E3 跨模块同名冲突
# --------------------------------------------------------------------------
def check_name_collisions(bundle: OntologyBundleView, allow: bool = False) -> list[ValidationProblem]:
    seen: dict[str, list[str]] = {}
    for cls in bundle.classes.values():
        seen.setdefault(cls.local, []).append(cls.prefixed)
        for member in cls.members:
            seen.setdefault(member.local, []).append(member.prefixed)
    for prop in bundle.all_properties():
        seen.setdefault(prop.local, []).append(prop.prefixed)

    problems: list[ValidationProblem] = []
    for local, prefixed_names in sorted(seen.items()):
        owners = {name.split(":", 1)[0] for name in prefixed_names}
        if len(owners) < 2:
            continue
        problems.append(
            ValidationProblem(
                severity=SEVERITY_WARN if allow else SEVERITY_ERROR,
                code="E3",
                message="本地名 %s 同时被多个模块声明：%s" % (local, "、".join(sorted(prefixed_names))),
                subject=local,
                module=",".join(sorted(owners)),
                hint="跨模块同名会让 LLM 一义多指；请重命名其中一方，或用 --allow-name-collisions 显式接受",
            )
        )
    return problems


# --------------------------------------------------------------------------
# E4 继承环
# --------------------------------------------------------------------------
def check_hierarchy_cycles(bundle: OntologyBundleView) -> list[ValidationProblem]:
    problems: list[ValidationProblem] = []
    reported: set[frozenset[str]] = set()
    state: dict[str, int] = {}  # 0 = 访问中，1 = 已完成
    stack: list[str] = []

    def walk(iri: str) -> None:
        if state.get(iri) == 1:
            return
        if state.get(iri) == 0:
            cycle = stack[stack.index(iri) :] + [iri]
            key = frozenset(cycle)
            if key not in reported:
                reported.add(key)
                problems.append(
                    ValidationProblem(
                        severity=SEVERITY_ERROR,
                        code="E4",
                        message="继承环：%s" % " -> ".join(display_iri(i, bundle.prefix_map) for i in cycle),
                        subject=display_iri(iri, bundle.prefix_map),
                        module=bundle.module_of(iri),
                        hint="OWL 不允许环形的 rdfs:subClassOf，请打断环",
                    )
                )
            return
        state[iri] = 0
        stack.append(iri)
        for parent in bundle.classes[iri].parents:
            if parent in bundle.classes:
                walk(parent)
        stack.pop()
        state[iri] = 1

    for iri in sorted(bundle.classes):
        walk(iri)
    return problems


def dedupe(problems: list[ValidationProblem]) -> list[ValidationProblem]:
    """按 (severity, code, subject, message) 去重，并保持可读的顺序。"""
    unique: dict[tuple[str, str, str, str], ValidationProblem] = {}
    for problem in problems:
        unique.setdefault((problem.severity, problem.code, problem.subject, problem.message), problem)
    return sort_problems(list(unique.values()))


def sort_problems(problems: list[ValidationProblem]) -> list[ValidationProblem]:
    return sorted(
        problems,
        key=lambda p: (SEVERITY_ORDER.get(p.severity, 9), p.code, p.module, p.subject, p.message),
    )


# --------------------------------------------------------------------------
# E5 枚举完整性 / E6 属性完整性 / W5 特征自相矛盾
# --------------------------------------------------------------------------
ENUM_MEMBER_WARN_THRESHOLD = 12


def check_enums(bundle: OntologyBundleView) -> list[ValidationProblem]:
    problems: list[ValidationProblem] = []
    for cls in bundle.sorted_classes():
        if cls.enum_style == "oneOf" and not cls.members:
            problems.append(
                ValidationProblem(
                    severity=SEVERITY_ERROR,
                    code="E5",
                    message="枚举类声明了 owl:oneOf 但没有取值",
                    subject=cls.prefixed,
                    module=cls.module,
                    hint="补上取值（rdf:List 成员），或删掉空的 owl:oneOf",
                )
            )
        if len(cls.members) > ENUM_MEMBER_WARN_THRESHOLD:
            problems.append(
                ValidationProblem(
                    severity=SEVERITY_WARN,
                    code="W3",
                    message="枚举类有 %d 个取值（>%d），疑似把示例数据当成枚举" % (len(cls.members), ENUM_MEMBER_WARN_THRESHOLD),
                    subject=cls.prefixed,
                    module=cls.module,
                    hint="确认这些确实是受控词表；若是业务实例数据，应改成普通类并放进实例图谱",
                )
            )
    return problems


def check_property_completeness(bundle: OntologyBundleView) -> list[ValidationProblem]:
    """缺 domain/range 的属性无法生成有意义的 SHACL 与提示词，因此对象属性按 ERROR 处理。"""
    problems: list[ValidationProblem] = []
    for prop in bundle.sorted_properties():
        severity = SEVERITY_ERROR if prop.is_object else SEVERITY_WARN
        if not prop.domain:
            problems.append(
                ValidationProblem(
                    severity=severity,
                    code="E6",
                    message="属性缺少 rdfs:domain（无法推断「谁可以拥有这个属性」）",
                    subject=prop.prefixed,
                    module=prop.module,
                    hint="补 rdfs:domain；若确实多形态，请显式写成 rdfs:domain 的联合类",
                )
            )
        if not prop.range:
            problems.append(
                ValidationProblem(
                    severity=severity,
                    code="E6",
                    message="属性缺少 rdfs:range（无法生成取值校验与类型提示）",
                    subject=prop.prefixed,
                    module=prop.module,
                    hint="补 rdfs:range（对象属性指向类，数据属性指向 xsd 类型）",
                )
            )
    return problems


def check_characteristics(bundle: OntologyBundleView) -> list[ValidationProblem]:
    """特征与限制自相矛盾：functional 最多 1 个值，却被 minCardinality/cardinality 要求 ≥2。"""
    problems: list[ValidationProblem] = []
    for prop in bundle.sorted_properties():
        if not prop.is_functional:
            continue
        for restriction in prop.restrictions:
            if restriction.kind not in ("minCardinality", "cardinality"):
                continue
            if not isinstance(restriction.value, int) or restriction.value <= 1:
                continue
            problems.append(
                ValidationProblem(
                    severity=SEVERITY_ERROR,
                    code="W5",
                    message="属性被声明为 owl:FunctionalProperty，但 %s 上要求 %s ≥ %d"
                    % (display_iri(restriction.owner_iri, bundle.prefix_map), restriction.kind, restriction.value),
                    subject=prop.prefixed,
                    module=prop.module,
                    hint="去掉 functional 声明，或把基数约束降到 1",
                )
            )
    return problems


# --------------------------------------------------------------------------
# W1/W2 文档完整性（标签与定义是提示词与标签归一化的燃料）
# --------------------------------------------------------------------------
def check_documentation(bundle: OntologyBundleView) -> list[ValidationProblem]:
    problems: list[ValidationProblem] = []
    for term in [*bundle.sorted_classes(), *bundle.sorted_properties()]:
        if not term.label:
            problems.append(
                ValidationProblem(
                    severity=SEVERITY_WARN,
                    code="W1",
                    message="术语缺少 rdfs:label（中文标签缺失会让标签归一化与提示词退化）",
                    subject=term.prefixed,
                    module=term.module,
                    hint='补 `rdfs:label "中文名"@zh`',
                )
            )
        if not term.comment:
            problems.append(
                ValidationProblem(
                    severity=SEVERITY_WARN,
                    code="W2",
                    message="术语缺少 rdfs:comment（提示词与文档会缺定义，模型只能靠猜）",
                    subject=term.prefixed,
                    module=term.module,
                    hint="补一句「什么时候用这个术语」的定义，避免与其他术语混淆",
                )
            )

    for cls in bundle.sorted_classes():
        missing = [member for member in cls.members if not member.label]
        if not missing:
            continue
        sample = "、".join(member.prefixed for member in missing[:5])
        more = "" if len(missing) <= 5 else "（另有 %d 个）" % (len(missing) - 5)
        problems.append(
            ValidationProblem(
                severity=SEVERITY_WARN,
                code="W1",
                message="枚举类 %d/%d 个取值缺 rdfs:label：%s%s"
                % (len(missing), len(cls.members), sample, more),
                subject=cls.prefixed,
                module=cls.module,
                hint="取值标签会直接进入提示词与标签映射表，建议补全",
            )
        )
    return problems


# --------------------------------------------------------------------------
# W4 顶层悬空类 / W6 反向属性配对
# --------------------------------------------------------------------------
def check_root_classes(bundle: OntologyBundleView) -> list[ValidationProblem]:
    """没有父类的类会原样出现在 Neo4j 元查询（Q5）里，需要人工确认是否是刻意的顶层。"""
    problems: list[ValidationProblem] = []
    for cls in bundle.sorted_classes():
        if cls.parents or cls.anonymous_parents or cls.is_enum:
            continue
        problems.append(
            ValidationProblem(
                severity=SEVERITY_WARN,
                code="W4",
                message="类没有父类（本体顶层悬空）",
                subject=cls.prefixed,
                module=cls.module,
                hint="确认是否刻意的顶层类；否则挂到上层概念（如 bmm:Resource / ea:ArchitectureElement）",
            )
        )
    return problems


def check_inverse_pairing(bundle: OntologyBundleView) -> list[ValidationProblem]:
    problems: list[ValidationProblem] = []
    for prop in bundle.sorted_properties():
        if not prop.inverse_of:
            continue
        target = bundle.object_properties.get(prop.inverse_of) or bundle.data_properties.get(prop.inverse_of)
        if target is None:  # 未声明的情况已由 E1 报出
            continue
        if target.inverse_of != prop.iri:
            problems.append(
                ValidationProblem(
                    severity=SEVERITY_WARN,
                    code="W6",
                    message="owl:inverseOf 未成对：%s 指向 %s，但对方没有指回来" % (prop.prefixed, target.prefixed),
                    subject=prop.prefixed,
                    module=prop.module,
                    hint="两端都写 inverseOf，才能让「所有权 / 持有」这类关系双向推理成立",
                )
            )
    return problems


# --------------------------------------------------------------------------
# I1/I2 事实记录（跨模块桥、模块清单）
# --------------------------------------------------------------------------
def check_bridges(bundle: OntologyBundleView) -> list[ValidationProblem]:
    """跨模块对象属性是「层间接缝」，必须显式记录：它们决定跨层推理能不能走通。"""
    grouped: dict[str, list[str]] = {}
    for prop in bundle.cross_module_bridges():
        grouped.setdefault(prop.module, []).append(prop.prefixed)
    order = bundle.module_index()
    problems: list[ValidationProblem] = []
    for module, names in sorted(grouped.items(), key=lambda item: order.get(item[0], 999)):
        problems.append(
            ValidationProblem(
                severity=SEVERITY_INFO,
                code="I1",
                message="跨模块桥 %d 条：%s" % (len(names), "、".join(sorted(names))),
                subject=module,
                module=module,
                hint="跨层推理依赖这些接缝，改动需同步更新查询与抽取提示词",
            )
        )
    return problems


def check_module_inventory(bundle: OntologyBundleView) -> list[ValidationProblem]:
    problems: list[ValidationProblem] = []
    for key in bundle.module_order():
        spec = bundle.modules[key]
        properties = bundle.sorted_properties(key)
        problems.append(
            ValidationProblem(
                severity=SEVERITY_INFO,
                code="I2",
                message="模块 %s（key=%s，%s）：类 %d / 对象属性 %d / 数据属性 %d，文件 %s"
                % (
                    spec.label,
                    key,
                    spec.kind,
                    len(bundle.sorted_classes(key)),
                    len([p for p in properties if p.is_object]),
                    len([p for p in properties if p.is_datatype]),
                    "、".join(spec.rel_files()),
                ),
                subject=key,
                module=key,
            )
        )
    return problems


# --------------------------------------------------------------------------
# 编排与输出
# --------------------------------------------------------------------------
def validate_ontology(
    bundle: OntologyBundleView,
    allow_name_collisions: bool = False,
    include_info: bool = True,
) -> list[ValidationProblem]:
    """跑全部检查项。ERROR 必须阻断编译；调用方用 `has_errors` / `--fail-on-warn` 决定退出码。"""
    index = collect_references(bundle)
    problems: list[ValidationProblem] = []
    problems += check_references(bundle, index)
    problems += check_name_collisions(bundle, allow=allow_name_collisions)
    problems += check_hierarchy_cycles(bundle)
    problems += check_enums(bundle)
    problems += check_property_completeness(bundle)
    problems += check_characteristics(bundle)
    problems += check_documentation(bundle)
    problems += check_root_classes(bundle)
    problems += check_inverse_pairing(bundle)
    if include_info:
        problems += check_bridges(bundle)
        problems += check_module_inventory(bundle)
    else:
        problems = [p for p in problems if p.severity != SEVERITY_INFO]
    return sort_problems(problems)


def count_by_severity(problems: list[ValidationProblem]) -> dict[str, int]:
    counts = {SEVERITY_ERROR: 0, SEVERITY_WARN: 0, SEVERITY_INFO: 0}
    for problem in problems:
        counts[problem.severity] = counts.get(problem.severity, 0) + 1
    return counts


def has_errors(problems: list[ValidationProblem]) -> bool:
    return any(p.severity == SEVERITY_ERROR for p in problems)


def has_warnings(problems: list[ValidationProblem]) -> bool:
    return any(p.severity == SEVERITY_WARN for p in problems)


def format_report(
    problems: list[ValidationProblem],
    bundle: OntologyBundleView | None = None,
    verbose: bool = True,
) -> str:
    lines: list[str] = []
    if bundle is not None:
        stats = bundle.stats()
        lines.append(
            "本体体检：模块 %d / 类 %d / 对象属性 %d / 数据属性 %d / 限制 %d / 枚举 %d（取值 %d）"
            % (
                stats["modules"],
                stats["classes"],
                stats["object_properties"],
                stats["data_properties"],
                stats["restrictions"],
                stats["enums"],
                stats["enum_values"],
            )
        )
        lines.append("载入时间戳：%s" % bundle.generated_at)
        lines.append("")

    if not problems:
        lines.append("未发现问题。")
        return "\n".join(lines)

    counts = count_by_severity(problems)
    for level in (SEVERITY_ERROR, SEVERITY_WARN, SEVERITY_INFO):
        group = [p for p in problems if p.severity == level]
        if not group:
            continue
        lines.append("---- %s（%d）----" % (level.upper(), len(group)))
        lines.extend(p.format() if verbose else "  %s  %s" % (p.code, p.subject) for p in group)
        lines.append("")

    lines.append(
        "合计：error %d / warn %d / info %d"
        % (counts[SEVERITY_ERROR], counts[SEVERITY_WARN], counts[SEVERITY_INFO])
    )
    return "\n".join(lines)




