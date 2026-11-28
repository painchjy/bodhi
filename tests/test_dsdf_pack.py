"""Standalone dsdf-mcp release package tests."""
from __future__ import annotations

import sys
import tarfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "tools" / "delivery"))

import pack_dsdf  # noqa: E402


def test_dsdf_package_contains_runtime_and_no_vendor_jar(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["pack_dsdf.py", "--version", "test1",
                                      "--out", str(tmp_path)])

    assert pack_dsdf.main() == 0

    archive_path = tmp_path / "bodhi2-dsdf-mcp-test1" / "bodhi2-dsdf-mcp-test1.tar.gz"
    with tarfile.open(archive_path, "r:gz") as archive:
        names = archive.getnames()
    assert "dsdf-mcp/tools/dsdf-mcp/server.py" in names
    assert "dsdf-mcp/deploy/delivery/dsdf-mcp/dsdf-mcp.env.example" in names
    assert not any(name.lower().endswith(".jar") for name in names)
    assert not any(name.endswith(".env") for name in names)
