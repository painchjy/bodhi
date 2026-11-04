"""docx → 分节（**02 知识侧**，纯标准库）：标题按行首编号识别，样式仅辅助。

为什么单独一份（2026-11-02 用户口径：两个 MCP 代码级隔离）
--------------------------------------------------------
04（it_platform）自己上传时也切节（`ke_domain_docs.sections`），但 02 解析正文不能去 import
04 的运行库 ⇒ 各自保留一份**纯函数**（同一套规则、零依赖、零共享状态）。
"""
from __future__ import annotations


_MAX_SECTION_CHARS = 6000          # 单段上限：超出按句子边界二次切（PDF 常出现 5 万字巨段）
_HEAD_NUM = r"^\s*(?:\*\*)?(\d+(?:\.\d+){0,3})(?:\*\*)?\s+\S"
_HEAD_CN = (
    r"^\s*第[一二三四五六七八九十百\d]+[章节部分篇]\s*",
    r"^\s*[一二三四五六七八九十]+、\s*\S",
    r"^\s*（[一二三四五六七八九十]+）\s*\S",
)
_SENT_END = ("。", "，", "；", "：", "！", "？", ".", ",", ";", ":")


def _is_heading(line: str, nxt: str = "") -> bool:
    """标题判定（PDF 常丢编号 ⇒ 除编号外还认中文编号 + "短行 + 下一行明显更长"）。"""
    import re as _re

    s = str(line or "").strip()
    if not s or len(s) > 40 or s.endswith(_SENT_END):
        return False
    if _re.match(_HEAD_NUM, s):
        return True
    if any(_re.match(p, s) for p in _HEAD_CN):
        return True
    n = str(nxt or "").strip()
    return len(s) <= 20 and len(n) >= 40          # 短行后接长正文 ⇒ 当标题（保守）


def _split_long(text: str, limit: int = _MAX_SECTION_CHARS) -> list:
    """超长正文按句末/换行二次切，保证单段 ≤ limit（不丢字、不改写）。"""
    import re as _re

    body = str(text or "")
    if len(body) <= limit:
        return [body]
    out, buf = [], ""
    for seg in _re.split(r"(?<=[。！？；\n])", body):
        if not seg:
            continue
        if buf and len(buf) + len(seg) > limit:
            out.append(buf)
            buf = seg
        else:
            buf += seg
    if buf:
        out.append(buf)
    return out or [body]


def _sections_from_text(text: str, title: str) -> list:
    """**纯文本**按标题切节（PDF / TXT 用；docx 走 `sections()`）。

    2026-11-03（内网 PDF 实测：49 页只切出 7 段、出现 5 万字巨段/空段/重复段）：
    - 标题判定扩展：编号 / 中文编号（第X章、一、、（一））/ 短行后接长正文；
    - **丢弃空段**（页码/页眉遗留）与**相邻重复段**；
    - 任何段 > `_MAX_SECTION_CHARS` 按句末二次切（不丢字）。
    """
    import re as _re

    lines = (text or "").splitlines()
    heads = []
    for i, ln in enumerate(lines):
        nxt = lines[i + 1] if i + 1 < len(lines) else ""
        if _is_heading(ln, nxt):
            heads.append(i)

    def _emit(sec_title: str, num: str, body: str) -> list:
        chunks = _split_long(body)
        out = []
        for j, part in enumerate(chunks):
            label = sec_title.strip()[:80]
            base = ("%s %s" % (num, label[:30])).strip() if num else label[:36]
            out.append({"path": base + ("" if j == 0 else "-%d" % (j + 1)),
                        "title": label, "text": part})
        return out

    if not heads:
        body = (text or "").strip()
        if not body:
            return [{"path": "全文", "title": title, "text": ""}]
        return [{"path": "全文-%d" % (i + 1) if i else "全文", "title": title, "text": part}
                for i, part in enumerate(_split_long(body))]

    out: list = []
    for k, idx in enumerate(heads):
        end = heads[k + 1] if k + 1 < len(heads) else len(lines)
        body = "\n".join(lines[idx + 1:end]).strip()
        if not body:                          # 空段丢弃（PDF 页码/页眉造成的空节）
            continue
        m = _re.match(_HEAD_NUM, lines[idx])
        out += _emit(lines[idx], (m.group(1) if m else ""), body)
    if not out:
        body = (text or "").strip()
        return [{"path": "全文", "title": title, "text": part} for part in _split_long(body)]
    dedup: list = []                          # 相邻重复段去重（页眉/页脚被当标题时会出现）
    for s in out:
        # 只对**短段**去重：长段可能是 `_split_long` 切出的等长块（文本相同但内容不能丢）
        if dedup and len(s["text"]) <= 500 and s["text"] == dedup[-1]["text"]:
            continue
        dedup.append(s)
    return dedup


def _docx_images(raw: bytes, doc_id: str, root: str = "") -> list:
    """docx 的 `word/media/*` → 落 `<root>/files`，返回 `[{name,path}]`。

    021（02 知识侧）落盘根与 `ke_doc_parse.doc_root()` 同口径：`BODHI_DOC_ROOT` 或
    `state/doc_parse`（**不是** 04 的 `BODHI_EXT_ROOT` ✗）。
    """
    import io
    import os
    import zipfile

    if not raw.startswith(b"PK"):
        return []
    base = root or os.environ.get("BODHI_DOC_ROOT") or os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
        "state", "doc_parse")
    sub = os.path.join(base, "files")
    out = []
    try:
        os.makedirs(sub, exist_ok=True)
        with zipfile.ZipFile(io.BytesIO(raw)) as z:
            media = sorted(n for n in z.namelist() if n.startswith("word/media/"))
            for i, n in enumerate(media, 1):
                ext = os.path.splitext(n)[1].lower() or ".png"
                fn = "%s_img%d%s" % (doc_id, i, ext)
                with open(os.path.join(sub, fn), "wb") as fh:
                    fh.write(z.read(n))
                out.append({"name": "图片%d%s" % (i, ext), "path": "files/" + fn})
    except Exception:  # noqa: BLE001  图取不到不影响正文（不阻断解析 ✓）
        return []
    return out


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
