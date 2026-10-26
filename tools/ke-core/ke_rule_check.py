"""ke_core · 规则体检（规则工程 · 规则化/评测的**门禁** ✓）

一条规则要能**被机算、被评测、被任务步骤引用**，必须过四关：
  ① **字段齐全**：title / definition / scope / enforcement / implementation ✓
  ② **可机算**：implementation ∈ 受控集合（含 事实核查/知识提取/图检索/LLM/规则推理 之一）✓；
     抽取型（needs_extract）⇒ `knowledgeNeed` 非空 且 `required_fields` 非空 ✓
  ③ **无歧义**：definition 不得只有模糊词（合理/适当/尽量/完善/良好/必要时…）✗
  ④ **可回溯**：`reference`（参考规范）建议非空（warn）；
     required_fields 必须含 conclusion / evidence / severity / rule_ref ✓ 且 conclusion 枚举四态 ✓

CLI：
  python3 ke_rule_check.py [--kb <领域库名>] [--spec policy:<策略页名>] [--write]
"""
from __future__ import annotations

import argparse
import json
import re

VERDICTS = ("合规", "违规", "不适用", "待澄清")
IMPL_ALLOWED = ("事实核查", "知识提取", "图检索", "LLM", "规则推理")
VAGUE = ("合理", "适当", "尽量", "完善", "良好", "必要时", "酌情", "大致", "较为")
REQUIRED = ("conclusion", "evidence", "severity", "rule_ref")
QUANT = re.compile(r"（|\(|\d|≥|≤|=|须|必须|不得|禁止|不少于|不超过")


def _verdicts() -> tuple:
    """判定枚举**与台账同源**（2026-10-27，P2）：读 it-platform 契约，失败回退常量。"""
    try:
        import ke_review  # noqa: PLC0415
        return tuple(ke_review._ledger_enums()["verdict"])
    except Exception:  # noqa: BLE001
        return VERDICTS


def check_rules(kb: str, spec: str) -> dict:
    import ke_lookup  # noqa: PLC0415

    lk = ke_lookup.knowledge_lookup(kb, mode="rules", spec=spec)
    rules = lk.get("rules") or []
    if not rules:
        return {"ok": False, "hash": lk.get("hash"), "rules": 0, "per_rule": [],
                "errors": ["没有解析到规则（spec=%s ✗ 检查策略是否挂规则）" % spec], "warns": []}

    errors, warns, per = [], [], []
    for r in rules:
        name = str(r.get("title") or r.get("slug") or "?")[:40]
        p, w = [], []
        for f in ("title", "definition", "scope", "enforcement", "implementation"):
            if not str(r.get(f) or "").strip():
                p.append("字段空：%s" % f)
        impl = str(r.get("implementation") or "")
        if impl and not any(k in impl for k in IMPL_ALLOWED):
            p.append("实现方式不受控：%r" % impl[:20])
        if r.get("needs_extract"):
            if not str(r.get("knowledgeNeed") or "").strip():
                p.append("抽取型缺 knowledgeNeed")
            rf = r.get("required_fields") or {}
            if not rf:
                p.append("抽取型缺 required_fields")
            else:
                lack = [k for k in REQUIRED if k not in rf]
                if lack:
                    p.append("required_fields 缺：%s" % "、".join(lack))
                concl = str(rf.get("conclusion") or "")
                if concl and not all(v in concl for v in _verdicts()):
                    p.append("conclusion 枚举与台账不一致（须含 %s）" % "｜".join(_verdicts()))
            aw = str(r.get("applies_when") or "").strip()
            if aw in ("", "always"):
                w.append("未声明适用条件（applies_when=always）：条件性规则请在 "
                         "`skos:note` 的 ```knowledgeSpec 块里写 applies_when")
        dfn = str(r.get("definition") or "")
        if dfn and len(re.sub(r"\s", "", dfn)) < 10:
            w.append("定义过短（%d 字）" % len(dfn))
        vag = [v for v in VAGUE if v in dfn]
        if vag and not QUANT.search(dfn):
            w.append("含模糊词（%s）且无可量化判据" % "、".join(vag))
        if not str(r.get("reference") or "").strip():
            w.append("参考规范为空")
        per.append({"rule": name, "slug": r.get("slug"), "errors": p, "warns": w})
        errors += ["%s：%s" % (name, x) for x in p]
        warns += ["%s：%s" % (name, x) for x in w]
    return {"ok": not errors, "hash": lk.get("hash"), "rules": len(rules),
            "errors": errors, "warns": warns, "per_rule": per}


