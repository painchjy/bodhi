"""A/B 轮次边界切片（2026-10-24，需求 2 · C1 口径）：`lookup_turn_begin`。

**只做边界标记，不做任何检索** —— 它存在的唯一理由是让服务端能判
"这一问的第一个检索动作是不是 `knowledge_lookup`"（A/B 的样本准入条件 ✓）。
实现见 `srv/ab_turn.py`（进程内表 + `logs/lookup_turns_*.jsonl`）。
"""
from __future__ import annotations

from srv import ab_turn
from srv.handlers import handle


@handle("lookup_turn_begin")
def lookup_turn_begin(args: dict) -> dict:
    return ab_turn.begin(str(args.get("session_no", "") or ""),
                         str(args.get("question", "") or ""),
                         args.get("kb_ids") or None)
