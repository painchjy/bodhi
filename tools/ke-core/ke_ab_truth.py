"""ke-core · **真值匹配 + 答案判分**（A/B 的"结果正确率"）

口径（2026-10-25 用户裁定）
-------------------------
- 真值集是**测试资产**：`tests/cases/lookup_truth.json`（扁平、跨领域）。
  **服务端不读它** ✗ —— 线上只记"问题 + 回执 + 分臂"，判分全在分析侧做。
- 匹配**按对象、不按句式**（实测：字符 bigram Jaccard 对真改写只有 0.07~0.38，
  而模板化的不同轮次反而 0.56~0.70 ⇒ 单一整句阈值必然两头误判）：
    ① 同**编号**(qid) ⇒ 同题（沿用报告口径）；
    ② **锚点**（实例名/别名，`anchors`）命中 + 意图词（`intent`）；
    ③ 兜底整句相似度（字符 bigram Jaccard，阈值默认 **0.35**）；
    ④ 配了 `BODHI_EMBED_*` 才走真向量（cosine，阈值默认 **0.80**），否则跳过。
- **未匹配 ⇒ 不进分子分母**（只影响 `coverage`），不影响命中率/调用次数/耗时等其它指标 ✓
- 判分 = 答案里**实际提到的规则集合** vs `expect.rules`；`forbid` 命中 ⇒ 直接判错（越界）。

为什么不在判分里"抽条数"：条数会被"少一条"和"多一条"互相抵消；
集合比对能同时给出 `缺 / 多 / 越界`，且 `forbid` 让"越界项永不算"可机器判 ✓
"""
from __future__ import annotations

import glob
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import ke_lookup as L  # noqa: E402

try:                                     # **单一实现**：与报告的问题归组同源（字符 bigram Jaccard）
    import ke_ab_report as _REPORT  # noqa: E402
    _sim = _REPORT._sim
except Exception:                        # noqa: BLE001  独立运行时兜底（同一算法）
    def _sim(a: str, b: str) -> float:
        def grams(t: str) -> set:
            t = "".join(ch for ch in (t or "") if not ch.isspace())
            return {t[i:i + 2] for i in range(len(t) - 1)} if len(t) > 1 else ({t} if t else set())
        x, y = grams(a), grams(b)
        return (len(x & y) / float(len(x | y))) if (x and y) else 0.0

DEFAULT_TRUTH = os.path.join(REPO, "tests", "cases", "lookup_truth.json")
SIM_THRESHOLD = 0.35          # 兜底整句相似度（实测真改写 0.35~0.38、异题 0.11~0.15）
EMBED_THRESHOLD = 0.80        # 真向量（未配置时整段跳过）

_CACHE: dict = {"path": "", "mtime": 0.0, "data": {}}


def load(path: str = "") -> dict:
    """读真值集（按 mtime 缓存；路径缺省 = `tests/cases/lookup_truth.json`）。"""
    p = path or DEFAULT_TRUTH
    if not p or not os.path.isfile(p):
        return {}
    mt = os.path.getmtime(p)
    if _CACHE["path"] == p and _CACHE["mtime"] == mt:
        return dict(_CACHE["data"])
    try:
        data = json.loads(open(p, encoding="utf-8").read())
    except Exception:  # noqa: BLE001
        return {}
    data["_path"] = p
    _CACHE.update({"path": p, "mtime": mt, "data": dict(data)})
    return data


def kb_id_of(truth: dict) -> str:
    """真值集声明的库（**库名写在数据里**，别把领域信息硬编码进工具 ✓）。"""
    kb = str((truth or {}).get("kb") or "")
    if not kb:
        return ""
    try:
        import ke_db  # noqa: PLC0415
        return str(ke_db.resolve_kb_id(kb)[0] or "")
    except Exception:  # noqa: BLE001
        return ""


# ---------------------------------------------------------------------------
# 规则词典（答案是自然语言 ⇒ 要能认出"提到了哪条规则"）
# ---------------------------------------------------------------------------
def _rule_types() -> list:
    try:
        import ke_review  # noqa: PLC0415
        out = ke_review._bmm_rule_class_closure()
        if out:
            return out
    except Exception:  # noqa: BLE001
        pass
    return ["bmm:BusinessRule", "bmm:OperativeBusinessRule"]


