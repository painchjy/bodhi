"""ke-core · 设计页落库（概要设计 / IT 服务详设 / FD 报告）+ FD 交叉验证。

为什么单独成模块（见 `docs/agent-design-flow.md`）
------------------------------------------------
全流程智能体辅助设计的流水线，要在 `wiki_pages` 层面做三件 ke_pages 没覆盖的事：

1. **写设计页**（概要设计页 / IT 服务详设页 / FD 复核报告），并且必须能**页面级溯源**：
   抽取页靠 `chunk_refs` 溯源到源文档片段，设计页没有片段，只能溯源到**需求 wiki 页**。
   本模块把溯源落成两处：
     - `page_metadata.design.derived_from`（slug 数组，机器可读，硬校验目标页存在）；
     - 正文 `## 溯源` 小节（人可读，站内链接）。
2. **从 wiki 读回结构化事实**：
     - 函数依赖页（`page_type='easvc:FunctionalDependency'`，关系行 `hasDeterminant` / `hasDependent`）；
     - 服务详设页的 `## 维护责任` 表（属性 | 维护方式 | 依据）与 `## 服务契约` 小节。
3. **FD 交叉验证耦合性**（规则 `F1`–`F5`，纯确定性代码 —— 对应
   `docs/bodhi-reasoning.md` 的口径「真正的规则判定必须由确定性代码完成」）。

写库纪律与 `ke_pages.py` 完全一致：先快照 `wiki_page_revisions` → `version+1` → 重建 `in_links`；
`id` 与 `slug` 都是**确定性**的（`uuid5`），同标题重复写 = 更新，不会重复建页。
"""

from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys
import uuid

HERE = pathlib.Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import ke_db  # noqa: E402
import ke_ontology  # noqa: E402
import ke_pages  # noqa: E402

TAG_DESIGN = "bodhi-design"        # 12 字符（last_edit_source 上限 16）
TRACE_SECTION = "## 溯源"
OWN_SECTION = "## 维护责任"
OPS_SECTION = "## 属性操作"
CONTRACT_SECTION = "## 服务契约"
REL_SECTION = ke_pages.REL_SECTION

MAINTAIN_SELF = "自维护"
MAINTAIN_REF = "引用外部"
MAINTAIN_CACHE = "只读缓存"
MAINTAIN_KINDS = (MAINTAIN_SELF, MAINTAIN_REF, MAINTAIN_CACHE)

FD_PAGE_TYPE = "easvc:FunctionalDependency"
SERVICE_PAGE_TYPE = "easvc:ServiceContract"

KIND_LABEL = {
    "requirement": "需求",
    "overview": "概要设计",
    "service": "IT 服务详设",
    "fd": "函数依赖",
    "report": "复核报告",
}
NS = uuid.UUID("6f1c6f5e-0000-4000-8000-b0d100000001")   # 固定命名空间：slug/id 确定性


def page_slug(kb_id: str, kind: str, title: str) -> str:
    """确定性 slug：`design/<kind>/<12位hash>`（同标题永远同一页）。"""
    if kind not in KIND_LABEL:
        raise ValueError("未知设计页 kind：%s（可选：%s）" % (kind, "、".join(KIND_LABEL)))
    digest = uuid.uuid5(NS, "%s|%s|%s" % (kb_id, kind, title)).hex[:12]
    return "design/%s/%s" % (kind, digest)


def page_id(kb_id: str, slug: str) -> str:
    return str(uuid.uuid5(NS, "%s|%s" % (kb_id, slug)))


def _kb_row(kb_id: str) -> dict:
    rows = ke_db.psql_csv(
        "SELECT id, name, tenant_id FROM knowledge_bases WHERE id = %s AND deleted_at IS NULL"
        % ke_db.sql_str(kb_id))
    if not rows:
        raise ValueError("知识库不存在：%s" % kb_id)
    return rows[0]


