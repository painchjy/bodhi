"""ke-core · 跨库「上下文映射」**只读内核**（一期 · 2026-09-28 用户拍板）。

一期 = **纯只读**：扫描/查询只会写我们自己的状态目录 `state/context_map/`，
**不碰任何 wiki 页、不改任何页面元数据**（写路径在二期：概念页/映射页 + 两段式 apply）。

口径（见 docs/context-mapping-plan.md §2 / §13）
--------------------------------------------------
- **上下文 = 一个知识库**；「企业共享概念模型」是 `kind=concept` 的那个库（认库见 `concept_kb()`）；
- **同名** = 跨库 `slug` **归一化后字面相同**（L1，用户口径）；
- **同实例** = 跨库 **本体类 + 归一化标题** 相同（L2，原 F1 口径）→ 与 L1 合并成一张候选表；
- **概念 id = 共享概念模型库里的页 slug**（不造 GUID）；概念页 `page_type` 仍是本体类（遵本体约束）；
- 判定**只出建议**（决策 5 档 2）：`equivalent` 永远要人确认；`distinct` 在**二期**可按阈值
  （默认 0.95）自动落 `unrelated`；一期只标 `auto_eligible=true` 让你看到"将来会自动做什么"。

对外函数
--------
    contexts()                     上下文注册表（自动派生，可被 contexts.json 覆盖）
    scan(kb_ids=None, limit=200)   全库同名/同实例分组 + R0/R1/R2 建议 + ticket（写扫描报告）
    lookup(kb_ids=None, q="", slug="")   检索前查：同义概念 / 异义映射 / 依赖 / 警告
    concept_kb()                   认「企业共享概念模型」库
    latest_scan()                  最近一次扫描报告

CLI
---
    python3 tools/ke-core/ke_context.py contexts
    python3 tools/ke-core/ke_context.py scan [kb,kb,...] [--limit 200] [--no-write]
    python3 tools/ke-core/ke_context.py lookup <kb> (--slug s | --q 关键词)
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import pathlib
import re
import sys
import unicodedata
from collections import Counter
from datetime import datetime

HERE = pathlib.Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import ke_db  # noqa: E402
import ke_ontology  # noqa: E402
import ke_pages  # noqa: E402

REPO = HERE.parents[1]
STATE_DIR = REPO / "state" / "context_map"
SCAN_DIR = STATE_DIR / "scan"
CONTEXTS_FILE = STATE_DIR / "contexts.json"
MAPPINGS_FILE = STATE_DIR / "mappings.json"          # **缓存**（事实源是概念页正文）
LATEST_FILE = STATE_DIR / "latest_scan.json"
HISTORY_DIR = STATE_DIR / "history"                  # apply 记录（可回滚）
CACHE_DIR = STATE_DIR / "cache"                      # 各领域库的**局部映射缓存**

CONCEPT_MARK_KEY = "bodhi_concept_kb"          # wiki_config 里的标记（与本体库标记同款套路）
CONCEPT_DEFAULT_NAME = "企业共享概念模型"
# 建议阈值（决策 5 档 2）：equivalent 一律人工；distinct 允许自动需要 ≥ AUTO_DISTINCT_MIN
EQ_MIN, DISTINCT_MAX, AUTO_DISTINCT_MIN = 0.90, 0.55, 0.95

# 结构性/上游页不算知识页（与 ke_audit._is_instance 同口径）
NON_INSTANCE = ("index", "summary")
NON_INSTANCE_PREFIX = ("ontology:",)


def _is_instance(page_type: str) -> bool:
    pt = (page_type or "").strip()
    if not pt or pt in NON_INSTANCE or pt.startswith(NON_INSTANCE_PREFIX):
        return False
    return ":" in pt


# ---------------------------------------------------------------------------
# 归一化 / 相似度
# ---------------------------------------------------------------------------
def norm_slug(slug: str) -> str:
    """slug 归一化（判"同名"用）：NFKC + 大小写折叠 + 去空白。

    **不做**同义词替换、**不去** `-`/`_`（保守：`user-account` 与 `useraccount` 算不同名，
    避免把"名字像"误判成"名字同"）。
    """
    text = unicodedata.normalize("NFKC", slug or "").casefold()
    return re.sub(r"[\s\u200b\u3000]+", "", text)


def norm_title(title: str) -> str:
    """标题归一化（与 ke_audit._norm_title 同口径）。"""
    return re.sub(r"[\s\-_（）()【】\[\]：:,.，。·]+", "", (title or "").lower())


def _ngrams(text: str, n: int = 3) -> Counter:
    text = re.sub(r"[\s\u3000]+", "", (text or "").lower())
    if len(text) < n:
        return Counter([text]) if text else Counter()
    return Counter(text[i:i + n] for i in range(len(text) - n + 1))


def sim_text(a: str, b: str) -> float:
    """字符 3-gram 余弦（离线可用；与 MCP 里既有 lexical 相似度同思路）。"""
    va, vb = _ngrams(a), _ngrams(b)
    if not va or not vb:
        return 0.0
    dot = sum(v * vb.get(k, 0) for k, v in va.items())
    na = math.sqrt(sum(v * v for v in va.values()))
    nb = math.sqrt(sum(v * v for v in vb.values()))
    return round(dot / (na * nb), 4) if na and nb else 0.0


def sim_set(a: set, b: set) -> float:
    """集合 Jaccard（关系签名/属性键用）。"""
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    return round(len(a & b) / len(a | b), 4)


def text_hash(text: str) -> str:
    return "sha1:" + hashlib.sha1((text or "").encode("utf-8")).hexdigest()


def _sha1(text: str) -> str:
    return hashlib.sha1((text or "").encode("utf-8")).hexdigest()


def _json_load(path: pathlib.Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return default


def _json_dump(path: pathlib.Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


# ---------------------------------------------------------------------------
# 库 / 页装载（只读）
# ---------------------------------------------------------------------------
_PAGE_COLUMNS: set | None = None


def _page_columns() -> set:
    global _PAGE_COLUMNS
    if _PAGE_COLUMNS is None:
        rows = ke_db.psql_csv("SELECT column_name FROM information_schema.columns "
                              "WHERE table_name = 'wiki_pages'")
        _PAGE_COLUMNS = {r["column_name"] for r in rows}
    return _PAGE_COLUMNS


def kb_rows() -> list[dict]:
    """所有活着的知识库（id/name/tenant/wiki_config）。"""
    return ke_db.psql_csv(
        "SELECT id, COALESCE(name,'') AS name, COALESCE(wiki_config::text,'{}') AS wiki_config, "
        "       tenant_id, COALESCE(creator_id::text,'') AS creator_id "
        "  FROM knowledge_bases WHERE deleted_at IS NULL ORDER BY created_at")


def _page_counts() -> dict:
    rows = ke_db.psql_csv(
        "SELECT knowledge_base_id AS kb, count(*) AS n, "
        "       count(*) FILTER (WHERE page_type LIKE '%:%') AS inst "
        "  FROM wiki_pages WHERE deleted_at IS NULL GROUP BY 1")
    return {r["kb"]: {"pages": int(r["n"] or 0), "instance": int(r["inst"] or 0)} for r in rows}


def load_pages(kb_id: str, limit: int = 5000) -> list[dict]:
    """一个库的知识页（`slug/title/page_type/summary/meta/out_links/head/version`）。"""
    cols = _page_columns()
    summary = "COALESCE(summary,'')" if "summary" in cols else "''"
    sql = ("SELECT slug, COALESCE(title,'') AS title, COALESCE(page_type,'') AS page_type, "
           "%s AS summary, COALESCE(page_metadata::text,'{}') AS meta, "
           "COALESCE(out_links::text,'[]') AS out_links, "
           "left(COALESCE(content,''), 1500) AS head, COALESCE(version,1) AS version, "
           "COALESCE(updated_at::text,'') AS updated_at "
           "  FROM wiki_pages WHERE knowledge_base_id = %s AND deleted_at IS NULL "
           " ORDER BY slug LIMIT %d" % (summary, ke_db.sql_str(kb_id), int(limit) + 1))
    try:
        rows = ke_db.psql_csv(sql)
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError("读页失败（%s）：%s" % (kb_id[:8], exc))
    return rows[:int(limit)]


def _json_load_str(text, default):
    try:
        val = json.loads(text or "null")
        return val if val is not None else default
    except Exception:  # noqa: BLE001
        return default


def _defn(page: dict) -> str:
    """用于相似度的"定义文本"：标题 + 摘要 + 正文前段（剥掉 markdown 记号）。"""
    text = "%s %s %s" % (page.get("title", ""), page.get("summary", ""), page.get("head", ""))
    return re.sub(r"[#>*`\[\]()|{}\-]+", " ", text)[:600]


def _definition_from_content(content: str, limit: int = 220) -> str:
    """从领域页正文里**摘出定义**（概念页的「标准定义」默认值）：优先 `## 定义/用途/说明` 小节，
    否则取正文第一段实质内容（跳过标题、引用块、表格、列表与元数据行）。"""
    text = content or ""
    for header in ("## 定义", "## 用途", "## 说明", "## 概念定义"):
        body = _section(text, header)
        para = _first_para(body)
        if para:
            return para[:limit]
    return _first_para(text)[:limit]


def _first_para(text: str) -> str:
    """取第一段"实质段落"（跳过标题/引用/表格/列表行）；没有就返回空串。"""
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line or line[0] in "#>|" or line.startswith(("-", "*", "```")):
            continue
        clean = re.sub(r"[*`\[\]]+", "", line)
        clean = re.sub(r"\s+", " ", clean).strip()
        if len(clean) >= 8:
            return clean
    return ""


def _rels(page: dict) -> set:
    return {str(x) for x in (_json_load_str(page.get("out_links"), []) or []) if x}


def _meta_dict(page: dict) -> dict:
    return _json_load_str(page.get("meta"), {}) or {}


# ---------------------------------------------------------------------------
# 上下文注册表 + 认「企业共享概念模型」库
# ---------------------------------------------------------------------------
def concept_kb(kbs: list[dict] | None = None) -> dict:
    """认「企业共享概念模型」库：env → `wiki_config.bodhi_concept_kb` → 库名 → 内容探测。

    与 `ke_ontology.resolve_ontology_kb` 同款套路（换库不用改代码/不用重建前端）。
    """
    kbs = kbs if kbs is not None else kb_rows()
    env_id = (os.environ.get("BODHI_CONCEPT_KB") or os.environ.get("BODHI_CONCEPT_KB_ID") or "").strip()
    env_name = (os.environ.get("BODHI_CONCEPT_KB_NAME") or CONCEPT_DEFAULT_NAME).strip()
    rows = ke_db.psql_csv(
        "SELECT knowledge_base_id AS kb, count(*) AS n FROM wiki_pages "
        " WHERE deleted_at IS NULL AND slug LIKE 'concept/%' GROUP BY 1")
    pages = {r["kb"]: int(r["n"] or 0) for r in rows}

    def _mk(kb: dict, source: str) -> dict:
        return {"id": kb["id"], "name": kb["name"], "source": source,
                "concept_pages": pages.get(kb["id"], 0)}

    def _find(pred) -> dict | None:
        for kb in kbs:
            if pred(kb):
                return kb
        return None

    hit = None
    if env_id:
        found = _find(lambda k: k["id"] == env_id or k["id"].startswith(env_id))
        hit = _mk(found, "env") if found else {
            "id": env_id, "name": env_id, "source": "env", "concept_pages": 0,
            "note": "env 指定的库不在库里（尚未创建/已删）"}
    if hit is None:
        found = _find(lambda k: ('"%s"' % CONCEPT_MARK_KEY) in (k["wiki_config"] or "")
                      and "true" in (k["wiki_config"] or ""))
        hit = _mk(found, "wiki_config") if found else None
    if hit is None and env_name:
        found = _find(lambda k: k["name"] == env_name) \
            or _find(lambda k: env_name in (k["name"] or "") or (k["name"] or "") in env_name)
        hit = _mk(found, "name") if found else None
    if hit is None:
        rich = [k for k in kbs if pages.get(k["id"], 0) >= 3]
        if rich:
            hit = _mk(max(rich, key=lambda k: pages.get(k["id"], 0)), "pages")
    return hit or {"id": "", "name": "", "source": "none", "concept_pages": 0,
                   "note": "未创建「%s」库（一期只读可跑；二期写入前需先建库）" % CONCEPT_DEFAULT_NAME}


# ---------------------------------------------------------------------------
# 扫描：L1（slug 同名）+ L2（类+标题同实例）→ 合并成一张候选表
# ---------------------------------------------------------------------------
class _UF:
    """并查集（把"同 slug"与"同类同标题"两种判据合并成**一张**候选表）。"""

    def __init__(self) -> None:
        self.p: dict = {}

    def find(self, x):
        self.p.setdefault(x, x)
        while self.p[x] != x:
            self.p[x] = self.p[self.p[x]]
            x = self.p[x]
        return x

    def union(self, a, b) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.p[rb] = ra


def _signals(pages: list[dict]) -> dict:
    titles = {norm_title(p["title"]) for p in pages if p["title"]}
    types = {p["page_type"] for p in pages if p["page_type"]}
    defs = [_defn(p) for p in pages]
    pair_def = [sim_text(a, b) for i, a in enumerate(defs) for b in defs[i + 1:]]
    rels = [_rels(p) for p in pages]
    pair_rel = [sim_set(rels[i], rels[j]) for i in range(len(rels)) for j in range(i + 1, len(rels))]
    return {"title_equal": (len(titles) == 1 and bool(titles)),
            "same_page_type": (len(types) == 1 and bool(types)),
            "definition_sim": round(sum(pair_def) / len(pair_def), 4) if pair_def else 0.0,
            "rel_signature_sim": round(sum(pair_rel) / len(pair_rel), 4) if pair_rel else 1.0}


def _verdict(sig: dict) -> tuple[str, float, dict]:
    """R0（完全一致）优先；否则加权打分 → `equivalent` / `distinct` / `unknown`。"""
    if sig["title_equal"] and sig["same_page_type"] \
            and sig["definition_sim"] >= 0.95 and sig["rel_signature_sim"] >= 0.80:
        return "equivalent", 0.99, {"reason": "R0 完全一致（类 + 标题 + 定义 + 关系签名）"}
    score = round(0.45 * sig["definition_sim"] + 0.25 * sig["rel_signature_sim"]
                  + 0.15 * (1.0 if sig["same_page_type"] else 0.0)
                  + 0.15 * (1.0 if sig["title_equal"] else 0.0), 4)
    if score >= EQ_MIN:
        return "equivalent", score, {"reason": "R1/R2 综合分 ≥ %.2f" % EQ_MIN, "score": score}
    if score <= DISTINCT_MAX:
        return "distinct", round(1.0 - score, 4), {
            "reason": "R1/R2 综合分 ≤ %.2f" % DISTINCT_MAX, "score": score}
    return "unknown", score, {
        "reason": "落在 %s–%s 之间，必须人工判定" % (DISTINCT_MAX, EQ_MIN), "score": score}


def _concept_slug_hint(pages: list[dict]) -> str:
    """二期落库时概念页 slug 的建议值：`<模块>/<类名小写>/<标准名>`（遵本体约束、与领域模型同构）。"""
    best = sorted(pages, key=lambda p: (-int(p.get("version") or 1), p["slug"]))[0]
    ptype = best.get("page_type") or ""
    if ":" not in ptype:
        return ""
    prefix, _sep, cls = ptype.partition(":")
    name = (best.get("title") or "").strip()
    return "%s/%s/%s" % (prefix, cls.lower(), name) if name else ""


def _unrelated_pairs() -> set:
    """`mappings.json` 里已声明 `unrelated` 的页对（一期通常为空；二期才有数据）。"""
    data = _json_load(MAPPINGS_FILE, {}) or {}
    out = set()
    for row in (data.get("pairs") or []):
        if str(row.get("mapping") or "") != "unrelated":
            continue
        src, dst = row.get("source") or {}, row.get("target") or {}
        a = (src.get("context") or src.get("kb") or "", src.get("slug") or "")
        b = (dst.get("context") or dst.get("kb") or "", dst.get("slug") or "")
        if a[1] and b[1]:
            out.add(tuple(sorted([a, b])))
    return out


def contexts(refresh: bool = False) -> dict:
    """上下文注册表：KB → context（自动派生；`state/context_map/contexts.json` 可覆盖 label/owner/notes）。"""
    kbs = kb_rows()
    counts = _page_counts()
    concept = concept_kb(kbs)
    ont_id = (ke_ontology.resolve_ontology_kb().get("id") or "")
    overrides = _json_load(CONTEXTS_FILE, {}) or {}
    given = (overrides.get("contexts") or {}) if isinstance(overrides, dict) else {}
    out: dict = {}
    for kb in kbs:
        key = "kb:%s" % kb["id"][:8]
        kind = "concept" if kb["id"] == concept.get("id") else ("ontology" if kb["id"] == ont_id else "biz")
        entry = {"key": key, "label": kb["name"], "kb_id": kb["id"], "tenant_id": kb["tenant_id"],
                 "kind": kind, "pages": counts.get(kb["id"], {}).get("pages", 0),
                 "instance_pages": counts.get(kb["id"], {}).get("instance", 0)}
        ov = given.get(key) or given.get(kb["id"])
        if isinstance(ov, dict):
            entry.update({k: v for k, v in ov.items() if k in ("label", "owner", "notes", "kind")})
            entry["overridden"] = sorted(k for k in ov if k in ("label", "owner", "notes", "kind"))
        out[key] = entry
    return {"version": 1, "generated_at": ke_db.now_text(),
            "source": "derived+overrides" if given else "derived",
            "concept": concept, "contexts": out,
            "overrides_file": str(CONTEXTS_FILE.relative_to(REPO)) if CONTEXTS_FILE.exists() else ""}


def scan(kb_ids: list[str] | None = None, limit: int = 200, page_limit: int = 5000,
         write: bool = True) -> dict:
    """全库扫描：跨库 `slug` 同名（L1）+ 跨库「类 + 标题」相同（L2）→ **一张**候选表 + 建议 + ticket。

    只读：只写 `state/context_map/scan/<scan_id>.json` 与 `latest_scan.json`（我们自己的状态目录）。
    **不参与分组**的两个库：本体模型库（`ontology:*` 投影页）与「企业共享概念模型」库
    （后者是候选的**落点**，不是候选本身）。
    """
    ctx = contexts()
    concept = ctx["concept"]
    all_kbs = kb_rows()
    if kb_ids:
        picked: list = []
        for raw in kb_ids:
            try:
                kid, name, _note = ke_db.resolve_kb_id(raw)
            except Exception as exc:  # noqa: BLE001
                raise ValueError("知识库解析失败：%s" % exc)
            picked.append({"id": kid, "name": name})
    else:
        picked = [{"id": k["id"], "name": k["name"]} for k in all_kbs]
    skip = {concept.get("id") or "", ke_ontology.resolve_ontology_kb().get("id") or ""}
    picked = [k for k in picked if k["id"] and k["id"] not in skip]

    pages_by_kb: dict = {}
    scanned = 0
    for kb in picked:
        rows = [p for p in load_pages(kb["id"], page_limit) if _is_instance(p["page_type"])]
        pages_by_kb[kb["id"]] = rows
        scanned += len(rows)

    uf = _UF()
    l1: dict = {}
    l2: dict = {}
    for kb in picked:
        for page in pages_by_kb[kb["id"]]:
            node = (kb["id"], page["slug"])
            uf.union(node, node)
            l1.setdefault(norm_slug(page["slug"]), []).append(node)
            if page["title"] and page["page_type"]:
                l2.setdefault((page["page_type"], norm_title(page["title"])), []).append(node)
    for nodes in list(l1.values()) + list(l2.values()):
        if len(nodes) > 1:
            for other in nodes[1:]:
                uf.union(nodes[0], other)

    groups: dict = {}
    for kb in picked:
        for page in pages_by_kb[kb["id"]]:
            groups.setdefault(uf.find((kb["id"], page["slug"])), []).append((kb, page))

    kb_name = {k["id"]: k["name"] for k in all_kbs}
    unrelated = _unrelated_pairs()
    candidates: list = []
    suppressed: list = []
    for nodes in groups.values():
        if len(nodes) < 2 or len({kb["id"] for kb, _p in nodes}) < 2:
            continue
        pages = [p for _kb, p in nodes]
        node_set = {(kb["id"], page["slug"]) for kb, page in nodes}
        slug_hits = sum(1 for v in l1.values()
                        if len(v) > 1 and len({a for a, _b in v}) > 1 and set(v) & node_set)
        title_hits = sum(1 for v in l2.values()
                         if len(v) > 1 and len({a for a, _b in v}) > 1 and set(v) & node_set)
        matched_by = ([m for m, hit in (("slug", slug_hits), ("class+title", title_hits)) if hit]
                      or ["slug"])
        sig = _signals(pages)
        verdict, confidence, why = _verdict(sig)
        pair_key = tuple(sorted((kb["id"][:8], p["slug"]) for kb, p in nodes))
        entry = {
            "id": "cand:%s" % _sha1(json.dumps(pair_key, ensure_ascii=False))[:10],
            "matched_by": matched_by,
            "pages": [{"context": "kb:%s" % kb["id"][:8], "kb": kb["id"],
                       "kb_name": kb_name.get(kb["id"], kb.get("name", "")), "slug": page["slug"],
                       "title": page["title"], "page_type": page["page_type"],
                       "version": int(page.get("version") or 1),
                       "hash": text_hash(page.get("head") or ""),
                       "same_as": (_meta_dict(page).get("same_as") or {})} for kb, page in nodes],
            "signals": sig, "verdict_suggestion": verdict, "confidence": confidence, "why": why,
            "auto_eligible": (verdict == "distinct" and confidence >= AUTO_DISTINCT_MIN),
            "concept_slug_hint": _concept_slug_hint(pages) if verdict == "equivalent" else "",
            "concept_kb": concept,
        }
        entry["auto_action"] = (("unrelated（异义；二期按阈值 %.2f 自动落 mappings.json）"
                                 % AUTO_DISTINCT_MIN) if entry["auto_eligible"] else "")
        refs = any(set(_rels(p)) & {q["slug"] for q in pages} for p in pages)
        risks: list = []
        if verdict == "equivalent":
            risks.append("search_merge_semantics")
            if any(p["same_as"] for p in entry["pages"]):
                risks.append("same_as_conflict")
        if refs:
            risks.append("cross_kb_reference_break")
        entry["required_risks"] = risks
        if pair_key in {tuple(sorted([a, b])) for a, b in unrelated}:
            suppressed.append({**entry, "reason": "已在 mappings.json 里声明 unrelated"})
        else:
            candidates.append(entry)
    return _finish_scan(ctx, picked, pages_by_kb, candidates, suppressed, concept, skip,
                        scanned, limit, write)


def _finish_scan(ctx, picked, pages_by_kb, candidates, suppressed, concept, skip,
                 scanned, limit, write) -> dict:
    """排序 + ticket + 写扫描报告（只写 `state/context_map/`）。"""
    candidates.sort(key=lambda c: ({"equivalent": 0, "unknown": 1, "distinct": 2}[c["verdict_suggestion"]],
                                   -float(c["confidence"]), c["id"]))
    suppressed.sort(key=lambda c: c["id"])
    ticket = _sha1("ctx-v1|" + json.dumps(sorted(
        (kb["id"], p["slug"], int(p.get("version") or 1), text_hash(p.get("head") or ""))
        for kb in picked for p in pages_by_kb[kb["id"]]), ensure_ascii=False))
    scan_id = "cs-%s" % datetime.now().strftime("%Y%m%d-%H%M%S")
    report = {
        "scan_id": scan_id, "ticket": ticket, "generated_at": ke_db.now_text(),
        "method": "ctx-v1（R0 完全一致 / R1 结构同构 / R2 3-gram 文本相似；一期不含 LLM）",
        "kb_ids": [k["id"] for k in picked], "contexts": ctx["contexts"], "concept": concept,
        "skipped_kb_ids": sorted(x for x in skip if x),
        "stats": {"kb_count": len(picked), "pages_scanned": scanned,
                  "candidates": len(candidates), "suppressed": len(suppressed),
                  "equivalent": sum(1 for c in candidates if c["verdict_suggestion"] == "equivalent"),
                  "distinct": sum(1 for c in candidates if c["verdict_suggestion"] == "distinct"),
                  "unknown": sum(1 for c in candidates if c["verdict_suggestion"] == "unknown"),
                  "auto_distinct_eligible": sum(1 for c in candidates if c["auto_eligible"])},
        "candidates": candidates[:int(limit)], "suppressed": suppressed[:int(limit)],
        "permission": "read_only",
        "note": ("一期只读：本报告**不写任何 wiki 页/页面元数据**；写路径（概念页/映射页 + 两段式 "
                 "apply/rollback + 写权限校验）见 docs/context-mapping-plan.md §7 / §13"),
    }
    if write:
        path = SCAN_DIR / ("%s.json" % scan_id)
        _json_dump(path, report)
        _json_dump(LATEST_FILE, {"scan_id": scan_id, "ticket": ticket,
                                 "generated_at": report["generated_at"], "stats": report["stats"],
                                 "file": str(path.relative_to(REPO))})
        report["wrote"] = str(path.relative_to(REPO))
    return report


def latest_scan() -> dict:
    """最近一次扫描的摘要（没跑过就返回 `{scan_id: ""}`）。"""
    return _json_load(LATEST_FILE, {"scan_id": "", "note": "还没跑过 context scan"}) or {}


def _one_page(kb_id: str, slug: str) -> dict | None:
    """取单页完整内容（渲染用）。"""
    cols = _page_columns()
    summary = "COALESCE(summary,'')" if "summary" in cols else "''"
    rows = ke_db.psql_csv(
        "SELECT slug, COALESCE(title,'') AS title, COALESCE(page_type,'') AS page_type, "
        "%s AS summary, COALESCE(content,'') AS content, COALESCE(page_metadata::text,'{}') AS meta, "
        "COALESCE(out_links::text,'[]') AS out_links, COALESCE(version,1) AS version "
        "  FROM wiki_pages WHERE knowledge_base_id = %s AND slug = %s AND deleted_at IS NULL LIMIT 1"
        % (summary, ke_db.sql_str(kb_id), ke_db.sql_str(slug)))
    return rows[0] if rows else None


def _section(content: str, header: str) -> str:
    """取 markdown 某小节的正文：命中 `header` 后开始收集，**遇到下一个任意级别的标题就停**
    （否则 `## 标准定义` 会把 `### 子小节` 也算进去）。"""
    out, grab = [], False
    for line in (content or "").splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            if grab and stripped != header.strip():
                break
            grab = stripped == header.strip()
            if grab:
                continue
        if grab:
            out.append(line)
    return "\n".join(out).strip()


def _table_rows(content: str, header: str) -> list:
    """把概念页「## 各领域映射」小节解析成表格行（`[{表头: 值}]`）。"""
    rows, header_cells = [], []
    for line in _section(content, header).splitlines():
        line = line.strip()
        if not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if all(set(c) <= set("-: ") for c in cells):
            continue
        if not header_cells:
            header_cells = cells
            continue
        rows.append(dict(zip(header_cells, cells)))
    return rows


