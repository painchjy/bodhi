"""按 B+C 口径给智能体补工具（**定向最小写**：只改 config.allowed_tools，不动别的字段）。

  · `bodhi-kb-ops`（运维）：+ `skills`（技能目录/指令）+ `kb_export` + `kb_import`
  · `bodhi-ea-modeler` / `bodhi-skill-dev`：+ `kb_export` + `kb_import`（它们本来就有 skills）

用法：/opt/bodhi-venv/bin/python3 tools/diag/_agents_fix.py [--apply]
"""
import json
import sys

sys.path.insert(0, "tools/ke-core")
import ke_db  # noqa: E402

APPLY = "--apply" in sys.argv
ADD = {
    "bodhi-kb-ops": ["mcp_bodhi_ontology_skills",
                     "mcp_bodhi_ontology_kb_export",
                     "mcp_bodhi_ontology_kb_import"],
    "bodhi-ea-modeler": ["mcp_bodhi_ontology_kb_export",
                         "mcp_bodhi_ontology_kb_import"],
    "bodhi-skill-dev": ["mcp_bodhi_ontology_kb_export",
                        "mcp_bodhi_ontology_kb_import"],
}

for aid, names in ADD.items():
    rows = ke_db.psql_csv("SELECT config->'allowed_tools' AS t FROM custom_agents WHERE id = '%s'" % aid)
    if not rows:
        print("!! 找不到智能体 %s" % aid)
        continue
    cur = json.loads(rows[0]["t"] or "[]")
    add = [n for n in names if n not in cur]
    new = cur + add
    print("%-18s %d → %d   新增：%s" % (aid, len(cur), len(new), add or "（无）"))
    if APPLY and add:
        lit = json.dumps(new, ensure_ascii=False).replace("'", "''")
        ke_db.psql("UPDATE custom_agents SET config = jsonb_set(config, '{allowed_tools}', '%s'::jsonb), "
                   "updated_at = now() WHERE id = '%s'" % (lit, aid))
        print("   → 已写入")
print("\n%s" % ("已应用。" if APPLY else "**dry-run**（未写库）；确认后加 --apply。"))
