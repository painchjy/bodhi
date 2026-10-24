"""文档内嵌图（批 3b 迁自 `server.py`，**实现逐字未改**）

`image_extract`：把 WeKnora 正文里的 `![alt](resource://<handle>)` 取出来，并**解析成宿主机文件** →
base64 data URL（多模态模型只认 http/data URL，认不了 `resource://` ✗）。**只读**，不写库。
"""
from __future__ import annotations

import os
import re

from ke_db import psql_csv, sql_str
from srv.kbresolve import resolve_kb_id


_IMG_REF_RE = re.compile(r"!\[([^\]]*)\]\((resource://[^)]+)\)")


def _image_root() -> str:
    """WeKnora local 存储根（**宿主机路径** ✓）。

    2026-10-19 实测（P0 图可读）：容器 `WeKnora-app` 的 `/data/files` 挂载到
    `/var/lib/docker/volumes/weknora_data-files/_data` ✓；资源 `physical_path` 形如
    `storage://<backend>/local://<tenant>/exports/<file>` ⇒ 落盘 = `<root>/<tenant>/exports/<file>` ✓。
    可用环境变量 `BODHI_IMAGE_ROOT` 覆盖（换部署时不用改代码 ✓）。
    """
    return (os.environ.get("BODHI_IMAGE_ROOT")
            or "/var/lib/docker/volumes/weknora_data-files/_data").rstrip("/")


def _resolve_image_path(handle: str) -> tuple:
    """handle → (本地绝对路径 or "", 诊断信息 ✓)。"""
    try:
        rows = psql_csv("SELECT physical_path, mime_type, size FROM resources "
                        "WHERE handle = %s AND deleted_at IS NULL LIMIT 1" % sql_str(handle))
    except Exception as exc:  # noqa: BLE001
        return "", "resources_query_failed:%r" % (str(exc)[:60],)
    if not rows:
        return "", "resource_not_found"
    pp = str(rows[0].get("physical_path") or "")
    # storage://<backend>/local://<tenant>/<rest>  →  <root>/<tenant>/<rest>
    rest = ""
    if "local://" in pp:
        rest = pp.split("local://", 1)[1]
    elif pp.startswith("/"):
        return pp if os.path.exists(pp) else "", "abs_path_missing"
    tried = []
    for root in (_image_root(), "/data/files"):
        cand = os.path.join(root, rest) if rest else ""
        if cand:
            tried.append(cand)
            if os.path.exists(cand):
                return cand, "ok"
    return "", "storage_unresolved:%s" % ("|".join(tried[:2]) or pp[:80])


def image_extract(kb_id: str = "", knowledge_id: str = "", figure_no: int = 0,
                  inline: bool = True, max_bytes: int = 1_500_000) -> dict:
    """**取文档内嵌图（只读）**（2026-10-08）。

    WeKnora 把 docx/pptx 的**内嵌图**抽成正文里的 markdown 引用 `![alt](resource://<id>)`
    （存于 `chunks.content`，`chunk_type` 没有 `image`）。本工具把图引用取出来交给
    **会话多模态模型**看；识别按技能 `multimodal_extraction` 的**契约 v0 + 门禁 1–4** 输出。
    """
    kb = (kb_id or "").strip()
    if kb:                                  # 2026-10-19 修：入参可能是**库名**，必须先解析成 UUID ✓
        try:
            kb = resolve_kb_id(kb)[0]
        except Exception:  # noqa: BLE001
            pass
    kid = (knowledge_id or "").strip()
    where = ["deleted_at IS NULL"]
    if kb:
        where.append("knowledge_base_id = %s" % sql_str(kb))
    if kid:
        where.append("(knowledge_id = %s OR knowledge_id IN (SELECT id FROM knowledges "
                     "WHERE title LIKE %s AND deleted_at IS NULL))"
                     % (sql_str(kid), sql_str("%" + kid + "%")))
    rows = psql_csv("SELECT knowledge_id, chunk_index, COALESCE(content,'') AS content FROM chunks "
                    "WHERE %s ORDER BY knowledge_id, chunk_index" % " AND ".join(where))
    imgs, by_doc = [], {}
    for r in rows:
        for m in _IMG_REF_RE.finditer(r["content"]):
            imgs.append({"no": len(imgs) + 1, "alt": m.group(1), "image_ref": m.group(2),
                         "markdown": m.group(0), "knowledge_id": r["knowledge_id"],
                         "chunk_index": r["chunk_index"]})
            by_doc[r["knowledge_id"]] = by_doc.get(r["knowledge_id"], 0) + 1
    # 去重（2026-10-19 ✓）：同一张图常被多个 chunk 重复引用（实测 2 张图 → 4 条引用 ✗）
    seen, uniq = {}, []
    for x in imgs:
        key = x.get("image_ref") or ""
        if key in seen:
            seen[key].setdefault("also_in_chunks", []).append(x["chunk_index"])
            continue
        x["also_in_chunks"] = []
        seen[key] = x
        uniq.append(x)
    for i, x in enumerate(uniq, 1):
        x["no"] = i
    imgs = uniq
    picked = [x for x in imgs if x["no"] == int(figure_no)] if figure_no else imgs

    # **P0 图可读**（2026-10-19 ✓）：把 `resource://<handle>` 解析成宿主机文件 → base64 data URL ✓
    # （多模态模型只认 http/data URL，认不了 resource:// ✗；WeKnora 图片端点要鉴权 401 ✗）
    import base64  # noqa: PLC0415

    readable = 0
    for x in picked:
        handle = str(x.get("image_ref") or "").replace("resource://", "").strip()
        path, why = _resolve_image_path(handle) if handle else ("", "no_handle")
        x["handle"], x["resolve"] = handle, why
        if not path:
            x.update({"readable": False, "reason": why})
            continue
        try:
            size = os.path.getsize(path)
            x["bytes"] = size
            if size > int(max_bytes):
                x.update({"readable": False, "reason": "too_large", "path": path})
                continue
            with open(path, "rb") as fh:
                raw = fh.read()
            mime = ("image/png" if raw[:8] == b"\x89PNG\r\n\x1a\n"
                    else "image/gif" if raw[:3] == b"GIF"
                    else "image/jpeg")
            x.update({"readable": True, "reason": "ok", "mime": mime, "path": path})
            if inline:
                x["data_url"] = "data:%s;base64,%s" % (mime, base64.b64encode(raw).decode())
            readable += 1
        except Exception as exc:  # noqa: BLE001
            x.update({"readable": False, "reason": "read_failed:%r" % (str(exc)[:60],)})

    return {"ok": True, "kb_id": kb, "knowledge_id": kid, "images_found": len(imgs),
            "by_document": by_doc, "images": picked or imgs,
            "images_readable": readable, "inline": bool(inline),
            "how": ("**可读图**：`data_url` 直接放进推理上下文 ✓（多模态模型认 data/http，不认 `resource://` ✗）；"
                    "**不可读**（`readable=false`）⇒ 看 `reason`（`storage_unresolved` / `too_large` / "
                    "`resource_not_found`），报告里**必须写具体原因** ✗ 不许笼统写「未取得可机读图像」；"
                    "识别结果按 `multimodal_extraction` 契约 v0 + 门禁 1–4 输出，再 `save_knowledge`（带 session_no）")}