def rule_dictionary(kb_id: str) -> dict:
    """`{slug: {title, aliases[]}}`（PG 优先：`aliases` 在页上；图只补缺）。"""
    if not kb_id:
        return {}
    types = _rule_types()
    out: dict = {}
    try:
        import ke_db  # noqa: PLC0415
        rows = ke_db.psql_csv(
            "SELECT slug, COALESCE(title,'') AS title, COALESCE(aliases::text,'[]') AS a "
            "  FROM wiki_pages WHERE knowledge_base_id = %s AND deleted_at IS NULL "
            "   AND page_type IN (%s)"
            % (ke_db.sql_str(kb_id), ", ".join(ke_db.sql_str(t) for t in types)))
        for r in rows:
            try:
                al = json.loads(r.get("a") or "[]") or []
            except Exception:  # noqa: BLE001
                al = []
            out[str(r["slug"])] = {"title": str(r.get("title") or ""),
                                   "aliases": [str(x) for x in al if x]}
    except Exception:  # noqa: BLE001  PG 不可用 ⇒ 用图上的 name
        try:
            import ke_neo4j  # noqa: PLC0415
            for r in ke_neo4j.query(
                    "MATCH (n:BodhiInstance {kb_id:$kb}) WHERE n.page_type IN $types "
                    "RETURN n.slug AS slug, coalesce(n.name,'') AS title",
                    {"kb": kb_id, "types": types}, timeout=8) or []:
                out[str(r.get("slug"))] = {"title": str(r.get("title") or ""), "aliases": []}
        except Exception:  # noqa: BLE001
            return {}
    return out


def mentioned_rules(answer: str, dictionary: dict) -> set:
    """答案里**实际提到**的规则（最长名优先；命中后从文本抹掉，防短名误配）。"""
    text = answer or ""
    if not text or not dictionary:
        return set()
    pairs = []
    for slug, d in dictionary.items():
        for name in [d.get("title")] + list(d.get("aliases") or []):
            name = str(name or "").strip()
            if len(name) >= 3:                 # 太短的名字（如两个字）不算，避免误配
                pairs.append((len(name), str(slug), name))
    pairs.sort(key=lambda x: -x[0])
    found = set()
    for _n, slug, name in pairs:
        if name in text:
            found.add(slug)
            text = text.replace(name, " ")
    return found


# ---------------------------------------------------------------------------
# 匹配（① qid ② 锚点+意图 ③ 整句相似度 ④ 真向量）
# ---------------------------------------------------------------------------
def _anchor_score(q_core: str, anchors: list) -> tuple:
    """→ `(full, lcs)`：任一锚点被**完整包含**，或与问题的最长公共子串够长。"""
    best = (0, 0)
    for a in anchors or []:
        an = L._norm(str(a or ""))
        if len(an) < 2:
            continue
        full = 1 if an in q_core else 0
        lcs = L._lcs_len(q_core, an)
        if (full, lcs) > best:
            best = (full, lcs)
    return best


def _entry_score(q_core: str, entry: dict) -> dict:
    full, lcs = _anchor_score(q_core, entry.get("anchors") or [])
    intent = [str(x) for x in (entry.get("intent") or []) if x]
    intent_hit = 1 if (not intent or any(L._norm(x) in q_core for x in intent)) else 0
    ok = 1 if ((full or lcs >= 3) and intent_hit) else 0
    return {"ok": ok, "full": full, "lcs": lcs, "intent_hit": intent_hit}


def _sim_of(q_core: str, entry: dict) -> float:
    cands = [entry.get("question") or ""] + list(entry.get("aliases") or [])
    return max((_sim(q_core, L._question_core(str(c or ""))) for c in cands), default=0.0)


def _embed_ready() -> bool:
    return bool(os.environ.get("BODHI_EMBED_BASE_URL") and os.environ.get("BODHI_EMBED_MODEL"))


def _embed_cosine(a: str, b: str) -> float:
    """可选真向量通道（OpenAI 兼容 `/embeddings`）；未配置/失败 ⇒ 0（调用方会跳过）。"""
    try:
        import math  # noqa: PLC0415
        from urllib import request as _rq  # noqa: PLC0415
        base = os.environ.get("BODHI_EMBED_BASE_URL", "").rstrip("/")
        model = os.environ.get("BODHI_EMBED_MODEL", "")
        key = os.environ.get("BODHI_EMBED_API_KEY", "")
        body = json.dumps({"model": model, "input": [a, b]}).encode("utf-8")
        req = _rq.Request(base + "/embeddings", data=body, method="POST")
        req.add_header("Content-Type", "application/json")
        if key:
            req.add_header("Authorization", "Bearer " + key)
        with _rq.urlopen(req, timeout=20) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        va, vb = data["data"][0]["embedding"], data["data"][1]["embedding"]
        dot = sum(x * y for x, y in zip(va, vb))
        na = math.sqrt(sum(x * x for x in va))
        nb = math.sqrt(sum(y * y for y in vb))
        return (dot / (na * nb)) if (na and nb) else 0.0
    except Exception:  # noqa: BLE001
        return 0.0