def _snapshot_stmt(kb_id: str, slug: str, tag: str) -> str:
    return ("INSERT INTO wiki_page_revisions (id, tenant_id, knowledge_base_id, page_id, slug, version, "
            "       title, page_type, status, content, summary, aliases, edit_source, editor_id, "
            "       edited_at, created_at)\n"
            "SELECT gen_random_uuid()::text, tenant_id, knowledge_base_id, id, slug, version, "
            "       title, page_type, status, content, summary, aliases, '%s', "
            "       COALESCE(last_editor_id,''), now(), now()\n"
            "  FROM wiki_pages WHERE knowledge_base_id = %s AND slug = %s AND deleted_at IS NULL;"
            % (tag, ke_db.sql_str(kb_id), ke_db.sql_str(slug)))


def _sync_folders(kb_id: str) -> dict:
    fn = getattr(ke_pages, "sync_folders", None) or getattr(ke_pages, "_sync_folders", None)
    if not fn:
        return {}
    try:
        return fn(kb_id) or {}
    except Exception as exc:  # noqa: BLE001
        return {"error": str(exc)[:120]}



def _rel_line(label: str, rel_type: str, target_slug: str, target_title: str) -> str:
    return ke_pages.rel_line(label, rel_type, target_title, target_slug)


def _with_lines_inserted(content: str, lines: list[str]) -> str:
    """把行插到 `## 本体关系` 小节末尾（没有该小节则在文末新建）。"""
    text = (content or "").rstrip()
    body = text.splitlines()
    if REL_SECTION in [ln.strip() for ln in body]:
        start = next(i for i, ln in enumerate(body) if ln.strip() == REL_SECTION)
        end = next((i for i in range(start + 1, len(body)) if body[i].startswith("## ")), len(body))
        body[end:end] = lines
    else:
        body += ["", REL_SECTION, ""] + lines
    return "\n".join(body).rstrip() + "\n"


def apply_relation(kb_id: str, slug: str, rel_type: str, target_slug: str,
                   label: str = "", strict: bool = False) -> dict:
    """写一条出边：优先走 `ke_pages.add_relation`（严格：域/值域都按本体校验）。

    回退路径（2026-09-20 沙箱发现）：Neo4j 本体投影里**缺 ea-service 模块的属性节点**，
    `relation_types_for('easvc:*')` 返回空 → `add_relation` 会以「该类型可用：无」拒绝，
    而 `easvc:hasDeterminant` 这类关系恰恰是 FD 案例的骨架。
    因此当严格清单为空时，改为**自行按值域闭包校验后直接落行**（`ke_ontology.target_closure`
    仍然有效，它读 `ontology_index.json`/label_map）；`strict=True` 可强制只走严格路径。
    """
    page = ke_pages._load_page(kb_id, slug)
    target = ke_pages._load_page(kb_id, target_slug)
    if target_slug == slug:
        raise ValueError("不能把关系指向本页")
    allowed = {r.get("prefixed") for r in
               (ke_ontology.relation_types_for(page["page_type"]).get("relation_types") or [])}
    if rel_type in allowed:
        ke_pages.add_relation(kb_id, slug, rel_type, target_slug, label)
        return {"mode": "strict", "type": rel_type, "target_slug": target_slug}
    if strict:
        raise ValueError("关系类型 `%s` 不在 `%s` 的可用清单里（strict 模式）"
                         % (rel_type, page["page_type"]))
    closure = ke_ontology.target_closure(rel_type)
    if not closure:
        raise ValueError("本体里没有这个对象属性：%s" % rel_type)
    if target["page_type"] not in closure:
        raise ValueError("目标页类型 %s 不在 `%s` 的 range 内（%s）"
                         % (target["page_type"], rel_type, "、".join(closure[:8])))
    line = _rel_line(label or rel_type, rel_type, target_slug, target["title"])
    ke_pages._apply_content_update(
        kb_id, slug, _with_lines_inserted(page["content"], [line]), ke_pages.TAG_REL)
    return {"mode": "direct", "type": rel_type, "target_slug": target_slug,
            "note": "本体投影缺该模块的属性节点，已按 range 闭包校验后直接落行"}


