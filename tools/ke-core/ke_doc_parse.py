"""bodhi-mcp 侧 · **文档正文解析**（原件由 it_platform 提供，本地解析，不与外部共享磁盘）

分工（2026-10-28 用户口径）
--------------------------
- **it_platform（04）只存文件**：`POST/GET /files/<doc_id>`，不解析、零 PG、零 docker；
- **本模块**：`GET {IT_MCP_PUBLIC_BASE}/files/<doc_id>` 拿原件 → 本地解析：
  · PDF  → docreader 容器（`docker exec -i … python3 helper`，**bytes 走 stdin**，不挂共享目录）
  · DOCX → 标准库（zipfile + XML，复用 `ke_ext_docs._docx_sections`）
  · TXT/MD → 直读 + 标题切节
- 解析结果**不落外部**：图落 `BODHI_DOC_ROOT/files/`（bodhi-mcp 自己的目录），供 `ext_doc_parse(action=image)` 取。

为什么这样切：内网 02/04 是两台/两个 systemd 服务，**没有共享盘**；docreader 也只有一个
`/tmp/docreader` 卷 ⇒ 只能走 stdin。这样 04 保持"纯外部服务"，解析能力留在平台侧。
"""
from __future__ import annotations

import base64
import json
import os
import subprocess

import ke_ext_docs as D          # 复用纯标准库的切节/取图（04 侧也带同一份）
import ke_ext_store as S

_PDF_HELPER = r'''
import sys, json, io, base64
data = sys.stdin.buffer.read()
out = {"pages": [], "images": [], "engine": "pypdf"}
try:
    from pypdf import PdfReader
except Exception as e:
    print(json.dumps({"error": "pypdf_missing", "detail": repr(e)})); sys.exit(0)
try:
    rd = PdfReader(io.BytesIO(data))
except Exception as e:
    print(json.dumps({"error": "pdf_open_failed", "detail": repr(e)})); sys.exit(0)
for i, pg in enumerate(rd.pages, 1):
    try:
        t = pg.extract_text() or ""
    except Exception:
        t = ""
    out["pages"].append({"no": i, "text": t})
n = 0
for i, pg in enumerate(rd.pages, 1):
    try:
        imgs = list(getattr(pg, "images", []) or [])
    except Exception:
        imgs = []
    for j, img in enumerate(imgs, 1):
        if n >= 12:
            break
        try:
            b = img.data
        except Exception:
            continue
        if not b or len(b) > 3 * 1024 * 1024:
            continue
        nm = str(getattr(img, "name", "") or "")
        ext = (nm.rsplit(".", 1)[-1].lower() if "." in nm else "png") or "png"
        if ext not in ("png", "jpg", "jpeg", "gif", "bmp", "emf", "wmf"):
            ext = "png"
        out["images"].append({"page": i, "index": j, "ext": ext,
                              "b64": base64.b64encode(b).decode()})
        n += 1
print(json.dumps(out))
'''


def doc_root() -> str:
    """解析产物的落地根（bodhi-mcp 自己的目录；与 04 的 `BODHI_EXT_ROOT` **无关**）。"""
    root = os.environ.get("BODHI_DOC_ROOT") or os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
        "state", "doc_parse")
    os.makedirs(os.path.join(root, "files"), exist_ok=True)
    return root


def _docreader_container() -> str:
    return os.environ.get("WEKNORA_DOCREADER_CONTAINER") or "WeKnora-docreader"


def _pdf_extract(raw: bytes) -> dict:
    """借 docreader 的 pypdf 解析 PDF（脚本 + 二进制都走 stdin，**不共享磁盘** ✓）。"""
    cname = _docreader_container()
    helper = "/tmp/bodhi_pdf_extract.py"
    try:
        subprocess.run(["docker", "exec", "-i", cname, "sh", "-c", "cat > %s" % helper],
                       input=_PDF_HELPER.encode(), capture_output=True, timeout=20)
        r = subprocess.run(["docker", "exec", "-i", cname, "python3", helper],
                           input=raw, capture_output=True, timeout=120)
    except FileNotFoundError:
        return {"error": "docker_missing",
                "detail": "宿主机没有 docker 命令；docreader 不可达"}
    except subprocess.TimeoutExpired:
        return {"error": "docreader_timeout", "detail": "docreader 超过 120s 未返回"}
    if r.returncode != 0:
        return {"error": "extractor_failed",
                "detail": (r.stderr or b"").decode("utf-8", "replace")[:300]}
    try:
        return json.loads((r.stdout or b"{}").decode("utf-8", "replace") or "{}")
    except json.JSONDecodeError:
        return {"error": "extractor_bad_json",
                "detail": (r.stdout or b"")[:200].decode("utf-8", "replace")}



def fetch(doc_id: str) -> tuple:
    """从 it_platform 取原件 → `(bytes, meta)`；拿不到回 `(b"", {error…})`（不抛）。"""
    import urllib.error
    import urllib.request

    did = str(doc_id or "").strip()
    if not did:
        return b"", {"error": "need_doc_id"}
    url = "%s/files/%s" % (S.public_base().rstrip("/"), did)
    try:
        with urllib.request.urlopen(url,
                                    timeout=int(os.environ.get("BODHI_DOC_FETCH_TIMEOUT", "120"))) as r:
            raw = r.read()
            return raw, {"url": url, "bytes": len(raw),
                         "content_type": r.headers.get("Content-Type") or ""}
    except urllib.error.HTTPError as exc:
        return b"", {"error": "ext_file_http_%s" % exc.code, "url": url,
                     "detail": exc.read()[:200].decode("utf-8", "replace")}
    except Exception as exc:  # noqa: BLE001
        return b"", {"error": "ext_unreachable", "url": url, "detail": repr(exc)[:200]}


