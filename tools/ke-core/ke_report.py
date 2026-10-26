"""报告模板（**模板驱动**）—— 报告章节口径**只来自模板页正文**，代码里不写死章节/列 ✗

约定（2026-10-23 用户裁定）：
- 模板 = `schema:DocumentTemplate` 实例页；按 `schema:applicableReportType`（= 报告类名）自动选，
  多候选时看 `schema:templateSelector`，仍多 ⇒ `ambiguous`（**不静默选** ✗）；
- 模板正文含「章节结构」表（`| 序号 | 章节 | 必填 | 要求 |`）⇒ 解析成 `[{title, required, requirement}]`；
- 报告校验 = 按模板**必填**章节比对 `##` 标题（**只告警、不拒写** ✓）；
- 没有模板 ⇒ **不猜**（不内置任何章节），校验返回空，由调用方给 `template_fallback_reason`；
- 旧「固定五节 + 固定列」渲染器（`columns` / `SECTION_RULES` / `render` / `write`）已删除 ✗
  —— 章节/列一律来自库内页（模板页 / DataObject 页 `skos:definition`）。
"""

import json
import re


def _plain(text: str) -> str:
    """去编号/装饰后的纯文本（`① 范围与依据` → `范围与依据`），用于章节标题比对。"""
    s = re.sub(r"[#*`\s]+", "", text or "")
    s = re.sub(r"^[①②③④⑤⑥⑦⑧⑨⑩\d]+[、.．)）:：]?", "", s)
    return s


def parse_template_sections(body: str) -> list:
    """从**模板正文**解析章节表 → `[{title, required, requirement, fields}]`。

    约定（模板页正文）：形如
    ```
    ## 章节结构
    | 序号 | 章节 | 必填 | 要求 |
    |---|---|---|---|
    | 1 | ① 范围与依据 | 是 | … |
    ```
    找不到表时**回退**为「取 `##` 标题」（排除模板自身的元小节），且全部标 `required=False`
    （不猜必填，宁缺勿错）。

    **可选第 5 列「数据对象字段」**（2026-10-25 用户口径）：该小节要展示/编辑哪几列，
    逗号分隔、**顺序即展示顺序** —— 工具字段可以多于某份报告所需，取哪些由模板定 ✓；
    没这一列 ⇒ `fields=[]`（= 用工具全量契约，不猜 ✗）。
    """
    lines = (body or "").splitlines()
    tbl: list = []
    for ln in lines:
        s = ln.strip()
        if s.startswith("|"):
            tbl.append(s)
        elif tbl and not s:
            break
    if len(tbl) >= 3:
        def _cells(row: str) -> list:
            return [c.strip() for c in row.strip().strip("|").split("|")]

        head = _cells(tbl[0])
        idx_title = next((i for i, c in enumerate(head) if "章节" in c), 1)
        idx_req = next((i for i, c in enumerate(head) if "必填" in c), -1)
        idx_req2 = next((i for i, c in enumerate(head) if "要求" in c or "说明" in c), -1)
        idx_fields = next((i for i, c in enumerate(head) if "字段" in c), -1)
        out = []
        for row in tbl[1:]:
            if set(row.replace("|", "").strip()) <= set("-: "):
                continue
            cells = _cells(row)
            if len(cells) <= idx_title:
                continue
            title = cells[idx_title]
            if not title:
                continue
            req = bool(idx_req >= 0 and len(cells) > idx_req
                       and cells[idx_req] in ("是", "必填", "Y", "y", "true"))
            req_txt = cells[idx_req2] if 0 <= idx_req2 < len(cells) else ""
            raw_fields = cells[idx_fields] if 0 <= idx_fields < len(cells) else ""
            fields = [f.strip() for f in re.split(r"[,，]", raw_fields) if f.strip()
                      and f.strip() not in ("—", "-", "无")]
            out.append({"title": title, "required": req, "requirement": req_txt,
                        "fields": fields})
        if out:
            return out
    # 回退：按 `##` 标题（排除模板元小节）；不猜必填 ✓
    skip = ("章节结构", "使用约束", "模板信息", "属性")
    return [{"title": ln[3:].strip(), "required": False, "requirement": "", "fields": []}
            for ln in lines
            if ln.startswith("## ") and not any(k in ln[3:] for k in skip)]


def section_fields(sections: list, title: str) -> list:
    """按章节名（忽略编号/装饰）取该节的字段子集（找不到 ⇒ 空表，不猜 ✗）。"""
    want = _plain(title)
    for sec in sections or []:
        if _plain(sec.get("title") or "") == want:
            return list(sec.get("fields") or [])
    return []


def check_report_md(md: str, sections: list | None = None) -> list:
    """报告**只读校验**（Q2=C ✓：只告警，不静默改）。

    `sections` = 模板章节表（`parse_template_sections` 的结果）；为空 ⇒ **不判**（返回 `[]`）——
    代码里**不再内置固定章节** ✗（旧五节规则随模板化退场，2026-10-23）。
    """
    if not sections:
        return []
    return _check_by_template(md, sections)


def _check_by_template(md: str, sections: list) -> list:
    """按**模板章节表**校验：必填章节缺失 ⇒ `sections_missing`（**只告警** ✓，不静默改）。"""
    heads = [_plain(ln[3:]) for ln in (md or "").splitlines() if ln.startswith("## ")]
    warns: list = []
    for sec in sections or []:
        if not sec.get("required"):
            continue
        want = _plain(sec.get("title") or "")
        if not want:
            continue
        if not any(want in h or (h and h in want) for h in heads):
            warns.append({"code": "sections_missing", "section": sec.get("title"),
                          "required": True})
    return warns


