"""产物：SHACL 校验规则（`artifacts/shacl/`）。

两类规则，缺一不可
------------------
- `generated.shapes.ttl`：**机器从 TBox 生成**，回答「结构对不对」。
  `sh:targetClass` 来自类的 IRI；属性形状来自 `rdfs:domain` / `rdfs:range`；
  基数来自类上的 OWL 限制（`owl:minCardinality` / `owl:maxCardinality` / `owl:cardinality`）；
  取值集合来自枚举类的成员（`sh:in`）。
- `authored/*.ttl`：**人工编写**，回答「设计好不好」（幂等键、副作用、服务边界、穿透时间口径）。
  由 `ontology/shapes/*.shapes.ttl` 原样复制而来，编译器只搬运、不解释。

严重级别约定（下游 `ke-core` 依赖，含义不可改）
    `sh:Violation` -> 违反本体，落库判 `pending_human`；
    `sh:Warning`   -> 仍判 `validated`，但打标进入人工抽检。

为什么不用 rdflib 序列化
------------------------
发射器保持与 rdflib 解耦（`model.py` 之外不碰 RDF 库），且手写 Turtle 的键序稳定，
产物可以直接做 sha256 比对（`--diff` 才有意义）。
"""

from __future__ import annotations

from pathlib import Path

from ontology_compiler.config import ARTIFACT_SCHEMA_VERSION, ARTIFACTS_DIR, OWL, RESERVED_CLASSES, SHAPES_DIR
from ontology_compiler.emitters._common import banner, ensure_dir, write_json, write_text
from ontology_compiler.model import OntClass, OntProperty, OntologyBundleView, Restriction

XSD_NS = "http://www.w3.org/2001/XMLSchema#"
SHAPES_NS = "http://example.org/bodhi/shapes/generated#"
CARDINALITY_KINDS = ("minCardinality", "maxCardinality", "cardinality")


class ShaclEmitter:
    name = "shacl"

    def emit(self, bundle: OntologyBundleView, out_dir: Path | None = None) -> list[str]:
        root = Path(out_dir) if out_dir is not None else Path(ARTIFACTS_DIR)
        written = [write_text(root / "shacl" / "generated.shapes.ttl", render_generated(bundle))]
        written.extend(copy_authored(bundle, root))
        written.append(write_json(root / "shacl" / "index.json", build_index(bundle)))
        written.append(write_text(root / "shacl" / "README.md", render_readme(bundle)))
        return written


# --------------------------------------------------------------------------
# 生成侧：TBox -> SHACL
# --------------------------------------------------------------------------
def ttl_literal(text: str, lang: str | None = "zh") -> str:
    escaped = str(text).replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ")
    return '"%s"@%s' % (escaped, lang) if lang else '"%s"' % escaped


def ttl_term(value: str) -> str:
    return "<%s>" % value


def prefix_header(bundle: OntologyBundleView) -> str:
    lines = [banner("BODHI2 机器生成 SHACL（结构约束：基数 / 值域 / 枚举）", source="ontology/*.ttl")]
    seen: dict[str, str] = {}
    for spec in bundle.modules.values():
        seen.setdefault(spec.prefix, spec.namespace)
    for prefix, namespace in seen.items():
        lines.append("@prefix %s: <%s> ." % (prefix, namespace))
    lines.append("@prefix sh:   <http://www.w3.org/ns/shacl#> .")
    lines.append("@prefix rdf:  <http://www.w3.org/1999/02/22-rdf-syntax-ns#> .")
    lines.append("@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .")
    lines.append("@prefix xsd:  <http://www.w3.org/2001/XMLSchema#> .")
    lines.append("")
    return "\n".join(lines)


def display(bundle: OntologyBundleView, iri: str) -> str:
    for spec in bundle.modules.values():
        if iri.startswith(spec.namespace):
            return "%s:%s" % (spec.prefix, iri[len(spec.namespace) :])
    if iri.startswith(XSD_NS):
        return "xsd:%s" % iri[len(XSD_NS) :]
    return "<%s>" % iri


def applicable_restrictions(bundle: OntologyBundleView, cls: OntClass) -> list[Restriction]:
    """类自身与其**祖先**上的限制（子类继承父类约束）；不含子类限制。"""
    owners = set(bundle.superclass_iris(cls.iri, include_self=True))
    return [r for r in bundle.restrictions if r.owner_iri in owners]


