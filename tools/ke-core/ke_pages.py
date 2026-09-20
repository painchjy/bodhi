"""ke-core · wiki 页面维护（本体关系增删改 / 类型修改 / 批量软删除）。

对应用户 2026-09-19 的三条需求
------------------------------
- 需求 1：wiki 编辑页可改**本体类型**（`set_page_type`，重算 category_path 并重建目录）。
- 需求 2：**Bodhi 图谱的关系维护**（`add_relation` / `update_relation` / `delete_relation`）：
  * 只允许用「本页类（含父类继承）的 domain 对象属性」建边；
  * 目标页必须落在该对象属性 `range` 的**子类闭包**内；
  * 出边（本页正文 `## 本体关系` 小节）只能由本页改；反向边（别的页指向本页）
    在**对方页面**里改，本页只读展示 —— 与 in_links 的语义一致。
- 需求 3（§5.3）：多选批量软删除（`soft_delete_pages`，永不动 `index`）。

写库纪律（沿用 `server.py` 已验证的约定）
---------------------------------------
- 先写 `wiki_page_revisions` 快照（version 用旧值）→ 再 `version = version + 1`，可回退；
- `last_edit_source` 是 varchar(16)：本模块用 `bodhi-rel-edit`(13) / `bodhi-type-edit`(15) / `bodhi-page-del`(13)；
- `out_links` / `in_links` 是 jsonb 数组；上游可能有**标量**脏值 → 一律先 `jsonb_typeof` 守卫；
- slug 派生主键是确定性 UUIDv5，本模块只做 UPDATE，不 INSERT，避免撞软删除旧页。
"""

from __future__ import annotations

import pathlib
import re
import sys

HERE = pathlib.Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import ke_db  # noqa: E402
import ke_ontology  # noqa: E402

REL_SECTION = "## 本体关系"
TAG_REL = "bodhi-rel-edit"     # 13 字符
TAG_TYPE = "bodhi-type-edit"   # 15 字符
TAG_DEL = "bodhi-page-del"     # 13 字符
assert max(len(TAG_REL), len(TAG_TYPE), len(TAG_DEL)) <= 16

# 「本体关系」小节的行格式（与 server.py / relink_pages.py 保持一致）
REL_LINE = re.compile(r"^- (?P<label>.+?)（`(?P<type>[^`]+)`）→ \[(?P<target>.+?)\]\(wiki:(?P<slug>[^)]+)\)\s*$")
REL_LINE_V2 = re.compile(r"^- (?P<label>.+?)（`(?P<type>[^`]+)`）→ \[\[(?P<slug>[^|\]]+)\|(?P<target>[^\]]+)\]\]\s*$")


def parse_rel_line(line: str):
    """解析「本体关系」小节的一行，兼容新旧两种链接格式。"""
    return REL_LINE.match(line) or REL_LINE_V2.match(line)


def rel_line(label: str, rel_type: str, target: str, slug: str) -> str:
    """生成「本体关系」小节的一行（统一 [[slug|正文]] 站内链接格式）。"""
    if slug:
        return "- %s（`%s`）→ [[%s|%s]]" % (label, rel_type, slug, target)
    return "- %s（`%s`）→ %s" % (label, rel_type, target)


def _load_page(kb_id: str, slug: str) -> dict:
    rows = ke_db.psql_csv(
        "SELECT id, slug, title, COALESCE(page_type,'') AS page_type, COALESCE(content,'') AS content, "
        "       COALESCE(version,1) AS version, COALESCE(in_links::text,'[]') AS in_links, "
        # page_metadata 必须带上：set_page_type 会**合并**它（丢了就会把 ontology.name /
        # created_at / 合并历史等键清空）。
        "       COALESCE(page_metadata::text,'{}') AS page_metadata, "
        "       COALESCE(last_editor_id,'') AS editor "
        "FROM wiki_pages WHERE knowledge_base_id = %s AND slug = %s AND deleted_at IS NULL"
        % (ke_db.sql_str(kb_id), ke_db.sql_str(slug)))
    if not rows:
        raise ValueError("页面不存在或已删除：%s" % slug)
    row = dict(rows[0])
    row["version"] = int(row.get("version") or 1)
    return row


