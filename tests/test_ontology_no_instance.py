#!/usr/bin/env python3
"""P0 铁律②：本体（ontology/*.ttl）**不得引用实例名**

口径：`tests/instance_terms.txt` 是"实例名"清单（具体系统/平台/策略/样例名）。
本体只允许出现**类/属性/关系/约束**；实例只存在于知识库数据 ✓（2026-10-21 用户口径）。

注意：像「技术方案评审报告」这类**类标签**不算实例 ✓ —— 所以清单只收真正的实例名。

跑法：`python3 tests/test_ontology_no_instance.py`
"""
import glob
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _helpers as H  # noqa: E402


def main() -> int:
    terms = [t.strip() for t in H.read(os.path.join(H.TESTS, "instance_terms.txt")).splitlines()
             if t.strip() and not t.startswith("#")]
    files = glob.glob(os.path.join(H.REPO, "ontology", "*.ttl"))
    hits = {}
    for p in files:
        txt = H.read(p)
        found = {t: txt.count(t) for t in terms if t in txt}
        if found:
            hits[os.path.basename(p)] = found
    checks = {
        "本体文件 %d 个全部无实例名 ✓" % len(files): not hits,
    }
    if hits:
        print("命中（须清理）：")
        for f, d in hits.items():
            print("  %s → %s" % (f, d))
    return H.report(checks, "本体无实例")


if __name__ == "__main__":
    sys.exit(main())
