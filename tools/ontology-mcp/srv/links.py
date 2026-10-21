"""候选关联登记 / 列清单 / 裁决（P2 切片 S6 ✓ 从 `server.py` 抽出，**实现逐字未改**）

语义（用户口径）：跨上下文/跨库的关联**先登记、后确认**；
`confirm` 才真写关系边（`ke_pages.add_relation`），`reject` 只标记丢弃 ✓
"""
from __future__ import annotations

import hashlib
import os
import sys

_CORE = os.path.abspath(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                     "..", "ke-core"))
if _CORE not in sys.path:
    sys.path.insert(0, _CORE)

import ke_db  # noqa: E402
import ke_pages  # noqa: E402
from srv.kbresolve import resolve_kb_id, resolve_write_kb  # noqa: E402
from srv.state import (  # noqa: E402
    _iter_states, _state_path, load_domain_state, save_domain_state,
)

psql_csv = ke_db.psql_csv
sql_str = ke_db.sql_str
now_text = ke_db.now_text


def _page_row_by_slug(kb_id: str, slug: str) -> dict | None:
    rows = psql_csv("SELECT slug, title, page_type FROM wiki_pages WHERE knowledge_base_id = %s "
                    "AND slug = %s AND deleted_at IS NULL LIMIT 1"
                    % (sql_str(kb_id), sql_str(slug)))
    return rows[0] if rows else None


def link_candidates(kb_id: str, source_slug: str, relation: str, candidates: list,
                    knowledge_id: str = "", round_no: int = 0,
                    kb_ids: list | None = None, confirm_kb_match: bool = False) -> dict:
    """登记**候选关联**（只登记，不写库）。用户确认后用 `resolve_link_candidate` 落地。"""
    kb_id, _note, block = resolve_write_kb(kb_id, kb_ids, confirm_kb_match)
    if block:
        return block
    src = _page_row_by_slug(kb_id, source_slug)
    if not src:
        raise RuntimeError("源页不存在：%s（先用 slug 或标题确认它已落库）" % source_slug)
    if not relation:
        raise RuntimeError("relation 必填（本体里的关系 prefixed 名，如 bmm:definedBy）")
    if not isinstance(candidates, list):
        raise RuntimeError("candidates 必须是数组：[{target_slug, similarity?, reason?}]")
    state = load_domain_state(kb_id, knowledge_id)
    added, skipped, reopened = [], [], []
    for item in candidates:
        if isinstance(item, str):
            item = {"target_slug": item}
        target = str(item.get("target_slug") or item.get("slug") or "").strip()
        if not target:
            continue
        tgt = _page_row_by_slug(kb_id, target)
        cid = hashlib.sha1(("%s|%s|%s|%s" % (source_slug, relation, target, knowledge_id))
                           .encode("utf-8")).hexdigest()[:12]
        existing = next((x for x in state.get("pending_links", []) if x.get("candidate_id") == cid),
                        None)
        if existing is not None and existing.get("status") == "pending":
            skipped.append({"candidate_id": cid, "target_slug": target, "why": "已在待确认队列"})
            continue
        if existing is not None:
            # 之前驳回过（或已确认过）：**复活为待确认**，否则同一对永远无法再提交确认
            # （candidate_id 是按 source|relation|target|doc 稳定哈希，不是每次新生成）
            existing.update({"status": "pending", "reopened_at": now_text(),
                             "similarity": item.get("similarity"),
                             "reason": str(item.get("reason") or existing.get("reason") or "")[:300],
                             "round_no": int(round_no or 0),
                             "target_exists": bool(tgt),
                             "target_title": (tgt or {}).get("title", ""),
                             "target_type": (tgt or {}).get("page_type", "")})
            existing.pop("resolved_at", None)
            reopened.append({"candidate_id": cid, "target_slug": target,
                             "prev_status": "rejected_or_confirmed"})
            added.append(existing)
            continue
        record = {"candidate_id": cid, "source_slug": source_slug, "source_title": src["title"],
                  "relation": relation, "target_slug": target,
                  "target_title": (tgt or {}).get("title", ""),
                  "target_exists": bool(tgt), "target_type": (tgt or {}).get("page_type", ""),
                  "similarity": item.get("similarity"), "reason": str(item.get("reason") or "")[:300],
                  "round_no": int(round_no or 0), "status": "pending", "created_at": now_text()}
        state.setdefault("pending_links", []).append(record)
        added.append(record)
    save_domain_state(kb_id, knowledge_id, state)
    return {"kb_id": kb_id, "knowledge_id": knowledge_id, "added": len(added), "skipped": skipped,
            "reopened": reopened,
            "items": added, "state_file": str(_state_path(kb_id, knowledge_id)),
            "note": ("这些**还没有写进领域模型**。把清单交给用户逐条确认后，再调 "
                     "`resolve_link_candidate(action=\"confirm\")`；`target_exists=false` 的先别确认。")}


