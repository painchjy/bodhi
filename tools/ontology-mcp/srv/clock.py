"""统一时间戳（A1-8 迁自 `server.py::now_text`，**实现逐字未改**）

只转发 `ke_db.now_text()`，让 `srv/*` 各模块共享（`server.now_text` 由导入再导出 ✓）。
"""
from __future__ import annotations

import os
import sys

_CORE = os.path.abspath(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                     "..", "ke-core"))
if _CORE not in sys.path:
    sys.path.insert(0, _CORE)

from ke_db import now_text as _now_text  # noqa: E402


def now_text() -> str:
    """（ke-core 拆分后保留的兼容别名：实现见 ke_db.now_text）"""
    return _now_text()
