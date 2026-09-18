"""本体模型图存储服务 — 将 .ttl 本体解析后存入 Kùzu，并提供实时查询。

设计：
- OntologyModel 节点：一个本体文件对应一个模型。
- OntoClass / OntoProperty 节点：本体类 / 属性，含长短 URI、label、comment、description、extra_json。
- OntoSubClass / OntoDomain / OntoRange / OntoImports 关系：层级、定义域、值域、模型引用。

替代原先每次请求都重新解析 TTL 文件的做法，概念树、节点类型、关系类型约束都从这里实时查询。
"""

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

from rdflib import Graph, RDF, RDFS, OWL, Literal, URIRef
from rdflib.namespace import DC, DCTERMS, SKOS

from ..config import settings
from .graph_service import graph_service

# 非知识节点实例类型（文档/摘录/枚举等），概念树与节点类型约束中剔除
NON_NODE_CLASSES = {
    "SourceDocument", "Regulation", "BusinessRequirementDoc",
    "TechnicalSolutionDoc", "Excerpt",
}

# 概念树中额外剔除的枚举/辅助类型（不参与知识节点筛选）
TREE_SKIP = NON_NODE_CLASSES | {
    "EnforcementLevel", "AssessmentType", "Strict", "Override", "Advisory",
    "Strength", "Weakness", "Opportunity", "Threat",
}

# 抽象类（仅用于层级分类，不作为可实例化知识节点类型）
ABSTRACT_CLASSES = {"DesiredResult", "Means", "Directive", "Influencer"}

# 附加属性解析时需要跳过的标准谓词（这些已映射为结构化字段）
_STD_SKIP_CLASS = {RDF.type, RDFS.label, RDFS.comment, RDFS.subClassOf,
                   OWL.equivalentClass, DC.description, DCTERMS.description, SKOS.definition}
_STD_SKIP_PROP = {RDF.type, RDFS.label, RDFS.comment, RDFS.domain, RDFS.range,
                  RDFS.subPropertyOf, OWL.equivalentProperty, DC.description,
                  DCTERMS.description, SKOS.definition}

# OWL/RDFS 内建命名空间（range 为 owl:Thing / owl:Resource 等时视为“任意类型”）
_BUILTIN_NS = (
    "http://www.w3.org/2002/07/owl#",
    "http://www.w3.org/2000/01/rdf-schema#",
    "http://www.w3.org/1999/02/22-rdf-syntax-ns#",
)