def _section_span(lines: list[str]) -> tuple[int, int]:
    """返回「## 本体关系」小节的 (小节标题行, 结束行=下一个 ## 或文件尾)。"""
    start = next((i for i, line in enumerate(lines) if line.strip() == REL_SECTION), -1)
    if start < 0:
        return -1, -1
    end = next((i for i in range(start + 1, len(lines)) if lines[i].startswith("## ")), len(lines))
    return start, end


def parse_out_relations(content: str) -> list[dict]:
    """解析正文里的出边（保持行序，便于原地替换/删除）。"""
    lines = (content or "").splitlines()
    start, end = _section_span(lines)
    if start < 0:
        return []
    out = []
    for idx in range(start + 1, end):
        hit = parse_rel_line(lines[idx].strip())
        if hit:
            out.append({"line_index": idx, "label": hit.group("label"), "type": hit.group("type"),
                        "slug": hit.group("slug"), "target": hit.group("target"),
                        "raw": lines[idx]})
    return out


def out_links_of(content: str) -> list[str]:
    return sorted({r["slug"] for r in parse_out_relations(content) if r["slug"]})


def rebuild_in_links_sql(kb_id: str) -> str:
    """按 out_links 重算 in_links（wiki 图谱的反向边；上游页面可能写标量 → 守卫）。"""
    arr = "(CASE WHEN jsonb_typeof(s.out_links) = 'array' THEN s.out_links ELSE '[]'::jsonb END)"
    return ("UPDATE wiki_pages SET in_links = '[]'::jsonb "
            " WHERE knowledge_base_id = %s AND deleted_at IS NULL; "
            "WITH edges AS (SELECT s.slug AS src, t.value AS dst FROM wiki_pages s "
            "CROSS JOIN LATERAL jsonb_array_elements_text(%s) t "
            "WHERE s.knowledge_base_id = %s AND s.deleted_at IS NULL), "
            "inbound AS (SELECT dst, jsonb_agg(DISTINCT src) AS arr FROM edges GROUP BY dst) "
            "UPDATE wiki_pages p SET in_links = COALESCE(i.arr, '[]'::jsonb) "
            "FROM wiki_pages x LEFT JOIN inbound i ON i.dst = x.slug "
            "WHERE p.knowledge_base_id = %s AND p.slug = x.slug AND p.deleted_at IS NULL;\n"
            % (ke_db.sql_str(kb_id), arr, ke_db.sql_str(kb_id), ke_db.sql_str(kb_id)))


def _snapshot_stmt(kb_id: str, slug: str, tag: str) -> str:
    """把当前版本快照进 wiki_page_revisions（沿用 server.py 的合并语义）。"""
    return ("INSERT INTO wiki_page_revisions (id, tenant_id, knowledge_base_id, page_id, slug, version, "
            "       title, page_type, status, content, summary, aliases, edit_source, editor_id, "
            "       edited_at, created_at)\n"
            "SELECT gen_random_uuid()::text, tenant_id, knowledge_base_id, id, slug, version, "
            "       title, page_type, status, content, summary, aliases, '%s', "
            "       COALESCE(last_editor_id,''), now(), now()\n"
            "  FROM wiki_pages WHERE knowledge_base_id = %s AND slug = %s AND deleted_at IS NULL;"
            % (tag, ke_db.sql_str(kb_id), ke_db.sql_str(slug)))


def _apply_content_update(kb_id: str, slug: str, content: str, tag: str,
                          extra_set: str = "") -> str:
    """快照 + 更新正文/out_links/version+1 + 重算 in_links，返回一条可执行 SQL 批。"""
    stmts = [_snapshot_stmt(kb_id, slug, tag),
             "UPDATE wiki_pages SET content = %s, out_links = %s::jsonb, "
             "version = version + 1, updated_at = now(), last_edit_source = '%s'%s "
             "WHERE knowledge_base_id = %s AND slug = %s AND deleted_at IS NULL;"
             % (ke_db.sql_str(content),
                ke_db.sql_json(out_links_of(content)), tag, extra_set,
                ke_db.sql_str(kb_id), ke_db.sql_str(slug)),
             rebuild_in_links_sql(kb_id)]
    ke_db.psql("BEGIN;\n" + "\n".join(stmts) + "\nCOMMIT;\n", stdin=True)
    return content


