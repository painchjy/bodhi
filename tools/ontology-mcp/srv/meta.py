"""本体面 + 技能目录（批 3b 迁自 `server.py`，**实现逐字未改**）

- `ontology_types(...)`：类/关系/数据属性清单。**产物 JSON 为主 + Neo4j 投影补录**
  （上传导入的模块不产 JSON 产物 ⇒ 只有投影里看得见，见巡检 B5）；
- `skills(...)`：不传参回**目录**，传参回该技能**全文 + 收窄后的本体面**；
- 技能真源 = `skills/<id>/SKILL.md`（front-matter + 正文），按 mtime 缓存 ⇒ **加技能不用改代码** ✓。
"""
from __future__ import annotations

import json
import pathlib

import ke_neo4j
import ke_ontology
import ke_yamlmini

REPO = pathlib.Path(__file__).resolve().parents[3]
INDEX_PATH = REPO / "artifacts" / "weknora" / "ontology_index.json"


# ---------------------------------------------------------------------------
# 技能（skills/）：目录 + 指令 + 该技能的本体面
# ---------------------------------------------------------------------------
# 为什么由 MCP 承载技能而不是塞进智能体提示词：
#   ① 省 token（智能体先看目录，用到哪个取哪个）；② 技能改了不用重新注册智能体；
#   ③ 未来若开 WeKnora 沙箱技能（tenant_skills/bundle），同一份 SKILL.md 可直接打成 bundle。
# 本次部署的 WeKnora 是**沙箱安装型**技能（tenant_sandbox_configs 为空、SANDBOX_DOCKER=false），
# 原生技能不可用 —— 所以由 MCP 提供等价能力（见 docs/agent-design-flow.md §11）。
SKILLS_DIR = REPO / "skills"
_SKILLS_CACHE: dict | None = None
_SKILLS_STAMP: tuple | None = None


def _skills_stamp() -> tuple:
    try:
        return tuple(sorted((p.parent.name, p.stat().st_mtime, p.stat().st_size)
                            for p in SKILLS_DIR.glob("*/SKILL.md")))
    except Exception:  # noqa: BLE001
        return ()


def load_skills() -> dict:
    """读 `skills/<id>/SKILL.md`（front-matter + 正文），按 mtime 缓存。"""
    global _SKILLS_CACHE, _SKILLS_STAMP
    stamp = _skills_stamp()
    if _SKILLS_CACHE is not None and stamp == _SKILLS_STAMP:
        return dict(_SKILLS_CACHE)
    try:
        import yaml as _yaml            # 有 PyYAML 就用它（本机开发环境通常有）
    except Exception:                    # noqa: BLE001  干净容器没有 PyYAML → 用 ke-core 的子集解析
        _yaml = None
    out: dict[str, dict] = {}
    for path in sorted(SKILLS_DIR.glob("*/SKILL.md")):
        text = path.read_text(encoding="utf-8")
        meta, body = {}, text
        if text.startswith("---"):
            parts = text.split("---", 2)
            if len(parts) >= 3:
                try:
                    meta = (_yaml.safe_load(parts[1]) if _yaml else ke_yamlmini.safe_load(parts[1])) or {}
                except Exception:  # noqa: BLE001  front-matter 写坏不该让技能整块消失
                    meta = {}
                body = parts[2].strip()
        sid = str(meta.get("id") or path.parent.name)
        out[sid] = {"id": sid, "dir": path.parent.name, "path": str(path),
                    "meta": meta, "instructions": body}
    _SKILLS_CACHE, _SKILLS_STAMP = out, stamp
    return dict(out)


