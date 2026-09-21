"""交付用导出：把"本体模型知识库"与"智能体/MCP 注册"导成**可直接导入**的种子。

导出物（默认写到 `--out`）：
    seed/ontology_kb_pages.sql   本体模型库全部页面（kb id 用占位符 `__ONTOLOGY_KB_ID__`）
    seed/import_ontology_kb.sh   一键：替换占位符 → 导入 → 重建目录（ke_pages.sync_folders）
    sql/agents.sql               bodhi 智能体（克隆内置智能体 config 再覆盖；可回滚）
    sql/mcp_service.sql          mcp_services 一行（URL 占位符 `__MCP_URL__`）
    sql/ROLLBACK.sql             回滚

用法：
    python3 tools/delivery/export_db.py --ontology-kb 08810cbd-… --agents --out /tmp/deliv
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "ke-core"))
import ke_db  # noqa: E402

KB_PLACEHOLDER = "__ONTOLOGY_KB_ID__"
MCP_URL_PLACEHOLDER = "__MCP_URL__"
PAGE_COLUMNS = (
    "id, tenant_id, knowledge_base_id, slug, title, page_type, status, content, summary, "
    "parent_slug, folder_id, category_path, wiki_path, depth, sort_order, source_refs, "
    "chunk_refs, in_links, out_links, page_metadata, aliases, version, created_at, updated_at, "
    "last_edit_source, last_editor_id"
)
JSON_FIELDS = ("category_path", "source_refs", "chunk_refs", "in_links", "out_links",
               "page_metadata", "aliases")


def sql_lit(value) -> str:
    if value is None:
        return "NULL"
    return "'" + str(value).replace("'", "''") + "'"


def json_lit(raw) -> str:
    try:
        obj = json.loads(raw) if raw else {}
    except Exception:  # noqa: BLE001
        obj = {}
    return "'%s'::jsonb" % json.dumps(obj, ensure_ascii=False).replace("'", "''")


def export_ontology_kb(kb_id: str, out: pathlib.Path) -> dict:
    rows = ke_db.psql_csv(
        "SELECT %s FROM wiki_pages WHERE knowledge_base_id = %s AND deleted_at IS NULL "
        "ORDER BY slug" % (PAGE_COLUMNS, sql_lit(kb_id)))
    if not rows:
        raise SystemExit("该知识库没有活页：%s" % kb_id)
    types = {}
    for r in rows:
        types[r["page_type"]] = types.get(r["page_type"], 0) + 1
    seed = out / "seed"
    seed.mkdir(parents=True, exist_ok=True)
    lines = ["-- 本体模型知识库种子（%d 页）；导入前把 %s 换成你的知识库 uuid" % (len(rows), KB_PLACEHOLDER),
             "-- 生成：python3 tools/delivery/export_db.py --ontology-kb <uuid>", "BEGIN;"]
    for r in rows:
        vals = [sql_lit(r["id"]),
                "(SELECT tenant_id FROM knowledge_bases WHERE id = %s)" % sql_lit(KB_PLACEHOLDER),
                sql_lit(KB_PLACEHOLDER)]
        for col in PAGE_COLUMNS.split(", ")[3:]:
            raw = r.get(col)
            if col in JSON_FIELDS:
                vals.append(json_lit(raw))
            elif col in ("depth", "sort_order", "version"):
                vals.append(str(int(raw or 0)))
            elif col in ("created_at", "updated_at"):
                vals.append(sql_lit(raw) if raw else "now()")
            else:
                vals.append(sql_lit(raw))
        lines.append("INSERT INTO wiki_pages (%s) VALUES (%s) ON CONFLICT (id) DO NOTHING;"
                     % (PAGE_COLUMNS, ", ".join(vals)))
    lines += ["COMMIT;", ""]
    (seed / "ontology_kb_pages.sql").write_text("\n".join(lines), encoding="utf-8")
    print("  本体库种子：%d 页 %s → %s" % (len(rows), types, seed / "ontology_kb_pages.sql"))
    return {"pages": len(rows), "types": types}


IMPORT_SH = """#!/usr/bin/env bash
# 导入本体模型知识库种子：先在 UI 建一个空知识库，把它的 uuid 填进 ONT_KB
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
ONT_KB="${ONT_KB:?用法: ONT_KB=<本体模型知识库 uuid> bash import_ontology_kb.sh}"
PSQL_URL="${PSQL_URL:?用法: PSQL_URL=postgresql://postgres:口令@127.0.0.1:5432/WeKnora bash import_ontology_kb.sh}"
sed "s/__ONTOLOGY_KB_ID__/$ONT_KB/g" "$HERE/ontology_kb_pages.sql" > /tmp/ont_kb.sql
psql "$PSQL_URL" -v ON_ERROR_STOP=1 -f /tmp/ont_kb.sql
# 重建目录树（wiki_folders）与页挂载
cd "${BODHI_DIR:-/opt/bodhi2}"
BODHI_DB_HOST="${BODHI_DB_HOST:-127.0.0.1}" python3 - <<PY
import sys; sys.path.insert(0, 'tools/ke-core'); import ke_pages
print('sync_folders →', ke_pages.sync_folders('$ONT_KB'))
PY
echo "== 导入完成（期望 248 页：类 52 / 关系 80 / 属性 107 / 模块 6 / 轻量版 2 / 索引 1）"
"""

AGENTS = ("bodhi-ea-modeler", "bodhi-kb-ops")


def export_agents(out: pathlib.Path) -> dict:
    sql_dir = out / "sql"
    sql_dir.mkdir(parents=True, exist_ok=True)
    parts, rollback, done = [], [], []
    for agent_id in AGENTS:
        rows = ke_db.psql_csv("SELECT id, name, description, config::text AS config "
                              "FROM custom_agents WHERE id = %s" % sql_lit(agent_id))
        if not rows:
            print("  （跳过 %s：库里没有）" % agent_id)
            continue
        row = rows[0]
        cfg = json.loads(row["config"] or "{}")
        cfg["knowledge_base_ids"] = ["__BIZ_KB_ID__", "__ONTOLOGY_KB_ID__"]
        cfg["mcp_services"] = ["__MCP_SERVICE_ID__"]
        cfg["mcp_selection_mode"] = "all"
        parts.append("""-- %s（%s）
