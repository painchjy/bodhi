#!/usr/bin/env python3
"""回归：**图 → PG** 写序（契约 §8）

覆盖：
  · `upsert_page` 建页 ⇒ 图节点**立即存在**（不是事后补 ✓）
  · `upsert_page` 更新 ⇒ 图 `wiki_content` / 属性 与 PG 一致 ✓
  · 清理（图节点 + PG 行），不留痕 ✓

跑法：`/opt/bodhi-venv/bin/python3 tools/ke-core/tests/test_graph_first_write.py`
退出码 0 = 全绿 ✓
"""
import sys

sys.path.insert(0, "/mnt/c/Users/PHJY/source/bodhi2/tools/ke-core")
import ke_db  # noqa: E402
import ke_graph  # noqa: E402
import ke_neo4j  # noqa: E402
import ke_pages  # noqa: E402

KB = "技术方案评审任务知识库"
SLUG = "review/_test-graph-first"
A = "# 测试 A\n\n第一版正文（较短）。\n"
B = "# 测试 B\n\n第二版正文，更长一些，用于验证 update 也是**图先 PG 后** ✓。\n"


def _node(kb: str) -> dict:
    r = ke_neo4j.query(
        "MATCH (n:BodhiInstance {kb_id:$kb, slug:$s}) "
        "RETURN size(coalesce(n.wiki_content,'')) AS n, n.definition AS d",
        {"kb": kb, "s": SLUG}, timeout=8)
    return (r[0] if r else {}) or {}


def main() -> int:
    kb = ke_db.resolve_kb_id(KB)[0]
    checks = {}
    try:
        r1 = ke_pages.upsert_page(kb, SLUG, "测试：图先写后建页", "bmm:Assessment", A,
                                  metadata={"ontology": {"attributes": {"skos:definition": "测试页 v1"}}},
                                  tag="test-gf")
        checks["建页返回 graph.ok"] = bool((r1.get("graph") or {}).get("ok"))
        checks["建页后图节点存在"] = bool(_node(kb).get("d") == "测试页 v1")

        r2 = ke_pages.upsert_page(kb, SLUG, "测试：图先写后建页", "bmm:Assessment", B,
                                  metadata={"ontology": {"attributes": {"skos:definition": "测试页 v2"}}},
                                  tag="test-gf")
        pg = ke_db.psql_csv("SELECT length(COALESCE(content,'')) AS n, COALESCE(version,1) AS v "
                            " FROM wiki_pages WHERE knowledge_base_id = %s AND slug = %s"
                            % (ke_db.sql_str(kb), ke_db.sql_str(SLUG)))
        nd = _node(kb)
        checks["更新后版本=2"] = int((pg[0] or {}).get("v") or 0) == 2
        checks["更新后图/ PG 内容等长"] = int((pg[0] or {}).get("n") or -1) == int(nd.get("n") or -2)
        checks["更新后图属性同步"] = nd.get("d") == "测试页 v2"
        checks["更新返回无 created"] = r2.get("created") is False
    finally:
        try:
            ke_graph.delete_page(kb, SLUG)
        except Exception:  # noqa: BLE001
            pass
        ke_db.psql("DELETE FROM wiki_pages WHERE knowledge_base_id = %s AND slug = %s;"
                   % (ke_db.sql_str(kb), ke_db.sql_str(SLUG)), stdin=True)

    for k, v in checks.items():
        print("  %-22s = %s" % (k, v))
    ok = all(checks.values())
    print("合计：%s" % ("全绿 ✓" if ok else "有失败 ✗"))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
