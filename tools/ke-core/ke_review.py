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

REPORT_TYPE = "bmm:TechnicalReviewReport"   # 2026-10-17：必须用 Report 子类 ✓（旧 Assessment ✗）          # 评审=对文档的评估（本体类；B1 干净）
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


def _bmm_rule_class_closure() -> list:
    """`bmm:BusinessRule` 的子类闭包（含自身），从 T-Box 图取。"""
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


def rules_resolve(kb_id: str = "", spec: str = "", limit: int = 300) -> dict:
    """**按步骤解析规则**（取代"任务→规则"的单一解 ✗）。

    `spec` 五种（**每步自选** ✓）：
      · `direct:<slug>[,<slug>…]`   直接指定 ✓
      · `policy:<策略名/slug 片段>`  经策略解析（**递归**：`isDerivedFrom` 入边 + `includesBusinessPolicy` 出边）✓
      · `ask`                        不回规则，回**可选范围**（策略清单），由用户定 ✓
      · `task:<任务名/slug>`         兼容旧行为（任务 → `taskExecutesRule`）✓
      · `path:<json>`                预留（`ontology_paths` 多路径并集）✓

    回执：`{resolved_by, rules[…与 rules_of_task 逐字段一致…], scope_options[], counts, hash}` ✓
    """
    import hashlib
    import json as _json
    import ke_graph as _kg

    kb, kb_name, _n = ke_db.resolve_kb_id(kb_id)
    sp = (spec or "").strip()
    if not sp:
        raise ValueError("rules_resolve 需要 spec（direct:/policy:/ask/task:/path:）")
    kind, _, arg = sp.partition(":")
    kind, arg = kind.strip().lower(), arg.strip()

    def _pols(like: str = "") -> list:
        """策略清单：**图查优先**（实测 ~10ms ✓）；图不可用才回 PG（~230ms ✗）。"""
        try:
            import ke_neo4j as _neo
            rows = _neo.query(
                "MATCH (n:BodhiInstance {kb_id:$kb}) WHERE n.page_type = 'bmm:BusinessPolicy' "
                + ("AND (n.slug CONTAINS $q OR coalesce(n.name,'') CONTAINS $q) " if like else "")
                + "RETURN n.slug AS slug, coalesce(n.name,'') AS t ORDER BY t LIMIT %d"
                % (5 if like else 200),
                {"kb": kb, "q": like}, timeout=8)
            out = [{"slug": str(r.get("slug")), "t": str(r.get("t") or "")}
                   for r in (rows or []) if r.get("slug")]
            if out or like:                 # 带过滤且无命中 ⇒ 就是没有 ✓（不回 PG ✗）
                return out
        except Exception:  # noqa: BLE001  图不可用 ⇒ 下降级 PG
            pass
        cond = ""
        if like:
            needle = ke_db.sql_str("%%%s%%" % like)
            cond = "AND (slug LIKE %s OR title LIKE %s)" % (needle, needle)
        return ke_db.psql_csv(
            "SELECT slug, COALESCE(title,'') AS t FROM wiki_pages "
            " WHERE knowledge_base_id = %s AND deleted_at IS NULL AND page_type = 'bmm:BusinessPolicy' "
            "   %s ORDER BY title %s"
            % (ke_db.sql_str(kb), cond, "LIMIT 5" if like else ""))

    resolved_by, rules, options = kind, [], []
    if kind == "direct":
        rules = _rule_contracts(kb, [x.strip() for x in arg.split(",") if x.strip()], kb_name)
    elif kind == "task":
        # 2026-10-18（用户口径 ✓）：**task: 已废弃** ✗ —— `bpmn:taskExecutesRule` 边已删除 ✓
        raise ValueError("spec 的 `task:` 已废弃 ✗（任务边已删除 ✓）；请改用 "
                         "`policy:<策略>` / `direct:<规则slug,…>` / `ask` ✓")
    elif kind == "policy":
        hits = _pols(arg)
        if not hits:
            raise ValueError("找不到策略（含 %r）；现有策略：%s"
                             % (arg, "、".join(x["t"] for x in _pols())))
        root = hits[0]["slug"]
        slugs = []
        try:                             # **一次** Cypher：策略闭包（includes 递归）+ 指向它的规则 ✓
            import ke_neo4j as _neo
            rr = _neo.query(
                "MATCH (p:BodhiInstance {kb_id:$kb, slug:$pol}) "
                "OPTIONAL MATCH (p)-[:`bmm:includesBusinessPolicy`*0..4]->(q:BodhiInstance {kb_id:$kb}) "
                "WITH collect(DISTINCT coalesce(q.slug, p.slug)) AS pols "
                "MATCH (r:BodhiInstance {kb_id:$kb})-[e]->(x:BodhiInstance {kb_id:$kb}) "
                "WHERE x.slug IN pols AND type(e) IN ['bmm:isDerivedFrom','bmm:isBasisFor'] "
                "RETURN DISTINCT r.slug AS slug",
                {"kb": kb, "pol": root}, timeout=8)
            slugs = [str(x.get("slug")) for x in (rr or []) if x.get("slug")]
        except Exception:                # noqa: BLE001  图不可用 ⇒ 逐条回溯（慢但可用）
            seen, queue = set(), [root]
            while queue:
                pol = queue.pop(0)
                if pol in seen:
                    continue
                seen.add(pol)
                try:
                    rel = _kg.relations_of(kb, pol) or {}
                except Exception:        # noqa: BLE001
                    rel = {}
                for e in (rel.get("in") or []):        # 规则 --isDerivedFrom--> 策略
                    if e.get("type") in ("bmm:isDerivedFrom", "bmm:isBasisFor") and e.get("source_slug"):
                        if e["source_slug"] not in slugs:
                            slugs.append(e["source_slug"])
                for e in (rel.get("out") or []):       # 策略 --includesBusinessPolicy--> 策略
                    if e.get("type") == "bmm:includesBusinessPolicy" and e.get("target_slug"):
                        queue.append(e["target_slug"])
        rules = _rule_contracts(kb, slugs, kb_name)
        resolved_by = "policy"
    elif kind == "ask":
        options = [{"kind": "policy", "slug": x["slug"], "title": x["t"]} for x in _pols()]
        resolved_by = "ask"
    elif kind == "path":
        resolved_by = "path"
        options = [{"note": "path 模式待接入 ontology_paths + walk_path（下一步）"}]
    else:
        raise ValueError("未知 spec 前缀 %r（direct:/policy:/ask/task:/path:）" % kind)

    payload = {"resolved_by": resolved_by, "spec": sp, "rules": rules}
    return {"ok": True, "kb": {"id": kb, "name": kb_name}, "resolved_by": resolved_by, "spec": sp,
            "rules": rules[:int(limit or 300)],
            "counts": {"rules": len(rules),
                       "needs_extract": sum(1 for x in rules if x.get("needs_extract"))},
            "scope_options": options,
            "hash": hashlib.sha256(_json.dumps(payload, ensure_ascii=False, sort_keys=True)
                                   .encode("utf-8")).hexdigest()[:16]}


