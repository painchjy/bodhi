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

try:                                     # IO 统计（2026-10-25 A/B 口径 v2；缺失则退化为"不统计"）
    import ke_stats
except Exception:  # noqa: BLE001
    class ke_stats:                      # type: ignore[no-redef]
        @staticmethod
        def enabled() -> bool:
            return False

        @staticmethod
        def begin() -> dict:
            return {}

        @staticmethod
        def snapshot():
            return {}

        @staticmethod
        def reset() -> None:
            return None


_PUNCT = re.compile(r"[\s\u3000·、，,。.（）()【】\[\]:：;；\-—_/]+")

# ---------------------------------------------------------------------------
# **版本指纹**（2026-10-24，A/B 的"程序版本"维度）
#   · `ALGO_VERSION` —— **人工语义位**：检索口径/排序/放宽策略变了才 bump；
#   · `CODE_REV`     —— **自动指纹**：本文件文本的 sha1 前 8 位（任何改动即变 ⇒ 改了忘 bump 也能区分 ✓）；
#   ⇒ 报告按 `lookup_version` 分组，避免"程序改过却混在一起比" ✗
# ---------------------------------------------------------------------------
ALGO_VERSION = "v2"


def _code_rev() -> str:
    try:
        import hashlib
        import pathlib

        return hashlib.sha1(pathlib.Path(__file__).read_bytes()).hexdigest()[:8]
    except Exception:  # noqa: BLE001  取不到指纹也不能让检索失败
        return "unknown"


CODE_REV = _code_rev()
LOOKUP_VERSION = "%s-%s" % (ALGO_VERSION, CODE_REV)


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


def _q_category(mode: str, query: str = "", spec: str = "") -> str:
    """**问题种类**（A/B 的分组维度）—— **只看查询形态，不看结果** ✓

    为什么不看结果：用"有没有命中"反推问题类型是**循环论证** ✗（结论会自己证明自己）。
    这里只按 `mode` + query 的**语法形态**（单词/多词/短语/OR）给一个粗粒度标签；
    问题集里写了 `category` 的（已知 14 题）在报告侧优先用问题集口径 ✓
    """
    m = (mode or "").strip().lower()
    if m == "instances":
        return "实例"
    if m in ("route", "path"):
        return "路径"
    if m == "rules":
        return "规则"
    if m == "semantic":
        return "语义"
    if m == "keyword":
        p = _parse_query(query)
        n = len(p.get("words") or []) + len(p.get("phrases") or []) + len(p.get("ors") or [])
        if n <= 0:
            return "空查询"
        return "多词" if n > 1 else "单词"
    return "未分类"


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


def _lcs_len(a: str, b: str) -> int:
    """最长公共子串长度（两行 DP；两侧各截 240 字，短问句足够）。"""
    a, b = (a or "")[:240], (b or "")[:240]
    if not a or not b:
        return 0
    best, prev = 0, [0] * (len(b) + 1)
    for i in range(1, len(a) + 1):
        cur, ai = [0] * (len(b) + 1), a[i - 1]
        for j in range(1, len(b) + 1):
            if ai == b[j - 1]:
                cur[j] = prev[j - 1] + 1
                if cur[j] > best:
                    best = cur[j]
        prev = cur
    return best


def _name_hits(text: str, items: list, key: str = "name", top: int = 2) -> list:
    """「文本里提到了哪个名字」—— **只比名字，不写死任何业务名** ✓

    判定：归一化后最长公共子串 ≥3 字；或名字**头 2 字**命中（短问句常见，如只提两个字）。
    返回按匹配长度降序的前 `top` 项（元素原样透传）。
    """
    core = _norm(text)
    if not core:
        return []
    scored = []
    for it in (items or []):
        name = str((it or {}).get(key) or "")
        k = _norm(name)
        if len(k) < 2:
            continue
        n = _lcs_len(core, k)
        if n >= 3 or (n == 2 and k.startswith(core[:2])):
            scored.append((n, len(k), it))
    scored.sort(key=lambda x: (-x[0], -x[1]))
    return [it for _n, _l, it in scored[:max(1, int(top))]]


