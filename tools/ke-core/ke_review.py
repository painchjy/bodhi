"""ke-core · 文档评审（按业务策略下的业务规则逐条评）。

用户口径（2026-09-29）：对选定的**文档**（知识库文档或附件）选一条**业务策略**评审；
按该策略下的**业务规则逐条**处理：

- 规则「实现方式」= **LLM软规则** → 交给大模型（智能体自己读范围章节判定）；
- 规则「实现方式」= **图检索**   → 按规则语义生成**只读 Cypher**，由图谱给结论；
- 若有**可参考规范**（`bmm:ruleReference`）→ 先取规范内容再评；
- **每条规则有范围**（`bmm:ruleScope`，一般=文档章节）→ 只在该范围内判断，**不通读全文**。

本模块只提供三个**确定性**工具（语义判断由智能体做）：
    rules_of_policy(kb_id, policy)                 列出某策略下的规则（含级别/范围/实现方式/参考规范）
    graph_query(cypher, limit)                     **只读** Cypher（白名单校验），给"图检索"型规则出结论
    review_apply(kb_id, doc, policy, findings)     把逐条结论写成**一页**评审报告（版本化、可回退）
"""

from __future__ import annotations

import pathlib
import re
import sys

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import ke_db  # noqa: E402
import ke_pages  # noqa: E402

REPORT_TYPE = "bmm:Assessment"          # 评审=对文档的评估（本体类；B1 干净）
REVIEW_TAG = "bodhi-review"
REL_SECTION = "## 本体关系"
EVIDENCE_SECTION = "## 原文依据"
SLUG_SAFE = re.compile(r"[\\/\s]+")
# 只读白名单：以这些关键字开头才放行；出现写操作关键字一律拒
READ_OK = ("MATCH", "OPTIONAL MATCH", "WITH", "UNWIND", "RETURN", "CALL DB.", "SHOW ", "EXPLAIN ")
WRITE_BAD = ("CREATE", "MERGE", "SET ", "DELETE", "REMOVE", "DROP", "FOREACH", "LOAD CSV",
             "CALL DBMS", "CALL APOC.", ";  ")


def _slug(text: str) -> str:
    return SLUG_SAFE.sub("-", str(text or "").strip()).strip("-")


def _enum_rels(meta: str) -> dict:
    """取页元数据里的「枚举关系」（`bmm:hasEnforcementLevel` → `bmm:Advisory`）。"""
    import json
    try:
        data = json.loads(meta or "{}")
    except Exception:  # noqa: BLE001
        return {}
    return ((data.get("ontology") or {}).get("enum_relations") or {})


def _attr(meta: str, key: str, hint: str = "") -> str:
    """从页面元数据里取某数据属性的值。**兼容两代口径**：

    - 新：`attributes` 键 = 本体属性名（`bmm:ruleScope`）或本地名（`ruleScope`）；
    - 老：`attributes` 键 = 中文列名（`适用范围`）→ 用工信 `hint` 在 `attributes_by_column` 里兜底。
    """
    import json
    try:
        data = json.loads(meta or "{}")
    except Exception:  # noqa: BLE001
        return ""
    onto = (data.get("ontology") or {})
    attrs = onto.get("attributes") or {}
    low = key.lower()
    for name, value in attrs.items():
        if str(name).split(":")[-1].lower() == low:
            return str(value or "")
    by_col = onto.get("attributes_by_column") or {}
    if hint:
        for source in (by_col, attrs):          # 新页看 by_column；老页的 attributes 键本身就是中文列名
            for column, value in source.items():
                if hint in str(column) or str(column) in hint:
                    return str(value or "")
    return ""


