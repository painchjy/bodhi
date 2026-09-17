"""编译编排：load -> validate -> emit -> manifest（含 --diff）。

一次编译的完整语义
------------------
1. **载入**：`load_ontology()` 把 `ontology/*.ttl`（显式清单）解析成 IR；
2. **体检**：`validate_ontology()` 出报告；有 ERROR 时默认**不写产物**（失败要停在明确错误上），
   调用方可用 `--force` 跳过（仅用于排查，不推荐）；
3. **发射**：六个发射器各自写自己负责的目录，返回相对路径；
4. **清点**：`manifest.json` 记录版本、计数、每个产物的 sha256 与生成时间；
5. **对比**：`--diff` 与上一次 manifest 比对，输出 added / removed / changed / unchanged。

产物目录整体可重建（`artifacts/` 在 .gitignore 里）：真源永远是 `ontology/*.ttl`。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path

from ontology_compiler.config import (
    ARTIFACT_SCHEMA_VERSION,
    ARTIFACTS_DIR,
    COMPILER_VERSION,
    REPO_ROOT,
    ModuleSpec,
    build_modules,
    parse_module_selection,
)
from ontology_compiler.emitters import EMITTERS
from ontology_compiler.lexicon import load_lexicons
from ontology_compiler.loader import OntologyBundle, load_ontology
from ontology_compiler.validate import (
    Severity,
    ValidationProblem,
    has_errors,
    has_warnings,
    validate_ontology,
)

MANIFEST_NAME = "manifest.json"


@dataclass
class ArtifactEntry:
    """manifest 里的单条产物记录。"""

    path: str
    sha256: str
    bytes: int
    emitter: str

    def as_dict(self) -> dict:
        return {"path": self.path, "sha256": self.sha256, "bytes": self.bytes, "emitter": self.emitter}


@dataclass
class CompileResult:
    """一次编译的结果（CLI 与测试共用的返回结构）。"""

    bundle: OntologyBundle | None
    problems: list[ValidationProblem] = field(default_factory=list)
    artifacts: list[ArtifactEntry] = field(default_factory=list)
    manifest_path: Path | None = None
    removed: list[str] = field(default_factory=list)
    diff: list[dict] = field(default_factory=list)
    wrote_artifacts: bool = False
    module_keys: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not has_errors(self.problems)

    def counts(self) -> dict[str, int]:
        counts = {Severity.ERROR: 0, Severity.WARN: 0, Severity.INFO: 0}
        for problem in self.problems:
            counts[problem.severity] = counts.get(problem.severity, 0) + 1
        return counts


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def artifact_entry(path: Path, emitter: str = "") -> ArtifactEntry:
    full = Path(path)
    return ArtifactEntry(path=_relative(full), sha256=sha256_file(full), bytes=full.stat().st_size, emitter=emitter)


def _relative(path: Path) -> str:
    resolved = Path(path).resolve()
    try:
        return resolved.relative_to(Path(REPO_ROOT).resolve()).as_posix()
    except ValueError:
        return resolved.as_posix()


def load_manifest(path: Path | None = None) -> dict:
    target = Path(path) if path is not None else Path(ARTIFACTS_DIR) / MANIFEST_NAME
    if not target.is_file():
        return {}
    try:
        return json.loads(target.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


# --------------------------------------------------------------------------
# 对比（--diff）
# --------------------------------------------------------------------------
def diff_manifests(previous: dict, current: list[ArtifactEntry]) -> list[dict]:
    """把上一次 manifest 与本次产物清单比对，得到 added / removed / changed / unchanged。"""
    old = {item.get("path"): item.get("sha256") for item in previous.get("artifacts", []) if item.get("path")}
    new = {entry.path: entry.sha256 for entry in current}
    rows: list[dict] = []
    for path in sorted(set(old) | set(new)):
        if path not in old:
            status = "added"
        elif path not in new:
            status = "removed"
        elif old[path] != new[path]:
            status = "changed"
        else:
            status = "unchanged"
        rows.append({"path": path, "status": status})
    return rows


def format_diff(rows: list[dict], include_unchanged: bool = False) -> str:
    if not rows:
        return "无上一次 manifest，无法对比（首次编译）。"
    lines = ["产物对比（相对上一次 manifest）："]
    counts: dict[str, int] = {}
    for row in rows:
        counts[row["status"]] = counts.get(row["status"], 0) + 1
        if row["status"] == "unchanged" and not include_unchanged:
            continue
        lines.append("  %-9s %s" % (row["status"], row["path"]))
    summary = "、".join("%s %d" % (key, counts[key]) for key in ("added", "changed", "removed", "unchanged") if key in counts)
    lines.append("  小计：%s" % (summary or "无变化"))
    return "\n".join(lines)


# --------------------------------------------------------------------------
# 主流程
# --------------------------------------------------------------------------
def build_manifest(bundle: OntologyBundle, entries: list[ArtifactEntry], module_keys: list[str]) -> dict:
    """manifest 是「这次编译到底产出了什么」的清单；时间戳与产物内一致（便于固定时间戳复现）。"""
    return {
        "artifact_schema_version": ARTIFACT_SCHEMA_VERSION,
        "compiler_version": COMPILER_VERSION,
        "generated_at": bundle.generated_at,
        "module_keys": list(module_keys),
        "modules": [
            {
                "key": key,
                "label": bundle.modules[key].label,
                "kind": bundle.modules[key].kind,
                "files": bundle.modules[key].rel_files(),
            }
            for key in module_keys
            if key in bundle.modules
        ],
        "stats": bundle.stats(),
        "artifacts": [entry.as_dict() for entry in entries],
    }


def _resolve(recorded_path: str, root: Path) -> Path:
    """把产物记录还原成磁盘路径：仓库相对路径用 REPO_ROOT 展开，绝对路径原样使用。"""
    candidate = Path(recorded_path)
    if candidate.is_absolute() or ":" in recorded_path[:3]:
        return candidate
    inside_root = root / recorded_path
    if inside_root.is_file():
        return inside_root
    return Path(REPO_ROOT) / recorded_path


def compile_all(
    module_selection: str | None = None,
    modules: dict[str, ModuleSpec] | None = None,
    out_dir: Path | None = None,
    allow_name_collisions: bool = False,
    fail_on_warn: bool = False,
    force: bool = False,
    diff: bool = False,
    prune: bool = True,
    generated_at: str | None = None,
) -> CompileResult:
    """完整编译流程。返回 `CompileResult`；是否阻断由 `ok` 与 `wrote_artifacts` 表达。

    `generated_at` 透传给 `load_ontology()`：不传时取环境变量 `BODHI_GENERATED_AT`，
    最后才回落到当前 UTC 时间（见 `resolved_generated_at`）。
    """
    mods = modules if modules is not None else build_modules()
    selected = parse_module_selection(module_selection, mods)
    selected_mods = {key: mods[key] for key in selected}
    root = Path(out_dir) if out_dir is not None else Path(ARTIFACTS_DIR)
    manifest_path = root / MANIFEST_NAME
    previous = load_manifest(manifest_path)

    bundle = load_ontology(selected_mods, generated_at=generated_at)
    problems = validate_ontology(bundle, allow_name_collisions=allow_name_collisions)
    result = CompileResult(bundle=bundle, problems=problems, module_keys=selected)

    blocked = has_errors(problems) or (fail_on_warn and has_warnings(problems))
    if blocked and not force:
        if diff:
            result.diff = diff_manifests(previous, [])
        return result

    from ontology_compiler.emitters._common import ensure_dir, relative, write_json  # 局部导入，避免环

    ensure_dir(root)
    entries: list[ArtifactEntry] = []
    for emitter in EMITTERS:
        for recorded in emitter.emit(bundle, root):
            full = _resolve(recorded, root)
            entries.append(artifact_entry(full, emitter=getattr(emitter, "name", type(emitter).__name__)))
    entries.sort(key=lambda entry: entry.path)

    if prune:
        current = {entry.path for entry in entries}
        for item in previous.get("artifacts", []):
            stale = item.get("path")
            if not stale or stale in current:
                continue
            stale_file = _resolve(stale, root)
            if stale_file.is_file():
                stale_file.unlink()
                result.removed.append(relative(stale_file))

    if diff:
        result.diff = diff_manifests(previous, entries)

    manifest = build_manifest(bundle, entries, selected)
    write_json(manifest_path, manifest)
    result.artifacts = entries
    result.manifest_path = manifest_path
    result.wrote_artifacts = True
    return result


def _rel_or_str(value: str) -> str:  # pragma: no cover - 兼容旧调用，保留即可
    return value

