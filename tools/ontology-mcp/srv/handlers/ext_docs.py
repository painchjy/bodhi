"""外部文档三件套：**探测附件 → 推送并绑档案 → 解析正文**（2026-10-28 用户口径）

分工
----
- it_platform（8770，04 包）= **纯外部服务**：只存文件、只给文件、只存流程与台账（零 PG/零 docker）；
- bodhi-mcp（本服务）= 能读会话附件（docker/psql）、能解析正文（PDF 借 docreader 的 stdin）：
    · `ext_attach_probe` 只读探测附件在哪个目录 / 最新一份是谁（内网普查 + 排障）
    · `ext_doc_push`     读附件 → `POST /files/<doc_id>`（it_platform 侧自动 attach 到流程档案）
    · `ext_doc_parse`    `GET /files/<doc_id>` → 本地解析 → outline / section / image（逐字）

> 三个工具都**不碰共享磁盘**：文件传输走 HTTP，解析走 stdin；04 那边既没有 PG 也没有 docker。
"""
from __future__ import annotations

from srv.handlers import handle


@handle("ext_attach_probe")
def ext_attach_probe(args: dict) -> dict:
    import ke_ext_docs_client as C  # noqa: PLC0415

    return C.probe(session_no=str(args.get("session_no", "")),
                   doc_title=str(args.get("doc_title", "")),
                   limit=int(args.get("limit") or 10))


@handle("ext_doc_push")
def ext_doc_push(args: dict) -> dict:
    import ke_ext_docs_client as C  # noqa: PLC0415

    return C.push(review_no=str(args.get("review_no", "")),
                  doc_title=str(args.get("doc_title", "")),
                  session_no=str(args.get("session_no", "")),
                  doc_id=str(args.get("doc_id", "")),
                  actor=str(args.get("actor", "")),
                  title=str(args.get("title", "")))


@handle("ext_doc_parse")
def ext_doc_parse(args: dict) -> dict:
    import ke_doc_parse as P  # noqa: PLC0415

    return P.parse(str(args.get("doc_id", "")),
                   action=str(args.get("action") or "outline"),
                   section_path=str(args.get("section_path", "")),
                   cursor=int(args.get("cursor") or 0),
                   limit_chars=int(args.get("limit_chars") or 6000),
                   inline_media=bool(args.get("inline_media", True)),
                   doc_title=str(args.get("doc_title", "")))
