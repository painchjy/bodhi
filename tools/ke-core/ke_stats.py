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


def record(kind: str, ms: float, ok: bool = True) -> None:
    """记一次 IO（未开启统计时立即返回）。"""
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
    for kind in _KINDS:
        cell = bucket.get(kind) or {"n": 0, "ms": 0.0, "err": 0}
        out["detail"][kind] = {"n": cell["n"], "ms": round(cell["ms"], 1), "err": cell["err"]}
        out["calls"] += cell["n"]
        out["ms"] += cell["ms"]
    out["ms"] = round(out["ms"], 1)
    if bucket.get("started"):
        out["wall_ms"] = round((time.time() - bucket["started"]) * 1000.0, 1)
    if bucket.get("marks"):
        out["marks"] = bucket["marks"][-8:]
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