def _concept_lookup(slug: str) -> dict:
    """按 **slug 同名**（归一化）在「企业共享概念模型」库里找概念页。"""
    ckb_info = concept_kb()
    ckb = ckb_info.get("id") or ""
    if not ckb:
        return {"exists": False, "kb": "", "kb_name": "", "slug": slug}
    page = _one_page(ckb, slug)
    if page is None:
        rows = ke_db.psql_csv(
            "SELECT slug FROM wiki_pages WHERE knowledge_base_id = %s AND deleted_at IS NULL "
            " AND lower(regexp_replace(slug, '\\s+', '', 'g')) = lower(%s) LIMIT 1"
            % (ke_db.sql_str(ckb), ke_db.sql_str(norm_slug(slug))))
        if rows:
            page = _one_page(ckb, rows[0]["slug"])
    if page is None:
        return {"exists": False, "kb": ckb, "kb_name": ckb_info.get("name", ""), "slug": slug,
                "note": "概念库里还没有这一页（二期 `concept/apply` 生成；同名 slug 才能被查询到）"}
    return {"exists": True, "kb": ckb, "kb_name": ckb_info.get("name", ""),
            "slug": page["slug"], "title": page["title"], "page_type": page["page_type"],
            "version": int(page.get("version") or 1),
            "standard_definition": _section(page["content"], "## 标准定义"),
            "mapping_rows": _table_rows(page["content"], "## 各领域映射")}