def rules_of_policy(kb_id: str = "", policy: str = "", limit: int = 300) -> dict:
    """列出某业务策略下的业务规则（只读）。`policy` 可用 slug 片段 / 标题片段。"""
    kb, kb_name, _note = ke_db.resolve_kb_id(kb_id)
    pages = ke_db.psql_csv(
        "SELECT slug, title, COALESCE(page_type,'') AS page_type, COALESCE(content,'') AS content, "
        "       COALESCE(page_metadata::text,'{}') AS meta "
        "  FROM wiki_pages WHERE knowledge_base_id = %s AND deleted_at IS NULL "
        "   AND page_type = 'bmm:BusinessPolicy' AND (slug ILIKE %s OR title ILIKE %s) LIMIT 5"
        % (ke_db.sql_str(kb), ke_db.sql_str("%" + policy + "%"), ke_db.sql_str("%" + policy + "%")))
    if not pages:
        return {"ok": False, "error": "knowledge_base 里找不到这条业务策略：%s" % policy,
                "hint": "先确认策略页已建（page_type=bmm:BusinessPolicy），或用 slug/标题片段再试"}
    pol = pages[0]
    rows = ke_db.psql_csv(
        "SELECT slug, title, COALESCE(page_type,'') AS page_type, COALESCE(content,'') AS content, "
        "       COALESCE(page_metadata::text,'{}') AS meta "
        "  FROM wiki_pages WHERE knowledge_base_id = %s AND deleted_at IS NULL "
        "   AND page_type LIKE 'bmm:%%BusinessRule' AND content LIKE %s "
        " ORDER BY slug LIMIT %d"
        % (ke_db.sql_str(kb), ke_db.sql_str("%" + pol["slug"] + "%"), int(limit or 300)))
    out = []
    for r in rows:
        level = ""
        enum_rel = _enum_rels(r["meta"])
        for name, value in enum_rel.items():
            if str(name).split(":")[-1].lower() == "hasenforcementlevel":
                level = str(value)
                break
        if not level:                                  # 老页：从正文关系行兜底（无链接形态）
            hit = re.search(r"hasEnforcementLevel`\)\s*→\s*(?P<v>[A-Za-z_]+:[A-Za-z]+)", r["content"])
            if hit:
                level = hit.group("v")
        ev = re.search(r"%s\n\n> ?(.+)" % re.escape(EVIDENCE_SECTION), r["content"])
        out.append({"slug": r["slug"], "name": r["title"], "page_type": r["page_type"],
                    "level": level, "scope": _attr(r["meta"], "ruleScope", "适用范围"),
                    "how": _attr(r["meta"], "ruleImplementation", "实现方式"),
                    "reference": _attr(r["meta"], "ruleReference", "参考规范"),
                    "evidence": (ev.group(1).strip()[:400] if ev else "")})
    return {"ok": True, "kb": {"id": kb, "name": kb_name},
            "policy": {"slug": pol["slug"], "name": pol["title"]},
            "count": len(out), "rules": out,
            "note": ("每条规则的 `scope` 是适用范围（一般=文档章节），`how` 是实现方式"
                     "（LLM软规则 / 图检索）；按 how 分流判定，见技能 document_review。")}


def graph_query(cypher: str = "", limit: int = 200, timeout: float = 25.0) -> dict:
    """**只读** Cypher（给"图检索"型规则出结论）。写操作/存储过程调用一律拒；自动补 LIMIT。"""
    import ke_neo4j
    text = (cypher or "").strip().rstrip(";").strip()
    if not text:
        return {"ok": False, "error": "需要 cypher"}
    head = text.upper().lstrip()
    if not head.startswith(READ_OK):
        return {"ok": False, "error": "只允许只读查询（MATCH / OPTIONAL MATCH / WITH / UNWIND / RETURN 开头）",
                "cypher": text}
    flat = " " + re.sub(r"\s+", " ", text.upper()) + " "
    for bad in WRITE_BAD:
        if bad in flat:
            return {"ok": False, "error": "查询里含写操作/危险调用：%s（图检索只读）" % bad.strip(),
                    "cypher": text}
    cap = max(1, min(int(limit or 200), 1000))
    if not re.search(r"\bLIMIT\b", text, re.I):
        text = "%s LIMIT %d" % (text, cap)
    try:
        rows = ke_neo4j.query(text, timeout=timeout)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": "图查询失败：%s" % str(exc)[:300], "cypher": text,
                "hint": "先跑一条探针（MATCH (n) RETURN labels(n)[0] AS kind, count(*) AS n）看标签"}
    return {"ok": True, "cypher": text, "rows": len(rows), "limit": cap,
            "result": rows[:cap], "truncated": len(rows) >= cap}


