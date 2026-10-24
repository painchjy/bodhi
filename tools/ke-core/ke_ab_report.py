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
import calendar
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
            io = tel.get("io") or {}
            retr = tel.get("retrieval") or {}
            rows.append({"variant": tel.get("variant") or "unknown",
                         "question_key": tel.get("question_key") or "",
                         "question_core": tel.get("question_core") or "",
                         "qid": tel.get("qid") or "",
                         "q_shape": tel.get("q_shape") or "",
                         "level": tel.get("level") or "",
                         "kw_terms": int(tel.get("kw_terms") or 0),
                         "session_no": tel.get("session_no") or "",
                         "question": tel.get("question") or tel.get("query") or "",
                         "query": tel.get("query") or "",
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
                         # 2026-10-25（口径 v2）：**真实库访问**（不是 MCP 调用次数 ✓）
                         "queries": int(retr.get("queries") or io.get("queries") or 0),
                         "pg_queries": int(retr.get("pg") or 0),
                         "neo4j_queries": int(retr.get("neo4j") or 0),
                         "empty_queries": int(retr.get("empty_queries") or io.get("empty") or 0),
                         "hit_queries": int(retr.get("hit_queries") or io.get("hit") or 0),
                         "retrieval_ok": bool(retr.get("ok")) if "ok" in retr else None,
                         "degraded": bool(retr.get("degraded")),
                         "io_ms": int(retr.get("io_ms") or io.get("ms") or 0),
                         "has_io": bool(io or retr),
                         "source": os.path.basename(f)})
    return rows


def load_trace(pattern: str = "") -> list:
    """C2 真值（`ke_ab_trace.py` 产物）：一行一问，**工具序列与耗时都来自服务端记录** ✓"""
    pat = pattern or os.path.join(REPO, "logs", "ab", "trace_*.jsonl")
    rows = []
    for f in sorted(glob.glob(pat)):
        for line in open(f, encoding="utf-8"):
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except Exception:  # noqa: BLE001
                continue
    return rows

def _ctx(text: str = ""):
    """取 ke_lookup 的口径函数（缺失则退化，报告仍可读）。"""
    try:
        import ke_lookup  # noqa: PLC0415
        return ke_lookup
    except Exception:  # noqa: BLE001
        return None


def _core(text: str = "") -> str:
    mod = _ctx()
    if mod is not None:
        return mod._question_core(text)
    return (text or "").strip().lower()


def _bigrams(text: str) -> set:
    t = "".join(ch for ch in (text or "") if not ch.isspace())
    if len(t) < 2:
        return {t} if t else set()
    return {t[i:i + 2] for i in range(len(t) - 1)}


def _sim(a: str, b: str) -> float:
    """字符 bigram 的 Jaccard —— 中文短问句上比"完全相等"稳（同义改写也能归组 ✓）。"""
    x, y = _bigrams(a), _bigrams(b)
    if not x or not y:
        return 0.0
    return len(x & y) / float(len(x | y))


def _ts_secs(text: str) -> float:
    """`YYYY-MM-DD HH:MM:SS[.fff][ +0800]` → 秒（解析失败回 -1，调用方自己判）"""
    t = str(text or "").strip()
    for fmt, n in (("%Y-%m-%d %H:%M:%S", 19), ("%Y-%m-%dT%H:%M:%S", 19),
                   ("%Y-%m-%d %H:%M", 16)):
        try:
            return calendar.timegm(time.strptime(t[:n], fmt))
        except Exception:  # noqa: BLE001
            continue
    return -1.0


