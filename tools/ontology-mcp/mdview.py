"""极小 Markdown → HTML 渲染器（仅标准库）。

为什么自己写：本项目策略是「自研一律 Python、最少依赖」（见 docs/weknora-fork.md §11），
不引入 markdown / mistune 之类的包，也不想为了渲染正文再走一次前端构建。
覆盖 wiki 页实际用到的语法：标题、段落、有序/无序列表、引用、代码块与行内代码、
粗体/斜体、分隔线、管道表格、链接（含 wiki 站内链接 → 可点击跳转到图谱里的对应节点）。

用法：
    from mdview import render
    html = render(text)                 # 返回 <div class="md">…</div>
    python3 mdview.py                   # 自检（若干断言）
"""

from __future__ import annotations

import html as _html
import re

_WIKI_PREFIXES = ("/wiki/", "wiki/")


def esc(text: str) -> str:
    return _html.escape(text or "", quote=True)


def _slug_of(target: str) -> str:
    """从链接目标里取站内 slug（/wiki/xxx → xxx；裸 slug 直接返回）。"""
    t = (target or "").strip()
    if t.startswith("/wiki/"):
        t = t[len("/wiki/"):]
    elif t.startswith("wiki/"):
        t = t[len("wiki/"):]
    return t.strip("/")


def _inline(text: str) -> str:
    """行内语法：先转义，再逐类替换（顺序有意为之：代码 → 链接 → 强调）。"""
    out = esc(text)

    # 行内代码：`x`（先抽出来，避免里面的 * 被当成强调）
    codes: list[str] = []

    def _stash(m: re.Match[str]) -> str:
        codes.append(m.group(1))
        return "\x00%d\x00" % (len(codes) - 1)

    out = re.sub(r"`([^`]+)`", _stash, out)

    # [[slug|文本]] 与 [[slug]]
    def _wikidouble(m: re.Match[str]) -> str:
        body = m.group(1)
        slug, _, label = body.partition("|")
        slug = slug.strip()
        return '<span class="chip link" data-goto="%s">%s</span>' % (
            esc(slug), label.strip() or esc(slug))

    out = re.sub(r"\[\[([^\]]+)\]\]", _wikidouble, out)

    # [文本](目标)：站内链接变成可跳转 chip，外链正常开新窗口
    def _link(m: re.Match[str]) -> str:
        label, target = m.group(1), m.group(2)
        if target.startswith(_WIKI_PREFIXES):
            slug = _slug_of(target)
            if slug:
                return '<span class="chip link" data-goto="%s">%s</span>' % (esc(slug), label)
        return '<a href="%s" target="_blank" rel="noopener">%s</a>' % (esc(target), label)

    out = re.sub(r"\[([^\]]*)\]\(([^)\s]+)\)", _link, out)

    out = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", out)
    out = re.sub(r"(?<!\*)\*([^*\n]+)\*(?!\*)", r"<em>\1</em>", out)

    def _unstash(m: re.Match[str]) -> str:
        return "<code>%s</code>" % codes[int(m.group(1))]

    return re.sub(r"\x00(\d+)\x00", _unstash, out)


def _is_table_sep(line: str) -> bool:
    cells = [c.strip() for c in line.strip().strip("|").split("|")]
    return bool(cells) and all(re.fullmatch(r":?-{2,}:?", c or "-") for c in cells)


def _table(rows: list[str]) -> str:
    def cells(line: str) -> list[str]:
        return [c.strip() for c in line.strip().strip("|").split("|")]

    head = cells(rows[0])
    body = [cells(r) for r in rows[2:]]
    parts = ["<table>", "<thead><tr>"]
    parts += ["<th>%s</th>" % _inline(c) for c in head]
    parts.append("</tr></thead><tbody>")
    for row in body:
        parts.append("<tr>")
        parts += ["<td>%s</td>" % _inline(c) for c in row]
        parts.append("</tr>")
    parts.append("</tbody></table>")
    return "".join(parts)



