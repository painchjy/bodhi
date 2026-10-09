"""ke-core · wiki 页面维护（本体关系增删改 / 类型修改 / 批量软删除）。

对应用户 2026-09-19 的三条需求
------------------------------
- 需求 1：wiki 编辑页可改**本体类型**（`set_page_type`，重算 category_path 并重建目录）。
- 需求 2：**Bodhi 图谱的关系维护**（`add_relation` / `update_relation` / `delete_relation`）：
  * 只允许用「本页类（含父类继承）的 domain 对象属性」建边；
  * 目标页必须落在该对象属性 `range` 的**子类闭包**内；
  * 出边（本页正文 `## 本体关系` 小节）只能由本页改；反向边（别的页指向本页）
    在**对方页面**里改，本页只读展示 —— 与 in_links 的语义一致。
- 需求 3（§5.3）：多选批量软删除（`soft_delete_pages`，永不动 `index`）。

写库纪律（沿用 `server.py` 已验证的约定）
---------------------------------------
- 先写 `wiki_page_revisions` 快照（version 用旧值）→ 再 `version = version + 1`，可回退；
- `last_edit_source` 是 varchar(16)：本模块用 `bodhi-rel-edit`(13) / `bodhi-type-edit`(15) / `bodhi-page-del`(13)；
- `out_links` / `in_links` 是 jsonb 数组；上游可能有**标量**脏值 → 一律先 `jsonb_typeof` 守卫；
- slug 派生主键是确定性 UUIDv5，本模块只做 UPDATE，不 INSERT，避免撞软删除旧页。
"""

from __future__ import annotations

import pathlib
import re
import sys

HERE = pathlib.Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import ke_db  # noqa: E402
import ke_ontology  # noqa: E402

REL_SECTION = "## 本体关系"
TAG_REL = "bodhi-rel-edit"     # 13 字符
TAG_TYPE = "bodhi-type-edit"   # 15 字符
TAG_DEL = "bodhi-page-del"     # 13 字符
TAG_OPS = "bodhi-ops-edit"     # 14 字符（一致性巡检的显式修复：删悬空/重复关系行）
assert max(len(TAG_REL), len(TAG_TYPE), len(TAG_DEL), len(TAG_OPS)) <= 16

# 「本体关系」小节的行格式（与 server.py / relink_pages.py 保持一致）
REL_LINE = re.compile(r"^- (?P<label>.+?)（`(?P<type>[^`]+)`）→ \[(?P<target>.+?)\]\(wiki:(?P<slug>[^)]+)\)\s*$")
REL_LINE_V2 = re.compile(r"^- (?P<label>.+?)（`(?P<type>[^`]+)`）→ \[\[(?P<slug>[^|\]]+)\|(?P<target>[^\]]+)\]\]\s*$")


def parse_rel_line(line: str):
    """解析「本体关系」小节的一行，兼容新旧两种链接格式。"""
    return REL_LINE.match(line) or REL_LINE_V2.match(line)


def rel_line(label: str, rel_type: str, target: str, slug: str) -> str:
    """生成「本体关系」小节的一行（统一 [[slug|正文]] 站内链接格式）。"""
    if slug:
        return "- %s（`%s`）→ [[%s|%s]]" % (label, rel_type, slug, target)
    return "- %s（`%s`）→ %s" % (label, rel_type, target)


# ---------------------------------------------------------------------------
# 边限定属性（「## 关系限定（边属性）」小节）
# ---------------------------------------------------------------------------
# 为什么单开一个小节：`## 本体关系` 的行语法被本模块解析成 out_links/in_links
# （反向边、巡检 A1/A5、关系面板都吃它），**改语法会连带一大片**；
# 而 CRUD 种类、幂等这类**边上的限定属性**（本体声明在源类上）只需要能读能写能渲染。
QUAL_SECTION = "## 关系限定（边属性）"
QUAL_LINE = re.compile(r"^- (?P<label>.+?)（`(?P<type>[^`]+)`）→ (?P<target>.*?)："
                       r"(?P<props>.+?)\s*$")


def rel_qualifier_line(label: str, rel_type: str, target: str, slug: str,
                       properties: dict) -> str:
    """生成「关系限定（边属性）」小节的一行（`crudKind=C,U` 这类）。

    分隔符用 **`；`** 而不是 `，`：值本身可能含逗号（`crudKind=C,U`），
    用逗号当分隔符会把一个值拆成两段（2026-09-21 实测：`C,U` 被读成 `C`）。
    """
    pairs = "；".join("%s=%s" % (k, v) for k, v in sorted((properties or {}).items()))
    link = ("[[%s|%s]]" % (slug, target)) if slug else target
    return "- %s（`%s`）→ %s：%s" % (label or rel_type, rel_type, link, pairs)


def parse_rel_qualifiers(content: str) -> list[dict]:
    """解析「关系限定（边属性）」小节 → [{type, slug, target, label, properties, line_index}]。

    与本模块的 out_links 解析**同一口径**（小节 + 行两个正则），保证渲染/回读闭环。
    分隔符：优先 `；`（新格式，值里可含逗号）；旧数据用 `，` 分隔时按逗号兜底。
    """
    lines = (content or "").splitlines()
    start = next((i for i, line in enumerate(lines) if line.strip() == QUAL_SECTION), -1)
    if start < 0:
        return []
    end = next((i for i in range(start + 1, len(lines)) if lines[i].startswith("## ")), len(lines))
    out = []
    for idx in range(start + 1, end):
        hit = QUAL_LINE.match(lines[idx].strip())
        if not hit:
            continue
        target_raw = hit.group("target")
        slug = ""
        link = re.search(r"\[\[([^|\]]+)\|?[^\]]*\]\]", target_raw)
        if link:
            slug = link.group(1)
        raw = hit.group("props")
        # 分隔符识别：`；` 一定分隔符；`，`/`,` **仅当**后面跟 `前缀:名=` 才算分隔符
        # —— 否则 `crudKind=C,U` 这种"值里带逗号"会被拆成 `C` 与 `U`（2026-09-21 实测丢值）。
        parts = re.split(r"[；;]|(?<=.)[，,](?=[A-Za-z_][\w\-]*:[^\s=]+=)", raw)
        props = {}
        for pair in parts:
            if "=" in pair:
                key, _, value = pair.partition("=")
                props[key.strip()] = value.strip()
        out.append({"line_index": idx, "label": hit.group("label"), "type": hit.group("type"),
                    "target": re.sub(r"\[\[[^\]]*\]\]", "", target_raw).strip(), "slug": slug,
                    "properties": props})
    return out


def _load_page(kb_id: str, slug: str) -> dict:
    rows = ke_db.psql_csv(
        "SELECT id, slug, title, COALESCE(page_type,'') AS page_type, COALESCE(content,'') AS content, "
        "       COALESCE(version,1) AS version, COALESCE(in_links::text,'[]') AS in_links, "
        # page_metadata 必须带上：set_page_type 会**合并**它（丢了就会把 ontology.name /
        # created_at / 合并历史等键清空）。
        "       COALESCE(page_metadata::text,'{}') AS page_metadata, "
        "       COALESCE(last_editor_id,'') AS editor "
        "FROM wiki_pages WHERE knowledge_base_id = %s AND slug = %s AND deleted_at IS NULL"
        % (ke_db.sql_str(kb_id), ke_db.sql_str(slug)))
    if not rows:
        raise ValueError("页面不存在或已删除：%s" % slug)
    row = dict(rows[0])
    row["version"] = int(row.get("version") or 1)
    return row


def _section_span(lines: list[str]) -> tuple[int, int]:
    """返回「## 本体关系」小节的 (小节标题行, 结束行=下一个 ## 或文件尾)。"""
    start = next((i for i, line in enumerate(lines) if line.strip() == REL_SECTION), -1)
    if start < 0:
        return -1, -1
    end = next((i for i in range(start + 1, len(lines)) if lines[i].startswith("## ")), len(lines))
    return start, end


def parse_out_relations(content: str) -> list[dict]:
    """解析正文里的出边（保持行序，便于原地替换/删除）。"""
    lines = (content or "").splitlines()
    start, end = _section_span(lines)
    if start < 0:
        return []
    out = []
    for idx in range(start + 1, end):
        hit = parse_rel_line(lines[idx].strip())
        if hit:
            out.append({"line_index": idx, "label": hit.group("label"), "type": hit.group("type"),
                        "slug": hit.group("slug"), "target": hit.group("target"),
                        "raw": lines[idx]})
    return out


def out_links_of(content: str) -> list[str]:
    """【已废弃 2026-10-05 M3】关系只走 Neo4j 图，不再从正文派生 out_links。"""
    return sorted({r["slug"] for r in parse_out_relations(content) if r["slug"]})


def rebuild_in_links_sql(kb_id: str) -> str:
    """【已废弃 2026-10-05 M3】in_links/out_links 两列恒空，无需重算。仅保留兼容旧引用。"""
    return ("UPDATE wiki_pages SET in_links = '[]'::jsonb "
            " WHERE knowledge_base_id = %s AND deleted_at IS NULL;\n" % ke_db.sql_str(kb_id))


# ---------------------------------------------------------------------------
# 副本写保护（2026-09-29 用户口径）：同义知识的副本页**不能本地修改**，只能从权威复制。
#   命中来源 = 映射缓存 `state/context_map/mappings.json` 的 `replica_protect`
#   （由 ke_context.rebuild_cache 从概念页「## 权威与副本」+ 各副本页自述派生）
# ---------------------------------------------------------------------------
CXT_PULL_TAG = "bodhi-cxt-pull"          # 唯一允许改副本正文的 tag（authority_pull 用）


