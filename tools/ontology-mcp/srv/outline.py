"""切片 → 上下文批次（P2 切片 S4 ✓ 从 `server.py` 抽出，**实现逐字未改**）

分工：
- `_chunk_units`：把切片按**父子关系**组装成单元（父块正文 + 子块索引）
- `_split_unit`：父块超预算时按子块细分（保证"调小阈值"永远能继续缩小 ✓）
- `doc_outline`：按预算/游标切批，回执带 `next_cursor` / `oversized_units` / `split_parents`
"""
from __future__ import annotations

import os
import sys

_CORE = os.path.abspath(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                     "..", "ke-core"))
if _CORE not in sys.path:
    sys.path.insert(0, _CORE)

import ke_db  # noqa: E402
from srv.constants import CHARS_PER_TOKEN, DEFAULT_ROUND_BUDGET  # noqa: E402
from srv.kbresolve import resolve_kb_id, resolve_knowledge_id  # noqa: E402
from srv.text import squash  # noqa: E402

psql_csv = ke_db.psql_csv
sql_str = ke_db.sql_str


def _est_tokens(text: str) -> int:
    return int(len(squash(text)) / CHARS_PER_TOKEN) + 1


def _chunk_units(knowledge_id: str) -> list[dict]:
    """把切片按**父子关系**组装成上下文单元：一个 parent_text（父）＋它名下的 text（子）。

    - 父块已经包含子块正文（实测：父 ≈ 子之和 + 头），所以**上下文用父块正文**，
      子块只用于定位（`chunk_refs`/`source_text` 匹配）；避免重复喂给模型。
    - 没有父块（非层级切分）时，按顺序把 text 块打包成等大单元。
    """
    rows = psql_csv("SELECT id, chunk_index, chunk_type, COALESCE(parent_chunk_id,'') AS parent_id, "
                    "content FROM chunks WHERE knowledge_id = %s AND deleted_at IS NULL "
                    "AND COALESCE(is_enabled, true) ORDER BY chunk_index, seq_id, id"
                    % sql_str(knowledge_id))
    parents = [r for r in rows if (r.get("chunk_type") or "") == "parent_text"]
    children: dict[str, list[dict]] = {}
    for row in rows:
        if row.get("parent_id"):
            children.setdefault(row["parent_id"], []).append(row)
    units: list[dict] = []
    if parents:
        for p in parents:
            kids = children.get(p["id"], [])
            text = p["content"] or ""
            units.append({"unit_id": p["id"], "kind": "parent",
                          "chunk_ids": [p["id"]] + [k["id"] for k in kids],
                          "child_count": len(kids), "chars": len(text), "text": text,
                          "parent_head": squash(text)[:120],
                          "children": [{"id": k["id"], "chars": len(k["content"] or ""),
                                        "text": k["content"] or "",
                                        "est_tokens": _est_tokens(k["content"] or "")}
                                       for k in kids]})
        # 有父块但有些 text 块没挂父（切分异常）→ 追加成独立单元，避免漏内容
        orphan = [r for r in rows if (r.get("chunk_type") or "") == "text" and not r.get("parent_id")]
        for r in orphan:
            units.append({"unit_id": r["id"], "kind": "orphan_text", "chunk_ids": [r["id"]],
                          "child_count": 0, "chars": len(r["content"] or ""), "text": r["content"] or "",
                          "parent_head": "", "children": []})
    else:
        buffer, chars, ids = [], 0, []
        for r in rows:
            buffer.append(r["content"] or "")
            ids.append(r["id"])
            chars += len(r["content"] or "")
            if chars >= 1500:
                units.append({"unit_id": ids[0], "kind": "packed_text", "chunk_ids": list(ids),
                              "child_count": len(ids) - 1, "chars": chars, "text": "\n".join(buffer),
                              "parent_head": "", "children": []})
                buffer, chars, ids = [], 0, []
        if buffer:
            units.append({"unit_id": ids[0], "kind": "packed_text", "chunk_ids": list(ids),
                          "child_count": len(ids) - 1, "chars": chars, "text": "\n".join(buffer),
                          "parent_head": "", "children": []})
    for u in units:
        u["est_tokens"] = _est_tokens(u["text"])
        u["headings"] = [squash((ln or ""))[:60] for ln in (u["text"] or "").splitlines()[:3] if (ln or "").strip()]
    return units


def _split_unit(unit: dict, budget: int) -> list[dict]:
    """父块本身超过预算时，**按子块细分**（这样"调小阈值"永远能继续缩小）。

    每个子块独立成一个上下文原子；带上父块开头 120 字作为归属提示（便于模型知道自己在读哪一段）。
    """
    children = unit.get("children") or []
    if not children:
        return [unit]                      # 没有子块可拆：只能整块给（回执里 oversized 会提示）
    atoms = []
    for i, child in enumerate(children):
        prefix = ("【父块：%s…】\n" % unit.get("parent_head", "")) if i == 0 else ""
        text = prefix + (child.get("text") or "")
        atoms.append({"unit_id": child["id"], "kind": "child_of",
                      "parent_id": unit["unit_id"], "chunk_ids": [child["id"]],
                      "child_count": 0, "chars": len(text), "text": text,
                      "headings": [squash(text)[:60]],
                      "est_tokens": _est_tokens(text), "children": []})
    return atoms


