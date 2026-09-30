"""发射器共享工具：路径、写出、命名。

所有发射器都只做两件事：把模型渲染成文本，然后按 `artifacts/` 的固定布局落盘。
把「相对路径计算 / JSON 序列化风格 / 目录创建」收敛到这里，避免六个发射器各写一套。
"""

from __future__ import annotations

import json
from pathlib import Path

from ontology_compiler.config import ARTIFACTS_DIR, REPO_ROOT
from ontology_compiler.model import OntologyBundleView, Term


def artifacts_root(out_dir: Path | None = None) -> Path:
    return Path(out_dir) if out_dir is not None else ARTIFACTS_DIR


def relative(path: Path) -> str:
    """转成仓库相对 posix 路径（manifest 里登记的就是这个）。"""
    resolved = Path(path).resolve()
    try:
        return resolved.relative_to(Path(REPO_ROOT).resolve()).as_posix()
    except ValueError:
        return resolved.as_posix()


def ensure_dir(path: Path) -> Path:
    directory = Path(path)
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def write_text(path: Path, text: str) -> str:
    target = Path(path)
    ensure_dir(target.parent)
    if not text.endswith("\n"):
        text = text + "\n"
    target.write_text(text, encoding="utf-8", newline="\n")
    return relative(target)


def write_json(path: Path, payload) -> str:
    return write_text(path, json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=False))


def banner(title: str, source: str | None = None, extra: list[str] | None = None) -> str:
    """生成物的统一文件头：字段含义 + 真源提示（产物可重建，禁止手改）。"""
    lines = [
        "# %s" % title,
        "#",
        "# 由 tools/ontology-compiler 自动生成 —— 请勿手改。",
    ]
    if source:
        lines.append("# 真源：%s" % source)
    lines.append("# 重新生成：python tools/ontology-compiler/compile.py compile")
    for line in extra or []:
        lines.append("# %s" % line)
    return "\n".join(lines) + "\n"


def term_summary(term: Term) -> str:
    """术语的一行人话摘要（用于表格/提示词）。"""
    label = term.label or "（无中文标签）"
    return "%s %s" % (term.prefixed, label)


def sort_key(term: Term) -> tuple[str, str]:
    return (term.prefix, term.local)


def enumerate_modules(bundle: OntologyBundleView) -> list[str]:
    return list(bundle.module_order())


def sdc_name(bundle: OntologyBundleView, iri: str) -> str:
    """把本体类 IRI 映射成 Neo4j / JSON Schema 里安全的标识名（模块前缀 + 本地名）。"""
    module = bundle.module_of(iri)
    local = iri.split("#")[-1] if "#" in iri else iri.rsplit("/", 1)[-1]
    prefix = bundle.modules[module].prefix if module in bundle.modules else module
    return "%s__%s" % (prefix, local)
