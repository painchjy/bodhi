"""ke-core · 知识计划（方案 C 的确定性部分，**只读**）

`ke_plan.build(domain_kb, task, policies=[])`：
  ① `rules_of_task` → `needs_extract=true` 的规则；
  ② `knowledgeNeed` 拆知识项 → 现查候选类（词面/别名匹配）；
  ③ 去重（同类合并，记 from_rules）；
  ④ 父子整合：**只合并同链**（祖先闭包 → 最近公共祖先 LCA），`collect_at` = parent|each；
  ⑤ 归属模型 + 专家角色：读**编译产物**（`module` / `models[].expert_role`）；
  ⑥ 外部系统已管理的数据 ⇒ `source=external` + DataObject + via=stub|api（**不本体化**）；
  ⑦ 歧义进 `questions`（人裁决）+ 稳定 `hash`。

技能 `knowledge_plan` 调它（确定性 ✓）；智能体只消费结果并回答 `questions` ✓。
"""
from __future__ import annotations

import hashlib
import json
import re

import ke_db
import ke_ontology
import ke_review

SPLIT = re.compile(r"[、，,;；/|]+")


def _meta() -> dict:
    try:
        return ke_ontology.class_meta() or {}
    except Exception:  # noqa: BLE001
        return {}


def _index() -> dict:
    try:
        return ke_ontology.index_data() or {}
    except Exception:  # noqa: BLE001
        return {}


def _role_of(module: str, idx: dict) -> str:
    for m in (idx.get("models") or []):
        if m.get("key") == module:
            return str(m.get("expert_role") or "")
    return ""


def _parents(meta: dict, cls: str) -> list:
    return [str(x) for x in ((meta.get(cls) or {}).get("parents") or []) if x]


def _ancestors(meta: dict, cls: str) -> list:
    seen, out, cur = {cls}, [], cls
    while True:
        ps = _parents(meta, cur)
        if not ps:
            break
        cur = ps[0]
        if cur in seen:
            break
        seen.add(cur)
        out.append(cur)
    return out


def _lca(meta: dict, classes: list) -> str:
    """同链才返回 LCA（否则 ""）。"""
    if len(classes) < 2:
        return ""
    chains = [[c] + _ancestors(meta, c) for c in classes]
    common = set(chains[0])
    for ch in chains[1:]:
        common &= set(ch)
    for c in chains[0]:
        if c in common:
            return c
    return ""


def _match_classes(token: str, meta: dict, limit: int = 5) -> list:
    """词面/别名匹配（label 含 或 含 label；忽略大小写与括号内容）。"""
    t = re.sub(r"[（(].*?[）)]", "", str(token or "")).strip().lower()
    if not t:
        return []
    scored = []
    for cls, info in meta.items():
        label = str((info or {}).get("label") or "").lower()
        if not label:
            continue
        score = 0
        if label == t:
            score = 100
        elif t in label or label in t:
            score = 60
        elif any(tok and tok in label for tok in SPLIT.split(t)):
            score = 30
        if score:
            scored.append((score, cls))
    scored.sort(key=lambda x: (-x[0], x[1]))
    return [c for _s, c in scored[:limit]]


def _external(token: str, kb: str) -> dict:
    """外部系统已管理的数据：在 DataObject 页里按标题/definition 找（**不建本体类** ✗）。"""
    try:
        rows = ke_db.psql_csv(
            "SELECT slug, COALESCE(title,'') AS t, "
            "       COALESCE(page_metadata->'ontology'->'attributes'->>'skos:definition','') AS d "
            " FROM wiki_pages WHERE knowledge_base_id = %s AND deleted_at IS NULL "
            "   AND page_type = 'bpmn:DataObject'" % ke_db.sql_str(kb))
    except Exception:  # noqa: BLE001
        return {}
    for r in rows:
        hay = (r["t"] or "") + " " + (r["d"] or "")
        if any(tok and tok in hay for tok in SPLIT.split(str(token or ""))):
            return {"dataobject": r["slug"], "title": r["t"], "via": "stub"}
    return {}