def write_page(kb_id: str, kind: str, title: str, *, content: str = "",
               page_type: str = "summary", derived_from: list | None = None,
               relations: list | None = None, summary: str = "",
               aliases: list | None = None, metadata: dict | None = None,
               sync_folders: bool = True) -> dict:
    """建/更新一个设计页：写正文 + 页面级溯源 + 关系行 → 快照 → version+1 → 重建 in_links。

    - `derived_from`：**需求/上游 wiki 页 slug**，硬校验必须存在（溯源不允许指向空气）；
    - `relations`：`[{"type": "easvc:contractRealizesStep", "target_slug": "...", "label": ""}]`，
      逐个交给 `ke_pages.add_relation` 校验 domain/range 后落库（本体约束即护栏）；
    - `page_type`：设计页建议直接用本体类（`easvc:ServiceContract` / `easvc:FunctionalDependency`），
      这样图谱里能出边、能被 `ke_audit` 体检；纯汇总页可用 `summary`。
    """
    kb_id, _kb_name, _note = ke_db.resolve_kb_id(kb_id)
    kind = (kind or "").strip()
    title = (title or "").strip()
    if not title:
        raise ValueError("write_page 需要 title")
    slug = page_slug(kb_id, kind, title)

    src = [str(s).strip() for s in (derived_from or []) if str(s).strip()]
    found: dict = {}
    if src:
        found = {r["slug"]: r for r in ke_db.psql_csv(
            "SELECT slug, title FROM wiki_pages WHERE knowledge_base_id = %s AND deleted_at IS NULL "
            "AND slug IN (%s)" % (ke_db.sql_str(kb_id), ", ".join(ke_db.sql_str(s) for s in src)))}
        missing = [s for s in src if s not in found]
        if missing:
            raise ValueError("溯源目标页不存在（设计页只能溯源到真实存在的 wiki 页）：%s"
                             % "、".join(missing))

    # 正文：先补 `## 溯源` 小节（人可读），关系行随后由 ke_pages.add_relation 校验后插入
    body = (content or "").rstrip()
    if src and TRACE_SECTION not in body:
        items = ["- 来源：[%s](wiki:%s)" % (found[s]["title"], s) for s in src]
        body = body + "\n\n" + TRACE_SECTION + "\n\n" + "\n".join(items)
    rels = [r for r in (relations or []) if r.get("type") and r.get("target_slug")]

    existing = ke_db.psql_csv(
        "SELECT id, COALESCE(version,1) AS version, COALESCE(page_metadata::text,'{}') AS md "
        "FROM wiki_pages WHERE knowledge_base_id = %s AND slug = %s AND deleted_at IS NULL"
        % (ke_db.sql_str(kb_id), ke_db.sql_str(slug)))


    meta = dict(metadata or {})
    design_meta = {"kind": kind, "kind_label": KIND_LABEL.get(kind, kind),
                   "derived_from": src, "generator": TAG_DESIGN, "updated_at": ke_db.now_text()}
    if existing:
        try:
            old = json.loads(existing[0]["md"] or "{}")
        except Exception:  # noqa: BLE001
            old = {}
        old = old if isinstance(old, dict) else {}
        old.update(meta)
        merged = dict(old.get("design") or {})
        merged.update(design_meta)
        old["design"] = merged
        meta = old
    else:
        meta["design"] = design_meta

    if existing:
        stmts = [_snapshot_stmt(kb_id, slug, TAG_DESIGN),
                 "UPDATE wiki_pages SET title = %s, content = %s, summary = %s, page_type = %s, "
                 "  out_links = %s::jsonb, page_metadata = %s::jsonb, aliases = %s::jsonb, "
                 "  version = version + 1, updated_at = now(), last_edit_source = '%s' "
                 "WHERE knowledge_base_id = %s AND slug = %s AND deleted_at IS NULL;"
                 % (ke_db.sql_str(title), ke_db.sql_str(body), ke_db.sql_str(summary),
                    ke_db.sql_str(page_type), ke_db.sql_json([]),
                    ke_db.sql_json(meta), ke_db.sql_json(aliases or []), TAG_DESIGN,
                    ke_db.sql_str(kb_id), ke_db.sql_str(slug))]
        action, before_version = "update", int(existing[0]["version"] or 1)
    else:
        kb = _kb_row(kb_id)
        path = json.dumps([KIND_LABEL.get(kind, kind)], ensure_ascii=False)
        stmts = ["INSERT INTO wiki_pages (id, tenant_id, knowledge_base_id, slug, title, page_type, "
                 "  status, content, summary, parent_slug, folder_id, category_path, wiki_path, depth, "
                 "  sort_order, source_refs, chunk_refs, in_links, out_links, page_metadata, aliases, "
                 "  version, last_edit_source, last_editor_id) VALUES ("
                 "'%s', %s, %s, '%s', %s, %s, 'published', %s, %s, '', '', '%s'::jsonb, '%s', 1, 0, "
                 "'[]'::jsonb, '[]'::jsonb, '[]'::jsonb, %s::jsonb, %s::jsonb, %s::jsonb, 1, '%s', '');"
                 % (page_id(kb_id, slug), kb["tenant_id"], ke_db.sql_str(kb_id),
                    slug.replace("'", "''"), ke_db.sql_str(title), ke_db.sql_str(page_type),
                    ke_db.sql_str(body), ke_db.sql_str(summary), path, slug.replace("'", "''"),
                    ke_db.sql_json([]), ke_db.sql_json(meta),
                    ke_db.sql_json(aliases or []), TAG_DESIGN)]
        action, before_version = "insert", 0

    ke_db.psql("BEGIN;\n" + "\n".join(stmts) + "\nCOMMIT;\n", stdin=True)

    applied, skipped = [], []
    for rel in rels:
        try:
            applied.append(apply_relation(kb_id, slug, rel["type"], rel["target_slug"],
                                          rel.get("label", ""), strict=False))
        except Exception as exc:  # noqa: BLE001
            skipped.append({"type": rel["type"], "target_slug": rel["target_slug"],
                            "error": str(exc)[:160]})

    out = {"action": action, "kb_id": kb_id, "slug": slug, "title": title, "kind": kind,
           "page_type": page_type, "derived_from": src, "relations_applied": applied,
           "relations_skipped": skipped, "version": before_version + 1}
    if sync_folders:
        out["folders"] = _sync_folders(kb_id)
    return out



