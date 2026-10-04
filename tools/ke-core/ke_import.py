"""ke-core · 结构化数据**批量建模**（probe / plan / apply / state）。

用户口径（2026-09-29）
--------------------
- 智能体**接受自然语言描述的 Excel 结构**，把它对应到本体的**类 / 关系 / 数据属性**；
- **约定：一次批量建模只包含「一个类（含其数据属性）」或「一条关系」** —— 因此同一份文件
  **多扫几遍**就能把所有类与关系建完（`import_state` 报还差哪些，收敛即完成）；
- 不逐行确认：`plan` 出**一次**完整影响面 + `ticket`，用户同意后 `apply` 整批写。

四个动作
--------
    probe(file)                     只读：sheet / 表头 / 行数 / 抽样 / 重复表头 / 列前缀（复用 ke_sheet）
    plan(kind, target, file, kb_id, …)  只读：校验本体面 + 影响面 + 风险 + ticket（写进账本）
    apply(ticket, ack, …)           写：**只写这一个 target**（500 行/事务；幂等；末了重算 in_links）
    state(batch)                    账本：每个 target 的状态与 remaining（智能体靠它循环到收敛）

关键不变量
----------
1. **一次一个目标**：`kind=class` 的 target 是一个类；`kind=relation` 的 target 是一条关系。多个 → 拒。
2. **两段式**：`plan` 出 `ticket`（= 文件 sha256 + target + 映射 + 行数 的指纹）；`apply` 必须带同一 ticket。
3. **幂等**：页面 `last_edit_source='bodhi-import:<batch>:<target>'`；内容哈希未变 → **零写入**；
   变化 → 快照(`wiki_page_revisions`) + `version+1`；`prune=true` 软删"源里已消失"的本批页。
4. **巡检干净**：每页带 `page_type`（本体类）+「## 原文依据」逐字原文 + `page_metadata.ontology.attributes`
   + `page_metadata.import{file_sha256,sheet,row}`；关系批次把 `- 标签（关系名）→ [[slug|标题]]` 追加到
   **domain 侧页**的「## 本体关系」，再全库重算 `in_links`。
5. **写权限**：`ke_db.assert_can_write(kb_id, tenant)`（与其它写路径同一守门）。
6. **零第三方依赖**：读表走 `ke_sheet`（标准库 zipfile+xml）。

账本：`state/import/<batch>.json`（含每个 target 的 plan 快照、ticket、结果），可人工审阅/回滚。
"""

from __future__ import annotations

import hashlib
import json
import pathlib
import re
import sys

HERE = pathlib.Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(HERE))

import ke_db  # noqa: E402
import ke_ontology  # noqa: E402
import ke_pages  # noqa: E402
import ke_sheet  # noqa: E402

STATE_DIR = REPO / "state" / "import"
TAG = "bodhi-import"                     # last_edit_source 前缀
CHUNK = 500                              # 每事务行数
REL_SECTION = "## 本体关系"
EVIDENCE_SECTION = "## 原文依据"
AUTH_SECTION = "## 属性"
DEF_SECTION = "## 定义"

# 本体类 → 正文里「属性表」的列顺序（缺省按 mapping 顺序）
SLUG_SAFE = re.compile(r"[\\/\s]+")