def replica_guard(kb_id: str, slug: str, tag: str) -> None:
    """该页若是权威副本且不是 pull 通道 → 抛错并给"从权威复制"的指引（服务端强约束）。"""
    if tag == CXT_PULL_TAG:
        return
    try:
        import json as _json
        import pathlib as _pathlib
        cache = _pathlib.Path(__file__).resolve().parents[2] / "state" / "context_map" / "mappings.json"
        data = _json.loads(cache.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return
    hit = (data.get("replica_protect") or {}).get("%s:%s" % (kb_id, slug))
    if not hit:
        return
    raise ValueError(
        "该页是**权威副本**（replica；权威=%s/%s）→ 领域库不能本地修改，只能从权威复制："
        "`ke_context.py authority-pull %s --kb <本库> --apply`（带 ticket + 风险确认）"
        % (str(hit.get("master_kb") or "")[:8], hit.get("master_slug") or "",
           hit.get("concept_slug") or slug))


def _snapshot_stmt(kb_id: str, slug: str, tag: str) -> str:
    """把当前版本快照进 wiki_page_revisions（沿用 server.py 的合并语义）。"""
    return ("INSERT INTO wiki_page_revisions (id, tenant_id, knowledge_base_id, page_id, slug, version, "
            "       title, page_type, status, content, summary, aliases, edit_source, editor_id, "
            "       edited_at, created_at)\n"
            "SELECT gen_random_uuid()::text, tenant_id, knowledge_base_id, id, slug, version, "
            "       title, page_type, status, content, summary, aliases, '%s', "
            "       COALESCE(last_editor_id,''), now(), now()\n"
            "  FROM wiki_pages WHERE knowledge_base_id = %s AND slug = %s AND deleted_at IS NULL;"
            % (tag, ke_db.sql_str(kb_id), ke_db.sql_str(slug)))


def _apply_content_update(kb_id: str, slug: str, content: str, tag: str,
                          extra_set: str = "") -> str:
    """快照 + 更新正文（**先图后 wiki**，2026-10-05 M3 统一写路径）：
    ① 先抽取正文里的本体关系；② 正文去掉「本体关系/被引用」小节；③ 出入链置空（不再维护）；
    ④ 落库后把关系投影到图实例（失败不阻断写库）。"""
    replica_guard(kb_id, slug, tag)      # 副本页禁止本地修改（pull 通道除外）
    import ke_graph  # noqa: PLC0415  懒导入避免 ke_pages ↔ ke_graph 环
    edges = [(r["type"], r["slug"]) for r in parse_out_relations(content) if r.get("slug")]
    content = ke_graph.strip_relation_sections(content)
    stmts = [_snapshot_stmt(kb_id, slug, tag),
             "UPDATE wiki_pages SET content = %s, out_links = '[]'::jsonb, in_links = '[]'::jsonb, "
             "version = version + 1, updated_at = now(), last_edit_source = '%s'%s "
             "WHERE knowledge_base_id = %s AND slug = %s AND deleted_at IS NULL;"
             % (ke_db.sql_str(content), tag, extra_set, ke_db.sql_str(kb_id), ke_db.sql_str(slug))]
    ke_db.psql("BEGIN;\n" + "\n".join(stmts) + "\nCOMMIT;\n", stdin=True)
    try:
        ke_graph.project_page(kb_id, slug, edges=edges)
    except Exception:  # noqa: BLE001  图投影失败不阻断写库（巡检/回填兜底）
        pass
    return content


# ---------------------------------------------------------------------------
# 读数：出边 + 入边
# ---------------------------------------------------------------------------
# 2026-10-05 M3：`page_relations` 已删（关系只存图，读走 `ke_graph.relations_of`）。


# ---------------------------------------------------------------------------
# 关系维护：新增 / 修改 / 删除（2026-10-05 M3：直写图实例，见 `ke_graph`）
# ---------------------------------------------------------------------------
def _allowed(page_type: str) -> dict[str, dict]:
    return ke_ontology.relation_type_map(page_type)


def _require_type(allowed: dict[str, dict], rel_type: str, page_type: str) -> dict:
    if rel_type not in allowed:
        raise ValueError(
            "关系类型 `%s` 不适用于 %s（该类型可用：%s）"
            % (rel_type, page_type, "、".join(sorted(allowed)[:12]) or "无"))
    return allowed[rel_type]


def _with_line_inserted(content: str, new_line: str) -> str:
    lines = (content or "").splitlines()
    start, end = _section_span(lines)
    if start < 0:
        lines += ["", REL_SECTION, "", new_line, ""]
        return "\n".join(lines).rstrip() + "\n"
    insert_at = end
    while insert_at > start + 1 and not lines[insert_at - 1].strip():
        insert_at -= 1
    lines.insert(insert_at, new_line)
    return "\n".join(lines).rstrip() + "\n"


def sync_folders(kb_id: str) -> dict:
    """重建该 KB 的 wiki 目录树（幂等；`delete_pages` 内部也用它）。"""
    return _sync_folders(kb_id)


def _uniq_str(seq) -> list:
    """去重且保序的字符串列表（`source_refs`/`chunk_refs` 用；顺带 strip/丢空）。"""
    out: list = []
    seen: set = set()
    for x in (seq or []):
        s = str(x).strip()
        if s and s not in seen:
            seen.add(s)
            out.append(s)
    return out


def upsert_page(kb_id: str, slug: str, title: str, page_type: str, content: str,
                summary: str = "", tag: str = "bodhi-cxt-edit",
                metadata: dict | None = None, sync_folders: bool = True,
                source_refs: list | None = None, chunk_refs: list | None = None,
                preloaded: dict | None = None) -> dict:
    """**建或改**一页（概念页/映射页专用；2026-09-28）。

    - 已存在 → 走 `_apply_content_update`（快照旧版 → content/version+1），
      并把 `title/summary/page_type/page_metadata` 一起更新（可回退到旧版本）；
    - 不存在 → INSERT（`id` 用 `gen_random_uuid()`；`tenant_id` 取该 KB 的），随后建目录树；
    - 只用于**我们自己的治理页**（「企业共享概念模型」里的概念页/映射页）；领域业务页禁止走这里。
    - `sync_folders=False`（2026-10-09 P0）：**不在本页尾部重建目录树** —— 旧实现**每页**都
      全量重建（还起子进程 `sync_folders.py`），批量写 40 页 = 40 次全库目录重建，
      是 knowledge_save 2 分钟超时的最大块开销。批量写路径由调用方**整批做一次**
      （或交给前端 `/bodhi/folders/refresh` 按需刷新）。
    - `source_refs` / `chunk_refs`（2026-10-09 补**内核缺口**）：巡检 C1/C3「无来源」看的
      就是这两列，而旧内核**根本没写**（走内核写的页必然被报"无来源"）。语义：
      · INSERT → 直接写入给定列表（`[]` 也行）；
      · UPDATE → 与既有值**并集**（不丢已继承的来源；要给空请显式传 `[]` 也只会"并空"，不会清空）。
    """
    if len(tag) > 16:
        raise ValueError("tag 超过 last_edit_source 的 varchar(16)：%s" % tag)
    # P3-0d（2026-10-10）：把这页"已构建好"的正文与数据属性**回传**给调用方，
    # 供批量路径用**一条 UNWIND**（`ke_graph.upsert_nodes_batch`）统一写图节点，
    # 替掉"每节点 1 次 Neo4j"（40 节点 ≈ 3 s）。`_wiki_content_for_graph` 用**剥离关系小节前**的原文，
    # 与旧 `_kg.upsert_node(wiki_content=…)` 逐字一致。
    _wiki_content_for_graph = content
    _attrs_for_graph = dict((metadata or {}).get("ontology", {}).get("attributes") or {})
    if preloaded is not None:
        # 批量路径（2026-10-10 P3-0c）：调用方已用 `page_info_map` **一次**预读全批，
        # 这里免掉"每页 1 次存在性查询"（40 页 ≈ 40×272ms ≈ 11s，本机）。
        existing = ([{"version": int(preloaded.get("version") or 1)}]
                    if preloaded.get("exists") else [])
    else:
        existing = ke_db.psql_csv(
            "SELECT COALESCE(version,1) AS version FROM wiki_pages "
            " WHERE knowledge_base_id = %s AND slug = %s AND deleted_at IS NULL LIMIT 1"
            % (ke_db.sql_str(kb_id), ke_db.sql_str(slug)))
    extra = (", title = %s, summary = %s, page_type = %s, "
             "page_metadata = COALESCE(page_metadata, '{}'::jsonb) || %s::jsonb"
             % (ke_db.sql_str(title), ke_db.sql_str(summary), ke_db.sql_str(page_type),
                ke_db.sql_json(metadata or {})))
    # 来源列（2026-10-09 内核缺口修复）：UPDATE 走**并集**，绝不丢已继承的来源。
    if source_refs is not None:
        extra += (", source_refs = COALESCE((SELECT jsonb_agg(DISTINCT v) FROM "
                  "jsonb_array_elements_text(COALESCE(source_refs,'[]'::jsonb) || %s::jsonb) AS t(v)),"
                  " '[]'::jsonb)" % ke_db.sql_json(_uniq_str(source_refs)))
    if chunk_refs is not None:
        extra += (", chunk_refs = COALESCE((SELECT jsonb_agg(DISTINCT v) FROM "
                  "jsonb_array_elements_text(COALESCE(chunk_refs,'[]'::jsonb) || %s::jsonb) AS t(v)),"
                  " '[]'::jsonb)" % ke_db.sql_json(_uniq_str(chunk_refs)))
    if existing:
        before = int(existing[0]["version"] or 1)
        _apply_content_update(kb_id, slug, content, tag, extra_set=extra)
        if sync_folders:
            _sync_folders(kb_id)
        return {"slug": slug, "created": False, "before_version": before, "after_version": before + 1,
                "content": _wiki_content_for_graph, "attrs": _attrs_for_graph}
    if preloaded is not None and preloaded.get("tenant_id") is not None:
        # 批量路径：租户也来自预读（免掉"每新建页 1 次 knowledge_bases 查询"）
        kb = [{"tenant_id": preloaded["tenant_id"]}]
    else:
        kb = ke_db.psql_csv("SELECT COALESCE(tenant_id,0) AS tenant_id FROM knowledge_bases WHERE id = %s"
                            % ke_db.sql_str(kb_id))
    if not kb:
        raise ValueError("知识库不存在：%s" % kb_id)
    # 本体分类目录（2026-09-29 用户口径：概念库也要「和领域模型一样有本体分类目录」）：
    #   `category_path` 由本体类推导（大类链），`wiki_path` 供前端目录树显示 —— 全部**派生**，不靠人维护。
    try:
        import json as _json
        cat = ke_ontology.category_path(page_type) or []
    except Exception:  # noqa: BLE001
        cat = []
    cols = ("id, tenant_id, knowledge_base_id, slug, title, page_type, content, summary, "
            "out_links, page_metadata, version, last_edit_source, category_path, depth, wiki_path, "
            "source_refs, chunk_refs")
    # 2026-10-05 M3：统一写路径——INSERT 也「先图后 wiki」（抽关系→去正文关系小节→出入链置空→投影图）
    import ke_graph as _kg
    edges = [(r["type"], r["slug"]) for r in parse_out_relations(content) if r.get("slug")]
    content = _kg.strip_relation_sections(content)
    vals = ("gen_random_uuid()::text, %d, %s, %s, %s, %s, %s, %s, '[]'::jsonb, %s::jsonb, 1, %s, "
            "%s::jsonb, %d, %s, %s::jsonb, %s::jsonb"
            % (int(kb[0]["tenant_id"] or 0), ke_db.sql_str(kb_id), ke_db.sql_str(slug),
               ke_db.sql_str(title), ke_db.sql_str(page_type), ke_db.sql_str(content),
               ke_db.sql_str(summary), ke_db.sql_json(metadata or {}),
               ke_db.sql_str(tag), ke_db.sql_json(cat), len(cat),
               ke_db.sql_str("/".join([str(x) for x in cat] + [title])),
               ke_db.sql_json(_uniq_str(source_refs)),
               ke_db.sql_json(_uniq_str(chunk_refs))))
    ke_db.psql("BEGIN;\nINSERT INTO wiki_pages (%s) VALUES (%s);\nCOMMIT;\n" % (cols, vals), stdin=True)
    if sync_folders:
        _sync_folders(kb_id)
    try:
        _kg.project_page(kb_id, slug, edges=edges)
    except Exception:  # noqa: BLE001
        pass
    return {"slug": slug, "created": True, "before_version": 0, "after_version": 1,
            "category_path": cat,
            "content": _wiki_content_for_graph, "attrs": _attrs_for_graph}


# ---------------------------------------------------------------------------
# 知识写入契约内核（2026-10-08 · docs/knowledge-write-contract.md）
#   三种展示模式：document（报告/文档）| entity（一般本体知识）| raw（存量兼容）
#   统一溯源：L1 session(对象属性,图边) / L2 locator(数据属性) / L3 文档(原文依据) / L4 页面(溯源)
#   图本优先：① 图节点/属性 → ② 图边 → ③ PG 页 → ④ 溯源
# ---------------------------------------------------------------------------
# 渲染模式**只有两种**（2026-10-09 用户口径）：由**本体**决定（见 `ke_ontology.render_mode`），
# 调用方不必自己选。`raw` 只作 `document` 的**旧别名**保留（存量调用兼容），不再出现在清单里。
CONTRACT_MODES = ("entity", "document")
MODE_ALIASES = {"raw": "document"}
# 属性值渲进正文的**字数上限**：`0` = 不截断（2026-10-09 用户口径：正文里属性值不再限 150 字，
# 完整内容本来就落在「数据属性」面板 + 图节点属性里，正文再截断只会让模型/人看到半句话）。
ATTR_RENDER_LIMIT = 0
TAG_CONTRACT = "bodhi-write"                 # 11 字符（≤16）
EVIDENCE_SECTION = "## 原文依据"


def _derive_slug(page_type: str, title: str) -> str:
    mod, _, cls = (page_type or "").partition(":")
    cls = (cls or page_type or "item").lower()
    safe = re.sub(r"[\\/\s]+", "-", str(title or "").strip()).strip("-") or "untitled"
    return "%s/%s/%s" % (mod or "kb", cls, safe)


def _attr_meta(page_type: str, key: str) -> dict:
    """取数据属性的本体元数据（兼容 keys 带/不带模块前缀两种口径）。"""
    try:
        props = ke_ontology.data_properties_for(page_type) or {}
    except Exception:  # noqa: BLE001
        return {}
    raw = str(key)
    local = raw.split(":")[-1]
    for cand in (raw, local):
        if cand in props:
            return props[cand] or {}
    for k, v in props.items():
        if str(k).split(":")[-1] == local:
            return v or {}
    return {}


def render_attributes_section(page_type: str, attributes: dict, skip_keys=()) -> list[str]:
    """§4：数据属性 → `## 属性（数据属性）` + `### 名称（prefixed，range）`\\n<值>；值 >150 字截断。"""
    skip = {str(k).split(":")[-1] for k in (skip_keys or [])}
    rows: list[str] = []
    for key, value in (attributes or {}).items():
        short = str(key).split(":")[-1]
        if short in skip:
            continue
        val = str(value if value is not None else "").strip()
        if not val:
            continue
        meta = _attr_meta(page_type, key)
        label = meta.get("label") or ""
        rng = ke_ontology.short_iri(str(meta.get("range_literal")
                                        or (meta.get("ranges") or [""])[0] or "")) if meta else ""
        title = ("%s（%s%s）" % (label, key, ("，%s" % rng) if rng else "")).strip() if label else str(key)
        if ATTR_RENDER_LIMIT and len(val) > ATTR_RENDER_LIMIT:
            val = val[:ATTR_RENDER_LIMIT].rstrip() + "……（完整内容见「数据属性」面板）"
        rows += ["### %s" % title, "", val, ""]
    return (["## 属性（数据属性）", ""] + rows) if rows else []


def _contract_head(spec: dict) -> list[str]:
    """统一页头（标题 + 本体类型/来源/会话 引用块）。"""
    title, pt = spec["title"], spec["page_type"]
    src = dict(spec.get("source") or {})
    lines = ["# %s（`%s`）" % (title, pt), "",
             "> **本体类型**：%s（`%s`）  " % (spec.get("type_label") or pt, pt)]
    if src.get("doc_title") or src.get("doc_refs"):
        lines.append("> **来源**：《%s》  " % (src.get("doc_title") or "（来源文档）"))
    elif spec.get("generated_by"):
        lines.append("> **生成方式**：%s  " % spec["generated_by"])
    if src.get("session_no"):
        lines.append("> **来源会话**：%s  " % src["session_no"])
    lines.append("> **首个版本生成**：%s" % ke_db.now_text())
    lines.append("")
    return lines


def render_entity_content(kb_id: str, spec: dict) -> str:
    """M-entity：由数据属性**自动生成**正文（首段=定义属性；属性段不重复该键）。"""
    attrs = dict(spec.get("attributes") or {})
    dkey = spec.get("definition_key", "definition")
    first = spec.get("first_paragraph_from", dkey)
    first_text = ""
    if first:
        fshort = str(first).split(":")[-1]
        for k, v in attrs.items():
            if str(k).split(":")[-1] == fshort:
                first_text = str(v or "").strip()
                break
    lines = _contract_head(spec)
    if first_text:
        lines += [first_text, ""]
    lines += render_attributes_section(spec["page_type"], attrs, skip_keys=[dkey] if first_text else [])
    src = dict(spec.get("source") or {})
    lines += [EVIDENCE_SECTION, "",
              "- %s%s" % (src.get("source_text") or "（未提供逐字原文）",
                          ("；定位：%s" % src["locator"]) if src.get("locator") else ""), ""]
    return "\n".join(lines).rstrip() + "\n"


SESSION_CLASS = "bmm:KnowledgeSession"


def ensure_session_page(kb_id: str, session_no: str, *, session_name: str = "",
                        agent_name: str = "", role: str = "", initial_question: str = "",
                        tenant_id: int | None = None) -> dict:
    """**核心会话页机制**（契约 §5.1 · 2026-10-09）：确保「会话页」存在；**智能体/角色切换 → 新建分页**。

    规则（用户口径）：
    - 会话页**没有** → **创建**（起始页 `partNo=1`）；
    - 会话页**已有**、且**场景指纹**（agent+role）与某分页一致 → **复用**该分页；
    - 场景变了（**换智能体 / 换角色**）→ 建**新分页** `session/<no>/p<n>`，并在起始页「## 分页索引」登记。

    这些都在**核心**做；外围智能体只要把 `session_no` + 场景（agent/role）传进来。
    返回 `{slug, part_no, created, paginated}`（供内核写 `bmm:sourceSession` 边）。
    """
    import json  # noqa: PLC0415
    sno = str(session_no or "").strip()
    if not sno:
        return {"slug": "", "created": False, "paginated": False, "reason": "缺 session_no"}
    fp = "|".join([str(agent_name or "").strip(), str(role or "").strip()]).strip("|")
    rows = ke_db.psql_csv(
        "SELECT slug, COALESCE(page_metadata::text,'{}') AS md FROM wiki_pages "
        "WHERE knowledge_base_id=%s AND page_type=%s AND deleted_at IS NULL "
        "AND (content LIKE %s OR slug LIKE %s) ORDER BY length(slug) ASC"
        % (ke_db.sql_str(kb_id), ke_db.sql_str(SESSION_CLASS),
           ke_db.sql_str("%" + sno + "%"), ke_db.sql_str("session/" + sno + "%")))

    def _fp_of(md_text):
        try:
            attrs = ((json.loads(md_text or "{}").get("ontology") or {}).get("attributes") or {})
        except Exception:  # noqa: BLE001
            attrs = {}
        return "|".join([str(attrs.get("agentName") or attrs.get("bmm:agentName") or "").strip(),
                         str(attrs.get("role") or attrs.get("bmm:role") or "").strip()]).strip("|")

    base = rows[0]["slug"] if rows else ""          # 最短 = 起始页
    for r in rows:
        if _fp_of(r["md"]) == fp:
            return {"slug": r["slug"], "part_no": _part_no(r["slug"]), "created": False,
                    "paginated": False}
    # 需要新建：起始页（没有）或新分页（场景变了）
    part_no = 1 if not base else (max([_part_no(x["slug"]) for x in rows] or [1]) + 1)
    slug = ("session/%s" % sno) if part_no == 1 else ("session/%s/p%d" % (sno, part_no))
    title = (session_name.strip() if (part_no == 1 and session_name) else
             ("会话分页 p%d · %s" % (part_no, session_name or sno)))
    attrs = {"bmm:sessionNo": sno, "bmm:sessionName": session_name or title,
             "bmm:sessionStatus": "进行中", "bmm:isAuthoritative": True,
             "bmm:partNo": str(part_no), "bmm:startedAt": ke_db.now_text()}
    if agent_name:
        attrs["bmm:agentName"] = agent_name
    if role:
        attrs["bmm:role"] = role
    if initial_question:
        attrs["bmm:initialQuestion"] = initial_question
    head = ["# %s（`%s`）" % (title, SESSION_CLASS), "",
            "> **本体类型**：知识会话（`%s`）  " % SESSION_CLASS,
            "> **来源会话**：%s  " % sno, ""]
    if part_no == 1 and initial_question:
        head += ["%s" % initial_question, ""]
    head += render_attributes_section(SESSION_CLASS, attrs)
    if part_no > 1 and base:                     # 起始页登记分页索引
        _register_session_part(kb_id, base, slug, title)
    upsert_page(kb_id, slug, title, SESSION_CLASS, "\n".join(head).rstrip() + "\n",
                summary=(initial_question or title)[:200], tag=TAG_CONTRACT,
                metadata={"ontology": {"model": "bmm", "class": SESSION_CLASS, "name": title,
                                       "attributes": attrs, "generator": TAG_CONTRACT}})
    return {"slug": slug, "part_no": part_no, "created": (part_no == 1), "paginated": (part_no > 1)}


def _part_no(slug: str) -> int:
    m = re.search(r"/p(\d+)$", slug or "")
    return int(m.group(1)) if m else 1


def _register_session_part(kb_id: str, base_slug: str, part_slug: str, part_title: str) -> None:
    """在会话起始页的「## 分页索引」追加一条（幂等）。"""
    row = ke_db.psql_csv("SELECT COALESCE(content,'') AS c FROM wiki_pages WHERE knowledge_base_id=%s "
                         "AND slug=%s AND deleted_at IS NULL" % (ke_db.sql_str(kb_id), ke_db.sql_str(base_slug)))
    if not row:
        return
    c = row[0]["c"]
    if part_slug in c:
        return
    line = "- %s · %s" % (part_slug, part_title)
    if "## 分页索引" in c:
        c = c.rstrip() + "\n" + line + "\n"
    else:
        c = c.rstrip() + "\n\n## 分页索引\n" + line + "\n"
    ke_db.psql("UPDATE wiki_pages SET content=%s, version=version+1, updated_at=now() "
               "WHERE knowledge_base_id=%s AND slug=%s;"
               % (ke_db.sql_str(c), ke_db.sql_str(kb_id), ke_db.sql_str(base_slug)), stdin=True)


def write_knowledge(kb_id: str, spec: dict, *, dry_run: bool = False,
                    strict_source: bool = True, sync_folders: bool = True,
                    preloaded: dict | None = None, skip_graph: bool = False) -> dict:
    """**知识写入统一内核**（契约 §1/§2/§8）。所有写入器都应薄封装本函数。

    `spec` 见 docs/knowledge-write-contract.md §2。`strict_source=True`（默认）时，
    缺「会话 / 文档」溯源 → **拒写**（fail-closed）。
    `sync_folders=False`（2026-10-09 P0）：本页写完后**不重建整库目录树**（批量路径用，
    避免「每页一次全量重建」把批量写拖成分钟级）。**单个**知识写入保持默认 `True`。
    """
    import ke_graph as _kg  # noqa: PLC0415  懒导入避免环
    spec = dict(spec or {})
    title = (spec.get("title") or "").strip()
    page_type = (spec.get("page_type") or "").strip()
    mode = (spec.get("mode") or "").strip().lower()
    if not title or not page_type:
        raise ValueError("write_knowledge 需要 title 与 page_type")
    if not mode:
        # 2026-10-09 用户口径：**渲染模式由本体决定**（只有 entity / document 两种），
        # 调用方不自己选 —— 要按文档渲染就在 TTL 给该类声明 `bodhi:renderMode "document"`。
        mode = ke_ontology.render_mode(page_type)
    mode = MODE_ALIASES.get(mode, mode)          # `raw` 旧别名 → document
    if mode not in CONTRACT_MODES:
        raise ValueError("mode 只能是 %s（旧别名 %s 也接受）"
                         % ("/".join(CONTRACT_MODES), "/".join(MODE_ALIASES)))
    src = dict(spec.get("source") or {})
    has_session = bool(src.get("session_slug") or src.get("session_no"))
    has_doc = bool(src.get("doc_refs") or src.get("source_text") or src.get("chunk_refs"))
    if strict_source and not (has_session or has_doc):
        raise ValueError("严格溯源（契约 §5）：source 需含会话(session_no/session_slug)或"
                         "文档(doc_refs/source_text/chunk_refs) 其一；否则拒写")
    slug = (spec.get("slug") or "").strip() or _derive_slug(page_type, title)
    attrs = dict(spec.get("attributes") or {})
    dkey = spec.get("definition_key", "definition")
    render_attrs = spec.get("render_attributes")
    if render_attrs is None:
        render_attrs = (mode == "entity")
    if mode == "entity":
        content = render_entity_content(kb_id, {**spec, "slug": slug, "attributes": attrs})
    else:
        content = spec.get("wiki_content")
        if not content:
            raise ValueError("mode=%s 需要 wiki_content" % mode)
        if mode == "document":
            content = "\n".join(_contract_head({**spec, "slug": slug})) + "\n" + content.lstrip()
        if render_attrs:
            content = content.rstrip() + "\n\n" + "\n".join(
                render_attributes_section(page_type, attrs, skip_keys=[dkey])) + "\n"
    if src.get("locator") and "sourceLocator" not in {str(k).split(":")[-1] for k in attrs}:
        attrs["bmm:sourceLocator"] = src["locator"]     # L2：片段定位（数据属性）
    metadata = {"ontology": {"model": page_type.split(":", 1)[0], "class": page_type,
                             "name": title, "attributes": attrs, "generator": TAG_CONTRACT}}
    for k, v in (spec.get("metadata_extra") or {}).items():     # 如 review 页的 `page_metadata.review`
        if k != "ontology":
            metadata[k] = v
    if src.get("derived_from"):
        metadata["design"] = {"derived_from": [s for s in src["derived_from"] if s],
                              "generator": TAG_CONTRACT}
    warnings: list[str] = []
    if not has_session:
        warnings.append("无会话溯源（source.session_no/session_slug 缺）：报告/文档类建议补会话页")
    if dry_run:
        return {"applied": False, "kb_id": kb_id, "slug": slug, "title": title, "mode": mode,
                "graph": {"dry_run": True, "edges_written": 0, "edge_errors": []},
                "warnings": warnings, "content_preview": content[:600]}
    graph = {"ok": True, "edges_written": 0, "edge_errors": []}
    # ① 节点先入图（图本优先，契约 §8）
    #    `skip_graph=True`（批量路径 P3-0d，2026-10-10）：图节点由调用方用**一条 UNWIND**
    #    （`ke_graph.upsert_nodes_batch`）统一写 —— 免掉"每节点 1 次 Neo4j"（40 节点 ≈ 3 s）。
    if skip_graph:
        graph["deferred"] = True
    else:
        try:
            _kg.upsert_node(kb_id, slug, title, page_type, attributes=attrs, wiki_content=content)
        except Exception as exc:  # noqa: BLE001
            graph["ok"] = False
            graph["reason"] = "图节点写入失败：%s" % str(exc)[:180]
    # ② 节点落 PG（建/合并页）—— **关系校验（domain/range、目标页存在）要读 PG 的 `page_type`**，
    #    所以必须在建边之前完成：否则源/目标页未落库 → add_edge 报「目标页不存在」（2026-10-09 修）。
    # 溯源列（契约 §5，2026-10-09 补内核缺口）：文档来源 → `source_refs`；片段/分块 → `chunk_refs`。
    # 允许调用方用 `spec["source_refs"]/["chunk_refs"]` **显式覆盖**（如设计页继承上游页的文档来源）。
    # 不写这两列 → 巡检 C1/C3 判「无来源」，所以内核必须落。
    _doc_refs = spec.get("source_refs")
    if _doc_refs is None:
        _doc_refs = [str(x) for x in (src.get("doc_refs") or []) if str(x).strip()]
        if src.get("doc_id"):
            _doc_refs.append(str(src["doc_id"]))
    _chunk_refs = spec.get("chunk_refs")
    if _chunk_refs is None:
        _chunk_refs = [str(x) for x in (src.get("chunk_refs") or []) if str(x).strip()]
    res = upsert_page(kb_id, slug, title, page_type, content,
                      summary=(spec.get("summary") or "").strip()[:500], tag=TAG_CONTRACT,
                      metadata=metadata, sync_folders=sync_folders,
                      source_refs=_doc_refs, chunk_refs=_chunk_refs, preloaded=preloaded)
    # ③ 关系（节点已就绪）
    for rel in (spec.get("relations") or []):
        rt, ts = rel.get("type"), rel.get("target_slug")
        if not (rt and ts):
            continue
        try:
            _kg.add_edge(kb_id, slug, rt, ts)
            graph["edges_written"] += 1
        except Exception as exc:  # noqa: BLE001
            graph["edge_errors"].append({"type": rt, "target_slug": ts, "error": str(exc)[:160]})
    # ④ 会话溯源边（L1）—— **核心自动确保会话页**（没有则建；换智能体/角色→新建分页）。
    #    外围只需传 `source.session_no` + 场景（session_name/agent_name/role）。
    session_slug = (src.get("session_slug") or "").strip()
    if not session_slug and src.get("session_no"):
        try:
            _sp = ensure_session_page(kb_id, src["session_no"],
                                      session_name=src.get("session_name", ""),
                                      agent_name=src.get("agent_name", ""),
                                      role=src.get("role", ""),
                                      initial_question=src.get("initial_question", ""))
            session_slug = (_sp.get("slug") or "")
            if _sp.get("created") or _sp.get("paginated"):
                graph["session_page"] = {"slug": session_slug, "part_no": _sp.get("part_no"),
                                         "created": _sp.get("created"), "paginated": _sp.get("paginated")}
        except Exception as exc:  # noqa: BLE001
            warnings.append("会话页确保失败：%s" % str(exc)[:160])
    if session_slug:
        try:
            _kg._ensure_instance(kb_id, slug, page_type)
            _kg._ensure_instance(kb_id, session_slug, _kg._page_type(kb_id, session_slug))
            _kg._run("MATCH (a:BodhiInstance {kb_id:$kb, slug:$s}) "
                     "MATCH (b:BodhiInstance {kb_id:$kb, slug:$t}) "
                     "MERGE (a)-[r:`bmm:sourceSession`]->(b)",
                     {"kb": kb_id, "s": slug, "t": session_slug})
        except Exception as exc:  # noqa: BLE001
            warnings.append("会话溯源边写入失败：%s" % str(exc)[:160])
    return {"applied": True, "kb_id": kb_id, "slug": slug, "title": title, "mode": mode,
            "created": res.get("created"), "version": res.get("after_version"),
            "graph": graph, "warnings": warnings,
            # P3-0d：回传"已构建好的"正文/属性，供批量路径一条 UNWIND 写图节点
            "content": res.get("content"), "attrs": res.get("attrs")}


def _bulk_write_pages(kb_id: str, items: list, tenant_id, tag: str) -> dict:
    """**PG 批量写（P3-0e，2026-10-10）**：替掉"每页 1 个事务"（40 页 ≈ 40×272ms ≈ 11 s）。

    三个语句、**两次往返**（新建一条事务；更新一条事务）：
      ① **新建**：**一条多行 `INSERT INTO wiki_pages … VALUES (…),(…)`**（`version=1`、`out_links='[]'`）；
      ② **更新**：`BEGIN;` → **一条快照 `INSERT INTO wiki_page_revisions … SELECT … FROM wiki_pages
         WHERE slug IN (…)`**（保留"每页留一份旧版"语义）→ **一条
         `UPDATE wiki_pages SET … FROM (VALUES …) AS v(…) WHERE p.slug = v.slug`**
         （`version = version+1`、`page_metadata` 用 `||` 合并、来源列走**并集去重**）→ `COMMIT;`。

    `items` 每项：`{slug,title,page_type,content,summary,metadata,category_path,wiki_path,
    source_refs,chunk_refs,exists}`；`exists` 由 `page_info_map` 一次预读给出（**不再逐页查**）。
    与逐页 `upsert_page` **语义对齐**；区别：**不投影图**（图由批量路径自己写）、不做副本写保护
    （`replica_guard`，逐页版才有）—— 因此本函数默认**关闭**，待 P0-c 接真实库做 diff 后再开。
    """
    news = [x for x in items if not x.get("exists")]
    olds = [x for x in items if x.get("exists")]
    out = {"inserted": 0, "updated": 0}
    if news:
        cols = ("id, tenant_id, knowledge_base_id, slug, title, page_type, content, summary, "
                "out_links, page_metadata, version, last_edit_source, category_path, depth, "
                "wiki_path, source_refs, chunk_refs")
        rows = ", ".join(
            "(gen_random_uuid()::text, %d, %s, %s, %s, %s, %s, %s, '[]'::jsonb, %s::jsonb, 1, "
            "%s, %s::jsonb, %d, %s, %s::jsonb, %s::jsonb)"
            % (int(tenant_id or 0), ke_db.sql_str(kb_id), ke_db.sql_str(x["slug"]),
               ke_db.sql_str(x["title"]), ke_db.sql_str(x["page_type"]), ke_db.sql_str(x["content"]),
               ke_db.sql_str(x.get("summary") or ""), ke_db.sql_json(x.get("metadata") or {}),
               ke_db.sql_str(tag), ke_db.sql_json(x.get("category_path") or []),
               len(x.get("category_path") or []), ke_db.sql_str(x.get("wiki_path") or ""),
               ke_db.sql_json(_uniq_str(x.get("source_refs"))),
               ke_db.sql_json(_uniq_str(x.get("chunk_refs")))) for x in news)
        ke_db.psql("BEGIN;\nINSERT INTO wiki_pages (%s) VALUES %s;\nCOMMIT;\n" % (cols, rows),
                   stdin=True)
        out["inserted"] = len(news)
    if olds:
        # 快照：逐条 `_snapshot_stmt` 拼（与单页版**逐字一致**）；与下面的 UPDATE 同一事务
        snap = "\n".join(_snapshot_stmt(kb_id, x["slug"], tag) for x in olds)
        vals = ", ".join(
            "(%s, %s, %s, %s, %s::jsonb, %s, %s::jsonb, %d, %s, %s::jsonb, %s::jsonb)"
            % (ke_db.sql_str(x["slug"]), ke_db.sql_str(x["content"]),
               ke_db.sql_str(x["title"]), ke_db.sql_str(x.get("summary") or ""),
               ke_db.sql_json(x.get("metadata") or {}), ke_db.sql_str(tag),
               ke_db.sql_json(x.get("category_path") or []), len(x.get("category_path") or []),
               ke_db.sql_str(x.get("wiki_path") or ""),
               ke_db.sql_json(_uniq_str(x.get("source_refs"))),
               ke_db.sql_json(_uniq_str(x.get("chunk_refs")))) for x in olds)
        upd = (
            "UPDATE wiki_pages p SET content = v.content, title = v.title, summary = v.summary, "
            "page_metadata = COALESCE(p.page_metadata, '{}'::jsonb) || v.metadata::jsonb, "
            "out_links = '[]'::jsonb, in_links = '[]'::jsonb, version = p.version + 1, "
            "updated_at = now(), last_edit_source = v.tag, category_path = v.cat::jsonb, "
            "depth = v.depth, wiki_path = v.wp, "
            "source_refs = COALESCE((SELECT jsonb_agg(DISTINCT e) FROM "
            "  jsonb_array_elements_text(COALESCE(p.source_refs,'[]'::jsonb) || v.refs::jsonb) AS t(e)), "
            "  '[]'::jsonb), "
            "chunk_refs = COALESCE((SELECT jsonb_agg(DISTINCT e) FROM "
            "  jsonb_array_elements_text(COALESCE(p.chunk_refs,'[]'::jsonb) || v.cr::jsonb) AS t(e)), "
            "  '[]'::jsonb) "
            "FROM (VALUES %s) AS v(slug, content, title, summary, metadata, tag, cat, depth, wp, "
            "refs, cr) "
            "WHERE p.knowledge_base_id = %s AND p.slug = v.slug AND p.deleted_at IS NULL;"
            % (vals, ke_db.sql_str(kb_id)))
        ke_db.psql("BEGIN;\n%s\n%s\nCOMMIT;\n" % (snap, upd), stdin=True)
        out["updated"] = len(olds)
    return out


def write_knowledge_batch(kb_id: str, specs: list, *, dry_run: bool = False,
                          strict_source: bool = True, sync_folders: bool = True) -> dict:
    """**批量写入**（契约 §7；2026-10-09 P0 性能改造）。

    三遍（**逐条小事务、失败可续作**）：
      ① **批量预读**：全批涉及的 slug（含关系目标）→ `{slug:{pt,title,tenant}}`（**1 次 psql**）；
      ② **节点**：逐个 `write_knowledge(..., sync_folders=False)` —— 每页各自一个事务；
         **不再逐页重建目录树**（旧实现每页一次全量重建 + 起子进程，是 2 分钟超时的最大块）；
      ③ **关系**：先 `ensure_instances()` **一次** UNWIND 批量确保节点，
         再逐条 `add_edge(ctx=…)` → **每条边 1 次 Neo4j**（旧实现 4 次 PG + 5 次 Neo4j ≈ 1.46 s）。

    实测基线（2026-10-09，本机）：1 次 psql 往返 ≈ 272 ms、1 次 Neo4j ≈ 74 ms。
    40 节点 + 40 关系：旧路径 ~150-250 次往返（>2 分钟，被客户端超时切断且无日志）；
    本实现 ≈ 40 次节点往返 + 40 次边往返 ≈ **15-25 s**，且失败只丢**当前那一条**。

    返回额外带 `applied_count / failed / resume_hint`（幂等：同 payload 重跑即续作）。
    """
    import ke_graph as _kg  # noqa: PLC0415
    specs = [dict(s or {}) for s in (specs or [])]
    pages, errors, prepared = [], [], []
    # ① 批量预读（本批 slug + 关系目标）——一次往返拿全 page_type，供 ③ 的 domain/range 校验
    want = [(s.get("slug") or "").strip() for s in specs]
    want += [(r.get("target_slug") or "").strip()
             for s in specs for r in (s.get("relations") or [])]
    info = _kg.page_info_map(kb_id, [x for x in want if x])
    # ② 节点：逐个事务（各自 fail-closed，互不牵连）
    #    P3-0c（2026-10-10）：全批已由 ① 预读 → 传 `preloaded` **免掉每页 2 次 psql**
    #    （存在性查询 + 新建页的租户查询；40 页 ≈ 80×272ms ≈ 21s，本机）。
    _kb_tenant = None
    if not dry_run:
        _t = ke_db.psql_csv("SELECT COALESCE(tenant_id,0) AS t FROM knowledge_bases WHERE id=%s"
                            % ke_db.sql_str(kb_id))
        _kb_tenant = (_t[0]["t"] if _t else None)
    _node_rows = []                      # P3-0d：收集"已构建好"的图节点载荷
    for spec in specs:
        rels = spec.pop("relations", None) or []
        _slug = (spec.get("slug") or "").strip() or _derive_slug(spec.get("page_type") or "",
                                                                spec.get("title") or "")
        _hit = info.get(_slug)
        _pre = {"exists": bool(_hit), "version": int((_hit or {}).get("version") or 1),
                "tenant_id": _kb_tenant if _kb_tenant is not None
                else ((_hit or {}).get("tenant") if _hit else None)}
        try:
            r = write_knowledge(kb_id, spec, dry_run=dry_run, strict_source=strict_source,
                                sync_folders=False, preloaded=_pre, skip_graph=not dry_run)
            pages.append(r)
            prepared.append((r, rels))
            slug = str(r.get("slug") or "")
            if slug:
                info.setdefault(slug, {"pt": (spec.get("page_type") or ""),
                                       "title": (spec.get("title") or ""), "tenant": _kb_tenant})
                if r.get("content") is not None:
                    _node_rows.append({"slug": slug, "name": (spec.get("title") or ""),
                                       "page_type": (spec.get("page_type") or ""),
                                       "tenant_id": _kb_tenant,
                                       "wiki_content": r.get("content"),
                                       "attrs": r.get("attrs") or {}})
        except Exception as exc:  # noqa: BLE001
            errors.append({"title": spec.get("title"), "error": str(exc)[:200]})
    edges_written, edge_errors = 0, []
    # ③-0 图节点：**一条 UNWIND** 统一写（P3-0d）——旧路径每节点 1 次 Neo4j（40 节点 ≈ 3 s）
    if not dry_run and _node_rows:
        try:
            _kg.upsert_nodes_batch(kb_id, _node_rows)
        except Exception as exc:  # noqa: BLE001
            edge_errors.append({"scope": "upsert_nodes_batch", "error": str(exc)[:160]})
    if not dry_run:
        # ③-1 一次 UNWIND：把全批节点（本批新建 + 关系目标）批量确保在图里
        if info:
            try:
                _kg.ensure_instances(kb_id, [
                    {"slug": k, "name": (v or {}).get("title") or k.rsplit("/", 1)[-1],
                     "page_type": (v or {}).get("pt") or "", "tenant_id": (v or {}).get("tenant")}
                    for k, v in info.items()])
            except Exception as exc:  # noqa: BLE001
                edge_errors.append({"scope": "ensure_instances", "error": str(exc)[:160]})
        # ③-2 **一次性批量建边**（P3-0）：全批汇总 → 按关系类型分组的 UNWIND
        #      （旧实现"每条边 1 次 Neo4j"：40 条边 = 40 次往返；现在 ≈ 关系类型数 次）
        ctx = {"info": info, "nodes_ensured": True, "skip_relations": True}
        triples = []
        for r, rels in prepared:
            for rel in rels:
                rt, ts = rel.get("type"), rel.get("target_slug")
                if rt and ts:
                    triples.append((r["slug"], rt, ts))
        if triples:
            out = _kg.add_edges_batch(kb_id, triples, ctx=ctx)
            edges_written = int(out.get("written") or 0)
            _errs = out.get("errors") or []
            edge_errors.extend(_errs)
            for r, rels in prepared:
                g = r.get("graph") if isinstance(r.get("graph"), dict) else {}
                _mine = [x for x in _errs if x.get("source") == r.get("slug")]
                _valid = len([1 for rel in rels if rel.get("type") and rel.get("target_slug")])
                g["edges_written"] = max(0, _valid - len(_mine))
                if _mine:
                    g.setdefault("edge_errors", []).extend(
                        [{"type": x.get("type"), "target_slug": x.get("target_slug"),
                          "error": x.get("error")} for x in _mine])
    else:
        edges_written = sum((p.get("graph") or {}).get("edges_written", 0) for p in pages)
    # ④ 目录树：**整批一次**（或交给前端 `/bodhi/folders/refresh` 按需刷新）
    folders = None
    if sync_folders and not dry_run and pages:
        try:
            folders = _sync_folders(kb_id)
        except Exception as exc:  # noqa: BLE001
            folders = {"ok": False, "error": str(exc)[:160]}
    return {"applied": not dry_run, "kb_id": kb_id, "pages": pages, "errors": errors,
            "count": len(pages), "edges_written": edges_written, "edge_errors": edge_errors,
            "applied_count": len(pages), "failed": errors,
            "folders": folders,
            "resume_hint": ("同 payload 重跑即可续作（节点按 (kb,slug)、关系按 (kb,源,类型,目标) 幂等；"
                            "已成功的会被判为 merged/已存在，不会重复建）。")}


def write_relations_batch(kb_id: str, pairs: list) -> dict:
    """**批量建关系**（2026-10-09 P0-b）：`pairs=[{"source","type","target"}]`（**均传 slug**）。

    为什么需要：`save_knowledge` 的设计/建模路径旧实现只把关系**渲染进页面正文**、**不写图**，
    于是出现「索引列表先出现、本体图谱要等一段时间才出现」（要等事后投影）。
    这里补上批量写图：① 一次 `page_info_map` 预读 ② 一次 `ensure_instances`
    ③ 逐条 `add_edge(ctx)` —— 每条边 **1-2 次 Neo4j**（旧 `add_edge` 是 4 次 PG + 5 次 Neo4j）。

    幂等：重复关系会被 `add_edge` 判为「已存在」→ 不报错、不重复建，所以**可安全重跑/续作**。
    """
    import ke_graph as _kg  # noqa: PLC0415
    rows = [{"source": str((p or {}).get("source") or "").strip(),
             "type": str((p or {}).get("type") or "").strip(),
             "target": str((p or {}).get("target_slug") or (p or {}).get("target") or "").strip()}
            for p in (pairs or [])]
    rows = [r for r in rows if r["source"] and r["type"] and r["target"]]
    if not rows:
        return {"ok": True, "written": 0, "total": 0, "errors": []}
    need = sorted({r["source"] for r in rows} | {r["target"] for r in rows})
    info = _kg.page_info_map(kb_id, need)
    try:
        _kg.ensure_instances(kb_id, [
            {"slug": k, "name": (v or {}).get("title") or k.rsplit("/", 1)[-1],
             "page_type": (v or {}).get("pt") or "", "tenant_id": (v or {}).get("tenant")}
            for k, v in info.items()])
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "written": 0, "total": len(rows),
                "errors": [{"scope": "ensure_instances", "error": str(exc)[:160]}]}
    ctx = {"info": info, "nodes_ensured": True, "skip_relations": True}
    # P3-0（2026-10-10）：改成**按关系类型分组的 UNWIND 批量**（N 条边 → 去重后类型数次往返）
    out = _kg.add_edges_batch(kb_id, [(r["source"], r["type"], r["target"]) for r in rows], ctx=ctx)
    return {"ok": bool(out.get("ok")), "written": int(out.get("written") or 0), "total": len(rows),
            "types": out.get("types"), "errors": out.get("errors") or []}


