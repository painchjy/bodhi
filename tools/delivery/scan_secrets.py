"""发布包 + 仓库 的敏感信息扫描（只读）。

用法：python3 tools/delivery/scan_secrets.py --dist dist-delivery/bodhi2-delivery-1.0 [--repo .]
"""
from __future__ import annotations

import argparse
import pathlib
import re
import subprocess
import sys
import tarfile
import tempfile

# 高危：真实密钥形态
PATTERNS = [
    ("OpenAI/DeepSeek 风格 key", r"sk-[A-Za-z0-9_\-]{16,}"),
    ("Anthropic key", r"sk-ant-[A-Za-z0-9_\-]{10,}"),
    ("AWS AK", r"AKIA[0-9A-Z]{12,}"),
    ("Google API key", r"AIza[0-9A-Za-z_\-]{20,}"),
    ("Slack token", r"xox[baprs]-[0-9A-Za-z\-]{10,}"),
    ("JWT / access_token", r"eyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\."),
    ("Bearer 长串", r"[Bb]earer\s+[A-Za-z0-9_\-\.]{20,}"),
    ("赋值型 api_key/secret", r"(?i)(api[_-]?key|secret[_-]?key|access[_-]?token|client[_-]?secret)\s*[:=]\s*['\"]?[A-Za-z0-9_\-]{12,}"),
    ("赋值型 password", r"(?i)(password|passwd|pgpassword)\s*[:=]\s*['\"]?[^\s'\"]{6,}"),
]
# 允许的占位符/间接引用（命中不算问题）
ALLOWLIST = re.compile(
    r"(?i)(CHANGE_ME|CHANGEME|YOUR_|xxx+|\*\*\*|<[^>]{1,30}>|env\.|getenv|os\.environ|"
    r"\$\{[A-Z_]+(\?|:-|})|placeholder|示例|占位|口令|your-|example|"
    r"BODHI_DB_PASSWORD|_ke_db|grep -hoP|PGPASSWORD=\"\$|NEO4J_PASSWORD=\$\{)"   # 读环境/读 .env 的写法
)
TEXT_EXT = {".md", ".txt", ".py", ".sh", ".yml", ".yaml", ".json", ".sql", ".jsonl", ".env",
            ".conf", ".template", ".js", ".ts", ".vue", ".css", ".html", ".service", ".example"}


def scan_text(name: str, text: str) -> list[tuple[str, int, str]]:
    hits = []
    for label, pattern in PATTERNS:
        for m in re.finditer(pattern, text):
            line_no = text[:m.start()].count("\n") + 1
            line = text.splitlines()[line_no - 1] if line_no <= len(text.splitlines()) else ""
            if ALLOWLIST.search(line):
                continue
            hits.append(("%s:%d" % (name, line_no), line_no, "[%s] %s" % (label, line.strip()[:120])))
    return hits


def scan_tree(root: pathlib.Path, prefix: str = "") -> list[tuple[str, int, str]]:
    hits = []
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        if path.suffix.lower() not in TEXT_EXT and path.name != ".env":
            continue
        if any(part in ("__pycache__", ".git") for part in path.parts):
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except Exception:  # noqa: BLE001
            continue
        hits.extend(scan_text(prefix + path.relative_to(root).as_posix(), text))
    return hits


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dist", default="dist-delivery/bodhi2-delivery-1.0")
    ap.add_argument("--repo", default=".")
    args = ap.parse_args()
    dist = pathlib.Path(args.dist)
    repo = pathlib.Path(args.repo).resolve()

    all_hits = []
    print("=== 1) 顶层交付文件 ===")
    for path in sorted(dist.iterdir()):
        if path.is_file() and path.suffix.lower() in TEXT_EXT:
            hits = scan_text(path.name, path.read_text(encoding="utf-8", errors="ignore"))
            all_hits += hits
            print("   %-34s %s" % (path.name, "命中 %d" % len(hits) if hits else "干净"))

    print("=== 2) 四个包（解包后扫描文本）===")
    for tar in sorted(dist.glob("*.tar.gz")):
        with tempfile.TemporaryDirectory() as tmp:
            sub = pathlib.Path(tmp) / tar.stem
            sub.mkdir()
            with tarfile.open(tar) as tf:
                tf.extractall(sub)
            hits = scan_tree(sub, "%s::" % tar.name)
            all_hits += hits
            print("   %-34s %s" % (tar.name, "命中 %d" % len(hits) if hits else "干净"))

    print("=== 3) 仓库受版本控制的文件 ===")
    files = subprocess.run(["git", "-C", str(repo), "ls-files"], capture_output=True, text=True).stdout.split()
    repo_hits = []
    for rel in files:
        path = repo / rel
        if not path.is_file() or path.suffix.lower() not in TEXT_EXT:
            continue
        repo_hits += scan_text("repo::" + rel, path.read_text(encoding="utf-8", errors="ignore"))
    all_hits += repo_hits
    print("   受控文件 %d 个，%s" % (len(files), "命中 %d" % len(repo_hits) if repo_hits else "干净"))

    print()
    if all_hits:
        print("!! 发现 %d 处需人工确认：" % len(all_hits))
        for where, _ln, text in all_hits[:60]:
            print("   %-70s %s" % (where, text))
        return 1
    print("结论：交付包与仓库**未发现明文密钥/口令** ✅")
    return 0


if __name__ == "__main__":
    sys.exit(main())
