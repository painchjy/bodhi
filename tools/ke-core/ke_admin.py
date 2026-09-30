"""ke-core · 本体模型维护（用户 2026-09-20 口径）。

功能：**选择一个本体模型重新加载** —— 若本体模型知识库里已有该模型，先清理它的
「图库 + wiki 页」，再按**编译器产物**（规范、幂等）重建，最后重投影 wiki 页。

为什么以编译器产物为准，而不是 app 的 rdflib 解析（旧 src/ 做法）：
- 编译器产物（`artifacts/neo4j/10_ontology.cypher` + `ontology_index.json`）是**规范投影**：
  前缀由本体 TTL 声明决定（`@prefix ea:`），类/属性/继承/domain/range 一次算全；
- app 的解析曾经把 `ea:Activity` 写成 `bmm-EA-ext:Activity`（用了 TTL 默认前缀），
  导致前端按类查关系类型全空 —— 这类差异从此不再可能。

对外入口（server.py 的 HTTP 路由会调用）：
    purge_model(model)               只清理（Neo4j 模块节点/类/属性 + 该模型的 wiki 页）
    apply_projection()               把编译产物灌进 Neo4j（幂等 MERGE）
    compile_artifacts()              跑 ontology-compiler（需要 rdflib 环境）
    regen_wiki()                     重投影本体 wiki 页（ontology_wiki.py build/project）
    load_model(...)                  上面的编排：compile → purge → apply → regen
"""

from __future__ import annotations

import json
import pathlib
import re
import subprocess
import sys

HERE = pathlib.Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import ke_db  # noqa: E402
import ke_neo4j  # noqa: E402
import ke_ontology  # noqa: E402
import ke_pages  # noqa: E402

try:                       # 跨库上下文映射（一期只读；见 docs/context-mapping-plan.md）
    import ke_context  # noqa: E402
except Exception:  # noqa: BLE001
    ke_context = None  # type: ignore

REPO = HERE.parents[1]
# 本体模型知识库：**不再写死 uuid**（客户环境不是我们的 uuid）。
# 解析顺序见 ke_ontology.resolve_ontology_kb：env → wiki_config 标记 → 库名 → 内容探测。
PROJECTION_DIR = REPO / "artifacts" / "neo4j"
ONTOLOGY_DIR = REPO / "ontology"                       # 本体真源目录（含中文完整版 TTL）
# 上传的扩展模块：TTL 落到这里（真源）并登记进 `_registry.json`，编译器/导入器都读它。
EXTENSIONS_DIR = ONTOLOGY_DIR / "extensions"
EXT_REGISTRY = ONTOLOGY_DIR / "sources" / "_registry.json"
_COMP_DIR = REPO / "tools" / "ontology-compiler"       # 编译器包目录（惰性加进 sys.path）
# 上传真源目录（用户口径 2026-09-30：上传的 TTL 落这里，不再放 extensions/）
SOURCES_DIR = ONTOLOGY_DIR / "sources"


def _index_totals() -> dict:
    """读编译产物的规模（模块/类/关系/属性…）—— 用来回报"这次编译改了什么"。"""
    path = REPO / "artifacts" / "weknora" / "ontology_index.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}
    return {k: int(v) for k, v in (data.get("totals") or {}).items()
            if isinstance(v, (int, float))}


def _ensure_expert_role(ttl_text: str, key: str, iri: str) -> tuple[str, list[str]]:
    """上传的扩展模块若没声明 `bodhi:expertRole`，自动补一条默认值。

    为什么必须补：编译器对每个模块都要求专家角色（见 `ontology_compiler/config.EXPERT_ROLE` 与
    `docs/weknora-fork.md` §8.5），缺了会直接报"模块 X 缺少专家角色"而**整次编译失败**
    （2026-09-24 实测：上传即编译时踩到）。这里只做"能编过"的最小补全，并把补了什么回报出来。

    补法：确保 `@prefix bodhi:` 存在，再在**末尾追加**一条针对该模块 ontology IRI 的语句 ——
    不动原文件里的任何已有语句（Turtle 允许多条语句描述同一主体）。
    返回 `(新文本, 注入项清单)`。
    """
    if re.search(r"expertRole", ttl_text or ""):
        return ttl_text, []
    if not iri:
        raise ValueError("TTL 里没有 `a owl:Ontology` 声明（拿不到本体 IRI），"
                         "无法自动补 bodhi:expertRole；请在 TTL 里声明 <...> a owl:Ontology")
    injected: list[str] = []
    text = ttl_text or ""
    if not re.search(r"@prefix\s+bodhi:\s*<http://example\.org/bodhi#>", text):
        text = "@prefix bodhi: <http://example.org/bodhi#> .\n" + text
        injected.append("@prefix bodhi:")
    text = text.rstrip("\n") + (
        '\n\n<%s> bodhi:expertRole "外部导入模块 %s：负责本模块类与关系的抽取、维护与校验" .\n'
        % (iri, key))
    injected.append("bodhi:expertRole（默认值；可在 TTL 里改写）")
    return text, injected


def _index_module_keys() -> list[str]:
    """编译产物里的模块 key 清单（回报"这次编译新增/认识了哪些模块"）。"""
    path = REPO / "artifacts" / "weknora" / "ontology_index.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return []
    models = data.get("models") or data.get("modules") or []
    keys = []
    for item in models:
        if isinstance(item, dict) and item.get("key"):
            keys.append(str(item["key"]))
        elif isinstance(item, str):
            keys.append(item)
    return sorted(keys)


def _builtin_specs() -> dict:
    """**内置清单**（`config.builtin_specs()`，不受 registry 覆盖影响）—— 判断"上传的是内置模块"。"""
    if _COMP_DIR not in sys.path:
        sys.path.insert(0, str(_COMP_DIR))
    try:
        from ontology_compiler.config import builtin_specs  # noqa: PLC0415
        return dict(builtin_specs())
    except Exception:  # noqa: BLE001
        return {}


def _builtin_file_for(module: str) -> pathlib.Path | None:
    """内置模块在清单里的**真源文件路径**（如 bmm → `ontology/BMM完整版.ttl`）。

    为什么：上传内置模块（bmm/ea）时**就地覆盖这份文件**，而不是新建 `extensions/<key>-ext.ttl`。
    这样①"真源"就是你上传的那个文件名/位置（不再出现"文件名都不对"）②不产生三份重复内容
    ③老版本（清单指向根目录中文完整版）与新版本一致，从根上避免"清单路径 vs extensions 路径"错配。
    """
    spec = _builtin_specs().get((module or "").strip())
    if not spec:
        return None
    for path in spec.files:
        if path.is_file():
            return path
    return spec.files[0] if spec.files else None