def rewrite_page_content(kb_id: str, slug: str, content: str, tag: str = TAG_OPS) -> dict:
    """按给定正文**重写一页**：快照旧版 → 更新 content/version+1。

    供「一致性巡检」的**显式修复**用（删悬空关系行 / 去重复关系行）。只 UPDATE 不 INSERT；
    `tag` 会写进 `last_edit_source`（≤16 字符）。
    """
    page = _load_page(kb_id, slug)
    _apply_content_update(kb_id, slug, content, tag)
    return {"slug": slug, "before_version": page["version"], "after_version": page["version"] + 1}


def add_relation(kb_id: str, slug: str, rel_type: str, target_slug: str,
                 label: str = "") -> dict:
    """新增一条出边（**直写图实例**，2026-10-05 M3；委派 `ke_graph.add_edge`）。"""
    import ke_graph  # noqa: PLC0415
    return ke_graph.add_edge(kb_id, slug, rel_type, target_slug, label)


def update_relation(kb_id: str, slug: str, target_slug: str, new_rel_type: str = "",
                    new_target_slug: str = "", label: str = "") -> dict:
    """修改一条出边（**直写图实例**；委派 `ke_graph.update_edge`）。"""
    import ke_graph  # noqa: PLC0415
    return ke_graph.update_edge(kb_id, slug, target_slug, new_rel_type, new_target_slug, label)


