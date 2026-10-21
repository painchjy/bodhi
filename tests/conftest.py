"""pytest 公共 fixture（P0.5 ✓）

- `mcp`：MCP 客户端（HTTP 8765 ✓）；服务不可用 ⇒ **skip**（不是 fail ✓）
- `test_kb`：`_test_*` 库（**硬删** teardown ✓，避免 context 重复 ✗）
- `domain_kb` / `work_kb`：领域库 / 工作库 id（DB 不可用 ⇒ skip ✓）
"""
import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
for p in (HERE, os.path.join(REPO, "tools", "ke-core")):
    if p not in sys.path:
        sys.path.insert(0, p)


def _load_dotenv() -> None:
    """测试进程没有 systemd 的 EnvironmentFile ✗ ⇒ 自己读 repo/.env 注入 ✓（2026-10-21 实测）
    只补**未设置**的键，不覆盖已有环境变量 ✓。"""
    p = os.path.join(REPO, ".env")
    if not os.path.isfile(p):
        return
    for ln in open(p, encoding="utf-8", errors="ignore"):
        s = ln.strip()
        if not s or s.startswith("#") or "=" not in s:
            continue
        k, v = s.split("=", 1)
        k, v = k.strip(), v.strip().strip('"').strip("'")
        if k and k not in os.environ:
            os.environ[k] = v


_load_dotenv()


@pytest.fixture(scope="session")
def mcp():
    from mcp_client import MCP
    try:
        c = MCP()
    except Exception as exc:  # noqa: BLE001
        pytest.skip("MCP 不可用：%r" % (str(exc)[:80],))
    if not c.available():
        pytest.skip("MCP tools/list 为空")
    return c


@pytest.fixture
def test_kb(mcp):
    """`_test_*` 库（**硬删** teardown ✓）。

    ⚠️ 2026-10-21 实测：服务端 `ke_db._kb_rows()` 有 **60s 进程内缓存** ⇒ 刚建的库在
    `resolve_write_kb` 里 **查不到**（回执 `kb_unresolved: not_found`，但 candidates 里就有它 ✗）。
    ⇒ 建库后必须**轮询到服务端可见**再用 ✓（不改生产缓存策略 ✗）
    """
    import time

    from kb_factory import TestKB
    f = TestKB("pytest")
    f.create()
    try:
        t0 = time.time()
        while True:
            r = mcp.call("knowledge_lookup", {"kb_id": f.kb_id, "mode": "keyword", "query": "_t"})
            # 服务端对"看不见的库"有两种回执形态（实测 ✓）：`kb_unresolved`（写路径解析器）
            # 与 `error: 知识库不存在…`（读路径解析器）⇒ 两种都要等 ✓
            miss = (r.get("kb_unresolved") or r.get("need_kb_selection")
                    or "知识库不存在" in str(r.get("error") or ""))
            if not miss:
                break
            if time.time() - t0 > 90:
                pytest.skip("服务端 90s 内仍未看到测试库（kb 清单缓存 60s）")
            time.sleep(5)
        yield f.kb_id
    finally:
        f.drop()          # 硬删 + 断言零残留 ✓


@pytest.fixture(scope="session")
def session_no():
    """**真实** WeKnora 会话编号（`sessions.id`）——`save_knowledge` 拒自编号 ✓
    （`server.py:1608` `_resolve_session_identity` ⇒ 查 `sessions` 表，`deleted_at IS NULL`）"""
    import ke_db
    try:
        rows = ke_db.psql_csv("SELECT id FROM sessions WHERE deleted_at IS NULL "
                              "ORDER BY created_at DESC LIMIT 1")
    except Exception as exc:  # noqa: BLE001
        pytest.skip("sessions 表不可用：%r" % (str(exc)[:80],))
    if not rows:
        pytest.skip("库里没有可用会话（不许伪造编号 ✓）")
    return rows[0]["id"]


@pytest.fixture(scope="session")
def test_kb_s():
    """**会话级**测试库（回放用 ✓）：一次建、结束时硬删 —— 避免"每条用例建/删库"拖成分钟级 ✗"""
    import time

    from kb_factory import TestKB
    f = TestKB("replay")
    f.create()
    try:
        t0 = time.time()
        while True:
            r = _mcp_probe(f.kb_id)
            miss = (r.get("kb_unresolved") or r.get("need_kb_selection")
                    or "知识库不存在" in str(r.get("error") or ""))
            if not miss:
                break
            if time.time() - t0 > 90:
                pytest.skip("服务端 90s 内仍未看到测试库（kb 清单缓存 60s）")
            time.sleep(5)
        yield f.kb_id
    finally:
        f.drop()


def _mcp_probe(kb):
    """用 MCP 读一次（**服务端视角** ✓）——不能只在测试进程查库 ✗（服务端有 60s 清单缓存）"""
    from mcp_client import MCP
    return MCP().call("knowledge_lookup", {"kb_id": kb, "mode": "keyword", "query": "_t"})


@pytest.fixture(scope="session")
def kbs():
    import ke_db
    try:
        dom = ke_db.resolve_kb_id("技术方案评审领域模型")[0]
        work = ke_db.resolve_kb_id("技术方案评审任务知识库")[0]
    except Exception as exc:  # noqa: BLE001
        pytest.skip("DB 不可用：%r" % (str(exc)[:80],))
    return {"domain": dom, "work": work}
