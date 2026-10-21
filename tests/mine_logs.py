"""P0.5-3 · 日志挖掘：`logs/mcp_calls_*.log` + `logs/args/*.json` → **可回放用例**（无业务语义 ✓）

口径（全部按**结构与类型**，不认领域名词 ✗）：
- **配对**：`BEGIN`/`DONE` 行都带 `args_file=` ⇒ 一次调用一条记录 ✓（无 DONE 记 `timeout`）
- **去重**：同工具 + 同参数（归一化 JSON）只留**首例**，附 `hits` 计数
- **切会话**：args 里有 `session_no` 用它；否则按 **90s 空闲**切段
- **正/反标注**（只看回执**字段**）：
    `ok`    ：`ok/applied` 为真，且无 `need_*`、无 `error`
    `guard` ：含 `need_session` / `need_kb_selection` / `kb_unresolved` / `retired`
    `error` ：含 `error`
    `dry`   ：`ok/applied` 为假
- **危险工具**：只留 `dry_run`/`preview` 形态（`apply` 只在 `_test_*` 库回放 ✓）

输出：`tests/cases/mined/*.json`、`COVERAGE.md`、`tests/golden/mined_index.json`、`tests/sessions/*.json`

用法：`python3 tests/mine_logs.py [--dry-run]`
"""
from __future__ import annotations

import glob
import io
import json
import os
import re
import sys
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
LOGS = os.path.join(REPO, "logs")
OUT_CASES = os.path.join(HERE, "cases", "mined")
OUT_SESS = os.path.join(HERE, "sessions")
INDEX = os.path.join(HERE, "golden", "mined_index.json")

TS_RE = re.compile(r"^(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d [+-]\d{4})\s+(\S+)\s")
ARGSFILE_RE = re.compile(r"args_file=(\S+)")
RESULT_RE = re.compile(r"\bresult=(\{.*\})\s*$")
GAP_SECONDS = 90
DANGEROUS = {"audit_purge", "kb_import", "retag_apply", "context_concept_apply",
             "context_authority_apply", "import_apply"}


def _parse(path: str) -> list[dict]:
    """一个日志文件 → 调用记录（BEGIN/DONE 已配对 ✓）。"""
    calls: dict[str, dict] = {}
    order: list[str] = []
    for line in io.open(path, encoding="utf-8", errors="ignore"):
        m = TS_RE.match(line)
        if not m:
            continue
        ts, tool = m.group(1), m.group(2)
        f = ARGSFILE_RE.search(line)
        if not f:
            continue
        key = f.group(1)
        rec = calls.get(key)
        if rec is None:
            rec = {"tool": tool, "ts": ts, "args_file": key, "ms": None, "done": False,
                   "result": {}}
            calls[key] = rec
            order.append(key)
        ms = re.search(r"(\d+)ms\s+DONE", line)
        if ms:
            rec["ms"] = int(ms.group(1))
            rec["done"] = True
            r = RESULT_RE.search(line)
            if r:
                try:
                    rec["result"] = json.loads(r.group(1))
                except Exception:  # noqa: BLE001  回执可能被截断 ⇒ 只影响标注精度 ✓
                    rec["result"] = {}
    return [calls[k] for k in order]


