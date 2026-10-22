"""Service overview + CRUD matrix aggregation (P3 slice C: moved verbatim out of server.py).

Read-mostly aggregation over service/operation/attribute pages and their ontology
relation lines; the async job shell and the save_knowledge call stay in the MCP layer.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime

_CORE = os.path.abspath(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "..", "ke-core"))
if _CORE not in sys.path:
    sys.path.insert(0, _CORE)

import ke_audit
import ke_db
import ke_ontology
import ke_pages

psql_csv = ke_db.psql_csv
sql_str = ke_db.sql_str
now_text = ke_db.now_text

# 2026-10-22: 原 server.py 引用了这两个名字但**从未定义**（潜伏 NameError）——这里补齐。
CRUD_ORDER = ("C", "R", "U", "D")
CRUD_SECTION = "## CRUD 矩阵"


def _resolve_kb_id(raw: str) -> tuple[str, str]:
    """(id, note) —— 与 server 侧同名 helper 同口径。"""
    uid, _name, note = ke_db.resolve_kb_id(raw)
    return uid, note


def service_types() -> set:
    """「IT 服务」类的集合 = `ea:Service` 的**子类闭包**（domain 侧）。

    注意别用 `target_closure("easvc:serviceHasOperation")`：那是 **range** 闭包（ServiceOperation），
    会把操作页当成服务页（2026-09-21 实测：CRUD 矩阵刷错对象）。
    """
    meta = ke_ontology.class_meta()
    out = set()
    for name in meta:
        try:
            if "ea:Service" in ke_ontology.ancestors(name, meta):
                out.add(name)
        except Exception:  # noqa: BLE001
            continue
    return out


def _page_row(kb_id: str, slug: str) -> dict | None:
    rows = psql_csv("SELECT slug, title, COALESCE(page_type,'') AS page_type, "
                    "       COALESCE(content,'') AS content FROM wiki_pages "
                    "WHERE knowledge_base_id = %s AND slug = %s AND deleted_at IS NULL"
                    % (sql_str(kb_id), sql_str(slug)))
    return rows[0] if rows else None


def detail_attributes(page_content: str) -> dict:
    """解析页里 `## 属性（数据属性）` 小节 → {prefixed: 值}（如 `easvc:keyRole` = PK）。

    双读（2026-10-08 契约 §4.3）：新 `### 名称（prefixed，range）` + 后续行（可多行）；旧 `- 名… = 值`。
    """
    lines = (page_content or "").splitlines()
    start = next((i for i, line in enumerate(lines) if line.strip() == "## 属性（数据属性）"), -1)
    if start < 0:
        return {}
    end = next((i for i in range(start + 1, len(lines)) if lines[i].startswith("## ")), len(lines))
    out, cur, buf = {}, None, []

    def _flush():
        nonlocal cur, buf
        if cur and buf:
            out[cur] = "\n".join(buf).strip()
        cur, buf = None, []

    for idx in range(start + 1, end):
        s = lines[idx].strip()
        if s.startswith("### "):
            _flush()
            m = re.search(r"[A-Za-z_]\w*:[A-Za-z_]\w*", s[4:])
            cur = m.group(0) if m else s[4:].strip()
            continue
        hit = _ATTR_VALUE.match(s)
        if hit:
            _flush()
            out[hit.group("name").strip()] = hit.group("value").strip()
            continue
        if cur is not None:
            buf.append(lines[idx].rstrip())
    _flush()
    return out


def crud_model(kb_id: str, service_slug: str) -> dict:
    """把「服务 → 操作 → 属性 + crudKind」聚合出来（只读，跨页）。

    数据真源是 **wiki 页的关系行**（与 in_links/关系面板同一口径）：
      - 服务页 `## 本体关系`：`easvc:serviceHasOperation` → 操作页；
      - 操作页 `## 本体关系`：`easvc:operationOperatesOnAttribute` → 属性页；
      - 操作页 `## 关系限定（边属性）`：同一目标的 `easvc:crudKind`（C/R/U/D）；
      - 属性页 `## 属性（数据属性）`：`easvc:keyRole`（PK/FK/UNIQUE）。
    """
    svc = _page_row(kb_id, service_slug)
    if not svc:
        raise ValueError("页面不存在：%s" % service_slug)
    operations, attributes = [], {}
    for rel in ke_pages.parse_out_relations(svc["content"]):
        if rel["type"] != "easvc:serviceHasOperation" or not rel["slug"]:
            continue
        op = _page_row(kb_id, rel["slug"])
        if not op:
            continue
        quals: dict = {}
        for q in ke_pages.parse_rel_qualifiers(op["content"]):
            quals.setdefault((q["type"], q["slug"]), {}).update(q["properties"])
        touched = []
        for orel in ke_pages.parse_out_relations(op["content"]):
            if orel["type"] != "easvc:operationOperatesOnAttribute" or not orel["slug"]:
                continue
            crud = (quals.get((orel["type"], orel["slug"])) or {}).get("easvc:crudKind", "")
            kinds = [k for k in CRUD_ORDER if k in (crud or "").upper()]
            touched.append({"slug": orel["slug"], "title": orel["target"], "crud": kinds,
                            "crud_raw": crud})
            attr = attributes.setdefault(orel["slug"], {"slug": orel["slug"], "title": orel["target"],
                                                        "key_role": "", "ops": {}})
            for kind in kinds:
                # 同一操作对同一属性可能有多条同类边（如 C 与 R 分开给）→ 矩阵里**只记一次**
                bucket = attr["ops"].setdefault(kind, [])
                if op["title"] not in bucket:
                    bucket.append(op["title"])
            if not attr["key_role"]:
                attr_page = _page_row(kb_id, orel["slug"])
                if attr_page:
                    attr["key_role"] = detail_attributes(attr_page["content"]).get("easvc:keyRole", "")
        operations.append({"slug": rel["slug"], "title": op["title"],
                           "attrs": detail_attributes(op["content"]), "touched": touched})
    return {"service": svc["title"], "service_slug": service_slug,
            "operations": operations, "attributes": attributes}


def crud_matrix_lines(kb_id: str, service_slug: str) -> list[str]:
    """渲染 `## CRUD 矩阵` 小节（没有操作/属性时返回 []，不留空表）。"""
    data = crud_model(kb_id, service_slug)
    attrs = [a for a in data["attributes"].values() if a["ops"]]
    if not attrs:
        return []
    lines = [CRUD_SECTION, "",
             "> 本表由系统按**本体关系**自动生成（服务 → 操作 → 属性，`easvc:crudKind` 为边限定属性）；"
             "改设计请去操作页的「本体关系 / 关系限定」小节，不要手改本表。", "",
             "| 业务属性 | 键 | C | R | U | D | 涉及操作 |", "|---|---|---|---|---|---|---|"]
    for attr in sorted(attrs, key=lambda a: a["slug"]):
        cells = ["√" if kind in attr["ops"] else "" for kind in CRUD_ORDER]
        ops = "、".join("%s(%s)" % (op, kind) for kind in CRUD_ORDER
                        for op in attr["ops"].get(kind, []))
        role = (attr.get("key_role") or "").upper() or "—"
        lines.append("| %s | %s | %s | %s |" % (attr["title"], role, " | ".join(cells), ops))
    writes = sum(1 for a in attrs if any(k in a["ops"] for k in ("C", "U", "D")))
    lines += ["", "**汇总**：操作 %d 个；属性 %d 个（其中被本服务写 %d 个、只读 %d 个）。"
              % (len(data["operations"]), len(attrs), writes, len(attrs) - writes), ""]
    return lines


def _replace_section(content: str, title: str, block: list[str]) -> str:
    """把正文里的 `title` 小节替换成 `block`（不存在则追加到末尾）。"""
    lines = (content or "").splitlines()
    start = next((i for i, line in enumerate(lines) if line.strip() == title), -1)
    if start < 0:
        return "\n".join(lines).rstrip() + "\n\n" + "\n".join(block).rstrip() + "\n"
    end = next((i for i in range(start + 1, len(lines)) if lines[i].startswith("## ")), len(lines))
    merged = lines[:start] + list(block) + lines[end:]
    return "\n".join(merged).rstrip() + "\n"


def refresh_crud_matrix(kb_id: str, service_slugs: list) -> dict:
    """把服务页的 `## CRUD 矩阵` 刷成最新（读库 → 改正文 → 版本快照 + 反向边重算）。

    为什么放在落库之后单独刷：矩阵是**跨页聚合**（服务 → 操作 → 属性），
    本次新写的操作/属性页刚落库，只有写库完成后再读才拿得到。
    """
    done, skipped = [], []
    for slug in sorted({s for s in (service_slugs or []) if s}):
        try:
            page = _page_row(kb_id, slug)
            if not page:
                continue
            block = crud_matrix_lines(kb_id, slug)
            if not block:
                skipped.append(slug)
                continue
            if "\n".join(block).rstrip() in (page["content"] or ""):
                skipped.append(slug)
                continue
            ke_pages.rewrite_page_content(kb_id, slug,
                                          _replace_section(page["content"], CRUD_SECTION, block))
            done.append(slug)
        except Exception as exc:  # noqa: BLE001  刷矩阵失败不影响落库结果
            skipped.append("%s(%s)" % (slug, str(exc)[:60]))
    return {"refreshed": done, "skipped": skipped}


# ---------------------------------------------------------------------------
# 服务详细设计总览（评审用；确定性渲染，跨页聚合）
# ---------------------------------------------------------------------------
OVERVIEW_SLUG = "ea/summary/it服务详细设计总览"
OVERVIEW_TITLE = "IT 服务详细设计总览"
OVERVIEW_FOLDER = "服务详细设计"
_ATTR_OF = "easvc:attributeOf"


def service_design_summary(kb_id: str) -> dict:
    """把库里所有 IT 服务的详设聚合成结构（只读）：服务 → 操作/属性/键/依赖。"""
    kb_id = _resolve_kb_id(kb_id)[0]  # 名称 / UUID 都要能传（2026-09-21 实测：传名字直接返回空）
    svc_types = sorted(service_types())
    if not svc_types:
        return {"services": [], "attributes": {}, "deps": [], "service_count": 0}
    cond = ", ".join(sql_str(t) for t in svc_types)
    rows = psql_csv("SELECT slug, title, page_type FROM wiki_pages WHERE knowledge_base_id = %s "
                    "AND deleted_at IS NULL AND page_type IN (%s) ORDER BY title"
                    % (sql_str(kb_id), cond))
    services, attr_writers, attr_of, op_service, deps = {}, {}, {}, {}, []
    collected = []  # (服务标题, 操作)——操作页稍后**一次批量查**（省 N 次 psql：29 操作 ≈ 12s）
    for row in rows:
        model = crud_model(kb_id, row["slug"])
        writes, reads = {}, {}
        for attr in model["attributes"].values():
            for kind in CRUD_ORDER:
                if kind not in attr["ops"]:
                    continue
                target = writes if kind in ("C", "U", "D") else reads
                target.setdefault(attr["title"], []).extend(attr["ops"][kind])
                if kind in ("C", "U", "D"):
                    attr_writers.setdefault(attr["title"], set()).add(row["title"])
        for op in model["operations"]:
            op_service[op["slug"]] = row["title"]
            op["retry"] = (op["attrs"].get("easvc:operationRetryPolicy") or "").strip()
            collected.append((row["title"], op))
        services[row["title"]] = {"slug": row["slug"], "page_type": row["page_type"], "model": model,
                                  "writes": {k: sorted(set(v)) for k, v in writes.items()},
                                  "reads": {k: sorted(set(v)) for k, v in reads.items()}}
    op_slugs = [op["slug"] for _t, op in collected if op.get("slug")]
    op_pages = {}
    if op_slugs:
        lst = ", ".join(sql_str(s) for s in op_slugs)
        for r in psql_csv("SELECT slug, COALESCE(content,'') AS content FROM wiki_pages "
                          "WHERE knowledge_base_id = %s AND deleted_at IS NULL AND slug IN (%s)"
                          % (sql_str(kb_id), lst)):
            op_pages[r["slug"]] = r["content"]
    for svc_title, op in collected:
        for rel in ke_pages.parse_out_relations(op_pages.get(op["slug"], "")):
            if rel["type"] == "easvc:operationDependsOnOperation" and rel["slug"]:
                deps.append({"reader": svc_title, "op": op["title"],
                             "dep_op_slug": rel["slug"], "dep_op": rel["target"]})
    for row in psql_csv("SELECT slug, title, COALESCE(content,'') AS content FROM wiki_pages "
                        "WHERE knowledge_base_id = %s AND deleted_at IS NULL "
                        "AND page_type = 'easvc:BusinessAttribute'" % sql_str(kb_id)):
        attrs = detail_attributes(row["content"])
        ref = next((r for r in ke_pages.parse_out_relations(row["content"])
                    if r["type"] == _ATTR_OF), None)
        attr_of[row["title"]] = {"key_role": (attrs.get("easvc:keyRole") or "").strip().upper(),
                                 "entity": (ref or {}).get("target", ""),
                                 "writers": sorted(attr_writers.get(row["title"], []))}
    for item in deps:
        item["dep_service"] = op_service.get(item["dep_op_slug"], "")
    return {"services": services, "attributes": attr_of, "deps": deps,
            "service_count": len(services)}


def service_overview_lines(kb_id: str, with_audit: bool = True, data: dict | None = None) -> list[str]:
    """渲染「IT 服务详细设计总览」Markdown（评审用）。

    `with_audit=False` 时不跑巡检；`data` 可传入已算好的 `service_design_summary()`
    结果（避免重复聚合：实测一次聚合 ~30s，重复一次就翻倍）。
    """
    kb_id = _resolve_kb_id(kb_id)[0]
    data = data or service_design_summary(kb_id)
    services, attrs = data["services"], data["attributes"]
    if not services:
        return []
    op_total = sum(len(s["model"]["operations"]) for s in services.values())
    keys = {n: m for n, m in attrs.items() if m["key_role"] in ("PK", "UNIQUE", "FK")}
    checks = {}
    if with_audit:
        try:
            checks = ke_audit.audit(kb_id, scope="coupling", max_findings=0)["summary"]["checks"]
        except Exception:  # noqa: BLE001
            checks = {}
    lines = ["# %s（自动生成）" % OVERVIEW_TITLE, "",
             "> 本页由系统按**本体关系**自动生成（服务 → 操作 → 业务属性 → CRUD/键/依赖），**不要手改**。",
             "> 口径：**写方收敛**（每个业务属性只有一个服务写 → 无 E1）+ 跨服务读一律声明 "
             "`easvc:operationDependsOnOperation`（**经接口**，E2 为合理耦合）。", "",
             "## 1. 规模与结论", "",
             "- 服务 **%d** 个；操作 **%d** 个；业务属性 **%d** 个（键属性 %d：PK %d / UNIQUE %d / FK %d）"
             % (data["service_count"], op_total, len(attrs), len(keys),
                sum(1 for m in keys.values() if m["key_role"] == "PK"),
                sum(1 for m in keys.values() if m["key_role"] == "UNIQUE"),
                sum(1 for m in keys.values() if m["key_role"] == "FK")),
             "- 巡检（coupling）：%s"
             % ("E1 写耦合 **%d**；E2 读耦合 **%d**（经接口）；E3 完整性 **%d**；E4 键一致性 **%d**"
                % (checks.get("E1", 0), checks.get("E2", 0), checks.get("E3", 0), checks.get("E4", 0))
                if with_audit else "见 `audit_scan(scope=\"coupling\")`（本页落库时由运维脚本补全）"),
             "- 生成时间：%s" % datetime.now().strftime("%Y-%m-%d %H:%M"), ""]

    lines += ["## 2. 服务一览", "",
              "| 服务 | 类型 | 操作 | 本服务作为写方的属性 | 只读的属性（他人写） | 涉及键 |",
              "|---|---|---|---|---|---|"]
    for name in sorted(services):
        svc = services[name]
        # 「写方」= 有 C/U/D 即算（同一操作对同属性读写并用时两边都出现，故不能相减）
        own = sorted(set(svc["writes"]))
        ro = sorted(set(svc["reads"]) - set(svc["writes"]))
        svc_keys = sorted(k for k in keys if k in set(svc["writes"]) | set(svc["reads"]))
        lines.append("| %s | `%s` | %d | %s | %s | %s |"
                     % (name, svc["page_type"], len(svc["model"]["operations"]),
                        "、".join(own) or "—", "、".join(ro) or "—", "、".join(svc_keys) or "—"))
    lines.append("")

    lines += ["## 3. 业务属性与键（数据设计）", "",
              "| 业务属性 | 键角色 | 所属实体 | 写它的服务（唯一） |", "|---|---|---|---|"]
    for name in sorted(attrs, key=lambda n: (attrs[n]["key_role"] or "Z", n)):
        meta = attrs[name]
        lines.append("| %s | %s | %s | %s |" % (name, meta["key_role"] or "非键",
                                               meta["entity"] or "—", "、".join(meta["writers"]) or "—"))
    lines += ["",
              "> 写方为 `—` = 该属性由**本流程之外的既有系统**维护，本设计只读（不是遗漏）；",
              "> 外键一律用 `easvc:referencesAttribute` 表达（`keyRole` 只标 PK / UNIQUE），"
              "见各服务页「## CRUD 矩阵」与关系面板。", ""]

    lines += ["## 4. 跨服务读依赖（经接口）", "",
              "| 读方服务 | 读的操作 | 被依赖的写方操作 | 写方服务 |", "|---|---|---|---|"]
    for item in sorted(data["deps"], key=lambda x: (x["reader"], x["op"])):
        lines.append("| %s | %s | %s | %s |" % (item["reader"], item["op"], item["dep_op"],
                                                item["dep_service"] or "—"))
    lines.append("")
    return lines + _overview_detail_lines(services, keys)


def _overview_detail_lines(services: dict, keys: dict) -> list[str]:
    """总览第 5 节：逐服务的操作明细（写/读属性、方法、幂等、事务、重试）。"""
    lines = ["## 5. 操作明细", ""]
    for name in sorted(services):
        svc = services[name]
        lines += ["### %s（`%s`）" % (name, svc["page_type"]), "",
                  "| 操作 | 实现方式 | 幂等 | 事务边界 | 写 | 读 |", "|---|---|---|---|---|---|"]
        for op in svc["model"]["operations"]:
            a = op["attrs"]
            lines.append("| %s | %s | %s | %s | %s | %s |"
                         % (op["title"], a.get("easvc:operationMethod", "—"),
                            a.get("easvc:isIdempotent", "—"),
                            a.get("easvc:transactionBoundary", "—"),
                            "、".join(sorted(k for k, v in svc["writes"].items() if op["title"] in v)) or "—",
                            "、".join(sorted(k for k, v in svc["reads"].items() if op["title"] in v)) or "—"))
        retry = [op for op in svc["model"]["operations"] if op.get("retry")]
        if retry:
            lines += ["", "非幂等写的重试/补偿口径："]
            lines += ["- **%s**：%s" % (op["title"], op["retry"]) for op in retry]
        lines.append("")
    return lines