# rules 模式的合法前缀 / 各路线的一句话口径（提示面用，纯类型与关系，无业务名词 ✓）
_SPEC_KINDS = ("direct", "policy", "influencer", "ask", "path", "task")
_ROUTE_WHY = {
    "direct": "按给定规则 slug 直接取（不展开策略）",
    "policy": "策略闭包（含子策略）+ 双向规则边（规则↔策略）",
    "influencer": "影响因素 → 影响手段 → 策略闭包 → 规则（双向边）",
    "ask": "只回策略清单（不取规则）",
    "path": "预留（多路径并集）",
    "task": "已废弃（任务边已删除）",
}


def _rules_guide(base: dict, kb: str, text: str) -> dict:
    """rules 模式**没给 spec**（或给的不是 spec）⇒ 回可执行路线，**不抛错** ✓

    历史踩坑（2026-10-23 实测）：把问题当 spec 传进来会抛「未知 spec 前缀」，
    智能体只能换着试 ⇒ 一次问答烧 20 步。这里改成"给下一步的精确参数"。
    """
    import ke_review as _rv  # noqa: PLC0415
    q = (text or "").strip()
    sug = []
    for x in _name_hits(q, _rv._influencers(kb), "name", top=1):
        sug.append({"tool": "knowledge_lookup",
                    "args": {"kb_id": kb, "mode": "rules",
                             "spec": "influencer:" + str(x.get("name") or "")},
                    "why": "问题提到「%s」（影响因素）⇒ 走影响因素链路取规则" % x.get("name")})
    if not sug and q:                       # 策略页命中 ⇒ 直接按策略取
        parsed = _parse_query(q)
        terms = (parsed["phrases"] + parsed["words"])[:3]
        for r in _graph_search(kb, terms or [q], ["bmm:BusinessPolicy"], 1):
            sug.append({"tool": "knowledge_lookup",
                        "args": {"kb_id": kb, "mode": "rules",
                                 "spec": "policy:" + str(r.get("slug") or "")},
                        "why": "命中策略页「%s」⇒ 按该策略取规则" % (r.get("name") or "")})
    if not sug:
        sug.append({"tool": "knowledge_lookup",
                    "args": {"kb_id": kb, "mode": "keyword", "query": q},
                    "why": "先按关键词定位策略/规则页，再用 policy:/direct: 取规则"})
    return {**base, "spec": "", "resolved_by": "", "rules": [],
            "counts": {"rules": 0, "needs_extract": 0}, "scope_options": [],
            "plan": {"mode": "rules", "route": "", "args": {"mode": "rules", "spec": ""},
                     "expect": {"rules": 0}},
            "strategy": {"used": [], "scope_by": "none",
                         "why": "rules 模式要 spec：direct:/policy:/influencer:/ask"},
            "suggestions": sug,
            "next": ("请给 spec（direct:<规则slug> | policy:<策略> | influencer:<影响因素>）；"
                     "确需全量策略清单才用 ask")}


def _normalize_kb_id(kb_id: str) -> tuple:
    """`kb_id` 归一化（2026-10-26）：模型爱传 `b1/b2`、`XX库`、带引号 ⇒ 纠正或给可复制的候选。

    返回 `(值, info)`；`info` 非空时回执带 `kb_id_corrected` / `one_of` / `retry_args`。
    """
    raw = str(kb_id or "").strip().strip('"').strip("'")
    if not raw:
        return raw, {}
    import ke_db as _db

    try:                                          # resolve 在未知库上会 raise（历史口径 ✓）
        kb, name, _note = _db.resolve_kb_id(raw)
    except Exception:                             # noqa: BLE001
        kb = ""
    if kb:
        return raw, {}
    # 去掉"库"字 / 空白 / 大小写差异后再试一次（b2 → 若唯一近似就纠正）
    alt = raw.rstrip("库").strip()
    cands = []
    try:
        rows = _db.psql_csv("SELECT id, name FROM knowledge_bases WHERE deleted_at IS NULL")
    except Exception:  # noqa: BLE001
        rows = []
    for r in rows:
        n = str(r.get("name") or "")
        if alt and (alt.lower() in n.lower() or n.lower() in alt.lower()):
            cands.append({"kb_id": r.get("id"), "name": n})
    if len(cands) == 1:
        return cands[0]["kb_id"], {"from": raw, "to": cands[0]["kb_id"],
                                   "name": cands[0]["name"], "why": "唯一近似库名 ⇒ 已纠正"}
    if len(cands) > 1:
        return raw, {"from": raw, "one_of": [c["name"] for c in cands],
                     "hint": "这些库名都近似：请用**清单里的原名或 id 原样传**（不要自造缩写 b1/b2）",
                     "retry_args": {"kb_id": cands[0]["kb_id"]}}
    names = [str(r.get("name") or "") for r in rows][:12]
    return raw, {"from": raw, "one_of": names,
                 "hint": "库不存在：`kb_id` 用**下面清单里的原名或 id 原样传**（不要自造缩写 b1/b2）",
                 "retry_args": {"kb_id": (rows[0].get("id") if rows else "")}}


