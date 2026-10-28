"""ke-core · 外部接口（模拟）：**文档与附件存取**（纯存储：零解析 / 零 PG / 零容器依赖）

定位（2026-10-28 用户口径）
--------------------------
本文件是 **it_platform（04 包）自己的实现**，只做「文件存取 + 元数据 + 索引」：

- **不解析正文** —— PDF/DOCX 的正文与内嵌图由 **bodhi-mcp** 侧的 `ke_doc_parse` 负责：
  它 `GET /files/<doc_id>` 拿原件，再本地解析（PDF 走 docreader 的 stdin，不共享磁盘）；
- **不连 PG、不 `docker exec`、不碰 WeKnora** ⇒ 04 包可单独部署（systemd 或容器，都不需要 02/WeKnora）；
- 文件正本只在这里（`BODHI_EXT_ROOT/it-platform/docs|files`），外部系统是**唯一权威**。

能力
----
    save_document(...)   收一份文件（POST /files/<doc_id> 调它）→ 元数据 + 索引
    doc_file(...)        下发改文件（GET /files/<doc_id>）
    access(action=list|meta|url|outline|section|attachment|image)   读取（正文取决于是否已被平台解析回写）
    access(action=import) 把**已收下**的文件挂到编号档案（`ke_ext_flow.attach_doc`）

口径
----
- 写默认 `dry_run=True`（`import_knowledge` 除外：它只是绑定）；
- 幂等键 `(review_no, doc_id)`；重复上传同 `doc_id` = 覆盖文件 + 更新元数据；
- 代码里不出现任何业务名词（业务取值只在 `schema.json` 与容器数据里）。
"""
from __future__ import annotations

import base64
import hashlib
import os
import re
import time

import ke_ext_store as S

TOOL = "ext_doc_access"
_IMG_EXT = (".png", ".jpg", ".jpeg", ".gif", ".webp")
MAX_IMG_BYTES = 1_500_000
# 04 侧不解析正文；这两个提示贯穿所有回执（让调用方知道去哪儿取正文）
NEXT_PARSE = ("正文/图请调 **bodhi-mcp** 的 `ext_doc_parse(doc_id=…, action=outline|section|image)`；"
              "本服务只存文件、只给下载地址")


def _now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def _docs_dir() -> str:
    d = os.path.join(S.system_dir(), "docs")
    os.makedirs(d, exist_ok=True)
    return d


def _index() -> dict:
    d = S.read_json(os.path.join(_docs_dir(), "index.json"), {}) or {}
    return d if isinstance(d, dict) else {}


def _load(doc_id: str) -> dict:
    safe = str(doc_id or "").replace("/", "_")
    return S.read_json(os.path.join(_docs_dir(), safe + ".json"), {}) or {}


def _safe_path(rel: str) -> str:
    """容器内相对路径 → 绝对路径（**必须**落在容器根内；否则回空 ✓）。"""
    root = os.path.abspath(S.system_dir())
    p = os.path.abspath(os.path.join(root, str(rel or "").replace("\\", "/")))
    return p if (p == root or p.startswith(root + os.sep)) else ""