def build(domain_kb: str = "", task: str = "", policies: list | None = None) -> dict:
    kb, kb_name, _n = ke_db.resolve_kb_id(domain_kb)
    rep = ke_review.rules_of_task(domain_kb, task)
    rules = [r for r in (rep.get("rules") or []) if r.get("needs_extract")]
    if not rules:
        return {"ok": True, "task": (rep.get("task") or {}).get("slug"), "empty": True,
                "counts": {"rules": len(rep.get("rules") or []), "items": 0},
                "note": "本步没有需要知识提取的规则"}

    meta, idx = _meta(), _index()
    items: dict = {}
    questions: list = []

    def _spec_of(text) -> dict | None:
        """`knowledgeNeed` 是结构化 JSON（规则已标注）⇒ 直接用它，**不再猜类** ✗。"""
        t = str(text or "").strip()
        if not t.startswith("{"):
            return None
        try:
            obj = json.loads(t)
        except Exception:  # noqa: BLE001
            return None
        return obj if isinstance(obj, dict) and obj.get("concepts") else None

    for r in rules:
        spec = _spec_of(r.get("knowledgeNeed"))
        if spec:
            for c in (spec.get("concepts") or []):
                if not isinstance(c, dict) or not c.get("id"):
                    continue
                key = "%s:%s" % (r["slug"], c["id"])
                kind = str(c.get("kind") or "class")
                it = items.setdefault(key, {
                    "need": c.get("note") or c.get("id"), "id": c["id"], "kind": kind,
                    "class": c.get("class") or None, "candidates": [],
                    "source": "external" if kind == "external" else "ontology",
                    "dataobject": c.get("dataobject") or "", "via": c.get("via") or "",
                    "collect_at": c.get("collect") or "each",
                    "module": "", "expert_role": "",
                    "tools": c.get("tools") or [], "required": bool(c.get("required")),
                    "confidence": c.get("confidence") or "high",
                    # 2026-10-17（用户口径 ✓）：供 `ke_score` 直接消费的三键 ✓
                    #   · status 三态：present / absent_verified / unavailable ✓（初值 present ✓，抽取端可改 ✓）
                    #   · source_type：用于置信加权 ✓（class→graph ✓ / image ✓ / text ✓ / table ✓ / external ✓）
                    "status": "present",
                    "source_type": {"class": "graph", "image": "image", "text": "doc_text",
                                    "table": "doc_table", "external": "external_query"}.get(kind, "unknown"),
                    # 2026-10-17：抽取端回填 ✓ —— 这条事实从哪来（决定"文档遗漏" vs "外部不可得" ✓）
                    #   取值：`doc` ✓ / `other_kb:<库名或slug>` ✓ / `none` ✗（哪都没有 ⇒ unavailable ✓）
                    "found_in": "",
                    "facts": c.get("facts") or [], "columns": c.get("columns") or [],
                    "from_rules": [], "why": "规则已标注（knowledgeSpec）✓ 不猜类"})
                it["from_rules"] = sorted(set(it["from_rules"] + [r["slug"]]))
                cls = it["class"]
                if cls and cls in meta:
                    it["module"] = str(((meta.get(cls) or {}).get("module")) or "")
                    it["expert_role"] = _role_of(it["module"], idx)
                    if not it["expert_role"]:
                        questions.append({"kind": "role_missing", "item": it["need"],
                                          "ask": "模块 %s 没有专家角色，用默认角色？" % (it["module"] or "?"),
                                          "options": ["默认", "指定"]})
            continue
        for need in SPLIT.split(str(r.get("knowledgeNeed") or "")):
            need = need.strip()
            if not need:
                continue
            it = items.setdefault(need, {"need": need, "class": None, "candidates": [],
                                         "source": "ontology", "dataobject": "", "via": "",
                                         "collect_at": "each", "module": "", "expert_role": "",
                                         "from_rules": [], "why": ""})
            it["from_rules"] = sorted(set(it["from_rules"] + [r["slug"]]))
            ext = _external(need, kb)
            if ext:
                it.update({"source": "external", "dataobject": ext["dataobject"], "via": ext["via"],
                           "why": "外部系统已管理的数据（DataObject + 挡板/接口，不本体化）"})
                continue
            cands = _match_classes(need, meta)
            if cands:
                it["candidates"] = sorted(set(it["candidates"] + cands))
                it["why"] = "词面/别名匹配（ontology_types 现查）"
            else:
                questions.append({"kind": "class_ambiguous", "item": need,
                                  "ask": "「%s」对应哪个本体类？" % need, "options": []})

    stated = sorted({c for it in items.values() for c in (it["candidates"] or []) if c})
    merged = _lca(meta, stated)
    for it in items.values():
        cands = [c for c in (it["candidates"] or []) if c]
        if not cands:
            continue
        pick = merged if (merged and merged in (cands + _ancestors(meta, cands[0]))) else cands[0]
        it["class"] = pick
        if len(cands) > 1:
            it["collect_at"] = "parent"
            questions.append({"kind": "merge_parent_child", "item": it["need"],
                              "ask": "「%s」可用父类 %s 统一采集吗？（否则逐类采集）" % (it["need"], pick),
                              "options": ["parent", "each"]})
        it["module"] = str(((meta.get(pick) or {}).get("module")) or "")
        it["expert_role"] = _role_of(it["module"], idx)
        if not it["expert_role"]:
            questions.append({"kind": "role_missing", "item": it["need"],
                              "ask": "模块 %s 没有专家角色，用默认角色？" % (it["module"] or "?"),
                              "options": ["默认", "指定"]})

    groups: dict = {}
    for it in items.values():
        if it["module"]:
            groups.setdefault(it["module"], []).append(it["need"])
    # 推论规则（spec.derived）——交给 rule_judge 消费：缺必需输入⇒违规、同城/异地、等级判定…
    derived_out: list = []
    for r in rules:
        sp = _spec_of(r.get("knowledgeNeed"))
        if not sp:
            continue
        for d in (sp.get("derived") or []):
            if isinstance(d, dict):
                derived_out.append(dict(d, from_rule=r["slug"]))
    payload = {"task": (rep.get("task") or {}).get("slug"),
               "items": sorted(items.values(), key=lambda x: x["need"]),
               "rules": sorted([{"slug": r["slug"], "knowledgeNeed": r["knowledgeNeed"]} for r in rules],
                               key=lambda x: x["slug"])}
    return {"ok": True, "kb": {"id": kb, "name": kb_name}, "task": (rep.get("task") or {}).get("slug"),
            "items": payload["items"], "groups": groups, "questions": questions,
            "derived": derived_out,
            "counts": {"rules": len(rep.get("rules") or []), "needs_extract": len(rules),
                       "items": len(items),
                       "external": sum(1 for x in items.values() if x["source"] == "external"),
                       "ambiguous": len(questions)},
            "hash": hashlib.sha256(json.dumps(payload, ensure_ascii=False,
                                              sort_keys=True).encode("utf-8")).hexdigest()[:16]}