def delete_relation(kb_id: str, slug: str, target_slug: str, rel_type: str = "") -> dict:
    """删除本页出边（**直写图实例**；委派 `ke_graph.delete_edge`）。"""
    import ke_graph  # noqa: PLC0415
    return ke_graph.delete_edge(kb_id, slug, target_slug, rel_type)


# ---------------------------------------------------------------------------
# 本体类型修改 / 批量软删除 / KB 能力位（wiki 自动生成开关）
# ---------------------------------------------------------------------------
def _sync_folders(kb_id: str) -> dict:
    """复用既有目录同步脚本（子进程，避免 ke-core ↔ ontology-mcp 的循环导入）。"""
    import subprocess
    script = HERE.parents[0] / "ontology-mcp" / "sync_folders.py"
    if not script.is_file():
        return {"ok": False, "error": "找不到 %s" % script}
    done = subprocess.run([sys.executable, str(script), "--kb-id", kb_id, "--link-pages"],
                          capture_output=True, text=True, encoding="utf-8", check=False)
    tail = (done.stdout or done.stderr or "").strip().splitlines()[-3:]
    return {"ok": done.returncode == 0, "tail": tail}


def set_page_type(kb_id: str, slug: str, new_type: str, sync_folders: bool = True) -> dict:
    """改本体类型：类型 + 分类路径 + page_metadata 一起改，并重建目录树。

    上游的 `PUT /wiki/pages/<slug>` **只接受** summary/entity/concept/index/... 六个内建
    类型（`types.IsValidWikiPageType`），传 `bmm:Goal` 会 400 —— 所以类型修改必须走本函数。
    """
    new_type = (new_type or "").strip()
    meta = ke_ontology.class_meta()
    if new_type not in meta:
        raise ValueError("未知本体类型：%s" % new_type)
    page = _load_page(kb_id, slug)
    import json as _json
    md = {}
    try:
        md = _json.loads(page.get("page_metadata") or "{}") or {}
    except Exception:  # noqa: BLE001
        md = {}
    if not isinstance(md, dict):
        md = {}
    ontology = dict(md.get("ontology") or {})
    ontology.update({"model": (meta.get(new_type) or {}).get("module") or "",
                     "class": new_type,
                     "class_label": (meta.get(new_type) or {}).get("label") or new_type,
                     "generator": TAG_TYPE, "updated_at": ke_db.now_text()})
    md["ontology"] = ontology
    path = ke_ontology.category_path(new_type)

    stmts = [_snapshot_stmt(kb_id, slug, TAG_TYPE),
             "UPDATE wiki_pages SET page_type = %s, category_path = %s::jsonb, "
             "page_metadata = %s::jsonb, version = version + 1, updated_at = now(), "
             "last_edit_source = '%s' "
             "WHERE knowledge_base_id = %s AND slug = %s AND deleted_at IS NULL;"
             % (ke_db.sql_str(new_type), ke_db.sql_json(path), ke_db.sql_json(md), TAG_TYPE,
                ke_db.sql_str(kb_id), ke_db.sql_str(slug))]
    ke_db.psql("BEGIN;\n" + "\n".join(stmts) + "\nCOMMIT;\n", stdin=True)

    result = {"changed": page["page_type"] != new_type, "slug": slug,
              "before": page["page_type"], "after": new_type,
              "type_label": (meta.get(new_type) or {}).get("label") or new_type,
              "category_path": path, "version": page["version"] + 1}
    if sync_folders:
        result["folders"] = _sync_folders(kb_id)
    return result