def _rule_contracts(kb_id: str, slugs: list, kb_name: str = "") -> list:
    """规则页 → 统一契约（与 `rules_of_task` 的字段一致 ✓）。

    **图本优先**（2026-10-16 用户口径 ✓）：规则属性（`ruleImplementation` / `knowledgeNeed` /
    `enforcementLevel` / `ruleScope` / `ruleReference`）与出边（`isDerivedFrom` 等）**都在图节点上**
    （写路径 `ke_pages` → `ke_graph.upsert_node(attributes=…)` 去前缀写入 ✓），
    所以**一次 Cypher** 取全；仅当图不可用、或该 slug 在图上一条属性都没有时，才降级 PG ✓。
    """
    import ke_graph as _kg

    want = [str(s).strip() for s in (slugs or []) if str(s).strip()]
    if not want:
        return []

    g = {}
    try:
        import ke_neo4j as _neo
        for r in _neo.query(
                "MATCH (n:BodhiInstance {kb_id:$kb}) WHERE n.slug IN $slugs "
                "OPTIONAL MATCH (n)-[rel]->(m:BodhiInstance {kb_id:$kb}) "
                "RETURN n.slug AS slug, n.name AS title, n.definition AS dfn, "
                "       n.ruleImplementation AS impl, n.knowledgeNeed AS need, "
                "       n.enforcementLevel AS lvl, n.ruleScope AS scope, "
                "       n.ruleReference AS ref, n.note AS note, "
                "       collect(DISTINCT {t: type(rel), s: m.slug}) AS outs",
                {"kb": kb_id, "slugs": want}, timeout=6) or []:
            g[str(r.get("slug") or "")] = r
    except Exception:  # noqa: BLE001  图不可用 ⇒ 全部下降级 PG
        g = {}

    miss = [s for s in want
            if not (g.get(s) and (g[s].get("impl") or g[s].get("need") or g[s].get("lvl")))]
    pg = {}
    if miss:                                   # 正常应为 0 条（属性都在图上 ✓）
        for r in ke_db.psql_csv(
                "SELECT slug, COALESCE(title,'') AS title, "
                "  COALESCE(page_metadata->'ontology'->'attributes'->>'bmm:ruleImplementation','') AS impl, "
                "  COALESCE(page_metadata->'ontology'->'attributes'->>'bmm:knowledgeNeed','') AS need, "
                "  COALESCE(page_metadata->'ontology'->'attributes'->>'bmm:ruleScope','') AS scope, "
                "  COALESCE(page_metadata->'ontology'->'attributes'->>'bmm:enforcementLevel','') AS lvl, "
                "  COALESCE(page_metadata->'ontology'->'attributes'->>'bmm:ruleReference','') AS ref, "
                "  COALESCE(page_metadata->'ontology'->'attributes'->>'skos:definition','') AS dfn, "
                "  COALESCE(page_metadata->'ontology'->'attributes'->>'skos:note','') AS note "
                " FROM wiki_pages WHERE knowledge_base_id = %s AND deleted_at IS NULL AND slug IN (%s) "
                " ORDER BY slug" % (ke_db.sql_str(kb_id), ", ".join(ke_db.sql_str(s) for s in miss))):
            pg[str(r["slug"])] = r

    out = []
    for slug in sorted(want):
        r = g.get(slug) or pg.get(slug) or {}
        impl, need = str(r.get("impl") or ""), str(r.get("need") or "")
        if slug in g:
            pols = [e.get("s") for e in (r.get("outs") or [])
                    if e.get("s") and e.get("t") in ("bmm:isDerivedFrom", "bmm:isBasisFor")]
            src = "graph:BodhiInstance"
        else:
            try:
                pols = [e.get("target_slug")
                        for e in ((_kg.relations_of(kb_id, slug) or {}).get("out") or [])
                        if e.get("type") in ("bmm:isDerivedFrom", "bmm:isBasisFor")]
            except Exception:  # noqa: BLE001
                pols = []
            src = "pg:page_metadata"
        pols = [p for i, p in enumerate(pols) if p and p not in pols[:i]]
        out.append({"slug": slug, "title": str(r.get("title") or ""),
                    "definition": str(r.get("dfn") or r.get("note") or "")[:400],
                    "implementation": impl, "knowledgeNeed": need, "scope": str(r.get("scope") or ""),
                    "enforcement": str(r.get("lvl") or ""), "reference": str(r.get("ref") or ""),
                    "needs_extract": bool(need) or ("知识提取" in impl),
                    "strategies": pols,
                    "required_fields": {"conclusion": "合规｜违规｜不适用｜待澄清（逐字枚举）",
                                        "evidence": "原文证据（逐字摘录，可回溯）",
                                        "severity": "高｜中｜低（违规时必填）",
                                        "rule_ref": "本规则的 slug（出处）"},
                    "source": src})
    return out


