"""ke-core · **外部系统接口的参考实现：本地容器存储层**（零第三方依赖）

定位（2026-10-25 用户口径）
--------------------------
这三个 `ext_*` 工具是**给内网存量系统接口打样的参考实现**：本文件用"容器文件"模拟
外部系统的库表读写；内网落地时**只把这一层换成自己的库表访问**（SQL/ORM），
上层的绑定解析、两种模式、幂等/审计/分页口径可原样复用 ✓。

为什么单独一层
--------------
三个模拟工具（清单查询 / 条目维护 / 文档与附件访问）都要"读写容器文件"，
若各写一套，**原子性 / 并发 / 审计 / 幂等**四个口径必然漂移。这里统一：

    load_binding()      读某 DataObject 页的 `bmm:sourceLocator` → 解析成绑定
    parse_locator()     `mcp:<tool>?mode=query,maintain&key=k1,k2&stub=<slug>`
    read_json() / write_json()   原子写（临时文件 + `os.replace`）
    locked()            `fcntl.flock` 文件锁（拿不到 ⇒ 直接报 busy，不阻塞）
    audit()             写操作审计（append-only jsonl，一行一条）
    shard_path()        条目按主键分片（少锁竞争、单文件可小到几十行）

口径（与仓库既有习惯一致）
- **写默认 `dry_run=True`**：只回 diff，不落盘；
- 幂等键来自绑定（`key=`），同键重复写 = 更新而非新增；
- 代码里**不出现任何业务名词**（业务取值只在 DataObject 属性与容器数据里）。
"""
from __future__ import annotations

import contextlib
import fcntl
import json
import time
import os
import re
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)


# ---------------------------------------------------------------------------
# 通用文件能力（原子写 / 锁 / 容器根）—— 与 02 共用**同一份实现**，靠 ke_fs 隔离
# ---------------------------------------------------------------------------
from ke_fs import (DEFAULT_SYSTEM, REPO, locked, read_json,  # noqa: F401,E402
                   root as ext_root, system_dir, write_json)  # noqa: F401,E402

# 种子数据：随交付走 `deploy/mock-ext/<system>/`，`bootstrap()` **只补缺失文件** ✓
# （`state/` 在 .gitignore 里 ⇒ 种子不能放那儿，否则交付包里没有）
SEED_DIR = os.path.join(REPO, "deploy", "mock-ext")


def shard_path(name: str, key: str, subdir: str = "") -> str:
    """按主键分片：`<root>/<system>/<subdir>/<安全名>.json`（主键为空 ⇒ `_all`）。"""
    safe = re.sub(r"[^\w\-.\u4e00-\u9fff]+", "_", str(key or "")).strip("_") or "_all"
    base = system_dir()
    if subdir:
        base = os.path.join(base, subdir)
        os.makedirs(base, exist_ok=True)
    return os.path.join(base, safe + ".json")


def public_base(default: str = "http://127.0.0.1:8770") -> str:
    """本服务的**对外基地址**（附件下载 URL 用它拼；env 可覆盖，便于内网域名/IP 切换）。"""
    for k in ("IT_MCP_PUBLIC_BASE", "BODHI_EXT_PUBLIC_BASE"):
        v = (os.environ.get(k) or "").strip().rstrip("/")
        if v:
            return v
    return default


def ledger_names(system: str = DEFAULT_SYSTEM, subdir: str = "issues") -> list:
    """台账分片名（= 外层主键值）：`<root>/<system>/<subdir>/*.json` 的文件名去后缀。"""
    d = os.path.join(system_dir(system), subdir)
    if not os.path.isdir(d):
        return []
    return sorted(os.path.splitext(f)[0] for f in os.listdir(d)
                  if f.endswith(".json") and not f.startswith("_"))


def audit(action: str, payload: dict, system: str = DEFAULT_SYSTEM) -> str:
    """写操作审计（append-only）：**只在真正落盘时写** ✓"""
    path = os.path.join(system_dir(system), "audit.jsonl")
    rec = {"ts": time.strftime("%Y-%m-%d %H:%M:%S"), "action": action,
           "actor": str((payload or {}).get("actor") or ""),
           "session_no": str((payload or {}).get("session_no") or ""),
           "tool": str((payload or {}).get("tool") or ""),
           "key": str((payload or {}).get("key") or ""),
           "dry_run": bool((payload or {}).get("dry_run")),
           "before": (payload or {}).get("before"),
           "after": (payload or {}).get("after")}
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return path


def bootstrap(system: str = DEFAULT_SYSTEM) -> dict:
    """把种子数据复制到容器根（幂等；**绝不覆盖**已存在的文件）。"""
    copied = []
    src = os.path.join(SEED_DIR, system or DEFAULT_SYSTEM)
    dst = system_dir(system)
    if not os.path.isdir(src):
        return {"copied": copied, "dir": dst, "seed": src}
    for root, _dirs, files in os.walk(src):
        rel = os.path.relpath(root, src)
        out_dir = dst if rel == "." else os.path.join(dst, rel)
        os.makedirs(out_dir, exist_ok=True)
        for name in files:
            s, d = os.path.join(root, name), os.path.join(out_dir, name)
            if os.path.isfile(s) and not os.path.exists(d):
                try:
                    import shutil  # noqa: PLC0415
                    shutil.copy2(s, d)
                    copied.append(os.path.join(rel, name) if rel != "." else name)
                except Exception:  # noqa: BLE001  复制失败不影响主流程
                    pass
    return {"copied": copied, "dir": dst, "seed": src}


# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# 绑定解析（04 侧口径，2026-11-02 用户裁定）
#   外部系统**不判断绑定**：收到什么工具/数据就按什么干；"该对象绑了谁"是 02 的事。
#   （真正的绑定/映射实现已搬到 02 的 `ke_ext_binding.py`，本服务不再 import 它 ✓）
# ---------------------------------------------------------------------------
def resolve_binding(tool: str, kb_id: str = "", object_slug: str = "") -> dict:
    tool = str(tool or "").strip()
    return {"ok": True, "source": "unbound", "binding_kb": "",
            "binding": {"tool": tool, "modes": ["query", "maintain"], "keys": [], "impl": ""},
            "object": {"slug": str(object_slug or "").strip()},
            "warn": "本服务不校验绑定（属外部系统职责边界；绑定由 02 判定）"}
