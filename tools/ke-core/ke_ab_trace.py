"""ke-core · **C2 真值抽取**：从 WeKnora 的 `messages.agent_steps` 拿"真实调用序列"。

为什么需要（2026-10-25 用户口径）
--------------------------------
A/B 报告此前只能靠**智能体自报** `first_step` 判"这一问的第一步是不是 knowledge_lookup"，
自报纪律一松，样本就得整批打折 ✗。实测发现 WeKnora 库里的 `messages.agent_steps`
是 jsonb 数组（每轮：`thought / iteration / timestamp / tool_calls / reasoning_content`），
`tool_calls[].name/args` 就是**真实工具序列**；`messages.agent_duration_ms` 就是
**这一问的端到端耗时**（含 LLM 思考，实测中位 ≈84s）。

⇒ 本模块把真值抽成侧车 `logs/ab/trace_<日期>.jsonl`（一行一问）：
    · `tool_seq`          真实工具顺序（`mcp_bodhi_ontology_` 前缀已剥）；
    · `first_tool` / `first_retrieval`  **真·首步**（不再自报 ✓）；
    · `lookup_calls`      `knowledge_lookup` 真实调用次数；
    · `native_retrieval_calls`  WeKnora 原生检索（wiki_search 等）次数；
    · `agent_ms`          端到端耗时（真值，报告里的"总耗时"用它 ✓）；
    · `question`          同会话里紧邻的上一条用户消息（**只入日志，不入业务库**）。

用法：
```
python3 tools/ke-core/ke_ab_trace.py --since-days 3        # → logs/ab/trace_YYYYMMDD.jsonl
python3 tools/ke-core/ke_ab_trace.py --since-days 7 --session d134af6c-...
```
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import ke_db  # noqa: E402
import ke_lookup  # noqa: E402

MCP_PREFIX = "mcp_bodhi_ontology_"
# 本体侧检索（我们能观测，也是 A/B 的自变量所在）
BODHI_RETRIEVAL = ("knowledge_lookup", "context_lookup", "doc_outline",
                   "extract_candidates", "reference_lookup")
# WeKnora 原生检索（不经过 bodhi-mcp ⇒ 首步若是它们，这问**不是**我们要比的 ✓）
NATIVE_RETRIEVAL = ("wiki_search", "grep_chunks", "list_knowledge_chunks",
                    "wiki_read_page", "get_document_info", "hybrid_search", "vector_search")

SQL = """
select m.id                         as message_id,
       m.session_id                 as session_no,
       (m.created_at at time zone 'Asia/Shanghai')::text as ts,
       coalesce(m.agent_duration_ms, 0) as agent_ms,
       m.agent_steps::text          as steps,
       (select u.content
          from messages u
         where u.session_id = m.session_id and u.role = 'user'
           and u.deleted_at is null and u.created_at <= m.created_at
         order by u.created_at desc limit 1) as question
  from messages m
 where m.role = 'assistant' and m.deleted_at is null
   and jsonb_typeof(m.agent_steps) = 'array' and jsonb_array_length(m.agent_steps) > 0
   and m.created_at > now() - interval '{days} days'
   {session}
 order by m.created_at
