"""knowledge_lookup —— **一个检索入口，三种模式**（只读 ✓）

| mode | 用途 | 收什么 | 出什么 | 取数 |
|---|---|---|---|---|
| `rules` | 取规则契约（**按步骤解析**） | `spec`（direct:/policy:/ask/task:） | 规则契约 + hash ✓ | 图 ✓（复用 `rules_resolve`） |
| `keyword` | 按关键词找知识 | `query` | 命中列表（title/**definition 全量**/attributes/version）+ **来源标注** | **图优先 ✓ → PG 收窄融合 ✓** |
| `path` | 沿关系多跳 | `start`（slug）+ `depth` | 路径（节点链 + 关系链） | 图 ✓ |

设计口径（与 `docs/graph-first-read-contract.md` 一致 ✓）：
- 图先查 ✓（实测 5–15 ms ✓），PG 只做**补字段/收窄** ✓，每条命中都带 `source`
  （`graph` / `graph+pg` / `pg` ✓）⇒ 一旦大量 `pg` ⇒ 说明图缺数据 ✗ ⇒ 跑 W10/rebuild ✓。
"""

import json
import os
import re
import time

_PUNCT = re.compile(r"[\s\u3000·、，,。.（）()【】\[\]:：;；\-—_/]+")


def _norm(text: str) -> str:
    """归一化：小写 + 去空白/标点（中文标签匹配用）。"""
    return _PUNCT.sub("", (text or "").lower())


def _parse_query(query: str) -> dict:
    """query 语法：`a b`=AND｜`a|b`=OR（同一词位）｜`"短语"`｜`-排除`。"""
    raw = (query or "").strip()
    phrases = [m.group(1) for m in re.finditer(r'"([^"]+)"', raw) if m.group(1).strip()]
    rest = re.sub(r'"[^"]+"', " ", raw)
    words, ors, excludes = [], [], []
    for tok in re.split(r"\s+", rest):
        tok = tok.strip()
        if not tok:
            continue
        if tok.startswith("-") and len(tok) > 1:
            excludes.append(tok[1:])
        elif "|" in tok:
            parts = [t for t in tok.split("|") if t.strip()]
            if parts:
                ors.append(parts)
        else:
            words.append(tok)
    return {"phrases": phrases, "words": words, "ors": ors, "excludes": excludes}


def _class_candidates(term: str, idx: dict | None = None, top: int = 3) -> list:
    """把**中文/英文词面**模糊匹配到本体类（不依赖别名数据，靠 label/本地名/前缀名）。

    评分：完全相等 100｜term ⊂ label 90−长度差｜label ⊂ term 70−长度差｜命中本地名 60。
    返回 `[(score, prefixed, label)]`（降序，最多 `top` 个）。
    """
    key = _norm(term)
    if not key or len(key) < 2:
        return []
    try:
        import ke_ontology  # noqa: PLC0415
        # ⚠️ 用 `class_meta()`（prefixed → {label, parents, ...}），不是 `classes()`
        # —— 后者回的是 `{source, classes:[…]}`（2026-10-22 实测 ✗ 踩过）
        idx = idx if idx is not None else ke_ontology.class_meta()
    except Exception:  # noqa: BLE001  本体不可用 ⇒ 不做类识别（纯文本检索）
        return []
    out = []
    for name, meta in idx.items():
        label = _norm(meta.get("label") or "")
        local = _norm(name.split(":")[-1])
        key_ok = key in (label, local, _norm(name))
        score, strong = 0, False
        if key_ok:
            score, strong = 100, True
        elif label and key in label:
            # **强证据**：词是标签的**词尾**（"流程"⊂"业务流程"✓）或占比 ≥75%；
            # 否则只是"前缀修饰"（"方案"⊂"方案报告" ✗）⇒ 弱证据，不自动圈范围
            if label.endswith(key) or len(key) * 4 >= len(label) * 3:
                score, strong = 90 - min(len(label) - len(key), 20), True
            else:
                score, strong = 55, False
        elif label and label in key:
            score, strong = 70 - min(len(key) - len(label), 20), True
        elif key in local:
            score, strong = 60, True
        if score > 0:
            out.append((score, name, meta.get("label") or name, strong))
    out.sort(key=lambda x: (-x[0], x[1]))
    return out[:top]


