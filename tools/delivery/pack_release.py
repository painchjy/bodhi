"""打交付包：前端 / MCP 服务 / 手册（文档+本体）三包，**包与包之间零重复内容**。

产出（`--out` 目录下）：
    bodhi2-delivery-<version>/
      MANUAL.md  README.md  MANIFEST.json  SHA256SUMS
      bodhi2-01-frontend.tar.gz     UI 镜像(可选 docker save) + nginx 模板 + overlay + 打补丁脚本 + FRONTEND.md
      bodhi2-02-mcp-server.tar.gz   MCP 服务「仓库根形状」整包：tools/** + artifacts/** + skills/** +
                                    Dockerfile/compose/systemd/自检/install.sh + MCP-SERVER.md
      bodhi2-03-manual.tar.gz       docs/ + ontology/(TTL 真源) + 页面种子 + 登记 SQL + 五份手册 +
                                    tools/delivery/(维护工具) + refresh_ontology_kb.sh

分包口径（2026-09-22 定，**每个文件只属于一个包**）
-------------------------------------------------
| 内容 | 归属 | 理由 |
|---|---|---|
| `deploy/weknora-fork/frontend/**`、UI 镜像、`FRONTEND.md` | 01 | 只跟 UI 有关 |
| `tools/**`（**除** `tools/delivery`）、`artifacts/**`（**除** `artifacts/shacl`）、`skills/**` | 02 | 服务运行 + 编译/投影工具的运行环境 |
| `docs/**`、`ontology/**`、`sql/**`、`seed/**`、`tools/delivery/**`、手册 5 份 | 03 | 文档 + 本体真源 + 维护工具 |
| `FRONTEND.md` / `MCP-SERVER.md` | 01 / 02 | 各自包的部署指引；总指引 `MANUAL.md` 在根目录（同时进 03 存档） |

两处容易踩的重复（已用代码去掉，并有断言兜底）
--------------------------------------------
1. `artifacts/shacl/authored/*.ttl` 与 `ontology/shapes/*.ttl` **逐字节相同** → 只留 `ontology/shapes`
   （真源，在 03）；运行时不需要 shapes（编译器用），故 02 不带 `artifacts/shacl`。
2. 各包不再把手册复制成包内 `README.md`（那会让 `01/README.md` == `03/FRONTEND.md`）→ 每包一个
   **包专属** `README.txt`。

`check_no_duplicates()` 在打包末尾对全部暂存文件做 sha256 比对（**跨包 + 包内**），发现重复直接报错
退出 —— 把「不许重复」变成机制，而不是靠人记得。

用法：
    python3 tools/delivery/pack_release.py --version 1.0 [--out dist-delivery] [--no-image]
    # --no-image：跳过 `docker save`（在没装 docker 的机器上打"轻包"）

设计：纯标准库；每次打包都先跑 `export_db.py` 刷新种子（保证包与当前库一致）。
"""

from __future__ import annotations

import argparse
import collections
import hashlib
import json
import pathlib
import re
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
# 根目录只放 MANUAL.md；FRONTEND.md → 01，MCP-SERVER.md → 02，其余 4 份 → 03
MANUAL_ROOT = ("MANUAL.md",)
MANUAL_PKG_03 = ("ONTOLOGY-KB.md", "KB-CONFIG.md", "TROUBLESHOOTING.md", "AGENTS-SQL.md")
MANUALS = MANUAL_ROOT + ("FRONTEND.md", "MCP-SERVER.md") + MANUAL_PKG_03
MCP_ARTIFACT_SKIP = ("shacl",)          # 与 03 的 ontology/shapes 逐字节相同 → 只留真源
TOOLS_MANUAL_PKG = ("delivery",)        # tools 下归 03（维护工具）
# 01 包里这三份补丁脚本只放 `frontend/patches/`（源目录里同时存在同名文件 → 必须排除一侧，否则包内重复）
FRONTEND_PATCH_FILES = ("patch_frontend.py", "gen_frontend_types.py", "ontologyTypes.ts")
PACKAGES = (("01-frontend", "bodhi2-01-frontend.tar.gz"),
            ("02-mcp-server", "bodhi2-02-mcp-server.tar.gz"),
            ("03-manual", "bodhi2-03-manual.tar.gz"))


