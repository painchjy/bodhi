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
  B5（本体投影 ↔ 编译产物不一致）：修复**直接给命令**（重编产物 / 重载投影），不走 plan_id
C（来源异常）：C1 无来源实例页 / C2 来源文档已删或不存在 / C3 正文称有来源但 `source_refs` 空 /
  C4 图侧实例无溯源 / **C5 建模会话记着的页不在本库**（2026-09-24：跨库同 slug 撞主键的存量体检）
D（重复/幂等）：D1 同语义多页 / D2 软删残留与活页并存 / D3 孤儿版本快照 /
  **D4 同库同 slug 多行（影子页）** / **D5 页 id 非规范派生**（2026-09-24 加固）
F（治理，2026-09-24 用户口径）：F1 跨库同名/同实例候选（**2026-09-28 合并**：
  L1 归一化 `slug` 字面同名 + L2 本体类 + 归一化标题相同，detail 标 `matched_by`）/ F2 权威·副本绑定漂移 /
  F3 原文依据不达标（缺摘录或只有占位文案）；设计见 `docs/knowledge-governance.md`
G（跨库上下文映射，2026-09-28 新增，见 `docs/context-mapping-plan.md`）：
  G2 同义组未指认概念 / 概念冲突 / **G3** 映射悬空 / **G4** 映射过期 / **G5** 同名异义未映射（high）/
  **G6** 映射非法或互相矛盾 —— 数据源是 `state/context_map/*.json` + `page_metadata.same_as`（一期只读）

用法
----
    /opt/bodhi-venv/bin/python3 tools/ke-core/ke_audit.py scan <kb_id> [--scope all] [--compact]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import pathlib
import re
import sys
import uuid

HERE = pathlib.Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import ke_db  # noqa: E402
import ke_neo4j  # noqa: E402
import ke_ontology  # noqa: E402
import ke_pages  # noqa: E402

REPO = HERE.parents[1]
# 领域建模会话状态（`state/domain_sessions/<kb>/<knowledge>.json`）：C5 检查读它
SESSION_STATE_DIR = REPO / "state" / "domain_sessions"

try:                                              # ke_docs 与 ke_audit 同在 ke-core
    import ke_docs  # noqa: E402
except Exception:  # noqa: BLE001
    ke_docs = None  # type: ignore

SEV = {"high": 0, "medium": 1, "low": 2}
# 企业标准定义「待评审」天数阈值（附录 A：draft 超过它 → G9 low）
STANDARD_REVIEW_DAYS = 14
SCOPES = ("all", "wiki", "model", "source", "dupes", "governance", "context", "coupling")
# 本体模型库 id（与 ke_admin.ONTOLOGY_KB 同源；这里不 import ke_admin，避免连带依赖）
ONTOLOGY_KB = os.environ.get("ONTOLOGY_KB_ID", "08810cbd-af86-48d1-bd25-3b2c338e3d68")
# B5（本体投影 ↔ 编译产物一致性）用的路径与**可直接执行的修复命令**
#   —— 用户 2026-09-21 口径：这类问题提示后**直接给命令**，不走 plan_id 确认流程
ONTOLOGY_INDEX = HERE.parents[1] / "artifacts" / "weknora" / "ontology_index.json"
ARTIFACT_FIX = ("/opt/bodhi-venv/bin/python3 tools/ontology-compiler/compile.py compile --diff"
                "（产物含前端类型清单 ontologyTypes.ts 的来源；重编后需重建/部署前端才会在界面出现新分类："
                "bash deploy/weknora-fork/build_frontend.sh && bash deploy/weknora-fork/deploy_frontend.sh）")
PROJECTION_FIX = "bash deploy/bootstrap-neo4j.sh"
# 结构性/上游页：不属于「实例页」，C1/A6/B1 一律豁免
NON_INSTANCE = ("index", "summary")
NON_INSTANCE_PREFIX = ("ontology:",)
# 已知页面生成器（`last_edit_source`）；其它值 = 元数据漂移（A6，低）
KNOWN_SOURCES = ("bodhi-onto-mcp", "ontology-wiki", "bodhi-type-edit", "bodhi-rel-edit",
                 "bodhi-page-del", "bodhi-ops-edit", "pipeline", "agent", "",
                 # 结构化批量建模（2026-09-29）：last_edit_source 是短 tag `bi-xxxxxxxx`（varchar(16) 限制）
                 "bodhi-import", "bodhi-review")


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
    # A2 已废弃（2026-10-05 M3）：关系只走 Neo4j 图，wiki in_links/out_links 两列恒空，
    # 不再校验反向边、也没有「重算 in_links」修复项（防设计智能体再跑残留的出入链重建）。
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
        elif page["src"] not in KNOWN_SOURCES and not str(page["src"]).startswith("bi-"):
            rep.add("A6", "low", page["slug"], "未知生成器 last_edit_source=%s" % page["src"],
                    "确认来源，必要时补记生成器（结构化导入用 `bi-xxxxxxxx`）")
    # A7 slug 与类型错位（2026-09-27 用户口径）：改本体类型必须**迁移 slug**（`模块/类/名称`），
    #    旧实现只改 page_type 不改 slug → 出现"类型是 X、slug 还写着 Y"。存量体检 +
    #    两段式迁移后的校验（迁移完 A7 应清零）。
    meta_all = ctx["model"]["meta"] if ctx.get("model") else ke_ontology.class_meta()
    for page in pages:
        if not _is_instance(page["page_type"]):
            continue
        parts = (page["slug"] or "").split("/")
        if len(parts) != 3 or not all(parts):
            continue
        cls_meta = meta_all.get(page["page_type"]) or {}
        want_module = str(cls_meta.get("module") or "")
        want_local = page["page_type"].split(":")[-1].lower()
        if (want_module and parts[0] != want_module) or parts[1] != want_local:
            rep.add("A7", "medium", page["slug"],
                    "slug 与类型错位：slug 段=`%s/%s`，但 page_type=`%s` 要求 `%s/%s`"
                    % (parts[0], parts[1], page["page_type"], want_module or "?", want_local),
                    "两段式迁移修：先 `POST /bodhi/page/retag/preview`（或 "
                    "`ke_admin.py retag-preview <kb> <slug> <new_type>`）看影响面，"
                    "用户确认后 retag-apply（带 ticket + acknowledge_risks）")


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


