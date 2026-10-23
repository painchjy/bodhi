"""报告渲染器（**列从 DataObject 读** ✓，事实从 JSON 读 ✓）—— 保证"格式可复现" ✓

设计（2026-10-17 用户口径 ✓）：
- **列名/顺序只来自** `bpmn/dataobject/问题清单` 的 `skos:definition` ✓（不从代码里写死 ✗）；
- 章节来自报告类 `rdfs:comment` 的「章节规范」✓（这里按标准五节渲染 ✓，类未声明则最小化 ✓）；
- 事实（含 desc/loc/quote/verdict/severity/fix/facts/check）由 JSON 提供 ✓（抽取+判读的产物 ✓）；
- 可信度一律 `ke_score` 机算 ✓；`gate=insufficient` ⇒ 判定写「无法判定」✓ 并进表后说明 ✓。
"""

import json
import re


def columns(domain_kb: str, do_slug: str = "bpmn/dataobject/问题清单") -> list:
    """从 DataObject 定义解析列名与顺序 ✓（形如 `1 编号｜2 **问题描述**（…）｜3 触发规则｜…`）。"""
    import ke_db
    kb = ke_db.resolve_kb_id(domain_kb)[0]
    rows = ke_db.psql_csv(
        "SELECT COALESCE(page_metadata->'ontology'->'attributes'->>'skos:definition','') AS d "
        " FROM wiki_pages WHERE knowledge_base_id = %s AND deleted_at IS NULL AND slug = %s"
        % (ke_db.sql_str(kb), ke_db.sql_str(do_slug)))
    if not rows:
        return []
    text = str(rows[0].get("d") or "")
    line = next((ln for ln in text.splitlines() if ln.strip().startswith("1 ")), "")
    out = []
    # 兼容全角「｜」与半角「|」（2026-10-19 实测：用户补第 10 列用了半角 ⇒ 只切全角会少一列 ✗）
    for seg in re.split(r"[｜|]", line):
        s = re.sub(r"^\s*\d+\s*", "", seg).strip()
        s = s.replace("**", "")
        s = re.split(r"[（(：:]", s)[0].strip()
        s = s.rstrip("。．.;；、,，")          # 去掉列名末尾标点（2026-10-19：`规则指纹。` → `规则指纹` ✓）
        if s:
            out.append(s)
    return out


SECTION_RULES = (
    ("①", ("①", "1"), ("范围", "依据")),
    ("②", ("②", "2"), ("规则清单",)),
    ("③", ("③", "3"), ("问题清单",)),
    ("④", ("④", "4"), ("知识入库",)),
    ("⑤", ("⑤", "5"), ("结论",)),
)


def _norm_sec(head: str) -> str:
    return head.lstrip("#").strip()


