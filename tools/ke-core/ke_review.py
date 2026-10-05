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


# 旧枚举个体 → 中文受控取值（2026-10-05 · 2A：执行级别改数据属性；迁移期双读）
ENFORCEMENT_ALIAS = {"bmm:Strict": "严格执行", "bmm:Override": "授权覆盖", "bmm:Advisory": "建议",
                     "Strict": "严格执行", "Override": "授权覆盖", "Advisory": "建议",
                     "严格执行": "严格执行", "授权覆盖": "授权覆盖", "建议": "建议"}


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
    # **2026-10-05 修（用户实测：某会话里 5 条策略全返回 0 条）**：
    # `bmm:isBasisFor` 的 domain=BusinessPolicy → range=BusinessRule ⇒ 「策略→规则」的关联行**写在策略页**上；
    # **规则页正文里没有策略 slug**（只有自动生成的「被引用」节，写的是策略**标题**）⇒
    # 旧实现 `content LIKE '%策略 slug%'` 必然 0 条。
    # 新口径：① 以**策略页的 `out_links`**（本体真实边）取目标规则页为准；
    #        ② `in_links`/正文含策略 slug **或** 含策略**标题** 兜底（兼容未重建 links 的老数据）。
    import json as _json

    def _links(sql_col: str) -> list:
        r = ke_db.psql_csv("SELECT COALESCE(%s::text,'[]') AS v FROM wiki_pages "
                           "WHERE knowledge_base_id = %s AND slug = %s"
                           % (sql_col, ke_db.sql_str(kb), ke_db.sql_str(pol["slug"])))
        if not r:
            return []
        try:
            out = _json.loads(r[0].get("v") or "[]")
            return [str(x) for x in out if isinstance(x, str)]
        except Exception:  # noqa: BLE001
            return []

    conds = ["out_links::text LIKE %s" % ke_db.sql_str("%" + pol["slug"] + "%"),
             "in_links::text LIKE %s" % ke_db.sql_str("%" + pol["slug"] + "%"),
             "content LIKE %s" % ke_db.sql_str("%" + pol["slug"] + "%"),
             "content LIKE %s" % ke_db.sql_str("%" + pol["title"] + "%")]
    targets = _links("out_links")
    if targets:
        conds.insert(0, "slug IN (%s)" % ", ".join(ke_db.sql_str(s) for s in targets))
    where = " OR ".join(conds)
    # **2026-10-05 用户口径**：业务规则**有子类**（Operative/Structural…，将来还会有别的命名），
    # 所以"规则目标类"必须用**图（本体 T-Box）**算子类闭包，而不是 `page_type LIKE '%BusinessRule'` 的
    # 字符串尾巴（会漏）。⚠️ 说明：**知识页实例目前不进图库**（Neo4j 只有本体层：BodhiOntClass/Property…），
    # 所以**页的检索仍在 PG**，但**类集合由图给**（图定义规则、PG 存实例）。
    def _rule_class_closure() -> list:
        try:
            import ke_neo4j  # noqa: PLC0415
            rows = ke_neo4j.query(
                "MATCH (c:BodhiOntClass)-[:BODHI_SUBCLASS_OF*0..]->(r:BodhiOntClass {prefixed:$root}) "
                "WHERE c.external IS NULL RETURN DISTINCT c.prefixed AS p", {"root": "bmm:BusinessRule"})
            out = {str(x["p"]) for x in rows if x.get("p")}
            out.add("bmm:BusinessRule")
            return sorted(out)
        except Exception:  # noqa: BLE001
            return []

    rule_classes = _rule_class_closure()
    if rule_classes:
        cls_cond = "page_type IN (%s)" % ", ".join(ke_db.sql_str(c) for c in rule_classes)
    else:                                              # 图不可用 → 退回旧写法（不阻断）
        cls_cond = "page_type LIKE 'bmm:%%BusinessRule'"
    where = "(%s) AND (%s)" % (cls_cond, where)
    rows = ke_db.psql_csv(
        "SELECT slug, title, COALESCE(page_type,'') AS page_type, COALESCE(content,'') AS content, "
        "       COALESCE(page_metadata::text,'{}') AS meta "
        "  FROM wiki_pages WHERE knowledge_base_id = %s AND deleted_at IS NULL "
        "   AND (%s) "
        " ORDER BY slug LIMIT %d"
        % (ke_db.sql_str(kb), where, int(limit or 300)))
    out = []
    for r in rows:
        # 执行级别读取顺序（2026-10-05 · 2A 口径：执行级别已从对象属性改**数据属性**）
        #   ① 元数据数据属性 `enforcementLevel`（新页，机器口径）
        #   ② 元数据枚举关系 `hasEnforcementLevel`（迁移期遗留）
        #   ③ 正文「本体关系」旧链接行（`…hasEnforcementLevel`）→ bmm:Strict`）
        #   ④ 正文「属性（数据属性）」新行（`- enforcementLevel = 严格执行`）
        level = _attr(r["meta"], "enforcementLevel", "执行级别")
        if not level:
            enum_rel = _enum_rels(r["meta"])
            for name, value in enum_rel.items():
                if str(name).split(":")[-1].lower() == "hasenforcementlevel":
                    level = str(value)
                    break
        if not level:                                  # 老页：从正文关系行兜底（无链接形态）
            hit = re.search(r"hasEnforcementLevel`\)\s*→\s*(?P<v>[A-Za-z_]+:[A-Za-z]+)", r["content"])
            if hit:
                level = hit.group("v")
        if not level:                                  # 迁移后：正文数据属性行兜底（兼容 `bmm:` 前缀/括号写法）
            m2 = re.search(r"^-\s*(?:[A-Za-z_][A-Za-z0-9_]*:)?enforcementLevel\b[^\n=]*=\s*(?P<v>.+?)\s*$",
                           r["content"], re.M)
            if m2:
                level = m2.group("v").strip()
        level = ENFORCEMENT_ALIAS.get(level, level)     # 旧个体 → 中文取值
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


