"""查询与规则解析切片（A1-3）：抽取候选 / 证据打分 / 统一检索 / 规则解析 / 文档切片 / 抽取状态。

迁移来源：`server.py::_call_tool_impl` 里对应的 `if name == "…"` 分支（**参数解析逐字照搬**）✓
实现：`ke_extract` / `ke_score` / `ke_lookup` / `ke_review` / `srv.outline` / `srv.state`

留在 `server.py` 的两个**已归一化**别名（`rules_of_task` / `graph_query`，只返回静态 `deprecated` 回执）
不属于本切片 —— 它们没有实现可搬，挪动不会带来任何结构收益 ✓
"""
from __future__ import annotations

import ke_extract
import ke_lookup
import ke_review
import ke_score
from srv.handlers import handle
from srv.outline import doc_outline as _doc_outline
from srv.state import extract_state as _extract_state


@handle("extract_candidates")
def extract_candidates(args: dict) -> dict:
    # 2026-10-17：**候选抽取**（只定位 ✓ 不判定 ✗）—— 判读交给知识提取技能/模型 ✓
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
    return ke_lookup.knowledge_lookup(
        str(args.get("kb_id", "")), str(args.get("mode", "rules") or "rules"),
        str(args.get("query", "")), str(args.get("spec", "")), str(args.get("start", "")),
        int(args.get("depth", 2) or 2), int(args.get("limit", 20) or 20),
        str(args.get("cls", "")), str(args.get("page_type", "")),
        args.get("kb_ids") or None, bool(args.get("auto_widen", False)),
        # 2026-10-24（需求 2）：本体提示开关 + 遥测字段（会话号/原问题仅入日志）
        args.get("ontology_hint"), str(args.get("session_no", "") or ""),
        str(args.get("question", "") or ""))


@handle("rules_resolve")
def rules_resolve(args: dict) -> dict:
    # 2026-10-16（用户口径）：**按步骤解析规则**（direct/policy 递归/ask/task 兼容/path 预留）
    return ke_review.rules_resolve(str(args.get("kb_id", "")), str(args.get("spec", "")),
                                   int(args.get("limit", 300) or 300))


@handle("rules_of_task_legacy")
def rules_of_task_legacy(args: dict) -> dict:
    # 2026-10-16（用户口径）：**按任务取规则契约**（图边 taskExecutesRule + 页面属性真读；
    # 带 needs_extract 分流 / required_fields / source 出处 / hash 指纹 ⇒ 反假断言可验）
    return ke_review.rules_of_task(str(args.get("kb_id", "")), str(args.get("task", "")),
                                   int(args.get("limit", 300) or 300))


@handle("rules_of_policy")
def rules_of_policy(args: dict) -> dict:
    return ke_review.rules_of_policy(str(args.get("kb_id", "")), str(args.get("policy", "")),
                                     int(args.get("limit", 300) or 300))


@handle("graph_query_legacy")
def graph_query_legacy(args: dict) -> dict:
    return ke_review.graph_query(str(args.get("cypher", "")), int(args.get("limit", 200) or 200))


@handle("doc_outline")
def doc_outline(args: dict) -> dict:
    return _doc_outline(str(args["kb_id"]), str(args.get("knowledge_id", "")),
                        int(args.get("budget_tokens", 0) or 0), int(args.get("cursor", 0) or 0),
                        int(args.get("batches", 1) or 1),
                        bool(args.get("with_text", True)))


@handle("extract_state")
def extract_state(args: dict) -> dict:
    return _extract_state(str(args["kb_id"]), str(args.get("knowledge_id", "")),
                          str(args.get("action", "get")))