def page_view(kb_id: str = "", slug: str = "", q: str = "") -> dict:
    """领域页 ←（**同名 slug**）→「企业共享概念模型」页 的**渲染视图**（只读）。

    用户口径（2026-09-28 二次确认）：
      · 领域库**不写 uuid**、也**不互相引用**；关联完全靠"按 slug 同名查询概念库"；
      · 跨域关系必须**经企业共享概念转换**（A 领域页 → 概念页 → B 领域页），页面之间不直接连边；
      · 本接口只**渲染**：概念页的标准名称/定义 + 各领域映射 + 同名领域页 + 经概念的中转关系。
    """
    slug = (slug or "").strip()
    if not slug and q:
        rows = ke_db.psql_csv(
            "SELECT slug FROM wiki_pages WHERE deleted_at IS NULL AND (slug ILIKE %s OR title ILIKE %s) "
            " ORDER BY slug LIMIT 1" % (ke_db.sql_str("%" + q + "%"), ke_db.sql_str("%" + q + "%")))
        slug = rows[0]["slug"] if rows else ""
    if not slug:
        raise ValueError("page_view 需要 slug（或 q）")
    me = _one_page(kb_id, slug) if kb_id else None
    if me is None and kb_id:
        raise ValueError("本库没有这一页：%s/%s" % (kb_id[:8], slug))

    concept = concept_kb()
    concept_page = _concept_lookup(slug)
    names = {k["id"]: k["name"] for k in kb_rows()}
    concept_id = concept.get("id") or ""
    ont_id = ke_ontology.resolve_ontology_kb().get("id") or ""
    peers_rows = ke_db.psql_csv(
        "SELECT knowledge_base_id AS kb, slug, COALESCE(title,'') AS title, "
        "       COALESCE(page_type,'') AS page_type, COALESCE(version,1) AS version "
        "  FROM wiki_pages WHERE deleted_at IS NULL "
        "   AND lower(regexp_replace(slug, '\\s+', '', 'g')) = lower(%s) LIMIT 30"
        % ke_db.sql_str(norm_slug(slug)))
    peers = [{"context": "kb:%s" % r["kb"][:8], "kb": r["kb"], "kb_name": names.get(r["kb"], ""),
              "slug": r["slug"], "title": r["title"], "page_type": r["page_type"],
              "version": int(r["version"] or 1),
              "role": ("concept" if r["kb"] == concept_id else
                       "ontology" if r["kb"] == ont_id else "domain")}
             for r in peers_rows if r["kb"] not in (ont_id,)]
    keys = {("kb:%s" % r["kb"][:8], r["slug"]) for r in peers_rows}
    mappings = _mappings_touching(keys) if keys else []

    # 跨库直接引用（**禁止**）：本页出边里指向"只存在于别的库"的 slug
    forbidden: list = []
    if me and kb_id:
        mine = {r["slug"] for r in ke_db.psql_csv(
            "SELECT DISTINCT slug FROM wiki_pages WHERE deleted_at IS NULL AND knowledge_base_id = %s "
            " LIMIT 5000" % ke_db.sql_str(kb_id))}
        others = {r["slug"] for r in ke_db.psql_csv(
            "SELECT DISTINCT slug FROM wiki_pages WHERE deleted_at IS NULL AND knowledge_base_id <> %s "
            " LIMIT 5000" % ke_db.sql_str(kb_id))}
        for target in sorted(_rels(me) - mine):
            if target in others:
                forbidden.append({"target": target,
                                  "why": "该 slug 只存在于**别的领域库** → 领域库不得互相引用，"
                                         "必须经「企业共享概念模型」同名概念页转换"})
    warnings: list = []
    if not concept_page.get("exists"):
        warnings.append({"kind": "concept_page_missing", "severity": "medium",
                         "detail": "概念库里没有同名页 `%s`（按 slug 同名查不到）→ 无法渲染企业标准定义" % slug,
                         "next": "二期 `context/concept/preview → apply`（有写权限者）生成概念页"})
    if forbidden:
        warnings.append({"kind": "cross_kb_reference_forbidden", "severity": "high",
                         "detail": "本页引用了别的领域库的 slug（%s）" % "、".join(f["target"] for f in forbidden),
                         "next": "改为经概念页转换：先在本库建立/指向本库页，再由概念页映射到其它领域"})
    domains = [p for p in peers if p["role"] == "domain"]
    if len(domains) >= 2 and not concept_page.get("exists") and not mappings:
        warnings.append({"kind": "same_name_no_decision", "severity": "high",
                         "detail": "同名页分布在 %d 个领域上下文，且既没有企业概念页、也没有 ACL 映射"
                                   % len(domains), "next": "context_scan → 裁决 → 二期 apply"})
    return {
        "kb_id": kb_id, "slug": slug,
        "page": ({"slug": me["slug"], "title": me["title"], "page_type": me["page_type"],
                  "version": int(me.get("version") or 1)} if me else None),
        "concept_page": concept_page, "concept_kb": concept,
        "peers": peers, "mappings": mappings, "forbidden_cross_kb_refs": forbidden,
        "warnings": warnings, "generated_at": ke_db.now_text(), "permission": "read_only",
        "rule": ("领域库不写 uuid、不互相引用；关系统一按 **slug 同名** 查概念库，"
                 "跨域关系只能**经企业共享概念页转换**（docs/context-mapping-plan.md §13.11）"),
    }


