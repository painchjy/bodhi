"""ke-core · Postgres 访问（零第三方依赖，走容器里的 psql）。

为什么单独成模块（2026-09-19 用户要求：拆小 `tools/ontology-mcp/server.py`）
--------------------------------------------------------------------------
`server.py` 一度把「MCP 传输 / 抽取合并流水线 / 图谱只读接口 / 本体投影查询 /
页面维护」全塞在一个 2600+ 行的文件里，任何一次改动都要整段读进来（上下文杀手）。
现在按职责拆到 `tools/ke-core/`：

    ke_db.py         —— 本文件：psql / SQL 字面量 / 时间戳（全部模块共用）
    ke_neo4j.py      —— Neo4j（本体投影）HTTP 查询客户端
    ke_ontology.py   —— 本体查询：类清单、按类（含继承）筛对象属性、range 闭包
    ke_pages.py      —— wiki 页面维护：本体关系增删改、类型修改、批量软删除
    ke_docs.py       —— 按来源文档统计/清理本体实例（删文档后的残留）
    ke_audit.py      —— 知识运维：wiki/本体图谱/本体模型 一致性巡检 + 计划→确认→硬删
    ke_design.py     —— 设计流水线：设计页落库（页面级溯源）+ FD × 服务详设交叉验证
    reason.py        —— （下一轮）SHACL 规则判定与推导，规格见 docs/bodhi-reasoning.md

约定与 `tools/ontology-mcp/server.py` 完全一致：`docker exec psql -t -A`，
容器名/库名/口令与既有实现保持同一份默认值（可用环境变量覆盖）。
"""

from __future__ import annotations

import csv as csvlib
import io
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            pass

DB_CONTAINER = os.environ.get("BODHI_DB_CONTAINER", "WeKnora-postgres")
DB_USER = os.environ.get("BODHI_DB_USER", "postgres")
DB_NAME = os.environ.get("BODHI_DB_NAME", "WeKnora")
REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]


def _password_from_env_file() -> str:
    """从 WeKnora 部署的 `.env` 取数据库口令 —— **口令绝不写进代码/仓库**。

    查找顺序：`BODHI_WEKNORA_DIR`（推荐显式指定）→ 常见的 `…/source/WeKnora/.env` → 仓库根 `.env`；
    键名兼容 `DB_PASSWORD` / `POSTGRES_PASSWORD`。
    """
    candidates = []
    if os.environ.get("BODHI_WEKNORA_DIR"):
        candidates.append(pathlib.Path(os.environ["BODHI_WEKNORA_DIR"]) / ".env")
    candidates += [pathlib.Path("/mnt/c/Users/PHJY/source/WeKnora/.env"),
                   REPO_ROOT.parent / "WeKnora" / ".env", REPO_ROOT / ".env"]
    for path in candidates:
        try:
            if not path.is_file():
                continue
            for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
                stripped = line.strip()
                if not stripped or stripped.startswith("#") or "=" not in stripped:
                    continue
                key, _, value = stripped.partition("=")
                if key.strip() in ("DB_PASSWORD", "POSTGRES_PASSWORD"):
                    return value.strip().strip("'\"")
        except Exception:  # noqa: BLE001
            continue
    return ""


DB_PASSWORD = os.environ.get("BODHI_DB_PASSWORD") or _password_from_env_file()
# 容器化/远端部署：设了 BODHI_DB_HOST 就**直连 TCP**（用本机 psql 客户端），
# 不再 `docker exec`（容器里没有 docker CLI；也不该给 MCP 容器 docker 权限）。
DB_HOST = os.environ.get("BODHI_DB_HOST", "").strip()
DB_PORT = os.environ.get("BODHI_DB_PORT", "5432").strip()


def now_text() -> str:
    """本地时区的时间戳（与既有落库文本保持一致）。"""
    return datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M:%S %z")


def _docker_prefix() -> list[str]:
    """找可用的 docker：先 Windows PATH，再 WSL（与本仓库既有实现一致）。"""
    if shutil.which("docker"):
        probe = subprocess.run(["docker", "version", "--format", "{{.Server.Version}}"],
                               capture_output=True, text=True, check=False)
        if probe.returncode == 0:
            return ["docker"]
    if shutil.which("wsl"):
        probe = subprocess.run(["wsl", "-d", "Ubuntu", "-u", "root",
                                "docker", "version", "--format", "{{.Server.Version}}"],
                               capture_output=True, text=True, check=False)
        if probe.returncode == 0:
            return ["wsl", "-d", "Ubuntu", "-u", "root", "docker"]
    raise RuntimeError("找不到可用的 docker（Windows PATH 或 WSL 里都没有）")


