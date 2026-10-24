"""抽取引擎句柄（批 3b 迁自 `server.py::load_engine`，**实现逐字未改**）

只做 `sys.path` + `import extract`（`tools/ontology-extract`），**不持有任何状态** ⇒ 无环、可复用 ✓
"""
from __future__ import annotations

import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parents[3]


def load_engine():
    here = str(REPO / "tools" / "ontology-extract")
    if here not in sys.path:
        sys.path.insert(0, here)
    import extract  # noqa: PLC0415
    return extract
