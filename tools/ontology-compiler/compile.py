#!/usr/bin/env python
"""BODHI2 本体编译器 · 命令行入口。

    python tools/ontology-compiler/compile.py list                   # 模块清单
    python tools/ontology-compiler/compile.py validate               # 只体检，不写产物
    python tools/ontology-compiler/compile.py validate --module bmm  # 只体检指定模块
    python tools/ontology-compiler/compile.py compile                # 体检 + 写 artifacts/
    python tools/ontology-compiler/compile.py compile --diff         # 并列出与上次编译的差异

退出码（CI 依赖这几个数字，改动需同步 README）
---------------------------------------------
0  成功（校验无 ERROR；compile 已写出产物）
1  校验未通过：有 ERROR，或 `--fail-on-warn` 命中 WARN（**不写产物**，避免半成品）
2  命令行参数错误（argparse 约定）
3  输入/环境错误：模块名非法、TTL 缺失或语法错误、词表 YAML 错误、缺少 rdflib/PyYAML

可复现编译
----------
产物里带 `generated_at`，默认每次编译的 sha256 都会变。要逐字节比对（快照测试 /
「产物是否与本体同步」）时固定时间戳：

    PowerShell: $env:BODHI_GENERATED_AT = "2026-01-01T00:00:00Z"
    cmd:        set BODHI_GENERATED_AT=2026-01-01T00:00:00Z

也可用 `--generated-at` 显式指定（优先级高于环境变量）。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from ontology_compiler import __version__ as COMPILER_VERSION  # noqa: E402
from ontology_compiler.compiler import compile_all, format_diff  # noqa: E402
from ontology_compiler.config import (  # noqa: E402
    ARTIFACTS_DIR,
    GENERATED_AT_ENV,
    REPO_ROOT,
    build_modules,
    parse_module_selection,
)
from ontology_compiler.loader import external_references, load_ontology  # noqa: E402
from ontology_compiler.validate import (  # noqa: E402
    count_by_severity,
    format_report,
    has_errors,
    has_warnings,
    validate_ontology,
)

EXIT_OK = 0
EXIT_INVALID = 1
EXIT_USAGE = 2
EXIT_INPUT = 3


def _configure_stdout() -> None:
    """Windows 控制台/管道默认编码可能装不下中文，能改就统一成 UTF-8。"""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:  # pragma: no cover - 非标准流
            continue
        try:
            reconfigure(encoding="utf-8")
        except (ValueError, OSError):  # pragma: no cover - 取决于终端
            pass


def _dump(payload) -> None:
    """`--json` 模式的统一输出（便于 CI 与其他工具解析）。"""
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def _fail(message: str) -> int:
    print("错误：%s" % message, file=sys.stderr)
    return EXIT_INPUT


def _selection(args: argparse.Namespace) -> dict | None:
    """把 `--module` 解析成模块字典；未指定时返回 None（= 全部模块）。非法 key 抛 ValueError。"""
    if not args.module:
        return None
    modules = build_modules()
    return {key: modules[key] for key in parse_module_selection(args.module, modules)}


# --------------------------------------------------------------------------
# list：模块清单（不需要解析 TTL，除非 --stats）
# --------------------------------------------------------------------------
def _repo_relative(path: Path) -> str:
    """词表、本体文件在清单里统一显示成仓库相对路径（便于复制到命令行/文档）。"""
    try:
        return Path(path).relative_to(REPO_ROOT).as_posix()
    except ValueError:
        return Path(path).as_posix()


def module_inventory(bundle=None) -> list[dict]:
    """模块元信息（key/前缀/命名空间/文件/词表）+ 可选的术语计数与跨模块引用数。"""
    rows: list[dict] = []
    for key, spec in build_modules().items():
        row = {
            "key": key,
            "prefix": spec.prefix,
            "kind": spec.kind,
            "label": spec.label,
            "short_label": spec.short_label,
            "namespace": spec.namespace,
            "ontology_iri": spec.ontology_iri,
            "affects": list(spec.affects),
            "files": spec.rel_files(),
            "files_present": not spec.missing_files(),
            "lexicon": _repo_relative(spec.lexicon) if spec.lexicon else None,
            "lexicon_present": bool(spec.lexicon and spec.lexicon.is_file()),
        }
        if bundle is not None and key in bundle.modules:
            classes = bundle.sorted_classes(key)
            properties = bundle.sorted_properties(key)
            references = external_references(bundle, key)
            row["stats"] = {
                "classes": len(classes),
                "object_properties": len([p for p in properties if p.is_object]),
                "data_properties": len([p for p in properties if p.is_datatype]),
                "enums": len([c for c in classes if c.is_enum]),
                "external_refs": sum(len(values) for values in references.values()),
                "referenced_modules": sorted(references),
            }
        rows.append(row)
    return rows


def cmd_list(args: argparse.Namespace) -> int:
    bundle = None
    if args.stats:
        try:
            bundle = load_ontology(generated_at=args.generated_at)
        except (FileNotFoundError, ValueError, RuntimeError) as exc:
            return _fail(str(exc))
    rows = module_inventory(bundle)

    if args.json:
        _dump({"compiler_version": COMPILER_VERSION, "modules": rows})
    else:
        print("本体模块（%d）—— 真源 ontology/*.ttl，产物 artifacts/" % len(rows))
        for row in rows:
            print("* %s（%s，%s）" % (row["key"], row["label"], row["kind"]))
            print("    前缀 %s / 命名空间 %s" % (row["prefix"], row["namespace"]))
            print("    文件：%s" % "、".join(row["files"]))
            lexicon_note = ""
            if row["lexicon"] and not row["lexicon_present"]:
                lexicon_note = "（缺失）"
            print("    词表：%s%s" % (row["lexicon"] or "（无）", lexicon_note))
            if "stats" in row:
                stats = row["stats"]
                refs = ""
                if stats["referenced_modules"]:
                    refs = "（引用模块：%s）" % "、".join(stats["referenced_modules"])
                print(
                    "    计数：类 %d / 对象属性 %d / 数据属性 %d / 枚举 %d / 外部引用 %d%s"
                    % (
                        stats["classes"],
                        stats["object_properties"],
                        stats["data_properties"],
                        stats["enums"],
                        stats["external_refs"],
                        refs,
                    )
                )

    missing = [row["key"] for row in rows if not row["files_present"]]
    if missing:
        print("注意：以下模块的本体文件缺失：%s" % "、".join(missing), file=sys.stderr)
        return EXIT_INPUT
    return EXIT_OK



# --------------------------------------------------------------------------
# validate：体检（只读，绝不写产物）
# --------------------------------------------------------------------------
def cmd_validate(args: argparse.Namespace) -> int:
    try:
        bundle = load_ontology(_selection(args), generated_at=args.generated_at)
    except (FileNotFoundError, ValueError, RuntimeError) as exc:
        return _fail(str(exc))

    problems = validate_ontology(bundle, allow_name_collisions=args.allow_name_collisions)
    counts = count_by_severity(problems)
    failed = has_errors(problems) or (args.fail_on_warn and has_warnings(problems))

    if args.json:
        _dump(
            {
                "compiler_version": COMPILER_VERSION,
                "ok": not failed,
                "generated_at": bundle.generated_at,
                "stats": bundle.stats(),
                "counts": counts,
                "problems": [problem.as_dict() for problem in problems],
            }
        )
        return EXIT_INVALID if failed else EXIT_OK

    report = format_report(problems, bundle, verbose=not args.summary)
    print(report)
    if args.report:
        target = Path(args.report)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(report if report.endswith("\n") else report + "\n", encoding="utf-8", newline="\n")
        print("体检报告已写入：%s" % target.as_posix())
    print(
        "结论：%s（error %d / warn %d / info %d）"
        % ("不通过" if failed else "通过", counts["error"], counts["warn"], counts["info"])
    )
    return EXIT_INVALID if failed else EXIT_OK


# --------------------------------------------------------------------------
# compile：体检 + 写产物（有 ERROR 默认不写，--force 才硬写）
# --------------------------------------------------------------------------
def cmd_compile(args: argparse.Namespace) -> int:
    root = Path(args.out) if args.out else Path(ARTIFACTS_DIR)
    try:
        result = compile_all(
            module_selection=args.module,
            out_dir=Path(args.out) if args.out else None,
            allow_name_collisions=args.allow_name_collisions,
            fail_on_warn=args.fail_on_warn,
            force=args.force,
            diff=args.diff or args.show_unchanged,
            prune=not args.keep_stale,
            generated_at=args.generated_at,
        )
    except (FileNotFoundError, ValueError, RuntimeError) as exc:
        return _fail(str(exc))

    counts = result.counts()
    failed = (not result.ok) or (args.fail_on_warn and has_warnings(result.problems))

    if args.json:
        _dump(
            {
                "compiler_version": COMPILER_VERSION,
                "ok": not failed,
                "wrote_artifacts": result.wrote_artifacts,
                "module_keys": result.module_keys,
                "out_dir": root.as_posix(),
                "generated_at": result.bundle.generated_at if result.bundle else None,
                "stats": result.bundle.stats() if result.bundle else {},
                "counts": counts,
                "problems": [problem.as_dict() for problem in result.problems],
                "artifacts": [entry.as_dict() for entry in result.artifacts],
                "removed": result.removed,
                "diff": result.diff,
            }
        )
        return EXIT_INVALID if failed else EXIT_OK

    if result.problems:
        print(format_report(result.problems, result.bundle, verbose=not args.summary))
    else:
        print("体检：未发现问题。")

    if result.wrote_artifacts:
        print("== 产物（%d）→ %s ==" % (len(result.artifacts), root.as_posix()))
        for entry in result.artifacts:
            print("  %-58s %8d B  %s" % (entry.path, entry.bytes, entry.emitter))
        if result.removed:
            print("已清理陈旧产物（%d）：%s" % (len(result.removed), "、".join(result.removed)))
        print("manifest：%s" % (result.manifest_path.as_posix() if result.manifest_path else "-"))
    else:
        print(
            "编译被阻断（error %d%s），未写任何产物；修掉 ERROR 后重跑，或 `--force` 硬写（仅排查用）。"
            % (counts["error"], "，--fail-on-warn 命中 warn %d" % counts["warn"] if args.fail_on_warn else "")
        )

    if result.diff:
        print("== 与上次编译的差异 ==")
        print(format_diff(result.diff, include_unchanged=args.show_unchanged))

    return EXIT_INVALID if failed else EXIT_OK


# --------------------------------------------------------------------------
# 命令行装配
# --------------------------------------------------------------------------
def _add_common_flags(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--module", default=None, help="只处理指定模块（逗号分隔，如 bmm,ea；默认全部）")
    parser.add_argument("--summary", action="store_true", help="体检输出只留一行一条（code + 术语）")
    parser.add_argument("--json", action="store_true", help="输出机器可读 JSON（CI 用）")
    parser.add_argument(
        "--generated-at",
        default=None,
        help="固定生成时间戳（默认取环境变量 %s，再回落当前 UTC 时间）" % GENERATED_AT_ENV,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="compile.py",
        description="BODHI2 本体编译器：ontology/*.ttl -> artifacts/（WeKnora / SHACL / Neo4j / JSON Schema / 提示词 / 标签表）",
        epilog="退出码：0 成功 / 1 校验未通过（未写产物） / 2 参数错误 / 3 输入或环境错误",
    )
    parser.add_argument("--version", action="version", version="ontology-compiler %s" % COMPILER_VERSION)
    subparsers = parser.add_subparsers(dest="command", required=True)

    p_list = subparsers.add_parser("list", help="列出本体模块（元信息；--stats 才解析 TTL）")
    p_list.add_argument("--stats", action="store_true", help="附带术语计数与跨模块引用数（会解析 TTL）")
    p_list.add_argument("--json", action="store_true", help="输出机器可读 JSON")
    p_list.add_argument("--module", default=None, help=argparse.SUPPRESS)
    p_list.add_argument(
        "--generated-at", default=None, help="固定生成时间戳（仅影响 --stats 时载入的视图）"
    )
    p_list.set_defaults(func=cmd_list)

    p_validate = subparsers.add_parser("validate", help="只体检（不写产物）")
    _add_common_flags(p_validate)
    p_validate.add_argument("--fail-on-warn", action="store_true", help="把 WARN 也当失败（退出码 1）")
    p_validate.add_argument("--allow-name-collisions", action="store_true", help="允许不同模块出现同名术语")
    p_validate.add_argument("--report", default=None, help="把体检报告写到指定文件（Markdown/文本均可）")
    p_validate.set_defaults(func=cmd_validate)

    p_compile = subparsers.add_parser("compile", help="体检 + 写产物（有 ERROR 默认不写）")
    _add_common_flags(p_compile)
    p_compile.add_argument("--out", default=None, help="产物输出目录（默认 artifacts/，测试可用临时目录）")
    p_compile.add_argument("--diff", action="store_true", help="列出与上次 manifest 的差异（added/changed/removed）")
    p_compile.add_argument("--show-unchanged", action="store_true", help="差异清单里包含 unchanged（必须先 --diff 或默认隐含）")
    p_compile.add_argument("--force", action="store_true", help="即使校验有 ERROR 也写产物（仅排查用，禁止合并）")
    p_compile.add_argument("--fail-on-warn", action="store_true", help="把 WARN 也当失败（退出码 1）")
    p_compile.add_argument("--allow-name-collisions", action="store_true", help="允许不同模块出现同名术语")
    p_compile.add_argument("--keep-stale", action="store_true", help="不清理上次 manifest 里记录、本次不再产出的文件")
    p_compile.set_defaults(func=cmd_compile)

    return parser


def main(argv: list[str] | None = None) -> int:
    _configure_stdout()
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
