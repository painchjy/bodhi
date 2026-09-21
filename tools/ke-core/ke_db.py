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
DB_PASSWORD = os.environ.get("BODHI_DB_PASSWORD", "postgres123!@#")
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


def sql_str(value) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def sql_json(value) -> str:
    return sql_str(json.dumps(value, ensure_ascii=False)) + "::jsonb"
