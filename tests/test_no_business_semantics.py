#!/usr/bin/env python3
"""P0 铁律①：工具 / 技能**无业务语义** —— 域词扫描（基线只降不升 ✓）

口径（2026-10-21 用户口径）：
- 扫描范围：`tools/**`、`skills/**`（**排除** `tools/ke-core/tests/**` 回归脚本、`skills/_deprecated/**`）
- 词表：`tests/biz_terms.txt`
- 基准：`tests/golden/biz_terms_baseline.json` 记录**当前命中数**（首次自动生成）
- 断言：**每个文件的命中数不得超过基线** ✓（重构目标是把它们压到 0，逐步降低 ✓）

跑法：`python3 tests/test_no_business_semantics.py [--update-golden]`
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _helpers as H  # noqa: E402

EXCLUDE = ("/tests/", "/_deprecated/", "/__pycache__/")


def _terms() -> list:
    return [t.strip() for t in H.read(os.path.join(H.TESTS, "biz_terms.txt")).splitlines()
            if t.strip() and not t.startswith("#")]


def scan() -> dict:
    terms = _terms()
    hits = {}
    roots = [os.path.join(H.REPO, "tools"), os.path.join(H.REPO, "skills")]
    for root in roots:
        for p in H.walk_files(root):
            rel = p.replace(H.REPO, "").replace("\\", "/")
            if any(x in rel for x in EXCLUDE):
                continue
            txt = H.read(p)
            n = sum(txt.count(t) for t in terms)
            if n:
                hits[rel] = n
    return hits


def main() -> int:
    now = scan()
    base, created = H.load_or_init_golden("biz_terms_baseline.json", now, H.update_golden_flag())
    if created:
        print("⚠️ 已生成基线：%d 个文件命中，共 %d 次（后续只许降 ✓）"
              % (len(now), sum(now.values())))
        return 0
    worse = {k: (now.get(k, 0), base.get(k, 0)) for k in now if now.get(k, 0) > base.get(k, 0)}
    improved = {k: (now[k], base[k]) for k in now if now.get(k, 0) < base.get(k, 0)}
    gone = sorted(set(base) - set(now))
    checks = {
        "无新增/加重（%d 个文件超基线）" % len(worse): not worse,
        # 2026-10-23（用户口径 ✓）：域词已清零 ⇒ **硬断言 0**（工具/技能只认类型与属性）
        "域词命中 = 0（硬断言；当前 %d）" % sum(now.values()): sum(now.values()) == 0,
    }
    if worse:
        print("超基线：%s" % json.dumps(worse, ensure_ascii=False)[:600])
    if improved:
        print("已下降：%s（记得 --update-golden 固化 ✓）"
              % json.dumps(improved, ensure_ascii=False)[:400])
    if gone:
        print("已清零：%d 个文件 ✓" % len(gone))
    return H.report(checks, "无业务语义")


if __name__ == "__main__":
    sys.exit(main())
