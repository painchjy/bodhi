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

import os
import pathlib
import re
import subprocess
import sys

HERE = pathlib.Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import ke_db  # noqa: E402
import ke_neo4j  # noqa: E402
import ke_pages  # noqa: E402

REPO = HERE.parents[1]
ONTOLOGY_KB = os.environ.get("ONTOLOGY_KB_ID", "08810cbd-af86-48d1-bd25-3b2c338e3d68")
PROJECTION_DIR = REPO / "artifacts" / "neo4j"


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
    kb = kb_id or ONTOLOGY_KB
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
    return {"ontology_iri": iri, "namespace": ns, "prefix": prefix}


def _cypher_statements(path: pathlib.Path) -> list[str]:
    """把 emitter 产出的 cypher 文本拆成可执行语句（去掉 `//` 注释行）。"""
    text = path.read_text(encoding="utf-8")
    text = "\n".join(line for line in text.splitlines() if not line.strip().startswith("//"))
    return [s.strip() for s in text.split(";") if s.strip()]


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

    spec = ModuleSpec(key=module, prefix=meta["prefix"] or module, label=module,
                      short_label=(meta["prefix"] or module).upper(),
                      ontology_iri=meta["ontology_iri"], namespace=meta["namespace"],
                      files=(ttl,), kind="extension", affects=())

    out: dict = {"module": module, "ttl": str(ttl), **{k: meta[k] for k in ("ontology_iri", "namespace", "prefix")}}
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


def _registered_module_keys() -> list[str]:
    """已登记模块的 key 列表（`_registered_modules()` 的轻量包装）。"""
    return list(_registered_modules().keys())


def upload_ttl(filename: str, content: str = "", module: str = "", project_wiki: bool = False,
               kb_id: str = "") -> dict:
    """把上传内容落进 `ontology/uploads/`（gitignore），再走 `import_ttl()`。

    落盘只为留痕/可追溯（同名覆盖，加时间戳前缀防冲突）；真正的真源判断走 env/config 那套不变。
    """
    import time

    content = content or ""
    if not content.strip():
        raise ValueError("上传内容为空")
    if len(content) > 2_000_000:
        raise ValueError("TTL 过大（%d 字节 > 2MB），请拆分模块" % len(content))
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    safe = pathlib.Path(filename or "upload.ttl").name
    target = UPLOAD_DIR / ("%s-%s" % (time.strftime("%Y%m%d-%H%M%S"), safe))
    target.write_text(content, encoding="utf-8")
    try:
        return import_ttl(target, module=module, project_wiki=project_wiki, kb_id=kb_id)
    except Exception:
        # 导入失败（例如依赖未就绪）→ 不要留下"看似已上传"的文件：改名标记为被拒（便于事后查看）
        try:
            target.rename(target.with_name(target.name + ".rejected"))
        except OSError:
            pass
        raise


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


def compile_artifacts() -> dict:
    """跑编译器（把 `ontology/*.ttl` 编译成 artifacts）。需要 rdflib 环境。

    编译成功后立即失效本体缓存：`ontology_index.json` 已换新，进程里的旧类清单必须作废。
    """
    out = _run(REPO / "tools" / "ontology-compiler" / "compile.py", ["compile"])
    out["cache"] = _invalidate_ontology_cache()
    return out


def regen_wiki(kb_id: str = "") -> dict:
    """重投影本体 wiki 页（build 生成页面清单 → project 幂等写进本体模型知识库）。"""
    kb = kb_id or ONTOLOGY_KB
    build = _run(REPO / "tools" / "ontology-extract" / "ontology_wiki.py", ["build"])
    project = _run(REPO / "tools" / "ontology-extract" / "ontology_wiki.py",
                   ["project", "--kb-id", kb])
    return {"kb_id": kb, "build": build, "project": project}


def load_model(model: str = "", kb_id: str = "", purge: bool = True,
               compile_first: bool = True, project_wiki: bool = True) -> dict:
    """本体模型的加载编排：编译 → 清理旧模型 → 灌投影 → 重投影 wiki。"""
    report: dict = {"model": model, "kb_id": kb_id or ONTOLOGY_KB}
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
    else:
        print(__doc__)
        sys.exit(1)
    print(json.dumps(out, ensure_ascii=False, indent=2)[:2000])
