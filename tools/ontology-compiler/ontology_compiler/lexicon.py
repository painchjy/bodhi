"""词表（lexicon）读取：`ontology/lexicon/*.keywords.yaml` -> dict。

词表是「本体之外的自然语言入口」：业务文档里出现的说法（同义词、缩写、口语）通过词表映射到
本体术语/属性，供 WeKnora 抽取提示词、标签归一化（label_map）与查询改写共用。

设计取舍
--------
- 只做**读取与结构化**，不做语义合并；缺文件返回空 dict（模块可以有本体但没有词表）。
- YAML 解析失败直接抛异常：词表错误会静默污染提示词，必须停在明确错误上。
- PyYAML 是编译器唯一的额外依赖（rdflib 之外），缺失时给出安装提示而不是退化成「假装没有词表」。
"""

from __future__ import annotations

from pathlib import Path

from ontology_compiler.config import ModuleSpec, build_modules


def read_yaml(path: Path) -> dict:
    try:
        import yaml  # 延迟导入：未用到词表的场景（如只跑结构校验）不必装 PyYAML
    except ImportError as exc:  # pragma: no cover - 取决于运行环境
        raise RuntimeError(
            "读取词表需要 PyYAML：请执行 `python -m pip install PyYAML`（词表文件：%s）" % path
        ) from exc

    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ValueError("词表 YAML 解析失败：%s\n%s" % (path.as_posix(), exc)) from exc
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise ValueError("词表顶层必须是映射（mapping），实际是 %s：%s" % (type(data).__name__, path.as_posix()))
    return data


def load_lexicon(path: Path | None) -> dict:
    """读取单个词表；路径为空或文件不存在时返回空 dict。"""
    if path is None or not Path(path).is_file():
        return {}
    return read_yaml(Path(path))


def load_lexicons(modules: dict[str, ModuleSpec] | None = None) -> dict[str, dict]:
    """按模块读取全部词表，返回 {module_key: lexicon}。"""
    mods = modules if modules is not None else build_modules()
    return {key: load_lexicon(spec.lexicon) for key, spec in mods.items()}


def entries(lexicon: dict, key: str) -> list[dict]:
    """取词表某个分组（如 `terms` / `anti_patterns`）并保证是「字典列表」。"""
    raw = lexicon.get(key) or []
    if isinstance(raw, dict):
        return [dict(value, term=name) if isinstance(value, dict) else {"term": name, "value": value} for name, value in raw.items()]
    if isinstance(raw, list):
        return [item for item in raw if isinstance(item, dict)]
    return []


def synonyms_for(lexicon: dict, term: str) -> list[str]:
    """取某个术语的同义词（支持 `synonyms: {Term: [..]}` 与 `terms: [{term:.., synonyms:..}]` 两种写法）。"""
    direct = lexicon.get("synonyms")
    if isinstance(direct, dict):
        values = direct.get(term) or []
        if isinstance(values, str):
            values = [values]
        return [str(v) for v in values]

    for entry in entries(lexicon, "terms"):
        if entry.get("term") == term or entry.get("name") == term:
            values = entry.get("synonyms") or []
            if isinstance(values, str):
                values = [values]
            return [str(v) for v in values]
    return []
