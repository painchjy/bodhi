"""参考规范**抓取留痕**（02 知识侧）：`reference_lookup` 写、巡检 G7 读。

为什么在 02：这是"有没有抓过这份参考规范"的记账，与外部系统数据无关；
文件仍放 `state/ext/ref_cache.json`（同一容器根，排障时一眼看全）。
"""
from __future__ import annotations

import os
import time

from ke_fs import locked, read_json, root, write_json

# ---------------------------------------------------------------------------
# 参考规范**抓取留痕**（2026-10-26）：`reference_lookup` 写、巡检 G6 读
# ---------------------------------------------------------------------------
def ref_cache_path() -> str:
    """留痕文件（`<root>/ref_cache.json`）：`{rev, urls: {url: {found, reason, at, …}}}`。"""
    os.makedirs(root(), exist_ok=True)
    return os.path.join(root(), "ref_cache.json")


def ref_cache_load() -> dict:
    d = read_json(ref_cache_path(), {}) or {}
    return d.get("urls") if isinstance(d.get("urls"), dict) else {}


def ref_cache_put(url: str, entry: dict, keep: int = 300) -> dict:
    """记一条抓取结果（**同 URL 覆盖**，只留最近 `keep` 条）。失败不当业务失败 ✓。"""
    url = str(url or "").strip()
    if not url:
        return {"ok": False, "error": "need_url"}
    path = ref_cache_path()
    try:
        with locked(path):
            cur = read_json(path, {}) or {}
            urls = cur.get("urls") if isinstance(cur.get("urls"), dict) else {}
            urls[url] = dict(entry or {}, url=url, at=time.strftime("%Y-%m-%d %H:%M:%S"))
            if len(urls) > int(keep):
                for k in list(urls)[:len(urls) - int(keep)]:
                    urls.pop(k, None)
            write_json(path, {"note": "参考规范抓取留痕（reference_lookup 写 / 巡检 G6 读）",
                              "rev": int(cur.get("rev") or 0) + 1, "urls": urls})
        return {"ok": True, "path": path, "urls": len(urls)}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": repr(exc)[:200]}


