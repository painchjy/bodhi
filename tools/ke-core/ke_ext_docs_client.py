"""bodhi-mcp 侧 · **会话附件 → it_platform**（推文件 + 绑档案）

为什么在 02 侧（2026-10-28 用户口径）
-----------------------------------
- 附件在 WeKnora 容器里（`/data/files/<tenant>/exports/chat_attachment_*`），只有 bodhi-mcp
  （宿主机 systemd，有 docker/psql）能读到；
- it_platform（04）是**纯外部服务**：只收文件、只给文件、只存流程与台账 —— 零 PG、零 docker；
- 所以"取附件 → 推送 → 绑定"放在这里；正文解析另走 `ke_doc_parse`（同样本地）。

对外能力
--------
    probe(session_no, doc_title)   只读探测：附件目录 / 最新文件 / 可读性（内网普查 + 排障）
    push(review_no, doc_title, …)  读附件 → POST /files/<doc_id> → it_platform 侧自动 attach
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import urllib.parse

import ke_ext_store as S


def _app_container() -> str:
    return os.environ.get("WEKNORA_APP_CONTAINER") or "WeKnora-app"


def _tenant() -> str:
    return (os.environ.get("BODHI_TENANT_ID") or "10000").strip() or "10000"


def probe(session_no: str = "", doc_title: str = "", limit: int = 10) -> dict:
    """**只读**探测：会话附件在哪些目录、最新一份是谁、能不能读（不写任何数据）。

    两条来源各报各的：
      ① 容器 exports 目录（`/data/files/<tenant>/exports/chat_attachment_*`）—— 实际原件；
      ② 会话记录 `messages.attachments`（有 `session_no` 才查；查不到不影响结论）。
    """
    out = {"ok": True, "tool": "ext_attach_probe", "container": _app_container(),
           "tenant": _tenant(), "dirs": [], "files": [], "session": {}}
    tenant = out["tenant"]
    globs = ["/data/files/%s/exports/chat_attachment_*" % tenant,
             "/data/files/*/exports/chat_attachment_*"]
    for g in globs:
        try:
            r = subprocess.run(["docker", "exec", _app_container(), "sh", "-lc",
                                "ls -l --time-style=+%%s %s 2>/dev/null" % g],
                               capture_output=True, text=True, timeout=10)
        except FileNotFoundError:
            return {**out, "ok": False, "error": "docker_missing",
                    "hint": "宿主机没有 docker 命令 ⇒ 只能由前端/WeKnora 推送附件"}
        except subprocess.TimeoutExpired:
            return {**out, "ok": False, "error": "docker_timeout",
                    "hint": "docker exec 超过 10s ⇒ 检查 WeKnora 容器名与 docker 权限"}
        rows = []
        for ln in (r.stdout or "").splitlines():
            parts = ln.split()
            if len(parts) < 7:
                continue
            try:
                rows.append({"path": parts[6], "size": int(parts[4]), "mtime": int(parts[5])})
            except ValueError:
                continue
        if rows:
            rows.sort(key=lambda x: x["mtime"], reverse=True)
            out["files"] = rows[:limit]
            out["dirs"] = sorted({os.path.dirname(x["path"]) for x in rows})
            out["latest"] = rows[0]
            break
    if session_no:
        try:
            import ke_db  # noqa: PLC0415  只有探测会话记录时才用（04 侧永不加载）
            rows = ke_db.psql_csv(
                "SELECT id, COALESCE(attachments::text,'[]') AS at, created_at FROM messages "
                "WHERE session_id = %s AND attachments IS NOT NULL "
                "ORDER BY created_at DESC LIMIT 3" % ke_db.sql_str(session_no))
            out["session"] = {"rows": [{"message_id": x.get("id"),
                                        "attachments": (x.get("at") or "")[:200],
                                        "at": str(x.get("created_at"))} for x in rows]}
        except Exception as exc:  # noqa: BLE001
            out["session"] = {"error": repr(exc)[:160]}
    if not out["files"]:
        out["ok"] = False
        out["error"] = "attachment_not_found"
        out["hint"] = ("容器 exports 下没有 chat_attachment_*：让用户在会话里重新上传附件，"
                       "或把文件放到 /data/files/%s/exports/" % tenant)
    out["next"] = ("拿到文件后调 `ext_doc_push(review_no=<编号>, doc_title=<文件名>)`："
                   "推给 it_platform 并绑定档案；正文再调 `ext_doc_parse(doc_id=…)`")
    return out



def _pick(files: list, doc_title: str) -> dict:
    """按标题挑一份（缺省最新）；多份同名且未指定 ⇒ 不猜，回 ambiguous。"""
    want = str(doc_title or "").strip().lower()
    if want:
        hit = [f for f in files if want in os.path.basename(f["path"]).lower()]
        if len(hit) == 1:
            return {"ok": True, "pick": hit[0]}
        if len(hit) > 1:
            return {"ok": False, "error": "doc_ambiguous",
                    "candidates": [os.path.basename(f["path"]) for f in hit[:5]],
                    "hint": "同名多份 ⇒ 给更精确的 doc_title，或直接给 doc_id"}
    return ({"ok": True, "pick": files[0]} if files
            else {"ok": False, "error": "attachment_not_found"})


def push(review_no: str, doc_title: str = "", session_no: str = "", doc_id: str = "",
         actor: str = "", title: str = "") -> dict:
    """读会话附件 → `POST {IT_MCP_PUBLIC_BASE}/files/<doc_id>` → it_platform 侧自动绑定档案。"""
    import urllib.error
    import urllib.request

    rno = str(review_no or "").strip()
    if not rno:
        return {"ok": False, "tool": "ext_doc_push", "error": "need_review_no"}
    pr = probe(session_no=session_no, doc_title=doc_title)
    if not pr.get("files"):
        return {"ok": False, "tool": "ext_doc_push",
                "error": pr.get("error") or "attachment_not_found", "hint": pr.get("hint"),
                "probe": {k: pr.get(k) for k in ("dirs", "tenant")}}
    got = _pick(pr["files"], doc_title)
    if not got.get("ok"):
        return {"ok": False, "tool": "ext_doc_push",
                **{k: got.get(k) for k in ("error", "candidates", "hint") if got.get(k)}}
    path = got["pick"]["path"]
    try:
        r = subprocess.run(["docker", "exec", _app_container(), "cat", path],
                           capture_output=True, timeout=60)
    except subprocess.TimeoutExpired:
        return {"ok": False, "tool": "ext_doc_push", "error": "read_timeout", "path": path}
    raw = r.stdout if r.returncode == 0 else b""
    if not raw:
        return {"ok": False, "tool": "ext_doc_push", "error": "read_failed", "path": path,
                "detail": (r.stderr or b"").decode("utf-8", "replace")[:200]}
    base = os.path.basename(path)
    did = (doc_id or "").strip() or ("chat-" + hashlib.sha256(raw).hexdigest()[:8])
    q = "review_no=%s&name=%s&title=%s&round=1&actor=%s" % (
        urllib.parse.quote(rno), urllib.parse.quote(base),
        urllib.parse.quote(title or os.path.splitext(base)[0]),
        urllib.parse.quote(actor or "push"))
    url = "%s/files/%s?%s" % (S.public_base().rstrip("/"), urllib.parse.quote(did), q)
    req = urllib.request.Request(url, data=raw, method="POST",
                                 headers={"Content-Type": "application/octet-stream"})
    try:
        with urllib.request.urlopen(req, timeout=180) as resp:
            up = json.loads(resp.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as exc:
        return {"ok": False, "tool": "ext_doc_push", "error": "ext_http_%s" % exc.code,
                "url": url, "detail": exc.read()[:300].decode("utf-8", "replace")}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "tool": "ext_doc_push", "error": "ext_unreachable", "url": url,
                "detail": repr(exc)[:200]}
    return {"ok": bool(up.get("ok")), "tool": "ext_doc_push", "review_no": rno,
            "doc_id": up.get("doc_id") or did, "size": up.get("size"),
            "sha256": up.get("sha256"), "url": up.get("url"), "flow": up.get("flow"),
            "source": {"path": path, "name": base},
            "next": "正文/图用 `ext_doc_parse(doc_id=…, action=outline|section|image)`"}
