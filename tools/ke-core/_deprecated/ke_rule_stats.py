"""ke_core · 规则执行结果采集（规则工程 · **按规则成组** ✓）

数据源：工作库里每份《技术方案评审报告》（`bmm:TechnicalReviewReport`）的 ③ 问题清单表格。
每条表行 = **一次规则执行结果**：{规则, 判定, 严重度, 可信度(level/score), 文档位置, 原文摘录, 报告页}

聚合口径（一规则一组 ✓）：
  · 执行次数 n ／ 判定分布（符合/不符合/无法判定/不适用）
  · 可信度分布（high/medium/low）
  · 「假断言」= 不符合 且 low（证据不足仍下违规结论 ✗）
  · 「待复核」= 不符合 且 medium（允许判定，需人工确认 ✓）
  · 样例数 = 出现过的不同报告页数（每份报告 = 一份样例 ✓）

CLI：
  python3 ke_rule_stats.py [--work-kb 技术方案评审任务知识库] [--json] [--md]
"""
from __future__ import annotations

import argparse
import io
import json
import re

VERDICTS = ("符合", "不符合", "无法判定", "不适用")
# 旧报告用「合规/违规」，新口径是「符合/不符合」⇒ 统一映射 ✓（2026-10-19 实测：6 份报告里 2 份是旧词 ✗）
ALIAS = {"合规": "符合", "违规": "不符合", "待澄清": "无法判定", "合规✓": "符合", "违规✗": "不符合"}


def _cells(line: str) -> list:
    return [c.strip() for c in line.strip().strip("|").split("|")]


def parse_report(content: str) -> list:
    """从报告正文解析 ③ 问题清单表格 → 结果行。

    健壮性（2026-10-19 实测 6 份报告 ✓）：
      · **遇到子块/汇总就停**（`### ③-补充`、`**汇总`、`④` 标题）—— 补充块的小表不是清单行 ✗；
      · `判定` 必须落在四态（含旧词别名 合规/违规 ✓），否则**跳过** ✓；
      · `触发规则` 超过 40 字视为散文（非规则名）⇒ 跳过 ✓。
    """
    rows, header = [], None
    in_sec = False
    for ln in (content or "").splitlines():
        s = ln.strip()
        if re.match(r"^#{2,3}\s*③", s):
            in_sec, header = True, None
            continue
        if not in_sec:
            continue
        if re.match(r"^#{2,3}\s*④", s) or s.startswith("**汇总"):
            break
        if re.match(r"^#{3,}\s*③-补充", s) or "③-补充" in s:
            break
        if not s.startswith("|"):
            continue
        cells = _cells(s)
        if all(set(c) <= set("-: ") for c in cells):
            continue
        if header is None:
            header = cells
            continue
        row = {re.split(r"[（(]", h)[0].strip(): v for h, v in zip(header, cells)}   # 表头归一化 ✓（2026-10-20：实际列名是「规则指纹（rule_hash）」✗ 精确匹配取不到）
        rule = (row.get("触发规则") or row.get("规则") or "").strip()
        verdict = (row.get("判定") or "").strip()
        verdict = ALIAS.get(verdict, verdict)
        # 指纹列是否存在（2026-10-19 ✓）：历史报告没有该列 ⇒ **不得判为人工** ✗（会误记「规则漏判」）
        has_fp_col = any("指纹" in h for h in header)
        fp = (row.get("规则指纹") or row.get("问题指纹") or "").strip().strip("`") if has_fp_col else ""
        no = (row.get("编号") or "").strip()
        conf = row.get("可信度") or ""
        m = re.search(r"(high|medium|low)\s*/\s*([0-9.]+)", conf, re.I)
        lvl = m.group(1).lower() if m else ""
        score = float(m.group(2)) if m else None
        base = {"no": no, "verdict": verdict,
                "severity": (row.get("严重度") or "").strip(), "level": lvl, "score": score,
                "locator": (row.get("文档位置") or "").strip(),
                "quote": (row.get("原文摘录") or "").strip()[:120],
                "desc": (row.get("问题描述") or "").strip()[:80], "fp": fp}
        # 图证据（2026-10-19 ✓）：候选=条目涉及图；已用=摘录里带 `（图证据：图片N）` 或 doc_image 标记
        _txt = "%s %s %s" % (base["quote"], base["locator"], base.get("desc") or "")
        base["img_ref"] = bool(re.search(r"图片\s*\d|图\s*\d|部署图|架构图|拓扑图", _txt))
        base["img_used"] = bool(re.search(r"图证据|doc_image|image_ref", _txt, re.I))
        # ① 指纹空 + 无规则 ⇒ 人工提交、无规则对应 ✓（仅当**存在指纹列**时才判 ✓）
        if has_fp_col and not fp and not rule:
            if verdict in VERDICTS:
                rows.append({**base, "rule": "", "source": "human_norule"})
            continue
        if not rule or len(rule) > 40 or verdict not in VERDICTS:
            continue
        # ② 指纹空 + 有规则 + **存在指纹列** ⇒ 人工提交且指定规则 ⇒ **规则漏判** ✓
        #    无指纹列（历史报告）⇒ 视为**流程生成** ✓（不误判为人工 ✗，2026-10-19 修）
        src = "human_rule" if (has_fp_col and not fp) else "machine"
        rows.append({**base, "rule": rule, "source": src})
    # 整份报告都没有指纹 ⇒ 视为**历史报告/流程生成** ✓（不把旧报告的全空指纹记成「规则漏判」✗）
    if rows and not any(r.get("fp") for r in rows):
        for r in rows:
            if r.get("source") == "human_rule":
                r["source"] = "machine"
    return rows


