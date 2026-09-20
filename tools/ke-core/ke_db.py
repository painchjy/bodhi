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
    reason.py        —— （下一轮）SHACL 规则判定与推导，规格见 docs/bodhi-reasoning.md

约定与 `tools/ontology-mcp/server.py` 完全一致：`docker exec psql -t -A`，
容器名/库名/口令与既有实现保持同一份默认值（可用环境变量覆盖）。
"""

from __future__ import annotations

import csv as csvlib
import io
import json
import os
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


def psql(sql: str, stdin: bool = False, csv: bool = False) -> str:
    cmd = _docker_prefix() + ["exec", "-i", "-e", "PGPASSWORD=" + DB_PASSWORD,
                              DB_CONTAINER, "psql", "-U", DB_USER, "-d", DB_NAME]
    if stdin:
        cmd += ["-v", "ON_ERROR_STOP=1", "-q", "-f", "-"]
        done = subprocess.run(cmd, input=sql, text=True, encoding="utf-8",
                              capture_output=True, check=False)
    else:
        cmd += (["--csv", "-c", sql] if csv else ["-t", "-A", "-c", sql])
        done = subprocess.run(cmd, text=True, encoding="utf-8", capture_output=True, check=False)
    if done.returncode != 0:
        raise RuntimeError("psql 失败：%s" % (done.stderr or done.stdout)[:600])
    return done.stdout


def psql_csv(sql: str) -> list[dict]:
    return list(csvlib.DictReader(io.StringIO(psql(sql, csv=True))))


def sql_str(value) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def sql_json(value) -> str:
    return sql_str(json.dumps(value, ensure_ascii=False)) + "::jsonb"
