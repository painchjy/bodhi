#!/usr/bin/env python3
"""Validate dsdf-mcp source, independent JDBC settings, and runtime dependencies."""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
APP = REPO / "tools" / "dsdf-mcp"
sys.path.insert(0, str(APP))

import jdbc  # noqa: E402
import server  # noqa: E402


def _read_env(path: Path) -> None:
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip("'\""))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--env-file", type=Path)
    ap.add_argument("--python", default=sys.executable)
    args = ap.parse_args()
    if args.env_file:
        _read_env(args.env_file)

    checks: list[tuple[str, bool, str]] = []
    checks.append(("three compatible tools",
                   [tool["name"] for tool in server.TOOLS]
                   == ["list_tables", "describe_table", "run_select"], ""))
    checks.append(("no ke-core database client import",
                   "ke_db" not in sys.modules, ""))
    try:
        config = jdbc.load_config()
        jdbc.validate_config(config)
        checks.append(("independent DWS/JDBC configuration", True,
                       "%s:%d/%s" % (config.host, config.port, config.database)))
    except (ValueError, OSError) as exc:
        checks.append(("independent DWS/JDBC configuration", False, str(exc)))

    try:
        probe = subprocess.run(
            [args.python, "-c",
             "import jaydebeapi, jpype; print(jpype.getDefaultJVMPath())"],
            capture_output=True, text=True, check=False)
        jvm_path = (probe.stdout or "").strip()
        available = (probe.returncode == 0 and bool(jvm_path)
                     and (shutil.which("java") or Path(jvm_path).is_file()))
        detail = jvm_path if probe.returncode == 0 else (probe.stderr or probe.stdout).strip()
        checks.append(("JayDeBeApi/JVM available", bool(available), detail))
    except (OSError, RuntimeError) as exc:
        checks.append(("JayDeBeApi/JVM available", False, str(exc)))

    failed = 0
    for label, ok, detail in checks:
        print("%s %s%s" % ("✓" if ok else "✗", label,
                           (" — " + detail) if detail else ""))
        failed += 0 if ok else 1
    print("\n%s（失败 %d 项）" % ("全绿 ✓" if not failed else "失败 ✗", failed))
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
