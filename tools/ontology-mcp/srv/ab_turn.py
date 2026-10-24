"""A/B 的「问-轮」边界（**C1 口径**，2026-10-24）—— 只记边界，**不做检索** ✓

要解决的问题（用户口径）
------------------------
"用户问一句 → 智能体**第一个检索动作就是 `knowledge_lookup`**" 才纳入 A/B 对比；
若第一步走的是别的检索（`wiki_search` 等 WeKnora 原生工具），样本不可比 ⇒ 排除。

能证明到哪一步（**诚实边界**）
------------------------------
· 服务端**看得到**：所有 MCP 工具调用顺序（本模块 + `dispatch` 认领）；
· 服务端**看不到**：WeKnora 原生工具（`wiki_search` / `grep_chunks` / `list_knowledge_chunks` /
  `get_document_info` / `wiki_read_page`）—— 它们不经过 bodhi-mcp ✗。
⇒ 所以口径是"**首个检索类 MCP 工具**"，并额外记录智能体**自报**的 `first_step`，
  报告把两者的一致性作为**数据质量指标**（不一致率高 ⇒ 纪律没执行到位，结论要打折 ✓）。

机制
----
1. `lookup_turn_begin(session_no, question)` → 开一轮：拿到 `turn_id`，只落 `logs/lookup_turns_*.jsonl`；
2. 5 个检索类工具调用时带 `turn_id` ⇒ `claim()`：**该轮第一个被认领的工具名**记下来（之后不覆盖）；
3. `knowledge_lookup` 回执的 `telemetry.turn_first_retrieval` = 认领结果
   ⇒ 报告据此判"是否首步就命中 knowledge_lookup" ✓

进程内表有上限（`_MAX_TURNS`）+ TTL（`_TTL_S`）：A/B 只是统计，不要求重启后仍在 ✓
"""
from __future__ import annotations

import json
import threading
import time
import uuid
from datetime import datetime

from srv.constants import LOG_DIR

# 检索类工具白名单：**只看"谁先动手"**，不看各工具内部做了什么
RETRIEVAL_TOOLS = ("knowledge_lookup", "context_lookup", "doc_outline",
                   "extract_candidates", "reference_lookup")

_LOCK = threading.Lock()
_TURNS: dict = {}          # turn_id → {session_no, question_key, opened, first, kb_ids}
_MAX_TURNS = 2000
_TTL_S = 30 * 60


def _qkey(text: str) -> str:
    try:
        import ke_lookup  # noqa: PLC0415

        return ke_lookup._question_key(text)
    except Exception:  # noqa: BLE001
        return (text or "").strip().lower()[:80]


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _jsonl(kind: str, rec: dict) -> None:
    """边界日志（一行一 JSON）；失败绝不影响工具返回 ✓"""
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        path = LOG_DIR / ("lookup_turns_%s.jsonl" % datetime.now().strftime("%Y%m%d"))
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(dict(rec, event=kind, ts=_now()), ensure_ascii=False) + "\n")
    except Exception:  # noqa: BLE001
        pass


def _evict(now: float) -> None:
    """清过期项 + 超上限时丢最旧的（简单 FIFO，够用 ✓）"""
    stale = [k for k, v in _TURNS.items() if now - v["opened"] > _TTL_S]
    for k in stale:
        _TURNS.pop(k, None)
    if len(_TURNS) > _MAX_TURNS:
        for k in sorted(_TURNS, key=lambda x: _TURNS[x]["opened"])[: len(_TURNS) - _MAX_TURNS]:
            _TURNS.pop(k, None)


def begin(session_no: str, question: str = "", kb_ids: list | None = None) -> dict:
    """开一轮（用户口径：**每个用户问题第一个工具调用**应当是它）"""
    turn_id = "t-" + uuid.uuid4().hex[:12]
    now = time.time()
    rec = {"session_no": str(session_no or ""), "question_key": _qkey(question),
           "opened": now, "first": "", "kb_ids": list(kb_ids or [])}
    with _LOCK:
        _TURNS[turn_id] = rec
        _evict(now)
    _jsonl("begin", {"turn_id": turn_id, "session_no": rec["session_no"],
                     "question_key": rec["question_key"], "kb_ids": rec["kb_ids"]})
    return {"ok": True, "turn_id": turn_id, "session_no": rec["session_no"],
            "question_key": rec["question_key"], "ts": _now(),
            "note": ("本轮已开；后续调用 `knowledge_lookup` 请带 `turn_id` 与 `first_step` ✓"
                     "（本工具**不做检索**，只为 A/B 判'首步'）")}


def claim(turn_id: str, tool: str) -> str:
    """认领"本轮首个检索动作"；返回**首个**工具名（未知轮次返回 `""`）。"""
    if not turn_id:
        return ""
    now = time.time()
    with _LOCK:
        rec = _TURNS.get(turn_id)
        if rec is None:
            _TURNS[turn_id] = {"session_no": "", "question_key": "", "opened": now,
                               "first": tool, "kb_ids": []}
            _evict(now)
            first = tool
        else:
            if not rec.get("first"):
                rec["first"] = tool
            first = rec["first"]
    _jsonl("claim", {"turn_id": turn_id, "tool": tool, "first": first})
    return first


def first_of(turn_id: str) -> str:
    with _LOCK:
        rec = _TURNS.get(turn_id) or {}
        return str(rec.get("first") or "")


def open_turns() -> int:
    """当前进程内未过期的轮次数（自检/报告数据质量用）"""
    now = time.time()
    with _LOCK:
        _evict(now)
        return len(_TURNS)