def run(cmd: list[str], cwd: pathlib.Path | None = None, check: bool = True) -> int:
    print("      $ %s" % " ".join(str(c) for c in cmd))
    return subprocess.run(cmd, cwd=str(cwd) if cwd else None, check=check).returncode


def copy_tree(src: pathlib.Path, dst: pathlib.Path, ignore_pycache: bool = True,
              skip_top: tuple[str, ...] = (), skip_names: tuple[str, ...] = ("uploads",),
              skip_files: tuple[str, ...] = ()) -> None:
    """复制目录树；**排除 `__pycache__` / `.pyc` / `archive`**，并可按名排除。

    - `skip_top`：排除源目录下的顶层条目（如 `artifacts/shacl`、`tools/delivery`）；
    - `skip_names`：排除任意层级的同名目录（默认排除 `uploads/` —— 上传暂存不交付）；
    - `skip_files`：排除任意层级的同名**文件**（用于「同一份内容只放一个路径」）。
    排除 `archive/` 是交付口径：归档脚本里可能有旧口令写法/旧 API 用法，不进发布包。
    """
    if not src.exists():
        return

    def _ignore(dir_path, names):
        out = []
        cur = pathlib.Path(dir_path)
        for n in names:
            if n == "archive" or n in skip_names or n in skip_files:
                out.append(n)
            elif cur == src and n in skip_top:
                out.append(n)
            elif ignore_pycache and (n == "__pycache__" or n.endswith(".pyc")):
                out.append(n)
        return out

    shutil.copytree(src, dst, dirs_exist_ok=True, ignore=_ignore)


def check_mcp_artifacts(stage: pathlib.Path) -> dict:
    """断言 02 包里**带着运行时必需的编译产物**。

    为什么要断言：`artifacts/` 在 `.gitignore` 里（编译产物不入库），所以**从 git 干净签出的机器出包时
    02 包会缺 32 个文件**（`artifacts/json_schema|prompts|neo4j|mapping` 等），装上去既没有类型索引也没有
    提示词 —— 这次排查重复内容时就撞到过。缺了直接报错，别让残缺包流出去。
    """
    art = stage / "02-mcp-server" / "artifacts"
    need = art / "weknora" / "ontology_index.json"
    files = [p for p in art.rglob("*") if p.is_file()] if art.is_dir() else []
    if not need.is_file() or len(files) < 20:
        raise SystemExit(
            "!! 02 包里的编译产物不完整（%d 个文件，`artifacts/weknora/ontology_index.json` %s）：\n"
            "   `artifacts/` 被 .gitignore，必须**在有编译产物的机器上出包**，或先跑\n"
            "   `python3 tools/ontology-compiler/compile.py compile`（需要 rdflib + PyYAML）重新生成。"
            % (len(files), "存在" if need.is_file() else "缺失"))
    return {"artifact_files": len(files), "has_index": True}


def check_no_machine_ids(stage: pathlib.Path) -> dict:
    """断言交付 SQL 里**没有写死我们这套环境的 UUID**（用户 2026-09-22 抓到的坑）。

    `export_db.py` 在生成时已脱敏并自带断言；这里是**打包侧的第二道闸**（覆盖所有 SQL，
    也防止将来有人手改 SQL 又把某个 id 写回去）。白名单只有 MCP 服务自身的固定 id
    （它在 `mcp_service.sql` 里被 INSERT，并被 `agents.sql` 用 `__MCP_SERVICE_ID__` 引用）。
    """
    allow = {"a7c1f0d2-1b2e-4f3a-9c4d-b0d100000001"}
    uuid_re = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
    hits: list[str] = []
    scanned = 0
    for f in sorted((stage / "03-manual" / "sql").glob("*.sql")):
        scanned += 1
        for found in set(uuid_re.findall(f.read_text(encoding="utf-8"))):
            if found not in allow:
                hits.append("%s: %s" % (f.name, found))
    if hits:
        raise SystemExit("!! 交付 SQL 里写死了我们环境的 UUID（客户库里不存在这些对象）：\n   "
                         + "\n   ".join(hits))
    return {"sql_files": scanned, "machine_ids": 0}


