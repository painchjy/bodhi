"""把 WikiBrowser.vue 里的「版本徽标」统一挪到**行末尾**（幂等）。

背景：徽标（`v3`）原先在标题前 → 标题起始位置不齐（用户 2026-09-21 反馈："放前面不整齐"）。
本脚本做两件事，可反复运行：
  1) 目录树行：`[checkbox][icon][徽标][类型点][标题]` → `[checkbox][icon][类型点][标题][徽标]`
  2) 扁平列表行：`[徽标][类型点][标题]`           → `[类型点][标题][徽标]`
  3) 样式：`margin-left: 6px` 保持不变（贴着标题，且不推挤其它元素）

用法：python3 tools/delivery/patch_version_badge.py [--src /root/fe-build]
"""

from __future__ import annotations

import argparse
import pathlib
import re

TREE_BADGE = ('<span v-if="(item.page.version || 1) > 1" '
              'class="wiki-page-item-version">v{{ item.page.version }}</span>')
FLAT_BADGE = ('<span v-if="(item.version || 1) > 1" '
              'class="wiki-page-item-version">v{{ item.version }}</span>')


def move_badge(text: str, badge: str, anchor: str, indent: str) -> tuple[str, bool]:
    """把 badge 行从原位置删掉，插到 anchor 之后（anchor 出现一次时）。"""
    if badge not in text:
        return text, False
    if text.count(anchor) != 1:
        return text, False
    pattern = re.compile(r"[ \t]*" + re.escape(badge) + r"[ \t]*\r?\n")
    stripped, n = pattern.subn("", text, count=1)
    if not n:
        return text, False
    return stripped.replace(anchor, anchor + "\n" + indent + badge, 1), True


def main() -> int:
    ap = argparse.ArgumentParser(description="版本徽标挪到行末（幂等）")
    ap.add_argument("--src", default="/root/fe-build", help="前端源码根（含 src/）")
    args = ap.parse_args()
    path = pathlib.Path(args.src) / "src" / "views" / "knowledge" / "wiki" / "WikiBrowser.vue"
    if not path.is_file():
        print("!! 找不到 %s" % path)
        return 1
    text = path.read_text(encoding="utf-8")
    before = text

    # 1) 目录树行：徽标挪到「标题 span」之后
    text, m1 = move_badge(
        text, TREE_BADGE,
        '<span class="wiki-page-item-title">{{ item.page.title }}</span>',
        "                      ")
    # 2) 扁平列表行：徽标挪到「标题文本 span」之后
    text, m2 = move_badge(
        text, FLAT_BADGE,
        '<span class="wiki-page-item-title-text">{{ item.title }}</span>',
        "                      ")
    # 3) 兜底：仍存在「徽标紧跟 t-tooltip」的旧写法（无标题锚点时）
    text = re.sub(r"[ \t]*" + re.escape(TREE_BADGE) + r"\r?\n(?=[ \t]*<t-tooltip)",
                  "", text, count=1)
    text = re.sub(r"[ \t]*" + re.escape(FLAT_BADGE) + r"\r?\n(?=[ \t]*<t-tooltip)",
                  "", text, count=1)

    if text != before:
        path.write_text(text, encoding="utf-8")
    print("树行徽标移动：%s ｜ 列表行徽标移动：%s ｜ 文件变化：%s"
          % ("是" if m1 else "已是目标位置/未找到", "是" if m2 else "已是目标位置/未找到",
             "是" if text != before else "否"))
    for line in text.splitlines():
        if "wiki-page-item-version" in line and "<span" in line:
            print("  现在：", line.strip()[:110])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
