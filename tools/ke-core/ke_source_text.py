"""来源 locator → 页面可渲染的**逐字摘录**（约 150 字）。

为什么单独成模块
----------------
`ke_provenance` 只做 locator 语法与回读方式提示，**无 IO**；本模块负责把
`bmm:sourceLocator` 真正回读成原文片段。页面正文的「## 原文依据」只渲染这里
返回的逐字文本，不再生成「概要设计 / 无原文片段 / 请补 source_text」占位。

支持：
  · kb_document   → `knowledges` + `chunks`（按 Markdown 标题取 section）
  · wiki_page     → `wiki_pages.content`（按 Markdown 标题取 section）
  · session_text  → `messages.content`（message_id 精确匹配）
  · external_doc  → 复用 `ke_doc_parse.parse(action="section")`

以下来源暂需外部适配器（返回 `source_not_supported`，不猜、不占位）：
  · session_attachment / file

口径：
  · 摘录 = 原文逐字子串（只做空白规整，不改写、不总结）；
  · 默认 150 字，超长返回 `truncated=true`；
  · section 找不到 ⇒ `section_not_found` + 候选标题，**绝不退回占位文案**；
  · 回读失败 ⇒ `text_source_unreadable`（带 resolver 提示）。
"""
from __future__ import annotations

import hashlib
import re

import ke_db
import ke_provenance as P

MAX_CHARS = 150

_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
_PLACEHOLDER = re.compile(r"^(?:（|\()?(?:概要设计|详细设计|领域建模)?[^。\n]{0,20}"
                          r"(?:无原文片段|未提供原文引用|请补\s*source_text)")
_WS = re.compile(r"[ \t\u3000]+")


def _clean(text: str) -> str:
    """统一换行并去掉行尾空白；保留正文中的换行结构用于定位。"""
    return str(text or "").replace("\r\n", "\n").replace("\r", "\n")


def _flat(text: str) -> str:
    """单行化：只规整空白，不增删文字。"""
    return _WS.sub(" ", _clean(text)).strip()


def _norm(text: str) -> str:
    return re.sub(r"\s+", "", str(text or "")).strip().lower()


def _looks_placeholder(text: str) -> bool:
    t = _flat(text)
    return bool(t and _PLACEHOLDER.search(t))


def is_placeholder(text: str) -> bool:
    """调用方显式传入的旧占位文案不得再写回页面。"""
    return _looks_placeholder(text)


def _headings(lines: list[str]) -> list[tuple[int, int, str]]:
    out: list[tuple[int, int, str]] = []
    for i, line in enumerate(lines):
        m = _HEADING.match(line.strip())
        if m:
            out.append((i, len(m.group(1)), m.group(2).strip()))
    return out


def _pick_heading(headings: list[tuple[int, int, str]], section: str) -> tuple[int, str]:
    """返回 `(下标, 命中方式)`；优先精确，其次包含；不猜。"""
    target = _norm(section)
    if not target:
        return -1, ""
    for i, _level, title in headings:
        if _norm(title) == target:
            return i, "section_exact"
    for i, _level, title in headings:
        nt = _norm(title)
        if target in nt or nt in target:
            return i, "section_contains"
    return -1, ""


def _body_until_next_head(lines: list[str], start: int, level: int,
                          headings: list[tuple[int, int, str]]) -> str:
    end = len(lines)
    for i, lv, _title in headings:
        if i > start and lv <= level:
            end = i
            break
    return "\n".join(lines[start + 1:end]).strip()


