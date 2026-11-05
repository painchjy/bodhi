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
max_side = 1400
for a in sys.argv:
    if a.startswith("--max-img-pages="):
        try:
            max_img_pages = int(a.split("=", 1)[1] or 0)
        except Exception:
            max_img_pages = 0
    elif a.startswith("--max-side="):
        try:
            max_side = int(a.split("=", 1)[1] or 1400)
        except Exception:
            max_side = 1400

try:
    from PIL import Image
except Exception:
    Image = None


def _shrink(b, cap):
    """超过 cap 的图用 Pillow 压缩（长边 max_side，JPEG q80→65→50），返回 (bytes, 是否压缩)。"""
    if not Image or len(b) <= cap:
        return b, False
    try:
        im = Image.open(io.BytesIO(b))
        if im.mode not in ("RGB", "L"):
            im = im.convert("RGB")
        w, h = im.size
        best = b
        for side in (max_side, int(max_side * 0.85), int(max_side * 0.7)):
            scale = min(1.0, float(side) / max(w, h))
            cur = im if scale >= 1.0 else im.resize(
                (max(1, int(w * scale)), max(1, int(h * scale))))
            for q in (70, 60, 50):
                buf = io.BytesIO()
                cur.convert("RGB").save(buf, format="JPEG", quality=q, optimize=True)
                got = buf.getvalue()
                if len(got) < len(best):
                    best = got
                if len(got) <= cap:
                    return got, True
        return best, True
    except Exception:
        return b, False


data = sys.stdin.buffer.read()
# 2026-11-05：**只压缩**模式（VLM 送图用）——stdin 进图，stdout 出 JPEG，不解析 PDF。
# 02 宿主机没有 Pillow，压缩只能在容器里做，所以复用这条 stdin 通道。
if "--shrink-only" in sys.argv:
    _target = 400
    for a in sys.argv:
        if a.startswith("--target-kb="):
            try:
                _target = int(a.split("=", 1)[1] or 400)
            except Exception:
                _target = 400
    _cb, _ = _shrink(data, _target * 1024)
    sys.stdout.buffer.write(_cb)
    sys.exit(0)
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
        if not b or len(b) > 8 * 1024 * 1024:
            continue
        nm = str(getattr(img, "name", "") or "")
        ext = (nm.rsplit(".", 1)[-1].lower() if "." in nm else "png") or "png"
        if ext not in ("png", "jpg", "jpeg", "gif", "bmp", "emf", "wmf"):
            ext = "png"
        orig = len(b)
        # 2026-11-05（用户口径）：**落盘只存原图**；不再产出「内联压缩版」
        # （`action=image` 已下线：模型侧走「section.figures 清单 + image_read 识图」）
        out["images"].append({"page": i, "index": j, "ext": ext,
                              "b64": base64.b64encode(b).decode(),
                              "bytes": len(b), "orig_bytes": orig})
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
            max_side = int(os.environ.get("BODHI_IMG_MAX_SIDE", "1400") or 1400)
            args += ["--with-images", "--max-img-pages=%d" % max_pages,
                     "--max-side=%d" % max_side]
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


def _page_section(quality: dict, page: int) -> str:
    """**图所属章节**：该页首个编号标题；命中表格类版式词则回退到上一页标题。

    2026-11-05 实测：第 3 页标题是「3 设备申请清单」，但该页顶部的部署图属上一节
    「2.3 部署架构」⇒ 表格节里的图一律按上一节归属（图通常画在表格之前）。
    """
    import re as _re

    titles = (quality or {}).get("page_titles") or []
    pg = int(page or 1)
    hit = ""
    for k in range(min(pg, len(titles)) - 1, -1, -1):
        if str(titles[k] or "").strip():
            hit = str(titles[k]).strip()
            break
    if not hit:
        return ""
    tbl = _re.compile(r"(清单|表格|附表|目录|明细|统计表)")
    if tbl.search(hit):
        for k in range(min(pg, len(titles)) - 2, -1, -1):
            t_k = str(titles[k] or "").strip()
            if t_k and not tbl.search(t_k):
                return t_k
    return hit


_FIG_CAP_RE = None


