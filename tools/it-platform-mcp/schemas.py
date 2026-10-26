"""`it-platform-mcp` 的工具声明（**服务内工具面**）。

WeKnora 侧看到的工具名 = `mcp_<mcp_services.name>_<name>`；本服务 name = `it_platform`，
所以智能体看到的是 `mcp_it_platform_ext_catalog_query` 等。领域库里的 DataObject 绑定
写的是**服务内名**（`ext_*`），两者一一对应。
"""
import hashlib
import json
import os

TOOLS = [
    {
        "name": "ext_catalog_query",
        "description": ("**内网工作台接口（参考实现）· 白名单 / 黑名单查询**：按 `name/vendor/license` "
                        "查白名单；按 `version` 查黑名单（**逐字比对含补丁位**，**黑名单优先**）。"
                        "`mode=query` 只读（`action=check|ls|denied`）；`mode=maintain` 写"
                        "（`action=upsert|disable`，默认 `dry_run=true`，落盘才写审计 + `rev+1`）。"
                        "绑定来自业务对象页的 4 个数据属性（`bpmn:dataObjectInterface` / "
                        "`dataObjectAccessMode` / `dataObjectKeyFields` / `dataObjectInterfaceRef`）。"),
        "inputSchema": {"type": "object", "properties": {
            "kb_id": {"type": "string",
                       "description": "业务对象所在库（**可省**：省略时按 object/接口名全库反查）"},
            "object": {"type": "string", "description": "业务对象页 slug（省略 ⇒ 按接口名反查）"},
            "mode": {"type": "string", "description": "query | maintain（默认 query）"},
            "action": {"type": "string",
                       "description": "check（判定）| ls（白名单）| denied（黑名单）| upsert | disable"},
            "name": {"type": "string"},
            "version": {"type": "string", "description": "**逐字**，含补丁位"},
            "vendor": {"type": "string"}, "license": {"type": "string"},
            "status": {"type": "string"},
            "limit": {"type": "integer"},
            "dry_run": {"type": "boolean", "description": "写模式默认 true（只回 diff）"},
            "actor": {"type": "string"}, "session_no": {"type": "string"}},
            "required": []},
    },
    {
        "name": "ext_issue_list",
        "description": ("**内网工作台接口（参考实现）· 条目清单（查询 + 维护）**：`mode=query` 读"
                        "（`action=list|get`；`view=table` 回 Markdown 表）；`mode=maintain` 写"
                        "（`create|update|close|delete`，默认 `dry_run=true`）。"
                        "幂等键 = `(review_no, issue_no)`；`update/close/delete` 支持 `if_rev`，"
                        "不匹配回 `conflict`。字段含 `expert_feedback`（外部专家反馈栏位）。"),
        "inputSchema": {"type": "object", "properties": {
            "kb_id": {"type": "string",
                       "description": "业务对象所在库（**可省**：绑定不在本库时自动跨库解析）"},
            "object": {"type": "string", "description": "业务对象页 slug（建议给，省一次反查）"},
            "mode": {"type": "string", "description": "query | maintain（默认 query）"},
            "action": {"type": "string",
                       "description": "list | get | create | update | close | delete"},
            "review_no": {"type": "string", "description": "外层主键"},
            "issue_no": {"type": "string", "description": "内层主键"},
            "fields": {"type": "object",
                       "description": ("desc/rule/doc_loc/quote/verdict/severity/fix/"
                                       "confidence/rule_fp/expert_feedback；"
                                       "**rule_fp 请显式给**（= 规则页 bmm:ruleHash），给了就不覆盖")},
            "items": {"type": "array", "items": {"type": "object"},
                      "description": ("**批量 create**：一次提交全部条目（每条含 issue_no + 上面 fields），"
                                      "整批只让 `rev` +1（避免\"一条一个版本\"）；"
                                      "回执给 created/updated/rejected")},
            "view": {"type": "string", "description": "rows | table"},
            "columns": {"type": "string",
                        "description": ("**报告要用哪几列**（逗号分隔，顺序即展示顺序；省略=全部）。"
                                        "工具字段可多于某份报告所需（服务端字段集 ⊋ 报告字段集）"
                                        "⇒ 子集与顺序由**模板**配置")},
            "if_rev": {"type": "integer"},
            "dry_run": {"type": "boolean"}, "actor": {"type": "string"},
            "session_no": {"type": "string"}},
            "required": ["review_no"]},
    },
    {
        "name": "ext_review_flow",
        "description": ("**内网工作台接口（参考实现）· 流程上下文**（流程编号/阶段/**状态**/**轮次**/"
                        "责任人/文档清单）。智能体只提交「**流程编号 + 文件名称**」即可开工，"
                        "**不需要上传文档** ✓：`action=get` 取状态与轮次；`action=docs` 取文档清单"
                        "（含 `doc_id` 与**可下载 url**）；`action=rounds` 取轮次表；`action=open` "
                        "**建档**（编号不存在也按\"平台已存在\"建档）并按文件名绑定文档；"
                        "`mode=maintain` 支持 `upsert_round|close`（默认 `dry_run=true`）。"
                        "轮次以回执为准（调用方要求更小 ⇒ `warn=round_behind`，不静默）。"),
        "inputSchema": {"type": "object", "properties": {
            "kb_id": {"type": "string", "description": "业务对象所在库（可省：自动跨库解析绑定）"},
            "object": {"type": "string", "description": "业务对象页 slug（建议给）"},
            "mode": {"type": "string", "description": "query | maintain（默认 query）"},
            "action": {"type": "string",
                       "description": ("get | docs | rounds | open | create | attach | "
                                       "upsert_round | close")},
            "review_no": {"type": "string", "description": "流程编号（外层主键）"},
            "title": {"type": "string", "description": "create 时的流程名称（可省）"},
            "doc_id": {"type": "string",
                       "description": "attach 时绑定已上传的文档（先 `POST /files/<doc_id>`）"},
            "round": {"type": "integer", "description": "轮次（open/maintain 用；省略=档案当前轮次）"},
            "filenames": {"type": "array", "items": {"type": "string"},
                          "description": "open 时按**文件名**绑定文档（模糊匹配标题）"},
            "status": {"type": "string", "description": "close 时的目标状态（默认「已完成」）"},
            "dry_run": {"type": "boolean", "description": "写模式默认 true（只回 diff）"},
            "actor": {"type": "string"}, "session_no": {"type": "string"}},
            "required": ["review_no"]},
    },
    {
        "name": "ext_doc_access",
        "description": ("**内网工作台接口（参考实现）· 文档与附件只读访问**：`action=list|meta|url` 取清单/元数据/"
                        "**可下载 url**；`action=outline` 取章节树、`action=section` 按 `section_path`/`cursor` 取"
                        "**逐字正文**（`next_cursor` 翻页）；`attachment` 列附件（带 `url`）；`image` 取图，"
                        "`inline_media=true` 直接回 `data_url`（多模态可用；限额 `max_bytes`，"
                        "失败给 `too_large/not_found/unsupported_type` 结构化原因）。"),
        "inputSchema": {"type": "object", "properties": {
            "kb_id": {"type": "string", "description": "业务对象所在库（可省：自动跨库解析绑定）"},
            "object": {"type": "string", "description": "业务对象页 slug（建议给）"},
            "action": {"type": "string",
                       "description": ("list | meta | url | outline | section | attachment | image | "
                                       "**import**（把知识库里已上传的附件导入并绑定编号，"
                                       "不需要 shell ✓）")},
            "review_no": {"type": "string", "description": "外层主键（import 必填：编号）"},
            "doc_id": {"type": "string"},
            "doc_title": {"type": "string",
                          "description": ("import 用：**用户给的文件名**（含扩展名，按 title/file_name 模糊匹配；"
                                          "同名多份 ⇒ `doc_ambiguous` + candidates，不猜）")},
            "knowledge_id": {"type": "string",
                             "description": "import 用：直接指定知识库文档 id（比 doc_title 更确定）"},
            "source": {"type": "string",
                       "description": ("import 用：`auto`(默认：先知识库，找不到再用**会话附件**) | "
                                       "`chat`(**直接用用户在本会话上传的附件**，不必先入知识库) | "
                                       "`kb`(只从知识库找)")},
            "session_no": {"type": "string",
                           "description": "import(source=chat) 用：会话编号（省略=全库最近一条带附件的消息）"},
            "round": {"type": "integer", "description": "import 用：轮次（默认 1）"},
            "actor": {"type": "string"},
            "section_path": {"type": "string"},
            "cursor": {"type": "integer"},
            "limit_chars": {"type": "integer"},
            "inline_media": {"type": "boolean"},
            "max_bytes": {"type": "integer"}},
            "required": []},
    },
]


