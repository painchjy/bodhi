"""请求级只读 memo（A1-8 迁自 `server.py`，**实现逐字未改**）

⚠️ 这是**隐式上下文**：`srv/session_link.py` 读它、`srv/elements.py` 清它；
生命周期 = 一次工具调用（`server._call_tool_impl` 入口 `reset()` + 写库后 `reset()`）。
彻底去隐式化 = 改成显式传入的 `RequestContext` 对象 —— 留作 A1-9（会改 `save_elements` 签名）。
"""
from __future__ import annotations

import threading


# ---------------------------------------------------------------------------
# P3-0f（2026-10-10）：**逐页只读查询**的批量预读 / 请求级 memo
#
# 实测归因（内网 33 页 dry_run，`|pg调用者:` 一行）：
#   `_resolve_page_id` **66 次**（每页 2 次：slug 查 id → 候选 id 查属主）、
#   `_session_page_ref` **32 次**（每页问一次，且问的是**同一个会话**）。
#   本机 1 次 psql ≈ 150 ms ⇒ 这两项就吃掉 ~14 s（pg 合计 138 次 / 20.9 s）。
#
# 做法：把"本批全部 slug"压成 **1 次** `IN (...)` 查询、"本批全部候选 id"再压成 1 次；
# 会话页引用是"同一问题问 N 遍"→ memo。作用域 = **一次工具调用**（`call_tool` 入口重建、
# 写库之后作废）⇒ 不跨请求复用，不会读到别处进程/别的工具的写入。
# ---------------------------------------------------------------------------
_REQ_CACHE = threading.local()


def _req_cache() -> dict:
    """本请求线程的只读查询 memo（无则自建：CLI/单测直接调这些函数时也能用）。"""
    cache = getattr(_REQ_CACHE, "d", None)
    if cache is None:
        cache = {"session_ref": {}}       # 只剩会话页引用 memo（页 id/版本预读已随 legacy 删除）
        _REQ_CACHE.d = cache
    return cache


def _reset_request_cache() -> None:
    """丢弃本请求的全部 memo。工具入口调一次；**写库之后**也必须调（页已变）。"""
    _REQ_CACHE.d = None
