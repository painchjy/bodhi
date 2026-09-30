"""把本体模型（TTL 的编译产物）投影成「企业本体模型」知识库的 wiki 页面。

设计（见 docs/weknora-fork.md §10.13）
------------------------------------
单一真源 = `ontology/*.ttl`，其余都是产物：

    TTL ─(tools/ontology-compiler)─┬─→ artifacts/weknora/ontology_index.json  （枚举/颜色）
                                   ├─→ artifacts/prompts/<key>_light.md       （轻量版正文）
                                   └─→ artifacts/weknora/ontology_wiki.jsonl  （本脚本的输入）★

本脚本做两件事：
  1) `build`   —— 由 ontology_index.json 生成页面清单（jsonl），每个本体类一页、每条关系一页、
                  每模块一页、轻量版一页、外加一张总览页；
  2) `project` —— 把页面清单幂等写进目标知识库（先删 last_edit_source='ontology-wiki' 的页面）。

页面类型（自描述，和抽取页 `bmm:*` / `ea:*` 区分开）：
    ontology:Module     模块页 / 总览页
    ontology:Class      本体类（每个类一页）
    ontology:Relation   对象属性（每条关系一页；含跨模块桥）
    ontology:LightDoc   轻量版提示词全文（供智能体直接读）

用法
----
    python tools/ontology-extract/ontology_wiki.py build
    python tools/ontology-extract/ontology_wiki.py stats
    python tools/ontology-extract/ontology_wiki.py project --kb-id 08810cbd-af86-48d1-bd25-3b2c338e3d68
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import re
import subprocess
import sys
import uuid
from datetime import datetime, timezone

REPO = pathlib.Path(__file__).resolve().parents[2]
INDEX_PATH = REPO / "artifacts" / "weknora" / "ontology_index.json"
OUT_PATH = REPO / "artifacts" / "weknora" / "ontology_wiki.jsonl"
LOG_DIR = REPO / "logs"
TOOL_TAG = "ontology-wiki"

DB_CONTAINER = os.environ.get("BODHI_DB_CONTAINER", "WeKnora-postgres")
DB_USER = os.environ.get("BODHI_DB_USER", "postgres")
DB_NAME = os.environ.get("BODHI_DB_NAME", "WeKnora")
# 口令**不内置**：复用 ke-core 的口径（env `BODHI_DB_PASSWORD` → WeKnora `.env` 的 DB_PASSWORD）。
try:
    sys.path.insert(0, str(REPO / "tools" / "ke-core"))
    import ke_db as _ke_db            # noqa: E402
    DB_PASSWORD = _ke_db.DB_PASSWORD
except Exception:                      # noqa: BLE001  独立运行时退化为仅 env
    DB_PASSWORD = os.environ.get("BODHI_DB_PASSWORD", "")

TYPE_MODULE = "ontology:Module"
TYPE_CLASS = "ontology:Class"
TYPE_RELATION = "ontology:Relation"
TYPE_PROPERTY = "ontology:Property"
TYPE_LIGHT = "ontology:LightDoc"

PAGE_COLUMNS = [
    "id", "tenant_id", "knowledge_base_id", "slug", "title", "page_type", "status",
    "content", "summary", "parent_slug", "folder_id", "category_path", "wiki_path",
    "depth", "sort_order", "source_refs", "chunk_refs", "in_links", "out_links",
    "page_metadata", "aliases", "version", "last_edit_source", "last_editor_id",
]

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            pass


# ---------------------------------------------------------------------------
# 基础工具
# ---------------------------------------------------------------------------
def local_name(type_name: str) -> str:
    """`bmm:OrganizationUnit` -> `organizationunit`（slug 用，全小写 ASCII）"""
    raw = type_name.split(":", 1)[-1]
    return re.sub(r"[^a-z0-9]+", "-", raw.lower()).strip("-")


def slug_class(module: str, type_name: str) -> str:
    return "ontology/%s/%s" % (module, local_name(type_name))


def slug_relation(module: str, type_name: str) -> str:
    return "ontology/%s/rel/%s" % (module, local_name(type_name))


def slug_module(module: str) -> str:
    return "ontology/%s" % module


def slug_property(module: str, prop_name: str) -> str:
    """属性定义页（**含数据属性**）：`ontology/bmm/prop/rule-severity`"""
    return "ontology/%s/prop/%s" % (module, local_name(prop_name))


def _neo4j_properties() -> dict[str, list[dict]]:
    """从 Neo4j 读**全部本体属性**（对象属性 + 数据属性），按模块分组。

    为什么从图库读而不是索引 JSON：`artifacts/weknora/ontology_index.json` 只收录了
    对象属性（relations），**数据属性（如 bmmfd:ruleSeverity）不在里面**；
    用户口径要求「没有在 Neo4j 之外另存的属性定义，必须在 wiki 中完整」，所以这里以
    Neo4j 本体投影（= TTL 编译/加载后的图）为准，把每个属性的 label/comment/domain/range
    都落到属性页上。Neo4j 不可用时返回 {}（脚本仍能产出类页/关系页）。
    """
    try:
        ke_core = REPO / "tools" / "ke-core"
        if str(ke_core) not in sys.path:
            sys.path.insert(0, str(ke_core))
        import ke_neo4j  # noqa: PLC0415  （零依赖的 Neo4j 客户端；不再依赖待删的 src/）

        classes = {r["uri"]: r for r in ke_neo4j.query(
            "MATCH (c:BodhiOntClass) WHERE c.external IS NULL "
            "RETURN c.iri AS uri, coalesce(c.prefix,'') AS prefix, coalesce(c.local_name,'') AS name")}
        props: dict[str, dict] = {}
        for r in ke_neo4j.query(
                "MATCH (p:BodhiOntProperty) "
                "RETURN p.iri AS uri, coalesce(p.module,'') AS model_id, "
                "       coalesce(p.prefix,'') AS prefix, coalesce(p.local_name,'') AS name, "
                "       coalesce(p.label,'') AS label, coalesce(p.comment,'') AS comment, "
                "       coalesce(p.property_kind,'object') AS kind"):
            kind = str(r.get("kind") or "object").lower()
            r["short_uri"] = ("%s:%s" % (r["prefix"], r["name"])) if r["prefix"] else r["name"]
            r["prop_type"] = "ObjectProperty" if kind.startswith("obj") else "DatatypeProperty"
            props[r["uri"]] = r
        dom: dict[str, list[str]] = {}
        for r in ke_neo4j.query("MATCH (p:BodhiOntProperty)-[:BODHI_DOMAIN]->(c:BodhiOntClass) "
                                "RETURN p.iri AS a, c.iri AS b"):
            dom.setdefault(r["a"], []).append(r["b"])
        rng: dict[str, list[str]] = {}
        for r in ke_neo4j.query("MATCH (p:BodhiOntProperty)-[:BODHI_RANGE]->(c:BodhiOntClass) "
                                "RETURN p.iri AS a, c.iri AS b"):
            rng.setdefault(r["a"], []).append(r["b"])
        inverse: dict[str, str] = {}
        for row in ke_neo4j.query("MATCH (p:BodhiOntProperty)-[:BODHI_INVERSE_OF]->(q) "
                                  "RETURN p.iri AS a, q.iri AS b"):
            inverse[row["a"]] = row["b"]

        def short(uri: str) -> str:
            rec = classes.get(uri) or {}
            if rec.get("name"):
                return ("%s:%s" % (rec["prefix"], rec["name"])) if rec.get("prefix") else rec["name"]
            return local_name(uri)

        out: dict[str, list[dict]] = {}
        for uri, rec in props.items():
            mod = rec.get("model_id") or ""
            out.setdefault(mod, []).append({
                "uri": uri, "name": rec.get("name") or "",
                "prefixed": rec.get("short_uri") or rec.get("name") or "",
                "label": rec.get("label") or rec.get("name") or "",
                "comment": rec.get("comment") or "",
                "kind": rec.get("prop_type") or "ObjectProperty",
                "domains": [short(u) for u in dom.get(uri, [])],
                "ranges": [short(u) for u in rng.get(uri, [])],
                "inverse": short(inverse[uri]) if uri in inverse else "",
            })
        for mod in out:
            out[mod].sort(key=lambda p: p["prefixed"])
        total = sum(len(v) for v in out.values())
        print("[ontology-wiki] Neo4j 读到 %d 个属性定义（%s）"
              % (total, "、".join("%s:%d" % (k, len(v)) for k, v in sorted(out.items()))), flush=True)
        return out
    except Exception as exc:  # noqa: BLE001
        print("[ontology-wiki] 读 Neo4j 属性失败（属性页将为空）：%s" % exc, flush=True)
        return {}


def wlink(title: str, slug: str) -> str:
    """wiki 内链：上游会解析成 out_links，图谱按它连边。"""
    return "[%s](wiki:%s)" % (title, slug) if slug else title


def node_label(type_name: str) -> str:
    return type_name


# ---------------------------------------------------------------------------
# 页面装配
# ---------------------------------------------------------------------------
class WikiBuilder:
    def __init__(self, index: dict):
        self.index = index
        self.models = index["models"]
        self.class_index: dict[str, tuple[str, dict]] = {}
        self.relation_index: dict[str, tuple[str, dict]] = {}
        for model in self.models:
            for cls in model["classes"]:
                self.class_index.setdefault(cls["name"], (model["key"], cls))
            for rel in list(model["relations"]) + list(model.get("cross_module_bridges") or []):
                self.relation_index.setdefault(rel["name"], (model["key"], rel))
        self.pages: list[dict] = []
        self.links: dict[str, set[str]] = {}
        self.seen: set[str] = set()
        # 全部属性定义（对象 + 数据）来自 Neo4j；用于类页的「属性定义」小节与属性页
        self.properties = _neo4j_properties()
        self.generated_at = datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M:%S %z")

    # --- 解析辅助 ---------------------------------------------------------
    def class_link(self, type_name: str) -> str:
        found = self.class_index.get(type_name)
        if not found:
            return "`%s`" % type_name
        module, cls = found
        return wlink("%s（%s）" % (cls.get("label") or type_name, type_name),
                     slug_class(module, type_name))

    def class_slug_of(self, type_name: str) -> str:
        found = self.class_index.get(type_name)
        return slug_class(found[0], type_name) if found else ""

    def relation_link(self, type_name: str) -> str:
        found = self.relation_index.get(type_name)
        if not found:
            return "`%s`" % type_name
        module, rel = found
        return wlink("%s（%s）" % (rel.get("label") or type_name, type_name),
                     slug_relation(module, type_name))

    def relation_slug_of(self, type_name: str) -> str:
        found = self.relation_index.get(type_name)
        return slug_relation(found[0], type_name) if found else ""

    # --- 属性（对象 + 数据）-----------------------------------------------
    def property_slug_of(self, module: str, prop: dict) -> str:
        return slug_property(module, prop.get("name") or "")

    def property_link(self, module: str, prop: dict) -> str:
        """对象属性一律指「关系页」；数据属性指「属性定义页」。

        2026-09-30 修：对象属性**不再建属性页**（目录只放数据属性）→ 若仍退回属性页 slug 就是悬空边
        （实测 33 条 A1）。所以对象属性：有关系页就链接它；没有则**纯文本**（不产生链接）。
        """
        name = prop.get("prefixed") or prop.get("name") or ""
        if prop.get("kind") == "ObjectProperty":
            if self.relation_slug_of(name):
                return self.relation_link(name)
            return "%s（`%s`，对象属性；无独立关系页）" % (prop.get("label") or name, name)
        return wlink("%s（%s）" % (prop.get("label") or name, name),
                     slug_property(module, prop.get("name") or name))

    def add(self, *, slug: str, title: str, page_type: str, module_label: str,
            group: str, content: str, summary: str, wiki_path: str,
            out_slugs: list[str], metadata: dict) -> None:
        if slug in self.seen:
            # 跨模块桥会同时出现在 relations[] 与 cross_module_bridges[] 里，
            # 同一个 slug 只保留一条（先出现的优先），避免 id/slug 冲突。
            print("[ontology-wiki] 跳过重复 slug：%s" % slug)
            return
        self.seen.add(slug)
        # 空 group（模块页/总览页）→ 直接落 module_label 这一层，**不再多嵌套一层同名目录**
        # （2026-09-30 用户实测：目录显示数量但展开没有页面 —— 页被挂到了 `…/总览/总览`、`…/模块/模块`）
        # **根级 = 模块短名**（用户口径 2026-09-30）：目录名用模块 key（`bmm`/`ea`），
        # ① 不再有"企业本体模型"这个无意义的根；② 名字短，避免前端拼 `category_path` 时截断
        # （长名曾被截掉末尾「）」，导致目录下的页永远查不到、列表空白）。
        # 完整名称在**页标题**与 **wiki_path**（"目录路径/标题"）里，悬浮/点开即可看到。
        cat = [module_label] + ([group] if group else [])
        self.pages.append({
            "slug": slug, "title": title, "page_type": page_type,
            "content": content, "summary": summary,
            "category_path": cat,
            # 「目录路径/标题」：目录名短（bmm/ea），**完整中文名在标题里** → 悬浮/点开可见
            "wiki_path": "/".join([str(x) for x in cat] + [title]),
            "out_links": sorted({s for s in out_slugs if s}),
            "page_metadata": {"ontology": metadata},
        })
        self.links[slug] = {s for s in out_slugs if s}


    # --- 本体类页 ---------------------------------------------------------
    def class_page(self, module: dict, cls: dict) -> None:
        name = cls["name"]
        label = cls.get("label") or name
        parents = cls.get("parents") or []
        as_domain, as_range = [], []
        for rel in module["relations"]:
            if name in (rel.get("domain") or []):
                as_domain.append(rel)
            if name in (rel.get("range") or []):
                as_range.append(rel)
        children = [c for c in module["classes"] if name in (c.get("parents") or [])]

        lines = ["# %s（`%s`）" % (label, name), "",
                 "> **类型**：本体类（`%s`）  " % TYPE_CLASS,
                 "> **模块**：%s（`%s`）  " % (module["label"], module["key"]),
                 "> **命名空间**：`%s`  " % cls.get("iri", ""),
                 "> **图谱颜色**：`%s`" % (cls.get("color") or "-"), ""]
        lines += ["## 定义", "", (cls.get("definition") or "（该 TTL 未给定义）").strip(), ""]

        out_slugs = [slug_module(module["key"])]
        if parents:
            lines += ["## 父类", ""]
            for parent in parents:
                lines.append("- %s" % self.class_link(parent))
                out_slugs.append(self.class_slug_of(parent))
            lines.append("")
        if children:
            lines += ["## 子类", ""]
            for child in sorted(children, key=lambda c: c["name"]):
                lines.append("- %s" % self.class_link(child["name"]))
                out_slugs.append(slug_class(module["key"], child["name"]))
            lines.append("")

        if as_domain or as_range:
            lines += ["## 相关关系", ""]
            if as_domain:
                lines += ["**作为起点（domain）**", ""]
                for rel in as_domain:
                    targets = "、".join(self.class_link(t) for t in (rel.get("range") or [])) or "?"
                    lines.append("- %s → %s" % (self.relation_link(rel["name"]), targets))
                    out_slugs.append(self.relation_slug_of(rel["name"]))
                    for t in (rel.get("range") or []):
                        out_slugs.append(self.class_slug_of(t))
                lines.append("")
            if as_range:
                lines += ["**作为终点（range）**", ""]
                for rel in as_range:
                    sources = "、".join(self.class_link(s) for s in (rel.get("domain") or [])) or "?"
                    lines.append("- %s ← %s" % (self.relation_link(rel["name"]), sources))
                    out_slugs.append(self.relation_slug_of(rel["name"]))
                    for s in (rel.get("domain") or []):
                        out_slugs.append(self.class_slug_of(s))
                lines.append("")
        own_props = [p for p in self.properties.get(module["key"], [])
                     if name in (p.get("domains") or [])]
        if own_props:
            lines += ["## 属性定义（%d 个，含数据属性）" % len(own_props), ""]
            for prop in own_props:
                if prop.get("ranges"):
                    target = "→ " + "、".join(self.class_link(r) for r in prop["ranges"])
                elif prop.get("kind") == "DatatypeProperty":
                    target = "→ 字面量（数据属性）"
                else:
                    target = "→ ?（未声明 range）"
                lines.append("- %s %s%s" % (self.property_link(module["key"], prop), target,
                                            ("  — " + prop["comment"][:60]) if prop.get("comment") else ""))
                # 出边：对象属性 → 关系页；数据属性 → 属性页（对象属性不再建属性页，否则悬空）
                if prop.get("kind") == "ObjectProperty":
                    rel_slug = self.relation_slug_of(prop.get("prefixed") or prop.get("name") or "")
                    if rel_slug:
                        out_slugs.append(rel_slug)
                else:
                    out_slugs.append(self.property_slug_of(module["key"], prop))
            lines.append("")

        if cls.get("restriction_count"):
            lines += ["## 约束", "", "- 该类的 OWL 限制（restriction）数量：%d" % cls["restriction_count"], ""]

        self.add(slug=slug_class(module["key"], name),
                 title="%s（%s）" % (label, name), page_type=TYPE_CLASS,
                 module_label=(module.get("short_label") or module["key"]), group="本体类",
                 content="\n".join(lines).rstrip() + "\n",
                 summary=(cls.get("definition") or "")[:400]
                         or "%s 模块的本体类 %s" % (module["label"], name),
                 wiki_path=slug_class(module["key"], name), out_slugs=out_slugs,
                 metadata={"kind": "class", "model": module["key"], "class": name,
                           "label": label, "iri": cls.get("iri", ""),
                           "color": cls.get("color"), "parents": parents,
                           "generated_at": self.generated_at, "generator": TOOL_TAG})


    # --- 关系页 -----------------------------------------------------------
    def relation_page(self, module: dict, rel: dict, is_bridge: bool) -> None:
        name = rel["name"]
        label = rel.get("label") or name
        domain = rel.get("domain") or []
        rng = rel.get("range") or []
        out_slugs = [slug_module(module["key"])]
        for t in domain + rng:
            out_slugs.append(self.class_slug_of(t))

        lines = ["# %s（`%s`）" % (label, name), "",
                 "> **类型**：本体关系（`%s`）  " % TYPE_RELATION,
                 "> **模块**：%s（`%s`）%s  "
                 % (module["label"], module["key"], "（**跨模块桥**）" if is_bridge else ""),
                 "> **方向**：%s → %s  "
                 % ("、".join(self.class_link(d) for d in domain) or "?",
                    "、".join(self.class_link(r) for r in rng) or "?"),
                 "> **逆关系**：%s ｜ **函数型**：%s"
                 % (rel.get("inverse_of") or "无", "是" if rel.get("functional") else "否"), ""]
        lines += ["## 定义", "", (rel.get("definition") or "（该 TTL 未给定义）").strip(), ""]
        lines += ["## 端点", "",
                  "- 起点（domain）：%s" % ("、".join(self.class_link(d) for d in domain) or "?"),
                  "- 终点（range）：%s" % ("、".join(self.class_link(r) for r in rng) or "?"),
                  ""]
        lines += ["## 用法", "",
                  "在本体提取里，这条关系表达「%s」的实例与「%s」的实例之间的「%s」。"
                  % ("、".join(domain) or "?", "、".join(rng) or "?", label), ""]
        if rel.get("inverse_of"):
            lines += ["- 逆关系页：%s" % self.relation_link(rel["inverse_of"]), ""]
            out_slugs.append(self.relation_slug_of(rel["inverse_of"]))

        self.add(slug=slug_relation(module["key"], name),
                 title="%s（%s）" % (label, name), page_type=TYPE_RELATION,
                 module_label=(module.get("short_label") or module["key"]),
                 group="跨模块桥" if is_bridge else "本体关系",
                 content="\n".join(lines).rstrip() + "\n",
                 summary=(rel.get("definition") or "")[:400]
                         or "%s 模块的本体关系 %s" % (module["label"], name),
                 wiki_path=slug_relation(module["key"], name), out_slugs=out_slugs,
                 metadata={"kind": "bridge" if is_bridge else "relation",
                           "model": module["key"], "relation": name, "label": label,
                           "domain": domain, "range": rng,
                           "inverse_of": rel.get("inverse_of") or "",
                           "functional": bool(rel.get("functional")),
                           "generated_at": self.generated_at, "generator": TOOL_TAG})


    # --- 模块页 / 轻量版页 ------------------------------------------------
    def module_page(self, module: dict) -> None:
        key, label = module["key"], module["label"]
        classes = sorted(module["classes"], key=lambda c: (c.get("label") or c["name"]))
        rels = module["relations"]
        bridges = module.get("cross_module_bridges") or []
        out_slugs = [slug_class(key, c["name"]) for c in classes]
        out_slugs += [slug_relation(key, r["name"]) for r in rels + bridges]

        lines = ["# %s（`%s`）" % (label, key), "",
                 "> **类型**：本体模块（`%s`）  " % TYPE_MODULE,
                 "> **规模**：类 %d 个 ｜ 关系 %d 条 ｜ 跨模块桥 %d 条 ｜ 轻量版 %s"
                 % (len(classes), len(rels), len(bridges),
                    "有" if module.get("light_available") else "无"), ""]
        if module.get("expert_role"):
            lines += ["> **专家角色**：%s" % module["expert_role"], ""]
        lines += ["## 本体类（%d）" % len(classes), ""]
        for cls in classes:
            lines.append("- %s — %s" % (self.class_link(cls["name"]),
                                        (cls.get("definition") or "")[:80]))
        lines += ["", "## 本体关系（%d）" % len(rels), ""]
        for rel in rels:
            lines.append("- %s：%s → %s"
                         % (self.relation_link(rel["name"]),
                            "、".join(rel.get("domain") or []) or "?",
                            "、".join(rel.get("range") or []) or "?"))
        if bridges:
            lines += ["", "## 跨模块桥（%d）" % len(bridges), ""]
            for rel in bridges:
                lines.append("- %s：%s → %s"
                             % (self.relation_link(rel["name"]),
                                "、".join(rel.get("domain") or []) or "?",
                                "、".join(rel.get("range") or []) or "?"))
        props = self.properties.get(key) or []
        if props:
            data_props = [p for p in props if p.get("kind") == "DatatypeProperty"]
            lines += ["", "## 本体属性（%d 条，含数据属性 %d 条；对象属性见「本体关系」目录）"
                      % (len(props), len(data_props)), ""]
            for prop in props:
                lines.append("- %s：%s → %s"
                             % (self.property_link(key, prop),
                                "、".join(prop.get("domains") or []) or "?",
                                "、".join(prop.get("ranges") or [])
                                or ("（字面量）" if prop.get("kind") == "DatatypeProperty" else "?")))
                # 出边：数据属性 → 属性页；对象属性 → 关系页（对象属性**不再建属性页**，
                # 若仍记属性页 slug 就会产生悬空边 —— 2026-09-30 实测 33 条 A1）
                if prop.get("kind") == "ObjectProperty":
                    rel_slug = self.relation_slug_of(prop.get("prefixed") or prop.get("name") or "")
                    if rel_slug:
                        out_slugs.append(rel_slug)
                else:
                    out_slugs.append(self.property_slug_of(key, prop))
        if module.get("light_available"):
            light_slug = slug_module(key) + "/light"
            lines += ["", "## 轻量版提示词", "",
                      "- 全文见：%s" % wlink("%s 轻量版" % label, light_slug), ""]
            out_slugs.append(light_slug)
        lines += ["", "## 说明", "",
                  "本页由 `ontology/%s` 的 TTL 编译生成（`%s`，生成于 %s）。"
                  % (key, TOOL_TAG, self.generated_at), ""]

        self.add(slug=slug_module(key), title="%s（%s）" % (label, key),
                 page_type=TYPE_MODULE, module_label=(module.get("short_label") or key), group="",
                 content="\n".join(lines).rstrip() + "\n",
                 summary="%s 模块：%d 个本体类、%d 条关系。" % (label, len(classes), len(rels)),
                 wiki_path=slug_module(key), out_slugs=out_slugs,
                 metadata={"kind": "module", "model": key, "classes": len(classes),
                           "relations": len(rels), "bridges": len(bridges),
                           "generated_at": self.generated_at, "generator": TOOL_TAG})

    # --- 本体属性页（含数据属性；用户口径：属性定义在 wiki 中必须完整）------
    def property_page(self, module: dict, prop: dict) -> None:
        key, label = module["key"], module["label"]
        name = prop.get("name") or ""
        plabel = prop.get("label") or name
        prefixed = prop.get("prefixed") or name
        kind = prop.get("kind") or "ObjectProperty"
        kind_cn = ("对象属性（连到其它知识节点）" if kind == "ObjectProperty"
                   else "数据属性（字面量取值）")
        lines = ["# %s（`%s`）" % (plabel, prefixed), "",
                 "> **类型**：数据属性（`%s`）  " % TYPE_PROPERTY,
                 "> **模块**：%s（`%s`）  " % (label, key),
                 "> **属性种类**：%s  " % kind_cn,
                 "> **命名空间**：`%s`" % prop.get("uri", ""), ""]
        lines += ["## 定义", "", (prop.get("comment") or "（该 TTL 未给 comment）").strip(), ""]
        out_slugs = [slug_module(key)]
        if prop.get("domains"):
            lines += ["## 定义域（domain：谁可以发起）", ""]
            for dom in prop["domains"]:
                lines.append("- %s" % self.class_link(dom))
                out_slugs.append(self.class_slug_of(dom))
            lines.append("")
        if prop.get("ranges"):
            lines += ["## 值域（range：可以指向谁）", ""]
            for rng in prop["ranges"]:
                lines.append("- %s" % self.class_link(rng))
                out_slugs.append(self.class_slug_of(rng))
            lines.append("")
        if prop.get("inverse"):
            lines += ["## 反向属性", "", "- %s" % self.relation_link(prop["inverse"]), ""]
            out_slugs.append(self.relation_slug_of(prop["inverse"]))
        if kind == "ObjectProperty" and prefixed in self.relation_index:
            lines += ["## 关系页", "", "- %s" % self.relation_link(prefixed), ""]
            out_slugs.append(self.relation_slug_of(prefixed))
        lines += ["## 说明", "",
                  "本页由 Neo4j 本体投影（TTL 编译/加载后的图）生成（`%s`，生成于 %s）。"
                  % (TOOL_TAG, self.generated_at), ""]

        self.add(slug=slug_property(key, name), title="%s（%s）" % (plabel, prefixed),
                 page_type=TYPE_PROPERTY, module_label=(module.get("short_label") or key), group="数据属性",
                 content="\n".join(lines).rstrip() + "\n",
                 summary=(prop.get("comment") or "")[:400]
                         or "%s 模块的本体属性 %s" % (label, prefixed),
                 wiki_path=slug_property(key, name), out_slugs=out_slugs,
                 metadata={"kind": "property", "model": key, "property": name,
                           "iri": prop.get("uri", ""), "prop_kind": kind,
                           "domains": prop.get("domains") or [],
                           "ranges": prop.get("ranges") or [],
                           "generated_at": self.generated_at, "generator": TOOL_TAG})

    def light_page(self, module: dict, text: str) -> None:
        key, label = module["key"], module["label"]
        slug = slug_module(key) + "/light"
        lines = ["# %s · 轻量版提示词" % label, "",
                 "> **类型**：轻量版（`%s`）  " % TYPE_LIGHT,
                 "> **模块**：%s（`%s`）  " % (label, key),
                 "> **来源**：`%s`（由 TTL 编译生成）" % module.get("light_prompt", ""), "",
                 "下面是喂给抽取提示词的**轻量版本体正文**（不含完整 TTL），"
                 "供智能体/人理解该模块的类与取值口径。", "", "---", "", text.strip(), ""]
        self.add(slug=slug, title="%s · 轻量版提示词" % label, page_type=TYPE_LIGHT,
                 module_label=(module.get("short_label") or key), group="轻量版",
                 content="\n".join(lines).rstrip() + "\n",
                 summary="%s 模块的轻量版提示词全文（%d 字符）。" % (label, len(text)),
                 wiki_path=slug, out_slugs=[slug_module(key)],
                 metadata={"kind": "light", "model": key, "chars": len(text),
                           "generated_at": self.generated_at, "generator": TOOL_TAG})


# ---------------------------------------------------------------------------
# 组装全量页面
# ---------------------------------------------------------------------------
def _supplement_from_neo4j(data: dict) -> None:
    """把「Neo4j 有、json 没有」的模块/类/关系补进 index（字段与编译产物**完全同构**）。

    为什么：上传导入的模块不产 json（见 docs/session-handoff.md §3.4bis），wiki 生成若只认 json
    就覆盖不到它们。补录口径与 ke_ontology.class_meta 一致：真源 Neo4j，缺了才用 json。
    字段集合照 artifacts/weknora/ontology_index.json 实测：
      类   name / iri / label / definition / parents / is_enum / restriction_count
      关系 name / iri / label / definition / domain / range / domain_display / range_display / inverse_of / functional
    """
    ke_core = REPO / "tools" / "ke-core"
    if str(ke_core) not in sys.path:
        sys.path.insert(0, str(ke_core))
    import ke_neo4j  # noqa: PLC0415  （模块级没 import，这里按需引入：ke-core 是零依赖客户端）

    known = {model["key"]: model for model in (data.get("models") or [])}
    have_classes = {c.get("name") for m in known.values() for c in (m.get("classes") or [])}
    have_rels = {r.get("name") for m in known.values() for r in (m.get("relations") or [])}

    parents: dict[str, list[str]] = {}
    for row in ke_neo4j.query("MATCH (c:BodhiOntClass)-[:BODHI_SUBCLASS_OF]->(p:BodhiOntClass) "
                              "WHERE c.bodhi_projection = 'ontology' "
                              "RETURN c.prefixed AS c, p.prefixed AS p ORDER BY c, p"):
        if row.get("c") and row.get("p"):
            parents.setdefault(row["c"], []).append(row["p"])
    classes = ke_neo4j.query(
        "MATCH (c:BodhiOntClass) WHERE c.bodhi_projection = 'ontology' "
        "AND coalesce(c.external, false) = false AND c.prefixed IS NOT NULL "
        "RETURN c.prefixed AS name, coalesce(c.iri,'') AS iri, coalesce(c.label,'') AS label, "
        "       coalesce(c.comment,'') AS definition, coalesce(c.module,'') AS module, "
        "       coalesce(c.is_enum,false) AS is_enum, coalesce(c.prefix,'') AS prefix")
    rels = ke_neo4j.query(
        "MATCH (p:BodhiOntProperty {property_kind: 'object'}) WHERE p.bodhi_projection = 'ontology' "
        "AND p.prefixed IS NOT NULL "
        "OPTIONAL MATCH (p)-[:BODHI_DOMAIN]->(d:BodhiOntClass) "
        "OPTIONAL MATCH (p)-[:BODHI_RANGE]->(g:BodhiOntClass) "
        "OPTIONAL MATCH (p)-[:BODHI_INVERSE_OF]->(inv:BodhiOntProperty) "
        "RETURN p.prefixed AS name, coalesce(p.iri,'') AS iri, coalesce(p.label,'') AS label, "
        "       coalesce(p.comment,'') AS definition, coalesce(p.module,'') AS module, "
        "       coalesce(inv.prefixed,'') AS inverse_of, "
        "       collect(DISTINCT d.prefixed) AS domain, collect(DISTINCT g.prefixed) AS range")

    def model_of(key: str, prefix: str = "") -> dict:
        model = known.get(key)
        if model is None:
            pf = prefix or key
            model = {"key": key, "prefix": pf, "label": key, "short_label": pf.upper(),
                     "expert_role": "", "kind": "extension", "namespace": "", "ontology_iri": "",
                     "source_files": [], "affects": [], "light_available": False,
                     "light_source": "", "light_prompt": "", "stats": {},
                     "classes": [], "relations": [],
                     "referenced": {"classes": [], "properties": [], "by_module": {}}}
            known[key] = model
            data.setdefault("models", []).append(model)
            order = data.setdefault("module_order", [])
            if key not in order:
                order.append(key)
        return model

    added = 0
    for row in classes:
        if row["name"] in have_classes:
            continue
        mod = model_of(row["module"] or "external", row.get("prefix") or "")
        mod["classes"].append({"name": row["name"], "iri": row["iri"],
                               "label": row["label"] or row["name"],
                               "definition": row["definition"],
                               "parents": parents.get(row["name"], []),
                               "is_enum": bool(row["is_enum"]), "restriction_count": 0})
        have_classes.add(row["name"])
        added += 1
    for row in rels:
        if row["name"] in have_rels:
            continue
        dom = [x for x in (row.get("domain") or []) if x]
        rng = [x for x in (row.get("range") or []) if x]
        mod = model_of(row["module"] or "external")
        mod["relations"].append({"name": row["name"], "iri": row["iri"],
                                 "label": row["label"] or row["name"],
                                 "definition": row["definition"], "domain": dom, "range": rng,
                                 "domain_display": dom[0] if dom else "",
                                 "range_display": rng[0] if rng else "",
                                 "inverse_of": row["inverse_of"] or "", "functional": False})
        have_rels.add(row["name"])
        added += 1
    if added:
        totals = data.setdefault("totals", {})
        totals["modules"] = len(data.get("models") or [])
        totals["classes"] = len(have_classes)
        totals["object_properties"] = len(have_rels)


def _neo4j_live_sets() -> dict:
    """Neo4j 里**当下真实存在**的模块 / 类 / 对象属性（= 真源）。查询失败会抛异常，由调用方兜底。"""
    ke_core = REPO / "tools" / "ke-core"
    if str(ke_core) not in sys.path:
        sys.path.insert(0, str(ke_core))
    import ke_neo4j  # noqa: PLC0415

    # 「现存模块」= **有内容**的模块（≥1 个类，或 ≥1 个属性）。
    # ⚠️ 不能只看 BodhiModule 节点（2026-09-20 实测）：历史上泄漏的导入会把子模块的**空模块节点**
    #    插回来（其语句里写着 `affects: 'bmm'`），只看模块节点就会给它生成一个"只有模块页、
    #    没有类也没有关系"的空模块页。有内容才算活着。
    modules = {str(r.get("m")) for r in ke_neo4j.query(
        "MATCH (c:BodhiOntClass) WHERE c.bodhi_projection = 'ontology' "
        "AND coalesce(c.external, false) = false AND c.module IS NOT NULL "
        "RETURN DISTINCT c.module AS m") if r.get("m")}
    modules |= {str(r.get("m")) for r in ke_neo4j.query(
        "MATCH (p:BodhiOntProperty) WHERE p.bodhi_projection = 'ontology' "
        "AND p.module IS NOT NULL RETURN DISTINCT p.module AS m") if r.get("m")}
    classes = {str(r.get("n")) for r in ke_neo4j.query(
        "MATCH (c:BodhiOntClass) WHERE c.bodhi_projection = 'ontology' "
        "AND coalesce(c.external, false) = false AND c.prefixed IS NOT NULL "
        "RETURN DISTINCT c.prefixed AS n") if r.get("n")}
    relations = {str(r.get("n")) for r in ke_neo4j.query(
        "MATCH (p:BodhiOntProperty {property_kind: 'object'}) "
        "WHERE p.bodhi_projection = 'ontology' AND p.prefixed IS NOT NULL "
        "RETURN DISTINCT p.prefixed AS n") if r.get("n")}
    return {"modules": modules, "classes": classes, "relations": relations}


def _prune_against_neo4j(data: dict) -> dict:
    """把 json 里「Neo4j 已经没有」的模块 / 类 / 关系剔掉 —— **Neo4j 是权威，json 只是展示增强**。

    为什么必须做（2026-09-20 用户实测踩到）：级联删除清掉了 ea / ea-service / ea-ownership 的图节点
    与 wiki 页，可紧接着的 wiki 重投影又按 json（构建期快照，永远列着 5 个已登记模块）把它们的页面
    写了回去 —— 现象是「清理成功了，但 ea 的 2 个子模型还在」。json 不会跟着 cascade 变，所以这里
    做**减法**：不在 Neo4j 里的模块/类/关系，wiki 里也不该有。
    返回统计供 CLI/日志用；Neo4j 不可用会抛异常，调用方回退成「json 原样」（宁可多页，也不静默清空）。
    """
    live = _neo4j_live_sets()
    dropped_modules: list[str] = []
    dropped_items = 0
    kept_models: list[dict] = []
    for model in data.get("models") or []:
        if str(model.get("key") or "") not in live["modules"]:
            dropped_modules.append(str(model.get("key") or ""))
            continue
        # 类 → 按 prefixed 名对齐；关系与跨模块桥（同形，桥是"别的模块的关系被本模块引用"）→ 同理
        for field, pool in (("classes", live["classes"]), ("relations", live["relations"]),
                            ("cross_module_bridges", live["relations"])):
            rows = model.get(field) or []
            if not rows:
                continue
            kept = [r for r in rows if r.get("name") in pool]
            dropped_items += len(rows) - len(kept)
            model[field] = kept
        kept_models.append(model)

    data["models"] = kept_models
    if data.get("module_order"):
        data["module_order"] = [k for k in data["module_order"] if k in live["modules"]]
    totals = data.setdefault("totals", {})
    totals["modules"] = len(kept_models)
    totals["classes"] = sum(len(m.get("classes") or []) for m in kept_models)
    totals["object_properties"] = sum(len(m.get("relations") or []) for m in kept_models)
    totals["cross_module_bridges"] = sum(len(m.get("cross_module_bridges") or []) for m in kept_models)
    return {"dropped_modules": dropped_modules, "dropped_items": dropped_items,
            "live_modules": sorted(live["modules"])}


def load_index() -> dict:
    """本体目录（**真源是 Neo4j 投影**；json 兜底 + 展示增强）。

    口径（2026-09-20 修正）：先按 Neo4j 实况**剔掉 json 里的过期内容**（级联删除过的模块不能复活），
    再补录「Neo4j 有、json 没有」的模块/类/关系（上传导入的模块不产 json）。
    顺序很重要：先减后加 —— 补录的内容本来就来自 Neo4j，不会被接着的减法误删。
    """
    data = json.loads(INDEX_PATH.read_text(encoding="utf-8"))
    try:
        pruned = _prune_against_neo4j(data)
        if pruned["dropped_modules"] or pruned["dropped_items"]:
            print("[wiki] 按 Neo4j 实况剔除 json 过期内容：模块 %s / 类与关系 %d 条（现存模块：%s）"
                  % ("、".join(pruned["dropped_modules"]) or "无", pruned["dropped_items"],
                     "、".join(pruned["live_modules"]) or "无"), flush=True)
            if not data["models"]:
                print("[wiki] ⚠️ Neo4j 里当前没有任何本体模块 —— wiki 只会生成总览页（index）。"
                      "要用 artifacts 回放全部模块：POST /bodhi/ontology/load {compile:false}",
                      flush=True)
    except Exception as exc:  # noqa: BLE001  剔除失败不阻断生成，但要说出来（否则会残留已删模块的页）
        print("[wiki] Neo4j 实况剔除失败（按 json 原样生成，可能残留已删模块的页）：%s" % exc, flush=True)
    try:
        _supplement_from_neo4j(data)
    except Exception as exc:  # noqa: BLE001  补录失败不影响 json 版结果，但要说出来（否则上传的模块静默缺页）
        print("[wiki] Neo4j 补录失败（上传的模块将不会生成页面）：%s" % exc, flush=True)
    return data


def load_light_text(module: dict) -> str:
    if not module.get("light_available"):
        return ""
    for key in ("light_prompt", "light_source"):
        rel = module.get(key)
        if rel and (REPO / rel).is_file():
            return (REPO / rel).read_text(encoding="utf-8")
    return ""


def overview_page(builder: WikiBuilder) -> None:
    index = builder.index
    classes_total = sum(len(m["classes"]) for m in builder.models)
    relations_total = sum(len(m["relations"]) for m in builder.models)
    bridges_total = sum(len(m.get("cross_module_bridges") or []) for m in builder.models)
    ns = index.get("namespace") or {}
    ns_text = (ns.get("base") or ns.get("base_iri")
               or "、".join("%s=%s" % (k, v) for k, v in list(ns.items())[:5]))
    out_slugs = [slug_module(m["key"]) for m in builder.models]
    props_total = sum(len(v) for v in builder.properties.values())
    data_total = sum(1 for v in builder.properties.values() for p in v
                     if p.get("kind") == "DatatypeProperty")
    lines = ["# 企业本体模型 · 总览", "",
             "> **类型**：本体模块（`%s`，根页）  " % TYPE_MODULE,
             "> **规模**：模块 %d 个 ｜ 类 %d 个 ｜ 关系 %d 条 ｜ 属性 %d 条（数据属性 %d） ｜ 跨模块桥 %d 条  "
             % (len(builder.models), classes_total, relations_total, props_total, data_total,
                bridges_total),
             "> **命名空间**：`%s`  " % ns_text,
             "> **生成**：`%s` @ %s（编译产物 schema %s）"
             % (TOOL_TAG, builder.generated_at, index.get("artifact_schema_version", "?")), "",
             "## 这个知识库是什么", "",
             "这里存放**企业本体模型的权威定义**：每个本体类一页、每条关系一页、"
             "**每个数据属性一页**（对象属性由「本体关系」目录下的关系页承担，不再重复建属性页）。"
             "页面由 `ontology/*.ttl` 编译 + Neo4j 本体投影生成，"
             "**改 TTL → 重新编译/加载 → 重新投影**即可更新本库。", "",
             "## 页面类型说明", "",
             "| 页面类型 | 含义 |", "| --- | --- |",
             "| `%s` | 模块页/总览页（本页） |" % TYPE_MODULE,
             "| `%s` | 一个本体类：定义、父类/子类、相关关系（domain/range）、**属性定义**、约束 |" % TYPE_CLASS,
             "| `%s` | 一条本体关系：方向 domain → range、逆关系、函数型、定义 |" % TYPE_RELATION,
             "| `%s` | 一个**数据属性**：定义、定义域、值域、反向属性 |" % TYPE_PROPERTY,
             "| `%s` | 该模块的轻量版提示词全文（抽取时喂给 LLM 的正文） |" % TYPE_LIGHT, "",
             "## 模块", ""]
    for module in builder.models:
        light = "（含轻量版）" if module.get("light_available") else ""
        lines.append("- %s — 类 %d ／ 关系 %d ／ 跨模块桥 %d %s"
                      % (builder.class_link_module(module), len(module["classes"]),
                         len(module["relations"]),
                         len(module.get("cross_module_bridges") or []), light))
    lines += ["", "## 怎么用（给其他知识库/智能体）", "",
              "- 抽取时：把本库一起选进对话，智能体可用 `wiki_search` / `wiki_read_page` 查类与关系的定义；",
              "- 归类时：`page_type` 一律写成 `模块:类`（例如 `bmm:Goal`），必须取自上面的类页；",
              "- 关系必须满足其 domain → range（见对应关系页的「方向」），否则视为违规。", ""]
    builder.add(slug="ontology/index", title="企业本体模型 · 总览", page_type=TYPE_MODULE,
                # group 留空：总览页**直接落「总览」目录**（旧实现 group="总览" → 页被挂到
                # `企业本体模型/总览/总览`，目录树里「总览」显示数量却看不到页 —— 2026-09-30 用户实测）
                module_label="总览", group="",
                content="\n".join(lines).rstrip() + "\n",
                summary="企业本体模型总览：%d 个模块、%d 个本体类、%d 条关系（含 %d 条跨模块桥）；页面类型与用法说明。"
                        % (len(builder.models), classes_total, relations_total, bridges_total),
                wiki_path="ontology/index", out_slugs=out_slugs,
                metadata={"kind": "overview",
                          "totals": {"classes": classes_total, "relations": relations_total,
                                     "bridges": bridges_total},
                          "generated_at": builder.generated_at, "generator": TOOL_TAG})


def class_link_module(self, module: dict) -> str:  # noqa: ANN001 - 挂到 WikiBuilder 上
    return wlink("%s（%s）" % (module["label"], module["key"]), slug_module(module["key"]))


WikiBuilder.class_link_module = class_link_module  # type: ignore[attr-defined]


def build_pages() -> tuple[list[dict], dict]:
    index = load_index()
    builder = WikiBuilder(index)
    bridge_names = {rel["name"] for m in builder.models
                    for rel in (m.get("cross_module_bridges") or [])}
    for module in builder.models:
        builder.module_page(module)
        for cls in module["classes"]:
            builder.class_page(module, cls)
        for rel in module["relations"]:
            # 关系若同时出现在某模块的 bridge 列表里，就标为「跨模块桥」
            builder.relation_page(module, rel, is_bridge=rel["name"] in bridge_names)
        for rel in module.get("cross_module_bridges") or []:
            builder.relation_page(module, rel, is_bridge=True)
        # **数据属性**定义页（用户口径 2026-09-30：属性目录**只放数据属性**；
        # 对象属性由「关系页」承担 → 不再重复建属性页，目录也不该混着放）
        for prop in builder.properties.get(module["key"]) or []:
            if (prop.get("kind") or "ObjectProperty") == "ObjectProperty":
                continue
            builder.property_page(module, prop)
        light = load_light_text(module)
        if light:
            builder.light_page(module, light)
    overview_page(builder)

    # 反向链接（wiki 图谱要用）
    inbound: dict[str, set[str]] = {}
    for slug, targets in builder.links.items():
        for target in targets:
            inbound.setdefault(target, set()).add(slug)
    for page in builder.pages:
        page["in_links"] = sorted(inbound.get(page["slug"], set()))

    def kind(page: dict) -> str:
        return (page["page_metadata"].get("ontology") or {}).get("kind", "")

    stats = {
        "modules": len(builder.models),
        "classes": sum(1 for p in builder.pages if p["page_type"] == TYPE_CLASS),
        "relations": sum(1 for p in builder.pages
                         if p["page_type"] == TYPE_RELATION and kind(p) == "relation"),
        "bridges": sum(1 for p in builder.pages
                       if p["page_type"] == TYPE_RELATION and kind(p) == "bridge"),
        "properties": sum(1 for p in builder.pages if p["page_type"] == TYPE_PROPERTY),
        "data_properties": sum(1 for p in builder.pages if p["page_type"] == TYPE_PROPERTY
                               and (p["page_metadata"].get("ontology") or {}).get("prop_kind")
                               == "DatatypeProperty"),
        "light": sum(1 for p in builder.pages if p["page_type"] == TYPE_LIGHT),
        "pages": len(builder.pages),
    }
    return builder.pages, stats


# ---------------------------------------------------------------------------
# 产物读写
# ---------------------------------------------------------------------------
def write_jsonl(pages: list[dict], path: pathlib.Path = OUT_PATH) -> pathlib.Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        for page in pages:
            handle.write(json.dumps(page, ensure_ascii=False) + "\n")
    return path


def read_jsonl(path: pathlib.Path = OUT_PATH) -> list[dict]:
    if not path.is_file():
        raise SystemExit("缺少 %s，先跑 `build`" % path.relative_to(REPO).as_posix())
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


# ---------------------------------------------------------------------------
# 投影到知识库（Postgres）
# ---------------------------------------------------------------------------
def _docker_prefix() -> list[str]:
    import shutil  # noqa: PLC0415
    if shutil.which("docker"):
        probe = subprocess.run(["docker", "version", "--format", "{{.Server.Version}}"],
                               capture_output=True, text=True, check=False)
        if probe.returncode == 0:
            return ["docker"]
    if shutil.which("wsl"):
        probe = subprocess.run(["wsl", "-d", "Ubuntu", "-u", "root",
                                "docker", "version", "--format", "{{.Server.Version}}"],
                               capture_output=True, text=True, check=False)
        if probe.returncode == 0:
            return ["wsl", "-d", "Ubuntu", "-u", "root", "docker"]
    raise SystemExit("找不到可用的 docker（Windows PATH 或 WSL 里都没有）")


def psql(sql: str, stdin: bool = False) -> str:
    # 与 ke-core/ke_db.py 同口径：设了 BODHI_DB_HOST 就**直连 TCP**（容器/远端部署），
    # 否则沿用 `docker exec psql`（本机开发）。
    host = os.environ.get("BODHI_DB_HOST", "").strip()
    if host:
        import shutil  # noqa: PLC0415
        exe = shutil.which("psql")
        if not exe:
            raise SystemExit("已设 BODHI_DB_HOST 但 PATH 里没有 psql 客户端（apt install postgresql-client）")
        cmd = [exe, "-h", host, "-p", os.environ.get("BODHI_DB_PORT", "5432"),
               "-U", DB_USER, "-d", DB_NAME]
        env = dict(os.environ, PGPASSWORD=DB_PASSWORD)
    else:
        cmd = _docker_prefix() + ["exec", "-i", "-e", "PGPASSWORD=" + DB_PASSWORD,
                                  DB_CONTAINER, "psql", "-U", DB_USER, "-d", DB_NAME]
        env = None
    if stdin:
        cmd += ["-v", "ON_ERROR_STOP=1", "-q", "-f", "-"]
        done = subprocess.run(cmd, input=sql, text=True, encoding="utf-8",
                              capture_output=True, check=False, env=env)
    else:
        cmd += ["-t", "-A", "-c", sql]
        done = subprocess.run(cmd, text=True, encoding="utf-8", capture_output=True,
                              check=False, env=env)
    if done.returncode != 0:
        raise SystemExit("psql 失败：%s" % (done.stderr or done.stdout)[:900])
    return done.stdout


def sql_str(value) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def sql_json(value) -> str:
    return sql_str(json.dumps(value, ensure_ascii=False)) + "::jsonb"


def page_id_for(kb_id: str, slug: str) -> str:
    """(知识库, slug) → 确定性页 id。

    2026-09-24 修：旧实现只按 slug 派生（`bodhi-ontology:<slug>`）→ 与别的库里同名页 id 相同，
    跨库会撞主键（与 MCP 侧同一个 bug）。投影前会先按库删旧页（`project` 里的 DELETE），
    所以换 id 只会让本库的这几页重建一次，不产生重复页。
    """
    return str(uuid.uuid5(uuid.NAMESPACE_URL, "bodhi-ontology:%s|%s" % (kb_id, slug)))


def project(pages: list[dict], kb_id: str) -> str:
    row = psql("SELECT tenant_id FROM knowledge_bases WHERE id = %s AND deleted_at IS NULL"
               % sql_str(kb_id))
    if not row.strip():
        raise SystemExit("知识库不存在：%s" % kb_id)
    tenant_id = int(row.strip().splitlines()[0])
    out = ["-- 由 tools/ontology-extract/ontology_wiki.py 生成（本体模型知识库）",
           "BEGIN;",
           "DELETE FROM wiki_pages WHERE knowledge_base_id = %s AND last_edit_source = %s;"
           % (sql_str(kb_id), sql_str(TOOL_TAG))]
    for page in pages:
        values = [
            sql_str(page_id_for(kb_id, page["slug"])), str(tenant_id), sql_str(kb_id),
            sql_str(page["slug"]), sql_str(page["title"]), sql_str(page["page_type"]),
            sql_str("published"), sql_str(page["content"]), sql_str(page["summary"]),
            sql_str(""), sql_str(""), sql_json(page["category_path"]),
            sql_str(page["wiki_path"]), str(len(page["category_path"])), "0",
            sql_json([]), sql_json([]), sql_json(page.get("in_links") or []),
            sql_json(page.get("out_links") or []), sql_json(page["page_metadata"]),
            sql_json([page["title"]]), "1", sql_str(TOOL_TAG), sql_str(""),
        ]
        out.append("INSERT INTO wiki_pages (%s) VALUES (%s);"
                   % (", ".join(PAGE_COLUMNS), ", ".join(values)))
    out.append("COMMIT;")
    sql = "\n".join(out) + "\n"
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    dump = LOG_DIR / ("ontology_wiki_%s.sql" % datetime.now().strftime("%Y%m%d_%H%M%S"))
    dump.write_text(sql, encoding="utf-8")
    psql(sql, stdin=True)
    print("[ontology-wiki] 已投影 %d 页到 %s；SQL 留档 %s"
          % (len(pages), kb_id, dump.relative_to(REPO).as_posix()))
    return psql("SELECT page_type || ' = ' || count(*) FROM wiki_pages "
                "WHERE knowledge_base_id = %s AND deleted_at IS NULL "
                "GROUP BY page_type ORDER BY page_type" % sql_str(kb_id))


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main() -> int:
    parser = argparse.ArgumentParser(description="本体模型 → wiki 页面（企业本体模型知识库）")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("build", help="由 ontology_index.json 生成页面清单（jsonl）")
    sub.add_parser("stats", help="只看统计")
    project_cmd = sub.add_parser("project", help="投影到知识库")
    project_cmd.add_argument("--kb-id", required=True, help="目标知识库 UUID")
    args = parser.parse_args()

    pages, stats = build_pages()
    print("[ontology-wiki] 模块 %d ｜ 类 %d ｜ 关系 %d ｜ 跨模块桥 %d ｜ 轻量版 %d ｜ 总页数 %d"
          % (stats["modules"], stats["classes"], stats["relations"], stats["bridges"],
             stats["light"], stats["pages"]))
    by_type: dict[str, int] = {}
    for page in pages:
        by_type[page["page_type"]] = by_type.get(page["page_type"], 0) + 1
    for key in sorted(by_type):
        print("               %-20s %d 页" % (key, by_type[key]))
    if args.cmd == "stats":
        return 0
    if args.cmd == "build":
        path = write_jsonl(pages)
        print("[ontology-wiki] 写出 %s（%d 行）" % (path.relative_to(REPO).as_posix(), len(pages)))
        return 0
    result = project(pages, args.kb_id)
    print("[ontology-wiki] 库内分布：")
    for line in result.strip().splitlines():
        print("               %s" % line)
    return 0


if __name__ == "__main__":
    sys.exit(main())







