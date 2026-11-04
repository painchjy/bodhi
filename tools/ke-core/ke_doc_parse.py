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

import ke_docx as D              # 02 自己的 docx 切节（不 import 04 的运行库 ✓）
import ke_ext_client as S        # public_base()：04 的对外地址属客户端职责

_PDF_HELPER = r'''
import sys, json, io, base64

with_images = "--with-images" in sys.argv
max_img_pages = 0
for a in sys.argv:
    if a.startswith("--max-img-pages="):
        try:
            max_img_pages = int(a.split("=", 1)[1] or 0)
        except Exception:
            max_img_pages = 0

data = sys.stdin.buffer.read()
out = {"pages": [], "images": [], "engine": "pypdf", "with_images": with_images}
try:
    from pypdf import PdfReader
except Exception as e:
    print(json.dumps({"error": "pypdf_missing", "detail": repr(e)})); sys.exit(0)
try:
    rd = PdfReader(io.BytesIO(data))
except Exception as e:
    print(json.dumps({"error": "pdf_open_failed", "detail": repr(e)})); sys.exit(0)

n = 0
for i, pg in enumerate(rd.pages, 1):
    try:
        t = pg.extract_text() or ""
    except Exception:
        t = ""
    out["pages"].append({"no": i, "text": t})
    if not with_images:
        continue
    if max_img_pages and i > max_img_pages:
        continue
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

_HELPER_SHA = __import__("hashlib").sha256(_PDF_HELPER.encode()).hexdigest()[:12]
_HELPER_READY: dict = {}



def doc_root() -> str:
    """解析产物的落地根（bodhi-mcp 自己的目录；与 04 的 `BODHI_EXT_ROOT` **无关**）。"""
    root = os.environ.get("BODHI_DOC_ROOT") or os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
        "state", "doc_parse")
    os.makedirs(os.path.join(root, "files"), exist_ok=True)
    return root


# 解析结果**进程内缓存**（2026-10-28）：同一 doc_id 顺序读多节时不必每次重跑 docreader。
#   - 键 = doc_id；值 = {sig: (len, sha256[:16]), secs, images, quality}
#   - 文件变了（长度或 sha 变）自动失效；`BODHI_DOC_CACHE=0` 可关；最多留 8 份。
_DOC_CACHE: dict = {}
_CACHE_MAX = 64          # 一次任务可能顺序读几十份/多节（原来 8 份会被挤掉）


def _cache_on() -> bool:
    return str(os.environ.get("BODHI_DOC_CACHE", "1")).strip().lower() \
        not in ("0", "false", "no", "off")


def _sig(raw: bytes) -> tuple:
    import hashlib
    return (len(raw), hashlib.sha256(raw).hexdigest()[:16])



def _docreader_container() -> str:
    return os.environ.get("WEKNORA_DOCREADER_CONTAINER") or "WeKnora-docreader"


def _ensure_helper(cname: str) -> str:
    """把解析脚本写进 docreader 容器（**按 sha 只写一次**；容器重启后会自动重写）。"""
    helper = "/tmp/bodhi_pdf_extract.py"
    if _HELPER_READY.get(cname) == _HELPER_SHA:
        return helper
    r = subprocess.run(["docker", "exec", "-i", cname, "sh", "-c", "cat > %s" % helper],
                       input=_PDF_HELPER.encode(), capture_output=True, timeout=20)
    if r.returncode == 0:
        _HELPER_READY[cname] = _HELPER_SHA
    return helper


def _pdf_extract(raw: bytes, with_images: bool = False) -> dict:
    """借 docreader 的 pypdf 解析 PDF（脚本 + 二进制都走 stdin，**不共享磁盘** ✓）。

    `with_images=False`（默认）只抽文本 —— pypdf 的 `pg.images` 是**主要耗时**，
    挪到 `action="image"` 时才跑（2026-10-29：内网 3MB PDF 每次 40s → 只抽文本后降到几秒）。
    """
    cname = _docreader_container()
    try:
        helper = _ensure_helper(cname)
        args = ["docker", "exec", "-i", cname, "python3", helper]
        if with_images:
            max_pages = int(os.environ.get("BODHI_PDF_MAX_IMAGE_PAGES", "20") or 0)
            args += ["--with-images", "--max-img-pages=%d" % max_pages]
        timeout = int(os.environ.get("BODHI_PDF_TIMEOUT", "90") or 90)
        r = subprocess.run(args, input=raw, capture_output=True, timeout=timeout)
    except FileNotFoundError:
        return {"error": "docker_missing", "detail": "宿主机没有 docker 命令；docreader 不可达"}
    except subprocess.TimeoutExpired:
        return {"error": "pdf_parse_timeout",
                "detail": "超过 %ss：可设 BODHI_PDF_TIMEOUT 调大，或把 PDF 拆页/转 docx"
                          % os.environ.get("BODHI_PDF_TIMEOUT", "90")}
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


def _strip_running_headers(pages: list, ratio: float = 0.3) -> tuple:
    """剔除**跨页重复**的页眉/页脚（公司名 / logo 文字 / 页码）。纯函数，便于单测。

    内网实测（49 页 PDF）：每页页眉都带公司名，pypdf 提取后这些行混进正文，
    既浪费上下文，又会被误判成标题（产生"空段/重复段")。
    返回 `(每页清洗后文本列表, 被剔除的行数)`。
    """
    from collections import Counter

    texts = [str(p.get("text") or "") for p in (pages or [])]
    if len(texts) < 3:
        return texts, 0
    cnt: Counter = Counter()
    for t in texts:
        lines = [x.strip() for x in t.splitlines()]
        for ln in {x for x in (lines[:2] + lines[-2:]) if x and len(x) <= 60}:
            cnt[ln] += 1
    thr = max(2, int(len(texts) * ratio))
    bad = {ln for ln, n in cnt.items() if n >= thr}
    if not bad:
        return texts, 0
    out, dropped = [], 0
    for t in texts:
        keep = []
        for x in t.splitlines():
            if x.strip() in bad:
                dropped += 1
                continue
            keep.append(x)
        out.append("\n".join(keep))
    return out, dropped


def _sections_and_images(raw: bytes, doc_id: str, title: str, name: str,
                         with_images: bool = False) -> tuple:
    """按类型选解析器 → `(sections, images, quality)`。

    `with_images=False`（默认）：PDF **只抽文本**（图证据在 `action="image"` 时才取），
    DOCX/TXT 本来很快、照旧带图。
    """
    ext = os.path.splitext(str(name or ""))[1].lower()
    if raw[:5] == b"%PDF-":
        got = _pdf_extract(raw, with_images=with_images)
        if got.get("error"):
            return [], [], {"text_source": "none", "images_available": False,
                            "error": got.get("error"), "detail": got.get("detail", "")}

        pages = got.get("pages") or []
        clean, _dropped = _strip_running_headers(pages)
        text = "\n".join(s.strip() for s in clean).strip()
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
                              "with_images": bool(with_images),
                              "images_skipped": (not with_images),
                              "headers_stripped": _dropped,
                              "images_available": bool(images), "tables_text_only": True}
    if ext == ".docx" or raw[:2] == b"PK":
        import tempfile
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".docx")
        try:
            tmp.write(raw)
            tmp.close()
            secs = D.sections(tmp.name)
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
          limit_chars: int = 6000, inline_media: bool = False, doc_title: str = "") -> dict:
    """取原件 + 解析 → `outline` / `section` / `image` / `full`（逐字，不改写 ✓）。

    回执带 `duration_ms{fetch,parse,total}` 与 `bytes`（2026-10-28：超时排查用，只量不改语义）。
    """
    import time

    action = (action or "outline").strip().lower()
    t0 = time.time()
    raw, meta = fetch(doc_id)
    t_fetch = int((time.time() - t0) * 1000)
    if not raw:
        return {"ok": False, "tool": "ext_doc_parse", "doc_id": doc_id,
                "duration_ms": {"fetch": t_fetch, "parse": 0, "total": t_fetch}, **meta}
    did = str(doc_id or "").strip()
    title = str(doc_title or "").strip() or did
    need_images = action == "image"
    # C（2026-10-29）：**小 PDF 在 outline 时顺手取图**（一次解析、成本低）；
    # 大文件仍延后到 action="image"，避免把"读正文"拖慢。
    if not need_images and action == "outline" and raw[:5] == b"%PDF-":
        try:
            mb = float(os.environ.get("BODHI_PDF_AUTO_IMAGES_MB", "10") or 10)
        except ValueError:
            mb = 2.0
        if mb > 0 and len(raw) <= mb * 1024 * 1024:
            need_images = True
    sig = _sig(raw)

    # 2026-11-03：缓存键**只按 doc_id**（原来 `(doc_id, need_images)` ⇒ outline 取图后
    # section 又要重解析一遍，内网 3MB PDF 直接翻倍耗时）。`with_images` 只记"这份缓存有没有图"。
    hit = _DOC_CACHE.get(did) if _cache_on() else None
    cache_hit = bool(hit and hit.get("sig") == sig
                     and (hit.get("with_images") or not need_images))
    if cache_hit:
        secs, images, quality = hit["secs"], hit["images"], hit["quality"]
        t_parse = 0
    else:
        t1 = time.time()
        secs, images, quality = _sections_and_images(raw, did, title,
                                                     str(meta.get("content_type") or ""),
                                                     with_images=need_images)
        t_parse = int((time.time() - t1) * 1000)
        if _cache_on() and not quality.get("error"):
            if len(_DOC_CACHE) >= _CACHE_MAX:
                _DOC_CACHE.pop(next(iter(_DOC_CACHE)), None)
            _DOC_CACHE[did] = {"sig": sig, "secs": secs, "images": images,
                               "quality": quality, "with_images": bool(need_images)}
    timing = {"fetch": t_fetch, "parse": t_parse, "total": int((time.time() - t0) * 1000),
              "cache_hit": cache_hit, "with_images": need_images,
              "pages": int(quality.get("pages") or 0)}


    if quality.get("error"):
        return {"ok": False, "tool": "ext_doc_parse", "doc_id": did, "error": quality["error"],
                "detail": quality.get("detail"), "bytes": len(raw), "duration_ms": timing,
                "hint": "docreader 不可达或 PDF 损坏 ⇒ 该文档按「无法判定」处置", "source": meta}
    base = {"ok": True, "tool": "ext_doc_parse", "doc_id": did, "source": meta,
            "quality": quality, "bytes": len(raw), "duration_ms": timing,
            "counts": {"sections": len(secs), "images": len(images)}}
    if quality.get("images_skipped"):
        base["next"] = ("本页只抽了文本（未取图）。**需要图证据请调 "
                        "`ext_doc_parse(doc_id=…, action=\"image\")`**"
                        "（首次解析较慢，之后命中缓存；涉及图的规则必须用图判定）")


    if action == "outline":
        return {**base, "outline": [{"path": s.get("path") or "", "title": s.get("title") or "",
                                     "chars": len(s.get("text") or "")} for s in secs],
                "sections_total": len(secs),
                "next": ("取正文用 action=section（section_path 或 cursor 定位）；"
                         "**需要图证据请直接调 action=image**（下面的 next_args 可直接用）"),
                "next_args": {"doc_id": did, "action": "image"},
                "hint": "取正文用 action=section（section_path 或 cursor 定位）"}
    if action == "image":
        # 4) 只取正文图：重复 logo / 过小图 过滤（2026-10-31）
        import hashlib as _hl  # noqa: PLC0415
        _sig_cnt = {}
        for x in images:
            p0 = os.path.join(doc_root(), str(x.get("path") or ""))
            if os.path.isfile(p0):
                try:
                    _sig_cnt[_hl.md5(open(p0, "rb").read()).hexdigest()] = \
                        _sig_cnt.get(_hl.md5(open(p0, "rb").read()).hexdigest(), 0) + 1
                except Exception:  # noqa: BLE001
                    pass
        _pages_n = int(quality.get("pages") or 0) or 1
        kept, skipped_detail = [], []
        for x in images:
            p = os.path.join(doc_root(), str(x.get("path") or ""))
            item = dict(x, readable=os.path.isfile(p))
            if not item["readable"]:
                skipped_detail.append({"page": x.get("page"), "reason": "unreadable"})
                continue
            size = os.path.getsize(p)
            try:
                _md5 = _hl.md5(open(p, "rb").read()).hexdigest()
            except Exception:  # noqa: BLE001
                _md5 = ""
            if _md5 and _pages_n >= 3 and _sig_cnt.get(_md5, 0) * 2 >= _pages_n:
                skipped_detail.append({"page": x.get("page"), "reason": "repeated_logo", "bytes": size})
                continue
            if size < 6000:
                skipped_detail.append({"page": x.get("page"), "reason": "tiny", "bytes": size})
                continue
            item["bytes"] = size
            ext = str(x.get("name") or "").rsplit(".", 1)[-1].lower()
            item["content_type"] = ("image/jpeg" if ext in ("jpg", "jpeg")
                                    else ("image/png" if ext == "png" else "application/octet-stream"))
            item["url"] = "/bodhi/ext/doc-image?doc_id=%s&name=%s" % (did, x.get("name") or "")
            # 5) 上下文标注（按页码近似定位章节 + 前后文）
            try:
                if secs:
                    idx = min(len(secs) - 1, max(0, int((int(x.get("page") or 1) - 1)
                                                         * len(secs) / float(_pages_n))))
                    s0 = secs[idx]
                    t0 = str(s0.get("text") or "")
                    item["context"] = {"section": s0.get("title") or s0.get("path") or "",
                                       "prev_text": t0[-160:], "next_text": t0[:160]}
            except Exception:  # noqa: BLE001
                pass
            kept.append(item)
        out = kept
        total_b64 = 0
        try:
            cap = int(os.environ.get("BODHI_IMG_INLINE_KB", "64") or 64) * 1024
        except ValueError:
            cap = 64 * 1024
        for item in out:
            if not inline_media:
                continue
            if int(item.get("bytes") or 0) > cap:
                item["inline_skipped"] = "too_large"
                continue
            p = os.path.join(doc_root(), str(item.get("path") or ""))
            try:
                enc = base64.b64encode(open(p, "rb").read()).decode()
            except Exception:  # noqa: BLE001
                continue
            if total_b64 + len(enc) > 131072:
                item["inline_skipped"] = "payload_cap"
                continue
            total_b64 += len(enc)
            item["data_url"] = "data:%s;base64,%s" % (item.get("content_type"), enc)
        return {**base, "images": out, "counts": {"sections": len(secs), "images": len(out)},
                "images_skipped_detail": skipped_detail[:40],
                "images_skipped_total": len(skipped_detail),
                "images_total_bytes": sum(int(i.get("bytes") or 0) for i in out)}
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
    if i < 0 or i >= len(secs):
        return {**base, "ok": False, "error": "cursor_out_of_range", "cursor": i,
                "total_sections": len(secs),
                "available": [s.get("path") for s in secs][:20],
                "hint": "cursor 从 0 开始；用回执的 next_cursor 续读（空段已被丢弃，序号会变）"}
    s = secs[i]
    text = str(s.get("text") or "")
    cut = text[:max(200, int(limit_chars or 6000))]
    out = {**base, "path": s.get("path") or "", "title": s.get("title") or "",
           "text": cut, "chars": len(text), "truncated": len(cut) < len(text),
           "cursor": i, "total_sections": len(secs)}
    if i + 1 < len(secs):
        out["next_cursor"] = i + 1
        out["next_path"] = secs[i + 1].get("path") or ""
        out["next"] = "继续请传 cursor=%d（或 section_path=%s）" % (i + 1, out["next_path"])
    else:
        out["next_cursor"] = None
        out["next"] = "已是最后一节；需要图证据请调 action=image"
    return out