def _load_args(rel: str) -> dict:
    try:
        return json.load(io.open(os.path.join(LOGS, rel), encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}


PAYLOAD_KEYS = ("count", "hits", "items", "rules", "pages", "nodes", "edges", "tools",
               "catalog", "images_found", "by_document", "chunks", "rows", "total")


def _shape(rec: dict) -> str:
    """回执形态（**只看字段**，不看领域词 ✓）。

    注意：日志里的 `result=` 是**摘要**（不是完整回执 ✗），所以"成功"要看**载荷字段**
    （`count/hits/items/...`）是否存在；日志只写 `note` 的记为 `ok_brief` ✓。
    """
    r = rec.get("result") or {}
    if not rec.get("done"):
        return "timeout"
    if any(k in r for k in ("need_session", "need_kb_selection", "kb_unresolved", "retired")):
        return "guard"
    if r.get("error"):
        return "error"
    if r.get("ok") is True or r.get("applied") is True:
        return "ok"
    if r.get("ok") is False or r.get("applied") is False:
        return "dry"
    if any(k in r for k in PAYLOAD_KEYS):
        return "ok"
    if r:
        return "ok_brief"
    return "silent"


def _norm(obj):
    """参数归一化（去重用）：dict 按 key 排序，字符串压空白。"""
    if isinstance(obj, dict):
        return {k: _norm(obj[k]) for k in sorted(obj, key=str)}
    if isinstance(obj, list):
        return [_norm(x) for x in obj]
    if isinstance(obj, str):
        return re.sub(r"\s+", " ", obj).strip()
    return obj


def mine(dry_run: bool = False) -> dict:
    files = sorted(glob.glob(os.path.join(LOGS, "mcp_calls_*.log")))
    all_calls: list[dict] = []
    for f in files:
        all_calls.extend(_parse(f))
    cases: dict[tuple, dict] = {}
    sessions: dict[str, list] = {}
    state: dict = {}
    stats = {"by_tool": {}, "by_shape": {}, "calls": len(all_calls)}
    for i, rec in enumerate(all_calls):
        args = _load_args(rec["args_file"])
        rec["args"] = args
        rec["shape"] = _shape(rec)
        key = (rec["tool"], json.dumps(_norm(args), ensure_ascii=False, sort_keys=True))
        t, s = rec["tool"], rec["shape"]
        stats["by_tool"].setdefault(t, {}).setdefault(s, 0)
        stats["by_tool"][t][s] += 1
        stats["by_shape"][s] = stats["by_shape"].get(s, 0) + 1
        sid = _sid_of(args, rec["ts"], state, i)
        sessions.setdefault(sid, []).append(
            {"tool": t, "ts": rec["ts"], "shape": s,
             "target": args.get("slug") or args.get("kb_id") or args.get("query") or ""})
        if key in cases:
            cases[key]["hits"] += 1
            continue
        cases[key] = {"tool": t, "args": args, "shape": s, "ms": rec["ms"], "ts": rec["ts"],
                      "source": rec["args_file"], "hits": 1,
                      "expect_keys": sorted((rec.get("result") or {}).keys())[:12]}
    index = {"totals": {"logs": len(files), "calls": len(all_calls),
                        "unique_cases": len(cases), "sessions": len(sessions)},
             "by_shape": stats["by_shape"], "by_tool": stats["by_tool"], "cases": []}
    if dry_run:
        return index
    os.makedirs(OUT_CASES, exist_ok=True)
    os.makedirs(OUT_SESS, exist_ok=True)
    for old in glob.glob(os.path.join(OUT_CASES, "*.json")) + glob.glob(os.path.join(OUT_SESS, "*.json")):
        os.remove(old)
    rows = []
    ordered = sorted(cases.items(), key=lambda kv: (kv[1]["tool"], kv[1]["ts"]))
    for n, (_key, c) in enumerate(ordered, 1):
        risky = c["tool"] in DANGEROUS and not (
            c["args"].get("dry_run") is True
            or str(c["args"].get("mode") or "") in ("preview", "dry_run"))
        name = "%04d-%s.json" % (n, re.sub(r"[^A-Za-z0-9_.-]+", "_", c["tool"]))
        payload = {"tool": c["tool"], "args": c["args"],
                   "expect": {"shape": c["shape"], "keys": c["expect_keys"]},
                   "meta": {"ms": c["ms"], "ts": c["ts"], "hits": c["hits"],
                            "source": c["source"], "replayable": not risky,
                            "why_not": "危险工具的 apply 形态（只在 _test_* 库回放）" if risky else ""}}
        io.open(os.path.join(OUT_CASES, name), "w", encoding="utf-8").write(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
        index["cases"].append({"file": name, "tool": c["tool"], "shape": c["shape"],
                               "replayable": not risky, "hits": c["hits"]})
        rows.append((c["tool"], c["shape"], "否" if risky else "是", c["hits"], c["ms"]))
    for sid, seq in sessions.items():
        if len(seq) < 2:          # 单次调用不成"会话"（噪声 ✓）
            continue
        io.open(os.path.join(OUT_SESS, "%s.json" % sid), "w", encoding="utf-8").write(
            json.dumps({"session": sid, "calls": seq}, ensure_ascii=False, indent=2) + "\n")
    io.open(INDEX, "w", encoding="utf-8").write(json.dumps(index, ensure_ascii=False, indent=2) + "\n")
    lines = ["# 挖掘用例覆盖矩阵（自动生成 ✓ 勿手改；改 `tests/mine_logs.py` 后重跑）", "",
             "缺口 = 某工具缺少某种形态（`guard`/`error`/`dry`/`ok`）⇒ 按 TODO 造例 ✓", "",
             "| 工具 | 形态 | 可回放 | 命中 | 耗时ms |", "|---|---|---|---|---|"]
    for r in sorted(rows):
        lines.append("| `%s` | %s | %s | %s | %s |" % r)
    io.open(os.path.join(OUT_CASES, "COVERAGE.md"), "w", encoding="utf-8").write("\n".join(lines) + "\n")
    return index


def _sid_of(args: dict, ts: str, state: dict, seq: int) -> str:
    """会话切分：有 `session_no` 就用它；否则 **90s 空闲**切段（长任务口径 ✓）。"""
    sno = str(args.get("session_no") or args.get("session_id") or "").strip()
    if sno:
        return "s-" + sno[:8]
    try:
        t = datetime.strptime(ts, "%Y-%m-%d %H:%M:%S %z")
    except Exception:  # noqa: BLE001
        return "win-%d" % seq
    prev = state.get("t")
    state["t"] = t
    if prev is None or (t - prev).total_seconds() > GAP_SECONDS:
        state["n"] = int(state.get("n") or 0) + 1
    return "win-%02d" % state["n"]


if __name__ == "__main__":
    res = mine("--dry-run" in sys.argv)
    print(json.dumps({"totals": res["totals"], "by_shape": res["by_shape"]},
                     ensure_ascii=False, indent=2))
