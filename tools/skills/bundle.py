"""把 `skills/<id>/SKILL.md` 打成 **WeKnora 原生技能 bundle**（zip），并算 sha256。

为什么需要它
------------
本部署的技能**目前由 MCP 承载**（`skills()` 工具读 `skills/<id>/SKILL.md`，单一来源）。
将来开沙箱后，同一份 SKILL.md 可以直接注册成 WeKnora 原生技能；原生那条路要求：

- 上传物是 **zip**，且 `SKILL.md` 在**技能根**（`tenant_skill_bundle.go`：`files["SKILL.md"]` 必需）；
- front-matter 必须是**合法 YAML**，且能取到 `name` / `description` / `version`
  （`ParseSkillFile` + `parseSkillBundleVersion`）；
- 单文件 ≤32 MiB、总量 ≤512 MiB、文件数 ≤20000（本仓库的技能远小于这些上限）。

本脚本只做“打包 + 校验 + 记账”，**不上传、不碰数据库**（上传要 Admin + 沙箱后端在线，
见 docs/agent-design-flow.md §11.7）。

用法
----
    python3 tools/skills/bundle.py                      # 打包全部技能 → skills/dist/
    python3 tools/skills/bundle.py --only domain_modeling
    python3 tools/skills/bundle.py --check              # 只校验（不写 zip）
"""

from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import re
import sys
import zipfile

REPO = pathlib.Path(__file__).resolve().parents[2]
SKILLS_DIR = REPO / "skills"
DIST_DIR = SKILLS_DIR / "dist"
MANIFEST = DIST_DIR / "manifest.json"
# 打包时一并进去的附带文件（相对技能目录；存在才带）
EXTRA_GLOBS = ("EXAMPLE.json", "examples/*.json", "examples/*.py", "examples/*.md")


def front_matter(text: str) -> dict:
    """取 SKILL.md 的 YAML front-matter（只支持本仓库用到的平铺 key: value 写法）。"""
    lines = text.lstrip("\ufeff").split("\n")
    start = next((i for i, ln in enumerate(lines) if ln.strip() == "---"), None)
    if start is None:
        return {}
    end = next((i for i in range(start + 1, len(lines)) if lines[i].strip() == "---"), None)
    if end is None:
        return {}
    meta = {}
    for line in lines[start + 1:end]:
        if not line.strip() or line.startswith((" ", "\t", "#")):
            continue
        m = re.match(r"^([A-Za-z_][\w-]*):\s*(.*)$", line)
        if m:
            meta[m.group(1)] = m.group(2).strip()
    return meta


def skill_ids() -> list[str]:
    return sorted(p.name for p in SKILLS_DIR.iterdir()
                  if p.is_dir() and (p / "SKILL.md").is_file() and p.name != "dist")


def bundle_one(skill_id: str, write: bool = True) -> dict:
    root = SKILLS_DIR / skill_id
    md = (root / "SKILL.md").read_text(encoding="utf-8")
    meta = front_matter(md)
    problems = []
    for key in ("id", "name", "description", "version"):
        if not meta.get(key):
            problems.append("front-matter 缺 %s（原生 bundle 必需）" % key)
    if meta.get("id") and meta["id"] != skill_id:
        problems.append("front-matter id(%s) 与目录名(%s) 不一致" % (meta["id"], skill_id))
    files = {"SKILL.md": md.encode("utf-8")}
    for pat in EXTRA_GLOBS:
        for extra in sorted(root.glob(pat)):
            files[extra.relative_to(root).as_posix()] = extra.read_bytes()
    payload = None
    if write and not problems:
        DIST_DIR.mkdir(parents=True, exist_ok=True)
        target = DIST_DIR / ("%s-%s.zip" % (skill_id, meta["version"]))
        with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as zf:
            for name, data in sorted(files.items()):
                zf.writestr(name, data)
        payload = target.read_bytes()
    out = {"skill": skill_id, "name": meta.get("name", ""), "version": meta.get("version", ""),
           "files": sorted(files), "bytes": sum(len(v) for v in files.values()),
           "bundle": (DIST_DIR / ("%s-%s.zip" % (skill_id, meta.get("version")))).name
                     if payload else "",
           "sha256": hashlib.sha256(payload).hexdigest() if payload else "",
           "problems": problems}
    return out


def validate_zip(zip_path: pathlib.Path) -> list[str]:
    """按 Go 侧规则复核已生成的 zip（SKILL.md 在根、能解析、sha256 可复算）。"""
    bad = []
    with zipfile.ZipFile(zip_path) as zf:
        names = zf.namelist()
        if "SKILL.md" not in names:
            bad.append("zip 根没有 SKILL.md")
        else:
            meta = front_matter(zf.read("SKILL.md").decode("utf-8"))
            for key in ("name", "description", "version"):
                if not meta.get(key):
                    bad.append("front-matter 缺 %s" % key)
        for info in zf.infolist():
            if info.file_size > 32 * 1024 * 1024:
                bad.append("单文件超 32MiB：%s" % info.filename)
    return bad


def main() -> int:
    ap = argparse.ArgumentParser(description="打包技能 bundle（zip + sha256）")
    ap.add_argument("--only", default="", help="只打某一个技能 id")
    ap.add_argument("--check", action="store_true", help="只校验，不写 zip")
    args = ap.parse_args()
    ids = [args.only] if args.only else skill_ids()
    rows, failed = [], 0
    for sid in ids:
        row = bundle_one(sid, write=not args.check)
        if not row["problems"] and not args.check:
            row["problems"] = validate_zip(DIST_DIR / row["bundle"])
        rows.append(row)
        failed += 1 if row["problems"] else 0
        print("%-24s %-8s %-6s %s" % (sid, row["version"] or "-",
                                      "%d 文件" % len(row["files"]),
                                      row["sha256"][:16] or "(check)"))
        for problem in row["problems"]:
            print("    ✗ %s" % problem)
        if row["bundle"]:
            print("    → skills/dist/%s" % row["bundle"])
    if not args.check:
        DIST_DIR.mkdir(parents=True, exist_ok=True)
        old = {}
        if MANIFEST.is_file():
            try:
                old = {r["skill"]: r for r in json.loads(MANIFEST.read_text(encoding="utf-8"))}
            except Exception:  # noqa: BLE001
                old = {}
        old.update({r["skill"]: r for r in rows})
        MANIFEST.write_text(json.dumps([old[k] for k in sorted(old)], ensure_ascii=False, indent=2),
                            encoding="utf-8")
        print("清单 → skills/dist/manifest.json（%d 个技能）" % len(old))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
