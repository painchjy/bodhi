"""打独立 MCP 网关包（不并入四个主包）。

用法
----
    python3 tools/delivery/pack_gateway.py --version 20261119

产物
----
    dist-delivery/bodhi2-mcp-gateway-<version>/
      bodhi2-mcp-gateway-<version>.tar.gz
      SHA256SUMS

包内不含任何真实地址/账号；`gateway.json.example` 只有占位符。
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import re
import shutil
import tarfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SRC = REPO / "tools" / "mcp-gateway"
DEFAULT_OUT = REPO / "dist-delivery"
FILES = (
    "server.py",
    "gateway.py",
    "README.md",
    "gateway.json.example",
    "bodhi-mcp-gateway.service",
    "install.sh",
    "VERSION",
)
ARC_ROOT = "mcp-gateway"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description="打独立 MCP 网关包")
    parser.add_argument("--version", default=dt.date.today().strftime("%Y%m%d"),
                        help="包版本（默认日期 YYYYMMDD）")
    parser.add_argument("--out", default=str(DEFAULT_OUT), help="输出目录")
    args = parser.parse_args()

    version = str(args.version).strip()
    if not re.fullmatch(r"[0-9A-Za-z._-]+", version):
        print("版本号只允许字母/数字/._-：%r" % version)
        return 2

    missing = [name for name in FILES if not (SRC / name).is_file()]
    if missing:
        print("缺少网关文件：%s" % ", ".join(missing))
        return 2

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    pkg_dir = out_dir / ("bodhi2-mcp-gateway-%s" % version)
    if pkg_dir.exists():
        shutil.rmtree(pkg_dir)
    pkg_dir.mkdir(parents=True)

    tar_path = pkg_dir / ("bodhi2-mcp-gateway-%s.tar.gz" % version)
    with tarfile.open(tar_path, "w:gz") as tar:
        for name in FILES:
            path = SRC / name
            info = tar.gettarinfo(str(path), arcname="%s/%s" % (ARC_ROOT, name))
            info.mode = 0o755 if name == "install.sh" else 0o644
            with path.open("rb") as fh:
                tar.addfile(info, fh)

    digest = sha256_file(tar_path)
    sums = pkg_dir / "SHA256SUMS"
    sums.write_text("%s  %s\n" % (digest, tar_path.name), encoding="utf-8")

    print("== MCP 网关包 ==")
    print("  %s" % tar_path)
    print("  sha256=%s" % digest)
    print("  %s" % sums)
    print("校验：cd %s && sha256sum -c SHA256SUMS" % pkg_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