def preflight_compile(ttl_path: pathlib.Path | None = None, module: str = "",
                      scope: list[str] | None = None) -> dict:
    """**只读预检**：**本次编译范围内**文件是否齐、上传的 TTL 能否解析 —— 不删数据、不写产物。

    用户口径（2026-09-30）：编译**不再全量** —— **编译范围 = 本模块 + 上游依赖闭包**
    （上传 bmm → 只编 bmm；上传 ea → 编 bmm+ea）；级联删除只作用于**本模块 + 下游**。
    所以预检只校验本次范围，不会因为"无关模块缺文件"而失败（旧实现全量编译 → 上传 bmm 报 ea 缺文件）。
    """
    if _COMP_DIR not in sys.path:
        sys.path.insert(0, str(_COMP_DIR))
    from dataclasses import replace as _replace                                      # noqa: PLC0415
    from ontology_compiler.config import ModuleSpec, build_modules, upstream_closure  # noqa: PLC0415
    from ontology_compiler.loader import parse_module_graphs                         # noqa: PLC0415

    mods = build_modules()
    keys = list(scope) if scope is not None else \
        (upstream_closure(module, mods) if module else list(mods))
    missing: list[dict] = []
    notes: list[str] = []
    for key in keys:
        spec = mods.get(key)
        if spec is None:
            missing.append({"module": key, "missing": ["（清单里没有这个模块）"]})
            continue
        miss = spec.missing_files()
        if miss:
            missing.append({"module": key, "missing": miss, "note": getattr(spec, "file_note", "")})
        elif getattr(spec, "file_note", ""):
            notes.append("%s：%s" % (key, spec.file_note))
    if module and ttl_path is not None:
        try:
            subset = {k: mods[k] for k in keys if k != module}
            subset[module] = (_replace(mods[module], files=(pathlib.Path(ttl_path),))
                              if module in mods else
                              ModuleSpec(key=module, prefix=module, label=module,
                                         short_label=module.upper(), ontology_iri="", namespace="",
                                         files=(pathlib.Path(ttl_path),), kind="extension"))
            parse_module_graphs(subset)
            notes.append("模块 %s：上传的 TTL 可解析" % module)
        except Exception as exc:  # noqa: BLE001
            missing.append({"module": module, "missing": [str(ttl_path)], "error": str(exc)[:300]})
    # **模块身份校验**（用户口径 2026-09-30）：短名必须来自 TTL 的 `bodhi:shortName`，
    # 且 ≤ SHORT_NAME_MAX、不含 `/`；不合格 → 拒绝导入（不落真源、不删任何数据）。
    from ontology_compiler.config import SHORT_NAME_MAX  # noqa: PLC0415
    for key in keys:
        spec = mods.get(key)
        short = (getattr(spec, "short_label", "") or "").strip() if spec else ""
        if not short:
            missing.append({"module": key, "missing": ["`bodhi:shortName`（TTL 未声明模块短名）"],
                            "hint": '在本体 IRI 上加一行：<%s> bodhi:shortName "%s" .'
                                    % (getattr(spec, "ontology_iri", "") or ("http://example.org/%s" % key), key)})
        elif len(short) > SHORT_NAME_MAX:
            missing.append({"module": key,
                            "missing": ["`bodhi:shortName` 超长：%r（%d 字符 > 上限 %d）"
                                        % (short, len(short), SHORT_NAME_MAX)],
                            "hint": "短名会被前端截断导致目录下的页查不到；请改短（建议用模块名）"})
        elif "/" in short:
            missing.append({"module": key, "missing": ["`bodhi:shortName` 不能含 /"]})
    hints: list[str] = []
    if missing:
        seen_dir = sorted(p.name for p in ONTOLOGY_DIR.glob("*.ttl")) if ONTOLOGY_DIR.is_dir() else []
        seen_src = sorted(p.name for p in SOURCES_DIR.glob("*.ttl")) if SOURCES_DIR.is_dir() else []
        seen_ext = (sorted(p.name for p in EXTENSIONS_DIR.glob("*.ttl"))
                    if EXTENSIONS_DIR.is_dir() else [])
        hints = ["本次编译范围 = %s（= 本模块 + 上游依赖闭包）" % ("、".join(keys) or "（空）"),
                 "`ontology/` 现有 TTL：%s" % ("、".join(seen_dir) or "（无）"),
                 "`ontology/sources/`（上传真源）现有 TTL：%s" % ("、".join(seen_src) or "（无）"),
                 "`ontology/extensions/`（随包扩展）现有 TTL：%s" % ("、".join(seen_ext) or "（无）"),
                 "修法：只需把**本次范围内缺的** TTL 放进上面任一路径（`<模块>.ttl` / `*完整版.ttl` / "
                 "`<模块>-ext.ttl` 都能被识别）",
                 "本次**没有删除任何 wiki 页/图谱节点**（预检失败即中止）"]
    return {"ok": not missing, "scope": keys, "missing": missing, "notes": notes, "hints": hints}


def _index_per_module() -> dict:
    """按模块的产物规模（`ontology_index.json` 的 models[]）—— 用于回执里"这次编了什么"。"""
    path = REPO / "artifacts" / "weknora" / "ontology_index.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}
    out: dict = {}
    for item in (data.get("models") or []):
        if not isinstance(item, dict) or not item.get("key"):
            continue
        out[str(item["key"])] = {k: item.get(k) for k in
                                 ("classes", "relations", "properties", "data_properties", "label")
                                 if k in item}
    return out


def _load_registry_entries() -> list[dict]:
    """读 `_registry.json` 的模块条目（缺失/坏文件 → 空表）。"""
    if not EXT_REGISTRY.is_file():
        return []
    try:
        return list(json.loads(EXT_REGISTRY.read_text(encoding="utf-8")).get("modules") or [])
    except Exception:  # noqa: BLE001
        return []


