"""打交付包：把前端 / MCP 服务 / 本体知识库 / 手册 分别打成可独立交付的 tar.gz。

产出（`--out` 目录下）：
    bodhi2-delivery-<version>/
      MANUAL.md  README.md  MANIFEST.json  SHA256SUMS
      bodhi2-01-frontend.tar.gz        UI 镜像(可选 docker save) + nginx 模板 + overlay + 打补丁脚本 + FRONTEND.md
      bodhi2-02-mcp-server.tar.gz      MCP 源码包 + Dockerfile/compose/systemd/自检 + MCP-SERVER.md
      bodhi2-03-ontology-kb.tar.gz     TTL 真源 + 编译器 + 投影工具 + 编译产物 + 页面种子 + ONTOLOGY-KB.md
      bodhi2-04-manual.tar.gz          docs/ + ontology/ + 五份手册 + 智能体与 MCP 登记 SQL

用法：
    python3 tools/delivery/pack_release.py --version 1.0 [--out dist-delivery] [--no-image]
    # --no-image：跳过 `docker save`（在没装 docker 的机器上打"轻包"）

设计：纯标准库；每次打包都先跑 `export_db.py` 刷新种子（保证包与当前库一致）。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time

HERE = pathlib.Path(__file__).resolve().parent
REPO = HERE.parents[1]
DELIVERY = REPO / "deploy" / "delivery"
PAYLOAD = DELIVERY / "payload"
ONTOLOGY_KB = "08810cbd-af86-48d1-bd25-3b2c338e3d68"   # 企业本体模型
UI_IMAGE = "weknora-ui:bodhi2"
MANUALS = ("MANUAL.md", "FRONTEND.md", "MCP-SERVER.md", "ONTOLOGY-KB.md", "KB-CONFIG.md",
           "TROUBLESHOOTING.md", "AGENTS-SQL.md")


def run(cmd: list[str], cwd: pathlib.Path | None = None, check: bool = True) -> int:
    print("      $ %s" % " ".join(str(c) for c in cmd))
    return subprocess.run(cmd, cwd=str(cwd) if cwd else None, check=check).returncode


def copy_tree(src: pathlib.Path, dst: pathlib.Path, ignore_pycache: bool = True) -> None:
    if not src.exists():
        return
    def _ignore(_dir, names):
        out = []
        for n in names:
            if ignore_pycache and (n == "__pycache__" or n.endswith(".pyc")):
                out.append(n)
        return out
    shutil.copytree(src, dst, dirs_exist_ok=True, ignore=_ignore if ignore_pycache else None)


def write(path: pathlib.Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def tar_dir(src: pathlib.Path, out_tar: pathlib.Path, arcname: str) -> None:
    out_tar.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(out_tar, "w:gz", format=tarfile.PAX_FORMAT) as tf:
        tf.add(src, arcname=arcname)


def sha256(path: pathlib.Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def pkg_frontend(stage: pathlib.Path, with_image: bool) -> dict:
    """01 前端包：UI 镜像 + nginx 模板 + overlay + 切换脚本 + 打补丁脚本 + 手册。"""
    d = stage / "01-frontend"
    write(d / "README.md", (DELIVERY / "FRONTEND.md").read_text(encoding="utf-8"))
    copy_tree(REPO / "deploy" / "weknora-fork" / "frontend", d / "frontend")
    copy_tree(PAYLOAD / "frontend", d / "frontend")
    shutil.copy2(REPO / "deploy" / "weknora-fork" / "deploy_frontend.sh",
                 d / "frontend" / "deploy_frontend.sh")
    shutil.copy2(REPO / "deploy" / "docker-compose.weknora.yml",
                 d / "frontend" / "docker-compose.weknora.yml")
    (d / "frontend" / "patches").mkdir(parents=True, exist_ok=True)
    for name in ("patch_frontend.py", "gen_frontend_types.py", "ontologyTypes.ts"):
        for cand in (REPO / "deploy" / "weknora-fork" / name,
                     REPO / "deploy" / "weknora-fork" / "frontend" / name):
            if cand.is_file():
                shutil.copy2(cand, d / "frontend" / "patches" / name)
                break
    info = {"image": UI_IMAGE, "image_tar": "", "image_sha256": ""}
    if with_image:
        tar_path = d / "weknora-ui-bodhi2.tar.gz"
        tmp_tar = pathlib.Path(tempfile.gettempdir()) / "weknora-ui-bodhi2.tar"
        if run(["docker", "save", "-o", str(tmp_tar), UI_IMAGE], check=False) == 0:
            import gzip  # noqa: PLC0415
            with open(tar_path, "wb") as out_fh, open(tmp_tar, "rb") as fh:
                with gzip.GzipFile(fileobj=out_fh, mode="wb", compresslevel=6) as gz:
                    shutil.copyfileobj(fh, gz, 1 << 20)
            tmp_tar.unlink(missing_ok=True)
            info["image_tar"] = tar_path.name
            info["image_sha256"] = sha256(tar_path)
        else:
            print("      !! docker save 失败（没装 docker？）→ 用 --no-image 或自行构建镜像")
    else:
        print("      （--no-image：跳过 docker save；README 里有自行构建说明）")
    return info


def pkg_mcp(stage: pathlib.Path, version: str) -> dict:
    """02 MCP 包：源码 tar（保持 REPO 布局）+ 容器/裸机部署件 + 自检脚本。"""
    d = stage / "02-mcp-server"
    src = d / "src"
    for rel in ("tools/ontology-mcp", "tools/ke-core", "tools/ontology-extract", "skills",
                "artifacts/weknora", "ontology"):
        copy_tree(REPO / rel, src / rel)
    (src / "logs").mkdir(parents=True, exist_ok=True)
    write(src / "logs" / ".gitkeep", "")
    shutil.copy2(PAYLOAD / "mcp" / ".env.example", src / ".env")
    write(src / "VERSION", "%s（打包于 %s）\n" % (version, time.strftime("%Y-%m-%d %H:%M")))
    inner = d / "bodhi2-mcp.tar.gz"
    tar_dir(src, inner, "bodhi2-mcp")
    shutil.rmtree(src)
    for name in ("Dockerfile", "docker-compose.mcp.yml", ".env.example", "bodhi2-mcp.service",
                 "selfcheck.py", "install.sh"):
        shutil.copy2(PAYLOAD / "mcp" / name, d / name)
    write(d / "README.md", (DELIVERY / "MCP-SERVER.md").read_text(encoding="utf-8"))
    return {"source_tar": inner.name, "source_sha256": sha256(inner)}


def pkg_ontology(stage: pathlib.Path, seed_dir: pathlib.Path) -> dict:
    """03 本体知识库包：TTL 真源 + 编译器 + 投影工具 + 编译产物 + 页面种子。"""
    d = stage / "03-ontology-kb"
    copy_tree(REPO / "ontology", d / "ontology")
    shutil.rmtree(d / "ontology" / "uploads", ignore_errors=True)   # 上传暂存不交付
    copy_tree(REPO / "tools" / "ontology-compiler", d / "tools" / "ontology-compiler")
    copy_tree(REPO / "tools" / "ontology-extract", d / "tools" / "ontology-extract")
    copy_tree(REPO / "tools" / "ke-core", d / "tools" / "ke-core")
    copy_tree(REPO / "artifacts", d / "artifacts")
    shutil.copy2(REPO / "deploy" / "weknora-fork" / "refresh_ontology_kb.sh",
                 d / "refresh_ontology_kb.sh")
    copy_tree(seed_dir, d)          # seed_dir 内就是 seed/ 与 sql/ 两层 → 直接铺到包根
    write(d / "README.md", (DELIVERY / "ONTOLOGY-KB.md").read_text(encoding="utf-8"))
    pages = 0
    seed_sql = d / "seed" / "ontology_kb_pages.sql"
    if seed_sql.is_file():
        pages = sum(1 for ln in seed_sql.read_text(encoding="utf-8").splitlines()
                    if ln.startswith("INSERT INTO wiki_pages"))
    return {"seed_pages": pages, "layout": sorted(p.name for p in d.iterdir())}


def pkg_manual(stage: pathlib.Path, seed_dir: pathlib.Path) -> dict:
    """04 手册包：docs/ + ontology/ + 五份手册 + 智能体与 MCP 登记 SQL + 技能全文。"""
    d = stage / "04-manual"
    d.mkdir(parents=True, exist_ok=True)
    for fname in MANUALS:
        if (DELIVERY / fname).is_file():
            shutil.copy2(DELIVERY / fname, d / fname)
    copy_tree(REPO / "docs", d / "docs")
    copy_tree(REPO / "ontology", d / "ontology")
    shutil.rmtree(d / "ontology" / "uploads", ignore_errors=True)
    copy_tree(seed_dir / "sql", d / "sql")
    copy_tree(REPO / "skills", d / "skills")
    shutil.rmtree(d / "skills" / "dist", ignore_errors=True)
    write(d / "README.md",
          "# bodhi2 配置手册包\n\n先读 `MANUAL.md`（总指引），再按需读：\n\n"
          "- `KB-CONFIG.md` —— 自定义知识库配置（wiki_config / 页面与类型约定 / 验收）\n"
          "- `TROUBLESHOOTING.md` —— 排错（12 条真实故障，含 SSRF / nginx 上游 / EOF）\n"
          "- `AGENTS-SQL.md` —— 智能体与 MCP 登记 SQL（可回滚）\n"
          "- `MCP-SERVER.md` / `FRONTEND.md` / `ONTOLOGY-KB.md` —— 三个包各自的部署指引\n"
          "- `docs/` 设计与运维文档、`ontology/` 本体规范、`skills/` 技能全文\n")
    return {"manuals": [f for f in MANUALS if (d / f).is_file()]}


def main() -> int:
    ap = argparse.ArgumentParser(description="打 bodhi2 交付包")
    ap.add_argument("--version", default=time.strftime("%Y%m%d"), help="版本号（默认日期）")
    ap.add_argument("--out", default=str(REPO / "dist-delivery"), help="输出目录")
    ap.add_argument("--no-image", action="store_true", help="跳过 docker save（轻包）")
    ap.add_argument("--skip-export", action="store_true", help="跳过刷新 DB 种子")
    args = ap.parse_args()

    out = pathlib.Path(args.out) / ("bodhi2-delivery-%s" % args.version)
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)

    print("== 1/6 刷新 DB 种子（本体模型库页面 + 智能体/MCP 登记）")
    seed_dir = pathlib.Path(tempfile.mkdtemp(prefix="bodhi-seed-"))
    if args.skip_export:
        print("   （--skip-export）")
    else:
        run([sys.executable, str(HERE / "export_db.py"), "--ontology-kb", ONTOLOGY_KB,
             "--agents", "--out", str(seed_dir)])

    print("== 2/6 暂存四个包")
    stage = pathlib.Path(tempfile.mkdtemp(prefix="bodhi-stage-"))
    fe = pkg_frontend(stage, not args.no_image)
    mcp = pkg_mcp(stage, args.version)
    ont = pkg_ontology(stage, seed_dir)
    man = pkg_manual(stage, seed_dir)

    print("== 3/6 打 tar.gz")
    packs = {}
    for sub, tar_name in (("01-frontend", "bodhi2-01-frontend.tar.gz"),
                          ("02-mcp-server", "bodhi2-02-mcp-server.tar.gz"),
                          ("03-ontology-kb", "bodhi2-03-ontology-kb.tar.gz"),
                          ("04-manual", "bodhi2-04-manual.tar.gz")):
        tar_path = out / tar_name
        with tarfile.open(tar_path, "w:gz", format=tarfile.PAX_FORMAT) as tf:
            tf.add(stage / sub, arcname=sub)
        packs[tar_name] = {"sha256": sha256(tar_path), "bytes": tar_path.stat().st_size}
        print("   %-34s %9.1f MB" % (tar_name, tar_path.stat().st_size / 1e6))

    print("== 4/6 顶层手册与清单")
    for fname in MANUALS:
        if (DELIVERY / fname).is_file():
            shutil.copy2(DELIVERY / fname, out / fname)
    write(out / "README.md",
          "# bodhi2 交付包 %s\n\n先读 **MANUAL.md**（安装部署总指引）。四个包：\n\n"
          "| 包 | 内容 | 手册 |\n|---|---|---|\n"
          "| `bodhi2-01-frontend.tar.gz` | 补丁 UI 镜像 + nginx 模板 + overlay + 验收脚本 | FRONTEND.md |\n"
          "| `bodhi2-02-mcp-server.tar.gz` | MCP 服务源码 + Dockerfile/compose/systemd + 自检 | MCP-SERVER.md |\n"
          "| `bodhi2-03-ontology-kb.tar.gz` | 本体 TTL/编译器/编译产物 + **页面种子** | ONTOLOGY-KB.md |\n"
          "| `bodhi2-04-manual.tar.gz` | docs/ + ontology/ + 手册 + 注册 SQL | MANUAL.md |\n\n"
          "校验：`sha256sum -c SHA256SUMS`\n" % args.version)
    manifest = {"version": args.version, "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                "ontology_kb": ONTOLOGY_KB, "ui_image": fe.get("image", ""),
                "packages": packs,
                "detail": {"frontend": fe, "mcp": mcp, "ontology": ont, "manual": man}}
    write(out / "MANIFEST.json", json.dumps(manifest, ensure_ascii=False, indent=2))
    write(out / "SHA256SUMS",
          "".join("%s  %s\n" % (v["sha256"], k) for k, v in packs.items()))

    print("== 5/6 清理临时目录")
    shutil.rmtree(stage, ignore_errors=True)
    shutil.rmtree(seed_dir, ignore_errors=True)

    print("== 6/6 完成：%s" % out)
    for f in sorted(out.iterdir()):
        print("   %-32s %9.2f MB" % (f.name, f.stat().st_size / 1e6))
    return 0


if __name__ == "__main__":
    sys.exit(main())
