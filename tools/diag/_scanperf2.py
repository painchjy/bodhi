#!/usr/bin/env python3
"""诊断（无缓冲、窄口径）：逐步计时定位 `scan` 的慢点。"""
import json
import pathlib
import sys
import time

H = pathlib.Path(__file__).resolve().parents[1] / "ke-core"
sys.path.insert(0, str(H))
import ke_stats      # noqa: E402
import ke_context    # noqa: E402

KB = "6effde80-3ad0-46a1-92b2-76d695e0b58f"    # 企业知识管理领域（66 页）


def timed(label, fn):
    ke_stats.begin()
    t0 = time.time()
    out = None
    try:
        out = fn()
    except Exception as exc:  # noqa: BLE001
        print("%-20s ERR %s" % (label, exc), flush=True)
    dt = (time.time() - t0) * 1000.0
    d = (ke_stats.snapshot() or {}).get("detail") or {}
    pg, neo = d.get("pg") or {}, d.get("neo4j") or {}
    print("%-20s wall=%7.0fms  pg=%d次/%.0fms %s  neo4j=%d次/%.0fms" % (
        label, dt, pg.get("n", 0), pg.get("ms", 0.0),
        json.dumps(pg.get("top_callers") or [], ensure_ascii=False),
        neo.get("n", 0), neo.get("ms", 0.0)), flush=True)
    return out


timed("kb_rows()", lambda: ke_context.kb_rows())
timed("_page_counts()", lambda: ke_context._page_counts())
timed("resolve_ontology_kb", lambda: ke_context.ke_ontology.resolve_ontology_kb())
timed("contexts()", lambda: ke_context.contexts())
rows = timed("load_pages(1库)", lambda: ke_context.load_pages(KB, 5000))
print("   → 页数=%s" % (len(rows) if rows else "?"), flush=True)