# ---------------------------------------------------------------------------
# `x-ext`：把**数据面契约**（列/主键/模式/枚举）随 `tools/list` 一并下发
# ---------------------------------------------------------------------------
# 为什么要有它（2026-10-25 用户口径）
#   列名/列序/枚举/主键如果各客户端各写一份（页面 note、技能、前端面板…），必然漂移。
#   真源只留**一处**：容器数据 `schema.json` 的 `x_ext` 段；客户端订阅 `tools/list`
#   就拿全，页面只做**投射**（带 hash + fetched_at），不再手写列规范 ✓。
#
# 读哪里：**优先读服务发布内的 seed 文件**（`deploy/mock-ext/<system>/schema.json`），
# 因为容器根里的副本是"首次使用时的初始化数据"、`bootstrap()` **只补缺失不覆盖**，
# 可能落后于本次发布；契约跟着服务版本走才对 ✓。
def _contract() -> dict:
    """容器数据契约（含 `x_ext`）：seed 优先 → 容器根兜底。"""
    try:
        import ke_ext_store as S
    except Exception:                     # noqa: BLE001  独立导入（如单测）时按无契约处理
        return {}
    for path in (os.path.join(S.SEED_DIR, S.DEFAULT_SYSTEM, "schema.json"),
                 os.path.join(S.system_dir(), "schema.json")):
        doc = S.read_json(path, None)
        if isinstance(doc, dict) and doc.get("x_ext"):
            return doc
    return {}