def excerpt_from_text(text: str, section: str = "", max_chars: int = MAX_CHARS) -> dict:
    """从纯文本取 section 的逐字摘录（无 DB；测试/复用入口）。

    返回 `{ok, text, chars, truncated, section, matched_by, available}` 或
    `{ok:false, code:...}`。不生成占位。
    """
    raw = _clean(text)
    max_chars = max(1, int(max_chars or MAX_CHARS))
    lines = raw.split("\n")
    headings = _headings(lines)
    available = [t for _i, _lv, t in headings]

    if section:
        idx, matched_by = _pick_heading(headings, section)
        if idx < 0:
            return {"ok": False, "code": "section_not_found", "section": section,
                    "available": available, "locator": ""}
        level = next(lv for i, lv, _t in headings if i == idx)
        body = _body_until_next_head(lines, idx, level, headings)
        if not body:
            return {"ok": False, "code": "source_empty", "section": section,
                    "available": available, "locator": ""}
    else:
        # 无 section：取正文第一段（跳过标题/空行）；仍保留逐字内容
        body_lines = [ln for ln in lines if ln.strip() and not _HEADING.match(ln.strip())]
        body = "\n".join(body_lines).strip()
        matched_by = "first_paragraph"
        if not body:
            return {"ok": False, "code": "source_empty", "section": "",
                    "available": available, "locator": ""}

    if _looks_placeholder(body):
        return {"ok": False, "code": "source_placeholder", "section": section,
                "available": available, "locator": ""}

    flat = _flat(body)
    truncated = len(flat) > max_chars
    out_text = flat[:max_chars]
    return {"ok": True, "text": out_text, "chars": len(out_text),
            "truncated": truncated, "section": section,
            "matched_by": matched_by, "available": available, "locator": ""}


def _fail(code: str, locator: str, **kw) -> dict:
    out = {"ok": False, "code": code, "locator": locator}
    out.update(kw)
    return out


def _ok(locator: str, kind: str, parsed: dict, text: str, section: str = "",
        source_title: str = "", max_chars: int = MAX_CHARS, **kw) -> dict:
    r = excerpt_from_text(text, section, max_chars)
    r["locator"] = locator
    r["kind"] = kind
    r["source_title"] = source_title
    r["sha256"] = hashlib.sha256(_clean(text).encode("utf-8")).hexdigest()[:16]
    r.update({k: v for k, v in parsed.items()
              if k in ("doc_id", "kb_id", "slug", "session_no", "message_id")})
    r.update(kw)
    return r



def _read_kb_document(parsed: dict, locator: str, max_chars: int) -> dict:
    kb_id, doc_id = parsed.get("kb_id", ""), parsed.get("doc_id", "")
    rows = ke_db.psql_csv(
        "SELECT id, title, file_name FROM knowledges "
        "WHERE knowledge_base_id = %s AND id = %s AND deleted_at IS NULL LIMIT 1"
        % (ke_db.sql_str(kb_id), ke_db.sql_str(doc_id)))
    if not rows:
        return _fail("source_not_found", locator, kind=P.KB_DOCUMENT,
                     kb_id=kb_id, doc_id=doc_id,
                     resolver=P.resolver_for(P.KB_DOCUMENT))
    doc = rows[0]
    chunks = ke_db.psql_csv(
        "SELECT chunk_index, content FROM chunks "
        "WHERE knowledge_base_id = %s AND knowledge_id = %s AND deleted_at IS NULL "
        "ORDER BY chunk_index" % (ke_db.sql_str(kb_id), ke_db.sql_str(doc_id)))
    # 同一 chunk_index/content 可能因解析重入重复：按内容去重，保持顺序
    seen, parts = set(), []
    for c in chunks:
        body = _clean(c.get("content") or "")
        if not body.strip():
            continue
        key = (str(c.get("chunk_index")), body)
        if key in seen:
            continue
        seen.add(key)
        parts.append(body)
    text = "\n".join(parts)
    if not text.strip():
        return _fail("chunks_missing", locator, kind=P.KB_DOCUMENT,
                     kb_id=kb_id, doc_id=doc_id,
                     resolver=P.resolver_for(P.KB_DOCUMENT))
    title = str(doc.get("title") or doc.get("file_name") or doc_id)
    return _ok(locator, P.KB_DOCUMENT, parsed, text, parsed.get("section", ""),
               title, max_chars)


