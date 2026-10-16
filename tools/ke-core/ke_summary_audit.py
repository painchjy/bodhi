#!/usr/bin/env python3
"""实例页「摘要/定义」一致性巡检清单（2026-10-16）· **只读**

用途：给出**人工核查用**的清单（不做任何批量修复）——
  ① `summary` 与定义（`skos:definition`→`bmm:definition`）**不一致**（定义非空）；
  ② `summary` **为空**但属性里有定义/备注（可用面板「编辑」保存一次回填）；
  ③ （`--drift`）`page_attributes.drift` 非空的键（元数据/图/正文不一致）。

严格隔离：**本体模型库自动跳过**（本体页 summary = `rdfs:comment`，由"加载页"独立生成）。

用法：
    python3 tools/ke-core/ke_summary_audit.py                 # 全部实例库
    python3 tools/ke-core/ke_summary_audit.py --kb <uuid|名>  # 单个库
    python3 tools/ke-core/ke_summary_audit.py --json          # 机器可读
    python3 tools/ke-core/ke_summary_audit.py --drift         # 附带 drift 明细（较慢）
"""
from __future__ import annotations

import argparse
import json
import sys

import ke_db
import ke_ontology
import ke_pages

DEF_KEYS = ("skos:definition", "bmm:definition")
NOTE_KEYS = ("skos:note", "bmm:description")


def _is_ontology_kb(kb_id: str) -> bool:
    try:
        return bool(ke_ontology.is_ontology_kb(kb_id))
    except Exception:  # noqa: BLE001
        return False


def scan(kb_filter: str = "", with_drift: bool = False, limit: int = 500) -> dict:
    out = {"generated_rows": 0, "kb_skipped": [], "mismatch": [], "empty": [], "drift": [],
           "totals": {}}
    kbs = ke_db.psql_csv("SELECT id, name FROM knowledge_bases WHERE deleted_at IS NULL ORDER BY name")
    for kb in kbs:
        if kb_filter:
            hit = [x for x in kbs if kb_filter in (x["id"] or "") or kb_filter in (x["name"] or "")]
            if kb["id"] not in {x["id"] for x in hit}:
                continue
        if _is_ontology_kb(kb["id"]):
            out["kb_skipped"].append("%s（本体模型库）" % kb["name"])
            continue
        rows = ke_db.psql_csv(
            "SELECT slug, COALESCE(title,'') AS t, COALESCE(page_type,'') AS pt, "
            "       COALESCE(summary,'') AS s, COALESCE(page_metadata::text,'{}') AS meta "
            "  FROM wiki_pages WHERE knowledge_base_id=%s AND deleted_at IS NULL "
            "   AND COALESCE(page_type,'') NOT LIKE 'ontology:%%' "
            " ORDER BY slug" % ke_db.sql_str(kb["id"]))
        for r in rows:
            out["generated_rows"] += 1
            try:
                attrs = (json.loads(r["meta"]).get("ontology") or {}).get("attributes") or {}
            except Exception:  # noqa: BLE001
                attrs = {}
            dfn = next((str(attrs.get(k) or "").strip() for k in DEF_KEYS if str(attrs.get(k) or "").strip()), "")
            note = next((str(attrs.get(k) or "").strip() for k in NOTE_KEYS if str(attrs.get(k) or "").strip()), "")
            summ = (r["s"] or "").strip()
            item = {"kb": kb["name"], "kb_id": kb["id"], "slug": r["slug"], "title": r["t"],
                    "page_type": r["pt"], "summary": summ, "definition": dfn, "note": note}
            if dfn and summ != dfn:
                out["mismatch"].append(item)
            elif not summ and (dfn or note):
                out["empty"].append(item)
            if with_drift:
                d = ke_pages.page_attributes(kb["id"], r["slug"])
                if d.get("drift"):
                    out["drift"].append(dict(item, drift=d.get("drift")))
    out["totals"] = {"pages": out["generated_rows"], "mismatch": len(out["mismatch"]),
                     "empty": len(out["empty"])}
    if with_drift:
        out["totals"]["drift"] = len(out["drift"])
    else:
        out["totals"]["drift"] = "未测（加 --drift）"
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="实例页 summary/定义 一致性清单（只读；跳过本体模型库）")
    ap.add_argument("--kb", default="", help="知识库 uuid（前缀）或名称（可选）")
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    ap.add_argument("--drift", action="store_true", help="附带 drift 明细（较慢）")
    ap.add_argument("--limit", type=int, default=500, help="每类最多打印条数（默认 500）")
    args = ap.parse_args()

    rep = scan(args.kb, with_drift=args.drift, limit=args.limit)
    if args.json:
        print(json.dumps(rep, ensure_ascii=False, indent=2))
        return 0
    print("== 实例页 摘要/定义 一致性清单（只读）==")
    if rep["kb_skipped"]:
        print("  跳过：%s" % "、".join(rep["kb_skipped"]))
    print("  检查页数 = %d" % rep["totals"]["pages"])

    def dump(title: str, rows: list) -> None:
        print("\n-- %s（%d）--" % (title, len(rows)))
        for x in rows[:args.limit]:
            print("  [%s] %s" % (x["kb"][:14], x["slug"]))
            print("     title=%r  type=%s" % (x["title"][:40], x["page_type"]))
            print("     summary=%r" % x["summary"][:60])
            print("     定义   =%r%s" % (x["definition"][:60], ("  备注=%r" % x["note"][:40]) if x["note"] else ""))
            if x.get("drift"):
                print("     drift  =%s" % x["drift"])
    dump("① summary ≠ 定义", rep["mismatch"])
    dump("② summary 空但有定义/备注（面板「编辑」保存一次即回填）", rep["empty"])
    if args.drift:
        dump("③ drift 非空", rep["drift"])
    print("\n合计：%s" % json.dumps(rep["totals"], ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