def _psql_prefix() -> list[str]:
    """psql 调用前缀。

    - `BODHI_DB_HOST` 非空 → **直连 TCP**（容器/远端部署；用镜像里的 psql 客户端，
      口令通过子进程环境 PGPASSWORD 传递）；
    - 否则 → 沿用本仓库既有做法 `docker exec <容器> psql`（本机开发）。
    """
    if DB_HOST:
        exe = shutil.which("psql")
        if not exe:
            raise RuntimeError("已设 BODHI_DB_HOST 但 PATH 里没有 psql 客户端（apt install postgresql-client）")
        return [exe, "-h", DB_HOST, "-p", DB_PORT, "-U", DB_USER, "-d", DB_NAME]
    return _docker_prefix() + ["exec", "-i", "-e", "PGPASSWORD=" + DB_PASSWORD,
                               DB_CONTAINER, "psql", "-U", DB_USER, "-d", DB_NAME]


def psql(sql: str, stdin: bool = False, csv: bool = False) -> str:
    if not DB_PASSWORD:
        raise RuntimeError(
            "未设置数据库口令：请设 `BODHI_DB_PASSWORD`，或设 `BODHI_WEKNORA_DIR` 指向含 `.env` 的 WeKnora 目录"
            "（代码里不再内置任何默认口令）")
    cmd = _psql_prefix()
    env = dict(os.environ, PGPASSWORD=DB_PASSWORD) if DB_HOST else None
    if stdin:
        cmd += ["-v", "ON_ERROR_STOP=1", "-q", "-f", "-"]
        done = subprocess.run(cmd, input=sql, text=True, encoding="utf-8",
                              capture_output=True, check=False, env=env)
    else:
        cmd += (["--csv", "-c", sql] if csv else ["-t", "-A", "-c", sql])
        done = subprocess.run(cmd, text=True, encoding="utf-8", capture_output=True,
                              check=False, env=env)
    if done.returncode != 0:
        raise RuntimeError("psql 失败：%s" % (done.stderr or done.stdout)[:600])
    return done.stdout


def psql_csv(sql: str) -> list[dict]:
    return list(csvlib.DictReader(io.StringIO(psql(sql, csv=True))))


def resolve_kb_id(raw: str) -> tuple[str, str, str]:
    """把 kb_id 参数解析成真实 UUID：支持 UUID / UUID 前缀 / 知识库名称（精确或包含，忽略空格）。

    与 `server.resolve_kb_id` 同口径（2026-09-19 实测：智能体常把**名称**当 id 传；
    2026-09-21 实测：它还会**自己编**一个短串，如 `b1`）→ 因此报错信息里**必须带 id**，
    否则调用方无从纠正；这里也支持用 UUID 前缀（如 `dbc2528f`）解析。
    """
    raw = (raw or "").strip()
    rows = psql_csv("SELECT id, name FROM knowledge_bases WHERE deleted_at IS NULL "
                    "ORDER BY updated_at DESC")
    options = "、".join("%s（%s…）" % (r["name"], r["id"][:8]) for r in rows) or "（无）"
    if re.fullmatch(r"[0-9a-fA-F-]{36}", raw):
        hit = [r for r in rows if r["id"].lower() == raw.lower()]
        if hit:
            return hit[0]["id"], hit[0]["name"], ""
        raise ValueError("知识库不存在：%s（按 UUID 找，但没有这个库）；可选：%s" % (raw, options))
    if re.fullmatch(r"[0-9a-fA-F-]{4,35}", raw):
        hit = [r for r in rows if r["id"].lower().startswith(raw.lower())]
        if len(hit) == 1:
            return hit[0]["id"], hit[0]["name"], "（kb_id「%s」按 UUID 前缀解析为 %s）" % (raw, hit[0]["id"])
        if len(hit) > 1:
            raise ValueError("UUID 前缀不唯一：%s → %s" % (raw, "、".join(r["name"] for r in hit)))

    def _norm(text: str) -> str:
        return re.sub(r"[\s\u3000]+", "", text or "")

    if raw:
        want = _norm(raw)
        hit = [r for r in rows if _norm(r["name"]) == want] \
            or [r for r in rows if _norm(r["name"]) in want or want in _norm(r["name"])]
        if len(hit) == 1:
            return (hit[0]["id"], hit[0]["name"],
                    "（kb_id「%s」按名称解析为 %s）" % (raw, hit[0]["id"]))
        if len(hit) > 1:
            raise ValueError("知识库名称不唯一：%s → %s；请改用 id"
                             % (raw, "、".join("%s（%s…）" % (r["name"], r["id"][:8]) for r in hit)))
    raise ValueError("知识库不存在：%s；**请把可选清单里的 id 原样传给 kb_id**，可选：%s"
                     % (raw or "(空)", options))


