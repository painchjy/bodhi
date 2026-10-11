"""v0.5 装配：按**标题标记**替换段落（不依赖行号，可重复执行）。

替换 4 段：
  ## 0 一屏            ← tools/diag/_q0.md
  ## 3 活动定义        ← tools/diag/_q3.md
  ## 6 活动 ↔ 技能     ← tools/diag/_q6.md
  ## 7 落地清单 … §8   ← tools/diag/_q78.md

用法：python3 tools/diag/_asm.py docs/knowledge-mgmt-agent-and-modeling-stages.md
"""
import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parents[2]
D = pathlib.Path(sys.argv[1])
if not D.is_absolute():
    D = REPO / D
TMP = REPO / "tools" / "diag"

EDITS = [
    ("_q0.md", "## 0 一屏", "## 1 本体调整"),
    ("_q3.md", "## 3 活动定义", "## 4 BPMN 对照"),
    ("_q6.md", "## 6 活动 ↔", "## 7 落地清单"),
    ("_q78.md", "## 7 落地清单", "## 9 附"),
]

text = D.read_text(encoding="utf-8")
for fname, start_marker, end_marker in EDITS:
    repl = (TMP / fname).read_text(encoding="utf-8").rstrip("\n") + "\n\n---\n\n"
    i = text.index(start_marker)
    j = text.index(end_marker, i)
    text = text[:i] + repl + text[j:]
    print("替换 %-32s → %s（%d..%d）" % (fname, start_marker, i, j))
D.write_text(text, encoding="utf-8")
print("已写：%s（%d 行）" % (D, len(text.splitlines())))