def _is_ancestor(a: str, b: str) -> bool:
    """`a` 是否为 `b` 的祖先（同族候选不算歧义）。"""
    try:
        import ke_ontology  # noqa: PLC0415
        return a in ke_ontology.ancestors(b) or b in ke_ontology.ancestors(a)
    except Exception:  # noqa: BLE001
        return False


def _scope_types(scope_classes: list) -> list:
    """类范围 = 该类 + 全部后代（"查流程"要连子类实例一起给 ✓）。"""
    types: set = set()
    try:
        import ke_ontology  # noqa: PLC0415
        for c in scope_classes:
            types.add(c)
            for d in ke_ontology.descendants(c) or []:
                types.add(d)
    except Exception:  # noqa: BLE001
        types = set(scope_classes)
    return sorted(t for t in types if t)


def _kb_names() -> dict:
    try:
        import ke_db as _db  # noqa: PLC0415
        return {k["id"]: k["name"] for k in _db._kb_rows()}
    except Exception:  # noqa: BLE001
        return {}


def _graph_search(kb: str, terms: list, types: list | None = None, limit: int = 20,
                  any_term: bool = False, excludes: list | None = None) -> list:
    """图内检索：`name` / **`articleBody`**（兼容旧键 `wiki_content`）多词 + 类型范围。

    `any_term=True` ⇒ OR（AND 空时的**一次内降级**，不让智能体换词重试 ✓）。
    """
    try:
        import ke_neo4j as _neo  # noqa: PLC0415
    except Exception:  # noqa: BLE001
        return []
    params: dict = {"kb": kb, "lim": max(1, int(limit)) * 2}
    where = []
    for i, t in enumerate(terms or []):
        k = "t%d" % i
        params[k] = str(t)
        where.append("(coalesce(n.name,'') CONTAINS $%s OR coalesce(n.articleBody,'') CONTAINS $%s"
                     " OR coalesce(n.wiki_content,'') CONTAINS $%s)" % (k, k, k))
    cond = ((" OR " if any_term else " AND ").join(where)) if where else "true"
    if types:
        params["types"] = list(types)
        cond = "(%s) AND n.page_type IN $types" % cond
    for j, x in enumerate(excludes or []):
        k = "x%d" % j
        params[k] = str(x)
        cond += (" AND NOT (coalesce(n.articleBody,'') CONTAINS $%s"
                 " OR coalesce(n.wiki_content,'') CONTAINS $%s)" % (k, k))
    cypher = ("MATCH (n:BodhiInstance {kb_id:$kb}) WHERE %s "
              "RETURN n.slug AS slug, coalesce(n.name,'') AS name, coalesce(n.page_type,'') AS pt, "
              "       coalesce(n.articleBody, n.wiki_content, '') AS body LIMIT $lim" % cond)
    try:
        return [dict(r) for r in (_neo.query(cypher, params, timeout=10) or [])]
    except Exception:  # noqa: BLE001  图不可用 ⇒ 调用方退回 PG
        return []


def _pg_by_slugs(kb: str, slugs: list) -> dict:
    """PG 补字段（标题/版本/属性）；**必须** `deleted_at IS NULL`（B7 部分索引口径 ✓）。"""
    if not slugs:
        return {}
    try:
        import ke_db as _db  # noqa: PLC0415
    except Exception:  # noqa: BLE001
        return {}
    arr = ",".join(_db.sql_str(s) for s in list(slugs)[:200])
    try:
        rows = _db.psql_csv(
            "SELECT slug, COALESCE(title,'') AS t, COALESCE(page_type,'') AS pt, "
            "       COALESCE(version,1) AS v, COALESCE(summary,'') AS s, "
            "       COALESCE(page_metadata->'ontology'->'attributes','{}'::jsonb)::text AS attrs "
            "  FROM wiki_pages WHERE knowledge_base_id = %s AND deleted_at IS NULL "
            "   AND slug = ANY(ARRAY[%s])" % (_db.sql_str(kb), arr))
    except Exception:  # noqa: BLE001
        return {}
    out = {}
    for r in rows:
        try:
            attrs = json.loads(r.get("attrs") or "{}") or {}
        except Exception:  # noqa: BLE001
            attrs = {}
        out[r["slug"]] = {
            "title": r.get("t") or "", "page_type": r.get("pt") or "",
            "version": int(r.get("v") or 1),
            "summary": (r.get("s") or "")[:200],
            "summary_truncated": len(r.get("s") or "") > 200,
            "attributes": attrs,
            "definition": str(attrs.get("skos:definition") or attrs.get("definition") or ""),
        }
    return out


