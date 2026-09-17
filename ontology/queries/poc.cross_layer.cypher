# ============================================================
# BODHI2 · 跨层元查询（TBox 层，作用于本体投影）
#
# 由 tools/ontology-compiler 复制本文件并替换占位符后输出到
#   artifacts/neo4j/20_cross_layer_queries.cypher
#
# 占位符（替换值已带引号，直接拼接使用）：
#   {{BMM_NS}}               = 'http://example.org/bmm#'
#   {{EA_NS}}                = 'http://example.org/bmm-EA-ext#'
#   {{EXT_EA_SERVICE_NS}}    = 'http://example.org/bodhi/ext/ea-service#'
#   {{EXT_EA_OWNERSHIP_NS}}  = 'http://example.org/bodhi/ext/ea-ownership#'
#   {{EXT_BMM_FD_NS}}        = 'http://example.org/bodhi/ext/bmm-fd#'
#   {{GENERATED_AT}}         = 生成时间（UTC ISO8601）
#
# 本体投影节点/关系（由 10_ontology.cypher 建立）：
#   节点：BodhiOntClass / BodhiOntProperty / BodhiEnumValue / BodhiRestriction
#   关系：BODHI_SUBCLASS_OF / BODHI_DOMAIN / BODHI_RANGE / BODHI_INVERSE_OF /
#         BODHI_HAS_RESTRICTION / BODHI_ON_PROPERTY / BODHI_ENUM_MEMBER
#
# 注意：这里只回答「本体允许/要求什么」（约束来源）。实例层（WeKnora 抽取出的
#       知识点及其溯源）由 ke-core 经 WeKnora 检索接口读取后在 Python 侧做推理，
#       不在本文件中硬编码 WeKnora 的实例 schema，避免与上游版本耦合。
# ============================================================

// Q1 · 跨模块（跨层）关系清单：哪些对象属性连接了不同本体模块
MATCH (p:BodhiOntProperty {property_kind: 'object'})-[:BODHI_DOMAIN]->(d:BodhiOntClass)
MATCH (p)-[:BODHI_RANGE]->(r:BodhiOntClass)
WHERE d.module <> r.module
RETURN p.module AS declared_in, p.local_name AS relation, p.label AS relation_label,
       d.module + ':' + d.local_name AS from_class,
       r.module + ':' + r.local_name AS to_class
ORDER BY declared_in, relation;

// Q2 · 类继承闭包上的约束（含从父类继承的约束）
MATCH (c:BodhiOntClass {iri: {{BMM_NS}} + 'BusinessRule'})-[:BODHI_SUBCLASS_OF*0..]->(sub)
MATCH (sub)-[:BODHI_HAS_RESTRICTION]->(res)-[:BODHI_ON_PROPERTY]->(p)
WHERE res.kind IN ['minCardinality', 'cardinality', 'maxCardinality', 'someValuesFrom', 'allValuesFrom']
RETURN DISTINCT sub.module AS module, sub.local_name AS class, p.local_name AS property,
       res.kind AS constraint_kind, res.value AS constraint_value, res.comment AS rationale
ORDER BY module, class, property;

// Q3 · 枚举类型及其取值、引用属性（LLM 不得自造取值）
MATCH (c:BodhiOntClass)-[:BODHI_ENUM_MEMBER]->(v:BodhiEnumValue)
OPTIONAL MATCH (p:BodhiOntProperty)-[:BODHI_RANGE]->(c)
RETURN c.module AS module, c.local_name AS enum_type, c.label AS enum_label,
       collect(DISTINCT v.local_name) AS allowed_values,
       collect(DISTINCT p.local_name) AS used_by
ORDER BY module, enum_type;

// Q4 · 跨模块属性清单：属性的 domain/range 落在其他模块（跨层语义桥）
MATCH (p:BodhiOntProperty)-[:BODHI_DOMAIN]->(d:BodhiOntClass)
MATCH (p)-[:BODHI_RANGE]->(r:BodhiOntClass)
WHERE d.module <> p.module OR r.module <> p.module
RETURN p.module AS declared_in, p.local_name AS property, p.property_kind AS kind,
       d.module + ':' + d.local_name AS domain, r.module + ':' + r.local_name AS range
ORDER BY declared_in, property;

// Q5 · 本体缺口清单：没有父类的类（对应待拍板/待扩展项）
MATCH (c:BodhiOntClass)
WHERE NOT (c)-[:BODHI_SUBCLASS_OF]->(:BodhiOntClass)
RETURN c.module AS module, c.local_name AS class, c.label AS label, c.comment AS comment
ORDER BY module, class;

// Q6 · 基数与值域要求总表（校验器与执行层网关的约束来源）
MATCH (c:BodhiOntClass)-[:BODHI_HAS_RESTRICTION]->(res)-[:BODHI_ON_PROPERTY]->(p)
RETURN c.module AS module, c.local_name AS class, p.local_name AS property,
       res.kind AS constraint_kind, res.value AS constraint_value, res.comment AS rationale
ORDER BY module, class, property, constraint_kind;

// Q7 · 服务契约设计约束一览（S1–S6 校验规则对应的本体约束）
MATCH (c:BodhiOntClass {iri: {{EXT_EA_SERVICE_NS}} + 'ServiceContract'})-[:BODHI_SUBCLASS_OF*0..]->(sub)
OPTIONAL MATCH (sub)-[:BODHI_HAS_RESTRICTION]->(res)-[:BODHI_ON_PROPERTY]->(p)
RETURN sub.local_name AS class, p.local_name AS required_property,
       res.kind AS constraint_kind, res.value AS constraint_value, res.comment AS rationale
ORDER BY class, required_property;
