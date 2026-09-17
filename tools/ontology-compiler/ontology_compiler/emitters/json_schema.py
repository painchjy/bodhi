"""产物：JSON Schema（`artifacts/json_schema/`）。

两份契约，分别钉住「进」与「出」
-------------------------------
1. `extraction_result.schema.json`（**进**：LLM 结构化输出）
   `nodes[].name` / `relations[].name` 用 `enum` 钉死为本体术语，模型不可能自造名称；
   每个节点/关系都要求 `evidence`，抽取结果必须可回溯到原文。
   另出逐模块版本 `extraction_result.<模块>.schema.json`（enum 收窄到该模块，便于分模块抽取）。

2. `knowledge_point.schema.json`（**出**：知识点落库）
   一条抽取结论落库时的字段契约：本体名、模块、属性、证据、溯源 chunk、校验状态。
   `validation.status` 的枚举（validated / pending_human）与 SHACL 严重级别约定一一对应：
   `sh:Violation` -> pending_human，`sh:Warning` -> validated 但打标抽检。

为什么用 enum 而不是 pattern
----------------------------
`enum` 在结构化输出（JSON mode / function calling）里能被模型侧强约束，`pattern` 只能事后校验。
术语集合是有限且稳定的，用 `enum` 约束更强、成本更低。
"""

from __future__ import annotations

from pathlib import Path

from ontology_compiler.config import ARTIFACTS_DIR, ARTIFACT_SCHEMA_VERSION
from ontology_compiler.emitters._common import banner, write_json, write_text
from ontology_compiler.lexicon import load_lexicons
from ontology_compiler.loader import ModuleView, build_module_views
from ontology_compiler.model import OntologyBundleView

SCHEMA_DRAFT = "https://json-schema.org/draft/2020-12/schema"


class JsonSchemaEmitter:
    name = "json_schema"

    def emit(self, bundle: OntologyBundleView, out_dir: Path | None = None) -> list[str]:
        root = Path(out_dir) if out_dir is not None else Path(ARTIFACTS_DIR)
        lexicons = load_lexicons(bundle.modules)
        views = build_module_views(bundle, lexicons)
        target = root / "json_schema"
        written = [
            write_json(target / "extraction_result.schema.json", extraction_schema(bundle)),
            write_json(target / "knowledge_point.schema.json", knowledge_point_schema(bundle)),
        ]
        for key, view in views.items():
            written.append(
                write_json(target / ("extraction_result.%s.schema.json" % key), extraction_schema(bundle, view))
            )
        written.append(write_json(target / "index.json", build_index(bundle, views)))
        written.append(write_text(target / "README.md", render_readme(bundle, views)))
        return written


def node_names(bundle: OntologyBundleView, view: ModuleView | None = None) -> list[str]:
    """允许作为 `nodes[].name` 的本体名：类 + 枚举取值（受控词表也要能被抽成节点）。"""
    classes = view.classes if view is not None else bundle.sorted_classes()
    names: list[str] = []
    for cls in classes:
        if cls.prefixed not in names:
            names.append(cls.prefixed)
        for member in cls.members:
            if member.prefixed not in names:
                names.append(member.prefixed)
    return names


def relation_names(bundle: OntologyBundleView, view: ModuleView | None = None) -> list[str]:
    props = view.object_properties if view is not None else bundle.sorted_properties(kind="object")
    return [prop.prefixed for prop in props]


def attribute_names(bundle: OntologyBundleView, view: ModuleView | None = None) -> list[str]:
    props = view.data_properties if view is not None else bundle.sorted_properties(kind="datatype")
    return [prop.prefixed for prop in props]


def scope_of(view: ModuleView | None) -> dict:
    if view is None:
        return {"scope": "all", "module": None, "scope_description": "全部模块合并的抽取契约"}
    return {"scope": "module", "module": view.key, "scope_description": view.spec.label}


def node_item_schema(nodes: list[str], attributes: list[str]) -> dict:
    return {
        "type": "object",
        "required": ["name", "evidence"],
        "additionalProperties": False,
        "properties": {
            "name": {"type": "string", "enum": nodes, "description": "本体名，必须命中节点术语表"},
            "label": {"type": "string", "description": "原文中的说法（全称/简称可保留）"},
            "properties": {
                "type": "object",
                "description": "该节点的数据属性；键取自属性术语表",
                "propertyNames": {"enum": attributes} if attributes else {},
                "additionalProperties": True,
            },
            "evidence": {"type": "string", "minLength": 1, "description": "原文片段或章节标题"},
            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        },
    }


def relation_item_schema(relations: list[str]) -> dict:
    return {
        "type": "object",
        "required": ["name", "source", "target", "evidence"],
        "additionalProperties": False,
        "properties": {
            "name": {"type": "string", "enum": relations, "description": "本体名，必须命中关系术语表"},
            "source": {"type": "string", "description": "起点的节点 name（必须满足关系的 domain）"},
            "target": {"type": "string", "description": "终点的节点 name（必须满足关系的 range）"},
            "evidence": {"type": "string", "minLength": 1},
            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        },
    }