# ---------------------------------------------------------------------------
# 类型迁移（retag）：改本体类型 = **迁移 slug + 联动引用**（2026-09-27 用户口径）
#   两段式：preview（只读：影响面 + ticket）→ 用户确认 → apply（带 ticket + 风险确认）
#   为什么必须迁 slug：slug 第一段是**模块**、第二段是**类名小写**（见 server.element_slug）；
#   类型改了却不改 slug，就与"slug = 模块/类/名称"的唯一性口径不一致（docs/agent-design-flow.md §11.11）。
#   页 id 不动（`_resolve_page_id` 按 (kb, slug) 找现有行；`wiki_page_revisions.page_id` 外键不受影响）。
# ---------------------------------------------------------------------------
RETAG_STATE_DIR = HERE.parents[1] / "state" / "retag"
RETAG_TAG = "bodhi-retag"          # ≤16 字符（last_edit_source 是 varchar(16)）


def split_slug(slug: str) -> tuple:
    """实例页 slug = `<模块>/<类小写>/<名称>`；不满足三段 → 返回三个空串。"""
    parts = (slug or "").split("/")
    if len(parts) != 3 or not all(p.strip() for p in parts):
        return "", "", ""
    return parts[0], parts[1], parts[2]


def slug_for_type(old_slug: str, new_type: str) -> str:
    """按**新类**推导目标 slug：`<新类模块>/<新类名小写>/<原名称段>`（名称段不变）。"""
    _, _, name = split_slug(old_slug)
    new_type = (new_type or "").strip()
    if not name or ":" not in new_type:
        return ""
    module = str((ke_ontology.class_meta().get(new_type) or {}).get("module") or "")
    if not module:
        return ""
    return "%s/%s/%s" % (module, new_type.split(":", 1)[-1].lower(), name)


