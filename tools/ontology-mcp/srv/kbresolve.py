"""知识库/文档解析（P2 切片 S2 ✓ 从 `server.py` 抽出，**实现逐字未改**）

为什么单独一层：读路径宽松、写路径严格（模糊必须二次确认 ✓），两条口径**必须**是同一处实现，
否则会出现"回执报成功、用户在别的库里看不到" ✗ —— 详见 `resolve_write_kb` 文档表。
"""
from __future__ import annotations

import os
import sys

_CORE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "..", "ke-core")
_CORE = os.path.abspath(_CORE)
if _CORE not in sys.path:
    sys.path.insert(0, _CORE)

import ke_db  # noqa: E402

psql = ke_db.psql
psql_csv = ke_db.psql_csv
sql_str = ke_db.sql_str


def resolve_kb_id(raw: str) -> tuple[str, str]:
    """把 kb_id 参数解析成真实 UUID：支持 UUID / UUID 前缀 / 知识库名称（精确或包含）。

    统一委托给 `ke_db.resolve_kb_id`（2026-09-21）：那里的报错**带 id**（形如
    `企业知识（dbc2528f…）`），调用方（智能体）能照着纠正 —— 实测它会自己编 `b1` 这种串。
    """
    kb_id, _name, note = ke_db.resolve_kb_id(raw)
    return kb_id, note


def kb_choices() -> list[dict]:
    """可选知识库清单（给「需要选择 / 需要确认」的回执用）。"""
    return [{"id": r["id"], "name": r["name"], "pages": None} for r in psql_csv(
        "SELECT id, name FROM knowledge_bases WHERE deleted_at IS NULL ORDER BY updated_at DESC")]


def resolve_write_kb(kb_id: str = "", kb_ids: list | None = None,
                     confirm: bool = False) -> tuple[str, str, dict | None]:
    """**写库**的目标库解析：唯一确定 + 模糊需二次确认（2026-09-22 用户口径）。

    为什么不能沿用读路径的宽松解析：会话可能绑定**多个**知识库（`<bound_knowledge_bases>`），
    智能体若随手传个含糊名称，宽松解析会静默命中"另一个库" → 回执成功但用户在自己的库里看不到。
    因此写路径：

    | 情形 | 行为 |
    |---|---|
    | `kb_id` 精确（完整 uuid / **精确**库名） | 直接写 |
    | `kb_id` 模糊（uuid 前缀 / 名称包含）且唯一命中 | **必须二次确认**（`confirm_kb_match=true`） |
    | `kb_id` 未给 + 会话只绑 1 个库（`kb_ids`） | 用那个库 |
    | `kb_id` 未给 + 会话绑了多个库 | **拒绝写** → `need_kb_selection`（请用户指明） |
    | 命中 0 个 / 多个 / 占位符 | 拒绝写 → `kb_unresolved` + 可选清单 |

    返回 `(uuid, note, block)`；`block` 非 None 时**不要写库**，直接把它作为工具回执返回。
    """
    ids = [str(x).strip() for x in (kb_ids or []) if str(x).strip()]
    raw = (kb_id or "").strip()
    if not raw:
        if len(ids) == 1:
            raw = ids[0]
        elif len(ids) > 1:
            return "", "", {
                "kb_unresolved": "multi_kb_session", "need_kb_selection": True,
                "bound_kb_ids": ids, "candidates": kb_choices(),
                "how": ("本次会话绑定了 %d 个知识库：**写操作必须明确唯一的目标库**。"
                        "请让用户指明写入哪一个，然后带 `kb_id=<该库 uuid 或精确名>` 重跑；"
                        "只读操作（doc_outline / extract_state / audit_scan …）不受限制。" % len(ids))}
        else:
            return "", "", {
                "kb_unresolved": "no_kb", "need_kb_selection": True,
                "candidates": kb_choices(),
                "how": ("没给 `kb_id` 也没提供会话绑定的库清单（`kb_ids`）："
                        "请把 `<bound_knowledge_bases>` 里的 id 传进 `kb_ids`（多库时会要求用户指明），"
                        "或直接传 `kb_id`")}
    try:
        uid, name, mode = ke_db.resolve_kb_candidate(raw)
    except ValueError as exc:
        return "", "", {"kb_unresolved": "not_found", "need_kb_selection": True,
                        "asked": raw, "candidates": kb_choices(), "how": str(exc)}
    if mode == "fuzzy" and not confirm:
        return "", "", {
            "kb_unresolved": "fuzzy_match", "need_kb_confirm": True,
            "matched": {"kb_id": uid, "name": name,
                        "how": "按 uuid 前缀 / 名称包含匹配到唯一库"},
            "how": ("模糊匹配即使只命中 1 个也必须确认：请改传**完整 uuid 或精确库名**，"
                    "或带 `confirm_kb_match=true` 重跑（表示「就是它」）")}
    note = "" if mode == "exact" else ("（kb_id「%s」按模糊匹配解析为 %s，已二次确认）" % (raw, uid))
    return uid, note, None


def resolve_knowledge_id(kb_id: str, raw: str) -> tuple[str, str]:
    """把 knowledge_id 解析成真实 UUID：支持 UUID / id 前缀 / 标题包含；
    占位符或无法解析时退回该知识库**最新一篇**文档（并在返回里说明）。"""
    rows = psql_csv("SELECT id, title FROM knowledges WHERE knowledge_base_id = %s AND deleted_at IS NULL "
                    "ORDER BY created_at DESC" % sql_str(kb_id))
    if not rows:
        raise RuntimeError("该知识库还没有文档：请先上传文档并等切片完成")
    raw = (raw or "").strip()
    if raw:
        for r in rows:
            if r["id"] == raw:
                return r["id"], ""
        for r in rows:
            if r["id"].startswith(raw):
                return r["id"], "（knowledge_id「%s」按 id 前缀匹配）" % raw
            if raw.lower() in (r["title"] or "").lower():
                return r["id"], "（knowledge_id「%s」按标题匹配：%s）" % (raw, r["title"])
    return rows[0]["id"], ("（knowledge_id「%s」无法解析，已改用该库最新文档：%s）"
                           % (raw or "(空)", rows[0]["title"]))


def get_kb_tenant(kb_id: str) -> int:
    row = psql("SELECT tenant_id FROM knowledge_bases WHERE id = %s AND deleted_at IS NULL"
               % sql_str(kb_id))
    if not row.strip():
        raise RuntimeError("知识库不存在：%s" % kb_id)
    return int(row.strip().splitlines()[0])
