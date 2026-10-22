"""Page-level operations (P3.5 capability front-loading).

Why a kernel module: copying a page must preserve ontology attributes, relations and provenance,
and must never touch the source page. Agents should call one tool instead of re-assembling pages by hand.
"""
from __future__ import annotations

import json

import ke_db
import ke_ontology
import ke_pages

psql_csv = ke_db.psql_csv
sql_str = ke_db.sql_str


def _row(kb_id: str, slug: str) -> dict | None:
    """读取一页（只读）：类型/正文/元数据/来源引用。"""
    sql = ("SELECT slug, title, COALESCE(page_type, %s) AS page_type, "
           "COALESCE(content, %s) AS content, "
           "COALESCE(page_metadata::text, %s) AS meta, "
           "COALESCE(source_refs::text, %s) AS refs, "
           "COALESCE(chunk_refs::text, %s) AS chunks "
           "FROM wiki_pages WHERE knowledge_base_id = %s AND slug = %s "
           "AND deleted_at IS NULL LIMIT 1") % (sql_str(""), sql_str(""), sql_str("{}"),
                                 sql_str("[]"), sql_str("[]"), sql_str(kb_id), sql_str(slug))
    rows = psql_csv(sql)
    return rows[0] if rows else None


def copy_page(kb_id: str, src_slug: str, new_slug: str = "", new_title: str = "",
              copy_relations: bool = True) -> dict:
    """复制一页：保留本体类型/数据属性/来源 + 关系；**源页零改动**（只读它）。

    new_slug 缺省 = `<src>-copy`；new_title 缺省 = `<原标题>（副本）`。
    """
    src = _row(kb_id, src_slug)
    if not src:
        raise ValueError("源页不存在：%s" % src_slug)
    slug = (new_slug or "").strip() or (src_slug + "-copy")
    if slug == src_slug:
        raise ValueError("new_slug 不能与源页相同")
    title = (new_title or "").strip() or ((src["title"] or src_slug) + "（副本）")
    try:
        meta = json.loads(src["meta"] or "{}")
    except json.JSONDecodeError:
        meta = {}
    attrs = ((meta.get("ontology") or {}).get("attributes") or {})
    page_type = src["page_type"] or ""
    mode = ke_ontology.render_mode(page_type) if page_type else "entity"
    spec = {"slug": slug, "title": title, "page_type": page_type, "mode": mode,
            "attributes": attrs,
            "source_refs": json.loads(src["refs"] or "[]"),
            "chunk_refs": json.loads(src["chunks"] or "[]"),
            "metadata_extra": {"copied_from": src_slug}}
    if mode == "document":
        spec["wiki_content"] = src["content"] or ""
    res = ke_pages.write_knowledge(kb_id, spec, strict_source=False, sync_folders=False)
    rels = []
    if copy_relations:
        for rel in ke_pages.parse_out_relations(src["content"] or ""):
            if not rel.get("slug") or rel.get("slug") == src_slug:
                continue
            try:
                ke_pages.add_relation(kb_id, slug, rel["type"], rel["slug"])
                rels.append({"type": rel["type"], "target": rel["slug"]})
            except Exception as exc:  # noqa: BLE001
                rels.append({"type": rel["type"], "target": rel["slug"], "error": str(exc)[:120]})
    return {"ok": True, "kb_id": kb_id, "from": src_slug, "slug": res.get("slug") or slug,
            "title": title, "page_type": page_type, "mode": mode,
            "attributes_copied": len(attrs), "relations_copied": len(rels),
            "relations": rels, "version": res.get("version"),
            "note": "源页只读、未改动；副本带 copied_from 元数据与相同来源引用"}