def _concept_page_body(primary: dict, members: list[dict], verdict: str = "equivalent",
                       definition: str = "") -> str:
    """概念页正文：**标准定义** + 各领域定义摘录 + **各领域映射**表（表里用 `code span` 而不是 wiki 链接
    —— 概念库不引用领域库的页，避免悬空链接/跨库边；关系的渲染一律由 `page_view()` 查询完成）。

    `verdict`：`equivalent`（同义 → 一张企业标准定义）/ `distinct`（同名异义 → 页首写明"同名但各领域含义不同"）。
    """
    head = ["## 标准定义", ""]
    if verdict == "distinct":
        head += ["> ⚠️ **同名异义**：本 slug 在不同领域上下文里**含义不同**（下表逐域列出）；",
                 "> 跨域引用**必须**经本页转换，不得假定同义。", ""]
    head += [(definition or primary.get("definition") or primary.get("summary") or "").strip()
             or "（待补：企业标准定义）", "", "### 各领域定义摘录", ""]
    for m in members:
        text = (m.get("definition") or m.get("summary") or "").strip()[:200] or "—"
        head.append("- **%s**（`%s`）：%s" % (m["kb_name"], m["slug"], text))
    head += ["", "## 各领域映射", "",
             "| 上下文 | 页 slug | 类 | 版本 | 结论 | 说明 |", "|---|---|---|---|---|---|"]
    for m in members:
        default_note = ("同名同义（待复核）" if verdict == "equivalent"
                        else "**同名异义**：本域含义与其他域不同（请在此列写明差异）")
        head.append("| %s | `%s` | %s | %d | %s | %s |"
                    % (m["kb_name"], m["slug"], m["page_type"], int(m.get("version") or 1),
                       m.get("mapping") or verdict, m.get("note") or default_note))
    head += ["", "> 本页是「企业共享概念模型」里的**企业标准概念**：领域库按 **slug 同名** 查询到它，",
             "> 领域库之间**不直接引用**，跨域关系一律**经本页转换**（docs/context-mapping-plan.md §13.11）。",
             "> 本页的「各领域映射」表是**映射关系的事实源**；`state/context_map/*.json` 只是它的缓存。"]
    return "\n".join(head)