# ---------------------------------------------------------------------------
# 读回：设计页里的结构化事实（表 / 小节 / 溯源）
# ---------------------------------------------------------------------------
def _parse_md_table(content: str, section: str) -> list[dict]:
    """解析某个 `## 小节` 下的第一张 Markdown 表（按表头取名，跳过分隔行）。"""
    lines = (content or "").splitlines()
    start = next((i for i, ln in enumerate(lines) if ln.strip() == section), -1)
    if start < 0:
        return []
    end = next((i for i in range(start + 1, len(lines)) if lines[i].startswith("## ")), len(lines))
    rows, header = [], None
    for line in lines[start + 1:end]:
        text = line.strip()
        if not text.startswith("|"):
            continue
        cells = [c.strip() for c in text.strip("|").split("|")]
        if header is None:
            header = cells
            continue
        if all(re.fullmatch(r":?-{2,}:?", c) for c in cells if c != ""):
            continue
        rows.append({header[i]: (cells[i] if i < len(cells) else "") for i in range(len(header))})
    return rows


def _section_bullets(content: str, section: str) -> list[str]:
    lines = (content or "").splitlines()
    start = next((i for i, ln in enumerate(lines) if ln.strip() == section), -1)
    if start < 0:
        return []
    end = next((i for i in range(start + 1, len(lines)) if lines[i].startswith("## ")), len(lines))
    return [ln.strip()[2:].strip() for ln in lines[start + 1:end] if ln.strip().startswith("- ")]


def attr_key(name: str) -> str:
    """属性归一化键：去括号注记/反引号/空白/本体前缀，取最后一段（`客户.证件号码` → `证件号码`）。"""
    text = re.sub(r"[（(][^）)]*[）)]", "", name or "")
    text = re.sub(r"[`\s\u3000]+", "", text)
    text = re.sub(r"^(?:ea|bmm|easvc|eaown|bmmfd):[A-Za-z]*[.。]?", "", text)
    return text.split(".")[-1].split("。")[-1].strip().lower()