# ---------------------------------------------------------------------------
# 读数：出边 + 入边
# ---------------------------------------------------------------------------
def page_relations(kb_id: str, slug: str) -> dict:
    """本页的**出边**（可维护）与**入边**（只读，需去对方页面改）。"""
    page = _load_page(kb_id, slug)
    out_lines = parse_out_relations(page["content"])
    meta = ke_ontology.class_meta()

    out = [{"label": r["label"], "type": r["type"],
            "type_label": (meta.get(r["type"]) or {}).get("label") or r["type"],
            "target_slug": r["slug"], "target_title": r["target"]} for r in out_lines]
    if out:
        slugs = ", ".join(ke_db.sql_str(x["target_slug"]) for x in out)
        found = {r["slug"]: r for r in ke_db.psql_csv(
            "SELECT slug, title, page_type FROM wiki_pages WHERE knowledge_base_id = %s "
            "AND deleted_at IS NULL AND slug IN (%s)" % (ke_db.sql_str(kb_id), slugs))}
        for item in out:
            hit = found.get(item["target_slug"]) or {}
            item["target_title"] = hit.get("title") or item["target_title"]
            item["target_type"] = hit.get("page_type") or ""
            item["target_exists"] = bool(hit)
            item["range_ok"] = True  # 出边已落库，历史数据可能超出当前 range，不拦
    else:
        pass

    inbound = []
    try:
        import json as _json
        links = _json.loads(page.get("in_links") or "[]")
        if isinstance(links, list) and links:
            cond = ", ".join(ke_db.sql_str(str(s)) for s in links)
            for row in ke_db.psql_csv(
                    "SELECT slug, title, COALESCE(page_type,'') AS page_type, COALESCE(content,'') AS content "
                    "FROM wiki_pages WHERE knowledge_base_id = %s AND deleted_at IS NULL AND slug IN (%s)"
                    % (ke_db.sql_str(kb_id), cond)):
                for rel in parse_out_relations(row["content"]):
                    if rel["slug"] != slug:
                        continue
                    inbound.append({"source_slug": row["slug"], "source_title": row["title"],
                                    "source_type": row["page_type"], "label": rel["label"],
                                    "type": rel["type"],
                                    "type_label": (meta.get(rel["type"]) or {}).get("label") or rel["type"]})
    except Exception:  # noqa: BLE001  （旧数据 in_links 为标量等）
        inbound = []

    return {"slug": slug, "title": page["title"], "page_type": page["page_type"],
            "type_label": (meta.get(page["page_type"]) or {}).get("label") or page["page_type"],
            "version": page["version"], "out": out, "in": inbound,
            "ontology_source": ke_ontology.classes()["source"]}


# ---------------------------------------------------------------------------
# 关系维护：新增 / 修改 / 删除（只动本页出边）
# ---------------------------------------------------------------------------
def _allowed(page_type: str) -> dict[str, dict]:
    return ke_ontology.relation_type_map(page_type)


def _require_type(allowed: dict[str, dict], rel_type: str, page_type: str) -> dict:
    if rel_type not in allowed:
        raise ValueError(
            "关系类型 `%s` 不适用于 %s（该类型可用：%s）"
            % (rel_type, page_type, "、".join(sorted(allowed)[:12]) or "无"))
    return allowed[rel_type]


def _with_line_inserted(content: str, new_line: str) -> str:
    lines = (content or "").splitlines()
    start, end = _section_span(lines)
    if start < 0:
        lines += ["", REL_SECTION, "", new_line, ""]
        return "\n".join(lines).rstrip() + "\n"
    insert_at = end
    while insert_at > start + 1 and not lines[insert_at - 1].strip():
        insert_at -= 1
    lines.insert(insert_at, new_line)
    return "\n".join(lines).rstrip() + "\n"


