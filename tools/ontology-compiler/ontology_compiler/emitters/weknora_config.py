"""产物：WeKnora 抽取配置（`artifacts/weknora/extract_config.<模块>.json`）。

零侵入原则
----------
WeKnora 是知识网络层，本编译器**只生成它认识的字段**，不改它一行代码。

字段契约（对齐 Tencent/WeKnora `main`，2026-09-16 逐字核对源码）
--------------------------------------------------------------
配置落在知识库的 `extract_config`（`internal/types/knowledgebase.go:105`，JSON 列）。
`ExtractConfig`（同文件 674-684 行）只有 6 个键：

    {
      "enabled": true,                                   // bool，无 omitempty：抽取总开关
      "text": "<示例文本>",                               // few-shot 的【输入】示例（Q 行）
      "tags": ["bmm:assesses", ...],                     // 关系类型白名单（不是知识库标签）
      "nodes": [{"name": "bmm:Assessment", "attributes": ["评估", "<定义>"]}],
      "relations": [{"node1": "bmm:Assessment", "node2": "bmm:Goal", "type": "bmm:assesses"}],
      "custom_instructions": "<领域约束>"                 // 追加到上游系统提示词
    }

元素结构取自 `internal/types/extract_graph.go:18-35`：
`GraphNode{name, chunks, attributes}`、`GraphRelation{node1, node2, type}`。

上游怎么用它（依据在 `provenance.json` 的 `upstream.evidence`）
--------------------------------------------------------------
1. 双开关：`ExtractConfig.Enabled` **且** `IndexingStrategy.GraphEnabled`
   （`internal/types/knowledgebase.go:841-844`），缺一不抽取；
2. 本体配置被改写成 few-shot 模板（`internal/application/service/extract.go:328-373`）：
   `Description = 基础模板 + custom_instructions`、`Tags = 本文件 tags`、
   `Examples = [{Text: text, Node: nodes, Relation: relations}]`；
3. `tags` 会被 `Sprintf` 注入基础模板里的 `%s`——上游原文是
   "Allowed relationship types are: %s."（`config/config.yaml` → `extract.extract_graph`），
   **所以 tags 必须是关系类型清单**；留空会让提示词里露出 `%s`；
4. `nodes[].chunks` 由 WeKnora 运行时空口回填（`extract.go` 里 `node.Chunks = []string{chunk.ID}`），
   编译器**必须不写**，否则示例 chunk 会混进真实图谱；
5. 读回是 `json.Unmarshal` 到结构体（`internal/types/knowledgebase.go:692-698`），
   **未知键会被静默丢弃**——字段名写错等于「看着配了、其实没生效」，故只写上面 6 个键。

编译器版本、真源 TTL、逐模块计数、上游证据一律放同目录 `provenance.json`。
"""

from __future__ import annotations

from pathlib import Path

from ontology_compiler.config import ARTIFACTS_DIR, ARTIFACT_SCHEMA_VERSION, COMPILER_VERSION
from ontology_compiler.emitters._common import banner, write_json, write_text
from ontology_compiler.emitters.prompts import compact_prompt, extraction_constraints
from ontology_compiler.lexicon import load_lexicons
from ontology_compiler.loader import ModuleView, build_module_views, display_iri
from ontology_compiler.model import OntProperty, Term

# 上游 `ExtractConfig` 认得的全部键；多写一个都是噪音（会被静默丢弃）
CONFIG_KEYS = ("enabled", "text", "tags", "nodes", "relations", "custom_instructions")


class WeKnoraConfigEmitter:
    name = "weknora"

    def emit(self, bundle, out_dir: Path | None = None) -> list[str]:
        root = Path(out_dir) if out_dir is not None else Path(ARTIFACTS_DIR)
        lexicons = load_lexicons(bundle.modules)
        views = build_module_views(bundle, lexicons)
        written: list[str] = []
        for key, view in views.items():
            written.append(write_json(root / "weknora" / ("extract_config.%s.json" % key), build_config(view)))
        written.append(write_json(root / "weknora" / "provenance.json", build_provenance(bundle, views)))
        written.append(write_text(root / "weknora" / "README.md", render_readme(views)))
        return written


# --------------------------------------------------------------------------
# 配置构造
# --------------------------------------------------------------------------
def values_of(term: Term, extra: str = "") -> list[str]:
    """术语属性：中文标签 + 定义（+ 补充说明）。

    上游把 `GraphNode.attributes` 渲染成示例答案里的 `entity_attributes` 字符串数组
    （`chat_pipeline/extract_entity.go:344-362`），所以定义必须放这里，
    而不是自定义一个 `description` 键（那会被静默丢弃）。
    """
    values: list[str] = []
    if term.label:
        values.append(term.label)
    if term.comment:
        values.append(" ".join(term.comment.split()))
    if extra:
        values.append(extra)
    return values or [term.local]


