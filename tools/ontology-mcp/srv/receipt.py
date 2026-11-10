"""MCP 工具回执模式：compact 只保留数据/错误，去掉试错提示。

部署开关（环境变量，不是智能体参数）::

    BODHI_RECEIPT_MODE=full       # 默认：保留 hint/next/why 等
    BODHI_RECEIPT_MODE=compact    # 内网：只保留 ok/error/counts/data/ref/rows/...

compact 只裁**顶层提示字段**，不递归裁业务数据：
`rows[].note` / `data[].hint` 这类业务字段保留，避免误删实例数据。
"""
from __future__ import annotations

import ke_db

COMPACT_KEYS = frozenset({
    "hint", "next", "why", "suggestion", "suggestions",
    "note", "recommendation", "troubleshooting",
})


def receipt_mode() -> str:
    raw = str(ke_db.env_value("BODHI_RECEIPT_MODE", "full") or "full").strip().lower()
    return "compact" if raw in ("compact", "short", "minimal", "1", "true", "on") else "full"


def compact_receipt(payload: dict, mode: str = "") -> dict:
    """按部署模式裁剪回执；`mode` 显式传入优先于环境变量。"""
    if not isinstance(payload, dict):
        return payload
    use = (mode or receipt_mode()).strip().lower()
    if use != "compact":
        return payload
    out = dict(payload)
    for key in COMPACT_KEYS:
        out.pop(key, None)
    out["receipt_mode"] = "compact"
    return out


def compact_result(payload: dict, mode: str = "") -> dict:
    """别名（调用方读起来更贴近“工具结果”）。"""
    return compact_receipt(payload, mode)
