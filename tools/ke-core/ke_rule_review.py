"""ke_core · 规则工程一键闭环（体检 → 副本 → 采集台账 → 评测 → 可选发布 ✓）

顺序（2026-10-19 用户口径 ✓）：
  ① `ke_rule_check`  规则体检（字段齐全/可机算/无歧义/可回溯）—— 不过 ⇒ 停 ✗
  ② `ke_rule_copy`   规则副本同步（工作库 ✓，报告/评测要同库建边 ✓）
  ③ `ke_rule_ledger` 台账（一规则一页 `agent:RuleEvaluation` + 知识入库率 + 规则漏判 ✓）
  ④ `ke_rule_eval`   回放评测（一致性 ≥0.90、假断言=0 ✓）
  ⑤ `ke_rule_publish`（`--apply` 才执行）发布（双门禁 ✓）

CLI：
  python3 ke_rule_review.py [--apply] [--domain-kb …] [--work-kb …] [--spec policy:…]
"""
from __future__ import annotations

import argparse
import glob
import json
import os

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
FIX = os.path.join(ROOT, "tools", "ke-core", "tests", "fixtures")


def main() -> None:
    ap = argparse.ArgumentParser(description="规则工程一键闭环")
    ap.add_argument("--domain-kb", default="技术方案评审领域模型")
    ap.add_argument("--work-kb", default="技术方案评审任务知识库")
    ap.add_argument("--spec", default="policy:技术方案逐项评审策略")
    ap.add_argument("--apply", action="store_true", help="最后一步执行发布（默认 dry-run ✓）")
    a = ap.parse_args()

    import ke_db
    import ke_rule_check
    import ke_rule_copy
    import ke_rule_eval
    import ke_rule_ledger

    dom = ke_db.resolve_kb_id(a.domain_kb)[0]
    wk = ke_db.resolve_kb_id(a.work_kb)[0]
    rows, steps = [], []

    chk = ke_rule_check.check_rules(dom, a.spec)
    steps.append({"step": "① 体检", "ok": chk["ok"], "detail": "规则 %d 条｜错误 %d｜提醒 %d"
                  % (chk["rules"], len(chk["errors"]), len(chk["warns"]))})
    if not chk["ok"]:
        print(json.dumps({"ok": False, "steps": steps, "errors": chk["errors"][:5]}, ensure_ascii=False))
        return

    cp = ke_rule_copy.sync_rules(wk, dom, spec=a.spec)
    steps.append({"step": "② 副本", "ok": not cp["missing"], "detail": "同步 %d｜缺 %d"
                  % (len(cp["copied"]), len(cp["missing"]))})

    ld = ke_rule_ledger.write_all(wk, dom, dry_run=False)
    steps.append({"step": "③ 台账", "ok": not ld["failed"], "detail": "写 %d｜边 %d｜幻觉 %d｜类型 %s"
                  % (len(ld["written"]), len([e for e in ld["edges"] if e.get("ok")]),
                     ld.get("phantom", 0), ld.get("page_type"))})

    fixtures = sorted(glob.glob(os.path.join(FIX, "facts_s*.json")))
    ev = ke_rule_eval.eval_samples(fixtures, dom)
    steps.append({"step": "④ 评测", "ok": ev["ok"], "detail": "一致性 %.2f｜假断言 %d｜样例 %d"
                  % (ev["consistency"], len(ev["false_claims"]), len(ev["samples"]))})

    if a.apply:
        import ke_rule_publish
        pub = ke_rule_publish.publish(dom, a.spec, apply=True)
        steps.append({"step": "⑤ 发布", "ok": pub["ok"], "detail": "发布 %d｜%s"
                      % (pub.get("published", 0), pub.get("stamp") or pub.get("reason"))})

    ok = all(s["ok"] for s in steps)
    print(json.dumps({"ok": ok, "steps": steps}, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
