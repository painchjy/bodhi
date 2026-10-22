"""Metric computation (P3.5-3 capability front-loading).

Design goals:
  * domain-agnostic: only counts pages by type/version/source coverage;
  * recomputable: same input -> same metrics + same sha1 fingerprint;
  * auditable: every metric carries its formula string.
"""
from __future__ import annotations

import hashlib
import json

import ke_db

psql_csv = ke_db.psql_csv
sql_str = ke_db.sql_str


def compute(kb_id: str, spec: str = "", with_rules: bool = False) -> dict:
    """计算知识库的基础指标（页数/类型分布/版本/来源覆盖[/规则数]）。

    `spec` 仅在 `with_rules=True` 时用于筛规则集（同 ke_lookup 的 rules 口径）。
    """
    rows = psql_csv(
        "SELECT COALESCE(page_type, %s) AS page_type, count(*) AS n, "
        "       COALESCE(avg(COALESCE(version,1)),0) AS avg_version, "
        "       count(*) FILTER (WHERE COALESCE(source_refs::text, %s) IN (%s, %s)) AS no_source "
        "  FROM wiki_pages WHERE knowledge_base_id = %s AND deleted_at IS NULL "
        " GROUP BY 1 ORDER BY n DESC"
        % (sql_str(""), sql_str("[]"), sql_str("[]"), sql_str("null"), sql_str(kb_id)))
    total = sum(int(r["n"] or 0) for r in rows)
    no_source = sum(int(r["no_source"] or 0) for r in rows)
    avg_ver = round(sum(float(r["avg_version"] or 0) * int(r["n"] or 0) for r in rows) / total, 2) if total else 0.0
    metrics = [
        {"name": "pages_total", "value": total,
         "formula": "count(wiki_pages where kb_id=<kb> and deleted_at is null)"},
        {"name": "pages_without_source", "value": no_source,
         "formula": "count(pages where source_refs is empty)"},
        {"name": "avg_version", "value": avg_ver,
         "formula": "sum(avg(version) * n) / pages_total"},
        {"name": "page_types", "value": len(rows),
         "formula": "count(distinct page_type)"},
    ]
    rules_info = None
    if with_rules:
        try:
            import ke_lookup
            lk = ke_lookup.knowledge_lookup(kb_id, mode="rules", spec=spec)
            rules = lk.get("rules") or []
            rules_info = {"count": len(rules), "spec": spec}
            metrics.append({"name": "rules_total", "value": len(rules),
                            "formula": "count(rules resolved by spec)"})
        except Exception as exc:  # noqa: BLE001
            rules_info = {"error": str(exc)[:160], "spec": spec}
    blob = json.dumps(metrics, ensure_ascii=False, sort_keys=True)
    return {"ok": True, "kb_id": kb_id, "spec": spec, "metrics": metrics,
            "rows": rows, "rules": rules_info,
            "hash": hashlib.sha1(blob.encode("utf-8")).hexdigest(),
            "note": "同输入同结果 + 同 hash（可复算）；每个指标自带 formula 口径"}
