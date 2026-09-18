"""BODHI2 本体编译器 · 配置（路径 / 模块 / 命名空间 / 产物布局）。

设计约束
--------
1. 本体文件使用**显式清单**而不是通配 glob：
   `ontology/old/` 下存在历史草稿（`BMM_EXTENDED_LIGHT.ttl` 是 Markdown 风格伪 TTL，
   不是合法 Turtle），通配抓取会直接报语法错。新增本体模块必须在本文件登记。
2. 编译器本身只依赖 `rdflib`；读取 YAML 词表额外依赖 `PyYAML`（已在开发机确认可用），
   缺失时给出明确报错而不是静默降级。
3. 所有产物路径都在 `artifacts/` 下，该目录整体是可重建物（列入 .gitignore），
   唯一真源始终是 `ontology/*.ttl`。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

COMPILER_VERSION = "0.1.0"
# 1.1：artifacts/weknora/extract_config.<模块>.json 的字段契约改为对齐 WeKnora 源码
#      （nodes[].attributes / relations[].node1,node2,type / tags=关系类型白名单 /
#       custom_instructions），并移除与上游不兼容的 .flat.json 形态（见 emitters/weknora_config.py）。
ARTIFACT_SCHEMA_VERSION = "1.1"

# 生成时间戳的环境变量（CI / 测试用固定值，让同一份本体产出**逐字节相同**的产物）。
# 不做成随机/忽略：产物里的 generated_at 是追溯信息，默认必须写真实时间。
GENERATED_AT_ENV = "BODHI_GENERATED_AT"

REPO_ROOT = Path(__file__).resolve().parents[3]
ONTOLOGY_DIR = REPO_ROOT / "ontology"
EXTENSIONS_DIR = ONTOLOGY_DIR / "extensions"
LEXICON_DIR = ONTOLOGY_DIR / "lexicon"
SHAPES_DIR = ONTOLOGY_DIR / "shapes"
QUERIES_DIR = ONTOLOGY_DIR / "queries"
ARTIFACTS_DIR = REPO_ROOT / "artifacts"

# --------------------------------------------------------------------------
# 命名空间（本体投影、生成 SHACL、Cypher 占位符均以此为准）
# --------------------------------------------------------------------------
NS: dict[str, str] = {
    "bmm": "http://example.org/bmm#",
    "ea": "http://example.org/bmm-EA-ext#",
    "easvc": "http://example.org/bodhi/ext/ea-service#",
    "eaown": "http://example.org/bodhi/ext/ea-ownership#",
    "bmmfd": "http://example.org/bodhi/ext/bmm-fd#",
    # 跨模块共享的注解命名空间：expertRole 等（见 docs/weknora-fork.md §8.5）
    "bodhi": "http://example.org/bodhi#",
    "owl": "http://www.w3.org/2002/07/owl#",
    "rdf": "http://www.w3.org/1999/02/22-rdf-syntax-ns#",
    "rdfs": "http://www.w3.org/2000/01/rdf-schema#",
    "xsd": "http://www.w3.org/2001/XMLSchema#",
    "sh": "http://www.w3.org/ns/shacl#",
}

OWL = NS["owl"]
RDF = NS["rdf"]
RDFS = NS["rdfs"]
XSD = NS["xsd"]
SH = NS["sh"]

OWL_THING = OWL + "Thing"
RDF_TYPE = RDF + "type"

# 跨模块共享注解：专家角色（写在各模块 owl:Ontology 上，编译器读入后下发到 WeKnora fork）
# 见 docs/weknora-fork.md §8.5；改角色请改 TTL，不要改代码。
EXPERT_ROLE = NS["bodhi"] + "expertRole"

# 保留词：不作为业务类参与编译
RESERVED_CLASSES = frozenset({OWL_THING, OWL + "Nothing", RDFS + "Resource", RDFS + "Class"})


def namespace_of(iri: str) -> str:
    """按 `#` 或最后一个 `/` 切出 IRI 的命名空间部分。"""
    if "#" in iri:
        return iri.split("#", 1)[0] + "#"
    if "/" in iri:
        return iri.rsplit("/", 1)[0] + "/"
    return iri


def cypher_tokens() -> dict[str, str]:
    """Cypher 模板占位符 -> 替换值（值自带单引号，可在 Cypher 中直接做字符串拼接）。"""
    return {
        "{{BMM_NS}}": "'%s'" % NS["bmm"],
        "{{EA_NS}}": "'%s'" % NS["ea"],
        "{{EXT_EA_SERVICE_NS}}": "'%s'" % NS["easvc"],
        "{{EXT_EA_OWNERSHIP_NS}}": "'%s'" % NS["eaown"],
        "{{EXT_BMM_FD_NS}}": "'%s'" % NS["bmmfd"],
    }


# --------------------------------------------------------------------------
# 模块定义
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class ModuleSpec:
    """一个本体模块 = 一组 TTL 文件 + 一份词表。模块是「语言子集」的划分，跨模块引用被显式允许。"""

    key: str
    prefix: str
    label: str
    short_label: str
    ontology_iri: str
    namespace: str
    files: tuple[Path, ...]
    kind: str = "base"  # base（原始本体） | extension（扩展本体）
    lexicon: Path | None = None
    affects: tuple[str, ...] = ()  # 该扩展在语义上补强了哪些模块
    # 抽取时的「专家角色」已迁到 TTL 的 bodhi:expertRole（见 loader.extract_expert_roles / config.EXPERT_ROLE）。
    # 这里保留空字段仅为兼容旧调用；不要再在此处硬编码角色。
    expert_role: str = ""
    # 轻量版 md（人工撰写、供提取提示词使用；**不注入完整 TTL**）。None = 本模块暂无轻量版。
    light_file: Path | None = None

    def rel_light(self) -> str:
        """轻量版的仓库相对路径（无轻量版时返回空串）。"""
        if self.light_file is None:
            return ""
        return self.light_file.relative_to(REPO_ROOT).as_posix()

    def light_ready(self) -> bool:
        """轻量版是否存在（不存在则不能作为可选的抽取模型）。"""
        return self.light_file is not None and Path(self.light_file).is_file()

    def rel_files(self) -> list[str]:
        return [p.relative_to(REPO_ROOT).as_posix() for p in self.files]

    def missing_files(self) -> list[str]:
        return [p.relative_to(REPO_ROOT).as_posix() for p in self.files if not p.is_file()]


def build_modules() -> dict[str, ModuleSpec]:
    """返回全部模块，插入顺序即文档与提示词的稳定顺序（base 在前，extension 在后）。"""
    return {
        "bmm": ModuleSpec(
            key="bmm",
            prefix="bmm",
            label="BMM 业务动机模型",
            short_label="BMM",
            ontology_iri="http://example.org/bmm",
            namespace=NS["bmm"],
            files=(ONTOLOGY_DIR / "BMM完整版.ttl",),
            lexicon=LEXICON_DIR / "bmm.keywords.yaml",
            light_file=ONTOLOGY_DIR / "BMM轻量版.md",
        ),
        "ea": ModuleSpec(
            key="ea",
            prefix="ea",
            label="EA 企业架构",
            short_label="EA",
            ontology_iri="http://example.org/bmm-EA-ext",
            namespace=NS["ea"],
            files=(ONTOLOGY_DIR / "EA完整版.ttl",),
            lexicon=LEXICON_DIR / "ea.keywords.yaml",
            light_file=ONTOLOGY_DIR / "EA轻量版.md",
        ),
        "ea-service": ModuleSpec(
            key="ea-service",
            prefix="easvc",
            label="EA 服务契约扩展",
            short_label="EA-SVC",
            ontology_iri="http://example.org/bodhi/ext/ea-service",
            namespace=NS["easvc"],
            files=(EXTENSIONS_DIR / "ea-service-ext.ttl",),
            kind="extension",
            affects=("ea",),
        ),
        "ea-ownership": ModuleSpec(
            key="ea-ownership",
            prefix="eaown",
            label="EA 所有权与控制关系扩展",
            short_label="EA-OWN",
            ontology_iri="http://example.org/bodhi/ext/ea-ownership",
            namespace=NS["eaown"],
            files=(EXTENSIONS_DIR / "ea-ownership-ext.ttl",),
            kind="extension",
            affects=("ea",),
        ),
        "bmm-fd": ModuleSpec(
            key="bmm-fd",
            prefix="bmmfd",
            label="BMM 规则可执行化扩展",
            short_label="BMM-FD",
            ontology_iri="http://example.org/bodhi/ext/bmm-fd",
            namespace=NS["bmmfd"],
            files=(EXTENSIONS_DIR / "bmm-fd-ext.ttl",),
            kind="extension",
            affects=("bmm", "ea"),
        ),
    }


def module_keys(modules: dict[str, ModuleSpec] | None = None) -> list[str]:
    mods = modules if modules is not None else build_modules()
    return list(mods.keys())


def parse_module_selection(raw: str | None, modules: dict[str, ModuleSpec] | None = None) -> list[str]:
    """解析 `--module` 参数：`all` / 逗号分隔的模块 key；非法值抛 ValueError。"""
    mods = modules if modules is not None else build_modules()
    if raw is None or raw.strip() in ("", "all", "*"):
        return list(mods.keys())
    wanted: list[str] = []
    for token in raw.split(","):
        key = token.strip()
        if not key:
            continue
        if key not in mods:
            raise ValueError("未知模块 %r，可选：%s" % (key, ", ".join(mods)))
        if key not in wanted:
            wanted.append(key)
    return wanted
