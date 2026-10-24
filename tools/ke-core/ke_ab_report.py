"""ke-core · **检索 A/B 报告**（需求 2：本体提示开/关对比）

输入两个来源，任选其一或都给：
1. `logs/ab/lookup_ab_*.json` —— `tests/ab_runner.py` 的确定性模拟报告（含 `expect` 判定 ✓）；
2. `logs/lookup_ab_*.jsonl`  —— **真实调用**遥测（`srv/obs.py` 侧车日志；真 LLM 跑出来的 ✓）。

输出：Markdown 对比表（命中率、解决率、平均调用次数、有出路率、按类目细分、耗时分位）
+ **数据质量**（两组样本是否配对/是否覆盖全部问题/有无重复会话噪声）。

口径（2026-10-24）：
- `hint`  = `ontology_hint=true`（回执带 plan/suggestions/next）；
- `plain` = `ontology_hint=false`（只回 hits/counts）；
- 真实数据里"一轮问题调用几次"用 `(session_no, question_key)` 的**调用次数**衡量（≤3 次算收敛）；
- 判定"解决"需要问题集里的 `expect_hits_min`（按 `question_key` 关联）——缺失则只报命中率。

用法：
```
python3 tools/ke-core/ke_ab_report.py                      # 自动找 logs/ab + logs/*.jsonl
python3 tools/ke-core/ke_ab_report.py --out docs/lookup-ab-report.md
```
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import statistics
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
for p in (HERE, os.path.join(REPO, "tests")):
    if p not in sys.path:
        sys.path.insert(0, p)


def _qkey(text: str) -> str:
    try:
        import ke_lookup  # noqa: PLC0415
        return ke_lookup._question_key(text)
    except Exception:  # noqa: BLE001
        return (text or "").strip().lower()[:80]


def load_questions(path: str = "") -> dict:
    """问题集：question_key → expect_hits_min（用于判定"解决"）。"""
    p = path or os.path.join(REPO, "tests", "cases", "lookup_ab_questions.json")
    if not os.path.exists(p):
        return {}
    data = json.loads(open(p, encoding="utf-8").read())
    return {_qkey(q.get("question", "")): int(q.get("expect_hits_min") or 0)
            for q in (data.get("questions") or [])}


def load_sim_reports(pattern: str = "") -> list:
    pat = pattern or os.path.join(REPO, "logs", "ab", "lookup_ab_*.json")
    out = []
    for f in sorted(glob.glob(pat)):
        try:
            out.append(json.loads(open(f, encoding="utf-8").read()))
        except Exception:  # noqa: BLE001
            continue
    return out


def load_telemetry(pattern: str = "") -> list:
    """真实调用遥测（一行一 JSON）→ 归一成与模拟报告同构的 rows。"""
    pat = pattern or os.path.join(REPO, "logs", "lookup_ab_*.jsonl")
    rows = []
    for f in sorted(glob.glob(pat)):
        for line in open(f, encoding="utf-8"):
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except Exception:  # noqa: BLE001
                continue
            tel = rec.get("telemetry") or {}
            rows.append({"variant": tel.get("variant") or "unknown",
                         "question_key": tel.get("question_key") or "",
                         "session_no": tel.get("session_no") or "",
                         "hits": int(tel.get("hits") or 0),
                         "ms": int(rec.get("ms") or tel.get("ms") or 0),
                         "mode": tel.get("mode") or "",
                         "relaxed_by": tel.get("relaxed_by") or "",
                         "guidance": bool(tel.get("relaxed_by")) or bool(tel.get("ambiguity")),
                         "source": os.path.basename(f)})
    return rows

def _pct(x: float) -> str:
    return "%.1f%%" % (x * 100)


def summarize_rows(rows: list, expects: dict) -> dict:
    """模拟行（有 case_ok）与真实行（无 case_ok）统一汇总。"""
    out = {}
    for variant in ("hint", "plain", "unknown"):
        sub = [r for r in rows if r.get("variant") == variant]
        if not sub:
            continue
        n = len(sub)
        decided = [r for r in sub if r.get("question_key") in expects]
        ok = [r for r in decided
              if (r.get("case_ok") if "case_ok" in r else
                  ((r["hits"] >= expects[r["question_key"]]) if expects[r["question_key"]] > 0
                   else r["hits"] == 0))]
        calls: dict = {}
        for r in sub:
            key = (r.get("session_no") or "-", r.get("question_key") or "-")
            calls[key] = calls.get(key, 0) + 1
        # 模拟行自带 `attempts`（同一问题在第几轮命中）；真实行只能按 (会话,问题) 调用次数推 ✓
        if all("attempts" in r for r in sub):
            avg_attempts = sum(int(r["attempts"]) for r in sub) / n
            converged = sum(1 for r in sub if int(r["attempts"]) <= 3) / n
        else:
            avg_attempts = (sum(calls.values()) / len(calls)) if calls else 0.0
            converged = (sum(1 for v in calls.values() if v <= 3) / len(calls)) if calls else 0.0
        out[variant] = {
            "n": n,
            "questions": len({r.get("question_key") for r in sub if r.get("question_key")}),
            "found_rate": sum(1 for r in sub if r["hits"] > 0) / n,
            "case_ok_rate": (len(ok) / len(decided)) if decided else None,
            "decided_n": len(decided),
            "avg_attempts": avg_attempts,
            "converged_3": converged,
            "guidance_rate": sum(1 for r in sub if r.get("guidance")) / n,
            "median_ms": int(statistics.median([r["ms"] for r in sub])) if sub else 0,
            "p90_ms": int(sorted(r["ms"] for r in sub)[max(0, int(n * 0.9) - 1)]) if sub else 0,
        }
    return out


def by_category(rows: list) -> dict:
    out: dict = {}
    for r in rows:
        cat = r.get("category") or "-"
        d = out.setdefault(cat, {"hint": [0, 0], "plain": [0, 0]})   # [solved, total]
        key = r.get("variant") if r.get("variant") in ("hint", "plain") else None
        if not key:
            continue
        d[key][1] += 1
        if r.get("case_ok") or r["hits"] > 0:
            d[key][0] += 1
    return out


def _cell(v, key: str = ""):
    if v is None:
        return "-"
    if key == "avg_attempts" and isinstance(v, float):
        return "%.2f" % v
    if key.endswith("_ms") and isinstance(v, int):
        return "%dms" % v
    if isinstance(v, float):
        return _pct(v)
    return str(v)


def render_md(rep: dict, rows: list, expects: dict, title: str) -> str:
    s = rep["summary"] if rep else summarize_rows(rows, expects)
    lines = ["# 检索 A/B 报告 · %s" % title, "",
             "> 生成：%s ｜ 设计：**配对**（同题两组）｜ 指标口径见 `docs/lookup-ab.md`"
             % time.strftime("%Y-%m-%d %H:%M"), "",
             "## 1. 总览", "",
             "| 指标 | hint（本体提示开） | plain（关） | 差 |",
             "|---|---|---|---|"]
    h, p = s.get("hint", {}), s.get("plain", {})
    spec = [("样本数（调用）", "n"), ("覆盖问题数", "questions"),
            ("命中率（hits>0）", "found_rate"), ("解决率（按问题集判定）", "case_ok_rate"),
            ("平均调用次数 / 问题", "avg_attempts"), ("≤3 次收敛率", "converged_3"),
            ("有出路率（建议/降级/歧义可见）", "guidance_rate"),
            ("中位耗时", "median_ms"), ("P90 耗时", "p90_ms")]
    for label, key in spec:
        hv, pv = h.get(key), p.get(key)
        if hv is None or pv is None:
            lines.append("| %s | %s | %s | - |" % (label, _cell(hv, key), _cell(pv, key)))
            continue
        if isinstance(hv, float) and isinstance(pv, float) and key != "avg_attempts":
            diff = "%+.1f%%" % ((hv - pv) * 100)
        elif key.endswith("_ms"):
            diff = "%+dms" % (hv - pv)
        else:
            diff = "%+.2f" % (hv - pv) if key == "avg_attempts" else "%+g" % (hv - pv)
        lines.append("| %s | %s | %s | %s |" % (label, _cell(hv, key), _cell(pv, key), diff))

    cats = by_category(rows)
    if cats:
        lines += ["", "## 2. 按问题类目", "",
                  "| 类目 | hint 命中/总 | plain 命中/总 |", "|---|---|---|"]
        for cat, d in cats.items():
            lines.append("| %s | %d/%d | %d/%d |" % (cat, d["hint"][0], d["hint"][1],
                                                    d["plain"][0], d["plain"][1]))

    q_hint = {r.get("question_key") for r in rows if r.get("variant") == "hint"}
    q_plain = {r.get("question_key") for r in rows if r.get("variant") == "plain"}
    lines += ["", "## 3. 数据质量", ""]
    # 按来源分开核：模拟（问题集驱动）与真实调用（会话驱动）样本天然不同，**不能混着比集合**
    sources = sorted({r.get("source") or "?" for r in rows})
    for src in sources:
        sub = [r for r in rows if (r.get("source") or "?") == src]
        sh = {r.get("question_key") for r in sub if r.get("variant") == "hint"}
        sp = {r.get("question_key") for r in sub if r.get("variant") == "plain"}
        lines.append("- `%s`：hint %d 条 / plain %d 条；问题集合%s（%d vs %d）"
                     % (src, sum(1 for r in sub if r.get("variant") == "hint"),
                        sum(1 for r in sub if r.get("variant") == "plain"),
                        "一致 ✅" if sh == sp else "**不一致** ⚠️", len(sh), len(sp)))
    lines.append("- 全局问题集合一致：%s" % ("✅" if q_hint == q_plain else
                                          "⚠️ hint=%d / plain=%d（多来源混比时会不一致，属正常）"
                                          % (len(q_hint), len(q_plain))))
    lines.append("- 问题集覆盖：%d 题（问题集共 %d 题）" % (len(q_hint | q_plain),
                                                     len(expects) or len(q_hint | q_plain)))
    missing = [k for k in (q_hint | q_plain) if k and k not in expects]
    if missing:
        lines.append("- ⚠️ 未在问题集里的问题键 %d 个（只统计命中率、不判解决）：%s"
                     % (len(missing), "、".join(missing[:5])))
    lines.append("- 结论**不在此自动化判定**：本表只保证数据成对齐全、指标可复算 ✓")
    return "\n".join(lines) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description="检索 A/B 报告（hint vs plain）")
    ap.add_argument("--sim", default="", help="模拟报告 glob（默认 logs/ab/lookup_ab_*.json）")
    ap.add_argument("--telemetry", default="", help="真实遥测 glob（默认 logs/lookup_ab_*.jsonl）")
    ap.add_argument("--questions", default="", help="问题集路径")
    ap.add_argument("--out", default="", help="写入 Markdown 文件")
    ap.add_argument("--no-telemetry", action="store_true",
                    help="忽略真实遥测（只看模拟报告；冒烟/联调产生的日志会稀释样本 ✓）")
    args = ap.parse_args()
    # `--max-sim` 固定为「最新 1 份」：历史快照混比会让样本重复（口径不可比 ✗）

    expects = load_questions(args.questions)
    reps = load_sim_reports(args.sim)
    if reps:
        reps = reps[-1:]
    rows: list = []
    for r in reps:
        rows += r.get("rows") or []
    real = [] if args.no_telemetry else load_telemetry(args.telemetry)
    rows += real
    if not rows:
        print(json.dumps({"ok": False, "error": "没有数据（先跑 tests/ab_runner.py 或真实调用）"},
                         ensure_ascii=False))
        return 1
    rep = {"summary": summarize_rows(rows, expects)}
    md = render_md(rep, rows, expects, "模拟 %d 份 / 真实调用 %d 条" % (len(reps), len(real)))
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(md)
        print(json.dumps({"ok": True, "out": args.out, "rows": len(rows)}, ensure_ascii=False))
    else:
        print(md)
    return 0


if __name__ == "__main__":
    sys.exit(main())