def rules_of_task(kb_id: str = "", task: str = "", limit: int = 300) -> dict:
    """**按任务取规则契约（只读）** —— 取代"按策略取清单"的旧口径（`rules_of_policy` 只回清单、
    不带判定口径 ⇒ 智能体只能回去"看文档复原"✗）。

    数据来源（**真读图边 + 页面属性，不猜**）：
      · 任务的 `bpmn:taskExecutesRule` **图边**（`ke_graph.relations_of`）→ 规则清单；
      · 规则页属性：`bmm:ruleImplementation` / `bmm:knowledgeNeed` / `bmm:ruleScope` /
        `bmm:enforcementLevel` / `bmm:ruleReference`；`skos:definition`/`skos:note` 兜底定义；
      · 任务页自身：`skos:definition` 摘要 + `taskUsesDataObject`（输入/输出）+ `taskHasRole`（角色）；
      · 规则所属策略：规则页 `bmm:isDerivedFrom` 出边。

    回执带 **`source`（每条规则的出处，如 `graph:taskExecutesRule`）** 与 **`hash`（整包稳定指纹）**
    —— 供"**反假断言**"：改动任一条规则的属性，字段与 `hash` 必须跟着变（证明不是大模型复原 ✗）。
    """
    import hashlib
    import json as _json
    import ke_db as _db
    import ke_graph as _kg

    kb, kb_name, _note = _db.resolve_kb_id(kb_id)
    q = (task or "").strip()
    if not q:
        raise ValueError("rules_of_task 需要 task（任务页 slug 片段或标题片段）")
    def _edges(slug: str) -> list:
        try:
            r = _kg.relations_of(kb, slug)
        except Exception:  # noqa: BLE001
            return []
        return (r or {}).get("out") or []

    # ① 任务页 + 出边：**一次 Cypher**（图本优先 ✓，与 `_rule_contracts` 同口径）
    task_slug, task_title, task_dfn, task_edges = "", "", "", []
    try:
        import ke_neo4j as _neo
        rows = _neo.query(
            "MATCH (t:BodhiInstance {kb_id:$kb}) "
            "WHERE t.page_type = 'bpmn:Task' AND (t.slug CONTAINS $q OR coalesce(t.name,'') CONTAINS $q) "
            "WITH t LIMIT 1 OPTIONAL MATCH (t)-[e]->(m:BodhiInstance {kb_id:$kb}) "
            "RETURN t.slug AS slug, coalesce(t.name,'') AS nm, coalesce(t.definition,'') AS dfn, "
            "       collect(DISTINCT {t: type(e), s: m.slug, n: coalesce(m.name,'')}) AS outs",
            {"kb": kb, "q": q}, timeout=8)
        if rows and rows[0].get("slug"):
            task_slug = str(rows[0]["slug"])
            task_title = str(rows[0].get("nm") or "")
            task_dfn = str(rows[0].get("dfn") or "")
            task_edges = [e for e in (rows[0].get("outs") or []) if e.get("t")]
    except Exception:  # noqa: BLE001  图不可用 ⇒ PG 兜底
        task_edges = []

    if not task_slug:
        trows = _db.psql_csv(
            "SELECT slug, COALESCE(title,'') AS title, "
            "       COALESCE(page_metadata->'ontology'->'attributes'->>'skos:definition','') AS dfn "
            "  FROM wiki_pages WHERE knowledge_base_id = %s AND deleted_at IS NULL "
            "   AND page_type = 'bpmn:Task' AND (slug LIKE %s OR title LIKE %s) LIMIT 5"
            % (_db.sql_str(kb), _db.sql_str("%%%s%%" % q), _db.sql_str("%%%s%%" % q)))
        if not trows:
            raise ValueError("找不到任务页（bpmn:Task，slug/标题含 %r）" % q)
        task_slug, task_title = trows[0]["slug"], trows[0]["title"]
        task_dfn = trows[0]["dfn"]
        task_edges = [{"t": e.get("type"), "s": e.get("target_slug"), "n": e.get("target_title")}
                      for e in _edges(task_slug)]

    rule_slugs = [e.get("s") for e in task_edges
                  if e.get("t") == "bpmn:taskExecutesRule" and e.get("s")]
    dataobjects = [{"slug": e.get("s"), "title": e.get("n"), "type": e.get("t")}
                   for e in task_edges if e.get("t") == "bpmn:taskUsesDataObject"]
    roles = [{"slug": e.get("s"), "title": e.get("n")} for e in task_edges
             if e.get("t") == "bpmn:taskHasRole"]

    rules = _rule_contracts(kb, rule_slugs, kb_name)   # 一次 Cypher（属性 + 策略边）✓
    for x in rules:                                    # 标注"经任务边到达"（可审计 ✓）
        x["via"] = "bpmn:taskExecutesRule"
    payload = {"task": task_slug, "rules": rules}
    return {"ok": True, "kb": {"id": kb, "name": kb_name},
            "task": {"slug": task_slug, "title": task_title, "definition": task_dfn[:400],
                     "dataobjects": dataobjects, "roles": roles},
            "rules": rules[:int(limit or 300)],
            "counts": {"rules": len(rules),
                       "needs_extract": sum(1 for x in rules if x["needs_extract"]),
                       "direct_judge": sum(1 for x in rules if not x["needs_extract"])},
            "read_from": "neo4j(node attrs + edges)",
            "hash": hashlib.sha256(_json.dumps(payload, ensure_ascii=False,
                                               sort_keys=True).encode("utf-8")).hexdigest()[:16]}


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
    # **① 图实例检索（2026-10-05 全量重构：推理走图）**——命中即返回，PG 只补正文。
    try:
        import ke_neo4j as _n4  # noqa: PLC0415
        cls = _bmm_rule_class_closure() or ["bmm:OperativeBusinessRule", "bmm:StructuralBusinessRule", "bmm:BusinessRule"]
        g = _n4.query(
            "MATCH (p:BodhiInstance {kb_id:$kb, slug:$ps})-[r:`bmm:isBasisFor`]->(n:BodhiInstance) "
            "WHERE n.page_type IN $cls "
            "RETURN n.slug AS slug, n.name AS name, n.page_type AS page_type, n.enforcementLevel AS level "
            "ORDER BY n.slug LIMIT $lim",
            {"kb": kb, "ps": pol["slug"], "cls": cls, "lim": int(limit or 300)})
        if g:
            slugs = [x["slug"] for x in g if x.get("slug")]
            pc = {r["slug"]: r for r in ke_db.psql_csv(
                "SELECT slug, content, COALESCE(page_metadata::text,'{}') AS meta FROM wiki_pages "
                "WHERE knowledge_base_id = %s AND deleted_at IS NULL AND slug IN (%s)"
                % (ke_db.sql_str(kb), ", ".join(ke_db.sql_str(s) for s in slugs)))}
            out_g = []
            for x in g:
                p = pc.get(x["slug"], {})
                lvl = ENFORCEMENT_ALIAS.get(x.get("level") or "", x.get("level") or "")
                ev = re.search(r"%s\n\n> ?(.+)" % re.escape(EVIDENCE_SECTION), p.get("content") or "")
                out_g.append({"slug": x["slug"], "name": x.get("name") or x["slug"],
                              "page_type": x.get("page_type"), "level": lvl,
                              "scope": _attr(p.get("meta"), "ruleScope", "适用范围"),
                              "how": _attr(p.get("meta"), "ruleImplementation", "实现方式"),
                              "reference": _attr(p.get("meta"), "ruleReference", "参考规范"),
                              "evidence": (ev.group(1).strip()[:400] if ev else "")})
            return {"ok": True, "kb": {"id": kb, "name": kb_name},
                    "policy": {"slug": pol["slug"], "name": pol["title"]},
                    "count": len(out_g), "rules": out_g, "source": "graph", "rule_classes": cls}
    except Exception:  # noqa: BLE001  图不可用 → 退回 PG 兜底
        pass
    # **② PG 兜底（图不可用/无实例时）**
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