def check_coupling(ctx: dict, rep: Report) -> None:
    """E（服务详细设计）：CRUD 矩阵推导出的耦合与完整性（**只读，不自动改**）。

    E1 写耦合：同一业务属性被 **≥2 个 IT 服务**以 C/U/D 操作（服务边界/内聚性评审重点）；
    E2 读耦合：某服务读（R）的属性由**别的服务**写（跨服务读依赖）；
    E3 详设完整性：服务无操作 / 操作无被操作属性 / 缺 operationMethod / 写操作非幂等无幂等键；
    E4 键一致性：keyRole=FK 无 referencesAttribute；引用目标非 PK/UNIQUE；PK 未被函数依赖覆盖。

    数据来源与 `server.crud_model` 同一口径：**wiki 页的关系行 + 关系限定小节 + 数据属性**，
    所以这里不 import server（ke-core 不依赖 ontology-mcp），自己走一遍页面解析。
    """
    pages = ctx["by_slug"]
    contracts, ops_by_service, attr_users = {}, {}, {}

    meta = ke_ontology.class_meta()

    def is_service_type(type_name: str) -> bool:
        return bool(type_name) and "ea:Service" in ke_ontology.ancestors(type_name, meta)

    def data_attrs(content: str) -> dict:
        lines = (content or "").splitlines()
        start = next((i for i, ln in enumerate(lines) if ln.strip() == "## 属性（数据属性）"), -1)
        if start < 0:
            return {}
        end = next((i for i in range(start + 1, len(lines)) if lines[i].startswith("## ")), len(lines))
        vals, cur, buf = {}, None, []

        def _flush():
            nonlocal cur, buf
            if cur and buf:
                vals[cur] = "\n".join(buf).strip()
            cur, buf = None, []

        for idx in range(start + 1, end):
            s = lines[idx].strip()
            if s.startswith("### "):
                _flush()
                m = re.search(r"[A-Za-z_]\w*:[A-Za-z_]\w*", s[4:])
                cur = m.group(0) if m else s[4:].strip()
                continue
            hit = re.match(r"^- (?P<k>[^（=]+?)(?:（[^）]*）)?\s*=\s*(?P<v>.+?)\s*$", s)
            if hit:
                _flush()
                vals[hit.group("k").strip()] = hit.group("v").strip()
                continue
            if cur is not None:
                buf.append(lines[idx].rstrip())
        _flush()
        return vals

    for page in pages.values():
        for rel in ke_pages.parse_out_relations(page["content"]):
            if rel["type"] == "easvc:serviceHasOperation" and rel["slug"]:
                ops_by_service.setdefault(page["slug"], []).append(rel["slug"])
    # 操作级依赖（=「经接口读取」的声明）：op_slug -> {被依赖的 op_slug}
    op_dep: dict = {}
    for page in pages.values():
        for rel in ke_pages.parse_out_relations(page["content"]):
            if rel["type"] == "easvc:operationDependsOnOperation" and rel["slug"]:
                op_dep.setdefault(page["slug"], set()).add(rel["slug"])
    service_of_op = {}
    for svc_slug, ops in ops_by_service.items():
        for op_slug in ops:
            service_of_op[op_slug] = svc_slug
    for page in pages.values():
        if not is_service_type(page.get("page_type") or ""):
            continue
        service_ops = ops_by_service.get(page["slug"]) or []
        if not service_ops:
            rep.add("E3", "low", page["slug"],
                    "服务「%s」还没有详细设计（没有声明任何 `easvc:serviceHasOperation` 操作）"
                    % page["title"],
                    "按「服务详细设计」技能补：操作（ServiceOperation）+ 操作→属性 CRUD + 主外键")
            continue
        for op_slug in service_ops:
            op = pages.get(op_slug)
            if not op:
                continue
            quals = {}
            for q in ke_pages.parse_rel_qualifiers(op["content"]):
                quals.setdefault(q["slug"], {}).update(q["properties"])
            touched = [r for r in ke_pages.parse_out_relations(op["content"])
                       if r["type"] == "easvc:operationOperatesOnAttribute" and r["slug"]]
            attrs = data_attrs(op["content"])
            if not touched:
                rep.add("E3", "medium", op_slug,
                        "服务操作「%s」没有声明被操作的业务属性（operationOperatesOnAttribute）"
                        % op["title"], "补 `easvc:operationOperatesOnAttribute` + 边限定 `crudKind`")
            if "easvc:operationMethod" not in attrs:
                rep.add("E3", "low", op_slug,
                        "服务操作「%s」没声明实现方式（easvc:operationMethod）" % op["title"],
                        "补 operationMethod（HTTP 方法 / MCP tool / 函数名）")
            idem = (attrs.get("easvc:isIdempotent") or "").strip().lower()
            writes = []
            for rel in touched:
                crud = (quals.get(rel["slug"]) or {}).get("easvc:crudKind", "")
                kinds = [k for k in ("C", "R", "U", "D") if k in (crud or "").upper()]
                if any(k in kinds for k in ("C", "U", "D")):
                    writes.append(rel)
                for kind in kinds:
                    attr_users.setdefault(rel["slug"], {}).setdefault(kind, set()).add(page["slug"])
            # 幂等提醒**按操作报一次**（一个操作可能写多个属性，逐属性报会重复刷屏）：
            # 只有"非幂等写 + 没写重试策略（`easvc:retryPolicy`）"才提醒 —— 填了就消解
            # （2026-09-21：让"提醒"变成可消解的设计字段，而不是永远挂着的红点）。
            if writes and idem == "false" and not (attrs.get("easvc:operationRetryPolicy") or "").strip():
                rep.add("E3", "low", op_slug,
                        "写操作「%s」声明 isIdempotent=false，但没写重试/补偿策略"
                        % op["title"],
                        "补 `easvc:operationRetryPolicy`（重试退避/补偿与幂等键来源）")
    # E1/E2：按属性聚合
    for attr_slug, by_kind in attr_users.items():
        writers = set()
        for kind in ("C", "U", "D"):
            writers |= by_kind.get(kind) or set()
        readers = by_kind.get("R") or set()
        if len(writers) >= 2:
            rep.add("E1", "medium", attr_slug,
                    "写耦合：业务属性被 %d 个服务写（%s）"
                    % (len(writers), "、".join(sorted(pages.get(w, {}).get("title") or w
                                                      for w in writers))),
                    "评审服务边界（同一属性的写方应收敛到一个服务，或明确主从）")
        cross = readers - writers
        if writers and cross:
            # 读耦合细分（2026-09-21）：声明了 `operationDependsOnOperation` → **经接口**（合理耦合）；
            # 否则 → **疑似直读**（要评审）。用户口径："E2 保留（合理读耦合）并说明是经接口读取"。
            via, direct = [], []
            for reader in sorted(cross):
                declared = any(service_of_op.get(dep) in writers
                               for op_slug in ops_by_service.get(reader, [])
                               for dep in op_dep.get(op_slug, ()))
                (via if declared else direct).append(reader)
            names = lambda ss: "、".join(sorted(pages.get(s, {}).get("title") or s for s in ss))
            if via:
                rep.add("E2", "low", attr_slug,
                        "读耦合（**经接口**）：%s 通过声明的操作依赖（`easvc:operationDependsOnOperation`）"
                        "读取 %s 写的属性 —— 属合理耦合"
                        % (names(via), names(writers)),
                        "确认依赖的是稳定对外接口；接口变更时按契约评审")
            if direct:
                rep.add("E2", "medium", attr_slug,
                        "读耦合（**疑似直读**）：%s 读由 %s 写的属性，但没有声明操作依赖"
                        % (names(direct), names(writers)),
                        "补 `easvc:operationDependsOnOperation` 走接口，或把读收敛到写方")
    # E4：键一致性
    for page in pages.values():
        attrs = data_attrs(page["content"])
        role = (attrs.get("easvc:keyRole") or "").strip().upper()
        refs = [r for r in ke_pages.parse_out_relations(page["content"])
                if r["type"] == "easvc:referencesAttribute" and r["slug"]]
        if role == "FK" and not refs:
            rep.add("E4", "medium", page["slug"],
                    "外键属性「%s」没有声明 referencesAttribute（引用目标）" % page["title"],
                    "补 `easvc:referencesAttribute`；目标应为被引用实体的 PK/UNIQUE 属性")
        for rel in refs:
            target = pages.get(rel["slug"])
            t_role = (data_attrs(target["content"]).get("easvc:keyRole") or "").upper() if target else ""
            if target and t_role not in ("PK", "UNIQUE"):
                rep.add("E4", "medium", page["slug"],
                        "外键「%s」引用的「%s」键角色是 %s（应为 PK/UNIQUE）"
                        % (page["title"], target["title"], t_role or "未声明"),
                        "把目标属性标为 PK/UNIQUE，或改引用正确的键属性")
            if not target:
                rep.add("E4", "medium", page["slug"],
                        "外键「%s」的引用目标页面不存在（%s）" % (page["title"], rel["slug"]),
                        "修正 referencesAttribute 目标或补建该属性页")


