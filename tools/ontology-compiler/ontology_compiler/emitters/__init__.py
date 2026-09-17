"""产物发射器（emitters）。

每个发射器接收「编译后的本体模型」并按目标层写出产物，互不知晓彼此细节：
    weknora_config  ->  artifacts/weknora/extract_config.<模块>.json
    shacl           ->  artifacts/shacl/
    neo4j           ->  artifacts/neo4j/
    json_schema     ->  artifacts/json_schema/
    label_map       ->  artifacts/mapping/label_map.json
    prompts         ->  artifacts/prompts/

约定：发射器只读模型、只写自己负责的目录，并返回已写文件的相对路径列表，
由 compiler 统一登记到 manifest.json（含 sha256）。
"""

from __future__ import annotations

from typing import Protocol

from ontology_compiler.model import OntologyBundleView


class Emitter(Protocol):
    """发射器协议。"""

    name: str

    def emit(self, bundle: "OntologyBundleView", out_dir) -> list[str]:  # noqa: ANN001
        """写出产物，返回相对于仓库根目录的 posix 路径列表。"""


from ontology_compiler.emitters.json_schema import JsonSchemaEmitter  # noqa: E402
from ontology_compiler.emitters.label_map import LabelMapEmitter  # noqa: E402
from ontology_compiler.emitters.neo4j import Neo4jEmitter  # noqa: E402
from ontology_compiler.emitters.prompts import PromptEmitter  # noqa: E402
from ontology_compiler.emitters.shacl import ShaclEmitter  # noqa: E402
from ontology_compiler.emitters.weknora_config import WeKnoraConfigEmitter  # noqa: E402

EMITTERS = (
    WeKnoraConfigEmitter(),
    ShaclEmitter(),
    Neo4jEmitter(),
    JsonSchemaEmitter(),
    LabelMapEmitter(),
    PromptEmitter(),
)

__all__ = [
    "EMITTERS",
    "Emitter",
    "JsonSchemaEmitter",
    "LabelMapEmitter",
    "Neo4jEmitter",
    "PromptEmitter",
    "ShaclEmitter",
    "WeKnoraConfigEmitter",
]
