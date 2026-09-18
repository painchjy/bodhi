"""Kùzu 嵌入式图数据库服务 — 内联值模式"""
import json, uuid
from datetime import datetime
from pathlib import Path
from typing import Optional, List, Dict, Any
import kuzu
from ..config import settings
from ..models.bmm_models import *

class GraphService:
    def __init__(self):
        self._db = None; self._conn = None; self._initialized = False
    @property
    def db(self):
        if self._db is None: self._db = kuzu.Database(str(Path(settings.kuzu_db_path).resolve()))
        return self._db
    @property
    def conn(self):
        if self._conn is None: self._conn = kuzu.Connection(self.db)
        return self._conn
    def close(self):
        if self._conn: self._conn.close(); self._conn = None
        if self._db: self._db.close(); self._db = None
    def verify_connection(self) -> bool:
        try: _ = self.db; _ = self.conn; return True
        except: return False
    def _q(self, s):
        """英文单引号 → 弯引号，避免破坏 Kùzu 字符串定界"""
        return str(s).replace("'", "\u2019").replace("\\", "\\\\")

    def init_schema(self):
        if self._initialized: return
        tables = [
            "CREATE NODE TABLE IF NOT EXISTS BmmNode (id STRING, node_type STRING, name STRING, english_name STRING, definition STRING, description STRING, extra_json STRING, created_at STRING, updated_at STRING, PRIMARY KEY (id))",
            "CREATE NODE TABLE IF NOT EXISTS SourceDocument (document_id STRING, document_title STRING, name STRING, description STRING, publication_date STRING, PRIMARY KEY (document_id))",
            "CREATE NODE TABLE IF NOT EXISTS Excerpt (id STRING, name STRING, text STRING, source_position STRING, PRIMARY KEY (id))",
            "CREATE REL TABLE IF NOT EXISTS BmmRel (FROM BmmNode TO BmmNode, id STRING, rel_type STRING, created_at STRING)",
            "CREATE REL TABLE IF NOT EXISTS HasSource (FROM BmmNode TO SourceDocument, is_authoritative BOOL, excerpt_text STRING, source_position STRING)",
            "CREATE REL TABLE IF NOT EXISTS FromExcerpt (FROM BmmNode TO Excerpt, is_authoritative BOOL)",
            "CREATE REL TABLE IF NOT EXISTS FromSource (FROM Excerpt TO SourceDocument)",
            "CREATE REL TABLE IF NOT EXISTS DefinesConcept (FROM Excerpt TO BmmNode)",
        ]
        for s in tables:
            try: self.conn.execute(s)
            except: pass
        self._initialized = True

    def create_node(self, node):
        node.id = node.id or f"bmm_{uuid.uuid4().hex[:12]}"
        node.created_at = datetime.now(); node.updated_at = datetime.now()
        ex = json.dumps(self._extra(node), ensure_ascii=False)
        self.conn.execute(
            f"MERGE (n:BmmNode {{id: '{self._q(node.id)}'}}) "
            f"SET n.node_type='{self._q(node.node_type)}', n.name='{self._q(node.name)}', "
            f"n.english_name='{self._q(node.english_name or '')}', n.definition='{self._q(node.definition or '')}', "
            f"n.description='{self._q(node.description or '')}', n.extra_json='{self._q(ex)}', "
            f"n.created_at='{node.created_at.isoformat()}', n.updated_at='{node.updated_at.isoformat()}' "
            f"RETURN n.id")
        return node.id

    def _extra(self, node):
        e = dict(getattr(node, "extra", None) or {})
        if node.ai_skill:
            e["ai_skill"] = node.ai_skill
        return e

    def create_document_node(self, doc):
        doc.document_id = doc.document_id or f"doc_{uuid.uuid4().hex[:12]}"
        pd = str(doc.publication_date) if doc.publication_date else ""
        self.conn.execute(
            f"MERGE (d:SourceDocument {{document_id: '{self._q(doc.document_id)}'}}) "
            f"SET d.document_title='{self._q(doc.document_title or '')}', d.name='{self._q(doc.name)}', "
            f"d.description='{self._q(doc.description or '')}', d.publication_date='{self._q(pd)}' "
            f"RETURN d.document_id")
        return doc.document_id

    def create_excerpt_node(self, excerpt):
        excerpt.id = excerpt.id or f"exc_{uuid.uuid4().hex[:12]}"
        self.conn.execute(
            f"MERGE (e:Excerpt {{id: '{self._q(excerpt.id)}'}}) "
            f"SET e.name='{self._q(excerpt.name)}', e.text='{self._q(excerpt.text)}', e.source_position='{self._q(excerpt.source_position or '')}' "
            f"RETURN e.id")
        return excerpt.id

    def link_excerpt_to_document(self, excerpt_id, document_id):
        self.conn.execute(
            f"MATCH (e:Excerpt), (d:SourceDocument) WHERE e.id='{self._q(excerpt_id)}' AND d.document_id='{self._q(document_id)}' "
            f"MERGE (e)-[:FromSource]->(d) RETURN e.id")

    def get_node(self, node_id):
        r = self.conn.execute(f"MATCH (n:BmmNode) WHERE n.id='{self._q(node_id)}' RETURN n.*")
        cols = r.get_column_names()
        while r.has_next(): return self._to_node(dict(zip(cols, r.get_next())))
        return None

    def update_node(self, node_id, updates):
        updates = dict(updates)
        # ai_skill 存于 extra_json，需合并处理
        if "ai_skill" in updates:
            r = self.conn.execute(f"MATCH (n:BmmNode) WHERE n.id='{self._q(node_id)}' RETURN n.extra_json")
            rows = list(r)
            cur_extra = {}
            if rows:
                try:
                    cur_extra = json.loads(rows[0][0] or "{}") or {}
                except Exception:
                    cur_extra = {}
            val = updates.pop("ai_skill")
            if val:
                cur_extra["ai_skill"] = str(val)
            else:
                cur_extra.pop("ai_skill", None)
            updates["extra_json"] = json.dumps(cur_extra, ensure_ascii=False)
        updates["updated_at"] = datetime.now().isoformat()
        parts = [f"n.{k}='{self._q(str(v))}'" for k, v in updates.items()]
        if not parts: return self.get_node(node_id)
        q = f"MATCH (n:BmmNode) WHERE n.id='{self._q(node_id)}' SET {','.join(parts)} RETURN n.*"
        r = self.conn.execute(q)
        cols = r.get_column_names()
        while r.has_next(): return self._to_node(dict(zip(cols, r.get_next())))
        return None

    def delete_node(self, node_id):
        self.conn.execute(f"MATCH (n:BmmNode) WHERE n.id='{self._q(node_id)}' DETACH DELETE n RETURN 1")
        return True

    def clear_knowledge_graph(self):
        """清空所有知识节点与关系（保留文档与摘录），用于重建"""
        self.conn.execute("MATCH (n:BmmNode) DETACH DELETE n")
        return True

    def delete_document(self, document_id):
        r = self.conn.execute(f"MATCH (e:Excerpt)-[:FromSource]->(d:SourceDocument) WHERE d.document_id='{self._q(document_id)}' RETURN e.id")
        cols = r.get_column_names()
        while r.has_next():
            eid = dict(zip(cols, r.get_next())).get("e.id","")
            if eid: self.conn.execute(f"MATCH (e:Excerpt) WHERE e.id='{self._q(eid)}' DETACH DELETE e RETURN 1")
        self.conn.execute(f"MATCH (d:SourceDocument) WHERE d.document_id='{self._q(document_id)}' DETACH DELETE d RETURN 1")
        return True

    def search_nodes(self, node_type=None, keyword=None, limit=50):
        conds = []
        if node_type: conds.append(f"n.node_type='{self._q(node_type)}'")
        if keyword: conds.append(f"CONTAINS(n.name,'{self._q(keyword)}')")
        where = ("WHERE " + " AND ".join(conds)) if conds else ""
        q = f"MATCH (n:BmmNode) {where} RETURN n.* ORDER BY n.name LIMIT {limit}"
        r = self.conn.execute(q)
        cols = r.get_column_names()
        return [self._to_node(dict(zip(cols, row))) for row in r]

    def find_node_by_type_name(self, type_ref, name):
        """按（带前缀或本地）类型 + 名称查找知识节点"""
        local = str(type_ref).split(":")[-1].split(".")[-1]
        r = self.conn.execute(
            f"MATCH (n:BmmNode) WHERE n.name='{self._q(name)}' RETURN n.* LIMIT 100")
        cols = r.get_column_names()
        for row in r:
            node = self._to_node(dict(zip(cols, row)))
            if node.node_type.split(":")[-1] == local:
                return node
        return None

    def get_all_nodes(self, limit=200):
        r = self.conn.execute(f"MATCH (n:BmmNode) RETURN n.* ORDER BY n.node_type, n.name LIMIT {limit}")
        cols = r.get_column_names()
        return [self._to_node(dict(zip(cols, row))) for row in r]

    def get_document(self, document_id):
        r = self.conn.execute(f"MATCH (d:SourceDocument) WHERE d.document_id='{self._q(document_id)}' RETURN d.*")
        cols = r.get_column_names()
        while r.has_next(): return dict(zip(cols, r.get_next()))
        return None

    def get_document_excerpts(self, document_id):
        r = self.conn.execute(f"MATCH (e:Excerpt)-[:FromSource]->(d:SourceDocument) WHERE d.document_id='{self._q(document_id)}' RETURN e.*")
        cols = r.get_column_names()
        return [dict(zip(cols, row)) for row in r]

    def get_all_excerpts(self):
        r = self.conn.execute("MATCH (e:Excerpt)-[:FromSource]->(d:SourceDocument) RETURN e.*, d.document_title, d.name")
        cols = r.get_column_names()
        return [dict(zip(cols, row)) for row in r]

    def get_excerpts_by_ids(self, excerpt_ids: list) -> list:
        """根据 ID 列表查询摘录及其文档信息"""
        if not excerpt_ids: return []
        ids_str = ", ".join([f"'{self._q(eid)}'" for eid in excerpt_ids])
        r = self.conn.execute(
            f"MATCH (e:Excerpt)-[:FromSource]->(d:SourceDocument) WHERE e.id IN [{ids_str}] RETURN e.*, d.document_id, d.document_title, d.name")
        cols = r.get_column_names()
        return [dict(zip(cols, row)) for row in r]

    def list_documents(self):
        r = self.conn.execute("MATCH (d:SourceDocument) RETURN d.* ORDER BY d.name")
        cols = r.get_column_names()
        return [dict(zip(cols, row)) for row in r]

    def create_relationship(self, rel):
        rel.id = rel.id or f"rel_{uuid.uuid4().hex[:12]}"
        rel.created_at = datetime.now()
        rt = str(rel.rel_type)
        self.conn.execute(
            f"MATCH (a:BmmNode), (b:BmmNode) WHERE a.id='{self._q(rel.source_node_id)}' AND b.id='{self._q(rel.target_node_id)}' "
            f"MERGE (a)-[r:BmmRel {{id:'{self._q(rel.id)}'}}]->(b) "
            f"SET r.rel_type='{self._q(rt)}', r.created_at='{rel.created_at.isoformat()}' RETURN r.id")
        return rel.id

    def delete_relationship(self, rel_id):
        self.conn.execute(f"MATCH ()-[r:BmmRel]->() WHERE r.id='{self._q(rel_id)}' DELETE r RETURN 1")
        return True

    def get_node_relationships(self, node_id):
        rels = []
        for d in ["out", "in"]:
            if d == "out":
                q = f"MATCH (n:BmmNode)-[r:BmmRel]->(m:BmmNode) WHERE n.id='{self._q(node_id)}' RETURN r.rel_type, r.id, m.id, m.name, m.node_type"
            else:
                q = f"MATCH (n:BmmNode)<-[r:BmmRel]-(m:BmmNode) WHERE n.id='{self._q(node_id)}' RETURN r.rel_type, r.id, m.id, m.name, m.node_type"
            r = self.conn.execute(q)
            cols = r.get_column_names()
            for row in r:
                rec = dict(zip(cols, row))
                rels.append({"rel_type": rec.get("r.rel_type",""), "rel_id": rec.get("r.id",""),
                    "target_id": rec.get("m.id",""), "target_name": rec.get("m.name",""),
                    "target_type": rec.get("m.node_type",""), "direction": d})
        return rels

    def get_node_relationships_detail(self, node_id):
        """获取节点全部关系（含源/目标完整信息），用于关系编辑"""
        rels = []
        r = self.conn.execute(
            f"MATCH (n:BmmNode)-[rel:BmmRel]->(m:BmmNode) WHERE n.id='{self._q(node_id)}' "
            f"RETURN rel.id, rel.rel_type, n.id, n.name, n.node_type, m.id, m.name, m.node_type")
        cols = r.get_column_names()
        for row in r:
            rec = dict(zip(cols, row))
            rels.append({"rel_id": rec.get("rel.id",""), "rel_type": rec.get("rel.rel_type",""),
                "source_id": rec.get("n.id",""), "source_name": rec.get("n.name",""), "source_type": rec.get("n.node_type",""),
                "target_id": rec.get("m.id",""), "target_name": rec.get("m.name",""), "target_type": rec.get("m.node_type",""),
                "direction": "out"})
        r2 = self.conn.execute(
            f"MATCH (n:BmmNode)<-[rel:BmmRel]-(m:BmmNode) WHERE n.id='{self._q(node_id)}' "
            f"RETURN rel.id, rel.rel_type, m.id, m.name, m.node_type, n.id, n.name, n.node_type")
        cols2 = r2.get_column_names()
        for row in r2:
            rec = dict(zip(cols2, row))
            rels.append({"rel_id": rec.get("rel.id",""), "rel_type": rec.get("rel.rel_type",""),
                "source_id": rec.get("m.id",""), "source_name": rec.get("m.name",""), "source_type": rec.get("m.node_type",""),
                "target_id": rec.get("n.id",""), "target_name": rec.get("n.name",""), "target_type": rec.get("n.node_type",""),
                "direction": "in"})
        return rels

    def update_relationship(self, rel_id, rel_type, target_node_id):
        """修改关系：删除旧关系，以原源节点 + 新目标节点 + 新类型重建"""
        r = self.conn.execute(
            f"MATCH (a:BmmNode)-[rel:BmmRel]->(b:BmmNode) WHERE rel.id='{self._q(rel_id)}' RETURN a.id")
        rows = list(r)
        if not rows:
            return None
        source_id = rows[0][0]
        self.conn.execute(f"MATCH ()-[rel:BmmRel]->() WHERE rel.id='{self._q(rel_id)}' DELETE rel")
        new_id = f"rel_{uuid.uuid4().hex[:12]}"
        self.conn.execute(
            f"MATCH (a:BmmNode), (b:BmmNode) WHERE a.id='{self._q(source_id)}' AND b.id='{self._q(target_node_id)}' "
            f"MERGE (a)-[rel:BmmRel {{id:'{self._q(new_id)}'}}]->(b) "
            f"SET rel.rel_type='{self._q(rel_type)}', rel.created_at='{datetime.now().isoformat()}' RETURN rel.id")
        return new_id

    def link_source(self, node_id, source):
        """节点来源关联：知识节点 → 摘录（经摘录中间关联文档），不再直接关联文档"""
        auth = "true" if source.is_authoritative else "false"
        if source.excerpt_id:
            self.conn.execute(
                f"MATCH (n:BmmNode), (e:Excerpt) WHERE n.id='{self._q(node_id)}' AND e.id='{self._q(source.excerpt_id)}' "
                f"MERGE (n)-[r:FromExcerpt]->(e) SET r.is_authoritative={auth} RETURN 1")
            # 确保摘录已挂接到文档（若摘录尚未关联文档）
            if source.document_id:
                self.conn.execute(
                    f"MATCH (e:Excerpt), (d:SourceDocument) WHERE e.id='{self._q(source.excerpt_id)}' AND d.document_id='{self._q(source.document_id)}' "
                    f"MERGE (e)-[:FromSource]->(d) RETURN 1")

    def set_authoritative_source(self, node_id, source_doc_id, excerpt_id=""):
        nid = self._q(node_id)
        self.conn.execute(f"MATCH (n:BmmNode)-[r:FromExcerpt]->() WHERE n.id='{nid}' SET r.is_authoritative=false RETURN 1")
        if excerpt_id:
            self.conn.execute(f"MATCH (n:BmmNode)-[r:FromExcerpt]->(e:Excerpt) WHERE n.id='{nid}' AND e.id='{self._q(excerpt_id)}' SET r.is_authoritative=true RETURN 1")
        elif source_doc_id:
            self.conn.execute(
                f"MATCH (n:BmmNode)-[r:FromExcerpt]->(e:Excerpt)-[:FromSource]->(d:SourceDocument) "
                f"WHERE n.id='{nid}' AND d.document_id='{self._q(source_doc_id)}' SET r.is_authoritative=true RETURN 1")
        return True

    def get_node_sources(self, node_id):
        sources = []
        r = self.conn.execute(
            f"MATCH (n:BmmNode)-[r:FromExcerpt]->(e:Excerpt)-[:FromSource]->(d:SourceDocument) WHERE n.id='{self._q(node_id)}' "
            f"RETURN d.document_id, d.document_title, e.id, e.text, e.source_position, r.is_authoritative")
        cols = r.get_column_names()
        while r.has_next():
            rec = dict(zip(cols, r.get_next()))
            sources.append(SourceInfo(
                document_id=rec.get("d.document_id",""),
                document_title=rec.get("d.document_title",""),
                excerpt_id=rec.get("e.id",""),
                excerpt_text=rec.get("e.text","") or "",
                source_position=rec.get("e.source_position",""),
                is_authoritative=bool(rec.get("r.is_authoritative", False))))
        return sources

    def get_subgraph(self, node_id, depth=2):
        nodes_set, rels_list = {}, []
        center = self.get_node(node_id)
        if center:
            nodes_set[node_id] = {"id": center.id, "name": center.name, "node_type": center.node_type}
        nid = self._q(node_id)
        for adir in [("out","->"),("in","<-")]:
            if adir[0] == "out":
                q = f"MATCH (n:BmmNode)-[r:BmmRel]->(m:BmmNode) WHERE n.id='{nid}' RETURN m.id, m.name, m.node_type, r.id, r.rel_type"
            else:
                q = f"MATCH (n:BmmNode)<-[r:BmmRel]-(m:BmmNode) WHERE n.id='{nid}' RETURN m.id, m.name, m.node_type, r.id, r.rel_type"
            r = self.conn.execute(q)
            cols = r.get_column_names()
            while r.has_next():
                rec = dict(zip(cols, r.get_next()))
                mid = rec.get("m.id","")
                if mid and mid not in nodes_set:
                    nodes_set[mid] = {"id": mid, "name": rec.get("m.name",""), "node_type": rec.get("m.node_type","")}
                s = node_id if adir[0]=="out" else mid
                t = mid if adir[0]=="out" else node_id
                rels_list.append({"id": rec.get("r.id",""), "type": rec.get("r.rel_type",""), "source": s, "target": t})
        return {"nodes": list(nodes_set.values()), "relationships": rels_list}

    def get_full_graph(self, limit=500):
        r = self.conn.execute(f"MATCH (n:BmmNode) RETURN n.* ORDER BY n.node_type, n.name LIMIT {limit}")
        cols = r.get_column_names()
        nodes_rows = list(r)
        nodes = [{"id": dict(zip(cols,row)).get("n.id",""), "name": dict(zip(cols,row)).get("n.name",""),
                  "node_type": dict(zip(cols,row)).get("n.node_type",""),
                  "definition": dict(zip(cols,row)).get("n.definition","")} for row in nodes_rows]

        # 分两步：先查关系计数，再查关系详情
        rr = self.conn.execute(f"MATCH (a:BmmNode)-[r:BmmRel]->(b:BmmNode) RETURN a.id, b.id, r.id, r.rel_type LIMIT {limit*3}")
        rcols = rr.get_column_names()
        rel_rows = list(rr)
        rels = [{"id": dict(zip(rcols,row)).get("r.id",""), "type": dict(zip(rcols,row)).get("r.rel_type",""),
                 "source": dict(zip(rcols,row)).get("a.id",""), "target": dict(zip(rcols,row)).get("b.id","")} for row in rel_rows]

        print(f"[Bodhi] get_full_graph: {len(nodes)} nodes, {len(rels)} relationships", flush=True)
        return {"nodes": nodes, "relationships": rels}

    def get_doc_node_ids(self, document_id: str):
        """document_id 支持逗号分隔多选；返回这些文档经摘录关联的节点ID集合。空返回 None 表示不过滤。"""
        if not document_id:
            return None
        ids = [d.strip() for d in str(document_id).split(",") if d.strip()]
        if not ids:
            return None
        in_str = ", ".join([f"'{self._q(d)}'" for d in ids])
        r = self.conn.execute(
            f"MATCH (n:BmmNode)-[:FromExcerpt]->(e:Excerpt)-[:FromSource]->(d:SourceDocument) WHERE d.document_id IN [{in_str}] RETURN n.id")
        return {row[0] for row in r}

    def get_excerpts_by_documents(self, document_ids: list) -> list:
        """按多个文档查询摘录（含文档信息）"""
        if not document_ids:
            return []
        in_str = ", ".join([f"'{self._q(d)}'" for d in document_ids])
        r = self.conn.execute(
            f"MATCH (e:Excerpt)-[:FromSource]->(d:SourceDocument) WHERE d.document_id IN [{in_str}] RETURN e.*, d.document_id, d.document_title, d.name")
        cols = r.get_column_names()
        return [dict(zip(cols, row)) for row in r]

    def get_graph_data(self, node_type="", keyword="", document_id="", limit=500):
        """返回过滤后的节点与关系（供图可视化）。node_type/document_id 支持逗号分隔多选。"""
        types = [t.strip() for t in str(node_type).split(",") if t.strip()] if node_type else []
        conds = []
        if types:
            conds.append("(" + " OR ".join([f"n.node_type='{self._q(t)}'" for t in types]) + ")")
        if keyword:
            conds.append(f"CONTAINS(n.name,'{self._q(keyword)}')")
        where = ("WHERE " + " AND ".join(conds)) if conds else ""
        r = self.conn.execute(
            f"MATCH (n:BmmNode) {where} RETURN n.id, n.name, n.node_type, n.definition, n.updated_at ORDER BY n.updated_at DESC, n.name LIMIT {limit}")
        cols = r.get_column_names()
        nodes = []
        seen = set()
        for row in r:
            rec = dict(zip(cols, row))
            nid = rec.get("n.id", "")
            if nid in seen:
                continue
            seen.add(nid)
            nodes.append({"id": nid, "name": rec.get("n.name", ""), "node_type": rec.get("n.node_type", ""),
                          "definition": rec.get("n.definition", ""),
                          "updated_at": rec.get("n.updated_at", "") or ""})
        # 文档过滤（经摘录中间关联）
        doc_ids = [d.strip() for d in str(document_id).split(",") if d.strip()] if document_id else []
        if doc_ids:
            allowed = self.get_doc_node_ids(",".join(doc_ids)) or set()
            nodes = [n for n in nodes if n["id"] in allowed]
        # 关系：仅保留两端都在节点集合内的关系
        node_ids = {n["id"] for n in nodes}
        if not node_ids:
            return {"nodes": [], "relationships": []}
        rr = self.conn.execute("MATCH (a:BmmNode)-[r:BmmRel]->(b:BmmNode) RETURN a.id, b.id, r.id, r.rel_type")
        rcols = rr.get_column_names()
        rels = []
        seen_rel = set()
        for row in rr:
            rec = dict(zip(rcols, row))
            sid, tid = rec.get("a.id", ""), rec.get("b.id", "")
            if sid not in node_ids or tid not in node_ids:
                continue
            rid = rec.get("r.id", "")
            if rid in seen_rel:
                continue
            seen_rel.add(rid)
            rels.append({"id": rid, "type": rec.get("r.rel_type", ""), "source": sid, "target": tid})
        return {"nodes": nodes, "relationships": rels}

    def _to_node(self, rec):
        nid = rec.get("n.id","") or rec.get("id","")
        node = BmmNode(
            id=nid,
            node_type=rec.get("n.node_type","") or rec.get("node_type",""),
            name=rec.get("n.name","") or rec.get("name",""),
            english_name=rec.get("n.english_name") or rec.get("english_name"),
            definition=rec.get("n.definition") or rec.get("definition"),
            description=rec.get("n.description") or rec.get("description"),
            created_at=self._pdt(rec.get("n.created_at") or rec.get("created_at")),
            updated_at=self._pdt(rec.get("n.updated_at") or rec.get("updated_at")),
        )
        ej = rec.get("n.extra_json") or rec.get("extra_json", "{}")
        try:
            extra = json.loads(ej) if ej else {}
        except Exception:
            extra = {}
        node.extra = extra
        node.ai_skill = extra.get("ai_skill")
        return node

    def _pdt(self, v):
        if not v: return None
        if isinstance(v, datetime): return v
        try: return datetime.fromisoformat(str(v))
        except: return None

graph_service = GraphService()