def group_rows(rows: list, threshold: float = 0.62) -> list:
    """**问题归组**（2026-10-25 用户口径）：不同编号/不同措辞也可能是同一个问题。

    规则（确定性、可复算）：
      ① 有业务编号（`qid`，如 `评审编号 YYYYMM-889999`）且该编号已归组 ⇒ **必进同组**
         （这正是"同一编号评审两次 = 两轮，不是两个问题" ✓）；
      ② 否则与各组**代表问题**算字符 bigram 相似度，取最高且 ≥ `threshold` 者；
      ③ 都不满足 ⇒ 新开一组（代表 = 本行问题，组名 `G1` / `G2`…）。
    组内**不**按形态（词数/层次）再拆：形态差异保留在 `q_shape` 维度里单独看 ✓。
    """
    ordered = sorted(rows, key=lambda r: (r.get("ts") or "", r.get("question_key") or ""))
    groups: list = []          # [{"gid","label","core"}]
    by_qid: dict = {}
    for r in ordered:
        core = r.get("question_core") or _core(r.get("question") or r.get("query") or "")
        qid = (r.get("qid") or "").strip()
        gid = by_qid.get(qid) if qid else None
        if not core:
            # 旧遥测（口径 v1）没有 question 文本 ⇒ **无法归组**：统一进 G0，不伪造分组 ✓
            r["question_group"] = "G0"
            continue
        if not gid:
            best, best_sim = None, 0.0
            for g in groups:
                s = _sim(core, g["core"])
                if s > best_sim:
                    best, best_sim = g, s
            if best is not None and best_sim >= threshold:
                gid = best["gid"]
            else:
                gid = "G%d" % (len(groups) + 1)
                groups.append({"gid": gid, "core": core,
                               "label": (r.get("question") or r.get("query") or core)[:28]})
        if qid:
            by_qid[qid] = gid
        r["question_group"] = gid
    labels = {g["gid"]: g["label"] for g in groups}
    labels["G0"] = "（未传 question：无法归组）"
    for r in rows:
        r["group_label"] = labels.get(r.get("question_group") or "", "")
    return rows


def pairing(rows: list) -> dict:
    """配对体检：**两组各自覆盖了多少个问题组、交集多少**（不是"字符串集合相等"✗）"""
    h = {r["question_group"] for r in rows if r.get("variant") == "hint" and r.get("question_group")}
    p = {r["question_group"] for r in rows if r.get("variant") == "plain" and r.get("question_group")}
    return {"hint_groups": len(h), "plain_groups": len(p), "paired": len(h & p),
            "only_hint": sorted(h - p), "only_plain": sorted(p - h),
            "arm_missing": ("plain" if not p else ("hint" if not h else "")),
            "comparable": bool(h and p and (h & p))}


def link_trace(trace: list, rows: list, threshold: float = 0.62) -> list:
    """把 C2 真值接到 A/B 组上：`(会话, 问题)` 精确匹配；失败则同会话语义最近（≥阈值）。

    连上之后，真值行才有 `variant`（哪一组跑出来的）⇒ 报告才能给"**端到端耗时**（含 LLM 思考）"
    与"**真·首步**"，这两项在服务端遥测里**看不到** ✓
    """
    idx: dict = {}
    for r in rows:
        if (r.get("source") or "").startswith("sim"):
            continue
        idx.setdefault((r.get("session_no") or "", r.get("question_key") or ""), []).append(r)
    out = []
    for t in trace:
        t = dict(t)
        key = (t.get("session_no") or "", t.get("question_key") or "")
        cands = idx.get(key) or []
        if not cands:
            best, best_sim = [], 0.0
            for (sno, _k), rs in idx.items():
                if sno != key[0]:
                    continue
                s = _sim(t.get("question_core") or _core(t.get("question") or ""),
                         rs[0].get("question_core") or _core(rs[0].get("question") or ""))
                if s > best_sim:
                    best, best_sim = rs, s
            cands = best if best_sim >= threshold else []
        if not cands and key[0]:
            # 再退一层：旧遥测没有 question 文本 ⇒ **同会话 + 时间最近（±10min）** 也算连上
            # （诚实边界：这不是精确配对，报告里只用于"端到端耗时/真首步"的旁证 ✓）
            t_ts = _ts_secs(t.get("ts"))
            best, best_d = [], 1e9
            for (sno, _k), rs in idx.items():
                if sno != key[0]:
                    continue
                for r in rs:
                    r_ts = _ts_secs(r.get("ts"))
                    if t_ts < 0 or r_ts < 0:
                        continue
                    d = abs(t_ts - r_ts)
                    if d < best_d:
                        best, best_d = [r], d
            cands = best if best_d <= 600 else []
        hit = cands[0] if cands else {}
        t["variant"] = hit.get("variant") or "unknown"
        t["hits"] = max([c.get("hits") or 0 for c in cands], default=0)
        # 本轮**服务端检索合计耗时**（多轮 lookup 相加）—— 用于算"检索占端到端的比例"
        t["lookup_ms"] = sum(int(c.get("ms") or 0) for c in cands)
        t["queries"] = sum(int(c.get("queries") or 0) for c in cands)
        t["empty_queries"] = sum(int(c.get("empty_queries") or 0) for c in cands)
        t["question_group"] = hit.get("question_group") or ""
        t["linked"] = bool(cands)
        out.append(t)
    return out