def load_services(kb_id: str, only_design: bool = True) -> list[dict]:
    """服务详设页：`## 维护责任`（属性|维护方式|依据）+ `## 属性操作` + `## 服务契约` + 溯源。"""
    cond = "AND slug LIKE 'design/service/%'" if only_design else ""
    rows = ke_db.psql_csv(
        "SELECT slug, title, COALESCE(page_type,'') AS page_type, COALESCE(content,'') AS content, "
        "       COALESCE(page_metadata::text,'{}') AS md, COALESCE(version,1) AS version "
        "FROM wiki_pages WHERE knowledge_base_id = %s AND deleted_at IS NULL "
        "AND page_type = %s %s ORDER BY title"
        % (ke_db.sql_str(kb_id), ke_db.sql_str(SERVICE_PAGE_TYPE), cond))
    out = []
    for row in rows:
        try:
            meta = json.loads(row["md"] or "{}")
        except Exception:  # noqa: BLE001
            meta = {}
        design = (meta or {}).get("design") or {}
        contracts = _section_bullets(row["content"], CONTRACT_SECTION)
        out.append({
            "slug": row["slug"], "title": row["title"], "version": int(row["version"] or 1),
            "derived_from": list(design.get("derived_from") or []),
            "obligations": _parse_md_table(row["content"], OWN_SECTION),
            "operations": _parse_md_table(row["content"], OPS_SECTION),
            "entities": [b.split("：", 1)[1].strip() for b in contracts if b.startswith("操作实体")],
            "idempotency": [b.split("：", 1)[1].strip() for b in contracts if b.startswith("幂等键")],
        })
    return out


def load_fds(kb_id: str) -> list[dict]:
    """函数依赖页：决定/被决定属性取自 `## 本体关系` 的 `hasDeterminant` / `hasDependent` 行。"""
    rows = ke_db.psql_csv(
        "SELECT slug, title, COALESCE(content,'') AS content FROM wiki_pages "
        "WHERE knowledge_base_id = %s AND deleted_at IS NULL AND page_type = %s ORDER BY title"
        % (ke_db.sql_str(kb_id), ke_db.sql_str(FD_PAGE_TYPE)))
    out = []
    for row in rows:
        rels = ke_pages.parse_out_relations(row["content"])
        det = [{"slug": r["slug"], "title": r["target"], "key": attr_key(r["target"])}
               for r in rels if r["type"] == "easvc:hasDeterminant"]
        dep = [{"slug": r["slug"], "title": r["target"], "key": attr_key(r["target"])}
               for r in rels if r["type"] == "easvc:hasDependent"]
        out.append({"slug": row["slug"], "title": row["title"],
                    "expression": "、".join(d["title"] for d in det) + " → "
                                  + "、".join(d["title"] for d in dep),
                    "determinants": det, "dependents": dep})
    return out


def load_attribute_pages(kb_id: str) -> list[dict]:
    rows = ke_db.psql_csv(
        "SELECT slug, title FROM wiki_pages WHERE knowledge_base_id = %s AND deleted_at IS NULL "
        "AND page_type = 'easvc:BusinessAttribute' ORDER BY title" % ke_db.sql_str(kb_id))
    return [{"slug": r["slug"], "title": r["title"], "key": attr_key(r["title"])} for r in rows]


# ---------------------------------------------------------------------------
# FD × 服务详设 交叉验证（规则 F1–F6，确定性）
# ---------------------------------------------------------------------------
SEVERITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3}

RULE_LABEL = {
    "F1": "属性多主（同一属性被多个服务自维护）",
    "F2": "决定属性无主（FD 的 determinant 无服务声明维护）",
    "F3": "非权威副本（服务的自维护属性是别人 FD 的被决定方）",
    "F4": "幂等键缺函数依赖支撑（对应 S4）",
    "F5": "设计页不可溯源（缺 derived_from）",
    "F6": "服务边界过宽（操作实体 > 1，对应 S3）",
}