def build_nodes(view: ModuleView) -> list[dict]:
    """节点示例：模块内的类 + 枚举取值（受控词表也要能被检索到）。"""
    nodes: list[dict] = []
    for cls in view.classes:
        nodes.append({"name": cls.prefixed, "attributes": values_of(cls)})
        for member in cls.members:
            nodes.append(
                {"name": member.prefixed, "attributes": values_of(member, "枚举取值（%s）" % cls.prefixed)}
            )
    return nodes


def relation_endpoints(prop: OntProperty, prefix_map: dict[str, str]) -> tuple[str, str] | None:
    """关系两端：domain/range 的**首个具名类**。

    不用 `domain_display()`：它会把多个 domain 用 `、` 连起来，还会在缺失时返回
    「（未声明 domain）」这类占位串，当节点名会污染示例。
    """
    domains = [display_iri(iri, prefix_map) for iri in prop.domain_iris]
    ranges = [display_iri(iri, prefix_map) for iri in prop.range_iris]
    if not domains or not ranges:
        return None
    return domains[0], ranges[0]


def build_relations(view: ModuleView) -> list[dict]:
    """关系示例：`GraphRelation{node1, node2, type}`（node1/node2 是节点名，type 是关系类型）。"""
    relations: list[dict] = []
    for prop in view.object_properties:
        endpoints = relation_endpoints(prop, view.prefix_map)
        if endpoints is None:
            # domain/range 缺失（E6 会在体检里拦下）：宁可不给示例，也不教模型输出空节点
            continue
        node1, node2 = endpoints
        relations.append({"node1": node1, "node2": node2, "type": prop.prefixed})
    return relations


def build_tags(view: ModuleView) -> list[str]:
    """`tags` = **关系类型白名单**，与 `relations[].type` 同集合。

    两处用到：① `QAPromptGenerator.System` 用 `Sprintf(基础模板, tagsJSON)` 填充
    "Allowed relationship types are: %s"；② `Extractor.RemoveUnknownRelation` 用它过滤
    `Relation.Type`。按「知识库标签」理解会让关系全被过滤/提示词露出 `%s`。
    """
    return [prop.prefixed for prop in view.object_properties]


def build_config(view: ModuleView) -> dict:
    """一份可直接填进知识库 `extract_config` 的配置（键顺序固定，便于逐字节复现）。"""
    return {
        "enabled": True,
        "text": compact_prompt(view),
        "tags": build_tags(view),
        "nodes": build_nodes(view),
        "relations": build_relations(view),
        "custom_instructions": extraction_constraints(view),
    }


# --------------------------------------------------------------------------
# 溯源
# --------------------------------------------------------------------------
UPSTREAM = {
    "repo": "https://github.com/Tencent/WeKnora",
    "ref": "main（README 徽章版本 v0.8.0）",
    "verified_on": "2026-09-16",
    "evidence": [
        "internal/types/knowledgebase.go:105  KnowledgeBase.extract_config（json 列）",
        "internal/types/knowledgebase.go:674-684  ExtractConfig 的 6 个字段与 json tag",
        "internal/types/knowledgebase.go:692-698  Scan = json.Unmarshal（未知键静默丢弃）",
        "internal/types/knowledgebase.go:841-844  IsGraphEnabled = graph_enabled && extract_config.enabled",
        "internal/types/indexing_strategy.go:11-31  IndexingStrategy.graph_enabled（默认 false）",
        "internal/types/extract_graph.go:12-35  PromptTemplateStructured / GraphNode / GraphRelation / GraphData",
        "internal/application/service/extract.go:328-373  extract_config -> few-shot 模板的改写",
        "internal/application/service/chat_pipeline/extract_entity.go:253-268,344-362  示例渲染（entity / _attributes / entity1 / entity2 / relation）",
        "internal/application/service/chat_pipeline/extract_entity.go:204-218  RemoveUnknownRelation（按 tags 过滤关系类型）",
        "config/config.yaml:49-90  extract.extract_graph 基准（description 含 %s 与 tags/examples）",
        "docs/KnowledgeGraph.md  NEO4J_ENABLE / docker compose --profile neo4j",
    ],
}

FIELD_CONTRACT = {
    "enabled": "bool，无 omitempty；且必须同时把知识库的 indexing_strategy.graph_enabled 置 true",
    "text": "few-shot 的输入示例（Q 行）：术语白名单 + 表述线索；硬约束不放这里",
    "tags": "关系类型白名单，与 relations[].type 同集合；会被 Sprintf 注入上游基础模板的 %s",
    "nodes": "节点示例 [{name, attributes[]}]；chunks 由 WeKnora 运行时回填，编译器不写",
    "relations": "关系示例 [{node1, node2, type}]；node1/node2 为 domain/range 的首个具名类",
    "custom_instructions": "领域约束，追加到上游系统提示词（不替换系统侧的输出协议）",
}