def concept_preview(slug: str = "", include_unknown: bool = False, limit: int = 50,
                    allow_distinct: bool = False) -> dict:
    """**只读预览**：二期 `concept/apply` 会在「企业共享概念模型」里生成/更新哪些概念页。

    - 概念页 `slug` = **与领域页同名**（用户口径：不写 uuid、靠同名 slug 查询）；
    - `page_type` = 该同名组的本体类（遵本体约束）；正文 = 标准定义 + 各领域映射表；
    - 一组同义（`equivalent`）才默认纳入；`unknown` 需 `include_unknown=true`；`distinct` 永不生成概念页
      （而是登记 ACL 映射）。本函数**不写任何东西**，只给 preview + ticket。
    """
    concept = concept_kb()
    members_by_key: dict = {}
    if slug:
        key = norm_slug(slug)
        rows = ke_db.psql_csv(
            "SELECT knowledge_base_id AS kb, slug, COALESCE(title,'') AS title, "
            "       COALESCE(page_type,'') AS page_type, COALESCE(summary,'') AS summary, "
            "       COALESCE(out_links::text,'[]') AS out_links, left(COALESCE(content,''),1500) AS head, "
            "       COALESCE(content,'') AS content, "
            "       COALESCE(version,1) AS version, COALESCE(page_metadata::text,'{}') AS meta "
            "  FROM wiki_pages WHERE deleted_at IS NULL "
            "   AND lower(regexp_replace(slug, '\\s+', '', 'g')) = lower(%s) LIMIT 30"
            % ke_db.sql_str(key)) if "summary" in _page_columns() else []
        members_by_key[key] = rows
        verdicts = {key: ""}                      # "" = 现场算（见下）
    else:
        latest = latest_scan()
        report = _json_load(REPO / str(latest.get("file") or ""), {}) or {}
        verdicts = {}
        for cand in (report.get("candidates") or []):
            v = str(cand.get("verdict_suggestion") or "")
            if v != "equivalent" and not (include_unknown and v == "unknown") \
                    and not (allow_distinct and v == "distinct"):
                continue
            key = norm_slug(str((cand.get("pages") or [{}])[0].get("slug") or ""))
            if not key:
                continue
            verdicts[key] = v
            members_by_key[key] = ke_db.psql_csv(
                "SELECT knowledge_base_id AS kb, slug, COALESCE(title,'') AS title, "
                "       COALESCE(page_type,'') AS page_type, '' AS summary, "
                "       COALESCE(out_links::text,'[]') AS out_links, "
                "       left(COALESCE(content,''),1500) AS head, COALESCE(content,'') AS content, "
                "       COALESCE(version,1) AS version, COALESCE(page_metadata::text,'{}') AS meta "
                "  FROM wiki_pages WHERE deleted_at IS NULL "
                "   AND lower(regexp_replace(slug, '\\s+', '', 'g')) = lower(%s) LIMIT 30"
                % ke_db.sql_str(key))
    names = {k["id"]: k["name"] for k in kb_rows()}
    ont_id = ke_ontology.resolve_ontology_kb().get("id") or ""
    skip = {concept.get("id") or "", ont_id}
    pages: list = []
    skipped: list = []
    for key, rows in members_by_key.items():
        members = [{"context": "kb:%s" % r["kb"][:8], "kb": r["kb"], "kb_name": names.get(r["kb"], ""),
                    "slug": r["slug"], "title": r["title"], "page_type": r["page_type"],
                    "summary": r.get("summary") or "", "version": int(r["version"] or 1),
                    "head": r.get("head") or "", "out_links": r.get("out_links") or "[]",
                    "definition": _definition_from_content(r.get("content") or r.get("head") or ""),
                    "same_as": (_meta_dict(r).get("same_as") or {})}
                   for r in rows if r["kb"] not in skip]
        contexts = {m["context"] for m in members}
        if len(members) < 2 or len(contexts) < 2:
            skipped.append({"key": key, "why": "跨库同名页不足 2 个上下文", "pages": len(members)})
            continue
        primary = sorted(members, key=lambda m: (-m["version"], m["slug"]))[0]
        slug_new = primary["slug"]                       # 与领域页**同名**
        exists = _one_page(concept.get("id") or "", slug_new) is not None
        verdict = verdicts.get(key) or _verdict(_signals(
            [{"title": m["title"], "page_type": m["page_type"], "summary": m.get("summary", ""),
              "head": m.get("head", ""), "out_links": m.get("out_links", "[]")} for m in members]))[0]
        risks = ["concept_page_rewrite"] if exists else []
        if any(m["same_as"] for m in members):
            risks.append("same_as_conflict")
        pages.append({
            "slug": slug_new, "title": primary["title"] or slug_new,
            "page_type": primary["page_type"], "exists": exists,
            "verdict": verdict,
            "members": members, "required_risks": risks,
            "body_md": _concept_page_body(primary, members, verdict),
        })
    pages.sort(key=lambda p: p["slug"])
    ticket = _sha1("concept-v1|%s|%s" % (concept.get("id") or "", json.dumps(
        sorted((m["kb"], m["slug"], m["version"]) for p in pages for m in p["members"]),
        ensure_ascii=False)))
    return {
        "mode": "dry-run（只读预览，不写任何页）", "generated_at": ke_db.now_text(),
        "concept_kb": concept, "ticket": ticket, "permission": "read_only",
        "stats": {"pages": len(pages), "new": sum(1 for p in pages if not p["exists"]),
                  "update": sum(1 for p in pages if p["exists"]), "skipped": len(skipped)},
        "pages": pages[:int(limit)], "skipped": skipped[:int(limit)],
        "apply_hint": ("二期 `POST /bodhi/context/concept/apply`（带 ticket + acknowledge_risks；"
                       "需对「企业共享概念模型」库有写权限）才会真正写页"),
        "note": "概念页 slug 与领域页同名；领域库不写 uuid、不互相引用，跨域关系经概念页转换",
    }


