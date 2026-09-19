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

DB_CONTAINER, DB_USER, DB_NAME, DB_PASSWORD = "WeKnora-postgres", "postgres", "WeKnora", "postgres123!@#"

TYPE_MODULE = "ontology:Module"
TYPE_CLASS = "ontology:Class"
TYPE_RELATION = "ontology:Relation"
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

    def add(self, *, slug: str, title: str, page_type: str, module_label: str,
            group: str, content: str, summary: str, wiki_path: str,
            out_slugs: list[str], metadata: dict) -> None:
        if slug in self.seen:
            # 跨模块桥会同时出现在 relations[] 与 cross_module_bridges[] 里，
            # 同一个 slug 只保留一条（先出现的优先），避免 id/slug 冲突。
            print("[ontology-wiki] 跳过重复 slug：%s" % slug)
            return
        self.seen.add(slug)
        self.pages.append({
            "slug": slug, "title": title, "page_type": page_type,
            "content": content, "summary": summary,
            "category_path": ["企业本体模型", module_label, group],
            "wiki_path": wiki_path,
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
        if cls.get("restriction_count"):
            lines += ["## 约束", "", "- 该类的 OWL 限制（restriction）数量：%d" % cls["restriction_count"], ""]

        self.add(slug=slug_class(module["key"], name),
                 title="%s（%s）" % (label, name), page_type=TYPE_CLASS,
                 module_label=module["label"], group="本体类",
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
                 module_label=module["label"],
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
        if module.get("light_available"):
            light_slug = slug_module(key) + "/light"
            lines += ["", "## 轻量版提示词", "",
                      "- 全文见：%s" % wlink("%s 轻量版" % label, light_slug), ""]
            out_slugs.append(light_slug)
        lines += ["", "## 说明", "",
                  "本页由 `ontology/%s` 的 TTL 编译生成（`%s`，生成于 %s）。"
                  % (key, TOOL_TAG, self.generated_at), ""]

        self.add(slug=slug_module(key), title="%s（%s）" % (label, key),
                 page_type=TYPE_MODULE, module_label=label, group="模块",
                 content="\n".join(lines).rstrip() + "\n",
                 summary="%s 模块：%d 个本体类、%d 条关系。" % (label, len(classes), len(rels)),
                 wiki_path=slug_module(key), out_slugs=out_slugs,
                 metadata={"kind": "module", "model": key, "classes": len(classes),
                           "relations": len(rels), "bridges": len(bridges),
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
                 module_label=label, group="轻量版",
                 content="\n".join(lines).rstrip() + "\n",
                 summary="%s 模块的轻量版提示词全文（%d 字符）。" % (label, len(text)),
                 wiki_path=slug, out_slugs=[slug_module(key)],
                 metadata={"kind": "light", "model": key, "chars": len(text),
                           "generated_at": self.generated_at, "generator": TOOL_TAG})


# ---------------------------------------------------------------------------
# 组装全量页面
# ---------------------------------------------------------------------------
def load_index() -> dict:
    return json.loads(INDEX_PATH.read_text(encoding="utf-8"))


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
    totals = index.get("totals") or {}
    out_slugs = [slug_module(m["key"]) for m in builder.models]
    lines = ["# 企业本体模型 · 总览", "",
             "> **类型**：本体模块（`%s`，根页）  " % TYPE_MODULE,
             "> **规模**：模块 %d 个 ｜ 类 %s 个 ｜ 关系 %s 条 ｜ 跨模块桥 %s 条  "
             % (len(builder.models), totals.get("classes", "?"),
                totals.get("relations", "?"), totals.get("bridges", "?")),
             "> **命名空间**：`%s`  " % (index.get("namespace") or {}).get("base", ""),
             "> **生成**：`%s` @ %s（编译产物 schema %s）"
             % (TOOL_TAG, builder.generated_at, index.get("artifact_schema_version", "?")), "",
             "## 这个知识库是什么", "",
             "这里存放**企业本体模型的权威定义**：每个本体类一页、每条关系一页，"
             "关系在页面里用链接表达（domain → range）。页面由 `ontology/*.ttl` 编译生成，"
             "**改 TTL → 重新编译 → 重新投影**即可更新本库。", "",
             "## 页面类型说明", "",
             "| 页面类型 | 含义 |", "| --- | --- |",
             "| `%s` | 模块页/总览页（本页） |" % TYPE_MODULE,
             "| `%s` | 一个本体类：定义、父类/子类、相关关系（domain/range）、约束、图谱颜色 |" % TYPE_CLASS,
             "| `%s` | 一条本体关系：方向 domain → range、逆关系、函数型、定义 |" % TYPE_RELATION,
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
                module_label="总览", group="总览",
                content="\n".join(lines).rstrip() + "\n",
                summary="企业本体模型总览：%d 个模块、%s 个本体类、%s 条关系；页面类型与用法说明。"
                        % (len(builder.models), totals.get("classes", "?"),
                           totals.get("relations", "?")),
                wiki_path="ontology/index", out_slugs=out_slugs,
                metadata={"kind": "overview", "totals": totals,
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
    cmd = _docker_prefix() + ["exec", "-i", "-e", "PGPASSWORD=" + DB_PASSWORD,
                              DB_CONTAINER, "psql", "-U", DB_USER, "-d", DB_NAME]
    if stdin:
        cmd += ["-v", "ON_ERROR_STOP=1", "-q", "-f", "-"]
        done = subprocess.run(cmd, input=sql, text=True, encoding="utf-8",
                              capture_output=True, check=False)
    else:
        cmd += ["-t", "-A", "-c", sql]
        done = subprocess.run(cmd, text=True, encoding="utf-8", capture_output=True, check=False)
    if done.returncode != 0:
        raise SystemExit("psql 失败：%s" % (done.stderr or done.stdout)[:900])
    return done.stdout


def sql_str(value) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def sql_json(value) -> str:
    return sql_str(json.dumps(value, ensure_ascii=False)) + "::jsonb"


def page_id_for(slug: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, "bodhi-ontology:" + slug))


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
            sql_str(page_id_for(page["slug"])), str(tenant_id), sql_str(kb_id),
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







