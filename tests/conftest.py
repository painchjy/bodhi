"""pytest 公共 fixture（P0.5 ✓）

- `mcp`：MCP 客户端（HTTP 8765 ✓）；服务不可用 ⇒ **skip**（不是 fail ✓）
- `test_kb` / `test_kb_s`：`_test_*` 库（**硬删** teardown ✓，避免 context 重复 ✗）；
  使用专用 `__pytest_scope_agent__`（`kb_mode=all` + `work_multi`）
- `session_no`：函数级测试 session（每个测试独立，避免 work_multi 锁污染）
- `review_session_no` / `kbs`：真实评审智能体 session 与动态 domain/work id
"""
import json
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


@pytest.fixture(autouse=True)
def _restore_session_scope():
    """测试隔离：测试结束恢复 `state/session_scope/*.json`。

    背景（2026-11-08）：有测试用真实最新会话 + `mode="apply"` 调 `save_knowledge`，
    `work_multi` 会把该会话的工作库锁到测试库；测试 teardown 只删页/图节点，
    不删会话快照 ⇒ 污染用户会话（c263 被锁到 c7bc322f）。
    这里只做快照/恢复，不改测试语义；生产快照文件不在测试中被永久修改。
    """
    root = os.path.join(REPO, "state", "session_scope")
    before = {}
    try:
        for name in os.listdir(root):
            if name.endswith(".json"):
                with open(os.path.join(root, name), "rb") as fh:
                    before[name] = fh.read()
    except OSError:
        before = {}

    # 测试需要一个“可写任意目标库”的干净会话：先把既有 work_kb 暂时清掉，
    # 结束再按 before 原样恢复（用户当前的真实锁不会被永久改）。
    for name, data in list(before.items()):
        try:
            snap = json.loads(data.decode("utf-8"))
        except Exception:  # noqa: BLE001
            continue
        if snap.get("work_kb"):
            snap["work_kb"] = {}
            snap.pop("locked_at", None)
            try:
                with open(os.path.join(root, name), "w", encoding="utf-8") as fh:
                    json.dump(snap, fh, ensure_ascii=False, indent=1)
            except OSError:
                pass

    yield

    if not before and not os.path.isdir(root):
        return
    try:
        for name, data in before.items():
            with open(os.path.join(root, name), "wb") as fh:
                fh.write(data)
        for name in os.listdir(root):
            if name.endswith(".json") and name not in before:
                os.remove(os.path.join(root, name))
    except OSError:
        pass


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


@pytest.fixture(scope="session")
def review_session_no():
    """真实评审智能体 session（用于 domain/真实库测试；不用于临时 `_test_*` 库）。"""
    import ke_db

    try:
        rows = ke_db.psql_csv(
            "SELECT s.id FROM sessions s "
            "JOIN custom_agents a ON a.id = COALESCE(s.agent_config->>'agent_id', s.agent_id) "
            "WHERE s.deleted_at IS NULL AND a.deleted_at IS NULL "
            "  AND COALESCE(a.config->>'system_prompt','') LIKE '%【库范围】%' "
            "  AND COALESCE(a.config->>'system_prompt','') LIKE '%技术方案评审领域%' "
            "ORDER BY s.created_at DESC LIMIT 1")
    except Exception as exc:  # noqa: BLE001
        pytest.skip("review session 不可用：%r" % (str(exc)[:80],))
    if not rows:
        pytest.skip("没有可用的技术方案评审会话（先开一个真实评审会话）")
    return rows[0]["id"]


@pytest.fixture(scope="session")
def _test_scope_agent():
    """专用 pytest 智能体：`kb_mode=all` + `work_multi`，让临时 `_test_*` 库进 scope。

    只用于测试库；不注册给用户，不参与 E2E。测试结束硬删 agent。
    """
    import json
    import uuid

    import ke_db

    try:
        rows = ke_db.psql_csv(
            "SELECT tenant_id, config::text AS cfg FROM custom_agents "
            "WHERE deleted_at IS NULL ORDER BY created_at LIMIT 1")
    except Exception as exc:  # noqa: BLE001
        pytest.skip("custom_agents 不可用：%r" % (str(exc)[:80],))
    if not rows:
        pytest.skip("库里没有可克隆的 custom_agents 模板")
    try:
        cfg = json.loads(rows[0].get("cfg") or "{}")
    except Exception:  # noqa: BLE001
        cfg = {}
    # 选一个真实存在的领域库名，避免 all + domain_kb 解析失败
    dom_rows = ke_db.psql_csv(
        "SELECT name FROM knowledge_bases WHERE deleted_at IS NULL "
        "ORDER BY created_at LIMIT 1")
    dom_name = str((dom_rows[0].get("name") if dom_rows else "") or "")
    cfg.update({
        "kb_selection_mode": "all",
        "knowledge_bases": [],
        "system_prompt": ("【库范围】domain_kb: %s | work_kb: 《指定工作库》" % dom_name)
                         if dom_name else "",
    })
    agent_id = "pytest-scope-%s" % uuid.uuid4().hex[:12]
    tenant_id = int(rows[0].get("tenant_id") or 10000)
    ke_db.psql(
        "INSERT INTO custom_agents "
        "(id, name, description, avatar, is_builtin, tenant_id, created_by, config, "
        " created_at, updated_at, runnable_by_viewer) VALUES "
        "(%s, %s, %s, '', false, %d, 'pytest', %s, now(), now(), true)"
        % (ke_db.sql_str(agent_id), ke_db.sql_str("__pytest_scope_agent__"),
           ke_db.sql_str("pytest 专用库范围智能体（自动清理）"), tenant_id,
           ke_db.sql_json(cfg)))
    try:
        yield agent_id
    finally:
        try:
            ke_db.psql("DELETE FROM custom_agents WHERE id = %s" % ke_db.sql_str(agent_id))
        except Exception:  # noqa: BLE001
            pass


