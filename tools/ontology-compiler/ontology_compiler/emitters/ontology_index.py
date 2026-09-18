"""产物：WeKnora 二次开发用的「本体目录」（`artifacts/weknora/ontology_index.json`）。

为什么需要它
------------
WeKnora 上游的 Wiki 页面类型是**硬编码枚举**：
`internal/handler/wiki_page.go` 用 `types.IsValidWikiPageType()` 白名单校验，
`internal/agent/prompts_wiki.go` 的提示词与前端四个筛选（实体/概念/综合/对比）写死同一套。
本仓库的改造（见 `docs/weknora-fork.md`）要让**页面类型来自本体 class**、并让用户
**选本体模型 + 专家角色**，因此需要一个机器可读的「本体目录」：

    fork 侧只读这个文件（挂载进容器），不解析 TTL、不重复实现本体逻辑。

内容
----
逐模块给出：key / 中文标签 / 短标签 / **专家角色** / 命名空间 / 真源文件 /
依赖关系（affects）/ 计数；并列出该模块的类（含枚举取值、父类、限制条数）、
对象属性（type + domain + range，即允许建立的关系）、跨模块桥（domain 与 range
分属不同模块的属性——跨层推理的接缝）。

字段稳定性
----------
`schema` 是给 fork 侧解析器的契约标识；跨产物契约版本见 `ARTIFACT_SCHEMA_VERSION`。
改动字段名时必须同步改 fork 侧解析器与 `docs/weknora-fork.md`。
"""

from __future__ import annotations

from pathlib import Path

from ontology_compiler.config import ARTIFACTS_DIR, ARTIFACT_SCHEMA_VERSION, COMPILER_VERSION
from ontology_compiler.emitters._common import write_json
from ontology_compiler.lexicon import load_lexicons
from ontology_compiler.loader import build_module_views, display_iri
from ontology_compiler.model import OntProperty, Term

# fork 侧解析器据此判断目录结构（改结构时递增）
INDEX_SCHEMA = "bodhi2.weknora_ontology_index/1"


class OntologyIndexEmitter:
    name = "weknora_index"

    def emit(self, bundle, out_dir: Path | None = None) -> list[str]:
        root = Path(out_dir) if out_dir is not None else Path(ARTIFACTS_DIR)
        lexicons = load_lexicons(bundle.modules)
        views = build_module_views(bundle, lexicons)
        return [write_json(root / "weknora" / "ontology_index.json", build_index(bundle, views))]


# --------------------------------------------------------------------------
# 目录构造
# --------------------------------------------------------------------------
def normalize(text: str | None) -> str:
    """定义文本归一：折叠空白（TTL 里的换行/缩进不该进 UI）。"""
    return " ".join((text or "").split())


def term_entry(term: Term) -> dict:
    return {
        "name": term.prefixed,
        "iri": term.iri,
        "label": term.label or "",
        "definition": normalize(term.comment),
    }


def class_entry(cls, prefix_map: dict[str, str]) -> dict:
    entry = term_entry(cls)
    entry["parents"] = [display_iri(iri, prefix_map) for iri in cls.parents]
    entry["is_enum"] = cls.is_enum
    entry["restriction_count"] = len(cls.restrictions)
    if cls.is_enum:
        entry["enum_style"] = cls.enum_style or ""
        entry["enum_values"] = [
            {
                "name": member.prefixed,
                "label": member.label or "",
                "definition": normalize(member.comment),
            }
            for member in cls.members
        ]
    return entry


def relation_entry(prop: OntProperty, prefix_map: dict[str, str]) -> dict:
    entry = term_entry(prop)
    entry["domain"] = [display_iri(iri, prefix_map) for iri in prop.domain_iris]
    entry["range"] = [display_iri(iri, prefix_map) for iri in prop.range_iris]
    entry["domain_display"] = normalize("、".join(expr.display for expr in prop.domain)) or "（未声明 domain）"
    entry["range_display"] = normalize("、".join(expr.display for expr in prop.range)) or "（未声明 range）"
    entry["inverse_of"] = display_iri(prop.inverse_of, prefix_map) if prop.inverse_of else ""
    entry["functional"] = prop.is_functional
    return entry


def model_entry(bundle, view, prefix_map: dict[str, str]) -> dict:
    spec = view.spec
    enum_classes = [c for c in view.classes if c.is_enum]
    plain_classes = [c for c in view.classes if not c.is_enum]
    bridges = [p for p in bundle.cross_module_bridges() if p.module == view.key]
    return {
        "key": view.key,
        "label": spec.label,
        "short_label": spec.short_label,
        "expert_role": spec.expert_role,
        "kind": spec.kind,
        "namespace": spec.namespace,
        "ontology_iri": spec.ontology_iri,
        "source_files": spec.rel_files(),
        "affects": list(spec.affects),
        "stats": {
            "classes": len(plain_classes),
            "enums": len(enum_classes),
            "enum_values": sum(len(c.members) for c in enum_classes),
            "object_properties": len(view.object_properties),
            "datatype_properties": len(view.data_properties),
            "restrictions": sum(len(c.restrictions) for c in view.classes),
            "cross_module_bridges": len(bridges),
        },
        # 页面类型候选 = 本模块声明的类（含枚举类；fork 侧按 is_enum 过滤）
        "classes": [class_entry(c, prefix_map) for c in view.classes],
        "relations": [relation_entry(p, prefix_map) for p in view.object_properties],
        "cross_module_bridges": [relation_entry(p, prefix_map) for p in bridges],
        "referenced": {
            "classes": [class_entry(c, prefix_map) for c in view.referenced_classes],
            "properties": [term_entry(p) for p in view.referenced_properties],
            "by_module": view.external_by_module,
        },
    }


def build_index(bundle, views) -> dict:
    order = bundle.module_order()
    prefix_map = dict(bundle.prefix_map)
    return {
        "schema": INDEX_SCHEMA,
        "compiler_version": COMPILER_VERSION,
        "artifact_schema_version": ARTIFACT_SCHEMA_VERSION,
        "generated_at": bundle.generated_at,
        "namespace": dict(bundle.prefix_map),
        "module_order": list(order),
        "totals": {
            "modules": len(order),
            "classes": sum(len(v.classes) for v in views.values()),
            "object_properties": sum(len(v.object_properties) for v in views.values()),
            "datatype_properties": sum(len(v.data_properties) for v in views.values()),
            "restrictions": len(bundle.restrictions),
            "cross_module_bridges": len(bundle.cross_module_bridges()),
        },
        "models": [model_entry(bundle, views[key], prefix_map) for key in order],
        "notes": [
            "页面类型候选来自 models[].classes（fork 侧按 is_enum 过滤后作为候选）。",
            "允许建立的关系来自 models[].relations；domain/range 即本体约束，用于校验抽取结果。",
            "expert_role 由本文件下发；fork 的界面让用户按知识库选择 本体模型 + 专家角色。",
        ],
    }
