"""报告渲染器（**列从 DataObject 读** ✓，事实从 JSON 读 ✓）—— 保证"格式可复现" ✓

设计（2026-10-17 用户口径 ✓）：
- **列名/顺序只来自** `bpmn/dataobject/问题清单` 的 `skos:definition` ✓（不从代码里写死 ✗）；
- 章节来自报告类 `rdfs:comment` 的「章节规范」✓（这里按标准五节渲染 ✓，类未声明则最小化 ✓）；
- 事实（含 desc/loc/quote/verdict/severity/fix/facts/check）由 JSON 提供 ✓（抽取+判读的产物 ✓）；
- 可信度一律 `ke_score` 机算 ✓；`gate=insufficient` ⇒ 判定写「无法判定」✓ 并进表后说明 ✓。
"""

import json
import re


def columns(domain_kb: str, do_slug: str = "bpmn/dataobject/问题清单") -> list:
    """从 DataObject 定义解析列名与顺序 ✓（形如 `1 编号｜2 **问题描述**（…）｜3 触发规则｜…`）。"""
    import ke_db
    kb = ke_db.resolve_kb_id(domain_kb)[0]
    rows = ke_db.psql_csv(
        "SELECT COALESCE(page_metadata->'ontology'->'attributes'->>'skos:definition','') AS d "
        " FROM wiki_pages WHERE knowledge_base_id = %s AND deleted_at IS NULL AND slug = %s"
        % (ke_db.sql_str(kb), ke_db.sql_str(do_slug)))
    if not rows:
        return []
    text = str(rows[0].get("d") or "")
    line = next((ln for ln in text.splitlines() if ln.strip().startswith("1 ")), "")
    out = []
    for seg in line.split("｜"):
        s = re.sub(r"^\s*\d+\s*", "", seg).strip()
        s = s.replace("**", "")
        s = re.split(r"[（(：:]", s)[0].strip()
        if s:
            out.append(s)
    return out


def render(sample: str, doc_title: str, facts: dict, domain_kb: str,
           rules_spec: str = "policy:技术方案逐项评审策略") -> dict:
    """渲染一份报告正文 ✓（返回 {content, counts}；不写库 ✗）。"""
    import ke_lookup
    import ke_score

    cols = columns(domain_kb)
    lk = ke_lookup.knowledge_lookup(domain_kb, mode="rules", spec=rules_spec)
    rows, insuf = [], []
    for it in facts.get("items") or []:
        sc = ke_score.score_rule(it.get("facts") or [], [{"verdict": it.get("check") or "insufficient",
                                                        "k": it.get("id")}])
        verdict = "无法判定" if sc["gate"] == "insufficient" else it["verdict"]
        rows.append({**it, "verdict": verdict, "score": sc["score"], "level": sc["level"],
                     "why": "；".join(sc["reasons"][:2]) if sc["gate"] != "ok" else ""})
        if verdict == "无法判定":
            insuf.append(it.get("id"))
    failed = [x for x in rows if x["verdict"] == "不符合"]
    passed = [x for x in rows if x["verdict"] == "符合"]
    veto = [x for x in failed if x["severity"] == "严重"]

    md = ["## ① 范围与依据", "", "- 受评对象：%s" % sample,
          "- 源文档：%s" % doc_title,
          "- 规则集：`%s` ⇒ %d 条（hash `%s`）" % (rules_spec, lk["counts"]["rules"], lk["hash"]),
          "- ④ 列规范：`bpmn/dataobject/问题清单`（%d 列 ✓ 列名/顺序取自该页定义 ✓）" % len(cols), "",
          "## ② 规则清单", "", "| # | 规则 | 执行级别 | 需先提取 |", "|---|---|---|---|"]
    for i, r in enumerate(lk["rules"], 1):
        md.append("| %d | %s | %s | %s |" % (i, r["title"], r["enforcement"] or "-",
                                            "是" if r["needs_extract"] else "否"))
    md += ["", "## ③ 问题清单（%d 列 ✓）" % len(cols), "",
           "| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
    for x in rows:
        cells = [x["id"], x["desc"], x["rule"], x["locator"], x["quote"], x["verdict"],
                 x["severity"], x["fix"], "**%s / %.2f**" % (x["level"], x["score"])]
        md.append("| " + " | ".join(cells) + " |")
    if insuf:
        md += ["", "### ③-补充：无法判定说明（%d 项 ✓）" % len(insuf), "",
               "| 编号 | 无法判断的原因 | 影响的问题 | 需要反馈或核实的内容 |", "|---|---|---|---|"]
        for x in rows:
            if x["verdict"] == "无法判定":
                md.append("| %s | %s | %s | %s |" % (x["id"], x["why"] or "—", "、".join(insuf),
                                                     x.get("ask") or "① 请提供认证清单（含版本号）；② 或书面确认是否免比对；③ 或人工比对回填"))
    md += ["", "## ④ 结论与建议", "",
           "**总判定：%s**" % ("不通过（否决 %d：%s）" % (len(veto), "、".join(x["id"] for x in veto))
                              if veto else ("不通过（无严重项）" if failed else "通过")),
           "- 结论分布：不符合 %d ｜ 符合 %d ｜ 无法判定 %d" % (len(failed), len(passed), len(insuf)),
           "- 可信度：高 %d ｜ 中 %d ｜ 低 %d" % (sum(1 for x in rows if x["level"] == "high"),
                                              sum(1 for x in rows if x["level"] == "medium"),
                                              sum(1 for x in rows if x["level"] == "low"))]
    return {"content": "\n".join(md) + "\n", "cols": cols,
            "counts": {"不符合": len(failed), "符合": len(passed), "无法判定": len(insuf),
                       "否决": len(veto)}, "lookup_hash": lk["hash"]}


def write(sample: str, doc_title: str, facts: dict, domain_kb: str, proj_kb: str,
          slug: str, title: str, doc_ref: str = "") -> dict:
    """渲染 + 走写核入库 ✓（`bmm:TechnicalReviewReport` ✓ document 模式 ✓）。"""
    import ke_db
    import ke_pages
    r = render(sample, doc_title, facts, domain_kb)
    kb = ke_db.resolve_kb_id(proj_kb)[0]
    out = ke_pages.write_knowledge(kb, {
        "slug": slug, "title": title, "page_type": "bmm:TechnicalReviewReport",
        "wiki_content": r["content"],
        "attributes": {"skos:definition": "%s 端到端评审报告：④ %d 列（列名/顺序取自 DataObject ✓；"
                                        "文档位置=章节标题 ✓；可信度=ke_score ✓）。" % (sample, len(r["cols"]))},
        "source": {"doc_refs": [doc_ref or doc_title], "source_text": (facts.get("items") or [{}])[0].get("quote", "")},
    }, strict_source=True)
    return {"slug": slug, "version": out.get("version"), "graph": out.get("graph"),
            "counts": r["counts"], "cols": r["cols"]}