def bounds_for(restrictions: list[Restriction], prop_iri: str) -> dict[str, object]:
    bounds: dict[str, object] = {}
    for restriction in restrictions:
        if restriction.on_property != prop_iri:
            continue
        if restriction.kind == "minCardinality":
            bounds["min"] = int(restriction.value)
        elif restriction.kind == "maxCardinality":
            bounds["max"] = int(restriction.value)
        elif restriction.kind == "cardinality":
            bounds["min"] = bounds["max"] = int(restriction.value)
        elif restriction.kind == "someValuesFrom":
            bounds.setdefault("min", 1)
            bounds["value_class"] = restriction.value_display
        elif restriction.kind == "allValuesFrom":
            bounds["all_value_class"] = restriction.value_display
    return bounds


def enum_members(bundle: OntologyBundleView, iri: str) -> list[str]:
    cls = bundle.classes.get(iri)
    if cls is None or not cls.is_enum:
        return []
    return [member.iri for member in cls.members]


def property_shape(bundle: OntologyBundleView, prop: OntProperty, bounds: dict[str, object], indent: str = "        ") -> str:
    """一条 sh:property [ ... ] 的花括号内容。"""
    body = ["%ssh:path %s ;" % (indent, display(bundle, prop.iri))]
    range_iris = prop.range_iris
    enum_iris: list[str] = []
    for iri in range_iris:
        enum_iris.extend(enum_members(bundle, iri))
    if enum_iris:
        body.append("%ssh:in ( %s ) ;" % (indent, " ".join(display(bundle, iri) for iri in enum_iris)))
    elif prop.is_object and range_iris:
        body.append("%ssh:class %s ;" % (indent, display(bundle, range_iris[0])))
        body.append("%ssh:nodeKind sh:IRI ;" % indent)
    elif prop.is_datatype:
        xsd_iris = [iri for iri in range_iris if iri.startswith(XSD_NS)]
        if xsd_iris:
            body.append("%ssh:datatype %s ;" % (indent, display(bundle, xsd_iris[0])))
    if bounds.get("min") is not None:
        body.append("%ssh:minCount %d ;" % (indent, bounds["min"]))
    if bounds.get("max") is not None:
        body.append("%ssh:maxCount %d ;" % (indent, bounds["max"]))
    body.append(
        '%ssh:message %s ;'
        % (indent, ttl_literal("「%s」的取值必须满足 %s（基数/类型/枚举之一不满足即判违规）" % (prop.label or prop.local, prop.prefixed)))
    )
    return "sh:property [\n%s\n%s] ;\n" % ("\n".join(body), indent[:-4])


def render_class_shape(bundle: OntologyBundleView, cls: OntClass) -> list[str]:
    """一个类的形状。枚举类只钉死取值集合；业务类声明属性形状（基数 / 类型 / 枚举）。"""
    shape_iri = "%s%s__%sShape" % (SHAPES_NS, cls.prefix, cls.local)
    statements = ["a sh:NodeShape ;", "sh:targetClass %s ;" % display(bundle, cls.iri)]
    if cls.label:
        statements.append("sh:name %s ;" % ttl_literal(cls.label))
    if cls.comment:
        statements.append("sh:description %s ;" % ttl_literal(cls.comment))

    if cls.is_enum and cls.members:
        statements.append("sh:in ( %s ) ;" % " ".join(display(bundle, member.iri) for member in cls.members))
    else:
        restrictions = applicable_restrictions(bundle, cls)
        props = bundle.properties_with_domain(cls.iri, compatible=True)
        for prop in props:
            statements.append(property_shape(bundle, prop, bounds_for(restrictions, prop.iri)))
        if not props:
            statements.append('sh:description "（本类暂无属性约束，仅登记类型）"@zh ;')

    body = "\n".join("    " + statement for statement in statements).rstrip()
    body = body[:-1] + "." if body.endswith(";") else body + " ."
    return ["<%s>" % shape_iri, body, ""]


