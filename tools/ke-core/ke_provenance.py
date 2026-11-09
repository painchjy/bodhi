"""来源定位（provenance locator）—— **统一语法 / 解析 / 校验 / 回读提示**。

口径（2026-11-06 用户确认，见 `ontology/sources/bpmn.ttl::TextualDataObject`）
----------------------------------------------------------------------
文本来源统一用 `bpmn:TextualDataObject` 表达；**只有 wiki_page 有知识页/slug**，
其他来源只保留 locator 表达式（值对象，不建页）。

locator 语法（值 URL 编码；`#` 段分隔）::

    ext-doc:<doc_id>#object:<DataObject 实例名称>#key:<字段>=<值>#section:<路径>
    kb:<kb_id>/doc:<doc_id>#section:<路径>
    session:<session_no>/att:<filename>#sha256:<hash>#section:<路径>
    session:<session_no>/msg:<message_id|hash>#section:<路径>
    page:<kb_id>/<slug>#section:<路径>
    file:<path>#sha256:<hash>#section:<路径>

- **object/key 只允许 ext-doc**：只有外部文档有 DataObject 接口契约与 key 确认方式；
  其他 kind 出现 object/key ⇒ `locator_invalid`。
- 会话来源（session_attachment / session_text）才配 `bmm:sourceSession`；
  其他来源只写 locator + `source_refs` / `external_ref`。
- 解析失败/不可读 ⇒ 调用方回 `locator_invalid` / `text_source_unreadable`，**不得猜**。

本模块**无领域词、无 IO**（纯字符串处理）；回读原文由调用方按 `resolver_for()` 提示执行。
"""
from __future__ import annotations

from urllib.parse import quote, unquote

# kind 常量（与 TTL comment 保持一致）
EXTERNAL_DOC = "external_doc"
KB_DOCUMENT = "kb_document"
SESSION_ATTACHMENT = "session_attachment"
SESSION_TEXT = "session_text"
WIKI_PAGE = "wiki_page"
FILE = "file"

KINDS = (EXTERNAL_DOC, KB_DOCUMENT, SESSION_ATTACHMENT, SESSION_TEXT, WIKI_PAGE, FILE)
SESSION_KINDS = (SESSION_ATTACHMENT, SESSION_TEXT)

# kind → 回读方式（提示给调用方；实际工具名在 02 侧）
_RESOLVER = {
    EXTERNAL_DOC: {"tool": "ext_doc_parse", "action": "section",
                   "hint": "按 doc_id 拉原件，用 section_path 取逐字正文"},
    KB_DOCUMENT: {"tool": "doc_outline|grep_chunks|knowledge_lookup", "action": "",
                  "hint": "按 kb_id + doc_id 取文档/片段"},
    SESSION_ATTACHMENT: {"tool": "ext_attach_probe|ext_doc_parse", "action": "",
                         "hint": "会话页 + 附件名/sha256 定位"},
    SESSION_TEXT: {"tool": "knowledge_lookup", "action": "page",
                   "hint": "会话页 + message_id/hash 定位"},
    WIKI_PAGE: {"tool": "knowledge_lookup", "action": "page",
                "hint": "按 kb_id + slug 取页面正文"},
    FILE: {"tool": "read_file", "action": "",
           "hint": "按路径 + sha256 读取/解析文件"},
}


def _q(v: str) -> str:
    return quote(str(v or ""), safe="")


def _u(v: str) -> str:
    return unquote(str(v or ""))


def is_session(kind: str) -> bool:
    return str(kind or "").strip().lower() in SESSION_KINDS


def resolver_for(kind: str) -> dict:
    """该 kind 的回读方式（工具/动作/提示）；未知 kind ⇒ {}。"""
    return dict(_RESOLVER.get(str(kind or "").strip().lower()) or {})


def needs_object_key(kind: str) -> bool:
    """只有 ext-doc 允许/要求 object/key。"""
    return str(kind or "").strip().lower() == EXTERNAL_DOC


def build_locator(kind: str, **kw) -> str:
    """构造 locator。必填缺失 ⇒ `ValueError`（调用方转结构化回执）。

    允许字段：doc_id / kb_id / session_no / filename / message_id / path /
    object / key / sha256 / section。
    """
    k = str(kind or "").strip().lower()
    if k not in KINDS:
        raise ValueError("locator_invalid: 未知 kind=%r（可选 %s）" % (kind, "/".join(KINDS)))
    section = str(kw.get("section") or "").strip()
    tail = ("#section:%s" % _q(section)) if section else ""
    if k == EXTERNAL_DOC:
        doc_id = str(kw.get("doc_id") or "").strip()
        obj = str(kw.get("object") or "").strip()
        key = str(kw.get("key") or "").strip()
        if not doc_id or not obj or not key:
            raise ValueError("locator_invalid: ext-doc 需要 doc_id + object + key")
        if "=" not in key:
            raise ValueError("locator_invalid: ext-doc 的 key 必须是 <字段>=<值>")
        f, _, v = key.partition("=")
        return "ext-doc:%s#object:%s#key:%s=%s%s" % (
            _q(doc_id), _q(obj), _q(f), _q(v), tail)
    if k == KB_DOCUMENT:
        kb_id = str(kw.get("kb_id") or "").strip()
        doc_id = str(kw.get("doc_id") or "").strip()
        if not kb_id or not doc_id:
            raise ValueError("locator_invalid: kb 需要 kb_id + doc_id")
        return "kb:%s/doc:%s%s" % (_q(kb_id), _q(doc_id), tail)
    if k == SESSION_ATTACHMENT:
        sno = str(kw.get("session_no") or "").strip()
        fn = str(kw.get("filename") or "").strip()
        if not sno or not fn:
            raise ValueError("locator_invalid: session/att 需要 session_no + filename")
        sha = str(kw.get("sha256") or "").strip()
        mid = ("#sha256:%s" % _q(sha)) if sha else ""
        return "session:%s/att:%s%s%s" % (_q(sno), _q(fn), mid, tail)
    if k == SESSION_TEXT:
        sno = str(kw.get("session_no") or "").strip()
        mid = str(kw.get("message_id") or kw.get("msg") or kw.get("sha256") or "").strip()
        if not sno or not mid:
            raise ValueError("locator_invalid: session/msg 需要 session_no + message_id/hash")
        return "session:%s/msg:%s%s" % (_q(sno), _q(mid), tail)
    if k == WIKI_PAGE:
        kb_id = str(kw.get("kb_id") or "").strip()
        slug = str(kw.get("slug") or "").strip()
        if not kb_id or not slug:
            raise ValueError("locator_invalid: page 需要 kb_id + slug")
        return "page:%s/%s%s" % (_q(kb_id), _q(slug), tail)
    # FILE
    path = str(kw.get("path") or "").strip()
    if not path:
        raise ValueError("locator_invalid: file 需要 path")
    sha = str(kw.get("sha256") or "").strip()
    mid = ("#sha256:%s" % _q(sha)) if sha else ""
    return "file:%s%s%s" % (_q(path), mid, tail)