def _columns(doc: dict, spec: dict) -> list:
    """列定义：`enumRef` 就地解析成 `enum`（枚举只在 schema.json 里定义一次 ✓）。"""
    out = []
    for col in spec.get("columns") or []:
        item = dict(col)
        ref = item.pop("enumRef", "")
        if ref and isinstance(doc.get(ref), list):
            item["enum"] = list(doc[ref])
        out.append(item)
    return out


def x_ext(tool: str) -> dict:
    """某工具的 `x-ext` 块（含 `schemaHash`）；没有契约 ⇒ 空 dict。"""
    doc = _contract()
    spec = (doc.get("x_ext") or {}).get(tool) or {}
    if not isinstance(spec, dict) or not spec:
        return {}
    body = {k: spec[k] for k in ("objectKind", "keyFields", "modes", "revScope", "views")
            if k in spec}
    body["columns"] = _columns(doc, spec)
    canon = json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    body["schemaHash"] = "sha256:" + hashlib.sha256(canon.encode("utf-8")).hexdigest()[:16]
    return body


def tools_with_ext() -> list:
    """`tools/list` 用的工具面（每个工具附 `x-ext`；无契约的工具原样返回）。"""
    out = []
    for tool in TOOLS:
        item = dict(tool)
        ext = x_ext(tool["name"])
        if ext:
            item["x-ext"] = ext
        out.append(item)
    return out
