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
                         "question": tel.get("question") or "",
                         "hits": int(tel.get("hits") or 0),
                         "ms": int(rec.get("ms") or tel.get("ms") or 0),
                         "mode": tel.get("mode") or "",
                         "relaxed_by": tel.get("relaxed_by") or "",
                         "guidance": bool(tel.get("relaxed_by")) or bool(tel.get("ambiguity")),
                         # 2026-10-24（A/B 四维）：版本 / 问题种类 / 轮次判定 / 时间
                         "ts": str(rec.get("ts") or ""),
                         "lookup_version": tel.get("lookup_version") or "unknown",
                         "q_category": tel.get("q_category") or "",
                         "turn_id": tel.get("turn_id") or "",
                         "first_step": tel.get("first_step"),
                         "turn_first_retrieval": tel.get("turn_first_retrieval") or "",
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
    """类目 → {hint: [命中, 总数], plain: [命中, 总数]}

    类目取值优先级（2026-10-24）：问题集 `category`（已知题）> telemetry `q_category`（形态推断）> `-`
    """
    out: dict = {}
    for r in rows:
        cat = r.get("category") or r.get("q_category") or "-"
        d = out.setdefault(cat, {"hint": [0, 0], "plain": [0, 0]})   # [solved, total]
        key = r.get("variant") if r.get("variant") in ("hint", "plain") else None
        if not key:
            continue
        d[key][1] += 1
        if r.get("case_ok") or r["hits"] > 0:
            d[key][0] += 1
    return out


def _row_date(r: dict) -> str:
    """行日期 `YYYY-MM-DD`（真实遥测取 ts 前缀；模拟报告取 at）"""
    ts = (r.get("ts") or r.get("at") or "").strip()
    return ts[:10] if len(ts) >= 10 else ""


def apply_filters(rows: list, since: str = "", until: str = "", version: str = "",
                  cohort: str = "c1", first_step_only: bool = True) -> tuple:
    """按**时间窗 / 版本 / 首步口径**筛选，返回 (保留行, 排除计数)。

    排除原因（报告必须**逐条计数**，不能悄悄丢样本 ✗）：
      · `out_of_range`           时间窗之外；
      · `version_mismatch`       版本不匹配（`--version` 模糊匹配）；
      · `turn_missing`           没有 `turn_id`/`turn_first_retrieval` ⇒ 无法证明首步（历史数据/未传参）；
      · `first_retrieval_other`  本轮首个检索动作**不是** knowledge_lookup（如先 context_lookup/doc_outline）；
      · `self_report_false`      智能体自报 `first_step=false`。

    `cohort="c3"`：不做首步门禁（只按会话首调取样），仅作旁证 ✓
    模拟行（`source=sim`）**不受首步门禁约束**（确定性基线，本来就是逐题一问一调 ✓）。
    """
    kept, excl = [], {}
    for r in rows:
        d = _row_date(r)
        if since and d and d < since:
            excl["out_of_range"] = excl.get("out_of_range", 0) + 1
            continue
        if until and d and d > until:
            excl["out_of_range"] = excl.get("out_of_range", 0) + 1
            continue
        if version and not _vmatch(str(r.get("lookup_version") or "unknown"), version):
            excl["version_mismatch"] = excl.get("version_mismatch", 0) + 1
            continue
        if cohort == "c3" or (r.get("source") or "").startswith("sim"):
            kept.append(r)
            continue
        first = str(r.get("turn_first_retrieval") or "")
        if not r.get("turn_id") and not first:
            excl["turn_missing"] = excl.get("turn_missing", 0) + 1
            continue
        if first and first != "knowledge_lookup":
            excl["first_retrieval_other"] = excl.get("first_retrieval_other", 0) + 1
            continue
        if first_step_only and r.get("first_step") is False:
            excl["self_report_false"] = excl.get("self_report_false", 0) + 1
            continue
        kept.append(r)
    return kept, excl


def _vmatch(actual: str, pattern: str) -> bool:
    """版本匹配：支持 `v2-*` / `v2` / 精确值 ✓"""
    import fnmatch  # noqa: PLC0415

    if not pattern or pattern in ("*", "all"):
        return True
    if fnmatch.fnmatch(actual, pattern):
        return True
    return actual.split("-")[0] == pattern or actual.startswith(pattern)


