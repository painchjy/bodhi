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
MAPPINGS_FILE = STATE_DIR / "mappings.json"
LATEST_FILE = STATE_DIR / "latest_scan.json"

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
    p_print = ap.add_argument("--print", type=int, default=6000, help="打印字符上限")
    for _p in (sub.choices["contexts"], p_scan, p_look):
        _p.add_argument("--print", dest="print", type=int, default=int(p_print.default),
                        help="打印字符上限（也可写在子命令前）")
    args = ap.parse_args()
    if args.cmd == "contexts":
        out = contexts()
    elif args.cmd == "scan":
        kb_ids = [x.strip() for x in (args.kb_ids or "").split(",") if x.strip()]
        out = scan(kb_ids or None, limit=args.limit, page_limit=args.page_limit,
                   write=not args.no_write)
    else:
        kb_ids = [x.strip() for x in (args.kb_ids or "").split(",") if x.strip()]
        out = lookup(kb_ids or None, q=args.q, slug=args.slug)
    print(json.dumps(out, ensure_ascii=False, indent=2)[:int(args.print)])
    return 0


if __name__ == "__main__":
    sys.exit(main())