def _pct(x) -> str:
    return "-" if x is None else "%.1f%%" % (float(x) * 100)


def summarize_rows(rows: list, expects: dict) -> dict:
    """模拟行（有 case_ok）与真实行（无 case_ok）统一汇总。

    2026-10-25（口径 v2）新增，回答用户三个问题：
      · **实际查库次数**：`queries` = PG + Neo4j 往返数（不是 MCP 调用次数 ✓）；
      · **空记录=失败**：`empty_queries` / `empty_rate`；`ok_rate` = 至少一次往返有结果的调用占比；
      · **总耗时**：单次 `median_ms` / `p90_ms`、每组（一问）检索合计 `group_ms_*`、
        整批 `sum_ms`；端到端真值另见 `trace_*`（含 LLM 思考 ✓）。
    """
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
            key = (r.get("session_no") or "-", r.get("question_group")
                   or r.get("question_key") or "-")
            calls[key] = calls.get(key, 0) + 1
        if all("attempts" in r for r in sub):
            avg_attempts = sum(int(r["attempts"]) for r in sub) / n
            converged = sum(1 for r in sub if int(r["attempts"]) <= 3) / n
        else:
            avg_attempts = (sum(calls.values()) / len(calls)) if calls else 0.0
            converged = (sum(1 for v in calls.values() if v <= 3) / len(calls)) if calls else 0.0
        # —— 真实库访问（只有带 io 的行才计入，模拟行没有 IO ⇒ 不冒充 ✓）
        withio = [r for r in sub if r.get("has_io")]
        q_tot = sum(int(r.get("queries") or 0) for r in withio)
        e_tot = sum(int(r.get("empty_queries") or 0) for r in withio)
        h_tot = sum(int(r.get("hit_queries") or 0) for r in withio)
        per_call = [int(r.get("queries") or 0) for r in withio]
        # —— 每组（一问）检索合计耗时：把同一组的多轮加总（"总耗时"的用户口径 ✓）
        gsum: dict = {}
        for r in sub:
            key = (r.get("session_no") or "-", r.get("question_group")
                   or r.get("question_key") or "-")
            gsum[key] = gsum.get(key, 0) + int(r.get("ms") or 0)
        gvals = sorted(gsum.values())
        ms_vals = sorted(int(r.get("ms") or 0) for r in sub)
        out[variant] = {
            "n": n,
            "questions": len({r.get("question_group") or r.get("question_key")
                              for r in sub if (r.get("question_group") or r.get("question_key"))}),
            "found_rate": sum(1 for r in sub if r["hits"] > 0) / n,
            "case_ok_rate": (len(ok) / len(decided)) if decided else None,
            "decided_n": len(decided),
            "avg_attempts": avg_attempts,
            "converged_3": converged,
            "guidance_rate": sum(1 for r in sub if r.get("guidance")) / n,
            "median_ms": int(statistics.median(ms_vals)) if ms_vals else 0,
            "p90_ms": _p90(ms_vals),
            "sum_ms": sum(ms_vals),
            "group_ms_median": int(statistics.median(gvals)) if gvals else 0,
            "group_ms_p90": _p90(gvals),
            # 真实库访问
            # 真实库访问（无 IO 的行不参与 ⇒ 旧日志不会显示成"0 次"✗）
            "io_n": len(withio),
            "queries": q_tot if withio else None,
            "empty_queries": e_tot if withio else None,
            "hit_queries": h_tot if withio else None,
            "pg_queries": (sum(int(r.get("pg_queries") or 0) for r in withio) if withio else None),
            "neo4j_queries": (sum(int(r.get("neo4j_queries") or 0) for r in withio)
                              if withio else None),
            "queries_per_call": (round(q_tot / len(withio), 2) if withio else None),
            "queries_median": int(statistics.median(per_call)) if per_call else None,
            "empty_rate": ((e_tot / float(e_tot + h_tot)) if (e_tot + h_tot) else None),
            "io_ms": sum(int(r.get("io_ms") or 0) for r in withio),
            "degraded_rate": (sum(1 for r in withio if r.get("degraded")) / len(withio)
                              if withio else None),
            "ok_rate": (sum(1 for r in withio if (r.get("hits") or 0) > 0) / len(withio)
                        if withio else None),
        }
    return out


