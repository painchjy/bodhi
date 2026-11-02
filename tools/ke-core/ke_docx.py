"""docx → 分节（**02 知识侧**，纯标准库）：标题按行首编号识别，样式仅辅助。

为什么单独一份（2026-11-02 用户口径：两个 MCP 代码级隔离）
--------------------------------------------------------
04（it_platform）自己上传时也切节（`ke_domain_docs.sections`），但 02 解析正文不能去 import
04 的运行库 ⇒ 各自保留一份**纯函数**（同一套规则、零依赖、零共享状态）。
"""
from __future__ import annotations


def sections(path: str) -> list:
    """docx → `[{path,title,text}]`（标题 = `2.1 xxx` 行首编号，样式仅辅助）。"""
    import re as _re
    import zipfile
    from xml.etree import ElementTree as ET

    W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    try:
        with zipfile.ZipFile(path) as z:
            root = ET.fromstring(z.read("word/document.xml"))
    except Exception:  # noqa: BLE001  读不了就当"全文无正文"（不阻断上传 ✓）
        return [{"path": "全文", "title": "全文", "text": ""}]

    def _lvl(style: str, text: str) -> int:
        m = _re.match(r"^(\d+(?:\.\d+){0,3})\s+\S", text or "")
        if m and len(text) <= 40:
            return m.group(1).count(".") + 1
        m2 = _re.match(r"(?i)^heading\s*(\d)$", style or "")
        styled = bool(m2) or ((style or "").isdigit() and 2 <= int(style or 0) <= 3)
        if styled and len(text or "") <= 30 and not text.endswith(("。", "，", "；")):
            return 1
        return 0

    paras = []
    for p in root.iter(W + "p"):
        style = ""
        for s in p.iter(W + "pStyle"):
            style = s.get(W + "val") or ""
            break
        text = "".join(t.text or "" for t in p.iter(W + "t")).strip()
        if text:
            paras.append((style, text))
    heads = [(i, _lvl(st, tx)) for i, (st, tx) in enumerate(paras) if _lvl(st, tx)]
    if not heads:
        return [{"path": "全文", "title": "全文", "text": "\n".join(tx for _s, tx in paras)}]
    out = []
    for k, (idx, lvl) in enumerate(heads):
        end = heads[k + 1][0] if k + 1 < len(heads) else len(paras)
        title = paras[idx][1]
        body = "\n".join(tx for _s, tx in paras[idx + 1:end])
        m = _re.match(r"^(\d+(?:\.\d+){0,3})\s+(.*)$", title)
        pid = (m.group(1) + " " + m.group(2)[:36]) if m else ("H%d %s" % (lvl, title[:36]))
        out.append({"path": pid, "title": title[:80], "text": body})
    return out