def _ref_fetch(url: str, timeout: float = 8.0, max_bytes: int = 200_000) -> dict:
    """抓取参考规范 URL（**内网友好**）：代理 / 超时 / 主机白名单 / **结构化失败原因**。

    用户口径（2026-10-22 · 内网问题 4.3）：内网实测 `urlopen error timed out` ✗
    —— 无代理、无超时口径、无白名单、失败只有一句 note。

    环境变量：
      · `BODHI_HTTP_PROXY`  —— http(s) 代理（内网常见；不设则用系统默认）
      · `BODHI_REF_TIMEOUT` —— 单次超时秒（默认 8）
      · `BODHI_REF_HOSTS`   —— 主机白名单（逗号分隔；空 = 不限；支持 `endswith` 匹配子域）

    返回 `{ok, text, reason, detail, http_status, elapsed_ms, attempts, host}`，
    `reason ∈ timeout｜dns｜tls｜blocked｜conn_refused｜http_4xx｜http_5xx｜empty｜other`。
    """
    import html  # noqa: PLC0415
    import os as _os  # noqa: PLC0415
    import socket  # noqa: PLC0415
    import ssl  # noqa: PLC0415
    import time  # noqa: PLC0415
    import urllib.error  # noqa: PLC0415
    import urllib.parse  # noqa: PLC0415
    import urllib.request  # noqa: PLC0415

    try:
        host = (urllib.parse.urlsplit(url).hostname or "").lower()
    except Exception:  # noqa: BLE001
        host = ""
    allow = [h.strip().lower() for h in (_os.environ.get("BODHI_REF_HOSTS") or "").split(",") if h.strip()]
    if allow and host not in allow and not any(host.endswith("." + a) for a in allow):
        return {"ok": False, "text": "", "reason": "blocked",
                "detail": "主机不在白名单（BODHI_REF_HOSTS）：%s" % (host or "?"),
                "http_status": None, "elapsed_ms": 0, "attempts": 0, "host": host}
    try:
        timeout = float(_os.environ.get("BODHI_REF_TIMEOUT") or timeout or 8)
    except (TypeError, ValueError):
        timeout = 8.0
    proxy = (_os.environ.get("BODHI_HTTP_PROXY") or "").strip()
    opener = (urllib.request.build_opener(
        urllib.request.ProxyHandler({"http": proxy, "https": proxy})) if proxy
        else urllib.request.build_opener())
    req = urllib.request.Request(url, headers={"User-Agent": "bodhi-review/1.0",
                                               "Accept": "text/html,text/plain,*/*"})
    attempts_max = 2 if timeout <= 10 else 1     # 总预算 ≤ 2×timeout，避开反代 60s
    t0 = time.time()
    last: dict = {}
    for attempt in range(1, attempts_max + 1):
        try:
            with opener.open(req, timeout=timeout) as resp:  # noqa: S310
                raw = resp.read(max_bytes)
                status = int(getattr(resp, "status", None) or 200)
            text = raw.decode("utf-8", "replace")
            text = re.sub(r"(?is)<(script|style).*?</\1>", " ", text)
            text = re.sub(r"(?s)<[^>]+>", " ", text)
            text = re.sub(r"\s+", " ", html.unescape(text)).strip()
            return {"ok": bool(text), "text": text, "reason": "" if text else "empty",
                    "detail": "" if text else "抓到了但正文为空", "http_status": status,
                    "elapsed_ms": int((time.time() - t0) * 1000), "attempts": attempt, "host": host}
        except urllib.error.HTTPError as exc:
            code = int(getattr(exc, "code", 0) or 0)
            last = {"ok": False, "text": "",
                    "reason": "http_4xx" if 400 <= code < 500 else "http_5xx",
                    "detail": "HTTP %d" % code, "http_status": code,
                    "elapsed_ms": int((time.time() - t0) * 1000), "attempts": attempt, "host": host}
            if code < 500:
                break                            # 4xx 重试无意义
        except (socket.timeout, TimeoutError) as exc:
            # 连接建立后**读超时**抛的是 `socket.timeout`（不是 URLError）⇒ 必须单独接 ✓
            last = {"ok": False, "text": "", "reason": "timeout", "detail": str(exc)[:160] or "read timeout",
                    "http_status": None, "elapsed_ms": int((time.time() - t0) * 1000),
                    "attempts": attempt, "host": host}
        except urllib.error.URLError as exc:
            why = getattr(exc, "reason", exc)
            if isinstance(why, (socket.timeout, TimeoutError)) or "timed out" in str(why).lower():
                kind = "timeout"
            elif isinstance(why, socket.gaierror):
                kind = "dns"
            elif isinstance(why, ssl.SSLError):
                kind = "tls"
            elif isinstance(why, ConnectionRefusedError) or "refused" in str(why).lower():
                kind = "conn_refused"
            else:
                kind = "other"
            last = {"ok": False, "text": "", "reason": kind, "detail": str(why)[:160],
                    "http_status": None, "elapsed_ms": int((time.time() - t0) * 1000),
                    "attempts": attempt, "host": host}
        except Exception as exc:  # noqa: BLE001
            last = {"ok": False, "text": "", "reason": "other", "detail": str(exc)[:160],
                    "http_status": None, "elapsed_ms": int((time.time() - t0) * 1000),
                    "attempts": attempt, "host": host}
            break
    return last or {"ok": False, "text": "", "reason": "other", "detail": "未知失败",
                    "http_status": None, "elapsed_ms": int((time.time() - t0) * 1000),
                    "attempts": 0, "host": host}


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
    if is_url:
        if not allow_fetch:
            out.update({"reason": "fetch_disabled",
                        "hint": ("需要抓取就传 allow_fetch=true；内网不可达时会回结构化 "
                                 "reason=timeout｜dns｜tls｜blocked｜conn_refused"),
                        "note": ("知识库里找不到同名规范，且 URL 抓取未开启（allow_fetch=false）→ "
                                 "标「参考规范不可得」，**不要编造**")})
            return out
        got = _ref_fetch(ref, timeout=float(timeout or 8))
        out.update({"found": bool(got.get("ok")), "reason": got.get("reason") or "",
                    "reason_detail": got.get("detail") or "", "host": got.get("host") or "",
                    "http_status": got.get("http_status"), "elapsed_ms": got.get("elapsed_ms"),
                    "attempts": got.get("attempts")})
        if got.get("ok"):
            out.update({"source": "url", "text": (got.get("text") or "")[:int(max_chars)],
                        "note": "从 URL 抓取并剥 HTML；判分时引用要标明来自外部规范"})
        else:
            out["note"] = ("URL 取不到（reason=%s，%s）→ 按技能口径标「参考规范不可得」，"
                           "**不要编造规范内容**；可改用规范名/文件名查本库文档，"
                           "或请用户离线提供该规范"
                           % (got.get("reason") or "other", str(got.get("detail") or "")[:120]))
        return out
    out["note"] = ("知识库里找不到同名规范 → 标「参考规范不可得」，只按规则原文判定，**不要编造**")
    return out