def _read_wiki_page(parsed: dict, locator: str, max_chars: int) -> dict:
    kb_id, slug = parsed.get("kb_id", ""), parsed.get("slug", "")
    rows = ke_db.psql_csv(
        "SELECT title, content FROM wiki_pages "
        "WHERE knowledge_base_id = %s AND slug = %s AND deleted_at IS NULL LIMIT 1"
        % (ke_db.sql_str(kb_id), ke_db.sql_str(slug)))
    if not rows:
        return _fail("source_not_found", locator, kind=P.WIKI_PAGE,
                     kb_id=kb_id, slug=slug, resolver=P.resolver_for(P.WIKI_PAGE))
    title = str(rows[0].get("title") or slug)
    return _ok(locator, P.WIKI_PAGE, parsed, rows[0].get("content") or "",
               parsed.get("section", ""), title, max_chars)


def _read_external_doc(parsed: dict, locator: str, max_chars: int) -> dict:
    """外部文档：复用 02 的 `ke_doc_parse.parse(action="section")`（惰性导入）。"""
    import ke_doc_parse as DP  # noqa: PLC0415

    doc_id = parsed.get("doc_id", "")
    section = parsed.get("section", "")
    r = DP.parse(doc_id, action="section", section_path=section,
                 limit_chars=max(6000, int(max_chars or MAX_CHARS) * 4))
    if not r.get("ok"):
        code = r.get("error") or "text_source_unreadable"
        if code == "section_not_found":
            return _fail("section_not_found", locator, kind=P.EXTERNAL_DOC,
                         section=section, available=r.get("available") or [])
        return _fail("text_source_unreadable", locator, kind=P.EXTERNAL_DOC,
                     detail=str(r.get("hint") or r.get("note") or code)[:240],
                     resolver=P.resolver_for(P.EXTERNAL_DOC))
    text = str(r.get("text") or "")
    if not text.strip():
        return _fail("source_empty", locator, kind=P.EXTERNAL_DOC,
                     section=section, resolver=P.resolver_for(P.EXTERNAL_DOC))
    # parse 已经按 section 取回；再统一做 150 字截断
    got = _ok(locator, P.EXTERNAL_DOC, parsed, text, "",
              str(r.get("doc_id") or doc_id), max_chars)
    got["section"] = r.get("path") or section
    return got


def _read_session_text(parsed: dict, locator: str, max_chars: int) -> dict:
    sno, mid = parsed.get("session_no", ""), parsed.get("message_id", "")
    rows = ke_db.psql_csv(
        "SELECT content FROM messages WHERE session_id = %s AND id = %s "
        "AND deleted_at IS NULL LIMIT 1"
        % (ke_db.sql_str(sno), ke_db.sql_str(mid)))
    if not rows:
        return _fail("source_not_found", locator, kind=P.SESSION_TEXT,
                     session_no=sno, message_id=mid,
                     resolver=P.resolver_for(P.SESSION_TEXT))
    return _ok(locator, P.SESSION_TEXT, parsed, rows[0].get("content") or "",
               parsed.get("section", ""), "会话消息 %s" % sno, max_chars)


def excerpt(locator: str, max_chars: int = MAX_CHARS) -> dict:
    """按 locator 回读来源，返回逐字摘录；失败给结构化错误，不生成占位。"""
    loc = str(locator or "").strip()
    parsed = P.parse_locator(loc)
    if not parsed.get("ok"):
        return _fail("locator_invalid", loc, errors=parsed.get("errors") or [],
                     kind=parsed.get("kind") or "")
    kind = parsed.get("kind") or ""
    try:
        if kind == P.KB_DOCUMENT:
            return _read_kb_document(parsed, loc, max_chars)
        if kind == P.WIKI_PAGE:
            return _read_wiki_page(parsed, loc, max_chars)
        if kind == P.SESSION_TEXT:
            return _read_session_text(parsed, loc, max_chars)
        if kind == P.EXTERNAL_DOC:
            return _read_external_doc(parsed, loc, max_chars)
        return _fail("source_not_supported", loc, kind=kind,
                     resolver=P.resolver_for(kind))
    except Exception as exc:  # noqa: BLE001
        return _fail("text_source_unreadable", loc, kind=kind,
                     detail=str(exc)[:240], resolver=P.resolver_for(kind))