def _page_lookup(base: dict, kb: str, kb_id: str, query: str, kb_ids, limit: int) -> dict:
    """`mode="page"`：**按 slug 确定性命中**（`<库名>/` 前缀自动容错）→ 正文 + 属性。

    为什么单独一个模式（2026-10-26 用户口径）：任务页的 `skos:note`、DataObject 的绑定、
    模板的口径节分别落在**页属性/正文**里；`keyword` 能命中片段但**不保证命中要的那一页**，
    于是智能体只能去用内置 `wiki_read_page`（污染 A/B、且跨库/前缀一错就失败）。
    这里给"按 slug 取整页"的确定性入口 ✓（不新增工具，工具数仍 38）。
    """
    import ke_db as _db

    q = str(query or "").strip().strip('"').strip("'")
    if not q:
        return {**base, "ok": False, "error": "page 需要 query（slug 或标题关键词）",
                "hint": "例：mode=page, query=\"bpmn/task/<任务页 slug>\"（`kb_id` 可省）", "counts": {"hits": 0}}
    kbs = [str(k) for k in (kb_ids or []) if str(k).strip()]
    if kb and kb not in kbs:
        kbs = [kb] + kbs
    where = ["deleted_at IS NULL"]
    if kbs:
        where.append("knowledge_base_id IN (%s)" % ", ".join(_db.sql_str(k) for k in kbs))
    # `<库名>/` 前缀容错：q 含 "/" 时，除原文外再按"去掉第一段"查一次 ✓
    variants = [q]
    if "/" in q:
        tail = q.split("/", 1)[1]
        if tail and tail not in variants:
            variants.append(tail)
    like_parts = []
    for v in variants:
        like_parts.append(_db.sql_str("%" + v + "%"))
    rows = _db.psql_csv(
        "SELECT knowledge_base_id AS kb_id, slug, title, page_type, version, "
        "       COALESCE(content,'') AS content, "
        "       COALESCE(page_metadata->'ontology'->'attributes','{}'::jsonb)::text AS attrs "
        "  FROM wiki_pages WHERE %s AND (slug = %s OR slug ILIKE %s OR title ILIKE %s"
        "        OR slug IN (%s)) "
        " ORDER BY (slug = %s) DESC, length(slug) ASC, updated_at DESC LIMIT 30"
        % (" AND ".join(where), _db.sql_str(q), like_parts[0], like_parts[0],
           ", ".join(_db.sql_str(v) for v in variants), _db.sql_str(q)))
    if not rows:
        return {**base, "ok": False, "error": "page_not_found", "query": q,
                "hint": "slug 写全（可带/不带 `<库名>/` 前缀）；或先用 mode=keyword 找 slug",
                "counts": {"hits": 0}}

    def _rank(r: dict) -> tuple:
        slug = str(r.get("slug") or "")
        if slug == q:
            return (0, slug)
        if slug.split("/", 1)[-1] == q or slug.endswith("/" + q):
            return (1, slug)
        if str(r.get("title") or "").strip() == q:
            return (2, slug)
        return (3, slug)

    rows.sort(key=_rank)
    seen, picked = set(), []
    for r in rows:
        s = str(r.get("slug") or "")
        if s in seen:
            continue
        seen.add(s)
        picked.append(r)
    best_rank = _rank(picked[0])[0]
    top = [r for r in picked if _rank(r)[0] == best_rank]
    hits = [{"slug": r.get("slug"), "title": r.get("title"), "page_type": r.get("page_type"),
             "version": r.get("version"), "kb_id": r.get("kb_id")} for r in picked[:10]]
    if len(top) > 1 and best_rank >= 2:          # 只有"模糊档"多命中才算歧义（精确/后缀档取第一）
        return {**base, "ok": False, "error": "page_ambiguous", "query": q,
                "candidates": hits[:10], "counts": {"hits": len(hits)},
                "hint": "多条同名/相近页 ⇒ 用**完整 slug** 重试（列表见 candidates），不猜 ✗"}
    r0 = picked[0]
    try:
        attrs = json.loads(r0.get("attrs") or "{}")
    except Exception:  # noqa: BLE001
        attrs = {}
    page = {"slug": r0.get("slug"), "title": r0.get("title"), "page_type": r0.get("page_type"),
            "version": r0.get("version"), "kb_id": r0.get("kb_id"),
            "attributes": attrs, "note": attrs.get("skos:note") or "",
            "content_md": r0.get("content") or "",
            "content_chars": len(r0.get("content") or "")}
    return {**base, "ok": True, "mode": "page", "query": q,
            "matched_by": ("slug" if best_rank == 0 else
                           "slug_suffix" if best_rank == 1 else
                           "title" if best_rank == 2 else "fuzzy"),
            "counts": {"hits": len(hits)}, "hits": hits, "page": page,
            "next": ("本页内容已给全 ⇒ **不要再调内置 wiki 阅读工具**；"
                     "缺口径就问用户或看该页的引用（DataObject/模板）")}