def _sections_and_images(raw: bytes, doc_id: str, title: str, name: str) -> tuple:
    """按类型选解析器 → `(sections, images, quality)`。"""
    ext = os.path.splitext(str(name or ""))[1].lower()
    if raw[:5] == b"%PDF-":
        got = _pdf_extract(raw)
        if got.get("error"):
            return [], [], {"text_source": "none", "images_available": False,
                            "error": got.get("error"), "detail": got.get("detail", "")}
        pages = got.get("pages") or []
        text = "\n".join((p.get("text") or "").strip() for p in pages).strip()
        secs = D._sections_from_text(text, title) if text else [
            {"path": "全文", "title": title, "text": ""}]
        sub = os.path.join(doc_root(), "files")
        images = []
        for k, im in enumerate(got.get("images") or [], 1):
            try:
                data = base64.b64decode(im.get("b64") or "")
            except Exception:  # noqa: BLE001
                continue
            if not data:
                continue
            fn = "%s_img%d.%s" % (doc_id, k, im.get("ext") or "png")
            with open(os.path.join(sub, fn), "wb") as fh:
                fh.write(data)
            images.append({"name": "图片%d.%s" % (k, im.get("ext") or "png"),
                           "path": "files/" + fn, "page": im.get("page")})
        return secs, images, {"text_source": "pdf_pypdf", "pages": len(pages),
                              "images_available": bool(images), "tables_text_only": True}
    if ext == ".docx" or raw[:2] == b"PK":
        import tempfile
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".docx")
        try:
            tmp.write(raw)
            tmp.close()
            secs = D._docx_sections(tmp.name)
        finally:
            try:
                os.unlink(tmp.name)
            except OSError:
                pass
        images = D._docx_images(raw, doc_id)
        return secs, images, {"text_source": "docx_raw", "images_available": bool(images)}
    text = ""
    for enc in ("utf-8", "gb18030"):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    secs = D._sections_from_text(text, title)
    return secs, [], {"text_source": "text", "chars": len(text), "images_available": False}


def parse(doc_id: str, action: str = "outline", section_path: str = "", cursor: int = 0,
          limit_chars: int = 6000, inline_media: bool = True, doc_title: str = "") -> dict:
    """取原件 + 解析 → `outline` / `section` / `image` / `full`（逐字，不改写 ✓）。"""
    action = (action or "outline").strip().lower()
    raw, meta = fetch(doc_id)
    if not raw:
        return {"ok": False, "tool": "ext_doc_parse", "doc_id": doc_id, **meta}
    did = str(doc_id or "").strip()
    title = str(doc_title or "").strip() or did
    secs, images, quality = _sections_and_images(raw, did, title,
                                                 str(meta.get("content_type") or ""))
    if quality.get("error"):
        return {"ok": False, "tool": "ext_doc_parse", "doc_id": did, "error": quality["error"],
                "detail": quality.get("detail"),
                "hint": "docreader 不可达或 PDF 损坏 ⇒ 该文档按「无法判定」处置", "source": meta}
    base = {"ok": True, "tool": "ext_doc_parse", "doc_id": did, "source": meta,
            "quality": quality, "counts": {"sections": len(secs), "images": len(images)}}
    if action == "outline":
        return {**base, "outline": [{"path": s.get("path") or "", "title": s.get("title") or "",
                                     "chars": len(s.get("text") or "")} for s in secs],
                "hint": "取正文用 action=section（section_path 或 cursor 定位）"}
    if action == "image":
        out = []
        for x in images:
            p = os.path.join(doc_root(), str(x.get("path") or ""))
            item = dict(x, readable=os.path.isfile(p))
            if inline_media and item["readable"]:
                with open(p, "rb") as fh:
                    item["data_url"] = ("data:image/png;base64,"
                                        + base64.b64encode(fh.read()).decode())
            out.append(item)
        return {**base, "images": out}
    if action == "full":
        return {**base, "sections": secs, "images": images}
    i = int(cursor or 0)
    if section_path:
        hit = [k for k, s in enumerate(secs)
               if str(s.get("path") or "").startswith(str(section_path))]
        if not hit:
            return {"ok": False, "tool": "ext_doc_parse", "error": "section_not_found",
                    "section_path": section_path,
                    "available": [s.get("path") for s in secs][:20]}
        i = hit[0]
    if not secs:
        return {**base, "text": "", "note": "该文档没有可读章节"}
    i = max(0, min(i, len(secs) - 1))
    s = secs[i]
    text = str(s.get("text") or "")
    cut = text[:max(200, int(limit_chars or 6000))]
    out = {**base, "path": s.get("path") or "", "title": s.get("title") or "",
           "text": cut, "chars": len(text), "truncated": len(cut) < len(text), "cursor": i}
    if i + 1 < len(secs):
        out["next_cursor"] = i + 1
        out["next_path"] = secs[i + 1].get("path") or ""
    return out

