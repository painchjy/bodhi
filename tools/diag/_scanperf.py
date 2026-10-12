#!/usr/bin/env python3
"""诊断 `context scan` 慢在哪：分段 wall 时间 + PG/Neo4j 调用次数与调用者归因。"""
import json
import pathlib
import sys
import time

H = pathlib.Path(__file__).resolve().parents[1] / "ke-core"
sys.path.insert(0, str(H))
import ke_stats       # noqa: E402
import ke_context     # noqa: E402

KBS = ["6effde80-3ad0-46a1-92b2-76d695e0b58f",
       "7efad3eb-762a-4981-9164-cd3b3a226bd4",
       "12ccea38-f388-432c-8683-e0201a15dcaa",
       "a7f31b49-3906-4df8-bc46-096370615371"]


def timed(label, fn):
    ke_stats.begin()
    t0 = time.time()
    out = None
    try:
        out = fn()
    except Exception as exc:  # noqa: BLE001
        print("%-18s ERR %s" % (label, exc))
    wall = (time.time() - t0) * 1000.0
    snap = ke_stats.snapshot() or {}
    pg = (snap.get("detail") or {}).get("pg") or {}
    neo = (snap.get("detail") or {}).get("neo4j") or {}
    print("%-18s wall=%7.0fms  pg=%s次/%.0fms %s  neo4j=%s次/%.0fms"
          % (label, wall, pg.get("n", 0), pg.get("ms", 0.0),
             json.dumps(pg.get("top_callers") or [], ensure_ascii=False),
             neo.get("n", 0), neo.get("ms", 0.0)))
    return out


timed("contexts()", lambda: ke_context.contexts())
timed("load_pages ×4", lambda: [ke_context.load_pages(k, 5000) for k in KBS])
timed("resolve_ontology", lambda: ke_context.ke_ontology.resolve_ontology_kb())
r = timed("scan(write=False)", lambda: ke_context.scan(KBS, limit=200, page_limit=5000, write=False))
if isinstance(r, dict):
    print("scan stats=%s" % json.dumps(r.get("stats"), ensure_ascii=False))
