"""WeKnora 真实库同步：用本体（BMM/EA）抽取出「wiki 页面 + 图谱」。

把 `extract.py` 的引擎（轻量版提示词 / LLM / 本体校验）接到 WeKnora 的**真实数据**上：

    Postgres(chunks)  ->  轻量版本体提示词  ->  LLM  ->  本体约束校验
        |- Neo4j        : BodhiInstance 节点 + 本体关系（带 knowledge_id / chunk_id 溯源）
        |- Postgres     : wiki_pages（page_type = 模块:类，如 bmm:Goal）

用法
----
    # 1) 只看提示词（不花额度）
    python tools/ontology-extract/weknora_sync.py --kb-id <kb> --knowledge-id <doc> --model bmm --dry-run

    # 2) 真跑：调 LLM -> 校验 -> 写图 + 写 wiki
    python tools/ontology-extract/weknora_sync.py --kb-id <kb> --knowledge-id <doc> --model bmm

    # 3) 复用上一次的 LLM 返回（省额度）
    ... --from-log logs/ontology_extraction_20260919_061500.log

    # 4) 只做一半 / 只看不写
    ... --no-wiki | --no-graph | --no-write

与正式形态的差异（v0.1 预演路径）
--------------------------------
- **wiki 页面由本脚本直写 Postgres**，字段与上游 `wikiPageService.CreatePage` 对齐
  （slug / title / summary / content / page_type / status / category_path / wiki_path /
  depth / source_refs / chunk_refs / in_links / out_links / aliases / page_metadata / version）。
  之所以还没走上游 service：`IsValidWikiPageType` 是硬编码白名单，`bmm:*` 会被拒，
  需要 fork 改代码 + 重建镜像（见 `docs/weknora-fork.md` §10.8 / §10.9）。
- slug 用 `<模型>/<类小写>/<name 的 sha1 前 12 位>`：本机没有拼音库；
  正式版由 Go 侧生成可读 slug（上游 ingest 就是拼音 slug）。
- 幂等：重跑会先删掉**本工具写过**的页面（`last_edit_source = 'ontology-extract'`）再重建。
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import pathlib
import shutil
import subprocess
import sys
from datetime import datetime, timezone

REPO = pathlib.Path(__file__).resolve().parents[2]
LOG_DIR = REPO / "logs"
TOOL_TAG = "ontology-extract"

DB_CONTAINER = "WeKnora-postgres"
DB_USER = "postgres"
DB_NAME = "WeKnora"
# 口令不内置（2026-09-21）：env `BODHI_DB_PASSWORD` → WeKnora `.env` 的 DB_PASSWORD
DB_PASSWORD = os.environ.get("BODHI_DB_PASSWORD", "")
if not DB_PASSWORD:
    for _env in (pathlib.Path(os.environ.get("BODHI_WEKNORA_DIR", "/mnt/c/Users/PHJY/source/WeKnora")) / ".env",):
        if _env.is_file():
            for _line in _env.read_text(encoding="utf-8", errors="ignore").splitlines():
                if _line.strip().startswith(("DB_PASSWORD=", "POSTGRES_PASSWORD=")):
                    DB_PASSWORD = _line.split("=", 1)[1].strip().strip("'\"")   # 从 .env 取值（不内置口令）
                    break

# Windows 控制台默认 GBK：含中文/emoji 的输出会抛 UnicodeEncodeError，这里切 UTF-8。
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            pass


def load_engine():
    """把同目录的 extract.py 当模块导入（复用提示词 / LLM / 校验 / 写图）。"""
    here = str(pathlib.Path(__file__).resolve().parent)
    if here not in sys.path:
        sys.path.insert(0, here)
    import extract  # noqa: PLC0415
    return extract


# ---------------------------------------------------------------------------
# Postgres 访问（走容器里的 psql，无需驱动）
# ---------------------------------------------------------------------------
def _docker_prefix() -> list[str]:
    """本机 Docker 跑在 WSL 里：优先用 PATH 上的 docker，否则走 `wsl docker`。"""
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


_DOCKER = None


def _docker_psql(extra: list[str], stdin: str | None = None) -> subprocess.CompletedProcess:
    global _DOCKER
    if _DOCKER is None:
        _DOCKER = _docker_prefix()
    cmd = _DOCKER + ["exec", "-i", "-e", "PGPASSWORD=" + DB_PASSWORD,
                     DB_CONTAINER, "psql", "-U", DB_USER, "-d", DB_NAME] + extra
    return subprocess.run(cmd, input=stdin, text=True, encoding="utf-8",
                          capture_output=True, check=False)


def psql_csv(sql: str) -> list[dict]:
    """查询并以 CSV 解析（RFC4180，正文里的换行/逗号都安全）。"""
    done = _docker_psql(["--csv", "-c", sql])
    if done.returncode != 0:
        raise SystemExit("psql 查询失败：%s" % (done.stderr or done.stdout)[:800])
    rows = list(csv.DictReader(io.StringIO(done.stdout)))
    return rows


def psql_exec(sql: str) -> str:
    """执行一段 SQL（走 stdin，ON_ERROR_STOP 让错误立刻中断）。"""
    done = _docker_psql(["-v", "ON_ERROR_STOP=1", "-q", "-f", "-"], stdin=sql)
    if done.returncode != 0:
        raise SystemExit("SQL 执行失败：%s" % (done.stderr or done.stdout)[:1200])
    return (done.stderr or "").strip()


# ---------------------------------------------------------------------------
# 1) 取真实片段：正文用 parent_text（含全文），溯源用子片段（更细粒度）
# ---------------------------------------------------------------------------
def _quote_list(values: list[str]) -> str:
    return ", ".join("'" + v.replace("'", "") + "'" for v in values)


def fetch_knowledges(knowledge_ids: list[str]) -> dict[str, dict]:
    rows = psql_csv(
        "SELECT id, title, knowledge_base_id, tenant_id FROM knowledges "
        "WHERE id IN (%s)" % _quote_list(knowledge_ids))
    return {r["id"]: r for r in rows}


def fetch_chunks(knowledge_ids: list[str]) -> list[dict]:
    rows = psql_csv(
        "SELECT id, knowledge_id, chunk_index, chunk_type, content FROM chunks "
        "WHERE knowledge_id IN (%s) AND deleted_at IS NULL "
        "ORDER BY knowledge_id, chunk_index, id" % _quote_list(knowledge_ids))
    for row in rows:
        row["chunk_index"] = int(row.get("chunk_index") or 0)
    return rows


def split_body_and_pool(chunks: list[dict]) -> tuple[list[dict], list[dict]]:
    """把片段分成「正文」（喂 LLM）与「溯源池」（定位 source_text 属于哪个片段）。

    WeKnora 的父子分块：`parent_text` 是含全文的父块，`text` 是子块。
    正文取父块（上下文完整、不重复），溯源优先用子块（粒度细）。
    """
    body = [c for c in chunks if c.get("chunk_type") == "parent_text"]
    if not body:
        texts = [c for c in chunks if c.get("chunk_type") == "text"]
        pool_candidates = texts or [c for c in chunks if c.get("chunk_type") != "summary"]
        if pool_candidates:
            body = [max(pool_candidates, key=lambda c: len(c["content"] or ""))]
    pool = [c for c in chunks if c.get("chunk_type") == "text"] or body
    return body, pool


def build_doc_text(bodies: list[dict]) -> str:
    return "\n\n---\n\n".join((c["content"] or "").strip() for c in bodies)


def _squash(text: str) -> str:
    """去掉全部空白后比较：LLM 引用时可能丢了换行/空格，但字面一致。"""
    return "".join((text or "").split())


def locate_chunk(source_text: str, source_span: str, pool: list[dict]) -> tuple[str, str, int]:
    """把一条 source_text 定位到具体片段。

    返回 (chunk_id, 定位方式, chunk_index)；定位不到时返回空串（调用方回退到正文块）。
    """
    needle = _squash(source_text)
    if needle:
        for chunk in pool:
            if needle in _squash(chunk["content"]):
                return chunk["id"], "exact", chunk["chunk_index"]
    span = _squash(source_span)[:12]
    if span:
        for chunk in pool:
            if span in _squash(chunk["content"]):
                return chunk["id"], "span", chunk["chunk_index"]
    return "", "miss", -1


# ---------------------------------------------------------------------------
# 2) 写 Neo4j：复用 extract.write_graph，再补 WeKnora 溯源字段
# ---------------------------------------------------------------------------
def graph_refs(engine, checked: dict, pool: list[dict], fallback_chunk: dict,
               knowledge: dict) -> tuple[list[dict], list[dict], dict]:
    """给每个要素/关系定位来源片段（knowledge_id + chunk_id）。"""
    node_refs, edge_refs, stats = [], [], {"exact": 0, "span": 0, "miss": 0}

    def resolve(source_text: str, source_span: str) -> tuple[str, int, str]:
        chunk_id, how, index = locate_chunk(source_text, source_span, pool)
        stats[how] = stats.get(how, 0) + 1
        if not chunk_id and fallback_chunk:
            return fallback_chunk["id"], fallback_chunk["chunk_index"], "body"
        return chunk_id, index, how

    for node in checked["nodes"]:
        chunk_id, index, how = resolve(node.get("source_text", ""), node.get("source_span", ""))
        node_refs.append({
            "key": engine.node_key(node), "knowledge_id": knowledge["id"],
            "knowledge_title": knowledge.get("title", ""), "chunk_id": chunk_id,
            "chunk_index": index, "how": how,
        })
    for edge in checked["edges"]:
        chunk_id, index, how = resolve(edge.get("source_text", ""), edge.get("source_span", ""))
        edge_refs.append({
            "key": "%s|%s|%s" % (edge["type"], edge["source"], edge["target"]),
            "knowledge_id": knowledge["id"], "chunk_id": chunk_id,
            "chunk_index": index, "how": how,
        })
    return node_refs, edge_refs, stats


def patch_provenance(engine, model: dict, node_refs: list[dict], edge_refs: list[dict],
                     kb: str) -> dict:
    """把 WeKnora 溯源写回图：节点按 key、关系按 key。"""
    statements = []
    for ref in node_refs:
        statements.append({
            "statement": (
                "MATCH (n:%s {key: $key}) "
                "SET n.knowledge_id = $kid, n.knowledge_title = $ktitle, "
                "    n.weknora_kb = $kb, n.chunk_id = $cid, n.chunk_index = $idx, "
                "    n.chunk_located_by = $how, n.model_key = $model"
            ) % engine.INSTANCE_LABEL,
            "parameters": {"key": ref["key"], "kid": ref["knowledge_id"],
                           "ktitle": ref["knowledge_title"], "kb": kb,
                           "cid": ref["chunk_id"], "idx": ref["chunk_index"],
                           "how": ref["how"], "model": model["key"]},
        })
    # 关系：Neo4j 关系类型必须在 Cypher 里显式给出，所以按类型分组、每组一条 UNWIND 语句
    typed: dict[str, list[dict]] = {}
    for ref in edge_refs:
        typed.setdefault(ref["key"].split("|", 1)[0], []).append(ref)
    for type_name, refs in typed.items():
        statements.append({
            "statement": (
                "UNWIND $rows AS row "
                "MATCH (:%s)-[r:%s {key: row.key}]->(:%s) "
                "SET r.knowledge_id = row.kid, r.weknora_kb = row.kb, r.chunk_id = row.cid, "
                "    r.chunk_index = row.idx, r.chunk_located_by = row.how"
            ) % (engine.INSTANCE_LABEL, engine.rel_label(type_name), engine.INSTANCE_LABEL),
            "parameters": {"rows": [{"key": r["key"], "kid": r["knowledge_id"], "kb": kb,
                                     "cid": r["chunk_id"], "idx": r["chunk_index"],
                                     "how": r["how"]} for r in refs]},
        })
    return engine.neo4j_commit(statements)


# ---------------------------------------------------------------------------
# 3) 要素 -> wiki 页面（page_type = 模块:类），并写 Postgres
# ---------------------------------------------------------------------------
def slug_for(model_key: str, type_name: str, name: str) -> str:
    """`bmm/goal/<name 的 sha1 前 12 位>`：稳定、唯一、ASCII 安全。

    本机没有拼音库；正式版由 Go 侧生成可读 slug（上游 ingest 用拼音）。
    """
    local = (type_name.split(":", 1)[-1] or "unknown").lower()
    digest = hashlib.sha1(name.encode("utf-8")).hexdigest()[:12]
    return "%s/%s/%s" % (model_key, local, digest)


def relation_spec(engine, model: dict, type_name: str) -> dict:
    for spec in engine.allowed_relations(model):
        if spec["name"] == type_name:
            return spec
    return {"name": type_name, "label": type_name}


def build_pages(engine, model: dict, checked: dict, node_refs: list[dict],
                doc_meta: dict, pages_meta: dict) -> list[dict]:
    """把校验后的要素变成 wiki 页面（含 index 汇总页）。"""
    slug_by_name = {n["name"]: slug_for(model["key"], n["type"], n["name"])
                    for n in checked["nodes"]}
    ref_by_key = {ref["key"]: ref for ref in node_refs}
    out_edges, in_edges = {}, {}
    for edge in checked["edges"]:
        out_edges.setdefault(edge["source"], []).append(edge)
        in_edges.setdefault(edge["target"], []).append(edge)

    class_order: dict[str, int] = {}
    pages = []
    for node in checked["nodes"]:
        type_label = node["type_label"] or node["type"]
        class_order[type_label] = class_order.get(type_label, 0) + 1
        ref = ref_by_key.get(engine.node_key(node)) or {}
        chunk_index = ref.get("chunk_index", -1)
        origin = "《%s》" % doc_meta["title"]
        if isinstance(chunk_index, int) and chunk_index >= 0:
            origin += " 片段 #%d" % chunk_index

        lines = ["# %s" % node["name"], "",
                 "> **本体类型**：%s（`%s`）  " % (type_label, node["type"]),
                 "> **来源**：%s  " % origin,
                 "> **模型**：%s（%s）" % (model["label"], model["key"]), ""]
        if node["definition"]:
            lines += [node["definition"], ""]
        if node["description"]:
            lines += ["## 判定依据", "", node["description"], ""]
        if node["source_text"]:
            lines += ["## 原文依据", "", "> " + node["source_text"].replace("\n", "\n> "), ""]

        outs = out_edges.get(node["name"], [])
        ins = in_edges.get(node["name"], [])
        out_slugs, in_slugs = [], []
        if outs or ins:
            lines += ["## 本体关系", ""]
            for edge in outs:
                spec = relation_spec(engine, model, edge["type"])
                target_slug = slug_by_name.get(edge["target"], "")
                out_slugs.append(target_slug)
                lines.append("- %s（`%s`）→ [%s](wiki:%s)"
                             % (spec.get("label") or edge["type"], edge["type"],
                                edge["target"], target_slug))
            for edge in ins:
                spec = relation_spec(engine, model, edge["type"])
                source_slug = slug_by_name.get(edge["source"], "")
                in_slugs.append(source_slug)
                lines.append("- ← [%s](wiki:%s) 通过 %s（`%s`）指向本要素"
                             % (edge["source"], source_slug,
                                spec.get("label") or edge["type"], edge["type"]))
            lines.append("")
        pages.append(build_element_page(model, node, type_label, lines, ref, out_slugs,
                                        in_slugs, doc_meta, pages_meta))
    pages.append(build_index_page(engine, model, checked, pages, doc_meta, pages_meta))
    return pages


def build_element_page(model: dict, node: dict, type_label: str, lines: list[str],
                       ref: dict, out_slugs: list[str], in_slugs: list[str],
                       doc_meta: dict, pages_meta: dict) -> dict:
    """单个要素 -> wiki 页面（字段与上游 CreatePage 对齐）。"""
    return {
        "slug": slug_for(model["key"], node["type"], node["name"]),
        "title": node["name"],
        "page_type": node["type"],                       # 如 bmm:Goal（本体类型）
        "summary": (node["definition"] or "")[:500],
        "content": "\n".join(lines).rstrip() + "\n",
        "category_path": [model["label"], type_label],
        "wiki_path": "%s/%s/%s" % (model["key"], type_label, node["name"]),
        "sort_order": 0,
        "source_refs": [doc_meta["id"]],
        "chunk_refs": [ref["chunk_id"]] if ref.get("chunk_id") else [],
        "out_links": [s for s in out_slugs if s],
        "in_links": [s for s in in_slugs if s],
        "aliases": [node["name"]],
        "page_metadata": {"ontology": {
            "model": model["key"], "model_label": model["label"],
            "class": node["type"], "class_label": type_label, "name": node["name"],
            "expert_role": model["expert_role"], "source_doc": doc_meta["title"],
            "chunk_index": ref.get("chunk_index", -1), "chunk_located_by": ref.get("how", ""),
            "generated_at": pages_meta["generated_at"], "generator": TOOL_TAG,
        }},
    }


def build_index_page(engine, model: dict, checked: dict, pages: list[dict],
                     doc_meta: dict, pages_meta: dict) -> dict:
    """一张索引页（page_type='index'，上游合法类型）：按本体类分组列出所有要素。"""
    grouped: dict[str, list[dict]] = {}
    for page in pages:
        grouped.setdefault(page["category_path"][1], []).append(page)
    lines = ["# %s · %s 本体提取索引" % (doc_meta["title"], model["label"]), "",
             "> 来源文档：《%s》  " % doc_meta["title"],
             "> 要素 %d 个 / 关系 %d 条 / 违规 %d 条  "
             % (len(checked["nodes"]), len(checked["edges"]), len(checked["violations"])),
             "> 生成方式：`%s`（%s）" % (TOOL_TAG, pages_meta["generated_at"]), ""]
    for class_label in sorted(grouped):
        lines += ["## %s（%d）" % (class_label, len(grouped[class_label])), ""]
        for page in grouped[class_label]:
            lines.append("- [%s](wiki:%s) — %s"
                         % (page["title"], page["slug"], page["summary"][:80]))
        lines.append("")
    if checked["unmatched"]:
        lines += ["## 未归类（unmatched，不丢弃）", ""]
        for item in checked["unmatched"]:
            lines.append("- %s（%s）" % (item.get("name"), item.get("reason")))
        lines.append("")
    slug = "%s/index/%s" % (model["key"], pages_meta["stamp"])
    return {
        "slug": slug,
        "title": "%s · %s 本体提取索引" % (doc_meta["title"], model["label"]),
        "page_type": "index",
        "summary": "按 %s 本体模型对《%s》的提取结果索引：%d 个要素 / %d 条关系。"
                   % (model["label"], doc_meta["title"], len(checked["nodes"]),
                      len(checked["edges"])),
        "content": "\n".join(lines).rstrip() + "\n",
        "category_path": [model["label"], "索引"],
        "wiki_path": "%s/index/%s" % (model["key"], pages_meta["stamp"]),
        "sort_order": 0,
        "source_refs": [doc_meta["id"]],
        "chunk_refs": [],
        "out_links": [p["slug"] for p in pages],
        "in_links": [],
        "aliases": [],
        "page_metadata": {"ontology": {"model": model["key"], "kind": "index",
                                       "generator": TOOL_TAG}},
    }


# ---------------------------------------------------------------------------
# 4) 写 Postgres（wiki_pages）
# ---------------------------------------------------------------------------
PAGE_COLUMNS = [
    "id", "tenant_id", "knowledge_base_id", "slug", "title", "page_type", "status",
    "content", "summary", "parent_slug", "folder_id", "category_path", "wiki_path",
    "depth", "sort_order", "source_refs", "chunk_refs", "in_links", "out_links",
    "page_metadata", "aliases", "version", "last_edit_source", "last_editor_id",
]


def sql_str(value) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def sql_json(value) -> str:
    return sql_str(json.dumps(value, ensure_ascii=False)) + "::jsonb"


def page_id_for(slug: str) -> str:
    """按 slug 生成稳定 UUID：重跑时同一页面拿同一个 id。"""
    import uuid  # noqa: PLC0415
    return str(uuid.uuid5(uuid.NAMESPACE_URL, "bodhi:" + slug))


def pages_to_sql(pages: list[dict], kb_id: str, tenant_id: int) -> str:
    """生成幂等 SQL：先删掉本工具写过的页面，再整批 INSERT。"""
    out = ["-- 由 tools/ontology-extract/weknora_sync.py 生成（本体驱动 wiki）",
           "BEGIN;",
           "DELETE FROM wiki_pages WHERE knowledge_base_id = %s AND last_edit_source = %s;"
           % (sql_str(kb_id), sql_str(TOOL_TAG))]
    for page in pages:
        values = [
            sql_str(page_id_for(page["slug"])), str(tenant_id), sql_str(kb_id),
            sql_str(page["slug"]), sql_str(page["title"]), sql_str(page["page_type"]),
            sql_str("published"), sql_str(page["content"]), sql_str(page["summary"]),
            sql_str(""), sql_str(""), sql_json(page["category_path"]),
            sql_str(page["wiki_path"]), str(len(page["category_path"])),
            str(page.get("sort_order") or 0), sql_json(page["source_refs"]),
            sql_json(page["chunk_refs"]), sql_json(page["in_links"]),
            sql_json(page["out_links"]), sql_json(page["page_metadata"]),
            sql_json(page["aliases"]), "1", sql_str(TOOL_TAG), sql_str(""),
        ]
        out.append("INSERT INTO wiki_pages (%s) VALUES (%s);"
                   % (", ".join(PAGE_COLUMNS), ", ".join(values)))
    out.append("COMMIT;")
    return "\n".join(out) + "\n"


def write_pages(pages: list[dict], kb_id: str, tenant_id: int) -> str:
    sql = pages_to_sql(pages, kb_id, tenant_id)
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    dump = LOG_DIR / ("ontology_wiki_%s.sql" % datetime.now().strftime("%Y%m%d_%H%M%S"))
    dump.write_text(sql, encoding="utf-8")
    psql_exec(sql)
    print("[Bodhi]   ✅ 已写 wiki：%d 页（含索引页）；SQL 留档 %s"
          % (len(pages), dump.relative_to(REPO).as_posix()))
    return sql


def verify_wiki(kb_id: str) -> list[dict]:
    return psql_csv(
        "SELECT page_type, count(*) AS pages FROM wiki_pages "
        "WHERE knowledge_base_id = %s AND deleted_at IS NULL "
        "GROUP BY page_type ORDER BY page_type" % sql_str(kb_id))


# ---------------------------------------------------------------------------
# 5) 主流程
# ---------------------------------------------------------------------------
def filter_dangling_edges(engine, nodes: list[dict], edges: list[dict]) -> tuple[list[dict], list[dict]]:
    """丢掉端点既不在本次要素里、也不在图里的关系（否则写图会中断）。"""
    names = {n["name"] for n in nodes}
    kept, dropped = [], []
    for edge in edges:
        ok = True
        for name in (edge["source"], edge["target"]):
            if name in names:
                continue
            try:
                engine.lookup_existing_type(name)
            except SystemExit:
                ok = False
                break
        (kept if ok else dropped).append(edge)
    return kept, dropped


def parse_args() -> argparse.Namespace:
    here = pathlib.Path(__file__).resolve().parent          # <repo>/tools/ontology-extract
    repo = here.parents[1]                                  # <repo>
    parser = argparse.ArgumentParser(
        description="WeKnora 真库 -> 本体抽取 -> wiki 页面 + 图谱")
    parser.add_argument("--kb-id", required=True, help="目标知识库 UUID（写 wiki 用）")
    parser.add_argument("--knowledge-id", required=True,
                        help="源文档 UUID，多个用逗号分隔（写溯源用）")
    parser.add_argument("--model", default="bmm", help="本体模型 key（bmm / ea）")
    parser.add_argument("--kb", default="", help="图里的知识库标记（默认取 --kb-id）")
    parser.add_argument("--index", default=str(repo / "artifacts" / "weknora"
                                               / "ontology_index.json"),
                        help="本体目录产物路径")
    parser.add_argument("--env-file", default=str(repo / ".env"),
                        help="LLM 配置所在 .env")
    parser.add_argument("--from-log", default="", help="复用日志里的 LLM 返回（省额度）")
    parser.add_argument("--dry-run", action="store_true", help="只导出提示词，不调 LLM")
    parser.add_argument("--no-write", action="store_true", help="只跑不写（打印计划）")
    parser.add_argument("--no-graph", action="store_true", help="不写 Neo4j")
    parser.add_argument("--no-wiki", action="store_true", help="不写 wiki 页面")
    parser.add_argument("--out", default="", help="报告 JSON 路径（默认 logs/ 下）")
    return parser.parse_args()


def build_llm_cfg(engine, env: dict) -> dict:
    return {
        "api_key": env.get("LLM_API_KEY", ""),
        "base_url": env.get("LLM_BASE_URL", "https://api.deepseek.com"),
        "model": env.get("LLM_MODEL", "deepseek-flash"),
        "temperature": float(env.get("LLM_TEMPERATURE", "0.1")),
        "max_tokens": int(env.get("LLM_MAX_TOKENS", "32768")),
        "json_mode": env.get("LLM_JSON_MODE", "1") == "1",
    }


def count_by(items: list[dict], key: str) -> dict[str, int]:
    out: dict[str, int] = {}
    for item in items:
        out[item.get(key, "")] = out.get(item.get(key, ""), 0) + 1
    return dict(sorted(out.items()))


def main() -> int:
    args = parse_args()
    engine = load_engine()
    kb_id, kb_tag = args.kb_id, (args.kb or args.kb_id)

    index = engine.load_index(pathlib.Path(args.index))
    model = engine.pick_model(index, args.model)
    light = engine.load_light(model, engine.DEFAULT_PROMPTS)
    system = engine.build_system_prompt(model, light)

    knowledge_ids = [x.strip() for x in args.knowledge_id.split(",") if x.strip()]
    knowledges = fetch_knowledges(knowledge_ids)
    missing = [k for k in knowledge_ids if k not in knowledges]
    if missing:
        raise SystemExit("知识库里找不到这些文档：%s" % ", ".join(missing))
    chunks = fetch_chunks(knowledge_ids)
    if not chunks:
        raise SystemExit("文档没有可用片段（chunks 为空或已删除）")
    body, pool = split_body_and_pool(chunks)
    doc_text = build_doc_text(body)
    titles = "；".join(sorted({k["title"] for k in knowledges.values()}))
    doc_meta = {"id": knowledge_ids[0], "title": titles}
    tenant_id = int(next(iter(knowledges.values())).get("tenant_id") or 10000)

    print("[Bodhi] 知识库 %s（图标记 %s）" % (kb_id, kb_tag))
    print("[Bodhi] 文档 %d 篇：%s" % (len(knowledge_ids), titles))
    print("[Bodhi] 片段 %d 个 -> 正文 %d 个父块（%d 字符）+ 溯源池 %d 个子块"
          % (len(chunks), len(body), len(doc_text), len(pool)))
    print("[Bodhi] 模型=%s（%s）｜专家角色=%s"
          % (model["key"], model["label"], model["expert_role"]))
    print("[Bodhi] 可用类型 %d 个 / 可用关系 %d 条"
          % (len(engine.allowed_classes(model)), len(engine.allowed_relations(model))))

    env = engine.load_env(pathlib.Path(args.env_file))
    existing: list[dict] = []
    if not args.dry_run:
        try:
            existing = engine.fetch_existing(model)
            print("[Bodhi] 存量要素 %d 条（已注入提示词）" % len(existing))
        except SystemExit as exc:
            print("[Bodhi] ⚠ 取存量失败（%s），按无存量继续" % exc)
    user = engine.build_user_prompt(doc_meta["title"], doc_text, existing)
    print("[Bodhi] system %d 字符（其中轻量版 %d）/ user %d 字符"
          % (len(system), len(light), len(user)))

    if args.dry_run:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        dump = LOG_DIR / "last_weknora_prompt_preview.md"
        dump.write_text("# system\n\n%s\n\n# user\n\n%s\n" % (system, user), encoding="utf-8")
        print("[Bodhi] ✅ dry-run：提示词已写到 %s（未调用 LLM）"
              % dump.relative_to(REPO).as_posix())
        return 0

    if args.from_log:
        raw = engine.read_raw_from_log(pathlib.Path(args.from_log))
        print("[Bodhi] ♻ 复用日志里的 LLM 返回（%d 字符），本次未调用 LLM" % len(raw))
    else:
        raw, _meta = engine.call_llm(build_llm_cfg(engine, env), system, user)
    checked = engine.validate(engine.parse_json(raw), model, doc_meta["title"])

    print("[Bodhi] 解析：要素 %d / 关系 %d / 违规 %d / unmatched %d"
          % (len(checked["nodes"]), len(checked["edges"]),
             len(checked["violations"]), len(checked["unmatched"])))
    for violation in checked["violations"][:10]:
        print("           ⚠ 违规 %s: %s（%s）"
              % (violation["kind"], violation["value"], violation["name"]))
    for item in checked["unmatched"][:5]:
        print("           · unmatched：%s（%s）" % (item.get("name"), item.get("reason")))

    node_refs, edge_refs, prov = graph_refs(engine, checked, pool, body[0], doc_meta)
    edges_kept, edges_dropped = filter_dangling_edges(engine, checked["nodes"], checked["edges"])
    kept_keys = {"%s|%s|%s" % (e["type"], e["source"], e["target"]) for e in edges_kept}
    edge_refs = [r for r in edge_refs if r["key"] in kept_keys]
    writable = dict(checked)
    writable["edges"] = edges_kept
    if edges_dropped:
        print("[Bodhi] ⚠ 丢弃 %d 条端点未定义的关系（见报告 dangling）" % len(edges_dropped))
    print("[Bodhi] 溯源定位：exact %d / span %d / body %d / miss %d"
          % (prov.get("exact", 0), prov.get("span", 0), prov.get("body", 0), prov.get("miss", 0)))

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    pages_meta = {"stamp": stamp, "generated_at": datetime.now(timezone.utc)
                  .astimezone().strftime("%Y-%m-%d %H:%M:%S %z")}
    pages = build_pages(engine, model, writable, node_refs, doc_meta, pages_meta)

    if args.no_write:
        print("[Bodhi] --no-write：本来会写 %d 个图节点 / %d 条关系 / %d 个 wiki 页面"
              % (len(writable["nodes"]), len(edges_kept), len(pages)))
        for page in pages:
            print("           · [%s] %s  (%s)" % (page["page_type"], page["title"], page["slug"]))
        return 0

    graph_stats: dict = {}
    if not args.no_graph:
        graph_stats = engine.write_graph(model, writable["nodes"], edges_kept, kb_tag)
        patch_provenance(engine, model, node_refs, edge_refs, kb_tag)
        print("[Bodhi] ✅ 已写 Neo4j：节点 %d / 关系 %d（含 knowledge_id + chunk_id 溯源）"
              % (graph_stats.get("nodes_written", 0), len(edges_kept)))
    if not args.no_wiki:
        write_pages(pages, kb_id, tenant_id)

    report = {
        "generated_at": pages_meta["generated_at"],
        "kb_id": kb_id, "kb_tag": kb_tag, "model": model["key"],
        "model_label": model["label"], "expert_role": model["expert_role"],
        "knowledge": {k: v["title"] for k, v in knowledges.items()},
        "chunks": {"total": len(chunks), "body": len(body), "pool": len(pool),
                   "chars": len(doc_text)},
        "extraction": {
            "elements": len(checked["nodes"]),
            "elements_by_class": count_by(checked["nodes"], "type"),
            "relationships": len(checked["edges"]),
            "relationships_by_type": count_by(checked["edges"], "type"),
            "violations": checked["violations"],
            "unmatched": checked["unmatched"],
            "dangling_dropped": ["%s:%s->%s" % (e["type"], e["source"], e["target"])
                                 for e in edges_dropped],
            "provenance": prov,
        },
        "graph": graph_stats,
        "wiki": {"pages": len(pages), "by_page_type": count_by(pages, "page_type"),
                 "written": not args.no_wiki,
                 "in_db": verify_wiki(kb_id) if not args.no_wiki else []},
    }
    out = pathlib.Path(args.out) if args.out else (LOG_DIR / ("ontology_sync_%s.json" % stamp))
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print("[Bodhi] 📄 报告：%s" % out)
    print("[Bodhi] 验证（Neo4j Browser http://localhost:7474）：")
    print("           MATCH (n:BodhiInstance {model_key:'%s'}) RETURN n.type AS 类型, count(*) AS 数量"
          " ORDER BY 数量 DESC" % model["key"])
    print("           MATCH (a:BodhiInstance)-[r]->(b:BodhiInstance) RETURN a, r, b LIMIT 200")
    print("[Bodhi] 验证（wiki_pages 按本体类型计数）：")
    for row in report["wiki"]["in_db"]:
        print("           %-18s %s 页" % (row["page_type"], row["pages"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())








