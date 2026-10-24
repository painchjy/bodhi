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
    """从**模板正文**解析章节表 → `[{title, required, requirement}]`。

    约定（模板页正文）：形如
    ```
    ## 章节结构
    | 序号 | 章节 | 必填 | 要求 |
    |---|---|---|---|
    | 1 | ① 范围与依据 | 是 | … |
    ```
    找不到表时**回退**为「取 `##` 标题」（排除模板自身的元小节），且全部标 `required=False`
    （不猜必填，宁缺勿错）。
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
            out.append({"title": title, "required": req, "requirement": req_txt})
        if out:
            return out
    # 回退：按 `##` 标题（排除模板元小节）；不猜必填 ✓
    skip = ("章节结构", "使用约束", "模板信息", "属性")
    return [{"title": ln[3:].strip(), "required": False, "requirement": ""}
            for ln in lines
            if ln.startswith("## ") and not any(k in ln[3:] for k in skip)]


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


def resolve_template(kb_id: str, report_type: str = "", template_slug: str = "") -> dict:
    """按**本体属性**找模板页（不猜 ✗）：`schema:DocumentTemplate` + `applicableReportType`。

    - 给了 `template_slug` ⇒ 直接取（校验存在且属本库）；
    - 否则按 `applicableReportType`（值=报告类名，支持多值）匹配；
    - 多条候选且仅一条带 `templateSelector` ⇒ 取它；仍多 ⇒ `ambiguous=true` + `candidates`
      （**不静默选**，交由调用方/用户定夺）；
    - 找不到 ⇒ `found=false` + `reason`（调用方据此回落内置结构并回传原因）。

    返回 `{found, slug, title, version, selector, sections[], candidates[], ambiguous, reason}`。
    """
    import ke_db  # noqa: PLC0415

    rows = ke_db.psql_csv(
        "SELECT slug, COALESCE(title,'') AS title, COALESCE(version,1) AS version, "
        "       COALESCE(content,'') AS content, COALESCE(page_metadata::text,'{}') AS meta, "
        "       COALESCE(page_metadata->'ontology'->'attributes','{}'::jsonb)::text AS attrs, "
        "       'schema:DocumentTemplate' AS page_type "
        "  FROM wiki_pages WHERE knowledge_base_id = %s AND deleted_at IS NULL "
        "   AND page_type = 'schema:DocumentTemplate'" % ke_db.sql_str(kb_id))
    return pick_template(template_items_from_pages(rows), report_type, template_slug)


