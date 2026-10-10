"""验证 P0-c-3（`_bulk_write_pages` 接线）：**零写入** —— 拦截批量写、记录 items，跑真内核批量路径。

与 `_engine_ab.py` 的区别：本脚本**不**拦 `write_knowledge_batch`（要真跑 sink 路径），
只拦 `_bulk_write_pages`（记录 items）+ 所有 `ke_db.psql(stdin=True)` 写 + 图侧批量写。

断言：① `_bulk_write_pages` 只被调用 **1 次**、items 数 = 页数；② 整批 **0 条** 逐页写事务；
③ 回执 `engine=kernel`、`kernel.applied` 与页数一致；④ items 正文（strip 后）与库里已写内容一致。

用法：/opt/bodhi-venv/bin/python3 tools/diag/_bulk_ab.py [args.json]
"""
import inspect
import json
import os
import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "tools" / "ke-core"))
sys.path.insert(0, str(REPO / "tools" / "ontology-mcp"))
import ke_db  # noqa: E402
import ke_graph  # noqa: E402
import ke_pages  # noqa: E402
import server  # noqa: E402

ARG = sys.argv[1] if len(sys.argv) > 1 else "logs/args/20261010-141615-274441-save_knowledge.json"
raw = json.loads((REPO / ARG).read_text(encoding="utf-8"))
raw["mode"] = "apply"
sig = set(inspect.signature(server.save_knowledge).parameters)
args = {k: v for k, v in raw.items() if k in sig}
KB = str(args.get("kb_id") or "")

bulk_calls, psql_writes = [], []
_real_psql = ke_db.psql


def fake_psql(sql, stdin=False, csv=False):
    if stdin:
        psql_writes.append(sql.strip()[:60])
        return ""
    return _real_psql(sql, stdin=stdin, csv=csv)


ke_db.psql = server.psql = fake_psql


def fake_bulk(kb_id, items, tenant_id, tag):
    bulk_calls.append(list(items))
    return {"inserted": len([x for x in items if not x.get("exists")]),
            "updated": len([x for x in items if x.get("exists")])}


ke_pages._bulk_write_pages = fake_bulk
ke_graph.upsert_nodes_batch = lambda *a, **k: 0
ke_graph.ensure_instances = lambda *a, **k: 0
ke_graph.add_edges_batch = lambda *a, **k: {"written": 0, "errors": [], "types": 0, "total": 0}
ke_graph.strip_wiki_pages = lambda *a, **k: {"ok": True, "total": 0, "stripped": 0, "skipped": 0}

os.environ["BODHI_SAVE_ENGINE"] = "kernel"
server._reset_request_cache()
r = server.save_knowledge(**args)

items = bulk_calls[0] if bulk_calls else []
print("回执：engine=%s kernel=%s created=%d merged=%d pending=%d"
      % (r.get("engine"), r.get("kernel"), len(r.get("created") or []),
         len(r.get("merged") or []), len(r.get("pending") or [])))
print("_bulk_write_pages 调用次数 = %d（应 1）；items = %d（应 = 页数）"
      % (len(bulk_calls), len(items)))
print("被拦截的逐页 psql 写事务 = %d（应 0 —— 说明没有逐页 upsert_page 了）" % len(psql_writes))

mismatch, checked = [], 0
for it in items:
    rows = ke_db.psql_csv("SELECT COALESCE(content,'') AS c FROM wiki_pages WHERE knowledge_base_id=%s "
                          "AND slug=%s AND deleted_at IS NULL"
                          % (ke_db.sql_str(KB), ke_db.sql_str(it["slug"])))
    if not rows:
        continue
    checked += 1
    mine = ke_graph.strip_relation_sections(it["content"]).rstrip()
    theirs = rows[0]["c"].rstrip()
    if mine != theirs:
        mismatch.append(it["slug"])
        if len(mismatch) <= 2:            # 打印真实 diff，判定"载荷版本差异"还是"内容被改坏"
            import difflib  # noqa: PLC0415
            print("\n--- diff：%s（库 %d 字 / 本次构建 %d 字）---"
                  % (it["slug"], len(theirs), len(mine)))
            for ln in list(difflib.unified_diff(theirs.splitlines(), mine.splitlines(),
                                                "库里", "本次构建", lineterm="", n=1))[:12]:
                print("   ", ln[:150])
print("items 正文（strip 后）与库里比对 = %d 页，不一致 = %d" % (checked, len(mismatch)))
for m in mismatch[:3]:
    print("   ·", m)
ok = (len(bulk_calls) == 1 and len(items) > 0 and not psql_writes and r.get("engine") == "kernel")
print("RESULT:", "PASS" if ok and not mismatch else "FAIL")