def _lookup_impl(kb_id: str = "", mode: str = "rules", query: str = "",
                 spec: str = "", start: str = "", depth: int = 2,
                 limit: int = 20, cls: str = "", page_type: str = "",
                 kb_ids: list | None = None, auto_widen: bool = False,
                 question: str = "") -> dict:
    import ke_db as _db
    import ke_review as _rv

    try:                                          # page 模式可省 kb；未知库不要炸（回可读原因 ✓）
        kb, kb_name, note = _db.resolve_kb_id(kb_id)
    except Exception:                             # noqa: BLE001
        kb, kb_name, note = "", "", ""
    m = (mode or "rules").strip().lower()
    base = {"ok": True, "mode": m, "kb": {"id": kb, "name": kb_name}}

    # ── ⓪ page：按 slug 取整页（正文 + 属性；`<库名>/` 前缀容错）────────────
    if m == "page":
        return _page_lookup(base, kb, kb_id, query, kb_ids, int(limit) or 20)

    # ── ① rules：按步骤解析规则（唯一规则入口 ✓）────────────────────────
    if m == "rules":
        sp = (spec or query or "").strip()
        kind = (sp.partition(":")[0].strip().lower() if sp else "")
        if not sp or kind not in _SPEC_KINDS:
            # 没给 spec / 把**问题**当 spec（历史踩坑：抛"未知 spec 前缀"⇒ 智能体反复试错 ✗）
            return _rules_guide(base, kb, sp or (question or ""))
        r = _rv.rules_resolve(kb, sp, int(limit) or 300)
        out = {**base, **r}                       # **全字段透传**（含 chain；不再白名单挑字段 ✓）
        out.setdefault("source", "graph:BodhiInstance")
        n_rules = int((out.get("counts") or {}).get("rules") or 0)
        text = (question or query or "").strip()
        sug, nxt = [], ""
        if kind == "ask":
            # ask 只回策略清单 ⇒ **必须**给下一步的精确参数（否则等于把宽口径甩给智能体 ✗）
            for x in _name_hits(text, _rv._influencers(kb), "name", top=1):
                sug.append({"tool": "knowledge_lookup",
                            "args": {"kb_id": kb_id or kb, "mode": "rules",
                                     "spec": "influencer:" + str(x.get("name") or "")},
                            "why": "问题提到「%s」（影响因素）⇒ 用 influencer: 取规则，"
                                   "不必枚举全部策略" % x.get("name")})
            for x in _name_hits(text, out.get("scope_options") or [], "title", top=1):
                sug.append({"tool": "knowledge_lookup",
                            "args": {"kb_id": kb_id or kb, "mode": "rules",
                                     "spec": "policy:" + str(x.get("slug") or x.get("title") or "")},
                            "why": "问题提到「%s」⇒ 直接按该策略取规则" % (x.get("title") or "")})
            nxt = (sug[0]["why"] if sug else
                   "`ask` 只回策略清单、不含规则：请从清单里选一条用 policy:/influencer: 再取")
        elif kind == "influencer":
            nxt = (("已按影响因素链路取规则 %d 条（仅链路内的策略，不含链路外策略）" % n_rules)
                   if n_rules else
                   "该影响因素的影响手段不是策略（多为手段类页），未派生规则；"
                   "可先 ask 看策略清单，或用 direct: 指定规则")
        elif kind == "policy":
            nxt = "已按策略闭包（含子策略）+ 双向规则边取规则 %d 条" % n_rules
        else:
            nxt = "已按 direct 取规则 %d 条" % n_rules
        out["plan"] = {"mode": "rules", "route": kind,
                       "args": {"mode": "rules", "spec": out.get("spec") or sp},
                       "expect": {"rules": n_rules}}
        out["strategy"] = {"used": ["graph"], "scope_by": "explicit",
                           "why": _ROUTE_WHY.get(kind, "")}
        out["suggestions"] = sug
        out["next"] = nxt
        return out

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
            for x in _name_hits(q, _rv._influencers(kb), "name", top=1):
                suggestions.append({"tool": "knowledge_lookup",
                                    "args": {"kb_id": kb_arg, "mode": "rules",
                                             "spec": "influencer:" + str(x.get("name") or "")},
                                    "why": "问题提到「%s」（影响因素）⇒ 走影响因素链路取规则"
                                           "（不要枚举全部策略）" % x.get("name")})
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


