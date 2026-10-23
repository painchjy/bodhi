# schema.org 模板化 · 落地方案（待你 view 后落 TTL）

> 2026-10-23。按你的三条裁定：**① 独立模块、bmm 依赖 schema；② `applicableReportType`/`templateSelector`
> 是 `schema:DocumentTemplate` 的属性 ⇒ 放 schema 模块；③ 正文**双声明**（`bmm:wikiContent` +
> `schema:articleBody`），前端"数据属性编辑"里不出现这两个。**
>
> ⚠️ 只改 `docs/ontology/*完整版.ttl`（手工维护）；`ontology/sources/*.ttl` 由上传程序生成，**不手改**。
> ⚠️ `upload_ttl` **不动**。

## 1. 新增模块：`docs/ontology/SCHEMA完整版.ttl`

```turtle
@prefix :     <https://schema.org/> .
@prefix owl:  <http://www.w3.org/2002/07/owl#> .
@prefix rdf:  <http://www.w3.org/1999/02/22-rdf-syntax-ns#> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
@prefix xsd:  <http://www.w3.org/2001/XMLSchema#> .
@prefix bodhi:<http://example.org/bodhi#> .

<https://schema.org/> rdf:type owl:Ontology ;
    rdfs:label "schema.org 参考模型（按需子集）"@zh ;
    rdfs:comment "只保留本工程用到的 schema.org 概念：CreativeWork / DocumentTemplate / articleBody 等。"@zh ;
    bodhi:shortName "标准参考模型" .          # ≤16 字符（导入时校验）

# ---- 类 ----
:CreativeWork rdf:type owl:Class ;
    rdfs:label "创作作品"@zh ;
    rdfs:comment "可被创作/编排的内容基类（报告、模板等都归它）。"@zh ;
    rdfs:subClassOf owl:Thing .

:DocumentTemplate rdf:type owl:Class ;
    rdfs:label "文档模板"@zh ;
    rdfs:comment "文档/报告模板：**章节结构写在正文**（articleBody），只用属性标记"可用于哪种报告类型/由哪条规则选择"。"@zh ;
    rdfs:subClassOf :CreativeWork .

# ---- 正文属性（唯一正文口径；bmm:wikiContent 与本属性等价）----
:articleBody rdf:type owl:DatatypeProperty ;
    rdfs:label "正文"@zh ;
    rdfs:domain :CreativeWork ;
    rdfs:range xsd:string ;
    rdfs:comment "整篇正文（Markdown）。事实源是**图节点 articleBody**；PG content 为渲染缓存。"@zh ;
    bodhi:hiddenFromEditor true .            # ⚠️ 需编译器支持（见 §4 开放项）

# ---- 模板选择属性（都属于 DocumentTemplate）----
:applicableReportType rdf:type owl:DatatypeProperty ;
    rdfs:label "适用报告类型"@zh ;
    rdfs:domain :DocumentTemplate ;
    rdfs:range xsd:string ;
    rdfs:comment "本模板可用于哪些报告类型（值=报告类的 prefixed 名，如 bmm:TechnicalReviewReport；可多值/逗号分隔）。"@zh .

:templateSelector rdf:type owl:DatatypeProperty ;
    rdfs:label "模板选用规则"@zh ;
    rdfs:domain :DocumentTemplate ;
    rdfs:range xsd:string ;
    rdfs:comment "选用本模板的规则页 slug（如 bmm/operativebusinessrule/…）；空=不设条件。裁剪规则**引用不复制**。"@zh .
```

## 2. 改 `docs/ontology/BMM完整版.ttl`

```turtle
# 模块头：加 imports（bmm 依赖 schema）
<http://example.org/bmm> rdf:type owl:Ontology ;
    owl:imports <https://schema.org/> ;       # ★ 新增这一行
    ...

# :Report 增加父类
:Report rdf:type owl:Class ;
    rdfs:subClassOf owl:Thing, :CreativeWork ;   # ★ 加 :CreativeWork（需 `@prefix schema:` 或写作 <https://schema.org/CreativeWork>）
    ...

# :wikiContent 与 schema:articleBody **双声明等价**
:wikiContent a owl:DatatypeProperty ;
    rdfs:label "正文（兼容别名）"@zh ;
    rdfs:domain :Report ;
    rdfs:range xsd:string ;
    owl:equivalentProperty <https://schema.org/articleBody> ;   # ★ 新增
    bodhi:hiddenFromEditor true .                              # ★ 新增（同 §4）
```

> 注意：`bmm.ttl` 里没有 `@prefix schema:`，建议直接写全 IRI 或加一行前缀声明；
> 两种写法编译等价，等你确认用哪种（我倾向**加前缀**，可读性好）。

## 3. 上传顺序（编译依赖）

```
① SCHEMA完整版.ttl  → 生成 sources/schemaorg.ttl（模块 key 待定，见 §4-1）
② BMM完整版.ttl     → 会级联删 bpmn/agent（下游），属既定行为
③ BPMN完整版.ttl
④ 智能体开发完整版.ttl
```
每次上传后跑 `BODHI_TEST_ONTOLOGY_UPLOAD=1 python3 -m pytest tests/test_ontology_upload.py`（真重编）。

## 4. 开放项（需你拍板，我不擅自做）

| # | 问题 | 影响 | 我的建议 |
|---|---|---|---|
| 1 | schema 模块的 **module key**：`ontology_compiler` 从 IRI/前缀推导；`https://schema.org/` 的 local name 为空，可能推成空或 `schema.org` | 产物里模块名/目录一级名 | 试编译一次看实际 key；若为空改用 `http://example.org/schema#`（前缀仍叫 `schema:`，类名不变） |
| 2 | `bodhi:hiddenFromEditor` 是**新注解**，编译器需要认识它才会传进 `class_attributes`（供前端过滤） | 前端"数据属性编辑"是否显示这两个属性 | 二选一：**(a)** 编译器加一行透传（**改代码，需你批**）；**(b)** 前端按固定名单过滤（不改本体编译） |
| 3 | `applicableReportType` 多值表达 | 模板可服务多个报告类型 | 本体只声明 DP；多值写 `attributes` 数组（前端多选）或逗号分隔字符串（更省事，推荐后者） |
| 4 | `schema:DocumentTemplate` 的实例页 type | 模板页写入校验 | 编译后 `page_type = schema:DocumentTemplate` 才会被 `write_knowledge` 接受；未编完前不要建模板页 |

## 5. 落地顺序（你确认后我执行）

1. 新建 `docs/ontology/SCHEMA完整版.ttl`（§1）
2. 改 `docs/ontology/BMM完整版.ttl`（§2 三处）
3. 按 §3 顺序走**上传接口**编译（不手编）
4. 校验：`ontology_index.json` 出现 `schema:CreativeWork` / `schema:DocumentTemplate` /
   `schema:articleBody` / `applicableReportType` / `templateSelector`，且 `bmm:Report` 的父类含 `CreativeWork`
5. `ke_report` 读模板（`page_metadata` 或 attributes）+ 用例
6. 域词清理（`ke_report` 32 + `report_compose` 47）另起一片
