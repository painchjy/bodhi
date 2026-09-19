"""把 wiki 页面的 category_path 同步成 wiki_folders 目录树。

为什么需要它（2026-09-19 实测的 bug）
------------------------------------
上游 WikiBrowser 的**目录行** `folderId` 来自
`GET /api/v1/knowledgebase/{kb}/wiki/folders`（后端读 `wiki_folders` 表）。
该表为空时每个目录行的 `folderId` 都是 `''`，而行模板的条件是

    <input v-if="editingFolderId === item.folderId" ...>   // editingFolderId 初值也是 ''

即 `'' === ''` 恒成立 → **每个目录行都渲染成「重命名输入框」**，目录名（`v-else` 里的
`wiki-directory-title`）被顶掉、目录也展不开。表现就是「前两层名称空白 + 出现要求填
目录名称的输入框」。补齐 `wiki_folders` 即可修复，**前端无需重建**。

做法
----
- 从 `wiki_pages.category_path` 取出全部前缀（模型 → 大类 → 类），逐级建目录行；
- 目录 id 用 **UUIDv5 确定性生成**（同名同父 ⇒ 同 id），重跑是 upsert，天然幂等；
- `--link-pages` 时额外把页面挂到最深一级目录（`wiki_pages.folder_id`），
  供后端的目录页数统计/拖拽使用；默认不动页面，先把显示修好。

用法：
    python tools/ontology-mcp/sync_folders.py --kb-id <UUID> [--dry-run] [--link-pages]
    python tools/ontology-mcp/sync_folders.py --all
"""

from __future__ import annotations

import argparse
import json as _json
import pathlib
import sys
import uuid

HERE = pathlib.Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import server  # noqa: E402

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            pass

NS = uuid.UUID("6f9b1c2e-2a7f-4a4e-9d1b-7f0c5a3e8d21")  # bodhi2 wiki_folders 命名空间


def folder_id(kb_id: str, path: str) -> str:
    return str(uuid.uuid5(NS, "wiki_folders:%s:%s" % (kb_id, path)))


def plan_folders(kb_id: str) -> tuple[list[dict], dict[str, str]]:
    """返回（目录行列表, {页面 slug: 最深目录 id}）。"""
    rows = server.psql_csv(
        "SELECT slug, COALESCE(category_path::text,'[]') AS cat, tenant_id "
        "FROM wiki_pages WHERE knowledge_base_id = %s AND deleted_at IS NULL "
        "AND jsonb_typeof(category_path) = 'array' AND jsonb_array_length(category_path) > 0"
        % server.sql_str(kb_id))

    dirs: dict[str, dict] = {}
    page_folder: dict[str, str] = {}
    for row in rows:
        try:
            path = _json.loads(row["cat"] or "[]")
        except Exception:  # noqa: BLE001
            continue
        parts = [str(p).strip() for p in path if str(p).strip()]
        if not parts:
            continue
        for depth in range(len(parts)):
            prefix = parts[:depth + 1]
            key = "/".join(prefix)
            dirs.setdefault(key, {
                "key": key,
                "name": prefix[-1],
                "parent": "/".join(prefix[:-1]),
                "depth": depth,
                "id": folder_id(kb_id, key),
            })
        page_folder[row["slug"]] = folder_id(kb_id, "/".join(parts))

    ordered = [dirs[k] for k in sorted(dirs)]
    return ordered, page_folder