class OntologyService:
    """本体模型的解析、存储与查询"""

    def __init__(self):
        self._tables_ready = False
        self._snap = None
        self._memo = {}

    def _invalidate(self):
        """数据变更后清除缓存，确保查询一致性"""
        self._snap = None
        self._memo.clear()

    # ---------- 基础 ----------
    def _conn(self):
        return graph_service.conn

    def _q(self, s):
        return graph_service._q(s)

    def init_tables(self):
        if self._tables_ready:
            return
        conn = self._conn()
        statements = [
            "CREATE NODE TABLE IF NOT EXISTS OntologyModel (model_id STRING, label STRING, ontology_uri STRING, base_uri STRING, prefix STRING, source_file STRING, updated_at STRING, PRIMARY KEY(model_id))",
            "CREATE NODE TABLE IF NOT EXISTS OntoClass (uri STRING, model_id STRING, name STRING, prefix STRING, short_uri STRING, label STRING, comment STRING, description STRING, extra_json STRING, PRIMARY KEY(uri))",
            "CREATE NODE TABLE IF NOT EXISTS OntoProperty (uri STRING, model_id STRING, name STRING, prefix STRING, short_uri STRING, label STRING, comment STRING, description STRING, prop_type STRING, extra_json STRING, PRIMARY KEY(uri))",
            "CREATE REL TABLE IF NOT EXISTS OntoSubClass (FROM OntoClass TO OntoClass)",
            "CREATE REL TABLE IF NOT EXISTS OntoEquivalentClass (FROM OntoClass TO OntoClass)",
            "CREATE REL TABLE IF NOT EXISTS OntoDomain (FROM OntoProperty TO OntoClass)",
            "CREATE REL TABLE IF NOT EXISTS OntoRange (FROM OntoProperty TO OntoClass)",
            "CREATE REL TABLE IF NOT EXISTS OntoImports (FROM OntologyModel TO OntologyModel)",
        ]
        for s in statements:
            try:
                conn.execute(s)
            except Exception:
                pass
        self._tables_ready = True

    # ---------- 解析 ----------
    @staticmethod
    def _local_name(uri: str) -> str:
        if "#" in uri:
            return uri.rsplit("#", 1)[1]
        return uri.rsplit("/", 1)[-1]

    @staticmethod
    def _namespace(uri: str) -> str:
        if "#" in uri:
            return uri.rsplit("#", 1)[0] + "#"
        return uri.rsplit("/", 1)[0] + "/"

    def _first(self, g, subject, *preds) -> str:
        for p in preds:
            for o in g.objects(subject, p):
                if isinstance(o, Literal):
                    return str(o)
                if isinstance(o, URIRef):
                    return str(o)
        return ""

    def _rdf_list(self, g, node) -> List[str]:
        items = []
        while node is not None:
            first = g.value(node, RDF.first)
            if first is not None:
                items.append(str(first))
            rest = g.value(node, RDF.rest)
            if rest is None or str(rest) == str(RDF.nil):
                break
            node = rest
        return items

    def _resolve_targets(self, g, subject, pred) -> List[str]:
        """解析属性取值，展开 owl:unionOf / owl:intersectionOf 匿名节点"""
        result = []
        for obj in g.objects(subject, pred):
            s = str(obj)
            if s.startswith("http"):
                result.append(s)
            else:
                for u in g.objects(obj, OWL.unionOf):
                    result.extend(self._rdf_list(g, u))
                for u in g.objects(obj, OWL.intersectionOf):
                    result.extend(self._rdf_list(g, u))
        return result

    def _extra_json(self, g, subject, skip) -> str:
        """把非常用属性打包为 JSON 字符串（谓词 URI -> 取值列表）"""
        extra = {}
        for p, o in g.predicate_objects(subject):
            if p in skip:
                continue
            key = str(p)
            val = str(o)
            if key not in extra:
                extra[key] = []
            if val not in extra[key]:
                extra[key].append(val)
        return json.dumps(extra, ensure_ascii=False) if extra else "{}"

    def parse_ttl(self, path: str) -> dict:
        g = Graph()
        g.parse(path, format="turtle")

        # 命名空间前缀表
        ns_prefix: Dict[str, str] = {}
        default_ns = None
        for prefix, ns in g.namespace_manager.namespaces():
            ns_prefix[str(ns)] = str(prefix)
            if str(prefix) in ("", "default", "None"):
                default_ns = str(ns)

        # 本体声明
        ontology_uri = None
        ontology_label = ""
        imports: List[str] = []
        for s in g.subjects(RDF.type, OWL.Ontology):
            if ontology_uri is None:
                ontology_uri = str(s)
            lab = self._first(g, s, RDFS.label)
            if lab:
                ontology_label = lab
            for imp in g.objects(s, OWL.imports):
                imports.append(str(imp))

        model_id = self._local_name(ontology_uri) if ontology_uri else Path(path).stem
        prefix = model_id
        own_ns = default_ns
        if not own_ns and ontology_uri:
            own_ns = self._namespace(ontology_uri + "#" if "#" not in ontology_uri else ontology_uri)

        classes = []
        for cls in g.subjects(RDF.type, OWL.Class):
            uri = str(cls)
            if not uri.startswith("http"):
                continue
            is_own = self._namespace(uri) == own_ns
            label = self._first(g, cls, RDFS.label)
            comment = self._first(g, cls, RDFS.comment)
            description = self._first(g, cls, DC.description, DCTERMS.description, SKOS.definition) or comment
            parents = [str(o) for o in g.objects(cls, RDFS.subClassOf) if str(o).startswith("http")]
            equivalents = self._resolve_targets(g, cls, OWL.equivalentClass)
            classes.append({
                "uri": uri,
                "name": self._local_name(uri),
                "prefix": prefix if is_own else ns_prefix.get(self._namespace(uri), ""),
                "label": label,
                "comment": comment,
                "description": description,
                "is_own": is_own,
                "parents": parents,
                "equivalents": equivalents,
                "extra_json": self._extra_json(g, cls, _STD_SKIP_CLASS),
            })

        properties = []
        for pt, kind in [(OWL.ObjectProperty, "ObjectProperty"), (OWL.DatatypeProperty, "DatatypeProperty")]:
            for prop in g.subjects(RDF.type, pt):
                uri = str(prop)
                if not uri.startswith("http"):
                    continue
                is_own = self._namespace(uri) == own_ns
                label = self._first(g, prop, RDFS.label)
                comment = self._first(g, prop, RDFS.comment)
                description = self._first(g, prop, DC.description, DCTERMS.description, SKOS.definition) or comment
                properties.append({
                    "uri": uri,
                    "name": self._local_name(uri),
                    "prefix": prefix if is_own else ns_prefix.get(self._namespace(uri), ""),
                    "label": label,
                    "comment": comment,
                    "description": description,
                    "prop_type": kind,
                    "is_own": is_own,
                    "domains": self._resolve_targets(g, prop, RDFS.domain),
                    "ranges": self._resolve_targets(g, prop, RDFS.range),
                    "extra_json": self._extra_json(g, prop, _STD_SKIP_PROP),
                })

        return {
            "model_id": model_id,
            "label": ontology_label or Path(path).stem,
            "ontology_uri": ontology_uri or "",
            "base_uri": own_ns or "",
            "prefix": prefix,
            "source_file": Path(path).name,
            "imports": imports,
            "classes": classes,
            "properties": properties,
        }

    # ---------- 写入 ----------
    def _create_edge(self, rel, src_label, src_key, src_val, tgt_label, tgt_key, tgt_val):
        try:
            self._conn().execute(
                f"MATCH (a:{src_label}), (b:{tgt_label}) "
                f"WHERE a.{src_key}='{self._q(src_val)}' AND b.{tgt_key}='{self._q(tgt_val)}' "
                f"CREATE (a)-[:{rel}]->(b)")
            return True
        except Exception:
            return False

    def _find_model_by_ref(self, ref: str) -> Optional[str]:
        for field in ("ontology_uri", "base_uri"):
            r = self._conn().execute(
                f"MATCH (m:OntologyModel) WHERE m.{field}='{self._q(ref)}' RETURN m.model_id")
            for row in r:
                return row[0]
        return None

    def _find_model_by_source(self, source_file: str) -> Optional[str]:
        r = self._conn().execute(
            f"MATCH (m:OntologyModel) WHERE m.source_file='{self._q(source_file)}' RETURN m.model_id")
        for row in r:
            return row[0]
        return None

    def load_ttl(self, file_name: str, model_id: Optional[str] = None, label: Optional[str] = None) -> dict:
        path = Path(file_name)
        if not path.is_absolute():
            path = (Path(settings.ontology_dir) / path.name).resolve()
        path = path.resolve()
        if not path.exists():
            raise FileNotFoundError(f"本体文件不存在: {path}")

        data = self.parse_ttl(str(path))
        # 若未显式指定模型标识，按源文件匹配已存在模型，实现“更新”语义
        if not (model_id or "").strip():
            existing = self._find_model_by_source(data["source_file"])
            if existing:
                model_id = existing
        model_id = (model_id or "").strip() or data["model_id"]
        label = (label or "").strip() or data["label"]
        self.init_tables()
        conn = self._conn()
        q = self._q

        # 1. 模型节点（upsert）
        conn.execute(
            f"MERGE (m:OntologyModel {{model_id:'{q(model_id)}'}}) "
            f"SET m.label='{q(label)}', m.ontology_uri='{q(data['ontology_uri'])}', "
            f"m.base_uri='{q(data['base_uri'])}', m.prefix='{q(data['prefix'])}', "
            f"m.source_file='{q(data['source_file'])}', m.updated_at='{datetime.now().isoformat()}' "
            f"RETURN m.model_id")

        own_class_uris = [c["uri"] for c in data["classes"] if c["is_own"]]
        own_prop_uris = [p["uri"] for p in data["properties"] if p["is_own"]]

        # 2. 删除本模型自身的出边（保留其他模型指向本模型节点的引用边）
        for uri in own_class_uris:
            conn.execute(f"MATCH (c:OntoClass)-[r:OntoSubClass]->() WHERE c.uri='{q(uri)}' DELETE r")
            conn.execute(f"MATCH (c:OntoClass)-[r:OntoEquivalentClass]->() WHERE c.uri='{q(uri)}' DELETE r")
        for uri in own_prop_uris:
            conn.execute(f"MATCH (p:OntoProperty)-[r:OntoDomain]->() WHERE p.uri='{q(uri)}' DELETE r")
            conn.execute(f"MATCH (p:OntoProperty)-[r:OntoRange]->() WHERE p.uri='{q(uri)}' DELETE r")

        # 3. 删除已不存在的本模型节点
        existing = conn.execute(f"MATCH (c:OntoClass) WHERE c.model_id='{q(model_id)}' RETURN c.uri")
        for row in existing:
            if row[0] not in own_class_uris:
                conn.execute(f"MATCH (c:OntoClass) WHERE c.uri='{q(row[0])}' DETACH DELETE c")
        existing_p = conn.execute(f"MATCH (p:OntoProperty) WHERE p.model_id='{q(model_id)}' RETURN p.uri")
        for row in existing_p:
            if row[0] not in own_prop_uris:
                conn.execute(f"MATCH (p:OntoProperty) WHERE p.uri='{q(row[0])}' DETACH DELETE p")

        # 4. 写入本模型类
        for c in data["classes"]:
            if not c["is_own"]:
                continue
            pfx = c["prefix"] or data["prefix"]
            short = f"{pfx}:{c['name']}" if pfx else c["name"]
            conn.execute(
                f"MERGE (c:OntoClass {{uri:'{q(c['uri'])}'}}) "
                f"SET c.model_id='{q(model_id)}', c.name='{q(c['name'])}', c.prefix='{q(pfx)}', "
                f"c.short_uri='{q(short)}', c.label='{q(c['label'])}', c.comment='{q(c['comment'])}', "
                f"c.description='{q(c['description'])}', c.extra_json='{q(c['extra_json'])}' "
                f"RETURN c.uri")

        # 5. 写入本模型属性
        for p in data["properties"]:
            if not p["is_own"]:
                continue
            pfx = p["prefix"] or data["prefix"]
            short = f"{pfx}:{p['name']}" if pfx else p["name"]
            conn.execute(
                f"MERGE (p:OntoProperty {{uri:'{q(p['uri'])}'}}) "
                f"SET p.model_id='{q(model_id)}', p.name='{q(p['name'])}', p.prefix='{q(pfx)}', "
                f"p.short_uri='{q(short)}', p.label='{q(p['label'])}', p.comment='{q(p['comment'])}', "
                f"p.description='{q(p['description'])}', p.prop_type='{q(p['prop_type'])}', "
                f"p.extra_json='{q(p['extra_json'])}' RETURN p.uri")

        # 6. 重建层级 / 定义域 / 值域边（含跨模型引用）
        for c in data["classes"]:
            if not c["is_own"]:
                continue
            for parent in c["parents"]:
                self._create_edge("OntoSubClass", "OntoClass", "uri", c["uri"], "OntoClass", "uri", parent)
            for eq in c.get("equivalents", []):
                self._create_edge("OntoEquivalentClass", "OntoClass", "uri", c["uri"], "OntoClass", "uri", eq)
        for p in data["properties"]:
            if not p["is_own"]:
                continue
            for d in p["domains"]:
                self._create_edge("OntoDomain", "OntoProperty", "uri", p["uri"], "OntoClass", "uri", d)
            for r in p["ranges"]:
                self._create_edge("OntoRange", "OntoProperty", "uri", p["uri"], "OntoClass", "uri", r)

        # 7. 重建 import 边
        conn.execute(f"MATCH (m:OntologyModel)-[r:OntoImports]->() WHERE m.model_id='{q(model_id)}' DELETE r")
        imported = 0
        for imp in data["imports"]:
            target = self._find_model_by_ref(imp)
            if target and target != model_id:
                if self._create_edge("OntoImports", "OntologyModel", "model_id", model_id,
                                     "OntologyModel", "model_id", target):
                    imported += 1

        self._invalidate()
        return {
            "status": "loaded",
            "model_id": model_id,
            "label": label,
            "classes": len(own_class_uris),
            "properties": len(own_prop_uris),
            "imports": imported,
        }

    def auto_load_from_config(self) -> List[dict]:
        """应用启动时，若本体库为空，则把 .env 配置的本体文件加载入图（自动识别模型标识）。"""
        if self.list_models():
            return []
        loaded = []
        for m in settings.ontology_models:
            rel = m.full_file or m.light_file
            if not rel:
                continue
            fp = Path(settings.ontology_dir) / rel
            if not fp.exists():
                fp = Path(rel)
            if not fp.exists() or not fp.suffix.lower().endswith((".ttl", ".rdf", ".owl")):
                continue
            try:
                loaded.append(self.load_ttl(str(fp)))
            except Exception as exc:
                print(f"[Bodhi] 自动加载本体失败 {fp}: {exc}", flush=True)
        return loaded

    # ---------- 查询（模型） ----------
    def list_models(self) -> List[dict]:
        self.init_tables()
        conn = self._conn()
        r = conn.execute("MATCH (m:OntologyModel) RETURN m.* ORDER BY m.model_id")
        cols = r.get_column_names()
        models = []
        for row in r:
            rec = dict(zip(cols, row))
            mid = rec.get("m.model_id", "")
            src = rec.get("m.source_file", "")
            env = self._env_config(src, mid)
            models.append({
                "name": mid,
                "label": rec.get("m.label", "") or mid,
                "model_id": mid,
                "ontology_uri": rec.get("m.ontology_uri", ""),
                "base_uri": rec.get("m.base_uri", ""),
                "prefix": rec.get("m.prefix", ""),
                "source_file": src,
                "updated_at": rec.get("m.updated_at", ""),
                "class_count": self._count("OntoClass", "model_id", mid),
                "property_count": self._count("OntoProperty", "model_id", mid),
                "has_full": env.has_full() if env else False,
                "full_file": env.full_file if env else "",
                "light_file": env.light_file if env else "",
                "expert_role": env.expert_role if env else "",
            })
        return models

    def _env_config(self, source_file: str, model_id: str = ""):
        """按源文件或名称匹配 .env 配置（用于完整版/轻量版等元信息）"""
        for cfg in settings.ontology_models:
            if cfg.name == model_id or (source_file and source_file in (cfg.full_file, cfg.light_file)):
                return cfg
        return None

    def get_model(self, model_id: str) -> Optional[dict]:
        for m in self.list_models():
            if m["model_id"] == model_id:
                return m
        return None

    def _count(self, label, key, value) -> int:
        r = self._conn().execute(f"MATCH (n:{label}) WHERE n.{key}='{self._q(value)}' RETURN count(*) AS c")
        for row in r:
            return int(row[0])
        return 0

    def get_model_classes(self, model_id: str) -> List[dict]:
        return self._node_list("OntoClass", model_id)

    def get_model_properties(self, model_id: str) -> List[dict]:
        return self._node_list("OntoProperty", model_id)

    def _node_list(self, label, model_id) -> List[dict]:
        conn = self._conn()
        r = conn.execute(f"MATCH (n:{label}) WHERE n.model_id='{self._q(model_id)}' RETURN n.* ORDER BY n.name")
        cols = r.get_column_names()
        out = []
        for row in r:
            rec = dict(zip(cols, row))
            out.append({k[2:]: v for k, v in rec.items() if k.startswith("n.")})
        return out

    def get_imported_model_ids(self, model_id: str) -> List[str]:
        """递归收集本模型 import 的模型 ID（含间接 import）"""
        conn = self._conn()
        out = []
        seen = {model_id}
        frontier = [model_id]
        while frontier:
            cur = frontier.pop(0)
            r = conn.execute(
                f"MATCH (m:OntologyModel)-[:OntoImports]->(t:OntologyModel) WHERE m.model_id='{self._q(cur)}' RETURN t.model_id")
            for row in r:
                tid = row[0]
                if tid not in seen:
                    seen.add(tid)
                    out.append(tid)
                    frontier.append(tid)
        return out

    def get_model_classes_including_imports(self, model_id: str) -> List[dict]:
        """本模型类 + 所有 import 模型的类（每项带 from_import 标记）"""
        classes = self.get_model_classes(model_id)
        for c in classes:
            c["from_import"] = False
        for iid in self.get_imported_model_ids(model_id):
            for c in self.get_model_classes(iid):
                c["from_import"] = True
                classes.append(c)
        return classes

    def get_model_properties_including_imports(self, model_id: str) -> List[dict]:
        """本模型属性 + 所有 import 模型的属性（每项带 from_import 标记）"""
        props = self.get_model_properties(model_id)
        for p in props:
            p["from_import"] = False
        for iid in self.get_imported_model_ids(model_id):
            for p in self.get_model_properties(iid):
                p["from_import"] = True
                props.append(p)
        return props

    def get_model_graph(self, model_id: str) -> dict:
        """本体模型结构图数据：类节点（中文名）+ 对象属性/子类/等价类关系边，含 import 模型"""
        classes, props, sub_edges, domains, ranges = self._snapshot()
        allowed = {model_id} | set(self.get_imported_model_ids(model_id))
        conn = self._conn()
        node_ids = set()
        nodes = []
        for u, c in classes.items():
            if c["model_id"] in allowed:
                nodes.append({
                    "id": u,
                    "name": c["label"] or c["name"],          # 中文 label 优先
                    "short_uri": c["short_uri"] or c["name"],
                    "model_id": c["model_id"],
                    "from_import": c["model_id"] != model_id,
                })
                node_ids.add(u)
        edges = []
        # 父子类：子 → 父
        for c, p in sub_edges:
            if c in node_ids and p in node_ids:
                edges.append({"source": c, "target": p, "type": "subclass", "label": "子类"})
        # 等价类
        r = conn.execute("MATCH (a:OntoClass)-[:OntoEquivalentClass]->(b:OntoClass) RETURN a.uri, b.uri")
        for row in r:
            a, b = row[0], row[1]
            if a in node_ids and b in node_ids:
                edges.append({"source": a, "target": b, "type": "equivalent", "label": "等价"})
        # 对象属性：域 → 值域，边标注属性中文 label（数据属性不展示）
        dom_map, rng_map = {}, {}
        for pu, cu in domains:
            dom_map.setdefault(pu, []).append(cu)
        for pu, cu in ranges:
            rng_map.setdefault(pu, []).append(cu)
        for pu, p in props.items():
            if p.get("prop_type") != "ObjectProperty":
                continue
            if p["model_id"] not in allowed:
                continue
            plabel = p["label"] or p["name"]
            for src in dom_map.get(pu, []):
                for tgt in rng_map.get(pu, []):
                    if src in node_ids and tgt in node_ids:
                        edges.append({"source": src, "target": tgt, "type": "objectproperty", "label": plabel})
        return {"nodes": nodes, "edges": edges, "imported_models": self.get_imported_model_ids(model_id)}

    def list_files(self) -> List[dict]:
        """列出 ontology_dir 下可加载的 .ttl/.rdf/.owl 文件"""
        base = Path(settings.ontology_dir)
        files = []
        if base.exists():
            for fp in sorted(base.glob("*")):
                if fp.is_file() and fp.suffix.lower() in (".ttl", ".rdf", ".owl"):
                    files.append({"file": fp.name, "path": str(fp)})
        return files

    # ---------- 删除 ----------
    def delete_model(self, model_id: str) -> bool:
        conn = self._conn()
        q = self._q
        # 删除 import 边（本模型作为源）
        conn.execute(f"MATCH (m:OntologyModel)-[r:OntoImports]->() WHERE m.model_id='{q(model_id)}' DELETE r")
        # 删除本模型节点的所有边，再删节点
        conn.execute(f"MATCH (c:OntoClass) WHERE c.model_id='{q(model_id)}' DETACH DELETE c")
        conn.execute(f"MATCH (p:OntoProperty) WHERE p.model_id='{q(model_id)}' DETACH DELETE p")
        conn.execute(f"MATCH (m:OntologyModel) WHERE m.model_id='{q(model_id)}' DELETE m")
        self._invalidate()
        return True

    def delete_class(self, uri: str) -> bool:
        self._conn().execute(f"MATCH (c:OntoClass) WHERE c.uri='{self._q(uri)}' DETACH DELETE c")
        self._invalidate()
        return True

    def delete_property(self, uri: str) -> bool:
        self._conn().execute(f"MATCH (p:OntoProperty) WHERE p.uri='{self._q(uri)}' DETACH DELETE p")
        self._invalidate()
        return True

    # ---------- 快照 ----------
    @staticmethod
    def _short_uri(rec: dict) -> str:
        """前缀:名称 短 URI（如 bmm:Goal），无前缀则用名称"""
        pfx = rec.get("prefix", "") or ""
        name = rec.get("name", "") or ""
        return f"{pfx}:{name}" if pfx else name

    def _all_classes(self) -> Dict[str, dict]:
        conn = self._conn()
        r = conn.execute("MATCH (c:OntoClass) RETURN c.uri, c.model_id, c.name, c.prefix, c.label, c.comment, c.description")
        cols = r.get_column_names()
        out = {}
        for row in r:
            rec = dict(zip(cols, row))
            c = {
                "uri": rec.get("c.uri", ""),
                "model_id": rec.get("c.model_id", ""),
                "name": rec.get("c.name", ""),
                "prefix": rec.get("c.prefix", ""),
                "label": rec.get("c.label", ""),
                "comment": rec.get("c.comment", ""),
                "description": rec.get("c.description", ""),
            }
            c["short_uri"] = self._short_uri(c)
            out[rec.get("c.uri", "")] = c
        return out

    def _all_properties(self) -> Dict[str, dict]:
        conn = self._conn()
        r = conn.execute("MATCH (p:OntoProperty) RETURN p.uri, p.model_id, p.name, p.prefix, p.label, p.comment, p.description, p.prop_type")
        cols = r.get_column_names()
        out = {}
        for row in r:
            rec = dict(zip(cols, row))
            p = {
                "uri": rec.get("p.uri", ""),
                "model_id": rec.get("p.model_id", ""),
                "name": rec.get("p.name", ""),
                "prefix": rec.get("p.prefix", ""),
                "label": rec.get("p.label", ""),
                "comment": rec.get("p.comment", ""),
                "description": rec.get("p.description", ""),
                "prop_type": rec.get("p.prop_type", ""),
            }
            p["short_uri"] = self._short_uri(p)
            out[rec.get("p.uri", "")] = p
        return out

    def _all_edges(self, rel) -> List[tuple]:
        conn = self._conn()
        if rel == "OntoSubClass":
            r = conn.execute("MATCH (c:OntoClass)-[:OntoSubClass]->(p:OntoClass) RETURN c.uri, p.uri")
        elif rel == "OntoDomain":
            r = conn.execute("MATCH (p:OntoProperty)-[:OntoDomain]->(c:OntoClass) RETURN p.uri, c.uri")
        else:
            r = conn.execute("MATCH (p:OntoProperty)-[:OntoRange]->(c:OntoClass) RETURN p.uri, c.uri")
        return [(row[0], row[1]) for row in r]

    def _snapshot(self):
        """读取全部本体数据并缓存（数据变更时自动失效）"""
        if self._snap is None:
            self._snap = (
                self._all_classes(),
                self._all_properties(),
                self._all_edges("OntoSubClass"),
                self._all_edges("OntoDomain"),
                self._all_edges("OntoRange"),
            )
        return self._snap

    def _all_equivalent_edges(self) -> List[tuple]:
        if "equiv_edges" in self._memo:
            return self._memo["equiv_edges"]
        conn = self._conn()
        r = conn.execute("MATCH (a:OntoClass)-[:OntoEquivalentClass]->(b:OntoClass) RETURN a.uri, b.uri")
        out = [(row[0], row[1]) for row in r]
        self._memo["equiv_edges"] = out
        return out

    def _hierarchy(self) -> tuple:
        """返回 (children, parents) 子类层次，等价类按双向子类关系合并"""
        _classes, _props, sub_edges, _domains, _ranges = self._snapshot()
        children: Dict[str, set] = {}
        parents: Dict[str, set] = {}
        for c, p in sub_edges:
            children.setdefault(p, set()).add(c)
            parents.setdefault(c, set()).add(p)
        for a, b in self._all_equivalent_edges():
            # 双向合并，兼容 union（A ≡ B∪C：成员是 A 的子类）与 intersection（A ≡ B∩C：A 是 B 的子类）
            children.setdefault(b, set()).add(a)
            parents.setdefault(a, set()).add(b)
            children.setdefault(a, set()).add(b)
            parents.setdefault(b, set()).add(a)
        return children, parents

    def _is_instantiable_class(self, c: dict) -> bool:
        return c.get("name") not in TREE_SKIP and c.get("name") not in ABSTRACT_CLASSES

    def _default_model(self) -> str:
        models = self.list_models()
        if not models:
            return ""
        return models[0]["model_id"]

    def _model_base_uri(self, model_id: str) -> str:
        m = self.get_model(model_id)
        return m["base_uri"] if m else ""

    def _model_label(self, model_id: str) -> str:
        m = self.get_model(model_id)
        return (m["label"] if m else "") or model_id

    def _included_uris(self, model_id: str) -> set:
        """本模型类 + 被引用的外部模型类（含其祖先与后代），用于概念树与类型约束。"""
        classes, props, edges, domains, ranges = self._snapshot()

        children: Dict[str, list] = {}
        parents: Dict[str, list] = {}
        for c, p in edges:
            children.setdefault(p, []).append(c)
            parents.setdefault(c, []).append(p)

        own = {u for u, c in classes.items() if c["model_id"] == model_id}
        own_props = {u for u, p in props.items() if p["model_id"] == model_id}

        def is_foreign(u):
            return u in classes and classes[u]["model_id"] != model_id

        foreign_roots = set()
        for c, p in edges:
            if c in own and is_foreign(p):
                foreign_roots.add(p)
        for p_uri, c_uri in domains:
            if p_uri in own_props and is_foreign(c_uri):
                foreign_roots.add(c_uri)
        for p_uri, c_uri in ranges:
            if p_uri in own_props and is_foreign(c_uri):
                foreign_roots.add(c_uri)

        included = set(own)
        for root in foreign_roots:
            stack = [root]
            while stack:
                u = stack.pop()
                if u not in classes or u in included:
                    continue
                included.add(u)
                for p in parents.get(u, []):
                    if p in classes:
                        stack.append(p)
                for ch in children.get(u, []):
                    if ch in classes:
                        stack.append(ch)
        return included

    # ---------- 对外查询 ----------
    def get_concept_tree(self, model_id: Optional[str] = None) -> dict:
        model_id = model_id or self._default_model()
        if not model_id:
            return {}
        key = ("tree", model_id)
        if key in self._memo:
            return self._memo[key]
        classes, _props, edges, _domains, _ranges = self._snapshot()
        included = {u for u in self._included_uris(model_id)
                    if classes.get(u, {}).get("name") not in TREE_SKIP}

        children_included: Dict[str, list] = {}
        parent_included: Dict[str, list] = {}
        for c, p in edges:
            if c in included and p in included:
                children_included.setdefault(p, []).append(c)
                parent_included.setdefault(c, []).append(p)

        def disp(u):
            c = classes[u]
            name = c["name"]
            lab = c["label"] or name
            if c["model_id"] != model_id:
                pfx = c["prefix"] or c["model_id"]
                return f"{pfx}:{lab}"
            return lab

        def build(u):
            c = classes[u]
            kids = sorted(set(children_included.get(u, [])), key=lambda x: classes[x]["name"])
            if not kids:
                return disp(u)
            node = {"label": disp(u)}
            sub = {}
            for kid in kids:
                res = build(kid)
                k = classes[kid].get("short_uri") or classes[kid]["name"]
                if isinstance(res, str):
                    sub[k] = res
                else:
                    sub[k] = res
            node["children"] = sub
            return node

        roots = sorted([u for u in included if u not in parent_included],
                       key=lambda x: classes[x]["name"])
        root_children = {}
        for r in roots:
            root_children[classes[r].get("short_uri") or classes[r]["name"]] = build(r)

        model_label = self._model_label(model_id)
        result = {model_id: {"label": model_label, "children": root_children}}
        self._memo[key] = result
        return result

    def get_concept_map(self, model_id: Optional[str] = None) -> dict:
        """返回 {父类型: [直接子类型]} 映射（用于概念筛选展开）"""
        model_id = model_id or self._default_model()
        if not model_id:
            return {}
        key = ("concept_map", model_id)
        if key in self._memo:
            return self._memo[key]
        classes, _props, edges, _domains, _ranges = self._snapshot()
        included = {u for u in self._included_uris(model_id)
                    if classes.get(u, {}).get("name") not in TREE_SKIP}
        cm: Dict[str, list] = {}
        for c, p in edges:
            if c in included and p in included:
                cn = classes[c].get("short_uri") or classes[c]["name"]
                pn = classes[p].get("short_uri") or classes[p]["name"]
                cm.setdefault(pn, [])
                if cn not in cm[pn]:
                    cm[pn].append(cn)
        self._memo[key] = cm
        return cm

    def expand_node_type(self, name: str) -> List[str]:
        """把（带前缀的）节点类型展开为可实例化类型集合（自身 + 所有子类 + 等价类）"""
        classes, _props, _edges, _domains, _ranges = self._snapshot()
        uri = self._find_class_uri_by_short(name)
        if not uri:
            return [name] if self._is_instantiable_class(
                {"name": name.split(":")[-1]}) else []
        children, _parents = self._hierarchy()
        reachable = set()
        stack = [uri]
        while stack:
            u = stack.pop()
            if u not in classes or u in reachable:
                continue
            reachable.add(u)
            for ch in children.get(u, []):
                if ch not in reachable:
                    stack.append(ch)
        result = sorted({classes[u].get("short_uri") or classes[u]["name"]
                         for u in reachable if self._is_instantiable_class(classes[u])})
        return result or [name]

    def _find_class_uri_by_short(self, short: str) -> str:
        classes = self._all_classes()
        # 1) 精确匹配短 URI 或本地名
        for u, c in classes.items():
            if (c.get("short_uri") == short) or (c["name"] == short):
                return u
        # 2) 去掉前缀后按本地名匹配（无歧义或唯一时）
        local = short.split(":")[-1]
        matches = [u for u, c in classes.items() if c["name"] == local]
        if len(matches) == 1:
            return matches[0]
        return ""

    def _find_prop_uri_by_short(self, short: str) -> str:
        props = self._all_properties()
        for u, p in props.items():
            if (p.get("short_uri") == short) or (p["name"] == short) or (p["name"] == short.split(":")[-1]):
                return u
        return ""

    def node_label(self, name: str) -> str:
        return self.get_labels()["node_types"].get(name, name)

    def rel_label(self, name: str) -> str:
        return self.get_labels()["rel_types"].get(name, name)

    def get_node_types(self, model_id: Optional[str] = None) -> List[dict]:
        model_id = model_id or self._default_model()
        if not model_id:
            return []
        key = ("node_types", model_id)
        if key in self._memo:
            return self._memo[key]
        classes, _props, _edges, _domains, _ranges = self._snapshot()
        included = self._included_uris(model_id)
        result = []
        seen = set()
        for u in included:
            c = classes.get(u)
            if not c or not self._is_instantiable_class(c):
                continue
            su = c.get("short_uri") or c["name"]
            if su in seen:
                continue
            seen.add(su)
            result.append({"name": su, "label": c["label"] or c["name"]})
        result.sort(key=lambda x: x["name"].split(":")[-1])
        self._memo[key] = result
        return result

    def _ancestors(self, uri: str, edges: List[tuple]) -> set:
        """祖先集合（含等价类双向合并）"""
        _children, parents = self._hierarchy()
        result = {uri}
        stack = [uri]
        while stack:
            u = stack.pop()
            for p in parents.get(u, []):
                if p not in result:
                    result.add(p)
                    stack.append(p)
        return result

    def get_rel_types_for_source(self, model_id: str, source_type: str) -> List[dict]:
        key = ("rel_types", model_id or "", source_type)
        if key in self._memo:
            return self._memo[key]
        classes, props, edges, domains, ranges = self._snapshot()

        src_uri = self._find_class_uri_by_short(source_type)
        if not src_uri:
            return []
        anc = self._ancestors(src_uri, edges)

        result = []
        seen = set()
        for pu, p in props.items():
            su = p.get("short_uri") or p["name"]
            if su in seen:
                continue
            for d_uri, c_uri in domains:
                if d_uri == pu and c_uri in anc:
                    seen.add(su)
                    result.append({
                        "name": su,
                        "label": p["label"] or p["name"],
                        "comment": p["comment"],
                        "ranges": [classes[r].get("short_uri") or classes[r]["name"]
                                   for r in [x for y, x in ranges if y == pu] if r in classes],
                    })
                    break
        self._memo[key] = result
        return result

    def get_rel_target_types(self, model_id: str, rel_type: str) -> List[str]:
        key = ("rel_targets", model_id or "", rel_type)
        if key in self._memo:
            return self._memo[key]
        classes, _props, _edges, _domains, ranges = self._snapshot()

        prop_uri = self._find_prop_uri_by_short(rel_type)
        if not prop_uri:
            return []

        children, _parents = self._hierarchy()

        builtin_any = False
        has_range = False
        reachable = set()
        for p_uri, r_uri in ranges:
            if p_uri != prop_uri:
                continue
            has_range = True
            if r_uri not in classes:
                # owl:Thing / owl:Resource / rdfs:Resource 等内建 range → 任意类型
                if r_uri.startswith(_BUILTIN_NS):
                    builtin_any = True
                continue
            stack = [r_uri]
            while stack:
                u = stack.pop()
                if u not in classes or u in reachable:
                    continue
                reachable.add(u)
                for ch in children.get(u, []):
                    if ch not in reachable:
                        stack.append(ch)

        # 属性未持久化任何 range（含 owl:Thing/owl:Resource 内建范围）→ 视为任意类型
        if not has_range:
            builtin_any = True

        if builtin_any:
            included = self._included_uris(model_id or self._default_model())
            result = sorted({classes[u].get("short_uri") or classes[u]["name"]
                             for u in included if self._is_instantiable_class(classes[u])})
        else:
            result = sorted({classes[u].get("short_uri") or classes[u]["name"]
                             for u in reachable if self._is_instantiable_class(classes[u])})
        self._memo[key] = result
        return result

    def resolve_node_type(self, raw: str, model_id: str = "") -> str:
        """把 LLM 返回的类型名解析为带前缀的类型标识（如 Goal → bmm:Goal）"""
        if not raw:
            return ""
        raw = raw.strip()
        classes = self._all_classes()
        # 已带前缀（bmm:Goal）
        if raw in {c.get("short_uri") for c in classes.values()}:
            return raw
        local = raw.split(":")[-1]
        if model_id:
            for u, c in classes.items():
                if c["name"] == local and c["model_id"] == model_id:
                    return c.get("short_uri") or c["name"]
        matches = [c for c in classes.values() if c["name"] == local]
        if len(matches) == 1:
            return matches[0].get("short_uri") or matches[0]["name"]
        return ""

    def resolve_rel_type(self, raw: str, model_id: str = "") -> str:
        """把 LLM 返回的关系类型名解析为带前缀的关系标识"""
        if not raw:
            return ""
        raw = raw.strip()
        # 全格式 "A -relType-> B" → 取中间
        m = re.match(r"^\S+\s+-(\S+)->\s+\S+$", raw)
        if m:
            raw = m.group(1)
        props = self._all_properties()
        if raw in {p.get("short_uri") for p in props.values()}:
            return raw
        local = raw.split(":")[-1]
        if model_id:
            for u, p in props.items():
                if p["name"] == local and p["model_id"] == model_id:
                    return p.get("short_uri") or p["name"]
        # 容错：包含匹配（处理 LLM 拼装如 influencesDesiredResult）
        cands = [p for p in props.values() if p["name"] in local or local in p["name"]]
        if cands:
            cands.sort(key=lambda p: len(p["name"]), reverse=True)
            return cands[0].get("short_uri") or cands[0]["name"]
        matches = [p for p in props.values() if p["name"] == local]
        if len(matches) == 1:
            return matches[0].get("short_uri") or matches[0]["name"]
        return ""

    def get_labels(self) -> dict:
        if "labels" in self._memo:
            return self._memo["labels"]
        classes, props, _edges, _domains, _ranges = self._snapshot()
        node_labels = {c.get("short_uri") or c["name"]: c["label"] or c["name"] for c in classes.values()}
        rel_labels = {p.get("short_uri") or p["name"]: p["label"] or p["name"] for p in props.values()}
        result = {"node_types": node_labels, "rel_types": rel_labels}
        self._memo["labels"] = result
        return result

    def get_class_info(self, ref: str) -> Optional[dict]:
        """按短 URI 或本地名解析本体概念，返回 wiki 展示信息"""
        classes = self._all_classes()
        uri = self._find_class_uri_by_short(ref)
        if not uri:
            return None
        c = classes.get(uri)
        if not c:
            return None
        return {
            "kind": "onto",
            "short_uri": c.get("short_uri") or c["name"],
            "uri": c["uri"],
            "name": c["name"],
            "label": c["label"] or c["name"],
            "description": c["description"] or c["comment"],
            "comment": c["comment"],
        }

    def search_classes(self, q: str = "", limit: int = 50) -> List[dict]:
        """搜索本体概念（供标注选择器），返回带短 URI 的概念列表"""
        classes = self._all_classes()
        result = []
        for u, c in classes.items():
            if c["name"] in TREE_SKIP:
                continue
            hay = f"{c['name']} {c['label']} {c.get('short_uri') or ''}"
            if q and q.lower() not in hay.lower():
                continue
            result.append({
                "short_uri": c.get("short_uri") or c["name"],
                "name": c["name"],
                "label": c["label"] or c["name"],
                "description": c["description"] or c["comment"],
            })
            if len(result) >= limit:
                break
        return result


ontology_service = OntologyService()