def doc_outline(kb_id: str, knowledge_id: str, budget_tokens: int = 0, cursor: int = 0,
                batches: int = 1, with_text: bool = True) -> dict:
    """按切片**父子关系**把文档组织成"大小合适"的上下文批次（领域建模 v2 的入口工具）。

    - `budget_tokens`：本批上下文的 token 上限（**会话参数**）。默认取环境变量
      `BODHI_ROUND_BUDGET_TOKENS`（默认 8000）。**超时就把它调小**再重跑本工具；
    - `cursor`：从第几个上下文单元开始（上一批回执里的 `next_cursor`）；
    - `batches`：本次取几批（默认 1，对应"一轮一次交互"）；
    - `with_text=False`：只给地图（标题/规模），不返回正文（省 token）。
    """
    kb_id, _note = resolve_kb_id(kb_id)
    knowledge_id, doc_title = resolve_knowledge_id(kb_id, knowledge_id)
    budget = max(500, int(budget_tokens or DEFAULT_ROUND_BUDGET))
    raw_units = _chunk_units(knowledge_id)
    if not raw_units:
        return {"error": "该文档没有可用切片：%s（先等切片完成）" % knowledge_id}
    # 父块本身超过预算 → 按子块细分（这样"调小阈值"永远能继续缩小上下文）
    units, split_parents = [], []
    for u in raw_units:
        if u["est_tokens"] > budget and (u.get("children") or []):
            atoms = _split_unit(u, budget)
            units.extend(atoms)
            split_parents.append({"parent_unit": u["unit_id"], "atoms": len(atoms)})
        else:
            units.append(u)
    total_chars = sum(u["chars"] for u in units)
    total_tokens = sum(u["est_tokens"] for u in units)
    start = max(0, min(int(cursor or 0), len(units)))
    out_batches, idx = [], start
    while idx < len(units) and len(out_batches) < max(1, int(batches or 1)):
        chars, group = 0, []
        while idx < len(units):
            unit = units[idx]
            if group and (chars + unit["chars"]) / CHARS_PER_TOKEN > budget:
                break
            group.append(unit)
            chars += unit["chars"]
            idx += 1
        if not group:
            break
        out_batches.append({
            "batch_no": len(out_batches) + 1,
            "units": [u["unit_id"] for u in group],
            "chunk_ids": [cid for u in group for cid in u["chunk_ids"]],
            "child_count": sum(u["child_count"] for u in group),
            "chars": chars,
            "est_tokens": int(chars / CHARS_PER_TOKEN) + 1,
            "headings": [h for u in group for h in u["headings"]][:8],
            "text": "\n\n---\n\n".join(u["text"] for u in group) if with_text else "",
        })
    return {
        "kb_id": kb_id, "knowledge_id": knowledge_id, "doc_title": doc_title,
        "budget_tokens": budget, "chars_per_token": CHARS_PER_TOKEN,
        "total_units": len(units), "total_chars": total_chars,
        "est_total_tokens": total_tokens,
        "est_total_batches": max(1, -(-total_tokens // budget)),
        "cursor": start, "next_cursor": idx, "done": idx >= len(units),
        "oversized_units": [u["unit_id"] for u in units if u["est_tokens"] > budget],
        "split_parents": split_parents,
        "batches": out_batches,
        "guidance": [
            "只从本批正文里抽知识：节点/关系必须有本批文本支撑（`source_text` 用原句，便于定位切片）。",
            "需要的目标节点**不在本批里**时：先用向量检索（wiki_search）找候选，再调 `link_candidates` "
            "登记候选 —— **不要自己编 slug**，也不要直接写关系。",
            "本批产出用 `save_knowledge(stage=\"graph\", mode=\"apply\", session={...})` 落库；回执里的 "
            "`created[].no` 就是页面的**会话编号**（下一轮引用编号/slug，而不是复述全文）。",
            "一批做完：用回执里的 `session.next_cursor` 作为本工具的 `cursor` 取下一批。",
            "某轮超时/被截断：把 `budget_tokens` 调小（8000 → 4000 → 2000）重跑本工具；"
            "父块超过预算时会**自动按子块细分**（回执 `split_parents` 可见）。",
            "**同一份文档尽量固定预算跑完**；中途改预算则 `cursor` 失效，从头重跑即可 —— "
            "落库是幂等的（同 slug 命中即合并更新，不会重复建页）。",
        ],
    }