def _p90(values: list) -> int:
    """P90（已排序或未排序都可；样本少时退化为最大值 ✓）"""
    if not values:
        return 0
    v = sorted(values)
    return int(v[max(0, int(len(v) * 0.9) - 1)])


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
            # 2026-10-25（口径 v2）：**真实库访问**（MCP 调用次数不等于库访问次数 ✗）
            ("带 IO 的样本数", "io_n"),
            ("真实库访问/次（中位）", "queries_median"),
            ("真实库访问合计", "queries"),
            ("其中 PG 往返（合计）", "pg_queries"),
            ("其中 Neo4j（合计）", "neo4j_queries"),
            ("空查询（空记录=失败）", "empty_queries"),
            ("空查询占比", "empty_rate"),
            ("有结果的往返", "hit_queries"),
            ("有结果调用占比", "ok_rate"),
            ("降级调用占比", "degraded_rate"),
            # 耗时：单次 / 每问（多轮合计，即"总耗时"）/ 整批
            ("单次检索 中位", "median_ms"), ("单次检索 P90", "p90_ms"),
            ("每问检索总耗时 中位", "group_ms_median"), ("每问检索总耗时 P90", "group_ms_p90"),
            ("整批检索耗时合计", "sum_ms")]
    for label, key in spec:
        hv, pv = h.get(key), p.get(key)
        if hv is None or pv is None:
            lines.append("| %s | %s | %s | - |" % (label, _cell(hv, key), _cell(pv, key)))
            continue
        if isinstance(hv, float) and isinstance(pv, float) and key != "avg_attempts":
            diff = "%+.1f%%" % ((hv - pv) * 100)
        elif "ms" in key and isinstance(hv, int) and isinstance(pv, int):
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
    if "ms" in key and isinstance(v, int):
        return "%dms" % v
    if isinstance(v, float):
        return _pct(v)
    return str(v)


def by_group(rows: list) -> list:
    """按问题组汇总（同一问题多轮合并；形态差异并列显示 ✓）"""
    out: dict = {}
    for r in rows:
        gid = r.get("question_group") or "-"
        d = out.setdefault(gid, {"gid": gid,
                                 "label": r.get("group_label") or r.get("question") or "",
                                 "q_shape": set(), "hint_runs": 0, "plain_runs": 0,
                                 "hint_hits": 0, "hint_queries": 0, "hint_empty": 0,
                                 "hint_ms": 0})
        if r.get("q_shape"):
            d["q_shape"].add(str(r["q_shape"]))
        if r.get("variant") == "hint":
            d["hint_runs"] += 1
            d["hint_hits"] += 1 if (r.get("hits") or 0) > 0 else 0
            d["hint_queries"] += int(r.get("queries") or 0)
            d["hint_empty"] += int(r.get("empty_queries") or 0)
            d["hint_ms"] += int(r.get("ms") or 0)
        elif r.get("variant") == "plain":
            d["plain_runs"] += 1
    res = list(out.values())
    for d in res:
        d["q_shape"] = "／".join(sorted(d["q_shape"])) if d["q_shape"] else "-"
    res.sort(key=lambda d: (-(d["hint_runs"] + d["plain_runs"]), d["gid"]))
    return res


