"""ke-core · 知识运维「一致性巡检」只读内核（P1，2026-09-20 用户口径）。

三件事（用户原话）：
1. 分析**现有 wiki 与本体图谱**的一致性问题；
2. 分析**本体图谱与本体模型**的不一致问题；
3. **无来源文档**的 wiki 页 / 本体图节点与关系 = **异常数据** → 排查后提供清理。

本模块是 P1：**纯只读**（一条写语句都没有）。清理/修复（P2）会另加白名单函数，
语义见 `docs/bodhi-ops-audit.md`。

数据源（与全仓口径一致）
------------------------
- 实例层就在 PG：`wiki_pages`（`page_type` / 正文 `## 本体关系` / `out_links` / `in_links` /
  `page_metadata.ontology` / `source_refs`）、`knowledges`（文档行，软删 = `deleted_at`）；
- 本体模型 = Neo4j 投影（`BodhiOntClass` / `BodhiOntProperty` / `BODHI_DOMAIN|RANGE`），
  经 `ke_ontology`（类清单、按类关系类型**含父类继承**、range 闭包）；
- Neo4j `BodhiInstance` 只有 CLI 抽取路径会写，当前部署为空 —— C4 只报数。

检查清单（编号与会话里确认的一致）
----------------------------------
A（wiki ↔ 图谱）：A1 悬空出边 / A2 反向边(in_links)不一致 / A3 类型自相矛盾 /
  A4 关系类型非法(domain 继承) / A5 重复关系行与自环 / A6 元数据缺失
B（图谱 ↔ 模型）：B1 类型不在模型 / B2 关系不在模型 / B3 range 违反 /
  B4（本体模型库）投影与页不一致
C（来源异常）：C1 无来源实例页 / C2 来源文档已删或不存在 / C3 正文称有来源但 `source_refs` 空 /
  C4 图侧实例无溯源
D（重复/幂等）：D1 同语义多页 / D2 软删残留与活页并存 / D3 孤儿版本快照

用法
----
    /opt/bodhi-venv/bin/python3 tools/ke-core/ke_audit.py scan <kb_id> [--scope all] [--compact]
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import re
import sys

HERE = pathlib.Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import ke_db  # noqa: E402
import ke_neo4j  # noqa: E402
import ke_ontology  # noqa: E402
import ke_pages  # noqa: E402

try:                                              # ke_docs 与 ke_audit 同在 ke-core
    import ke_docs  # noqa: E402
except Exception:  # noqa: BLE001
    ke_docs = None  # type: ignore

SEV = {"high": 0, "medium": 1, "low": 2}
SCOPES = ("all", "wiki", "model", "source", "dupes")
# 本体模型库 id（与 ke_admin.ONTOLOGY_KB 同源；这里不 import ke_admin，避免连带依赖）
ONTOLOGY_KB = os.environ.get("ONTOLOGY_KB_ID", "08810cbd-af86-48d1-bd25-3b2c338e3d68")
# 结构性/上游页：不属于「实例页」，C1/A6/B1 一律豁免
NON_INSTANCE = ("index", "summary")
NON_INSTANCE_PREFIX = ("ontology:",)
# 已知页面生成器（`last_edit_source`）；其它值 = 元数据漂移（A6，低）
KNOWN_SOURCES = ("bodhi-onto-mcp", "ontology-wiki", "bodhi-type-edit", "bodhi-rel-edit",
                 "bodhi-page-del", "bodhi-ops-edit", "pipeline", "agent", "")


def _is_instance(page_type: str) -> bool:
    """实例页 = `模块:类`（如 `bmm:Goal`）；本体模型库的 `ontology:*` 与 index/summary 不算。"""
    pt = (page_type or "").strip()
    if not pt or pt in NON_INSTANCE:
        return False
    if pt.startswith(NON_INSTANCE_PREFIX):
        return False
    return ":" in pt


def _norm_title(title: str) -> str:
    return re.sub(r"[\s\-_（）()【】\[\]：:,.，。·]+", "", (title or "").lower())


# ---------------------------------------------------------------------------
# 数据装载（只读）
# ---------------------------------------------------------------------------
def load_pages(kb_id: str, limit: int = 5000) -> tuple[list[dict], bool]:
    rows = ke_db.psql_csv(
        "SELECT slug, COALESCE(title,'') AS title, COALESCE(page_type,'') AS page_type, "
        "       COALESCE(content,'') AS content, COALESCE(page_metadata::text,'{}') AS meta, "
        "       COALESCE(source_refs::text,'[]') AS refs, COALESCE(chunk_refs::text,'[]') AS chunks, "
        "       COALESCE(out_links::text,'[]') AS out_links, COALESCE(in_links::text,'[]') AS in_links, "
        "       COALESCE(version,1) AS version, COALESCE(last_edit_source,'') AS src, "
        "       COALESCE(updated_at::text,'') AS updated_at "
        "  FROM wiki_pages WHERE knowledge_base_id = %s AND deleted_at IS NULL "
        " ORDER BY slug LIMIT %d" % (ke_db.sql_str(kb_id), int(limit) + 1))
    truncated = len(rows) > int(limit)
    return rows[:int(limit)], truncated


def _json_list(text: str) -> list:
    try:
        val = json.loads(text or "[]")
    except json.JSONDecodeError:
        return []
    return val if isinstance(val, list) else []


def _page_meta(text: str) -> dict:
    try:
        meta = json.loads(text or "{}")
    except json.JSONDecodeError:
        return {}
    if not isinstance(meta, dict):
        return {}
    ont = meta.get("ontology")
    return ont if isinstance(ont, dict) else {}


def model_view() -> dict:
    """本体模型视图：类集合 / 对象属性集合 / 类元数据（Neo4j 优先，json 兜底）。"""
    classes, source = set(), "neo4j"
    try:
        data = ke_ontology.classes()
        classes = {c["prefixed"] for c in (data.get("classes") or []) if c.get("prefixed")}
        source = data.get("source") or "neo4j"
    except Exception as exc:  # noqa: BLE001
        source = "json: %s" % exc
        for model in (ke_ontology.index_data().get("models") or []):
            for cls in (model.get("classes") or []):
                if cls.get("name"):
                    classes.add(cls["name"])
    relations = set()
    try:
        for row in ke_neo4j.query(
                "MATCH (p:BodhiOntProperty) WHERE p.bodhi_projection = 'ontology' "
                "AND p.property_kind = 'object' AND p.prefixed IS NOT NULL "
                "RETURN DISTINCT p.prefixed AS n"):
            if row.get("n"):
                relations.add(row["n"])
    except Exception:  # noqa: BLE001
        for model in (ke_ontology.index_data().get("models") or []):
            for rel in (model.get("relations") or []):
                if rel.get("name"):
                    relations.add(rel["name"])
    return {"classes": classes, "relations": relations, "meta": ke_ontology.class_meta(),
            "source": source}


# ---------------------------------------------------------------------------
# 报告累加器
# ---------------------------------------------------------------------------
# P2 才能自动修的（这里只作标注，P1 不写）：A1 删悬空关系行 / A2 重算 in_links /
# A5 去重关系行 / C1、C2 清理异常页 / D3 清孤儿快照
FIXABLE = {"A1": True, "A2": True, "A5": True, "C1": True, "C2": True, "D3": True}


class Report:
    def __init__(self, kb_id: str, scope: str, max_findings: int):
        self.kb_id = kb_id
        self.scope = scope
        self.max = max(1, int(max_findings))
        self.findings: list = []
        self.totals: dict = {}
        self.sev: dict = {"high": 0, "medium": 0, "low": 0}
        self.data: dict = {}

    def add(self, check: str, severity: str, subject: str, detail: str, fix_hint: str = "") -> None:
        self.totals[check] = self.totals.get(check, 0) + 1
        self.sev[severity] = self.sev.get(severity, 0) + 1
        if len(self.findings) < self.max:
            self.findings.append({"check": check, "severity": severity, "subject": subject,
                                  "detail": detail, "fix_hint": fix_hint,
                                  "fixable": FIXABLE.get(check, False)})


# ---------------------------------------------------------------------------
# A. wiki ↔ 本体图谱
# ---------------------------------------------------------------------------
def check_wiki_graph(ctx: dict, rep: Report) -> None:
    pages, live = ctx["pages"], ctx["live"]
    # A1 悬空出边：正文「## 本体关系」解析出的目标 + out_links 列，指向不存在的页
    for page in pages:
        rels = ke_pages.parse_out_relations(page["content"])
        targets = {r["slug"] for r in rels if r["slug"]}
        targets |= {s for s in _json_list(page["out_links"]) if isinstance(s, str)}
        missing = sorted(targets - live)
        if missing:
            rep.add("A1", "high", page["slug"],
                    "悬空出边 %d 条 → %s" % (len(missing), "、".join(missing[:5])),
                    "删正文对应关系行（或补建缺失页）后再重算 in_links")
    # A2 反向边不一致：期望的 in_links 由所有页的出边推导
    expected: dict = {}
    for page in pages:
        rels = ke_pages.parse_out_relations(page["content"])
        targets = {r["slug"] for r in rels if r["slug"]}
        targets |= {s for s in _json_list(page["out_links"]) if isinstance(s, str)}
        for target in targets:
            expected.setdefault(target, set()).add(page["slug"])
    for page in pages:
        have = {s for s in _json_list(page["in_links"]) if isinstance(s, str)}
        want = expected.get(page["slug"], set())
        if have != want:
            rep.add("A2", "medium", page["slug"],
                    "in_links 不一致：缺 %d（%s）／多 %d（%s）"
                    % (len(want - have), "、".join(sorted(want - have)[:3]) or "-",
                       len(have - want), "、".join(sorted(have - want)[:3]) or "-"),
                    "重算 in_links（ke_pages.rebuild_in_links_sql，幂等）")
    # A3 类型自相矛盾：page_type vs page_metadata.ontology.class/type
    for page in pages:
        if not _is_instance(page["page_type"]):
            continue
        meta = _page_meta(page["meta"])
        cls = str(meta.get("class") or meta.get("type") or "").strip()
        if not cls:
            continue
        model = str(meta.get("model") or "").strip()
        full = cls if ":" in cls else ("%s:%s" % (model, cls) if model else cls)
        if full and full != page["page_type"]:
            rep.add("A3", "medium", page["slug"],
                    "page_type=%s 与元数据 class=%s 不一致" % (page["page_type"], full),
                    "以 page_type 为准修元数据（或反之）——需人确认")
    # A5 重复关系行 / 自环；A6 元数据缺失或未知生成器
    for page in pages:
        rels = ke_pages.parse_out_relations(page["content"])
        counter: dict = {}
        for rel in rels:
            counter[(rel["type"], rel["slug"])] = counter.get((rel["type"], rel["slug"]), 0) + 1
        for (rtype, slug), n in counter.items():
            if n > 1:
                rep.add("A5", "low", page["slug"],
                        "重复关系行 %s → %s（%d 次）" % (rtype, slug, n), "去重该行（保留一条）")
            if slug and slug == page["slug"]:
                rep.add("A5", "low", page["slug"], "自环关系：%s → 自己" % rtype, "删该行")
        if not _is_instance(page["page_type"]):
            continue
        if not _page_meta(page["meta"]):
            rep.add("A6", "low", page["slug"], "缺 page_metadata.ontology（老数据/手写页）",
                    "按需补元数据（非必须）")
        elif page["src"] not in KNOWN_SOURCES:
            rep.add("A6", "low", page["slug"], "未知生成器 last_edit_source=%s" % page["src"],
                    "确认来源，必要时补记生成器")


# ---------------------------------------------------------------------------
# B. 本体图谱 ↔ 本体模型
# ---------------------------------------------------------------------------
def check_graph_model(ctx: dict, rep: Report) -> None:
    classes, relations = ctx["model"]["classes"], ctx["model"]["relations"]
    allowed_cache: dict = {}
    range_cache: dict = {}
    for page in ctx["pages"]:
        pt = page["page_type"]
        if _is_instance(pt) and pt not in classes:
            rep.add("B1", "high", page["slug"],
                    "类型 %s 不在本体模型（%s）里" % (pt, ctx["model"]["source"]),
                    "补本体模型（TTL → load）或把该页改成合法类型/删除")
        rels = ke_pages.parse_out_relations(page["content"])
        if not rels:
            continue
        for rel in rels:
            rtype, target_slug = rel["type"], rel["slug"]
            if rtype and rtype not in relations:
                rep.add("B2", "high", page["slug"], "关系类型 %s 不在本体模型里" % rtype,
                        "先确认是否拼写错误/模块未加载；补模型或删该关系")
                continue
            # A4 domain（含父类继承）——顺路在这里判，避免重复遍历
            if pt in classes:
                if pt not in allowed_cache:
                    allowed_cache[pt] = ke_ontology.relation_type_map(pt)
                if rtype not in allowed_cache[pt]:
                    rep.add("A4", "high", page["slug"],
                            "关系 %s 不属于 %s 的 domain（含继承）" % (rtype, pt),
                            "改用该类合法关系；或在本体模型补 domain 声明")
            # B3 range 闭包
            target = ctx["by_slug"].get(target_slug) if target_slug else None
            if not target:
                continue
            tpt = target["page_type"]
            if not (_is_instance(tpt) and tpt in classes):
                continue
            if rtype in range_cache:
                allowed_ranges = range_cache[rtype]
            else:
                allowed_ranges = range_cache[rtype] = set(ke_ontology.target_closure(rtype))
            if allowed_ranges and tpt not in allowed_ranges:
                rep.add("B3", "high", page["slug"],
                        "range 违反：%s 连到 %s，模型允许 %s"
                        % (rtype, tpt, "、".join(sorted(allowed_ranges)[:5])),
                        "改关系类型/目标页，或以模型 range 声明为准")
    if ctx["kb_id"] == ctx["ontology_kb"]:
        check_model_kb_pages(ctx, rep)


def _local_name(type_name: str) -> str:
    """`bmm:CourseOfAction` → `courseofaction`（与 ontology_wiki.slug_class 同规则）。"""
    raw = (type_name or "").split(":", 1)[-1]
    return re.sub(r"[^a-z0-9]+", "-", raw.lower()).strip("-")


def check_model_kb_pages(ctx: dict, rep: Report) -> None:
    """B4（本体模型库）：Neo4j 里的模块/类 与 `ontology:*` 页是否一一对应。"""
    try:
        rows = ke_neo4j.query(
            "MATCH (c:BodhiOntClass) WHERE c.bodhi_projection = 'ontology' "
            "AND coalesce(c.external, false) = false AND c.prefixed IS NOT NULL "
            "RETURN DISTINCT c.prefixed AS name, coalesce(c.module,'') AS module")
        mods = [r["m"] for r in ke_neo4j.query("MATCH (m:BodhiModule) RETURN m.key AS m") if r.get("m")]
    except Exception as exc:  # noqa: BLE001
        rep.add("B4", "medium", ctx["kb_id"], "无法读 Neo4j 投影：%s" % exc, "先恢复本体投影")
        return
    want = {}
    for row in rows:
        module = (row.get("module") or "").strip()
        name = row.get("name") or ""
        if module and name:
            want["ontology/%s/%s" % (module, _local_name(name))] = "ontology:Class"
    for key in mods:
        want["ontology/%s" % key] = "ontology:Module"
    have = {p["slug"]: p["page_type"] for p in ctx["pages"]}
    missing = [s for s, t in want.items() if have.get(s) != t]
    # 总览页 `ontology/index` 是设计如此（`ontology:Module`），不算"多余"
    extra = [(s, t) for s, t in have.items()
             if t in ("ontology:Class", "ontology:Module") and s not in want
             and s != "ontology/index"]
    if missing:
        rep.add("B4", "medium", ctx["kb_id"],
                "模型库缺 %d 个页（Neo4j 投影里有）：%s" % (len(missing), "、".join(sorted(missing)[:6])),
                "重投影：POST /bodhi/ontology/wiki（或 load {compile:false}）")
    if extra:
        rep.add("B4", "medium", ctx["kb_id"],
                "模型库多 %d 个页（Neo4j 里没有对应类/模块）：%s"
                % (len(extra), "、".join(sorted(s for s, _ in extra)[:6])),
                "按 §6.4 口径剔除过期页（load/wikiregen 会做）")


# ---------------------------------------------------------------------------
# C. 来源异常（用户口径：无来源 / 来源已删 = 异常数据）
# ---------------------------------------------------------------------------
def check_sources(ctx: dict, rep: Report) -> None:
    pages = ctx["pages"]
    for page in pages:
        refs = _json_list(page["refs"])
        claims = bool(re.search(r"（来源[:：]", page["content"] or "")) or "<sources>" in (page["content"] or "")
        if _is_instance(page["page_type"]) and not refs:
            rep.add("C1", "high", page["slug"],
                    "实例页无来源文档（source_refs 为空）%s" % ("；正文却有来源标记" if claims else ""),
                    "确认后清理（P2）：删该页并重算 in_links；误判可先 --strip-only")
        elif not refs and claims and not _is_instance(page["page_type"]):
            rep.add("C3", "low", page["slug"],
                    "正文有来源标记但 source_refs 为空（非实例页，上游维护）",
                    "上游页不在本工具清理范围；如需纳入请先确认口径")
    docs = ctx.get("docs") or {}
    orphan_docs = [d for d in (docs.get("docs") or [])
                   if d.get("status") in ("deleted", "missing")]
    ctx["data"]["sources"] = docs.get("totals") or {}
    for doc in orphan_docs:
        sample = []
        if ke_docs is not None:
            try:
                sample = [p["slug"] for p in ke_docs.pages_of_doc(ctx["kb_id"], doc["knowledge_id"])][:5]
            except Exception:  # noqa: BLE001
                sample = []
        rep.add("C2", "high", doc["knowledge_id"],
                "来源文档《%s》%s，仍被 %d 页引用（独占 %d）：%s"
                % (doc.get("title") or "(无标题)", "已删除" if doc.get("status") == "deleted"
                   else "不存在", doc.get("pages") or 0, doc.get("exclusive_pages") or 0,
                   "、".join(sample)),
                "用 ke_docs：purge/sweep（P2 会接到同一个工具里）")
    # C4 图侧实例溯源（当前部署 BodhiInstance 为空，只报数）
    try:
        total = int((ke_neo4j.query("MATCH (n:BodhiInstance) RETURN count(n) AS n")
                     or [{"n": 0}])[0].get("n") or 0)
        bad = int((ke_neo4j.query(
            "MATCH (n:BodhiInstance) WHERE n.knowledge_id IS NULL AND n.source_doc IS NULL "
            "RETURN count(n) AS n") or [{"n": 0}])[0].get("n") or 0)
        ctx["data"]["neo4j_instances"] = {"total": total, "without_source": bad}
        if bad:
            rep.add("C4", "medium", "neo4j",
                    "%d/%d 个 BodhiInstance 节点没有任何溯源（knowledge_id/source_doc 均为空）" % (bad, total),
                    "按实例层口径补溯源或清掉（CLI 抽取路径才会写）")
    except Exception as exc:  # noqa: BLE001
        ctx["data"]["neo4j_instances"] = {"error": str(exc)}


# ---------------------------------------------------------------------------
# D. 重复 / 幂等残留
# ---------------------------------------------------------------------------
def check_dupes(ctx: dict, rep: Report) -> None:
    kb_id = ctx["kb_id"]
    groups: dict = {}
    for page in ctx["pages"]:
        if not _is_instance(page["page_type"]):
            continue
        groups.setdefault((page["page_type"], _norm_title(page["title"])), []).append(page["slug"])
    for (ptype, _norm), slugs in sorted(groups.items()):
        if len(slugs) > 1:
            rep.add("D1", "medium", slugs[0],
                    "同类型同语义多页（%s，%d 个）：%s"
                    % (ptype, len(slugs), "、".join(sorted(slugs)[:5])),
                    "人工合并（保留一页、把另一页的出边并过去）")
    # D2 软删旧行与活页并存（重复抽取撞主键的历史根因）
    rows = ke_db.psql_csv(
        "SELECT slug, count(*) AS n FROM wiki_pages "
        " WHERE knowledge_base_id = %s AND deleted_at IS NOT NULL GROUP BY slug"
        % ke_db.sql_str(kb_id))
    dupes = [(r["slug"], int(r["n"] or 0)) for r in rows if r["slug"] in ctx["live"]]
    ctx["data"]["soft_deleted_dupes"] = len(dupes)
    for slug, n in dupes[:20]:
        rep.add("D2", "medium", slug, "有 %d 条软删旧行 + 当前活页同在" % n,
                "可硬删旧行（P2，硬删才彻底）；不影响展示")
    # D3 孤儿版本快照
    rows = ke_db.psql_csv(
        "SELECT count(*) AS n FROM wiki_page_revisions r WHERE r.knowledge_base_id = %s "
        " AND NOT EXISTS (SELECT 1 FROM wiki_pages p WHERE p.knowledge_base_id = r.knowledge_base_id "
        "                 AND p.id = r.page_id)" % ke_db.sql_str(kb_id))
    orphan_rev = int((rows or [{"n": 0}])[0]["n"] or 0)
    ctx["data"]["orphan_revisions"] = orphan_rev
    if orphan_rev:
        rep.add("D3", "low", kb_id, "%d 条版本快照没有对应页（孤儿快照）" % orphan_rev,
                "可清（P2 白名单）")


# ---------------------------------------------------------------------------
# P2：计划(只读) → 人工确认 → 执行（**硬删**；用户 2026-09-20 口径：不得自动修）
# ---------------------------------------------------------------------------
PURGE_KINDS = ("no_source_pages", "deleted_source_pages", "mixed_source_refs",
               "soft_deleted_rows", "orphan_revisions")
FIX_KINDS = ("dangling_edges", "dup_edges", "in_links")
INIT_KINDS = ("init_wiki", "init_graph")
ALL_KINDS = INIT_KINDS + PURGE_KINDS + FIX_KINDS
KIND_HELP = {
    "init_wiki": "初始化-清空 wiki（该 KB 全部页与快照、目录、问题项；**保留索引页 index**）",
    "init_graph": "初始化-清空图谱（Neo4j 里该 KB 的本体实例节点/边）",
    "no_source_pages": "无来源的实例页（C1）→ 删（并级联删指向它的关系行）",
    "deleted_source_pages": "来源文档已删/不存在的页（C2，引用全删的）→ 删（并级联删指向它的关系行）",
    "mixed_source_refs": "多源页摘掉已删文档的引用（保留仍有活来源的页）",
    "soft_deleted_rows": "软删旧行（D2，同 slug 与活页并存的残留记录）→ 硬删",
    "orphan_revisions": "孤儿版本快照（D3，没有对应页）→ 删",
    "dangling_edges": "悬空关系行（A1）→ 删该行（快照+版本+1）",
    "dup_edges": "重复/自环关系行（A5）→ 去重（快照+版本+1）",
    "in_links": "重算 in_links（A2，幂等，无内容改动）",
}
PLAN_DIR = HERE.parents[1] / "logs" / "audit"


def _q(value) -> str:
    return ke_db.sql_str(value)


def _plan_id(payload: dict) -> str:
    """计划指纹：对「kb + kinds + 动作清单」做稳定哈希（数据一变指纹就变 → 拒绝执行）。"""
    import hashlib
    body = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(body.encode("utf-8")).hexdigest()[:16]


def _instance_nodes(kb_id: str) -> int:
    """Neo4j 里该 KB 的实例节点数（按 kb 属性或该 KB 的文档 id）。当前部署通常为 0。"""
    try:
        ids = [r["id"] for r in ke_db.psql_csv(
            "SELECT id FROM knowledges WHERE knowledge_base_id = %s" % _q(kb_id))]
        rows = ke_neo4j.query(
            "MATCH (n:BodhiInstance) WHERE n.kb = $kb OR n.knowledge_id IN $ids "
            "RETURN count(n) AS n", {"kb": kb_id, "ids": ids})
        return int((rows or [{"n": 0}])[0].get("n") or 0)
    except Exception:  # noqa: BLE001
        return 0


def _hard_delete_wiki(kb_id: str) -> dict:
    """初始化：硬删该 KB 的 wiki 数据，**保留索引页 `index`**（用户口径：只留文档与索引页）。

    顺序：版本快照 → 问题项 → 页（保留 index）→ 目录（随后由 `sync_folders` 按活页重建）；
    最后重算 in_links。**不可逆**。
    """
    where = ("knowledge_base_id = %s AND slug <> 'index'" % _q(kb_id))
    before = {
        "pages_live": int(ke_db.psql_csv(
            "SELECT count(*) AS n FROM wiki_pages WHERE knowledge_base_id = %s AND deleted_at IS NULL"
            % _q(kb_id))[0]["n"] or 0),
        "pages_all": int(ke_db.psql_csv(
            "SELECT count(*) AS n FROM wiki_pages WHERE knowledge_base_id = %s"
            % _q(kb_id))[0]["n"] or 0),
        "revisions": int(ke_db.psql_csv(
            "SELECT count(*) AS n FROM wiki_page_revisions WHERE knowledge_base_id = %s"
            % _q(kb_id))[0]["n"] or 0),
        "folders": int(ke_db.psql_csv(
            "SELECT count(*) AS n FROM wiki_folders WHERE knowledge_base_id = %s"
            % _q(kb_id))[0]["n"] or 0),
        "issues": int(ke_db.psql_csv(
            "SELECT count(*) AS n FROM wiki_page_issues WHERE knowledge_base_id = %s"
            % _q(kb_id))[0]["n"] or 0),
    }
    ke_db.psql("BEGIN;\n"
               "DELETE FROM wiki_page_revisions WHERE %s;\n"
               "DELETE FROM wiki_page_issues WHERE knowledge_base_id = %s;\n"
               "DELETE FROM wiki_pages WHERE %s;\n"
               "DELETE FROM wiki_folders WHERE knowledge_base_id = %s;\n"
               "COMMIT;\n" % (where, _q(kb_id), where, _q(kb_id)), stdin=True)
    out = {"deleted": before, "index_kept": True}
    try:
        out["folders_synced"] = bool(ke_pages.sync_folders(kb_id))
    except Exception as exc:  # noqa: BLE001
        out["folders_synced"] = "failed: %s" % exc
    ke_db.psql(ke_pages.rebuild_in_links_sql(kb_id), stdin=True)
    out["in_links_rebuilt"] = True
    return out


def _hard_delete_graph(kb_id: str) -> dict:
    """初始化：删 Neo4j 里该 KB 的本体实例（节点 + 其边）。"""
    try:
        ids = [r["id"] for r in ke_db.psql_csv(
            "SELECT id FROM knowledges WHERE knowledge_base_id = %s" % _q(kb_id))]
        before = _instance_nodes(kb_id)
        if before:
            ke_neo4j.query("MATCH (n:BodhiInstance) WHERE n.kb = $kb OR n.knowledge_id IN $ids "
                           "DETACH DELETE n", {"kb": kb_id, "ids": ids})
        return {"documents": len(ids), "nodes_deleted": before}
    except Exception as exc:  # noqa: BLE001
        return {"error": str(exc)}


def _hard_delete_soft_rows(kb_id: str) -> dict:
    """硬删「软删旧行」及其快照（同 slug 与活页并存的残留）。"""
    rows = ke_db.psql_csv(
        "SELECT slug FROM wiki_pages WHERE knowledge_base_id = %s AND deleted_at IS NOT NULL"
        % _q(kb_id))
    slugs = sorted({r["slug"] for r in rows})
    if not slugs:
        return {"deleted": 0}
    lst = ", ".join(_q(s) for s in slugs)
    ke_db.psql("BEGIN;\n"
               "DELETE FROM wiki_page_revisions WHERE knowledge_base_id = %s AND slug IN (%s);\n"
               "DELETE FROM wiki_pages WHERE knowledge_base_id = %s AND deleted_at IS NOT NULL;\n"
               "COMMIT;\n" % (_q(kb_id), lst, _q(kb_id)), stdin=True)
    return {"deleted": len(slugs)}


def _delete_orphan_revisions(kb_id: str) -> dict:
    out = ke_db.psql(
        "WITH gone AS (DELETE FROM wiki_page_revisions r WHERE r.knowledge_base_id = %s "
        " AND NOT EXISTS (SELECT 1 FROM wiki_pages p WHERE p.knowledge_base_id = r.knowledge_base_id "
        "                 AND p.id = r.page_id) RETURNING 1) SELECT count(*) AS n FROM gone;"
        % _q(kb_id))
    return {"deleted": int((out.strip().splitlines() or ["0"])[0] or 0)}


def _apply_edge_edits(kb_id: str, page: dict, drop_lines: list) -> dict:
    """按行号删掉正文里的关系行（快照 + 版本+1 + 重算 out_links/in_links）。"""
    drop = {int(i) for i in drop_lines}
    lines = [line for i, line in enumerate((page["content"] or "").splitlines()) if i not in drop]
    new_content = "\n".join(lines).rstrip() + "\n"
    res = ke_pages.rewrite_page_content(kb_id, page["slug"], new_content)
    return {"slug": page["slug"], "removed_lines": len(drop), "version": res["after_version"]}


def _rebuild_in_links(kb_id: str) -> dict:
    ke_db.psql(ke_pages.rebuild_in_links_sql(kb_id), stdin=True)
    return {"rebuilt": True}


def _normalize_kinds(kinds) -> list:
    if not kinds:
        raise ValueError("plan 需要 kinds（all / init / 或具体 kind，见 KIND_HELP）")
    if isinstance(kinds, str):
        kinds = [k for k in kinds.replace(" ", "").split(",") if k]
    out: list = []
    for kind in kinds:
        kind = str(kind).strip()
        if kind == "all":
            out += list(PURGE_KINDS) + list(FIX_KINDS)
        elif kind == "init":
            out += list(INIT_KINDS)
        elif kind in ALL_KINDS:
            out.append(kind)
        else:
            raise ValueError("未知 kind：%s（可用：all / init / %s）" % (kind, "、".join(ALL_KINDS)))
    return sorted(set(out))


def _collect(kb_id: str, kinds: list, page_limit: int = 5000) -> dict:
    """只读地算出每个 kind 要动的东西（初始化 / 清理 / 修复）。"""
    pages, truncated = load_pages(kb_id, page_limit)
    live = {p["slug"] for p in pages}
    actions: dict = {}
    edits: dict = {}

    if "init_wiki" in kinds:
        counts = {}
        for table in ("wiki_pages", "wiki_page_revisions", "wiki_folders", "wiki_page_issues"):
            n = int(ke_db.psql_csv(
                "SELECT count(*) AS n FROM %s WHERE knowledge_base_id = %s" % (table, _q(kb_id))
            )[0]["n"] or 0)
            counts[table] = n
        live_pages = int(ke_db.psql_csv(
            "SELECT count(*) AS n FROM wiki_pages WHERE knowledge_base_id = %s "
            " AND deleted_at IS NULL" % _q(kb_id))[0]["n"] or 0)
        # 用户口径：初始化后**只保留文档和索引页** → `slug='index'` 永不删
        index_kept = "index" in live
        actions["init_wiki"] = {"pages_live": live_pages, "pages_all": counts["wiki_pages"],
                                "pages_to_delete": counts["wiki_pages"] - (1 if index_kept else 0),
                                "index_kept": index_kept,
                                "revisions": counts["wiki_page_revisions"],
                                "folders": counts["wiki_folders"],
                                "issues": counts["wiki_page_issues"],
                                "note": "删该 KB 全部 wiki 页与快照，**保留索引页 index**；硬删不可逆"}
    if "init_graph" in kinds:
        actions["init_graph"] = {"nodes": _instance_nodes(kb_id),
                                 "note": "Neo4j BodhiInstance（当前部署通常为 0）"}
    if "no_source_pages" in kinds:
        slugs = sorted(p["slug"] for p in pages
                       if _is_instance(p["page_type"]) and not _json_list(p["refs"]))
        actions["no_source_pages"] = {"count": len(slugs), "slugs": slugs}
    if "deleted_source_pages" in kinds or "mixed_source_refs" in kinds:
        res = (ke_docs.residue(kb_id) if ke_docs is not None
               else {"will_delete": [], "will_strip": {}, "doc_ids": []})
        if "deleted_source_pages" in kinds:
            actions["deleted_source_pages"] = {"count": len(res["will_delete"]),
                                               "slugs": res["will_delete"],
                                               "docs": res["doc_ids"]}
        if "mixed_source_refs" in kinds:
            actions["mixed_source_refs"] = {"count": len(res["will_strip"]),
                                            "slugs": sorted(res["will_strip"]),
                                            "docs": res["doc_ids"]}
    if "soft_deleted_rows" in kinds:
        rows = ke_db.psql_csv(
            "SELECT slug, count(*) AS n FROM wiki_pages WHERE knowledge_base_id = %s "
            " AND deleted_at IS NOT NULL GROUP BY slug ORDER BY slug" % _q(kb_id))
        slugs = [r["slug"] for r in rows]
        actions["soft_deleted_rows"] = {"count": len(slugs), "slugs": slugs,
                                        "coexist_with_live": sum(1 for s in slugs if s in live)}
    # __TAIL__
    if "orphan_revisions" in kinds:
        rows = ke_db.psql_csv(
            "SELECT r.slug AS slug FROM wiki_page_revisions r WHERE r.knowledge_base_id = %s "
            " AND NOT EXISTS (SELECT 1 FROM wiki_pages p WHERE p.knowledge_base_id = r.knowledge_base_id "
            "                 AND p.id = r.page_id) GROUP BY r.slug ORDER BY r.slug" % _q(kb_id))
        actions["orphan_revisions"] = {"count": len(rows), "slugs": [r["slug"] for r in rows][:50]}
    if "dangling_edges" in kinds or "dup_edges" in kinds:
        for page in pages:
            rels = ke_pages.parse_out_relations(page["content"])
            if not rels:
                continue
            drop: dict = {}
            seen: dict = {}
            for rel in rels:
                key = (rel["type"], rel["slug"])
                if "dangling_edges" in kinds and rel["slug"] and rel["slug"] not in live:
                    drop[rel["line_index"]] = "悬空 → %s" % rel["slug"]
                    continue
                if "dup_edges" in kinds:
                    if rel["slug"] and rel["slug"] == page["slug"]:
                        drop[rel["line_index"]] = "自环"
                        continue
                    if key in seen:
                        drop[rel["line_index"]] = "重复（首行 %d）" % (seen[key] + 1)
                        continue
                    seen[key] = rel["line_index"]
            if drop:
                edits[page["slug"]] = {"lines": sorted(drop),
                                       "why": {str(k): v for k, v in sorted(drop.items())}}
        if "dangling_edges" in kinds:
            actions["dangling_edges"] = {
                "pages": sum(1 for e in edits.values() if any("悬空" in v for v in e["why"].values())),
                "lines": sum(1 for e in edits.values() for v in e["why"].values() if "悬空" in v)}
        if "dup_edges" in kinds:
            actions["dup_edges"] = {
                "pages": sum(1 for e in edits.values() if any("悬空" not in v for v in e["why"].values())),
                "lines": sum(1 for e in edits.values() for v in e["why"].values() if "悬空" not in v)}
    if "in_links" in kinds:
        tmp = Report(kb_id, "wiki", 1)
        check_wiki_graph({"kb_id": kb_id, "pages": pages, "live": live,
                          "by_slug": {p["slug"]: p for p in pages}, "data": {},
                          "model": None, "docs": None, "ontology_kb": ONTOLOGY_KB}, tmp)
        actions["in_links"] = {"count": tmp.totals.get("A2", 0)}

    # 级联：删页时会一并删掉**指向这些页的关系行**（用户口径：节点清理后关系一起删）
    delete_slugs: set = set()
    if "init_wiki" in kinds:
        delete_slugs |= {s for s in live if s != "index"}
    for kind in ("no_source_pages", "deleted_source_pages"):
        if kind in kinds:
            delete_slugs |= set(actions[kind]["slugs"])
    cascade: dict = {}
    if delete_slugs:
        survivors = live - delete_slugs
        for page in pages:
            if page["slug"] not in survivors:
                continue
            drop: dict = {}
            for rel in ke_pages.parse_out_relations(page["content"]):
                if rel["slug"] and rel["slug"] in delete_slugs:
                    drop[rel["line_index"]] = "目标页将被删（%s）" % rel["slug"]
            if drop:
                cascade[page["slug"]] = {"lines": sorted(drop),
                                         "why": {str(k): v for k, v in sorted(drop.items())}}
        actions["cascade_edges"] = {
            "pages": len(cascade), "lines": sum(len(c["lines"]) for c in cascade.values()),
            "note": "删页时一并删掉这些**指向被删页**的关系行（级联）；页自己的出边随页一起消失"}
    return {"actions": actions, "edits": edits, "cascade": cascade, "truncated": truncated}


def build_plan(kb_id: str, kinds=None, scope: str = "all", page_limit: int = 5000,
               save: bool = True) -> dict:
    """生成**只读**清理计划（含 `plan_id`）。执行见 `apply_plan` / CLI `apply --confirm`。

    `kinds`：`init`（清空 wiki+图谱）/ `all`（清理异常 + 修一致性问题）/ 或具体 kind
    （见 `KIND_HELP`）。计划会落在 `logs/audit/`，供确认与事后追溯。
    """
    kb_id = (kb_id or "").strip()
    if not kb_id:
        raise ValueError("plan 需要 kb_id")
    # 与 audit 同口径：名称/UUID 都接受，不存在的库直接报错
    kb_id, kb_name, _kb_note = ke_db.resolve_kb_id(kb_id)
    kinds = _normalize_kinds(kinds)
    collected = _collect(kb_id, kinds, page_limit)
    payload = {"kb_id": kb_id, "kinds": kinds, "actions": collected["actions"],
               "edits": collected["edits"], "cascade": collected["cascade"]}
    plan = {
        "plan_id": _plan_id(payload), "kb_id": kb_id, "kb_name": kb_name, "kinds": kinds,
        "created_at": ke_db.now_text(), "mode": "hard-delete（不可逆）",
        "actions": collected["actions"], "edits": collected["edits"],
        "cascade": collected["cascade"], "truncated": collected["truncated"],
        "kinds_help": {k: KIND_HELP.get(k, "") for k in kinds},
        "current": audit(kb_id, scope=scope, max_findings=1)["summary"],
        "execute_hint": ("ke_audit.py apply %s --plan-id %s --confirm" % (kb_id, _plan_id(payload))),
        "note": "计划本身不写数据；执行必须显式确认（confirm=True / --confirm）",
    }
    if save:
        PLAN_DIR.mkdir(parents=True, exist_ok=True)
        path = PLAN_DIR / ("%s-%s.json" % (kb_id[:8], plan["plan_id"]))
        path.write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
        plan["plan_file"] = path.relative_to(HERE.parents[1]).as_posix()
    return plan


def apply_plan(kb_id: str, plan_id: str, confirm: bool = False, page_limit: int = 5000) -> dict:
    """执行已确认的计划（**硬删**）。要求 `confirm=True`，且 `plan_id` 与**当前数据**一致。"""
    if not confirm:
        raise ValueError("拒绝执行：必须显式确认（confirm=True / CLI --confirm）")
    hits = sorted(PLAN_DIR.glob("*-%s.json" % plan_id)) if PLAN_DIR.is_dir() else []
    if not hits:
        raise ValueError("找不到计划 %s —— 先跑 plan（计划落在 logs/audit/）" % plan_id)
    plan = json.loads(hits[0].read_text(encoding="utf-8"))
    if plan.get("kb_id") != kb_id:
        raise ValueError("计划属于另一个知识库：%s" % plan.get("kb_id"))
    kinds = plan.get("kinds") or []
    fresh = _collect(kb_id, kinds, page_limit)
    verify = {"kb_id": kb_id, "kinds": kinds, "actions": fresh["actions"],
              "edits": fresh["edits"], "cascade": fresh["cascade"]}
    if _plan_id(verify) != plan_id:
        raise ValueError("数据已变化（plan_id 不匹配）——请重新 plan 并再次确认")

    applied: dict = {}
    if "init_wiki" in kinds:
        applied["init_wiki"] = _hard_delete_wiki(kb_id)
    if "init_graph" in kinds:
        applied["init_graph"] = _hard_delete_graph(kb_id)
    if "no_source_pages" in kinds and fresh["actions"]["no_source_pages"]["slugs"]:
        applied["no_source_pages"] = ke_pages.delete_pages(
            kb_id, fresh["actions"]["no_source_pages"]["slugs"])
    if "deleted_source_pages" in kinds and fresh["actions"]["deleted_source_pages"]["slugs"]:
        applied["deleted_source_pages"] = ke_pages.delete_pages(
            kb_id, fresh["actions"]["deleted_source_pages"]["slugs"])
    if "mixed_source_refs" in kinds and fresh["actions"]["mixed_source_refs"]["slugs"] \
            and ke_docs is not None:
        applied["mixed_source_refs"] = [
            ke_docs.purge_document(kb_id, knowledge_id=doc_id, apply=True,
                                   sync_folders=False, delete_exclusive=False)["totals"]
            for doc_id in fresh["actions"]["mixed_source_refs"]["docs"]]
    if "soft_deleted_rows" in kinds and fresh["actions"]["soft_deleted_rows"]["count"]:
        applied["soft_deleted_rows"] = _hard_delete_soft_rows(kb_id)
    if "orphan_revisions" in kinds and fresh["actions"]["orphan_revisions"]["count"]:
        applied["orphan_revisions"] = _delete_orphan_revisions(kb_id)
    # 正文改写：一次算齐（悬空/重复关系行 + 级联删边行），同一页只重写一次
    drop_by_slug: dict = {}
    for slug, info in (fresh.get("cascade") or {}).items():
        drop_by_slug.setdefault(slug, set()).update(int(x) for x in info["lines"])
    if "dangling_edges" in kinds or "dup_edges" in kinds:
        for slug, info in (fresh.get("edits") or {}).items():
            for line_no, why in info["why"].items():
                if ("悬空" in why and "dangling_edges" in kinds) or \
                        ("悬空" not in why and "dup_edges" in kinds):
                    drop_by_slug.setdefault(slug, set()).add(int(line_no))
    if drop_by_slug:
        by_slug = {p["slug"]: p for p in load_pages(kb_id, page_limit)[0]}
        done = []
        for slug, lines in sorted(drop_by_slug.items()):
            page = by_slug.get(slug)
            if page and lines:
                done.append(_apply_edge_edits(kb_id, page, sorted(lines)))
        applied["edges"] = {"pages": len(done),
                            "lines": sum(len(v) for v in drop_by_slug.values()),
                            "detail": done[:20]}
    if "in_links" in kinds:
        applied["in_links"] = _rebuild_in_links(kb_id)

    result = {"kb_id": kb_id, "plan_id": plan_id, "kinds": kinds, "applied": applied,
              "executed_at": ke_db.now_text(),
              "after": audit(kb_id, scope="all", max_findings=1)["summary"]}
    plan["executed_at"] = result["executed_at"]
    plan["applied"] = applied
    hits[0].write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


# ---------------------------------------------------------------------------
# 编排 + CLI
# ---------------------------------------------------------------------------
def audit(kb_id: str, scope: str = "all", max_findings: int = 200,
          page_limit: int = 5000) -> dict:
    """只读一致性巡检（**不写任何数据**）。

    scope：`all` / `wiki`(A) / `model`(B) / `source`(C) / `dupes`(D)。
    返回 `{summary, totals, findings[], data}`：`totals` 是**完整计数**，
    `findings` 最多 `max_findings` 条（按严重度排序）。
    """
    kb_id = (kb_id or "").strip()
    if not kb_id:
        raise ValueError("audit 需要 kb_id")
    # 名称/UUID 都接受；**不存在的库必须报错**（否则会给出"0 页 0 问题"的假报告）
    kb_id, kb_name, kb_note = ke_db.resolve_kb_id(kb_id)
    scope = (scope or "all").strip().lower()
    if scope not in SCOPES:
        raise ValueError("scope 只能是 %s" % " / ".join(SCOPES))

    pages, truncated = load_pages(kb_id, page_limit)
    rep = Report(kb_id, scope, max_findings)
    ctx: dict = {"kb_id": kb_id, "pages": pages, "by_slug": {p["slug"]: p for p in pages},
                 "live": {p["slug"] for p in pages}, "model": None, "docs": None,
                 "data": {}, "ontology_kb": ONTOLOGY_KB}

    if scope in ("all", "wiki", "model"):
        ctx["model"] = model_view()
    if scope in ("all", "source") and ke_docs is not None:
        try:
            ctx["docs"] = ke_docs.doc_index(kb_id)
        except Exception as exc:  # noqa: BLE001
            rep.add("C2", "low", kb_id, "来源统计失败：%s" % exc, "检查 ke_docs / 数据库")
    if scope in ("all", "wiki"):
        check_wiki_graph(ctx, rep)
    if scope in ("all", "model"):
        check_graph_model(ctx, rep)
    if scope in ("all", "source"):
        check_sources(ctx, rep)
    if scope in ("all", "dupes"):
        check_dupes(ctx, rep)

    model = ctx["model"]
    rep.data = ctx["data"]
    rep.findings.sort(key=lambda f: (SEV.get(f["severity"], 9), f["check"], f["subject"]))
    return {
        "kb_id": kb_id, "kb_name": kb_name, "scope": scope, "generated_at": ke_db.now_text(),
        "kb_id_note": kb_note,
        "summary": {
            "pages": len(pages),
            "instance_pages": sum(1 for p in pages if _is_instance(p["page_type"])),
            "findings": sum(rep.totals.values()),
            "by_severity": dict(rep.sev),
            "checks": dict(sorted(rep.totals.items())),
            "findings_returned": len(rep.findings), "max_findings": rep.max,
            "page_limit": page_limit, "truncated": truncated,
            "model": ({"source": model["source"], "classes": len(model["classes"]),
                       "relations": len(model["relations"])} if model else None),
            "ontology_kb": kb_id == ONTOLOGY_KB,
        },
        "totals": dict(sorted(rep.totals.items())),
        "findings": rep.findings,
        "data": rep.data,
        "note": "P1 只读：本报告不写任何数据；修复/清理见 docs/bodhi-ops-audit.md（P2）",
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="wiki ↔ 本体图谱 ↔ 本体模型 一致性巡检 / 清理（P1 只读 + P2 计划→确认→硬删）")
    sub = parser.add_subparsers(dest="cmd", required=True)
    scan = sub.add_parser("scan", help="扫描一个知识库并输出报告（只读）")
    scan.add_argument("kb_id")
    scan.add_argument("--scope", default="all", choices=list(SCOPES))
    scan.add_argument("--max-findings", type=int, default=200)
    scan.add_argument("--page-limit", type=int, default=5000)
    scan.add_argument("--compact", action="store_true", help="只打印 summary/totals/data（省上下文）")
    plan_cmd = sub.add_parser("plan", help="生成清理/修复计划（只读，含 plan_id）")
    plan_cmd.add_argument("kb_id")
    plan_cmd.add_argument("--kinds", default="all",
                          help="init（清空 wiki+图谱）/ all（清理异常+修问题）/ 逗号分隔的具体 kind")
    plan_cmd.add_argument("--scope", default="all", choices=list(SCOPES))
    plan_cmd.add_argument("--page-limit", type=int, default=5000)
    init_cmd = sub.add_parser("init", help="初始化知识库的计划（= plan --kinds init）")
    init_cmd.add_argument("kb_id")
    apply_cmd = sub.add_parser("apply", help="执行已确认的计划（硬删，不可逆）")
    apply_cmd.add_argument("kb_id")
    apply_cmd.add_argument("--plan-id", required=True)
    apply_cmd.add_argument("--confirm", action="store_true",
                           help="必须显式给出（用户口径：执行前必须确认），否则拒绝执行")
    apply_cmd.add_argument("--page-limit", type=int, default=5000)

    args = parser.parse_args()
    if args.cmd == "scan":
        report = audit(args.kb_id, args.scope, args.max_findings, args.page_limit)
        if args.compact:
            print(json.dumps({"summary": report["summary"], "totals": report["totals"],
                              "data": report["data"]}, ensure_ascii=False, indent=2))
        else:
            print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0
    if args.cmd == "plan":
        print(json.dumps(build_plan(args.kb_id, args.kinds, args.scope, args.page_limit),
                         ensure_ascii=False, indent=2))
        return 0
    if args.cmd == "init":
        print(json.dumps(build_plan(args.kb_id, "init", "all", ), ensure_ascii=False, indent=2))
        return 0
    print(json.dumps(apply_plan(args.kb_id, args.plan_id, args.confirm, args.page_limit),
                     ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