def review_apply(kb_id: str = "", doc: str = "", policy: str = "", findings: list | None = None,
                 page_type: str = REPORT_TYPE, actor: str = "agent:document_review",
                 policy_slug: str = "", tenant: int | None = None) -> dict:
    """把逐条评审结论写成**一页**（`slug=review/<doc>-<policy>`；版本化可回退）。

    `findings[]`：{rule, verdict(符合|不符合|不适用|无法判定), severity, scope, evidence, how, cypher, suggestion}
    """
    kb, kb_name, _note = ke_db.resolve_kb_id(kb_id)
    acl = ke_db.assert_can_write(kb, tenant if tenant is not None else ke_db.caller_tenant())
    if not acl.get("allowed"):
        return {"ok": False, "error": "need_write_permission", "permission": acl}
    items = [f for f in (findings or []) if isinstance(f, dict)]
    if not items:
        return {"ok": False, "error": "需要 findings（每条：rule/verdict/evidence…）"}
    verdicts = {}
    for f in items:
        verdicts[str(f.get("verdict") or "无法判定")] = verdicts.get(str(f.get("verdict") or "无法判定"), 0) + 1
    slug = "review/%s-%s" % (_slug(doc), _slug(policy))
    title = "评审报告：%s × %s" % (doc, policy)
    lines = ["# %s" % title, "",
             "> **本体类型**：评估（`%s`）  " % page_type,
             "> **生成方式**：文档评审技能（`%s`，按策略 `%s`）  " % (REVIEW_TAG, policy),
             "> **文档**：%s  ｜ **策略**：%s  ｜ **规则数**：%d" % (doc, policy, len(items)), "",
             "## 结论汇总", "",
             "| 结论 | 条数 |", "|---|---|"]
    for key in ("不符合", "符合", "不适用", "无法判定"):
        if verdicts.get(key):
            lines.append("| %s | %d |" % (key, verdicts[key]))
    for key, value in verdicts.items():
        if key not in ("不符合", "符合", "不适用", "无法判定"):
            lines.append("| %s | %d |" % (key, value))
    lines += ["", "## 逐条结论", "", "| 规则 | 结论 | 严重度 | 范围 | 方式 |", "|---|---|---|---|---|"]
    for f in items:
        lines.append("| %s | %s | %s | %s | %s |"
                     % (f.get("rule") or "", f.get("verdict") or "", f.get("severity") or "",
                        f.get("scope") or "", f.get("how") or ""))
    lines += ["", "## 结论明细", ""]
    for f in items:
        lines.append("### %s" % (f.get("rule") or "（未命名规则）"))
        lines.append("")
        lines.append("- 结论：**%s**（严重度 %s；方式 %s；范围 %s）"
                     % (f.get("verdict") or "", f.get("severity") or "-", f.get("how") or "-",
                        f.get("scope") or "-"))
        if f.get("cypher"):
            lines.append("- 图检索语句：`%s`" % str(f["cypher"]).replace("`", "'")[:400])
        if f.get("suggestion"):
            lines.append("- 建议：%s" % f["suggestion"])
        quote = str(f.get("evidence") or "").strip() or "（该条未给逐字证据）"
        lines += ["", "#### 证据（逐字）", "", "> %s" % quote.replace("\n", "\n> "), ""]
    # 页级「## 原文依据」：汇总各条证据（巡检 F3 按此小节判定"是否有逐字摘录"）
    quotes = [str(f.get("evidence") or "").strip() for f in items if str(f.get("evidence") or "").strip()]
    lines += [EVIDENCE_SECTION, ""] + (["> %s" % q.replace("\n", "\n> ") for q in quotes]
                                       or ["> （本次评审未附逐字证据）"]) + [""]
    if policy_slug:
        lines += [REL_SECTION, "",
                  "- 促进指导规范（`bmm:promotesDirective`）→ [[%s|%s]]" % (policy_slug, policy), ""]
    content = "\n".join(lines)
    written = ke_pages.upsert_page(kb, slug, title, page_type, content,
                                   summary="按「%s」评审「%s」：%s" % (policy, doc,
                                                                  "、".join("%s%d条" % (k, v)
                                                                          for k, v in verdicts.items())),
                                   tag=REVIEW_TAG,
                                   metadata={"ontology": {"model": "bmm", "class": page_type, "label": "评估",
                                                          "name": title, "generator": REVIEW_TAG},
                                             "review": {"doc": doc, "policy": policy,
                                                        "policy_slug": policy_slug,
                                                        "rules": len(items), "verdicts": verdicts,
                                                        "generator": REVIEW_TAG}})
    return {"ok": True, "kb": {"id": kb, "name": kb_name}, "slug": slug, "title": title,
            "rules": len(items), "verdicts": verdicts, "version": written.get("after_version"),
            "permission": acl.get("mode"), "next": "跑 audit_scan 让结论页也满足 F3（本页已带原文依据）"}