def check_no_duplicates(stage: pathlib.Path) -> dict:
    """断言**三包之间 + 各包内部**没有内容相同的文件（用户口径：三包不应有重复内容）。

    以 sha256 判定「同一份内容」；空文件也算重复（所以占位文件必须带一行注释）。
    """
    seen: dict[str, tuple[str, str]] = {}
    dups: list[tuple[str, str, str]] = []
    per_pkg = collections.Counter()
    for pkg in sorted(p for p in stage.iterdir() if p.is_dir()):
        for f in sorted(pkg.rglob("*")):
            if not f.is_file():
                continue
            rel = f.relative_to(stage).as_posix()
            per_pkg[pkg.name] += 1
            h = sha256(f)
            if h in seen:
                dups.append((seen[h][0], seen[h][1], rel))
            else:
                seen[h] = (pkg.name, rel)
    if dups:
        lines = "\n".join("   %s/%s  ==  %s/%s" % (a[0], a[1], b[0], b[1]) for a, b, _ in dups)
        raise SystemExit("!! 发现重复内容（每个文件只能属于一个路径/一个包）：\n%s" % lines)
    return {"unique_files": len(seen), "per_package": dict(per_pkg)}


def write(path: pathlib.Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def sha256(path: pathlib.Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def pkg_frontend(stage: pathlib.Path, with_image: bool) -> dict:
    """01 前端包：UI 镜像 + nginx 模板 + overlay + 切换脚本 + 打补丁脚本 + 手册。"""
    d = stage / "01-frontend"
    d.mkdir(parents=True, exist_ok=True)
    (d / "frontend").mkdir(parents=True, exist_ok=True)
    shutil.copy2(DELIVERY / "FRONTEND.md", d / "FRONTEND.md")
    write(d / "README.txt",
          "bodhi2 前端包（01）\n"
          "====================\n\n"
          "先读 **FRONTEND.md**（部署/验收/外部资源清单）。\n\n"
          "  weknora-ui-bodhi2.tar.gz   UI 镜像（docker load 后得到 `%s`）\n"
          "  frontend/                  nginx 模板 / compose overlay / 部署与补丁脚本 / patches\n\n"
          "本包**不含**：MCP 服务、本体 TTL、docs/（分别在 02 / 03 包）；也不含任何密钥。\n"
          % UI_IMAGE)
    copy_tree(REPO / "deploy" / "weknora-fork" / "frontend", d / "frontend",
              skip_files=FRONTEND_PATCH_FILES)
    copy_tree(PAYLOAD / "frontend", d / "frontend", skip_files=FRONTEND_PATCH_FILES)
    shutil.copy2(REPO / "deploy" / "weknora-fork" / "deploy_frontend.sh",
                 d / "frontend" / "deploy_frontend.sh")
    shutil.copy2(REPO / "deploy" / "docker-compose.weknora.yml",
                 d / "frontend" / "docker-compose.weknora.yml")
    # 三份补丁脚本**只放 `patches/`**（否则它们会与 frontend/ 下的同名文件内容重复 → 去重断言会拦）
    (d / "frontend" / "patches").mkdir(parents=True, exist_ok=True)
    for name in FRONTEND_PATCH_FILES:
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
    """02 服务包：**仓库根形状**整包（可直接 `docker build .`）+ 部署件 + 自检 + MCP-SERVER.md。

    2026-09-22 改动：不再套一层 `bodhi2-mcp.tar.gz`。原来内层 tar 是为了"解到 /opt/bodhi2 保持布局"，
    但 `docker build .` 的构建上下文里没有 `tools/`（源码还在内层 tar 里）→ 必须先解内层 tar 才能构建，
    文档里的三步因此容易踩空。现在外层包内**就是**仓库根：解包（可 `--strip-components=1` 到 /opt/bodhi2）
    即得 `tools/ artifacts/ skills/ logs/ .env`，Dockerfile / compose 直接可用。
    """
    d = stage / "02-mcp-server"
    d.mkdir(parents=True, exist_ok=True)
    copy_tree(REPO / "tools", d / "tools", skip_top=TOOLS_MANUAL_PKG)
    copy_tree(REPO / "skills", d / "skills")
    shutil.rmtree(d / "skills" / "dist", ignore_errors=True)
    copy_tree(REPO / "artifacts", d / "artifacts", skip_top=MCP_ARTIFACT_SKIP)
    # 运行时用得到的两个可写目录（占位文件带一行注释：空文件会与他包占位文件判为重复）
    write(d / "logs" / ".gitkeep", "# 工具调用日志 mcp_calls_YYYYMMDD.log（容器/宿主都必须可写）\n")
    write(d / "ontology" / "uploads" / ".gitkeep",
          "# TTL 上传暂存目录（/bodhi/ontology/upload 写入这里）\n"
          "# 注意：本体**真源** TTL 在 03-manual 包的 ontology/ 里，本目录只作运行时暂存。\n")
    for name in ("Dockerfile", "docker-compose.mcp.yml", ".env.example", "bodhi2-mcp.service",
                 "selfcheck.py", "install.sh"):
        shutil.copy2(PAYLOAD / "mcp" / name, d / name)
    shutil.copy2(DELIVERY / "MCP-SERVER.md", d / "MCP-SERVER.md")
    write(d / "VERSION", "%s（打包于 %s）\n" % (version, time.strftime("%Y-%m-%d %H:%M")))
    write(d / "README.txt",
          "bodhi2 MCP 服务包（02）\n"
          "=====================\n\n"
          "先读 **MCP-SERVER.md**（部署/自检/环境变量）。\n\n"
          "包内就是「仓库根」形状，解包即得：\n"
          "  tools/        MCP 服务源码 + ke-core + 编译/投影工具（不再套内层 tar）\n"
          "  artifacts/    本体编译产物（ontology_index.json 等）——服务运行时要读\n"
          "  skills/       技能全文（`skills()` 工具下发）\n"
          "  logs/         工具调用日志目录（可写）\n"
          "  ontology/uploads/  上传暂存目录（TTL 真源在 03-manual 包）\n"
          "  .env.example  配置模板（cp .env.example .env 后改 BODHI_DB_*）\n"
          "  Dockerfile / docker-compose.mcp.yml / bodhi2-mcp.service / selfcheck.py / install.sh\n\n"
          "容器：  docker build -t bodhi2-mcp:1.0 .   # 包内 Dockerfile：python:3.12-slim + psql\n"
          "        docker compose -f docker-compose.mcp.yml up -d\n"
          "裸机：  sudo tar -xzf bodhi2-02-mcp-server.tar.gz -C /opt/bodhi2 --strip-components=1\n"
          "        sudo cp /opt/bodhi2/bodhi2-mcp.service /etc/systemd/system/ && sudo systemctl enable --now bodhi2-mcp\n\n"
          "本包**不含**：docs/、ontology/ 真源 TTL、sql/、页面种子、其它手册（都在 03-manual 包）；\n"
          "也不含任何 API Key / 口令（配置见 MCP-SERVER.md §2）。\n")
    return {"files": sum(1 for _ in d.rglob("*") if _.is_file()),
            "layout": sorted(p.name for p in d.iterdir())}


def pkg_manual(stage: pathlib.Path, seed_dir: pathlib.Path) -> dict:
    """03 手册包：文档 + **本体真源**（TTL/词表/shapes/queries）+ 页面种子 + 登记 SQL + 维护工具。"""
    d = stage / "03-manual"
    d.mkdir(parents=True, exist_ok=True)
    for fname in MANUAL_ROOT + MANUAL_PKG_03:
        if (DELIVERY / fname).is_file():
            shutil.copy2(DELIVERY / fname, d / fname)
    copy_tree(REPO / "docs", d / "docs")
    copy_tree(REPO / "ontology", d / "ontology")          # uploads/ 由 copy_tree 默认排除
    copy_tree(seed_dir / "sql", d / "sql")
    copy_tree(seed_dir / "seed", d / "seed")
    copy_tree(REPO / "tools" / "delivery", d / "tools" / "delivery")
    shutil.copy2(REPO / "deploy" / "weknora-fork" / "refresh_ontology_kb.sh",
                 d / "refresh_ontology_kb.sh")
    write(d / "README.txt",
          "bodhi2 手册包（03）：文档 + 本体真源\n"
          "==================================\n\n"
          "先读 **MANUAL.md**（安装部署总指引），再按需读：\n"
          "- `ONTOLOGY-KB.md` —— 本体知识库（**TTL 真源就在本包 `ontology/`**；编译/投影/上传流程）\n"
          "- `KB-CONFIG.md` —— 自定义知识库配置（wiki_config / 页面与类型约定 / 验收）\n"
          "- `TROUBLESHOOTING.md` —— 排错（含 SSRF / nginx 上游 / EOF 等真实故障）\n"
          "- `AGENTS-SQL.md` —— 智能体与 MCP 登记 SQL（可回滚）\n"
          "- `docs/` —— 设计与运维文档（`agent-design-flow.md` 是建模流程真源）\n"
          "- `ontology/` —— 本体真源：`*完整版.ttl`、`extensions/`、`shapes/`、`lexicon/`、`queries/`\n"
          "- `seed/` `sql/` —— 本体模型知识库页面种子 + 智能体/MCP 登记 SQL\n"
          "- `tools/delivery/` —— 维护工具（外链体检 `offline_harden.py`、密钥审计 `scan_secrets.py`、\n"
          "  打包 `pack_release.py`、导出种子 `export_db.py`、版本徽标 `patch_version_badge.py`）\n"
          "- `refresh_ontology_kb.sh` —— 编译 TTL → 投影本体知识库（**需与 02 包解到同一父目录**：\n"
          "  它调用 02 包的 `tools/ontology-compiler` 与 `tools/ontology-extract/ontology_wiki.py`）\n\n"
          "本包**不含**：前端材料（01 包）、MCP 服务与编译产物（02 包）。\n")
    pages = 0
    seed_sql = d / "seed" / "ontology_kb_pages.sql"
    if seed_sql.is_file():
        pages = sum(1 for ln in seed_sql.read_text(encoding="utf-8").splitlines()
                    if ln.startswith("INSERT INTO wiki_pages"))
    return {"seed_pages": pages, "manuals": [f for f in MANUAL_ROOT + MANUAL_PKG_03 if (d / f).is_file()],
            "layout": sorted(p.name for p in d.iterdir())}


def prepare_out_dir(out: pathlib.Path) -> None:
    """准备输出目录：已存在则清空（**先整体删，删不掉就清内容复用**）。

    DrvFs（/mnt/c）上目录偶尔会因 Windows 侧句柄/属性而 `rmdir` 失败（实测：`rm -rf` 与
    `rename` 都被拒，但目录内的文件可以删）。这种情况下**清空内容继续用同一个目录**，
    而不是让打包崩掉或产出到意外路径。
    """
    if not out.exists():
        out.mkdir(parents=True)
        return
    try:
        shutil.rmtree(out)
    except OSError as exc:
        print("   !! 旧目录整体删不掉（%s）→ 改为清空内容复用" % exc)
    if out.exists():
        stuck: list[str] = []
        for child in sorted(out.iterdir()):
            try:
                if child.is_dir() and not child.is_symlink():
                    shutil.rmtree(child)
                else:
                    child.unlink()
            except OSError as exc:
                stuck.append("%s（%s）" % (child.name, exc))
        if stuck:
            raise SystemExit("!! 旧目录里这些条目删不掉，无法复用 %s：\n   %s\n"
                             "   请关掉占用它们的程序（资源管理器/编辑器/解压工具）或换 --out 目录。"
                             % (out, "\n   ".join(stuck)))
    out.mkdir(parents=True, exist_ok=True)


def main() -> int:
    ap = argparse.ArgumentParser(description="打 bodhi2 交付包")
    ap.add_argument("--version", default=time.strftime("%Y%m%d"), help="版本号（默认日期）")
    ap.add_argument("--out", default=str(REPO / "dist-delivery"), help="输出目录")
    ap.add_argument("--no-image", action="store_true", help="跳过 docker save（轻包）")
    ap.add_argument("--skip-export", action="store_true", help="跳过刷新 DB 种子")
    args = ap.parse_args()

    out = pathlib.Path(args.out) / ("bodhi2-delivery-%s" % args.version)
    prepare_out_dir(out)

    print("== 1/6 刷新 DB 种子（本体模型库页面 + 智能体/MCP 登记）")
    seed_dir = pathlib.Path(tempfile.mkdtemp(prefix="bodhi-seed-"))
    if args.skip_export:
        print("   （--skip-export）")
    else:
        run([sys.executable, str(HERE / "export_db.py"), "--ontology-kb", ONTOLOGY_KB,
             "--agents", "--out", str(seed_dir)])

    print("== 2/6 暂存三个包")
    stage = pathlib.Path(tempfile.mkdtemp(prefix="bodhi-stage-"))
    fe = pkg_frontend(stage, not args.no_image)
    mcp = pkg_mcp(stage, args.version)
    man = pkg_manual(stage, seed_dir)
    dup = check_no_duplicates(stage)          # 零重复断言（跨包 + 包内）
    print("   去重断言通过：%d 个文件，无任何内容重复" % dup["unique_files"])
    for name, cnt in sorted(dup["per_package"].items()):
        print("      %-16s %4d 个文件" % (name, cnt))
    mid = check_no_machine_ids(stage)         # 交付 SQL 不许写死我们环境的 UUID
    print("   ID 断言通过：%d 个 SQL 文件里无写死 UUID（仅保留 MCP 服务自身 id）" % mid["sql_files"])
    art = check_mcp_artifacts(stage)          # 02 必须带编译产物（artifacts/ 不入 git）
    print("   编译产物断言通过：%d 个文件，ontology_index.json 在" % art["artifact_files"])

    print("== 3/6 打 tar.gz")
    packs = {}
    for sub, tar_name in PACKAGES:
        tar_path = out / tar_name
        with tarfile.open(tar_path, "w:gz", format=tarfile.PAX_FORMAT) as tf:
            tf.add(stage / sub, arcname=sub)
        packs[tar_name] = {"sha256": sha256(tar_path), "bytes": tar_path.stat().st_size}
        print("   %-34s %9.1f MB" % (tar_name, tar_path.stat().st_size / 1e6))

    print("== 4/6 顶层：只放 MANUAL.md（总指引）+ 清单")
    manual_hashes = {}
    for fname in MANUALS:
        src_file = DELIVERY / fname
        if src_file.is_file():
            manual_hashes[fname] = sha256(src_file)
    if (DELIVERY / "MANUAL.md").is_file():
        shutil.copy2(DELIVERY / "MANUAL.md", out / "MANUAL.md")
    write(out / "README.md",
          "# bodhi2 交付包 %s\n\n先读 **MANUAL.md**（安装部署总指引）。**三个包，内容互不重复**：\n\n"
          "| 包 | 内容 | 手册 |\n|---|---|---|\n"
          "| `bodhi2-01-frontend.tar.gz` | UI 镜像 + nginx 模板 + overlay + 部署/补丁脚本 | FRONTEND.md |\n"
          "| `bodhi2-02-mcp-server.tar.gz` | MCP 服务「仓库根形状」整包（tools/ artifacts/ skills/）+ Dockerfile/compose/systemd/自检 | MCP-SERVER.md |\n"
          "| `bodhi2-03-manual.tar.gz` | **文档 + 本体真源 TTL** + 页面种子 + 登记 SQL + 维护工具 + 其余 4 份手册 | MANUAL.md / ONTOLOGY-KB.md / KB-CONFIG.md / TROUBLESHOOTING.md / AGENTS-SQL.md |\n\n"
          "## 手册在哪\n\n"
          "- **根目录只放 `MANUAL.md`**（总指引，免解压即可读）；\n"
          "- 各包自带自己的部署手册（`FRONTEND.md` 在 01、`MCP-SERVER.md` 在 02），\n"
          "  `ONTOLOGY-KB.md` / `KB-CONFIG.md` / `TROUBLESHOOTING.md` / `AGENTS-SQL.md` 与 `docs/`、`ontology/` 在 **`bodhi2-03-manual.tar.gz`** 里；\n"
          "- **交接/归档以包内为准**；单一来源是仓库 `deploy/delivery/*.md`，`MANIFEST.json` 的 "
          "`manual_sha256` 记录每份哈希，可自校验。\n\n"
          "## 为什么不重复（可选读）\n\n"
          "打包器对**所有暂存文件做 sha256 两两比对**（跨包 + 包内），一旦有重复内容即报错退出：\n"
          "本体编译产物 `artifacts/shacl/authored/*.ttl` 与真源 `ontology/shapes/*.ttl` 逐字节相同，故只留真源；\n"
          "各包不再把手册复制成包内 `README.md`，改为包专属 `README.txt`。\n\n"
          "## 依赖（省心）\n\n"
          "MCP 服务**零第三方 Python 依赖**（标准库 + `psql`）；`rdflib`/`PyYAML` **只在本体编译时**需要\n"
          "（`tools/ontology-compiler`，随 02 包交付）；**不需要 `openai`**（抽取由智能体按技能做，服务端不调 LLM）。\n\n"
          "## 密钥说明\n\n"
          "发布包**不含任何 API Key / 访问令牌 / 数据库口令**（打包前跑 `tools/delivery/scan_secrets.py` 审计）：\n"
          "LLM API key 由你们在内网 WeKnora 里配置（只落在你们自己的数据库），数据库口令通过 `BODHI_DB_PASSWORD` "
          "或 WeKnora `.env` 提供 —— 清单见 `MANUAL.md` §3。\n\n"
          "校验：`sha256sum -c SHA256SUMS`\n" % args.version)
    manifest = {"version": args.version, "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                "ontology_kb": ONTOLOGY_KB, "ui_image": fe.get("image", ""),
                "packages_count": len(PACKAGES),
                "no_duplicate_contents": True,
                "no_machine_specific_ids": True,
                "unique_files": dup["unique_files"], "files_per_package": dup["per_package"],
                "manual_sha256": manual_hashes,
                "manuals_authoritative": ("按包归属：根目录 MANUAL.md；FRONTEND.md→01；MCP-SERVER.md→02；"
                                          "其余手册 + docs/ + ontology/(TTL 真源) + sql/ + seed/→03"),
                "split_policy": {"01": "前端材料（镜像/模板/脚本/FRONTEND.md）",
                                 "02": "MCP 服务：tools/**（除 tools/delivery）+ artifacts/**（除 artifacts/shacl）+ skills/** + 部署件",
                                 "03": "文档 + 本体真源 ontology/** + sql/** + seed/** + tools/delivery/** + 其余手册"},
                "runtime_deps": {"mcp_server": "零第三方 Python 依赖（标准库 + psql）",
                                 "ontology_compile": "rdflib + PyYAML（仅 tools/ontology-compiler）",
                                 "llm": "服务端不调 LLM（无 openai 依赖）"},
                "secrets_note": "包内不含 API key / token / 数据库口令；请在内网重新配置（MANUAL.md §3）",
                "packages": packs,
                "detail": {"frontend": fe, "mcp": mcp, "manual": man}}
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