def render_generated(bundle: OntologyBundleView) -> str:
    """按模块顺序输出形状；类按 (模块序, 本地名) 稳定排序，保证产物可做 sha256 比对。"""
    lines = [prefix_header(bundle)]
    lines.append("# ============================================================")
    lines.append("# 机器生成（TBox -> SHACL）：每个类一个 NodeShape")
    lines.append("#   sh:targetClass = 类 IRI；sh:property = domain/range + OWL 限制 + 枚举成员")
    lines.append("#   人工规则见同目录 authored/*.ttl（原样搬运 ontology/shapes/*.shapes.ttl）")
    lines.append("# ============================================================")
    lines.append("")
    for key in bundle.module_order():
        classes = [cls for cls in bundle.sorted_classes(key) if cls.iri not in RESERVED_CLASSES]
        if not classes:
            continue
        lines.append("# ---- 模块 %s（%s）----" % (key, bundle.modules[key].label))
        lines.append("")
        for cls in classes:
            lines.extend(render_class_shape(bundle, cls))
    return "\n".join(lines)


# --------------------------------------------------------------------------
# 搬运侧：artifacts/shacl/authored/（人工规则原样复制，内容不改一个字符）
# --------------------------------------------------------------------------
def authored_files() -> list[Path]:
    if not SHAPES_DIR.is_dir():
        return []
    return sorted(path for path in SHAPES_DIR.glob("*.ttl") if path.is_file())


def copy_authored(bundle: OntologyBundleView, root: Path) -> list[str]:
    written: list[str] = []
    for path in authored_files():
        text = path.read_text(encoding="utf-8")
        written.append(write_text(root / "shacl" / "authored" / path.name, text))
    return written


def build_index(bundle: OntologyBundleView) -> dict:
    """SHACL 装配索引：`ke-core` 按此加载「生成 + 人工」两组形状，不需要自己通配目录。"""
    authored = [
        {
            "file": "artifacts/shacl/authored/%s" % path.name,
            "source": path.relative_to(SHAPES_DIR.parent.parent).as_posix(),
            "origin": "authored",
        }
        for path in authored_files()
    ]
    generated = []
    for key in bundle.module_order():
        classes = [cls for cls in bundle.sorted_classes(key) if cls.iri not in RESERVED_CLASSES]
        if not classes:
            continue
        generated.append(
            {
                "module": key,
                "shapes": len(classes),
                "target_classes": ["%s:%s" % (cls.prefix, cls.local) for cls in classes],
            }
        )
    return {
        "artifact_schema_version": ARTIFACT_SCHEMA_VERSION,
        "generated_at": bundle.generated_at,
        "load_order": ["artifacts/shacl/generated.shapes.ttl"] + [item["file"] for item in authored],
        "severity_contract": {
            "sh:Violation": "违反本体 -> 落库判 pending_human",
            "sh:Warning": "仍判 validated -> 打标进入人工抽检",
        },
        "generated": {"file": "artifacts/shacl/generated.shapes.ttl", "origin": "generated", "modules": generated},
        "authored": authored,
    }


def render_readme(bundle: OntologyBundleView) -> str:
    lines = [banner("SHACL 校验规则说明", source="ontology/*.ttl + ontology/shapes/*.shapes.ttl"), ""]
    lines.append("# artifacts/shacl —— 校验规则（自动生成 + 人工规则搬运）")
    lines.append("")
    lines.append("| 文件 | 来源 | 管什么 |")
    lines.append("| --- | --- | --- |")
    lines.append("| `generated.shapes.ttl` | 机器从 TBox 生成 | 结构对不对：基数（OWL 限制）、值域（domain/range）、枚举（sh:in） |")
    for path in authored_files():
        lines.append("| `authored/%s` | 人工编写，原样搬运 | 设计好不好：语义规则（幂等、副作用、边界、穿透口径） |" % path.name)
    lines.append("| `index.json` | 机器生成 | 装配清单：加载顺序 + 严重级别契约 |")
    lines.append("")
    lines.append("严重级别：`sh:Violation` 落库判 `pending_human`；`sh:Warning` 仍判 `validated` 但打标抽检。")
    lines.append("")
    lines.append("生成规模：类 %d 个 / 对象属性 %d 个 / 数据属性 %d 个 / OWL 限制 %d 条。" % (len(bundle.classes), len(bundle.object_properties), len(bundle.data_properties), len(bundle.restrictions)))
    lines.append("用法（Python 侧）：`pyshacl` 加载 `index.json.load_order` 中的文件，对抽取结果图执行校验。")
    return "\n".join(lines)

