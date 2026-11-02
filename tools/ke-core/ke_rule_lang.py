"""规则语言（**规则工程的机器口径**）：模式 → 语言 → `knowledgeNeed` 的格式校验与迁移。

口径（2026-11-02 用户）
----------------------
- 规则 = **内容**（自然语言：定义/范围/执行级别/参考）+ **规范化**（机器口径 `bmm:knowledgeNeed`）；
- 机器口径**只有一个载体**：`bmm:knowledgeNeed`（JSON）。note 里的 knowledgeSpec 围栏块**已废弃**；
- 先定**目的/机理**（`bmm:ruleMode`）⇒ 再定**语言**（`bmm:ruleLanguage`）⇒ 按该语言写 `knowledgeNeed`；
- 三种模式先行：`text-logic`（文本/图像提取 + 真实性完整性 + 逻辑推理）、
  `ontology-extract`（按本体类型抽取）、`compute`（取数 + 公式阈值）。

三种语言的 schema（**v1.1**，宽松：允许多余键，不写死业务词）
------------------------------------------------------------------
text-logic        {concepts:[{id, kind∈text|table|image|external|graph|class, facts:[..],
                             required?, on_missing?∈四态, tools?:[..], columns?:[..],
                             class?（kind=class 必填，本体类名）}],
                   derived?:[{id, by∈check|compare|inference|calc, from:[conceptId], rule}],
                   answer:{conclusion,evidence,locator, rule_ref?, confidence?∈high|medium|low},
                   applies_when?}
ontology-extract  {types:[{class,required?}], relations?:[{from,rel,to,min?}],
                   answer:{conclusion,evidence,locator}}
compute           {inputs:[{id,source,field?}], formula, threshold?:{...},
                   answer:{conclusion,evidence,locator}}

体检分两档（2026-11-02 v1.1）：`lint()` → `{errors, warns}`；errors **阻断**、warns **只提醒**。
    · 阻断：结构缺失 / `kind=class` 缺 `class` / `by` 不在受控集 / `on_missing` 非四态 /
            结论位置仍写旧枚举（合规·违规·待澄清）
    · 提醒：`table` 缺 `columns`、`image|table|external` 缺 `tools`、`required` 缺 `on_missing`
            （**缺省按「无法判定」，不替你猜**）、`answer.confidence` 非三档、`answer.rule_ref` 为空

命令行
------
    python3 ke_rule_lang.py --kb <库> [--apply]        # 迁移：knowledgeSpec/中文清单 → knowledgeNeed
"""
from __future__ import annotations

import argparse
import json
import re

MODES = ("text-logic", "ontology-extract", "compute")
LANG_BY_MODE = {"text-logic": "kn/text-logic-v1",
                "ontology-extract": "kn/ontology-extract-v1",
                "compute": "kn/compute-v1"}
MODE_BY_LANG = {v: k for k, v in LANG_BY_MODE.items()}
# 规则**类型用子类表达**（2026-11-02 用户口径：本体不靠枚举属性区分类型）：
#   · 文本/图像 + 逻辑推理 ⇒ `bmm:TextLogicOperationalRule`（OperativeBusinessRule 的子类）
#   · 本体类型提取       ⇒ 既有 `bmm:StructuralBusinessRule`（**不另建类**）
#   · 计算类             ⇒ 暂无专属类（用 note 里的 language 声明；要建模时再加子类）
MODE_BY_CLASS = {"textlogicoperationalrule": "text-logic",
                 "structuralbusinessrule": "ontology-extract"}
LANG_RE = re.compile(r"(?mi)^\s*(?:language|schema)\s*[:：]\s*(kn/[\w./-]+)\s*$")
KINDS = ("text", "table", "image", "external", "graph", "class")
KIND_ALIAS = {"doc_text": "text", "doc_table": "table", "doc_image": "image",
              "docimage": "image", "docfile": "external", "doc": "text",
              "ontology": "class", "type": "class"}
DERIVE_ALIAS = {"infer": "inference", "logic": "inference", "rule": "inference",
                "reasoning": "inference", "match": "compare", "equals": "compare",
                "eq": "compare", "compute": "calc", "calculation": "calc"}