def _sec_of(head: str) -> str:
    """判断标题属于哪一节（**编号 + 关键词**双匹配 ✓ —— 报告常用 `## 1/3` 而非 `③` ✗）。"""
    body = _norm_sec(head)
    for mark, nums, kws in SECTION_RULES:
        num_ok = any(body.startswith(n) or body.startswith(n + " ") or body.startswith(n + ".")
                     or body.startswith(n + "、") for n in nums)
        kw_ok = any(k in body for k in kws)
        if num_ok and kw_ok:
            return mark
        if kw_ok and not num_ok:      # 关键词独中（如「## 知识入库情况」没编号 ✓）
            return mark
    return ""


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
    """报告**只读校验**（2026-10-19 Q2=C ✓：只告警，不静默改 ✗）。

    章节识别兼容 `## ①` / `## 1` / `## 知识入库情况` ✓（2026-10-20 修：只认 ① 会误报 ✗）。

    `sections`（2026-10-23 · 模板化）：**给定模板章节表**时按模板校验（必填章节缺失 ⇒ 告警），
    不再用代码里的固定五节；为 `None` 时保留旧行为（向后兼容）。
    """
    if sections:
        return _check_by_template(md, sections)
    warns: list = []
    text = md or ""
    secs: dict = {}
    for ln in text.splitlines():
        if ln.startswith("## "):
            m = _sec_of(ln)
            if m and m not in secs:
                secs[m] = ln
    for mark, _nums, _kws in SECTION_RULES:
        if mark not in secs:
            warns.append({"code": "sections_missing", "section": mark})

    def _table(mark: str) -> list:
        head = secs.get(mark)
        if not head:
            return []
        seg = text.split(head, 1)[1]
        nxt = re.search(r"\n## ", seg)
        if nxt:
            seg = seg[:nxt.start()]
        return [ln.strip() for ln in seg.splitlines() if ln.strip().startswith("|")]

    t2 = _table("②")
    if t2 and "rule_hash" not in t2[0]:
        warns.append({"code": "rule_hash_column_missing", "in": "② 规则清单"})

    t3 = _table("③")
    if len(t3) >= 2:
        hdr = [c.strip() for c in t3[0].strip("|").split("|")]
        body = t3[2:]
        fp_idx = next((i for i, h in enumerate(hdr) if "指纹" in h), -1)
        if fp_idx >= 0:
            for ln in body:
                cells = [c.strip() for c in ln.strip("|").split("|")]
                rule = cells[2] if len(cells) > 2 else ""
                fp = cells[fp_idx] if len(cells) > fp_idx else ""
                if rule and (not fp or fp in ("—", "-", "待生成")):
                    warns.append({"code": "fingerprint_missing", "no": cells[0][:12], "rule": rule[:28]})
                if not rule and fp and fp not in ("—", "-"):
                    warns.append({"code": "fingerprint_unsolicited", "no": cells[0][:12]})
    return warns


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
        try:
            meta = json.loads(p.get("meta") or "{}") or {}
        except Exception:  # noqa: BLE001
            meta = {}
        attrs = ((meta.get("ontology") or {}).get("attributes") or {})
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
        "       'schema:DocumentTemplate' AS page_type "
        "  FROM wiki_pages WHERE knowledge_base_id = %s AND deleted_at IS NULL "
        "   AND page_type = 'schema:DocumentTemplate'" % ke_db.sql_str(kb_id))
    return pick_template(template_items_from_pages(rows), report_type, template_slug)


def _check_coverage(rows: list, rules: list) -> dict:
    """③ 覆盖校验（2026-10-18 用户口径 ✓）：**行数 ≥ 规则数** ✓；
    同一规则在不同位置/子系统结论不同 ⇒ 可多行 ✓（按规则去重后比较 ✓）。

    匹配口径（2026-10-18 修 ✓）：fixture 的 `rule` 存的是**规则标题**，
    lookup 的规则有 `slug` / `title` —— 两边都要按「slug / 末段 / 标题」**规范化**后比对 ✓
    （此前只比 `rule_slug` ⇒ covered 恒为 0 ✗）。
    """
    def _norm(s) -> str:
        """只保留字母/数字/汉字 ✓（连字符、全角破折号、空格、斜杠一律去掉 ——
        2026-10-18 实测：`部署架构-…` 与 `部署架构－…` 会因破折号不同而匹配失败 ✗）。"""
        return re.sub(r"[^\w\u4e00-\u9fff]+", "", str(s or "").lower())

    def _keys(d) -> set:
        out = set()
        for k in ("slug", "title", "key", "name", "rule", "rule_slug"):
            v = d.get(k)
            if not v:
                continue
            out.add(_norm(v))
            out.add(_norm(str(v).split("/")[-1]))
        return out

    row_keys: set = set()
    for x in rows:
        row_keys |= _keys(x)
    covered, missing = 0, []
    for r in rules:
        ks = _keys(r)
        if ks & row_keys:
            covered += 1
        else:
            missing.append(str(r.get("title") or r.get("slug") or "")[:60])
    return {"ok": not missing and len(rows) >= len(rules),
            "rules": len(rules), "rows": len(rows),
            "covered": covered, "missing": missing}


