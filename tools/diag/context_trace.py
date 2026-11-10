#!/usr/bin/env python3
"""只读会话上下文审计：每次工具调用后的 prompt 大小变化。

数据来源：
  1. `messages.agent_steps`：iteration / tool_calls / args / result（工具名与回执大小）
  2. WeKnora-app 日志：`context_debug` 的 actual_prompt / tool_results / tool_schemas
  3. `messages.usage`：整条 assistant 的累计 usage（只做校验，不能冒充每轮）

注意：这是运维诊断脚本，tools/diag 不进交付包。
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools" / "ke-core"))
import ke_db  # noqa: E402

# 日志行形如：
# INFO [2026-11-09 21:36:00.284] [FDlzn91C6tpB] context_debug.go:154[...] ...
_LINE = re.compile(r"^[A-Z]+ ?\[([^\]]+)\] \[([^\]]*)\]")
_REQ = re.compile(r"\[AgentQA\] Request: .*?session_id=([0-9a-fA-F-]{8,})")
_USAGE = re.compile(
    r"\[LLM Usage\].*?prompt_tokens=(\d+),\s*completion_tokens=(\d+)"
    r".*?session_id=([0-9a-fA-F-]{8,})")
_START = re.compile(r"\[Agent\]\[Round-(\d+)/\d+\] Starting:\s*(\d+) messages,"
                    r"\s*(\d+) tools, est_tokens=(\d+)")
_DRIFT = re.compile(r"\[Agent\]\[Round-(\d+)\]\[ctx\] drift:.*?"
                    r"actual_prompt=(\d+).*?completion=(\d+)")
_BREAK = re.compile(
    r"\[Agent\]\[Round-(\d+)\]\[ctx\] breakdown:.*?"
    r"msgs=(\d+) total=(\d+) text=(\d+) reasoning=(\d+) "
    r"tool_results=(\d+) tool_args=(\d+) tool_schemas=(\d+)")
_CTXPC = re.compile(r"\[Agent\]\[Round-(\d+)\]\[ctx\] predicted=.*?request=(\d+)")
# [Agent][Round-3][Tool mcp_x (2/?)] Completed in 283ms: success=true, output=6545 chars
_TOOL_DONE = re.compile(
    r"\[Agent\]\[Round-(\d+)\]\[Tool ([^\]]+?) \(\d+/\?\)\] Completed.*?"
    r"output=(\d+) chars")


def _line_meta(line: str) -> tuple[str, str]:
    m = _LINE.match(line)
    return (m.group(1), m.group(2)) if m else ("", "")


def parse_logs(text: str) -> dict:
    """把 WeKnora-app 日志解析成 request_id → rounds/usage。"""
    reqs: dict[str, dict] = {}

    def slot(rid: str, ts: str = "") -> dict:
        r = reqs.setdefault(rid, {"request_id": rid, "session": "", "start": ts,
                                  "rounds": {}, "usage": []})
        if ts and not r["start"]:
            r["start"] = ts
        return r

    for line in str(text or "").splitlines():
        ts, rid = _line_meta(line)
        if not rid:
            continue
        r = slot(rid, ts)
        m = _REQ.search(line)
        if m:
            r["session"] = m.group(1)
            if ts:
                r["start"] = ts
            continue
        m = _USAGE.search(line)
        if m:
            r["session"] = r["session"] or m.group(3)
            r["usage"].append({"prompt_tokens": int(m.group(1)),
                              "completion_tokens": int(m.group(2))})
            continue
        m = _START.search(line)
        if m:
            n = int(m.group(1))
            rd = r["rounds"].setdefault(n, {})
            rd.update({"messages": int(m.group(2)), "tools": int(m.group(3)),
                       "est_tokens": int(m.group(4))})
            continue
        m = _DRIFT.search(line)
        if m:
            n = int(m.group(1))
            rd = r["rounds"].setdefault(n, {})
            rd.update({"actual_prompt": int(m.group(2)),
                       "completion": int(m.group(3))})
            continue
        m = _BREAK.search(line)
        if m:
            n = int(m.group(1))
            rd = r["rounds"].setdefault(n, {})
            rd.update({"msgs": int(m.group(2)), "total": int(m.group(3)),
                       "text": int(m.group(4)), "reasoning": int(m.group(5)),
                       "tool_results": int(m.group(6)), "tool_args": int(m.group(7)),
                       "tool_schemas": int(m.group(8))})
            continue
        m = _TOOL_DONE.search(line)
        if m:
            n = int(m.group(1))
            rd = r["rounds"].setdefault(n, {})
            rd.setdefault("tool_outputs", []).append(
                {"name": m.group(2), "output_chars": int(m.group(3))})
            continue
        m = _CTXPC.search(line)
        if m:
            n = int(m.group(1))
            rd = r["rounds"].setdefault(n, {})
            rd.setdefault("request", int(m.group(2)))
    return reqs


def _as_int(v) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return 0


def parse_steps(raw: str) -> dict:
    """`agent_steps` JSON → {round(1-based): {tools, args_chars, result_chars}}。"""
    try:
        steps = json.loads(raw or "[]")
    except Exception:  # noqa: BLE001
        steps = []
    out: dict[int, dict] = {}
    for step in (steps or []):
        if not isinstance(step, dict):
            continue
        rnd = _as_int(step.get("iteration")) + 1
        row = out.setdefault(rnd, {"tools": [], "tool_calls": [],
                                   "args_chars": 0, "result_chars": 0})
        for tc in (step.get("tool_calls") or []):
            if not isinstance(tc, dict):
                continue
            name = str(tc.get("name") or "")
            args_chars = len(json.dumps(tc.get("args") or {}, ensure_ascii=False))
            result_chars = len(json.dumps(tc.get("result") or {}, ensure_ascii=False))
            row["tools"].append(name)
            row["tool_calls"].append({"name": name, "args_chars": args_chars,
                                      "result_chars": result_chars})
            row["args_chars"] += args_chars
            row["result_chars"] += result_chars
    return out


def load_messages(session: str) -> list[dict]:
    rows = ke_db.psql_csv(
        "SELECT id, role, "
        "       (created_at at time zone 'Asia/Shanghai')::text AS ts, "
        "       COALESCE(agent_steps::text,'[]') AS steps, "
        "       COALESCE(usage::text,'{}') AS usage, "
        "       length(COALESCE(content,'')) AS content_chars "
        "FROM messages WHERE session_id = %s AND deleted_at IS NULL "
        "ORDER BY created_at" % ke_db.sql_str(session))
    out = []
    for row in rows:
        if str(row.get("role") or "") != "assistant":
            continue
        try:
            usage = json.loads(row.get("usage") or "{}")
        except Exception:  # noqa: BLE001
            usage = {}
        out.append({"id": str(row.get("id") or ""), "ts": str(row.get("ts") or ""),
                    "steps": parse_steps(str(row.get("steps") or "[]")),
                    "usage": usage, "content_chars": _as_int(row.get("content_chars"))})
    return out


def docker_logs(container: str, since: str, until: str = "") -> str:
    cmd = ["docker", "logs", container]
    if since:
        cmd += ["--since", since]
    if until:
        cmd += ["--until", until]
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, errors="replace")
    except (FileNotFoundError, OSError):
        return ""
    if p.returncode != 0 and not (p.stdout or "").strip():
        return ""
    return (p.stdout or "") + (p.stderr or "")


def _merge_tool_chars(st: dict, lg: dict) -> list[dict]:
    """优先用日志 `output=N chars` 覆盖 agent_steps 的 JSON 长度。"""
    calls = [dict(tc) for tc in (st.get("tool_calls") or [])]
    logs = [dict(x) for x in (lg.get("tool_outputs") or [])]
    used: set[int] = set()
    for tc in calls:
        tc.setdefault("result_chars", 0)
        tc.setdefault("result_source", "agent_steps")
        for i, lo in enumerate(logs):
            if i in used or lo.get("name") != tc.get("name"):
                continue
            tc["result_chars"] = _as_int(lo.get("output_chars"))
            tc["result_source"] = "log"
            used.add(i)
            break
    return calls


def _match_request(msg: dict, reqs: list[dict]) -> dict | None:
    """按开始时间匹配 assistant 消息；时间不可比时按顺序由调用方兜底。"""
    if not reqs:
        return None
    ts = msg.get("ts") or ""
    before = [r for r in reqs if r.get("start") and r["start"] <= ts]
    return before[-1] if before else reqs[0]


def trace(session: str, logs: str = "", container: str = "WeKnora-app",
          since: str = "", until: str = "") -> dict:
    raw = logs if logs else docker_logs(container, since, until)
    reqs = parse_logs(raw)
    # 只认明确属于本会话的 request；未知 session 的 request 不参与匹配，避免串会话。
    selected = [r for r in reqs.values() if r.get("session") == session]
    selected.sort(key=lambda r: r.get("start") or "")
    messages = load_messages(session)
    out = {"session": session, "requests": [], "warnings": []}
    if not reqs:
        out["warnings"].append("no_context_debug：日志中没有 Agent Round / LLM Usage")
    elif not selected:
        out["warnings"].append("no_request_for_session：日志里没有该 session 的 AgentQA Request")
    missing = 0
    for idx, msg in enumerate(messages):
        req = _match_request(msg, selected)
        if req is None and idx < len(selected):
            req = selected[idx]
        item = {"message_id": msg["id"], "message_ts": msg["ts"],
                "content_chars": msg["content_chars"], "usage": msg["usage"],
                "request_id": (req or {}).get("request_id", ""),
                "rounds": []}
        log_rounds = (req or {}).get("rounds") or {}
        all_rounds = sorted(set(msg["steps"]) | set(log_rounds))
        prev = 0
        for rnd in all_rounds:
            st = msg["steps"].get(rnd) or {}
            lg = log_rounds.get(rnd) or {}
            actual = _as_int(lg.get("actual_prompt"))
            delta = actual - prev if prev else actual
            if actual:
                prev = actual
            tool_calls = _merge_tool_chars(st, lg)
            if not actual:
                missing += 1
            item["rounds"].append({
                "round": rnd,
                "tools": st.get("tools") or [],
                "args_chars": st.get("args_chars", 0),
                "result_chars": sum(_as_int(tc.get("result_chars")) for tc in tool_calls),
                "tool_calls": tool_calls,
                "actual_prompt": actual,
                "prompt_delta": delta,
                "completion": _as_int(lg.get("completion")),
                "tool_results_tokens": _as_int(lg.get("tool_results")),
                "tool_args_tokens": _as_int(lg.get("tool_args")),
                "tool_schemas_tokens": _as_int(lg.get("tool_schemas")),
                "request_tokens": _as_int(lg.get("request") or lg.get("total")),
                "trace_missing": not bool(actual),
            })
        out["requests"].append(item)
    if missing:
        out["warnings"].append(
            "trace_missing=%d：这些轮没有日志 actual_prompt，只显示工具字符数" % missing)
    if not raw.strip():
        out["warnings"].append("no_logs：只输出 agent_steps 的工具字符数，无每轮 prompt token")
    if not messages:
        out["warnings"].append("no_assistant_messages")
    return out


def _md_row(it: dict, r: dict) -> str:
    tools = ", ".join(r.get("tools") or []) or "—"
    return "| %s | %s | %s | %s | %s | %s | %s | %s | %s |" % (
        r.get("round"), tools, r.get("args_chars"), r.get("result_chars"),
        r.get("actual_prompt") or "—", r.get("prompt_delta") or "—",
        r.get("completion") or "—", r.get("tool_results_tokens") or "—",
        r.get("request_tokens") or "—")


def _md_tool_rows(rounds: list[dict]) -> list[str]:
    rows = []
    for r in rounds:
        for tc in (r.get("tool_calls") or []):
            rows.append("| %s | %s | %s | %s |" % (
                r.get("round"), tc.get("name"), tc.get("args_chars"),
                tc.get("result_chars")))
    return rows


def to_markdown(data: dict) -> str:
    lines = ["# Context Trace", "",
             "session: `%s`" % data.get("session", ""), ""]
    for it in data.get("requests") or []:
        lines += ["## message `%s`" % it.get("message_id", ""), "",
                  "timestamp: `%s`  request: `%s`" % (it.get("message_ts", ""),
                                                      it.get("request_id", "")),
                  "", "| 轮次 | 工具 | 参数 chars | 回执 chars | 本轮 prompt | 较上轮增量 | completion | tool_results | request |",
                  "|---:|---|---:|---:|---:|---:|---:|---:|---:|"]
        rounds = it.get("rounds") or []
        lines += [_md_row(it, r) for r in rounds]
        lines += ["", "> 本轮 prompt 已包含上一轮工具回执；因此本行的“较上轮增量”对应上一轮工具调用后的变化。"]
        tool_rows = _md_tool_rows(rounds)
        if tool_rows:
            lines += ["", "| 轮次 | 工具 | 参数 chars | 回执 chars |",
                      "|---:|---|---:|---:|"] + tool_rows
        lines += ["", "cumulative usage: `%s`" % json.dumps(
            it.get("usage") or {}, ensure_ascii=False), ""]
    if data.get("warnings"):
        lines += ["## warnings", ""] + ["- %s" % w for w in data["warnings"]]
    return "\n".join(lines).rstrip() + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description="只读会话上下文审计")
    ap.add_argument("--session", required=True)
    ap.add_argument("--log-file", default="", help="保存好的 WeKnora-app 日志；省略则 docker logs")
    ap.add_argument("--container", default="WeKnora-app")
    ap.add_argument("--since", default="", help="docker logs --since")
    ap.add_argument("--until", default="", help="docker logs --until")
    ap.add_argument("--format", choices=("md", "json"), default="md")
    args = ap.parse_args()
    logs = ""
    if args.log_file:
        logs = Path(args.log_file).read_text(encoding="utf-8", errors="replace")
    data = trace(args.session, logs=logs, container=args.container,
                 since=args.since, until=args.until)
    print(to_markdown(data) if args.format == "md" else
          json.dumps(data, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