def review_apply(kb_id: str = "", doc: str = "", policy: str = "", findings: list | None = None,
                 page_type: str = REPORT_TYPE, actor: str = "agent:document_review",
                 policy_slug: str = "", tenant: int | None = None,
                 session_no: str = "", session_slug: str = "") -> dict:
    """把逐条评审结论写成**一页**（`slug=review/<doc>-<policy>`；版本化可回退）。

    `findings[]`：{rule, verdict(符合|不符合|不适用|无法判定), severity, scope, evidence, how, cypher, suggestion}
    2026-10-08 契约：走统一内核 `ke_pages.write_knowledge(mode="document")`——正文=报告体，
    关系→图边，**补齐会话溯源**（`session_no`/`session_slug` 由服务端解析后传入）。
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
    body = ["## 结论汇总", "", "| 结论 | 条数 |", "|---|---|"]
    for key in ("不符合", "符合", "不适用", "无法判定"):
        if verdicts.get(key):
            body.append("| %s | %d |" % (key, verdicts[key]))
    for key, value in verdicts.items():
        if key not in ("不符合", "符合", "不适用", "无法判定"):
            body.append("| %s | %d |" % (key, value))
    body += ["", "## 逐条结论", "", "| 规则 | 结论 | 严重度 | 范围 | 方式 |", "|---|---|---|---|---|"]
    for f in items:
        body.append("| %s | %s | %s | %s | %s |"
                    % (f.get("rule") or "", f.get("verdict") or "", f.get("severity") or "",
                       f.get("scope") or "", f.get("how") or ""))
    body += ["", "## 结论明细", ""]
    for f in items:
        body += ["### %s" % (f.get("rule") or "（未命名规则）"), "",
                 "- 结论：**%s**（严重度 %s；方式 %s；范围 %s）"
                 % (f.get("verdict") or "", f.get("severity") or "-", f.get("how") or "-",
                    f.get("scope") or "-")]
        if f.get("cypher"):
            body.append("- 图检索语句：`%s`" % str(f["cypher"]).replace("`", "'")[:400])
        if f.get("suggestion"):
            body.append("- 建议：%s" % f["suggestion"])
        quote = str(f.get("evidence") or "").strip() or "（该条未给逐字证据）"
        body += ["", "#### 证据（逐字）", "", "> %s" % quote.replace("\n", "\n> "), ""]
    quotes = [str(f.get("evidence") or "").strip() for f in items if str(f.get("evidence") or "").strip()]
    body += [EVIDENCE_SECTION, ""] + (["> %s" % q.replace("\n", "\n> ") for q in quotes]
                                      or ["> （本次评审未附逐字证据）"]) + [""]
    # 统一内核（契约 §1/§2 · M-document）：正文=报告体；关系→图边；**补齐会话溯源**。
    rels = ([{"type": "bmm:promotesDirective", "target_slug": policy_slug}] if policy_slug else [])
    spec = {
        "mode": "document", "slug": slug, "title": title, "page_type": page_type,
        "type_label": "评估", "generated_by": "文档评审技能（%s，按策略 %s）" % (REVIEW_TAG, policy),
        "wiki_content": "\n".join(body),
        "attributes": {"rules": len(items), "verdicts": verdicts},
        "relations": rels,
        "summary": "按「%s」评审「%s」：%s" % (policy, doc,
                                            "、".join("%s%d条" % (k, v) for k, v in verdicts.items())),
        "source": {"doc_title": doc, "doc_refs": [doc],
                   "source_text": (quotes[0] if quotes else "（本次评审未附逐字证据）"),
                   "session_no": session_no, "session_slug": session_slug},
        "metadata_extra": {"review": {"doc": doc, "policy": policy, "policy_slug": policy_slug,
                                      "rules": len(items), "verdicts": verdicts,
                                      "generator": REVIEW_TAG}},
    }
    written = ke_pages.write_knowledge(kb, spec, strict_source=True)
    return {"ok": True, "kb": {"id": kb, "name": kb_name}, "slug": slug, "title": title,
            "rules": len(items), "verdicts": verdicts, "version": written.get("version"),
            "graph": written.get("graph"), "warnings": written.get("warnings"),
            "policy_slug": policy_slug, "permission": acl.get("mode"),
            "next": "跑 audit_scan 让结论页也满足 F3（本页已带原文依据）"}