def _new_test_session(agent_id: str, title: str) -> str:
    """克隆一条真实 session 的必填字段，换上测试 agent；返回 session id。"""
    import uuid

    import ke_db

    sid = str(uuid.uuid4())
    try:
        ke_db.psql(
            "INSERT INTO sessions "
            "(id, tenant_id, title, description, max_rounds, enable_rewrite, "
            " fallback_strategy, fallback_response, keyword_threshold, vector_threshold, "
            " embedding_top_k, rerank_top_k, rerank_threshold, summary_parameters, "
            " is_pinned, agent_config, agent_id, created_at, updated_at) "
            "SELECT %s, tenant_id, %s, '', max_rounds, enable_rewrite, "
            "       fallback_strategy, fallback_response, keyword_threshold, vector_threshold, "
            "       embedding_top_k, rerank_top_k, rerank_threshold, summary_parameters, "
            "       false, %s::jsonb, %s, now(), now() "
            "  FROM sessions WHERE deleted_at IS NULL ORDER BY created_at DESC LIMIT 1"
            % (ke_db.sql_str(sid), ke_db.sql_str(title),
               ke_db.sql_json({"agent_id": agent_id}), ke_db.sql_str(agent_id)))
    except Exception as exc:  # noqa: BLE001
        pytest.skip("创建 pytest session 失败：%r" % (str(exc)[:100],))
    return sid


def _drop_test_session(sid: str) -> None:
    import os

    import ke_db

    try:
        ke_db.psql("DELETE FROM sessions WHERE id = %s" % ke_db.sql_str(sid))
    except Exception:  # noqa: BLE001
        pass
    try:
        root = os.path.join(REPO, "state", "session_scope")
        path = os.path.join(root, "%s.json" % sid)
        if os.path.isfile(path):
            os.remove(path)
    except OSError:
        pass


@pytest.fixture
def test_scope_session(_test_scope_agent):
    """函数级测试 session：每个测试独立，避免 work_multi 锁污染下一条用例。"""
    sid = _new_test_session(_test_scope_agent, "pytest scope")
    try:
        yield sid
    finally:
        _drop_test_session(sid)


@pytest.fixture(scope="session")
def test_scope_session_s(_test_scope_agent):
    """会话级测试 session（供 `test_kb_s` 回放用）。"""
    sid = _new_test_session(_test_scope_agent, "pytest scope (session)")
    try:
        yield sid
    finally:
        _drop_test_session(sid)


@pytest.fixture
def test_kb(mcp, session_no):
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
            r = mcp.call("knowledge_lookup", {"kb_id": f.kb_id, "mode": "keyword", "query": "_t",
                                               "session_no": session_no})
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


@pytest.fixture
def session_no(test_scope_session):
    """真实 WeKnora 会话编号（测试专用 agent 的 session）——`save_knowledge` 拒自编号 ✓。

    专用 agent 为 `kb_mode=all` + `work_multi`，临时 `_test_*` 库天然在 scope 内；
    每个测试新建 session，避免 work_multi 首次写入锁污染下一条用例。
    """
    return test_scope_session


@pytest.fixture(scope="session")
def test_kb_s(test_scope_session_s):
    """**会话级**测试库（回放用 ✓）：一次建、结束时硬删 —— 避免"每条用例建/删库"拖成分钟级 ✗"""
    import time

    from kb_factory import TestKB
    f = TestKB("replay")
    f.create()
    try:
        t0 = time.time()
        while True:
            r = _mcp_probe(f.kb_id, test_scope_session_s)
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


def _mcp_probe(kb, session_no):
    """用 MCP 读一次（**服务端视角** ✓）——不能只在测试进程查库 ✗（服务端有 60s 清单缓存）"""
    from mcp_client import MCP
    return MCP().call("knowledge_lookup", {"kb_id": kb, "mode": "keyword", "query": "_t",
                                            "session_no": session_no})


@pytest.fixture(scope="session")
def kbs(review_session_no):
    import ke_scope

    try:
        sc = ke_scope.agent_scope(review_session_no)
    except Exception as exc:  # noqa: BLE001
        pytest.skip("review scope 不可用：%r" % (str(exc)[:80],))
    if not sc.get("ok"):
        pytest.skip("review scope 解析失败：%s" % sc.get("error"))
    dom = str((sc.get("domain_kb") or {}).get("id") or "")
    work = str((sc.get("work_kb") or {}).get("id") or "")
    if not dom:
        pytest.skip("review scope 没有 domain_kb")
    return {"domain": dom, "work": work}
