"""领域建模会话状态（P2 切片 S3 ✓ 从 `server.py` 抽出，**实现逐字未改**）

状态文件：`state/domain_sessions/<kb>/<knowledge>.json`（C5 巡检也读它 ✓，见 `ke_audit`）
本模块只做**读写 + 归零**，不含任何判定逻辑 ✓
"""
from __future__ import annotations

import json
import pathlib
import re

from srv.constants import DEFAULT_ROUND_BUDGET, DOMAIN_STATE_DIR
from srv.kbresolve import resolve_kb_id


def _state_path(kb_id: str, knowledge_id: str) -> pathlib.Path:
    safe = re.sub(r"[^0-9a-zA-Z._-]", "_", knowledge_id or "__kb__")
    return DOMAIN_STATE_DIR / kb_id / ("%s.json" % safe)


def _empty_state(kb_id: str, knowledge_id: str) -> dict:
    return {"kb_id": kb_id, "knowledge_id": knowledge_id, "doc_title": "",
            "budget_tokens": DEFAULT_ROUND_BUDGET, "cursor": 0, "next_no": 1,
            "rounds": [], "pages": [], "pending_links": []}


def load_domain_state(kb_id: str, knowledge_id: str = "") -> dict:
    path = _state_path(kb_id, knowledge_id)
    if not path.is_file():
        return _empty_state(kb_id, knowledge_id)
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return _empty_state(kb_id, knowledge_id)
    base = _empty_state(kb_id, knowledge_id)
    base.update(state)
    return base


def save_domain_state(kb_id: str, knowledge_id: str, state: dict) -> pathlib.Path:
    path = _state_path(kb_id, knowledge_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")
    return path


def extract_state(kb_id: str, knowledge_id: str = "", action: str = "get") -> dict:
    """会话页索引与进度：每轮落库的**页面名称 + 编号**、已做轮次、待确认候选关联。"""
    kb_id, _note = resolve_kb_id(kb_id)
    if action == "reset":
        state = _empty_state(kb_id, knowledge_id)
        save_domain_state(kb_id, knowledge_id, state)
        return {"action": "reset", "kb_id": kb_id, "knowledge_id": knowledge_id, "state": state}
    if action != "get":
        raise RuntimeError("action 只能是 get 或 reset")
    state = load_domain_state(kb_id, knowledge_id)
    return {
        "kb_id": kb_id, "knowledge_id": knowledge_id, "doc_title": state.get("doc_title", ""),
        "budget_tokens": state.get("budget_tokens"), "cursor": state.get("cursor"),
        "rounds": state.get("rounds", []), "pages_total": len(state.get("pages", [])),
        "pages": state.get("pages", []), "next_no": state.get("next_no", 1),
        "pending_links": [x for x in state.get("pending_links", []) if x.get("status") == "pending"],
        "hint": "页面的 `no` 是本会话编号；引用既有页面用 slug/title，不要重建同名页。",
    }


def _iter_states(kb_id: str, knowledge_id: str = "") -> list[tuple[str, dict]]:
    if knowledge_id:
        return [(knowledge_id, load_domain_state(kb_id, knowledge_id))]
    base = DOMAIN_STATE_DIR / kb_id
    out = []
    if base.is_dir():
        for path in sorted(base.glob("*.json")):
            out.append((path.stem, load_domain_state(kb_id, path.stem)))
    return out