# 旧口径（台账统一前）→ 四态（2026-11-02 迁移用；只换**取值**，不改散文）
VERDICT_MAP = {"合规": "符合", "违规": "不符合", "待澄清": "无法判定"}
DERIVE_BY = ("check", "compare", "inference", "calc")
VERDICTS = ("符合", "不符合", "不适用", "无法判定")
CONFIDENCE = ("high", "medium", "low")
TOOL_KINDS = ("image", "table", "external")      # 这几类输入通常要工具才取得到
# 「结论位置」= `conclusion/结论 = …`、`⇒`、`->`、`→`、`否则` 之后 —— **只有这里**才允许换旧枚举
CONCL_MARK = re.compile(r"(?i)(?:conclusion|结论)\s*[=:：]|⇒|->|→|否则")
CONCL_END = re.compile(r"[;；。\n]")
SPEC_RE = re.compile(r"(?ms)^```knowledgeSpec[^\n]*\n(.*?)^```")


# ---------------------------------------------------------------------------
# 解析与校验
# ---------------------------------------------------------------------------
def parse_need(raw) -> tuple:
    """`knowledgeNeed` → `(dict|None, error)`。**宽容**：dict 直通；str 试 JSON；
    其它（如中文清单）⇒ error=`not_json`（交迁移器处理）。"""
    if isinstance(raw, dict):
        return raw, ""
    s = str(raw or "").strip()
    if not s:
        return None, "empty"
    try:
        got = json.loads(s)
    except Exception:  # noqa: BLE001
        return None, "not_json"
    if not isinstance(got, dict):
        return None, "not_object"
    return got, ""


def _map_concl_text(text: str) -> str:
    """把**结论位置**（`conclusion/结论 =`、`⇒`、`->`、`→` 之后）的旧枚举换成四态。

    其它位置（如「违规项的整改要求」这种定语）**原样保留** —— 由 lint 报 error 交人改写 ✓
    """
    s = str(text or "")
    if not s:
        return s
    out, last = [], 0
    for m in CONCL_MARK.finditer(s):
        out.append(s[last:m.end()])
        rest = s[m.end():]
        cut = CONCL_END.search(rest)
        seg = rest[:cut.start()] if cut else rest
        for old, new in VERDICT_MAP.items():
            seg = seg.replace(old, new)
        out.append(seg)
        last = m.end() + len(seg)
    out.append(s[last:])
    return "".join(out)


def fix_need(need: dict) -> dict:
    """把旧口径**就地规范化**（深拷贝）：`kind` 别名归一 + 结论换成台账四态。

    只改"机器取值"，不动散文（`derived.rule` / `note` 原样保留）。
    """
    if not isinstance(need, dict):
        return need
    out = json.loads(json.dumps(need, ensure_ascii=False))
    for c in out.get("concepts") or []:
        if isinstance(c, dict) and c.get("kind") is not None:
            k = str(c.get("kind") or "").strip().lower()
            c["kind"] = KIND_ALIAS.get(k, k)
    for d in out.get("derived") or []:
        if isinstance(d, dict):
            b = str(d.get("by") or "").strip().lower()
            d["by"] = DERIVE_ALIAS.get(b, b)
            if str(d.get("rule") or "").strip():      # 只动**结论位置**的旧枚举（2026-11-02 口径）
                d["rule"] = _map_concl_text(str(d["rule"]))
    ans = out.get("answer")
    if not isinstance(ans, dict):                  # 迁移：answer 缺失 ⇒ 补**骨架**（不编结论）
        out["answer"] = {"conclusion": "|".join(VERDICTS),
                         "evidence": "原文/实例证据（逐字摘录，可回溯）",
                         "locator": "章节号/图号/表行"}
        ans = out["answer"]
    if isinstance(ans, dict):
        concl = str(ans.get("conclusion") or "")
        parts = [p.strip() for p in re.split(r"[|｜/]", concl) if p.strip()]
        mapped = [VERDICT_MAP.get(p, p) for p in parts]
        if mapped:
            ans["conclusion"] = "|".join(list(dict.fromkeys(mapped)))
    return out


