"""P0-c-2 真实库 diff（**只读**）：legacy 已落库正文 vs 内核 `write_knowledge` 会生成的正文。

回答的问题：把领域建模路径换成内核后，**页面正文会不会变**（变了多少、变在哪一段）。
做法：取库里真实页 → `ke_pages.spec_from_page()` 适配成内核 spec → 直接调内核渲染
（`_contract_head` / `render_entity_content`）算出"内核会写成什么" → 与库里现有正文逐行比对；
再对每个 spec 跑一次 `write_knowledge(..., dry_run=True)` 证明内核**接受**它（dry_run 不写库）。

用法：/opt/bodhi-venv/bin/python3 _p0c2_diff.py <kb_id> [页数=6] [auto|document|entity]
"""
import difflib
import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "tools" / "ke-core"))
import ke_db  # noqa: E402
import ke_graph  # noqa: E402
import ke_pages  # noqa: E402


def _rels(kb, slug):
    try:
        rows = ke_graph.relations_of(kb, slug) or []
    except Exception:  # noqa: BLE001
        return []
    out = []
    for r in rows:
        if not isinstance(r, dict):
            continue
        t = r.get("target_slug") or r.get("t") or r.get("slug") or ""
        ty = r.get("type") or r.get("rel") or ""
        if t and ty:
            out.append({"type": ty, "target_slug": t})
    return out


def main():
    if len(sys.argv) < 2:
        print("用法：_p0c2_diff.py <kb_id> [页数] [auto|document|entity]")
        return 2
    kb = sys.argv[1].strip()
    limit = int(sys.argv[2]) if len(sys.argv) > 2 else 6
    mode_arg = sys.argv[3].strip().lower() if len(sys.argv) > 3 else "auto"
    strip_head = (len(sys.argv) > 4 and sys.argv[4].strip() in ("strip-head", "1", "true"))
    rows = ke_db.psql_csv(
        "SELECT slug, title, COALESCE(page_type,'') AS page_type, COALESCE(content,'') AS content, "
        "       COALESCE(summary,'') AS summary, COALESCE(page_metadata::text,'{}') AS meta, "
        "       COALESCE(source_refs::text,'[]') AS srefs, COALESCE(chunk_refs::text,'[]') AS cref "
        "FROM wiki_pages WHERE knowledge_base_id=%s AND deleted_at IS NULL "
        "AND COALESCE(page_type,'') NOT IN ('index') ORDER BY updated_at DESC LIMIT %d"
        % (ke_db.sql_str(kb), limit))
    if not rows:
        print("本库没有可比对的页")
        return 1
    print("比对 %d 页（mode=%s，库=%s）\n" % (len(rows), mode_arg, kb[:8]))
    ok_accept, same, changed = 0, 0, 0
    for r in rows:
        import json as _json
        page = {"slug": r["slug"], "title": r["title"], "page_type": r["page_type"],
                "content": r["content"], "summary": r["summary"],
                "page_metadata": _json.loads(r["meta"] or "{}"),
                "source_refs": _json.loads(r["srefs"] or "[]"),
                "chunk_refs": _json.loads(r["cref"] or "[]")}
        spec = ke_pages.spec_from_page(
            page, relations=_rels(kb, r["slug"]),
            mode="" if mode_arg == "auto" else mode_arg,
            strip_legacy_head=strip_head,
            source={"doc_refs": page["source_refs"], "chunk_refs": page["chunk_refs"]})
        # 内核会写成什么（直接调内核渲染，不落库）
        try:
            if spec["mode"] == "entity":
                kern = ke_pages.render_entity_content(kb, spec)
            else:
                kern = "\n".join(ke_pages._contract_head(spec)) + "\n" + (spec["wiki_content"] or "").lstrip()
        except Exception as exc:  # noqa: BLE001
            print("[%s] mode=%s **内核渲染失败**：%s" % (r["slug"][:60], spec["mode"], exc))
            continue
        try:
            res = ke_pages.write_knowledge(kb, spec, dry_run=True, strict_source=False,
                                           sync_folders=False)
            accepted = (res.get("slug") == r["slug"] and res.get("mode") == spec["mode"])
        except Exception as exc:  # noqa: BLE001
            accepted, res = False, {"error": str(exc)[:160]}
        ok_accept += 1 if accepted else 0
        d = list(difflib.unified_diff(r["content"].splitlines(), kern.splitlines(),
                                      lineterm="", n=0))
        add = sum(1 for x in d if x.startswith("+") and not x.startswith("+++"))
        rm = sum(1 for x in d if x.startswith("-") and not x.startswith("---"))
        if add == 0 and rm == 0:
            same += 1
        else:
            changed += 1
        print("[%s] type=%s mode=%-8s 正文 %d→%d 字符  差异 +%d/-%d 行  内核接受=%s"
              % (r["slug"][:52], r["page_type"], spec["mode"], len(r["content"]), len(kern),
                 add, rm, accepted))
        if (add or rm) and changed <= 2:
            for line in d[:8]:
                print("      %s" % line[:150])
    print("\n合计：正文逐字相同 %d ／ 有差异 %d ／ 内核接受 %d/%d"
          % (same, changed, ok_accept, len(rows)))
    print("RESULT:", "SAME" if changed == 0 else "DIFF", "（有差异就必须先定渲染口径再切内核）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