def _mime_of(raw: bytes) -> str:
    if raw[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if raw[:3] == b"GIF":
        return "image/gif"
    if raw[:4] == b"RIFF" and raw[8:12] == b"WEBP":
        return "image/webp"
    return "image/jpeg"


def _image_item(x: dict, inline: bool, max_bytes: int) -> dict:
    """一张图 → `{name, mime, bytes, sha256, data_url?}`（不可读时给结构化原因 ✓）。"""
    name = str(x.get("name") or "")
    path = _safe_path(str(x.get("path") or ""))
    if not path or not os.path.isfile(path):
        return {"name": name, "readable": False, "reason": "not_found"}
    if not name.lower().endswith(_IMG_EXT):
        return {"name": name, "readable": False, "reason": "unsupported_type"}
    size = os.path.getsize(path)
    if size > int(max_bytes):
        return {"name": name, "readable": False, "reason": "too_large", "bytes": size}
    with open(path, "rb") as fh:
        raw = fh.read()
    mime = _mime_of(raw)
    item = {"name": name, "mime": mime, "bytes": size, "readable": True,
            "sha256": hashlib.sha256(raw).hexdigest()[:16]}
    if inline:
        item["data_url"] = "data:%s;base64,%s" % (mime, base64.b64encode(raw).decode())
    return item


def doc_file(doc_id: str, name: str = "") -> dict:
    """**下载用**：`doc_id`（+可选附件名）→ `{ok, path, name, mime}`（路径必须落在容器根内 ✓）。"""
    d = _load(doc_id)
    if not d:
        return {"ok": False, "error": "doc_not_found"}
    cands = list(d.get("files") or []) + list(d.get("attachments") or [])
    pick = None
    for x in cands:
        if name and (x.get("name") == name or str(name) in str(x.get("name") or "")):
            pick = x
            break
    if pick is None and cands:
        pick = cands[0]
    if pick is None:
        return {"ok": False, "error": "no_file"}
    p = _safe_path(str(pick.get("path") or ""))
    if not p or not os.path.isfile(p):
        return {"ok": False, "error": "file_not_found", "path": pick.get("path")}
    return {"ok": True, "path": p, "name": pick.get("name") or os.path.basename(p),
            "mime": pick.get("mime") or d.get("mime") or "application/octet-stream"}



# ---------------------------------------------------------------------------
# 纯标准库解析工具（04 侧**不自动调用**；02 侧 ke_doc_parse 复用它）
# ---------------------------------------------------------------------------
def _sections_from_text(text: str, title: str) -> list:
    """把**解析后的纯文本**按标题切节（`**2.3** xxx` / `2.3 xxx` / markdown 标题）。"""
    import re as _re

    lines = (text or "").splitlines()
    heads = [(i, m.group(0)) for i, ln in enumerate(lines)
             for m in [_re.match(r"^\s*(?:\*\*)?(\d+(?:\.\d+){0,3})(?:\*\*)?\s+\S", ln)] if m]
    if not heads:
        return [{"path": "全文", "title": title, "text": text or ""}]
    out = []
    for k, (idx, h) in enumerate(heads):
        end = heads[k + 1][0] if k + 1 < len(heads) else len(lines)
        num = _re.match(r"^\s*(?:\*\*)?(\d+(?:\.\d+){0,3})", h).group(1)
        body = "\n".join(lines[idx + 1:end]).strip()
        out.append({"path": "%s %s" % (num, lines[idx].strip().lstrip("*# ").split(" ", 1)[-1][:30]),
                    "title": lines[idx].strip()[:80], "text": body})
    return out


def _docx_sections(path: str) -> list:
    """docx → `[{path,title,text}]`（标准库；标题 = `2.1 xxx` 行首编号，样式仅辅助）。"""
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


def _docx_images(raw: bytes, doc_id: str) -> list:
    """docx 里的 `word/media/*` → 落盘并返回 images 列表（图证据可用 ✓）。"""
    import io
    import zipfile

    if not raw.startswith(b"PK"):
        return []
    out = []
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as z:
            media = [n for n in z.namelist() if n.startswith("word/media/")]
            media.sort()
            sub = os.path.join(S.system_dir(), "files")
            os.makedirs(sub, exist_ok=True)
            for i, n in enumerate(media, 1):
                ext = os.path.splitext(n)[1].lower() or ".png"
                fn = "%s_img%d%s" % (doc_id, i, ext)
                with open(os.path.join(sub, fn), "wb") as fh:
                    fh.write(z.read(n))
                out.append({"name": "图片%d%s" % (i, ext), "path": "files/" + fn})
    except Exception:  # noqa: BLE001
        return []
    return out


# ---------------------------------------------------------------------------
# 写入：收文件（POST /files/<doc_id> 的唯一落点）
# ---------------------------------------------------------------------------
def save_document(doc_id: str, review_no: str, filename: str, raw: bytes, title: str = "",
                  round: int = 1, actor: str = "", session_no: str = "",
                  sections: list | None = None, images: list | None = None,
                  extra: dict | None = None) -> dict:
    """**收下一份文件**（本服务只存不解析：`sections` 由调用方显式给，缺省为空）。

    - 文件落 `<root>/it-platform/files/<doc_id>.<ext>`；元数据落 `docs/<doc_id>.json`；
    - `doc_id` 挂到 `docs/index.json` 的该编号下；
    - 返回 `doc_id / title / size / sha256 / url`，随后 `ext_review_flow(action=attach)` 绑到流程。
    """
    doc_id = re.sub(r"[^A-Za-z0-9._-]+", "-", str(doc_id or "").strip()).strip("-") \
        or ("doc-" + time.strftime("%Y%m%d%H%M%S"))
    name = os.path.basename(str(filename or "") or (doc_id + ".bin"))
    ext = os.path.splitext(name)[1].lower()
    sub = os.path.join(S.system_dir(), "files")
    os.makedirs(sub, exist_ok=True)
    fname = doc_id + (ext or ".bin")
    fpath = os.path.join(sub, fname)
    with open(fpath, "wb") as fh:
        fh.write(raw)
    mime = {".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            ".pdf": "application/pdf", ".md": "text/markdown", ".txt": "text/plain",
            ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png"}.get(
        ext, "application/octet-stream")
    sha = hashlib.sha256(raw).hexdigest()[:16]
    sections = list(sections or [])
    doc = {"doc_id": doc_id, "title": str(title or os.path.splitext(name)[0]),
           "mime": mime, "size": len(raw), "round": int(round or 1),
           "updated_at": _now(), "sha256": sha,
           "files": [{"name": name, "path": "files/" + fname, "mime": mime, "size": len(raw),
                      "sha256": sha}],
           "attachments": [{"name": name, "path": "files/" + fname, "mime": mime,
                            "size": len(raw)}],
           "sections": sections, "images": list(images or [])}
    for k, v in (extra or {}).items():
        doc[k] = v
    ddir = _docs_dir()
    dp = os.path.join(ddir, doc_id + ".json")
    with S.locked(dp):
        S.write_json(dp, doc)
        ip = os.path.join(ddir, "index.json")
        idx = S.read_json(ip, {}) or {}
        ids = [str(x) for x in (idx.get(str(review_no or "")) or [])]
        if doc_id not in ids:
            ids.append(doc_id)
        idx[str(review_no or "_default")] = ids
        defs = [str(x) for x in (idx.get("_default") or [])]
        if doc_id not in defs:
            defs.append(doc_id)
        idx["_default"] = defs
        S.write_json(ip, idx)
    S.audit("docs:upload", {"tool": TOOL, "actor": actor, "session_no": session_no,
                            "key": "%s/%s" % (review_no, doc_id),
                            "size": len(raw), "sections": len(sections)})
    return {"ok": True, "doc_id": doc_id, "title": doc["title"], "mime": mime,
            "size": len(raw), "sha256": sha, "sections": len(sections),
            "round": doc["round"], "path": "files/" + fname,
            "url": "%s/files/%s" % (S.public_base(), doc_id), "next": NEXT_PARSE}




# ---------------------------------------------------------------------------
# 绑定：把已收下的文件挂到流程档案（唯一入口）
# ---------------------------------------------------------------------------
def import_knowledge(review_no: str, doc_id: str = "", doc_title: str = "", knowledge_id: str = "",
                     kb_id: str = "", round: int = 1, actor: str = "", session_no: str = "",
                     source: str = "local") -> dict:
    """把**已经收下**的文件挂到编号档案（2026-10-28 用户口径）。

    > 本服务**不看知识库、不猜文件名、不扫容器**：文件必须先由平台方推送进来
    > （`POST /files/<doc_id>`，bodhi-mcp 的 `ext_doc_push` 会自动做），这里只做绑定。
    """
    rno = str(review_no or "").strip()
    if not rno:
        return {"ok": False, "tool": TOOL, "error": "need_review_no",
                "hint": "外层主键 = 流程编号（用户消息里给的那个）"}
    did = str(doc_id or knowledge_id or "").strip()
    if not did:
        return {"ok": False, "tool": TOOL, "error": "need_doc_id",
                "hint": ("本服务只存文件：先 `POST /files/<doc_id>` 上传"
                         "（bodhi-mcp 的 `ext_doc_push(review_no, doc_title)` 会自动推送并绑定），"
                         "再 `action=import` 绑定档案"),
                "doc_title": doc_title}
    d = _load(did)
    if not d:
        return {"ok": False, "tool": TOOL, "error": "doc_not_found", "doc_id": did,
                "hint": "该 doc_id 不在本服务里：先 POST /files/<doc_id> 上传文件",
                "known": sorted(str(k) for k in (_index().get("_default") or []))[:10]}
    flow = {}
    try:
        import ke_ext_flow as F  # noqa: PLC0415
        flow = F.attach_doc(rno, {k: d.get(k) for k in
                                  ("doc_id", "title", "mime", "size", "sha256", "round",
                                   "sections", "attachments", "images")},
                            actor=actor or "import", session_no=session_no)
    except Exception as exc:  # noqa: BLE001
        flow = {"ok": False, "error": repr(exc)[:200]}
    return {"ok": True, "tool": TOOL, "review_no": rno, "doc_id": d.get("doc_id") or did,
            "title": d.get("title") or "", "size": d.get("size") or 0,
            "sha256": d.get("sha256") or "", "mime": d.get("mime") or "",
            "sections": len(d.get("sections") or []), "images": len(d.get("images") or []),
            "url": "%s/files/%s" % (S.public_base(), d.get("doc_id") or did),
            "source": "local", "flow": flow, "next": NEXT_PARSE}



# ---------------------------------------------------------------------------
# 读取入口
# ---------------------------------------------------------------------------
def access(kb_id: str = "", object_slug: str = "", action: str = "list",
           review_no: str = "", doc_id: str = "", section_path: str = "",
           cursor: int = 0, limit_chars: int = 6000, inline_media: bool = False,
           max_bytes: int = MAX_IMG_BYTES, doc_title: str = "", knowledge_id: str = "",
           round: int = 1, actor: str = "", session_no: str = "",
           source: str = "local") -> dict:
    """统一入口（读写）。

    - `action="import"`：把**已收下**的文件挂到编号档案（不解析、不查知识库）；
    - 其余只读：元数据 / 附件地址 / 已回写的正文与图（**04 自己不解析**，见 `NEXT_PARSE`）。
    """
    action = (action or "list").strip().lower()
    if action == "import":
        return import_knowledge(review_no=review_no, doc_id=doc_id, doc_title=doc_title,
                                knowledge_id=knowledge_id, kb_id=kb_id,
                                round=int(round or 1), actor=actor, session_no=session_no,
                                source=source)
    # 04 是**纯文件服务**：默认**不解析 DataObject 绑定**（那需要 PG / wiki，属 bodhi-mcp 侧职责）。
    # 只有调用方显式给了 kb_id/object 时才顺手解析一次，解析不到也不阻断取文件 ✓
    base = {"ok": True, "tool": TOOL, "action": action, "fetched_at": _now()}
    if kb_id or object_slug:
        rb = S.resolve_binding(TOOL, kb_id, object_slug)
        if rb.get("ok"):
            binding, obj = rb["binding"], rb["object"]
            base["object"] = obj.get("slug")
            base["binding"] = {"tool": binding.get("tool"), "modes": binding.get("modes"),
                               "impl": binding.get("impl")}


    if action == "list":
        idx = _index()
        ids = list(idx.get(str(review_no or ""), []) or [])
        if not ids:                                  # 没给外层键 ⇒ 全量清单（不含正文）
            ids = [k for k in idx.keys() if not str(k).startswith("_")]
        rows = []
        for did in ids[:50]:
            d = _load(did)
            if d:
                rows.append({"doc_id": d.get("doc_id") or did, "title": d.get("title") or "",
                             "mime": d.get("mime") or "", "size": d.get("size") or 0,
                             "updated_at": d.get("updated_at") or "",
                             "sections": len(d.get("sections") or []),
                             "attachments": len(d.get("attachments") or []),
                             "images": len(d.get("images") or [])})
        return {**base, "review_no": review_no, "rows": rows, "counts": {"docs": len(rows)},
                "hint": NEXT_PARSE}

    did = str(doc_id or "").strip()
    if not did:
        return {"ok": False, "tool": TOOL, "error": "该 action 需要 doc_id",
                "hint": "先 action=list（可按 review_no 过滤）拿 doc_id"}
    d = _load(did)
    if not d:
        return {"ok": False, "tool": TOOL, "error": "doc_not_found", "doc_id": did}
    meta = {"doc_id": d.get("doc_id") or did, "title": d.get("title") or "",
            "mime": d.get("mime") or "", "size": d.get("size") or 0,
            "updated_at": d.get("updated_at") or "", "sha256": d.get("sha256") or "",
            # 附件**可下载**地址（外部系统给 URL，不要求调用方上传 ✓）
            "url": "%s/files/%s" % (S.public_base(), d.get("doc_id") or did),
            "files": [dict(f, url="%s/files/%s" % (S.public_base(), d.get("doc_id") or did))
                      for f in (d.get("files") or [])]}

    if action in ("meta", "url"):
        return {**base, **meta, "sections": len(d.get("sections") or []),
                "attachments": len(d.get("attachments") or []),
                "images": len(d.get("images") or []),
                "hint": "`url` = GET 附件流（Content-Disposition: attachment）；" + NEXT_PARSE}

    if action == "outline":
        outline = [{"path": s.get("path") or "", "title": s.get("title") or "",
                    "chars": len(s.get("text") or "")} for s in (d.get("sections") or [])]
        out = {**base, **meta, "outline": outline, "counts": {"sections": len(outline)}}
        if not outline:
            out["note"] = "本服务不解析正文（只存文件）"
            out["next"] = NEXT_PARSE
        else:
            out["hint"] = "取正文用 action=section（section_path 或 cursor 定位）"
        return out

    if action == "section":
        secs = d.get("sections") or []
        if not secs:
            return {**base, **meta, "text": "", "counts": {"sections": 0},
                    "note": "本服务不解析正文（只存文件）", "next": NEXT_PARSE}
        i = int(cursor or 0)
        if section_path:
            hit = [k for k, s in enumerate(secs)
                   if str(s.get("path") or "").startswith(str(section_path))]
            if not hit:
                return {"ok": False, "tool": TOOL, "error": "section_not_found",
                        "section_path": section_path,
                        "available": [s.get("path") for s in secs][:20]}
            i = hit[0]
        i = max(0, min(i, len(secs) - 1))
        s = secs[i]
        text = str(s.get("text") or "")
        cut = text[:max(200, int(limit_chars or 6000))]
        out = {**base, **meta, "path": s.get("path") or "", "title": s.get("title") or "",
               "text": cut, "chars": len(text), "truncated": len(cut) < len(text),
               "cursor": i, "counts": {"sections": len(secs)}}
        if i + 1 < len(secs):
            out["next_cursor"] = i + 1
            out["next_path"] = secs[i + 1].get("path") or ""
        return out

    if action == "attachment":
        rows = [{"name": a.get("name") or "", "mime": a.get("mime") or "",
                 "size": a.get("size") or 0,
                 "url": "%s/files/%s" % (S.public_base(), d.get("doc_id") or did),
                 "readable": bool(_safe_path(str(a.get("path") or "")) and
                                  os.path.isfile(_safe_path(str(a.get("path") or ""))))}
                for a in (d.get("attachments") or [])]
        return {**base, **meta, "rows": rows, "counts": {"attachments": len(rows)},
                "hint": ("附件可直接用 `url` 下载（GET 附件流）✓；图与正文解析见 "
                         "`ext_doc_parse`（bodhi-mcp）")}

    if action == "image":
        imgs = [_image_item(x, bool(inline_media), int(max_bytes or MAX_IMG_BYTES))
                for x in (d.get("images") or [])]
        ok = [x for x in imgs if x.get("readable")]
        out = {**base, **meta, "images": imgs, "counts": {"images": len(imgs), "readable": len(ok)},
               "how": ("`data_url` 可直接放进推理上下文 ✓；`readable=false` ⇒ 照原因写报告"
                       "（too_large/not_found/unsupported_type）✗ 不许笼统说取不到")}
        if not imgs:
            out["note"] = "本服务不解析文档内嵌图（只存文件）"
            out["next"] = NEXT_PARSE
        elif inline_media and not ok:
            out["next"] = "试 inline_media=false 看原因；或换 max_bytes 更大的取值"
        return out

    return {"ok": False, "tool": TOOL, "error": "未知 action",
            "allowed": ["list", "meta", "url", "outline", "section", "attachment", "image",
                        "import"]}
