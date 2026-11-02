"""通用文件层（**无领域语义**）：容器根 / 原子写 / 文件锁。

为什么要单独一层（2026-11-02 用户口径：两个 MCP 代码级隔离）
------------------------------------------------------------
`ke_domain_store`（04 领域 lib）与 `ke_ref_cache`（02 知识侧）都要往 `state/ext` 原子写文件，
但两边**不能互相 import** ⇒ 这段纯机械代码抽成 ke_fs，各自带一份（04 包内是副本，不指向 02）。
"""
from __future__ import annotations

import contextlib
import fcntl
import json
import os
import time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))

DEFAULT_SYSTEM = "it-platform"


def root() -> str:
    """容器根目录：`BODHI_EXT_ROOT`（默认 `<repo>/state/ext`）。"""
    return os.environ.get("BODHI_EXT_ROOT") or os.path.join(REPO, "state", "ext")


def system_dir(system: str = DEFAULT_SYSTEM) -> str:
    d = os.path.join(root(), system or DEFAULT_SYSTEM)
    os.makedirs(d, exist_ok=True)
    return d


def read_json(path: str, default=None):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:  # noqa: BLE001  文件不存在/坏 JSON ⇒ 用默认值（调用方自己判）
        return default


def write_json(path: str, data) -> dict:
    """原子写：同目录临时文件 → `os.replace`（读者永远看不到半截文件 ✓）。"""
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = "%s.tmp.%d" % (path, os.getpid())
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=1)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)
    return {"path": path, "bytes": os.path.getsize(path)}


@contextlib.contextmanager
def locked(path: str, timeout: float = 3.0):
    """对 `<path>.lock` 加排它锁；超时 ⇒ `RuntimeError("busy")`（调用方照实回执）。"""
    lock_path = path + ".lock"
    os.makedirs(os.path.dirname(lock_path) or ".", exist_ok=True)
    fh = open(lock_path, "a+")
    deadline = time.time() + max(0.1, float(timeout))
    while True:
        try:
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            break
        except BlockingIOError:
            if time.time() >= deadline:
                fh.close()
                raise RuntimeError("busy: 容器文件被占用（请重试）")
            time.sleep(0.05)
    try:
        yield
    finally:
        try:
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
        finally:
            fh.close()