def parse_ingest(content: str) -> list:
    """解析报告「知识入库情况」节（7 列 ✓）→ 入库记录（2026-10-19 新增）。

    列：知识条目 | 要求来源 | 入库对象 | 入库状态 | 知识库比对 | 抽检结论 | 说明
    """
    rows, header, in_sec = [], None, False
    for ln in (content or "").splitlines():
        s = ln.strip()
        if "知识入库" in s and s.startswith("#"):
            in_sec, header = True, None
            continue
        if in_sec and s.startswith("#") and "知识入库" not in s:
            break
        if not in_sec or not s.startswith("|"):
            continue
        cells = _cells(s)
        if all(set(c) <= set("-: ") for c in cells):
            continue
        if header is None:
            header = cells
            continue
        row = dict(zip(header, cells))
        status = (row.get("入库状态") or "").strip()
        if not status:
            continue
        rows.append({"item": (row.get("知识条目") or "").strip()[:60],
                     "rule": (row.get("要求来源") or "").strip()[:40],
                     "target": (row.get("入库对象") or "").strip(),
                     "status": status[:6],
                     "kb_check": (row.get("知识库比对") or "").strip()[:20],
                     "spot": (row.get("抽检结论") or "").strip()[:20]})
    return rows


def _page_exists(kb: str, slug: str) -> bool:
    import ke_db

    s = (slug or "").strip().strip("`")
    if not s:
        return False
    rows = ke_db.psql_csv(
        "SELECT 1 AS x FROM wiki_pages WHERE knowledge_base_id = %s AND slug = %s AND deleted_at IS NULL "
        "UNION ALL SELECT 1 FROM wiki_pages WHERE knowledge_base_id = %s AND title = %s AND deleted_at IS NULL "
        "LIMIT 1" % (ke_db.sql_str(kb), ke_db.sql_str(s), ke_db.sql_str(kb), ke_db.sql_str(s)))
    return bool(rows)


