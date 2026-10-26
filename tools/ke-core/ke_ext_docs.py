"""ke-core · **外部接口（模拟）：文档与附件只读访问**

为什么要这个
------------
外部系统的文档**不再允许下载**、只给接口 ⇒ 我们拿不到 `doc_outline` 那种"整篇入库"的便利，
只能按章节/游标取**逐字正文**；附件（含图）也只能按接口取。

    action=list | meta | outline | section | attachment | image
    inline_media=True ⇒ 图片直接回 `data_url`（多模态模型可用；**图不出内网** ✓）

安全：所有文件路径必须落在容器根内（防穿越）；图片有 `max_bytes` 与 mime 白名单。
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


def _docx_sections(path: str) -> list:
    """docx → `[{path,title,text}]`（标准库解析；标题 = `2.1 xxx` 这类行首编号，样式仅辅助）。"""
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


def save_document(doc_id: str, review_no: str, filename: str, raw: bytes, title: str = "",
                  round: int = 1, actor: str = "", session_no: str = "") -> dict:
    """**上传一份文档**（2026-10-26 用户口径：内网工作台未接通前，文档自己上传给模拟服务 ✓）。

    - 文件落 `<root>/it-platform/files/<doc_id>.<ext>`；docx 用标准库切节（正文可按 section 取 ✓）；
    - 元数据落 `docs/<doc_id>.json`，并把 doc_id 挂到 `docs/index.json` 的该编号下；
    - 返回 `doc_id / title / size / sections / url`，随后 `ext_review_flow(action=attach)` 绑到流程。
    """
    import hashlib

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
    sections = _docx_sections(fpath) if ext == ".docx" else [
        {"path": "全文", "title": name, "text": ""}]
    doc = {"doc_id": doc_id, "title": str(title or os.path.splitext(name)[0]),
           "mime": mime, "size": len(raw), "round": int(round or 1),
           "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"), "sha256": sha,
           "files": [{"name": name, "path": "files/" + fname, "mime": mime, "size": len(raw),
                      "sha256": sha}],
           "attachments": [{"name": name, "path": "files/" + fname, "mime": mime,
                            "size": len(raw)}],
           "sections": sections, "images": []}
    ddir = os.path.join(S.system_dir(), "docs")
    os.makedirs(ddir, exist_ok=True)
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
            "url": "%s/files/%s" % (S.public_base(), doc_id)}


def _knowledge_rows(doc_title: str = "", knowledge_id: str = "", kb_id: str = "") -> list:
    """查 WeKnora 的 `knowledges`（附件已在库里 ⇒ 别让用户再走 shell ✗）。"""
    import ke_db  # noqa: PLC0415

    where = ["deleted_at IS NULL"]
    if knowledge_id:
        where.append("id = %s" % ke_db.sql_str(knowledge_id))
    elif doc_title:
        like = ke_db.sql_str("%" + doc_title.strip() + "%")
        where.append("(title ILIKE %s OR file_name ILIKE %s)" % (like, like))
    else:
        return []
    if kb_id:
        where.append("knowledge_base_id = %s" % ke_db.sql_str(kb_id))
    return ke_db.psql_csv(
        "SELECT id, tenant_id, knowledge_base_id, title, COALESCE(file_name,'') AS file_name, "
        "       COALESCE(file_type,'') AS file_type, COALESCE(file_size,0) AS file_size "
        "  FROM knowledges WHERE %s ORDER BY created_at DESC LIMIT 5" % " AND ".join(where))


def _read_knowledge_bytes(tenant_id, knowledge_id: str, file_name: str = "") -> tuple:
    """从 WeKnora 容器读附件原文 → `(bytes, diag)` ✓。

    布局（实测 2026-10-26）：`/data/files/<tenant>/<knowledge_id>/<原文件名>`（**是目录**）；
    兼容"直接是文件"的旧布局。优先取与 `file_name` 同名的那个文件。
    """
    import subprocess  # noqa: PLC0415

    cname = os.environ.get("WEKNORA_APP_CONTAINER") or "WeKnora-app"
    base = "/data/files/%s/%s" % (tenant_id, knowledge_id)
    diag = {"container": cname, "path": base}
    r = subprocess.run(["docker", "exec", cname, "cat", base], capture_output=True)
    if r.returncode == 0 and r.stdout:
        return r.stdout, dict(diag, layout="file")
    ls = subprocess.run(["docker", "exec", cname, "ls", "-1", base], capture_output=True)
    names = [x.strip() for x in (ls.stdout or b"").decode("utf-8", "replace").splitlines()
             if x.strip()]
    diag["entries"] = names[:5]
    pick = file_name if file_name in names else (names[0] if names else "")
    if pick:
        p2 = base + "/" + pick
        r2 = subprocess.run(["docker", "exec", cname, "cat", p2], capture_output=True)
        diag.update(path=p2, rc=r2.returncode)
        if r2.returncode == 0 and r2.stdout:
            return r2.stdout, dict(diag, layout="dir")
    diag["stderr"] = (ls.stderr or b"").decode("utf-8", "replace")[:160]
    return b"", diag


def import_knowledge(review_no: str, doc_title: str = "", knowledge_id: str = "",
                     kb_id: str = "", doc_id: str = "", round: int = 1,
                     actor: str = "", session_no: str = "") -> dict:
    """**把用户上传到知识库的附件**导入模拟 it-platform，并挂到该编号档案（2026-10-26）。

    智能体用法（**不需要 shell** ✓）：用户说"编号 <编号>，附件《<文件名>》" ⇒
        `action=import, review_no=<编号>, doc_title=<文件名>`
    ⇒ 本函数按文件名（或 `knowledge_id`）在 `knowledges` 里定位、从容器读出原文、
      切节后落 `docs/`+`files/`+索引，并**自动建档/绑定**（档案不存在就建 ✓）。

    歧义（同名多份）⇒ 回 `doc_ambiguous` + `candidates`，**不猜** ✗。
    """
    rno = str(review_no or "").strip()
    if not rno:
        return {"ok": False, "tool": TOOL, "error": "need_review_no",
                "hint": "外层主键 = 编号（用户消息里给的那个）"}
    rows = _knowledge_rows(doc_title=doc_title, knowledge_id=knowledge_id, kb_id=kb_id)
    if not rows:
        return {"ok": False, "tool": TOOL, "error": "knowledge_not_found",
                "doc_title": doc_title, "knowledge_id": knowledge_id,
                "hint": "先在知识库上传附件，或把 doc_title 写全（含扩展名）；"
                        "也可直接用 knowledge_id"}
    if len(rows) > 1 and not knowledge_id:
        uniq = {(r.get("id"), r.get("title")) for r in rows}
        if len(uniq) > 1:
            return {"ok": False, "tool": TOOL, "error": "doc_ambiguous", "doc_title": doc_title,
                    "candidates": [{"knowledge_id": r.get("id"), "title": r.get("title"),
                                    "kb_id": r.get("knowledge_base_id")} for r in rows],
                    "hint": "同名附件有多份 ⇒ 指定 `knowledge_id` 或换更精确的 doc_title"}
    row = rows[0]
    kid = str(row.get("id") or "")
    fname = str(row.get("file_name") or row.get("title") or (kid + ".bin"))
    raw, diag = _read_knowledge_bytes(row.get("tenant_id"), kid, fname)
    if not raw:
        return {"ok": False, "tool": TOOL, "error": "file_not_found", "knowledge_id": kid,
                "diag": diag,
                "hint": "容器里读不到附件（默认 WeKnora-app:/data/files/<tenant>/<kid>/<原文件名>）；"
                        "可用 env WEKNORA_APP_CONTAINER 指定容器名"}
    title = str(row.get("title") or os.path.splitext(fname)[0])
    did = doc_id or ("doc-" + kid[:8])
    saved = save_document(doc_id=did, review_no=rno, filename=fname, raw=raw, title=title,
                          round=int(round or 1), actor=actor or "import",
                          session_no=session_no)
    flow = {}
    try:                                          # 自动建档 + 绑定（与 attach 同一实现 ✓）
        import ke_ext_flow as F  # noqa: PLC0415
        flow = F.attach_doc(rno, {k: saved.get(k) for k in
                                  ("doc_id", "title", "mime", "size", "sha256", "round",
                                   "sections", "attachments", "images", "url")},
                            actor=actor or "import", session_no=session_no)
    except Exception as exc:  # noqa: BLE001  绑定失败不影响导入（文档已在索引里 ✓）
        flow = {"ok": False, "error": repr(exc)[:200]}
    return {"ok": True, "tool": TOOL, "review_no": rno, "doc_id": saved["doc_id"],
            "title": saved["title"], "size": saved["size"], "sha256": saved["sha256"],
            "sections": saved["sections"], "url": saved["url"],
            "knowledge": {"knowledge_id": kid, "kb_id": row.get("knowledge_base_id"),
                          "file_name": fname},
            "flow": flow,
            "next": "ext_doc_access(action=outline, doc_id=…) ⇒ section 逐节取正文；"
                    "报告 ① 的文档信息用 `get` 回执"}


def access(kb_id: str = "", object_slug: str = "", action: str = "list",
           review_no: str = "", doc_id: str = "", section_path: str = "",
           cursor: int = 0, limit_chars: int = 6000, inline_media: bool = False,
           max_bytes: int = MAX_IMG_BYTES, doc_title: str = "", knowledge_id: str = "",
           round: int = 1, actor: str = "", session_no: str = "") -> dict:
    """统一入口（**只读**；外部文档/附件/图都从这里取）。

    `action="import"` 是**写**：把用户上传到知识库的附件导入模拟 it-platform 并绑定档案
    （智能体用它，不需要 shell ✓）—— 所以它**不要求** DataObject 绑定（先做文档，再谈契约）。
    """
    action = (action or "list").strip().lower()
    if action == "import":
        return import_knowledge(review_no=review_no, doc_title=doc_title,
                                knowledge_id=knowledge_id, kb_id=kb_id, doc_id=doc_id,
                                round=int(round or 1), actor=actor, session_no=session_no)
    rb = S.resolve_binding(TOOL, kb_id, object_slug)
    if not rb.get("ok"):
        return {"ok": False, "tool": TOOL,
                **{k: rb.get(k) for k in ("why", "hint", "candidates", "object", "binding")
                   if rb.get(k) is not None}}
    binding, obj = rb["binding"], rb["object"]
    base = {"ok": True, "tool": TOOL, "object": obj.get("slug"), "action": action,
            "binding": {"tool": binding.get("tool"), "modes": binding.get("modes"),
                        "impl": binding.get("impl")}, "fetched_at": _now()}

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
                "hint": ("外部系统只给接口、不提供下载 ⇒ 正文用 action=section 按章节取；"
                         "图用 action=image&inline_media=true ✓")}

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
            # 附件**可下载**地址（2026-10-26）：外部系统给 URL，不要求调用方上传 ✓
            "url": "%s/files/%s" % (S.public_base(), d.get("doc_id") or did),
            "files": [dict(f, url="%s/files/%s" % (S.public_base(), d.get("doc_id") or did))
                      for f in (d.get("files") or [])]}

    if action in ("meta", "url"):
        return {**base, **meta, "sections": len(d.get("sections") or []),
                "attachments": len(d.get("attachments") or []),
                "images": len(d.get("images") or []),
                "hint": "`url` = GET 附件流（Content-Disposition: attachment）；正文仍用 action=section ✓"}

    if action == "outline":
        outline = [{"path": s.get("path") or "", "title": s.get("title") or "",
                    "chars": len(s.get("text") or "")} for s in (d.get("sections") or [])]
        return {**base, **meta, "outline": outline, "counts": {"sections": len(outline)},
                "hint": "取正文用 action=section（section_path 或 cursor 定位）"}

    if action == "section":
        secs = d.get("sections") or []
        if not secs:
            return {**base, **meta, "text": "", "counts": {"sections": 0},
                    "note": "该文档没有可读章节（外部系统未提供正文）"}
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
                "hint": ("附件可直接用 `url` 下载（GET，附件流）✓；图请走 action=image "
                         "（`data_url` 可直接进多模态上下文）")}

    if action == "image":
        imgs = [_image_item(x, bool(inline_media), int(max_bytes or MAX_IMG_BYTES))
                for x in (d.get("images") or [])]
        ok = [x for x in imgs if x.get("readable")]
        out = {**base, **meta, "images": imgs, "counts": {"images": len(imgs), "readable": len(ok)},
               "how": ("`data_url` 可直接放进推理上下文 ✓（多模态模型认 data/http）；"
                       "`readable=false` ⇒ 照原因写报告（too_large/not_found/unsupported_type）✗ 不许笼统说取不到")}
        if inline_media and not ok:
            out["next"] = "试 inline_media=false 看原因；或换 max_bytes 更大的取值"
        return out

    return {"ok": False, "tool": TOOL, "error": "未知 action",
            "allowed": ["list", "meta", "outline", "section", "attachment", "image"]}
