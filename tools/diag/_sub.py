"""全量字面替换（支持多行、替换所有出现处）。

用法：_sub.py <file> <old_file> <new_file>
输出命中次数（0 次视为失败，非 0 退出）。
"""
import pathlib
import sys

p = pathlib.Path(sys.argv[1])
old = pathlib.Path(sys.argv[2]).read_text(encoding="utf-8")
new = pathlib.Path(sys.argv[3]).read_text(encoding="utf-8")
txt = p.read_text(encoding="utf-8")
n = txt.count(old)
if not n:
    print("!! 未命中：%s" % p)
    raise SystemExit(1)
p.write_text(txt.replace(old, new), encoding="utf-8")
print("替换 %s：命中 %d 处" % (p, n))
