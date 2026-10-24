"""ke_core · 评测（意图识别准确度 + 流程符合度）✓

口径（2026-10-18 用户要求 ✓）：
  ① **意图识别准确度** = 1 − 越界调用率。越界 = 调**退役工具** ✗ 或 调**不在本流程白名单**的工具 ✗。
  ② **流程符合度** = 环节覆盖率 + **顺序单调性**（环节首现位置必须递增 ✓）。
  ③ 数据源：`logs/mcp_calls_*.log`（每次调用一行 `BEGIN` ✓）。
  ④ 产出：Markdown《评测报告》✓；`--write` 时落 `bmm:Report` 页（版本化 ✓）。

CLI:
  python3 ke_eval.py [--log logs/mcp_calls_20261018.log] [--write] [--kb <目标库名>]
"""
from __future__ import annotations

import argparse
import json
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
LOG_DIR = os.path.join(ROOT, "logs")

# 流程骨架：顺序敏感 ✓（阶段名, 该阶段允许的工具）
STAGES = [
    ("取规则", {"knowledge_lookup", "rules_resolve"}),
    ("取文档", {"doc_outline", "list_knowledge_chunks", "grep_chunks", "wiki_read_page",
                "get_document_info", "image_extract"}),
    ("取事实", {"extract_candidates", "knowledge_lookup"}),
    ("判可信度", {"evidence_score"}),
    ("写报告", {"save_knowledge"}),
]
ALLOWED = set().union(*[s[1] for s in STAGES]) | {
    "ontology_types", "skills", "audit_scan", "audit_plan", "page_attributes_set",
    "clean_alias_noise",
    "job_status", "context_lookup", "kb_export", "import_plan", "import_apply",
    "import_probe", "retag_preview", "retag_apply", "context_scan", "context_page",
    "context_concept_apply", "context_authority", "reference_lookup", "extract_state",
}
RETIRED = {"review_apply", "graph_query", "rules_of_task", "rules_of_policy",
           "service_overview", "rebuild_wiki_from_graph", "link_candidates",
           "list_link_candidates", "resolve_link_candidate", "link_source_session",
           "import_state", "import_refresh", "retag_rollback", "context_concept_rollback",
           "context_authority_apply", "recategorize", "audit_kb", "kb_import"}

LINE = re.compile(r"^\S+ \S+ \+0800\t(?P<tool>\S+)\s+\t\s*(?P<phase>BEGIN|DONE)")


def parse_log(path: str) -> list[dict]:
    """解析调用序列（只取 BEGIN 行 ✓）。"""
    calls: list[dict] = []
    with open(path, encoding="utf-8", errors="ignore") as fh:
        for ln in fh:
            m = LINE.match(ln.rstrip("\n"))
            if not m or m.group("phase") != "BEGIN":
                continue
            args = {}
            am = re.search(r"args=(\{.*?\})\s*(?:\t|$)", ln)
            if am:
                try:
                    args = json.loads(am.group(1))
                except Exception:  # noqa: BLE001
                    pass
            calls.append({"tool": m.group("tool").strip(), "args": args})
    return calls


def _stage_of(call: dict) -> str:
    """按**参数**消歧 ✓：`knowledge_lookup(mode=rules)` = 取规则；keyword/path = 取事实 ✓。
    （2026-10-18 实测：不分参数会把「取事实」判到「取规则」前面 ⇒ 顺序单调误判为「否」✗）
    """
    t = call.get("tool") or ""
    a = call.get("args") or {}
    if t == "knowledge_lookup":
        return "取规则" if str(a.get("mode") or "").lower() == "rules" else "取事实"
    for name, allowed in STAGES:
        if t in allowed and t != "knowledge_lookup":
            return name
    return ""


def evaluate(calls: list[dict]) -> dict:
    n = len(calls)
    if not n:
        return {"ok": False, "error": "没有解析到调用（日志为空或格式不符 ✗）"}
    retired = [c for c in calls if c["tool"] in RETIRED]
    out_of_scope = [c for c in calls if c["tool"] not in ALLOWED and c["tool"] not in RETIRED]
    intent = round(1 - (len(retired) + len(out_of_scope)) / n, 4)

    first, hit = {}, []
    for i, c in enumerate(calls):
        name = _stage_of(c)
        if name and name not in first:
            first[name] = i
            hit.append(name)
    order = [first[k] for k in hit]
    monotonic = all(order[i] < order[i + 1] for i in range(len(order) - 1))
    coverage = round(len(hit) / len(STAGES), 4)
    stage_score = coverage if monotonic else round(coverage * 0.8, 4)
    return {"ok": True, "calls": n, "intent_accuracy": intent,
            "retired_calls": [c["tool"] for c in retired],
            "out_of_scope": [c["tool"] for c in out_of_scope],
            "stages_hit": hit,
            "stages_missing": [s[0] for s in STAGES if s[0] not in hit],
            "monotonic": monotonic, "stage_coverage": coverage,
            "process_fitness": stage_score,
            "verdict": "通过" if (intent >= 0.9 and stage_score >= 0.8 and not retired) else "不通过"}


