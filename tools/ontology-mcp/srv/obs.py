"""Tool-call observability (P3 slice D: moved verbatim out of server.py).

Two-phase logging: BEGIN before the tool runs (client timeouts stay visible), DONE with
timing + PG/Neo4j round trips + result digest.
"""
from __future__ import annotations

import json
import os
import re
import sys
from datetime import datetime

_CORE = os.path.abspath(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "..", "ke-core"))
if _CORE not in sys.path:
    sys.path.insert(0, _CORE)

import ke_db
from srv.constants import ARGS_DIR, LOG_DIR

now_text = ke_db.now_text


def _log_brief(value, limit=60):
    """入参/结果摘要（日志用；不追求无损）。"""
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    if isinstance(value, str):
        return value if len(value) <= limit else value[:limit] + "…"
    if isinstance(value, list):
        return [_log_brief(v, 24) for v in value[:4]] \
            + (["…共%d项" % len(value)] if len(value) > 4 else [])
    if isinstance(value, dict):
        return {k: _log_brief(v, 40) for k, v in list(value.items())[:6]}
    return str(value)[:limit]


def _log_line(text: str) -> None:
    """主日志追加一行（`logs/mcp_calls_YYYYMMDD.log`）。"""
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    path = LOG_DIR / ("mcp_calls_%s.log" % datetime.now().strftime("%Y%m%d"))
    with path.open("a", encoding="utf-8") as fh:
        fh.write(text + "\n")


def _dump_args(name: str, args: dict) -> str:
    """把**完整入参**落盘 `logs/args/<时间>-<工具>.json`（P1，2026-10-09）。

    为什么：主日志里 args 一直按 `[:300]` 截断 → 超时/异常时**无法还原当时到底传了什么**
    （用户实测：一次超时的 `knowledge_save` 连 nodes/edges 都看不全，无法重放/续作）。
    这里落一份完整 JSON，主日志只留摘要 + 文件名。
    """
    try:
        ARGS_DIR.mkdir(parents=True, exist_ok=True)
        fname = "%s-%s.json" % (datetime.now().strftime("%Y%m%d-%H%M%S-%f"),
                                re.sub(r"[^\w.-]", "_", name or "tool"))
        (ARGS_DIR / fname).write_text(json.dumps(args, ensure_ascii=False, indent=2),
                                      encoding="utf-8")
        return "args/" + fname
    except Exception:  # noqa: BLE001
        return ""


def _log_tool_begin(name: str, args: dict) -> str:
    """**两段写日志的"开始"段**（P1）：进入工具**之前**就落一行，返回 args 文件名。

    为什么必须两段：旧实现只在 `call_tool` **返回后**写日志 → 被客户端超时切断的调用
    **一行记录都没有**（这正是内网 `knowledge_save` 超 2 分钟时的观测盲区：
    连"调没调、传了什么、卡在哪"都无法判断）。现在超时也能看到 `BEGIN` 行 + 完整入参文件。
    """
    args_file = _dump_args(name, args)
    try:
        _log_line("%s\t%-26s\t  BEGIN\targs_file=%s\targs=%s"
                  % (now_text(), name, args_file or "(未落盘)",
                     json.dumps(_log_brief(args), ensure_ascii=False)[:300]))
    except Exception:  # noqa: BLE001  日志失败绝不影响工具执行
        pass
    return args_file


