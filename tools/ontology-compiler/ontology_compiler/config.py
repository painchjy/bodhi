"""BODHI2 本体编译器 · 配置（路径 / 模块 / 命名空间 / 产物布局）。

设计约束
--------
1. 本体文件使用**显式清单**而不是通配 glob：
   `ontology/old/` 曾放历史草稿（`BMM_EXTENDED_LIGHT.ttl` 是 Markdown 风格伪 TTL，不是合法
   Turtle），通配抓取会直接报语法错；该目录已于 2026-09-20 清理干净，但"显式清单"这条规矩
   保持不变 —— 新增本体模块必须在本文件登记。
2. 编译器本身只依赖 `rdflib`；读取 YAML 词表额外依赖 `PyYAML`（已在开发机确认可用），
   缺失时给出明确报错而不是静默降级。
3. 所有产物路径都在 `artifacts/` 下，该目录整体是可重建物（列入 .gitignore），
   唯一真源始终是 `ontology/*.ttl`。
"""

from __future__ import annotations

import json
import re
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
# 上传真源目录（用户口径 2026-09-30：上传的 TTL 落这里，不再放 extensions/）
SOURCES_DIR = ONTOLOGY_DIR / "sources"
LEXICON_DIR = ONTOLOGY_DIR / "lexicon"
SHAPES_DIR = ONTOLOGY_DIR / "shapes"
QUERIES_DIR = ONTOLOGY_DIR / "queries"
ARTIFACTS_DIR = REPO_ROOT / "artifacts"

# --------------------------------------------------------------------------
# 命名空间（本体投影、生成 SHACL、Cypher 占位符均以此为准）
# --------------------------------------------------------------------------
NS: dict[str, str] = {
    "bmm": "http://example.org/bmm#",
    "ea": "http://example.org/ea#",
    "easvc": "http://example.org/bodhi/ext/ea-service#",
    "eaown": "http://example.org/bodhi/ext/ea-ownership#",
    "bmmfd": "http://example.org/bodhi/ext/bmmfd#",
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
    # 文件来源说明（候选回退时写明"清单路径不在，改用哪个候选"；见 `_resolve_module_files`）。
    file_note: str = ""
    # **上游依赖**（编译这个模块时**必须一起编**的模块；用户口径 2026-09-30：
    # "上传 bmm 只编译 bmm；上传 ea 编译 bmm+ea"）。空 = 自足（如 bmm）。
    requires: tuple[str, ...] = ()

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


def _resolve_module_files(key: str, files: tuple[Path, ...]) -> tuple[tuple[Path, ...], str]:
    """模块文件**候选回退**（2026-09-30）：清单首选不在时，按命名约定找候选。

    为什么需要（内网实测事故）：内置清单写的是 `ontology/BMM完整版.ttl` / `ontology/EA完整版.ttl`，
    而前端「上传本体文件」通道把 TTL 规范化落成 `ontology/extensions/<key>-ext.ttl`（并登记 registry）。
    两边命名/位置不一致时，**一次编译会因任一模块缺文件而整次失败**，报错只提清单路径
    （"模块 ea 缺少本体文件：ontology/EA完整版.ttl"），而当时的调用链已经把 wiki/图谱删了 → 用户看到
    "先删后报错"。这里让两种布局都能被认出来：找到就用，并把"用了哪个候选"记进 `file_note`。

    候选顺序：清单原路径 → `extensions/<key>-ext.ttl` → `extensions/<key>.ttl` → `<key>.ttl`
             → `ontology/*<key>*完整版.ttl` → `ontology/*<key>*.ttl`
    """
    if all(p.is_file() for p in files):
        return files, ""
    found: list[Path] = []
    for path in files:
        if path.is_file():
            found.append(path)
            continue
        cands = [SOURCES_DIR / ("%s.ttl" % key), ONTOLOGY_DIR / ("%s.ttl" % key)]
        cands += sorted(ONTOLOGY_DIR.glob("*%s*完整版.ttl" % key))
        cands += sorted(ONTOLOGY_DIR.glob("*%s*.ttl" % key))
        hit = next((c for c in cands if c.is_file()), None)
        if hit is None:
            return files, ""                     # 一个都没找到 → 保留原路径，让缺文件报错定位到清单口径
        found.append(hit)
        note = "清单路径 `%s` 不存在，改用候选 `%s`" % (
            path.relative_to(REPO_ROOT).as_posix(), hit.relative_to(REPO_ROOT).as_posix())
    return tuple(found), "；".join([note] if not all(p.is_file() for p in files) else [])


def builtin_specs() -> dict[str, ModuleSpec]:
    """**内置清单**（不含 `_registry.json` 覆盖）—— 供 `ke_admin` 判断"上传的是内置模块"并就地覆盖其文件。"""
    return _builtin_specs()


# 内置模块的上游依赖（编译一个模块时须一起编的模块；用户口径 2026-09-30）：
#   EA完整版.ttl 里 29 处引用 bmm: → ea 依赖 bmm；扩展都依赖它们 affects 的基础模块。
BUILTIN_REQUIRES: dict[str, tuple[str, ...]] = {
    "bmm": (),
    "ea": ("bmm",),
    "ea-service": ("ea",),
    "ea-ownership": ("ea",),
    "bmmfd": ("bmm", "ea"),
}


def _builtin_specs() -> dict[str, ModuleSpec]:
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
            ontology_iri="http://example.org/ea",
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
            files=(SOURCES_DIR / "ea-service.ttl",),
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
            files=(SOURCES_DIR / "ea-ownership.ttl",),
            kind="extension",
            affects=("ea",),
        ),
        "bmmfd": ModuleSpec(
            key="bmmfd",
            prefix="bmmfd",
            label="BMM 规则可执行化扩展",
            short_label="BMMFD",
            ontology_iri="http://example.org/bodhi/ext/bmmfd",
            namespace=NS["bmmfd"],
            files=(SOURCES_DIR / "bmmfd.ttl",),
            kind="extension",
            affects=("bmm", "ea"),
        ),
    }