def concept_apply(slug: str, ticket: str = "", acknowledge_risks: list | None = None,
                  definition: str = "", standard_name: str = "", actor: str = "",
                  tenant: int | None = None) -> dict:
    """**二期写路径**：把某个同名组的「企业共享概念模型」概念页**建/改**出来（版本化、可回滚）。

    口径（用户 2026-09-28）：
      · **映射关系的事实源 = 概念页正文的「各领域映射」表**（不是 JSON；JSON 只是缓存）；
      · 概念页 `slug` 与领域页**同名**；`page_type` = 该组的本体类（遵本体约束）；
      · **领域库零写入**（不改领域页、不动其版本）；概念页版本化（`version+1` + 快照，可回退）；
      · 守门：写权限（`ke_db.assert_can_write`，目标是概念库）+ ticket（影响面指纹）+ 风险确认。
    """
    concept = concept_kb()
    ckb = concept.get("id") or ""
    if not ckb:
        return {"error": "还没有「企业共享概念模型」库（先建库，见 docs/context-mapping-plan.md §13.11）",
                "need_kb": True, "concept": concept}
    acl = ke_db.assert_can_write(ckb, tenant if tenant is not None else ke_db.caller_tenant())
    if not acl["allowed"]:
        return {"error": "need_write_permission", "permission": acl,
                "hint": "只有对企业共享概念模型库有写权限的租户才能 apply（他人只读 + 提建议）"}
    prev = concept_preview(slug, allow_distinct=True, limit=1)
    pages = prev.get("pages") or []
    if not pages:
        return {"error": "该 slug 不构成跨库同名组（<2 个上下文），无需生成概念页", "slug": slug,
                "skipped": prev.get("skipped")}
    page = pages[0]
    if str(prev.get("ticket") or "") != str(ticket or ""):
        return {"error": "ticket 不匹配（影响面已变化或未先 preview）→ need_repreview=true",
                "need_repreview": True, "ticket": prev.get("ticket"), "slug": slug}
    want = sorted(page.get("required_risks") or [])
    if sorted(acknowledge_risks or []) != want:
        return {"error": "风险确认不一致：需 acknowledge_risks=%s" % want,
                "required_risks": want, "slug": slug}
    body = _concept_page_body(sorted(page["members"], key=lambda m: (-m["version"], m["slug"]))[0],
                              page["members"], page["verdict"], definition)
    before = _one_page(ckb, page["slug"])
    written = ke_pages.upsert_page(ckb, page["slug"], standard_name or page["title"],
                                   page["page_type"], body, summary=definition or "",
                                   tag="bodhi-cxt-edit",
                                   metadata={"concept": {
                                       "generated_by": "context-map", "verdict": page["verdict"],
                                       "actor": actor or "unknown",
                                       "members": [{"context": m["context"], "slug": m["slug"],
                                                    "version": m["version"]} for m in page["members"]]}})
    cache = rebuild_cache()
    HISTORY_DIR.mkdir(parents=True, exist_ok=True)
    record = {"ticket": prev["ticket"], "slug": page["slug"], "at": ke_db.now_text(),
              "actor": actor or "unknown", "verdict": page["verdict"], "concept_kb": concept,
              "created": written["created"], "page_version": written["after_version"],
              "before_content": (before or {}).get("content", ""), "after_content": body,
              "members": page["members"], "permission": acl}
    path = HISTORY_DIR / ("%s.json" % prev["ticket"])
    _json_dump(path, record)
    return {"ok": True, "applied": True, "concept_kb": concept, "slug": page["slug"],
            "verdict": page["verdict"],
            "page": {"created": written["created"], "version": written["after_version"],
                     "page_type": page["page_type"]},
            "permission": acl, "cache": cache.get("stats"),
            "record": str(path.relative_to(REPO)),
            "note": "领域库零写入（不动领域页/版本）；映射表就是事实源；缓存已刷新"}


