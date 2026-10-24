"""ke_core · 规则评测台账（**每条规则一组** ✓）

一规则一页（`bmm:Assessment`）：正文 = ① 评测方案（口径/指标/阈值）② 评测结果（累积执行记录）
③ 结论；并尝试与规则页建边 `bmm:isDerivedFrom`（台账 ← 规则 ✓，失败则保留正文关系行 ✓）。

数据来源：`ke_rule_stats.collect()`（解析工作库里每份《技术方案评审报告》的 ③ 表格 ✓）——
**评审报告就是规则执行结果的事实源** ✓，按规则累积后即该规则的评测输入 ✓。

CLI：
  python3 ke_rule_ledger.py [--work-kb …] [--dry-run]
"""
from __future__ import annotations

import argparse
import json
import re

PLAN = """## ① 评测方案（口径固定 ✓）

| 项 | 口径 |
|---|---|
| 样例 | 每份《技术方案评审报告》= 一份样例 ✓（③ 问题清单里的该规则行 = 一次执行结果 ✓） |
| 逐条记录 | 文档 / 判定（符合·不符合·无法判定·不适用）/ 严重度 / 可信度（level + score）/ 文档位置 / 原文摘录 |
| 指标 | 判定分布 ✓ ｜ 可信度分布 ✓ ｜ **假断言**（不符合 且 low）应为 0 ✗ ｜ **待复核**（不符合 且 medium）需人工确认 ✓ |
| 阈值 | 假断言 = 0 ✓；medium 占比过高 ⇒ 补证据（外部源/图注）✗ |
| 版本口径 | 规则 `rules_hash` 变更后**分段统计** ✓（不同 hash 的结果不混算 ✓） |
"""


MARK = "（副本）"      # 规则副本标题后缀（工作库副本 ✓，由 ke_rule_copy.py 同步 ✓）


def _slug(title: str) -> str:
    return re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]+", "-", title).strip("-")[:48] or "rule"


def render(g: dict, ing: dict | None = None) -> str:
    ing = ing or {}
    vd = " ｜ ".join("%s %d" % (k, v) for k, v in sorted(g["verdicts"].items())) or "—"
    lv = " ｜ ".join("%s %d" % (k, v) for k, v in sorted(g["levels"].items())) or "—"
    req = int(ing.get("required") or 0)
    got = int(ing.get("ingested") or 0)
    rate = ("%.0f%%" % (100.0 * got / req)) if req else "—"
    md = ["# 规则评测台账：%s（`agent:RuleEvaluation`）" % g["rule"], "",
          "> 关联规则：本台账统计**该规则**在历次评审中的执行结果 + 知识入库情况 ✓  \n"
          "> 累计：执行 **%d** 次 ｜ 样例 **%d** 份 ｜ 假断言 **%d** ｜ 待复核 **%d** ｜ 知识入库率 **%s**"
          % (g["n"], g["samples"], g["false_claims"], g["review_needed"], rate), "",
          PLAN, "## ② 评测结果（累积 ✓）", "",
          "| 指标 | 值 |", "|---|---|",
          "| 判定分布 | %s |" % vd,
          "| 可信度分布 | %s |" % lv,
          "| 假断言（应为 0） | %d %s |" % (g["false_claims"], "✓" if g["false_claims"] == 0 else "✗"),
          "| 待复核（medium 不符合） | %d |" % g["review_needed"],
          "| **知识入库率** | **%s**（要求 %d ／ 已入库 %d ／ 缺失 %d） |"
          % (rate, req, got, int(ing.get("missing") or 0)),
          "| 自述与事实不符（已入库但查无页 ✗） | %d |" % int(ing.get("mismatch") or 0),
          "| **规则漏判**（人工提交+指定规则 ⇒ 机器漏判 ✗） | %d |" % int(g.get("missed") or 0),
          "| 覆盖样例（报告页） | %s |" % "、".join(s.split("/")[-1][:24] for s in g["reports"][:6]),
          "| rules_hash | %s%s |" % ("、".join(g.get("hashes") or []) or "—（报告未带 hash ✗）",
                                     "  ⚠️ **多版本混算**：请按 hash 分段 ✓" if g.get("hash_mixed") else ""),
          "| **图证据使用率** | %s（有图 %d ／ 用图 %d 🔻未用 %d） |"
          % (("%.0f%%" % (100.0 * int(g.get("img_judged") or 0) / g["img_candidates"]))
             if g.get("img_candidates") else "—",
             int(g.get("img_candidates") or 0), int(g.get("img_judged") or 0),
             int(g.get("img_candidates") or 0) - int(g.get("img_judged") or 0)),
          "", "## ③ 结论与建议", ""]
    if g.get("missed_cases"):
        md += ["## ④ 规则漏判明细（机器漏判 ✓）", "", "| 报告 | 编号 | 人工描述 |", "|---|---|---|"]
        for x in g["missed_cases"][:10]:
            md.append("| %s | %s | %s |" % (x.get("report"), x.get("no"), (x.get("desc") or "—")[:60]))
        md.append("")
    md += ["## ⑤ 关联（跨库引用 ✓）", "",
           "- 规则页：领域库 `bmm/operativebusinessrule/<规则名>`（同名 ✓；用 `knowledge_lookup(mode=\"path\")` 可跳 ✓）",
           "- 评测来源：上式「覆盖样例」里的报告页 ✓（图上间接关系由 `agent:reportAppliesRule` 推导 ✓）",
           "- 知识入库：报告末尾「知识入库情况」节 → `bpmn/dataobject/知识入库清单` ✓"]
    if g["false_claims"]:
        md.append("- **不通过** ✗：存在 %d 条低可信度违规结论 ⇒ 回到「规则化」补证据口径" % g["false_claims"])
    elif g["review_needed"]:
        md.append("- **通过（需复核）** ✓：假断言 0；%d 条 medium 不符合结论请人工确认（多为部署图/外部源 ✓）"
                  % g["review_needed"])
    else:
        md.append("- **通过** ✓：假断言 0，无待复核项")
    if g["locators"]:
        md.append("- 典型定位：%s" % "；".join(str(x)[:30] for x in g["locators"][:3]))
    return "\n".join(md) + "\n"


