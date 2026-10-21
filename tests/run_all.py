#!/usr/bin/env python3
"""P0 测试总入口：跑 `tests/test_*.py` 全部用例，汇总退出码

跑法：
```
python3 tests/run_all.py               # 全跑（DB 相关用例会在 DB 不可用时自动跳过 ✓）
python3 tests/run_all.py --list        # 只列用例
```
退出码：0 = 全绿 ✓ ／ 1 = 有失败 ✗
"""
import glob
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)


def main() -> int:
    files = sorted(glob.glob(os.path.join(HERE, "test_*.py")))
    if "--list" in sys.argv:
        for f in files:
            print(os.path.basename(f))
        return 0
    if not files:
        print("没有用例 ✗")
        return 1
    results = {}
    for f in files:
        name = os.path.basename(f)
        print("\n" + "=" * 66)
        print("▶ %s" % name)
        print("=" * 66)
        rc = subprocess.run([sys.executable, f], cwd=REPO).returncode
        results[name] = rc
    bad = [k for k, v in results.items() if v != 0]
    print("\n" + "=" * 66)
    print("汇总：%d 个用例 ｜ 通过 %d ｜ 失败 %d" % (len(results), len(results) - len(bad), len(bad)))
    for k, v in results.items():
        print("  %s %s" % ("✓" if v == 0 else "✗", k))
    return 0 if not bad else 1


if __name__ == "__main__":
    sys.exit(main())