def _ans_err(need: dict) -> list:
    ans = need.get("answer")
    if not isinstance(ans, dict):
        return ["answer 缺失（应有 conclusion/evidence/locator）"]
    errs = []
    # `conclusion` 在 knowledgeNeed 里是**约束**（允许取值的说明），既可是单个四态值，
    # 也可是 `符合|不符合|不适用|无法判定` 这样的全集（2026-11-02：按正式库既有写法兼容 ✓）
    concl = str(ans.get("conclusion") or "").strip()
    parts = [p.strip() for p in re.split(r"[|｜/]", concl) if p.strip()]
    bad = [p for p in parts if p not in VERDICTS]
    if not parts or bad:
        errs.append("answer.conclusion 必须是四态之一或其集合（%s）"
                    % "/".join(VERDICTS))
    for k in ("evidence", "locator"):
        if not str(ans.get(k) or "").strip():
            errs.append("answer.%s 不能为空" % k)
    return errs


def _errors_only(mode: str, need: dict) -> list:
    """按模式校验 `knowledgeNeed` 的**格式**（语义/样例验证后续再做）。→ errors[]（阻断项）"""
    mode = str(mode or "").strip()
    if mode not in MODES:
        return ["ruleMode 必须是 %s 之一（当前 %r）" % (" / ".join(MODES), mode)]
    if not isinstance(need, dict):
        return ["knowledgeNeed 必须是 JSON 对象"]
    need = fix_need(need)
    errs = _ans_err(need)
    if mode == "text-logic":
        cs = need.get("concepts")
        if not isinstance(cs, list) or not cs:
            errs.append("text-logic 需要非空 concepts[]")
        else:
            ids = set()
            for i, c in enumerate(cs):
                if not isinstance(c, dict):
                    errs.append("concepts[%d] 必须是对象" % i)
                    continue
                cid = str(c.get("id") or "").strip()
                if not cid:
                    errs.append("concepts[%d] 缺 id" % i)
                else:
                    ids.add(cid)
                if str(c.get("kind") or "") not in KINDS:
                    errs.append("concepts[%d].kind 必须是 %s 之一" % (i, "/".join(KINDS)))
                if str(c.get("kind") or "") == "class" and not str(c.get("class") or "").strip():
                    errs.append("concepts[%d]（kind=class）缺 class（本体类名，如 bmm:SubSystem）" % i)
                if c.get("required") is True:
                    om = str(c.get("on_missing") or "").strip()
                    if om and om not in VERDICTS:
                        errs.append("concepts[%d].on_missing 必须是四态之一（%s）"
                                    % (i, "/".join(VERDICTS)))
                facts = c.get("facts")
                if facts is not None and not isinstance(facts, list):
                    errs.append("concepts[%d].facts 必须是字符串数组" % i)
            for i, d in enumerate(need.get("derived") or []):
                if not isinstance(d, dict):
                    errs.append("derived[%d] 必须是对象" % i)
                    continue
                # v1.1（2026-11-02）：`by` 收编为受控集（新增 `check`=按清单/条件逐项核对）
                _by = str(d.get("by") or "").strip()
                if _by not in DERIVE_BY:
                    errs.append("derived[%d].by 必须是 %s 之一（当前 %r）"
                                % (i, "｜".join(DERIVE_BY), _by))
                bad_from = [f for f in (d.get("from") or []) if str(f) not in ids]
                if bad_from:
                    errs.append("derived[%d].from 引用了不存在的 concept：%s"
                                % (i, "、".join(str(x) for x in bad_from)))
                if not str(d.get("rule") or "").strip():
                    errs.append("derived[%d].rule 不能为空" % i)
                else:
                    _old = [v for v in VERDICT_MAP if v in str(d["rule"])]
                    if _old:
                        errs.append("derived[%d].rule 用了旧结论枚举（%s）⇒ 结论位置只许四态：%s"
                                    % (i, "、".join(_old), "/".join(VERDICTS)))
    elif mode == "ontology-extract":
        ts = need.get("types")
        if not isinstance(ts, list) or not ts:
            errs.append("ontology-extract 需要非空 types[]（每项含 class）")
        else:
            for i, t in enumerate(ts):
                if not isinstance(t, dict) or not str(t.get("class") or "").strip():
                    errs.append("types[%d] 需要 class" % i)
        for i, r in enumerate(need.get("relations") or []):
            if not isinstance(r, dict) or not all(r.get(k) for k in ("from", "rel", "to")):
                errs.append("relations[%d] 需要 from/rel/to" % i)
    elif mode == "compute":
        ins = need.get("inputs")
        if not isinstance(ins, list) or not ins:
            errs.append("compute 需要非空 inputs[]（每项含 id/source）")
        else:
            for i, it in enumerate(ins):
                if not isinstance(it, dict) or not str(it.get("id") or "").strip():
                    errs.append("inputs[%d] 需要 id" % i)
        if not str(need.get("formula") or "").strip():
            errs.append("compute 需要 formula（如 `pass = p95 <= 200`）")
    return errs


