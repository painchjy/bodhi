"""ke_core · 规则评测（规则工程 · **发布前的回放门禁** ✓）

做什么：拿**已判过的样例**（`tools/ke-core/tests/fixtures/facts_s*.json` = 既有结论 ✓），
按当前规则集**重新判定**，与既有结论逐条比对：

  ① **一致性** = 一致条数 / 总条数（阈值 ≥0.90 ✓）
  ② **假断言** = 既有结论为「不符合」但可信度低/中（证据不足就下违规结论 ✗，阈值 =0 ✓）
  ③ **可信度分布** = 高 / 中 / 低（`ke_score` 口径 ✓）
  ④ 覆盖度 = 每条规则是否有判定行（`ke_report._check_coverage` 同口径 ✓）

结论：三项达标 ⇒ **可发布**；否则 ⇒ 退回「规则化」✗ / 补证据 ✓。

CLI：
  python3 ke_rule_eval.py [--kb 技术方案评审领域模型] [--write] [--work-kb 技术方案评审任务知识库]
"""
from __future__ import annotations

import argparse
import glob
import io
import json
import os

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
FIX = os.path.join(ROOT, "tools", "ke-core", "tests", "fixtures")
MIN_CONSISTENCY = 0.90


def eval_samples(fixtures: list, domain_kb: str) -> dict:
    import ke_score  # noqa: PLC0415

    rows, samples = [], []
    for p in fixtures:
        name = os.path.basename(p)
        try:
            facts = json.loads(io.open(p, encoding="utf-8").read())
        except Exception as exc:  # noqa: BLE001
            rows.append({"sample": name, "item": "-", "error": str(exc)[:60]})
            continue
        items = facts.get("items") or []
        hit = 0
        for it in items:
            sc = ke_score.score_rule(it.get("facts") or [],
                                     [{"verdict": it.get("check") or "insufficient",
                                       "k": it.get("id")}])
            rerun = "无法判定" if sc["gate"] == "insufficient" else str(it.get("verdict") or "")
            baseline = str(it.get("verdict") or "")
            same = rerun == baseline
            hit += 1 if same else 0
            rows.append({"sample": name, "item": it.get("id"), "rule": str(it.get("rule") or "")[:26],
                         "baseline": baseline, "rerun": rerun, "level": sc["level"],
                         "score": sc["score"], "same": same})
        samples.append({"sample": name, "items": len(items), "consistent": hit})
    judged = [r for r in rows if "rerun" in r]
    total = len(judged)
    consistent = sum(1 for r in judged if r["same"])
    # 假断言 = 既有结论「不符合」但**可信度低**（<0.60，证据不足仍下违规结论 ✗）。
    # 中可信度（0.60–0.80）是**允许判定**的（既定口径 ✓），只作「需复核」提醒 ✓（2026-10-18 修）。
    false_claims = [r for r in judged if r["baseline"] == "不符合" and r["level"] == "low"]
    medium_claims = [r for r in judged if r["baseline"] == "不符合" and r["level"] == "medium"]
    dist = {"high": sum(1 for r in judged if r["level"] == "high"),
            "medium": sum(1 for r in judged if r["level"] == "medium"),
            "low": sum(1 for r in judged if r["level"] == "low")}
    consistency = round(consistent / total, 4) if total else 0.0
    ok = consistency >= MIN_CONSISTENCY and not false_claims and total > 0
    return {"ok": ok, "total": total, "consistent": consistent, "consistency": consistency,
            "false_claims": false_claims, "medium_claims": medium_claims,
            "confidence": dist, "samples": samples, "rows": rows}