def collect(work_kb: str, domain_kb: str = "") -> dict:
    """采集 + 幻觉识别 + 知识入库比对（2026-10-19 ✓）。

    幻觉：报告里的规则名**不在领域库规则集** ⇒ 记 `phantom`（不建台账 ✗，单独报警 ✓）。
    入库：解析报告「知识入库情况」表，按 `入库对象` 在**知识库实际查存在性** ✓
    （自述「已入库」但查无页面 ⇒ 记 `self_claim_mismatch` ✗）。
    """
    import ke_db

    real_rules: set = set()
    rule_hashes: dict = {}
    slug_to_title: dict = {}
    if domain_kb:
        for r in ke_db.psql_csv(
                "SELECT title, slug FROM wiki_pages WHERE knowledge_base_id = %s AND deleted_at IS NULL "
                " AND page_type IN ('bmm:OperativeBusinessRule','bmm:StructuralBusinessRule')"
                % ke_db.sql_str(domain_kb)):
            if r.get("title"):
                real_rules.add(str(r["title"]).strip())
            if r.get("slug") and r.get("title"):
                slug_to_title[str(r["slug"]).strip()] = str(r["title"]).strip()
                slug_to_title[str(r["slug"]).strip().split("/")[-1]] = str(r["title"]).strip()
        try:                              # 规则级发布 hash（属性 ✓）
            import ke_fp
            rule_hashes = ke_fp.rule_hash_map(domain_kb)
        except Exception:  # noqa: BLE001
            rule_hashes = {}

    pages = ke_db.psql_csv(
        "SELECT slug, title, COALESCE(content,'') AS content, "
        "       COALESCE(page_metadata::text,'{}') AS meta FROM wiki_pages "
        " WHERE knowledge_base_id = %s AND deleted_at IS NULL "
        " AND page_type = 'bmm:TechnicalReviewReport' ORDER BY updated_at" % ke_db.sql_str(work_kb))
    per_rule: dict = {}
    phantom: list = []
    norule: list = []
    ingest_rows: list = []
    total = 0
    for p in pages:
        try:
            _meta = json.loads(p.get("meta") or "{}")
        except Exception:  # noqa: BLE001
            _meta = {}
        _rhash = str((_meta.get("review") or {}).get("rules_hash") or "")
        for r in parse_report(p["content"]):
            total += 1
            if r.get("source") == "human_norule":
                norule.append({"report": p["slug"], "no": r["no"], "desc": r.get("desc") or r.get("quote"),
                               "severity": r.get("severity"), "verdict": r["verdict"]})
                continue
            if real_rules and r["rule"] not in real_rules:
                # 兼容：报告的「触发规则」列可能写的是 **slug** ✗（000009 实测 4 行）
                mapped = slug_to_title.get(r["rule"]) or slug_to_title.get(str(r["rule"]).split("/")[-1])
                if mapped:
                    r["rule"] = mapped
                else:
                    phantom.append({"report": p["slug"], "no": r["no"], "rule": r["rule"],
                                    "verdict": r["verdict"]})
                    continue
            g = per_rule.setdefault(r["rule"], {
                "rule": r["rule"], "n": 0, "verdicts": {}, "levels": {},
                "false_claims": 0, "review_needed": 0, "reports": set(), "locators": [],
                "missed": 0, "missed_cases": [], "hashes": set()})
            if _rhash:
                g["hashes"].add(_rhash)
            # 优先用规则页**属性** `bmm:ruleHash`（发布值 ✓，2026-10-19）
            _attrh = (rule_hashes or {}).get(r["rule"]) or ""
            if _attrh:
                g["hashes"].add(_attrh)
            if r.get("source") == "human_rule":
                # 人工提交 + 指定规则 ⇒ **规则漏判**（机器漏判 ✗，计入该规则评测）
                g["missed"] += 1
                g["missed_cases"].append({"report": p["slug"].split("/")[-1][:24], "no": r["no"],
                                          "desc": r.get("desc") or "", "verdict": r["verdict"]})
                g["reports"].add(p["slug"])
                continue
            g["n"] += 1
            if r.get("img_ref"):
                g["img_candidates"] = g.get("img_candidates", 0) + 1
                if r.get("img_used"):
                    g["img_judged"] = g.get("img_judged", 0) + 1
                else:
                    g.setdefault("img_unused_cases", []).append(
                        {"report": p["slug"].split("/")[-1][:24], "no": r["no"],
                         "locator": r.get("locator") or ""})
            g["verdicts"][r["verdict"]] = g["verdicts"].get(r["verdict"], 0) + 1
            if r["level"]:
                g["levels"][r["level"]] = g["levels"].get(r["level"], 0) + 1
            if r["verdict"] == "不符合" and r["level"] == "low":
                g["false_claims"] += 1
            if r["verdict"] == "不符合" and r["level"] == "medium":
                g["review_needed"] += 1
            g["reports"].add(p["slug"])
            if r["locator"]:
                g["locators"].append(r["locator"])
        for it in parse_ingest(p["content"]):
            it["report"] = p["slug"]
            it["exists"] = _page_exists(work_kb, it["target"]) or (
                bool(domain_kb) and _page_exists(domain_kb, it["target"]))
            if it["status"].startswith("已入库") and not it["exists"]:
                it["mismatch"] = True
            ingest_rows.append(it)

    ingest: dict = {}
    for it in ingest_rows:
        k = it["rule"] or "（未标来源）"
        e = ingest.setdefault(k, {"required": 0, "ingested": 0, "missing": 0,
                                  "mismatch": 0, "verified": 0})
        e["required"] += 1
        if it["status"].startswith("已入库"):
            e["ingested"] += 1
        elif it["status"].startswith(("缺失", "部分")):
            e["missing"] += 1
        if it.get("mismatch"):
            e["mismatch"] += 1
        if it["exists"]:
            e["verified"] += 1

    for g in per_rule.values():
        g["samples"] = len(g["reports"])
        g["reports"] = sorted(g["reports"])
        g["locators"] = g["locators"][:5]
        g["hashes"] = sorted(g.get("hashes") or [])
        g["hash_mixed"] = len(g["hashes"]) > 1   # 规则改过版 ⇒ 新旧结果不混算 ✓（分段提示）
    return {"reports": len(pages), "rows": total, "per_rule": per_rule,
            "phantom": phantom, "norule": norule, "ingest": ingest, "ingest_rows": ingest_rows}