DELETE FROM custom_agents WHERE id = %s;
INSERT INTO custom_agents (id, name, description, avatar, is_builtin, tenant_id, created_by,
                           config, created_at, updated_at, runnable_by_viewer)
SELECT %s, %s, %s, '', false, t.tenant_id, COALESCE(t.created_by, ''),
       (t.config || %s::jsonb), now(), now(), true
FROM (SELECT * FROM custom_agents WHERE is_builtin = true ORDER BY created_at LIMIT 1) t;
""" % (row["name"], agent_id, sql_lit(agent_id), sql_lit(agent_id), sql_lit(row["name"]),
       sql_lit(row["description"] or ""), sql_lit(json.dumps(cfg, ensure_ascii=False))))
        rollback.append("UPDATE custom_agents SET deleted_at = now() WHERE id = %s;  -- 停用 %s"
                        % (sql_lit(agent_id), row["name"]))
        done.append(agent_id)
    (sql_dir / "agents.sql").write_text("\n".join(parts), encoding="utf-8")
    (sql_dir / "mcp_service.sql").write_text(
        "-- bodhi2 MCP 服务登记（URL 用容器 DNS 可绕开 SSRF 白名单）\n"
        "INSERT INTO mcp_services (id, tenant_id, name, description, enabled, transport_type, url,\n"
        "                          created_at, updated_at)\n"
        "SELECT 'a7c1f0d2-1b2e-4f3a-9c4d-b0d100000001', t.tenant_id, 'bodhi_ontology',\n"
        "       '本体知识保存工具（抽取/设计落库、巡检、技能）', true, 'streamable_http',\n"
        "       %s, now(), now()\n"
        "FROM tenants t ORDER BY t.id LIMIT 1\n"
        "ON CONFLICT (id) DO UPDATE SET url = EXCLUDED.url, enabled = true, updated_at = now();\n"
        % sql_lit(MCP_URL_PLACEHOLDER), encoding="utf-8")
    (sql_dir / "ROLLBACK.sql").write_text(
        "UPDATE custom_agents SET deleted_at = now() WHERE id IN (%s);\n"
        "DELETE FROM mcp_services WHERE id = 'a7c1f0d2-1b2e-4f3a-9c4d-b0d100000001';\n"
        % ", ".join(sql_lit(a) for a in done), encoding="utf-8")
    print("  智能体/MCP SQL → %s（%s）" % (sql_dir, done))
    return {"agents": done}


def main() -> int:
    ap = argparse.ArgumentParser(description="导出交付种子（本体库页面 / 智能体 / MCP 登记）")
    ap.add_argument("--ontology-kb", default="", help="本体模型知识库 uuid（导出页面种子）")
    ap.add_argument("--agents", action="store_true", help="导出智能体与 MCP 登记 SQL")
    ap.add_argument("--out", required=True, help="输出目录")
    args = ap.parse_args()
    out = pathlib.Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    if args.ontology_kb:
        export_ontology_kb(ke_db.resolve_kb_id(args.ontology_kb)[0], out)
        (out / "seed" / "import_ontology_kb.sh").write_text(IMPORT_SH, encoding="utf-8")
    if args.agents:
        export_agents(out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