def match(question: str, qid: str = "", truth: dict | None = None) -> dict:
    """会话问题 → 真值条目（或 `matched=False`）。**服务端不做这件事**，只在分析侧跑 ✓"""
    truth = truth if truth is not None else load()
    q_core = L._question_core(question or "")
    out = {"matched": False, "matched_by": "", "entry": None, "entry_id": "", "score": 0.0,
           "sim": 0.0, "qid": str(qid or "")}
    if not q_core or not truth:
        return out
    entries = truth.get("questions") or []
    if not entries:
        return out

    q = str(qid or "").strip().lower()
    if q:                                       # ① 同编号 ⇒ 同题
        for e in entries:
            if q in [str(x).strip().lower() for x in (e.get("qids") or [])]:
                out.update({"matched": True, "matched_by": "qid", "entry": e,
                            "entry_id": str(e.get("id") or ""), "score": 1.0,
                            "sim": _sim_of(q_core, e)})
                return out

    scored = []                                 # ② 锚点 + 意图（主判据）
    for e in entries:
        s = _entry_score(q_core, e)
        if s["ok"]:
            scored.append((s["full"], s["lcs"], s["intent_hit"], e, s))
    if scored:
        scored.sort(key=lambda x: (-x[0], -x[1], -x[2]))
        best = scored[0]
        if len(scored) == 1 or scored[1][:3] != best[:3]:   # 并列 ⇒ 宁可不判（不猜 ✓）
            out.update({"matched": True, "matched_by": "anchor", "entry": best[3],
                        "entry_id": str(best[3].get("id") or ""),
                        "score": round(min(1.0, best[1] / 10.0), 3),
                        "sim": _sim_of(q_core, best[3]), "detail": best[4]})
            return out

    thr = float(((truth.get("match") or {}).get("sim_threshold")) or SIM_THRESHOLD)
    sims = sorted(((_sim_of(q_core, e), e) for e in entries), key=lambda x: -x[0])
    if sims and sims[0][0] >= thr and (len(sims) == 1 or sims[0][0] > sims[1][0]):
        out.update({"matched": True, "matched_by": "sim", "entry": sims[0][1],   # ③ 兜底
                    "entry_id": str(sims[0][1].get("id") or ""),
                    "score": round(float(sims[0][0]), 3), "sim": round(float(sims[0][0]), 3)})
        return out

    if _embed_ready():                          # ④ 真向量（配了才用）
        best, bi = 0.0, None
        for e in entries:
            c = _embed_cosine(q_core, L._question_core(str(e.get("question") or "")))
            if c > best:
                best, bi = c, e
        if bi is not None and best >= EMBED_THRESHOLD:
            out.update({"matched": True, "matched_by": "embed", "entry": bi,
                        "entry_id": str(bi.get("id") or ""),
                        "score": round(float(best), 3), "sim": round(float(best), 3)})
            return out
    out["sim"] = round(float(sims[0][0]), 3) if sims else 0.0
    return out


# ---------------------------------------------------------------------------
# 判分（集合口径：缺 / 多 / 越界）
# ---------------------------------------------------------------------------
def _compare(got: set, entry: dict) -> dict:
    """`got`（规则 slug 集合）vs 真值 ⇒ 缺 / 多 / 越界（**判分的唯一原子**）。"""
    expect = entry.get("expect") or {}
    exp = {str(s) for s in (expect.get("rules") or [])}
    forbid = {str(s) for s in (expect.get("forbid") or [])}
    groups = [set(str(x) for x in g) for g in (expect.get("rules_any_of") or [])]
    missing = exp - got
    extra = got - exp
    for g in groups:                         # 组内任一命中 ⇒ 该组不算缺（互为副本的规则）
        if g & got:
            missing -= g
        else:
            extra -= g
    forbidden = got & forbid
    n_hit = len(exp & got)
    prec = (n_hit / len(got)) if got else 0.0
    rec = ((len(exp) - len(missing)) / len(exp)) if exp else 0.0
    f1 = (2 * prec * rec / (prec + rec)) if (prec + rec) else 0.0
    return {"correct": bool(not missing and not extra and not forbidden),
            "f1": round(f1, 3), "precision": round(prec, 3), "recall": round(rec, 3),
            "n_expected": len(exp), "n_got": len(got), "n_hit": n_hit,
            "missing": sorted(missing), "extra": sorted(extra), "forbidden": sorted(forbidden)}