def check_ontology_artifacts(ctx: dict, rep: Report) -> None:
    """B5：Neo4j 本体投影（**运行真源**）与编译产物 `artifacts/` 是否一致。

    为什么需要：**通过本体上传接口导入的模块不产 json**（见 docs/session-handoff.md §3.4bis），
    于是 `ontology_types` 工具、抽取契约（`extract_config.*.json`）、SHACL、前端类型候选都可能看不到
    新类/新关系（2026-09-21 实测：上传的 `bmm-ea-ext` 模块在投影里有、产物里没有）。
    本检查只读，**修复直接给命令**（不走 plan_id 确认流程）。
    """
    import json as _json
    try:
        proj_classes = {r["name"] for r in ke_neo4j.query(
            "MATCH (c:BodhiOntClass) WHERE c.bodhi_projection = 'ontology' "
            "AND coalesce(c.external, false) = false AND c.prefixed IS NOT NULL "
            "RETURN DISTINCT c.prefixed AS name") if r.get("name")}
        proj_props = {r["name"] for r in ke_neo4j.query(
            "MATCH (p:BodhiOntProperty) WHERE p.bodhi_projection = 'ontology' "
            "AND p.prefixed IS NOT NULL "
            "AND toLower(coalesce(p.property_kind, 'object')) <> 'datatype' "
            "RETURN DISTINCT p.prefixed AS name") if r.get("name")}
        # 数据属性（datatype）：产物只给**计数**（`stats.datatype_properties`），投影侧单独计数对比。
        # 早期版本把数据属性混进属性集合，于是 `bmm:definition`、`ea:ai_skill` 等被误报为「投影独有」
        # （2026-09-21 实测：投影 109 = object 93 + datatype 16，产物 object 72 + datatype 21）。
        proj_dt = int(ke_neo4j.query(
            "MATCH (p:BodhiOntProperty) WHERE p.bodhi_projection = 'ontology' "
            "AND p.prefixed IS NOT NULL AND toLower(coalesce(p.property_kind, '')) = 'datatype' "
            "RETURN count(p) AS n")[0]["n"] or 0)
    except Exception as exc:  # noqa: BLE001
        rep.add("B5", "medium", ctx["kb_id"], "无法读 Neo4j 本体投影：%s" % exc, PROJECTION_FIX)
        return
    try:
        index = _json.loads(ONTOLOGY_INDEX.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        rep.add("B5", "high", ctx["kb_id"], "读不到编译产物 %s：%s" % (ONTOLOGY_INDEX, exc),
                ARTIFACT_FIX)
        return
    art_classes, art_props, modules = set(), set(), []
    art_dt = 0
    for model in (index.get("models") or []):
        modules.append(str(model.get("key") or ""))
        for cls in (model.get("classes") or []):
            if cls.get("name"):
                art_classes.add(cls["name"])
        for rel in (model.get("relations") or []):
            if rel.get("name"):
                art_props.add(rel["name"])
        art_dt += int((model.get("stats") or {}).get("datatype_properties") or 0)
    only_proj_c = sorted(proj_classes - art_classes)
    only_art_c = sorted(art_classes - proj_classes)
    only_proj_p = sorted(proj_props - art_props)
    only_art_p = sorted(art_props - proj_props)
    dt_drift = proj_dt - art_dt
    ctx["data"]["ontology_artifacts"] = {
        "projection": {"classes": len(proj_classes), "properties": len(proj_props),
                       "datatype_properties": proj_dt},
        "artifacts": {"classes": len(art_classes), "properties": len(art_props),
                      "datatype_properties": art_dt, "modules": modules},
        "projection_only": {"classes": only_proj_c[:20], "properties": only_proj_p[:20]},
        "artifacts_only": {"classes": only_art_c[:20], "properties": only_art_p[:20]},
        "fix_commands": {"recompile": ARTIFACT_FIX, "reload_projection": PROJECTION_FIX},
    }
    if only_proj_c or only_proj_p or only_art_c or only_art_p or dt_drift:
        rep.add("B5", "medium", ctx["kb_id"],
                "本体投影与编译产物不一致：投影 %d 类/%d 对象属性/%d 数据属性，"
                "产物 %d 类/%d 对象属性/%d 数据属性（产物模块：%s）。"
                "投影有产物无 → 类 %s；对象属性 %s。产物有投影无 → 类 %s；对象属性 %s。"
                "数据属性差 %+d。"
                % (len(proj_classes), len(proj_props), proj_dt,
                   len(art_classes), len(art_props), art_dt,
                   "、".join(m for m in modules[:8] if m) or "—",
                   "、".join(only_proj_c[:6]) or "—", "、".join(only_proj_p[:6]) or "—",
                   "、".join(only_art_c[:6]) or "—", "、".join(only_art_p[:6]) or "—", dt_drift),
                "① 重编产物：%s；② 重载投影：%s（两步都是直接命令，无需计划确认）"
                % (ARTIFACT_FIX, PROJECTION_FIX))


# ---------------------------------------------------------------------------
# C. 来源异常（用户口径：无来源 / 来源已删 = 异常数据）
# ---------------------------------------------------------------------------
def _import_source(page: dict) -> str:
    """结构化批量建模导入的页：`page_metadata.import.file_sha256` 就是它的来源（Excel 行即原文）。

    用户口径（2026-09-29）：结构化数据**不加"原文依据"列**——Excel 行本身即原文，
    正文已带「## 原文依据」逐字；来源用**文件 sha256 + sheet + 行号**记在元数据里。
    因此这类页**不算"无来源"**（C1 豁免），但会在 `data.structured_imports` 里报数。
    """
    try:
        meta = json.loads(page.get("meta") or "{}")
    except Exception:  # noqa: BLE001
        return ""
    imp = meta.get("import") or {}
    if meta.get("review"):                       # 文档评审页：依据=被评审文档，逐条带「原文依据」
        return "review"
    return str(imp.get("file_sha256") or "")


def _session_slugs(kb_id: str) -> set:
    """本库**已挂会话溯源**的页 slug 集合（图上有 `bmm:sourceSession` 出边）。

    2026-10-09 用户口径（方案 ①）：**溯源主口径 = L1 会话边 + 正文 `## 原文依据`**；
    `source_refs` / `chunk_refs` **降级为「有就写」的兼容字段**（WeKnora 内建列，不删、不删也不判违规）。
    于是 C1/C3 的判分改为「**会话边 或 source_refs 任一**」——先看会话，再看文档。
    图不可用时返回空集 → 行为退回旧版（只看 `source_refs`），不会误放行。
    """
    try:
        rows = ke_neo4j.query(
            "MATCH (a:BodhiInstance {kb_id:$kb})-[:`bmm:sourceSession`]->(:BodhiInstance) "
            "RETURN DISTINCT a.slug AS slug", {"kb": kb_id})
        return {str(r.get("slug")) for r in rows if r.get("slug")}
    except Exception:  # noqa: BLE001
        # 静默退回：会话集合为空 ⇒ C1/C3 只看 source_refs（与旧版一致，绝不误放行）；
        # 这里**不打印**：本函数被 HTTP `/bodhi/audit` 复用，stdout 要留给结构化输出。
        return set()


def check_sources(ctx: dict, rep: Report) -> None:
    pages = ctx["pages"]
    structured = 0
    traced = _session_slugs(ctx["kb_id"])          # ← 方案 ①：会话边也算溯源
    ctx["data"]["session_traced"] = len(traced)
    for page in pages:
        refs = _json_list(page["refs"])
        has_session = page["slug"] in traced
        claims = bool(re.search(r"（来源[:：]", page["content"] or "")) or "<sources>" in (page["content"] or "")
        if _is_instance(page["page_type"]) and not refs and not has_session:
            if _import_source(page):
                structured += 1                     # 结构化导入页：来源=文件 sha256，C1 豁免
                continue
            rep.add("C1", "high", page["slug"],
                    "实例页无溯源（`source_refs` 为空**且图上无 `bmm:sourceSession` 会话边**）%s"
                    % ("；正文却有来源标记" if claims else ""),
                    "确认后清理（P2）：删该页并重算 in_links；误判可先 --strip-only")
        elif not refs and not has_session and claims and not _is_instance(page["page_type"]):
            rep.add("C3", "low", page["slug"],
                    "正文有来源标记但既无 `source_refs`、图上也无会话边（非实例页，上游维护）",
                    "上游页不在本工具清理范围；如需纳入请先确认口径")
    ctx["data"]["structured_imports"] = structured

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
# C5（2026-09-24）：领域建模会话状态里记的页**不在本库**
# ---------------------------------------------------------------------------
# 历史 bug（用户实测）：页 id 只按 slug 派生（UUIDv5）→ 两个知识库里的同名页 id 相同 →
# `INSERT … ON CONFLICT (id) DO UPDATE` 更新了**别的库**那一行，本库没有该页，回执却报成功
# （目标库 11 页全部落在别的库）。已修：id 并入 kb + upsert 同库守卫 + 写后对账；
# 本检查用来**发现存量**（哪些会话页落错、实际落在哪个库）。
def check_session_pages(ctx: dict, rep: Report) -> None:
    kb_id = ctx["kb_id"]
    base = SESSION_STATE_DIR / kb_id
    if not base.is_dir():
        ctx["data"]["session_pages"] = {"sessions": 0, "pages": 0, "missed": 0}
        return
    sessions = pages_seen = missed_total = 0
    for path in sorted(base.glob("*.json")):
        try:
            state = json.loads(path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            continue
        entries = state.get("pages") or []
        if not entries:
            continue
        sessions += 1
        slugs = [str(p.get("slug") or "") for p in entries]
        slugs = [s for s in slugs if s]
        pages_seen += len(slugs)
        if not slugs:
            continue
        lst = ", ".join(ke_db.sql_str(s) for s in slugs)
        rows = ke_db.psql_csv(
            "SELECT slug, left(knowledge_base_id::text,8) AS kb FROM wiki_pages "
            "WHERE deleted_at IS NULL AND slug IN (%s)" % lst)
        here = {r["slug"] for r in rows if r["kb"] == kb_id[:8]}
        elsewhere = {}
        for r in rows:
            if r["kb"] != kb_id[:8]:
                elsewhere.setdefault(r["slug"], []).append(r["kb"])
        for slug in slugs:
            if slug in here:
                continue
            missed_total += 1
            where = "、".join(elsewhere.get(slug) or []) or "（该 slug 在库里不存在）"
            rep.add("C5", "high", slug,
                    "建模会话记着这一页，但**本库没有**它（实际落点：%s）—— 会话 %s"
                    % (where, path.stem[:24]),
                    "历史 bug：页 id 只按 slug 派生 → 跨库撞主键、upsert 更新了别库那一行。"
                    "修复已上线（id 含库 + 同库守卫 + 写后对账）；存量请重跑该文档的建模批次"
                    "落进本库，并人工核对别库那几页是否需回退/删除")
    ctx["data"]["session_pages"] = {"sessions": sessions, "pages": pages_seen,
                                    "missed": missed_total}


# ---------------------------------------------------------------------------
# D4 / D5（2026-09-24）：页身份完整性
#   D4 同库同 slug 多行（"影子页"）：同 (kb, slug) 有 2+ 活行 —— 更新只会命中其中一行，
#      另一行在前端/巡检里表现为"内容不跟着变"。可能来源：历史跨库撞主键时代的残留、
#      手工 SQL、suffix 兜底后又建了规范行。
#   D5 页 id 非规范派生：既不是旧口径 uuid5('bodhi-element:'+slug)，也不是新口径
#      uuid5('bodhi-element:<kb>|<slug>')，也不是带后缀的兜底口径 —— 说明这行不是本工具写的，
#      或写的时候走过 `_resolve_page_id` 的 suffix 分支（该分支按设计几乎不可达，出现即为信号）。
# 两项目前都是 0；加进来是为了**将来一旦发生能被体检发现**，而不是静默。
# ---------------------------------------------------------------------------
def _canonical_page_ids(kb_id: str, slug: str) -> set[str]:
    """本页可能出现的**规范 id**集合：新口径（kb+slug）、旧口径（纯 slug）、兜底后缀 1..5。"""
    ids = {str(uuid.uuid5(uuid.NAMESPACE_URL, "bodhi-element:%s|%s" % (kb_id, slug))),
           str(uuid.uuid5(uuid.NAMESPACE_URL, "bodhi-element:" + slug))}
    for n in range(1, 6):
        ids.add(str(uuid.uuid5(uuid.NAMESPACE_URL, "bodhi-element:%s|%s|%d" % (kb_id, slug, n))))
    return ids


def check_page_identity(ctx: dict, rep: Report) -> None:
    """D4 同库同 slug 多行 + D5 页 id 非规范派生（只读；目前库里都为 0）。"""
    kb_id = ctx["kb_id"]
    rows = ke_db.psql_csv(
        "SELECT slug, count(*) AS n, string_agg(version::text, ',' ORDER BY version) AS versions, "
        "       string_agg(left(id::text, 8), ',' ORDER BY version) AS ids "
        "FROM wiki_pages WHERE knowledge_base_id = %s AND deleted_at IS NULL "
        "GROUP BY slug HAVING count(*) > 1 ORDER BY 2 DESC LIMIT 20" % ke_db.sql_str(kb_id))
    for r in rows:
        rep.add("D4", "medium", r["slug"],
                "同库同 slug 有 %s 行（版本 %s；id 前缀 %s）—— 更新只会命中其中一行，其余是"
                "「影子页」，前端/巡检会看到\"内容不更新\"" % (r["n"], r["versions"], r["ids"]),
                "保留版本最高的一行、把其余行合并或硬删（先备份 content），然后重跑本巡检")
    ctx["data"]["dup_slug_rows"] = len(rows)

    bad = []
    for page in ctx["pages"]:
        pid = str(page.get("id") or "")
        if not pid:
            continue
        if pid.lower() not in _canonical_page_ids(kb_id, page["slug"]):
            bad.append(page)
            if len(bad) <= 10:
                rep.add("D5", "low", page["slug"],
                        "页 id %s… 不是规范派生（新口径 uuid5('bodhi-element:<kb>|<slug>')/"
                        "旧口径 uuid5('bodhi-element:<slug>')）—— 可能是 suffix 兜底或外部写入"
                        % pid[:8],
                        "无需处理（查询一律按 (kb, slug)）；若批量出现请核对是否有脚本直接写 wiki_pages")
    ctx["data"]["non_canonical_ids"] = len(bad)


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
# F（治理）：跨库实例的权威/副本 + 溯源质量（2026-09-24 用户口径）
#   F1 跨库同实例候选：同 (模型, 类, 归一化名称) 的实例页在多个知识库都有 → 需租户指认**权威知识**；
#   F2 权威/副本绑定漂移：master 缺失（detached）/ 权威版本已前进（outdated）/ 副本被本地改写；
#   F3 原文依据不达标：实例页缺「## 原文依据」摘录，或只有「（…无原文片段）」占位文案。
#   设计与决策点见 `docs/knowledge-governance.md`。本组**只读**（不写任何数据）。
# ---------------------------------------------------------------------------
PLACEHOLDER_QUOTE_RE = re.compile(r"[（(][^）)]*无原文[^）)]*[）)]")


def _meta_full(text: str) -> dict:
    """整份 `page_metadata`（`_page_meta` 只取 `ontology` 子字典）。"""
    try:
        val = json.loads(text or "{}")
    except json.JSONDecodeError:
        return {}
    return val if isinstance(val, dict) else {}


def _governance_section(content: str, header: str) -> str | None:
    """取正文里某个 `## ` 小节的内容（到下一个 `## ` 或文尾）；没有该小节返回 None。"""
    lines = (content or "").splitlines()
    start = None
    for i, line in enumerate(lines):
        if line.strip() == header.strip():
            start = i + 1
            break
    if start is None:
        return None
    body: list[str] = []
    for line in lines[start:]:
        if line.startswith("## "):
            break
        body.append(line)
    return "\n".join(body)


def _sha1(text: str) -> str:
    return "sha1:" + hashlib.sha1((text or "").encode("utf-8")).hexdigest()


def _page_ver_hash(kb_id: str, slug: str):
    """页的 `(version, sha1)` —— 映射过期（G4）用；页不存在返回 `(None, "")`。"""
    rows = ke_db.psql_csv(
        "SELECT COALESCE(version,1) AS version, COALESCE(content,'') AS content "
        "  FROM wiki_pages WHERE knowledge_base_id = %s AND slug = %s AND deleted_at IS NULL LIMIT 1"
        % (ke_db.sql_str(kb_id), ke_db.sql_str(slug)))
    if not rows:
        return None, ""
    return int(rows[0]["version"] or 1), _sha1(rows[0]["content"])


def check_context_map(ctx: dict, rep: Report) -> None:
    """G 系列（2026-09-28 新增，**只读**）：跨库上下文映射（DDD Context Map）一致性。

    G2 同义组未指认概念 / 同一同名页挂了两个概念（冲突）
    G3 映射悬空：源/目标页不存在或已删（detached）
    G4 映射过期：源/目标在决定之后改过（stale）
    G5 同名异义未映射：跨库同名 slug、扫描建议 distinct、却没有任何 mapping
    G6 映射不可达 / 互相矛盾

    数据源：`state/context_map/mappings.json`（二期写、一期读）+ 页 `page_metadata.same_as`
    + `state/context_map/latest_scan.json`（扫描报告）。设计见 docs/context-mapping-plan.md §8/§13。
    """
    try:
        import ke_context  # noqa: E402  （同目录；一期只读模块）
    except Exception:  # noqa: BLE001
        return
    pairs = [p for p in ((ke_context._json_load(ke_context.MAPPINGS_FILE, {}) or {}).get("pairs") or [])
             if isinstance(p, dict)]

    # ---- G2 同义组未指认概念 / 概念冲突 ---------------------------------------
    mounted: list = []
    for page in ctx["pages"]:
        same = _meta_full(page.get("meta")).get("same_as")
        if not isinstance(same, dict) or not same:
            continue
        cslug = str(same.get("concept_slug") or "")
        ckb = str(same.get("concept_kb") or "")
        if not cslug:
            rep.add("G2", "medium", page["slug"], "`page_metadata.same_as` 缺 `concept_slug`（挂了空概念）",
                    "补全指针或移除（docs/context-mapping-plan.md §5 L1）")
            continue
        mounted.append((page["slug"], ckb, cslug))
        if ckb and not _page_ver_hash(ckb, cslug)[0]:
            rep.add("G2", "medium", page["slug"],
                    "概念页不存在：`%s/%s`（same_as 指向空的落点）" % (ckb[:8], cslug),
                    "在「企业共享概念模型」里建该概念页后重签")
    if mounted:
        slugs = sorted({s for s, _ckb, _cs in mounted})
        placeholders = ",".join(ke_db.sql_str(s) for s in slugs[:200])
        rows = ke_db.psql_csv(
            "SELECT left(t.kb::text, 8) AS kb, t.slug, t.concept FROM ("
            "  SELECT knowledge_base_id AS kb, slug, "
            "         COALESCE(page_metadata -> 'same_as' ->> 'concept_slug', '') AS concept "
            "    FROM wiki_pages WHERE deleted_at IS NULL AND knowledge_base_id <> %s"
            "     AND slug IN (%s)) t WHERE t.concept <> ''"
            % (ke_db.sql_str(ctx["kb_id"]), placeholders))
        mine = {s: cs for s, _ckb, cs in mounted}
        for row in rows:
            other = str(row.get("concept") or "")
            if row["slug"] in mine and other and mine[row["slug"]] != other:
                rep.add("G2", "medium", row["slug"],
                        "同一同名页在不同上下文挂了**不同概念**（本库 `%s` vs %s/%s）—— 概念冲突"
                        % (mine[row["slug"]], row["kb"], other),
                        "先裁决同义/异义，再统一概念指针")
    ctx["data"]["context_same_as_mounted"] = len(mounted)

    # ---- G3/G4/G6 映射表自身 --------------------------------------------------
    detached = stale = bad = 0
    seen: dict = {}
    for row in pairs:
        src, dst = row.get("source") or {}, row.get("target") or {}
        mtype = str(row.get("mapping") or "")
        s_kb = str(src.get("kb") or ""); d_kb = str(dst.get("kb") or "")
        s_slug = str(src.get("slug") or ""); d_slug = str(dst.get("slug") or "")
        label = "%s → %s" % (s_slug or "?", d_slug or "?")
        if mtype not in ("equivalent", "rename", "narrower", "broader", "split", "merge",
                         "unrelated", "unknown"):     # unknown = 尚未裁决（概念页里可先留空/待定）
            bad += 1
            rep.add("G6", "medium", label, "非法映射类型 `%s`" % mtype,
                    "取值域 equivalent/rename/narrower/broader/split/merge/unrelated/unknown")
        if not (s_kb and d_kb and s_slug and d_slug):
            bad += 1
            rep.add("G6", "medium", label, "映射缺 `source{kb,slug}` 或 `target{kb,slug}`",
                    "补全映射四要素（docs/context-mapping-plan.md §5 L2）")
            continue
        for side, kb, slug in (("source", s_kb, s_slug), ("target", d_kb, d_slug)):
            ver, sha = _page_ver_hash(kb, slug)
            if ver is None:
                detached += 1
                rep.add("G3", "high", label, "映射%s页不存在或已删：%s/%s" % (side, kb[:8], slug),
                        "重建映射或归档为 detached（二期两段式 apply）")
                continue
            recorded = str(row.get("%s_hash" % side) or "")
            if recorded and recorded != sha:
                stale += 1
                rep.add("G4", "medium", label, "映射%s页自决定后改动过（%s 指纹不一致）→ 复核同义/异义"
                        % (side, side), "重跑 context_scan → preview → 重签该映射")
            elif row.get("%s_version" % side) and ver > int(row.get("%s_version" % side) or 0):
                stale += 1
                rep.add("G4", "medium", label, "映射%s页版本 %s → %s（决定后改过）→ 复核"
                        % (side, row.get("%s_version" % side), ver),
                        "重跑 context_scan → preview → 重签该映射")
        back = seen.get((d_kb, d_slug, s_kb, s_slug))
        if back and str(back.get("mapping") or "") != mtype:
            rep.add("G6", "medium", label, "映射互相矛盾：反向记录类型是 `%s`（本记录 `%s`）"
                    % (back.get("mapping"), mtype), "统一两侧映射口径")
        seen[(s_kb, s_slug, d_kb, d_slug)] = row
    covered = {(s_kb[:8], s_slug, d_kb[:8], d_slug) for (s_kb, s_slug, d_kb, d_slug) in seen}
    ctx["data"]["context_mappings"] = {"pairs": len(pairs), "detached": detached, "stale": stale,
                                       "invalid": bad, "same_as_mounted": len(mounted)}

    # ---- G5 同名异义未映射（读一期扫描报告）------------------------------------
    latest = ke_context.latest_scan()
    report_file = str(latest.get("file") or "")
    if not report_file:
        return
    data = ke_context._json_load(ke_context.REPO / report_file, {}) or {}
    undecided = 0
    me = "kb:%s" % ctx["kb_id"][:8]
    for cand in (data.get("candidates") or []):
        pages = cand.get("pages") or []
        if me not in [p.get("context") for p in pages] or cand.get("verdict_suggestion") != "distinct":
            continue
        keys = [(str(p.get("kb") or "")[:8], str(p.get("slug") or ""), p.get("context")) for p in pages]
        pair_covered = any((keys[i][0], keys[i][1], keys[j][0], keys[j][1]) in covered
                           for i in range(len(keys)) for j in range(len(keys)) if i != j)
        if not pair_covered:
            undecided += 1
            rep.add("G5", "high", str(cand.get("id") or ""),
                    "同名**异义**但**没有 ACL 映射**：%s —— 跨库引用会误读"
                    % " | ".join("%s:%s" % (p.get("context"), p.get("slug")) for p in pages),
                    "由有写权限的人/智能体登记映射（unrelated/rename/…；二期两段式 apply）")
    ctx["data"]["context_undecided_distinct"] = undecided

    # ---- G8 映射缓存过期/缺失（缓存=技术产物；事实源是概念页）-------------------
    cache = ke_context.read_cache()
    if cache.get("generated_from"):
        if cache.get("stale_count"):
            rep.add("G8", "low", "state/context_map/mappings.json",
                    "映射缓存过期 %d 项（%s）—— 概念页在缓存生成后改过/删过；缓存只用于渲染加速，"
                    "事实源始终是概念页正文" % (cache["stale_count"],
                                             "、".join(str(x.get("slug") or "") for x in
                                                       (cache.get("stale") or [])[:3])),
                    "刷新缓存：`ke_context.py cache-rebuild` 或 `POST /bodhi/context/cache/rebuild`")
    elif pairs or mounted:
        rep.add("G8", "low", "state/context_map/mappings.json",
                "有概念页但**没有映射缓存**（渲染/查询会少一层加速数据）", "跑 `cache-rebuild`")
    ctx["data"]["context_cache"] = {"built_at": cache.get("built_at", ""),
                                    "pairs": len(cache.get("pairs") or []),
                                    "stale": cache.get("stale_count", 0)}

    # ---- G9 企业标准定义治理状态（附录 A）：draft 超期未评审 / approved 后被改 ----------
    ckb = ke_context.concept_kb().get("id") or ""
    if ckb:
        rows = ke_db.psql_csv(
            "SELECT slug, COALESCE(page_metadata::text,'{}') AS meta, "
            "       COALESCE(extract(day from (now() - updated_at)),0) AS age_days "
            "  FROM wiki_pages WHERE deleted_at IS NULL AND knowledge_base_id = %s" % ke_db.sql_str(ckb))
        stale_slugs = {str(x.get("slug") or "") for x in (cache.get("stale") or [])}
        counts = {"draft": 0, "reviewed": 0, "approved": 0, "none": 0}
        for row in rows:
            try:
                cmeta = (json.loads(row["meta"] or "{}").get("concept") or {})
            except Exception:  # noqa: BLE001
                cmeta = {}
            state = str(cmeta.get("state") or "")
            counts[state if state in counts else "none"] += 1
            age = int(float(row.get("age_days") or 0))
            if state in ("", "draft") and age >= STANDARD_REVIEW_DAYS:
                rep.add("G9", "low", row["slug"],
                        "企业标准定义**待评审**：状态=%s，已 %d 天未动（阈值 %d 天）"
                        % (state or "（未标）", age, STANDARD_REVIEW_DAYS),
                        "评审后推进状态：`ke_context.py concept-state <slug> reviewed --by 谁`")
            if state == "approved" and row["slug"] in stale_slugs:
                rep.add("G9", "medium", row["slug"],
                        "已 `approved` 的企业标准被改动（缓存指纹不一致）→ 需**重新评审**",
                        "复核后重新置 approved（或先退回 reviewed）")
        ctx["data"]["context_concept_states"] = counts

    # ---- G10 权威/副本：副本本地漂移 / 落后权威 / 未绑定（2026-09-29 口径）---------
    #   口径：同义知识**认定一个领域为权威（master）**，其它领域**只读**、只能 `authority_pull`
    #   从权威复制。事实源 = 概念页「## 权威与副本」表（缓存 → `authority` 段）。
    auth_entries = cache.get("authority") or []
    drift = outdated = unbound = detached = 0
    for entry in auth_entries:
        cslug = str(entry.get("concept_slug") or "")
        master = entry.get("master") or {}
        m_name = str(master.get("kb_name") or str(master.get("kb") or "")[:8])
        if not entry.get("master_version"):
            detached += 1
            rep.add("G10", "medium", cslug,
                    "权威/副本关系**失去权威**（detached）：master 页 %s/%s 不存在或已删 —— 副本没有同步来源"
                    % (m_name, master.get("slug") or ""),
                    "重新指认权威：`ke_context.py authority-decide <slug> --master <库>`（两段式，带 ticket）")
        for r in (entry.get("replicas") or []):
            r_name = str(r.get("kb_name") or str(r.get("kb") or "")[:8])
            pull = "`ke_context.py authority-pull %s --kb \"%s\" --apply`" % (cslug, r_name)
            state = str(r.get("state") or "")
            if state == "local_drift":
                drift += 1
                rep.add("G10", "high", "%s @%s" % (r.get("slug"), r_name),
                        "副本正文**本地漂移**（local_drift）：上次从权威（%s v%s）复制后又被人本地改过 —— "
                        "违反\"副本只读\"，下次从权威复制会被**覆盖**"
                        % (m_name, entry.get("master_version") or "?"),
                        "把本地补充挪进 `page_metadata.local_notes`，然后从权威复制：%s" % pull)
            elif state == "outdated":
                outdated += 1
                rep.add("G10", "medium", "%s @%s" % (r.get("slug"), r_name),
                        "副本**落后于权威**（outdated）：已同步 v%s，权威已到 v%s（权威前进不自动推送）"
                        % (r.get("synced_version") or "?", entry.get("master_version") or "?"),
                        "评估下游影响后从权威复制：%s" % pull)
            elif state == "unbound":
                unbound += 1
                rep.add("G10", "low", "%s @%s" % (r.get("slug"), r_name),
                        "副本**尚未绑定同步指纹**（unbound）：还没跑过 `authority-pull`，漂移检测不到",
                        "从权威复制一次即绑定：%s" % pull)
    ctx["data"]["authority_replicas"] = {"groups": len(auth_entries), "local_drift": drift,
                                         "outdated": outdated, "unbound": unbound,
                                         "detached": detached}

    # ---- G7 领域库**不得互相引用**（跨库直接引用 → high）------------------------
    #   用户口径（2026-09-28）：领域库之间不能互相引用；相互关系必须**经企业共享概念页转换**。
    out_slugs: set = set()
    for page in ctx["pages"]:
        out_slugs |= set(_json_list(page.get("out_links")))
    out_slugs -= ctx["live"]
    forbidden = 0
    if out_slugs:
        placeholders = ",".join(ke_db.sql_str(s) for s in sorted(out_slugs)[:500])
        rows = ke_db.psql_csv(
            "SELECT DISTINCT slug FROM wiki_pages WHERE deleted_at IS NULL "
            " AND knowledge_base_id <> %s AND slug IN (%s)"
            % (ke_db.sql_str(ctx["kb_id"]), placeholders))
        others = {r["slug"] for r in rows}
        for page in ctx["pages"]:
            hits = sorted(set(_json_list(page.get("out_links"))) & others)
            if not hits:
                continue
            forbidden += 1
            rep.add("G7", "high", page["slug"],
                    "跨库直接引用：本页出边指向**别的领域库**的 slug（%s）—— 领域库不得互相引用，"
                    "必须经「企业共享概念模型」同名概念页转换" % "、".join(hits[:4]),
                    "删掉跨库出边；改指向本库等价页（跨域关系由概念页映射呈现）")
    ctx["data"]["context_forbidden_cross_kb_refs"] = forbidden


def check_governance(ctx: dict, rep: Report) -> None:
    kb_id = ctx["kb_id"]
    mine = [p for p in ctx["pages"] if _is_instance(p["page_type"])]

    # ---- F1 跨库同实例候选（需指认权威）-------------------------------------
    keys = {(p["page_type"], _norm_title(p["title"])) for p in mine}
    others: dict = {}
    if keys:
        rows = ke_db.psql_csv(
            "SELECT left(w.knowledge_base_id::text, 8) AS kb, COALESCE(k.name, '') AS kb_name, "
            "       w.slug, COALESCE(w.title, '') AS title, COALESCE(w.page_type, '') AS page_type "
            "  FROM wiki_pages w LEFT JOIN knowledge_bases k ON k.id = w.knowledge_base_id "
            " WHERE w.deleted_at IS NULL AND w.knowledge_base_id <> %s "
            "   AND position(':' in COALESCE(w.page_type, '')) > 0 "
            " LIMIT 5000" % ke_db.sql_str(kb_id))
        for row in rows:
            key = (row["page_type"], _norm_title(row["title"]))
            if key in keys:
                others.setdefault(key, []).append(row)
    for key, rowset in sorted(others.items())[:20]:
        mine_slug = next((p["slug"] for p in mine
                          if (p["page_type"], _norm_title(p["title"])) == key), "")
        rep.add("F1", "low", key[1],
                "同一实例知识**本库 + 另 %d 个库**都有：本库 `%s`（%s）；别库 %s —— 需租户指认**权威知识**，"
                "其余库按副本与权威版本**单向绑定**"
                % (len({r["kb"] for r in rowset}), mine_slug, key[0],
                   "、".join("%s/%s（%s）" % (r["kb"], r["slug"], r["kb_name"]) for r in rowset[:3])),
                "裁决与绑定方案见 docs/knowledge-governance.md §B（元数据写 `page_metadata.authority`）")
    ctx["data"]["cross_kb_instance_candidates"] = len(others)

    # ---- F1 补充判据：**L1 `slug` 字面同名**（2026-09-28 用户拍板：与原 F1 合并成同一检查）------
    #   口径（docs/context-mapping-plan.md §2 / §13.3）：
    #     L1 同名   = 跨库 slug **归一化后字面相同**（用户口径，名字冲突 → 可能同名异义）；
    #     L2 同实例 = 跨库 本体类 + 归一化标题 相同（原 F1 口径，语义更强的"同一实例"线索）。
    #   两者**同属 F1**，detail 里标 `matched_by`，便于一眼看出是"名字撞了"还是"确实是同一实例"。
    same_slug_groups: dict = {}
    try:
        import ke_context  # noqa: E402  （同目录；一期只读模块）
    except Exception:  # noqa: BLE001
        ke_context = None  # type: ignore
    if ke_context is not None:
        l1_rows = ke_db.psql_csv(
            "SELECT left(w.knowledge_base_id::text, 8) AS kb, COALESCE(k.name, '') AS kb_name, "
            "       w.slug, COALESCE(w.title, '') AS title, COALESCE(w.page_type, '') AS page_type "
            "  FROM wiki_pages w LEFT JOIN knowledge_bases k ON k.id = w.knowledge_base_id "
            " WHERE w.deleted_at IS NULL AND w.knowledge_base_id <> %s "
            "   AND position(':' in COALESCE(w.page_type, '')) > 0 "
            " LIMIT 5000" % ke_db.sql_str(kb_id))
        by_slug: dict = {}
        for row in l1_rows:
            if _is_instance(row["page_type"]):
                by_slug.setdefault(ke_context.norm_slug(row["slug"]), []).append(row)
        for page in mine:
            rowset = by_slug.get(ke_context.norm_slug(page["slug"]))
            if not rowset:
                continue
            same_slug_groups[page["slug"]] = rowset
            l2_hit = [r for r in rowset
                      if r["page_type"] == page["page_type"]
                      and _norm_title(r["title"]) == _norm_title(page["title"])]
            matched = "L1+L2" if l2_hit else "L1"
            rep.add("F1", "low", page["slug"],
                    "跨库**同名**（归一化 slug 相同，matched_by=%s）：本库 `%s`（%s）；别库 %s —— "
                    "同名不等于同义：同义 → 挂同一个「企业共享概念模型」里的概念页；"
                    "异义 → 必须登记 ACL 映射（`unrelated`/`rename`/`split`…）"
                    % (matched, page["slug"], page["page_type"],
                       "、".join("%s/%s（%s）" % (r["kb"], r["slug"], r["kb_name"]) for r in rowset[:3])),
                    "跑 `context_scan` 拿 ticket → 裁决（一期只读；写路径见 "
                    "docs/context-mapping-plan.md §7/§13）")
    ctx["data"]["cross_kb_same_slug"] = len(same_slug_groups)
    check_governance_rest(ctx, rep, mine)


def check_governance_rest(ctx: dict, rep: Report, mine: list) -> None:
    """F2 权威/副本绑定漂移 + F3 原文依据不达标（F1 见 `check_governance`）。"""
    # ---- F2 权威/副本绑定漂移（口径 2026-09-29：事实源=概念页「## 权威与副本」→ 缓存）----
    cache_auth: list = []
    try:
        import ke_context as _kc      # 与 check_context_map 一致：按需导入，缺依赖时降级
        cache_auth = _kc.read_cache().get("authority") or []
    except Exception:  # noqa: BLE001
        cache_auth = []
    cache_rep: dict = {}
    c_by_slug: dict = {}
    for entry in cache_auth:
        for r in (entry.get("replicas") or []):
            hit = {"entry": entry, "replica": r}
            cache_rep[(str(r.get("kb") or ""), str(r.get("slug") or ""))] = hit
            c_by_slug.setdefault(str(r.get("slug") or ""), []).append(hit)
    bindings = 0
    seen: set = set()
    for page in ctx["pages"]:
        slug = str(page["slug"])
        kb_id = str(page.get("knowledge_base_id") or "")
        hit = cache_rep.get((kb_id, slug))
        if hit is None:
            cands = c_by_slug.get(slug) or []
            hit = cands[0] if len(cands) == 1 else None
            if hit is not None and kb_id and str(hit["replica"].get("kb") or "") != kb_id:
                hit = None
        if hit is None:
            continue
        entry, replica = hit["entry"], hit["replica"]
        seen.add((str(replica.get("kb") or ""), slug))
        bindings += 1
        master = entry.get("master") or {}
        r_name = str(replica.get("kb_name") or str(replica.get("kb") or "")[:8])
        cslug = str(entry.get("concept_slug") or slug)
        pull = ("从权威复制：`ke_context.py authority-pull %s --kb \"%s\" --apply`" % (cslug, r_name))
        if not entry.get("master_version"):
            rep.add("F2", "medium", slug,
                    "绑定失效：权威页 %s/%s 不存在或已删（detached）"
                    % (str(master.get("kb") or "")[:8], master.get("slug") or ""),
                    "重新指认权威（`ke_context.py authority-decide <slug> --master <库>`）或解除副本")
        st = str(replica.get("state") or "")
        if st == "local_drift":
            rep.add("F2", "high", slug,
                    "副本正文在复制后被**本地改写**（local_drift）—— 副本只读、只能从权威复制；"
                    "本页上次同步于权威 v%s" % (replica.get("synced_version") or "?"),
                    "本地补充挪进 `page_metadata.local_notes`，然后 " + pull)
        elif st == "outdated":
            rep.add("F2", "medium", slug,
                    "副本落后于权威（outdated）：已同步 v%s，权威已到 v%s —— 权威前进**不自动推送**"
                    % (replica.get("synced_version") or "?", entry.get("master_version") or "?"),
                    "评估引用本页的页/边后 " + pull)
        elif st == "unbound":
            rep.add("F2", "low", slug,
                    "副本尚未绑定同步指纹（unbound）—— 还没从权威复制过，漂移检测不到", pull)

    # 旧口径兜底：页上仍有 `page_metadata.authority`（历史/手工绑定）且概念页权威表未覆盖到
    for page in ctx["pages"]:
        auth = _meta_full(page.get("meta")).get("authority")
        if not isinstance(auth, dict) or str(auth.get("role") or "") != "replica":
            continue
        if (str(page.get("knowledge_base_id") or ""), str(page["slug"])) in seen:
            continue
        bindings += 1
        master = auth.get("master") if isinstance(auth.get("master"), dict) else {}
        m_kb = str((master or {}).get("kb_id") or "")
        m_slug = str((master or {}).get("slug") or "")
        if not (m_kb and m_slug):
            rep.add("F2", "medium", page["slug"], "副本已登记权威但缺 `master{kb_id, slug}`",
                    "补全绑定信息（`ke_context.py authority-decide`）")
            continue
        mrows = ke_db.psql_csv(
            "SELECT COALESCE(version,1) AS version, COALESCE(content,'') AS content "
            "  FROM wiki_pages WHERE knowledge_base_id = %s AND slug = %s AND deleted_at IS NULL"
            % (ke_db.sql_str(m_kb), ke_db.sql_str(m_slug)))
        if not mrows:
            rep.add("F2", "medium", page["slug"],
                    "绑定失效：权威页 %s/%s 不存在或已删（detached）" % (m_kb[:8], m_slug),
                    "重新指认权威或解除绑定")
            continue
        m_version = int(mrows[0]["version"] or 1)
        bound_v = int(auth.get("master_version") or 0)
        if bound_v and m_version > bound_v:
            rep.add("F2", "medium", page["slug"],
                    "副本绑定在权威 v%s，权威已到 v%s（outdated）—— 需**评估下游影响**后升级绑定"
                    % (bound_v, m_version),
                    "评估引用本页的页/边 → 升级绑定（docs/knowledge-governance.md §B）")
        if auth.get("master_hash") and auth["master_hash"] != _sha1(mrows[0]["content"]):
            rep.add("F2", "low", page["slug"], "绑定的权威正文指纹与权威当前正文不一致（权威被改过）",
                    "复核后重绑（升级绑定版本）")
        if auth.get("replica_hash") and auth["replica_hash"] != _sha1(page["content"]):
            rep.add("F2", "medium", page["slug"],
                    "副本正文在绑定后被**本地改写**（local_drift）—— 单向绑定要求副本只读",
                    "回滚到权威版本，或把本地补充移进 `page_metadata.local_notes` 后重绑")
    ctx["data"]["replica_bindings"] = bindings

    # ---- F3 原文依据不达标 ------------------------------------------------
    with_quote = no_section = placeholder_only = 0
    for page in mine:
        body = _governance_section(page["content"], "## 原文依据")
        if body is None:
            no_section += 1
            if no_section <= 20:
                rep.add("F3", "low", page["slug"], "实例页没有「## 原文依据」小节 —— 无法溯源",
                        "补 `source_text`（逐字摘录）重跑；综合型知识改用 `sources[]`+`synthesis` 声明")
            continue
        text = (body or "").strip()
        quotes = [x for x in text.splitlines() if x.strip().startswith(">")]
        if not text or PLACEHOLDER_QUOTE_RE.search(text) or (quotes and
                all(PLACEHOLDER_QUOTE_RE.search(x) for x in quotes)):
            placeholder_only += 1
            if placeholder_only <= 20:
                rep.add("F3", "low", page["slug"], "「原文依据」只有占位文案（没有任何逐字摘录）",
                        "补 `source_text`；综合型知识用 `sources[]` + `synthesis{mode,confidence,owner}`")
            continue
        with_quote += 1
    ctx["data"]["source_text_quality"] = {"with_quote": with_quote, "no_section": no_section,
                                          "placeholder_only": placeholder_only}


# ---------------------------------------------------------------------------
# P2：计划(只读) → 人工确认 → 执行（**硬删**；用户 2026-09-20 口径：不得自动修）
# ---------------------------------------------------------------------------
PURGE_KINDS = ("no_source_pages", "deleted_source_pages", "mixed_source_refs",
               "soft_deleted_rows", "orphan_revisions")
FIX_KINDS = ("dangling_edges", "dup_edges")
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
        # 与 check_sources（C1）**同口径**：结构化导入页（page_metadata.import.file_sha256）与
        # 文档评审页（page_metadata.review）的"来源"记在元数据里 → **豁免**，不列入清理计划。
        # （2026-09-30 修：此前 plan 侧没走豁免 → purge --kinds all 会把 33 页合法导入页硬删！）
        slugs, exempt = [], 0
        for page in pages:
            if not (_is_instance(page["page_type"]) and not _json_list(page["refs"])):
                continue
            if _import_source(page):
                exempt += 1
                continue
            slugs.append(page["slug"])
        actions["no_source_pages"] = {"count": len(slugs), "slugs": sorted(slugs), "exempt": exempt,
                                      "note": ("结构化导入页/评审页按口径豁免（来源=page_metadata.import.file_sha256 "
                                               "/ page_metadata.review）；exempt=%d" % exempt)}
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

    scope：`all` / `wiki`(A) / `model`(B) / `source`(C) / `dupes`(D) / `governance`(F)。
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
        check_ontology_artifacts(ctx, rep)
    if scope in ("all", "source"):
        check_sources(ctx, rep)
        check_session_pages(ctx, rep)
    if scope in ("all", "dupes"):
        check_dupes(ctx, rep)
        check_page_identity(ctx, rep)
    if scope in ("all", "governance"):
        check_governance(ctx, rep)
    if scope in ("all", "governance", "context"):
        check_context_map(ctx, rep)
    if scope in ("all", "coupling"):
        check_coupling(ctx, rep)

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


def purge(kb_id: str, kinds: str = "all", scope: str = "all", page_limit: int = 5000,
          slugs: list | None = None, tenant: int | None = None, dry_run: bool = False) -> dict:
    """**一步硬删**（用户口径 2026-09-29）：只要调用者对该库**有写权限**就执行，不再走后台 plan/confirm。

    - `slugs` 给了 → 只硬删这些页（含快照/关系行清理）；
    - 否则按 `kinds`（见 `KIND_HELP`）生成清理计划**并立即执行**；
    - `dry_run=True` 只回影响面，不写库。
    写权限口径与其它写路径一致：属主 / `kb_shares` 的 editor|writer|admin；身份缺失 fail-closed。
    """
    import ke_pages as _ke_pages
    kb, kname, _note = ke_db.resolve_kb_id(kb_id)
    acl = ke_db.assert_can_write(kb, tenant if tenant is not None else ke_db.caller_tenant())
    if not acl.get("allowed"):
        return {"ok": False, "error": "need_write_permission", "permission": acl,
                "hint": ("巡检清理需要对该知识库的写权限（属主 / kb_shares 的 editor|writer|admin）；"
                         "调用者租户来自 MCP 头 X-Bodhi-Tenant 或 env BODHI_TENANT_ID")}
    if slugs:
        targets = [s for s in dict.fromkeys(slugs) if s]
        existing = [r["slug"] for r in ke_db.psql_csv(
            "SELECT slug FROM wiki_pages WHERE knowledge_base_id = %s AND deleted_at IS NULL "
            " AND slug = ANY(ARRAY['%s'])" % (ke_db.sql_str(kb), "','".join(targets)))]
        if dry_run:
            return {"ok": True, "dry_run": True, "kb": {"id": kb, "name": kname}, "mode": "slugs",
                    "will_delete": len(existing), "not_found": [s for s in targets if s not in existing],
                    "permission": acl.get("mode")}
        out = _ke_pages.delete_pages(kb, existing) if existing else {"deleted": 0}
        return {"ok": True, "mode": "slugs", "kb": {"id": kb, "name": kname},
                "permission": acl.get("mode"), "not_found": [s for s in targets if s not in existing],
                **(out if isinstance(out, dict) else {"result": out})}
    plan = build_plan(kb, kinds, scope, page_limit, save=True)
    actions = plan.get("actions") or {}
    per_kind = {}
    for kind, info in actions.items():
        if isinstance(info, dict):
            per_kind[kind] = info.get("count", info.get("pages", info.get("lines")))
    brief = {"plan_id": plan.get("plan_id"), "kinds": plan.get("kinds"),
             "actions": len(actions), "per_kind": per_kind, "current": plan.get("current")}
    if dry_run:
        return {"ok": True, "dry_run": True, "kb": {"id": kb, "name": kname}, "mode": "kinds",
                "plan": brief, "permission": acl.get("mode")}
    applied = apply_plan(kb, plan["plan_id"], True, page_limit)
    return {"ok": True, "mode": "kinds", "kb": {"id": kb, "name": kname},
            "permission": acl.get("mode"), "plan": brief, "applied": applied}


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
    purge_cmd = sub.add_parser("purge", help="**一步硬删**（有该库写权限即可）：--kinds 或 --slugs")
    purge_cmd.add_argument("kb_id")
    purge_cmd.add_argument("--kinds", default="all")
    purge_cmd.add_argument("--slugs", default="", help="逗号分隔：只硬删这些页")
    purge_cmd.add_argument("--tenant", type=int, default=0)
    purge_cmd.add_argument("--dry-run", action="store_true")

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
    if args.cmd == "purge":
        slugs = [s.strip() for s in args.slugs.split(",") if s.strip()]
        print(json.dumps(purge(args.kb_id, args.kinds, "all", 5000, slugs or None,
                               args.tenant or None, args.dry_run), ensure_ascii=False, indent=2))
        return 0
    print(json.dumps(apply_plan(args.kb_id, args.plan_id, args.confirm, args.page_limit),
                     ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