def add_relation(kb_id: str, slug: str, rel_type: str, target_slug: str,
                 label: str = "") -> dict:
    """新增一条出边：本页 --rel_type--> target_slug。"""
    page = _load_page(kb_id, slug)
    info = _require_type(_allowed(page["page_type"]), rel_type, page["page_type"])
    if target_slug == slug:
        raise ValueError("不能把关系指向本页")
    target = _load_page(kb_id, target_slug)
    closure = ke_ontology.target_closure(rel_type)
    if closure and target["page_type"] not in closure:
        raise ValueError("目标页类型 %s 不在 `%s` 的 range 范围内（%s）"
                         % (target["page_type"], rel_type, "、".join(closure[:10])))
    existing = parse_out_relations(page["content"])
    if any(r["type"] == rel_type and r["slug"] == target_slug for r in existing):
        return {"changed": False, "reason": "同样的关系已存在", "slug": slug,
                "version": page["version"]}
    content = _with_line_inserted(page["content"],
                                  rel_line(label or info["label"], rel_type, target["title"], target_slug))
    _apply_content_update(kb_id, slug, content, TAG_REL)
    return {"changed": True, "action": "add", "slug": slug, "relation": {
        "label": label or info["label"], "type": rel_type,
        "target_slug": target_slug, "target_title": target["title"]},
        "relations": page_relations(kb_id, slug)}


def update_relation(kb_id: str, slug: str, target_slug: str, new_rel_type: str = "",
                    new_target_slug: str = "", label: str = "") -> dict:
    """修改一条出边（关系类型和/或目标页）。`target_slug` 是**原**目标页。"""
    page = _load_page(kb_id, slug)
    rels = parse_out_relations(page["content"])
    hit = next((r for r in rels if r["slug"] == target_slug), None)
    if not hit:
        raise ValueError("本页没有指向 %s 的出边" % target_slug)
    rel_type = new_rel_type or hit["type"]
    info = _require_type(_allowed(page["page_type"]), rel_type, page["page_type"])
    final_slug = new_target_slug or target_slug
    if final_slug == slug:
        raise ValueError("不能把关系指向本页")
    target = _load_page(kb_id, final_slug)
    closure = ke_ontology.target_closure(rel_type)
    if closure and target["page_type"] not in closure:
        raise ValueError("目标页类型 %s 不在 `%s` 的 range 范围内（%s）"
                         % (target["page_type"], rel_type, "、".join(closure[:10])))
    lines = page["content"].splitlines()
    lines[hit["line_index"]] = rel_line(label or info["label"], rel_type,
                                        target["title"], final_slug)
    _apply_content_update(kb_id, slug, "\n".join(lines).rstrip() + "\n", TAG_REL)
    return {"changed": True, "action": "update", "slug": slug,
            "before": {"type": hit["type"], "target_slug": target_slug},
            "after": {"type": rel_type, "target_slug": final_slug},
            "relations": page_relations(kb_id, slug)}


def delete_relation(kb_id: str, slug: str, target_slug: str, rel_type: str = "") -> dict:
    """删除本页出边（可只删某一类）。**反向边不在这里删**：那是对方页面的出边。"""
    page = _load_page(kb_id, slug)
    rels = parse_out_relations(page["content"])
    victims = [r for r in rels if r["slug"] == target_slug and (not rel_type or r["type"] == rel_type)]
    if not victims:
        raise ValueError("本页没有匹配的出边（目标 %s%s）"
                         % (target_slug, "，类型 " + rel_type if rel_type else ""))
    drop = {r["line_index"] for r in victims}
    lines = [line for i, line in enumerate(page["content"].splitlines()) if i not in drop]
    _apply_content_update(kb_id, slug, "\n".join(lines).rstrip() + "\n", TAG_REL)
    return {"changed": True, "action": "delete", "slug": slug,
            "removed": [{"type": r["type"], "target_slug": r["slug"]} for r in victims],
            "relations": page_relations(kb_id, slug)}