def _trace_table(trace: list) -> list:
    """C2 真值表：**端到端耗时**（含 LLM 思考）与**真·首步**（不再自报 ✓）"""
    if not trace:
        return []
    lines = ["## 3. C2 真值（WeKnora `agent_steps` + `agent_duration_ms`）", "",
             "| 组 | 问数 | 端到端耗时 中位 | 端到端 P90 | 检索耗时占比 | 真·首步=knowledge_lookup | 原生检索先行 | 平均工具调用 |",
             "|---|---|---|---|---|---|---|---|"]
    for variant in ("hint", "plain", "unknown"):
        sub = [t for t in trace if t.get("variant") == variant]
        if not sub:
            continue
        dur = sorted(int(t.get("agent_ms") or 0) for t in sub)
        linked = [t for t in sub if t.get("linked")]
        first_ok = sum(1 for t in sub if t.get("first_is_lookup"))
        native = sum(1 for t in sub if (t.get("first_retrieval") or "") not in
                     ("", "knowledge_lookup"))
        # 检索耗时占比 = 我方 lookup 合计 / 端到端（只看连上的样本）
        io_ms = sum(int(t.get("lookup_ms") or 0) for t in linked)
        e2e = sum(int(t.get("agent_ms") or 0) for t in linked)
        lines.append("| %s | %d | %dms | %dms | %s | %s（%d/%d） | %d | %.1f |"
                     % (variant, len(sub), int(statistics.median(dur)) if dur else 0,
                        _p90(dur), _pct(io_ms / e2e) if e2e else "-",
                        _pct(first_ok / len(sub)) if sub else "-", first_ok, len(sub),
                        native, (sum(int(t.get("tool_calls") or 0) for t in sub)
                                 / float(len(sub)))))
    lines += ["", "> `端到端耗时` 来自 `messages.agent_duration_ms`（真值，含 LLM 思考）；"
              "`检索耗时占比` = 本轮 `knowledge_lookup` 合计 / 端到端（只统计能与遥测连上的样本）。",
              "> `unknown` = **未能与服务端遥测连上**的问（旧日志没传 `session_no`/`question`）；"
              "它只作端到端耗时旁证，**不参与 A/B 差异** ✓", ""]
    return lines