def _hint_mode() -> str:
    """env 三态：`on` | `off` | `abtest`（未知值 ⇒ 按 abtest 处理并落到遥测，不再**静默**当 on）。

    为什么要三态：手工切 `.env` 跑 A/B 太麻烦，且会污染真实流量；
    `abtest` 让服务端按 50% 分臂、把"哪一臂"记进遥测，分析侧再判对错（服务端不读真值 ✗）。
    """
    raw = (os.environ.get("BODHI_ONTOLOGY_HINT") or "on").strip().lower()
    if raw in ("0", "false", "off", "no", "n"):
        return "off"
    if raw in ("1", "true", "on", "yes", "y"):
        return "on"
    return "abtest"                       # `abtest` / 未知值 ⇒ 自动分臂（遥测里带 mode，可回溯）


def _ab_arm(key: str) -> tuple:
    """50% 分臂 → `(arm, rotation)`。

    `hash`（默认）用 `blake2b(问题键|会话号)` 的奇偶 ⇒ **随机分布但可复算**
    （同一会话同一问稳定、跨会话约 50/50，便于复核）；`random` 才是真随机（不可复算）。
    """
    rot = (os.environ.get("BODHI_AB_ROTATION") or "hash").strip().lower()
    if rot == "random":
        import random  # noqa: PLC0415
        return ("hint" if random.getrandbits(1) else "plain"), "random"
    try:
        import hashlib  # noqa: PLC0415
        h = hashlib.blake2b((key or "").encode("utf-8"), digest_size=8).digest()
        return ("hint" if (h[0] & 1) else "plain"), "hash"
    except Exception:  # noqa: BLE001  分臂失败也要能用
        return "hint", "hash"


