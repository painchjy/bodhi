"""BODHI2 三层知识库 · 本体编译器（ontology-compiler）。

把 `ontology/*.ttl` 这份「唯一真源」编译为各层可直接消费的约束与契约（零侵入上游）：

    artifacts/weknora/extract_config.<模块>.json   WeKnora 知识网络层：按知识库注入的抽取配置
                                                   （enabled / text / tags / nodes / relations / custom_instructions）
    artifacts/shacl/generated.shapes.ttl           Python 侧校验：由 TBox 机器生成的 SHACL
    artifacts/shacl/authored/*.ttl                 Python 侧校验：人工编写的语义规则（原样复制）
    artifacts/shacl/index.json                     SHACL 装配索引（供 ke-core 加载）
    artifacts/neo4j/00_constraints.cypher          Neo4j 本体投影：约束与索引
    artifacts/neo4j/10_ontology.cypher             Neo4j 本体投影：类/属性/枚举/约束（单向投影）
    artifacts/neo4j/20_cross_layer_queries.cypher  跨层元查询（模板 + 占位符替换）
    artifacts/json_schema/*.schema.json            LLM 结构化输出契约 + 知识点落库契约
    artifacts/mapping/label_map.json               中文标签/别名 -> 本体名称 归一化表
    artifacts/prompts/<模块>_extraction.md         抽取提示词（含校验规则与输出契约）
    artifacts/manifest.json                        版本 / 计数 / 逐产物 sha256（供 --diff 与追溯）

命令行：
    python tools/ontology-compiler/compile.py validate
    python tools/ontology-compiler/compile.py compile
    python tools/ontology-compiler/compile.py compile --diff
    python tools/ontology-compiler/compile.py list
"""

from ontology_compiler.compiler import CompileResult, compile_all
from ontology_compiler.config import COMPILER_VERSION, GENERATED_AT_ENV, REPO_ROOT
from ontology_compiler.loader import OntologyBundle, load_ontology, resolved_generated_at
from ontology_compiler.validate import Severity, ValidationProblem, validate_ontology

__all__ = [
    "COMPILER_VERSION",
    "CompileResult",
    "GENERATED_AT_ENV",
    "OntologyBundle",
    "REPO_ROOT",
    "Severity",
    "ValidationProblem",
    "compile_all",
    "load_ontology",
    "resolved_generated_at",
    "validate_ontology",
]

__version__ = COMPILER_VERSION