def judge(answer: str, entry: dict, dictionary: dict) -> dict:
    """**答案口径**：答案里提到的规则集合 vs 真值。"""
    out = _compare(mentioned_rules(answer, dictionary), entry)
    out["answer_len"] = len(answer or "")
    return out


def judge_slugs(slugs, entry: dict) -> dict:
    """**检索口径**：本轮实际取到的规则 slug vs 真值（不必等答案，样本更多 ✓）。"""
    return _compare({str(s) for s in (slugs or []) if s}, entry)


# ---------------------------------------------------------------------------
# 批量：trace（真实问答）→ 匹配 + 判分
# ---------------------------------------------------------------------------
def _load_trace(pattern: str = "") -> list:
    pat = pattern or os.path.join(REPO, "logs", "ab", "trace_*.jsonl")
    rows = []
    for f in sorted(glob.glob(pat)):
        for line in open(f, encoding="utf-8"):
            line = line.strip()
            if line:
                try:
                    rows.append(json.loads(line))
                except Exception:  # noqa: BLE001
                    continue
    return rows


def judge_trace(trace: list | None = None, truth_path: str = "") -> dict:
    """逐行判分 → `{rows, stats, sweep}`（**未匹配不进分子分母** ✓）。"""
    truth = load(truth_path)
    rows_in = trace if trace is not None else _load_trace()
    dictionary = rule_dictionary(kb_id_of(truth)) if truth else {}
    out = []
    for t in rows_in:
        m = match(t.get("question") or "", t.get("qid") or "", truth)
        answer = (t.get("answer") or "").strip()
        fetched = [str(s) for s in (t.get("rule_slugs") or []) if s]
        got = mentioned_rules(answer, dictionary) if answer else set()
        row = {"session_no": t.get("session_no") or "", "ts": t.get("ts") or "",
               "question": (t.get("question") or "")[:60],
               "qid": t.get("qid") or "", "variant": t.get("variant") or "unknown",
               "answer_len": int(t.get("answer_len") or 0),
               "matched": bool(m["matched"]), "matched_by": m["matched_by"] or "",
               "entry_id": m["entry_id"] or "", "score": round(float(m["score"] or 0), 3),
               "sim": round(float(m["sim"] or 0), 3),
               "route": str(t.get("resolved_by") or t.get("route") or ""),
               "question_group": t.get("question_group") or "",
               "n_fetched": len(fetched), "n_mentioned": len(got),
               "judged": False, "reason": ""}
        if not m["matched"]:
            row["reason"] = "no_truth"           # 不进分子分母
        else:
            if answer:                           # **答案口径**
                row.update(_compare(got, m["entry"]))
                row["judged"] = True
            else:
                row["reason"] = "no_answer"      # 旧 trace（未取正文）⇒ 先重跑 ke_ab_trace
            if fetched:                          # **检索口径**（不看答案；样本更多 ✓）
                fj = _compare(set(fetched), m["entry"])
                row.update({"fetched_correct": fj["correct"],
                            "fetched_missing": len(fj["missing"]),
                            "fetched_extra": len(fj["extra"]),
                            "fetched_forbidden": len(fj["forbidden"])})
                if row["judged"]:
                    row["adoption"] = round(len(got & set(fetched)) / len(fetched), 3)
        out.append(row)

    judged = [r for r in out if r["judged"]]
    stats = {"n_total": len(out), "n_matched": sum(1 for r in out if r["matched"]),
             "n_judged": len(judged),
             "coverage": round(len(judged) / len(out), 3) if out else 0.0,
             "correct": sum(1 for r in judged if r["correct"]),
             "by_arm": {}, "matched_by": {}, "reasons": {}}
    if judged:
        stats["accuracy"] = round(stats["correct"] / len(judged), 3)
        stats["f1_avg"] = round(sum(r["f1"] for r in judged) / len(judged), 3)
    # 2026-10-25（P3-b）：**检索口径**（只看取到的规则，不看答案）+ **采纳率**
    fetched_rows = [r for r in out if "fetched_correct" in r]
    stats["n_fetched_judged"] = len(fetched_rows)
    stats["by_route"] = {}
    for r in fetched_rows:
        k = r.get("route") or "(未记)"
        d = stats["by_route"].setdefault(k, {"n": 0, "correct": 0})
        d["n"] += 1
        d["correct"] += 1 if r["fetched_correct"] else 0
    if fetched_rows:
        stats["fetched_correct"] = sum(1 for r in fetched_rows if r["fetched_correct"])
        stats["fetched_accuracy"] = round(stats["fetched_correct"] / len(fetched_rows), 3)
    adopt = [r["adoption"] for r in out if "adoption" in r]
    if adopt:
        stats["adoption_avg"] = round(sum(adopt) / len(adopt), 3)
    # 2026-10-25（P3-c）：**按问题组去重** —— 同一编号/同一组的多轮只算一个样本（组等权）。
    # 为什么必须给：同一题跑 3 轮会让 n 看着大 3 倍，但**不是 3 个独立样本** ✗。
    groups: dict = {}
    for r in judged:
        # 2026-10-25（修）：**去重单元 = (真值题, 业务编号)** —— 确定性、不依赖"连没连到遥测"：
        #   · 同编号 ⇒ 同一次任务的多轮（合并 ✓）
        #   · 无编号 ⇒ 同一真值题即同一问（合并 ✓；不同措辞都映射到同一真值）
        qid = (r.get("qid") or "").strip().lower()
        gid = "%s|%s" % (r.get("entry_id") or "?", qid)
        d = groups.setdefault(gid, {"entry": r.get("entry_id") or "", "n": 0, "correct": 0})
        d["n"] += 1
        d["correct"] += 1 if r["correct"] else 0
    stats["n_groups"] = len(groups)
    if groups:
        stats["group_accuracy"] = round(
            sum(d["correct"] / d["n"] for d in groups.values()) / len(groups), 3)
        stats["groups_all_correct"] = sum(1 for d in groups.values() if d["correct"] == d["n"])
        stats["groups_none_correct"] = sum(1 for d in groups.values() if d["correct"] == 0)
    out_groups = [dict(gid=k, **v) for k, v in groups.items()]
    for r in out:
        k = r["matched_by"] or "none"
        stats["matched_by"][k] = stats["matched_by"].get(k, 0) + 1
        if r["reason"]:
            stats["reasons"][r["reason"]] = stats["reasons"].get(r["reason"], 0) + 1
    for r in judged:
        a = stats["by_arm"].setdefault(r["variant"], {"n": 0, "correct": 0})
        a["n"] += 1
        a["correct"] += 1 if r["correct"] else 0
    sweep = []                                   # 阈值敏感性（结论对阈值有多敏感）
    for thr in (0.20, 0.25, 0.30, 0.35, 0.40, 0.50):
        n = 0
        for t in rows_in:
            qc = L._question_core(t.get("question") or "")
            if not qc:
                continue
            hit = any(_entry_score(qc, e)["ok"] for e in (truth.get("questions") or []))
            if not hit:
                hit = any(_sim_of(qc, e) >= thr for e in (truth.get("questions") or []))
            n += 1 if hit else 0
        sweep.append({"threshold": thr, "n_matched": n})
    return {"rows": out, "stats": stats, "sweep": sweep, "groups": out_groups,
            "truth": {"path": truth.get("_path") or truth_path,
                      "version": truth.get("version") or "",
                      "n": len(truth.get("questions") or [])},
            "dict_size": len(dictionary)}


