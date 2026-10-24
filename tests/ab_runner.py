"""A/B 运行器（需求 2）：同一问题在 hint / plain 两种策略下各跑一遍，产出可比数据。

为什么单独一个模块：`tests/test_lookup_ab.py` 要用它，人工测试时也可以直接：
```
python3 tests/ab_runner.py --kb 技术方案评审领域模型        # 默认 paired，写 logs/ab/*.json
```

口径（2026-10-24）：
- `hint`  = `ontology_hint=true`  → 回执带 `plan/suggestions/next`（**提示用本体规划查询**）
- `plain` = `ontology_hint=false` → 只回 `hits/counts`（对照组：没有本体提示）
- **配对**：同一问题两组都跑（天然 50/50），逐题可比；
- "解决"判定：`expect_hits_min`>0 ⇒ `hits >= 该值`；`=0` ⇒ `hits == 0`（负样例要判对"没有"）；
- "有出路"：hint 组在 0 命中时能否给出 `suggestions/next`（这是本体提示的直接价值）。
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import sys
import time

HERE = pathlib.Path(__file__).resolve().parent
REPO = HERE.parent
for p in (str(HERE), str(REPO / "tools" / "ke-core")):
    if p not in sys.path:
        sys.path.insert(0, p)


def load_questions() -> list:
    data = json.loads((HERE / "cases" / "lookup_ab_questions.json").read_text(encoding="utf-8"))
    return data.get("questions") or []


def _hits(resp: dict) -> int:
    tel = resp.get("telemetry") or {}
    if "hits" in tel:
        return int(tel.get("hits") or 0)
    counts = resp.get("counts") or {}
    for key in ("hits", "paths"):
        if key in counts:
            return int(counts.get(key) or 0)
    # route 模式没有 counts:hits/paths ⇒ 用 recipe 步数衡量"可用的出路"
    return (len(resp.get("hits") or []) or len(resp.get("paths") or [])
            or len(resp.get("recipe") or []))


def _guidance(resp: dict) -> bool:
    return bool(resp.get("suggestions") or resp.get("next"))


def _call(mcp, kb: str, args: dict, hint: bool, session_no: str, question: str) -> dict:
    payload = dict(args)
    payload.pop("kb_id", None)
    payload.update({"kb_id": kb, "ontology_hint": bool(hint),
                    "session_no": session_no, "question": question})
    return mcp.call("knowledge_lookup", payload)


def simulate(mcp, kb: str, q: dict, hint: bool, session_no: str) -> dict:
    """模拟"智能体的检索行为"（**确定性、不调 LLM**）。

    - hint 组：0 命中时**读取回执建议**再查一次（最多 2 次调用）；
    - plain 组：没有建议 ⇒ 只能拿到原始结果（1 次调用）。
    这正是两种策略的差别所在（"有没有东西可照着试"）。
    """
    t0 = time.time()
    first = _call(mcp, kb, q.get("args") or {}, hint, session_no, q.get("question", ""))
    attempts = 1
    resp = first
    hits = _hits(first)
    used_suggestion = False
    if hint and hits == 0:
        for sug in (first.get("suggestions") or []):
            if str(sug.get("tool")) != "knowledge_lookup" or not isinstance(sug.get("args"), dict):
                continue
            second = _call(mcp, kb, sug["args"], True, session_no, q.get("question", ""))
            attempts += 1
            resp = second
            hits = _hits(second)
            used_suggestion = True
            break
    expect_min = int(q.get("expect_hits_min") or 0)
    case_ok = (hits >= expect_min) if expect_min > 0 else (hits == 0)
    tel = first.get("telemetry") or {}
    return {"id": q.get("id"), "category": q.get("category"),
            "question": q.get("question"), "variant": "hint" if hint else "plain",
            "hits": hits, "attempts": attempts, "case_ok": bool(case_ok),
            "guidance": _guidance(first), "used_suggestion": used_suggestion,
            "ms": int((time.time() - t0) * 1000),
            "question_key": tel.get("question_key", ""),
            "relaxed_by": tel.get("relaxed_by", ""),
            # 2026-10-24（A/B 四维）：模拟报告也要带**程序版本 + 问题种类**，才能和真实遥测同构 ✓
            "lookup_version": tel.get("lookup_version", ""),
            "q_category": tel.get("q_category", ""),
            # 2026-10-25（口径 v2）：归组/形态 + 真实 IO（模拟也走真检索 ⇒ 同样有 IO ✓）
            "qid": tel.get("qid", ""),
            "question_core": tel.get("question_core", ""),
            "q_shape": tel.get("q_shape", ""),
            "kw_terms": tel.get("kw_terms", 0),
            "level": tel.get("level", ""),
            "queries": (tel.get("retrieval") or {}).get("queries", 0),
            "pg_queries": (tel.get("retrieval") or {}).get("pg", 0),
            "neo4j_queries": (tel.get("retrieval") or {}).get("neo4j", 0),
            "empty_queries": (tel.get("retrieval") or {}).get("empty_queries", 0),
            "hit_queries": (tel.get("retrieval") or {}).get("hit_queries", 0),
            "degraded": (tel.get("retrieval") or {}).get("degraded", False),
            "io_ms": (tel.get("retrieval") or {}).get("io_ms", 0),
            "has_io": bool(tel.get("io") or tel.get("retrieval")),
            "first_step": tel.get("first_step"),
            "turn_first_retrieval": tel.get("turn_first_retrieval", ""),
            "at": time.strftime("%Y-%m-%d %H:%M:%S")}


def summarize(rows: list) -> dict:
    out: dict = {}
    for variant in ("hint", "plain"):
        sub = [r for r in rows if r.get("variant") == variant]
        if not sub:
            out[variant] = {"n": 0}
            continue
        n = len(sub)
        out[variant] = {
            "n": n,
            "found_rate": round(sum(1 for r in sub if r["hits"] > 0) / n, 3),
            "case_ok_rate": round(sum(1 for r in sub if r["case_ok"]) / n, 3),
            "avg_attempts": round(sum(r["attempts"] for r in sub) / n, 2),
            "guidance_rate": round(sum(1 for r in sub if r["guidance"]) / n, 3),
            "relaxed_rate": round(sum(1 for r in sub if r["relaxed_by"]) / n, 3),
            "avg_ms": int(sum(r["ms"] for r in sub) / n),
        }
    return out


def run_paired(mcp, kb: str, session_no: str, questions: list | None = None) -> dict:
    qs = questions if questions is not None else load_questions()
    rows = []
    for q in qs:
        for hint in (True, False):          # 配对：同一问题两组都跑
            row = simulate(mcp, kb, q, hint, session_no)
            row["source"] = "sim"
            rows.append(row)
    rep = {"at": time.strftime("%Y-%m-%d %H:%M:%S"), "kb": kb, "questions": len(qs),
           "design": "paired", "summary": summarize(rows), "rows": rows,
           # 版本指纹（取自真实回执 telemetry ⇒ 模拟与真实同构，跨版本不可混比 ✓）
           "lookup_version": next((r.get("lookup_version") for r in rows if r.get("lookup_version")), ""),
           "cohort": "sim（逐题一问一调，天然满足首步口径 ✓）"}
    outdir = REPO / "logs" / "ab"
    outdir.mkdir(parents=True, exist_ok=True)
    path = outdir / ("lookup_ab_%s.json" % time.strftime("%Y%m%d-%H%M%S"))
    path.write_text(json.dumps(rep, ensure_ascii=False, indent=1), encoding="utf-8")
    rep["report_path"] = str(path)
    return rep


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--kb", default="技术方案评审领域模型")
    ap.add_argument("--session-no", default="")
    args = ap.parse_args()
    from mcp_client import MCP  # noqa: PLC0415
    import ke_db  # noqa: PLC0415
    sno = args.session_no or ke_db.psql_csv(
        "SELECT id FROM sessions WHERE deleted_at IS NULL ORDER BY created_at DESC LIMIT 1")[0]["id"]
    kb = ke_db.resolve_kb_id(args.kb)[0]
    rep = run_paired(MCP(), kb, sno)
    print(json.dumps(rep["summary"], ensure_ascii=False, indent=1))
    print("报告：", rep.get("report_path"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
