"""清理 kb_transfer 自测产生的测试知识库（**软删**：knowledge_bases.deleted_at = now()）。

用法：/opt/bodhi-venv/bin/python3 tools/diag/_kb_cleanup.py [--apply]
"""
import sys

sys.path.insert(0, "tools/ke-core")
import ke_db  # noqa: E402

APPLY = "--apply" in sys.argv

print("=== 全部知识库（活/软删 + 页数）===")
for r in ke_db.psql_csv(
        "SELECT kb.id, COALESCE(kb.name,'') AS name, "
        "       CASE WHEN kb.deleted_at IS NULL THEN '活' ELSE '已软删' END AS state, "
        "       (SELECT count(*) FROM wiki_pages p WHERE p.knowledge_base_id = kb.id "
        "          AND p.deleted_at IS NULL) AS pages, "
        "       kb.created_at::date AS created "
        "  FROM knowledge_bases kb ORDER BY kb.created_at"):
    print("  %-38s %-10s %-6s 页=%-5s %s" % (r["id"], r["name"][:10] or "（空名）", r["state"],
                                             r["pages"], r["created"]))

TARGETS = ["829dbb59-52c6-4a53-8a2b-589e14f2de4f",   # 迁移测试-技术方案评审领域
           "a33033c7-d114-4d57-9cab-ef383fbe3647",   # …-B
           "4cee3df6-2ec7-43a8-926c-129bc1bd8a65",   # …-D（导入失败的残库）
           "cb375482-e386-4864-a9fa-79eb797fe1db",   # …（上传导入全绿的基准）
           # 其它测试库（用户 2026-10-12 口径：清理测试库）
           "a2d414b7-e203-4a16-9e0f-e4b2a424a6d8",   # 迁移测试…（0 页）
           "a8d54d6c-d4dd-415c-90f3-d7bec0ef14eb",   # 迁移测试…（0 页）
           "dd2ee45c-d85b-4cdf-a1aa-68da67ae2096",   # 迁移测试…-F（zip 基准）
           "8a7d9aa7-6ee1-4448-ae6d-e227c3758354",   # 嘎嘎嘎
           "af656ef0-e352-4db8-9636-10652dcb95fb"]   # 测试（S1/S2 靶子，66 页）
print("\n=== 待软删（本次 kb_transfer 自测库，%d 个）===" % len(TARGETS))
for kid in TARGETS:
    rows = ke_db.psql_csv("SELECT COALESCE(name,'') AS n, deleted_at::text AS d "
                          "FROM knowledge_bases WHERE id = '%s'" % kid)
    if not rows:
        print("  %s  不存在（跳过）" % kid)
        continue
    print("  %s  %s  %s" % (kid, rows[0]["n"], "已软删" if rows[0]["d"] else "将软删"))
    if APPLY and not rows[0]["d"]:
        ke_db.psql("UPDATE knowledge_bases SET deleted_at = now(), updated_at = now() "
                   "WHERE id = '%s'" % kid)
        print("     → 已软删")
print("\n%s" % ("已应用。" if APPLY else "**dry-run**（未写库）；确认后加 --apply。"))