def _warns_only(mode: str, need: dict) -> list:
    """**不阻断**的提醒（v1.1，2026-11-02）：取数信息缺失 / `on_missing` 未写（默认「无法判定」）。"""
    if not isinstance(need, dict):
        return []
    need = fix_need(need)
    w = []
    if str(mode or "").strip() != "text-logic":
        return w
    for i, c in enumerate(need.get("concepts") or []):
        if not isinstance(c, dict):
            continue
        kind = str(c.get("kind") or "")
        if kind == "table" and not c.get("columns"):
            w.append("concepts[%d]（table）建议写 columns（要读哪几列）" % i)
        if kind in TOOL_KINDS and not c.get("tools"):
            w.append("concepts[%d]（%s）建议写 tools（取数工具：image_extract/doc_outline/grep_chunks…）"
                     % (i, kind))
        if c.get("required") is True and not str(c.get("on_missing") or "").strip():
            w.append("concepts[%d] required=true 但未写 on_missing ⇒ 缺该输入时按「无法判定」处理"
                     "（建议显式写，不替你猜 ✗）" % i)
    ans = need.get("answer") if isinstance(need.get("answer"), dict) else {}
    conf = str(ans.get("confidence") or "").strip()
    if conf and conf not in CONFIDENCE:
        w.append("answer.confidence 建议取 %s（当前 %r）" % ("/".join(CONFIDENCE), conf))
    if not str(ans.get("rule_ref") or "").strip():
        w.append("answer.rule_ref 为空（建议写本规则页 slug，便于溯源）")
    return w


def lint(mode: str, need: dict) -> dict:
    """格式体检（schema v1.1）：`{errors:[…], warns:[…]}` —— **errors 阻断，warns 只进回执**。"""
    return {"errors": _errors_only(mode, need), "warns": _warns_only(mode, need)}


def validate(mode: str, need: dict) -> list:
    """兼容旧调用：只返回 errors（等价 `lint()["errors"]`）。"""
    return lint(mode, need)["errors"]


def infer_mode(need: dict) -> str:
    """按结构猜模式（**只用于迁移提示**，最终以规则页的**类**为准）。"""
    if not isinstance(need, dict):
        return ""
    if need.get("types"):
        return "ontology-extract"
    if need.get("formula") or need.get("inputs"):
        return "compute"
    if need.get("concepts"):
        return "text-logic"
    return ""


def mode_of(page_type: str = "", note: str = "") -> str:
    """规则页 → 模式：**先看类**（子类即类型），类认不出再看 note 里的 language 声明。"""
    pt = str(page_type or "").strip().lower().rsplit("/", 1)[-1]
    if pt.endswith(")"):
        pt = pt[:-1]
    for k, m in MODE_BY_CLASS.items():
        if k in pt:
            return m
    lang = language_of(note)
    return MODE_BY_LANG.get(lang, "")


def language_of(note: str = "") -> str:
    """note 里声明语言/schema 的那一行（如 `language: kn/text-logic-v1`）→ 语言标识。"""
    m = LANG_RE.search(str(note or ""))
    return m.group(1).strip() if m else ""


def expected_language(mode: str) -> str:
    return LANG_BY_MODE.get(str(mode or "").strip(), "")


def note_with_language(note: str, mode: str) -> str:
    """把（缺的）`language:` 行补进 note（**幂等**；已有则不动）。"""
    lang = expected_language(mode)
    cur = str(note or "").strip()
    if not lang:
        return cur
    if language_of(cur) == lang:
        return cur
    line = "language: %s" % lang
    if language_of(cur):                       # 换语言：替换旧行
        return LANG_RE.sub(line, cur)
    return (cur + "\n" + line).strip() if cur else line



