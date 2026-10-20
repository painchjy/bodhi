"""ke_core · 规则发布（规则工程 · 最后一环 ✓）

门禁（**两道都过才允许发布** ✗ 少一道不发）：
  ① `ke_rule_check.check_rules` —— 字段齐全 / 可机算 / 无歧义 ✓
  ② `ke_rule_eval.eval_samples` —— 一致性 ≥0.90、假断言 =0 ✓
发布动作：
  · 每条规则页 `skos:note` 追加一行发布戳：`已发布 <日期>｜hash <rules_hash>｜体检✓ 评测✓（一致性 x.xx）`✓
  · 回执列出「可被任务步骤引用」的规则清单 ✓（未通过的不得引用 ✗）
CLI：
  python3 ke_rule_publish.py [--kb …] [--spec …] [--apply]   # 默认 dry-run ✓
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import time

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
FIX = os.path.join(ROOT, "tools", "ke-core", "tests", "fixtures")


def evaluate(kb: str, spec: str) -> dict:
    import ke_rule_check
    import ke_rule_eval

    chk = ke_rule_check.check_rules(kb, spec)
    fixtures = sorted(glob.glob(os.path.join(FIX, "facts_s*.json")))
    ev = ke_rule_eval.eval_samples(fixtures, kb) if fixtures else {"ok": False, "total": 0}
    return {"check": chk, "eval": ev, "ok": bool(chk.get("ok")) and bool(ev.get("ok"))}


def publish(kb: str, spec: str, apply: bool = False) -> dict:
    import ke_db
    import ke_fp
    import ke_pages

    res = evaluate(kb, spec)
    if not res["ok"]:
        return {"ok": False, "published": 0, "reason": "门禁未过 ⇒ 不发布 ✗",
                "check_errors": res["check"].get("errors"), "eval": {
                    "consistency": res["eval"].get("consistency"),
                    "false_claims": len(res["eval"].get("false_claims") or [])}}
    import ke_lookup

    lk = ke_lookup.knowledge_lookup(kb, mode="rules", spec=spec)
    # 发布戳：**不再写 rules_hash** ✗（哈希走属性 `bmm:ruleHash` ✓）
    stamp = ("已发布 %s｜体检 ✓｜评测 ✓（一致性 %.2f、假断言 0）"
             % (time.strftime("%Y-%m-%d"), float(res["eval"].get("consistency") or 0)))
    done, failed = [], []
    for r in lk.get("rules") or []:
        slug = str(r.get("slug") or "")
        if not slug:
            continue
        rh = ke_fp.rule_hash(r)          # **规则级**哈希 ✓（本规则内容算出）
        if not apply:
            done.append({"slug": slug, "ruleHash": rh, "dry_run": True})
            continue
        try:
            rows = ke_db.psql_csv(
                "SELECT COALESCE(page_metadata->'ontology'->'attributes'->>'skos:note','') AS n "
                "  FROM wiki_pages WHERE knowledge_base_id = %s AND slug = %s AND deleted_at IS NULL"
                % (ke_db.sql_str(kb), ke_db.sql_str(slug)))
            note = (rows[0]["n"] if rows else "") or ""
            attrs = {"bmm:ruleHash": rh}
            if stamp not in note:
                attrs["skos:note"] = (note.rstrip() + "\n" + stamp).strip()
            ke_pages.set_page_attributes(kb, slug, attrs, dry_run=False)
            done.append({"slug": slug, "ruleHash": rh, "published": True})
        except Exception as exc:  # noqa: BLE001
            failed.append({"slug": slug, "ruleHash": rh, "error": str(exc)[:140]})
    return {"ok": True, "apply": apply, "published": len(done), "failed": failed,
            "stamp": stamp, "rules": done,
            "consistency": res["eval"].get("consistency")}


def main() -> None:
    ap = argparse.ArgumentParser(description="规则发布（门禁：体检 + 评测）")
    ap.add_argument("--kb", default="技术方案评审领域模型")
    ap.add_argument("--spec", default="policy:技术方案逐项评审策略")
    ap.add_argument("--apply", action="store_true", help="真正写库（默认 dry-run ✓）")
    a = ap.parse_args()
    import ke_db

    kb = ke_db.resolve_kb_id(a.kb)[0]
    out = publish(kb, a.spec, apply=a.apply)
    print(json.dumps(out, ensure_ascii=False)[:1500])


if __name__ == "__main__":
    main()