# ---------------------------------------------------------------------------
# 本体类型修改 / 批量软删除 / KB 能力位（wiki 自动生成开关）
# ---------------------------------------------------------------------------
def _sync_folders(kb_id: str) -> dict:
    """复用既有目录同步脚本（子进程，避免 ke-core ↔ ontology-mcp 的循环导入）。"""
    import subprocess
    script = HERE.parents[0] / "ontology-mcp" / "sync_folders.py"
    if not script.is_file():
        return {"ok": False, "error": "找不到 %s" % script}
    done = subprocess.run([sys.executable, str(script), "--kb-id", kb_id, "--link-pages"],
                          capture_output=True, text=True, encoding="utf-8", check=False)
    tail = (done.stdout or done.stderr or "").strip().splitlines()[-3:]
    return {"ok": done.returncode == 0, "tail": tail}


def set_page_type(kb_id: str, slug: str, new_type: str, sync_folders: bool = True) -> dict:
    """改本体类型：类型 + 分类路径 + page_metadata 一起改，并重建目录树。

    上游的 `PUT /wiki/pages/<slug>` **只接受** summary/entity/concept/index/... 六个内建
    类型（`types.IsValidWikiPageType`），传 `bmm:Goal` 会 400 —— 所以类型修改必须走本函数。
    """
    new_type = (new_type or "").strip()
    meta = ke_ontology.class_meta()
    if new_type not in meta:
        raise ValueError("未知本体类型：%s" % new_type)
    page = _load_page(kb_id, slug)
    import json as _json
    md = {}
    try:
        md = _json.loads(page.get("page_metadata") or "{}") or {}
    except Exception:  # noqa: BLE001
        md = {}
    if not isinstance(md, dict):
        md = {}
    ontology = dict(md.get("ontology") or {})
    ontology.update({"model": (meta.get(new_type) or {}).get("module") or "",
                     "class": new_type,
                     "class_label": (meta.get(new_type) or {}).get("label") or new_type,
                     "generator": TAG_TYPE, "updated_at": ke_db.now_text()})
    md["ontology"] = ontology
    path = ke_ontology.category_path(new_type)

    stmts = [_snapshot_stmt(kb_id, slug, TAG_TYPE),
             "UPDATE wiki_pages SET page_type = %s, category_path = %s::jsonb, "
             "page_metadata = %s::jsonb, version = version + 1, updated_at = now(), "
             "last_edit_source = '%s' "
             "WHERE knowledge_base_id = %s AND slug = %s AND deleted_at IS NULL;"
             % (ke_db.sql_str(new_type), ke_db.sql_json(path), ke_db.sql_json(md), TAG_TYPE,
                ke_db.sql_str(kb_id), ke_db.sql_str(slug))]
    ke_db.psql("BEGIN;\n" + "\n".join(stmts) + "\nCOMMIT;\n", stdin=True)

    result = {"changed": page["page_type"] != new_type, "slug": slug,
              "before": page["page_type"], "after": new_type,
              "type_label": (meta.get(new_type) or {}).get("label") or new_type,
              "category_path": path, "version": page["version"] + 1}
    if sync_folders:
        result["folders"] = _sync_folders(kb_id)
    return result