def by_version(rows: list) -> dict:
    """版本 → 行（**跨版本不能混比**：程序改了、数据也可能改了 ✗）"""
    out: dict = {}
    for r in rows:
        out.setdefault(str(r.get("lookup_version") or "unknown"), []).append(r)
    return out


def _metrics_table(s: dict) -> list:
    """单版本指标表（hint vs plain）"""
    lines = ["| 指标 | hint（本体提示开） | plain（关） | 差 |", "|---|---|---|---|"]
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
    return lines



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
    """渲染 Markdown（**四维口径**：版本 × A/B × 问题类目 × 时间窗）"""
    versions = rep.get("versions") or {}
    excl = rep.get("excluded") or {}
    lines = ["# 检索 A/B 报告 · %s" % title, "",
             "> 生成：%s ｜ 设计：**配对**（同题两组）｜ 准入口径：%s ｜ 指标口径见 `docs/lookup-ab.md`"
             % (time.strftime("%Y-%m-%d %H:%M"), rep.get("cohort_note") or "C1（首步即 knowledge_lookup）"),
             ""]

    # ---- 表1：跨版本总览（版本行） ----
    if len(versions) >= 1:
        lines += ["## 1. 按版本总览", "",
                  "| 版本 | 组 | 样本 | 命中率 | 解决率 | ≤3 收敛 | 中位耗时 |",
                  "|---|---|---|---|---|---|---|"]
        for ver in sorted(versions):
            s = versions[ver]
            for variant, label in (("hint", "hint"), ("plain", "plain")):
                c = s.get(variant) or {}
                if not c:
                    continue
                lines.append("| %s | %s | %d | %s | %s | %s | %s |"
                             % (ver, label, c.get("n", 0), _cell(c.get("found_rate")),
                                _cell(c.get("case_ok_rate")), _cell(c.get("converged_3")),
                                _cell(c.get("median_ms"), "median_ms")))
        lines.append("")

    # ---- 表2：逐版本指标（hint vs plain + 差） ----
    if len(versions) > 1:
        lines += ["## 2. 逐版本指标（**跨版本不可混比** ✓）", ""]
        for ver in sorted(versions):
            lines += ["### 版本 `%s`" % ver, ""] + _metrics_table(versions[ver]) + [""]
    else:
        only = sorted(versions)[0] if versions else ""
        lines += ["## 2. 指标（版本 `%s`）" % only, ""] + _metrics_table(rep.get("summary") or {}) + [""]

    # ---- 表3：版本 × 问题类目 ----
    cats = rep.get("categories") or by_category(rows)
    if cats:
        lines += ["## 3. 按版本 × 问题类目", "",
                  "| 版本 | 类目 | hint 命中/总 | plain 命中/总 |", "|---|---|---|---|"]
        for (ver, cat), d in sorted(cats.items()):
            lines.append("| %s | %s | %d/%d | %d/%d |"
                         % (ver, cat, d["hint"][0], d["hint"][1], d["plain"][0], d["plain"][1]))
        lines.append("")

    # ---- 表4：数据质量（配对 + 准入排除计数） ----
    q_hint = {r.get("question_key") for r in rows if r.get("variant") == "hint"}
    q_plain = {r.get("question_key") for r in rows if r.get("variant") == "plain"}
    lines += ["## 4. 数据质量", ""]
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
    if excl:
        lines.append("- **准入排除**（未纳入统计，逐条计数 ✓）：%s"
                     % "、".join("%s=%d" % (k, v) for k, v in sorted(excl.items())))
    else:
        lines.append("- **准入排除**：0（全部样本满足 `%s` 口径）" % (rep.get("cohort") or "c1"))
    # 首步一致率：自报 vs 服务端裁决（真实数据才有意义）
    real = [r for r in rows if not (r.get("source") or "").startswith("sim")]
    judged = [r for r in real if r.get("turn_first_retrieval")]
    if judged:
        agree = sum(1 for r in judged
                    if (r["turn_first_retrieval"] == "knowledge_lookup") is not (r.get("first_step") is False))
        lines.append("- **首步一致率**（自报 `first_step` vs 服务端裁决）：%.1f%%（n=%d）"
                     % (100.0 * agree / len(judged), len(judged)))
    lines.append("- 问题集覆盖：%d 题（问题集共 %d 题）" % (len(q_hint | q_plain),
                                                     len(expects) or len(q_hint | q_plain)))
    missing = [k for k in (q_hint | q_plain) if k and k not in expects]
    if missing:
        lines.append("- ⚠️ 未在问题集里的问题键 %d 个（只统计命中率、不判解决）：%s"
                     % (len(missing), "、".join(missing[:5])))
    lines.append("- 结论**不在此自动化判定**：本表只保证样本可比、指标可复算 ✓")
    return "\n".join(lines) + "\n"



