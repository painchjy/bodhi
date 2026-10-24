"""查询与规则解析切片（A1-3）：抽取候选 / 证据打分 / 统一检索 / 规则解析 / 文档切片 / 抽取状态。

迁移来源：`server.py::_call_tool_impl` 里对应的 `if name == "…"` 分支（**参数解析逐字照搬**）✓
实现：`ke_extract` / `ke_score` / `ke_lookup` / `ke_review` / `srv.outline` / `srv.state`

留在 `server.py` 的两个**已归一化**别名（`rules_of_task` / `graph_query`，只返回静态 `deprecated` 回执）
不属于本切片 —— 它们没有实现可搬，挪动不会带来任何结构收益 ✓

2026-10-24（A1-8 批 3c）：删除 `graph_query_legacy` —— 它注册了一个**不在 schema 里**的名字，
等于给已退役的"直接拼 Cypher"开了个后门（`_RETIRED_TOOLS["graph_query"]` 明令不再接受 Cypher ✗）。
图查询的 REST 入口 `/bodhi/graph/query` 保留（运维面，有自己的鉴权口径）。
"""
from __future__ import annotations

import ke_extract
import ke_lookup
import ke_review
import ke_score
from srv import ab_turn
from srv.handlers import handle
from srv.outline import doc_outline as _doc_outline
from srv.state import extract_state as _extract_state


def _as_bool(value):
    """三态布尔：`true/false` 已是 bool；字符串按常见写法解析；缺省 ⇒ None（= 未声明 ✓）"""
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() not in ("0", "false", "no", "off", "n")



@handle("extract_candidates")
def extract_candidates(args: dict) -> dict:
    # 2026-10-17：**候选抽取**（只定位 ✓ 不判定 ✗）—— 判读交给知识提取技能/模型 ✓
    _turn = str(args.get("turn_id", "") or "")
    if _turn:
        ab_turn.claim(_turn, "extract_candidates")
    return ke_extract.extract(str(args.get("kb_id", "")), str(args.get("knowledge_id", "")),
                              str(args.get("domain_kb", "")),
                              str(args.get("task", "") or ""),
                              args.get("other_kbs") or None,
                              int(args.get("limit_items", 20) or 20))


@handle("evidence_score")
def evidence_score(args: dict) -> dict:
    # 2026-10-17：**证据/结论可信度**（只读纯函数 ✓，口径唯一 ✓，智能体不必自己算 ✗）
    return ke_score.score_rule(args.get("facts") or [], args.get("checks") or [],
                               float(args.get("threshold", 0.60) or 0.60))


@handle("knowledge_lookup")
def knowledge_lookup(args: dict) -> dict:
    # 2026-10-22：**统一检索入口**（rules / keyword 类范围+多词 / instances / route / path / semantic）
    # 2026-10-24（A/B C1）：带 `turn_id` 时先认领"本轮首个检索动作"，供报告判"首步是否 knowledge_lookup"
    _turn = str(args.get("turn_id", "") or "")
    _first = ab_turn.claim(_turn, "knowledge_lookup") if _turn else ""
    return ke_lookup.knowledge_lookup(
        str(args.get("kb_id", "")), str(args.get("mode", "rules") or "rules"),
        str(args.get("query", "")), str(args.get("spec", "")), str(args.get("start", "")),
        int(args.get("depth", 2) or 2), int(args.get("limit", 20) or 20),
        str(args.get("cls", "")), str(args.get("page_type", "")),
        args.get("kb_ids") or None, bool(args.get("auto_widen", False)),
        # 2026-10-24（需求 2）：本体提示开关 + 遥测字段（会话号/原问题仅入日志）
        args.get("ontology_hint"), str(args.get("session_no", "") or ""),
        str(args.get("question", "") or ""),
        _turn, _as_bool(args.get("first_step")), _first)


@handle("rules_resolve")
def rules_resolve(args: dict) -> dict:
    # 2026-10-16（用户口径）：**按步骤解析规则**（direct/policy 递归/ask/task 兼容/path 预留）
    return ke_review.rules_resolve(str(args.get("kb_id", "")), str(args.get("spec", "")),
                                   int(args.get("limit", 300) or 300))


@handle("doc_outline")
def doc_outline(args: dict) -> dict:
    # A/B C1：与 knowledge_lookup 同轮时认领"首个检索动作"（顺序判定用 ✓）
    _turn = str(args.get("turn_id", "") or "")
    if _turn:
        ab_turn.claim(_turn, "doc_outline")
    return _doc_outline(str(args["kb_id"]), str(args.get("knowledge_id", "")),
                        int(args.get("budget_tokens", 0) or 0), int(args.get("cursor", 0) or 0),
                        int(args.get("batches", 1) or 1),
                        bool(args.get("with_text", True)))


@handle("extract_state")
def extract_state(args: dict) -> dict:
    return _extract_state(str(args["kb_id"]), str(args.get("knowledge_id", "")),
                          str(args.get("action", "get")))