MISSING_SCHEMA = {
    "type": "array",
    "description": "本体要求必填、但文档未支持的关系（不要编造，登记在此）",
    "items": {
        "type": "object",
        "required": ["name"],
        "additionalProperties": False,
        "properties": {"name": {"type": "string"}, "reason": {"type": "string"}},
    },
}

UNMATCHED_SCHEMA = {
    "type": "array",
    "description": "无法归入本体术语的原文说法（保留下来供人工扩展本体或词表）",
    "items": {
        "type": "object",
        "required": ["text"],
        "additionalProperties": False,
        "properties": {"text": {"type": "string"}, "why": {"type": "string"}},
    },
}


def extraction_schema(bundle: OntologyBundleView, view: ModuleView | None = None) -> dict:
    """抽取结果契约：节点名/关系名受限，证据必填。"""
    nodes = node_names(bundle, view)
    relations = relation_names(bundle, view)
    attributes = attribute_names(bundle, view)
    scope = scope_of(view)
    return {
        "$schema": SCHEMA_DRAFT,
        "$id": "https://example.org/bodhi/schema/extraction_result%s.json" % ("" if view is None else "." + view.key),
        "title": "BODHI2 抽取结果%s" % ("" if view is None else "（模块 %s）" % view.key),
        "description": "LLM 抽取的结构化输出契约（%s）：名称必须取自本体，证据必须可回溯。" % scope["scope_description"],
        "x-bodhi": {
            "artifact_schema_version": ARTIFACT_SCHEMA_VERSION,
            "generated_at": bundle.generated_at,
            **scope,
            "term_counts": {"nodes": len(nodes), "relations": len(relations), "attributes": len(attributes)},
        },
        "type": "object",
        "required": ["nodes", "relations"],
        "additionalProperties": False,
        "properties": {
            "nodes": {"type": "array", "description": "抽到的实体节点", "items": node_item_schema(nodes, attributes)},
            "relations": {
                "type": "array",
                "description": "抽到的关系边；两端必须是本次抽出的节点 name",
                "items": relation_item_schema(relations),
            },
            "missing": MISSING_SCHEMA,
            "unmatched": UNMATCHED_SCHEMA,
        },
    }


def knowledge_point_schema(bundle: OntologyBundleView) -> dict:
    """知识点落库契约：一条抽取结论的完整字段（含溯源与校验状态）。"""
    modules = bundle.module_order()
    return {
        "$schema": SCHEMA_DRAFT,
        "$id": "https://example.org/bodhi/schema/knowledge_point.json",
        "title": "BODHI2 知识点落库契约",
        "description": "WeKnora 抽取结论 -> ke-core 落库时的一条知识点；与 SHACL 校验状态一一对应。",
        "x-bodhi": {
            "artifact_schema_version": ARTIFACT_SCHEMA_VERSION,
            "generated_at": bundle.generated_at,
            "source_terms": {
                "classes": [cls.prefixed for cls in bundle.sorted_classes()],
                "object_properties": relation_names(bundle),
                "data_properties": attribute_names(bundle),
            },
        },
        "type": "object",
        "required": ["name", "module", "evidence", "source"],
        "additionalProperties": False,
        "properties": {
            "id": {"type": "string", "description": "稳定 ID：<module>:<name>:<source.chunk_id> 的哈希"},
            "name": {"type": "string", "description": "本体名（prefixed），必须命中术语表"},
            "module": {"type": "string", "enum": modules},
            "kind": {"type": "string", "enum": ["class", "enum_value", "object_property", "data_property"]},
            "labels": {
                "type": "object",
                "description": "多语言显示名：{zh, en, ...}",
                "additionalProperties": {"type": "string"},
            },
            "properties": {
                "type": "object",
                "description": "数据属性取值（键取自属性术语表）",
                "additionalProperties": True,
            },
            "evidence": {"type": "string", "minLength": 1, "description": "原文片段（不改写、不摘要）"},
            "source": {
                "type": "object",
                "required": ["doc_id", "chunk_id"],
                "additionalProperties": False,
                "properties": {
                    "doc_id": {"type": "string"},
                    "chunk_id": {"type": "string"},
                    "chunk_index": {"type": "integer", "minimum": 0},
                    "locator": {"type": "string", "description": "页码 / 章节路径 / 单元格"},
                },
            },
            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
            "validation": {
                "type": "object",
                "required": ["status"],
                "additionalProperties": False,
                "properties": {
                    "status": {"type": "string", "enum": ["validated", "pending_human", "rejected"]},
                    "violations": {
                        "type": "array",
                        "description": "SHACL 违规（sh:Violation -> pending_human）",
                        "items": {
                            "type": "object",
                            "required": ["shape", "message"],
                            "additionalProperties": False,
                            "properties": {
                                "severity": {"type": "string", "enum": ["sh:Violation", "sh:Warning", "sh:Info"]},
                                "shape": {"type": "string"},
                                "path": {"type": "string"},
                                "message": {"type": "string"},
                            },
                        },
                    },
                },
            },
            "provenance": {
                "type": "object",
                "description": "生成器溯源：本体版本 + 抽取模型 + 契约版本",
                "additionalProperties": True,
            },
        },
    }