def build_modules() -> dict[str, ModuleSpec]:
    """返回全部模块，插入顺序即文档与提示词的稳定顺序（base 在前，extension 在后）。

    清单来源 = **内置（`_builtin_specs()`）+ `ontology/extensions/_registry.json` 登记 + 目录扫描兜底**
    （后者见 `registry_modules_from_dir()`：`extensions/*.ttl` 里没登记的 TTL 也自动纳入 —— 用户口径
    "统一通过导入完成所有产物和注册的变更"，见 2026-09-30）。
    每个模块的文件再做**候选回退**（`_resolve_module_files`），兼容老版本目录布局。
    """
    builtin = _builtin_specs()
    merged = {**builtin, **registry_modules()}      # 上传登记的扩展模块（同名覆盖，2026-09-24）
    # 目录扫描兜底：`extensions/*.ttl` 里**有文件但没登记**的也纳入（"文件即登记"）
    for spec in registry_modules_from_dir().values():
        merged.setdefault(spec.key, spec)
    from dataclasses import replace as _replace      # 局部导入：本文件顶部只用到 dataclass
    out: dict[str, ModuleSpec] = {}
    for key, spec in merged.items():
        files, note = _resolve_module_files(key, spec.files)
        req = (BUILTIN_REQUIRES.get(key, ()) or spec.requires) if key in builtin else spec.requires
        out[key] = _replace(spec, files=files, file_note=note, requires=req)
    return out


# --------------------------------------------------------------------------
# 上传注册表（2026-09-24）：`ontology/extensions/_registry.json`
#   前端「上传本体文件」勾选「编译并生效」时，ke_admin 会把 TTL 落到 EXTENSIONS_DIR，
#   并把模块元数据登记到这里；本文件与其它导入器都读它。
#   为什么用 sidecar JSON 而不是往 build_modules() 里写死：服务端不该改源码（易冲突、难回滚）；
#   登记表是**数据**，可 git 追踪、可单独回滚；正式产物仍只由 compile.py 生成。
# --------------------------------------------------------------------------
REGISTRY_PATH = SOURCES_DIR / "_registry.json"


def registry_modules_from_dir() -> dict[str, ModuleSpec]:
    """**目录扫描兜底**（2026-09-30 用户口径）：`ontology/extensions/*.ttl` 里**有文件但没登记**的模块也纳入。

    为什么：用户质疑"除了导入 ttl 难道还要额外注册么？统一通过导入完成所有产物和注册的变更不行么" ——
    合理。`_registry.json` 只承载**元数据**（prefix/label/短名，用于类名前缀与显示名），
    不该是"能不能被编译认识"的开关。这里按"**文件即登记**"补位：扫描到的模块用文件名当 key、
    用**TTL 里的 @prefix / rdfs:label** 当元数据（解析不出来就退回 key），登记表里有它就仍以登记表为准。
    """
    known = {str(m.get("key")) for m in _load_registry()}
    builtin_keys = set(_builtin_specs())      # 内置模块优先：遗留文件不得覆盖内置清单
    out: dict[str, ModuleSpec] = {}
    # ① sources/：**上传真源**（文件即登记；同名覆盖内置清单）
    # ② extensions/：随包自带的扩展（`*-ext.ttl`），内置 key 跳过
    for path in sorted(SOURCES_DIR.glob("*.ttl")) if SOURCES_DIR.is_dir() else []:
        key = path.name[: -len(".ttl")].strip().lower()
        if not key or key.startswith("_") or key in known or key in out:
            continue
        head = path.read_text(encoding="utf-8", errors="replace")[:4000]
        prefix = ""
        for m in re.finditer(r"@prefix\s+([A-Za-z0-9_.-]+)\s*:\s*<([^>]+)>", head):
            if m.group(1) and m.group(2).rstrip("/#").endswith("/%s" % key):
                prefix = m.group(1)
                break
        out[key] = ModuleSpec(
            key=key, prefix=prefix or key,
            label=key, short_label=(prefix or key).upper(),
            ontology_iri="", namespace="", files=(path,), kind="extension",
            file_note="目录扫描纳入（`extensions/%s` 未登记；文件即登记）" % path.name,
        )
    return out


