"""ke-core · 每次工具调用的 **IO 统计**（PG / Neo4j 的次数与时长）。

为什么需要（2026-10-09 P1）
---------------------------
实测：**1 次 `psql` 往返 ≈ 272 ms、1 次 Neo4j ≈ 74 ms** —— 瓶颈是**往返次数**，不是计算。
但「这次调用到底打了多少次 IO、时间花在 PG 还是 Neo4j」过去**完全看不见**：
工具日志只有一个总耗时，且**超时被客户端切断时连日志都没有**（`_log_tool_call` 在返回后才写）。
内网一次 `knowledge_save` 超时 → 无从判断是 PG 慢、Neo4j 慢、还是子进程启动慢。

设计
----
· `ContextVar` 承载「当前这次工具调用」的计数桶 → `ThreadingHTTPServer` **多线程天然隔离**；
· `ke_db.psql()` / `ke_neo4j.query()` 各包一层 `record()` —— **未开启统计时零开销**（一次 `get()`）；
· 工具入口 `begin()`，出口 `snapshot()` → 写进 `logs/mcp_calls_*.log`；
· `psql` 的**子进程启动时间计入**（那正是主要成本）。

与「异步派生」的关系（P3 方向）：桶里预留 `phase`，将来 PG 派生若改异步，
派生消费者自己 `begin()/snapshot()` 就能把「派生耗时」单独记一笔，便于事后查询。
"""
from __future__ import annotations

import contextvars
import time

_KINDS = ("pg", "neo4j")
_BUCKET = contextvars.ContextVar("bodhi_io_stats", default=None)


def begin() -> dict:
    """开启本次调用统计（工具入口调用）。返回桶本身（调用方一般不看）。"""
    bucket = {k: {"n": 0, "ms": 0.0, "err": 0} for k in _KINDS}
    bucket["started"] = time.time()
    bucket["phase"] = ""
    bucket["marks"] = []
    _BUCKET.set(bucket)
    return bucket


def reset() -> None:
    _BUCKET.set(None)


def enabled() -> bool:
    return _BUCKET.get() is not None


def _who(skip: int = 2) -> str:
    """当前 IO 的**调用者**（`模块.函数`）——跳过 ke_stats/ke_neo4j/ke_db 自身的帧。

    用途（2026-10-10）：`io=neo4j:1145 次` 这种"总数很高但不知谁打的"很难定位；
    按调用者归因后，日志里直接能看到是哪个函数在打图。
    """
    import sys  # noqa: PLC0415  （只在开启统计时调用，开销可忽略）
    frame = sys._getframe(skip + 1)
    while frame is not None and frame.f_code.co_filename.endswith(
            ("ke_stats.py", "ke_neo4j.py", "ke_db.py")):
        frame = frame.f_back
    if frame is None:
        return "?"
    return "%s.%s" % (frame.f_globals.get("__name__", "?"), frame.f_code.co_name)


def record(kind: str, ms: float, ok: bool = True, who: str = "") -> None:
    """记一次 IO（未开启统计时立即返回）。`who` = 调用者（**按 kind 分开累计**，见 P3-0i2）。"""
    bucket = _BUCKET.get()
    if bucket is None:
        return
    cell = bucket.get(kind)
    if cell is None:
        return
    cell["n"] += 1
    cell["ms"] += float(ms)
    if not ok:
        cell["err"] += 1
    if who:
        # P3-0i2（2026-10-10）：归因挂在**各自类型**的格子里。此前 pg+neo4j 混在一张表，
        # `_resolve_page_id=66次` 这种看不出是打库还是打图（实测 neo4j 已降到 64 次、
        # 瓶颈转到 pg 的 138 次 psql），分开后才能直接读出 PG 侧消费点。
        by = cell.setdefault("who", {})
        by[who] = by.get(who, 0) + 1


def mark(label: str) -> None:
    """打一个**阶段标记**（如 `nodes 12/40`）——超时时能看出卡在哪个阶段。"""
    bucket = _BUCKET.get()
    if bucket is None:
        return
    bucket["marks"].append([round((time.time() - bucket["started"]) * 1000.0), label])
    if len(bucket["marks"]) > 60:                 # 防无限增长
        del bucket["marks"][:30]


def snapshot() -> dict | None:
    """当前累计（未开启统计时返回 `None`）。"""
    bucket = _BUCKET.get()
    if bucket is None:
        return None
    out: dict = {"calls": 0, "ms": 0.0, "detail": {}}
    merged: dict = {}
    for kind in _KINDS:
        cell = bucket.get(kind) or {"n": 0, "ms": 0.0, "err": 0}
        by = cell.get("who") or {}
        out["detail"][kind] = {"n": cell["n"], "ms": round(cell["ms"], 1), "err": cell["err"]}
        if by:
            out["detail"][kind]["top_callers"] = [
                [k, v] for k, v in sorted(by.items(), key=lambda kv: -kv[1])[:6]]
            for k, v in by.items():
                merged[k] = merged.get(k, 0) + v
        out["calls"] += cell["n"]
        out["ms"] += cell["ms"]
    out["ms"] = round(out["ms"], 1)
    if bucket.get("started"):
        out["wall_ms"] = round((time.time() - bucket["started"]) * 1000.0, 1)
    if bucket.get("marks"):
        out["marks"] = bucket["marks"][-8:]
    if merged:                       # 兼容旧口径：跨类型的合计 top（新日志按类型分开打印）
        out["top_callers"] = [[k, v] for k, v in sorted(merged.items(), key=lambda kv: -kv[1])[:6]]
    return out


class timed:
    """`with ke_stats.timed("pg"):` —— 自己包一层计时（也可直接调 `record`）。"""

    __slots__ = ("kind", "_t0", "ok")

    def __init__(self, kind: str):
        self.kind = kind
        self._t0 = 0.0
        self.ok = True

    def __enter__(self):
        self._t0 = time.perf_counter()
        return self

    def __exit__(self, exc_type, exc, tb):
        self.ok = exc_type is None
        record(self.kind, (time.perf_counter() - self._t0) * 1000.0, self.ok)
        return False
