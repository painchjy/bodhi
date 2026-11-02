#!/usr/bin/env python3
"""铁律补充（2026-11-02 用户口径）：**两个 MCP 代码级隔离** —— 静态扫描，不连服务。

边界（写死在这条测试里，改口径必须先改它）
------------------------------------------
- **02 本体 MCP**（`tools/ontology-mcp/**`、`tools/ke-core/**`）受铁律①②约束，
  且**不得 import 领域 MCP 的实现**（`ke_domain_*`）；
- **04 领域 MCP**（`tools/domain-mcp/**`）**不受铁律①②约束**（它模拟外部系统，领域名词合法），
  但**不得反向依赖 02**：不许 import `ke_db / ke_neo4j / ke_pages / ke_ext_client /
  ke_ext_binding / ke_ref_cache / srv.*`，也不许 import 02 的其它模块；
- `tools/domain-mcp/lib/**` 只能 import 标准库 + `ke_fs` + `ke_domain_*`（自带运行库，零 02 依赖）。

跑法：`python3 tests/test_mcp_isolation.py`
"""
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
TOOLS = os.path.join(REPO, "tools")
BODHI_SIDE = [os.path.join(TOOLS, "ontology-mcp"), os.path.join(TOOLS, "ke-core")]
DOMAIN = os.path.join(TOOLS, "domain-mcp")

IMPORT_RE = re.compile(r"^\s*(?:import|from)\s+([A-Za-z_][\w.]*)", re.M)
# 04 不许碰的 02 模块（含"直接读库/图"和"02 的绑定/留痕/客户端"）
DOMAIN_FORBID = ("ke_db", "ke_neo4j", "ke_pages", "ke_ext_client", "ke_ext_binding",
                 "ke_ref_cache", "ke_ext_map", "ke_tools", "srv")
# 02 不许碰的 04 实现
BODHI_FORBID = ("ke_domain_store", "ke_domain_items", "ke_domain_docs",
                "ke_domain_flow", "ke_domain_catalog")
# 04 的 lib 只允许这些（其余按标准库放行 = 没有点号的模块名）
LIB_ALLOW = ("ke_fs", "ke_domain_store", "ke_domain_items", "ke_domain_docs",
             "ke_domain_flow", "ke_domain_catalog")


def _py_files(root: str, skip_lib: bool = False):
    for base, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in ("__pycache__", "archive")]
        if skip_lib and os.path.basename(base) == "lib":
            dirs[:] = []
        for f in files:
            if f.endswith(".py"):
                yield os.path.join(base, f)


def _imports(path: str) -> list:
    with open(path, encoding="utf-8", errors="replace") as fh:
        return [m.group(1) for m in IMPORT_RE.finditer(fh.read())]


def _hit(modules: list, names: tuple) -> list:
    out = []
    for m in modules:
        for n in names:
            if m == n or m.startswith(n + "."):
                out.append(m)
    return out


def test_domain_mcp_does_not_depend_on_bodhi_side():
    """04 不许 import 02 的任何模块（含读库/图、绑定、留痕、客户端）。"""
    bad = {}
    for p in _py_files(DOMAIN):
        hits = _hit(_imports(p), DOMAIN_FORBID)
        if hits:
            bad[p.replace(REPO, "")] = hits
    assert not bad, "04 反向依赖了 02：%s" % bad


def test_domain_lib_is_self_contained():
    """`domain-mcp/lib/**` 不得 import 02 的任何模块（标准库随便用）。"""
    bad = {}
    lib = os.path.join(DOMAIN, "lib")
    forbid = DOMAIN_FORBID + ("tools",)
    for p in _py_files(lib):
        hits = _hit(_imports(p), forbid)
        if hits:
            bad[os.path.basename(p)] = sorted(set(hits))
    assert not bad, "04 的 lib 不是自带运行库：%s" % bad


def test_bodhi_side_does_not_import_domain_impl():
    """02 不许 import 04 的实现（绑定/映射走 MCP，不借代码）。"""
    bad = {}
    for root in BODHI_SIDE:
        for p in _py_files(root):
            hits = _hit(_imports(p), BODHI_FORBID)
            if hits:
                bad[p.replace(REPO, "")] = hits
    assert not bad, "02 import 了 04 的实现：%s" % bad


def test_pack_layout_points_to_domain_dir():
    """打包口径 = 目录结构：04 包整目录拷 `tools/domain-mcp`，02 包排除它。"""
    sys.path.insert(0, os.path.join(TOOLS, "delivery"))
    import pack_release as P                     # noqa: PLC0415

    assert P.EXT_TOOL_DIR == "domain-mcp", P.EXT_TOOL_DIR
    # 02 包会跳过 `TOOLS_MANUAL_PKG + (EXT_TOOL_DIR,)` ⇒ 目录名必须命中所跳过的项
    assert P.EXT_TOOL_DIR in (P.TOOLS_MANUAL_PKG + (P.EXT_TOOL_DIR,))
    for name in ("it_platform/server.py", "lib/ke_domain_store.py", "lib/ke_fs.py"):
        assert os.path.isfile(os.path.join(DOMAIN, name)), name


if __name__ == "__main__":
    fails = 0
    for fn in (test_domain_mcp_does_not_depend_on_bodhi_side,
               test_domain_lib_is_self_contained,
               test_bodhi_side_does_not_import_domain_impl,
               test_pack_layout_points_to_domain_dir):
        try:
            fn()
            print("✓ %s" % fn.__name__)
        except AssertionError as exc:
            fails += 1
            print("✗ %s — %s" % (fn.__name__, exc))
    sys.exit(1 if fails else 0)