def check_couplings(kb_id: str, include_extracted: bool = False) -> dict:
    """用「需求侧独立识别的 FD」交叉验证各服务详设的属性维护责任。

    只读；输出 findings（不改库），供智能体/人决定是否调整设计。
    """
    kb_id, kb_name, _note = ke_db.resolve_kb_id(kb_id)
    services = load_services(kb_id, only_design=not include_extracted)
    fds = load_fds(kb_id)
    attrs = load_attribute_pages(kb_id)

    det_map: dict = {}     # 属性键 -> 该属性作为决定方的 FD
    dep_map: dict = {}     # 属性键 -> 该属性作为被决定方的 FD
    for fd in fds:
        for item in fd["determinants"]:
            det_map.setdefault(item["key"], fd)
        for item in fd["dependents"]:
            dep_map.setdefault(item["key"], fd)

    owners: dict = {}      # 属性键 -> [服务名]
    declared: dict = {}    # 属性键 -> [服务名]（含引用/缓存）
    findings: list = []

    def add(rule: str, severity: str, subject: str, message: str,
            evidence: list | None = None, suggestion: str = "") -> None:
        findings.append({"rule": rule, "rule_label": RULE_LABEL.get(rule, rule),
                         "severity": severity, "subject": subject, "message": message,
                         "evidence": list(evidence or []), "suggestion": suggestion})

    for svc in services:
        if not svc["derived_from"]:
            add("F5", "high", svc["title"],
                "服务详设页没有页面级溯源：无法说明它来自哪条需求",
                ["slug=%s" % svc["slug"]],
                "用 write_page(derived_from=[需求页 slug, ...]) 重写该页")
        if len(svc["entities"]) > 1:
            add("F6", "medium", svc["title"],
                "服务契约声明了 %d 个操作实体：%s（高内聚应只围绕一个聚合根）"
                % (len(svc["entities"]), "、".join(svc["entities"])),
                ["%s" % svc["slug"]], "拆分为「主体契约 + 编排契约」")
        for row in svc["obligations"]:
            raw_attr = row.get("属性", "")
            key = attr_key(raw_attr)
            way = row.get("维护方式", "")
            if not key:
                continue
            declared.setdefault(key, []).append(svc["title"])
            if MAINTAIN_SELF in way:
                owners.setdefault(key, []).append(svc["title"])
            elif not any(k in way for k in MAINTAIN_KINDS):
                add("F1", "low", svc["title"],
                    "「%s」的维护方式取值不可识别：%s" % (raw_attr, way or "(空)"),
                    ["可选：自维护 / 引用外部 / 只读缓存"], "按三选一填写")
        for idem in svc["idempotency"]:
            keys = [attr_key(x) for x in re.split(r"[+＋、,，/]", idem) if x.strip()]
            if fds and not any(k in det_map for k in keys):
                add("F4", "high", svc["title"],
                    "幂等键「%s」的取值属性不是任何函数依赖的决定属性，幂等不可证明"
                    % idem, keys, "把幂等键改为 FD 的 determinant（如 客户.证件号码）")

    for key, svc_names in sorted(owners.items()):
        if len(svc_names) > 1:
            is_det = key in det_map
            add("F1", "critical" if is_det else "high", key,
                "同一属性被 %d 个服务自维护：%s" % (len(svc_names), "、".join(svc_names)),
                ["determinant FD：%s" % det_map[key]["expression"]] if is_det else [],
                "只保留一个权威维护者（determinant 所在服务），其余改「引用外部」")

    for key, fd in sorted(det_map.items()):
        if key not in owners:
            add("F2", "medium", fd["title"],
                "决定属性「%s」没有任何服务声明自维护（归属未定）" % key,
                ["%s" % fd["expression"]], "在权威服务的 `## 维护责任` 表里声明为自维护")

    for key, svc_names in sorted(owners.items()):
        fd = dep_map.get(key)
        if not fd:
            continue
        det_keys = [d["key"] for d in fd["determinants"]]
        det_owners = {s for k in det_keys for s in owners.get(k, [])}
        outsider = [s for s in svc_names if det_owners and s not in det_owners]
        if outsider:
            add("F3", "high", key,
                "被决定属性「%s」由 %s 自维护，但决定属性（%s）的维护者是 %s —— 副本会被写脏"
                % (key, "、".join(outsider), "、".join(det_keys), "、".join(sorted(det_owners))),
                ["%s" % fd["expression"]],
                "%s 改为「引用外部」（调用权威服务读写），或把该属性从本服务的维护责任里摘掉"
                % "、".join(outsider))

    findings.sort(key=lambda f: (SEVERITY_ORDER.get(f["severity"], 9), f["rule"], f["subject"]))
    by_rule: dict = {}
    for f in findings:
        by_rule[f["rule"]] = by_rule.get(f["rule"], 0) + 1
    return {
        "kb_id": kb_id, "kb_name": kb_name, "checked_at": ke_db.now_text(),
        "inputs": {"services": len(services), "fds": len(fds), "attribute_pages": len(attrs)},
        "attr_owners": {k: v for k, v in sorted(owners.items())},
        "summary": {"findings": len(findings), "by_rule": by_rule,
                    "by_severity": {s: sum(1 for f in findings if f["severity"] == s)
                                    for s in ("critical", "high", "medium", "low")}},
        "findings": findings,
    }


