"""ke-core · Neo4j（本体投影）查询客户端 —— 只用标准库（urllib）。

为什么用 HTTP 而不是 bolt
-------------------------
`docs/bodhi-reasoning.md` 的硬指标是「零第三方依赖」（与 `bodhi-mcp` 同标准）：
Python 侧没有 neo4j 驱动，也不打算装。Neo4j 官方镜像自带 HTTP 事务端点
（`POST /db/<database>/tx/commit`），一条 curl 就能跑 Cypher，于是这里用
`urllib.request` + Basic Auth 实现，参数走 `parameters`（不拼字符串，避免注入）。

连接信息（默认值取自本机实测：`NEO4J_AUTH=neo4j/password`，端口 7474 已发布到宿主机）：
    BODHI_NEO4J_HTTP  默认 http://127.0.0.1:7474
    NEO4J_USERNAME    默认 neo4j
    NEO4J_PASSWORD    默认 password
    BODHI_NEO4J_DB    默认 neo4j

投影内容（真源 `artifacts/neo4j/10_ontology.cypher`，由 ontology-compiler 生成）：
    节点 BodhiModule / BodhiOntClass / BodhiOntProperty / BodhiEnumValue / BodhiRestriction
    关系 BODHI_DECLARES / BODHI_SUBCLASS_OF / BODHI_DOMAIN / BODHI_RANGE /
         BODHI_INVERSE_OF / BODHI_HAS_RESTRICTION / BODHI_ON_PROPERTY / BODHI_ENUM_MEMBER
"""

from __future__ import annotations

import base64
import json
import os
import time
from urllib import error as urlerror
from urllib import request as urlrequest

# 取值与 `ke_db` 同口径（**进程 env 优先，其次 `.env` 文件**）：交付 `.env` 里配的
# `BODHI_NEO4J_HTTP` / `NEO4J_PASSWORD` 也必须生效（2026-10-04 修，与 ke_db 同因：旧实现只读
# `os.environ`，写在 `.env` 里的一律读不到）。
try:
    from ke_db import env_value as _env_value            # 同目录；ke_db 不反向依赖本模块
except Exception:                                        # noqa: BLE001  独立运行时退化为仅 env
    def _env_value(key: str, default: str = "") -> str:
        return os.environ.get(key, default)

try:                                     # IO 统计（P1 观测性；同目录，缺失则退化为"不统计"）
    import ke_stats as _stats
except Exception:  # noqa: BLE001
    class _stats:                        # type: ignore[no-redef]
        @staticmethod
        def enabled() -> bool:
            return False

        @staticmethod
        def record(kind: str, ms: float, ok: bool = True) -> None:
            pass


HTTP_URL = _env_value("BODHI_NEO4J_HTTP", "http://127.0.0.1:7474").rstrip("/")
USER = _env_value("NEO4J_USERNAME", "neo4j")
PASSWORD = _env_value("NEO4J_PASSWORD", "password")
DATABASE = _env_value("BODHI_NEO4J_DB", "neo4j")

# 探测结果缓存（避免每次请求都打一次探针；服务是长驻进程）
_PROBE: dict[str, float | bool] = {"ok": False, "at": 0.0}
PROBE_TTL = 30.0


def _endpoint() -> str:
    return "%s/db/%s/tx/commit" % (HTTP_URL, DATABASE)


def query(statement: str, params: dict | None = None, timeout: float = 20.0) -> list[dict]:
    """执行一条 Cypher，返回 [{列名: 值}]。失败直接抛 RuntimeError（调用方决定降级）。

    P1（2026-10-09）：整段计入 `ke_stats`（kind=`neo4j`）——超时时能看出"图库打了多少次、花了多少"。
    """
    _t0 = time.perf_counter()
    _ok = True
    try:
        body = json.dumps({"statements": [{"statement": statement,
                                           "parameters": params or {}}]}).encode("utf-8")
        token = base64.b64encode(("%s:%s" % (USER, PASSWORD)).encode("utf-8")).decode("ascii")
        req = urlrequest.Request(_endpoint(), data=body, method="POST", headers={
            "Content-Type": "application/json; charset=utf-8",
            "Authorization": "Basic " + token,
            "Accept": "application/json",
        })
        try:
            with urlrequest.urlopen(req, timeout=timeout) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
        except urlerror.HTTPError as exc:
            _ok = False
            detail = exc.read().decode("utf-8", "replace")[:300]
            raise RuntimeError("Neo4j HTTP %s：%s" % (exc.code, detail)) from exc
        except Exception as exc:  # noqa: BLE001  (URLError / timeout / 解析失败)
            _ok = False
            raise RuntimeError("Neo4j 不可用（%s）：%s" % (_endpoint(), exc)) from exc

        errors = payload.get("errors") or []
        if errors:
            _ok = False
            raise RuntimeError("Cypher 失败：%s" % json.dumps(errors, ensure_ascii=False)[:400])
        results = payload.get("results") or []
        if not results:
            return []
        columns = results[0].get("columns") or []
        return [dict(zip(columns, item.get("row") or []))
                for item in (results[0].get("data") or [])]
    finally:
        if _stats.enabled():
            _stats.record("neo4j", (time.perf_counter() - _t0) * 1000.0, _ok)


def available(force: bool = False) -> bool:
    """本体投影是否可查（带 30s 缓存；服务启动时 Neo4j 可能还在起）。"""
    now = time.time()
    if not force and now - float(_PROBE["at"] or 0) < PROBE_TTL:
        return bool(_PROBE["ok"])
    try:
        rows = query("MATCH (c:BodhiOntClass) WHERE c.bodhi_projection = 'ontology' "
                     "RETURN count(c) AS n", timeout=6.0)
        ok = bool(rows) and int(rows[0].get("n") or 0) > 0
    except Exception:  # noqa: BLE001
        ok = False
    _PROBE["ok"] = ok
    _PROBE["at"] = now
    return ok


def info() -> dict:
    """给前端/排查用的当前连接信息（不回传口令）。"""
    return {"http_url": HTTP_URL, "user": USER, "database": DATABASE,
            "available": available(force=True)}
