"""BMM 本体加载器 — 解析 TTL 文件并生成约束"""

from pathlib import Path
from rdflib import Graph, RDF, RDFS, OWL
from typing import Dict, List, Set


class OntologyLoader:
    """加载并解析 BMM_EXTENDED.ttl 本体文件"""

    BMM_NS = "http://example.org/bmm#"

    def __init__(self, ontology_path: str):
        self.ontology_path = Path(ontology_path).resolve()
        self.graph = Graph()
        with open(self.ontology_path, "rb") as f:
            self.graph.parse(f, format="turtle")

        # 缓存
        self._classes: Dict[str, dict] = {}
        self._object_properties: Dict[str, dict] = {}
        self._datatype_properties: Dict[str, dict] = {}
        self._class_hierarchy: Dict[str, List[str]] = {}
        self._property_domains: Dict[str, List[str]] = {}
        self._property_ranges: Dict[str, List[str]] = {}

        self._parse()

    def _parse(self):
        """解析本体文件"""
        # 解析类（同时存储 URIRef 引用，支持多命名空间）
        self._class_urirefs = {}
        self._namespaces = set()
        for cls in self.graph.subjects(RDF.type, OWL.Class):
            cls_uri = str(cls)
            # 过滤匿名节点和 Restriction 节点
            if not cls_uri.startswith("http"):
                continue
            if "#" in cls_uri:
                self._namespaces.add(cls_uri.rsplit("#", 1)[0] + "#")
            elif "/" in cls_uri:
                self._namespaces.add(cls_uri.rsplit("/", 1)[0] + "/")
            label = self._get_label(cls)
            comment = self._get_comment(cls)
            self._classes[cls_uri] = {
                "uri": cls_uri,
                "local_name": self._local_name(cls_uri),
                "label": label,
                "comment": comment,
            }
            self._class_urirefs[cls_uri] = cls  # 保存 URIRef

        # 解析对象属性（不限命名空间，仅过滤非 http URI）
        for prop in self.graph.subjects(RDF.type, OWL.ObjectProperty):
            prop_uri = str(prop)
            if not prop_uri.startswith("http"):
                continue
            label = self._get_label(prop)
            comment = self._get_comment(prop)
            domains = self._resolve_property_targets(prop, RDFS.domain)
            ranges = self._resolve_property_targets(prop, RDFS.range)

            self._object_properties[prop_uri] = {
                "uri": prop_uri,
                "local_name": self._local_name(prop_uri),
                "label": label,
                "comment": comment,
                "domains": domains,
                "ranges": ranges,
            }

        # 解析数据属性
        for prop in self.graph.subjects(RDF.type, OWL.DatatypeProperty):
            prop_uri = str(prop)
            if not prop_uri.startswith("http"):
                continue
            label = self._get_label(prop)
            comment = self._get_comment(prop)
            domains = self._get_property_values(prop, RDFS.domain)

            self._datatype_properties[prop_uri] = {
                "uri": prop_uri,
                "local_name": self._local_name(prop_uri),
                "label": label,
                "comment": comment,
                "domains": domains,
            }

        # 构建类层级（保留 owl:Thing 引用用于判断一级概念）
        self.OWL_THING = "http://www.w3.org/2002/07/owl#Thing"
        for cls_uri, cls_ref in self._class_urirefs.items():
            parents = []
            for parent in self.graph.objects(cls_ref, RDFS.subClassOf):
                parent_uri = str(parent)
                if parent_uri in self._classes or parent_uri == self.OWL_THING:
                    parents.append(parent_uri)
            self._class_hierarchy[cls_uri] = parents

    @staticmethod
    def _local_name(uri: str) -> str:
        """从 URI 中提取局部名称（# 或 / 分隔）"""
        return uri.split("#")[-1] if "#" in uri else uri.rsplit("/", 1)[-1]

    def _get_label(self, subject) -> str:
        for label in self.graph.objects(subject, RDFS.label):
            return str(label)
        return ""

    def _get_comment(self, subject) -> str:
        for comment in self.graph.objects(subject, RDFS.comment):
            return str(comment)
        return ""

    def _get_property_values(self, subject, prop) -> List[str]:
        values = []
        for obj in self.graph.objects(subject, prop):
            uri = str(obj)
            if uri.startswith("http"):
                values.append(uri)
        return values

    def _rdf_list_items(self, list_node) -> List[str]:
        """展开 RDF 列表（owl:unionOf / owl:intersectionOf 的成员）"""
        from rdflib import RDF as _RDF
        items = []
        node = list_node
        while node is not None:
            first = self.graph.value(node, _RDF.first)
            if first is not None:
                items.append(str(first))
            rest = self.graph.value(node, _RDF.rest)
            if rest is None or str(rest) == str(_RDF.nil):
                break
            node = rest
        return items

    def _resolve_property_targets(self, subject, predicate) -> List[str]:
        """解析属性取值，展开 owl:unionOf/intersectionOf 匿名节点"""
        result = []
        for obj in self.graph.objects(subject, predicate):
            obj_str = str(obj)
            if obj_str.startswith("http"):
                result.append(obj_str)
            else:
                for member in self.graph.objects(obj, OWL.unionOf):
                    result.extend(self._rdf_list_items(member))
                for member in self.graph.objects(obj, OWL.intersectionOf):
                    result.extend(self._rdf_list_items(member))
        return result

    def get_classes(self) -> Dict[str, dict]:
        return self._classes

    def get_object_properties(self) -> Dict[str, dict]:
        return self._object_properties

    def get_datatype_properties(self) -> Dict[str, dict]:
        return self._datatype_properties

    def get_class_hierarchy(self) -> Dict[str, List[str]]:
        return self._class_hierarchy

    def to_concept_tree(self) -> dict:
        """根据 TTL 中的 rdfs:subClassOf 生成概念树，模型名作为根节点"""
        children = {}
        labels = {}
        has_known_parent = set()
        for cls_uri, info in self._classes.items():
            local = info["local_name"]
            labels[local] = info.get("label", "") or local
            parents = self._class_hierarchy.get(cls_uri, [])
            for parent_uri in parents:
                p_local = self._classes.get(parent_uri, {}).get("local_name", "")
                if p_local:
                    children.setdefault(p_local, []).append(local)
                    has_known_parent.add(local)
        skip = {"SourceDocument", "Regulation", "BusinessRequirementDoc",
                "TechnicalSolutionDoc", "Excerpt", "EnforcementLevel",
                "AssessmentType", "Strict", "Override", "Advisory",
                "Strength", "Weakness", "Opportunity", "Threat"}
        # 一级概念：owl:Thing 的子类 或 无已知父类（如 FlowNode 用 equivalentClass 定义）
        all_locals = set(labels.keys())
        first_level = [r for r in all_locals if r not in has_known_parent and r not in skip]

        def build_node(local_name):
            label = labels.get(local_name, local_name)
            kids = [c for c in children.get(local_name, []) if c not in skip]
            if not kids:
                return label
            node = {"label": label}
            sub = {}
            for kid in sorted(kids):
                result = build_node(kid)
                if isinstance(result, str):
                    sub[kid] = result
                else:
                    sub[kid] = result
            node["children"] = sub
            return node

        # 模型名作为根节点
        model_name = self.ontology_path.stem
        root_children = {}
        for fl in sorted(first_level):
            root_children[fl] = build_node(fl)
        return {model_name: {"label": model_name, "children": root_children}}

    def _is_subclass_of(self, cls_uri: str, parent_uri: str) -> bool:
        """检查 cls 是否是 parent 的子类"""
        if cls_uri == parent_uri:
            return True
        parents = self._class_hierarchy.get(cls_uri, [])
        for p in parents:
            if self._is_subclass_of(p, parent_uri):
                return True
        return False

    def to_graph_schema(self) -> dict:
        """生成图模式的提示信息"""
        schema = {
            "classes": [],
            "relationships": [],
        }
        for cls_uri, info in self._classes.items():
            schema["classes"].append({
                "name": info["local_name"],
                "label": info["label"],
                "comment": info["comment"],
            })
        for prop_uri, info in self._object_properties.items():
            schema["relationships"].append({
                "name": info["local_name"],
                "label": info["label"],
                "comment": info["comment"],
                "from": [self._local_name(d) for d in info.get("domains", [])],
                "to": [self._local_name(r) for r in info.get("ranges", [])],
            })
        return schema

    def get_rel_types_for_source(self, source_type: str) -> list:
        """返回 domain 包含 source_type（或其超类）的对象属性（关系类型）列表"""
        src_uri = None
        for uri, info in self._classes.items():
            if info.get("local_name") == source_type:
                src_uri = uri
                break
        result = []
        for prop_uri, info in self._object_properties.items():
            domains = info.get("domains", [])
            allowed = False
            for d_uri in domains:
                d_local = self._local_name(d_uri)
                if d_local == source_type:
                    allowed = True
                    break
                if src_uri and self._is_subclass_of(src_uri, d_uri):
                    allowed = True
                    break
            if allowed:
                result.append({
                    "name": info["local_name"],
                    "label": info.get("label", ""),
                    "comment": info.get("comment", ""),
                    "ranges": [self._local_name(r) for r in info.get("ranges", [])],
                })
        return result
