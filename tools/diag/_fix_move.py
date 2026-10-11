"""把误插到 `cmd_import` 中间的「⑤ 硬删」区块搬到 CLI 段之前（修 IndentationError）。"""
import pathlib

P = pathlib.Path("tools/ke-core/kb_transfer.py")
L = P.read_text(encoding="utf-8").splitlines(keepends=True)
start = next(i for i, ln in enumerate(L)
             if ln.startswith("# ============================================================ ⑤ 硬删"))
end = next(i for i in range(start, len(L)) if L[i].startswith("    return res"))
j = end + 1
while j < len(L) and not L[j].strip():
    j += 1
block = L[start:j]
rest = L[:start] + L[j:]
cli = next(i for i, ln in enumerate(rest)
           if ln.startswith("# ============================================================ CLI"))
out = rest[:cli] + block + ["\n"] + rest[cli:]
P.write_text("".join(out), encoding="utf-8")
print("搬运 %d 行到 CLI 段之前；文件 %d → %d 行" % (len(block), len(L), len(out)))
