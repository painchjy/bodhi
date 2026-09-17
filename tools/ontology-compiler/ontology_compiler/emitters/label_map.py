"""产物：中文标签 / 别名 -> 本体名称 的归一化表（`artifacts/mapping/label_map.json`）。

用途（执行层网关与 WeKnora 抽取都要用）
---------------------------------------
LLM 与业务文档里出现的是「业务规则 / 服务契约 / 数据所有者」这类**自然语言说法**，
图谱里需要的是 `bmm:BusinessRule` / `easvc:ServiceContract` 这类**本体名称**。这张表就是两者的桥：

1. `terms`：每个术语的规范信息（IRI、模块、各语言标签、别名候选）；
2. `alias_index`：归一化别名 -> 术语（**唯一命中**才登记，避免一义多指被静默吞掉）；
3. `conflicts`：同一别名命中多个术语时集中列出，必须人工改名或加限定词消解。

约定：别名比较前会做「去首尾空白 + 忽略大小写 + 忽略空白差异 + 全角转半角」，
`conventions` 字段把这几条写进产物里，供下游用同一套规则实现。
"""

from __future__ import annotations

import unicodedata
from pathlib import Path

from ontology_compiler.config import ARTIFACTS_DIR
from ontology_compiler.lexicon import load_lexicons, synonyms_for
from ontology_compiler.model import EnumMember, OntClass, OntProperty, OntologyBundleView, Term

from ontology_compiler.emitters._common import write_json


class LabelMapEmitter:
    name = "label_map"

    def emit(self, bundle: OntologyBundleView, out_dir: Path | None = None) -> list[str]:
        root = Path(out_dir) if out_dir is not None else Path(ARTIFACTS_DIR)
        lexicons = load_lexicons(bundle.modules)
        return [write_json(root / "mapping" / "label_map.json", build_label_map(bundle, lexicons))]


def normalize_alias(text: str) -> str:
    """归一化别名：NFKC（全角->半角）、去空白、转小写。"""
    return "".join(unicodedata.normalize("NFKC", str(text)).split()).lower()


def split_camel(local: str) -> list[str]:
    """把驼峰本地名拆成可读写法，便于匹配英文口语（ServiceContract -> service contract）。"""
    words: list[str] = []
    current = ""
    for char in local:
        if char.isupper() and current:
            words.append(current)
            current = char
        else:
            current += char
    if current:
        words.append(current)
    parts = [word.lower() for word in words if word.lower() not in ("of", "and", "the")]
    return [" ".join(words).lower()] if len(parts) > 1 else []


def candidate_aliases(term: Term, lexicon: dict) -> list[str]:
    """一个术语的全部候选写法（标签 + 本地名 + 驼峰拆词 + 词表同义词）。"""
    values: list[str] = []
    values.extend(str(label) for label in term.labels.values())
    values.append(term.local)
    values.extend(split_camel(term.local))
    values.extend(synonyms_for(lexicon, term.local))
    seen: list[str] = []
    for value in values:
        text = str(value).strip()
        if text and text not in seen:
            seen.append(text)
    return seen


def term_entry(term: Term, kind: str, tier: str) -> dict:
    return {
        "iri": term.iri,
        "prefixed": term.prefixed,
        "type": kind,
        "tier": tier,
        "module": term.module,
        "canonical": term.local,
        "labels": {key or "none": value for key, value in term.labels.items()},
        "comment": term.comment,
        "source_files": list(term.source_files),
    }


def build_label_map(bundle: OntologyBundleView, lexicons: dict[str, dict] | None = None) -> dict:
    lex = lexicons or {}
    terms: dict[str, dict] = {}
    alias_index: dict[str, list[str]] = {}

    def register(term: Term, kind: str, tier: str, lexicon: dict) -> None:
        if term.prefixed in terms:
            return
        terms[term.prefixed] = term_entry(term, kind, tier)
        terms[term.prefixed]["aliases"] = candidate_aliases(term, lexicon)
        for alias in terms[term.prefixed]["aliases"]:
            key = normalize_alias(alias)
            bucket = alias_index.setdefault(key, [])
            if term.prefixed not in bucket:
                bucket.append(term.prefixed)

    for key in bundle.module_order():
        lexicon = lex.get(key, {})
        for cls in bundle.sorted_classes(key):
            register(cls, "class", "tbox", lexicon)
            for member in cls.members:
                register(member, "enum-value", "tbox", lexicon)
        for prop in bundle.sorted_properties(key):
            register(prop, "object-property" if prop.is_object else "datatype-property", "tbox", lexicon)

    unique = {key: values[0] for key, values in alias_index.items() if len(values) == 1}
    conflicts = {key: sorted(values) for key, values in alias_index.items() if len(values) > 1}
    return {
        "schema_version": "1.0",
        "generated_at": bundle.generated_at,
        "conventions": {
            "case_insensitive": True,
            "ignore_whitespace": True,
            "unicode_normalization": "NFKC",
            "note": "下游必须使用同一套归一化规则查表，否则中文别名与英文缩写会命中不同条目",
        },
        "counts": {
            "terms": len(terms),
            "aliases_unique": len(unique),
            "aliases_conflicting": len(conflicts),
        },
        "terms": terms,
        "alias_index": dict(sorted(unique.items())),
        "conflicts": dict(sorted(conflicts.items())),
        "conflict_hint": "冲突别名不会被登记到 alias_index；请重命名术语或在词表中加限定词消解",
    }