def _resolve_arm(ontology_hint, text: str = "", session_no: str = "") -> tuple:
    """→ `(hint: bool, assignment: str, ab: dict)`；优先级：显式布尔 > env 三态。"""
    if isinstance(ontology_hint, bool):
        return (ontology_hint, "forced",
                {"mode": "forced", "arm": "hint" if ontology_hint else "plain"})
    mode = _hint_mode()
    if mode == "on":
        return True, "manual", {"mode": "on", "arm": "hint"}
    if mode == "off":
        return False, "manual", {"mode": "off", "arm": "plain"}
    arm, rotation = _ab_arm(_question_key(text) + "|" + str(session_no or ""))
    return arm == "hint", "abtest", {"mode": "abtest", "arm": arm, "rotation": rotation}


def _hint_enabled(ontology_hint=None) -> bool:
    """开关解析（**兼容旧口径**）：显式布尔 > env。

    `abtest` 在这里回 True（=提示面**可能**在场）；真正的分臂要靠问题/会话上下文，
    走 `_resolve_arm` ✓（没有上下文的老调用方保持旧行为，不回退成 off）。
    """
    if isinstance(ontology_hint, bool):
        return ontology_hint
    return _hint_mode() != "off"


_QKEY_PUNCT = re.compile(r"[\s\u3000·、，,。.（）()【】\[\]:：;；\-—_/？?！!…\"'“”‘’]+")


def _question_key(text: str) -> str:
    """问题归一化键（同类问题聚类用）：小写 + 去标点/空白（含全角 ？ ！）。"""
    return _QKEY_PUNCT.sub("", (text or "").lower())[:80]


# ---------------------------------------------------------------------------
# **问题归组 / 形态分类**（2026-10-25，用户口径）
# ---------------------------------------------------------------------------
# 场景（用户实测）：不是在跑"问题集"，而是「选一个文件 → 给业务编号 → 要求判定」。
#   · 同一个**编号**跑两次 ⇒ 是**同一个问题的两轮**（不能算两个问题 ✗）；
#   · 编号不同但语义相似 ⇒ **可以当同一个问题**对比（报告侧用相似度归组）；
#   · 关键字**数量**不同、**层次**不同（类 vs 实例组合）⇒ **算不同类问题**（`q_shape`）。
# 服务端只落**可复算的原始特征**（编号 / 词数 / 层次），归组与阈值放报告侧
# （`ke_ab_report.group_rows`）—— 口径可调、不用重启发服务 ✓
# ---------------------------------------------------------------------------
_QID_PAT = (
    re.compile(r"编号\s*[：:#]?\s*([A-Za-z0-9][A-Za-z0-9\-_/]{3,})"),
    re.compile(r"\b(?:no|no\.|id)\s*[：:#]?\s*([A-Za-z0-9][A-Za-z0-9\-_/]{3,})", re.I),
)
_POLITE = re.compile(r"(请问|帮我|麻烦|谢谢|一下|能否|请)")


def _extract_qid(question: str) -> str:
    """问题里的**业务编号**（如 `业务编号 YYYYMM-889999`）——同编号 ⇒ 同问题（多轮）。

    取不到就回空串（报告侧退化为"语义相似度归组"✓）。
    """
    text = question or ""
    for pat in _QID_PAT:
        m = pat.search(text)
        if m:
            return m.group(1).strip().lower()
    return ""


def _q_shape(mode: str = "", query: str = "", cls: str = "", plan: dict | None = None) -> dict:
    """查询**形态**（只看问题怎么问，不看结果 ✗）：词数档 × 关键字层次。

    · `kw_terms`  —— 参与检索的词数（keyword 模式按解析结果；instances/rules 记 1）；
    · `kw_bucket` —— `0` / `1` / `2` / `3+`（"数据量不同 ⇒ 不同类问题" ✓）；
    · `level`     —— `类` / `类+文本` / `文本` / `实例`（"有实例有类的组合" ⇒ 不同类 ✓）。
    """
    plan = plan or {}
    m = (mode or "").strip().lower()
    terms = [t for t in (plan.get("text_terms") or []) if t]
    if not terms and query:
        terms = [t for t in re.split(r"[\s,，、]+", query.strip()) if t]
    scope = [c for c in (plan.get("scope_classes") or []) if c]
    if not scope and cls:
        scope = [c for c in re.split(r"[,，\s]+", cls.strip()) if c]
    kw = len(terms) if m in ("keyword", "route") else 1
    if m == "instances":
        level = "实例"
    elif scope and terms:
        level = "类+文本"
    elif scope:
        level = "类"
    elif terms:
        level = "文本"
    else:
        level = "无"
    bucket = "0" if kw <= 0 else ("1" if kw == 1 else ("2" if kw == 2 else "3+"))
    return {"kw_terms": kw, "kw_bucket": bucket, "level": level,
            "q_shape": "%s|%s" % (bucket, level)}