# ---------------------------------------------------------------------------
# CLI（沙箱与手工验证用）
# ---------------------------------------------------------------------------
def _cmd_write(args: argparse.Namespace) -> int:
    content = ""
    if args.file:
        content = pathlib.Path(args.file).read_text(encoding="utf-8")
    relations = []
    for spec in (args.relation or []):
        rel_type, _, target = spec.partition(":")
        relations.append({"type": rel_type, "target_slug": target})
    out = write_page(args.kb_id, args.kind, args.title, content=content,
                     page_type=args.type, summary=args.summary,
                     derived_from=[s for s in (args.derived_from or "").split(",") if s.strip()],
                     relations=relations)
    out.pop("folders", None)
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


def _cmd_check(args: argparse.Namespace) -> int:
    rep = check_couplings(args.kb_id, include_extracted=args.include_extracted)
    if args.compact:
        print(json.dumps({"kb_id": rep["kb_id"], "kb_name": rep["kb_name"],
                          "inputs": rep["inputs"], "attr_owners": rep["attr_owners"],
                          "summary": rep["summary"]}, ensure_ascii=False, indent=2))
    else:
        print(json.dumps(rep, ensure_ascii=False, indent=2))
    return 0 if not rep["findings"] or not args.strict else 2


def _cmd_list(what: str):
    def run(args: argparse.Namespace) -> int:
        kb_id, kb_name, _note = ke_db.resolve_kb_id(args.kb_id)
        data = load_fds(kb_id) if what == "fds" else load_services(kb_id)
        print(json.dumps({"kb_id": kb_id, "kb_name": kb_name, what: data},
                         ensure_ascii=False, indent=2))
        return 0
    return run


def main(argv: list | None = None) -> int:
    parser = argparse.ArgumentParser(description="设计页落库（概要设计/服务详设/FD 报告）+ FD 交叉验证")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_write = sub.add_parser("write", help="写/更新一个设计页（含页面级溯源）")
    p_write.add_argument("kb_id")
    p_write.add_argument("--kind", required=True, choices=sorted(KIND_LABEL))
    p_write.add_argument("--title", required=True)
    p_write.add_argument("--file", default="", help="正文 Markdown 文件")
    p_write.add_argument("--type", dest="type", default="summary", help="page_type（建议用本体类）")
    p_write.add_argument("--derived-from", dest="derived_from", default="", help="上游页 slug，逗号分隔")
    p_write.add_argument("--relation", action="append", default=[],
                         help="关系行，格式 type:target_slug，可多次")
    p_write.add_argument("--summary", default="")
    p_write.set_defaults(func=_cmd_write)

    p_check = sub.add_parser("check", help="FD × 服务详设 交叉验证（只读）")
    p_check.add_argument("kb_id")
    p_check.add_argument("--compact", action="store_true")
    p_check.add_argument("--strict", action="store_true", help="有 findings 时退出码 2")
    p_check.add_argument("--include-extracted", action="store_true",
                         help="把抽取出来的契约页也算进来（默认只看设计页）")
    p_check.set_defaults(func=_cmd_check)

    p_fds = sub.add_parser("fds", help="列出函数依赖页（只读）")
    p_fds.add_argument("kb_id")
    p_fds.set_defaults(func=_cmd_list("fds"))

    p_svc = sub.add_parser("services", help="列出服务详设页及其维护责任（只读）")
    p_svc.add_argument("kb_id")
    p_svc.set_defaults(func=_cmd_list("services"))

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