def sync_kb(kb_id: str, dry_run: bool, link_pages: bool, prune: bool = False) -> int:
    dirs, page_folder = plan_folders(kb_id)
    if not dirs:
        print("  知识库 %s：没有带 category_path 的页面，跳过" % kb_id)
        return 0

    tenant = server.psql_csv(
        "SELECT COALESCE(max(tenant_id), 10000) AS t FROM wiki_pages "
        "WHERE knowledge_base_id = %s" % server.sql_str(kb_id))
    tenant_id = int((tenant or [{"t": 10000}])[0]["t"] or 10000)

    # 同级按出现顺序编号，保证 sort_order 可复现
    counter: dict[str, int] = {}
    statements: list[str] = []
    for d in dirs:
        order = counter.get(d["parent"], 0)
        counter[d["parent"]] = order + 1
        parent_id = "" if not d["parent"] else folder_id(kb_id, d["parent"])
        statements.append(
            "INSERT INTO wiki_folders (id, tenant_id, knowledge_base_id, parent_id, name, path, depth, sort_order) "
            "VALUES (%s, %d, %s, %s, %s, %s, %d, %d) "
            "ON CONFLICT (id) DO UPDATE SET name = EXCLUDED.name, path = EXCLUDED.path, "
            "parent_id = EXCLUDED.parent_id, depth = EXCLUDED.depth, sort_order = EXCLUDED.sort_order, "
            "deleted_at = NULL, updated_at = now();"
            % (server.sql_str(d["id"]), tenant_id, server.sql_str(kb_id), server.sql_str(parent_id),
               server.sql_str(d["name"]), server.sql_str(d["key"]), d["depth"], order))

    if link_pages:
        for slug, fid in sorted(page_folder.items()):
            statements.append(
                "UPDATE wiki_pages SET folder_id = %s, updated_at = now() "
                "WHERE knowledge_base_id = %s AND slug = %s AND COALESCE(folder_id,'') <> %s;"
                % (server.sql_str(fid), server.sql_str(kb_id), server.sql_str(slug), server.sql_str(fid)))

    pruned = 0
    if prune:
        # 结构变更（如三级 → 两级）后，旧目录会残留；按计划里的 id 白名单软删除其余目录
        keep = ", ".join(server.sql_str(d["id"]) for d in dirs)
        stale = server.psql_csv(
            "SELECT id, path FROM wiki_folders WHERE knowledge_base_id = %s AND deleted_at IS NULL "
            "AND id NOT IN (%s)" % (server.sql_str(kb_id), keep))
        for row in stale:
            statements.append(
                "UPDATE wiki_folders SET deleted_at = now(), updated_at = now() WHERE id = %s;"
                % server.sql_str(row["id"]))
            print("    - 软删除残留目录：%s" % row["path"])
        pruned = len(stale)

    print("  知识库 %s：目录 %d 行（最深 %d 级）%s"
          % (kb_id, len(dirs), max(d["depth"] for d in dirs) + 1,
             ("，并挂 %d 页到最深目录" % len(page_folder)) if link_pages else ""))

    if dry_run:
        for st in statements[:4]:
            print("    样例：%s" % st[:200])
        print("    （--dry-run，未写入）")
        return 0

    server.psql("BEGIN;\n" + "\n".join(statements) + "\nCOMMIT;\n", stdin=True)
    return len(statements)


def main() -> int:
    parser = argparse.ArgumentParser(description="同步 wiki_folders 目录树（幂等）")
    parser.add_argument("--kb-id", help="单个知识库 UUID")
    parser.add_argument("--all", action="store_true", help="处理所有含 category_path 的知识库")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--link-pages", action="store_true",
                        help="同时把页面挂到最深一级目录（wiki_pages.folder_id）")
    parser.add_argument("--prune", action="store_true",
                        help="软删除不在本次计划里的历史目录（例如结构从三级改成两级后残留的旧目录）")
    args = parser.parse_args()

    kbs: list[str] = []
    if args.all:
        rows = server.psql_csv(
            "SELECT knowledge_base_id, count(*) AS n FROM wiki_pages "
            "WHERE deleted_at IS NULL AND jsonb_typeof(category_path)='array' "
            "AND jsonb_array_length(category_path) > 0 GROUP BY 1 ORDER BY 2 DESC")
        kbs = [r["knowledge_base_id"] for r in rows]
    elif args.kb_id:
        kbs = [args.kb_id]
    else:
        parser.error("需要 --kb-id 或 --all")

    print("== 同步 wiki_folders（%d 个知识库%s%s） =="
          % (len(kbs), "，dry-run" if args.dry_run else "",
             "，prune 残留目录" if args.prune else ""))
    total = 0
    for kb in kbs:
        total += sync_kb(kb, args.dry_run, args.link_pages, args.prune)
    if args.dry_run:
        return 0
    print("== 写入完成：%d 条语句 ==" % total)
    for kb in kbs:
        rows = server.psql_csv(
            "SELECT count(*) AS n, COALESCE(max(depth),0) AS d FROM wiki_folders "
            "WHERE knowledge_base_id = %s AND deleted_at IS NULL" % server.sql_str(kb))
        print("  %s：wiki_folders 行数 %s（最深 %s 级）"
              % (kb, rows[0]["n"], int(rows[0]["d"]) + 1))
    return 0


if __name__ == "__main__":
    sys.exit(main())