def render_md(res: dict, kb: str, spec: str) -> str:
    md = ["# 规则体检报告（规则工程 · 规则化门禁）", "",
          "> 库：`%s`｜规则集：`%s`｜规则 %d 条｜hash `%s`  " % (kb, spec, res.get("rules", 0),
                                                              res.get("hash") or "-"),
          "> 结论：**%s**（错误 %d ／ 提醒 %d）" % ("通过" if res.get("ok") else "未通过",
                                                   len(res["errors"]), len(res["warns"])), "",
          "## ① 逐条体检", "", "| 规则 | 错误 | 提醒 |", "|---|---|---|"]
    for x in res["per_rule"]:
        md.append("| %s | %s | %s |" % (x["rule"], "；".join(x["errors"]) or "✓",
                                        "；".join(x["warns"]) or "—"))
    md += ["", "## ② 结论与建议", ""]
    md.append("- 结论：**%s**" % ("可进入「规则评测」✓" if res["ok"]
                                 else "退回「规则化」✗（先修错误项再重跑体检）"))
    if res["warns"]:
        md.append("- 提醒 %d 条（不阻断，但影响可信度）：参考规范缺失 / 模糊词" % len(res["warns"]))
    return "\n".join(md) + "\n"


def _specs_all(kb: str) -> list:
    """该库**全部策略** → `policy:<slug>` 清单（用于 `--all`；只列，不猜）。"""
    import ke_lookup  # noqa: PLC0415

    lk = ke_lookup.knowledge_lookup(kb, mode="rules", spec="ask")
    out = []
    for p in (lk.get("scope_options") or []):
        s = str(p.get("slug") or p.get("title") or "").strip()
        if s:
            out.append("policy:" + s)
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description="规则体检（规则工程门禁）")
    ap.add_argument("--kb", default="")
    ap.add_argument("--spec", default="")
    ap.add_argument("--all", action="store_true", help="扫该库**全部策略**（逐策略体检 + 汇总）")
    ap.add_argument("--write", action="store_true", help="落库为 bmm:Report 页")
    ap.add_argument("--work-kb", default="")
    a = ap.parse_args()
    import ke_db  # noqa: PLC0415

    kb = ke_db.resolve_kb_id(a.kb)[0]
    # 2026-10-27 修（用户实测）：不给 spec 时原来直接"规则 0 条 ⇒ 未通过（错误 1）"，
    # 会被误读成"规则不合格"。现在：明确告诉怎么用；`--all` 则逐策略体检并汇总 ✓。
    if a.all:
        specs = _specs_all(kb)
        if not specs:
            print("本库没有解析到策略（`knowledge_lookup(mode=\"rules\", spec=\"ask\")` 回空）"
                  "⇒ 先确认策略页是否挂了规则")
            raise SystemExit(2)
        total_e = total_w = 0
        rows = []
        for sp in specs:
            res = check_rules(kb, sp)
            e, w = len(res.get("errors") or []), len(res.get("warns") or [])
            total_e += e
            total_w += w
            rows.append((sp, res.get("rules") or 0, e, w, res.get("hash") or "-"))
        lines = ["# 规则体检（全库策略）", "",
                 "> 库：`%s`｜策略 %d 个｜规则 %d 条｜错误 %d ／ 提醒 %d"
                 % (kb, len(specs), sum(r[1] for r in rows), total_e, total_w), "",
                 "| 策略 | 规则 | 错误 | 提醒 | 规则集 hash |", "|---|---|---|---|---|"]
        for sp, n, e, w, h in rows:
            lines.append("| `%s` | %d | %d | %d | `%s` |" % (sp, n, e, w, h))
        lines += ["", "- 结论：**%s**" % ("可进入「规则评测」✓" if total_e == 0 else "退回「规则化」✗（先修错误项）")]
        print("\n".join(lines))
        if total_e:
            raise SystemExit(1)
        return
    if not str(a.spec or "").strip():
        print("请给 `--spec policy:<策略页名>`（单策略）或 `--all`（本库全部策略）")
        print("  例：python3 tools/ke-core/ke_rule_check.py --kb <领域库> --spec policy:部署架构设计原则")
        raise SystemExit(2)

    res = check_rules(kb, a.spec)
    md = render_md(res, a.kb, a.spec)
    print(md)
    if a.write:
        import ke_pages  # noqa: PLC0415
        wk = ke_db.resolve_kb_id(a.work_kb)[0]
        slug = "review/规则体检-" + re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]+", "-", a.spec).strip("-")[-40:]
        out = ke_pages.write_knowledge(wk, {
            "slug": slug, "title": "规则体检报告（%s）" % a.spec,
            "page_type": "bmm:Report", "wiki_content": md,
            "attributes": {"skos:definition": "规则工程门禁：字段齐全 / 可机算 / 无歧义 / 可回溯 ✓"},
            "source": {"doc_refs": [a.spec],
                       "source_text": "规则集来自领域库规则页（hash %s）" % (res.get("hash") or "-")},
        }, strict_source=True)
        print(json.dumps({"written": out.get("slug"), "version": out.get("version")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
