"""工具处理注册表（A1 切片）——**结构拆分的落点**，不改任何工具语义。

为什么要有这一层
----------------
`server.py` 原来是一个 2700+ 行的 `if name == "xxx":` 长链：新增/修改一个工具要在同一文件里
翻几百行，且难以按"域"（读/写/抽取/巡检/上下文/导入）分开评审。

做法（**双轨、零行为变化**）
--------------------------
1. `@handle("<工具名>")` 把处理函数登记进 `REGISTRY`（**别在文档里写真实工具名**：
   `tests/_helpers.py::dispatcher_names()` 会把它当成"已注册工具"✓）；
2. `server._call_tool_impl` 先调 `dispatch(name, args)`：
   命中 ⇒ 直接用注册表；未命中（返回 `None`）⇒ 继续走**原来的 if 链**。
3. 逐片迁移：每迁一个工具就从 `server.py` 删掉对应分支（保留 `srv.*` 名字不变 ⇒ 下游零改动）。

约定（重要）
-----------
- `srv/handlers/**` **不得 import server**（否则循环依赖 ✗）——只用 `srv.*` 与 `ke_*`；
- 处理函数签名统一 `f(args: dict) -> dict`，参数解析（`args.get(...)` 与默认值）**逐字照搬**原分支，
  这样"迁移"只是搬家，不是重写；
- 返回 `None` 表示"未注册"，返回 dict（含 `ok=False`）表示已处理 —— 不在注册表里做二次兜底逻辑。
"""
from __future__ import annotations

from typing import Callable, Dict, Optional

REGISTRY: Dict[str, Callable[[dict], dict]] = {}


def handle(name: str):
    """把处理函数登记为某工具的实现（同名重复登记直接报错，避免静默覆盖 ✗）。"""
    def deco(fn: Callable[[dict], dict]) -> Callable[[dict], dict]:
        if name in REGISTRY and REGISTRY[name] is not fn:
            raise RuntimeError("工具处理函数重复登记：%s" % name)
        REGISTRY[name] = fn
        return fn
    return deco


def dispatch(name: str, args: dict) -> Optional[dict]:
    """注册表优先分发；未登记 ⇒ `None`（调用方回落到旧 if 链 ✓）。"""
    fn = REGISTRY.get(name)
    if fn is None:
        return None
    return fn(args or {})


def names() -> list:
    """已迁移的工具名（文档/自检用）。"""
    return sorted(REGISTRY)


# 各切片按"域"导入，导入即登记（顺序无关：只影响注册表填充，不影响分发）
from srv.handlers import read as _read  # noqa: E402,F401
from srv.handlers import ops as _ops  # noqa: E402,F401
from srv.handlers import import_ as _import  # noqa: E402,F401