def skills(skill: str = "", model: str = "") -> dict:
    """技能目录 / 技能全文 + 该技能需要的那部分本体面。

    - 不传 `skill`：返回**目录**（id / name / when / models / stages / tools）；
    - 传 `skill`：返回该技能**完整指令**（SKILL.md 正文）+ `front_matter`，
      并按 front-matter 的 `scope`（focus/classes/relations）把 `ontology_types` **收窄**后一并返回
      —— 智能体拿到"这个技能能用的类/关系/数据属性"，不用再自己筛。
    """
    all_skills = load_skills()
    if not skill:
        catalog = []
        for sid, item in all_skills.items():
            meta = item["meta"]
            catalog.append({"id": sid, "name": meta.get("name") or sid,
                            "when": meta.get("when") or "",
                            "models": meta.get("models") or [],
                            "default_model": meta.get("default_model") or "",
                            "stages": meta.get("stages") or [],
                            "tools": meta.get("tools") or []})
        return {"count": len(catalog), "catalog": catalog,
                "how_to_use": ("先用本目录选技能，再 `skills(skill=\"<id>\")` 取该技能完整指令"
                               "（含它需要的类/关系/数据属性）。**不要凭记忆猜步骤**。")}
    if skill not in all_skills:
        raise ValueError("未知技能：%s（可用：%s）" % (skill, "、".join(sorted(all_skills)) or "无"))
    item = all_skills[skill]
    meta = item["meta"]
    scope = meta.get("scope") or {}
    model_key = (model or meta.get("default_model")
                 or ((meta.get("models") or ["ea"])[0]))
    ontology = {}
    try:
        ontology = ontology_types(model_key, focus=str(scope.get("focus") or ""),
                                  classes=scope.get("classes") or None,
                                  relations=scope.get("relations") or None)
    except Exception as exc:  # noqa: BLE001  技能仍可用（只是没有本体面）
        ontology = {"error": str(exc)[:160]}
    if isinstance(ontology, dict):   # 去重（ontology_types 会把"本模块引用"的类/关系并进来）
        for key, field in (("classes", "name"), ("relations", "name")):
            seen, uniq = set(), []
            for row in ontology.get(key) or []:
                if row.get(field) in seen:
                    continue
                seen.add(row.get(field))
                uniq.append(row)
            if uniq:
                ontology[key] = uniq
    return {"id": skill, "name": meta.get("name") or skill, "when": meta.get("when") or "",
            "front_matter": meta, "instructions": item["instructions"],
            "ontology_model": model_key, "ontology": ontology,
            "source": item["path"].replace(str(REPO) + "/", "")}