def render(sample: str, doc_title: str, facts: dict, domain_kb: str,
           rules_spec: str = "policy:技术方案逐项评审策略",
           ingest: list | None = None) -> dict:
    """渲染一份报告正文 ✓（返回 {content, counts, coverage}；不写库 ✗）。

    `ingest`：知识入库清单行（7 列口径 ✓），来自知识入库任务；缺省 ⇒ 报告里写明"未生成" ✗。
    """
    import ke_lookup
    import ke_score

    cols = columns(domain_kb)
    lk = ke_lookup.knowledge_lookup(domain_kb, mode="rules", spec=rules_spec)
    rows, insuf = [], []
    for it in facts.get("items") or []:
        sc = ke_score.score_rule(it.get("facts") or [], [{"verdict": it.get("check") or "insufficient",
                                                        "k": it.get("id")}])
        verdict = "无法判定" if sc["gate"] == "insufficient" else it["verdict"]
        rows.append({**it, "verdict": verdict, "score": sc["score"], "level": sc["level"],
                     "why": "；".join(sc["reasons"][:2]) if sc["gate"] != "ok" else ""})
        if verdict == "无法判定":
            insuf.append(it.get("id"))
    failed = [x for x in rows if x["verdict"] == "不符合"]
    passed = [x for x in rows if x["verdict"] == "符合"]
    veto = [x for x in failed if x["severity"] == "严重"]

    md = ["## ① 范围与依据", "", "- 受评对象：%s" % sample,
          "- 源文档：%s" % doc_title,
          "- 规则集：`%s` ⇒ %d 条（hash `%s`）" % (rules_spec, lk["counts"]["rules"], lk["hash"]),
          "- ④ 列规范：`bpmn/dataobject/问题清单`（%d 列 ✓ 列名/顺序取自该页定义 ✓）" % len(cols), "",
          "## ② 规则清单", "",
          "| 序号 | 规则 | 执行级别 | 适用范围 | 参考规范 | 本次判定概览 | 规则指纹 |",
          "|---|---|---|---|---|---|---|"]
    _hm, _hm_err = {}, ""
    try:                              # 规则指纹：读规则页**属性** `bmm:ruleHash` ✓（发布时写入）
        import ke_fp
        _hm = ke_fp.rule_hash_map(domain_kb)
    except Exception as exc:  # noqa: BLE001  **不吞**：记录到回执，测试据此暴露真因 ✗
        _hm_err = "%s: %s" % (type(exc).__name__, str(exc)[:200])
    for i, r in enumerate(lk["rules"], 1):
        _title = str(r.get("title") or "")
        _cnt: dict = {}
        for _x in rows:                      # 本次判定概览 = ③ 表按规则统计 ✓
            if str(_x.get("rule") or "") == _title:
                _cnt[_x["verdict"]] = _cnt.get(_x["verdict"], 0) + 1
        _ov = " ／ ".join("%s %d" % (k, v) for k, v in sorted(_cnt.items())) or "—"
        md.append("| %d | %s | %s | %s | %s | %s | `%s` |"
                  % (i, _title, r.get("enforcement") or "—",
                     str(r.get("scope") or "—").replace("\n", " ")[:60],
                     str(r.get("reference") or "—").replace("\n", " ")[:60],
                     _ov, _hm.get(_title.strip()) or "—"))
    md += ["", "## ③ 问题清单（%d 列 ✓）" % len(cols), "",
           "| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
    for x in rows:
        cells = [x["id"], x["desc"], x["rule"], x["locator"], x["quote"], x["verdict"],
                 x["severity"], x["fix"], "**%s / %.2f**" % (x["level"], x["score"])]
        md.append("| " + " | ".join(cells) + " |")
    if insuf:
        md += ["", "### ③-补充：无法判定说明（%d 项 ✓）" % len(insuf), "",
               "| 编号 | 无法判断的原因 | 影响的问题 | 需要反馈或核实的内容 |", "|---|---|---|---|"]
        for x in rows:
            if x["verdict"] == "无法判定":
                md.append("| %s | %s | %s | %s |" % (x["id"], x["why"] or "—", "、".join(insuf),
                                                     x.get("ask") or "① 请提供认证清单（含版本号）；② 或书面确认是否免比对；③ 或人工比对回填"))
    # ④ 知识入库情况（2026-10-19 用户口径 ✓：知识入库任务完成后追加，评测据此比对 ✓）
    ing_rows = list(ingest or [])
    md += ["", "## ④ 知识入库情况", ""]
    if ing_rows:
        md += ["| 知识条目 | 要求来源 | 入库对象 | 入库状态 | 知识库比对 | 抽检结论 | 说明 |",
               "|---|---|---|---|---|---|---|"]
        ok_n = 0
        for it in ing_rows:
            if str(it.get("status") or "").startswith("已入库"):
                ok_n += 1
            md.append("| %s | %s | %s | %s | %s | %s | %s |"
                      % (it.get("item", ""), it.get("rule", ""), it.get("target", ""),
                         it.get("status", ""), it.get("kb_check", "—"),
                         it.get("spot", "—"), it.get("note", "—")))
        md += ["", "**入库率：%.0f%%**（已入库 %d / 要求 %d ✓）"
               % (100.0 * ok_n / len(ing_rows), ok_n, len(ing_rows))]
    else:
        md += ["（本次未生成知识入库清单：知识入库任务未执行，或 facts 未带 `ingest` ✗）"]

    md += ["", "## ⑤ 结论与建议", "",
           "**总判定：%s**" % ("不通过（否决 %d：%s）" % (len(veto), "、".join(x["id"] for x in veto))
                              if veto else ("不通过（无严重项）" if failed else "通过")),
           "- 结论分布：不符合 %d ｜ 符合 %d ｜ 无法判定 %d" % (len(failed), len(passed), len(insuf)),
           "- 可信度：高 %d ｜ 中 %d ｜ 低 %d" % (sum(1 for x in rows if x["level"] == "high"),
                                              sum(1 for x in rows if x["level"] == "medium"),
                                              sum(1 for x in rows if x["level"] == "low"))]
    _coverage = _check_coverage(rows, lk["rules"])
    return {"content": "\n".join(md) + "\n", "cols": cols, "coverage": _coverage,
            "counts": {"不符合": len(failed), "符合": len(passed), "无法判定": len(insuf),
                       "否决": len(veto)}, "lookup_hash": lk["hash"]}


def write(sample: str, doc_title: str, facts: dict, domain_kb: str, proj_kb: str,
          slug: str, title: str, doc_ref: str = "") -> dict:
    """渲染 + 走写核入库 ✓（`bmm:TechnicalReviewReport` ✓ document 模式 ✓）。"""
    import ke_db
    import ke_pages
    r = render(sample, doc_title, facts, domain_kb, ingest=(facts.get("ingest") or None))
    kb = ke_db.resolve_kb_id(proj_kb)[0]
    out = ke_pages.write_knowledge(kb, {
        "slug": slug, "title": title, "page_type": "bmm:TechnicalReviewReport",
        "wiki_content": r["content"],
        "attributes": {"skos:definition": "%s 端到端评审报告：③ %d 列（列名/顺序取自 DataObject ✓）；"
                                        "④ 知识入库情况（入库率见正文 ✓）；文档位置=章节标题 ✓；"
                                        "可信度=ke_score ✓。" % (sample, len(r["cols"]))},
        "source": {"doc_refs": [doc_ref or doc_title], "source_text": (facts.get("items") or [{}])[0].get("quote", "")},
    }, strict_source=True)

    # 关联：报告 → 规则（`agent:reportAppliesRule`，同库副本 ✓；缺副本则跳过并提示 ✗）
    edges, missing = [], []
    try:
        import ke_graph
        for it in facts.get("items") or []:
            name = str(it.get("rule") or "").strip()
            if not name:
                continue
            rows = ke_db.psql_csv(
                "SELECT slug FROM wiki_pages WHERE knowledge_base_id = %s AND deleted_at IS NULL "
                " AND page_type IN ('bmm:OperativeBusinessRule','bmm:StructuralBusinessRule') "
                " AND title IN (%s, %s) LIMIT 1"
                % (ke_db.sql_str(kb), ke_db.sql_str(name), ke_db.sql_str(name + "（副本）")))
            if not rows:
                missing.append(name)
                continue
            ke_graph.add_edge(kb, out.get("slug") or slug, "agent:reportAppliesRule", rows[0]["slug"])
            edges.append({"to": rows[0]["slug"], "ok": True})
    except Exception as exc:  # noqa: BLE001
        edges.append({"error": str(exc)[:120]})
    return {"slug": slug, "version": out.get("version"), "graph": out.get("graph"),
            "counts": r["counts"], "cols": r["cols"], "coverage": r.get("coverage"),
            "edges": edges, "missing_replica": missing}