def locator_kind(locator: str) -> str:
    """从前缀判断 kind（不做完整校验）；未知 ⇒ ""。"""
    s = str(locator or "").strip()
    if s.startswith("ext-doc:"):
        return EXTERNAL_DOC
    if s.startswith("kb:"):
        return KB_DOCUMENT
    if s.startswith("session:"):
        return SESSION_ATTACHMENT if "/att:" in s.split("#", 1)[0] else SESSION_TEXT
    if s.startswith("page:"):
        return WIKI_PAGE
    if s.startswith("file:"):
        return FILE
    return ""


def parse_locator(locator: str) -> dict:
    """解析 locator → `{ok, kind, ..., errors[]}`；严格按语法（object/key 只许 ext-doc）。"""
    s = str(locator or "").strip()
    errors: list[str] = []
    kind = locator_kind(s)
    if not kind:
        return {"ok": False, "kind": "", "locator": s,
                "errors": ["locator_invalid: 前缀必须是 ext-doc/kb/session/page/file"]}
    head, *segs = s.split("#")
    out: dict = {"ok": False, "kind": kind, "locator": s, "section": ""}
    for seg in segs:
        if seg.startswith("section:"):
            out["section"] = _u(seg[len("section:"):])
        elif seg.startswith("object:"):
            out["object"] = _u(seg[len("object:"):])
        elif seg.startswith("key:"):
            kv = seg[len("key:"):]
            f, _, v = kv.partition("=")
            out["key_field"], out["key_value"] = _u(f), _u(v)
            out["key"] = "%s=%s" % (_u(f), _u(v))
        elif seg.startswith("sha256:"):
            out["sha256"] = _u(seg[len("sha256:"):])
        else:
            errors.append("locator_invalid: 未知段 %r" % seg[:40])
    if ("object" in out or "key" in out or out.get("key_field")) and kind != EXTERNAL_DOC:
        errors.append("locator_invalid: object/key 只允许 ext-doc（当前 %s）" % kind)
    body = head.split(":", 1)[1] if ":" in head else ""
    if kind == EXTERNAL_DOC:
        out["doc_id"] = _u(body)
        if not out["doc_id"]:
            errors.append("locator_invalid: ext-doc 缺 doc_id")
        if not out.get("object"):
            errors.append("locator_invalid: ext-doc 缺 object（DataObject 实例名称）")
        if not out.get("key_field") or out.get("key_value") in (None, ""):
            errors.append("locator_invalid: ext-doc 缺 key=<字段>=<值>")
    elif kind == KB_DOCUMENT:
        if "/doc:" not in body:
            errors.append("locator_invalid: kb 需要 <kb_id>/doc:<doc_id>")
        else:
            kb, _, doc = body.partition("/doc:")
            out["kb_id"], out["doc_id"] = _u(kb), _u(doc)
            if not out["kb_id"] or not out["doc_id"]:
                errors.append("locator_invalid: kb/doc 为空")
    elif kind == SESSION_ATTACHMENT:
        if "/att:" not in body:
            errors.append("locator_invalid: session/att 需要 <session_no>/att:<filename>")
        else:
            sno, _, fn = body.partition("/att:")
            out["session_no"], out["filename"] = _u(sno), _u(fn)
            if not out["session_no"] or not out["filename"]:
                errors.append("locator_invalid: session/att 为空")
    elif kind == SESSION_TEXT:
        if "/msg:" not in body:
            errors.append("locator_invalid: session/msg 需要 <session_no>/msg:<message_id|hash>")
        else:
            sno, _, mid = body.partition("/msg:")
            out["session_no"], out["message_id"] = _u(sno), _u(mid)
            if not out["session_no"] or not out["message_id"]:
                errors.append("locator_invalid: session/msg 为空")
    elif kind == WIKI_PAGE:
        kb, _, slug = body.partition("/")
        out["kb_id"], out["slug"] = _u(kb), _u(slug)
        if not out["kb_id"] or not out["slug"]:
            errors.append("locator_invalid: page 需要 <kb_id>/<slug>")
    elif kind == FILE:
        out["path"] = _u(body)
        if not out["path"]:
            errors.append("locator_invalid: file 缺 path")
    out["errors"] = errors
    out["ok"] = not errors
    return out


def validate_locator(locator: str) -> dict:
    """轻量校验（= parse_locator；语义化别名，便于调用方阅读）。"""
    return parse_locator(locator)