"""


def _short(name: str) -> str:
    """`mcp_bodhi_ontology_knowledge_lookup` → `knowledge_lookup`（其它工具原样）。"""
    n = str(name or "")
    return n[len(MCP_PREFIX):] if n.startswith(MCP_PREFIX) else n


def parse_steps(raw: str) -> list:
    """`agent_steps` JSON 文本 → 真实调用序列 `[{name, args}]`（按 iteration 排序）。"""
    try:
        steps = json.loads(raw or "[]")
    except Exception:  # noqa: BLE001
        return []
    if not isinstance(steps, list):
        return []
    steps = sorted(steps, key=lambda s: (s.get("iteration") if isinstance(s, dict) else 0) or 0)
    out = []
    for st in steps:
        if not isinstance(st, dict):
            continue
        for call in (st.get("tool_calls") or []):
            if isinstance(call, dict) and call.get("name"):
                out.append({"name": _short(call["name"]), "args": call.get("args") or {}})
    return out


def build_row(rec: dict) -> dict:
    """一条 assistant 消息 → 一行真值（报告可读的最小集合）。"""
    calls = parse_steps(rec.get("steps") or "")
    seq = [c["name"] for c in calls]
    first_tool = seq[0] if seq else ""
    first_retr = next((n for n in seq if n in BODHI_RETRIEVAL or n in NATIVE_RETRIEVAL), "")
    q = (rec.get("question") or "").strip()
    # 第一条 knowledge_lookup 的入参形态（词数/层次）—— 与遥测侧 `q_shape` 同口径 ✓
    kw = next((c["args"] for c in calls if c["name"] == "knowledge_lookup"), {}) or {}
    shape = ke_lookup._q_shape(kw.get("mode") or "", kw.get("query") or "",
                               kw.get("cls") or "", {})
    return {
        "ts": (rec.get("ts") or "")[:19],
        "source": "weknora", "assignment": "weknora",
        "message_id": rec.get("message_id") or "",
        "session_no": rec.get("session_no") or "",
        "question": q[:300],
        "question_key": ke_lookup._question_key(q),
        "question_core": ke_lookup._question_core(q),
        "qid": ke_lookup._extract_qid(q),
        "q_shape": shape["q_shape"], "kw_bucket": shape["kw_bucket"], "level": shape["level"],
        "tool_seq": seq,
        "first_tool": first_tool, "first_retrieval": first_retr,
        # 2026-10-25 修（用户口径）：**首工具必然是 `lookup_turn_begin`**（开轮纪律要求的），
        # 所以"首步是不是 knowledge_lookup"必须看**首个检索类动作**，不能看 first_tool ✗
        "first_is_lookup": first_retr == "knowledge_lookup",
        "turn_begin_first": first_tool == "lookup_turn_begin",
        "lookup_calls": seq.count("knowledge_lookup"),
        "retrieval_calls": sum(1 for n in seq if n in BODHI_RETRIEVAL),
        "native_retrieval_calls": sum(1 for n in seq if n in NATIVE_RETRIEVAL),
        "tool_calls": len(seq),
        "agent_ms": int(rec.get("agent_ms") or 0),
    }


def collect(since_days: int = 3, session: str = "", limit: int = 0) -> list:
    sql = SQL.format(days=int(since_days),
                     session=("and m.session_id = %s" % ke_db.sql_str(session)) if session else "")
    rows = [build_row(r) for r in ke_db.psql_csv(sql)]
    if limit:
        rows = rows[-int(limit):]
    return rows


def main() -> int:
    ap = argparse.ArgumentParser(description="从 WeKnora agent_steps 抽 C2 真值（真实工具序列 + 端到端耗时）")
    ap.add_argument("--since-days", type=int, default=3)
    ap.add_argument("--session", default="", help="只看某个 WeKnora 会话")
    ap.add_argument("--limit", type=int, default=0, help="只取最近 N 条")
    ap.add_argument("--out", default="", help="输出 JSONL（默认 logs/ab/trace_<日期>.jsonl）")
    args = ap.parse_args()

    rows = collect(args.since_days, args.session, args.limit)
    out = args.out or os.path.join(REPO, "logs", "ab",
                                   "trace_%s.jsonl" % time.strftime("%Y%m%d"))
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(json.dumps({"ok": True, "out": out, "rows": len(rows),
                      "with_lookup": sum(1 for r in rows if r["lookup_calls"]),
                      "first_is_lookup": sum(1 for r in rows if r["first_is_lookup"]),
                      "native_first": sum(1 for r in rows
                                          if r["first_retrieval"] in NATIVE_RETRIEVAL)},
                     ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
