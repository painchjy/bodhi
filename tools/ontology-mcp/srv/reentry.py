"""Same-payload re-entry guard (P3 slice E: moved verbatim out of server.py).

Only write tools that bump versions/pages participate; dry_run is idempotent.
"""
from __future__ import annotations

import hashlib
import json
import threading


_REENTRY: dict = {}                                  # key → {"event","result","done","at"}
_REENTRY_LOCK = threading.Lock()
REENTRY_TOOLS = ("save_knowledge",)                  # 只有会产生版本/页面的写工具需要
REENTRY_WINDOW_S = 600                               # 兜底窗口（远大于客户端超时即可）


def _reentry_key(name: str, args: dict) -> str:
    """同载荷指纹；**只对真正的写库调用**生效（dry_run 幂等，重复无害，不该被挡）。"""
    if name not in REENTRY_TOOLS:
        return ""
    if str((args or {}).get("mode") or "dry_run").strip().lower() != "apply":
        return ""
    blob = json.dumps(args, ensure_ascii=False, sort_keys=True, default=str)
    return "%s|%s" % (name, hashlib.sha1(blob.encode("utf-8")).hexdigest())


def _prune_reentry(now: float) -> None:
    for k, v in list(_REENTRY.items()):
        if v.get("done") and now - float(v.get("at") or 0.0) > REENTRY_WINDOW_S:
            _REENTRY.pop(k, None)