def _question_core(text: str) -> str:
    """问题**语义核**：去编号/版本/文件名/礼貌语/标点（报告侧相似度归组的输入）。

    例：`业务编号 YYYYMM-889999；受评文档 设计说明-v3.docx；第一轮，开始判定`
        ⇒ `业务编号受评文档设计说明开始判定`
    """
    t = (text or "").lower()
    t = re.sub(r"https?://\S+", " ", t)
    t = re.sub(r"\.(docx?|pdf|xlsx?|pptx?|wps|md|txt|csv)\b", " ", t)      # 文件后缀
    t = re.sub(r"[A-Za-z]{0,4}\d[\dA-Za-z\-_./]*", " ", t)          # 编号/版本/示例
    t = re.sub(r"第\s*[0-9一二三四五六七八九十百]+\s*[轮版次阶段步章节]", " ", t)
    t = _POLITE.sub(" ", t)
    return _question_key(t)


def knowledge_lookup(kb_id: str = "", mode: str = "rules", query: str = "",
                     spec: str = "", start: str = "", depth: int = 2,
                     limit: int = 20, cls: str = "", page_type: str = "",
                     kb_ids: list | None = None, auto_widen: bool = False,
                     ontology_hint: bool | None = None, session_no: str = "",
                     question: str = "", turn_id: str = "", first_step: bool | None = None,
                     turn_first: str = "") -> dict:
    """**统一检索入口**（原实现 + 本体提示开关 + 遥测 + A/B 轮次口径）。

    向后兼容：前 14 个参数与旧签名完全一致 ✓（旧调用方无需改动）。
    `session_no` / `question` / `turn_id` / `first_step` / `turn_first` **只进遥测**，不参与检索。
    """
    t0 = time.time()
    # 2026-10-26：`kb_id` 归一化（b1/b2、带"库"字、带引号 ⇒ 唯一近似则纠正；否则给候选/retry_args）
    kb_id, _kb_fix = _normalize_kb_id(kb_id)
    if _kb_fix and not _kb_fix.get("to") and str(mode or "").lower() != "page":
        # 库解析不了 ⇒ 直接回**可读纠错回执**（不抛异常，避免智能体反复试错 ✗）
        return {"ok": False, "error": "kb_unresolved", "kb_id_corrected": _kb_fix,
                "hint": _kb_fix.get("hint") or "kb_id 用库清单里的原名或 id 原样传",
                "retry_args": _kb_fix.get("retry_args") or {}}
    # 2026-10-25（P2）：三态开关 —— 显式布尔 > env(on|off|abtest)；abtest 按 50% 分臂并**记进遥测**。
    hint, assignment, ab = _resolve_arm(ontology_hint, question or query or spec, session_no)
    # 2026-10-25（A/B 口径 v2）：**真实 IO 统计** —— 打了库/图几次、几次空手而归。
    #   · 走 MCP 时 `srv/http.py` 已经 `begin()`，这里只读快照（**不重复计数** ✓）；
    #   · CLI / 测试直接调用时自己开一个桶，结束后 `reset()`（**不影响外层** ✓）。
    own_bucket = not ke_stats.enabled()
    if own_bucket:
        ke_stats.begin()
    try:
        r = _lookup_impl(kb_id, mode, query, spec, start, depth, limit, cls, page_type,
                         kb_ids, auto_widen, question=question)
    finally:
        snap = ke_stats.snapshot() or {}
        if own_bucket:
            ke_stats.reset()
    variant = "hint" if hint else "plain"
    if _kb_fix:
        r["kb_id_corrected"] = _kb_fix
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
    # 2026-10-25（用户口径）：问题归组的原始特征 + 查询形态（不同类问题要分开看 ✓）
    shape = _q_shape(r.get("mode") or mode, query, cls, plan)
    by_src = (counts.get("by_source") or {})
    io_sub = snap.get("io") or {}
    detail = snap.get("detail") or {}
    retrieval = {
        "queries": int(io_sub.get("queries") or 0),
        "pg": int((detail.get("pg") or {}).get("n", 0)),
        "neo4j": int((detail.get("neo4j") or {}).get("n", 0)),
        "empty_queries": int(io_sub.get("empty") or 0),      # **空记录 = 失败**（用户口径）
        "hit_queries": int(io_sub.get("hit") or 0),          # 有结果的往返 = 成功
        "unknown_queries": int(io_sub.get("unknown") or 0),
        "empty_rate": io_sub.get("empty_rate"),
        "io_ms": int(round(io_sub.get("ms") or 0)),
        # "降级"= 检索中途换过招：AND→OR，或图没命中只能靠 PG 兜底
        "degraded": bool(plan.get("relaxed_by")) or (bool(by_src.get("pg")) and not by_src.get("graph")),
        "ok": bool(hits),                                     # 本次调用是否**拿到结果**
    }
    # 2026-10-26（用户口径）：**检索类别分列** —— 读口径/读页不计入"检索次数"，
    # 但仍参与"本轮首个动作"的顺序判定（A/B 报告按 kind 分组 ✓）
    _kind = {"rules": "policy", "page": "page"}.get(str(r.get("mode") or mode).lower(),
                                                    "knowledge")
    r["telemetry"] = {
        "variant": variant, "ontology_hint": hint,
        "retrieval_kind": _kind,
        # 2026-10-24（A/B 四维）：程序版本（语义位 + 代码指纹）／问题种类／轮次判定
        "lookup_version": LOOKUP_VERSION, "code_rev": CODE_REV,
        "q_category": _q_category(r.get("mode") or mode, query, spec),
        "turn_id": str(turn_id or ""), "first_step": first_step,
        "turn_first_retrieval": str(turn_first or ""),
        "session_no": str(session_no or ""), "question": str(question or ""),
        "question_key": _question_key(question or query or spec),
        # 2026-10-25：编号（同编号=同问题多轮）/ 语义核（相似度归组）/ 形态（不同类问题）
        "qid": _extract_qid(question or query or spec),
        "question_core": _question_core(question or query or spec),
        "q_shape": shape["q_shape"], "kw_terms": shape["kw_terms"],
        "kw_bucket": shape["kw_bucket"], "level": shape["level"],
        # 2026-10-25：**真实库访问**（不是 MCP 调用次数 ✓）+ 是否有结果
        "io": io_sub, "retrieval": retrieval,
        "query": str(query or spec or "")[:120], "mode": r.get("mode") or mode,
        "hits": int(hits or 0), "top_slug": top,
        "relaxed_by": str(plan.get("relaxed_by") or ""),
        "ambiguity": bool(plan.get("ambiguity")), "scope_by": str(plan.get("scope_by") or ""),
        # 2026-10-25（P2/P3）：分臂来源 + 规则取数路径（报告按路径分组判正确率）
        "assignment": assignment, "ab": ab,
        "route": str(plan.get("route") or r.get("resolved_by") or ""),
        "resolved_by": str(r.get("resolved_by") or ""),
        "rules_n": int((r.get("counts") or {}).get("rules") or 0),
        # 2026-10-25（P3-b）：**本轮实际取到的规则 slug**（≤20 个）—— 判"检索口径"用，
        # 不必等最终答案就能判对错（样本量因此大幅增加 ✓）
        "rule_slugs": [str(x.get("slug")) for x in (r.get("rules") or []) if x.get("slug")][:20],
        "ms": int((time.time() - t0) * 1000),
    }
    return r

