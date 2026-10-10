"""按名字删除**顶层函数定义块**（从 `def NAME(` 到下一个顶层 def/class/EOF），并归整空行。

用法：/opt/bodhi-venv/bin/python3 tools/diag/_rmfunc.py <file.py> <name> [name...]
"""
import pathlib
import re
import sys


def main():
    if len(sys.argv) < 3:
        print(__doc__)
        return 2
    p = pathlib.Path(sys.argv[1])
    names = sys.argv[2:]
    lines = p.read_text(encoding="utf-8").splitlines(keepends=True)
    removed = []
    for name in names:
        start = next((i for i, ln in enumerate(lines)
                      if re.match(r"^def %s\s*\(" % re.escape(name), ln)), None)
        if start is None:
            print("  !! 找不到 def %s（跳过）" % name)
            continue
        end = start + 1
        while end < len(lines) and not re.match(r"^(def |class |async def )", lines[end]):
            if end > start and not lines[end].strip():
                nxt = end
                while nxt < len(lines) and not lines[nxt].strip():
                    nxt += 1
                if nxt < len(lines) and re.match(r"^(def |class |async def )", lines[nxt]):
                    break
            end += 1
        del lines[start:end]
        while start - 2 >= 0 and not lines[start - 1].strip() and not lines[start - 2].strip():
            del lines[start - 1]
            start -= 1
        removed.append(name)
        print("  已删 def %s（原 %d 行）" % (name, end - start))
    p.write_text("".join(lines), encoding="utf-8")
    print("文件已更新：%s（删了 %d 个）" % (p, len(removed)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