def _log_tool_call(name: str, args: dict, result, ms: float, io: dict | None = None,
                   args_file: str = "", phase: str = "DONE") -> None:
    """**两段写日志的"结束"段**：时间 / 工具 / 耗时 / **IO 统计** / 结果摘要。

    `io` = `ke_stats.snapshot()` —— **PG / Neo4j 各自的往返次数与时长**。
    从此"这 2 分钟到底花在 PG 还是 Neo4j、打了多少次往返"可以直接从日志读出来，
    不必再靠推算（实测基线：1 次 psql ≈ 272 ms、1 次 Neo4j ≈ 74 ms）。
    """
    try:
        summary = result if isinstance(result, dict) else {}
        keys = ("applied", "created", "merged", "violations", "unmatched", "retract",
                "retract_planned", "crud_matrix", "report_page", "page_versions", "count",
                "catalog", "id", "name", "error", "how_to_use", "source", "note",
                "images_found", "images", "by_document",
                # P0/P0-b：落库性能与写图结果（便于内网自证"边有没有真的写进图"）
                "graph_edges", "edge_errors", "folders_synced", "maintenance", "resume_hint",
                "merge", "engine", "kernel")
        picked = {}
        for key in keys:
            if key in summary:
                value = summary[key]
                if isinstance(value, list):
                    value = "len=%d %s" % (len(value),
                                           json.dumps(_log_brief(value), ensure_ascii=False)[:120])
                elif isinstance(value, dict) and len(json.dumps(value, ensure_ascii=False)) > 160:
                    value = json.dumps(_log_brief(value), ensure_ascii=False)[:160] + "…"
                picked[key] = value
        io_txt = "-"
        if isinstance(io, dict):
            d = io.get("detail") or {}
            parts = []
            for kind in ("pg", "neo4j"):
                cell = d.get(kind) or {}
                parts.append("%s:%s次/%.0fms%s" % (kind, cell.get("n", 0), cell.get("ms", 0),
                                                   ("/错%s" % cell["err"]) if cell.get("err") else ""))
            io_txt = ("io=%s |合计%s次/%.0fms |墙钟%.0fms"
                      % ("，".join(parts), io.get("calls", 0), io.get("ms", 0),
                         io.get("wall_ms", 0)))
            marks = io.get("marks") or []
            if marks:
                io_txt += " |阶段:%s" % "；".join("%sms:%s" % (m[0], m[1]) for m in marks[-4:])
            # P3-0i（2026-10-10）：**按调用者归因** —— "io=neo4j:1145 次"这类总数无从定位，
            # 这里直接给出打得最多的几个函数（谁在打图/打库一眼可见）。
            # P3-0i2（2026-10-10）：**按类型分开**打印 —— 此前 pg/neo4j 混在一张表，
            # "138 次 psql 到底谁打的"读不出来；分开后 PG 侧消费点直接可见
            # （重启到新代码后实测：neo4j 1214→64 次，瓶颈已转到 pg 138 次）。
            for kind in ("pg", "neo4j"):
                tc = (d.get(kind) or {}).get("top_callers") or []
                if tc:
                    io_txt += " |%s调用者:%s" % (kind, "；".join(
                        "%s=%s次" % (k, v) for k, v in tc[:4]))
        _log_line("%s\t%-26s\t%7.0fms\t%s\t%s\targs_file=%s\tresult=%s"
                  % (now_text(), name, ms, phase, io_txt, args_file or "-",
                     json.dumps(picked, ensure_ascii=False)[:500]))
    except Exception:  # noqa: BLE001  日志失败绝不影响工具结果
        pass


# ---------------------------------------------------------------------------
# MCP：Streamable HTTP 传输（JSON-RPC 2.0）
# ---------------------------------------------------------------------------
PROTOCOL_VERSION = "2025-03-26"
SERVER_NAME = "bodhi-ontology-mcp"
SERVER_VERSION = "0.1.0"


# ---------------------------------------------------------------------------
# 目录按需全量重建（2026-10-04，用户口径）
#   `wiki_folders` 是**物化**的目录表：新页落下后若不重建，前端（懒加载 + 已加载层不重取）
#   就看不到新目录/新页（用户实测："目录更新时间早于最新页面"）。
#   判据 = `max(页.updated_at) > max(目录.updated_at)` → **全量重建**该库目录树（幂等）：
#   重算 category_path 前缀 → upsert 目录 → 把页挂到最深一级 → prune 掉不在计划里的空目录。
#   前端进入 wiki tab / 切库时先调本接口（`/bodhi/folders/refresh?kb_id=…`）再拉目录。
# ---------------------------------------------------------------------------
