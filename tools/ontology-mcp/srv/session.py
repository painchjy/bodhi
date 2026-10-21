"""会话身份解析（P2 切片 S5 ✓ 从 `server.py` 抽出，**实现逐字未改**）

用户口径（2026-10-04）：`agentName` **只认会话**（`sessions.agent_config->>'agent_id'` →
`custom_agents.name`），**不信任客户端自报、无 env 兜底**；取不到 → 拒绝写库（fail-closed ✓）。

线程局部 `_IDENT` 由调用方（`save_knowledge`）写入，读方用 `_session_ident()`。
"""
from __future__ import annotations

import os
import sys
import threading

_CORE = os.path.abspath(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                     "..", "ke-core"))
if _CORE not in sys.path:
    sys.path.insert(0, _CORE)

import ke_db  # noqa: E402

sql_str = ke_db.sql_str


class _IdentCtx(threading.local):
    """本次调用的**会话身份**（由 `save_knowledge` 解析后放进来，供各节点注入用）。

    2026-10-04 用户口径：`agentName` **只认会话**（`sessions.agent_config->>'agent_id'` →
    `custom_agents.name`），**不信任客户端自报、无 env 兜底**；取不到 → 拒绝写库（fail-closed）。
    """
    ident: dict | None = None


_IDENT = _IdentCtx()


def _session_ident() -> dict:
    return _IDENT.ident or {}


def _attach_session(summary: dict, ident: dict, session_warn: str) -> dict:
    """把**会话身份**写进回执（`stage=report` 与 `stage=graph` **两条 return 路径共用**）。"""
    if ident.get("ok"):
        summary["session_identity"] = {"session_no": ident["session_no"], "agent_id": ident["agent_id"],
                                       "agent_name": ident["agent_name"], "session_title": ident["title"]}
    elif session_warn:
        summary["need_session"] = session_warn
    return summary


def _resolve_session_identity(session_no: str) -> dict:
    """会话编号（WeKnora `sessions.id`）→ **权威身份**：agentName / tenant / kb 范围。

    - 会话编号必须**真实存在于 `sessions` 表**（否则视为编造 → 拒）；
    - 智能体名取自 `sessions.agent_config.agent_id` → `custom_agents.name`（**会话选择的智能体**）；
    - 任何一步取不到 → `ok=False` → 调用方**拒绝写库**。
    """
    sid = (session_no or "").strip()
    if not sid:
        return {"ok": False, "reason": "缺少会话编号（请把 WeKnora 会话编号作为 session_no / 会话页的 bmm:sessionNo 传入）"}
    try:
        rows = ke_db.psql_csv(
            "SELECT id, tenant_id, COALESCE(agent_config->>'agent_id','') AS agent_id, "
            "COALESCE(title,'') AS title FROM sessions "
            "WHERE id = %s AND deleted_at IS NULL LIMIT 1" % sql_str(sid))
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "reason": "查会话失败：%s" % exc}
    if not rows:
        return {"ok": False, "reason": "会话编号在 WeKnora 里不存在：%s" % sid}
    agent_id = (rows[0].get("agent_id") or "").strip()
    if not agent_id:
        return {"ok": False, "reason": "该会话未绑定智能体（sessions.agent_config.agent_id 为空）"}
    try:
        ar = ke_db.psql_csv("SELECT name FROM custom_agents WHERE id = %s LIMIT 1" % sql_str(agent_id))
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "reason": "查智能体名称失败：%s" % exc}
    agent_name = ((ar[0].get("name") if ar else "") or "").strip()
    if not agent_name:
        return {"ok": False, "reason": "会话绑定的智能体 %s 在 custom_agents 里查不到名称" % agent_id}
    return {"ok": True, "session_no": sid, "agent_id": agent_id, "agent_name": agent_name,
            "tenant_id": rows[0].get("tenant_id"), "title": rows[0].get("title") or ""}