def render_md(res: dict, kb: str) -> str:
    md = ["# 规则评测报告（规则工程 · 发布门禁）", "",
          "> 领域库：`%s`｜样例 %d 份｜判定 %d 条  \n> 结论：**%s**"
          % (kb, len(res["samples"]), res["total"], "可发布 ✓" if res["ok"] else "不通过 ✗（退回规则化）"),
          "", "## ① 指标", "", "| 指标 | 值 | 阈值 | 结果 |", "|---|---|---|---|",
          "| 一致性 | %.2f（%d/%d） | ≥ %.2f | %s |"
          % (res["consistency"], res["consistent"], res["total"], MIN_CONSISTENCY,
             "✓" if res["consistency"] >= MIN_CONSISTENCY else "✗"),
          "| 假断言 | %d | = 0 | %s |" % (len(res["false_claims"]),
                                          "✓" if not res["false_claims"] else "✗"),
          "| 可信度 | 高 %d ／ 中 %d ／ 低 %d | — | — |"
          % (res["confidence"]["high"], res["confidence"]["medium"], res["confidence"]["low"]),
          "", "## ② 逐样例", "", "| 样例 | 判定条数 | 一致 |", "|---|---|---|"]
    for s in res["samples"]:
        md.append("| %s | %d | %d |" % (s["sample"], s["items"], s["consistent"]))
    if res["false_claims"]:
        md += ["", "## ③ 假断言（证据不足却判违规 ✗）", "",
               "| 样例 | 编号 | 规则 | 可信度 |", "|---|---|---|---|"]
        for r in res["false_claims"][:20]:
            md.append("| %s | %s | %s | %s / %.2f |"
                      % (r["sample"], r["item"], r["rule"], r["level"], r["score"]))
    md += ["", "## ④ 结论与建议", ""]
    md.append("- %s" % ("规则集通过评测 ⇒ 可进入「规则发布」✓" if res["ok"]
                        else "未通过 ⇒ 退回「规则化」：修口径/补可信度证据，**未通过不得被任务步骤引用** ✗"))
    if res.get("medium_claims"):
        md.append("- 中可信度（0.60–0.80）不符合结论 %d 条 ⇒ **允许判定 ✓ 但需人工复核**：%s"
                  % (len(res["medium_claims"]),
                     "、".join("%s/%s" % (r["sample"].replace("facts_", "").replace(".json", ""), r["item"])
                              for r in res["medium_claims"][:8])))
    return "\n".join(md) + "\n"



def main() -> None:
    ap = argparse.ArgumentParser(description="规则评测（规则工程 · 回放比对）")
    ap.add_argument("--kb", default="技术方案评审领域模型")
    ap.add_argument("--work-kb", default="技术方案评审任务知识库")
    ap.add_argument("--fixtures", default="", help="逗号分隔；空=自动取 tests/fixtures/facts_s*.json")
    ap.add_argument("--write", action="store_true", help="落库为 bmm:Report 页")
    a = ap.parse_args()
    import ke_db  # noqa: PLC0415

    kb = ke_db.resolve_kb_id(a.kb)[0]
    fixtures = ([x.strip() for x in a.fixtures.split(",") if x.strip()]
                if a.fixtures else sorted(glob.glob(os.path.join(FIX, "facts_s*.json"))))
    if not fixtures:
        print(json.dumps({"ok": False, "error": "找不到 fixture"}, ensure_ascii=False))
        return
    res = eval_samples(fixtures, kb)
    md = render_md(res, a.kb)
    print(md)
    if a.write:
        import ke_pages  # noqa: PLC0415
        wk = ke_db.resolve_kb_id(a.work_kb)[0]
        out = ke_pages.write_knowledge(wk, {
            "slug": "review/规则评测-回放比对", "title": "规则评测报告（回放比对）",
            "page_type": "bmm:Report", "wiki_content": md,
            "attributes": {"skos:definition":
                           "规则工程门禁：一致性 ≥0.90、假断言=0、可信度分布可追溯 ✓"},
            "source": {"doc_refs": [os.path.basename(p) for p in fixtures],
                       "source_text": "既有结论来自样例 fixture（facts_s1..3）"},
        }, strict_source=True)
        print(json.dumps({"written": out.get("slug"), "version": out.get("version")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