def _snippet(body: str, terms: list, width: int = 60) -> str:
    """命中片段（来自**图里的 articleBody**，不再用截断 summary ✗）。"""
    text = re.sub(r"\s+", " ", body or "").strip()
    if not text:
        return ""
    low = text.lower()
    for t in terms or []:
        i = low.find(str(t).lower())
        if i >= 0:
            start = max(0, i - width // 2)
            return ("…" if start else "") + text[start:start + width * 2] + ("…" if start + width * 2 < len(text) else "")
    return text[:width * 2] + ("…" if len(text) > width * 2 else "")


def _cross_kb(types: list, kb: str) -> dict:
    """该类型在本库没有、但在**别的库**有 ⇒ 直接给跨库提示（省一次来回 ✓）。"""
    if not types:
        return {}
    try:
        import ke_db as _db  # noqa: PLC0415
        arr = ",".join(_db.sql_str(t) for t in types)
        rows = _db.psql_csv(
            "SELECT knowledge_base_id AS kb, count(*) AS n FROM wiki_pages "
            " WHERE deleted_at IS NULL AND page_type = ANY(ARRAY[%s]) AND knowledge_base_id <> %s "
            " GROUP BY 1 ORDER BY n DESC LIMIT 3" % (arr, _db.sql_str(kb)))
    except Exception:  # noqa: BLE001
        return {}
    if not rows:
        return {}
    names = _kb_names()
    return {"types": types[:6],
            "exists_in": [{"kb_id": r["kb"], "kb_name": names.get(r["kb"], ""), "pages": int(r["n"])}
                          for r in rows],
            "why": "本库没有这些类型的实例；它们在本会话的其它库里有（换 kb_id 再查 ✓）"}


def _lookup_impl(kb_id: str = "", mode: str = "rules", query: str = "",
                 spec: str = "", start: str = "", depth: int = 2,
                 limit: int = 20, cls: str = "", page_type: str = "",
                 kb_ids: list | None = None, auto_widen: bool = False) -> dict:
    import ke_db as _db
    import ke_review as _rv

    kb, kb_name, note = _db.resolve_kb_id(kb_id)
    m = (mode or "rules").strip().lower()
    base = {"ok": True, "mode": m, "kb": {"id": kb, "name": kb_name}}

    # ── ① rules：按步骤解析规则（唯一规则入口 ✓）────────────────────────
    if m == "rules":
        r = _rv.rules_resolve(kb, spec or query, int(limit) or 300)
        return {**base, "resolved_by": r.get("resolved_by"), "spec": r.get("spec"),
                "rules": r.get("rules"), "counts": r.get("counts"),
                "scope_options": r.get("scope_options"), "hash": r.get("hash"),
                "source": "graph:BodhiInstance"}

    # ── ② keyword：**类范围 + 多词**，图优先（`articleBody` 全文）→ PG 补字段 ─────
    if m == "keyword":
        q = (query or "").strip()
        parsed = _parse_query(q)
        lim = max(1, int(limit or 20))
        terms = parsed["phrases"] + parsed["words"]
        try:
            import ke_ontology as _ont  # noqa: PLC0415
            idx = _ont.class_meta()                 # prefixed → {label, parents, …}
        except Exception:  # noqa: BLE001
            idx = {}
        scope, cands, ambiguity, text_terms = [], [], False, []
        explicit = [c.strip() for c in (cls or "").split(",") if c.strip()]
        scope_by = "explicit" if explicit else "none"
        if explicit:
            scope = list(explicit)
        for t in ([] if explicit else terms):
            cc = _class_candidates(t, idx)
            strong_cc = [c for c in cc if c[3]]
            if not strong_cc:
                if cc:                    # 弱证据 ⇒ 只进候选提示，**不自动圈范围**（"方案" ≠ 报告类）
                    cands.extend({"class": c[1], "label": c[2], "score": c[0], "strong": False}
                                 for c in cc[:2])
                text_terms.append(t)
                continue
            top = strong_cc[0]
            if len(strong_cc) > 1 and (top[0] - strong_cc[1][0]) < 10 \
                    and not _is_ancestor(top[1], strong_cc[1][1]):
                ambiguity = True                      # 多候选且互不同族 ⇒ **不静默选错**
                cands = [{"class": c[1], "label": c[2], "score": c[0], "strong": True}
                         for c in strong_cc]
                text_terms.append(t)
                continue
            if top[1] not in scope:
                scope.append(top[1])
                if scope_by == "none":
                    scope_by = "fuzzy_label"
        types = _scope_types(scope)
        if page_type:
            types = sorted(set(types) | {page_type})
        rows = (_graph_search(kb, text_terms, types or None, lim, excludes=parsed["excludes"])
                if (text_terms or types) else [])
        relaxed_by = ""
        if not rows and len(text_terms) > 1:      # AND 空 ⇒ **一次内降级 OR**（不让智能体换词重试 ✓）
            rows = _graph_search(kb, text_terms, types or None, lim, any_term=True,
                                 excludes=parsed["excludes"])
            relaxed_by = "and→or" if rows else ""
        items = []
        for r in rows:
            slug = str(r.get("slug") or "")
            if not slug:
                continue
            items.append({"slug": slug, "title": r.get("name") or "",
                          "page_type": r.get("pt") or "", "source": "graph",
                          "snippet": _snippet(r.get("body") or "", text_terms),
                          "matched_fields": ["name/articleBody"]})
        if items:                                 # PG 只补字段（属性/版本/定义）
            extra = _pg_by_slugs(kb, [h["slug"] for h in items][:lim])
            for h in items:
                e = extra.get(h["slug"])
                if e:
                    h.update(e)
                    h["source"] = "graph+pg"
        if not items and text_terms:              # 图不可用/图缺正文 ⇒ PG 兜底
            like = text_terms[0]
            try:
                for r in _db.psql_csv(
                        "SELECT slug, COALESCE(title,'') AS t, COALESCE(page_type,'') AS pt, "
                        "       COALESCE(version,1) AS v FROM wiki_pages "
                        " WHERE knowledge_base_id = %s AND deleted_at IS NULL "
                        "   AND (COALESCE(title,'') ILIKE %s OR slug ILIKE %s) LIMIT %d"
                        % (_db.sql_str(kb), _db.sql_str("%" + like + "%"),
                           _db.sql_str("%" + like + "%"), lim)):
                    items.append({"slug": r["slug"], "title": r.get("t") or "",
                                  "page_type": r.get("pt") or "", "version": int(r.get("v") or 1),
                                  "source": "pg"})
            except Exception:  # noqa: BLE001
                pass
        items = items[:lim]
        plan = {"mode": "keyword", "scope_classes": scope, "scope_by": scope_by,
                "class_candidates": cands, "ambiguity": ambiguity, "text_terms": text_terms,
                "attr_terms": [], "excludes": parsed["excludes"],
                "page_types": types[:20], "relaxed_by": relaxed_by}
        by_src: dict = {}
        for h in items:
            s = h.get("source") or "?"
            by_src[s] = by_src.get(s, 0) + 1
        suggestions, cross = [], {}
        if not items:                             # 空结果 ⇒ 诊断 + **可执行**建议（省来回 ✓）
            cross = _cross_kb(types, kb) if types else {}
            kb_arg = kb_id or kb
            base_args = {"kb_id": kb_arg, "mode": "keyword", "query": q}
            for c in scope[:2]:
                for p in ((idx.get(c) or {}).get("parents") or [])[:2]:
                    suggestions.append({"tool": "knowledge_lookup",
                                        "args": {**base_args, "cls": p},
                                        "why": "本类 0 命中 ⇒ 用父类 %s 扩大范围" % p})
            if scope:
                suggestions.append({"tool": "knowledge_lookup",
                                    "args": {"kb_id": kb_arg, "mode": "instances", "cls": scope[0]},
                                    "why": "列该类全部实例（去掉文本条件）"})
            if len(text_terms) > 1:
                suggestions.append({"tool": "knowledge_lookup",
                                    "args": {**base_args, "query": " ".join(text_terms[:2])},
                                    "why": "只留最关键的 2 个词（AND 过窄）"})
            if cross.get("exists_in"):
                kb0 = cross["exists_in"][0]
                suggestions.append({"tool": "knowledge_lookup",
                                    "args": {"kb_id": kb0["kb_id"], "mode": "keyword", "query": q},
                                    "why": "该类型在 %s 有 %s 页"
                                           % (kb0.get("kb_name") or kb0["kb_id"], kb0.get("pages"))})
            suggestions.append({"tool": "knowledge_lookup",
                                "args": {"kb_id": kb_arg, "mode": "rules", "spec": "ask"},
                                "why": "若查的是策略/规则：先列全部策略（spec=ask），再按策略名取规则"})
            if not scope and len(text_terms) == 1:
                short = text_terms[0][:2]
                if short and short != text_terms[0]:
                    suggestions.append({"tool": "knowledge_lookup",
                                        "args": {"kb_id": kb_arg, "mode": "keyword", "query": short},
                                        "why": "把词改短到 2 字再试（长词可能载库里不存在）"})
                suggestions.append({"tool": "metric_compute",
                                    "args": {"kb_id": kb_arg},
                                    "why": "先看该库的类型分布/页数，确认库里有什么再查（避免空转）"})
        out = {**base, "query": q, "counts": {"hits": len(items), "by_source": by_src},
               "hits": items, "plan": plan,
               "strategy": {"used": (["graph"] if by_src.get("graph") or by_src.get("graph+pg") else [])
                                     + (["pg"] if by_src.get("pg") or by_src.get("graph+pg") else []),
                            "scope_by": scope_by, "index_used": "none",
                            "why": ("按本体类定范围 + 多词检索" if scope else "纯文本检索")
                                   + ("；AND 空 ⇒ 已自动降级 OR" if relaxed_by else "")},
               "suggestions": suggestions,
               "note": ("命中片段来自**图里的 articleBody**；推理请用 `attributes`/`definition` ✓"
                        + ("；AND 过窄已自动放宽为 OR" if relaxed_by else ""))}
        if cross:
            out["cross_kb_hint"] = cross
        if ambiguity:
            out["next"] = "词面同时匹配多个本体类 ⇒ 用 `cls=` 明确一个（见 plan.class_candidates）"
        elif suggestions:
            out["next"] = suggestions[0].get("why", "")
        elif not text_terms and not scope:
            out["next"] = "没有可检索条件：请给 query（关键词/短语）或 cls（本体类）"
        return out

    # ── ⑤ instances：列某类全部实例（"没有其它关键字就列全部" ✓）───────────────
    if m == "instances":
        target = (cls or "").strip()
        if not target and query:
            cc_all = _class_candidates(query)
            cc = [c for c in cc_all if c[3]] or cc_all
            target = cc[0][1] if cc else ""
        if not target:
            raise ValueError("instances 模式需要 cls（本体类），或 query 能识别出类")
        types = _scope_types([target])
        rows = _graph_search(kb, [], types, max(1, int(limit or 50)))
        items = [{"slug": r.get("slug"), "title": r.get("name") or "",
                  "page_type": r.get("pt") or "", "source": "graph"} for r in rows if r.get("slug")]
        return {**base, "cls": target, "types": types, "counts": {"hits": len(items)},
                "hits": items,
                "plan": {"mode": "instances", "scope_classes": [target], "scope_by": "explicit",
                         "page_types": types[:20]},
                "strategy": {"used": ["graph"], "scope_by": "explicit", "why": "按类型列实例"}}

    # ── ④ route：**类型路径模板**（"流程 活动 任务" ⇒ 关系链，不做 AND ✗）────────
    if m == "route":
        parsed = _parse_query(query)
        seq: list = []
        for t in parsed["phrases"] + parsed["words"]:
            cc_all = _class_candidates(t)
            cc = [c for c in cc_all if c[3]] or cc_all
            if cc and cc[0][1] not in seq:
                seq.append(cc[0][1])
        explicit2 = [c.strip() for c in (cls or "").split(",") if c.strip()]
        if explicit2:
            seq = explicit2
        if len(seq) < 2:
            return {**base, "ok": False,
                    "error": "route 需要能识别出 ≥2 个本体类（如「流程 活动 任务」）",
                    "recognized": seq, "hint": "也可显式传 cls=\"A,B,C\"（按顺序）"}
        import ke_ontology as _ont2  # noqa: PLC0415
        legs = []
        for a, b in zip(seq, seq[1:]):
            p = _ont2.ontology_paths(a, b, max_hops=2, limit=8)
            legs.append({"from": a, "to": b, "paths": (p.get("paths") or [])[:3],
                         "hint": p.get("hint", "")})
        recipe = [{"step": 1, "tool": "knowledge_lookup",
                   "args": {"kb_id": kb_id or kb, "mode": "instances", "cls": seq[0]},
                   "why": "先取起点类的实例"}]
        for leg in legs:
            for pth in leg["paths"][:1]:
                recipe.append({"step": len(recipe) + 1, "relation": pth.get("seq"),
                               "why": "沿 %s 取下游：%s" % ("、".join(pth.get("seq") or []),
                                                          pth.get("display", ""))})
        return {**base, "sequence": seq, "legs": legs, "recipe": recipe,
                "plan": {"mode": "route", "scope_classes": seq, "scope_by": "fuzzy_label",
                         "path_template": [leg["paths"][0] if leg["paths"] else {} for leg in legs]},
                "strategy": {"used": ["ontology"], "scope_by": "fuzzy_label",
                             "why": "类型路径（这些词是**类**，不是 AND 关键词 ✓）"},
                "suggestions": [],
                "next": (recipe[1]["why"] if len(recipe) > 1
                         else "没有 ≤2 跳通路：先确认类名或用 cls= 显式给（见 legs[].hint）"),
                "note": "按类型路径检索：先取起点实例，再沿关系取下游；**不要把它们当 AND 关键词** ✗"}

    # ── ⑦ semantic：向量检索（W2.5，未配置时给结构化说明，不静默 ✗）─────────────
    if m == "semantic":
        return {**base, "ok": False, "reason": "not_configured",
                "strategy": {"used": [], "why": "向量检索未配置（W2.5：PG pgvector 页面级向量）"},
                "hint": ("当前请用 mode=keyword（图 articleBody 全文 + 类范围）；"
                         "配好 BODHI_EMBED_* 与 pgvector 后再用 semantic")}

    # ── ③ path：沿关系多跳（起点必须给 slug ✓）─────────────────────────
    if m == "path":
        s = (start or "").strip()
        if not s:
            raise ValueError("path 模式需要 start（起始 slug）")
        d = max(1, min(int(depth or 2), 4))
        try:
            import ke_neo4j as _neo
            rows = _neo.query(
                "MATCH p=(a:BodhiInstance {kb_id:$kb, slug:$s})-[*1..%d]->(b:BodhiInstance {kb_id:$kb}) "
                "RETURN [x IN nodes(p) | x.slug] AS nodes, "
                "       [r IN relationships(p) | type(r)] AS rels LIMIT $lim" % d,
                {"kb": kb, "s": s, "lim": max(1, int(limit or 20))}, timeout=10)
        except Exception as exc:  # noqa: BLE001
            return {**base, "ok": False, "error": "graph_unavailable", "detail": str(exc)[:200]}
        paths = []
        for r in rows or []:
            nodes = [str(x) for x in (r.get("nodes") or [])]
            rels = [str(x) for x in (r.get("rels") or [])]
            paths.append({"nodes": nodes, "rels": rels, "len": len(rels)})
        paths.sort(key=lambda x: x["len"])
        return {**base, "start": s, "depth": d, "counts": {"paths": len(paths)},
                "paths": paths, "source": "graph:path"}
        # JSON 便于外部复算 hash ✓（调用方可自行序列化）

    raise ValueError("mode 只能是 rules / keyword / instances / route / path / semantic（收到 %r）" % mode)


# ---------------------------------------------------------------------------
# 对外入口（2026-10-24 需求 2）：**本体提示开关 + 调用遥测**
# ---------------------------------------------------------------------------
# 目的：可对比"是否用本体信息规划查询"对智能体检索成败的影响。
#   · `ontology_hint=True`（默认 / env `BODHI_ONTOLOGY_HINT=on`）：
#       回执带 `plan` / `strategy` / `suggestions` / `next` / `cross_kb_hint`
#       —— 这就是"提示智能体用本体规划查询"的能力面；
#   · `ontology_hint=False`（对照组）：**剥掉**上述提示，只回 `hits/counts/kb/query`
#       —— 模拟"没有本体提示、只能看原始命中"的基线；
#   · 两种情况下都回 `telemetry`（variant / 会话 / 原问题 / 命中数 / 耗时），
#     供 `logs/lookup_ab_*.jsonl` 与 `ke_ab_report.py` 统计。
_HINT_KEYS = ("plan", "strategy", "suggestions", "next", "cross_kb_hint")


def _hint_enabled(ontology_hint=None) -> bool:
    """开关解析：显式布尔 > env `BODHI_ONTOLOGY_HINT`（默认 on）。"""
    if isinstance(ontology_hint, bool):
        return ontology_hint
    raw = (os.environ.get("BODHI_ONTOLOGY_HINT") or "on").strip().lower()
    return raw not in ("0", "false", "off", "no", "n")


_QKEY_PUNCT = re.compile(r"[\s\u3000·、，,。.（）()【】\[\]:：;；\-—_/？?！!…\"'“”‘’]+")


def _question_key(text: str) -> str:
    """问题归一化键（同类问题聚类用）：小写 + 去标点/空白（含全角 ？ ！）。"""
    return _QKEY_PUNCT.sub("", (text or "").lower())[:80]


def knowledge_lookup(kb_id: str = "", mode: str = "rules", query: str = "",
                     spec: str = "", start: str = "", depth: int = 2,
                     limit: int = 20, cls: str = "", page_type: str = "",
                     kb_ids: list | None = None, auto_widen: bool = False,
                     ontology_hint: bool | None = None, session_no: str = "",
                     question: str = "") -> dict:
    """**统一检索入口**（原实现 + 本体提示开关 + 遥测）。

    向后兼容：前 11 个参数与旧签名完全一致 ✓（旧调用方无需改动）。
    `session_no` / `question` **只进遥测**，不参与检索（检索仍用 `query`）。
    """
    t0 = time.time()
    hint = _hint_enabled(ontology_hint)
    r = _lookup_impl(kb_id, mode, query, spec, start, depth, limit, cls, page_type,
                     kb_ids, auto_widen)
    variant = "hint" if hint else "plain"
    if not hint:
        for key in _HINT_KEYS:
            r.pop(key, None)
    counts = r.get("counts") or {}
    hits = counts.get("hits")
    if hits is None:
        hits = counts.get("paths")
    if hits is None:
        # **route 模式没有 hits/paths**：以 `recipe` 步数计"可用的出路"（否则恒 0 ✗）
        hits = (len(r.get("hits") or []) or len(r.get("rules") or [])
                or len(r.get("paths") or []) or len(r.get("recipe") or []))
    top = ""
    first = (r.get("hits") or [{}])[0] if isinstance(r.get("hits"), list) else {}
    if isinstance(first, dict):
        top = str(first.get("slug") or "")
    plan = r.get("plan") or {}
    r["telemetry"] = {
        "variant": variant, "ontology_hint": hint,
        "session_no": str(session_no or ""), "question": str(question or ""),
        "question_key": _question_key(question or query or spec),
        "query": str(query or spec or "")[:120], "mode": r.get("mode") or mode,
        "hits": int(hits or 0), "top_slug": top,
        "relaxed_by": str(plan.get("relaxed_by") or ""),
        "ambiguity": bool(plan.get("ambiguity")), "scope_by": str(plan.get("scope_by") or ""),
        "ms": int((time.time() - t0) * 1000),
        "assignment": "manual",
    }
    return r