def concept_rollback(ticket: str) -> dict:
    """按 apply 留下的记录回滚概念页（恢复旧正文或删除新建页），并刷新缓存。"""
    path = HISTORY_DIR / ("%s.json" % (ticket or ""))
    if not path.is_file():
        return {"error": "没有这条 apply 记录：%s" % ticket}
    rec = _json_load(path, {}) or {}
    ckb = rec.get("concept_kb", {}).get("id") if isinstance(rec.get("concept_kb"), dict) else ""
    ckb = ckb or (concept_kb().get("id") or "")
    slug = rec.get("slug") or ""
    member = (rec.get("members") or [{}])[0]
    if not (ckb and slug):
        return {"error": "记录不完整（缺 concept_kb/slug）"}
    if rec.get("created"):
        ke_pages.delete_pages(ckb, [slug], dry_run=False)
        action = "deleted"
    else:
        ke_pages.upsert_page(ckb, slug, member.get("title") or slug,
                             member.get("page_type") or "ontology:Class",
                             rec.get("before_content") or "", tag="bodhi-cxt-edit")
        action = "restored"
    cache = rebuild_cache()
    return {"ok": True, "rolled_back": True, "action": action, "slug": slug, "ticket": ticket,
            "cache": cache.get("stats")}


def read_cache() -> dict:
    """读映射缓存（`state/context_map/mappings.json`）+ 失效检测（概念页版本/指纹变了 → `stale`）。"""
    data = _json_load(MAPPINGS_FILE, {}) or {}
    if not data:
        return {"kind": "cache", "pairs": [], "generated_from": [], "stale": [], "built_at": "",
                "note": "还没有缓存（跑 `rebuild_cache` 或 concept_apply 生成）"}
    stale = []
    ckb = (data.get("concept_kb") or {}).get("id") or (concept_kb().get("id") or "")
    for item in (data.get("generated_from") or []):
        page = _one_page(item.get("kb") or ckb, item.get("slug") or "")
        if page is None:
            stale.append({"slug": item.get("slug"), "why": "概念页已删"})
        elif int(page.get("version") or 1) != int(item.get("version") or 0) \
                or text_hash(page.get("content") or "") != (item.get("hash") or ""):
            stale.append({"slug": item.get("slug"), "why": "概念页已改（缓存过期）"})
    data["stale"] = stale
    data["stale_count"] = len(stale)
    return data


def rebuild_cache() -> dict:
    """**刷新缓存**：把概念页上的「各领域映射」表解析成 `mappings.json`（企业级）+ 每个领域库一份局部缓存。

    缓存是**技术产物**：给渲染/查询加速；**事实源始终是概念页正文**；**不写任何领域页、不动其版本**。
    """
    concept = concept_kb()
    ckb = concept.get("id") or ""
    rows = ke_db.psql_csv(
        "SELECT slug, COALESCE(title,'') AS title, COALESCE(version,1) AS version, "
        "       COALESCE(content,'') AS content "
        "  FROM wiki_pages WHERE deleted_at IS NULL AND knowledge_base_id = %s ORDER BY slug"
        % ke_db.sql_str(ckb)) if ckb else []
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    pairs: list = []
    generated_from: list = []
    per_kb: dict = {}
    names = {k["id"]: k["name"] for k in kb_rows()}
    for row in rows:
        table = _table_rows(row["content"], "## 各领域映射")
        if not table:
            continue
        generated_from.append({"kb": ckb, "slug": row["slug"], "version": int(row["version"] or 1),
                               "hash": text_hash(row["content"])})
        nodes = []
        for line in table:
            slug = str(line.get("页 slug") or "").strip("` ")
            kb_name = str(line.get("上下文") or "")
            kb_id = next((k for k, v in names.items() if v == kb_name), "")
            raw_v = str(line.get("版本") or "")
            if kb_id and slug:
                nodes.append({"context": "kb:%s" % kb_id[:8], "kb": kb_id, "kb_name": kb_name,
                              "slug": slug, "page_type": str(line.get("类") or ""),
                              "mapping": str(line.get("结论") or "equivalent"),
                              "version": int(raw_v) if raw_v.isdigit() else 0})
        for i, a in enumerate(nodes):
            for b in nodes[i + 1:]:
                pairs.append({
                    "id": "m:%s:%s→%s:%s" % (a["context"], a["slug"], b["context"], b["slug"]),
                    "concept_slug": row["slug"], "concept_kb": ckb,
                    "source": {"context": a["context"], "kb": a["kb"], "slug": a["slug"],
                               "type": a["page_type"]},
                    "target": {"context": b["context"], "kb": b["kb"], "slug": b["slug"],
                               "type": b["page_type"]},
                    "mapping": a["mapping"] or "equivalent", "decided_by": "concept-page",
                    "state": "active"})
        for node in nodes:
            per_kb.setdefault(node["context"], []).append(
                {"kb_id": node["kb"], "slug": node["slug"], "concept_slug": row["slug"],
                 "concept_kb": ckb, "mapping": node["mapping"],
                 "peers": [p["slug"] for p in nodes if p["slug"] != node["slug"]]})
    payload = {"version": 1, "kind": "cache", "built_at": ke_db.now_text(), "concept_kb": concept,
               "generated_from": generated_from, "pairs": pairs,
               "note": "缓存（程序产物）：事实源是概念页正文的「各领域映射」表"}
    _json_dump(MAPPINGS_FILE, payload)
    for ctx_key, items in per_kb.items():
        _json_dump(CACHE_DIR / ("%s.json" % ctx_key.replace(":", "-")),
                   {"kind": "cache", "context": ctx_key,
                    "kb_id": items[0].get("kb_id", ""), "built_at": payload["built_at"],
                    "concept_kb": concept, "pages": items})
    return {"ok": True, "cache": str(MAPPINGS_FILE.relative_to(REPO)),
            "stats": {"concept_pages": len(generated_from), "pairs": len(pairs),
                      "kb_caches": len(per_kb)},
            "note": "领域库零写入；缓存可随时重建"}