def _page_captions(texts: list) -> list:
    """抽每页的**图注**（`（图 1：…）` / `图2: …`）→ `[[{no,text}, …], …]`（与页号对齐）。"""
    global _FIG_CAP_RE
    if _FIG_CAP_RE is None:
        import re as _re

        _FIG_CAP_RE = _re.compile(r"[（(]?\s*图\s*(\d{1,2})\s*[：:]\s*([^）)\n]{4,140})")
    out = []
    for t in texts or []:
        caps = [{"no": int(m.group(1)), "text": m.group(2).strip()}
                for m in _FIG_CAP_RE.finditer(str(t or ""))]
        out.append(caps)
    return out


def _content_images(images: list, quality: dict) -> tuple:
    """**正文图**（去掉重复 logo / 过小图）→ `(kept, skipped)`。

    `figure_no` 按**全量顺序**编号，保证 `section.figures[].figure_no` 与
    `image_read(figure_no=N)` 指向同一张图（2026-11-05）。
    """
    import hashlib as _hl

    pages_n = int((quality or {}).get("pages") or 0) or 1
    sig_cnt = {}
    for x in images or []:
        p = os.path.join(doc_root(), str(x.get("path") or ""))
        if os.path.isfile(p):
            try:
                h = _hl.md5(open(p, "rb").read()).hexdigest()
                sig_cnt[h] = sig_cnt.get(h, 0) + 1
            except Exception:  # noqa: BLE001
                pass
    kept, skipped = [], []
    for n, x in enumerate(images or [], 1):
        p = os.path.join(doc_root(), str(x.get("path") or ""))
        if not os.path.isfile(p):
            skipped.append({"page": x.get("page"), "reason": "unreadable"})
            continue
        size = os.path.getsize(p)
        try:
            h = _hl.md5(open(p, "rb").read()).hexdigest()
        except Exception:  # noqa: BLE001
            h = ""
        if h and pages_n >= 3 and sig_cnt.get(h, 0) * 2 >= pages_n:
            skipped.append({"page": x.get("page"), "reason": "repeated_logo", "bytes": size})
            continue
        if size < 6000:
            skipped.append({"page": x.get("page"), "reason": "tiny", "bytes": size})
            continue
        kept.append({"figure_no": n, "page": x.get("page"), "name": x.get("name"),
                     "path": x.get("path"), "bytes": size,
                     "caption": x.get("caption") or ""})
    return kept, skipped


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
        captions = _page_captions(clean)          # 每页图注（与页号对齐）
        sub = os.path.join(doc_root(), "files")
        images = []
        for k, im in enumerate(got.get("images") or [], 1):
            try:
                data = base64.b64decode(im.get("b64") or "")
            except Exception:  # noqa: BLE001
                continue
            if not data:
                continue
            ext = im.get("ext") or "png"
            fn = "%s_img%d.%s" % (doc_id, k, ext)
            with open(os.path.join(sub, fn), "wb") as fh:
                fh.write(data)                       # **原图**（VLM 识图 / 人工复核）
            item = {"name": "图片%d.%s" % (k, ext),
                    "path": "files/" + fn, "page": im.get("page"),
                    "bytes": len(data),
                    "orig_bytes": int(im.get("orig_bytes") or len(data))}
            # 图注（该页文本里的「图 N：…」）→ 不调 VLM 也能引用图的语义
            _pg = int(im.get("page") or 0)
            _caps = captions[_pg - 1] if 0 < _pg <= len(captions) else []
            if _caps:
                _same = [c for c in _caps if int(c.get("no") or 0) == k]
                item["caption"] = (_same[0]["text"] if _same else _caps[0]["text"])
            images.append(item)
        # 2026-11-05：每页**首个编号标题**（供 image 的 context 用；不再按页码比例猜章节）
        import re as _re2
        page_titles = []
        for t in clean:
            hit = ""
            for ln in str(t or "").splitlines():
                if len(ln) <= 60 and _re2.match(
                        r"^\s*(?:\*\*)?(\d+(?:\.\d+){0,3})(?:\*\*)?\s+\S", ln):
                    hit = ln.strip()[:60]
                    break
            page_titles.append(hit)
        return secs, images, {"text_source": "pdf_pypdf", "pages": len(pages),
                              "with_images": bool(with_images),
                              "images_skipped": (not with_images),
                              "headers_stripped": _dropped, "page_titles": page_titles,
                              "captions": captions,
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


# ---------------------------------------------------------------------------
# VLM 识图（2026-11-05）：`action="image_read"`
#   · 图 → 容器内压缩（02 无 Pillow）→ OpenAI 兼容 `/chat/completions`
#   · **额度闸门** `BODHI_VLM_RPM`（默认 2 次/分钟；0=不限）
#   · 结果按 (图, 问题) 缓存；命中**不占额度**；失败绝不影响正文读取
# ---------------------------------------------------------------------------
def _vlm_env_int(name: str, dflt: int) -> int:
    try:                        # 同样剥掉 `.env` 行尾注释（`# …`）
        return int(str(os.environ.get(name, str(dflt)) or dflt).split("#", 1)[0].strip())
    except ValueError:
        return dflt


def _vlm_dir() -> str:
    d = os.path.join(doc_root(), "vlm")
    os.makedirs(d, exist_ok=True)
    return d


def _vlm_gate() -> dict:
    """**频度闸门**：`BODHI_VLM_RPM` 次/分钟（默认 2，0=不限）。

    跨进程安全：`state/doc_parse/vlm/.rate.json` + `flock`。
    额度不足时**等待**至多 `BODHI_VLM_WAIT_MAX`（默认 120s）；仍不足则回
    `{ok:False, rate_limited:True, retry_after}` —— 调用方**不得**把这种情况
    当作「图上没有该信息」（这是"没看到"，不是"不存在"）。
    """
    rpm = _vlm_env_int("BODHI_VLM_RPM", 2)
    if rpm <= 0:
        return {"ok": True, "rpm": 0}
    import time

    wait_max = _vlm_env_int("BODHI_VLM_WAIT_MAX", 120)
    path = os.path.join(_vlm_dir(), ".rate.json")
    deadline = time.time() + max(0, wait_max)
    while True:
        fh = None
        try:
            import fcntl
        except ImportError:                    # 非 POSIX：退化为不加锁
            fcntl = None
        try:
            fh = open(path, "a+")
        except OSError:
            return {"ok": True, "rpm": rpm, "degraded": "rate_file_unavailable"}
        retry = 1
        try:
            if fcntl:
                fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
            fh.seek(0)
            try:
                slots = [float(x) for x in (json.loads(fh.read() or "[]") or [])]
            except Exception:  # noqa: BLE001
                slots = []
            now = time.time()
            slots = [t for t in slots if now - t < 60]
            if len(slots) < rpm:
                slots.append(now)
                fh.seek(0)
                fh.truncate()
                fh.write(json.dumps(slots))
                fh.flush()
                return {"ok": True, "rpm": rpm, "used": len(slots)}
            retry = max(1, int(60 - (now - slots[0])) + 1)
        finally:
            try:
                if fh:
                    fh.close()
            except Exception:  # noqa: BLE001
                pass
        if time.time() + retry > deadline:
            return {"ok": False, "rate_limited": True, "rpm": rpm, "retry_after": retry,
                    "hint": ("VLM 额度已用尽（%d 次/分钟）⇒ 约 %ds 后再试；"
                             "**不要**把「本次没调到」写成「图上没有该信息」" % (rpm, retry))}
        time.sleep(min(retry, 5))


def _vlm_shrink(raw: bytes, target_kb: int) -> bytes:
    """原图 → 送 VLM 的压缩版（容器内 Pillow；任何失败都原样返回，不抛）。"""
    if not raw:
        return b""
    try:
        cname = _docreader_container()
        helper = _ensure_helper(cname)
        r = subprocess.run(
            ["docker", "exec", "-i", cname, "python3", helper, "--shrink-only",
             "--max-side=%d" % _vlm_env_int("BODHI_VLM_MAX_SIDE", 1400),
             "--target-kb=%d" % max(64, int(target_kb))],
            input=raw, capture_output=True,
            timeout=_vlm_env_int("BODHI_VLM_SHRINK_TIMEOUT", 60))
        if r.returncode == 0 and r.stdout:
            return r.stdout
    except Exception:  # noqa: BLE001
        pass
    return raw


_VLM_PROMPT = """你是**部署架构图**的结构化识别器。只输出一个 JSON 对象，不要解释、不要 Markdown 代码块。

输出结构：
{{"watermark_text":"",
"texts":[{{"id":"n1","text":"","location":"","bbox":[0,0,0,0]}}],
"lines":[{{"from":"n1","to":"n2","from_label":"","to_label":"","style":"solid|dashed|arc|box","arrow":"single|double|none","label":""}}],
"legend":[{{"text":"","means":""}}],
"uncertain":[{{"what":"","location":"","why":""}}]}}

硬要求：
1) `texts` —— **每个文字块一个唯一 `id`**（n1、n2、n3…，按从上到下、从左到右编号）。
   **同名但不同位置的必须分开编号**（图里常有多个「网关」「OSS」「联机应用」「OB数据库主集群」，绝不能合并）。
   `text` 逐字抄原文（地区/云/AZ/机房/单元/分片区间/组件/图例/注释），保持原语言；
   `location` 取 左上/上中/右上/左中/中/右中/左下/下中/右下，同名项要写清所属（如「浦江云第2个机房」）；
   `bbox` 归一化 [x1,y1,x2,y2]，必须 0~1 且 x1<x2、y1<y2（越界夹紧）。
2) `lines` —— **必须用 `id` 精确引用，禁止只写文字**（同名节点只能靠 id 区分）：
   · `from`/`to` 填第 1 步的 id；同时填 `from_label`/`to_label` 供人读（如「浦江云OSS」「浦江云OB主集群」）；
   · 框线（矩形框/分组外框）⇒ `style`=`box`，`from`=该框的 id，`to`=`inside`；
   · 连线 ⇒ `style`=`solid`/`dashed`/`arc`；`arrow`=`single`/`double`/`none`；
   · `label` 抄线上的字（如「同步」「数据同步」）；**跨云、跨大区的连线必须写**。
3) `legend` —— 图例逐项抄出来并写含义（如 红块=应用故障）。
4) **排除无效数据**：斜向重复水印（人名+编号）只写进 `watermark_text` **一次**，
   **绝不允许**出现在 `texts`/`lines`/`legend` 里；装饰性背景、页码也不要抄。
5) 看不清的放 `uncertain`（位置+原因），**不要猜**；宁可少写不可编。

本次关注点：{question}
"""


def _vlm_clean(v: str) -> str:
    """`.env` 的值可能带行尾注释（systemd `EnvironmentFile` **不剥** `#…`）⇒ 这里清掉。

    2026-11-05 实测：`BODHI_VLM_BASE_URL=…/v1   # 默认` ⇒ URL 含空格 ⇒ `InvalidURL`。
    """
    return str(v or "").split("#", 1)[0].strip()


def _vlm_chat(img: bytes, question: str) -> dict:
    """调 OpenAI 兼容 VLM（`.env` 配 base_url/model/key；DeepSeek 需关 thinking）。"""
    import urllib.error
    import urllib.request

    base = (_vlm_clean(os.environ.get("BODHI_VLM_BASE_URL"))
            or "https://oai.endpoints.kepler.ai.cloud.ovh.net/v1").rstrip("/")
    model = _vlm_clean(os.environ.get("BODHI_VLM_MODEL")) or "Qwen2.5-VL-72B-Instruct"
    key = _vlm_clean(os.environ.get("BODHI_VLM_API_KEY"))
    body = {"model": model, "temperature": 0,
            "max_tokens": _vlm_env_int("BODHI_VLM_MAX_TOKENS", 6144),
            "messages": [{"role": "user", "content": [
                {"type": "text", "text": _VLM_PROMPT.format(question=question or "无")},
                {"type": "image_url",
                 "image_url": {"url": "data:image/jpeg;base64," + base64.b64encode(img).decode()}}]}]}
    # 2026-11-05 实测（DeepSeek）：默认开 thinking 时 reasoning 会把 max_tokens 吃光、
    # content 为空（finish_reason=length）。关掉后 completion≈2200 就能出完整 JSON。
    # 非 DeepSeek 端点不认这个字段 ⇒ 400 时自动摘掉重试一次（见下）。
    think = _vlm_clean(os.environ.get("BODHI_VLM_THINKING")) or "disabled"
    if think not in ("default", "off", "0"):
        body["thinking"] = {"type": think}
    headers = {"Content-Type": "application/json"}
    if key:
        headers["Authorization"] = "Bearer " + key
    timeout = _vlm_env_int("BODHI_VLM_TIMEOUT", 300)

    def _post(payload: dict) -> dict:
        req = urllib.request.Request(base + "/chat/completions",
                                     data=json.dumps(payload).encode(), headers=headers)
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8", "replace"))

    try:
        data = _post(body)
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = exc.read()[:400].decode("utf-8", "replace")
        except Exception:  # noqa: BLE001
            pass
        if "thinking" in body and exc.code in (400, 422):
            body.pop("thinking", None)          # 端点不认 ⇒ 去掉再试一次
            data = _post(body)
        else:
            raise RuntimeError("vlm_http_%s: %s" % (exc.code, detail)) from exc
    ch = (data.get("choices") or [{}])[0] or {}
    msg = ch.get("message") or {}
    # DeepSeek 推理模型：`reasoning_content` 是思考、`content` 才是答案
    # 2026-11-05 实测：纯文本模型（supports_vision=false）会把 max_tokens 全烧在 reasoning 上、content 为空
    return {"model": model,
            "content": str(msg.get("content") or "").strip(),
            "reasoning_head": str(msg.get("reasoning_content") or "").strip()[:400],
            "finish_reason": str(ch.get("finish_reason") or ""),
            "usage": data.get("usage") or {}}


def _vlm_parse_json(text: str) -> dict:
    """从模型输出抠 JSON（容忍 ```json 包裹、前后废话）。"""
    import re as _re

    s = str(text or "").strip()
    m = _re.search(r"```(?:json)?\s*(\{.*\})\s*```", s, _re.S)
    s = m.group(1) if m else s
    if not m:
        i, j = s.find("{"), s.rfind("}")
        if i >= 0 and j > i:
            s = s[i:j + 1]
    try:
        got = json.loads(s)
    except Exception:  # noqa: BLE001
        return {"_error": "bad_json", "_raw_head": str(text or "")[:500]}
    return got if isinstance(got, dict) else {"_error": "not_object"}


def _vlm_read(doc_id: str, figure_no: int = 0, question: str = "",
              figure_name: str = "", detail: str = "compact",
              base: dict = None) -> dict:
    """读**一张图** → 契约 v0（nodes/edges/annotations/ambiguity）。

    - `figure_no` 或 `figure_name` 定位（都来自 `section.figures[]`）
    - `detail=compact`（默认）只回 label/location/线条（**不带 bbox**，控制回执体积）；
      `detail=full` 带 bbox（可做像素级复核）
    缓存 `state/doc_parse/vlm/<doc_id>_img<N>_<sha>.json`（同图同问题不重复调、不占额度）；
    任何失败都回 `{ok:False, error…}`，**绝不抛**（正文链路不受影响）。
    """
    import hashlib
    import time

    t0 = time.time()
    base = dict(base or {})
    figs = list(base.pop("_figures", []) or [])
    if not figs:
        return {**base, "ok": False, "tool": "ext_doc_parse", "action": "image_read",
                "error": "no_images",
                "hint": "该文档没有可读的正文图（先 action=section 看 figures[] 是否为空）"}
    if figure_no:
        pick = [f for f in figs if int(f.get("figure_no") or 0) == int(figure_no)]
    elif str(figure_name or "").strip():
        nm = str(figure_name).strip()
        pick = ([f for f in figs if str(f.get("name") or "").strip() == nm]
                or [f for f in figs if nm and nm in str(f.get("name") or "")])
    else:
        pick = figs[:1]
    if not pick:
        return {**base, "ok": False, "action": "image_read", "error": "figure_not_found",
                "figure_no": figure_no, "figure_name": figure_name,
                "available": [{"figure_no": f.get("figure_no"), "page": f.get("page"),
                               "name": f.get("name"), "caption": f.get("caption")}
                              for f in figs[:20]]}
    fig = pick[0]
    path = os.path.join(doc_root(), str(fig.get("path") or ""))
    if not os.path.isfile(path):
        return {**base, "ok": False, "action": "image_read", "error": "image_unreadable",
                "figure": {"figure_no": fig.get("figure_no"), "name": fig.get("name")}}
    try:
        raw = open(path, "rb").read()
    except OSError as exc:
        return {**base, "ok": False, "action": "image_read", "error": "read_failed",
                "detail": repr(exc)[:120]}

    q = str(question or "").strip()
    ck = hashlib.sha256(("%s|%s|%s" % (doc_id, fig.get("figure_no"), q)).encode()).hexdigest()[:16]
    cache_fp = os.path.join(_vlm_dir(), "%s_img%s_%s.json" % (doc_id, fig.get("figure_no"), ck))
    fmeta = {"figure_no": fig.get("figure_no"), "page": fig.get("page"),
             "name": fig.get("name"), "caption": fig.get("caption") or "",
             "url": (fig.get("url") or ("/bodhi/ext/doc-image?doc_id=%s&name=%s"
                                        % (doc_id, fig.get("name") or ""))),
             "orig_bytes": len(raw)}
    if os.path.isfile(cache_fp):
        try:
            got0 = json.load(open(cache_fp, encoding="utf-8"))
            c0 = got0.get("contract") or {}
            _mb0 = len([n for n in (c0.get("nodes") or []) if not n.get("bbox")])
            if str(detail or "").strip().lower() != "full":
                for n in (c0.get("nodes") or []):
                    n.pop("bbox", None)
            if isinstance(c0.get("gate"), dict):
                c0["gate"]["missing_bbox"] = _mb0
            return {**base, "ok": True, "action": "image_read", "figure": fmeta,
                    "cache": "hit",
                    "detail": "compact" if str(detail or "").strip().lower() != "full" else "full",
                    "extraction": got0.get("extraction") or {},
                    "contract": c0, "usage": got0.get("usage") or {},
                    "duration_ms": {**((base.get("duration_ms") or {})), "vlm": 0,
                                    "total": int((time.time() - t0) * 1000)},
                    "hint": "命中缓存（未消耗 VLM 额度）；引用请带 bbox/evidence + confidence"}
        except Exception:  # noqa: BLE001
            pass

    gate = _vlm_gate()
    if not gate.get("ok"):
        return {**base, "ok": False, "action": "image_read", "figure": fmeta,
                "error": "rate_limited", **gate,
                "duration_ms": {**((base.get("duration_ms") or {})), "vlm": 0,
                                "total": int((time.time() - t0) * 1000)}}
    return _vlm_run(raw, fig, fmeta, cache_fp, q, base, t0, detail)


def _vlm_run(raw: bytes, fig: dict, fmeta: dict, cache_fp: str, q: str,
              base: dict, t0: float, detail: str = "compact") -> dict:
    """真正发一次 VLM 并把输出规整成契约 v0（失败**只**影响本图）。"""
    import time

    img = _vlm_shrink(raw, _vlm_env_int("BODHI_VLM_MAX_KB", 400))
    t1 = time.time()
    try:
        got = _vlm_chat(img, q)
    except Exception as exc:  # noqa: BLE001
        return {**base, "ok": False, "action": "image_read", "figure": fmeta,
                "error": "vlm_failed", "detail": repr(exc)[:200],
                "hint": ("VLM 不可达/超时 ⇒ 该图**判不了**（不是「图上没有」）；"
                         "正文与其他 action 不受影响"),
                "duration_ms": {**((base.get("duration_ms") or {})),
                                "vlm": int((time.time() - t1) * 1000),
                                "total": int((time.time() - t0) * 1000)}}
    if not got.get("content"):
        # 模型没产出答案（多半是：推理模型把 max_tokens 烧光，或该模型不支持视觉）
        return {**base, "ok": False, "action": "image_read", "figure": fmeta,
                "error": "vlm_empty_content",
                "finish_reason": got.get("finish_reason"),
                "reasoning_head": got.get("reasoning_head") or "",
                "usage": got.get("usage") or {},
                "hint": ("模型没有产出 JSON：检查 ① 该模型是否**支持视觉**（`supports_vision`）；"
                         "② `BODHI_VLM_MAX_TOKENS` 是否被推理吃光（finish_reason=length）⇒ 调大或换模型。"
                         "**这不是「图上没有信息」**，不得据此判「文档未提供」"),
                "duration_ms": {**((base.get("duration_ms") or {})),
                                "vlm": int((time.time() - t1) * 1000),
                                "total": int((time.time() - t0) * 1000)}}
    parsed = _vlm_parse_json(got.get("content") or "")
    if "_error" in parsed:
        extraction = parsed
    else:
        extraction = {k: parsed.get(k) for k in
                      ("texts", "lines", "legend", "uncertain", "watermark_text")}
    nodes = [{"id": t.get("id"), "label": t.get("text"), "location": t.get("location"),
              "bbox": t.get("bbox")}
             for t in (parsed.get("texts") or []) if isinstance(t, dict)]
    edges = [{"from": e.get("from"), "to": e.get("to"),
              "from_label": e.get("from_label"), "to_label": e.get("to_label"),
              "style": e.get("style"), "arrow": e.get("arrow"), "label": e.get("label")}
             for e in (parsed.get("lines") or []) if isinstance(e, dict)]
    _ids = {str(n.get("id")) for n in nodes if n.get("id")}
    _dangling = sorted({str(e.get(k)) for e in edges for k in ("from", "to")
                        if e.get(k) and str(e.get(k)) not in _ids
                        and str(e.get(k)) not in ("inside", "")})
    anns = [{"key": "legend", "value": l.get("means"), "evidence": l.get("text"),
             "confidence": 0.9}
            for l in (parsed.get("legend") or []) if isinstance(l, dict)]
    _missing_bbox = len([n for n in nodes if not n.get("bbox")])   # **pop 之前**统计

    def _mk_contract(ns: list) -> dict:
        return {"nodes": ns, "edges": edges, "annotations": anns,
                "ambiguity": parsed.get("uncertain") or [],
                "watermark": parsed.get("watermark_text") or "",
                "todo": [] if ns else [{"action": "人工看图", "assignee": "human"}],
                "gate": {"nodes": len(ns), "edges": len(edges),
                         "missing_bbox": _missing_bbox,
                         "dangling_ids": _dangling[:10],
                         "dangling_total": len(_dangling)}}

    # 缓存存 **full**（带 bbox），回执按 detail 裁剪 ⇒ 之后 `detail=full` 能直接命中缓存
    full_contract = _mk_contract([dict(n) for n in nodes])
    compact = str(detail or "").strip().lower() != "full"
    if compact:                      # 默认只回 label/location/线条，控制回执体积（bbox 可要 full）
        for n in nodes:
            n.pop("bbox", None)
    contract = _mk_contract(nodes)
    out = {**base, "ok": True, "action": "image_read", "figure": fmeta, "cache": "miss",
           "detail": "compact" if compact else "full",
           "channel": "vlm:" + str(got.get("model")), "extraction": extraction,
           "contract": contract, "usage": got.get("usage") or {},
           "duration_ms": {**((base.get("duration_ms") or {})),
                           "vlm": int((time.time() - t1) * 1000),
                           "total": int((time.time() - t0) * 1000)},
           "hint": (("识别结果是**图上读到的**，不是正文；`uncertain` 条目**不得**当结论。"
                     + ("" if compact else " bbox 为归一化 [x1,y1,x2,y2]。")))}
    try:
        if "_error" not in parsed:       # **失败结果不写缓存**（否则会一直命中坏缓存）
            with open(cache_fp, "w", encoding="utf-8") as fh:
                json.dump({"extraction": extraction, "contract": full_contract,
                           "usage": got.get("usage") or {}}, fh, ensure_ascii=False)
    except OSError:
        pass
    return out


def parse(doc_id: str, action: str = "outline", section_path: str = "", cursor: int = 0,
          limit_chars: int = 6000, doc_title: str = "",
          figure_no: int = 0, question: str = "", figure_name: str = "",
          detail: str = "compact") -> dict:
    """取原件 + 解析 → `outline` / `section` / `image_read` / `full`（逐字，不改写 ✓）。

    - `section`    ：逐字正文 + `figures[]`（figure_no/page/name/bytes/caption/url/section）
                     + `figure_placeholders[]`（`[[图N｜pX｜文件名｜url｜图注]]`）
    - `image_read` ：**识图**（一次一张，`figure_no` 或 `figure_name` 定位；受 `BODHI_VLM_RPM` 限流）
                     → 契约 v0 文本（`detail=compact` 默认只回 label/location/线条，`full` 带 bbox）
    - `image`      ：**已下线**（不再回 base64）⇒ 回 `action_removed` + 指引，不静默
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
    # `image` 虽已下线，但仍需取图才能在下线回执里给清单（不含 base64）
    need_images = action in ("image", "image_read", "section", "outline")
    # C（2026-10-29）：**小 PDF 在 outline 时顺手取图**（一次解析、成本低）；
    # 大文件仍延后到 action="section"/"image_read"，避免把"读正文"拖慢。
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


    # 正文图（去 logo/过小图）；`figure_no` **全局一致** ⇒ section 里看到的号 = image_read 能用的号
    figs_all, figs_skipped = _content_images(images, quality)
    base["counts"] = {"sections": len(secs), "images": len(figs_all)}
    if figs_skipped:
        base["images_skipped_detail"] = figs_skipped[:40]
        base["images_skipped_total"] = len(figs_skipped)

    import re as _re_fig

    def _sec_no(x) -> str:
        m = _re_fig.match(r"\s*(\d+(?:\.\d+){0,3})", str(x or ""))
        return m.group(1) if m else ""

    def _figs_of(path_t: str) -> list:
        """某章节的图（用**章节编号**匹配：切节器 path 可能是「2.3 2.3 部署架构」）。"""
        no = _sec_no(path_t)
        out = []
        for f in figs_all:
            sec_t = _page_section(quality, int(f.get("page") or 1))
            same = bool(sec_t) and bool(path_t) and (
                sec_t.startswith(path_t) or path_t.startswith(sec_t)
                or (no and _sec_no(sec_t) == no))
            if same:
                out.append(f)
        return out

    def _fig_brief(f: dict) -> dict:
        """给模型看的图元数据（**不含 base64**）。"""
        return {"figure_no": f.get("figure_no"), "page": f.get("page"),
                "name": f.get("name"), "bytes": f.get("bytes"),
                "caption": f.get("caption") or "",
                "section": _page_section(quality, int(f.get("page") or 1)),
                "url": ("/bodhi/ext/doc-image?doc_id=%s&name=%s"
                        % (did, f.get("name") or ""))}

    def _fig_placeholder(f: dict) -> str:
        """正文占位符：图号｜页码｜文件名｜url｜图注 —— 自带定位信息，可直接引用或拿去 image_read。"""
        b = _fig_brief(f)
        return "[[图%s｜p%s｜%s｜%s｜%s]]" % (
            b["figure_no"], b["page"], b["name"] or "", b["url"] or "",
            b["caption"] or b["name"] or "")

    if action == "image_read":
        return _vlm_read(did, figure_no=figure_no, figure_name=figure_name,
                         question=question, detail=detail,
                         base={**base, "_figures": figs_all})

    if action == "outline":
        return {**base,
                "outline": [{"path": s.get("path") or "", "title": s.get("title") or "",
                             "chars": len(s.get("text") or ""),
                             "figures": len(_figs_of(str(s.get("path") or "")))}
                            for s in secs],
                "sections_total": len(secs), "figures_total": len(figs_all),
                "next": ("取正文用 action=section（section_path 或 cursor 定位）——"
                         "回执带该节 `figures[]` 与 `figure_placeholders[]`（含 url 与图注）；"
                         "**图内信息调 action=image_read(figure_no=N)**"),
                "hint": "取正文用 action=section；图内信息用 action=image_read"}
    if action == "image":
        # 2026-11-05（用户口径）：**action=image 已下线** —— 不再回 base64（不与 image_read 并存）。
        # 取图清单 = `action=section` 的 `figures[]`（含 url/图注）；识图 = `action=image_read`。
        kept, _skipped = _content_images(images, quality)
        return {**base, "ok": False, "action": "image", "error": "action_removed",
                "counts": {"sections": len(secs), "images": len(kept)},
                "images": [{"figure_no": f["figure_no"], "page": f["page"],
                            "name": f["name"], "bytes": f["bytes"],
                            "caption": f["caption"],
                            "section": _page_section(quality, int(f["page"] or 1)),
                            "url": ("/bodhi/ext/doc-image?doc_id=%s&name=%s"
                                    % (did, f["name"] or ""))}
                           for f in kept],
                "hint": ("action=image 已下线（不再回 base64）。取图清单请用 `action=section` "
                         "回执的 `figures[]`；需要图内信息调 `action=image_read, figure_no=N`"),
                "next_args": {"doc_id": did, "action": "image_read", "figure_no": 1}}
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
    # 本节相关的图 ⇒ `figures[]`（结构化元数据）+ `figure_placeholders[]`（含 url/图注，可直接引用）
    figs = []
    for f in _figs_of(str(s.get("path") or "")):
        item = _fig_brief(f)
        item["placeholder"] = _fig_placeholder(f)
        figs.append(item)
    if i + 1 < len(secs):
        out["next_cursor"] = i + 1
        out["next_path"] = secs[i + 1].get("path") or ""
        out["next"] = "继续请传 cursor=%d（或 section_path=%s）" % (i + 1, out["next_path"])
    else:
        out["next_cursor"] = None
        out["next"] = "已是最后一节"
    if figs:
        out["figures"] = figs
        out["figure_placeholders"] = [f["placeholder"] for f in figs]
        out["next"] = (out["next"] +
                       "；本节含图 %d 张 ⇒ 需要图内信息调 "
                       "`action=image_read, figure_no=N`（受 VLM 限流）" % len(figs))
    return out

