"""ke-core · **规则运行期评测回写**（规则工程闭环的最后一环，2026-10-27）。

与既有的 `ke_rule_eval.py` 分工（两者互补，别混）：
  - `ke_rule_eval.py` = **发布前回放门禁**：拿 fixtures 重放 → 一致性/假断言/覆盖度 → 写 bmm:Report；
  - 本模块 = **运行期回写**：每次真实评审后，把台账里该规则的结论统计**追加**到
    `agent:RuleEvaluation`（一规则一页）⇒ 才能回答"这条规则历次表现如何、可否被任务步骤引用"。

写库只走 MCP `save_knowledge(stage="graph")`（闸门生效 ✓）；枚举/阈值读真源（台账契约 + 指标页）。

CLI：
  python3 tools/ke-core/ke_rule_runtime_eval.py --kb <领域库> --rule <规则名> --review-no <编号> [--apply]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.request
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import ke_db  # noqa: E402
import ke_review  # noqa: E402

MCP = os.environ.get("BODHI_MCP_URL", "http://127.0.0.1:8765/mcp")
METRIC_TITLE = "指标 · 规则评测门槛"
METRIC_KB = "6effde80-3ad0-46a1-92b2-76d695e0b58f"   # 企业知识管理领域（指标页所在库）
KEEP_EVALS = 20


def metric_spec(kb_id: str = "") -> dict:
    """指标页 ```metricSpec 块（阈值/等级/权重）；读不到 ⇒ {}（回退 0.60）。

    ⚠️ 指标页在**知识管理领域库**（`METRIC_KB`），规则在**领域模型库** ⇒ 两个库参数分开：
    本函数按指标库读；写评测页则写**规则所在库**（否则 `agent:evaluationOfRule` 边跨库解析不到 ✗）。
    """
    kb = kb_id or METRIC_KB
    rows = ke_db.psql_csv(
        "SELECT COALESCE(page_metadata->'ontology'->'attributes'->>'skos:note','') AS note "
        "FROM wiki_pages WHERE deleted_at IS NULL AND knowledge_base_id = %s "
        "AND page_type='agent:Metric' AND (title = %s OR slug LIKE %s) LIMIT 1"
        % (ke_db.sql_str(kb), ke_db.sql_str(METRIC_TITLE), ke_db.sql_str("%规则评测门槛%")))
    note = (rows[0].get("note") if rows else "") or ""
    m = re.search(r"(?ms)^```metricSpec[^\n]*\n(.*?)^```", note)
    if not m:
        return {}
    try:
        return json.loads(m.group(1))
    except Exception:  # noqa: BLE001
        return {}


def summarize(items: list, rule_title: str, verdicts: list,
              threshold: float = 0.60, levels: dict | None = None,
              applies_when: str = "always") -> dict:
    """台账 items → 该规则的一次评测统计（**纯函数**，可单测）。

    `evidence_traceable_rate` = `quote` 与 `doc_loc` **都非空**的条目占比（证据可回溯）；
    `veto_count` = 结论「不符合」∧ 严重度「严重」的条数；
    `gate`：有结论时按阈值判 ok/insufficient；**无条目**时按 `applies_when` 判
    `not_applicable`（条件不成立）或 `not_triggered`（该适用却没被触发 ⇒ 要查是不是漏判 ✓）。
    """
    lv = levels or {"high": 0.80, "medium": 0.60}
    rows = [x for x in (items or [])
            if str(x.get("rule") or "").strip() == str(rule_title or "").strip()]
    counts = {v: 0 for v in (verdicts or [])}
    for x in rows:
        v = str(x.get("verdict") or "").strip()
        if v in counts:
            counts[v] += 1
    traced = sum(1 for x in rows
                 if str(x.get("quote") or "").strip() and str(x.get("doc_loc") or "").strip())
    veto = sum(1 for x in rows
               if str(x.get("verdict") or "").strip() == "不符合"
               and str(x.get("severity") or "").strip() == "严重")
    rate = (traced / len(rows)) if rows else 0.0
    if not rows:
        gate = "not_applicable" if str(applies_when or "").strip() not in ("", "always") else "not_triggered"
    else:
        gate = "ok" if rate >= threshold else "insufficient"
    return {"rule": rule_title, "triggered": bool(rows), "items": len(rows),
            "verdicts": counts, "evidence_traceable_rate": round(rate, 3),
            "veto_count": veto,
            "gate": gate,
            "level": (("high" if rate >= float(lv.get("high", 0.80))
                       else "medium" if rate >= float(lv.get("medium", 0.60)) else "low")
                      if rows else "n/a"),
            "threshold": threshold,
            "applies_when": str(applies_when or "always")}


# ---------------------------------------------------------------------------
# 台账读 / 规则契约 / 评测页读写（写库只走 MCP ✓）
# ---------------------------------------------------------------------------
def _mcp(name: str, args: dict) -> dict:
    body = {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
            "params": {"name": name, "arguments": args}}
    req = urllib.request.Request(
        MCP, data=json.dumps(body, ensure_ascii=False).encode(),
        headers={"Content-Type": "application/json",
                 "Accept": "application/json, text/event-stream"})
    raw = urllib.request.urlopen(req, timeout=180).read().decode()
    d = None
    for line in raw.splitlines():
        if line.startswith("data: "):
            d = json.loads(line[6:])
            break
    d = d or json.loads(raw)
    txt = ((d.get("result") or {}).get("content") or [{}])[0].get("text") or "{}"
    try:
        return json.loads(txt)
    except Exception:  # noqa: BLE001
        return {"_raw": txt[:600]}


def ledger_items(review_no: str) -> list:
    """台账条目（经 MCP 工具读，不直连文件 ✓）。"""
    return list((_mcp("ext_issue_list", {"mode": "query", "action": "list",
                                         "review_no": review_no, "view": "rows"})
                 .get("rows") or []))


def _slug_of_rule(kb_id: str, rule: str) -> str:
    """规则标题 / slug 片段 → 规则页 slug（规则类页面的 page_type 都含 `Rule`）。"""
    got = (rule or "").strip()
    if not got:
        return ""
    rows = ke_db.psql_csv(
        "SELECT slug FROM wiki_pages WHERE deleted_at IS NULL AND knowledge_base_id=%s "
        "AND page_type LIKE '%%Rule%%' AND (title = %s OR slug = %s OR slug LIKE %s) "
        "ORDER BY (title = %s) DESC, length(slug) LIMIT 1"
        % (ke_db.sql_str(kb_id), ke_db.sql_str(got), ke_db.sql_str(got),
           ke_db.sql_str("%" + got + "%"), ke_db.sql_str(got)))
    return str(rows[0].get("slug") or "") if rows else ""


def rule_contract(kb_id: str, rule: str) -> dict:
    slug = _slug_of_rule(kb_id, rule) or rule
    rules = ke_review.rules_resolve(kb_id, "direct:%s" % slug, 5).get("rules") or []
    return rules[0] if rules else {}


def _prev_note(kb_id: str, rule_title: str) -> str:
    rows = ke_db.psql_csv(
        "SELECT COALESCE(page_metadata->'ontology'->'attributes'->>'skos:note','') AS note "
        "FROM wiki_pages WHERE deleted_at IS NULL AND page_type='agent:RuleEvaluation' "
        "AND title = %s LIMIT 1" % ke_db.sql_str("规则评测 · %s" % rule_title))
    return (rows[0].get("note") if rows else "") or ""


def _parse_evals(note: str) -> list:
    m = re.search(r"(?ms)^```ruleEval[^\n]*\n(.*?)^```", note or "")
    if not m:
        return []
    try:
        return list((json.loads(m.group(1)) or {}).get("evaluations") or [])
    except Exception:  # noqa: BLE001
        return []


def build_node(rule_title: str, stat: dict, prev: str = "") -> dict:
    """构造 `agent:RuleEvaluation` 节点：本次统计**追加**进 `evaluations[]`（只留最近 N 次）。"""
    hist = _parse_evals(prev)
    hist.append({"at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                 "review_no": stat.get("review_no") or "",
                 "items": stat["items"], "verdicts": stat["verdicts"],
                 "evidence_traceable_rate": stat["evidence_traceable_rate"],
                 "veto_count": stat["veto_count"], "gate": stat["gate"], "level": stat["level"]})
    keep = hist[-KEEP_EVALS:]
    n = sum(h.get("items") or 0 for h in keep)
    bad = sum((h.get("verdicts") or {}).get("不符合", 0) for h in keep)
    trace = (sum((h.get("evidence_traceable_rate") or 0) * (h.get("items") or 0) for h in keep) / n
             if n else 0.0)
    summary = {"reviews": len(hist), "items": n, "not_pass": bad,
               "evidence_traceable_rate": round(trace, 3),
               "veto_count": sum(h.get("veto_count") or 0 for h in keep),
               "triggered_reviews": sum(1 for h in keep if (h.get("items") or 0) > 0)}
    note = ("【规则评测】一规则一页；每次评审把台账结论**追加**进 `evaluations[]`（只留最近 %d 次）\n"
            "```ruleEval\n%s\n```\n最近一次：%s（%s，%s 条，gate=%s）"
            % (KEEP_EVALS, json.dumps({"summary": summary, "evaluations": keep},
                                      ensure_ascii=False, indent=1),
               keep[-1]["at"], keep[-1]["review_no"], keep[-1]["items"], keep[-1]["gate"]))
    defn = ("规则「%s」的运行期评测台账：累计 %d 次评审 / %d 条结论（不符合 %d、否决 %d），"
            "证据可回溯率 %.2f ⇒ 判断该规则是否可信、可否被任务步骤引用 ✓"
            % (rule_title, summary["reviews"], n, bad, summary["veto_count"],
               summary["evidence_traceable_rate"]))
    return {"type": "agent:RuleEvaluation", "name": "规则评测 · %s" % rule_title,
            "definition": defn,
            "attributes": {"skos:definition": defn, "skos:note": note}}


def _slugify(title: str) -> str:
    return re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]+", "", title or "") or "rule"


def _render_md(rule_title: str, stat: dict, node: dict) -> str:
    """文档类页面必须有正文（`agent:RuleEvaluation` 走 report 阶段）⇒ 生成可读评测页。"""
    evals = _parse_evals((node.get("attributes") or {}).get("skos:note") or "")
    lines = ["# %s" % node["name"], "",
             "> 规则：`%s`（ruleHash `%s`）｜执行级别：%s｜适用条件：`%s`"
             % (stat.get("rule_slug") or "-", stat.get("rule_hash") or "-",
                stat.get("enforcement") or "-", stat.get("applies_when") or "always"),
             "> 门槛：%.2f（来源：%s）｜本次评审：%s｜gate=`%s`"
             % (float(stat.get("threshold") or 0.6), stat.get("threshold_source") or "-",
                stat.get("review_no") or "-", stat.get("gate") or "-"), "",
             "## 本次结论分布", "",
             "| 判定 | 条数 |", "|---|---|"]
    for k, v in (stat.get("verdicts") or {}).items():
        lines.append("| %s | %d |" % (k, v))
    lines += ["", "| 指标 | 值 |", "|---|---|",
              "| 条目数 | %d |" % int(stat.get("items") or 0),
              "| 证据可回溯率 | %.2f |" % float(stat.get("evidence_traceable_rate") or 0),
              "| 否决数（不符合∧严重） | %d |" % int(stat.get("veto_count") or 0), "",
              "## 历次评测（最近 %d 次）" % KEEP_EVALS, "",
              "| 时间 | 评审编号 | 条目 | 不符合 | 可回溯率 | gate |",
              "|---|---|---|---|---|---|"]
    for e in evals:
        lines.append("| %s | %s | %d | %d | %.2f | %s |"
                     % (e.get("at", ""), e.get("review_no", ""), e.get("items") or 0,
                        (e.get("verdicts") or {}).get("不符合", 0),
                        float(e.get("evidence_traceable_rate") or 0), e.get("gate") or ""))
    lines += ["", "## 口径", "",
              "- 判定枚举与台账同源（符合/不符合/不适用/无法判定）；证据不足只能「无法判定」✗ 不得写不符合；",
              "- `gate=not_applicable` = 本次方案不满足 `applies_when`（规则不适用）；`not_triggered` = 该适用却没判 ⇒ 查漏判；",
              "- 数据来源：台账 `ext_issue_list`（一次批量、rev 只 +1）；本页只做**累计观测**，不存业务数据。"]
    return "\n".join(lines) + "\n"


def apply_eval(kb_id: str, rule_title: str, stat: dict, session_no: str = "",
               actor: str = "规则评测") -> dict:
    """写库：`stage="report"`（`agent:RuleEvaluation` 是**文档类**，graph 阶段会被跳过 ✗）。"""
    node = build_node(rule_title, stat, _prev_note(kb_id, rule_title))
    return _mcp("save_knowledge", {
        "kb_id": kb_id, "stage": "report", "mode": "apply",
        "session_no": session_no, "actor": actor,
        "report": {"slug": "agent/ruleevaluation/%s" % _slugify(rule_title),
                   "title": node["name"], "page_type": "agent:RuleEvaluation",
                   "content_md": _render_md(rule_title, stat, node),
                   "attributes": node["attributes"]}})


def _session_of_rule(kb_id: str, slug: str) -> str:
    """规则页溯源会话（`bmm:sourceLocator = session/<会话>/pN`）⇒ 写库审计用。"""
    if not slug:
        return ""
    rows = ke_db.psql_csv(
        "SELECT COALESCE(page_metadata->'ontology'->'attributes'->>'bmm:sourceLocator','') AS loc "
        "FROM wiki_pages WHERE deleted_at IS NULL AND knowledge_base_id=%s AND slug=%s"
        % (ke_db.sql_str(kb_id), ke_db.sql_str(slug)))
    loc = (rows[0].get("loc") if rows else "") or ""
    return loc.split("/")[1] if loc.startswith("session/") and "/" in loc else ""


def main() -> int:
    ap = argparse.ArgumentParser(description="规则运行期评测回写（台账 → agent:RuleEvaluation）")
    ap.add_argument("--kb", required=True, help="领域库（规则所在库）")
    ap.add_argument("--rule", required=True, help="规则标题（= 台账 rule 列的值）")
    ap.add_argument("--review-no", required=True, help="评审编号（台账分片键）")
    ap.add_argument("--session-no", default="", help="会话编号（审计用；缺省取规则页溯源会话）")
    ap.add_argument("--threshold", type=float, default=0.0,
                    help="可信度阈值（缺省：指标页 > 规则 knowledgeSpec.threshold > 0.60）")
    ap.add_argument("--apply", action="store_true", help="真写库（默认只打印）")
    a = ap.parse_args()

    kb, kb_name, _ = ke_db.resolve_kb_id(a.kb)
    rc_ = rule_contract(kb, a.rule)          # 先取规则契约（阈值可能写在 knowledgeSpec 里）
    spec = rc_.get("knowledge_spec") or {}
    ms = metric_spec()
    thr = (a.threshold or float(ms.get("threshold") or 0.0)
           or float(spec.get("threshold") or 0.0) or 0.60)
    src = ("命令行 --threshold" if a.threshold else
           "agent/metric/指标规则评测门槛" if ms.get("threshold") else
           "规则页 knowledgeSpec.threshold" if spec.get("threshold") else "默认 0.60")
    stat = summarize(ledger_items(a.review_no), a.rule, ke_review._ledger_enums()["verdict"],
                     threshold=thr, levels=ms.get("levels"),
                     applies_when=str(rc_.get("applies_when") or "always"))
    stat["review_no"] = a.review_no
    stat.update({"rule_slug": rc_.get("slug") or "", "rule_hash": rc_.get("ruleHash") or "",
                 "enforcement": rc_.get("enforcement") or "",
                 "threshold_source": src})
    out = {"kb": {"id": kb, "name": kb_name}, "stat": stat}
    if a.apply:
        sess = a.session_no or _session_of_rule(kb, stat["rule_slug"])
        out["write"] = apply_eval(kb, a.rule, stat, sess, actor="规则评测（%s）" % a.review_no)
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