def ontology_types(model_key: str, focus: str = "", classes: list | None = None,
                   relations: list | None = None, with_attributes: bool = True) -> dict:
    """某本体模型的类与关系清单（供智能体选类型/关系）。

    2026-09-21 收窄参数（「领域知识建模」技能按对话缩范围用）：
      - `focus`：关键词（匹配类/关系的中文 label 或 prefixed，大小写不敏感）；
      - `classes` / `relations`：显式白名单（prefixed）；给了白名单就以它为准。
    2026-09-21 数据属性：每个类带 `attributes`（该类+祖先允许的数据属性，见 ke_ontology.data_properties_for）
      —— 「通用保存」的 schema 一半：节点 attributes 的键、边限定属性的键都按它校验，不再是硬编码。

    2026-09-21：**以 Neo4j 投影为真源补录**（`ke_ontology.class_meta()` 已含上传导入模块），
    否则上传的新模块（如 `bmm-ea-ext`）在旧产物里看不到 —— 表现就是智能体"看不见新类型/新关系"
    （对应巡检 B5：本体投影 ↔ 编译产物不一致）。
    """
    index = json.loads(INDEX_PATH.read_text(encoding="utf-8"))
    for model in index["models"]:
        if model["key"] == model_key:
            out = {
                "model": model["key"], "label": model["label"],
                "expert_role": model.get("expert_role", ""),
                "classes": [{"name": c["name"], "label": c.get("label"),
                             "definition": c.get("definition")} for c in model["classes"]],
                "relations": [{"name": r["name"], "label": r.get("label"),
                               "domain": r.get("domain"), "range": r.get("range")}
                              for r in list(model["relations"])
                              + list(model.get("cross_module_bridges") or [])],
                "source": "artifacts",
            }
            try:
                proj_classes = [r["name"] for r in ke_neo4j.query(
                    "MATCH (c:BodhiOntClass) WHERE c.bodhi_projection = 'ontology' "
                    "AND coalesce(c.external,false) = false AND c.prefixed IS NOT NULL "
                    "RETURN DISTINCT c.prefixed AS name ORDER BY name") if r.get("name")]
                proj_props = [{"name": r["name"], "label": r.get("label") or "",
                               "domain": r.get("domain"), "range": r.get("range")}
                              for r in ke_neo4j.query(
                    "MATCH (p:BodhiOntProperty {property_kind:'object'}) "
                    "WHERE p.bodhi_projection = 'ontology' AND p.prefixed IS NOT NULL "
                    "OPTIONAL MATCH (p)-[:BODHI_DOMAIN]->(d:BodhiOntClass) "
                    "OPTIONAL MATCH (p)-[:BODHI_RANGE]->(r:BodhiOntClass) "
                    "RETURN DISTINCT p.prefixed AS name, p.label AS label, "
                    "       collect(DISTINCT d.prefixed) AS domain, collect(DISTINCT r.prefixed) AS range "
                    "ORDER BY name")]
            except Exception:  # noqa: BLE001  投影不可用就用产物（保持旧行为）
                proj_classes, proj_props = [], []
            # 只把「本模块引用」与「上传导入（该前缀不在产物模块里）」的类/关系补进结果；
            # 说明文案也**只在真有上传导入模块时**才标「产物可能过期」——
            # 否则投影里其它模块的类会被当成漂移，出现「已用投影补录（见 B5）」的误导提示
            # （2026-09-21：产物与投影已一致，提示却仍在）。
            model_prefixes = {str(m.get("prefix") or "") for m in index["models"]}
            ref_classes = {c["name"] for c in (model.get("referenced") or {}).get("classes") or []}
            have_cls = {c["name"] for c in out["classes"]}
            uploaded_cls, referenced_cls = [], []
            for name in proj_classes:
                if name in have_cls:
                    continue
                meta = ke_ontology.class_meta().get(name) or {}
                if name.split(":")[0] in model_prefixes:
                    if name not in ref_classes:
                        continue  # 别的模块自己的类，不是本模块契约
                    out["classes"].append({"name": name, "label": meta.get("label") or name,
                                           "definition": meta.get("definition") or ""})
                    referenced_cls.append(name)
                else:
                    out["classes"].append({"name": name, "label": meta.get("label") or name,
                                           "definition": "（来自运行投影：上传导入的模块）"})
                    uploaded_cls.append(name)
            have_rel = {r["name"] for r in out["relations"]}
            uploaded_rel = []
            for prop in proj_props:
                if prop["name"] in have_rel:
                    continue
                if str(prop["name"]).split(":")[0] in model_prefixes:
                    out["relations"].append({**prop, "note": "（本模块引用）"})
                else:
                    out["relations"].append({**prop, "note": "（来自运行投影：上传导入的模块）"})
                    uploaded_rel.append(prop["name"])
            # 收窄：显式白名单优先，其次关键词（匹配 prefixed 或中文 label）
            key = (focus or "").strip().lower()
            cls_list = [c for c in out["classes"]
                        if (not classes or c["name"] in set(classes))
                        and (not key or key in c["name"].lower()
                             or key in (c.get("label") or "").lower())]
            rel_list = [r for r in out["relations"]
                        if (not relations or r["name"] in set(relations))
                        and (not key or key in r["name"].lower()
                             or key in (r.get("label") or "").lower())]
            if key or classes or relations:
                out["classes"], out["relations"] = cls_list, rel_list
                out["narrowed"] = {"focus": focus or "", "classes": len(cls_list),
                                   "relations": len(rel_list)}
            if with_attributes:
                # 「通用保存」的 schema：每个类允许哪些数据属性（类 + 祖先声明）
                attrs = {}
                for cls in out["classes"]:
                    allowed = ke_ontology.data_properties_for(cls["name"])
                    if allowed:
                        attrs[cls["name"]] = [
                            {"name": a["prefixed"], "label": a.get("label") or "",
                             "range": ke_ontology.short_iri(a.get("range_literal") or "")}
                            for a in sorted(allowed.values(), key=lambda x: x["prefixed"])]
                out["class_attributes"] = attrs
            # 2026-10-22（内网问题 4.4）：**让智能体看到类层级** ——
            # `parents` / `children` 直接给出，省掉"临时 lookup 后猜"的来回 ✓
            try:
                _meta = ke_ontology.class_meta()
                _kids: dict = {}
                for _n, _m2 in _meta.items():
                    for _p2 in (_m2.get("parents") or []):
                        _kids.setdefault(_p2, []).append(_n)
                for _c in out["classes"]:
                    _m2 = _meta.get(_c.get("name")) or {}
                    _c["parents"] = list(_m2.get("parents") or [])
                    _c["children"] = sorted(_kids.get(_c.get("name")) or [])
            except Exception:  # noqa: BLE001  本体元信息不可用 ⇒ 不影响主回执
                pass
            # 数据属性（如 ea:ai_skill）—— 设计要写"AI 技能/工具定义"，必须让智能体看得到
            try:
                out["data_properties"] = [{"name": r["name"], "label": r.get("label") or ""}
                                          for r in ke_neo4j.query(
                        "MATCH (p:BodhiOntProperty {property_kind:'datatype'}) "
                        "WHERE p.bodhi_projection = 'ontology' AND p.prefixed IS NOT NULL "
                        "RETURN DISTINCT p.prefixed AS name, p.label AS label ORDER BY name")
                                          if r.get("name")]
            except Exception:  # noqa: BLE001
                out["data_properties"] = []
            if uploaded_cls or uploaded_rel:
                out["source"] = "artifacts+projection"
                out["note"] = ("投影里有**上传导入、不产 JSON 产物**的模块（%s），"
                               "其类型/关系在产物里看不到（见巡检 B5）："
                               "重编 `tools/ontology-compiler/compile.py compile`、"
                               "重载 `deploy/bootstrap-neo4j.sh`"
                               % "、".join(sorted(uploaded_cls + uploaded_rel)[:6]))
            return out
    raise RuntimeError("未知本体模型：%s" % model_key)
