"""HTTP / JSON-RPC transport (P2 slice S9, moved verbatim from server.py).

server.main() calls bind(globals()) before serve_forever, so every server-level name
used by this transport (call_tool, tool_definitions, handlers...) resolves here.
=> no per-name injection, and this module never imports server (no cycle).
"""
from __future__ import annotations

import argparse, hashlib, json, os, pathlib, sys, threading, time, uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib import parse as urlparse

_CORE = os.path.abspath(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "..", "ke-core"))
if _CORE not in sys.path:
    sys.path.insert(0, _CORE)
from srv.constants import EXPORT_DIR, PROTOCOL_VERSION, SERVER_NAME, SERVER_VERSION  # noqa: E402
from srv.context_write import (  # noqa: E402
    context_concept_apply, context_concept_rollback,
)
from srv.tools_ops import kb_export, kb_import  # noqa: E402


def bind(ns: dict) -> None:
    """Bind server-level globals into this module namespace (called once from server.main)."""
    globals().update({k: v for k, v in ns.items() if k not in globals() and not k.startswith("__")})


class MCPHandler(BaseHTTPRequestHandler):
    server_version = "%s/%s" % (SERVER_NAME, SERVER_VERSION)
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):  # 静音默认访问日志
        print("[mcp] %s - %s" % (self.address_string(), fmt % args))

    def _send(self, code: int, body: bytes, ctype: str, extra: dict | None = None) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("MCP-Protocol-Version", PROTOCOL_VERSION)
        for key, value in (extra or {}).items():
            self.send_header(key, value)
        self.end_headers()
        if body:
            self.wfile.write(body)

    def _json(self, payload: dict, code: int = 200, extra: dict | None = None) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self._send(code, body, "application/json", extra)

    def do_POST(self) -> None:  # noqa: N802
        # bodhi2 前端用的 JSON 接口（/bodhi/*）与 MCP 传输（/mcp）共用同一端口。
        parsed = urlparse.urlsplit(self.path)
        path = parsed.path.rstrip("/") or "/"
        # 调用者租户：`X-Bodhi-Tenant` 头（WeKnora 的 mcp_services.headers 可配）→ 写权限校验用；
        # 没带就问 env `BODHI_TENANT_ID`；都没有 → 写路径 fail-closed 拒（只读照常）。
        raw_tenant = str(self.headers.get("X-Bodhi-Tenant") or "").strip()
        ke_db.set_request_tenant(int(raw_tenant) if raw_tenant.isdigit() else None)
        if path in ("/bodhi/kb-upload", "/bodhi/kb-import-upload"):
            self._kb_upload(path)      # **原始字节**上传（非 JSON），故必须在解析 JSON 之前分流
            return
        if path.startswith("/bodhi/"):
            self._bodhi_post(path)
            return
        if path not in ("", "/mcp"):
            self._json({"jsonrpc": "2.0", "id": None,
                        "error": {"code": -32600, "message": "未知端点：%s" % self.path}}, 404)
            return
        length = int(self.headers.get("Content-Length") or 0)
        try:
            request = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
        except json.JSONDecodeError as exc:
            self._json({"jsonrpc": "2.0", "id": None,
                        "error": {"code": -32700, "message": "JSON 解析失败：%s" % exc}}, 400)
            return

        method = request.get("method", "")
        params = request.get("params") or {}
        rid = request.get("id")
        extra = {"Mcp-Session-Id": hashlib.sha1(str(uuid.uuid4()).encode()).hexdigest()[:24]}

        if method.startswith("notifications/"):
            self._send(202, b"", "application/json", extra)
            return

        if method == "initialize":
            result = {
                "protocolVersion": params.get("protocolVersion") or PROTOCOL_VERSION,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
                "instructions": ("本体知识保存工具：按 `domain_modeling` 技能**分批**工作 —— "
                                 "`doc_outline` 取本批上下文 → 自己比对后 "
                                 "`save_knowledge(stage=\"graph\")` 落库 → `extract_state` 看会话进度；"
                                 "服务端只做确定性校验与写入。整篇抽取工具（原名 `extract_and_save`）"
                                 "已于 2026-09-22 退役。"),
            }
        elif method == "ping":
            result = {}
        elif method == "tools/list":
            result = {"tools": tool_definitions()}
        elif method == "tools/call":
            name = params.get("name", "")
            arguments = params.get("arguments") or {}
            print("[mcp] tools/call %s %s" % (name, json.dumps(arguments, ensure_ascii=False)))
            try:
                _started = time.time()
                # P1（2026-10-09）**两段写日志**：进入工具前先落 BEGIN 行 + 完整入参文件，
                # 再开 IO 统计桶；这样被客户端超时切断的调用**也有痕迹**（旧实现一行都没有）。
                _args_file = _log_tool_begin(name, arguments)
                ke_io_stats.begin()
                payload = call_tool(name, arguments)
                result = tool_result(payload)
                _log_tool_call(name, arguments, payload, ms=(time.time() - _started) * 1000.0,
                               io=ke_io_stats.snapshot(), args_file=_args_file)
            except Exception as exc:  # noqa: BLE001
                print("[mcp] 工具失败：%s" % exc)
                result = tool_result({"error": str(exc)}, is_error=True)
                try:
                    _log_tool_call(name, arguments, {"error": str(exc)},
                                   ms=(time.time() - _started) * 1000.0,
                                   io=ke_io_stats.snapshot(), args_file=_args_file, phase="ERR")
                except Exception:  # noqa: BLE001
                    pass
            finally:
                ke_io_stats.reset()
        else:
            self._json({"jsonrpc": "2.0", "id": rid,
                        "error": {"code": -32601, "message": "未实现的方法：%s" % method}}, 200, extra)
            return

        payload = {"jsonrpc": "2.0", "id": rid, "result": result}
        if "text/event-stream" in (self.headers.get("Accept") or ""):
            body = ("event: message\ndata: %s\n\n"
                    % json.dumps(payload, ensure_ascii=False)).encode("utf-8")
            self._send(200, body, "text/event-stream", extra)
        else:
            self._json(payload, 200, extra)

    # ------------------------------------------------------------------
    # bodhi2 前端 JSON 接口（/bodhi/*）：与 MCP 传输共端口，见 do_POST 分支
    # ------------------------------------------------------------------
    CORS = {"Access-Control-Allow-Origin": "*"}

    def _read_json(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        try:
            return json.loads(self.rfile.read(length).decode("utf-8") or "{}")
        except json.JSONDecodeError as exc:
            raise ValueError("JSON 解析失败：%s" % exc) from exc

    def _kb_upload(self, path: str) -> None:
        """`POST /bodhi/kb-upload?name=<file.zip>[&new_kb=…|&kb_id=…][&keep_ids=1][&verify=1]`

        **body 就是文件原始字节**（不是 JSON、不是 multipart）→ curl / 浏览器 fetch 都能发：

            curl -X POST --data-binary @kb-xfer.zip \\
              "http://<mcp主机>:8765/bodhi/kb-upload?name=kb-xfer.zip&new_kb=<目标库名>&verify=1"

        存到 `exports/uploads/<name>`；带 `new_kb`/`kb_id` 时**顺带导入**（默认 verify=1 → 回执含 `verify_ok`）。
        """
        params = dict(urlparse.parse_qsl(urlparse.urlsplit(self.path).query))
        name = pathlib.Path(params.get("name", "")).name
        if not name.lower().endswith(".zip"):
            self._json({"error": "只接收 .zip：请给 name=<文件名.zip>（body 用 --data-binary @文件）"},
                       400, self.CORS)
            return
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            self._json({"error": "空 body：请用 curl --data-binary @<zip> 上传（或上传页选文件）"},
                       400, self.CORS)
            return
        updir = EXPORT_DIR / "uploads"
        updir.mkdir(parents=True, exist_ok=True)
        fp = updir / name
        fp.write_bytes(self.rfile.read(length))
        out = {"ok": True, "saved": str(fp), "bytes": length,
               "note": "已收到 zip；带 new_kb/kb_id 时本端点会顺带导入"}
        if params.get("new_kb") or params.get("kb_id"):
            # 2026-10-20 修（用户实测）：导入异常时**必须返回 JSON 错误** ✓
            # 旧实现异常直接冒泡 ⇒ 浏览器收不到响应 ⇒ 前端永远停在"上传中…"✗
            try:
                out["import"] = kb_import(str(fp), kb_id=params.get("kb_id", ""),
                                          new_kb=params.get("new_kb", ""),
                                          keep_ids=bool(params.get("keep_ids")),
                                          verify=bool(params.get("verify", "1")))
            except BaseException as exc:  # noqa: BLE001  SystemExit 也要接住 ✓
                import traceback as _tb  # noqa: PLC0415
                tb = _tb.format_exc()
                out.update({"ok": False, "error": "%s: %s" % (type(exc).__name__, str(exc)[:400]),
                            "traceback_tail": tb[-1200:],
                            "note": ("导入中断 ✗（zip 已收下，可修好后重跑本端点 ✓）；"
                                     "常见：修订唯一键冲突 / 图写入失败 —— 看 error 与 traceback_tail")})
        self._json(out, 200 if out.get("ok") else 500, self.CORS)

    def _bodhi_post(self, path: str) -> None:
        try:
            body = self._read_json()
        except ValueError as exc:
            self._json({"error": str(exc)}, 400, self.CORS)
            return
        if path in ("/bodhi/ext/update", "/bodhi/ext/describe", "/bodhi/ext/sync-report",
                    "/bodhi/ext/bind", "/bodhi/ext/unbind", "/bodhi/ext/mapping",
                    "/bodhi/ext/tool/refresh", "/bodhi/ext/tool/sync",
                    "/bodhi/ext/tool/create"):
            # 面板写台账 / 刷新契约 / 快照回写 / 绑定与映射（2026-10-29）
            try:
                from srv import ext_api                     # noqa: PLC0415
                if path.endswith("update"):
                    out = ext_api.update_payload(body)
                elif path.endswith("sync-report"):
                    out = ext_api.sync_report_payload(body)
                elif path.endswith("/bind"):
                    out = ext_api.bind_payload(body)
                elif path.endswith("/unbind"):
                    out = ext_api.unbind_payload(body)
                elif path.endswith("/mapping"):
                    out = ext_api.mapping_payload(body)
                elif path.endswith("/tool/refresh"):
                    out = ext_api.tool_refresh_payload(body)
                elif path.endswith("/tool/sync"):
                    out = ext_api.tool_sync_payload(body)
                elif path.endswith("/tool/create"):
                    out = ext_api.tool_create_payload(body)
                else:
                    out = ext_api.describe_payload(body)

            except Exception as exc:  # noqa: BLE001
                print("[mcp] %s 失败：%s" % (path, exc))
                self._json({"error": str(exc)}, 400, self.CORS)
                return
            self._json(out, 200 if out.get("ok") else 400, self.CORS)
            return
        def _reject_if_failed(result, what: str):
            """门禁回执（`ok=False`）→ 抛 ValueError（HTTP 层映射成 400 `{"error": …}`）。

            用户口径 2026-09-30：不合格的 TTL **不允许导入**，且提示里要给出**可直接复制**的
            修复片段。HTTP 层原本把 `ok=False` 的 dict 当 200 返回 → 前端弹"导入完成"面板，
            用户会以为"删掉 bodhi:shortName 也能导入"（实测确认）。前端只展示 400 的
            `error` 原文，所以下面把它拼成多行文本（含两行片段）。
            """
            if not (isinstance(result, dict) and result.get("ok") is False):
                return result
            parts = ["%s 被拒绝：%s" % (what, result.get("error") or "校验未通过")]
            for key in ("note", "hint", "snippet"):
                if result.get(key):
                    parts.append(str(result[key]))
            for m in result.get("missing") or []:
                parts.append("· %s：%s" % (m.get("module") or "",
                                           "、".join(m.get("missing") or [])))
                if m.get("snippet"):
                    parts.append(str(m["snippet"]))
                if m.get("note"):
                    parts.append(str(m["note"]))
            for h in result.get("hints") or []:
                parts.append("· %s" % h)
            raise ValueError("\n".join(p for p in parts if p))

        import ke_graph as _ke_graph  # noqa: PLC0415  关系读写统一走图实例（2026-10-05 M2）

        handlers = {
            "/bodhi/relations/add":
                lambda b: _ke_graph.add_edge(b.get("kb_id", ""), b.get("slug", ""),
                                             b.get("rel_type", ""), b.get("target_slug", ""),
                                             b.get("label", "")),
            "/bodhi/relations/update":
                lambda b: _ke_graph.update_edge(b.get("kb_id", ""), b.get("slug", ""),
                                                b.get("target_slug", ""), b.get("new_rel_type", ""),
                                                b.get("new_target_slug", ""), b.get("label", "")),
            "/bodhi/relations/delete":
                lambda b: _ke_graph.delete_edge(b.get("kb_id", ""), b.get("slug", ""),
                                                b.get("target_slug", ""), b.get("rel_type", "")),
            "/bodhi/page/type":
                lambda b: ke_pages.set_page_type(b.get("kb_id", ""), b.get("slug", ""),
                                                 b.get("page_type", "")),
            # 数据属性写口（方案 D，2026-10-13）：**只改数据属性**（entity 模式页）；
            # `dry_run=1`（默认）只回 diff。三处同写：PG 元数据 + 图节点属性 + 正文属性段（标题式）。
            "/bodhi/page/attributes":
                lambda b: ke_pages.set_page_attributes(b.get("kb_id", ""), b.get("slug", ""),
                                                       b.get("patch") or {},
                                                       actor=str(b.get("actor", "")),
                                                       dry_run=bool(b.get("dry_run", True)),
                                                       # 「以摘要为准同步」按钮（2026-10-15 用户口径）：
                                                       # 把 PG `summary` 写进定义键的 元数据 + 图节点（**不含正文**）
                                                       sync_from_summary=bool(b.get("sync_from_summary", False))),
            "/bodhi/delete":
                lambda b: ke_pages.delete_pages(b.get("kb_id", ""), b.get("slugs") or [],
                                                bool(b.get("dry_run"))),
            "/bodhi/kb/wiki-flag":
                lambda b: ke_pages.set_wiki_enabled(b.get("kb_id", ""), bool(b.get("enabled"))),
            # 本体模型维护（用户 2026-09-20）：编译 → 清理旧模型 → 灌投影 → 重投影 wiki
            "/bodhi/ontology/load":
                lambda b: ke_admin.load_model(b.get("model_id", ""), b.get("kb_id", ""),
                                              bool(b.get("purge", True)),
                                              bool(b.get("compile", True)),
                                              bool(b.get("project_wiki", True))),
            "/bodhi/ontology/purge":
                lambda b: ke_admin.purge_model(b.get("model_id", ""), b.get("kb_id", "")),
            "/bodhi/ontology/apply":
                lambda b: ke_admin.apply_projection(),
            "/bodhi/ontology/wiki":
                lambda b: ke_admin.regen_wiki(b.get("kb_id", "")),
            # 上传即加载（用户 2026-09-20）：前端读文件文本 → JSON 传过来（免 multipart），
            # 落 ontology/uploads/ → 级联删下游 → 解析入库（零产物）。依赖未就绪会抛 ValueError → 400。
            "/bodhi/ontology/upload":
                lambda b: _reject_if_failed(ke_admin.upload_ttl(b.get("filename", ""), b.get("content", ""),
                                              b.get("module_id", ""),
                                              # 默认 True：上传会级联删下游，必须重投影（否则目录空）
                                              bool(b.get("project_wiki", True)),
                                              b.get("kb_id", ""),
                                              # 2026-09-24：默认"落真源 + 编译并生效"（可在前端关掉）
                                              write_source=bool(b.get("write_source", True)),
                                              compile_after=bool(b.get("compile_after", True)),
                                              apply_after=bool(b.get("apply_after", False))),
                                               what="上传导入"),
            # 运维修复（幂等）：编译 → 灌 Neo4j 投影 → 重投影本体库 wiki → 一致性体检
            "/bodhi/ontology/repair":
                lambda b: _reject_if_failed(ke_admin.repair_all(b.get("kb_id", ""),
                                                                bool(b.get("compile", True)),
                                                                bool(b.get("project_wiki", True))),
                                            what="repair"),
            # 类型迁移（retag，2026-09-27 用户口径）：改本体类型 = **迁移 slug + 联动引用**，
            #   两段式：preview（只读影响面 + ticket）→ 用户确认 → apply（ticket + 风险确认，缺一即拒）
            "/bodhi/page/retag/preview":
                lambda b: ke_pages.retag_preview(b.get("kb_id", ""), b.get("slug", ""),
                                                 b.get("new_type", "")),
            "/bodhi/page/retag/apply":
                lambda b: ke_pages.retag_apply(b.get("kb_id", ""), b.get("slug", ""),
                                               b.get("new_type", ""), b.get("ticket", ""),
                                               b.get("acknowledge_risks") or []),
            "/bodhi/page/retag/rollback":
                lambda b: ke_pages.retag_rollback(b.get("kb_id", ""), b.get("ticket", "")),
            # 按来源文档清理本体实例（用户 2026-09-20 第二问：删文档不会联动清实例层）
            #   删「独占页」+ 多源页摘引用；默认 dry-run（apply=false 只出计划）
            "/bodhi/context/concept/preview":
                lambda b: ke_context.concept_preview(str(b.get("slug", "")),
                                                     bool(b.get("include_unknown", False)),
                                                     allow_distinct=bool(b.get("allow_distinct", False))),
            "/bodhi/context/concept/apply":
                lambda b: context_concept_apply(str(b.get("slug", "")), str(b.get("ticket", "")),
                                               b.get("acknowledge_risks") or [],
                                               str(b.get("definition", "")),
                                               str(b.get("standard_name", "")),
                                               str(b.get("actor", ""))),
            "/bodhi/context/concept/rollback":
                lambda b: context_concept_rollback(str(b.get("ticket", ""))),
            "/bodhi/context/cache/rebuild":
                lambda b: ke_context.rebuild_cache(),
            "/bodhi/context/concept/state":
                lambda b: ke_context.set_concept_state(str(b.get("slug", "")), str(b.get("state", "")),
                                                       by=str(b.get("by", "")), note=str(b.get("note", ""))),
            "/bodhi/context/authority/decide":
                lambda b: ke_context.authority_decide(str(b.get("slug", "")), str(b.get("master", "")),
                                                      str(b.get("ticket", "")),
                                                      b.get("acknowledge_risks") or [],
                                                      str(b.get("actor", ""))),
            "/bodhi/context/authority/pull":
                lambda b: ke_context.authority_pull(str(b.get("kb", "")), str(b.get("slug", "")),
                                                    str(b.get("ticket", "")),
                                                    b.get("acknowledge_risks") or [],
                                                    str(b.get("actor", "")),
                                                    apply=bool(b.get("apply", False))),
            # 结构化批量建模（一次一个类/一条关系）
            "/bodhi/import/plan":
                lambda b: ke_import.plan(kind=str(b.get("kind", "")), target=str(b.get("target", "")),
                                         file=str(b.get("file", "")), kb_id=str(b.get("kb_id", "")),
                                         sheet=str(b.get("sheet", "")), mapping=b.get("mapping") or {},
                                         key_column=str(b.get("key_column", "")),
                                         title=str(b.get("title", "")), aliases=b.get("aliases") or [],
                                         source_key_column=str(b.get("source_key_column", "")),
                                         target_key_column=str(b.get("target_key_column", "")),
                                         source_class=str(b.get("source_class", "")),
                                         target_class=str(b.get("target_class", "")),
                                         unknown_to_description=bool(b.get("unknown_to_description", True)),
                                         prune=bool(b.get("prune", False)),
                                         enums=b.get("enums") or None,
                                         batch_id=str(b.get("batch_id", "")),
                                         limit=int(b.get("limit", 0) or 0)),
            "/bodhi/import/apply":
                lambda b: ke_import.apply(str(b.get("ticket", "")), actor=str(b.get("actor", "http:import")),
                                          prune=bool(b["prune"]) if "prune" in b else None),
            # 结构化批量建模：元数据刷新（存量页一次性对齐）
            "/bodhi/import/refresh":
                lambda b: ke_import.refresh_metadata(str(b.get("kb_id", "")), bool(b.get("dry_run", False)),
                                                     int(b.get("limit", 5000) or 5000)),
            # 巡检清理：有库写权限即可一步硬删
            "/bodhi/audit/purge":
                lambda b: ke_audit.purge(str(b.get("kb_id", "")), str(b.get("kinds", "all")), "all", 5000,
                                         b.get("slugs") or None, None, bool(b.get("dry_run", False))),
            # 文档核对
            "/bodhi/review/apply":
                lambda b: ke_review.review_apply(
                    kb_id=str(b.get("kb_id", "")), doc=str(b.get("doc", "")),
                    policy=str(b.get("policy", "")), policy_slug=str(b.get("policy_slug", "")),
                    findings=b.get("findings") or [],
                    page_type=str(b.get("page_type", "") or "bmm:TechnicalReviewReport"),
                    actor=str(b.get("actor", "http:review")),
                    session_no=_resolve_session_for(str(b.get("kb_id", "")), str(b.get("session_no", "")))[0],
                    session_slug=_resolve_session_for(str(b.get("kb_id", "")), str(b.get("session_no", "")))[1]),
            "/bodhi/context/ensure-marks":
                lambda b: ke_context.ensure_marks(),
            "/bodhi/docs/purge":
                lambda b: ke_docs.purge_document(b.get("kb_id", ""), b.get("knowledge_id", ""),
                                                 b.get("title", ""), bool(b.get("apply", False)),
                                                 bool(b.get("sync_folders", True)),
                                                 bool(b.get("delete_exclusive", True))),
            # 巡检清理：plan 只读出计划；apply 必须带 confirm=true（人工确认后才执行，硬删）
            "/bodhi/audit/plan":
                lambda b: ke_audit.build_plan(b.get("kb_id", ""), b.get("kinds", "all"),
                                              b.get("scope", "all"),
                                              int(b.get("page_limit", 5000) or 5000)),
            "/bodhi/audit/apply":
                lambda b: ke_audit.apply_plan(b.get("kb_id", ""), b.get("plan_id", ""),
                                              bool(b.get("confirm", False)),
                                              int(b.get("page_limit", 5000) or 5000)),
            # 巡检兜底：把所有「来源文档已删/不存在」的残留一次清掉（默认 dry-run）
            "/bodhi/docs/sweep":
                lambda b: ke_docs.sweep(b.get("kb_id", ""), bool(b.get("apply", False)),
                                        bool(b.get("include_missing", True)),
                                        bool(b.get("delete_exclusive", True))),
        }
        fn = handlers.get(path)
        if not fn:
            self._json({"error": "未知端点：%s" % path}, 404, self.CORS)
            return
        try:
            print("[mcp] %s %s" % (path, json.dumps(body, ensure_ascii=False)[:200]))
            self._json(fn(body), 200, self.CORS)
        except ValueError as exc:      # 校验类错误 → 400（前端直接展示原文）
            self._json({"error": str(exc)}, 400, self.CORS)
        except Exception as exc:  # noqa: BLE001
            print("[mcp] %s 失败：%s" % (path, exc))
            self._json({"error": str(exc)}, 500, self.CORS)

    def do_OPTIONS(self) -> None:  # noqa: N802
        self._send(204, b"", "text/plain",
                   {"Access-Control-Allow-Origin": "*",
                    "Access-Control-Allow-Methods": "GET,POST,OPTIONS",
                    "Access-Control-Allow-Headers": "Content-Type"})

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse.urlparse(self.path)
        path = parsed.path.rstrip("/")
        if path in ("/bodhi/ext/list", "/bodhi/ext/schema",
                    "/bodhi/ext/binding", "/bodhi/ext/tool",
                    "/bodhi/ext/service/tools", "/bodhi/ext/mapping/candidates",
                    "/bodhi/ext/mapping/stale"):
            # 面板读台账 / 读接口列清单 / 读绑定与工具契约（2026-10-29）
            params = dict(urlparse.parse_qsl(parsed.query))
            try:
                from srv import ext_api                   # noqa: PLC0415
                if path.endswith("/schema"):
                    out = ext_api.schema_payload(params)
                elif path.endswith("/binding"):
                    out = ext_api.binding_payload(params)
                elif path.endswith("/service/tools"):
                    out = ext_api.service_tools_payload(params)
                elif path.endswith("/mapping/candidates"):
                    out = ext_api.mapping_candidates_payload(params)
                elif path.endswith("/mapping/stale"):
                    out = ext_api.mapping_stale_payload(params)
                elif path.endswith("/tool"):
                    out = ext_api.tool_payload(params)
                else:
                    out = ext_api.list_payload(params)

                self._json(out, 200 if out.get("ok") else 400, self.CORS)
            except Exception as exc:  # noqa: BLE001
                print("[mcp] %s 失败：%s" % (path, exc))
                self._json({"error": str(exc)}, 400, self.CORS)
            return
        if path in ("/bodhi/ontology/classes", "/bodhi/ontology/classes.json"):
            try:
                self._json(ke_ontology.classes(), 200, self.CORS)
            except Exception as exc:  # noqa: BLE001
                print("[mcp] /bodhi/ontology/classes 失败：%s" % exc)
                self._json({"error": str(exc)}, 400, self.CORS)
            return
        if path.startswith("/bodhi/page/attributes"):
            # 数据属性**读**口（方案 D，2026-10-13，只读）：前端「数据属性」面板用 ——
            # 回 `attributes`（事实源现值）、`source`（meta/graph/content）、`editable`、
            # `allowed`（该类可编辑属性；**已剔除正文载体** `schema:articleBody`/`bmm:wikiContent`）、
            # `body_keys`（正文键，仅说明用；正文改走 `stage="report"`）、`drift`（三处不一致的键）。
            params = dict(urlparse.parse_qsl(parsed.query))
            try:
                self._json(ke_pages.page_attributes(str(params.get("kb_id", "")),
                                                    str(params.get("slug", ""))), 200, self.CORS)
            except Exception as exc:  # noqa: BLE001
                self._json({"error": str(exc)}, 400, self.CORS)
            return
        if path.startswith("/bodhi/ontology/relation-types"):
            params = dict(urlparse.parse_qsl(parsed.query))
            try:
                self._json(ke_ontology.relation_types_for(params.get("page_type", "")), 200, self.CORS)
            except Exception as exc:  # noqa: BLE001
                print("[mcp] /bodhi/ontology/relation-types 失败：%s" % exc)
                self._json({"error": str(exc)}, 400, self.CORS)
            return
        if path.startswith("/bodhi/session/link"):
            # **补来源会话边（显式入口）**：给知识页补 `bmm:sourceSession` 双链 → 会话页（分页优先）。
            # `dry_run=1`（默认）只出清单；`all=1` = 本库所有缺该行的非会话页。
            params = dict(urlparse.parse_qsl(parsed.query))
            try:
                slugs = [s for s in (params.get("slug", "") or "").split(",") if s.strip()]
                self._json(link_source_session(params.get("kb_id", ""), params.get("session_no", ""),
                                               slugs=slugs or None,
                                               all_pages=params.get("all", "0") not in ("0", "false", ""),
                                               dry_run=params.get("dry_run", "1") not in ("0", "false", "")),
                           200, self.CORS)
            except Exception as exc:  # noqa: BLE001
                print("[mcp] /bodhi/session/link 失败：%s" % exc)
                self._json({"error": str(exc)}, 400, self.CORS)
            return
        if path.startswith("/bodhi/folders/recategorize"):
            # **重新编目（显式入口，不自动跑）**：按当前本体重算本体实例页的 category_path/wiki_path
            # 再重建目录（挂页+prune）。`dry_run=1`（默认）只出清单。用户口径 2026-10-04。
            params = dict(urlparse.parse_qsl(parsed.query))
            try:
                self._json(recategorize(params.get("kb_id", ""),
                                        params.get("dry_run", "1") not in ("0", "false", ""),
                                        int(params.get("depth", "1") or 1)),
                           200, self.CORS)
            except Exception as exc:  # noqa: BLE001
                print("[mcp] /bodhi/folders/recategorize 失败：%s" % exc)
                self._json({"error": str(exc)}, 400, self.CORS)
            return
        if path.startswith("/bodhi/folders/refresh"):
            # 目录按需全量重建（2026-10-04）：有比目录更新的页 → 重建（幂等，一般是 no-op）
            params = dict(urlparse.parse_qsl(parsed.query))
            try:
                self._json(refresh_folders(params.get("kb_id", ""),
                                           params.get("force", "0") not in ("0", "false", "")),
                           200, self.CORS)
            except Exception as exc:  # noqa: BLE001
                print("[mcp] /bodhi/folders/refresh 失败：%s" % exc)
                self._json({"error": str(exc)}, 400, self.CORS)
            return
        if path in ("/bodhi/ontology/kb", "/bodhi/ontology/kb.json"):
            # 「当前打开的是不是本体模型库」——**只读判定，不靠 uuid 相等**（2026-09-22，
            # 用户报「上传本体文件按钮不出现」：旧实现是前端拿构建期常量比 uuid，客户环境必失效）。
            # 判定顺序：env BODHI_ONTOLOGY_KB_ID/ONTOLOGY_KB_ID → wiki_config.bodhi_ontology_kb=true
            # → 库名（默认「企业本体模型」）→ 内容探测（ontology:* 页数最多且 ≥ 阈值）。
            params = dict(urlparse.parse_qsl(parsed.query))
            try:
                self._json(ke_ontology.ontology_kb_report(params.get("kb_id", "")), 200, self.CORS)
            except Exception as exc:  # noqa: BLE001
                print("[mcp] /bodhi/ontology/kb 失败：%s" % exc)
                self._json({"error": str(exc)}, 400, self.CORS)
            return
        if path.startswith("/bodhi/ontology/targets"):
            params = dict(urlparse.parse_qsl(parsed.query))
            try:
                data = ke_ontology.target_pages(params.get("kb_id", ""), params.get("rel_type", ""),
                                                params.get("slug", ""), params.get("q", ""),
                                                int(params.get("limit", 200) or 200))
                self._json(data, 200, self.CORS)
            except Exception as exc:  # noqa: BLE001
                print("[mcp] /bodhi/ontology/targets 失败：%s" % exc)
                self._json({"error": str(exc)}, 400, self.CORS)
            return
        if path in ("/bodhi/context/page", "/bodhi/context/page.json"):
            # 领域页 ←同名 slug→ 概念页 的渲染视图（只读）：**领域库不写 uuid、不互相引用**
            params = dict(urlparse.parse_qsl(parsed.query))
            try:
                self._json(context_page(params.get("kb_id", ""), params.get("slug", ""),
                                        params.get("q", "")), 200, self.CORS)
            except Exception as exc:  # noqa: BLE001
                print("[mcp] /bodhi/context/page 失败：%s" % exc)
                self._json({"error": str(exc)}, 400, self.CORS)
            return
        if path in ("/bodhi/context/concept/preview", "/bodhi/context/concept/preview.json"):
            # 二期概念页生成的**只读预览**（dry-run）：GET 便于 curl；POST 版见写端点表
            params = dict(urlparse.parse_qsl(parsed.query))
            try:
                self._json(ke_context.concept_preview(
                    params.get("slug", ""),
                    params.get("include_unknown", "0") not in ("0", "false")), 200, self.CORS)
            except Exception as exc:  # noqa: BLE001
                print("[mcp] /bodhi/context/concept/preview 失败：%s" % exc)
                self._json({"error": str(exc)}, 400, self.CORS)
            return
        if path in ("/bodhi/import/probe", "/bodhi/import/probe.json"):
            params = dict(urlparse.parse_qsl(parsed.query))
            try:
                self._json(ke_sheet.probe(params.get("file", ""), params.get("sheet", ""),
                                          int(params.get("sample", 3) or 3)), 200, self.CORS)
            except Exception as exc:  # noqa: BLE001
                print("[mcp] /bodhi/import/probe 失败：%s" % exc)
                self._json({"error": str(exc)}, 400, self.CORS)
            return
        if path in ("/bodhi/import/state", "/bodhi/import/state.json"):
            params = dict(urlparse.parse_qsl(parsed.query))
            try:
                self._json(ke_import.state(params.get("batch", "")), 200, self.CORS)
            except Exception as exc:  # noqa: BLE001
                print("[mcp] /bodhi/import/state 失败：%s" % exc)
                self._json({"error": str(exc)}, 400, self.CORS)
            return
        if path in ("/bodhi/review/rules", "/bodhi/review/rules.json"):
            params = dict(urlparse.parse_qsl(parsed.query))
            try:
                # 2026-10-24（软退）：MCP 工具 `rules_of_policy` 已退役 ⇒ REST 路由**保留兼容但打标**，
                # 回执里给明确替代路径（前端/脚本若还在打它，能读到 deprecated + hint 而不是无声漂移 ✗）
                out = ke_review.rules_of_policy(params.get("kb_id", ""), params.get("policy", ""),
                                               int(params.get("limit", 300) or 300))
                if isinstance(out, dict):
                    out.setdefault("deprecated", True)
                    out.setdefault("hint", ("取规则统一走 MCP `knowledge_lookup(mode=\"rules\", "
                                            "spec=\"policy:<策略>\"|\"direct:<slug,…>\"|\"ask\")` ✓"
                                            "（本路由仅保留兼容）"))
                self._json(out, 200, self.CORS)
            except Exception as exc:  # noqa: BLE001
                print("[mcp] /bodhi/review/rules 失败：%s" % exc)
                self._json({"error": str(exc)}, 400, self.CORS)
            return
        if path in ("/bodhi/review/reference", "/bodhi/review/reference.json"):
            params = dict(urlparse.parse_qsl(parsed.query))
            try:
                self._json(ke_review.reference_lookup(params.get("kb_id", ""), params.get("reference", ""),
                                                      params.get("allow_fetch", "").lower() in ("1", "true", "yes"),
                                                      int(params.get("max_chars", 6000) or 6000)),
                           200, self.CORS)
            except Exception as exc:  # noqa: BLE001
                print("[mcp] /bodhi/review/reference 失败：%s" % exc)
                self._json({"error": str(exc)}, 400, self.CORS)
            return
        if path in ("/bodhi/review/graph", "/bodhi/review/graph.json"):
            params = dict(urlparse.parse_qsl(parsed.query))
            try:
                self._json(ke_review.graph_query(params.get("cypher", ""),
                                                 int(params.get("limit", 200) or 200)), 200, self.CORS)
            except Exception as exc:  # noqa: BLE001
                print("[mcp] /bodhi/review/graph 失败：%s" % exc)
                self._json({"error": str(exc)}, 400, self.CORS)
            return
        if path in ("/bodhi/context/authority", "/bodhi/context/authority.json"):
            # 权威/副本（只读）：给前端面板与运维看分工、副本漂移、pull 预览 ticket
            params = dict(urlparse.parse_qsl(parsed.query))
            try:
                self._json(ke_context.authority_preview(params.get("slug", ""), params.get("master", "")),
                           200, self.CORS)
            except Exception as exc:  # noqa: BLE001
                print("[mcp] /bodhi/context/authority 失败：%s" % exc)
                self._json({"error": str(exc)}, 400, self.CORS)
            return
        if path in ("/bodhi/context/cache", "/bodhi/context/cache.json"):
            # 映射**缓存**（技术产物；事实源是概念页正文）：给前端/运维看缓存与失效情况
            try:
                self._json(ke_context.read_cache(), 200, self.CORS)
            except Exception as exc:  # noqa: BLE001
                print("[mcp] /bodhi/context/cache 失败：%s" % exc)
                self._json({"error": str(exc)}, 400, self.CORS)
            return
        if path in ("/bodhi/contexts", "/bodhi/contexts.json"):
            # 跨库上下文映射（一期只读）：上下文注册表（**KB = 限界上下文**，见 docs/context-mapping-plan.md）
            try:
                self._json(ke_context.contexts(), 200, self.CORS)
            except Exception as exc:  # noqa: BLE001
                print("[mcp] /bodhi/contexts 失败：%s" % exc)
                self._json({"error": str(exc)}, 400, self.CORS)
            return
        if path in ("/bodhi/context/scan", "/bodhi/context/scan.json"):
            params = dict(urlparse.parse_qsl(parsed.query))
            try:
                kbs = [x.strip() for x in (params.get("kb_ids") or "").split(",") if x.strip()]
                self._json(context_scan(kbs or None, int(params.get("limit", 200) or 200),
                                        params.get("write", "1") not in ("0", "false")), 200, self.CORS)
            except Exception as exc:  # noqa: BLE001
                print("[mcp] /bodhi/context/scan 失败：%s" % exc)
                self._json({"error": str(exc)}, 400, self.CORS)
            return
        if path in ("/bodhi/context/lookup", "/bodhi/context/lookup.json"):
            params = dict(urlparse.parse_qsl(parsed.query))
            try:
                self._json(context_lookup(None, params.get("q", ""), params.get("slug", "")),
                           200, self.CORS)
            except Exception as exc:  # noqa: BLE001
                print("[mcp] /bodhi/context/lookup 失败：%s" % exc)
                self._json({"error": str(exc)}, 400, self.CORS)
            return
        if path in ("/bodhi/crud", "/bodhi/crud.json"):
            # 服务详细设计的 CRUD 矩阵（只读派生视图，与页面里的小节同源）：
            # 给前端/运维直接查「某服务读了写了哪些属性、谁是写耦合热点」。
            params = dict(urlparse.parse_qsl(parsed.query))
            try:
                kb, slug = params.get("kb_id", "") or "", params.get("slug", "") or ""
                data = crud_model(kb, slug) if slug else {"error": "需要 slug"}
                if isinstance(data, dict) and "error" not in data:
                    data["matrix_md"] = "\n".join(crud_matrix_lines(kb, slug))
                self._json(data, 200, self.CORS)
            except Exception as exc:  # noqa: BLE001
                print("[mcp] /bodhi/crud 失败：%s" % exc)
                self._json({"error": str(exc)}, 400, self.CORS)
            return
        if path in ("/bodhi/overview", "/bodhi/overview.md"):
            # 「IT 服务详细设计总览」只读渲染（核对用）：不写库、与页面同源。
            params = dict(urlparse.parse_qsl(parsed.query))
            try:
                kb = params.get("kb_id", "") or ""
                lines = service_overview_lines(kb)
                if params.get("format") == "json":
                    self._json(service_design_summary(kb), 200, self.CORS)
                else:
                    body = "\n".join(lines).encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type", "text/markdown; charset=utf-8")
                    self.send_header("Content-Length", str(len(body)))
                    for k, v in self.CORS.items():
                        self.send_header(k, v)
                    self.end_headers()
                    self.wfile.write(body)
            except Exception as exc:  # noqa: BLE001
                print("[mcp] /bodhi/overview 失败：%s" % exc)
                self._json({"error": str(exc)}, 400, self.CORS)
            return
        if path in ("/bodhi/relations", "/bodhi/relations.json"):
            # ⚠️ 前端 `BodhiRelationsPanel.vue` 用 **GET** `/bodhi/relations?kb_id=&slug=` 取「出边 + 入边」
            #    （写边才用 POST /bodhi/relations/{add,update,delete}）。2026-09-21 实测 GET 曾落到
            #    http.server 兜底 404（HTML）→ 关系面板空白（用户报"查不到引入的本体关系"）——
            #    排查时务必确认本分支在 `do_GET` 链里（而不是只在 `_bodhi_post` 的 handlers 里）。
            params = dict(urlparse.parse_qsl(parsed.query))
            try:
                kb = params.get("kb_id", "") or ""
                slug = params.get("slug", "") or ""
                # 2026-10-05 M2：本体关系面板**只读图实例**（pg 不再是主本、正文/出入链已无关系信息）。
                # 图不可用/无该实例 → 返回空结构（不再退 PG）。
                data = None
                try:
                    import ke_graph  # noqa: PLC0415
                    data = ke_graph.relations_of(kb, slug)
                except Exception:  # noqa: BLE001
                    data = None
                if data is None:
                    data = {"slug": slug, "title": "", "page_type": "", "type_label": "",
                            "out": [], "in": [], "source": "graph", "graph_unavailable": True}
                self._json(data, 200, self.CORS)
            except Exception as exc:  # noqa: BLE001
                print("[mcp] /bodhi/relations 失败：%s" % exc)
                self._json({"error": str(exc)}, 400, self.CORS)
            return
        if path in ("/bodhi/model-graph", "/bodhi/model-graph.json"):
            params = dict(urlparse.parse_qsl(parsed.query))
            try:
                self._json(bodhi_model_graph(params.get("model", ""), params.get("kb_id", "")),
                           200, self.CORS)
            except Exception as exc:  # noqa: BLE001
                print("[mcp] /bodhi/model-graph 失败：%s" % exc)
                self._json({"error": str(exc)}, 400, self.CORS)
            return
        if path == "/bodhi/ontology/deps":
            # 只读：上传前"会连带删除哪些下游模块"（给前端确认框 + 运维自查用）
            params = dict(urlparse.parse_qsl(parsed.query))
            model = params.get("model_id", "")
            try:
                victims = ke_admin.dependent_modules(model)
                self._json({"model_id": model, "dependent_modules": victims,
                            "purge_order": [*victims, model]}, 200, self.CORS)
            except Exception as exc:  # noqa: BLE001
                print("[mcp] %s 失败：%s" % (path, exc))
                self._json({"error": str(exc)}, 500, self.CORS)
            return
        if path in ("/bodhi/audit", "/bodhi/audit.json"):
            # 一致性巡检（只读）：?kb_id=&scope=all|wiki|model|source|dupes&max_findings=50
            params = dict(urlparse.parse_qsl(parsed.query))
            try:
                data = ke_audit.audit(params.get("kb_id", ""), params.get("scope", "all"),
                                      int(params.get("max_findings", 50) or 50),
                                      int(params.get("page_limit", 5000) or 5000),
                                      params.get("template_kb_id", ""))
                self._json(data, 200, self.CORS)
            except Exception as exc:  # noqa: BLE001
                print("[mcp] %s 失败：%s" % (path, exc))
                self._json({"error": str(exc)}, 400, self.CORS)
            return
        if path in ("/bodhi/docs", "/bodhi/docs.json"):
            # 按来源文档统计本体实例页（只读）；`?orphans=1` 只回「来源文档已删/不存在」的残留
            params = dict(urlparse.parse_qsl(parsed.query))
            kb_id = params.get("kb_id", "")
            try:
                data = ke_docs.orphans(kb_id) if params.get("orphans") else ke_docs.doc_index(kb_id)
                self._json(data, 200, self.CORS)
            except Exception as exc:  # noqa: BLE001
                print("[mcp] %s 失败：%s" % (path, exc))
                self._json({"error": str(exc)}, 400, self.CORS)
            return
        if path in ("/bodhi/docs/pages", "/bodhi/docs/pages.json"):
            # 某来源文档的实例页清单（只读）
            params = dict(urlparse.parse_qsl(parsed.query))
            try:
                pages = ke_docs.pages_of_doc(params.get("kb_id", ""), params.get("knowledge_id", ""))
                self._json({"kb_id": params.get("kb_id", ""),
                            "knowledge_id": params.get("knowledge_id", ""),
                            "pages": pages}, 200, self.CORS)
            except Exception as exc:  # noqa: BLE001
                print("[mcp] %s 失败：%s" % (path, exc))
                self._json({"error": str(exc)}, 400, self.CORS)
            return
        if path in ("/bodhi/graph", "/bodhi/graph.json"):
            params = dict(urlparse.parse_qsl(parsed.query))
            try:
                data = bodhi_graph(params.get("kb_id", ""), params.get("model", ""),
                                   params.get("types", ""), int(params.get("limit", 300) or 300))
                self._json(data, 200, {"Access-Control-Allow-Origin": "*"})
            except Exception as exc:  # noqa: BLE001
                print("[mcp] /bodhi/graph 失败：%s" % exc)
                self._json({"error": str(exc)}, 500, {"Access-Control-Allow-Origin": "*"})
            return
        if path in ("/bodhi/page", "/bodhi/page.json"):
            params = dict(urlparse.parse_qsl(parsed.query))
            try:
                data = bodhi_page(params.get("kb_id", ""), params.get("slug", ""))
                # 右栏要的是「渲染后的正文」而不是 md 源码（此前直接 <pre> 了原文）。
                # 渲染在服务端做：零依赖（mdview.py），前端与镜像都不用动。
                if isinstance(data, dict) and data.get("content"):
                    try:
                        data["content_html"] = render_md(data["content"])
                    except Exception as exc:  # noqa: BLE001
                        print("[mcp] markdown 渲染失败，退回纯文本：%s" % exc)
                        data["content_html"] = ""
                self._json(data, 200, {"Access-Control-Allow-Origin": "*"})
            except Exception as exc:  # noqa: BLE001
                self._json({"error": str(exc)}, 404, {"Access-Control-Allow-Origin": "*"})
            return
        if path in ("/graph", "/graph.html", "/bodhi/view"):
            params = dict(urlparse.parse_qsl(parsed.query))
            view = params.get("view", "")
            if not view:
                # 自动判定：**本体模型库**（只有这 5 种统一页类型）默认看「模型结构图」；
                # ⚠️ 不能只判 `ontology:%`：普通知识库里也有 `ontology:PendingMerge`（待确认合并）页。
                try:
                    rows = psql_csv(
                        "SELECT count(*) AS n FROM wiki_pages WHERE knowledge_base_id = %s "
                        "AND deleted_at IS NULL AND page_type IN "
                        "('ontology:Module','ontology:Class','ontology:Relation',"
                        "'ontology:Property','ontology:LightDoc')"
                        % sql_str(params.get("kb_id", "")))
                    view = "model" if rows and int(rows[0]["n"] or 0) > 0 else "browse"
                except Exception:  # noqa: BLE001
                    view = "browse"
            html = render_graph_page(params.get("kb_id", ""), params.get("model", ""), view)
            self._send(200, html.encode("utf-8"), "text/html; charset=utf-8")
            return
        # 上传页（不依赖前端 UI：选 zip → 直接 POST 字节 → 显示导入回执）
        if path in ("/bodhi/kb-upload", "/bodhi/kb-upload.html"):
            html = """<!DOCTYPE html><meta charset="utf-8"><title>知识库导入（上传 zip）</title>
<body style="font:14px/1.6 system-ui;max-width:720px;margin:32px auto">
<h3>知识库导入 · 上传导出包（zip）</h3>
<p>导出包来自 `kb_export`（或 <code>kb_transfer.py export --zip</code>）。填 <b>新库名</b>（跨环境迁移推荐）
或 <b>既有 kb_id</b>；<code>keep_ids</code> 仅跨实例迁移时勾（同实例会自动降级为重编 UUID）。</p>
<form id="f">
  <p><input type="file" id="file" accept=".zip" required></p>
  <p>新库名 <input name="new_kb" id="new_kb" size="32" placeholder="如：<目标库名>">
     或既有 kb_id <input name="kb_id" id="kb_id" size="36" placeholder="UUID"></p>
  <p><label><input type="checkbox" id="keep_ids" checked> keep_ids（跨实例迁移）</label></p>
  <p><button>上传并导入</button></p>
</form>
<pre id="out" style="background:#f6f8fa;padding:12px;white-space:pre-wrap"></pre>
<script>
f.onsubmit = async (e) => {
  e.preventDefault();
  const file = document.getElementById('file').files[0];
  if (!file) { document.getElementById('out').textContent = '请先选一个 .zip'; return; }
  const q = new URLSearchParams({name: file.name,
    new_kb: document.getElementById('new_kb').value.trim(),
    kb_id: document.getElementById('kb_id').value.trim(),
    keep_ids: document.getElementById('keep_ids').checked ? 1 : 0, verify: 1});
  document.getElementById('out').textContent = '上传中…（大包请耐心等）';
  const r = await fetch('/bodhi/kb-upload?' + q.toString(), {method: 'POST', body: file});
  let j; try { j = await r.json(); } catch (err) { j = {http: r.status, text: await r.text()}; }
  document.getElementById('out').textContent = JSON.stringify(j, null, 2);
};
</script></body>"""
            self._send(200, html.encode("utf-8"), "text/html; charset=utf-8",
                       {"Access-Control-Allow-Origin": "*"})
            return
        # 导出包下载（`kb_export` 的产物 → 直接拉 zip；只允许 exports/ 下的文件名，防目录穿越）
        if path in ("/bodhi/kb-export", "/bodhi/kb-export.zip"):
            params = dict(urlparse.parse_qsl(parsed.query))
            name = pathlib.Path(params.get("name", "")).name
            fp = EXPORT_DIR / name
            if not name or not fp.is_file():
                self._json({"error": "找不到导出包：%s（在 %s 下）" % (name, EXPORT_DIR)}, 404,
                           {"Access-Control-Allow-Origin": "*"})
                return
            self._send(200, fp.read_bytes(), "application/zip",
                       {"Content-Disposition": 'attachment; filename="%s"' % name,
                        "Access-Control-Allow-Origin": "*"})
            return
        # （原 `/bodhi/pending`、`/bodhi/resolve`（待确认合并的查/裁决）已随「相似度合并永久取消」删除，
        #  2026-10-10；相似知识治理改走巡检 `audit_scan` S1/S2。）
        if path in ("/bodhi/pending", "/bodhi/pending.json", "/bodhi/resolve", "/bodhi/resolve.json"):
            self._json({"error": "待确认合并已废弃（2026-10-10）：相似知识治理走 audit_scan（S1/S2）"},
                       410, {"Access-Control-Allow-Origin": "*"})
            return
        # 本实现不使用服务端主动推送（GET SSE），按规范返回 405 即可
        self._json({"jsonrpc": "2.0", "id": None,
                    "error": {"code": -32000, "message": "GET 未支持；请用 POST /mcp"}}, 405)

    def do_DELETE(self) -> None:  # noqa: N802
        self._send(200, b"", "application/json")


def main() -> int:
    # 启动自愈：把「本体库 / 企业共享概念模型库」的 wiki_config 标记补齐（**派生配置**，幂等，见 §14/A1）
    try:
        marks = ke_context.ensure_marks()
        print("[mcp] ensure-marks：%s" % json.dumps(marks.get("marks"), ensure_ascii=False))
    except Exception as exc:  # noqa: BLE001
        # **非致命**：ensure-marks 只是把 wiki_config 标记补齐（配了 env 就按 env 走，标记是兜底）。
        # 但若这里报「未设置数据库口令」，说明 DB 访问整体不可用 → 所有落库工具会一起失败，必须修。
        print("[mcp] ensure-marks 跳过（非致命，但若因「未设置数据库口令」则 DB 类工具会全部失败）：%s" % exc)
    parser = argparse.ArgumentParser(description="本体知识保存工具（MCP over Streamable HTTP）")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    server = ThreadingHTTPServer((args.host, args.port), MCPHandler)
    print("[mcp] %s %s 监听 http://%s:%d/mcp（工具：%s）"
          % (SERVER_NAME, SERVER_VERSION, args.host, args.port,
             ", ".join(t["name"] for t in tool_definitions())))
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("[mcp] 停止")
    return 0