def build_index(bundle: OntologyBundleView, views: dict[str, ModuleView]) -> dict:
    """契约索引：`ke-core` 按此取 schema，不自己拼文件名。"""
    counts = {
        "classes": len(bundle.classes),
        "object_properties": len(bundle.object_properties),
        "data_properties": len(bundle.data_properties),
        "enum_values": sum(len(cls.members) for cls in bundle.classes.values()),
        "restrictions": len(bundle.restrictions),
    }
    return {
        "artifact_schema_version": ARTIFACT_SCHEMA_VERSION,
        "generated_at": bundle.generated_at,
        "contracts": {
            "extraction_result": {
                "file": "artifacts/json_schema/extraction_result.schema.json",
                "direction": "in",
                "consumer": "LLM 结构化输出 / WeKnora 抽取链路",
            },
            "knowledge_point": {
                "file": "artifacts/json_schema/knowledge_point.schema.json",
                "direction": "out",
                "consumer": "ke-core 落库",
            },
        },
        "module_contracts": {
            key: {
                "file": "artifacts/json_schema/extraction_result.%s.schema.json" % view.key,
                "module": view.key,
                "label": view.spec.label,
                "nodes": len(node_names(bundle, view)),
                "relations": len(relation_names(bundle, view)),
                "attributes": len(attribute_names(bundle, view)),
            }
            for key, view in views.items()
        },
        "term_counts": counts,
    }


def render_readme(bundle: OntologyBundleView, views: dict[str, ModuleView]) -> str:
    lines = [banner("JSON Schema 契约说明", source="ontology/*.ttl + lexicon/*.keywords.yaml"), ""]
    lines.append("# artifacts/json_schema —— 抽取契约（进）+ 落库契约（出）")
    lines.append("")
    lines.append("## 1. 用在哪一段")
    lines.append("")
    lines.append("```")
    lines.append("文档 -> WeKnora 切块 -> LLM 抽取 -[extraction_result.schema.json 约束]-> 结构化 JSON")
    lines.append("                                        |")
    lines.append("                                   SHACL 校验（artifacts/shacl）")
    lines.append("                                        |")
    lines.append("                          -[knowledge_point.schema.json 约束]-> ke-core 落库")
    lines.append("```")
    lines.append("")
    lines.append("## 2. 文件清单")
    lines.append("")
    lines.append("| 文件 | 方向 | 关键约束 |")
    lines.append("| --- | --- | --- |")
    lines.append("| `extraction_result.schema.json` | 进 | 全部模块的节点/关系名 `enum`（节点 %d / 关系 %d）|" % (len(node_names(bundle)), len(relation_names(bundle))))
    for key, view in views.items():
        lines.append(
            "| `extraction_result.%s.schema.json` | 进 | 仅 %s：节点 %d / 关系 %d |"
            % (key, view.spec.label, len(node_names(bundle, view)), len(relation_names(bundle, view)))
        )
    lines.append("| `knowledge_point.schema.json` | 出 | 证据必填、`validation.status` 与 SHACL 严重级别对齐 |")
    lines.append("| `index.json` | - | 契约索引 + 各模块术语计数（做断言用）|")
    lines.append("")
    lines.append("## 3. 两条硬规则")
    lines.append("")
    lines.append("1. **名称只能取自本体**：`nodes[].name` / `relations[].name` 是 `enum`，模型无法自造术语；")
    lines.append("   归不进本体的原文说法进 `unmatched[]`，不要硬塞，这批数据就是本体迭代的输入。")
    lines.append("2. **证据必填**：每个节点与关系都要求 `evidence`（原文片段或章节标题），落库后可回溯到 chunk。")
    lines.append("")
    lines.append("## 4. 校验状态约定")
    lines.append("")
    lines.append("| SHACL 严重级别 | `validation.status` | 后续处理 |")
    lines.append("| --- | --- | --- |")
    lines.append("| `sh:Violation` | `pending_human` | 落库但挂人工复核任务 |")
    lines.append("| `sh:Warning` | `validated` | 落库并打标，进入抽检样本 |")
    lines.append("| 无违规 | `validated` | 正常落库 |")
    lines.append("")
    lines.append("用法（Python 侧）：`jsonschema.Draft202012Validator` 校验模型输出；`additionalProperties: false` 保证字段不悄悄膨胀。")
    return "\n".join(lines)