def _kb_pages_raw(kb_id: str) -> list[dict]:
    """本库活页（只读）：slug / title / page_type / content / out_links / page_metadata / version。"""
    return ke_db.psql_csv(
        "SELECT slug, COALESCE(title,'') AS title, COALESCE(page_type,'') AS page_type, "
        "       COALESCE(content,'') AS content, COALESCE(out_links::text,'[]') AS out_links, "
        "       COALESCE(page_metadata::text,'{}') AS meta, COALESCE(version,1) AS version "
        "  FROM wiki_pages WHERE knowledge_base_id = %s AND deleted_at IS NULL ORDER BY slug"
        % ke_db.sql_str(kb_id))


def _json_safe(text, default=None):
    """宽松 JSON 解析：坏了、或值是 JSON `null`，都回落到默认（列表 / 传入的 default）。

    为什么必须容 null：`jsonb` 列里可以存字面量 `null`（不是 SQL NULL），
    `COALESCE(col::text,'[]')` 拿到的是字符串 `"null"` → `json.loads` 得到 None
    → 直接迭代会 TypeError（2026-09-27 自测踩到）。
    """
    import json as _json

    fallback = [] if default is None else default
    try:
        val = _json.loads("[]" if text is None else text)
    except Exception:  # noqa: BLE001
        return fallback
    return fallback if val is None else val


