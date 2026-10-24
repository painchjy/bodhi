"""ke_core · 规则副本同步（工作库 ← 领域库 ✓）

**为什么需要副本**：`ke_graph.add_edge` 只认同库目标 ✗（跨库边建不了），而核对要表达
「报告 → 规则」「评测 → 规则」两类关系 ⇒ 规则页必须在**工作库**有一份副本 ✓。

**责任方（用户口径 2026-10-19 ✓）**：按**关系**定副本，谁产出该关系，谁负责同步副本 ——
| 关系 | 副本 | 责任任务步骤 |
|---|---|---|
| `agent:reportAppliesRule`（报告→规则） | 规则 | **成批核对 / 生成报告**（写报告前同步 ✓） |
| `agent:evaluationOfRule`（评测→规则） | 规则 | **规则评测**（台账生成时校验副本存在 ✓） |
| `agent:reportKnowledgeIngest`（报告→入库清单） | **不需要**（清单实例直接建在工作库 ✓） | 入库任务 |
| `evaluationUsesMetric`（评测→指标） | 指标 | 规则评测（用到指标时 ✓，暂未启用） |

副本口径：**slug / 类型 / 正文保持一致** ✓，元数据标 `replica_of`（正本库与 slug ✓），
标题加「（副本）」便于人眼区分 ✓；幂等（同 slug 覆盖、版本+1 ✓）。

CLI：
  python3 ke_rule_copy.py [--spec policy:...] [--titles A,B] [--domain-kb …] [--work-kb …] [--dry-run]
"""
from __future__ import annotations

import argparse
import json

MARK = "（副本）"


def sync_rules(work_kb: str, domain_kb: str, titles: list | None = None,
               spec: str = "", dry_run: bool = False) -> dict:
    import ke_db
    import ke_lookup
    import ke_pages

    names = [t.strip() for t in (titles or []) if str(t).strip()]
    if not names and spec:
        lk = ke_lookup.knowledge_lookup(domain_kb, mode="rules", spec=spec)
        names = [str(r.get("title") or "").strip() for r in (lk.get("rules") or []) if r.get("title")]
    if not names:
        return {"ok": False, "error": "没有要同步的规则（titles/spec 都空 ✗）"}

    out = {"ok": True, "requested": len(names), "copied": [], "skipped": [], "missing": []}
    for name in names:
        rows = ke_db.psql_csv(
            "SELECT slug, title, page_type, COALESCE(content,'') AS content, "
            "       COALESCE(page_metadata::text,'{}') AS meta FROM wiki_pages "
            " WHERE knowledge_base_id = %s AND deleted_at IS NULL "
            " AND page_type IN ('bmm:OperativeBusinessRule','bmm:StructuralBusinessRule') "
            " AND title = %s LIMIT 1" % (ke_db.sql_str(domain_kb), ke_db.sql_str(name)))
        if not rows:
            out["missing"].append(name)
            continue
        src = rows[0]
        if dry_run:
            out["copied"].append({"slug": src["slug"], "dry_run": True})
            continue
        try:
            meta = json.loads(src["meta"] or "{}")
        except Exception:  # noqa: BLE001
            meta = {}
        ont = meta.get("ontology") or {}
        payload = {"ontology": {k: v for k, v in ont.items() if k in ("attributes", "comment", "label")}}
        payload["replica_of"] = {"kb_id": domain_kb, "slug": src["slug"],
                                 "note": "副本：正本在领域库 ✓；本副本由核对/评测任务按需同步 ✓（勿单独编辑 ✗）"}
        title = src["title"] + MARK if not str(src["title"]).endswith(MARK) else src["title"]
        try:
            r = ke_pages.upsert_page(work_kb, src["slug"], title, src["page_type"], src["content"],
                                     metadata=payload, tag="rule-replica")
            out["copied"].append({"slug": src["slug"], "version": r.get("after_version")})
        except Exception as exc:  # noqa: BLE001
            out["skipped"].append({"slug": src["slug"], "error": str(exc)[:130]})
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description="规则副本同步（工作库 ← 领域库）")
    ap.add_argument("--domain-kb", default="")
    ap.add_argument("--work-kb", default="")
    ap.add_argument("--spec", default="")
    ap.add_argument("--titles", default="", help="逗号分隔；给了就只同步这些 ✓")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    import ke_db

    dk = ke_db.resolve_kb_id(a.domain_kb)[0]
    wk = ke_db.resolve_kb_id(a.work_kb)[0]
    titles = [x.strip() for x in a.titles.split(",") if x.strip()] if a.titles else []
    print(json.dumps(sync_rules(wk, dk, titles, a.spec if not titles else "", dry_run=a.dry_run),
                     ensure_ascii=False)[:1800])


if __name__ == "__main__":
    main()