def render_md(rep: dict, rows: list, expects: dict, title: str) -> str:
    """渲染 Markdown（**四维**：版本 × A/B × 问题类目 × 时间窗；**v2 增**真实 IO / 总耗时 / 归组）"""
    versions = rep.get("versions") or {}
    excl = rep.get("excluded") or {}
    lines = ["# 检索 A/B 报告 · %s" % title, "",
             "> 生成：%s ｜ 设计：**配对**（同题两组）｜ 准入口径：%s ｜ 指标口径见 `docs/lookup-ab.md`"
             % (time.strftime("%Y-%m-%d %H:%M"),
                rep.get("cohort_note") or "C1（首步即 knowledge_lookup）"),
             "> 口径 **v2（2026-10-25）**：查询次数按**真实的 PG / Neo4j 往返**算（不是 MCP 调用次数）；"
             "**返回空记录的往返算失败**；耗时给三层（单次 / 每问合计 / 端到端真值）。",
             ""]

    # ---- 表1：跨版本总览（版本行） ----
    if len(versions) >= 1:
        lines += ["## 1. 按版本总览", "",
                  "| 版本 | 组 | 样本 | 命中率 | 解决率 | ≤3 收敛 | 单次中位 | 每问总耗时中位 |",
                  "|---|---|---|---|---|---|---|---|"]
        for ver in sorted(versions):
            s = versions[ver]
            for variant, label in (("hint", "hint"), ("plain", "plain")):
                c = s.get(variant) or {}
                if not c:
                    continue
                lines.append("| %s | %s | %d | %s | %s | %s | %s | %s |"
                             % (ver, label, c.get("n", 0), _cell(c.get("found_rate")),
                                _cell(c.get("case_ok_rate")), _cell(c.get("converged_3")),
                                _cell(c.get("median_ms"), "median_ms"),
                                _cell(c.get("group_ms_median"), "group_ms_median")))
        lines.append("")

    # ---- 表2：逐版本指标（hint vs plain + 差） ----
    if len(versions) > 1:
        lines += ["## 2. 逐版本指标（**跨版本不可混比** ✓）", ""]
        for ver in sorted(versions):
            lines += ["### 版本 `%s`" % ver, ""] + _metrics_table(versions[ver]) + [""]
    else:
        only = sorted(versions)[0] if versions else ""
        lines += ["## 2. 指标（版本 `%s`）" % only, ""] \
            + _metrics_table(rep.get("summary") or {}) + [""]

    # ---- 表3：C2 真值（端到端耗时 + 真首步） ----
    lines += _trace_table(rep.get("trace") or [])

    # ---- 表4：版本 × 问题类目 ----
    cats = rep.get("categories")
    if not cats:                      # 兜底：by_category 的键只有类目 ⇒ 补上版本维度 ✓
        cats = {("-", cat): d for cat, d in (by_category(rows) or {}).items()}
    if cats:
        lines += ["## 4. 按版本 × 问题类目", "",
                  "| 版本 | 类目 | hint 命中/总 | plain 命中/总 |", "|---|---|---|---|"]
        for (ver, cat), d in sorted(cats.items()):
            lines.append("| %s | %s | %d/%d | %d/%d |"
                         % (ver, cat, d["hint"][0], d["hint"][1], d["plain"][0], d["plain"][1]))
        lines.append("")

    # ---- 表5：按问题组（同问题多轮合并；形态差异单列） ----
    groups = rep.get("groups") or []
    if groups:
        lines += ["## 5. 按问题组（不同编号/措辞可归同组 · 形态不同单列）", "",
                  "| 组 | 代表问题 | 形态（词数／层次） | hint 轮次 | plain 轮次 | hint 命中 | hint 查询 | "
                  "hint 空查询 | hint 每问总耗时 |",
                  "|---|---|---|---|---|---|---|---|---|"]
        for g in groups[:25]:
            lines.append("| %s | %s | %s | %d | %d | %d | %d | %d | %s |"
                         % (g["gid"], (g.get("label") or "")[:24], g.get("q_shape") or "-",
                            g["hint_runs"], g["plain_runs"], g["hint_hits"],
                            g["hint_queries"], g["hint_empty"],
                            _cell(g.get("hint_ms"), "hint_ms")))
        if len(groups) > 25:
            lines.append("| … | 其余 %d 组（见 JSON 输出） | | | | | | | |" % (len(groups) - 25))
        lines.append("")

    # ---- 表6：数据质量（配对 + 准入排除计数） ----
    pair = rep.get("pairing") or pairing(rows)
    q_hint = {r.get("question_key") for r in rows if r.get("variant") == "hint"}
    q_plain = {r.get("question_key") for r in rows if r.get("variant") == "plain"}
    lines += ["## 6. 数据质量", ""]
    sources = sorted({r.get("source") or "?" for r in rows})
    for src in sources:
        sub = [r for r in rows if (r.get("source") or "?") == src]
        nh = sum(1 for r in sub if r.get("variant") == "hint")
        np_ = sum(1 for r in sub if r.get("variant") == "plain")
        sh = {r.get("question_group") for r in sub if r.get("variant") == "hint"}
        sp = {r.get("question_group") for r in sub if r.get("variant") == "plain"}
        if not np_ and nh:
            lines.append("- `%s`：hint **%d 轮** / plain **0 轮** ⇒ **对照臂为空，两组不可比**"
                         "（这**不是**“数据不一致”）；跑 B 组时显式传 `ontology_hint=false` 即可 ✓"
                         % (src, nh))
        elif not nh and np_:
            lines.append("- `%s`：plain %d 轮 / hint **0 轮** ⇒ 同样缺一组，不可比 ⚠️" % (src, np_))
        else:
            lines.append("- `%s`：hint %d 轮 / plain %d 轮；**问题组** %d vs %d，交集 %d"
                         "（仅 hint %d / 仅 plain %d）%s"
                         % (src, nh, np_, len(sh), len(sp), len(sh & sp),
                            len(sh - sp), len(sp - sh),
                            "✅" if (sh & sp) else "⚠️ 无交集，仍不可比"))
    lines.append("- 全局配对：hint 组 %d / plain 组 %d / **交集 %d** ⇒ %s"
                 % (pair["hint_groups"], pair["plain_groups"], pair["paired"],
                    ("可比 ✓" if pair["comparable"] else "**不可比**（缺 %s 臂）⚠️"
                     % (pair["arm_missing"] or "任一"))))
    lines.append("- 旧版那行「hint 35 条 / plain 0 条；问题集合不一致（23 vs 0）」的意思是："
                 "`23` = hint 侧**不同 `question_key` 的个数**（问题文本去重后的键），"
                 "`0` = plain 侧个数；一侧为 0 时“两个集合相等”必然不成立 "
                 "⇒ 现在改为**先判对照臂是否为空**，再判配对 ✓")
    if excl:
        lines.append("- **准入排除**（未纳入统计，逐条计数 ✓）：%s"
                     % "、".join("%s=%d" % (k, v) for k, v in sorted(excl.items())))
    else:
        lines.append("- **准入排除**：0（全部样本满足 `%s` 口径）" % (rep.get("cohort") or "c1"))
    # 首步：有 C2 真值就比"自报 vs 真值"；没有则退回"自报 vs 服务端裁决"
    trace = rep.get("trace") or []
    linked = [t for t in trace if t.get("linked")]
    idx_tel = {(r.get("session_no"), r.get("question_key")): r for r in rows
               if not (r.get("source") or "").startswith("sim")}
    done = False
    if linked:
        agree = tot = 0
        for t in linked:
            r = idx_tel.get((t.get("session_no"), t.get("question_key"))) or {}
            if r.get("first_step") is None:
                continue
            tot += 1
            agree += (bool(r.get("first_step")) == bool(t.get("first_is_lookup")))
        if tot:
            lines.append("- **自报 vs 真值一致率**（`first_step` ⇄ `agent_steps` 首个工具）：%.1f%%（n=%d）"
                         "—— 这才是真口径（旧版“首步一致率”只是自报 vs 服务端裁决）"
                         % (100.0 * agree / tot, tot))
            done = True
    if not done:
        real = [r for r in rows if not (r.get("source") or "").startswith("sim")]
        judged = [r for r in real if r.get("turn_first_retrieval")]
        if judged:
            agree = sum(1 for r in judged
                        if (r["turn_first_retrieval"] == "knowledge_lookup")
                        is not (r.get("first_step") is False))
            lines.append("- 首步一致率（自报 vs 服务端裁决，**未接入 C2 真值**）：%.1f%%（n=%d）"
                         % (100.0 * agree / len(judged), len(judged)))
    lines.append("- 问题集覆盖：%d 题（问题集共 %d 题）"
                 % (len(q_hint | q_plain), len(expects) or len(q_hint | q_plain)))
    missing = [k for k in (q_hint | q_plain) if k and k not in expects]
    if missing:
        lines.append("- ℹ️ 未在**固定问题集**里的 %d 个（只统计命中率、不判解决）：%s"
                     % (len(missing), "、".join(missing[:4])))
        lines.append("  —— 实际跑法常是「选文件 + 给编号 + 要求评审」而非按题库逐题 ⇒ 这类**自建问题**"
                     "按第 5 节的 `question_group` 归组比较 ✓")

    # ---- 7. 口径说明 ----
    lines += ["", "## 7. 口径说明（为什么这么算）", "",
              "- **查询次数**：一次 `knowledge_lookup` 内部可能打 1–5 次库/图"
              "（AND 空 ⇒ 自动降级 OR；图不可用 ⇒ 退 PG）⇒ **MCP 调用次数 ≠ 实际查库次数** ✗ "
              "现在按 **PG + Neo4j 的真实往返**统计（`ke_stats` 在 `psql()` / `neo4j.query()` 逐次回填行数）。",
              "- **成功/失败**：**返回空记录的往返算失败**；`有结果的往返` = rows>0 的次数；"
              "`空查询占比` = 空 ÷（空 + 有结果）；`有结果调用占比` = 该次调用最终 `hits>0` 的占比。",
              "- **总耗时**三层：`单次检索`（一次工具调用）／`每问检索总耗时`（同一问题多轮合计）／"
              "`端到端`（`agent_duration_ms`，含 LLM 思考与全部工具；只有 C2 真值能给）。",
              "- **问题归组**：同 `qid`（业务编号）必同组；否则字符 bigram 相似度 ≥ 阈值"
              "（默认 0.62，`--group-threshold` 可调）归同组；`q_shape`（词数档 × 类/实例/文本）"
              "单独成列 —— “关键字数量/层次不同”算不同类问题 ✓。",
              "- 结论**不在此自动化判定**：本表只保证样本可比、指标可复算 ✓"]
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
    # 2026-10-25（口径 v2）：C2 真值 + 问题归组
    ap.add_argument("--trace", default="",
                    help="C2 真值 glob（默认 logs/ab/trace_*.jsonl，由 ke_ab_trace.py 生成）")
    ap.add_argument("--no-trace", action="store_true", help="忽略 C2 真值（只看服务端遥测）")
    ap.add_argument("--group-threshold", type=float, default=0.62,
                    help="问题归组相似度阈值（同 qid 必同组；默认 0.62）")
    args = ap.parse_args()

    expects = load_questions(args.questions)
    reps = load_sim_reports(args.sim)
    if reps:
        # 取**行数最多、且最新**的一份（测试会写小样本报告，若取"最后一份"会把全量模拟悄悄挤掉 ✗）
        reps = [max(reps, key=lambda r: (len(r.get("rows") or []), r.get("at") or ""))]
    rows: list = []
    for r in reps:
        sub = r.get("rows") or []
        ver = r.get("lookup_version") or "sim"
        for row in sub:
            row.setdefault("lookup_version", ver)
            row.setdefault("at", r.get("at") or "")
            # 旧版模拟报告没写 `source` ⇒ 它**就是**模拟行（否则会被 C1 门禁当"无 turn"丢掉 ✗）
            row.setdefault("source", "sim")
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

    # 2026-10-25：问题归组（同 qid 必同组 / 相似措辞同组）+ C2 真值连接
    rows = group_rows(rows, threshold=args.group_threshold)
    trace = [] if args.no_trace else link_trace(load_trace(args.trace), rows,
                                                threshold=args.group_threshold)

    versions = {ver: summarize_rows(sub, expects) for ver, sub in by_version(rows).items()}
    # 版本 × 类目（四维里的第 2、3 维交叉）
    cats: dict = {}
    for ver, sub in by_version(rows).items():
        for cat, d in by_category(sub).items():
            cats[(ver, cat)] = d
    summary = summarize_rows(rows, expects)
    rep = {"summary": summary, "versions": versions, "categories": cats, "excluded": excl,
           "cohort": args.cohort,
           # 2026-10-25（口径 v2）：真值 / 归组 / 配对体检
           "trace": trace, "groups": by_group(rows), "pairing": pairing(rows),
           "group_threshold": args.group_threshold,
           "cohort_note": ("C1（首步即 knowledge_lookup；排除项逐条计数 ✓）" if args.cohort == "c1"
                           else "C3（不判首步，仅旁证）")}
    md = render_md(rep, rows, expects,
                   "模拟 %d 份 / 真实调用 %d 条 / 纳入 %d 条 / 问题组 %d 个"
                   % (len(reps), len(real), len(rows), len(rep["groups"])))
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(md)
        print(json.dumps({"ok": True, "out": args.out, "rows": len(rows),
                          "versions": sorted(versions), "excluded": excl,
                          "groups": len(rep["groups"]), "pairing": rep["pairing"],
                          "trace_rows": len(trace),
                          "min_sample": args.min_sample}, ensure_ascii=False))
    else:
        print(md)
    return 0


if __name__ == "__main__":
    sys.exit(main())