def find_slug_refs(kb_id: str, slug: str, pages: list[dict] | None = None) -> dict:
    """扫出**引用了该 slug** 的地方（只读）：关系行 / out_links / 正文 / 溯源 / 元数据 / 会话状态 / 跨库绑定。"""
    import json as _json

    pages = pages if pages is not None else _kb_pages_raw(kb_id)
    samples: list[dict] = []
    by_kind: dict = {}

    def _hit(page_slug: str, kind: str, detail: str = "") -> None:
        by_kind[kind] = by_kind.get(kind, 0) + 1
        if len(samples) < 25:
            samples.append({"page": page_slug, "kind": kind, "detail": detail[:120]})

    for page in pages:
        if page["slug"] == slug:
            continue
        for rel in parse_out_relations(page["content"]):
            if rel.get("slug") == slug:
                _hit(page["slug"], "relation_line", "%s → %s" % (rel.get("type"), slug))
        links = [s for s in _json_safe(page["out_links"]) if isinstance(s, str)]
        if slug in links:
            _hit(page["slug"], "out_links", "out_links 数组")
        if ("wiki:%s" % slug) in page["content"] or ("`%s`" % slug) in page["content"]:
            _hit(page["slug"], "content_text", "正文里的 wiki: 链接或反引号引用")
        meta = _json_safe(page["meta"], default={})
        if isinstance(meta, dict):
            design = meta.get("design") or {}
            if isinstance(design, dict) and slug in (design.get("upstream") or []):
                _hit(page["slug"], "design_upstream", "page_metadata.design.upstream")
            ont = meta.get("ontology") or {}
            if isinstance(ont, dict) and str(ont.get("candidate_slug") or "") == slug:
                _hit(page["slug"], "pending_candidate", "page_metadata.ontology.candidate_slug")
            auth = meta.get("authority") or {}
            if isinstance(auth, dict):
                master = auth.get("master") or {}
                if isinstance(master, dict) and str(master.get("slug") or "") == slug:
                    _hit(page["slug"], "cross_kb_binding", "page_metadata.authority.master.slug")

    session_hits: list[dict] = []
    base = HERE.parents[1] / "state" / "domain_sessions" / kb_id
    if base.is_dir():
        for path in sorted(base.glob("*.json")):
            try:
                data = _json.loads(path.read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001
                continue
            for item in (data.get("pages") or []):
                if isinstance(item, dict) and str(item.get("slug") or "") == slug:
                    session_hits.append({"file": path.name, "no": item.get("no"),
                                         "round": item.get("round_no")})

    return {"total": sum(by_kind.values()), "by_kind": by_kind, "samples": samples,
            "session_hits": session_hits}


def _schema_violations(page: dict, new_type: str, pages: list[dict]) -> list[dict]:
    """把该页的关系行按**新类**重新校验（预计 violations；domain/range 口径同 ke_ontology）。"""
    try:
        allowed = ke_ontology.relation_type_map(new_type)
    except Exception:  # noqa: BLE001
        return []
    by_slug = {p["slug"]: p for p in pages}
    out: list[dict] = []
    for rel in parse_out_relations(page["content"]):
        rtype, target = rel.get("type") or "", rel.get("slug") or ""
        if not rtype:
            continue
        if rtype not in allowed:
            out.append({"relation": rtype, "target": target,
                        "reason": "关系类型不适用于新类（新类的可用关系里没有它）"})
            continue
        if not target or target not in by_slug:
            continue
        try:
            closure = ke_ontology.target_closure(rtype)
        except Exception:  # noqa: BLE001
            closure = []
        if closure and by_slug[target]["page_type"] not in closure:
            out.append({"relation": rtype, "target": target,
                        "reason": "目标类型 %s 不在该关系的 range 闭包内"
                                  % by_slug[target]["page_type"]})
    return out


def retag_preview(kb_id: str, slug: str, new_type: str) -> dict:
    """**只读**影响面：新 slug、引用页清单、会话命中、预计 violations、风险与 ticket。"""
    import hashlib
    import json as _json

    new_type = (new_type or "").strip()
    meta_all = ke_ontology.class_meta()
    if new_type and new_type not in meta_all:
        raise ValueError("未知本体类型：%s" % new_type)
    page = _load_page(kb_id, slug)
    pages = _kb_pages_raw(kb_id)
    module, _local, name = split_slug(slug)
    out: dict = {"kb_id": kb_id, "slug": slug, "title": page.get("title", ""),
                 "old": {"page_type": page["page_type"],
                         "class_label": (meta_all.get(page["page_type"]) or {}).get("label", ""),
                         "module": (meta_all.get(page["page_type"]) or {}).get("module", "")},
                 "new": {"page_type": new_type,
                         "class_label": (meta_all.get(new_type) or {}).get("label", ""),
                         "module": (meta_all.get(new_type) or {}).get("module", "")},
                 "warnings": [], "risks": [], "required_risks": []}
    if not module or not name:
        out["applicable"] = False
        out["warnings"].append("该页 slug 不是实例页三段式（`模块/类/名称`）→ 不适用类型迁移")
        out["ticket"] = ""
        return out
    out["applicable"] = True
    new_slug = slug_for_type(slug, new_type)
    if not new_slug:
        out["applicable"] = False
        out["warnings"].append("按新类推不出目标 slug（新类没有模块信息）")
        out["ticket"] = ""
        return out
    out["slug_change"] = {"from": slug, "to": new_slug, "changed": new_slug != slug}
    conflict = next((p for p in pages if p["slug"] == new_slug), None)
    out["conflict"] = ({"slug": new_slug, "title": conflict.get("title", "")} if conflict else None)
    if new_type == page["page_type"] and new_slug == slug:
        out["already"] = True
        out["ticket"] = ""
        out["apply_hint"] = "类型与 slug 都已是目标状态，无需迁移"
        return out

    refs = find_slug_refs(kb_id, slug, pages)
    out["refs"] = refs
    out["schema_violations"] = _schema_violations(page, new_type, pages)
    if new_slug != slug:
        out["required_risks"].append("url_break")
    if refs["total"]:
        out["required_risks"].append("refs_rewrite")
    if refs["session_hits"]:
        out["required_risks"].append("agent_session")
    if "cross_kb_binding" in refs["by_kind"]:
        out["required_risks"].append("cross_kb_binding")
    if out["schema_violations"]:
        out["required_risks"].append("schema_violation")
    out["risks"] = [
        "url_break：旧 slug 的链接/书签会失效（新 slug：%s）" % new_slug,
        "refs_rewrite：%d 处引用会被改写（关系行 / out_links / 正文 / 溯源 / 元数据）" % refs["total"],
        "agent_session：%d 处建模会话状态记着旧 slug（续跑会找不到页，apply 会一并改写）"
        % len(refs["session_hits"]),
        "schema_violation：%d 条关系在新类下不合法（apply 后会被巡检报出）"
        % len(out["schema_violations"]),
    ]
    sig = _json.dumps({"kb": kb_id, "old_type": page["page_type"], "new_type": new_type,
                       "from": slug, "to": new_slug, "ref_total": refs["total"],
                       "refs": sorted("%s|%s|%s" % (s["page"], s["kind"], s["detail"])
                                      for s in refs["samples"]),
                       "sess": sorted("%s|%s" % (h["file"], h.get("no")) for h in refs["session_hits"]),
                       "viol": sorted("%s|%s" % (v["relation"], v["target"])
                                      for v in out["schema_violations"])},
                      ensure_ascii=False, sort_keys=True)
    out["ticket"] = hashlib.sha1(sig.encode("utf-8")).hexdigest()[:16]
    out["apply_hint"] = ("确认后调 apply（HTTP `POST /bodhi/page/retag/apply` 或 `ke_admin.py retag-apply`），"
                         "需带 ticket=%s 与 acknowledge_risks=%s" % (out["ticket"], out["required_risks"]))
    return out


def retag_apply(kb_id: str, slug: str, new_type: str, ticket: str = "",
                acknowledge_risks: list | None = None) -> dict:
    """两段式的第二段：带 ticket + 风险确认才执行（缺一即拒，等价 HTTP 409）。"""
    import json as _json

    prev = retag_preview(kb_id, slug, new_type)
    if not prev.get("applicable"):
        raise ValueError("不可迁移：%s" % "；".join(prev.get("warnings") or []))
    if prev.get("already"):
        return {"already": True, "slug": slug, "page_type": new_type, "note": prev["apply_hint"]}
    if str(ticket or "") != prev["ticket"]:
        raise ValueError("ticket 不匹配（影响面已变化或未先 preview）→ need_repreview=true："
                         "请重新 preview 让用户再确认一次")
    ack = set(acknowledge_risks or [])
    missing = [r for r in prev["required_risks"] if r not in ack]
    if missing:
        raise ValueError("缺少风险确认：%s（本页需要的确认项：%s）"
                         % ("、".join(missing), "、".join(prev["required_risks"])))
    if prev.get("conflict"):
        raise ValueError("目标 slug 已被占用：%s（%s）—— 先合并/删除该页再迁移"
                         % (prev["conflict"]["slug"], prev["conflict"]["title"]))

    kb_id = prev["kb_id"]
    new_slug = prev["slug_change"]["to"]
    pages = _kb_pages_raw(kb_id)
    by_slug = {p["slug"]: p for p in pages}
    page = by_slug[slug]
    meta_all = ke_ontology.class_meta()
    md = _json_safe(page["meta"], default={})
    md = md if isinstance(md, dict) else {}
    ont = dict(md.get("ontology") or {})
    ont.update({"model": (meta_all.get(new_type) or {}).get("module") or ont.get("model", ""),
                "class": new_type,
                "class_label": (meta_all.get(new_type) or {}).get("label") or new_type,
                "generator": RETAG_TAG, "updated_at": ke_db.now_text(),
                "retag": {"from_slug": slug, "from_type": page["page_type"],
                          "ticket": prev["ticket"], "at": ke_db.now_text()}})
    md["ontology"] = ont
    stmts: list[str] = [_snapshot_stmt(kb_id, slug, RETAG_TAG),
                        "UPDATE wiki_pages SET slug = %s, wiki_path = %s, page_type = %s, "
                        "category_path = %s::jsonb, page_metadata = %s::jsonb, "
                        "version = COALESCE(version,1) + 1, updated_at = now(), "
                        "last_edit_source = '%s' WHERE knowledge_base_id = %s AND slug = %s "
                        "AND deleted_at IS NULL;"
                        % (ke_db.sql_str(new_slug), ke_db.sql_str(new_slug), ke_db.sql_str(new_type),
                           ke_db.sql_json(ke_ontology.category_path(new_type)), ke_db.sql_json(md),
                           RETAG_TAG, ke_db.sql_str(kb_id), ke_db.sql_str(slug))]
    before: list[dict] = [{"slug": slug, "page_type": page["page_type"], "content": page["content"],
                           "out_links": page["out_links"], "page_metadata": page["meta"],
                           "kind": "target"}]
    rewritten = _rewrite_refs(kb_id, slug, new_slug, pages, stmts, before)
    ke_db.psql("BEGIN;\n" + "\n".join(stmts) + "\nCOMMIT;\n", stdin=True)
    session_rewritten = _rewrite_session_state(kb_id, slug, new_slug)
    folders = _sync_folders(kb_id)
    RETAG_STATE_DIR.mkdir(parents=True, exist_ok=True)
    state_path = RETAG_STATE_DIR / ("%s.json" % prev["ticket"])
    state_path.write_text(_json.dumps({"kb_id": kb_id, "slug": slug, "new_slug": new_slug,
                                       "old_type": page["page_type"], "new_type": new_type,
                                       "ticket": prev["ticket"], "at": ke_db.now_text(),
                                       "before": before, "rewritten": rewritten,
                                       "session_rewritten": session_rewritten},
                                      ensure_ascii=False), encoding="utf-8")
    return {"ok": True, "ticket": prev["ticket"], "slug": slug, "new_slug": new_slug,
            "page_type": {"from": page["page_type"], "to": new_type},
            "refs_rewritten": len(rewritten), "refs": rewritten[:20],
            "session_rewritten": session_rewritten,
            "schema_violations": prev["schema_violations"], "folders": folders,
            "state_file": str(state_path),
            "rollback_hint": "ke_admin.py retag-rollback %s %s" % (kb_id, prev["ticket"])}


def _rewrite_refs(kb_id: str, old_slug: str, new_slug: str, pages: list[dict],
                  stmts: list, before: list) -> list[dict]:
    """把所有**引用了 old_slug** 的页改写为 new_slug（正文/out_links/元数据），并追加 SQL。

    只改"这个 slug 的引用"，不动其他内容；每页先写版本快照（回滚依据见 `retag_rollback`）。
    """
    import json as _json

    rewritten: list[dict] = []
    for ref in pages:
        if ref["slug"] == old_slug:
            continue
        touched: list[str] = []
        content = ref["content"]
        if ("[[%s|" % old_slug) in content or ("wiki:%s" % old_slug) in content \
                or ("`%s`" % old_slug) in content:
            content = (content.replace("[[%s|" % old_slug, "[[%s|" % new_slug)
                              .replace("wiki:%s" % old_slug, "wiki:%s" % new_slug)
                              .replace("`%s`" % old_slug, "`%s`" % new_slug))
            touched.append("content")
        links = [s for s in _json_safe(ref["out_links"]) if isinstance(s, str)]
        new_links = [new_slug if s == old_slug else s for s in links]
        if new_links != links:
            touched.append("out_links")
        rmd = _json_safe(ref["meta"], default={})
        rmd = rmd if isinstance(rmd, dict) else {}
        rmd_text = _json.dumps(rmd, ensure_ascii=False)
        if ('"%s"' % old_slug) in rmd_text:
            try:
                rmd = _json.loads(rmd_text.replace('"%s"' % old_slug, '"%s"' % new_slug))
                touched.append("page_metadata")
            except Exception:  # noqa: BLE001
                pass
        if not touched:
            continue
        before.append({"slug": ref["slug"], "content": ref["content"],
                       "out_links": ref["out_links"], "page_metadata": ref["meta"],
                       "kind": "ref", "touched": touched})
        stmts.append(_snapshot_stmt(kb_id, ref["slug"], RETAG_TAG))
        sets = ["version = COALESCE(version,1) + 1", "updated_at = now()",
                "last_edit_source = '%s'" % RETAG_TAG]
        if "content" in touched:
            sets.append("content = %s" % ke_db.sql_str(content))
        if "out_links" in touched:
            sets.append("out_links = %s::jsonb" % ke_db.sql_json(new_links))
        if "page_metadata" in touched:
            sets.append("page_metadata = %s::jsonb" % ke_db.sql_json(rmd))
        stmts.append("UPDATE wiki_pages SET %s WHERE knowledge_base_id = %s AND slug = %s "
                     "AND deleted_at IS NULL;"
                     % (", ".join(sets), ke_db.sql_str(kb_id), ke_db.sql_str(ref["slug"])))
        rewritten.append({"slug": ref["slug"], "touched": touched})
    return rewritten


def _rewrite_session_state(kb_id: str, old_slug: str, new_slug: str) -> list[dict]:
    """把 `state/domain_sessions/<kb>/*.json` 里记着的旧 slug 改成新 slug（逐文件重写）。"""
    import json as _json

    done: list[dict] = []
    base = HERE.parents[1] / "state" / "domain_sessions" / kb_id
    if not base.is_dir():
        return done
    for path in sorted(base.glob("*.json")):
        try:
            data = _json.loads(path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            continue
        changed = 0
        for item in (data.get("pages") or []):
            if isinstance(item, dict) and str(item.get("slug") or "") == old_slug:
                item["slug"] = new_slug
                changed += 1
        if changed:
            path.write_text(_json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            done.append({"file": path.name, "pages": changed})
    return done


def retag_rollback(kb_id: str, ticket: str) -> dict:
    """按 apply 留下的状态文件回滚（恢复 slug/page_type/content/out_links/page_metadata）。"""
    import json as _json

    path = RETAG_STATE_DIR / ("%s.json" % (ticket or "").strip())
    if not path.is_file():
        raise ValueError("找不到迁移记录：%s" % path)
    data = _json.loads(path.read_text(encoding="utf-8"))
    if data.get("kb_id") != kb_id:
        raise ValueError("迁移记录属于另一个知识库：%s" % data.get("kb_id"))
    stmts: list[str] = []
    for item in data.get("before") or []:
        stmts.append(_snapshot_stmt(kb_id, item["slug"], RETAG_TAG))
        if item.get("kind") == "target":
            md = _json_safe(item["page_metadata"], default={})
            md = md if isinstance(md, dict) else {}
            ont = dict(md.get("ontology") or {})
            ont.pop("retag", None)
            md["ontology"] = ont
            stmts.append("UPDATE wiki_pages SET slug = %s, wiki_path = %s, page_type = %s, "
                         "page_metadata = %s::jsonb, version = COALESCE(version,1) + 1, "
                         "updated_at = now(), last_edit_source = '%s' "
                         "WHERE knowledge_base_id = %s AND slug = %s AND deleted_at IS NULL;"
                         % (ke_db.sql_str(item["slug"]), ke_db.sql_str(item["slug"]),
                            ke_db.sql_str(item["page_type"]), ke_db.sql_json(md), RETAG_TAG,
                            ke_db.sql_str(kb_id), ke_db.sql_str(data["new_slug"])))
        else:
            stmts.append("UPDATE wiki_pages SET content = %s, out_links = %s::jsonb, "
                         "page_metadata = %s::jsonb, version = COALESCE(version,1) + 1, "
                         "updated_at = now(), last_edit_source = '%s' "
                         "WHERE knowledge_base_id = %s AND slug = %s AND deleted_at IS NULL;"
                         % (ke_db.sql_str(item["content"]),
                            ke_db.sql_json(_json_safe(item["out_links"])),
                            ke_db.sql_json(_json_safe(item["page_metadata"], default={})),
                            RETAG_TAG, ke_db.sql_str(kb_id), ke_db.sql_str(item["slug"])))
    ke_db.psql("BEGIN;\n" + "\n".join(stmts) + "\nCOMMIT;\n", stdin=True)
    sessions = _rewrite_session_state(kb_id, data["new_slug"], data["slug"])
    return {"ok": True, "ticket": ticket, "restored_pages": len(data.get("before") or []),
            "slug": data["slug"], "session_rewritten": sessions, "folders": _sync_folders(kb_id)}


def delete_pages(kb_id: str, slugs: list[str], dry_run: bool = False,
                 sync_folders: bool = True) -> dict:
    """多选**硬删除**（用户 2026-09-19 口径：直接删 pg 记录 + 图库记录，便于重新提取；
    软删除/恢复以后再单独做）。`slug='index'` 永远保留。"""
    wanted = [str(s).strip() for s in (slugs or []) if str(s).strip()]
    wanted = [s for s in wanted if s != "index"]
    if not wanted:
        raise ValueError("没有可删除的 slug（index 受保护，始终保留）")
    cond = ("knowledge_base_id = %s AND deleted_at IS NULL AND slug <> 'index' AND slug IN (%s)"
            % (ke_db.sql_str(kb_id), ", ".join(ke_db.sql_str(s) for s in wanted)))
    if dry_run:
        rows = ke_db.psql_csv("SELECT slug, page_type FROM wiki_pages WHERE %s ORDER BY slug" % cond)
        return {"dry_run": True, "mode": "hard", "requested": len(wanted), "matched": len(rows),
                "pages": [dict(r) for r in rows]}

    # 1) 先记下要删的 slug（含软删的旧行，一并清掉），便于图库同删
    existing = ke_db.psql_csv(
        "SELECT slug FROM wiki_pages WHERE knowledge_base_id = %s AND slug IN (%s)"
        % (ke_db.sql_str(kb_id), ", ".join(ke_db.sql_str(s) for s in wanted)))
    slugs_all = [r["slug"] for r in existing] or wanted

    # 2) PG：版本快照 + 主页行一起硬删（in_links 由 rebuild 兜底重算）
    stmts = [
        "DELETE FROM wiki_page_revisions WHERE knowledge_base_id = %s AND slug IN (%s);"
        % (ke_db.sql_str(kb_id), ", ".join(ke_db.sql_str(s) for s in slugs_all)),
        "DELETE FROM wiki_pages WHERE %s;" % cond,
    ]
    ke_db.psql("BEGIN;\n" + "\n".join(stmts) + "\nCOMMIT;\n", stdin=True)

    # 3) 图库（Neo4j）：删该库对应实例（**kb_id 作用域**，防跨库误删）
    neo4j_deleted = 0
    try:
        import ke_graph  # noqa: PLC0415
        for slug in slugs_all:
            try:
                neo4j_deleted += int(ke_graph.delete_page(kb_id, slug).get("deleted") or 0)
            except Exception:  # noqa: BLE001
                break
    except Exception:  # noqa: BLE001
        pass

    result = {"dry_run": False, "mode": "hard", "requested": len(wanted),
              "deleted": len(slugs_all), "slugs": slugs_all, "neo4j_deleted": neo4j_deleted,
              "protected": ["index"]}
    if sync_folders:
        result["folders"] = _sync_folders(kb_id)
    return result


def set_wiki_enabled(kb_id: str, enabled: bool) -> dict:
    """知识库能力位 `indexing_strategy.wiki_enabled`（决定上传文档是否自动生成 wiki）。

    直接改 KB 的 jsonb 字段，不走上游 `PUT /knowledge-bases/:id`——那条链路要求
    整份 config（chunking_config 等是**值类型**，缺字段会被清空），风险不值得冒。
    """
    flag = "true" if enabled else "false"
    out = ke_db.psql(
        "UPDATE knowledge_bases SET indexing_strategy = "
        "jsonb_set(COALESCE(indexing_strategy, '{}'::jsonb), '{wiki_enabled}', '%s'::jsonb, true), "
        "updated_at = now() WHERE id = %s AND deleted_at IS NULL "
        "RETURNING indexing_strategy::text;" % (flag, ke_db.sql_str(kb_id)))
    if not out.strip():
        raise ValueError("知识库不存在：%s" % kb_id)
    return {"kb_id": kb_id, "wiki_enabled": bool(enabled),
            "indexing_strategy": out.strip().splitlines()[0]}


if __name__ == "__main__":  # 本地自测：python3 ke_pages.py relations <kb> <slug>
    import json as _json2

    def _main() -> int:
        args = sys.argv[1:]
        if len(args) >= 3 and args[0] == "relations":
            import ke_graph  # noqa: PLC0415
            print(_json2.dumps(ke_graph.relations_of(args[1], args[2]), ensure_ascii=False, indent=2))
            return 0
        if len(args) >= 3 and args[0] == "types":
            print(_json2.dumps(ke_ontology.relation_types_for(args[2]), ensure_ascii=False, indent=2))
            return 0
        print("用法：ke_pages.py relations <kb_id> <slug> | types <kb_id> <page_type>")
        return 1

    sys.exit(_main())



