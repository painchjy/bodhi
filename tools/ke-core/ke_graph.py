"""本体实例图谱（实例层）—— 2026-10-05 全量重构：Neo4j 为主存储、PG wiki 为伴生索引。

本模块只做一件事：把「知识库里的页 + 页正文的 `## 本体关系` 行」投影成 Neo4j 实例层：
  - 节点：`(:BodhiInstance {kb_id, slug, page_type, module, name, tenant_id, <数据属性…>})`
  - 边：本体对象属性（prefixed 作关系类型，如 `` :`bmm:isBasisFor` ``）
`kb_id` 是**强制**隔离键：一切读写都必须带它（防跨库）。
"""
from __future__ import annotations

import json
import re
from typing import Iterable

import ke_neo4j
import ke_pages

INSTANCE_LABEL = "BodhiInstance"


def _ensure_index() -> None:
    try:
        ke_neo4j.query(
            "CREATE INDEX bodhi_instance_idx IF NOT EXISTS "
            "FOR (n:BodhiInstance) ON (n.kb_id, n.slug)")
    except Exception:  # noqa: BLE001  老版本 Neo4j 不支持 IF NOT EXISTS
        try:
            ke_neo4j.query("CREATE INDEX ON :BodhiInstance(kb_id, slug)")
        except Exception:  # noqa: BLE001  已存在等
            pass


def _module_of(page_type: str) -> str:
    return (page_type or "").split(":", 1)[0] if ":" in (page_type or "") else ""


def _attrs_from_content(content: str) -> dict[str, str]:
    """正文「## 属性（数据属性）」小节的属性行 → {键去前缀: 值}（md 表达 = 数据属性的伴生）。"""
    out: dict[str, str] = {}
    sec = ""
    for line in (content or "").splitlines():
        if line.startswith("## "):
            sec = line[3:].strip()
            continue
        if sec not in ("属性（数据属性）", "属性"):
            continue
        m = re.match(r"^-\s*([A-Za-z_][\w:]*)\b[^\n=]*=\s*(.+?)\s*$", line.strip())
        if m:
            out[m.group(1).split(":")[-1]] = m.group(2).strip()
    return out


def _attrs_of(page_meta_json: str) -> dict[str, str]:
    """从 `page_metadata.ontology.attributes` 取数据属性（键去模块前缀，只留标量）。"""
    out: dict[str, str] = {}
    try:
        data = json.loads(page_meta_json or "{}")
        attrs = (data.get("ontology") or {}).get("attributes") or {}
    except Exception:  # noqa: BLE001
        return out
    for k, v in (attrs or {}).items():
        if v is None:
            continue
        if isinstance(v, (dict, list)):
            continue
        out[str(k).split(":")[-1]] = str(v)
    return out


def _edges_of(content: str, known_slugs: set[str]) -> list[tuple[str, str]]:
    """页正文 `## 本体关系` 行 → [(关系类型, 目标 slug)]；只留目标在库内的边。"""
    out: list[tuple[str, str]] = []
    for r in ke_pages.parse_out_relations(content):
        if r.get("type") and r.get("slug") and r["slug"] in known_slugs:
            out.append((r["type"], r["slug"]))
    return out


def _run(statement: str, params: dict) -> list[dict]:
    return ke_neo4j.query(statement, params)


def rebuild_kb_graph(kb_id: str, tenant_id: int | None = None) -> dict:
    """按 PG 页**幂等全量重建**该知识库的实例层（先清本库旧实例，再灌）。"""
    import ke_db
    _ensure_index()
    rows = ke_db.psql_csv(
        "SELECT slug, title, COALESCE(page_type,'') AS page_type, COALESCE(content,'') AS content, "
        "       COALESCE(page_metadata::text,'{}') AS meta, tenant_id "
        "  FROM wiki_pages WHERE knowledge_base_id = %s AND deleted_at IS NULL "
        "  AND COALESCE(page_type,'') NOT IN ('index','summary')"
        % ke_db.sql_str(kb_id))
    known = {r["slug"] for r in rows}
    if not rows:
        _run("MATCH (n:BodhiInstance {kb_id:$kb}) DETACH DELETE n", {"kb": kb_id})
        return {"ok": True, "kb_id": kb_id, "nodes": 0, "edges": 0, "cleared": True}

    # 先清本库旧实例（幂等重灌）
    _run("MATCH (n:BodhiInstance {kb_id:$kb}) DETACH DELETE n", {"kb": kb_id})
    nodes = 0
    edges = 0
    for r in rows:
        # 数据属性来源：正文属性行（md 表达）作兜底，page_metadata 机器口径优先覆盖
        attrs = _attrs_from_content(r["content"])
        attrs.update(_attrs_of(r["meta"]))
        params = {
            "kb": kb_id, "slug": r["slug"], "pt": r["page_type"],
            "module": _module_of(r["page_type"]), "name": r["title"],
            "tenant": r.get("tenant_id"),
        }
        sets = ["n.page_type=$pt", "n.module=$module", "n.name=$name", "n.tenant_id=$tenant"]
        for k, v in attrs.items():
            sets.append("n.%s=$%s" % (k, "a_" + k))
            params["a_" + k] = v
        _run("MERGE (n:BodhiInstance {kb_id:$kb, slug:$slug}) SET %s" % ", ".join(sets), params)
        nodes += 1
    # 边（先建节点，再连边，避免顺序问题）
    for r in rows:
        for rel_type, tgt in _edges_of(r["content"], known):
            _run("MATCH (a:BodhiInstance {kb_id:$kb, slug:$src}) "
                 "MATCH (b:BodhiInstance {kb_id:$kb, slug:$tgt}) "
                 "MERGE (a)-[e:`%s`]->(b)" % rel_type.replace("`", ""),
                 {"kb": kb_id, "src": r["slug"], "tgt": tgt})
            edges += 1
    return {"ok": True, "kb_id": kb_id, "nodes": nodes, "edges": edges}


def instance_count(kb_id: str) -> int:
    rows = _run("MATCH (n:BodhiInstance {kb_id:$kb}) RETURN count(n) AS n", {"kb": kb_id})
    return int((rows[0].get("n") if rows else 0) or 0)


def delete_kb_graph(kb_id: str) -> int:
    rows = _run("MATCH (n:BodhiInstance {kb_id:$kb}) WITH n, count(*) AS c DETACH DELETE n "
                "RETURN count(*) AS n", {"kb": kb_id})
    return int((rows[0].get("n") if rows else 0) or 0)