def list_link_candidates(kb_id: str, knowledge_id: str = "", status: str = "pending") -> dict:
    """列出候选关联（默认只看待确认的）。

    按 `candidate_id` **去重**（保留同 id 的**最后一条**）：早期版本在"驳回后重新登记"时会
    append 出重复记录，这里做兼容收敛，避免同一对出现两条。
    """
    kb_id, _note = resolve_kb_id(kb_id)
    latest: dict[str, dict] = {}
    for kid, state in _iter_states(kb_id, knowledge_id):
        for rec in state.get("pending_links", []):
            cid = rec.get("candidate_id") or ""
            if not cid:
                continue
            latest[cid] = {**rec, "knowledge_id": kid}
    items = [r for r in latest.values()
             if not status or status == "all" or r.get("status") == status]
    return {"kb_id": kb_id, "status": status, "count": len(items), "items": items}


def resolve_link_candidate(kb_id: str, candidate_id: str, action: str, knowledge_id: str = "",
                           kb_ids: list | None = None, confirm_kb_match: bool = False) -> dict:
    """裁决候选关联：`confirm` = 写进领域模型（真关系边）；`reject` = 丢弃。"""
    kb_id, _note, block = resolve_write_kb(kb_id, kb_ids, confirm_kb_match)
    if block:
        return block
    action = (action or "").strip().lower()
    if action not in ("confirm", "reject"):
        raise RuntimeError("action 只能是 confirm 或 reject")
    for kid, state in _iter_states(kb_id, knowledge_id):
        matches = [rec for rec in state.get("pending_links", [])
                   if rec.get("candidate_id") == candidate_id]
        if not matches:
            continue
        # 同 id 可能有多条（历史遗留）：**优先取待确认的**，否则取最后一条
        rec = next((x for x in matches if x.get("status") == "pending"), matches[-1])
        if rec.get("status") != "pending":
            return {"candidate_id": candidate_id, "status": rec.get("status"),
                    "note": "该候选已裁决过，未重复执行"}
        if action == "reject":
            rec.update({"status": "rejected", "resolved_at": now_text()})
            save_domain_state(kb_id, kid, state)
            return {"candidate_id": candidate_id, "status": "rejected",
                    "source_slug": rec.get("source_slug"), "target_slug": rec.get("target_slug")}
        if not rec.get("target_exists"):
            raise RuntimeError("目标页不存在，不能确认：%s" % rec.get("target_slug"))
        res = ke_pages.add_relation(kb_id, rec["source_slug"], rec["relation"], rec["target_slug"])
        version = ((res or {}).get("relations") or {}).get("version") or (res or {}).get("version")
        rec.update({"status": "confirmed", "resolved_at": now_text(), "page_version": version})
        save_domain_state(kb_id, kid, state)
        return {"candidate_id": candidate_id, "status": "confirmed",
                "source_slug": rec.get("source_slug"), "relation": rec.get("relation"),
                "target_slug": rec.get("target_slug"), "page_version": version,
                "apply": res}
    raise RuntimeError("找不到候选：%s（先用 list_link_candidates 看清单）" % candidate_id)
