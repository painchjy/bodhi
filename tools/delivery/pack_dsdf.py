"""Build the standalone dsdf-mcp JDBC service package."""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import re
import tarfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
DEFAULT_OUT = REPO / "dist-delivery"
FILES = (
    "tools/dsdf-mcp/server.py",
    "tools/dsdf-mcp/jdbc.py",
    "tools/dsdf-mcp/requirements.txt",
    "tools/dsdf-mcp/VERSION",
    "tools/dsdf-mcp/README.md",
    "deploy/delivery/dsdf-mcp/dsdf-mcp.env.example",
    "deploy/delivery/dsdf-mcp/dsdf-mcp.service",
    "deploy/delivery/dsdf-mcp/install.sh",
    "deploy/delivery/dsdf-mcp/selfcheck.py",
    "deploy/delivery/dsdf-mcp/README.md",
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description="Build standalone dsdf-mcp package")
    parser.add_argument("--version", default=dt.date.today().strftime("%Y%m%d"))
    parser.add_argument("--out", default=str(DEFAULT_OUT))
    args = parser.parse_args()
    version = str(args.version).strip()
    if not re.fullmatch(r"[0-9A-Za-z._-]+", version):
        print("版本号只允许字母/数字/._-：%r" % version)
        return 2
    missing = [name for name in FILES if not (REPO / name).is_file()]
    if missing:
        print("缺少 dsdf-mcp 文件：%s" % ", ".join(missing))
        return 2

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    package_dir = out_dir / ("bodhi2-dsdf-mcp-%s" % version)
    package_dir.mkdir()
    tar_path = package_dir / ("bodhi2-dsdf-mcp-%s.tar.gz" % version)
    with tarfile.open(tar_path, "w:gz") as archive:
        for name in FILES:
            path = REPO / name
            arcname = "dsdf-mcp/" + name
            info = archive.gettarinfo(str(path), arcname=arcname)
            info.mode = 0o755 if name.endswith((".sh", ".py")) and (
                name.endswith(".sh") or name.endswith("selfcheck.py")) else 0o644
            with path.open("rb") as stream:
                archive.addfile(info, stream)
    sums = package_dir / "SHA256SUMS"
    sums.write_text("%s  %s\n" % (_sha256(tar_path), tar_path.name), encoding="utf-8")
    print("Package: %s" % tar_path)
    print("SHA256: %s" % sums)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