def delete_pages(kb_id: str, slugs: list[str], dry_run: bool = False,
                 sync_folders: bool = True) -> dict:
    """多选**硬删除**（用户 2026-09-19 口径：直接删 pg 记录 + 图库记录，便于重新提取；
    软删除/恢复以后再单独做）。`slug='index'` 永远保留。"""
    wanted = [str(s).strip() for s in (slugs or []) if str(s).strip()]
    wanted = [s for s in wanted if s != "index"]
    if not wanted:
        raise ValueError("没有可删除的 slug（index 受保护，始终保留）")
    cond = ("knowledge_base_id = %s AND deleted_at IS NULL AND slug <> 'index' AND slug IN (%s)"
            % (ke_db.sql_str(kb_id), ", ".join(ke_db.sql_str(s) for s in wanted)))
    if dry_run:
        rows = ke_db.psql_csv("SELECT slug, page_type FROM wiki_pages WHERE %s ORDER BY slug" % cond)
        return {"dry_run": True, "mode": "hard", "requested": len(wanted), "matched": len(rows),
                "pages": [dict(r) for r in rows]}

    # 1) 先记下要删的 slug（含软删的旧行，一并清掉），便于图库同删
    existing = ke_db.psql_csv(
        "SELECT slug FROM wiki_pages WHERE knowledge_base_id = %s AND slug IN (%s)"
        % (ke_db.sql_str(kb_id), ", ".join(ke_db.sql_str(s) for s in wanted)))
    slugs_all = [r["slug"] for r in existing] or wanted

    # 2) PG：版本快照 + 主页行一起硬删（in_links 由 rebuild 兜底重算）
    stmts = [
        "DELETE FROM wiki_page_revisions WHERE knowledge_base_id = %s AND slug IN (%s);"
        % (ke_db.sql_str(kb_id), ", ".join(ke_db.sql_str(s) for s in slugs_all)),
        "DELETE FROM wiki_pages WHERE %s;" % cond,
        rebuild_in_links_sql(kb_id),
    ]
    ke_db.psql("BEGIN;\n" + "\n".join(stmts) + "\nCOMMIT;\n", stdin=True)

    # 3) 图库（Neo4j）：页面若在投影里留了 slug 记录就同删（当前投影按 iri 存本体，
    #    通常是空操作；保留这个钩子是为了以后实例层也进 Neo4j 时行为一致）
    neo4j_deleted = 0
    try:
        import ke_ontology  # noqa: F401  仅为了确认 ke-core 可用
        from ke_neo4j import query as _cypher  # type: ignore
        for slug in slugs_all:
            try:
                rows = _cypher("MATCH (n) WHERE n.slug = $s RETURN count(n) AS c", {"s": slug})
                if rows and int(rows[0].get("c") or 0) > 0:
                    _cypher("MATCH (n) WHERE n.slug = $s DETACH DELETE n", {"s": slug})
                    neo4j_deleted += 1
            except Exception:  # noqa: BLE001
                break
    except Exception:  # noqa: BLE001
        pass

    result = {"dry_run": False, "mode": "hard", "requested": len(wanted),
              "deleted": len(slugs_all), "slugs": slugs_all, "neo4j_deleted": neo4j_deleted,
              "protected": ["index"]}
    if sync_folders:
        result["folders"] = _sync_folders(kb_id)
    return result


def set_wiki_enabled(kb_id: str, enabled: bool) -> dict:
    """知识库能力位 `indexing_strategy.wiki_enabled`（决定上传文档是否自动生成 wiki）。

    直接改 KB 的 jsonb 字段，不走上游 `PUT /knowledge-bases/:id`——那条链路要求
    整份 config（chunking_config 等是**值类型**，缺字段会被清空），风险不值得冒。
    """
    flag = "true" if enabled else "false"
    out = ke_db.psql(
        "UPDATE knowledge_bases SET indexing_strategy = "
        "jsonb_set(COALESCE(indexing_strategy, '{}'::jsonb), '{wiki_enabled}', '%s'::jsonb, true), "
        "updated_at = now() WHERE id = %s AND deleted_at IS NULL "
        "RETURNING indexing_strategy::text;" % (flag, ke_db.sql_str(kb_id)))
    if not out.strip():
        raise ValueError("知识库不存在：%s" % kb_id)
    return {"kb_id": kb_id, "wiki_enabled": bool(enabled),
            "indexing_strategy": out.strip().splitlines()[0]}


if __name__ == "__main__":  # 本地自测：python3 ke_pages.py relations <kb> <slug>
    import json as _json2

    def _main() -> int:
        args = sys.argv[1:]
        if len(args) >= 3 and args[0] == "relations":
            print(_json2.dumps(page_relations(args[1], args[2]), ensure_ascii=False, indent=2))
            return 0
        if len(args) >= 3 and args[0] == "types":
            print(_json2.dumps(ke_ontology.relation_types_for(args[2]), ensure_ascii=False, indent=2))
            return 0
        print("用法：ke_pages.py relations <kb_id> <slug> | types <kb_id> <page_type>")
        return 1

    sys.exit(_main())



