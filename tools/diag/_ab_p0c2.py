"""P0-c-2 等价性验证（**只读**）：图取 `page_type` 是否与 PG 权威一致。

① 逐 slug 比对 `ke_graph.info_from_graph()` 的 pt 与 PG `ke_graph._page_type()`；
② 按图上真实的实例边，分别用「图优先」与「纯 PG」解析 `(源类型, 目标类型)`，比对是否完全一致
   （校验结果一致 ⇒ `add_edge` 的接受/拒绝不会变）；
③ 顺带给出漂移画像：图里有、PG 没有（陈旧节点）／图形有 pt 但 PG 类型不同。

用法：/opt/bodhi-venv/bin/python3 _ab_p0c2.py <kb_id>
"""
import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "tools" / "ke-core"))
import ke_db  # noqa: E402
import ke_graph  # noqa: E402


def main():
    if len(sys.argv) < 2:
        print("用法：_ab_p0c2.py <kb_id>")
        return 2
    kb = sys.argv[1].strip()
    pg_rows = ke_db.psql_csv(
        "SELECT slug, COALESCE(page_type,'') AS pt FROM wiki_pages "
        "WHERE knowledge_base_id=%s AND deleted_at IS NULL" % ke_db.sql_str(kb))
    pg = {r["slug"]: (r["pt"] or "") for r in pg_rows}
    g_rows = ke_graph._run("MATCH (n:BodhiInstance {kb_id:$kb}) "
                           "RETURN n.slug AS slug, n.page_type AS pt", {"kb": kb})
    gmap = {str(r.get("slug")): str(r.get("pt") or "") for r in (g_rows or []) if r.get("slug")}
    info = ke_graph.info_from_graph(kb, sorted(set(pg) | set(gmap)))

    print("PG 活页 %d ／ 图节点 %d ／ 图里有类型 %d" % (len(pg), len(gmap), len(info)))
    same = diff = stale = 0
    bad = []
    for slug in sorted(set(pg) | set(gmap)):
        gp = (info.get(slug) or {}).get("pt") or ""
        pp = pg.get(slug)
        if not gp:                                    # 图里没有 → 回落 PG，必然一致
            continue
        if pp is None:
            stale += 1
            bad.append((slug, "图有PG无(PG里已删/未投影)", gp, None))
        elif gp == pp:
            same += 1
        else:
            diff += 1
            bad.append((slug, "类型不一致", gp, pp))
    print("① 图取的 pt 与 PG：一致 %d ／ 不一致 %d ／ 图有 PG 无 %d" % (same, diff, stale))
    for item in bad[:5]:
        print("   ·", item)

    # ② 用真实边比对"图优先"与"纯 PG"解析出的 (st, tt)
    trows = ke_graph._run(
        "MATCH (a:BodhiInstance {kb_id:$kb})-[r]->(b:BodhiInstance {kb_id:$kb}) "
        "RETURN DISTINCT a.slug AS s, b.slug AS t LIMIT 40", {"kb": kb})
    pairs = [(r.get("s"), r.get("t")) for r in (trows or []) if r.get("s") and r.get("t")]
    mismatch = []
    for s, t in pairs:
        g_tt = (info.get(t) or {}).get("pt") or ke_graph._page_type(kb, t)
        p_tt = ke_graph._page_type(kb, t)
        g_st = (info.get(s) or {}).get("pt") or ke_graph._page_type(kb, s)
        p_st = ke_graph._page_type(kb, s)
        if (g_st, g_tt) != (p_st, p_tt):
            mismatch.append((s, t, (g_st, g_tt), (p_st, p_tt)))
    print("② 边解析：样例 %d 对，图优先与纯 PG 不一致 %d 对" % (len(pairs), len(mismatch)))
    for item in mismatch[:5]:
        print("   ·", item)
    ok = not mismatch and not diff
    print("RESULT:", "PASS" if ok else "FAIL", "（不一致 %d，陈旧 %d）" % (diff, stale))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