def resolve_kb_candidate(raw: str) -> tuple[str, str, str]:
    """**写路径**用的解析：返回 `(id, name, mode)`，`mode` ∈ `exact` / `fuzzy`。

    与 `resolve_kb_id`（读路径，宽松）的区别：
    - `exact`：**完整 UUID** 或库名**归一化后完全相等** → 唯一无歧义，可直接用；
    - `fuzzy`：**UUID 前缀**或**名称包含**且只命中 1 个 → 不算精确，调用方必须**二次确认**
      （2026-09-22 用户口径：模糊命中=1 也要确认，只有精确匹配不用）；
    - 命中 0 个 / 多个 / 参数为空或占位符 → 抛 ValueError（带可选清单，供调用方纠正）。
    """
    raw = (raw or "").strip()
    rows = psql_csv("SELECT id, name FROM knowledge_bases WHERE deleted_at IS NULL "
                    "ORDER BY updated_at DESC")
    options = "、".join("%s（%s…）" % (r["name"], r["id"][:8]) for r in rows) or "（无）"
    if not raw:
        raise ValueError("kb_id 不能为空：**写库必须明确指定一个知识库**；可选：%s" % options)
    if raw.startswith("__") and raw.endswith("__"):
        raise ValueError("kb_id 还是占位符「%s」：请换成真实的知识库 uuid（或精确库名）；可选：%s"
                         % (raw, options))
    if re.fullmatch(r"[0-9a-fA-F-]{36}", raw):
        hit = [r for r in rows if r["id"].lower() == raw.lower()]
        if hit:
            return hit[0]["id"], hit[0]["name"], "exact"
        raise ValueError("知识库不存在：%s（按 UUID 找，没有这个库）；可选：%s" % (raw, options))
    if re.fullmatch(r"[0-9a-fA-F-]{4,35}", raw):
        hit = [r for r in rows if r["id"].lower().startswith(raw.lower())]
        if len(hit) == 1:
            return hit[0]["id"], hit[0]["name"], "fuzzy"
        if len(hit) > 1:
            raise ValueError("UUID 前缀不唯一：%s → %s" % (raw, "、".join(r["name"] for r in hit)))

    def _norm(text: str) -> str:
        return re.sub(r"[\s\u3000]+", "", text or "")

    want = _norm(raw)
    exact = [r for r in rows if _norm(r["name"]) == want]
    if len(exact) == 1:
        return exact[0]["id"], exact[0]["name"], "exact"
    if len(exact) > 1:
        raise ValueError("知识库名称重复：%s → %s；请改用 id"
                         % (raw, "、".join("%s（%s…）" % (r["name"], r["id"][:8]) for r in exact)))
    loose = [r for r in rows if _norm(r["name"]) in want or want in _norm(r["name"])]
    if len(loose) == 1:
        return loose[0]["id"], loose[0]["name"], "fuzzy"
    if len(loose) > 1:
        raise ValueError("知识库名称不唯一（包含匹配命中 %d 个）：%s → %s；请改用 id"
                         % (len(loose), raw,
                            "、".join("%s（%s…）" % (r["name"], r["id"][:8]) for r in loose)))
    raise ValueError("知识库不存在：%s；**请把可选清单里的 id 原样传给 kb_id**，可选：%s" % (raw, options))


def sql_str(value) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def sql_json(value) -> str:
    return sql_str(json.dumps(value, ensure_ascii=False)) + "::jsonb"