def _unregister_extension(key: str) -> dict:
    """从 `_registry.json` 摘掉一个模块（回滚用；原子写）。"""
    key = (key or "").strip().lower()
    entries = [e for e in _load_registry_entries() if str(e.get("key")) != key]
    EXT_REGISTRY.parent.mkdir(parents=True, exist_ok=True)
    tmp = EXT_REGISTRY.with_name("_registry.json.tmp")
    tmp.write_text(json.dumps({"modules": entries}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(EXT_REGISTRY)
    return {"removed": key, "remaining": len(entries)}


def _register_extension(meta: dict) -> dict:
    """把上传模块登记进 `ontology/extensions/_registry.json`（同名覆盖，临时文件+替换=原子写）。"""
    import datetime

    entries: list[dict] = []
    if EXT_REGISTRY.is_file():
        try:
            entries = json.loads(EXT_REGISTRY.read_text(encoding="utf-8")).get("modules") or []
        except Exception:  # noqa: BLE001
            entries = []
    entry = {**meta, "file": "ontology/sources/%s.ttl" % meta["key"],
             "registered_at": datetime.datetime.now().isoformat(timespec="seconds")}
    entries = [e for e in entries if str(e.get("key")) != meta["key"]] + [entry]
    EXT_REGISTRY.parent.mkdir(parents=True, exist_ok=True)
    tmp = EXT_REGISTRY.with_name("_registry.json.tmp")
    tmp.write_text(json.dumps({"modules": entries}, ensure_ascii=False, indent=2) + "\n",
                   encoding="utf-8")
    tmp.replace(EXT_REGISTRY)
    return entry


def repair_all(kb_id: str = "", compile_first: bool = True, project_wiki: bool = True) -> dict:
    """**运维修复**（幂等）：编译产物 → 灌 Neo4j 投影 → 重投影本体库 wiki → 一致性体检。

    用途：崩溃/手工改动/换机器后，让 **真源 TTL → artifacts → Neo4j → wiki** 四层重新一致。
    可以在任何时刻重复执行（编译与投影都是幂等 MERGE；wiki 投影先删自己的页再写）。
    """
    kb = ontology_kb_id(kb_id)
    report: dict = {"kb_id": kb}
    if compile_first:
        report["compile"] = compile_artifacts()
        report["totals"] = _index_totals()
    report["apply"] = apply_projection()
    if project_wiki:
        report["wiki"] = regen_wiki(kb)
    try:
        import ke_audit          # 延迟导入：ke_audit 反过来会 import 本模块的常量
        rep = ke_audit.audit(kb, scope="all", max_findings=40)
        report["audit"] = {"totals": rep.get("totals"), "summary": rep.get("summary"),
                           "session_pages": (rep.get("data") or {}).get("session_pages")}
    except Exception as exc:  # noqa: BLE001
        report["audit"] = {"error": str(exc)[:200]}
    return report


def ontology_kb_id(kb_id: str = "") -> str:
    """要操作的本体库：显式传优先，否则按特征认；认不出就**报错说清怎么配**（不猜）。"""
    if (kb_id or "").strip():
        return kb_id.strip()
    det = ke_ontology.resolve_ontology_kb()
    if det.get("id"):
        return det["id"]
    raise ValueError(
        "找不到「本体模型知识库」：请任选一种方式指定 ——\n"
        "  ① MCP 服务 env：BODHI_ONTOLOGY_KB_ID=<你们的本体库 uuid>\n"
        "  ② 给库打标记：UPDATE knowledge_bases SET wiki_config = COALESCE(wiki_config,'{}'::jsonb) "
        "|| '{\"bodhi_ontology_kb\": true}'::jsonb WHERE id='<本体库 uuid>';\n"
        "  ③ 把库名起成「企业本体模型」（或设 env BODHI_ONTOLOGY_KB_NAME=<你们的库名>）")



def _run(script: pathlib.Path, args: list[str]) -> dict:
    done = subprocess.run([sys.executable, str(script), *args], cwd=str(REPO),
                          capture_output=True, text=True, encoding="utf-8", check=False)
    tail = ((done.stdout or "") + (done.stderr or "")).strip().splitlines()[-6:]
    return {"ok": done.returncode == 0, "exit": done.returncode, "tail": tail}


def purge_model(model: str, kb_id: str = "") -> dict:
    """清理一个本体模型的「图库 + 本体 wiki 页」（只动本体模型知识库，不碰实例知识库）。

    Neo4j：DETACH DELETE 该模块的类/属性节点（连同**其它模块指向它的边**）+ 模块节点。
    PG   ：硬删该模型在本体模型知识库里的 `ontology:*` 页（含版本快照）+ 重算 in_links。
    """
    model = (model or "").strip()
    if not model:
        raise ValueError("purge 需要 model（模块 key，如 bmm / ea / ea-service）")
    kb = ontology_kb_id(kb_id)
    neo_deleted = 0
    for cypher in (
        "MATCH (n) WHERE n.bodhi_projection = 'ontology' AND n.module = $m DETACH DELETE n",
        "MATCH (m:BodhiModule {key: $m}) DETACH DELETE m",
    ):
        before = ke_neo4j.query("MATCH (n) WHERE n.bodhi_projection='ontology' AND n.module=$m "
                                "RETURN count(n) AS n", {"m": model})
        ke_neo4j.query(cypher, {"m": model})
        neo_deleted += int((before or [{"n": 0}])[0].get("n") or 0)

    where = ("knowledge_base_id = %s AND page_type LIKE 'ontology:%%' "
             "AND page_metadata->'ontology'->>'model' = %s"
             % (ke_db.sql_str(kb), ke_db.sql_str(model)))
    slugs = [r["slug"] for r in ke_db.psql_csv("SELECT slug FROM wiki_pages WHERE %s" % where)]
    if slugs:
        lst = ", ".join(ke_db.sql_str(s) for s in slugs)
        ke_db.psql("BEGIN;\n"
                   "DELETE FROM wiki_page_revisions WHERE knowledge_base_id = %s AND slug IN (%s);\n"
                   "DELETE FROM wiki_pages WHERE %s;\n"
                   "%s\nCOMMIT;\n"
                   % (ke_db.sql_str(kb), lst, where, ke_pages.rebuild_in_links_sql(kb)), stdin=True)
    return {"model": model, "kb_id": kb, "neo4j_nodes_deleted": neo_deleted,
            "wiki_pages_deleted": len(slugs), "pages": slugs[:10]}


def _namespace_of(iri: str) -> str:
    """IRI -> 命名空间（`#` 优先，其次最后一段 `/`）：与 ontology_compiler.config.namespace_of 同规则。"""
    if "#" in iri:
        return iri.split("#", 1)[0] + "#"
    if "/" in iri:
        return iri.rsplit("/", 1)[0] + "/"
    return iri


def module_namespace(model: str) -> str:
    """该模块的命名空间：取它在 Neo4j 里的任一节点 IRI 推出（不依赖 config.py，上传的模块也适用）。"""
    rows = ke_neo4j.query(
        "MATCH (n) WHERE n.bodhi_projection = 'ontology' AND n.module = $m AND n.iri IS NOT NULL "
        "RETURN n.iri AS iri LIMIT 1", {"m": (model or "").strip()})
    return _namespace_of(str(rows[0]["iri"])) if rows else ""


def dependent_modules(model: str) -> list[str]:
    """**下游模块**（依赖 model 的模块），按"最下游在前"排好；model 自身不在其中。

    判定口径（用户 2026-09-20：本体模型与知识模型松耦合，只做级联删除）：
      子类 `BODHI_SUBCLASS_OF` / `BODHI_DOMAIN` / `BODHI_RANGE` / `BODHI_INVERSE_OF`
      指向 model 命名空间的边 —— **以 Neo4j 的边为准**（不是只读 TTL），
      历史遗留的跨模块引用也查得到；再对下游做同样的传递展开（bmm 被删 ⇒ ea、ea-service、bmm-fd）。
    """
    model = (model or "").strip()
    if not model:
        return []
    seen = {model}
    order: list[str] = []          # BFS 顺序：先直接下游，再它们的下游
    queue = [model]
    while queue:
        cur = queue.pop(0)
        ns = module_namespace(cur)
        if not ns:
            continue
        rows = ke_neo4j.query(
            "MATCH (s)-[r:BODHI_SUBCLASS_OF|BODHI_DOMAIN|BODHI_RANGE|BODHI_INVERSE_OF]->(t) "
            "WHERE t.iri STARTS WITH $ns AND s.module IS NOT NULL AND s.module <> $m "
            "RETURN DISTINCT s.module AS m", {"ns": ns, "m": cur})
        for row in rows:
            kid = str(row.get("m") or "")
            if kid and kid not in seen:
                seen.add(kid)
                order.append(kid)
                queue.append(kid)
    return list(reversed(order))    # 倒序 = 最下游先删，最后才轮到 model（由调用方补上）


def cascade_purge(model: str, kb_id: str = "") -> dict:
    """级联清理一个模块：**先删下游、再删自身**（B 案：只删不重建，依赖者要重新上传才回来）。

    返回 `{model, deleted: [{model, neo4j_nodes_deleted, wiki_pages_deleted}], totals}`，
    `deleted` 的顺序即实际删除顺序（最下游在前）。想先看会删谁：用 `dependent_modules()`。
    """
    model = (model or "").strip()
    if not model:
        raise ValueError("cascade_purge 需要 model")
    victims = dependent_modules(model)
    out: dict = {"model": model, "deleted": [], "totals": {"neo4j_nodes_deleted": 0,
                                                          "wiki_pages_deleted": 0}}
    for m in [*victims, model]:
        r = purge_model(m, kb_id)
        out["deleted"].append({"model": m,
                               "neo4j_nodes_deleted": r["neo4j_nodes_deleted"],
                               "wiki_pages_deleted": r["wiki_pages_deleted"]})
        out["totals"]["neo4j_nodes_deleted"] += r["neo4j_nodes_deleted"]
        out["totals"]["wiki_pages_deleted"] += r["wiki_pages_deleted"]
    return out


# ---------------------------------------------------------------------------
# 上传即加载（用户 2026-09-20 口径）：同名模块整体替换，**零编译产物**
#   上传 TTL -> 级联删下游 -> 复用编译器 loader 解析 -> 复用 Neo4jEmitter 但输出到临时目录
#   -> 执行 cypher -> 删临时目录。artifacts/ 一个字节都不动，也不需要登记 config.py。
# ---------------------------------------------------------------------------
UPLOAD_DIR = REPO / "ontology" / "uploads"

_TTL_PREFIX_RE = re.compile(r"@prefix\s+(?P<p>[A-Za-z0-9_.\-]*):\s*<(?P<ns>[^>]+)>")
_TTL_ONTOLOGY_RE = re.compile(r"<(?P<iri>[^>]+)>\s+(?:a|rdf:type)\s+owl:Ontology")
# emitter 产出的语句里节点的 identity：`iri: 'http://…'`（第一个即语句主体，见 import_ttl 的归属判据）
_STMT_IRI_RE = re.compile(r"iri:\s*'([^']+)'")


def inspect_ttl(path: pathlib.Path) -> dict:
    """读 TTL 自己的「本体 IRI / 命名空间 / 前缀」声明 —— **只认文件内的 @prefix，不用默认前缀**。

    （历史教训：曾用文件内部 `@prefix :` 的默认前缀，把 `ea:Activity` 写成 `bmm-EA-ext:Activity`。）
    """
    text = pathlib.Path(path).read_text(encoding="utf-8")
    prefixes = {m.group("p"): m.group("ns") for m in _TTL_PREFIX_RE.finditer(text)}
    hit = _TTL_ONTOLOGY_RE.search(text)
    iri = hit.group("iri") if hit else ""
    if not iri:
        named = sorted({ns for p, ns in prefixes.items() if p}, key=len)
        if not named:
            raise ValueError("TTL 里既没有 owl:Ontology 也没有非默认前缀声明：%s" % path)
        iri = named[-1].rstrip("#/")
    ns = iri if iri.endswith(("#", "/")) else iri + "#"
    prefix = next((p for p, declared in prefixes.items() if p and declared == ns), "")
    # 默认前缀（`@prefix : <ns>`）没有名字，无法当 prefixed name 用。
    # 2026-09-26 事故：`ea-service`/`bmm-fd` 的 TTL 就是这种写法 → 这里解析出空 prefix
    # → 上传登记回退成模块 key → 编译产物类名从 `easvc:*` 漂成 `ea-service:*`。
    default_this_ns = any((not p) and declared == ns for p, declared in prefixes.items())
    # 模块 key：ontology IRI 末段（`…/ext/bmmfd` → `bmmfd`）
    key_guess = iri.rstrip("#/").rsplit("/", 1)[-1]
    # 类前缀推导（2026-09-26 用户口径）：TTL **不必**显式写 prefix
    #   ① 有与命名空间匹配的**命名**前缀 → 用它（`@prefix easvc: <…/ea-service#>`）；
    #   ② 否则若本 TTL 用**默认前缀**指向自己的命名空间 → **prefix := key（IRI 末段）**，
    #      即"约定：模块名就是类前缀"；
    #   ③ 都拿不到 → 空串（调用方回退，且必须告警）。
    derived_from_key = False
    if not prefix and default_this_ns:
        prefix = key_guess
        derived_from_key = True
    # 显示名 = **只用 TTL 的 `rdfs:label`**（2026-09-26 用户口径：不引入新词汇/新字段）：
    #   取「（」前的部分并去掉结尾"本体" —— 例："EA 服务契约扩展本体（业务属性 / …）" → "EA 服务契约扩展"
    label = ""
    m2 = re.search(r"rdfs:label\s+\"([^\"]+)\"", text)
    if m2:
        label = m2.group(1).strip()
    label = re.split(r"[（(]", label)[0].strip()
    if label.endswith("本体"):
        label = label[:-2].strip()
    return {"ontology_iri": iri, "namespace": ns, "prefix": prefix,
            "default_namespace": default_this_ns, "key": key_guess,
            "prefix_derived_from_key": derived_from_key,
            "prefix_derivable": bool(prefix),
            "label": label}


def _cypher_statements(path: pathlib.Path) -> list[str]:
    """把 emitter 产出的 cypher 文本拆成可执行语句（去掉 `//` 注释行）。"""
    text = path.read_text(encoding="utf-8")
    text = "\n".join(line for line in text.splitlines() if not line.strip().startswith("//"))
    return [s.strip() for s in text.split(";") if s.strip()]


def _module_import_stats(module: str, namespace: str, ontology_iri: str) -> dict:
    """前端「结果报告」要的统计（老 `import_ttl` 的回执字段，2026-09-30 重写时漏掉过一次）：
    本模块类/属性数、投影语句数、外部占位、交叉引用。数据源 = **编译产物**
    `artifacts/neo4j/10_ontology.cypher` + Neo4j（只读）。

    语义（新上传链路第⑥步 `apply_projection()` 是**全量回放**）：
    `cypher_statements` = 产物中**属于本模块**的语句数；`statements_skipped` = 0
    （新链路不跳过任何模块的语句，全量幂等重放）；实际执行总数见回执 `apply.statements`。
    """
    ns = namespace or ontology_iri or ""
    stmts = 0
    placeholders: list[str] = []
    own_key_re = re.compile(r"key:\s*'%s'" % re.escape(module))
    path = PROJECTION_DIR / "10_ontology.cypher"
    if path.is_file():
        for stmt in _cypher_statements(path):
            stubs = (re.findall(r"iri:\s*'([^']+)'", stmt)
                     + re.findall(r'iri:\s*"([^"]+)"', stmt))
            low = stmt.lower()
            if stubs and "external" in low and "true" in low and \
                    all(not i.startswith(ns) for i in stubs):
                placeholders += stubs
                continue
            iris = _STMT_IRI_RE.findall(stmt)
            own = ((iris[0].startswith(ns) or iris[0] == ontology_iri) if iris
                   else bool(own_key_re.search(stmt)))
            if own:
                stmts += 1
    refs: list[dict] = []
    try:
        for r in ke_neo4j.query(
                "MATCH (s)-[rel:BODHI_SUBCLASS_OF|BODHI_DOMAIN|BODHI_RANGE|BODHI_INVERSE_OF]->(t) "
                "WHERE s.module = $m AND t.module IS NOT NULL AND t.module <> $m "
                "RETURN DISTINCT t.module AS module, t.iri AS iri, coalesce(t.local_name,'') AS name, "
                "       type(rel) AS kind ORDER BY module, iri", {"m": module}):
            row = ke_neo4j.query("MATCH (c:BodhiOntClass {iri: $iri}) "
                                 "RETURN coalesce(c.external, false) AS ext", {"iri": r["iri"]})
            r["defined"] = bool(row) and not row[0]["ext"]
            refs.append(dict(r))
        classes = int(ke_neo4j.query(
            "MATCH (c:BodhiOntClass) WHERE c.external IS NULL RETURN count(c) AS n")[0]["n"] or 0)
        module_classes = int(ke_neo4j.query(
            "MATCH (c:BodhiOntClass) WHERE c.module = $m AND c.external IS NULL "
            "RETURN count(c) AS n", {"m": module})[0]["n"] or 0)
        module_properties = int(ke_neo4j.query(
            "MATCH (p:BodhiOntProperty) WHERE p.module = $m RETURN count(p) AS n",
            {"m": module})[0]["n"] or 0)
    except Exception as exc:  # noqa: BLE001
        return {"stats_error": str(exc)[:200]}
    dangling = [r["iri"] for r in refs if not r["defined"]]
    return {"classes": classes, "module_classes": module_classes,
            "module_properties": module_properties,
            "cypher_statements": stmts, "statements_skipped": 0,
            "statements_executed": stmts,
            "placeholders_skipped": sorted(set(placeholders)),
            "cross_refs": refs, "dangling_refs": dangling,
            "hint": ("本模块交叉引用了 %d 个外部类（来自 %s）；未定义的引用：%s"
                     % (len(refs), "、".join(sorted({r["module"] for r in refs})) or "无",
                        "、".join(dangling) if dangling else "无"))}


def import_ttl(ttl_path, module: str = "", project_wiki: bool = False, kb_id: str = "",
               allow_dangling: bool = False) -> dict:
    """上传/替换一个本体模块：**级联删下游 → 解析 → 灌 Neo4j**（零产物）。

    - 解析复用编译器的 `load_ontology`（唯一解析器），发射复用 `Neo4jEmitter`，
      但输出落在 `tempfile.mkdtemp()` 里、执行完即删 —— 不写 artifacts/、不改 config.py。
    - 删除遵循 B 案：只删下游不重建（依赖者需重新上传）；想先看会删谁用 `dependent_modules()`。
    - `project_wiki=False`（默认）：wiki 生成目前仍读编译产物 json，等 `ontology_wiki.py`
      改成从 Neo4j 读类/关系后再默认打开（见 docs/session-handoff.md §3.4bis 待做 2）。
    """
    import shutil
    import tempfile

    ttl = pathlib.Path(ttl_path).resolve()
    if not ttl.is_file():
        raise FileNotFoundError("找不到 TTL：%s" % ttl)
    module = (module or ttl.stem).strip().lower().replace(" ", "-")
    meta = inspect_ttl(ttl)
    comp_dir = str(REPO / "tools" / "ontology-compiler")
    if comp_dir not in sys.path:
        sys.path.insert(0, comp_dir)
    from ontology_compiler.config import ModuleSpec          # noqa: PLC0415
    from ontology_compiler.emitters.neo4j import Neo4jEmitter  # noqa: PLC0415
    from ontology_compiler.loader import load_ontology        # noqa: PLC0415

    # —— 模块身份四件套（2026-09-26 用户口径：TTL 是唯一真源、前端只读）——
    #   key（模块标识，= TTL 的 ontology IRI 末段）
    #   prefix（类前缀）：已登记 > TTL 命名前缀 > **IRI 末段（= 模块名；TTL 用默认前缀时的约定）** > key
    #   label（显示名） ：已登记 > TTL(`bodhi:label`/`rdfs:label`，取「（」前) > 模块名
    #   short_label     ：已登记 > TTL(`bodhi:shortLabel`) > **模块名大写**
    # 历史教训：曾因 TTL 用"默认前缀"解析不到 prefix → 回退成模块 key，导致类名前缀
    # `easvc:*` 漂成 `ea-service:*`、技能/巡检/存量页全线失配（2026-09-24 事故）。
    known = _registered_modules().get(module)
    known_prefix = getattr(known, "prefix", "") if known else ""
    known_label = getattr(known, "label", "") if known else ""
    known_short = getattr(known, "short_label", "") if known else ""
    ttl_label = str(meta.get("label") or "")
    # 2026-09-26 用户口径修正：**TTL 是唯一真源**（"要改直接改 TTL"）。
    # 因此 TTL 能推出来的值**优先**，已登记值只作兜底 —— 否则改 TTL 的 label 会被
    # 注册表/源码里的旧值挡住（实测：把 rdfs:label 改成"…测试模型"后目录名不变）。
    # short_label 不引入 TTL 新字段：固定"模块名大写"（已登记值兼容保留）。
    prefix = meta["prefix"] or known_prefix or module
    label = ttl_label or known_label or module
    short_label = known_short or module.upper()
    spec = ModuleSpec(key=module, prefix=prefix, label=label, short_label=short_label,
                      ontology_iri=meta["ontology_iri"], namespace=meta["namespace"],
                      files=(ttl,), kind="extension", affects=())

    if meta.get("prefix_derived_from_key"):
        prefix_source = "TTL 用默认前缀 → 按约定取模块名当类前缀（%s）" % prefix
    elif meta["prefix"]:
        prefix_source = "取自 TTL 命名前缀 `@prefix %s:`" % meta["prefix"]
    elif known_prefix:
        prefix_source = "TTL 推不出前缀 → 沿用已登记模块 %s 的 prefix（%s）" % (module, known_prefix)
    else:
        prefix_source = "回退为模块 key（TTL 无命名前缀、也无指向本命名空间的默认前缀）"
    label_source = ("取自 TTL 的 bodhi:label/rdfs:label（%s）" % label if ttl_label
                    else ("沿用已登记模块 label（%s）" % label if known_label
                          else "TTL 未声明显示名 → 用模块名"))

    out: dict = {"module": module, "ttl": str(ttl),
                 "ontology_iri": meta["ontology_iri"], "namespace": meta["namespace"],
                 "prefix": prefix, "label": label, "short_label": short_label,
                 "prefix_source": prefix_source, "label_source": label_source,
                 "identity": {"key": module, "prefix": prefix, "label": label,
                              "short_label": short_label,
                              "key_from_ttl": meta.get("key"),
                              "label_from_ttl": ttl_label or None},
                 "default_namespace": bool(meta.get("default_namespace"))}
    if not known_prefix and not meta.get("prefix_derivable"):
        out["warning_prefix"] = (
            "TTL 只有默认前缀（`@prefix : <%s>`），登记时会回退 prefix=%s —— 这会让类名前缀"
            "与既有技能/巡检/存量页失配。建议在 TTL 顶部加 `@prefix %s: <%s> .`，"
            "或上传时显式指定 prefix/label。" % (meta["namespace"], prefix, prefix, meta["namespace"]))
    if module in _registered_module_keys():
        out["warning"] = "模块 %s 也已在 config.py 注册；本次导入只更新图库，正式产物仍由编译产出" % module
    out["purge"] = cascade_purge(module, kb_id)

    tmp = pathlib.Path(tempfile.mkdtemp(prefix="bodhi-import-"))
    try:
        # 关键：喂**全部已登记模块 + 同名覆盖**。emitter 的投影是"全量重建"语义
        # （含清理/约束），只喂上传的单个模块会把它模块的节点也算漏（实测 classes 47→46）。
        mods = _registered_modules()
        mods[spec.key] = spec              # 同名覆盖 = 整体替换该模块
        bundle = load_ontology(mods)
        written = Neo4jEmitter().emit(bundle, tmp)
        stmts = 0
        # 只执行投影文件 00/10（20_cross_layer_queries.cypher 是跨层查询模板，不是执行件），
        # 且**必须在临时目录里找**——emitter 返回的是"相对仓库根"的路径，直接按它读会读到 artifacts/ 的旧文件。
        # 只执行**与上传模块相关**的语句。为什么不能全量执行：全量投影会把"被未登记模块引用的类"
        # 判成外部占位（external），而 MERGE 对已存在节点是 ON CREATE SET → 标记**粘住**，
        # 之后连 apply 从 artifacts 回放也改不回来。实测：导入 probe 后 bmm:Offering / ea:Activity
        # 被翻成 external，类数 47→45（bmm 26→25、ea 11→10）。
        # 只执行**与上传模块相关**的语句。判据（2026-09-20 修正，实测踩过两次）：
        #   语句的**首个 `iri:`** 就是它所属的节点（emitter 一贯写法：先 MERGE/MATCH 主体，再连边）
        #   → 属于本模块 ⇔ 首 iri 在本模块命名空间内，或就是本模块的 ontology IRI；
        #   没有 `iri:` 的诊断语句 → 退化为 `key: '<本模块 key>'` 精确匹配。
        # 为什么不能用裸 key 子串（第一版）：'ea' 命中 332 条（含 ea-service / ea-ownership 的全部语句
        #   以及所有带 "ea" 的英文串）、'bmm' 命中 320 条（含 bmm-fd 的 33 条）—— 级联删掉的子模块被插回。
        # 为什么也不能用「命名空间子串 + key 定界」（第二版）：依赖模块的**模块节点语句**里写着
        #   `key: 'bmm-fd'` / `affects: 'bmm'`，仍会把子模块的模块节点插回来（wiki 里于是多出一个
        #   只有模块页、没有类与关系的空模块）。
        own_ns = meta["namespace"]
        own_iri = meta["ontology_iri"]
        own_key_re = re.compile(r"key:\s*'%s'" % re.escape(spec.key))

        def _own_statement(stmt: str) -> bool:
            iris = _STMT_IRI_RE.findall(stmt)
            if iris:
                return iris[0].startswith(own_ns) or iris[0] == own_iri
            return bool(own_key_re.search(stmt))
        placeholders: list[str] = []
        for wanted in ("00_constraints.cypher", "10_ontology.cypher"):
            found = sorted(tmp.rglob(wanted))
            if not found:
                out.setdefault("missing", []).append(wanted)
                continue
            skipped = 0
            for stmt in _cypher_statements(found[0]):
                if wanted.startswith("10"):
                    stubs = (re.findall(r"iri:\s*'([^']+)'", stmt)
                             + re.findall(r'iri:\s*"([^"]+)"', stmt))
                    low = stmt.lower()
                    # 结构判断①：外部占位语句（给"被引用但未定义"的类建 external 占位）。
                    # 导入路径**不造孤立节点** —— 未落地的引用只由 missing_defs / cross_refs 提示
                    # （见上面的依赖前置检查）。用户口径 2026-09-20：顺序我们保证，占位不必造。
                    if stubs and "external" in low and "true" in low and \
                            all(not i.startswith(meta["namespace"]) for i in stubs):
                        placeholders += stubs
                        skipped += 1
                        continue
                    # 结构判断②：非本模块的语句（其它模块的节点已在库里，不必重放，也就不可能被改动）
                    if not _own_statement(stmt):
                        skipped += 1
                        continue
                ke_neo4j.query(stmt)
                stmts += 1
            if wanted.startswith("10"):
                out["statements_skipped"] = skipped
        out["placeholders_skipped"] = sorted(set(placeholders))
        out["cypher_statements"] = stmts
        out["projection_files"] = [p.name for p in sorted(tmp.rglob("*.cypher"))]
        out["classes"] = int(ke_neo4j.query(
            "MATCH (c:BodhiOntClass) WHERE c.external IS NULL RETURN count(c) AS n")[0]["n"] or 0)
        out["module_classes"] = int(ke_neo4j.query(
            "MATCH (c:BodhiOntClass) WHERE c.module = $m AND c.external IS NULL "
            "RETURN count(c) AS n", {"m": module})[0]["n"] or 0)
        out["module_properties"] = int(ke_neo4j.query(
            "MATCH (p:BodhiOntProperty) WHERE p.module = $m RETURN count(p) AS n",
            {"m": module})[0]["n"] or 0)

        # 交叉引用报告（用户 2026-09-20：上传的 TTL 会引用其它模块的类，"加载后提示一下"）。
        # 列出本模块的边指向了哪些**别的模块**的类；目标只是外部占位（未定义）则给断链警告，
        # 提示用户"该引用指不到已导入的类 —— 可能拼错，或对应模块还没导入"。
        refs: list[dict] = []
        for r in ke_neo4j.query(
                "MATCH (s)-[rel:BODHI_SUBCLASS_OF|BODHI_DOMAIN|BODHI_RANGE|BODHI_INVERSE_OF]->(t) "
                "WHERE s.module = $m AND t.module IS NOT NULL AND t.module <> $m "
                "RETURN DISTINCT t.module AS module, t.iri AS iri, coalesce(t.local_name,'') AS name, "
                "       type(rel) AS kind ORDER BY module, iri", {"m": module}):
            row = ke_neo4j.query("MATCH (c:BodhiOntClass {iri: $iri}) "
                                 "RETURN coalesce(c.external, false) AS ext", {"iri": r["iri"]})
            r["defined"] = bool(row) and not row[0]["ext"]
            refs.append(dict(r))
        dangling = [r["iri"] for r in refs if not r["defined"]]
        out["cross_refs"] = refs
        out["dangling_refs"] = dangling
        out["hint"] = ("本模块交叉引用了 %d 个外部类（来自 %s）；未定义的引用：%s"
                       % (len(refs), "、".join(sorted({r["module"] for r in refs})) or "无",
                          "、".join(dangling) if dangling else "无"))

        # 依赖前置检查（用户 2026-09-20 第二版）：从**上传的 TTL 文本**判定"引用了但未定义"的类。
        # 第一版扫"边"不可靠 —— emitter 对不存在的父类**不建边**，悬空引用在库里是隐形的（实测 bad.ttl 漏过）。
        # 口径：只看 rdfs:subClassOf / domain / range 位置上的 prefixed 名，排除标准词汇与自身命名空间，
        # 逐个查 Neo4j；不是"已定义类"（不存在或只是 external 占位）就算未就绪 → 回滚并提示先导入谁。
        std = {"xsd", "owl", "rdf", "rdfs", "skos", "dc", "dcterms", "sh", "bodhi"}
        text = ttl.read_text(encoding="utf-8")
        prefixes = {m.group("p"): m.group("ns") for m in _TTL_PREFIX_RE.finditer(text)}
        pending: list[str] = []
        for hit in re.finditer(
                r"rdfs:(?:subClassOf|domain|range)\s+(?P<tok>[A-Za-z0-9_.\-]+:[A-Za-z0-9_.\-]+)", text):
            tok = hit.group("tok")
            pf, _, local = tok.partition(":")
            ns = prefixes.get(pf, "")
            if not ns or pf in std or ns == meta["namespace"]:
                continue
            row = ke_neo4j.query("MATCH (c:BodhiOntClass {iri: $iri}) "
                                 "RETURN coalesce(c.external, false) AS ext", {"iri": ns + local})
            if not row or row[0]["ext"]:
                pending.append(tok)
        pending = sorted(set(pending))
        out["missing_defs"] = pending
        if pending and not allow_dangling:
            rolled = cascade_purge(module, kb_id)
            raise ValueError(
                "依赖未就绪：引用了未定义的类 %s —— 已自动回滚本次导入（删除 %s 个节点）。"
                "请先导入定义这些类的模块（或修正拼写）" % ("、".join(pending),
                                                        rolled["totals"]["neo4j_nodes_deleted"]))
        # （projection_files 已在上面按临时目录产出，此处不再用 emitter 的"仓库相对路径"覆盖）
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    if project_wiki:
        out["wiki"] = regen_wiki(kb_id)
    return out


def _registered_modules() -> dict:
    """config.py 里已登记的模块表（失败返回空表，不影响导入）。"""
    try:
        comp_dir = str(REPO / "tools" / "ontology-compiler")
        if comp_dir not in sys.path:
            sys.path.insert(0, comp_dir)
        from ontology_compiler.config import build_modules  # noqa: PLC0415
        return dict(build_modules())
    except Exception:  # noqa: BLE001
        return {}


def check_prefix_drift() -> dict:
    """**模块前缀漂移**检查（2026-09-26 事故后的护栏）。

    为什么需要：模块身份是三件套 `key`（模块名）/ `prefix`（类前缀）/ `label`（显示名）。
    `prefix` 一变，编译产物里的类名就变（实测 `easvc:*` → `ea-service:*`），于是
    **技能文档、巡检代码、存量页面**三处同时失配（报"未知本体模型：easvc"、
    详细设计的类型/关系校验全线错位、本体库还多出一个英文目录）。

    检查三件事：
      ① 产物 `models[].prefix` 与真源期望（注册表 → config.py 内置）是否一致；
      ② 扩展模块的注册 prefix 是否**等于模块 key**（那是"回退"的典型特征）；
      ③ TTL 是否**只声明默认前缀**（`@prefix : <ns>`）——那会让登记时回退，是本事故根因。

    用法：`python3 tools/ke-core/ke_admin.py check-prefix`；`upload_ttl` 回执里也带
    `prefix_check`（上传后立刻就能看到有没有漂移）。
    """
    rule = ("模块身份三件套必须稳定：key（模块名）/ prefix（类前缀）/ label（显示名）。"
            "prefix 一变，产物类名就变（easvc:* → ea-service:*），技能/巡检/存量页会全线失配。")
    try:
        index = json.loads((REPO / "artifacts" / "weknora" / "ontology_index.json")
                           .read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "drift": [], "hints": ["读不到编译产物：%s" % exc], "rule": rule}

    registry: dict = {}
    if EXT_REGISTRY.is_file():
        try:
            registry = {str(m.get("key")): m for m in
                        (json.loads(EXT_REGISTRY.read_text(encoding="utf-8")).get("modules") or [])
                        if isinstance(m, dict) and m.get("key")}
        except Exception:  # noqa: BLE001
            registry = {}

    built = _registered_modules()
    drift: list[dict] = []
    hints: list[str] = []
    for model in (index.get("models") or []):
        key = str(model.get("key") or "")
        prefix = str(model.get("prefix") or "")
        expected = ""
        if key in registry:
            item = registry[key]
            expected = str(item.get("prefix") or "")
            ttl = REPO / str(item.get("file") or "")
            if ttl.is_file():
                try:
                    parsed = inspect_ttl(ttl)
                except Exception:  # noqa: BLE001
                    parsed = {}
                # 2026-09-26 用户口径：TTL 用默认前缀时，prefix 按约定 = 模块名（IRI 末段）
                # → 这是**确定性推导**，不再算"回退"。只有当 TTL 里既没有命名前缀、默认前缀
                # 也没指向本命名空间（prefix 推不出来）时才算隐患。
                if parsed and not parsed.get("prefix_derivable"):
                    hints.append(
                        "模块 %s 的 TTL 里没有指向本模块命名空间的前缀声明（`@prefix : <%s>` "
                        "未指向它，也没有 `@prefix %s:`）→ 类前缀推导不出来；请补前缀声明"
                        % (key, parsed.get("namespace", "…"), str(item.get("prefix") or key)))
                if parsed.get("key") and parsed["key"] != key:
                    hints.append("模块 %s 的 TTL 里 ontology IRI 末段是 `%s`（与注册 key 不同）——"
                                 "若改 key 请同时改 IRI，否则页 slug/类型名会对不上"
                                 % (key, parsed["key"]))
        if key in built and not expected:
            expected = str(getattr(built[key], "prefix", "") or "")
        if expected and prefix and prefix != expected:
            drift.append({"module": key, "artifact_prefix": prefix, "expected": expected,
                          "source": "registry" if key in registry else "config.py"})
    return {"ok": not drift, "drift": drift, "hints": hints, "rule": rule}


def _registered_module_keys() -> list[str]:
    """已登记模块的 key 列表（`_registered_modules()` 的轻量包装）。"""
    return list(_registered_modules().keys())


def upload_ttl(filename: str, content: str = "", module: str = "", project_wiki: bool = True,
               kb_id: str = "", write_source: bool = True, compile_after: bool = True,
               apply_after: bool = False) -> dict:
    """前端「上传本体文件」的完整链路 —— **2026-09-30 改为安全顺序**：

    ```
    ① 留痕：TTL 落 ontology/uploads/<时间戳>-<名>.ttl
    ② **预检（只读）**：编译所需模块文件是否齐 + 该 TTL 能否解析 → 缺就**直接中止**
       （旧实现把"级联删下游"放在编译之前，编译一失败就是"wiki 和图谱都删了才报错"）
    ③ 真源：**内置模块（bmm/ea/…）就地覆盖清单里的文件**（如 `ontology/BMM完整版.ttl`，不新增登记）；
       新模块 → `ontology/extensions/<key>-ext.ttl` + 登记 `_registry.json`（登记表只放元数据）
    ④ 编译 artifacts（不碰 wiki / 图谱）
    ⑤ **成功后才** 灌图库：`import_ttl()`（级联删本模块+下游模块的图库/wiki → 全量重灌 Neo4j）
    ⑥ 可选：`apply_after` 全量回放投影、`project_wiki` 重投影本体库 wiki
    ```

    任一步失败：**不删任何 wiki/图谱**，真源与登记**回滚**，回执给出可自助修的 hints。

    > 回执里的 `compiled.totals` 是**全量总数**（所有模块合计）—— 两次上传若模块集合没变，数字本来就相同，
    > 别把它当成"这次没生效"；要判断本次影响请看 `compiled.per_module` / `compiled.modules_added`。

    为什么要 `apply_after` 默认关：⑤已经把该模块写进图库，而**全量回放**要逐条执行整份投影 cypher
    （实测几十秒到几分钟）—— 上传路径不需要它；整库一致性交给运维 `ke_admin.py repair`（幂等）。

    两个开关都默认打开；关掉 `write_source` 就回到旧行为（只更新图库、不动产物）。
    """
    import time

    # **强制重投影**（2026-09-30 事故）：上传走的第④步是"级联删除本模块+下游"，
    # 若此处 project_wiki=False，页被删掉却不再重建 → 前端目录在、点开全空（用户实测）。
    # 因此无论调用方传什么，上传路径一律重投影。
    project_wiki = True

    content = content or ""
    if not content.strip():
        raise ValueError("上传内容为空")
    if len(content) > 2_000_000:
        raise ValueError("TTL 过大（%d 字节 > 2MB），请拆分模块" % len(content))
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    safe = pathlib.Path(filename or "upload.ttl").name
    target = UPLOAD_DIR / ("%s-%s" % (time.strftime("%Y%m%d-%H%M%S"), safe))
    target.write_text(content, encoding="utf-8")
    out: dict = {"upload_saved": str(target.relative_to(REPO))}

    # ② 预检（只读）：此时磁盘上除了"留痕"那份，什么都没改
    try:
        meta = inspect_ttl(target)
    except Exception as exc:  # noqa: BLE001
        return {**out, "ok": False, "error": "ttl_unparsable",
                "note": "TTL 无法解析（**没有改动真源、没有删除任何数据**）：%s" % str(exc)[:300]}
    module_key = (module or str(meta.get("key") or "") or "").strip().lower()
    if not module_key:
        module_key = re.sub(r"[^a-z0-9-]+", "-", pathlib.Path(safe).stem.lower()).strip("-") or "upload"
    pre = preflight_compile(target, module_key)
    out["preflight"] = pre
    out["module_key"] = module_key
    if not pre["ok"]:
        return {**out, "ok": False, "error": "preflight_failed",
                "note": ("预检未通过：**没有删除任何 wiki 页/图谱节点，也没有改真源**。"
                         "按 hints 补齐文件后重试即可。"),
                "missing": pre["missing"], "hints": pre["hints"]}

    if not write_source:
        out["import"] = import_ttl(target, module=module, project_wiki=project_wiki, kb_id=kb_id)
        out["note"] = ("只更新了图库（write_source=false）：类型校验 / 前端类型下拉 / 投影回放仍以"
                       "**编译产物**为准，新类会被判「本体里没有这个类」；要完整生效请打开该开关"
                       "（或把 TTL 放进 ontology/extensions/ 后跑 `ke_admin.py repair`）")
        return out

    # ③ 落真源：内置模块**就地覆盖**清单文件；新模块落 `extensions/` 并登记
    known = _registered_modules().get(module_key)
    ontology_iri = str(meta.get("ontology_iri") or "")
    prefix = str(meta.get("prefix") or getattr(known, "prefix", "") or module_key)
    label = str(meta.get("label") or getattr(known, "label", "") or module_key)
    # 短名口径（2026-09-30）：**只认 TTL 的 `bodhi:shortName`**（≤16 字符，预检已校验）；
    # 老代码用 `known.short_label or module_key.upper()` 会绕开 TTL —— 已移除。
    try:
        from ontology_compiler.config import _spec_from_ttl as _sft  # noqa: PLC0415
        short_label = (getattr(_sft(target, module_key, None), "short_label", "") or "").strip()
    except Exception:  # noqa: BLE001
        short_label = ""
    source_text, injected = _ensure_expert_role(content, module_key, ontology_iri)
    src_path = SOURCES_DIR / ("%s.ttl" % module_key)
    src_path.parent.mkdir(parents=True, exist_ok=True)
    mode = "上传真源：ontology/sources/<模块>.ttl（**文件即模块**，无注册表）"
    is_builtin = False                 # 上传**一律**落 sources/（内置清单文件保持出厂的随包版本）
    backup = src_path.read_text(encoding="utf-8") if src_path.is_file() else None
    created_file = backup is None
    entry = None
    old_registry_entry = None
    try:
        src_path.write_text(source_text, encoding="utf-8")
        # **不再登记**：文件即模块（用户口径 2026-09-30）——编译器直接扫 `sources/*.ttl`，
        # 模块身份从 TTL 自身解析；所以这里只写文件，没有"注册信息"这一步。
        out["source"] = {"file": str(src_path.relative_to(REPO)), "mode": mode, "registered": entry,
                         "injected": injected,
                         "note": ("为了让编译通过，真源副本里自动补了：%s" % "、".join(injected))
                                 if injected else "原样写入（TTL 已含全部必需声明）"}
        out["prefix_check"] = check_prefix_drift()

        if not compile_after:
            out["note"] = ("已落真源%s；本次未编译（compile_after=false）—— 需要时跑 `ke_admin.py repair`"
                           "（编译+灌投影+重投影+体检）" % ("（就地覆盖）" if is_builtin else "并登记"))
            return out

        # ④ **级联删除**（本模块 + 下游依赖）：图库/wiki + 真源文件 + 注册信息
        from ontology_compiler.config import downstream_closure, upstream_closure  # noqa: PLC0415
        scope = upstream_closure(module_key)                    # 编译范围 = 本模块 + 上游
        victims = [k for k in downstream_closure(module_key) if k != module_key]
        out["compile_scope"] = scope
        out["cascade_purge"] = cascade_delete_modules(victims, keep_files=(src_path.name,))

        # ⑤ 编译（只编本次范围；不碰 wiki / 图谱）
        before, keys_before = _index_totals(), _index_module_keys()
        out["compile"] = compile_artifacts(modules=scope)
        after, keys_after = _index_totals(), _index_module_keys()
        out["compiled"] = {
            "artifact": "artifacts/weknora/ontology_index.json",
            "totals_before": before, "totals_after": after,
            "delta": {k: after.get(k, 0) - before.get(k, 0) for k in sorted(set(before) | set(after))},
            "per_module": _index_per_module(),
            "modules": keys_after,
            "modules_added": sorted(set(keys_after) - set(keys_before)),
            "registered_extensions": sorted(
                str(e.get("key")) for e in _load_registry_entries() if isinstance(e, dict) and e.get("key")),
            "note": ("`totals_*` 是**全量总数**（所有模块合计）—— 与上次相同说明模块集合没变，"
                     "不代表本次没生效；本次影响看 `per_module` 与 `modules_added`"),
        }

        # ⑥ 编译成功**之后**才灌图库：执行**编译产物的投影**（`artifacts/neo4j/*.cypher`，幂等 MERGE）
        #    为什么不用 import_ttl：它按"全部已登记模块"生成投影，会把刚级联删除的模块又灌回去
        #    （产物里已经没有它们）→ 用产物投影才能保证"只编 bmm ⇒ 图里也只有 bmm"。
        out["apply"] = apply_projection()
    except Exception as exc:  # noqa: BLE001
        # 回滚真源与登记；**不删任何 wiki/图谱**
        try:
            if created_file:
                src_path.unlink(missing_ok=True)
            elif backup is not None:
                src_path.write_text(backup, encoding="utf-8")
            if entry is not None:
                _unregister_extension(module_key)
            if old_registry_entry is not None:
                _register_extension(old_registry_entry)
        except Exception:  # noqa: BLE001
            pass
        out["rollback"] = {"restored_source": str(src_path.relative_to(REPO)),
                           "removed_registry_entry": bool(entry),
                           "deleted_any_data": False}
        out["error"] = "upload_failed"
        out["note"] = ("失败已回滚（真源/登记恢复原状；**没有删除任何 wiki 页或图谱节点**）：%s"
                       % str(exc)[:300])
        return out

    if apply_after and "apply" not in out:
        out["apply"] = apply_projection()
    if project_wiki:
        out["wiki"] = regen_wiki(kb_id)
    out["note"] = ("已把该 TTL 纳入真源（%s）并**自动编译**：类型校验、前端类型下拉与"
                   "本体库 wiki 现在都以新产物为准（详见回执 compiled.per_module）"
                   % out["source"]["file"])
    # —— 前端「结果报告」契约字段（2026-09-30 用户复现：重写本函数时漏掉 → 那几格全空白）——
    out["module"] = module_key
    out["prefix"] = prefix
    out["ontology_iri"] = ontology_iri
    out["namespace"] = meta.get("namespace", "")
    out["label"] = label
    out["short_label"] = short_label
    out["prefix_source"] = ("TTL 用默认前缀 → 按约定取模块名当类前缀（%s）" % prefix
                            if meta.get("prefix_derived_from_key")
                            else ("取自 TTL 命名前缀 `@prefix %s:`" % prefix if meta.get("prefix")
                                  else "TTL 推不出前缀 → 回退模块名（%s）" % prefix))
    out["label_source"] = ("取自 TTL 的 rdfs:label（%s）" % label if meta.get("label")
                           else "TTL 未声明显示名 → 用模块名（%s）" % label)
    out["identity"] = {"key": module_key, "prefix": prefix, "label": label,
                       "short_label": short_label, "key_from_ttl": meta.get("key"),
                       "label_from_ttl": meta.get("label") or None,
                       "short_from_ttl": short_label or None}
    out.update(_module_import_stats(module_key, str(meta.get("namespace") or ""), ontology_iri))
    return out


def apply_projection() -> dict:
    """把 `artifacts/neo4j/{00_constraints,10_ontology}.cypher` 灌进 Neo4j（幂等 MERGE）。"""
    total = 0
    for name in ("00_constraints.cypher", "10_ontology.cypher"):
        path = PROJECTION_DIR / name
        if not path.is_file():
            raise FileNotFoundError("缺投影产物：%s（先跑 compile_artifacts）" % path)
        text = path.read_text(encoding="utf-8")
        stmts = [s.strip() for s in "\n".join(
            line for line in text.splitlines() if not line.strip().startswith("//")).split(";")
            if s.strip()]
        for stmt in stmts:
            ke_neo4j.query(stmt)
        total += len(stmts)
    classes = ke_neo4j.query("MATCH (c:BodhiOntClass) WHERE c.external IS NULL "
                             "RETURN count(c) AS n")[0]["n"]
    return {"statements": total, "classes": int(classes or 0)}


def _invalidate_ontology_cache() -> dict:
    """让 ke_ontology 的编译产物缓存失效（下次调用惰性重读 ontology_index.json）。

    为什么必须做：编译/灌库/重投影之后磁盘产物已是新内容，但**进程里**可能还留着旧的
    类清单 —— 表现是维护后新模块的类在 `/bodhi/ontology/relation-types` 里回
    `source: "unknown-class"`、关系类型为空，直到重启服务（2026-09-20 新增 ea-test 实测踩到）。
    `ke_ontology.index_data()` 另有 mtime+size 指纹兜底（跨进程编译也生效），这里做的是
    同进程内的**立即失效**，并把重读后的模块清单回带进结果，便于在 load 返回值里核对。
    """
    import importlib
    import sys as _sys

    core = str(REPO / "tools" / "ke-core")
    if core not in _sys.path:
        _sys.path.insert(0, core)
    try:
        ko = importlib.import_module("ke_ontology")
        ko.invalidate_cache()
        return {"invalidated": True,
                "modules": [m.get("key") for m in (ko.index_data().get("models") or [])]}
    except Exception as exc:  # noqa: BLE001
        return {"invalidated": False, "error": str(exc)}


def cascade_delete_modules(keys: list, keep_files: tuple = ()) -> dict:
    """**级联删除**（用户口径 2026-09-30）：删 `keys`（= 本模块 + 其下游依赖）的

    - **Neo4j 本体投影节点**（该模块的类/属性/关系 + 模块节点）与**本体库 wiki 页**；
    - **真源文件** `ontology/sources/<模块>.ttl`（`keep_files` 里的除外 —— 正在上传的那份要留）；
    - **注册信息** `_registry.json` 里的对应条目。

    为什么要删真源与注册：级联删除的语义是"这些模块的产物不再存在"；只删图库/wiki 而保留真源，
    下一次"编译全部"会把它们又编回来，状态就不一致了。要恢复：重新上传该模块的 TTL 即可。
    """
    report: dict = {"modules": list(keys), "files_removed": [], "registry_removed": [],
                    "wiki_pages_deleted": 0, "neo4j_nodes_deleted": 0, "kept_files": list(keep_files)}
    for key in keys:
        if not key:
            continue
        try:
            one = purge_model(key)                 # Neo4j 节点 + 本体库 wiki 页
            report["neo4j_nodes_deleted"] += int(one.get("neo4j_nodes_deleted") or 0)
            report["wiki_pages_deleted"] += int(one.get("wiki_pages_deleted") or 0)
        except Exception as exc:  # noqa: BLE001
            report.setdefault("errors", []).append("purge %s: %s" % (key, str(exc)[:120]))
        path = SOURCES_DIR / ("%s.ttl" % key)
        if path.is_file() and path.name not in keep_files:
            try:
                path.unlink()
                report["files_removed"].append(str(path.relative_to(REPO)))
            except OSError as exc:  # noqa: PERF203
                report.setdefault("errors", []).append("rm %s: %s" % (path.name, str(exc)[:80]))
        entry = next((e for e in _load_registry_entries() if str(e.get("key")) == key), None)
        if entry is not None:
            _unregister_extension(key)
            report["registry_removed"].append(key)
    return report


def compile_artifacts(modules: list | None = None) -> dict:
    """跑编译器（把真源 TTL 编译成 artifacts）。需要 rdflib 环境。

    `modules` 给了就**只编这些模块**（用户口径 2026-09-30："上传 bmm 只编译 bmm"）——
    编译器原生支持 `--module a,b`；不传 = 全部（运维 `repair` 用）。
    编译成功后立即失效本体缓存：`ontology_index.json` 已换新，进程里的旧类清单必须作废。
    """
    skipped: dict = {}
    if modules is None:
        # 编译**全部**时：只编"真源文件齐"的模块，缺文件的模块**单独列出**而不是整次失败
        # （2026-09-30：内网会遇到"某个扩展文件不在"→ 旧行为是整次编译报错、用户看不到任何产物）
        try:
            mods = _registered_modules()
            complete = [k for k, spec in mods.items() if not spec.missing_files()]
            skipped = {k: spec.missing_files() for k, spec in mods.items() if spec.missing_files()}
            modules = complete or None
        except Exception:  # noqa: BLE001
            skipped = {}
    args = ["compile"]
    if modules:
        args += ["--module", ",".join(modules)]
    out = _run(REPO / "tools" / "ontology-compiler" / "compile.py", args)
    out["module_scope"] = list(modules) if modules else "all"
    if skipped:
        out["skipped_modules"] = skipped
        out["note"] = ("以下模块的真源文件缺失，**本次未参与编译**（不影响其它模块）：%s"
                       % "、".join("%s(%s)" % (k, "、".join(v)) for k, v in skipped.items()))
    out["cache"] = _invalidate_ontology_cache()
    return out


def _sync_folders(kb: str, prune: bool = False) -> dict:
    """把「页的 category_path」落成 `wiki_folders` 目录树并把页挂到最深一级。

    为什么每个重投影入口都要调：`ontology_wiki.py project` 只写页（含 category_path），
    **不建目录也不写 `folder_id`** —— 于是重投影后本体模型库的页在 wiki 树里"看不到"、
    目录计数为 0（用户 2026-09-21 反馈"目录归类都不正确，目录统计数量也不正确"）。
    `prune=False`：删除残留空目录属管理动作，交给显式调用（`sync_folders.py --prune`）。
    """
    import importlib

    mcp = str(REPO / "tools" / "ontology-mcp")
    if mcp not in sys.path:
        sys.path.insert(0, mcp)
    try:
        mod = importlib.import_module("sync_folders")
        stmts = mod.sync_kb(kb, dry_run=False, link_pages=True, prune=prune)
        return {"ok": True, "statements": stmts}
    except Exception as exc:  # noqa: BLE001  挂目录失败不应让重投影整体失败
        return {"ok": False, "error": str(exc)}


def regen_wiki(kb_id: str = "") -> dict:
    """重投影本体 wiki 页（build 生成页面清单 → project 幂等写进本体模型知识库 → 挂目录）。

    `_sync_folders(kb, prune=True)`：本体模型库的目录**完全由页面的 category_path 推导**
    （没有人工建的目录），所以重投影时顺手清掉残留目录 —— 否则改模块 label/显示名之后，
    旧目录会空着留下（实测：把 bmmfd 的 rdfs:label 改成"…测试模型"，目录名不变 + 多一个空目录）。
    """
    kb = ontology_kb_id(kb_id)
    build = _run(REPO / "tools" / "ontology-extract" / "ontology_wiki.py", ["build"])
    project = _run(REPO / "tools" / "ontology-extract" / "ontology_wiki.py",
                   ["project", "--kb-id", kb])
    return {"kb_id": kb, "build": build, "project": project,
            "folders": _sync_folders(kb, prune=True)}


def load_model(model: str = "", kb_id: str = "", purge: bool = True,
               compile_first: bool = True, project_wiki: bool = True) -> dict:
    """本体模型的加载编排：编译 → 清理旧模型 → 灌投影 → 重投影 wiki。"""
    report: dict = {"model": model, "kb_id": ontology_kb_id(kb_id)}
    if compile_first:
        report["compile"] = compile_artifacts()
    if purge and model:
        report["purge"] = purge_model(model, kb_id)
    report["apply"] = apply_projection()
    if project_wiki:
        report["wiki"] = regen_wiki(kb_id)
    return report


if __name__ == "__main__":  # 运维自测：python3 ke_admin.py purge <model> | apply | wiki | load <model>
    import json
    args = sys.argv[1:]
    cmd = args[0] if args else ""
    if cmd == "purge" and len(args) > 1:
        out = purge_model(args[1])
    elif cmd == "apply":
        out = apply_projection()
    elif cmd == "check-prefix":
        out = check_prefix_drift()
    elif cmd == "retag-preview" and len(args) > 3:
        out = ke_pages.retag_preview(args[1], args[2], args[3])
    elif cmd == "retag-apply" and len(args) > 3:
        # 用法：retag-apply <kb> <slug> <new_type> --ticket <t> --ack url_break,refs_rewrite
        ticket, ack, rest = "", [], args[4:]
        for i, item in enumerate(rest):
            if item == "--ticket" and i + 1 < len(rest):
                ticket = rest[i + 1]
            elif item == "--ack" and i + 1 < len(rest):
                ack = [x.strip() for x in rest[i + 1].split(",") if x.strip()]
        out = ke_pages.retag_apply(args[1], args[2], args[3], ticket, ack)
    elif cmd == "retag-rollback" and len(args) > 2:
        out = ke_pages.retag_rollback(args[1], args[2])
    elif cmd == "compile":
        out = compile_artifacts()
    elif cmd == "wiki":
        out = regen_wiki()
    elif cmd == "import" and len(args) > 1:        # 上传即加载（零产物）：import <ttl> [module]
        out = import_ttl(args[1], args[2] if len(args) > 2 else "")
    elif cmd == "deps" and len(args) > 1:          # 只查：会连带删掉哪些下游模块（不删任何东西）
        out = {"model": args[1], "dependent_modules": dependent_modules(args[1])}
    elif cmd == "cascade" and len(args) > 1:       # 真删：先删下游再删自己（B 案）
        out = cascade_purge(args[1])
    elif cmd == "load":
        out = load_model(args[1] if len(args) > 1 else "", purge=True)
    elif cmd == "repair":                          # 运维修复：编译 → 灌投影 → 重投影 wiki → 体检
        out = repair_all(args[1] if len(args) > 1 else "")
    elif cmd in ("ctx-contexts", "ctx-scan", "ctx-lookup"):   # 跨库上下文映射（一期只读）
        if ke_context is None:
            out = {"error": "ke_context 不可用（tools/ke-core/ke_context.py 缺失）"}
        elif cmd == "ctx-contexts":
            out = ke_context.contexts()
        elif cmd == "ctx-scan":
            kbs = [x.strip() for x in (args[1] if len(args) > 1 else "").split(",") if x.strip()]
            out = ke_context.scan(kbs or None, write="--no-write" not in args)
        else:
            out = (ke_context.lookup(slug=args[1]) if len(args) > 1 and "/" in args[1]
                   else ke_context.lookup(q=args[1] if len(args) > 1 else ""))
    else:
        print(__doc__)
        sys.exit(1)
    print(json.dumps(out, ensure_ascii=False, indent=2)[:2000])
