#!/usr/bin/env python3
"""P0 基线：报告结构契约（**P4 会改成"模板驱动"，此用例届时改写** ✓）

当前契约（2026-10-21）：
- 5 节：① 范围与依据 ② 规则清单 ③ 问题清单 ④ 知识入库情况 ⑤ 结论与建议
- ② 规则清单 7 列：序号 | 规则 | 执行级别 | 适用范围 | 参考规范 | 本次判定概览 | 规则指纹
- ③ 末列 = 规则指纹（= 该规则的 `bmm:ruleHash`，发布时写入 ✓）

依赖：DB（读领域库规则属性）+ fixtures；DB 不可用则 **跳过**（退出码 0 ✓）

跑法：`python3 tests/test_report_structure.py`
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _helpers as H  # noqa: E402

sys.path.insert(0, os.path.join(H.REPO, "tools", "ke-core"))


def main() -> int:
    checks = {}
    try:
        import ke_db
        import ke_report
        dom = ke_db.resolve_kb_id("技术方案评审领域模型")[0]
    except Exception as exc:  # noqa: BLE001
        print("（DB 不可用，跳过 ✓：%r）" % (str(exc)[:80],))
        return 0
    fx = os.path.join(H.REPO, "tools", "ke-core", "tests", "fixtures", "facts_s1.json")
    facts = json.loads(H.read(fx))
    r = ke_report.render("样例1", "样例1", facts, dom)
    md = r["content"]
    for name in ("①", "②", "③", "④", "⑤"):
        checks["章节 %s 存在" % name] = ("## " + name) in md
    sec2 = md.split("## ② 规则清单", 1)[-1].split("## ③", 1)[0]
    hdr = next((ln for ln in sec2.splitlines() if ln.strip().startswith("|")), "")
    cols = [c.strip() for c in hdr.strip().strip("|").split("|")]
    checks["② 规则清单 7 列"] = len(cols) == 7
    checks["② 含「规则指纹」列"] = any("规则指纹" in c for c in cols)
    checks["② 含 rule_hash 数据（16 位 hex）"] = any(
        len(t) == 16 and all(ch in "0123456789abcdef" for ch in t)
        for ln in sec2.splitlines() for t in ln.replace("`", " ").split())
    sec3 = md.split("## ③ 问题清单", 1)[-1].split("## ④", 1)[0]
    t3 = [ln for ln in sec3.splitlines() if ln.strip().startswith("|")]
    hdr3 = t3[0] if t3 else ""
    body = t3[2:] if len(t3) > 2 else []
    checks["③ 有数据行（%d）" % len(body)] = len(body) > 0
    checks["③ 表头末列为规则指纹"] = ("规则指纹" in hdr3.split("|")[-2] if hdr3 else False)
    checks["② 含 rule_hash 数据（16 位 hex）"] = bool(
        __import__("re").search(r"\b[0-9a-f]{16}\b", sec2))
    return H.report(checks, "报告结构")


if __name__ == "__main__":
    sys.exit(main())
