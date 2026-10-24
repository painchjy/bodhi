"""零依赖 undefined-name 自检（等价 `pyflakes` 的 F821）—— **搬家用**。

为什么需要它
-----------
`server.py` 拆分的最大风险不是语法错，而是「函数搬走了、调用点还在」⇒ **运行期 NameError**
（2026-10-22 S9 事故就是这样：`import server` OK、`active+success` OK，但每个请求都 RemoteDisconnected）。
容器里不装 pyflakes（交付口径 = 只用标准库），所以这里用 `ast` 做一次全量核对。

用法
----
    cd tools/ontology-mcp && python3 check_undefined.py          # 只读，退出码 0/1
    python3 check_undefined.py --all                             # 连同 archive/ 一起扫

豁免
----
`srv/http.py` 有一批名字来自 `bind(globals())` 注入（`call_tool` / `ke_*` / `tool_definitions` …），
它们**故意**不 import（避免和 server 成环）。这份白名单写在文件末尾，新增注入名要同步补，
否则本检查会报"未定义"（那是提示你去更新白名单，不是让你删掉那行引用 ✗）。
"""
from __future__ import annotations

import ast
import builtins
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
SKIP_DIRS = {"archive", "__pycache__"}

# `srv/http.py` 里由 `server.main()` → `bind(globals())` 注入的名字（**显式白名单**）
BIND_INJECTED = {
    "_log_tool_begin", "_log_tool_call", "_resolve_session_for", "bodhi_graph", "bodhi_model_graph",
    "bodhi_page", "call_tool", "context_lookup", "context_page", "context_scan", "crud_matrix_lines",
    "crud_model", "ke_admin", "ke_audit", "ke_context", "ke_db", "ke_docs", "ke_import", "ke_io_stats",
    "ke_ontology", "ke_pages", "ke_review", "ke_sheet", "link_source_session", "psql_csv",
    "recategorize", "refresh_folders", "render_graph_page", "render_md", "service_design_summary",
    "service_overview_lines", "sql_str", "tool_definitions", "tool_result",
}


def bound_names(tree: ast.AST) -> set:
    """模块内**被绑定**的名字（def/class/赋值/import/参数/推导式目标/except/global）。"""
    out = set(dir(builtins)) | {"__file__", "__name__", "__doc__"}
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            out.add(node.name)
        elif isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)):
            out.add(node.id)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                out.add((alias.asname or alias.name).split(".")[0])
        elif isinstance(node, ast.arg):
            out.add(node.arg)
        elif isinstance(node, ast.ExceptHandler) and node.name:
            out.add(node.name)
        elif isinstance(node, ast.Global):
            out.update(node.names)
    return out


def used_names(tree: ast.AST) -> set:
    return {n.id for n in ast.walk(tree) if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)}


def scan(path: pathlib.Path) -> list:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    allowed = bound_names(tree)
    if path.name == "http.py":
        allowed |= BIND_INJECTED
    return sorted(used_names(tree) - allowed)


def main(argv: list) -> int:
    include_archive = "--all" in argv
    bad = 0
    for path in sorted(HERE.rglob("*.py")):
        rel = path.relative_to(HERE)
        if not include_archive and (SKIP_DIRS & set(rel.parts)):
            continue
        missing = scan(path)
        if missing:
            bad += len(missing)
            print("%-34s ✗ 未定义 %d 个：%s" % (rel, len(missing), ", ".join(missing[:12])))
    print("\n未定义引用合计：%d" % bad)
    return 0 if bad == 0 else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