def pick_template(items: list, report_type: str = "", template_slug: str = "") -> dict:
    """**纯函数**：从候选模板（`[{slug,title,version,content,types,selector}]`）里选一个。

    DB 路径（`resolve_template`）与巡检（已载入全库页）**共用**这段判定，避免两处口径不一 ✗。
    """
    if not items:
        return {"found": False, "candidates": [], "ambiguous": False, "sections": [],
                "reason": "本库没有 `schema:DocumentTemplate` 模板页"}
    picked = []
    for it in items:
        if template_slug and it["slug"] != template_slug:
            continue
        if report_type and not template_slug and report_type not in (it.get("types") or []):
            continue
        picked.append(it)
    if not picked:
        return {"found": False, "candidates": [], "ambiguous": False, "sections": [],
                "reason": ("指定的模板不存在：%s" % template_slug if template_slug
                           else "没有模板声明适用于 `%s`（`applicableReportType`）" % report_type)}
    ambiguous = False
    if len(picked) > 1:
        with_sel = [p for p in picked if p.get("selector")]
        if len(with_sel) == 1:
            picked = with_sel
        else:
            ambiguous = True
    best = sorted(picked, key=lambda p: (-int(p.get("version") or 1), p["slug"]))[0]
    return {"found": True, "slug": best["slug"], "title": best.get("title", ""),
            "version": int(best.get("version") or 1), "selector": best.get("selector") or "",
            "sections": parse_template_sections(best.get("content") or ""),
            "candidates": [{"slug": p["slug"], "title": p.get("title", "")} for p in picked],
            "ambiguous": ambiguous, "reason": ""}


def template_items_from_pages(pages: list) -> list:
    """把**已载入的页**（巡检用，避免 N 次查库）转成 `pick_template` 的候选结构。"""
    out = []
    for p in pages or []:
        if (p.get("page_type") or "") != "schema:DocumentTemplate":
            continue
        attrs: dict = {}
        raw = p.get("attrs")             # ① 只取属性的列（推荐 ✓，不解析整块 meta）
        if raw is not None:
            try:
                attrs = raw if isinstance(raw, dict) else (json.loads(raw or "{}") or {})
            except Exception:  # noqa: BLE001
                attrs = {}
        if not attrs:                    # ② 回落：整块 page_metadata → ontology.attributes
            meta_raw = p.get("meta")
            try:
                meta = meta_raw if isinstance(meta_raw, dict) else (json.loads(meta_raw or "{}") or {})
            except Exception:  # noqa: BLE001
                meta = {}
            onto = meta.get("ontology") if isinstance(meta, dict) else None
            attrs = (onto.get("attributes") if isinstance(onto, dict) else None) or {}
        types = attrs.get("schema:applicableReportType") or []
        if isinstance(types, str):
            types = [x for x in re.split(r"[,，、;\s]+", types) if x]
        out.append({"slug": p.get("slug"), "title": p.get("title", ""),
                    "version": int(p.get("version") or 1), "content": p.get("content", ""),
                    "types": [str(t) for t in types],
                    "selector": str(attrs.get("schema:templateSelector") or "")})
    return out


def _template_rows(kb_id: str = "") -> list:
    """读模板页（`kb_id` 空 ⇒ **全库**）。"""
    import ke_db  # noqa: PLC0415

    where = ("knowledge_base_id = %s AND " % ke_db.sql_str(kb_id)) if kb_id else ""
    return ke_db.psql_csv(
        "SELECT slug, COALESCE(title,'') AS title, COALESCE(version,1) AS version, "
        "       COALESCE(content,'') AS content, COALESCE(page_metadata::text,'{}') AS meta, "
        "       COALESCE(page_metadata->'ontology'->'attributes','{}'::jsonb)::text AS attrs, "
        "       'schema:DocumentTemplate' AS page_type "
        "  FROM wiki_pages WHERE %sdeleted_at IS NULL "
        "   AND page_type = 'schema:DocumentTemplate'" % where)


def resolve_template(kb_id: str, report_type: str = "", template_slug: str = "") -> dict:
    """按**本体属性**找模板页（不猜 ✗）：`schema:DocumentTemplate` + `applicableReportType`。

    - 给了 `template_slug` ⇒ 直接取（校验存在）；
    - 否则按 `applicableReportType`（值=报告类名，支持多值）匹配；
    - **本库找不到 ⇒ 跨库兜底**（2026-10-25 用户口径：报告在**工作库**、模板在**领域库**，
      不跨库会"静默跳过校验"✗）；跨库命中回执带 `template_scope=global` + `local_reason`（透明 ✓）；
    - 本库自带多候选 ⇒ **不跨库**、也不静默选 ⇒ `ambiguous=true` + `candidates`；
    - 都找不到 ⇒ `found=false` + `reason`（调用方据此回传原因）。
    """
    got = pick_template(template_items_from_pages(_template_rows(kb_id)), report_type, template_slug)
    if got.get("found") or template_slug:
        return got
    local_reason = got.get("reason") or ""
    got2 = pick_template(template_items_from_pages(_template_rows()), report_type, "")
    if got2.get("found"):
        got2["template_scope"] = "global"
        got2["local_reason"] = local_reason
    else:
        got2["reason"] = local_reason or got2.get("reason") or ""
    return got2


