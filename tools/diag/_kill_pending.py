"""删除 save_elements 里已被 Cut-1 孤立出来的 `else:`（pending 分支）整块。

块起点 = 内容为 `        else:` 且下一行含 `build_pending_page(` 的那行；
块终点 = 其后第一条缩进 <= 8 且非空的行（`    summary["engine"] = ...`）之前。
用法：_kill_pending.py <file>
"""
import pathlib
import sys

p = pathlib.Path(sys.argv[1])
L = p.read_text(encoding="utf-8").splitlines(keepends=True)
i = next(i for i in range(len(L) - 1)
         if L[i].rstrip("\n") == "        else:" and "build_pending_page" in L[i + 1])
j = i + 1
while j < len(L):
    ln = L[j]
    if ln.strip() and (len(ln) - len(ln.lstrip(" "))) <= 8:
        break
    j += 1
s = i
while s > 0 and not L[s - 1].strip():
    s -= 1
n = j - s
del L[s:j]
p.write_text("".join(L), encoding="utf-8")
print("删除 pending 块：%d-%d（共 %d 行）；文件现 %d 行" % (s + 1, j, n, len(L)))
