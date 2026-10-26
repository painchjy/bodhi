"""内网 IT 工作台 · MCP 服务（**参考实现 / 打样**）

为什么单独一个服务
------------------
这些接口的**正本在内网 IT 工作台**，不是 bodhi-mcp 的能力：WeKnora 里它是
**与 `bodhi_ontology` 并列的第二个 MCP 服务**（`mcp_services.name = it_platform`），
工具名在 WeKnora 侧形如 `mcp_it_platform_<tool>`。所以本目录是**独立服务**，不复用 bodhi-mcp。

内网怎么落地（照这套结构改）
--------------------------
1. 保持 MCP 协议层（本文件）不变：`initialize` / `tools/list` / `tools/call`；
2. 把**存储层**换掉：`tools/ke-core/ke_ext_store.py` 的"容器文件"改成你们的库表访问
   （SQL/ORM 皆可），字段与幂等键口径照 `bpmn:dataObject*` 四个数据属性；
3. 工具名/入参/回执保持不变 ⇒ 领域库里的 DataObject 绑定、技能与提示词都不用改 ✓。

运行
----
    python3 tools/it-platform-mcp/server.py --port 8770
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
CORE = os.path.join(REPO, "tools", "ke-core")
for p in (HERE, CORE):
    if p not in sys.path:
        sys.path.insert(0, p)

import schemas as S  # noqa: E402
import ke_ext_catalog as CAT  # noqa: E402
import ke_ext_docs as DOCS  # noqa: E402
import ke_ext_flow as FLOW  # noqa: E402
import ke_ext_issues as ISSUE  # noqa: E402

SERVER_NAME = "it-platform-mcp"
SERVER_VERSION = "0.1.0"
PROTOCOL = "2024-11-05"
_SESSIONS: set = set()
_LOCK = threading.Lock()


def _bool(v, default: bool = False) -> bool:
    if v is None or v == "":
        return default
    if isinstance(v, bool):
        return v
    return str(v).strip().lower() not in ("0", "false", "no", "off", "n")


def call_tool(name: str, args: dict) -> dict:
    """工具分发（入参与回执口径与内网替换前的实现保持一致，便于对照改造）。"""
    a = args or {}
    if name == "ext_catalog_query":
        return CAT.query(
            kb_id=str(a.get("kb_id", "")), object_slug=str(a.get("object", "")),
            mode=str(a.get("mode", "query")), action=str(a.get("action", "check")),
            name=str(a.get("name", "")), vendor=str(a.get("vendor", "")),
            license=str(a.get("license", "")), version=str(a.get("version", "")),
            status=str(a.get("status", "")), limit=int(a.get("limit", 20) or 20),
            dry_run=_bool(a.get("dry_run"), True), actor=str(a.get("actor", "")),
            session_no=str(a.get("session_no", "")))
    if name == "ext_issue_list":
        return ISSUE.maintain(
            kb_id=str(a.get("kb_id", "")), object_slug=str(a.get("object", "")),
            mode=str(a.get("mode", "query")), action=str(a.get("action", "list")),
            review_no=str(a.get("review_no", "")), issue_no=str(a.get("issue_no", "")),
            fields=a.get("fields") or None, items=a.get("items") or None,
            view=str(a.get("view", "rows")),
            columns=a.get("columns"), if_rev=a.get("if_rev"), dry_run=_bool(a.get("dry_run"), True),
            actor=str(a.get("actor", "")), session_no=str(a.get("session_no", "")))
    if name == "ext_review_flow":
        return FLOW.call(
            kb_id=str(a.get("kb_id", "")), object_slug=str(a.get("object", "")),
            mode=str(a.get("mode", "query")), action=str(a.get("action", "get")),
            review_no=str(a.get("review_no", "")), round=int(a.get("round", 0) or 0),
            filenames=a.get("filenames") or [], status=str(a.get("status", "")),
            title=str(a.get("title", "")), doc_id=str(a.get("doc_id", "")),
            dry_run=_bool(a.get("dry_run"), True), actor=str(a.get("actor", "")),
            session_no=str(a.get("session_no", "")))
    if name == "ext_doc_access":
        return DOCS.access(
            kb_id=str(a.get("kb_id", "")), object_slug=str(a.get("object", "")),
            action=str(a.get("action", "list")), review_no=str(a.get("review_no", "")),
            doc_id=str(a.get("doc_id", "")), section_path=str(a.get("section_path", "")),
            cursor=int(a.get("cursor", 0) or 0),
            limit_chars=int(a.get("limit_chars", 6000) or 6000),
            inline_media=_bool(a.get("inline_media"), False),
            max_bytes=int(a.get("max_bytes", 1500000) or 1500000),
            doc_title=str(a.get("doc_title", "")), knowledge_id=str(a.get("knowledge_id", "")),
            round=int(a.get("round", 1) or 1), actor=str(a.get("actor", "")),
            session_no=str(a.get("session_no", "")), source=str(a.get("source", "auto")))
    raise ValueError("tool not found: %s（可用：%s）"
                     % (name, ", ".join(t["name"] for t in S.TOOLS)))


def handle_rpc(req: dict) -> tuple:
    """→ `(payload, session_id, is_notification)`；`payload=None` = 无响应体（通知）。"""
    method = str(req.get("method") or "")
    rid = req.get("id")
    if method == "initialize":
        sid = uuid.uuid4().hex[:24]
        with _LOCK:
            _SESSIONS.add(sid)
        return ({"jsonrpc": "2.0", "id": rid,
                 "result": {"protocolVersion": PROTOCOL,
                            "capabilities": {"tools": {"listChanged": False}},
                            "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION}}},
                sid, False)
    if method.startswith("notifications/"):
        return None, "", True
    if method == "ping":
        return {"jsonrpc": "2.0", "id": rid, "result": {}}, "", False
    if method == "tools/list":
        return ({"jsonrpc": "2.0", "id": rid,
                 "result": {"tools": S.tools_with_ext()}}, "", False)
    if method == "tools/call":
        params = req.get("params") or {}
        name = str(params.get("name") or "")
        try:
            out = call_tool(name, params.get("arguments") or {})
            is_err = bool(out.get("ok") is False)
        except Exception as exc:  # noqa: BLE001  回执里给可读原因（不 500）
            import traceback
            out, is_err = {"ok": False, "error": repr(exc)[:400],
                           "traceback_tail": traceback.format_exc()[-900:]}, True
        return ({"jsonrpc": "2.0", "id": rid,
                 "result": {"content": [{"type": "text",
                                         "text": json.dumps(out, ensure_ascii=False)}],
                            "isError": is_err}}, "", False)
    return ({"jsonrpc": "2.0", "id": rid,
             "error": {"code": -32601, "message": "method not found: %s" % method}}, "", False)


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "%s/%s" % (SERVER_NAME, SERVER_VERSION)

    def log_message(self, fmt, *a):
        pass

    def _log(self, code: int):
        """访问日志（stdout ⇒ `/var/log/bodhi-it-mcp.log` 与 journald）。

        排障价值：WeKnora 只在**首次同步/点刷新**时才访问 MCP 服务，之后用缓存；
        有了这行就能直接判断"到底有没有打进来、**调用的是哪个方法/工具**、返回几个字节"，
        不用再猜前端/后端谁的问题。设 `IT_MCP_QUIET=1` 可关。
        """
        if os.environ.get("IT_MCP_QUIET") == "1":
            return
        rpc = getattr(self, "_rpc", "") or "-"
        print("%s %s %s -> %d  rpc=%s  accept=%r ua=%r"
              % (time.strftime("%Y-%m-%d %H:%M:%S"), self.command, self.path, code, rpc,
                 (self.headers.get("Accept") or "-")[:60],
                 (self.headers.get("User-Agent") or "-")[:40]), flush=True)

    def _send(self, code: int, body: bytes, sid: str = "", ctype="application/json"):
        self._log(code)
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        if sid:
            self.send_header("Mcp-Session-Id", sid)
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = self.path.split("?")[0].rstrip("/")
        if path in ("/health", ""):
            self._send(200, json.dumps({"ok": True, "server": SERVER_NAME,
                                        "tools": len(S.TOOLS)}).encode())
            return
        if path.startswith("/files/"):
            # 附件**下载**（2026-10-26）：外部系统给 URL、不给"上传" ⇒ 这里按 doc_id 流式返回文件流。
            # 仅服务**容器根**内的文件（防穿越）；`?name=` 可指定附件名，缺省取第一份。
            self._send_file(path[len("/files/"):])
            return
        self._send(405, b'{"ok":false,"error":"use POST /mcp"}')

    def _upload_file(self, n: int) -> None:
        """`POST /files/<doc_id>[?review_no=&name=&title=&round=]` → 存盘 + 切节 + 挂索引 ✓。"""
        import urllib.parse

        path = self.path.split("?")[0].rstrip("/")
        doc_id = urllib.parse.unquote(path[len("/files/"):])
        q = urllib.parse.parse_qs(self.path.split("?", 1)[1]) if "?" in self.path else {}

        def _q(k: str, default: str = "") -> str:
            return (q.get(k) or [default])[0]

        raw = self.rfile.read(int(n)) if n else b""
        if not raw:
            self._send(400, json.dumps({"ok": False, "error": "empty_body"},
                                       ensure_ascii=False).encode())
            return
        try:
            out = DOCS.save_document(
                doc_id=doc_id or "doc-%s" % time.strftime("%Y%m%d%H%M%S"),
                review_no=_q("review_no"), filename=_q("name") or (doc_id + ".bin"),
                raw=raw, title=_q("title"), round=int(_q("round", "1") or 1),
                actor=_q("actor"), session_no=_q("session_no"))
        except Exception as exc:  # noqa: BLE001
            self._send(500, json.dumps({"ok": False, "error": repr(exc)[:300]},
                                       ensure_ascii=False).encode())
            return
        self._send(200, json.dumps(out, ensure_ascii=False).encode())

    def _send_file(self, doc_id: str) -> None:
        """`GET /files/<doc_id>[?name=<附件名>]` → 附件二进制（带 Content-Disposition ✓）。"""
        import urllib.parse

        q = urllib.parse.parse_qs(self.path.split("?", 1)[1]) if "?" in self.path else {}
        want = (q.get("name") or [""])[0]
        try:
            info = DOCS.doc_file(doc_id, want)
        except Exception as exc:  # noqa: BLE001
            info = {"ok": False, "error": repr(exc)[:200]}
        if not info.get("ok"):
            self._send(404, json.dumps({"ok": False, "error": info.get("error") or "not_found",
                                        "doc_id": doc_id},
                                       ensure_ascii=False).encode())
            return
        try:
            with open(info["path"], "rb") as fh:
                raw = fh.read()
        except Exception as exc:  # noqa: BLE001
            self._send(500, json.dumps({"ok": False, "error": repr(exc)[:200]}).encode())
            return
        name = info.get("name") or "attachment"
        self._log(200)
        self.send_response(200)
        self.send_header("Content-Type", info.get("mime") or "application/octet-stream")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Content-Disposition",
                         "attachment; filename*=UTF-8''%s" % urllib.parse.quote(name))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(raw)

    def do_OPTIONS(self):
        self.send_response(204)
        for k, v in (("Access-Control-Allow-Origin", "*"),
                     ("Access-Control-Allow-Headers", "content-type,mcp-session-id"),
                     ("Access-Control-Allow-Methods", "POST,GET,OPTIONS")):
            self.send_header(k, v)
        self.end_headers()

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        # 2026-10-26：**上传文档**（内网工作台未接通前的补位）
        #   POST /files/<doc_id>?review_no=…&name=…&title=…&round=1   （body = 文件二进制）
        if self.path.split("?")[0].rstrip("/").startswith("/files/"):
            self._upload_file(n)
            return
        raw = self.rfile.read(n) if n else b"{}"
        try:
            req = json.loads(raw.decode("utf-8") or "{}")
        except Exception:  # noqa: BLE001
            self._send(400, b'{"error":"bad json"}')
            return
        # 记下 JSON-RPC 方法 / 工具名（访问日志据此区分 `tools/list` 与 `tools/call` ✓）
        _p = req.get("params") or {}
        self._rpc = "%s%s" % (req.get("method") or "?",
                              ("/" + str(_p.get("name"))) if _p.get("name") else "")
        if self.path.rstrip("/") not in ("/mcp",):
            self._send(404, b'{"error":"not found"}')
            return
        payload, sid, is_note = handle_rpc(req)
        if is_note or payload is None:
            self._send(202, b"", sid)
            return
        raw = json.dumps(payload, ensure_ascii=False)
        if "text/event-stream" in (self.headers.get("Accept") or ""):
            body = ("event: message\ndata: %s\n\n" % raw).encode("utf-8")
            self._send(200, body, sid, ctype="text/event-stream")
        else:                       # 普通客户端（含测试脚本）⇒ 直接 JSON ✓
            self._send(200, raw.encode("utf-8"), sid)


def main() -> int:
    ap = argparse.ArgumentParser(description="内网 IT 工作台 MCP 服务（参考实现）")
    ap.add_argument("--host", default=os.environ.get("IT_MCP_HOST", "127.0.0.1"))
    ap.add_argument("--port", type=int, default=int(os.environ.get("IT_MCP_PORT", "8770")))
    args = ap.parse_args()
    srv = ThreadingHTTPServer((args.host, args.port), Handler)
    print("[%s] listening on http://%s:%d/mcp（tools=%d）"
          % (SERVER_NAME, args.host, args.port, len(S.TOOLS)), flush=True)
    srv.serve_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())