def render(text: str) -> str:
    """把 Markdown 文本渲染成 HTML（块级按行扫描：够用、可预测、无依赖）。"""
    lines = (text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")
    out: list[str] = []
    i = 0
    n = len(lines)

    while i < n:
        stripped = lines[i].strip()

        # 代码块
        if stripped.startswith("```"):
            lang = stripped[3:].strip()
            i += 1
            buf: list[str] = []
            while i < n and not lines[i].strip().startswith("```"):
                buf.append(lines[i])
                i += 1
            i += 1
            cls = ' class="lang-%s"' % esc(lang) if lang else ""
            out.append("<pre%s><code>%s</code></pre>" % (cls, esc("\n".join(buf))))
            continue

        # 标题
        m = re.match(r"^(#{1,6})\s+(.*)$", stripped)
        if m:
            level = len(m.group(1))
            out.append("<h%d>%s</h%d>" % (level, _inline(m.group(2).strip()), level))
            i += 1
            continue

        # 分隔线
        if re.fullmatch(r"(-{3,}|\*{3,}|_{3,})", stripped):
            out.append("<hr>")
            i += 1
            continue

        # 表格（表头 + 分隔行）
        if stripped.startswith("|") and i + 1 < n and _is_table_sep(lines[i + 1]):
            rows = []
            while i < n and lines[i].strip().startswith("|"):
                rows.append(lines[i])
                i += 1
            out.append(_table(rows))
            continue

        # 引用
        if stripped.startswith(">"):
            buf = []
            while i < n and lines[i].strip().startswith(">"):
                buf.append(lines[i].strip()[1:].strip())
                i += 1
            out.append("<blockquote>%s</blockquote>" % _inline(" ".join(buf)))
            continue

        # 列表（无序/有序）
        m = re.match(r"^([-*+]|\d+[.)])\s+(.*)$", stripped)
        if m:
            tag = "ol" if m.group(1)[0].isdigit() else "ul"
            items: list[str] = []
            while i < n:
                mm = re.match(r"^([-*+]|\d+[.)])\s+(.*)$", lines[i].strip())
                if not mm:
                    break
                items.append(_inline(mm.group(2)))
                i += 1
            out.append("<%s>%s</%s>" % (tag, "".join("<li>%s</li>" % x for x in items), tag))
            continue

        # 空行
        if not stripped:
            i += 1
            continue

        # 段落（连续非空行合并，行内换行保留为 <br>）
        buf = []
        while i < n and lines[i].strip() and not re.match(
                r"^\s*(#{1,6}\s|```|>|[-*+]\s|\d+[.)]\s|\|)", lines[i]):
            buf.append(lines[i].strip())
            i += 1
        if buf:
            out.append("<p>%s</p>" % "<br>".join(_inline(x) for x in buf))

    return '<div class="md">%s</div>' % "".join(out)


def _selftest() -> int:
    src = "# 标题一\n## 目的\n\n本页说明 **幂等** 与 `isReadOnly` 的关系，见 [服务契约](/wiki/ontology-easvc-servicecontract)。\n\n- 第一条\n- 第二条\n\n| 列 A | 列 B |\n|---|---|\n| 1 | 2 |\n\n> 引用一句话\n"
    html = render(src)
    checks = [
        ("<h1>标题一</h1>", "一级标题"),
        ("<strong>幂等</strong>", "粗体"),
        ("<code>isReadOnly</code>", "行内代码"),
        ('data-goto="ontology-easvc-servicecontract"', "站内链接转跳转 chip"),
        ("<ul><li>第一条</li><li>第二条</li></ul>", "无序列表"),
        ("<table><thead><tr><th>列 A</th>", "表格"),
        ("<blockquote>引用一句话</blockquote>", "引用"),
    ]
    bad = 0
    for frag, what in checks:
        ok = frag in html
        print("  %s %s" % ("OK  " if ok else "FAIL", what))
        bad += 0 if ok else 1
    print("  渲染结果 %d 字节，失败 %d 项" % (len(html.encode("utf-8")), bad))
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(_selftest())