def render_md(res: dict) -> str:
    md = ["# 规则执行结果台账（按规则成组 ✓）", "",
          "> 数据源：工作库 %d 份评审报告｜累计执行 %d 条" % (res["reports"], res["rows"]), "",
          "## ① 每条规则一组", "",
          "| 规则 | 执行次数 | 样例数 | 判定分布 | 可信度 | 假断言 | 待复核 |",
          "|---|---|---|---|---|---|---|"]
    for rule, g in sorted(res["per_rule"].items(), key=lambda kv: -kv[1]["n"]):
        vd = " / ".join("%s %d" % (k, v) for k, v in sorted(g["verdicts"].items()))
        lv = " / ".join("%s %d" % (k, v) for k, v in sorted(g["levels"].items())) or "—"
        md.append("| %s | %d | %d | %s | %s | %d | %d |"
                  % (rule, g["n"], g["samples"], vd or "—", lv, g["false_claims"], g["review_needed"]))
    md += ["", "## ② 说明", "",
           "- **样例数** = 出现过的不同评审报告数 ✓（一规则在多份报告里的执行结果累积 ✓）",
           "- **假断言** = 不符合 且 可信度 low ✗（应为 0）",
           "- **待复核** = 不符合 且 可信度 medium ✓（允许判定，需人工确认）",
           "- 用途：每份评审报告的 ③ 表格就是**规则执行结果的事实源** ✓，按规则聚合后即该规则的评测输入 ✓"]
    return "\n".join(md) + "\n"


def main() -> None:
    ap = argparse.ArgumentParser(description="规则执行结果采集（按规则成组）")
    ap.add_argument("--work-kb", default="技术方案评审任务知识库")
    ap.add_argument("--json", action="store_true", help="输出 JSON（供落页/对账）")
    a = ap.parse_args()
    import ke_db

    wk = ke_db.resolve_kb_id(a.work_kb)[0]
    res = collect(wk)
    if a.json:
        print(json.dumps(res, ensure_ascii=False, default=list)[:4000])
    else:
        print(render_md(res))


if __name__ == "__main__":
    main()