def _load_registry() -> list[dict]:
    """读 `_registry.json` 的模块条目（缺失/坏文件 → 空表；**不能让编译被一条坏数据打断**）。"""
    if not REGISTRY_PATH.is_file():
        return []
    try:
        return list(json.loads(REGISTRY_PATH.read_text(encoding="utf-8")).get("modules") or [])
    except Exception:  # noqa: BLE001
        return []


def registry_modules() -> dict[str, ModuleSpec]:
    """读上传注册表 → ModuleSpec；文件缺失/坏行都**静默跳过**（不能让编译被一条坏数据打断）。"""
    out: dict[str, ModuleSpec] = {}
    for item in _load_registry():
        try:
            key = str(item.get("key") or "").strip().lower()
            rel = str(item.get("file") or "").strip()
            if not key or not rel:
                continue
            prefix = str(item.get("prefix") or key).strip()
            namespace = str(item.get("namespace") or "").strip()
            spec = ModuleSpec(
                key=key,
                prefix=prefix,
                label=str(item.get("label") or key),
                short_label=str(item.get("short_label") or prefix.upper()),
                ontology_iri=str(item.get("ontology_iri") or ""),
                namespace=namespace,
                files=(REPO_ROOT / rel,),
                kind="extension",
                affects=tuple(item.get("affects") or ()),
            )
            if namespace:
                NS.setdefault(prefix, namespace)     # cypher 占位符 / 提示词也认得这个前缀
            out[key] = spec
        except Exception:  # noqa: BLE001
            continue
    return out


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


def _module_requires(modules=None) -> dict:
    """每个模块的上游依赖：显式声明优先（requires / BUILTIN_REQUIRES），否则按 TTL 里的跨模块 IRI 引用推断。"""
    mods = modules if modules is not None else build_modules()
    out: dict = {}
    ns_to_key = {}
    for key, spec in mods.items():
        for ns in (spec.namespace, spec.ontology_iri):
            if ns:
                ns_to_key[ns.rstrip("#/")] = key
    for key, spec in mods.items():
        deps = set(spec.requires)
        try:
            body = "\n".join(p.read_text(encoding="utf-8", errors="replace")
                             for p in spec.files if p.is_file())
        except Exception:  # noqa: BLE001
            body = ""
        for ns, other in ns_to_key.items():
            if other != key and ns and (ns in body):
                deps.add(other)
        if deps:
            out[key] = deps
    return out


def upstream_closure(key: str, modules=None) -> list:
    """**编译范围**：key 自身 + 上游依赖（传递），被依赖的排在前。

    例：upstream_closure("bmm") == ["bmm"]；upstream_closure("ea") == ["bmm", "ea"]。
    """
    mods = modules if modules is not None else build_modules()
    req = _module_requires(mods)
    ordered: list = []
    seen: set = set()

    def _visit(k: str) -> None:
        if k in seen or k not in mods:
            return
        seen.add(k)
        for up in sorted(req.get(k, ())):
            _visit(up)
        ordered.append(k)

    _visit((key or "").strip())
    return ordered or ([key] if key else [])


def downstream_closure(key: str, modules=None) -> list:
    """**级联删除范围**：key 自身 + 所有依赖它的模块（传递），最下游在前。"""
    mods = modules if modules is not None else build_modules()
    req = _module_requires(mods)
    out: list = []
    seen: set = set()
    queue = [(key or "").strip()]
    while queue:
        cur = queue.pop(0)
        if not cur or cur in seen:
            continue
        seen.add(cur)
        for other, deps in req.items():
            if cur in deps and other not in seen:
                queue.append(other)
        out.append(cur)
    return list(reversed(out))
