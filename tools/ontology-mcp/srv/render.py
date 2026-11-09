"""页正文小节渲染 + 技能上下文文案（P2 切片 S7 ✓ 从 `server.py` 抽出，**实现逐字未改**）

只做**纯渲染**（无 IO）：把结构化载荷（purpose/inputs/outputs/assertions/attributes/incoming）
拼成 wiki 小节；以及按技能上下文取文案（`SKILL_CONTEXTS`）✓
"""
from __future__ import annotations

import os
import sys

_CORE = os.path.abspath(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                     "..", "ke-core"))
if _CORE not in sys.path:
    sys.path.insert(0, _CORE)

import ke_ontology  # noqa: E402
import ke_pages  # noqa: E402
from srv.text import squash  # noqa: E402


def _design_sections(element: dict) -> list[str]:
    """把设计载荷里的结构化字段渲染成 wiki 小节（服务页要能自解释：用途/输入输出/规范/属性/被引用）。

    字段约定（设计智能体按此给 JSON）：
      purpose(str) / inputs(list[str]) / outputs(list[str])
      assertions(list[{id, kind(N|E), assertion}])
      attributes(dict{本体属性名: 值})           # 数据属性（如 ai_skill）
      incoming(list[{source_title, type}])        # 由 design_elements 反推的入边
    """
    lines: list[str] = []
    purpose = (element.get("purpose") or "").strip()
    body_text = (element.get("definition") or "").strip()
    # 正文首段与「用途」是同一段时不再重复输出（2026-09-21 用户实测：服务页里同一段出现两遍）
    if purpose and squash(purpose) and squash(purpose) in squash(body_text):
        purpose = ""
    if purpose:
        lines += ["## 用途", "", purpose, ""]
    inputs = [str(x).strip() for x in (element.get("inputs") or []) if str(x).strip()]
    outputs = [str(x).strip() for x in (element.get("outputs") or []) if str(x).strip()]
    if inputs or outputs:
        lines += ["## 输入 / 输出", "", "| 方向 | 业务对象 |", "|---|---|"]
        lines += ["| 输入 | %s |" % x for x in inputs]
        lines += ["| 输出 | %s |" % x for x in outputs]
        lines.append("")
    assertions = element.get("assertions") or []
    if assertions:
        lines += ["## 设计规范（正常 / 异常案例 · ASSERTION）", "",
                  "| 编号 | 类型 | 断言 |", "|---|---|---|"]
        for a in assertions:
            kind = "正常" if str(a.get("kind", "N")).upper().startswith("N") else "异常"
            lines.append("| %s | %s | %s |" % (a.get("id", ""), kind, a.get("assertion", "")))
        lines.append("")
    attributes = element.get("attributes") or {}
    if attributes:
        # 本体当 schema：属性名（中文标签，prefixed，range）由本体元数据渲染，不靠调用方排版。
        # 2026-10-13（方案 D）：**统一走内核渲染器** —— 标题式（`### 名称（prefixed，range）` + 值）、
        # 值不截断；旧的 `- 名称（标签，range） = 值` 一行式**不再写**（只在读侧兼容，契约 §4.3）。
        lines += ke_pages.render_attributes_section(element.get("type") or "", attributes)
        lines.append("")
    # 边限定属性（如 `easvc:crudKind` = CRUD 种类）：单独小节，**不动 `## 本体关系` 的行格式**
    # （那行的语法被 ke_pages 解析成 out_links/in_links，改语法会连带审计与反向边）。
    qualified = [r for r in (element.get("relations") or []) if r.get("properties")]
    if qualified:
        lines += [ke_pages.QUAL_SECTION, ""]
        # 同 (类型, 目标) 合并成一行（如 C 与 R 两条边 → `crudKind=C,R`），
        # 否则同一键会输出多行，人工与解析都难读（2026-09-21 实测）。
        merged_qual: dict = {}
        order: list = []
        for rel in qualified:
            key = (rel["type"], rel.get("target_slug") or rel.get("target") or "")
            if key not in merged_qual:
                merged_qual[key] = {"rel": rel, "props": {}}
                order.append(key)
            for k, v in (rel["properties"] or {}).items():
                old = (merged_qual[key]["props"].get(k) or "").strip()
                vals = [x for x in (old + "," + str(v)).strip(",").split(",") if x]
                merged_qual[key]["props"][k] = ",".join(sorted(set(vals)))
        for key in order:
            rel = merged_qual[key]["rel"]
            lines.append(ke_pages.rel_qualifier_line(
                rel.get("label") or rel.get("type"), rel.get("type"), rel.get("target") or "",
                rel.get("target_slug") or "", merged_qual[key]["props"]))
        lines.append("")
    incoming = element.get("incoming") or []
    if incoming:
        lines += ["## 被引用（入边）", "",
                  "> 本节由系统按本体关系**自动生成**（正文里不要手写引用链接）；要改请到来源页「本体关系」小节。",
                  ""]
        for edge in incoming:
            lines.append("- 「%s」（`%s`）→ 本页" % (edge.get("source_title", ""), edge.get("type", "")))
        lines.append("")
    return lines


# ---------------------------------------------------------------------------
# 技能上下文（2026-11-09 收敛）
#   domain_modeling     领域建模（当前主流程）
#   knowledge_modeling  中性上下文（规则/流程/对象/验收等 L1 活动共用）
# 旧的 ea_overview_design / service_detailed_design / design 已无活跃技能；
# 不再生成「概要设计 / 无原文片段 / 请补 source_text」占位。
# ---------------------------------------------------------------------------
SKILL_CONTEXTS = {
    "domain_modeling": {
        "label": "领域建模",
        "report_label": "领域建模报告",
        "report_category": "领域建模报告",
        "report_source": "领域建模报告（由领域建模智能体按文档分批生成、人工确认后落库）",
        "generated_by": "领域建模智能体",
    },
    "knowledge_modeling": {
        "label": "知识建模",
        "report_label": "知识建模报告",
        "report_category": "知识建模报告",
        "report_source": "知识建模报告（由知识建模智能体生成、人工确认后落库）",
        "generated_by": "知识建模智能体",
    },
}

# 旧 context / L1 活动的兼容入口：一律落到中性上下文，绝不回落到“概要设计”。
CONTEXT_ALIASES = {
    "process_modeling": "knowledge_modeling",
    "rule_engineering": "knowledge_modeling",
    "object_modeling": "knowledge_modeling",
    "acceptance": "knowledge_modeling",
    "design": "knowledge_modeling",
    "ea_overview_design": "knowledge_modeling",
    "service_detailed_design": "knowledge_modeling",
}


def _type_label_of(page_type: str, fallback: str = "") -> str:
    """本体类型的中文名：**必须查本体** ✓（`class_meta()`），查不到才用 fallback。
    2026-10-18 修：报告页曾写死「概要设计报告」⇒ 页头出现
    「本体类型：概要设计报告（bmm:TechnicalReviewReport）」的自相矛盾（用户实测）。
    """
    try:
        info = (ke_ontology.class_meta() or {}).get(page_type) or {}
        lab = (info.get("label") or "").strip()
        if lab and lab != page_type:
            return lab
    except Exception:  # noqa: BLE001
        pass
    return (fallback or page_type)


def context_meta(context: str = "") -> dict:
    """取技能上下文文案（未知/旧 context → 中性 `knowledge_modeling`）。"""
    key = (context or "").strip().lower()
    key = CONTEXT_ALIASES.get(key, key)
    if key not in SKILL_CONTEXTS:
        key = "knowledge_modeling"
    return {"id": key, **SKILL_CONTEXTS[key]}
