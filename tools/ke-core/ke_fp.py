"""ke_core · 规则哈希（**规则级** ✓，2026-10-19 用户口径）

- 载体：本体数据属性 **`bmm:ruleHash`**（`domain bmm:BusinessRule`，`range xsd:string`）✓
  —— 不再写进 `skos:note` ✗（note 只留发布留痕：日期 / 体检 / 评测 ✓）
- 口径：`sha1(定义 | 适用范围 | 执行级别 | 实现方式 | 所需知识 | 必填字段 | 参考规范)` 取 16 位；
  任一字段变化 ⇒ 发布时重算 ⇒ 值变化 ✓（规则变更即版本变化 ✓）
- 写入：`ke_rule_publish`（发布时写属性 ✓）
- 读取：报告里的规则行逐条取 `bmm:ruleHash` ✓
"""
from __future__ import annotations

import hashlib
import json

FIELDS = ("definition", "scope", "enforcement", "implementation",
          "knowledgeNeed", "required_fields", "reference")


def _norm(v) -> str:
    if isinstance(v, (dict, list, tuple)):
        return json.dumps(v, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return str(v or "").strip()


def rule_hash(rule: dict) -> str:
    """单条规则的版本哈希（16 位 hex）✓。"""
    raw = "|".join(_norm((rule or {}).get(f)) for f in FIELDS)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def rules_with_hash(kb: str, spec: str) -> list:
    """取规则并逐条计算 `ruleHash`（只读 ✓，供发布/报告引用）。"""
    import ke_lookup

    lk = ke_lookup.knowledge_lookup(kb, mode="rules", spec=spec)
    return [{**r, "ruleHash": rule_hash(r)} for r in (lk.get("rules") or [])]


def rule_hash_map(kb: str) -> dict:
    """规则标题 → **已发布**的 `bmm:ruleHash` 属性值（读页面属性 ✓，不读 note/metadata ✗）。"""
    import ke_db

    rows = ke_db.psql_csv(
        "SELECT COALESCE(title,'') AS t, "
        "       COALESCE(page_metadata->'ontology'->'attributes'->>'bmm:ruleHash','') AS h "
        "  FROM wiki_pages WHERE knowledge_base_id = %s AND deleted_at IS NULL "
        "   AND page_type IN ('bmm:OperativeBusinessRule','bmm:StructuralBusinessRule')"
        % ke_db.sql_str(kb))
    return {str(r.get("t") or "").strip(): str(r.get("h") or "").strip()
            for r in rows if str(r.get("t") or "").strip()}