def render_md(result: dict, log_name: str = "") -> str:
    if not result.get("ok"):
        return "# 评测报告\n\n- 结果：**无法评测**（%s）\n" % result.get("error")
    md = ["# 评测报告（意图识别 + 流程符合度）", "",
          "> 数据源：`%s`｜调用 %d 次｜结论：**%s**" % (log_name, result["calls"], result["verdict"]), "",
          "## ① 意图识别准确度", "",
          "| 指标 | 值 | 口径 |", "|---|---|---|",
          "| 准确度 | **%.2f** | 1 − 越界调用/总调用 ✓ |" % result["intent_accuracy"],
          "| 退役工具调用 | %d | 应为 0 ✗ |" % len(result["retired_calls"]),
          "| 越界调用 | %d | 不在流程白名单 ✗ |" % len(result["out_of_scope"]), "",
          "## ② 流程符合度", "", "| 环节 | 是否命中 |", "|---|---|"]
    for name, _ in STAGES:
        md.append("| %s | %s |" % (name, "✓" if name in result["stages_hit"] else "✗"))
    md += ["", "- 环节覆盖率：**%.2f**（%d/%d）" % (result["stage_coverage"],
                                                len(result["stages_hit"]), len(STAGES)),
           "- 顺序单调：**%s**（环节首现位置递增 ✓）" % ("是" if result["monotonic"] else "否 ✗"),
           "- 流程符合度：**%.2f**" % result["process_fitness"], "",
           "## ③ 结论与建议", "",
           "- 结论：**%s**（阈值：意图 ≥0.90、符合度 ≥0.80、退役调用 =0 ✓）" % result["verdict"]]
    if result["retired_calls"]:
        md.append("- 退役工具：`%s` ⇒ 改用 `knowledge_lookup`（已取代 rules_of_policy / graph_query / review_apply）✓"
                  % "`, `".join(sorted(set(result["retired_calls"]))))
    if result["out_of_scope"]:
        md.append("- 越界工具：`%s` ⇒ 不属于本流程 ✓（请确认是否该调）"
                  % "`, `".join(sorted(set(result["out_of_scope"]))))
    if result["stages_missing"]:
        md.append("- 缺环节：%s ⇒ 检查任务步骤是否漏调 ✓" % "、".join(result["stages_missing"]))
    return "\n".join(md) + "\n"


def main() -> None:
    ap = argparse.ArgumentParser(description="评测：意图识别准确度 + 流程符合度")
    ap.add_argument("--log", default="", help="mcp 调用日志；空=取最新的 mcp_calls_*.log")
    ap.add_argument("--write", action="store_true", help="落库为 bmm:Report 页")
    ap.add_argument("--kb", default="", help="落库目标库")
    ap.add_argument("--last", type=int, default=0, help="只评最近 N 次调用（0=全部 ✓ 单次约 40-60 次）")
    a = ap.parse_args()
    log = a.log
    if not log:
        cand = sorted(f for f in os.listdir(LOG_DIR)
                      if f.startswith("mcp_calls_") and f.endswith(".log"))
        log = os.path.join(LOG_DIR, cand[-1]) if cand else ""
    if not log or not os.path.isfile(log):
        print(json.dumps({"ok": False, "error": "找不到日志"}, ensure_ascii=False))
        return
    calls = parse_log(log)
    if a.last and a.last > 0:
        calls = calls[-a.last:]
    res = evaluate(calls)
    md = render_md(res, os.path.basename(log))
    print(md)
    if a.write and res.get("ok"):
        import ke_db
        import ke_pages
        kb = ke_db.resolve_kb_id(a.kb)[0]
        out = ke_pages.write_knowledge(kb, {
            "slug": "review/评测报告-流程符合度", "title": "评测报告：意图识别与流程符合度",
            "page_type": "bmm:Report", "wiki_content": md,
            "attributes": {"skos:definition":
                           "按 mcp_calls 日志评测：意图识别准确度（越界=0）+ 流程符合度（环节覆盖+顺序）✓"},
            "source": {"doc_refs": [os.path.basename(log)], "source_text": "评测数据源：工具调用日志"},
        }, strict_source=True)
        print(json.dumps({"written": out.get("slug"), "version": out.get("version")},
                         ensure_ascii=False))


if __name__ == "__main__":
    main()