def build_provenance(bundle, views: dict[str, ModuleView]) -> dict:
    """产物溯源：编译器版本、真源文件、字段契约依据、逐模块计数。

    刻意**不塞进 extract_config**：上游只认它自己的键，未知键会被静默丢弃。
    """
    return {
        "artifact_schema_version": ARTIFACT_SCHEMA_VERSION,
        "compiler_version": COMPILER_VERSION,
        "generated_at": bundle.generated_at,
        "source_files": {key: list(value) for key, value in sorted(bundle.source_files.items())},
        "config_keys": list(CONFIG_KEYS),
        "field_contract": dict(FIELD_CONTRACT),
        "upstream": {key: list(value) if isinstance(value, list) else value for key, value in UPSTREAM.items()},
        "modules": {
            key: {
                "label": view.spec.label,
                "short_label": view.spec.short_label,
                "kind": view.spec.kind,
                "affects": list(view.spec.affects),
                "files": view.spec.rel_files(),
                "counts": {
                    "classes": len(view.classes),
                    "object_properties": len(view.object_properties),
                    "data_properties": len(view.data_properties),
                    "enum_values": sum(len(cls.members) for cls in view.classes),
                    "external_referenced": sum(len(iris) for iris in view.external_by_module.values()),
                    "nodes": len(build_nodes(view)),
                    "relations": len(build_relations(view)),
                },
            }
            for key, view in views.items()
        },
    }


# --------------------------------------------------------------------------
# 说明文档（产物内的 README）
# --------------------------------------------------------------------------
def render_readme(views: dict[str, ModuleView]) -> str:
    lines: list[str] = [banner("WeKnora 抽取配置说明", source="ontology/*.ttl"), ""]
    lines.append("# artifacts/weknora —— WeKnora 知识网络层配置（自动生成）")
    lines.append("")
    lines.append("每个本体模块一份 `extract_config.<模块>.json`，字段与上游 `KnowledgeBase.extract_config`")
    lines.append("逐字对应。零侵入：只写上游认得的 6 个键（多一个都会被静默丢弃）；")
    lines.append("编译器版本、真源 TTL、逐字段依据见同目录 `provenance.json`。")
    lines.append("")
    lines.append("| 上游键 | 本产物填什么 | 上游拿它做什么 |")
    lines.append("| --- | --- | --- |")
    lines.append("| `enabled` | 恒 `true` | 与 `indexing_strategy.graph_enabled` 组成抽取双开关 |")
    lines.append("| `text` | 术语白名单 + 表述线索（紧凑提示词） | few-shot 的 Q 行 |")
    lines.append("| `tags` | 本模块全部对象属性名 | 填充基础模板里的 `%s`（“允许的关系类型”）；并过滤未知关系类型 |")
    lines.append("| `nodes` | 类 + 枚举取值，定义放 `attributes` | few-shot 的 A 行（节点与其 `entity_attributes`） |")
    lines.append("| `relations` | 对象属性：domain -> range，`type` 为属性名 | few-shot 的 A 行（三元组） |")
    lines.append("| `custom_instructions` | 硬约束（domain/range 匹配、枚举白名单、evidence、unmatched 出口） | 追加到系统提示词 |")
    lines.append("")
    lines.append("| 模块 | 配置 | 节点 | 关系（= `tags`） |")
    lines.append("| --- | --- | --- | --- |")
    for key, view in views.items():
        lines.append(
            "| %s | `extract_config.%s.json` | %d | %d |"
            % (view.spec.label, key, len(build_nodes(view)), len(build_relations(view)))
        )
    lines.append("")
    lines.append("## 接入步骤（四个闸门，缺一不抽）")
    lines.append("")
    lines.append("1. **服务侧**：`.env` 里 `NEO4J_ENABLE=true`（另配 `NEO4J_URI` / `NEO4J_USERNAME` / `NEO4J_PASSWORD`），")
    lines.append("   再用 `docker compose --profile neo4j up -d` 起图库；")
    lines.append("2. **知识库开关**：把目标知识库的 `indexing_strategy.graph_enabled` 置 `true`")
    lines.append("   （`IsGraphEnabled()` 要求它与 `extract_config.enabled` 同时为真）；")
    lines.append("3. **本体注入**：把 `extract_config.<模块>.json` 的内容整体写进该知识库的 `extract_config`；")
    lines.append("   一个知识库一份配置，所以不同文档类型请分库，或按库切换配置；")
    lines.append("4. **验证**：导入 1~2 篇文档，然后 `http://localhost:7474` 里 `match (n) return (n)`：")
    lines.append("   节点名必须命中配置里的 `nodes[].name`，关系 `type` 必须落在 `tags` 里，否则说明配置没生效。")
    lines.append("")
    lines.append("抽取结果落库前，用 `artifacts/shacl/generated.shapes.ttl` 做一次机械校验；")
    lines.append("违反本体定义的结论按需求文档要求走人工确认。")
    lines.append("")
    lines.append("重新生成：`python tools/ontology-compiler/compile.py compile`")
    return "\n".join(lines)
