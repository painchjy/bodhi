"""删除 TOOLS 列表里某个工具定义块（含前后空行）。

块起点 = `"name": "<tool>",` 之前最近的一行 `        {`（缩进 8）；
块终点 = 其后第一行 `        },`（缩进 8）。
用法：_kiltool.py <file.py> <tool_name> [tool_name...]
"""
import pathlib
import sys

p = pathlib.Path(sys.argv[1])
L = p.read_text(encoding="utf-8").splitlines(keepends=True)
for name in sys.argv[2:]:
    nm = next((i for i, ln in enumerate(L) if ln.strip() == '"name": "%s",' % name), None)
    if nm is None:
        print("  !! 找不到工具定义 %s" % name)
        continue
    s = nm
    while s > 0 and L[s].rstrip("\n") != "        {":
        s -= 1
    j = nm + 1
    while j < len(L) and L[j].rstrip("\n") != "        },":
        j += 1
    j += 1
    while s > 0 and not L[s - 1].strip():
        s -= 1
    n = j - s
    del L[s:j]
    print("  已删工具 %s（%d 行）" % (name, n))
p.write_text("".join(L), encoding="utf-8")
print("文件已更新：%s" % p)
