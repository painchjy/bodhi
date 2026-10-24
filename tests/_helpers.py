"""P0 重构测试 · 公共助手（零依赖 ✓）

约定（与 `tools/ke-core/tests/test_graph_first_write.py` 一致）：
- 每个 `test_*.py` 都能**单独跑**（`python3 tests/test_x.py`），`main()` 返回退出码 0=绿；
- 本模块只做**静态读取**（解析 server.py / 扫源码 / 读 golden），不连库；
- `golden/` 下是基准快照：**首次运行自动生成**，之后必须一致（改契约要显式 `--update-golden`）。
"""
import io
import json
import os
import re
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TESTS = os.path.join(REPO, "tests")
GOLDEN = os.path.join(TESTS, "golden")
SERVER = os.path.join(REPO, "tools", "ontology-mcp", "server.py")
# 2026-10-22（P2 切片 S8a）：工具 schema 的**单一来源**已搬到 `srv/schemas.py`（纯声明数据 ✓）
SCHEMAS = os.path.join(REPO, "tools", "ontology-mcp", "srv", "schemas.py")


def ensure_golden_dir() -> str:
    os.makedirs(GOLDEN, exist_ok=True)
    return GOLDEN


def read(path: str) -> str:
    return io.open(path, encoding="utf-8").read()


def tool_definitions() -> dict:
    """从 server.py 的 `tool_definitions()` 解析出 {name: inputSchema}（静态 AST 替代 ✓ 无副作用）。

    注：schema 文本是 **Python dict 字面量**（可能带尾逗号 ✗）⇒ 用 `ast.literal_eval` ✓
    （2026-10-21 实测：`json.loads` 报 Illegal trailing comma ✗）。
    """
    import ast  # noqa: PLC0415

    src = read(SCHEMAS)
    start = src.index("TOOL_SCHEMAS = [")
    end = src.rindex("\n    ]")      # 列表以 4 空格缩进的 `]` 收尾（`srv/schemas.py` ✓）
    body = src[start:end]
    out = {}
    for m in re.finditer(r'\{\s*\n\s+"name": "([a-z_0-9]+)",', body):
        name = m.group(1)
        seg = body[m.start():m.start() + 6000]
        nxt = re.search(r'\n        \{\n            "name": "', seg[10:])
        if nxt:
            seg = seg[:10 + nxt.start()]
        i = seg.find('"inputSchema":')
        if i < 0:
            out[name] = {}
            continue
        j = seg.find("{", i)
        depth, k = 0, j
        while k < len(seg):
            if seg[k] == "{":
                depth += 1
            elif seg[k] == "}":
                depth -= 1
                if depth == 0:
                    break
            k += 1
        raw = seg[j:k + 1]
        try:
            out[name] = ast.literal_eval(raw)
        except Exception:  # noqa: BLE001
            out[name] = {"_unparsed": raw[:200]}
    return out


def retired_tools() -> set:
    """退役工具集合 = **两个单一来源的并集** ✓
    · `server._RETIRED_TOOLS`（运行时拦截表）
    · `ke_eval.RETIRED`（历史退役词表，评测口径）
    """
    src = read(SERVER)
    i = src.index("_RETIRED_TOOLS = {")
    j = src.index("\n}", i)
    out = set(re.findall(r'"([a-z_0-9]+)":', src[i:j]))
    ev = os.path.join(REPO, "tools", "ke-core", "ke_eval.py")
    if os.path.isfile(ev):
        m = re.search(r"RETIRED = \{(.*?)\}", read(ev), re.S)
        if m:
            out |= set(re.findall(r'"([a-z_0-9]+)"', m.group(1)))
    return out


def dispatcher_names() -> set:
    """分发器拥有的工具名 = **旧 if 链** ∪ **A1 注册表**（`srv/handlers/*.py` 的 `@handle("x")`）。

    2026-10-24 A1 切片：工具处理函数逐个搬进 `srv/handlers/`，`server.py` 里的 `if name == …`
    会相应减少 —— 只解析 server.py 会**误判"没人处理"** ✗，所以两边都读。
    """
    out = set(re.findall(r'if name == "([a-z_0-9]+)":', read(SERVER)))
    hdir = os.path.join(REPO, "tools", "ontology-mcp", "srv", "handlers")
    if os.path.isdir(hdir):
        for fn in sorted(os.listdir(hdir)):
            if fn.endswith(".py"):
                out |= set(re.findall(r'@handle\("([a-z_0-9]+)"\)', read(os.path.join(hdir, fn))))
    return out


def load_or_init_golden(name: str, value, update: bool = False):
    """读 golden；不存在或 `--update-golden` 时写入 ✓。返回 (当前值, 是否新建/更新)。"""
    ensure_golden_dir()
    p = os.path.join(GOLDEN, name)
    if update or not os.path.isfile(p):
        io.open(p, "w", encoding="utf-8").write(
            json.dumps(value, ensure_ascii=False, indent=1, sort_keys=True) + "\n")
        return value, True
    return json.loads(read(p)), False


def report(checks: dict, title: str = "") -> int:
    """统一打印 + 返回退出码（0=全绿）。"""
    bad = [k for k, v in checks.items() if not v]
    print("%s%s" % ("[%s] " % title if title else "", "全绿 ✓" if not bad else "失败 %d 项 ✗" % len(bad)))
    for k, v in checks.items():
        print("  %s %s" % ("✓" if v else "✗", k))
    if bad:
        print("\n失败项：")
        for k in bad:
            print("  - %s" % k)
    return 0 if not bad else 1


def walk_files(root: str, suffixes=(".py", ".md")):
    for base, _dirs, files in os.walk(root):
        if "__pycache__" in base:
            continue
        for f in files:
            if f.endswith(suffixes):
                yield os.path.join(base, f)


def update_golden_flag() -> bool:
    return "--update-golden" in sys.argv