def lookup(kb_ids: list[str] | None = None, q: str = "", slug: str = "", limit: int = 20) -> dict:
    """检索前查（只读）：同名组 / 同义概念（`same_as`）/ 异义映射 / 依赖页 / 警告。

    - `slug`：精确查某一页在**各库**的同名页（跨库同名组）；
    - `q`：按标题 / slug 模糊找页，再各自展开同名组；
    - `warnings`：如 `same_name_no_decision` = 跨库同名但**没有任何裁决**
      （既没挂企业标准概念、也没 ACL 映射）← 跨库引用最危险的场景。
    """
    q, slug = (q or "").strip(), (slug or "").strip()
    if not q and not slug:
        raise ValueError("lookup 需要 q 或 slug")
    sql_cols = ("SELECT knowledge_base_id AS kb, slug, COALESCE(title,'') AS title, "
                "       COALESCE(page_type,'') AS page_type, COALESCE(version,1) AS version, "
                "       COALESCE(page_metadata::text,'{}') AS meta "
                "  FROM wiki_pages WHERE deleted_at IS NULL ")
    rows = []
    if slug:
        rows = ke_db.psql_csv(sql_cols + "AND slug = %s LIMIT %d"
                              % (ke_db.sql_str(slug), int(limit)))
    if not rows and q:
        like = ke_db.sql_str("%" + q + "%")
        rows = ke_db.psql_csv(sql_cols + "AND (slug ILIKE %s OR title ILIKE %s) ORDER BY slug LIMIT %d"
                              % (like, like, int(limit)))
    names = {k["id"]: k["name"] for k in kb_rows()}
    concept = concept_kb()

    def _group(key: str) -> list:
        return ke_db.psql_csv(
            sql_cols + "AND lower(regexp_replace(slug, '\\s+', '', 'g')) = lower(%s) LIMIT 20"
            % ke_db.sql_str(key))

    hits: list = []
    warnings: list = []
    cache: dict = {}
    for row in rows:
        key = norm_slug(row["slug"])
        nodes = cache.setdefault(key, _group(key))
        ctxs = ["kb:%s" % r["kb"][:8] for r in nodes]
        meta_keys = {("kb:%s" % r["kb"][:8], r["slug"]) for r in nodes}
        same_as = [_meta_dict(r).get("same_as") for r in nodes if _meta_dict(r).get("same_as")]
        maps = _mappings_touching(meta_keys)
        hits.append({
            "context": "kb:%s" % row["kb"][:8], "kb": row["kb"],
            "kb_name": names.get(row["kb"], ""), "slug": row["slug"], "title": row["title"],
            "page_type": row["page_type"], "version": int(row["version"] or 1),
            "same_as": _meta_dict(row).get("same_as") or {},
            "same_name_group": [{"context": "kb:%s" % r["kb"][:8], "kb": r["kb"],
                                 "kb_name": names.get(r["kb"], ""), "slug": r["slug"],
                                 "title": r["title"], "page_type": r["page_type"],
                                 "version": int(r["version"] or 1),
                                 "same_as": _meta_dict(r).get("same_as") or {}} for r in nodes],
            "distinct_contexts": len(set(ctxs)),
            "concept_targets": sorted({str(s.get("concept_slug") or "") for s in same_as if s}),
            "mappings": maps,
        })
        if len(set(ctxs)) >= 2 and not same_as and not maps:
            warnings.append({"kind": "same_name_no_decision", "severity": "high", "slug": row["slug"],
                             "detail": "跨库同名（%s）在 %d 个上下文里都有，但**没有任何裁决**"
                                       "（既无 same_as 企业标准概念、也无 ACL 映射）→ 跨库引用会误读"
                                       % (row["slug"], len(set(ctxs))),
                             "next": "context_scan 拿 ticket → 由有写权限的人/智能体做 concept/mapping apply（二期）"})
    for hit in hits[:10]:
        like = "%" + '"' + str(hit["slug"]) + '"' + "%"      # out_links 是 jsonb 数组文本：匹配 "slug"
        dep = ke_db.psql_csv(
            "SELECT knowledge_base_id AS kb, slug FROM wiki_pages WHERE deleted_at IS NULL "
            " AND out_links::text LIKE %s LIMIT 10" % ke_db.sql_str(like))
        if dep:
            hit["depended_by"] = [{"context": "kb:%s" % d["kb"][:8], "kb": d["kb"],
                                   "kb_name": names.get(d["kb"], ""), "slug": d["slug"]} for d in dep]
    return {"query": {"q": q, "slug": slug, "kb_ids": kb_ids or []},
            "generated_at": ke_db.now_text(), "concept": concept, "latest_scan": latest_scan(),
            "hits": hits, "warnings": warnings, "permission": "read_only",
            "note": "一期只读：只查不写；跨库引用前先用本接口确认同义/异义"}


def _mappings_touching(keys: set) -> list:
    """`mappings.json` 里涉及这些 `(context, slug)` 的映射（一期通常为空）。"""
    data = _json_load(MAPPINGS_FILE, {}) or {}
    out = []
    for row in (data.get("pairs") or []):
        src, dst = row.get("source") or {}, row.get("target") or {}
        if (src.get("context"), src.get("slug")) in keys or (dst.get("context"), dst.get("slug")) in keys:
            out.append(row)
    return out


# ---------------------------------------------------------------------------
# CLI（只读）
# ---------------------------------------------------------------------------
def main() -> int:
    ap = argparse.ArgumentParser(description="跨库上下文映射（一期只读）")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("contexts", help="上下文注册表（只读）")
    p_scan = sub.add_parser("scan", help="全库同名/同实例候选（只读；只写 state/context_map）")
    p_scan.add_argument("kb_ids", nargs="?", default="", help="逗号分隔的库；省略=全部")
    p_scan.add_argument("--limit", type=int, default=200)
    p_scan.add_argument("--page-limit", type=int, default=5000)
    p_scan.add_argument("--no-write", action="store_true", help="不落扫描报告")
    p_look = sub.add_parser("lookup", help="检索前查同义/异义/依赖（只读）")
    p_look.add_argument("kb_ids", nargs="?", default="", help="逗号分隔（仅作回显）")
    p_look.add_argument("--slug", default="")
    p_look.add_argument("--q", default="")
    p_page = sub.add_parser("page", help="领域页 ←同名 slug→ 概念页 的渲染视图（只读）")
    p_page.add_argument("kb_id", nargs="?", default="", help="本库（给 slug 时用于判定跨库引用）")
    p_page.add_argument("--slug", default="")
    p_page.add_argument("--q", default="")
    p_prev = sub.add_parser("concept-preview", help="概念页生成预览（dry-run，只读）")
    p_prev.add_argument("slug", nargs="?", default="", help="只预览某个 slug 组；省略=按最近一次扫描的 equivalent 组")
    p_prev.add_argument("--include-unknown", action="store_true")
    p_prev.add_argument("--allow-distinct", action="store_true", help="把同名异义（distinct）组也纳入")
    p_app = sub.add_parser("concept-apply", help="**写**：把某同名组落成概念页（需 ticket + 风险 + 写权限）")
    p_app.add_argument("slug")
    p_app.add_argument("--ticket", default="")
    p_app.add_argument("--ack", default="", help="逗号分隔的风险确认，须与 preview 的 required_risks 一致")
    p_app.add_argument("--definition", default="", help="企业标准定义（不填则用该组首选页的摘要）")
    p_app.add_argument("--name", default="", help="企业标准名称（不填则用标题）")
    p_app.add_argument("--actor", default="", help="操作者标识（记进概念页元数据与记录）")
    p_app.add_argument("--tenant", type=int, default=None, help="调用租户（省略则读 env BODHI_TENANT_ID）")
    p_rb = sub.add_parser("concept-rollback", help="**写**：按 apply 记录回滚概念页")
    p_rb.add_argument("ticket")
    sub.add_parser("cache-rebuild", help="**写**：刷新映射缓存（mappings.json + 各库局部缓存）")
    sub.add_parser("cache-show", help="只读：看缓存内容与失效情况")
    p_print = ap.add_argument("--print", type=int, default=6000, help="打印字符上限")
    for _p in list(sub.choices.values()):        # 所有子命令都接受 --print（写法随意）
        _p.add_argument("--print", dest="print", type=int, default=int(p_print.default),
                        help="打印字符上限（也可写在子命令前）")
    args = ap.parse_args()
    if args.cmd == "contexts":
        out = contexts()
    elif args.cmd == "scan":
        kb_ids = [x.strip() for x in (args.kb_ids or "").split(",") if x.strip()]
        out = scan(kb_ids or None, limit=args.limit, page_limit=args.page_limit,
                   write=not args.no_write)
    elif args.cmd == "page":
        out = page_view(args.kb_id or "", slug=args.slug, q=args.q)
    elif args.cmd == "concept-preview":
        out = concept_preview(args.slug or "", include_unknown=bool(args.include_unknown),
                              allow_distinct=bool(args.allow_distinct))
    elif args.cmd == "concept-apply":
        out = concept_apply(args.slug, ticket=args.ticket,
                            acknowledge_risks=[x.strip() for x in (args.ack or "").split(",") if x.strip()],
                            definition=args.definition, standard_name=args.name, actor=args.actor,
                            tenant=args.tenant)
    elif args.cmd == "concept-rollback":
        out = concept_rollback(args.ticket)
    elif args.cmd == "cache-rebuild":
        out = rebuild_cache()
    elif args.cmd == "cache-show":
        out = read_cache()
    else:
        kb_ids = [x.strip() for x in (args.kb_ids or "").split(",") if x.strip()]
        out = lookup(kb_ids or None, q=args.q, slug=args.slug)
    print(json.dumps(out, ensure_ascii=False, indent=2)[:int(args.print)])
    return 0


if __name__ == "__main__":
    sys.exit(main())