def _sha256_file(path: str | pathlib.Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def _sha1(text: str) -> str:
    return hashlib.sha1((text or "").encode("utf-8")).hexdigest()


def _slugify(text: str) -> str:
    """键值 → slug 片段：去首尾空白、把 `/` 与空白压成 `-`，保留中文。"""
    return SLUG_SAFE.sub("-", (text or "").strip()).strip("-")


def _norm_key(text: str) -> str:
    return re.sub(r"\s+", "", (text or "")).strip().lower()


def class_slug_prefix(page_type: str) -> str:
    """`bmm:MainSystem` → `bmm/mainsystem`（实例页三段式的前两段）。"""
    module, _, local = (page_type or "").partition(":")
    return "%s/%s" % (module, local.lower())


def page_slug(page_type: str, key: str) -> str:
    return "%s/%s" % (class_slug_prefix(page_type), _slugify(key))


def _rows_as_dicts(sheet_rows: list[list[str]], header: list[str]) -> list[dict]:
    """二维行 → [{列名: 值, "__row__": 原始行号}]；跳过多余/全空行；重复表头**保留第一列**并记 `__dup__`。"""
    out = []
    for idx, row in enumerate(sheet_rows, start=2):      # 表头占第 1 行
        if not any((c or "").strip() for c in row):
            continue
        item: dict = {"__row__": idx, "__raw__": list(row)}
        for ci, col in enumerate(header):
            value = (row[ci] if ci < len(row) else "") or ""
            if col in item:
                continue                                  # 重复表头：只取第一个（第二个进 __raw__）
            item[col] = value.strip()
        out.append(item)
    return out


# ---------------------------------------------------------------------------
# 数据 / 本体 读取辅助
# ---------------------------------------------------------------------------
def _load_sheet(file: str, sheet: str = "", limit: int = 0) -> tuple[list[str], list[dict], str]:
    """→ (表头, 行字典列表, 文件 sha256)。空表头/空 sheet 直接报错。"""
    data = ke_sheet.read_any(file, sheet=sheet, max_rows=limit)
    if not data["sheets"]:
        raise ValueError("文件里没有可读 sheet：%s" % file)
    item = data["sheets"][0]
    rows = item["rows"]
    if not rows:
        raise ValueError("sheet 是空的：%s" % (sheet or item["name"]))
    header = [(c or "").strip() for c in rows[0]]
    return header, _rows_as_dicts(rows[1:], header), _sha256_file(file)


def class_meta(page_type: str) -> dict:
    """本体类元信息（不存在 → 报错）。`classes()` = {"source","classes"}，classes 是**列表**。"""
    data = ke_ontology.classes() or {}
    table = data.get("classes") if data.get("classes") is not None else data
    name = str(page_type or "").strip()
    if isinstance(table, dict):
        info = table.get(name)
    else:
        info = next((c for c in (table or []) if c.get("prefixed") == name), None)
    if not info:
        raise ValueError("不是本体的类：%s（用 `ontology_types` 或 skills(skill=\"structured_modeling\") "
                         "拿可用类清单）" % page_type)
    return info


def class_attrs(page_type: str, mapping: dict) -> tuple[dict, list]:
    """列 → 数据属性（`systemNo` / `bmm:systemNo` 都认）→ (归一化映射, 未声明项)。"""
    allowed = ke_ontology.data_properties_for(page_type) or {}
    out, bad = {}, []
    module = str(page_type).split(":")[0]
    for column, attr in (mapping or {}).items():
        want = str(attr or "").strip()
        if not want:
            continue
        prefixed = want if ":" in want else "%s:%s" % (module, want)
        if prefixed not in allowed:
            bad.append({"column": column, "attribute": want})
            continue
        out[column] = prefixed
    return out, bad


def relation_meta(target: str) -> dict:
    """按 prefixed 名找本体对象属性 → {'label','targets'(range),'domain','module'}。"""
    name = str(target or "").strip()
    for module in (ke_ontology.index_data().get("models") or []):
        for rel in (module.get("relations") or []):
            if rel.get("name") == name:
                return {"label": rel.get("label") or name, "targets": rel.get("range") or [],
                        "domain": rel.get("domain") or [], "module": module.get("key") or ""}
    raise ValueError("不是本体的关系：%s（关系只能取本体里声明的对象属性）" % target)


def _ledger_path(batch: str) -> pathlib.Path:
    return STATE_DIR / ("%s.json" % batch)


def _ledger_load(batch: str) -> dict:
    path = _ledger_path(batch)
    if path.is_file():
        return json.loads(path.read_text(encoding="utf-8"))
    return {"batch": batch, "targets": {}}


# ---------------------------------------------------------------------------
# plan：只读校验 + 影响面 + ticket（写进账本，不改任何页）
# ---------------------------------------------------------------------------
def plan(kind: str, target: str, file: str, kb_id: str = "", sheet: str = "", mapping: dict | None = None,
         key_column: str = "", title: str = "", aliases: list | None = None,
         source_key_column: str = "", target_key_column: str = "",
         source_class: str = "", target_class: str = "", prune: bool = False,
         batch_id: str = "", limit: int = 0, unknown_to_description: bool = True,
         enums: dict | None = None) -> dict:
    """**只读**。`kind="class"`：一个类 + 它的数据属性（`key_column` 是行→slug 的键列）；
    `kind="relation"`：一条关系（`source_key_column`/`target_key_column` 是两侧键列）。

    `unknown_to_description=False` 时**不把未映射列聚合进 description**（适合"部门/组织机构"这类
    只有名称、整行其余列与本实体无关的表）。

    `enums`：把**枚举列**建成「关系 → 枚举值」（不是数据属性），例：
    `{"级别": {"relation": "bmm:hasEnforcementLevel",
               "values": {"强制": "bmm:Strict", "推荐": "bmm:Advisory", "可覆盖": "bmm:Override"}}}`
    —— 工具只校验「该关系是不是这个类的合法关系」，取值映射由你（智能体）从用户口径给出。
    """
    if kind not in ("class", "relation"):
        return {"error": "kind 只能是 class（一个类）或 relation（一条关系）", "kind": kind}
    header, rows, sha = _load_sheet(file, sheet, limit)
    if not rows:
        return {"error": "没有数据行（只有表头？）", "file": file, "header": header}
    if not key_column or key_column not in header:
        return {"error": "key_column 必须给出且存在于表头", "key_column": key_column, "header": header}
    kb_res = ke_db.resolve_kb_id(kb_id) if kb_id else ("", "")
    kb = kb_res[0] if isinstance(kb_res, (tuple, list)) else str(kb_res)
    kb_name = kb_res[1] if isinstance(kb_res, (tuple, list)) and len(kb_res) > 1 else kb_id
    if not kb:
        return {"error": "需要 kb_id（目标知识库的 uuid 或精确库名）"}
    if not ke_db.psql_csv("SELECT 1 AS ok FROM knowledge_bases WHERE id = %s AND deleted_at IS NULL"
                          % ke_db.sql_str(kb)):
        return {"error": "知识库不存在或已删：%s" % kb_id}

    issues, questions, samples, counts, extra = [], [], [], {}, {}
    if kind == "class":
        conv = str(target or "").strip()
        conv = conv if ":" in conv else "bmm:%s" % conv
        info = class_meta(conv)
        attrs, bad = class_attrs(conv, mapping or {})
        if bad:
            return {"ok": False, "error": "attribute_not_declared（有列映射到未声明的数据属性）",
                    "issues": [{"code": "attribute_not_declared", "detail": bad,
                                "allowed": sorted(ke_ontology.data_properties_for(conv) or {})}],
                    "header": header}
        enum_spec, enum_bad, enum_miss = build_enum_spec(conv, enums or {}, rows)
        if enum_bad:
            return {"ok": False, "error": "enum_relation_not_allowed（枚举列映射到了不合法的关系）",
                    "issues": [{"code": "enum_relation_not_allowed", "detail": enum_bad}],
                    "header": header}
        for item in enum_miss:
            questions.append("列「%s」有 %d 个取值没在映射表里（不写关系行）：%s"
                             % (item["column"], len(item["values"]), "、".join(item["values"])))
        keys, dup, empty = [], [], 0
        for row in rows:
            value = (row.get(key_column) or "").strip()
            if not value:
                empty += 1
                continue
            if value in keys:
                dup.append(value)
                continue
            keys.append(value)
        unknown = [c for c in header if c not in attrs and c not in enum_spec and c != key_column]
        if unknown:
            questions.append("有 %d 列未映射：%s → 默认聚合进 description（要单独建模请改 mapping）"
                             % (len(unknown), "、".join(unknown[:8])))
        slugs = [page_slug(conv, k) for k in keys]
        existing = {r["slug"] for r in ke_db.psql_csv(
            "SELECT slug FROM wiki_pages WHERE knowledge_base_id = %s AND deleted_at IS NULL "
            " AND slug LIKE %s" % (ke_db.sql_str(kb), ke_db.sql_str(class_slug_prefix(conv) + "/%")))}
        counts = {"rows": len(rows), "entities": len(keys), "duplicate_keys": len(dup),
                  "empty_keys": empty, "create": len([s for s in slugs if s not in existing]),
                  "update": len([s for s in slugs if s in existing])}
        for value, slug in list(zip(keys, slugs))[:3]:
            row = next(r for r in rows if (r.get(key_column) or "").strip() == value)
            samples.append({"slug": slug, "title": render_title(title, row, value, info),
                            "attributes": {column: row.get(column, "") for column in attrs},
                            "evidence": " | ".join((c or "") for c in row["__raw__"]).strip()[:180],
                            "row": row["__row__"]})
        extra = {"page_type": conv, "class_label": info.get("label") or conv, "attrs": attrs,
                 "unknown_columns": unknown, "aliases": aliases or [], "title": title,
                 "unknown_to_description": bool(unknown_to_description), "enums": enum_spec}
        plan_key = "class|%s|%s" % (conv, key_column)
    else:
        rel = relation_meta(str(target or "").strip())
        if not source_key_column or not target_key_column:
            return {"error": "关系批次需要 source_key_column 与 target_key_column", "relation": rel}
        for col in (source_key_column, target_key_column):
            if col not in header:
                return {"error": "键列不在表头里：%s" % col, "header": header}
        pairs = []
        for row in rows:
            a = (row.get(source_key_column) or "").strip()
            b = (row.get(target_key_column) or "").strip()
            if a and b and (a, b) not in pairs:
                pairs.append((a, b))
        if not (source_class and target_class):
            return {"error": "关系批次需要 source_class 与 target_class（拼 slug 用）",
                    "hint": "例如 source_class=bmm:MainSystem, target_class=bmm:SubSystem"}
        s_prefix, t_prefix = class_slug_prefix(source_class) + "/", class_slug_prefix(target_class) + "/"
        known = {r["slug"] for r in ke_db.psql_csv(
            "SELECT slug FROM wiki_pages WHERE knowledge_base_id = %s AND deleted_at IS NULL"
            % ke_db.sql_str(kb))}
        missing, resolvable = [], 0
        for a, b in pairs:
            s_slug, t_slug = s_prefix + _slugify(a), t_prefix + _slugify(b)
            if s_slug in known and t_slug in known:
                resolvable += 1
            else:
                missing.append({"source": a, "target": b, "source_slug": s_slug, "target_slug": t_slug,
                                "why": ("源页缺 " if s_slug not in known else "")
                                + ("目标页缺" if t_slug not in known else "")})
        counts = {"rows": len(rows), "pairs": len(pairs), "resolvable": resolvable,
                  "dangling": len(missing)}
        if missing:
            questions.append("有 %d 对键在库里找不到页（先跑类批次、再跑关系批次）" % len(missing))
        samples = [{"source_key": a, "target_key": b} for a, b in pairs[:3]]
        extra = {"relation_label": rel["label"], "relation_targets": rel["targets"],
                 "dangling": missing[:10], "source_class": source_class, "target_class": target_class,
                 "page_type": source_class}
        plan_key = "relation|%s|%s|%s" % (target, source_key_column, target_key_column)

    ticket = _sha1("import-v1|%s|%s|%s|%s|%s|%s|%s|%s"
                   % (sha, kind, target, kb, json.dumps(mapping or {}, sort_keys=True),
                      json.dumps(counts, sort_keys=True),
                      key_column + source_key_column + target_key_column,
                      json.dumps(enums or {}, sort_keys=True)))
    batch = batch_id or ("imp-%s" % _sha1("%s|%s" % (sha, kb))[:10])
    ledger = _ledger_load(batch)
    ledger.update({"batch": batch, "kb_id": kb, "kb_name": kb_name, "file": str(file), "sha256": sha,
                   "sheet": sheet or "(first)", "header": header, "updated_at": ke_db.now_text()})
    entry = ledger["targets"].get(plan_key, {})
    entry.update({"kind": kind, "target": target, "ticket": ticket, "status": "planned",
                  "counts": counts, "mapping": mapping or {}, "key_column": key_column,
                  "source_key_column": source_key_column, "target_key_column": target_key_column,
                  "title": title, "aliases": aliases or [], "prune": bool(prune),
                  "planned_at": ke_db.now_text(), **extra})
    ledger["targets"][plan_key] = entry
    _ledger_save(ledger)
    return {"ok": True, "batch": batch, "kind": kind, "target": target, "ticket": ticket,
            "kb_id": kb, "kb_name": kb_name, "counts": counts, "samples": samples,
            "issues": issues, "questions": questions, "ledger": str(_ledger_path(batch)),
            "plan_key": plan_key, "next": "确认后用同一 ticket 调 import_apply（一次只写这一个目标）"}


# ---------------------------------------------------------------------------
# 渲染：标题 / 正文（定义 + 属性表 + 原文依据）
# ---------------------------------------------------------------------------
def render_title(template: str, row: dict, key: str, info: dict) -> str:
    """标题模板：`{列名}` 占位 + `{key}`（键列值）+ `{name}`（兜底用键值）。"""
    text = str(template or "{key}")
    for col, value in row.items():
        if col.startswith("__"):
            continue
        text = text.replace("{%s}" % col, str(value or "").strip())
    text = text.replace("{key}", key).replace("{name}", key)
    return text.strip() or key


def _description_of(row: dict, attrs: dict, unknown: list) -> str:
    """「## 定义」：优先取映射到 `:description`/`:definition` 的列；未映射列按 `【列名】值` 聚合。"""
    parts = []
    for column, attr in attrs.items():
        if attr.endswith(":description") or attr.endswith(":definition"):
            value = (row.get(column) or "").strip()
            if value:
                parts.append(value)
    for column in unknown:
        value = (row.get(column) or "").strip()
        if value:
            parts.append("【%s】%s" % (column, value))
    return "\n\n".join(parts) or "（源表未提供）"


def enum_relations_of(meta: str) -> dict:
    """从页元数据取「枚举关系」（`bmm:hasEnforcementLevel` → `bmm:Advisory`）。"""
    try:
        data = json.loads(meta or "{}")
    except Exception:  # noqa: BLE001
        return {}
    return ((data.get("ontology") or {}).get("enum_relations") or {})


def build_enum_spec(klass: str, enums: dict, rows: list) -> tuple[dict, list, list]:
    """校验「枚举列 → 关系」的映射（**确定性**：只查本体，不做语义推断）。

    `enums` 形态：`{"级别": {"relation": "bmm:hasEnforcementLevel",
                            "values": {"强制": "bmm:Strict", "推荐": "bmm:Advisory"}}}`
    返回 `(spec, bad, unmapped)`：spec 可直接进 plan/apply；bad = 关系不合法；unmapped = 数据里出现的、映射表没覆盖的取值。
    """
    allowed = ke_ontology.relation_type_map(klass) if hasattr(ke_ontology, "relation_type_map") else {}
    spec, bad, unmapped = {}, [], []
    for column, raw in (enums or {}).items():
        conf = raw if isinstance(raw, dict) else {"relation": str(raw), "values": {}}
        # **2A（2026-10-05）：列 → 数据属性**（受控取值，如 执行级别/评估类型）。
        # `{"级别": {"property": "bmm:enforcementLevel", "values": {"强制": "严格执行"}}}`
        # 与对象属性（`relation`）两条路并存：本体的枚举**取值**不再是类实例，禁止另建知识页。
        prop = str(conf.get("property") or "").strip()
        if prop:
            values = {str(k).strip(): (str(v).strip() or str(k).strip())
                      for k, v in (conf.get("values") or {}).items()}
            attr_fn = getattr(ke_ontology, "attribute_types_for", None)
            allowed_attrs = attr_fn(klass) if callable(attr_fn) else {}
            if allowed_attrs and prop not in allowed_attrs:
                bad.append({"column": column, "property": prop,
                            "why": "%s 不是 %s 声明（含继承）的数据属性" % (prop, klass),
                            "allowed": sorted(allowed_attrs)[:40]})
                continue
            seen = {str(r.get(column) or "").strip() for r in rows}
            miss = sorted(v for v in seen if v and v not in values)
            if miss:
                unmapped.append({"column": column, "property": prop, "values": miss[:10]})
            spec[column] = {"property": prop, "label": prop.split(":")[-1], "values": values}
            continue
        rel = str(conf.get("relation") or "").strip()
        if not rel:
            bad.append({"column": column, "why": "缺 relation"})
            continue
        if allowed and rel not in allowed:
            bad.append({"column": column, "relation": rel,
                        "why": "%s 不是 %s 的合法关系（domain 不含该类，含继承）" % (rel, klass),
                        "allowed": sorted(allowed)})
            continue
        info = allowed.get(rel) or {}
        values = {str(k).strip(): str(v).strip() for k, v in (conf.get("values") or {}).items()}
        seen = {str(r.get(column) or "").strip() for r in rows}
        miss = sorted(v for v in seen if v and v not in values)
        if miss:
            unmapped.append({"column": column, "relation": rel, "values": miss[:10]})
        spec[column] = {"relation": rel, "label": info.get("label") or rel, "values": values,
                        "range": info.get("targets") or info.get("range") or []}
    return spec, bad, unmapped


def render_class_content(page_type: str, row: dict, attrs: dict, title: str, unknown: list,
                         source: dict, tag: str, enums: dict | None = None) -> str:
    """类批次正文：`# 标题` + 类型/生成方式/来源 + `## 定义` + `## 属性` + `## 原文依据`
    （`enums` 给了就再写「## 本体关系」的**枚举关系行**，如 `- 具有执行级别（`bmm:hasEnforcementLevel`）→ bmm:Advisory（推荐）`）。"""
    label = source.get("class_label") or page_type
    lines = ["# %s" % title, "",
             "> **本体类型**：%s（`%s`）  " % (label, page_type),
             "> **生成方式**：结构化批量建模（`%s`）—— **以来源文件为唯一事实源**"
             % (source.get("tag_full") or tag),
             "> **来源**：`%s`（sha256:%s）sheet=`%s` 第 %d 行"
             % (source.get("file"), str(source.get("sha256") or "")[:12], source.get("sheet"),
                int(row.get("__row__") or 0)),
             "", DEF_SECTION, "", _description_of(row, attrs, unknown), ""]
    if attrs:
        lines += [AUTH_SECTION, "", "| 属性 | 取值 |", "|---|---|"]
        for column, attr in attrs.items():
            lines.append("| %s | %s |" % (column, (row.get(column) or "").strip()))
        lines.append("")
    evidence = " | ".join(str(c or "") for c in (row.get("__raw__") or [])).strip(" |")
    lines += [EVIDENCE_SECTION, "", "> %s" % (evidence or "（空行）"), ""]
    rel_lines = []
    for column, spec in (enums or {}).items():
        raw = (row.get(column) or "").strip()
        value = (spec.get("values") or {}).get(raw)
        if not value:
            continue
        # 目标不是 wiki 页（枚举值/外部个体）→ `ke_pages.rel_line` 的**无链接**形态：
        # 巡检 A1 只解析 `[[slug|…]]`，所以这种行既表达关系，又不会产生悬空出边。
        rel_lines.append(ke_pages.rel_line(spec.get("label") or spec["relation"], spec["relation"],
                                           "%s（%s）" % (value, raw), ""))
    if rel_lines:
        lines += [REL_SECTION, ""] + rel_lines + [""]
    return "\n".join(lines)


def _merge_relation_section(prev: str, content: str) -> str:
    """类批次只维护「定义/属性/原文依据」；**已有**「## 本体关系」小节原样保留。

    为什么：`## 本体关系` 由**关系批次**（或人在页面上）维护——类批次重跑时若整页覆盖，
    会把 `bmm:isDerivedFrom → [[策略页]]` 这类行擦掉（实测踩到：规则页的关系线被清空 → 评审取不到规则）。
    本函数把旧小节的行与本批新生成的行**并集去重**后写回。
    """
    p_lines = (prev or "").splitlines()
    ps, pe = ke_pages._section_span(p_lines)
    if ps < 0:
        return content
    keep = [ln.rstrip() for ln in p_lines[ps + 1:pe] if ln.strip()]
    cur = (content or "").splitlines()
    cs, ce = ke_pages._section_span(cur)
    if cs >= 0:
        new = [ln.rstrip() for ln in cur[cs + 1:ce] if ln.strip()]
        body, rest = cur[:cs], cur[ce:]
    else:
        new, body, rest = [], cur, []
    merged = list(keep)
    for line in new:
        if line not in merged:
            merged.append(line)
    if not merged:
        return content
    while body and not body[-1].strip():
        body.pop()
    text = "\n".join(body + ["", REL_SECTION, ""] + merged + rest).rstrip("\n")
    return text + "\n"


def _page_row(kb: str, kb_name: str, page_type: str, label: str, slug: str, title: str, content: str,
              aliases: list, row: dict, source: dict, tag: str) -> dict:
    """一行 → 可插入的页字段（id 用规范派生 `bodhi-element:<kb>|<slug>`，巡检 D5 认它）。"""
    import uuid
    module = page_type.split(":")[0]
    attrs = source.get("attrs") or {}
    # `attributes` 用**本体数据属性名**为键（`bmm:ruleScope`）——机器口径（评审/巡检/智能体按本体名读）；
    # `attributes_by_column` 保留**中文列名**——人类口径（页详情页好看）。老数据只有中文键，读取方需兼容。
    by_column = {column: (row.get(column) or "").strip()
                 for column in attrs if (row.get(column) or "").strip()}
    attributes = {attrs.get(column, column): value for column, value in by_column.items()}
    # 枚举列 → 关系（`bmm:hasEnforcementLevel` → `bmm:Advisory`）：机器口径放元数据，正文另有一行（无链接）
    # 2A（2026-10-05）：`property` 形态的列 = **数据属性**（受控取值）→ 直接进 `attributes`（不再进 enum_relations）
    enum_rel = {}
    for column, spec in (source.get("enums") or {}).items():
        raw = (row.get(column) or "").strip()
        if spec.get("property"):
            value = (spec.get("values") or {}).get(raw) or raw
            if value:
                attributes[spec["property"]] = value
            continue
        value = (spec.get("values") or {}).get(raw)
        if value:
            enum_rel[spec.get("relation") or column] = value
    onto = {"model": module, "class": page_type, "label": label, "name": title,
            "attributes": attributes, "attributes_by_column": by_column,
            "generator": TAG, "created_at": ke_db.now_text()}
    if enum_rel:
        onto["enum_relations"] = enum_rel
    metadata = {"ontology": onto,
                "import": {"batch": source.get("batch"), "file": source.get("file"),
                           "file_sha256": source.get("sha256"), "sheet": source.get("sheet"),
                           "row": int(row.get("__row__") or 0), "key": source.get("key_value"),
                           "tag": source.get("tag_full")}}
    category_path = [kb_name or "批量建模", label]
    return {"id": str(uuid.uuid5(uuid.NAMESPACE_URL, "bodhi-element:%s|%s" % (kb, slug))),
            "slug": slug, "title": title, "page_type": page_type, "content": content,
            "summary": (attributes.get("bmm:description") or title)[:200],
            "aliases": aliases, "metadata": metadata,
            "category_path": category_path,
            # `wiki_path` 供**前端目录树显示**：口径与 `ke_pages.upsert_page` 一致（目录路径/标题），
            # 派生自 category_path；**不能**写 slug（否则目录里显示不出来 —— 2026-09-30 用户实测"目录缺失"）。
            "wiki_path": "/".join([str(x) for x in category_path] + [title]),
            "last_edit_source": tag, "out_links": ke_pages.out_links_of(content)}


PAGE_COLUMNS = ("id, tenant_id, knowledge_base_id, slug, title, page_type, status, content, summary, "
                "parent_slug, folder_id, category_path, wiki_path, depth, sort_order, source_refs, "
                "chunk_refs, in_links, out_links, page_metadata, aliases, version, last_edit_source, "
                "last_editor_id")


def _insert_stmt(kb: str, tenant_id: str, page: dict, actor: str) -> str:
    values = [ke_db.sql_str(page["id"]), str(int(tenant_id)), ke_db.sql_str(kb),
              ke_db.sql_str(page["slug"]), ke_db.sql_str(page["title"]), ke_db.sql_str(page["page_type"]),
              ke_db.sql_str("published"), ke_db.sql_str(page["content"]), ke_db.sql_str(page["summary"]),
              ke_db.sql_str(""), ke_db.sql_str(""), ke_db.sql_json(page["category_path"]),
              ke_db.sql_str(page["slug"]), str(len(page["category_path"])), "0",
              ke_db.sql_json([]), ke_db.sql_json([]), ke_db.sql_json([]),
              ke_db.sql_json(page["out_links"]), ke_db.sql_json(page["metadata"]),
              ke_db.sql_json(page["aliases"]), "1", ke_db.sql_str(page["last_edit_source"]),
              ke_db.sql_str(actor)]
    return ("INSERT INTO wiki_pages (%s) VALUES (%s) ON CONFLICT DO NOTHING;"
            % (PAGE_COLUMNS, ", ".join(values)))


def _find_entry(ticket: str) -> tuple[dict, str, dict]:
    """按 ticket 找账本条目（账本在 state/import/*.json）。"""
    if not STATE_DIR.is_dir():
        return {}, "", {}
    for path in sorted(STATE_DIR.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            continue
        for key, entry in (data.get("targets") or {}).items():
            if entry.get("ticket") == ticket:
                return data, key, entry
    return {}, "", {}


def _upsert_relation_lines(content: str, lines: list) -> tuple[str, int]:
    """把关系行并入「## 本体关系」小节（已存在的不重复加）；小节不存在就追加到文尾。"""
    text = content or ""
    existing = text.splitlines()
    start = next((i for i, ln in enumerate(existing) if ln.strip() == REL_SECTION), -1)
    added = [ln for ln in lines if ln not in existing]
    if not added:
        return text, 0
    if start < 0:
        block = "\n" + REL_SECTION + "\n\n" + "\n".join(lines) + "\n"
        return (text.rstrip() + block), len(added)
    end = next((i for i in range(start + 1, len(existing)) if existing[i].startswith("## ")), len(existing))
    insert_at = end
    while insert_at > start + 1 and not existing[insert_at - 1].strip():
        insert_at -= 1
    merged = existing[:insert_at] + added + [""] + existing[insert_at:]
    return "\n".join(merged), len(added)


def _extra_set(title: str, aliases: list, metadata: dict, page_type: str, summary: str,
               wiki_path: str = "", category_path: list | None = None) -> str:
    """更新已存在页时要一并覆盖的列（正文由 `_apply_content_update` 写）。

    `wiki_path`/`category_path` 也要覆盖：标题变了（如新增中文名称列）若不同步，
    目录树里会一直显示旧标题 —— 2026-09-30 用户实测"目录缺失/不一致"。
    """
    extra = (", title = %s, summary = %s, page_type = %s, aliases = %s::jsonb, page_metadata = %s::jsonb"
             % (ke_db.sql_str(title), ke_db.sql_str(summary), ke_db.sql_str(page_type),
                ke_db.sql_json(aliases), ke_db.sql_json(metadata)))
    if wiki_path:
        extra += ", wiki_path = %s" % ke_db.sql_str(wiki_path)
    if category_path is not None:
        extra += (", category_path = %s::jsonb, depth = %d"
                  % (ke_db.sql_json(category_path), len(category_path)))
    return extra


# ---------------------------------------------------------------------------
# apply：**只写这一个 target**（一个类 或 一条关系）
# ---------------------------------------------------------------------------
def apply(ticket: str, actor: str = "cli:import", tenant: int | None = None,
          prune: bool | None = None, limit: int = 0) -> dict:
    """按 ticket 落库（**一次只处理一个类或一条关系**；内部按 CHUNK 行分批提交）。"""
    import time
    data, key, entry = _find_entry(ticket)
    if not entry:
        return {"error": "ticket 未登记（先 import_plan）", "need_plan": True}
    kb, batch = data["kb_id"], data["batch"]
    acl = ke_db.assert_can_write(kb, tenant if tenant is not None else ke_db.caller_tenant())
    if not acl.get("allowed"):
        return {"error": "need_write_permission", "permission": acl,
                "hint": "import_apply 写目标知识库；需要对该库有写权限"}
    file = entry.get("file") or data["file"]
    sheet = data.get("sheet") or ""
    header, rows, sha = _load_sheet(file, "" if sheet == "(first)" else sheet, limit)
    if sha != data.get("sha256"):
        return {"error": "文件内容已变（sha256 不一致）→ need_replan", "need_replan": True}
    tenant_id = ke_db.psql_csv("SELECT tenant_id FROM knowledge_bases WHERE id = %s"
                               % ke_db.sql_str(kb))[0]["tenant_id"]
    tag = "bi-%s" % _sha1("%s|%s" % (batch, key))[:8]        # last_edit_source 是 varchar(16)
    tag_full = "%s:%s:%s" % (TAG, batch, key)                # 完整标识进 page_metadata.import.tag
    started = time.time()
    result: dict = {"ok": True, "batch": batch, "plan_key": key, "kind": entry["kind"],
                    "target": entry["target"], "kb_id": kb, "tag": tag, "tag_full": tag_full}

    if entry["kind"] == "class":
        page_type = entry["page_type"]
        label = entry.get("class_label") or page_type
        attrs = entry.get("attrs") or {}
        unknown = entry.get("unknown_columns") or []
        if not entry.get("unknown_to_description", True):
            unknown = []                       # 部门这类实体：不把整行其余列塞进 description
        key_column = entry["key_column"]
        entities: dict = {}
        for row in rows:
            value = (row.get(key_column) or "").strip()
            if value and value not in entities:
                entities[value] = row
        existing = {r["slug"]: r["content"] for r in ke_db.psql_csv(
            "SELECT slug, COALESCE(content,'') AS content FROM wiki_pages "
            " WHERE knowledge_base_id = %s AND deleted_at IS NULL AND slug LIKE %s"
            % (ke_db.sql_str(kb), ke_db.sql_str(class_slug_prefix(page_type) + "/%")))}
        created = updated = skipped = 0
        items = list(entities.items())
        for offset in range(0, len(items), CHUNK):
            stmts = []
            for value, row in items[offset:offset + CHUNK]:
                slug = page_slug(page_type, value)
                title = render_title(entry.get("title"), row, value, {"label": label})
                source = {**entry, "batch": batch, "key_value": value, "page_type": page_type,
                          "class_label": label, "attrs": attrs, "sha256": data.get("sha256"),
                          "file": data.get("file"), "sheet": sheet, "tag_full": tag_full}
                content = render_class_content(page_type, row, attrs, title, unknown, source, tag,
                                               enums=entry.get("enums"))
                content = _merge_relation_section(existing.get(slug) or "", content)
                aliases = [str(row.get(c) or "").strip() for c in (entry.get("aliases") or [])]
                aliases = [a for a in aliases if a]
                if slug in existing:
                    if existing[slug].strip() == content.strip():
                        skipped += 1
                        continue
                    meta = _page_row(kb, data.get("kb_name"), page_type, label, slug, title, content,
                                     aliases, row, source, tag)["metadata"]
                    cat = [data.get("kb_name") or "批量建模", label]
                    ke_pages._apply_content_update(
                        kb, slug, content, tag,
                        _extra_set(title, aliases, meta, page_type, title,
                                   wiki_path="/".join([str(x) for x in cat] + [title]),
                                   category_path=cat))
                    updated += 1
                    continue
                page = _page_row(kb, data.get("kb_name"), page_type, label, slug, title, content,
                                 aliases, row, source, tag)
                stmts.append(_insert_stmt(kb, tenant_id, page, actor))
            if stmts:
                ke_db.psql("BEGIN;\n" + "\n".join(stmts) + "\nCOMMIT;")
                created += len(stmts)
        pruned = 0
        if prune if prune is not None else entry.get("prune"):
            keep = [page_slug(page_type, v) for v in entities]
            victims = [r["slug"] for r in ke_db.psql_csv(
                "SELECT slug FROM wiki_pages WHERE knowledge_base_id = %s AND deleted_at IS NULL "
                " AND last_edit_source LIKE %s AND slug LIKE %s"
                % (ke_db.sql_str(kb), ke_db.sql_str(tag + "%"),
                   ke_db.sql_str(class_slug_prefix(page_type) + "/%")))]
            victims = [s for s in victims if s not in keep]
            if victims:
                ke_db.psql("UPDATE wiki_pages SET deleted_at = now(), updated_at = now() "
                           " WHERE knowledge_base_id = %s AND slug = ANY(ARRAY['%s']);"
                           % (ke_db.sql_str(kb), "','".join(victims)))
                pruned = len(victims)
        if created or updated or pruned:
            ke_db.psql(ke_pages.rebuild_in_links_sql(kb))
            # 目录树：`wiki_folders` + 页 `folder_id`（与 `ke_pages.upsert_page` 同口径；
            # 否则前端目录里看不到这批页 —— 2026-09-30 用户实测"目录缺失"）
            folders = ke_pages.sync_folders(kb)
        else:
            folders = {"ok": True, "skipped": "无写入，跳过目录同步"}
        result.update({"created": created, "updated": updated, "skipped": skipped, "pruned": pruned,
                       "entities": len(entities), "chunk_rows": CHUNK, "folders": folders,
                       "next": "继续下一个目标（另一个类或一条关系）；全跑完再 audit_scan 验收"})
    else:
        rel = entry["target"]
        rel_label = entry.get("relation_label") or rel
        sc, tc = entry["source_class"], entry["target_class"]
        s_col, t_col = entry["source_key_column"], entry["target_key_column"]
        per_source: dict = {}
        for row in rows:
            a = (row.get(s_col) or "").strip()
            b = (row.get(t_col) or "").strip()
            if a and b:
                per_source.setdefault(a, set()).add(b)
        titles = {r["slug"]: (r["title"] or "") for r in ke_db.psql_csv(
            "SELECT slug, COALESCE(title,'') AS title FROM wiki_pages "
            " WHERE knowledge_base_id = %s AND deleted_at IS NULL AND slug LIKE %s"
            % (ke_db.sql_str(kb), ke_db.sql_str(class_slug_prefix(tc) + "/%")))}
        pages_written = edges = dangling = 0
        updates = []
        for a, group in per_source.items():
            slug = page_slug(sc, a)
            cur = ke_db.psql_csv("SELECT COALESCE(content,'') AS content FROM wiki_pages "
                                 " WHERE knowledge_base_id = %s AND slug = %s AND deleted_at IS NULL"
                                 % (ke_db.sql_str(kb), ke_db.sql_str(slug)))
            if not cur:
                dangling += 1
                continue
            lines = []
            for b in sorted(group):
                t_slug = page_slug(tc, b)
                if t_slug not in titles:
                    dangling += 1
                    continue
                lines.append("- %s（`%s`）→ [[%s|%s]]" % (rel_label, rel, t_slug, titles[t_slug] or b))
            new_text, added = _upsert_relation_lines(cur[0]["content"], lines)
            if not added:
                continue
            updates.append((slug, new_text))
            edges += added
        # 批量写：每 CHUNK 个源页一个事务（快照 + version+1；比逐页 helper 快一个量级）
        for offset in range(0, len(updates), CHUNK):
            stmts = []
            for slug, new_text in updates[offset:offset + CHUNK]:
                stmts.append(ke_pages._snapshot_stmt(kb, slug, tag))
                stmts.append("UPDATE wiki_pages SET content = %s, out_links = %s::jsonb, "
                             "version = version + 1, updated_at = now(), last_edit_source = '%s' "
                             " WHERE knowledge_base_id = %s AND slug = %s AND deleted_at IS NULL;"
                             % (ke_db.sql_str(new_text), ke_db.sql_json(ke_pages.out_links_of(new_text)),
                                tag, ke_db.sql_str(kb), ke_db.sql_str(slug)))
            if stmts:
                ke_db.psql("BEGIN;\n" + "\n".join(stmts) + "\nCOMMIT;")
                pages_written += len(updates[offset:offset + CHUNK])
        if pages_written:
            ke_db.psql(ke_pages.rebuild_in_links_sql(kb))
        result.update({"pages_written": pages_written, "edges": edges, "dangling": dangling,
                       "pairs": sum(len(v) for v in per_source.values()),
                       "next": "import_state 看 remaining；为空即整个文件建完"})

    import time as _t
    result["duration_ms"] = int((_t.time() - started) * 1000)
    entry["status"] = "applied"
    entry["applied_at"] = ke_db.now_text()
    entry["result"] = {k: v for k, v in result.items()}
    data["targets"][key] = entry
    data["updated_at"] = ke_db.now_text()
    _ledger_save(data)
    result["ledger"] = str(_ledger_path(batch))
    return result


def state(batch: str = "") -> dict:
    """账本：不传 → 最近几批；传 batch → 每个目标的状态 + `remaining`（为空=全部建完）。"""
    if not STATE_DIR.is_dir():
        return {"batches": [], "note": "还没有任何批次"}
    if not batch:
        out = []
        for path in sorted(STATE_DIR.glob("*.json")):
            data = json.loads(path.read_text(encoding="utf-8"))
            targets = data.get("targets") or {}
            out.append({"batch": data.get("batch"), "kb_name": data.get("kb_name"),
                        "file": data.get("file"), "targets": len(targets),
                        "done": len([1 for e in targets.values() if e.get("status") == "applied"]),
                        "updated_at": data.get("updated_at")})
        return {"batches": out[-10:]}
    data = _ledger_load(batch)
    targets = data.get("targets") or {}
    remaining = [k for k, e in targets.items() if e.get("status") != "applied"]
    return {"batch": batch, "kb_id": data.get("kb_id"), "kb_name": data.get("kb_name"),
            "file": data.get("file"), "sha256": data.get("sha256"),
            "targets": {k: {"kind": v.get("kind"), "target": v.get("target"), "status": v.get("status"),
                            "counts": v.get("counts"), "result": v.get("result")}
                        for k, v in targets.items()},
            "remaining": remaining, "done": not remaining}


# ---------------------------------------------------------------------------
# CLI：probe / plan / apply / state（智能体走 MCP 工具，运维/脚本走这里）
# ---------------------------------------------------------------------------
def _dump(payload: dict) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def _label_lookup(page_type: str) -> dict:
    """本体 label（中文）→ 本体属性名（`适用范围` → `bmm:ruleScope`）。"""
    props = ke_ontology.data_properties_for(page_type) or {}
    lookup = {}
    for prefixed, info in props.items():
        for key in ((info or {}).get("label"), prefixed, str(prefixed).split(":")[-1]):
            if key:
                lookup[str(key).strip()] = prefixed
    return lookup


def _ledger_colmap(row: dict, cache: dict) -> dict:
    """该页**所属批次账本**里记的「列名 → 本体属性名」（最权威；账本没有才用本体 label 兜底）。"""
    try:
        meta = json.loads(row.get("meta") or "{}")
    except json.JSONDecodeError:
        return {}
    batch = ((meta.get("import") or {}).get("batch") or "").strip()
    if not batch:
        return {}
    if batch not in cache:
        try:
            cache[batch] = _ledger_load(batch)
        except Exception:  # noqa: BLE001
            cache[batch] = {}
    out = {}
    for entry in ((cache[batch] or {}).get("targets") or {}).values():
        if entry.get("kind") == "class" and entry.get("page_type") == row.get("page_type"):
            out.update({str(k): str(v) for k, v in (entry.get("attrs") or {}).items()})
    return out


def refresh_metadata(kb_id: str = "", dry_run: bool = False, limit: int = 5000,
                     tenant: int | None = None) -> dict:
    """把**结构化导入页**的元数据刷成当前口径（`attributes` 键=本体属性名 + `attributes_by_column`=中文列名）。

    **只升级、不降级**：
    - 已是本体键（含 `:`）的条目**原样保留**；
    - 中文列名的条目按「账本该批次 mapping（最权威）→ 本体 label 反查」升级；
    - 两条都查不到就保留原键（不猜）。
    只改 `page_metadata`（正文/标题/slug/version 都不动）；**幂等**：已合规的页零写入。
    """
    kb, kb_name, _note = ke_db.resolve_kb_id(kb_id)
    acl = ke_db.assert_can_write(kb, tenant if tenant is not None else ke_db.caller_tenant())
    if not acl.get("allowed"):
        return {"ok": False, "error": "need_write_permission", "permission": acl}
    rows = ke_db.psql_csv(
        "SELECT slug, COALESCE(page_type,'') AS page_type, COALESCE(title,'') AS title, "
        "       COALESCE(page_metadata::text,'{}') AS meta, COALESCE(wiki_path,'') AS wiki_path, "
        "       COALESCE(category_path::text,'[]') AS cat, COALESCE(depth,0) AS depth "
        "  FROM wiki_pages WHERE knowledge_base_id = %s AND deleted_at IS NULL "
        "   AND page_metadata->'import'->>'file_sha256' IS NOT NULL "
        " ORDER BY slug LIMIT %d" % (ke_db.sql_str(kb), int(limit or 5000)))
    changed, samples, skipped, failed, paths_fixed = [], [], 0, [], 0
    cache: dict = {}
    for row in rows:
        try:
            meta = json.loads(row["meta"] or "{}")
        except json.JSONDecodeError:
            failed.append({"slug": row["slug"], "why": "page_metadata 不是合法 JSON"})
            continue
        onto = meta.get("ontology") or {}
        attrs = onto.get("attributes") or {}
        if not attrs:
            skipped += 1
            continue
        colmap = dict(_label_lookup(row["page_type"]))
        colmap.update(_ledger_colmap(row, cache))            # 账本优先
        want = {}
        for key, value in attrs.items():
            text = str(key)
            want[text if ":" in text else colmap.get(text, text)] = value
        by_col = onto.get("attributes_by_column") or {k: v for k, v in attrs.items() if ":" not in str(k)}
        # 目录路径（`wiki_path` 供前端目录树显示）：期望值 = 目录路径/标题；顺带补 category_path/depth
        try:
            cat = json.loads(row["cat"] or "[]")
        except json.JSONDecodeError:
            cat = []
        if not cat:
            cat = [kb_name or "批量建模", onto.get("label") or row["page_type"]]
        want_wp = "/".join([str(x) for x in cat] + [row["title"]])
        path_ok = (str(row["wiki_path"]) == want_wp) and bool(cat)
        if attrs == want and (onto.get("attributes_by_column") or {}) == by_col and by_col and path_ok:
            skipped += 1
            continue
        if dry_run:
            samples.append({"slug": row["slug"], "page_type": row["page_type"],
                            "before_keys": sorted(attrs)[:8], "after_keys": sorted(want)[:8],
                            "wiki_path_before": row["wiki_path"], "wiki_path_after": want_wp})
            continue
        onto2 = dict(onto)
        onto2["attributes"] = want
        onto2["attributes_by_column"] = by_col
        meta2 = dict(meta)
        meta2["ontology"] = onto2
        if not path_ok:
            paths_fixed += 1
        ke_db.psql("UPDATE wiki_pages SET page_metadata = %s::jsonb, wiki_path = %s, "
                   " category_path = %s::jsonb, depth = %d, updated_at = now(), last_edit_source = %s "
                   " WHERE knowledge_base_id = %s AND slug = %s AND deleted_at IS NULL;"
                   % (ke_db.sql_json(meta2), ke_db.sql_str(want_wp), ke_db.sql_json(cat), len(cat),
                      ke_db.sql_str("bodhi-import"), ke_db.sql_str(kb), ke_db.sql_str(row["slug"])))
        changed.append(row["slug"])
    folders = ke_pages.sync_folders(kb) if changed else {"ok": True, "skipped": "无写入，跳过目录同步"}
    return {"ok": True, "kb": {"id": kb, "name": kb_name}, "permission": acl.get("mode"),
            "scanned": len(rows), "changed": len(changed), "skipped": skipped, "failed": failed,
            "paths_fixed": paths_fixed, "folders": folders,
            "samples": samples[:5], "slugs": changed[:50], "dry_run": bool(dry_run),
            "note": ("元数据：只升级不降级（本体键原样保留；中文列名按「账本 mapping → 本体 label」升级）；"
                     "目录：`wiki_path` 对齐成「目录路径/标题」并重建 `wiki_folders`（前端目录树显示用）；"
                     "只改这些列（正文/version 不动）")}


def main() -> int:
    import argparse
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except Exception:  # noqa: BLE001
                pass
    ap = argparse.ArgumentParser(description="结构化数据批量建模（一次一个类或一条关系）")
    sub = ap.add_subparsers(dest="cmd", required=True)
    q = sub.add_parser("probe", help="只读：结构 / 表头 / 抽样 / 重复表头 / 列前缀")
    q.add_argument("file")
    q.add_argument("--sheet", default="")
    q.add_argument("--sample", type=int, default=3)
    q = sub.add_parser("plan", help="只读：校验 + 影响面 + ticket")
    q.add_argument("--kind", required=True, choices=("class", "relation"))
    q.add_argument("--target", required=True, help="类（bmm:MainSystem）或关系（bmm:mainSystemContainsSubSystem）")
    q.add_argument("--file", required=True)
    q.add_argument("--kb", required=True)
    q.add_argument("--sheet", default="")
    q.add_argument("--key-column", default="")
    q.add_argument("--map", default="", help='列=属性,列=属性（属性可写 systemNo 或 bmm:systemNo）')
    q.add_argument("--title", default="{key}")
    q.add_argument("--aliases", default="", help="逗号分隔的列名（进 aliases，便于检索）")
    q.add_argument("--source-key-column", default="")
    q.add_argument("--target-key-column", default="")
    q.add_argument("--source-class", default="")
    q.add_argument("--target-class", default="")
    q.add_argument("--batch", default="")
    q.add_argument("--prune", action="store_true")
    q.add_argument("--limit", type=int, default=0)
    q.add_argument("--no-unknown-to-description", action="store_true",
                   help="未映射列不聚合进 description（适合部门/组织机构这类只有名称的类）")
    q.add_argument("--enums", default="",
                   help='JSON：把枚举列建成关系，如 {"级别":{"relation":"bmm:hasEnforcementLevel",'
                        '"values":{"强制":"bmm:Strict","推荐":"bmm:Advisory"}}}')
    q = sub.add_parser("apply", help="写：按 ticket 落库（只写这一个目标）")
    q.add_argument("--ticket", required=True)
    q.add_argument("--actor", default="cli:import")
    q.add_argument("--prune", action="store_true")
    q = sub.add_parser("state", help="账本：目标状态 + remaining")
    q.add_argument("--batch", default="")
    q = sub.add_parser("refresh-metadata", help="写：把导入页元数据刷成当前口径（只改 page_metadata）")
    q.add_argument("--kb", required=True)
    q.add_argument("--dry-run", action="store_true")
    q.add_argument("--limit", type=int, default=5000)
    args = ap.parse_args()

    if args.cmd == "probe":
        _dump(ke_sheet.probe(args.file, args.sheet, args.sample))
        return 0
    if args.cmd == "plan":
        mapping = {}
        for pair in args.map.split(","):
            if "=" in pair:
                column, _, attr = pair.partition("=")
                mapping[column.strip()] = attr.strip()
        out = plan(kind=args.kind, target=args.target, file=args.file, kb_id=args.kb, sheet=args.sheet,
                   mapping=mapping, key_column=args.key_column, title=args.title,
                   aliases=[a.strip() for a in args.aliases.split(",") if a.strip()],
                   source_key_column=args.source_key_column, target_key_column=args.target_key_column,
                   source_class=args.source_class, target_class=args.target_class,
                   prune=args.prune, batch_id=args.batch, limit=args.limit,
                   unknown_to_description=not args.no_unknown_to_description,
                   enums=(json.loads(args.enums) if args.enums.strip() else None))
        _dump(out)
        return 0 if out.get("ok") or out.get("error") is None else 1
    if args.cmd == "apply":
        out = apply(args.ticket, actor=args.actor, prune=True if args.prune else None)
        _dump(out)
        return 0 if out.get("ok") else 1
    if args.cmd == "refresh-metadata":
        out = refresh_metadata(args.kb, args.dry_run, args.limit)
        _dump(out)
        return 0 if out.get("ok") else 1
    _dump(state(args.batch))
    return 0


def _ledger_save(data: dict) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    _ledger_path(data["batch"]).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    sys.exit(main())






def _ledger_save(data: dict) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    _ledger_path(data["batch"]).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