def main() -> int:
    ap = argparse.ArgumentParser(description="检索 A/B 报告（hint vs plain；四维：版本×组×类目×时间）")
    ap.add_argument("--sim", default="", help="模拟报告 glob（默认 logs/ab/lookup_ab_*.json）")
    ap.add_argument("--telemetry", default="", help="真实遥测 glob（默认 logs/lookup_ab_*.jsonl）")
    ap.add_argument("--questions", default="", help="问题集路径")
    ap.add_argument("--out", default="", help="写入 Markdown 文件")
    ap.add_argument("--no-telemetry", action="store_true",
                    help="忽略真实遥测（只看模拟报告；冒烟/联调产生的日志会稀释样本 ✓）")
    # 2026-10-24（A/B 四维）：时间窗 / 版本 / 准入口径 / 最小样本
    ap.add_argument("--since", default="", help="起始日期 YYYY-MM-DD（含）")
    ap.add_argument("--until", default="", help="结束日期 YYYY-MM-DD（含）")
    ap.add_argument("--version", default="", help="版本过滤：`v2` / `v2-*` / 精确 `v2-1a2b3c4d`")
    ap.add_argument("--cohort", default="c1", choices=["c1", "c3"],
                    help="c1=首步即 knowledge_lookup 才纳入（默认）；c3=不看首步（仅旁证）")
    ap.add_argument("--no-first-step-gate", action="store_true",
                    help="忽略智能体自报 `first_step=false`（只看服务端裁决）")
    ap.add_argument("--min-sample", type=int, default=20,
                    help="每格最小样本；不足则只报 n（默认 20）")
    args = ap.parse_args()

    expects = load_questions(args.questions)
    reps = load_sim_reports(args.sim)
    if reps:
        reps = reps[-1:]
    rows: list = []
    for r in reps:
        sub = r.get("rows") or []
        ver = r.get("lookup_version") or "sim"
        for row in sub:
            row.setdefault("lookup_version", ver)
            row.setdefault("at", r.get("at") or "")
        rows += sub
    real = [] if args.no_telemetry else load_telemetry(args.telemetry)
    raw_n = len(rows) + len(real)
    rows += real

    rows, excl = apply_filters(rows, since=args.since, until=args.until, version=args.version,
                               cohort=args.cohort,
                               first_step_only=not args.no_first_step_gate)
    if not rows:
        print(json.dumps({"ok": False,
                          "error": "没有可用数据（先跑 tests/ab_runner.py 或真实调用）",
                          "excluded": excl, "raw_rows": raw_n}, ensure_ascii=False))
        return 1

    versions = {ver: summarize_rows(sub, expects) for ver, sub in by_version(rows).items()}
    # 版本 × 类目（四维里的第 2、3 维交叉）
    cats: dict = {}
    for ver, sub in by_version(rows).items():
        for cat, d in by_category(sub).items():
            cats[(ver, cat)] = d
    summary = summarize_rows(rows, expects)
    rep = {"summary": summary, "versions": versions, "categories": cats, "excluded": excl,
           "cohort": args.cohort,
           "cohort_note": ("C1（首步即 knowledge_lookup；排除项逐条计数 ✓）" if args.cohort == "c1"
                           else "C3（不判首步，仅旁证）")}
    md = render_md(rep, rows, expects,
                   "模拟 %d 份 / 真实调用 %d 条 / 纳入 %d 条" % (len(reps), len(real), len(rows)))
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(md)
        print(json.dumps({"ok": True, "out": args.out, "rows": len(rows),
                          "versions": sorted(versions), "excluded": excl,
                          "min_sample": args.min_sample}, ensure_ascii=False))
    else:
        print(md)
    return 0


if __name__ == "__main__":
    sys.exit(main())

