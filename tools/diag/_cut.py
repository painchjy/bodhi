"""按行区间替换（1-based，闭区间）。用于删除被判死的代码块。

用法：_cut.py <file> <start> <end> <repl_file|->      # repl 为 '-' 表示纯删除
"""
import pathlib
import sys

f, s, e, rp = sys.argv[1], int(sys.argv[2]), int(sys.argv[3]), sys.argv[4]
p = pathlib.Path(f)
lines = p.read_text(encoding="utf-8").splitlines(keepends=True)
prior = len(lines)
assert 1 <= s <= e <= prior, "区间越界: %d-%d / 共 %d 行" % (s, e, prior)
repl = ([] if rp == "-"
        else pathlib.Path(rp).read_text(encoding="utf-8").splitlines(keepends=True))
p.write_text("".join(lines[:s - 1] + repl + lines[e:]), encoding="utf-8")
print("cut %s: %d-%d（%d 行）→ %d 行；文件 %d → %d 行"
      % (f, s, e, e - s + 1, len(repl), prior,
         len(lines[:s - 1]) + len(repl) + len(lines[e:])))