# ---------------------------------------------------------------------------
# 渲染（报告附录：**逐题清单 + 打分 + 影响的指标**）
# ---------------------------------------------------------------------------
def _cell(text: str, n: int = 28) -> str:
    s = str(text or "").replace("|", "\\|").replace("\n", " ")
    return (s[:n] + "…") if len(s) > n else s


def _impact(r: dict) -> str:
    """这一行**影响哪些指标** —— 让读者自己决定采信到什么程度。"""
    if not r.get("judged"):
        return "仅 coverage（不计入正确率）"
    bits = ["正确率（计入分母）", "F1"]
    if r.get("forbidden"):
        bits.append("**越界命中断言**")
    if r.get("missing"):
        bits.append("漏项断言")
    if r.get("variant") in ("hint", "plain"):
        bits.append("配对比较（%s 臂）" % r.get("variant"))
    return "；".join(bits)


def render_md(result: dict, route_stats: dict | None = None) -> str:
    """报告附录 §7：真值覆盖 / 正确率 / **逐题清单** / 阈值敏感性 / 采信提示。"""
    st = result.get("stats") or {}
    rows = result.get("rows") or []
    md = ["", "## 8. 真值判分（结果正确率）", "",
          "> 真值集：`%s`（v%s，%d 题）｜规则词典 %d 条｜**未匹配的问题不进分子分母** ✓"
          % (result.get("truth", {}).get("path") or "-",
             result.get("truth", {}).get("version") or "-",
             int(result.get("truth", {}).get("n") or 0), int(result.get("dict_size") or 0)), ""]
    md += ["| 项 | 值 | 说明 |", "|---|---|---|",
           "| 会话轮次（trace） | %d | 真实问答条数 |" % int(st.get("n_total") or 0),
           "| 匹配到真值 | %d | 其中判分 %d 条 |" % (int(st.get("n_matched") or 0),
                                                int(st.get("n_judged") or 0)),
           "| 覆盖率 coverage | %s | 判分条数 / 全部（**正确率分母**）|" % st.get("coverage"),
           "| 正确率 accuracy | %s | 严格集合：无缺、无多、无越界 |" % st.get("accuracy", "—"),
           "| 平均 F1 | %s | 部分正确的松口径 |" % st.get("f1_avg", "—"),
           "| 检索口径正确率 | %s | 本轮**取到的规则** vs 真值（n=%d，不看答案）|"
           % (st.get("fetched_accuracy", "—"), int(st.get("n_fetched_judged") or 0)),
           "| 答案采纳率 | %s | 答案提到的规则 ∩ 取到的规则 ÷ 取到的 |"
           % st.get("adoption_avg", "—"),
           "| **按问题组去重正确率** | %s | 组=(真值题, 编号)；组等权；**有效 n = %d 组** |"
           % (st.get("group_accuracy", "—"), int(st.get("n_groups") or 0)), ""]
    if st.get("matched_by"):
        md += ["**匹配判据分布**（`anchor/qid` 最稳；`sim` 靠相似度兜底 ⇒ 对阈值敏感）：", ""]
        for k, v in sorted(st["matched_by"].items(), key=lambda x: -x[1]):
            md.append("- `%s`：%d" % (k, v))
        md.append("")
    if st.get("reasons"):
        md += ["**未判分原因**：" + "；".join("%s=%d" % (k, v) for k, v in st["reasons"].items()), ""]
    if st.get("by_arm"):
        md += ["| 臂 | 判分条数 | 正确 | 正确率 |", "|---|---|---|---|"]
        for arm, d in sorted(st["by_arm"].items()):
            md.append("| %s | %d | %d | %s |"
                      % (arm, d["n"], d["correct"],
                         round(d["correct"] / d["n"], 3) if d["n"] else "—"))
        md.append("")
    if result.get("groups"):
        md += ["**按问题组去重（同组多轮只算一个样本；组等权）**：", "",
               "| 组 | 真值题 | 判分轮次 | 正确 | 组正确率 |", "|---|---|---|---|---|"]
        for g in result["groups"][:20]:
            md.append("| %s | %s | %d | %d | %s |"
                      % (g["gid"], g["entry"] or "—", g["n"], g["correct"],
                         round(g["correct"] / g["n"], 3) if g["n"] else "—"))
        if len(result["groups"]) > 20:
            md.append("| … | 其余 %d 组 | | | |" % (len(result["groups"]) - 20))
        md += ["", "> **分群口径**：`(真值题, 业务编号)` —— 同编号=同一次任务的多轮；"
               "无编号时同一真值题即同一问（不同措辞会合并）。组**等权**平均，"
               "避免同题重跑把 n 放大。组内全对=%d 组、全错=%d 组、部分对=%d 组。"
               % (int(st.get("groups_all_correct") or 0), int(st.get("groups_none_correct") or 0),
                  int(st.get("n_groups") or 0) - int(st.get("groups_all_correct") or 0)
                  - int(st.get("groups_none_correct") or 0)), ""]
    if route_stats:
        md += ["**按取数路径**（`resolved_by`；空 = 非 rules 模式）：", "",
               "| 路径 | 调用 | 有规则返回 | 空记录 | 规则条数均值 |", "|---|---|---|---|---|"]
        for route, d in sorted(route_stats.items(), key=lambda x: -x[1]["n"]):
            md.append("| %s | %d | %d | %d | %s |"
                      % (route or "(其它)", d["n"], d["hit"], d["empty"],
                         round(d["rules_n"] / d["n"], 1) if d["n"] else "—"))
        md.append("")
    md += ["### 8.1 检索口径（只看本轮取到的规则，不看答案表述）", ""]
    if not st.get("by_route"):
        md += ["> 暂无样本：等新调用带上 `rule_slugs`（本轮之前的遥测没记路径/规则）。", ""]
    else:
        md += ["| 取数路径 | 样本 | 正确 | 正确率 |", "|---|---|---|---|"]
        for route, d in sorted(st["by_route"].items(), key=lambda x: -x[1]["n"]):
            md.append("| %s | %d | %d | %s |"
                      % (route or "(未记)", d["n"], d["correct"],
                         round(d["correct"] / d["n"], 3) if d["n"] else "—"))
        md += ["", "> 这一口径**不受答案表述影响**（智能体即使写成长篇报告也能判），"
               "样本比答案口径多；它衡量「取数路径是否给出了正确的规则集」。", ""]
    md += ["### 8.2 答案口径逐题清单（实际问题 ↔ 命中真值 ↔ 判分）", "",
           "| # | 实际问题 | 真值题 | 判据 | sim | 臂 | 取到/提到 | 判定 | 缺/多/越界 | F1 | 影响指标 |",
           "|---|---|---|---|---|---|---|---|---|---|---|"]
    for i, r in enumerate(rows, 1):
        if r.get("judged"):
            verdict = "✓ 对" if r.get("correct") else "✗ 错"
            detail = "%d/%d/%d" % (len(r.get("missing") or []), len(r.get("extra") or []),
                                   len(r.get("forbidden") or []))
        else:
            verdict = "— 未纳入"
            detail = r.get("reason") or "-"
        md.append("| %d | %s | %s | %s | %s | %s | %d/%d | %s | %s | %s | %s |"
                  % (i, _cell(r.get("question")), r.get("entry_id") or "—",
                     r.get("matched_by") or "—", r.get("sim"), r.get("variant"),
                     int(r.get("n_fetched") or 0), int(r.get("n_mentioned") or 0),
                     verdict, detail, r.get("f1", "-"), _impact(r)))
    md += ["", "> 「缺/多/越界」= 漏掉的期望规则数 / 多给的规则数 / 命中 `forbid`（越界）数；"
           "任一非 0 ⇒ 判错。", ""]
    if result.get("sweep"):
        md += ["### 8.3 阈值敏感性（兜底相似度阈值变化时，可纳入判分的问题数）", "",
               "| 阈值 | 匹配数 |", "|---|---|"]
        for s in result["sweep"]:
            md.append("| %.2f | %d |" % (s["threshold"], s["n_matched"]))
        md += ["", "> 若 `n_matched` 随阈值剧烈变化，说明结论**依赖阈值**；"
               "请以 `判据=anchor/qid` 那部分为主看。", ""]
    md += ["### 8.4 采信提示（读结论前请先看这几条）", "",
           "1. **只对 `✓/✗` 行下结论**：`— 未纳入` 的问题没有真值，只影响 `coverage`，不影响正确率；",
           "2. **检索口径优先看**（§8.1）：它不受答案表述影响、样本更多；答案口径（§8.2）再用来看「取到有没有用上」；",
           "3. **锚点匹配最可靠**（`anchor/qid`）；`sim` 兜底的行请对照 §8.3，"
           "阈值一挪结论若翻转 ⇒ 不宜采信；",
           "4. **越界命中（forbid）直接判错**，即使其余规则全对 —— 这是「越界项永不算」的机器判据；",
           "5. 臂不平衡（§8.2 里 hint/plain 条数悬殊）时，配对比较不成立，只能看总体；",
           "6. 答案抽规则靠**规则名/别名**词典 ⇒ 智能体若只用概括表述（不点规则名）会被判「漏」，"
           "属**判分器的保守偏差**，不是检索错；",
           "7. **同一题的多轮会被重复计数**（每轮一条）⇒ 它们**不是独立样本**："
           "有效独立样本 ≈ 不同 `question_group` 的个数，只宜看趋势、不宜算显著性。", ""]
    return "\n".join(md)