def reference_lookup(kb_id: str = "", reference: str = "", allow_fetch: bool = False,
                     max_chars: int = 6000, timeout: float = 8.0) -> dict:
    """按规则里的「参考规范」（`bmm:ruleReference`）线索找**规范内容**（只读）。三级查找：

    ① 知识库**文档/附件**同名（`knowledges`）→ 回文档 id（正文用 `doc_outline` 取）；
    ② 知识库**wiki 页**标题/slug 命中 → 直接回正文片段；
    ③ `allow_fetch=True` 且是 http(s) URL → 抓取并剥 HTML（内网可能不可达，默认关）。
    都找不到 → `found=False` + `note`（技能口径：标「参考规范不可得」，**不要编造**）。
    """
    kb, kb_name, _note = ke_db.resolve_kb_id(kb_id) if kb_id else ("", "", "")
    ref = str(reference or "").strip()
    if not ref:
        return {"ok": False, "error": "需要 reference（规则里的参考规范线索）"}
    is_url = ref.lower().startswith(("http://", "https://"))
    tail = ref.rstrip("/").split("/")[-1].split("?")[0].split("#")[0]
    stem = tail.rsplit(".", 1)[0] if "." in tail else tail
    like = "%" + (stem or ref) + "%"
    out = {"ok": True, "kb": {"id": kb, "name": kb_name}, "reference": ref, "is_url": is_url,
           "found": False, "source": "", "hits": [], "text": "", "note": ""}
    if kb:
        # ② wiki 页（规范文档常被建成页）
        rows = ke_db.psql_csv(
            "SELECT slug, title, LEFT(COALESCE(content,''), %d) AS snippet FROM wiki_pages "
            " WHERE knowledge_base_id = %s AND deleted_at IS NULL AND page_type NOT LIKE 'bmm:%%' "
            "   AND (title ILIKE %s OR slug ILIKE %s) ORDER BY length(title) LIMIT 3"
            % (int(max_chars), ke_db.sql_str(kb), ke_db.sql_str(like), ke_db.sql_str(like)))
        if rows:
            out["found"], out["source"] = True, "wiki"
            out["hits"] = [{"slug": r["slug"], "title": r["title"]} for r in rows]
            out["text"] = rows[0]["snippet"]
            out["note"] = "命中的是知识库页；正文片段已附（要看全文用 wiki_read_page）"
            return out
        # ① 知识库文档/附件
        cols = {str(r["column_name"]).lower() for r in ke_db.psql_csv(
            "SELECT column_name FROM information_schema.columns WHERE table_name = 'knowledges'")}
        name_cols = [c for c in ("title", "name", "file_name", "filename") if c in cols]
        if name_cols:
            where = " OR ".join("%s::text ILIKE %s" % (c, ke_db.sql_str(like)) for c in name_cols)
            try:
                docs = ke_db.psql_csv(
                    "SELECT id, %s FROM knowledges WHERE knowledge_base_id = %s AND (%s) LIMIT 3"
                    % (", ".join(name_cols), ke_db.sql_str(kb), where))
            except Exception as exc:  # noqa: BLE001
                docs = []
                out["note"] = "查文档表失败：%s" % str(exc)[:120]
            if docs:
                out["found"], out["source"] = True, "doc"
                out["hits"] = [{"knowledge_id": d.get("id"),
                                "title": next((d.get(c) for c in name_cols if d.get(c)), "")}
                               for d in docs]
                out["note"] = "命中的是知识库文档/附件；正文用 doc_outline（或 list_knowledge_chunks）取"
                return out
    if is_url and allow_fetch:
        import html
        import urllib.request
        try:
            req = urllib.request.Request(ref, headers={"User-Agent": "bodhi-review/1.0"})
            with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
                raw = resp.read(200_000)
            text = raw.decode("utf-8", "replace")
            text = re.sub(r"(?is)<(script|style).*?</\1>", " ", text)
            text = re.sub(r"(?s)<[^>]+>", " ", text)
            text = re.sub(r"\s+", " ", html.unescape(text)).strip()
            out.update({"found": True, "source": "url", "text": text[:int(max_chars)],
                        "note": "从 URL 抓取并剥 HTML；判分时引用要标明来自外部规范"})
            return out
        except Exception as exc:  # noqa: BLE001
            out["note"] = ("URL 取不到（%s）→ 按技能口径标「参考规范不可得」，**不要编造规范内容**"
                           % str(exc)[:120])
            return out
    out["note"] = ("知识库里找不到同名规范%s → 标「参考规范不可得」，只按规则原文判定，**不要编造**"
                   % ("；URL 抓取未开启（allow_fetch=false）" if is_url else ""))
    return out


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
    if not policy_slug and policy:                 # 默认自动挂到策略页（bmm:promotesDirective）
        hits = ke_db.psql_csv(
            "SELECT slug FROM wiki_pages WHERE knowledge_base_id = %s AND deleted_at IS NULL "
            " AND page_type = 'bmm:BusinessPolicy' AND (title ILIKE %s OR slug ILIKE %s) "
            " ORDER BY length(title) LIMIT 1"
            % (ke_db.sql_str(kb), ke_db.sql_str("%" + str(policy) + "%"),
               ke_db.sql_str("%" + str(policy) + "%")))
        policy_slug = hits[0]["slug"] if hits else ""
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
            "policy_slug": policy_slug, "permission": acl.get("mode"),
            "next": "跑 audit_scan 让结论页也满足 F3（本页已带原文依据）"}