def _page_type() -> str:
    """台账页类型：优先 `agent:RuleEvaluation`（本体新增子类 ✓）；
    本体还没重编译（类不在索引里）⇒ 退回 `agent:Evaluation` 并提示上传 ✓。"""
    try:
        import ke_ontology
        meta = ke_ontology.class_meta() or {}
        if "agent:RuleEvaluation" in meta:
            return "agent:RuleEvaluation"
    except Exception:  # noqa: BLE001
        pass
    return "agent:Evaluation"


def write_all(work_kb: str, domain_kb: str = "", dry_run: bool = False) -> dict:
    import ke_db
    import ke_pages
    import ke_rule_stats

    pt = _page_type()
    res = ke_rule_stats.collect(work_kb, domain_kb)
    out = {"page_type": pt, "rules": len(res["per_rule"]), "rows": res["rows"],
           "phantom": len(res.get("phantom") or []), "written": [], "edges": [], "failed": []}
    for rule, g in sorted(res["per_rule"].items(), key=lambda kv: -kv[1]["n"]):
        slug = "review/规则台账/" + _slug(rule)
        ing = (res.get("ingest") or {}).get(rule) or {}
        md = render(g, ing)
        if dry_run:
            out["written"].append({"slug": slug, "rule": rule, "chars": len(md)})
            continue
        try:
            r = ke_pages.upsert_page(work_kb, slug, "规则评测台账：%s" % rule, pt, md,
                                     metadata={"ontology": {"attributes": {
                                         "skos:definition": "规则工程台账（一规则一组 ✓）：累积该规则在历次评审中的执行结果 + 知识入库情况"}}},
                                     tag="rule-ledger")
            out["written"].append({"slug": slug, "version": r.get("after_version"), "type": pt})
        except Exception as exc:  # noqa: BLE001
            out["failed"].append({"slug": slug, "error": str(exc)[:140]})
            continue
        if not domain_kb:
            continue
        # 规则页：优先**工作库副本**（同库 ⇒ 边可建 ✓）；没有副本再退回领域库（只能记引用 ✗）
        rows = ke_db.psql_csv(
            "SELECT slug FROM wiki_pages WHERE knowledge_base_id = %s AND deleted_at IS NULL "
            " AND page_type IN ('bmm:OperativeBusinessRule','bmm:StructuralBusinessRule') "
            " AND title IN (%s, %s) LIMIT 1"
            % (ke_db.sql_str(work_kb), ke_db.sql_str(rule), ke_db.sql_str(rule + MARK)))
        same_kb = bool(rows)
        if not rows and domain_kb:
            rows = ke_db.psql_csv(
                "SELECT slug FROM wiki_pages WHERE knowledge_base_id = %s AND deleted_at IS NULL "
                " AND page_type IN ('bmm:OperativeBusinessRule','bmm:StructuralBusinessRule') "
                " AND title = %s LIMIT 1" % (ke_db.sql_str(domain_kb), ke_db.sql_str(rule)))
        if rows and same_kb:
            try:
                import ke_graph
                ke_graph.add_edge(work_kb, slug, "agent:evaluationOfRule", rows[0]["slug"])
                out["edges"].append({"rel": "agent:evaluationOfRule", "to": rows[0]["slug"], "ok": True})
            except Exception as exc:  # noqa: BLE001
                out["edges"].append({"rel": "agent:evaluationOfRule", "error": str(exc)[:110]})
        elif rows:
            # 跨库：规则只存在领域库 ⇒ 不建边，记引用 ✓（副本由「规则评测」任务负责同步 ✓）
            out.setdefault("cross_refs", []).append({"rel": "agent:evaluationOfRule",
                                                     "rule_page": rows[0]["slug"],
                                                     "hint": "缺工作库副本 ⇒ 跑 ke_rule_copy.py 同步 ✓"})
        # 说明（2026-10-19 用户口径 ✓）：**不再建 evaluationEvidenceReport** ✗ ——
        # 「评测 ← 报告」的间接关系靠 `agent:reportAppliesRule`（报告→规则）即可推导 ✓，
        # `knowledge_lookup(mode="path")` 也能快速找到 ✓，多一条边只会增加维护成本 ✗。

    # 幻觉清单（规则名不在领域库 ✗）单独成页
    ph = res.get("phantom") or []
    if ph and not dry_run:
        md = ["# 规则名异常清单（幻觉检测 ✓）", "",
              "> 以下报告行引用了**领域库里不存在**的规则 ✗ —— 视为幻觉，**不纳入评测** ✓", "",
              "| 报告 | 编号 | 报告里写的规则 | 判定 |", "|---|---|---|---|"]
        for x in ph:
            md.append("| %s | %s | %s | %s |" % (x["report"].split("/")[-1][:28], x["no"], x["rule"], x["verdict"]))
        try:
            ke_pages.upsert_page(work_kb, "review/规则名异常清单", "规则名异常清单（幻觉）", "bmm:Report",
                                 "\n".join(md) + "\n", tag="phantom")
            out["phantom_page"] = "review/规则名异常清单"
        except Exception as exc:  # noqa: BLE001
            out["phantom_page_error"] = str(exc)[:120]

    # 无规则对应的人工问题（需分析/给改进建议 ✓）
    nr = res.get("norule") or []
    if nr and not dry_run:
        md = ["# 无规则对应问题清单（人工提交 ✓）", "",
              "> 这些是**人工提交、且没有规则可对应**的问题 ✓ ⇒ 需分析并给改进建议（或据此**新增规则** ✓）", "",
              "| 报告 | 编号 | 问题描述 | 严重度 | 判定 |", "|---|---|---|---|---|"]
        for x in nr:
            md.append("| %s | %s | %s | %s | %s |"
                      % (str(x["report"]).split("/")[-1][:26], x["no"],
                         str(x.get("desc") or "—")[:60], x.get("severity") or "—", x["verdict"]))
        try:
            ke_pages.upsert_page(work_kb, "review/无规则对应问题清单", "无规则对应问题清单（人工提交）",
                                 "bmm:Report", "\n".join(md) + "\n", tag="norule")
            out["norule_page"] = "review/无规则对应问题清单"
            out["norule_count"] = len(nr)
        except Exception as exc:  # noqa: BLE001
            out["norule_page_error"] = str(exc)[:120]

    # 清理陈旧台账（规则已不存在 / 已不在采集结果里 ✗）
    if not dry_run:
        valid = {"review/规则台账/" + _slug(r) for r in res["per_rule"]}
        stale = []
        for row in ke_db.psql_csv(
                "SELECT slug FROM wiki_pages WHERE knowledge_base_id = %s AND deleted_at IS NULL "
                " AND slug LIKE 'review/规则台账/%%'" % ke_db.sql_str(work_kb)):
            if row["slug"] not in valid:
                stale.append(row["slug"])
        if stale:
            try:
                ke_pages.delete_pages(work_kb, stale, dry_run=False)
                out["stale_deleted"] = stale
            except Exception as exc:  # noqa: BLE001
                out["stale_error"] = str(exc)[:120]
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description="规则评测台账（一规则一页）")
    ap.add_argument("--work-kb", default="技术方案评审任务知识库")
    ap.add_argument("--domain-kb", default="技术方案评审领域模型")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    import ke_db

    wk = ke_db.resolve_kb_id(a.work_kb)[0]
    dk = ke_db.resolve_kb_id(a.domain_kb)[0]
    print(json.dumps(write_all(wk, dk, dry_run=a.dry_run), ensure_ascii=False)[:2500])


if __name__ == "__main__":
    main()