# ---------------------------------------------------------------------------
# 迁移：knowledgeSpec（note 围栏块）/ 中文清单 → knowledgeNeed
# ---------------------------------------------------------------------------
def from_legacy_spec(spec: dict) -> dict:
    """旧 `knowledgeSpec`（note 里的块）→ text-logic 的 `knowledgeNeed`（**保留原意，不编内容**）。

    - `evidence_required` → 一个 `kind=text` 的 concept，`facts` 原样搬；
    - `pass_when`/`fail_when` → `derived[{by:inference, rule:…}]`（原句拼接，不改写）；
    - `applies_when` → 顶层 `applies_when`（允许的附加键，供后续语言扩展）；
    - `answer` 用四态占位（conclusion 允许模型填，evidence/locator 说明来源要求）。
    """
    spec = spec if isinstance(spec, dict) else {}
    facts = [str(x) for x in (spec.get("evidence_required") or []) if str(x).strip()]
    concept = {"id": "evidence", "kind": "text", "facts": facts or ["evidence"],
               "note": "迁移自旧 knowledgeSpec.evidence_required"}
    rules = []
    for key, tag in (("pass_when", "pass"), ("fail_when", "fail")):
        for x in (spec.get(key) or []):
            if str(x).strip():
                rules.append("【%s】%s" % (tag, str(x).strip()))
    need = {"concepts": [concept],
            "answer": {"conclusion": "|".join(VERDICTS),
                       "evidence": "原文证据（逐字摘录，可回溯）",
                       "locator": "章节号/图号/表行"}}
    if rules:
        need["derived"] = [{"id": "verdict", "by": "inference", "from": ["evidence"],
                            "rule": "；".join(rules)}]
    if str(spec.get("applies_when") or "").strip():
        need["applies_when"] = str(spec["applies_when"]).strip()
    if spec.get("threshold") is not None:
        need["threshold"] = spec["threshold"]
    return need


def from_term_list(text: str) -> dict:
    """中文类名清单（如「技术方案、应用系统、设备申请」）→ ontology-extract 的 `types[]`。"""
    items = [x.strip() for x in re.split(r"[、,，;；\n]+", str(text or "")) if x.strip()]
    return {"types": [{"class": x, "required": True} for x in items],
            "answer": {"conclusion": "|".join(VERDICTS),
                       "evidence": "实例/关系（逐字摘录，可回溯）",
                       "locator": "章节号/图号/表行"}}


def normalize(raw, mode: str = "") -> tuple:
    """任意形态的旧口径 → `(mode, need, notes[])`（**dry-run / 迁移共用**）。

    顺序：JSON 直通 → note 里的 ```knowledgeSpec 块 → 中文清单（ontology-extract）。
    """
    notes = []
    need, _err = parse_need(raw)
    if need is not None:
        need = fix_need(need)
        m = mode if mode in MODES else infer_mode(need)
        if mode and m and m != mode:
            notes.append("结构像 %s，但规则页 ruleMode=%s ⇒ 以 ruleMode 为准" % (m, mode))
        return m or "text-logic", need, notes
    m = SPEC_RE.search(str(raw or ""))
    if m:
        body = m.group(1)
        spec = None
        try:
            spec = json.loads(body)
        except Exception:  # noqa: BLE001  旧块也可能是 YAML（2026-11-02 补）
            try:
                import yaml  # noqa: PLC0415
                spec = yaml.safe_load(body)
            except Exception:  # noqa: BLE001
                spec = None
        if isinstance(spec, dict):
            notes.append("来源：note 里的 knowledgeSpec 块（已废弃，迁移后摘除）")
            return "text-logic", from_legacy_spec(spec), notes
    got = from_term_list(raw)
    if got["types"]:
        notes.append("来源：中文清单（按 ontology-extract 的 types 收）")
        return "ontology-extract", got, notes
    return "", {}, ["无法识别：既不是 JSON，也不是中文清单"]


# ---------------------------------------------------------------------------
# 批量迁移（dry_run 默认；落库走 ke_pages.set_page_attributes，本体守卫仍生效）
# ---------------------------------------------------------------------------
def migrate(kb: str = "", apply: bool = False, limit: int = 500) -> dict:
    import ke_db  # noqa: PLC0415

    where = (" AND knowledge_base_id = %s" % ke_db.sql_str(kb)) if kb else ""
    rows = ke_db.psql_csv(
        "SELECT knowledge_base_id AS kb, slug, COALESCE(title,'') AS title, "
        "  COALESCE(page_type,'') AS ptype, "
        "  COALESCE(page_metadata->'ontology'->'attributes'->>'bmm:knowledgeNeed','') AS need, "
        "  COALESCE(page_metadata->'ontology'->'attributes'->>'bmm:ruleMode','') AS mode, "
        "  COALESCE(page_metadata->'ontology'->'attributes'->>'bmm:ruleLanguage','') AS lang, "
        "  COALESCE(page_metadata->'ontology'->'attributes'->>'skos:note','') AS note "
        " FROM wiki_pages WHERE deleted_at IS NULL AND page_type LIKE %s%s "
        " ORDER BY slug LIMIT %d" % (ke_db.sql_str("%Rule%"), where, int(limit)))
    plan, done, failed = [], [], []
    for r in rows:
        raw = str(r.get("need") or "")
        if not raw.strip() and not str(r.get("note") or ""):
            continue
        mode, need, notes = normalize(raw, mode_of(str(r.get("ptype") or ""),
                                                  str(r.get("note") or "")))
        if not mode:
            empty = not raw.strip() and not SPEC_RE.search(str(r.get("note") or ""))
            plan.append({"slug": r["slug"], "action": "author_needed" if empty else "manual",
                         "need_raw": raw[:80], "notes": notes})
            continue
        errs = lint(mode, need)["errors"]
        item = {"slug": r["slug"], "title": r.get("title"), "kb": r.get("kb"),
                "mode": mode, "language": LANG_BY_MODE[mode], "notes": notes,
                "errors": errs, "warns": lint(mode, need)["warns"],
                "preview": json.dumps(need, ensure_ascii=False)[:280]}
        if not apply or errs:
            plan.append(item)
            continue
        ans = need.get("answer") if isinstance(need.get("answer"), dict) else {}
        if str(ans.get("rule_ref") or "").strip() in ("", "…", "...", "-", "—"):
            # v1.1：迁移时补**溯源**（本规则页 slug）—— 占位符不算溯源，同样覆盖 ✓
            ans["rule_ref"] = r["slug"]
            need["answer"] = ans
        attrs = {"bmm:knowledgeNeed": json.dumps(need, ensure_ascii=False)}
        note = SPEC_RE.sub("", str(r.get("note") or "")).strip()
        note2 = note_with_language(note, mode)
        if note2.strip() != str(r.get("note") or "").strip():
            attrs["skos:note"] = note2          # 摘掉废弃 spec 块 + 补 `language:` 行
        try:
            import ke_pages  # noqa: PLC0415
            ke_pages.set_page_attributes(r.get("kb") or kb, r["slug"], attrs, dry_run=False)
            done.append({"slug": r["slug"], "mode": mode})
        except Exception as exc:  # noqa: BLE001
            failed.append({"slug": r["slug"], "error": str(exc)[:160]})
    by_action = {}
    for it in plan:
        a = it.get("action") or "migrate"
        by_action[a] = by_action.get(a, 0) + 1
    return {"ok": not failed, "apply": apply, "scanned": len(rows),
            "planned": len(plan), "migrated": len(done), "failed": failed,
            "by_action": by_action, "plan": plan[:60],
            "note": ("dry_run：只出计划，不写库" if not apply
                     else "已迁移（有 errors 的留在 plan 里，未写库）")}


def main() -> None:
    ap = argparse.ArgumentParser(description="规则语言迁移（knowledgeSpec/中文清单 → knowledgeNeed）")
    ap.add_argument("--kb", default="", help="知识库 id 或名称（缺省=全库）")
    ap.add_argument("--apply", action="store_true", help="真写库（默认 dry-run ✓）")
    a = ap.parse_args()
    kb = a.kb
    if kb:
        import ke_db  # noqa: PLC0415
        kb = ke_db.resolve_kb_id(kb)[0]
    print(json.dumps(migrate(kb, apply=a.apply), ensure_ascii=False)[:4000])


if __name__ == "__main__":
    main()



