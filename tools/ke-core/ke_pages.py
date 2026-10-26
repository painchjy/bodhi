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

import json
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

# 外部接口绑定串（`bpmn:dataObject*` 四个数据属性）：**只允许 ASCII**
#   2026-10-25 用户口径：技术绑定用 ASCII（`,` 分接口 / `;` 按序对齐 / `:` 服务名前缀）；
#   全角标点、空格一律拒收 —— 绑定串只进节点属性、不进 Cypher 注释，所以 `;` 不会截断语句 ✓
_ASCII_BINDING = re.compile(r"^[A-Za-z0-9_.,;:/~|-]+$")



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


def clean_alias_noise(kb_id: str = "", dry_run: bool = True, limit: int = 2000) -> dict:
    """**维护 · 清别名噪音**：删掉 `aliases` 里**与标题同名**的标签（历史写入把标题塞进 aliases ✗）。

    - `dry_run=True`（默认）只念清单 ✓；
    - 执行时：**先快照**（`wiki_page_revisions`）→ 只改 `aliases` → `version+1`（可回退 ✓）；
    - 其它别名（真别名，如缩写/英文名）**一律保留** ✓；空数组不写 `[]` 而是保持原样（仅当全部是噪音才置 `[]`）；
    - `kb_id` 空 ⇒ 全库（运维口径），建议先在单库 dry-run 看清单 ✓。

    为什么需要：W7 巡检会把"别名=标题"当低风险噪音报出来（34 条），但它只是**冗余**、
    不影响读写；清理后 W7 归零，别名列表也更好读。
    """
    import json  # noqa: PLC0415

    cond = ("knowledge_base_id = %s AND " % ke_db.sql_str(kb_id)) if kb_id else ""
    rows = ke_db.psql_csv(
        "SELECT knowledge_base_id AS kb, slug, COALESCE(title,'') AS t, "
        "       COALESCE(aliases::text,'[]') AS a "
        "  FROM wiki_pages WHERE %sdeleted_at IS NULL AND aliases IS NOT NULL "
        "   AND aliases::text <> '[]' ORDER BY slug LIMIT %d" % (cond, int(limit) + 1))
    truncated = len(rows) > int(limit)
    hits, cleaned = [], 0
    for r in rows[:int(limit)]:
        try:
            aliases = json.loads(r["a"] or "[]")
        except Exception:  # noqa: BLE001
            continue
        if not isinstance(aliases, list):
            continue
        title = str(r["t"] or "").strip()
        if not title:
            continue
        keep = [str(x) for x in aliases if str(x).strip() and str(x).strip() != title]
        noise = [str(x) for x in aliases if str(x).strip() == title]
        if not noise:
            continue
        hits.append({"kb_id": r["kb"], "slug": r["slug"], "title": title,
                     "removed": noise, "kept": keep})
        if dry_run:
            continue
        stmt = ("BEGIN;\n%s\nUPDATE wiki_pages SET aliases = %s::jsonb, "
                "version = COALESCE(version,1) + 1, updated_at = now(), "
                "last_edit_source = 'bodhi-ops-edit' "
                "WHERE knowledge_base_id = %s AND slug = %s AND deleted_at IS NULL;\nCOMMIT;\n"
                % (_snapshot_stmt(r["kb"], r["slug"], "bodhi-ops-edit"),
                   ke_db.sql_json(keep), ke_db.sql_str(r["kb"]), ke_db.sql_str(r["slug"])))
        ke_db.psql(stmt, stdin=True)
        cleaned += 1
    return {"ok": True, "dry_run": dry_run, "scanned": min(len(rows), int(limit)),
            "truncated": truncated, "hits": len(hits),
            **({"cleaned": cleaned} if not dry_run else {}),
            **({"pages": hits[:50]} if hits else {}),
            "note": ("只删**与标题同名**的别名；其它别名保留。执行会先写 `wiki_page_revisions` 快照并 `version+1`。"
                     if dry_run else "已清理并按页快照 + 版本 +1（可回退）。")}



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
                          extra_set: str = "", title: str = "", page_type: str = "",
                          attributes: dict | None = None) -> str:
    """快照 + 更新正文（**图 → PG**，2026-10-17 用户口径统一）：

    ① **图先写**：正文（+属性）写图节点；**失败即中止** ✓（不产生"PG 新 / 图旧"的半成品 ✗）
    ② 再落 PG：快照旧版 → content/version+1（`extra_set` 里带 title/summary/属性等 ✓）
    """
    replica_guard(kb_id, slug, tag)      # 副本页禁止本地修改（pull 通道除外）
    import ke_graph  # noqa: PLC0415  懒导入避免 ke_pages ↔ ke_graph 环
    # 2026-10-10（用户口径）：**wiki→图 投影已彻底移除** ——
    # 关系不再从正文派生（正文早已不承载关系，`strip_relation_sections` 会去掉那两小节），
    # 图一律由写路径直写：`upsert_node`/`upsert_nodes_batch`、`add_edge`/`add_edges_batch`、
    # `ensure_instances`；存量重建只用 `ke_graph.rebuild_kb_graph`（它**保留边**、只重建节点/属性）。
    content = ke_graph.strip_relation_sections(content)
    # ① 图先写（缺 title/page_type 时读一次 PG —— 只为了不把节点 name 写坏 ✓）
    if not title or not page_type:
        _row = ke_db.psql_csv(
            "SELECT COALESCE(title,'') AS t, COALESCE(page_type,'') AS pt FROM wiki_pages "
            " WHERE knowledge_base_id = %s AND slug = %s AND deleted_at IS NULL LIMIT 1"
            % (ke_db.sql_str(kb_id), ke_db.sql_str(slug)))
        if _row:
            title = title or str(_row[0].get("t") or "")
            page_type = page_type or str(_row[0].get("pt") or "")
    try:
        ke_graph.upsert_node(kb_id, slug, title, page_type,
                             attributes=(attributes if attributes is not None else None),
                             wiki_content=content)
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError("图节点写入失败，已中止 PG 更新（契约 §8 图本优先）：%s" % str(exc)[:180])
    # ② 再落 PG
    stmts = [_snapshot_stmt(kb_id, slug, tag),
             "UPDATE wiki_pages SET content = %s, out_links = '[]'::jsonb, in_links = '[]'::jsonb, "
             "version = version + 1, updated_at = now(), last_edit_source = '%s'%s "
             "WHERE knowledge_base_id = %s AND slug = %s AND deleted_at IS NULL;"
             % (ke_db.sql_str(content), tag, extra_set, ke_db.sql_str(kb_id), ke_db.sql_str(slug))]
    ke_db.psql("BEGIN;\n" + "\n".join(stmts) + "\nCOMMIT;\n", stdin=True)
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


def derive_summary(page_type: str, attributes: dict, fallback: str = "") -> str:
    """**实例页 `summary` 的唯一派生入口**（2026-10-15 用户口径：所有写路径共用一套核心）。

    规则（仅**实例知识**）：
        `skos:definition` → `bmm:definition`（定义）非空 ⇒ 用它；
        否则 `skos:note` → `bmm:description`（备注）非空 ⇒ 用它；
        再否则回落到 `fallback`（调用方给的事实，如标题 / 报告摘要）。

    ⚠️ **本体模型库的页不由此函数管**：本体页的 `summary` 是 `rdfs:comment`，由"加载页"
    （`ontology_wiki.py` + `ke_admin.load_model` 那条链）独立生成 —— 两条链**严格隔离**，
    本函数及其调用方都不得触碰本体模型知识的维护方法。
    """
    attrs = {str(k): v for k, v in (attributes or {}).items()}
    for key in ("skos:definition", "bmm:definition"):
        val = str(attrs.get(key) or "").strip()
        if val:
            return val
    for key in ("skos:note", "bmm:description"):
        val = str(attrs.get(key) or "").strip()
        if val:
            return val
    return str(fallback or "").strip()


# ── 别名（aliases）规则（2026-10-16 用户口径）──────────────────────────────────────
#   前端渲染位置：wiki 阅读页**标题下方的一排「别名」标签**
#   （上游 `WikiBrowser.vue`：`selectedPage.aliases` + i18n `aliases: '别名'`）—— 纯展示 + 供模糊检索。
#   规则（**不区分本体页/实例页**，来源驱动）：
#     ① 页面属性里的**注释类别名**：`rdfs:label` / `skos:altLabel`
#        （本体里已声明为 `owl:AnnotationProperty`；实例层若用了，就是别名的作用 —— 用户口径）；
#     ② 被本体标记 `bodhi:aliasKey true` 的**数据属性**的值（如 `bmm:systemAbbr` 主/子模块简称）；
#     ③ 页面**已有** aliases（并集保留，不丢历史）；
#   排除：与**标题相同**的值（同名标签是旧 `save_elements` 默认 `[name]` 留下的噪音）、空串、重复值。
#   ⚠️ 不引入 `bodhi:alias` 实例属性：别名一律用「标记 + 已有属性」表达（用户口径）。
ALIAS_KEYS = ("rdfs:label", "skos:altLabel")     # 内置别名键（标准词表，本体已声明为注释属性）
ALIAS_KEY_MARKER = "bodhi:aliasKey"              # 本体里标记"此属性的值进 aliases"
ALIAS_MAX = 8                                    # 标签上限，防标题下方刷屏


def _ontology_alias_keys(page_type: str) -> tuple:
    """本体模型里被 `bodhi:aliasKey true` 标记的属性键（如 `bmm:systemAbbr`）。

    走 `ke_ontology.alias_keys()`；本体侧尚未实现（或模型未编译）时**静默回落到空元组**，
    只用内置的 `ALIAS_KEYS` —— 核心不因本体侧进度而不可用（fail-open，与 summary 护栏同风格）。
    """
    try:
        import ke_ontology as _ko  # noqa: PLC0415
        return tuple(_ko.alias_keys(page_type) or ())
    except Exception:  # noqa: BLE001
        return ()


def derive_aliases(attributes: dict, title: str = "", page_type: str = "",
                   keep=(), alias_keys=None, max_items: int = ALIAS_MAX) -> list:
    """**实例页 `aliases` 的唯一派生入口**（与 `derive_summary` 并列，同一个写口）。

    返回：去重后的别名列表（已剔除标题同名值/空值，最多 `max_items` 个）。
    `alias_keys` 显式给出时用它（便于测试/本体侧直接传）；否则向 `ke_ontology` 取标记键。
    """
    keys = list(ALIAS_KEYS)
    if alias_keys is None:
        alias_keys = _ontology_alias_keys(page_type)
    keys += [str(k) for k in (alias_keys or []) if str(k) not in keys]
    t = str(title or "").strip()
    attrs = {str(k): v for k, v in (attributes or {}).items()}
    out: list = []
    for key in keys:
        raw = attrs.get(key)
        vals = raw if isinstance(raw, (list, tuple)) else ([raw] if raw is not None else [])
        for one in vals:
            s = str(one or "").strip()
            if s and s != t and s not in out:
                out.append(s)
    for one in (keep or []):
        s = str(one or "").strip()
        if s and s != t and s not in out:
            out.append(s)
    return out[:max_items]


def _sql_id(x: dict) -> str:
    """INSERT 的 `id`：给了就用（批量建模走 **uuid5 派生**，巡检 D5 认它），没给就让库侧随机。"""
    return ke_db.sql_str(x["id"]) if x.get("id") else "gen_random_uuid()::text"


def _opt_sql(x: dict, key: str, kind: str = "text") -> str:
    """**可选列**：传了就写值，没传就写 SQL 关键字 `DEFAULT`（PostgreSQL 允许在 `VALUES` 里用）。

    ⚠️ 为什么用 `DEFAULT` 而不是"照抄 legacy 的常量"：现有调用方（面板 / 会话 / 设计页）**本来不写**
    这些列、靠库默认 —— 若核心统一写常量，等于把它们的写入行为一起改了。用 `DEFAULT` 则
    **老调用方零变化**，而 `ke_import`（收编后）传什么就写什么（与它自有 INSERT 逐列等价）。
    """
    if key not in x or x.get(key) is None:
        return "DEFAULT"
    val = x.get(key)
    if kind == "jsonb":
        return "%s::jsonb" % ke_db.sql_json(val)
    if kind == "int":
        return str(int(val))
    return ke_db.sql_str(val)


def _page_aliases(x: dict) -> list:
    """批量写项 → 别名（与 `upsert_page` 同一入口；`page_metadata.ontology.attributes` 里取依据）。"""
    attrs = ((x.get("metadata") or {}).get("ontology") or {}).get("attributes") or {}
    keep = [str(v).strip() for v in (x.get("aliases") or []) if str(v or "").strip()]
    return derive_aliases(attrs, title=x.get("title") or "", page_type=x.get("page_type") or "",
                          keep=keep)


def upsert_page(kb_id: str, slug: str, title: str, page_type: str, content: str,
                summary: str = "", tag: str = "bodhi-cxt-edit",
                metadata: dict | None = None, sync_folders: bool = True,
                source_refs: list | None = None, chunk_refs: list | None = None,
                preloaded: dict | None = None) -> dict:
    # ── 实例页 summary 护栏（2026-10-15 用户口径：单一写核）────────────────────────────
    #   属性里有定义键时**强制以定义值为准**，忽略调用方传入的 summary —— 任何写路径
    #   （save_elements / ke_import 合并 / 设计页 / 面板 / 以摘要为准同步）都绕不过这一层，
    #   口径不可能再分叉。⚠️ 本体模型库页不走本函数（它们由"加载页"独立生成，严格隔离）。
    _carrier_attrs = ((metadata or {}).get("ontology") or {}).get("attributes") or {}
    if _carrier_attrs:
        summary = derive_summary(page_type, _carrier_attrs, summary)
    # ── 实例页 aliases 护栏（2026-10-16 用户口径：别名规则进内核）────────────────────
    #   别名 = 属性派生（rdfs:label / skos:altLabel / 本体标记 bodhi:aliasKey 的属性值）；
    #   写入时与**页面已有别名**并集（旧值不清）、**排除与标题同名**的值（旧路径噪音）。
    _alias_derived = derive_aliases(_carrier_attrs, title=title, page_type=page_type)
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
    # ── 报告（document 模式）统一护栏（2026-10-17 用户口径 ✓）──────────────────────────
    #   工具**不猜场景** ✗：只保证规范 —— `page_type` 属 `bmm:Report` 子类 ⇒ **必须有正文** ✓；
    #   报告类型（TechnicalReviewReport / RequirementAnalysisReport / agent:* 等）由**上游/用户**
    #   按任务步骤指定 ✓，这里不限制具体类型 ✗；属性仍按本体约束（与 entity 类同一套 ✓）；
    #   document 类**不做**"数据属性渲正文" ✓（正文完全由上游给 ✓）。
    try:
        _is_doc = ke_ontology.render_mode(page_type) == "document"
    except Exception:  # noqa: BLE001
        _is_doc = False
    if _is_doc and not str(content or "").strip():
        raise ValueError(
            "document 类必须有正文：%s 属 `bmm:Report` 子类 ⇒ 正文（wiki_content）由上游生成 ✓；"
            "数据属性不产正文 ✗" % page_type)
    _attrs_for_graph = dict((metadata or {}).get("ontology", {}).get("attributes") or {})
    if preloaded is not None:
        # 批量路径（2026-10-10 P3-0c）：调用方已用 `page_info_map` **一次**预读全批，
        # 这里免掉"每页 1 次存在性查询"（40 页 ≈ 40×272ms ≈ 11s，本机）。
        existing = ([{"version": int(preloaded.get("version") or 1)}]
                    if preloaded.get("exists") else [])
    else:
        existing = ke_db.psql_csv(
            "SELECT COALESCE(version,1) AS version, COALESCE(page_type,'') AS page_type, "
            "       COALESCE(category_path::text,'[]') AS cat "
            "  FROM wiki_pages "
            " WHERE knowledge_base_id = %s AND slug = %s AND deleted_at IS NULL LIMIT 1"
            % (ke_db.sql_str(kb_id), ke_db.sql_str(slug)))
    extra = (", title = %s, summary = %s, page_type = %s, "
             "page_metadata = COALESCE(page_metadata, '{}'::jsonb) || %s::jsonb"
             % (ke_db.sql_str(title), ke_db.sql_str(summary), ke_db.sql_str(page_type),
                ke_db.sql_json(metadata or {})))
    # ── 目录三列（2026-10-16 用户口径：面板改类型/标题后，目录列必须跟上）────────────────
    #   `category_path` 由本体类推导 → `depth = len(category_path)` → `wiki_path = 目录/标题`。
    #   规则（**保住用户选的层级**）：
    #     · `page_type` **没变** → 保留现存 `category_path`（含 `recategorize` 选过的层级），只按它重算
    #       `depth`/`wiki_path`（标题改了目录树里就不能再显示旧标题 —— 2026-09-30 实测口径）；
    #     · `page_type` **变了** → 按当前本体重算 `category_path`（默认两层形态「模块短名/顶层大类」）。
    #   批量路径（`preloaded`，无 page_type 预读）不动这三列 —— 由 `_bulk_write_pages` 自己写 ✓。
    _old_pt = ""
    _old_cat: list = []
    if existing:
        _old_pt = str(existing[0].get("page_type") or "")
        try:
            import json as _json   # noqa: PLC0415
            _old_cat = _json.loads(existing[0].get("cat") or "[]")
        except Exception:  # noqa: BLE001
            _old_cat = []
    if _old_pt and _old_pt == page_type:
        _cat = _old_cat
    else:
        try:
            _cat = ke_ontology.category_path(page_type) or []
        except Exception:  # noqa: BLE001
            _cat = _old_cat
    if _cat:
        extra += (", category_path = %s::jsonb, depth = %d, wiki_path = %s"
                  % (ke_db.sql_json(_cat), len(_cat),
                     ke_db.sql_str("/".join([str(x) for x in _cat] + [title]))))
    # 来源列（2026-10-09 内核缺口修复）：UPDATE 走**并集**，绝不丢已继承的来源。
    if source_refs is not None:
        extra += (", source_refs = COALESCE((SELECT jsonb_agg(DISTINCT v) FROM "
                  "jsonb_array_elements_text(COALESCE(source_refs,'[]'::jsonb) || %s::jsonb) AS t(v)),"
                  " '[]'::jsonb)" % ke_db.sql_json(_uniq_str(source_refs)))
    if chunk_refs is not None:
        extra += (", chunk_refs = COALESCE((SELECT jsonb_agg(DISTINCT v) FROM "
                  "jsonb_array_elements_text(COALESCE(chunk_refs,'[]'::jsonb) || %s::jsonb) AS t(v)),"
                  " '[]'::jsonb)" % ke_db.sql_json(_uniq_str(chunk_refs)))
    if _alias_derived:
        # 别名与旧值**并集**（存量先保留口径：不因写入而丢历史别名）
        extra += (", aliases = COALESCE((SELECT jsonb_agg(DISTINCT v) FROM "
                  "jsonb_array_elements_text(COALESCE(aliases,'[]'::jsonb) || %s::jsonb) AS t(v)),"
                  " '[]'::jsonb)" % ke_db.sql_json(_alias_derived))
    if existing:
        before = int(existing[0]["version"] or 1)
        _apply_content_update(kb_id, slug, content, tag, extra_set=extra,
                              title=title, page_type=page_type,
                              attributes=_attrs_for_graph)
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
    # 本体分类目录由**上面的目录三列**统一派生（2026-10-16 起 INSERT/UPDATE 共用同一份取值）。
    cat = _cat or []
    cols = ("id, tenant_id, knowledge_base_id, slug, title, page_type, content, summary, "
            "out_links, in_links, page_metadata, version, last_edit_source, category_path, depth, "
            "wiki_path, source_refs, chunk_refs, aliases")
    # 2026-10-10（用户口径）：wiki→图 投影已彻底移除 —— INSERT 只去关系小节，图由写路径直写。
    import ke_graph as _kg
    content = _kg.strip_relation_sections(content)
    vals = ("gen_random_uuid()::text, %d, %s, %s, %s, %s, %s, %s, '[]'::jsonb, '[]'::jsonb, %s::jsonb, 1, %s, "
            "%s::jsonb, %d, %s, %s::jsonb, %s::jsonb, %s::jsonb"
            % (int(kb[0]["tenant_id"] or 0), ke_db.sql_str(kb_id), ke_db.sql_str(slug),
               ke_db.sql_str(title), ke_db.sql_str(page_type), ke_db.sql_str(content),
               ke_db.sql_str(summary), ke_db.sql_json(metadata or {}),
               ke_db.sql_str(tag), ke_db.sql_json(cat), len(cat),
               ke_db.sql_str("/".join([str(x) for x in cat] + [title])),
               ke_db.sql_json(_uniq_str(source_refs)),
               ke_db.sql_json(_uniq_str(chunk_refs)),
               ke_db.sql_json(_alias_derived)))
    # ② **图先写**（2026-10-17 用户口径 ✓）：顺序必须是「图 → PG」✓
    #    图节点写不成功 ⇒ **拒绝建 PG 页** ✓（不能出现"只有 PG、没有图"的半成品 ✗，
    #    也就不会再有"事后补图"这种补救流程 ✗ —— 与 `write_knowledge` 的 §8「图本优先」一致 ✓）。
    try:
        _kg.upsert_node(kb_id, slug, title, page_type,
                        attributes=_attrs_for_graph, wiki_content=_wiki_content_for_graph)
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError("图节点写入失败，已拒绝建页（图本优先，契约 §8）：%s" % str(exc)[:200])
    # ③ 图成功后再落 PG
    ke_db.psql("BEGIN;\nINSERT INTO wiki_pages (%s) VALUES (%s);\nCOMMIT;\n" % (cols, vals), stdin=True)
    if sync_folders:
        _sync_folders(kb_id)
    return {"slug": slug, "created": True, "before_version": 0, "after_version": 1,
            "category_path": cat, "graph": {"ok": True, "node": "upserted"},
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


def _summary_sync_key(props: dict) -> str:
    """与 PG `summary` 同步的那个数据属性键（本地名 ∈ `SUMMARY_SYNC_LOCALS`，如 `skos:definition`）。

    用途（2026-10-14 用户口径）：WeKnora 原生编辑框的「定义」= `wiki_pages.summary`；
    本体侧对应 `skos:definition` ⇒ 读（`page_attributes`）与写（`set_page_attributes`）都要带上它。
    该类没声明该键时也返回 `skos:definition`（面板/接口照样能显示这一处值）。
    """
    for name in (props or {}):
        if str(name).split(":")[-1].lower() in SUMMARY_SYNC_LOCALS:
            return str(name)
    return "skos:definition"


ATTR_SECTION = "## 属性（数据属性）"
# 与 PG `summary` 同步的键（**本地名**，2026-10-14 用户口径）：
#   `skos:definition` ↔ WeKnora 原生编辑框里的「定义」（就是 `wiki_pages.summary`），
#   所以**正文属性段不再重复渲染**它（正文里由首段承载，见 `set_page_attributes` / `render_entity_content`）。
SUMMARY_SYNC_LOCALS = ("definition",)

# 从**图节点**取"属性值"时要排除的簿记字段（它们是节点自身的元数据/标题，不是数据属性取值）：
# 实测踩到：`name` 既是节点属性又是 bmm 的数据属性 → 会凭空渲染出一行「### 名称」，属噪音。
_GRAPH_NODE_INTERNAL = ("slug", "kb_id", "tenant_id", "page_type", "wiki_content", "module",
                        "name", "englishName")


def render_attributes_section(page_type: str, attributes: dict, skip_keys=()) -> list[str]:
    """§4：数据属性 → `## 属性（数据属性）` + `### 名称（prefixed，range）`\\n<值>。

    **标题式 + 不截断**（2026-10-13 用户口径，方案 D）：
    - 属性名作**标题**、值紧随其下；**不再用 `名字 = 值` 一行式**（老 `=` 格式只在**读**侧兼容，
      见契约 §4.3 双读；**写**一律走本函数）；
    - `ATTR_RENDER_LIMIT = 0` ⇒ 值**不截断**：正文只是这个值的**只读渲染**；
    - 事实源 = `page_metadata.ontology.attributes` + 图节点属性；改值走 `set_page_attributes()`
      /「数据属性」面板，**改正文不会回写**（ke_review 的读路径不解析正文）。
    """
    skip = {str(k).split(":")[-1] for k in (skip_keys or [])}
    rows: list[str] = []
    for key, value in (attributes or {}).items():
        short = str(key).split(":")[-1]
        if short in skip or short.lower() in SUMMARY_SYNC_LOCALS:
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
    return ([ATTR_SECTION, ""] + rows) if rows else []


def _first_para_before_attr(content: str) -> str:
    """取「## 属性（数据属性）」段**之前**的首段实质正文（判断"首段=定义属性"用）。"""
    head: list[str] = []
    for ln in (content or "").splitlines():
        if ln.strip() == ATTR_SECTION:
            break
        head.append(ln)
    for raw in head:
        s = raw.strip()
        if not s or s.startswith("#") or s.startswith(">"):
            continue
        return s
    return ""


def _attr_key_canonical(key: str, props: dict) -> str:
    """把 `ruleReference` / `bmm:ruleReference` 归一到本体面里的规范写法（用本体里的键名）。"""
    raw = str(key or "").strip()
    if not raw:
        return ""
    local = raw.split(":")[-1].lower()
    for name in props or {}:
        if str(name).split(":")[-1].lower() == local:
            return str(name)
    return raw


def _attrs_seed_from_content(page_type: str, content: str, props: dict) -> dict:
    """老页的种子：元数据 `attributes` 为空时，从正文属性段读现值（标题式 / `=` 式**双读**）。"""
    import ke_graph as _kg  # noqa: PLC0415  懒导入（ke_pages ↔ ke_graph 有环）
    try:
        parsed = _kg._attrs_from_content(content) or {}
    except Exception:  # noqa: BLE001
        return {}
    out: dict = {}
    for k, v in parsed.items():
        ck = _attr_key_canonical(k, props)
        if str(v or "").strip():
            out[ck] = str(v).strip()
    return out


def _attrs_from_graph(kb_id: str, slug: str, props: dict) -> dict:
    """从**图节点属性**读数据属性（图是"事实源"的另一处）。

    为什么要它：本机实测 —— 领域规则页 `page_metadata.ontology.attributes` 是**空的**，值只在
    图节点属性里（`ruleImplementation/ruleReference/...`，键是**本地名**、不带模块前缀）。
    老页做属性编辑时若只读元数据，会把"图上明明有的值"当成不存在（改一个键就把其它键抹掉）。
    """
    pairs: list = []                                   # [(本地名, 本体键)]
    for key in (props or {}):
        local = str(key).split(":")[-1]
        if local in _GRAPH_NODE_INTERNAL:              # 跳过节点簿记字段（见常量注释）
            continue
        if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", local) and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*",
                                                                          str(key).split(":")[0]):
            pairs.append((local, str(key)))
    if not pairs:
        return {}
    import ke_graph as _kg  # noqa: PLC0415  懒导入（ke_pages ↔ ke_graph 有环，本模块一律函数内导入）
    ret = ", ".join("n.`%s` AS c%d" % (local, i) for i, (local, _k) in enumerate(pairs))
    try:
        rows = _kg._run("MATCH (n:BodhiInstance {kb_id:$kb, slug:$s}) RETURN %s LIMIT 1" % ret,
                        {"kb": kb_id, "s": slug})
    except Exception:  # noqa: BLE001  图不可用 → 交给调用方回落其它来源
        return {}
    if not rows:
        return {}
    row = rows[0] or {}
    out: dict = {}
    for i, (_local, key) in enumerate(pairs):
        val = row.get("c%d" % i)
        if val not in (None, ""):
            out[key] = str(val)
    return out


def _keys_in_section(content: str) -> list[str]:
    """按**出现顺序**列出「## 属性（数据属性）」段里的键（标题式取 `prefixed`；旧一行式取 `=` 前的名字）。"""
    out: list[str] = []
    inside = False
    for raw in (content or "").splitlines():
        s = raw.strip()
        if s == ATTR_SECTION:
            inside = True
            continue
        if inside and s.startswith("## "):
            break
        if not inside:
            continue
        if s.startswith("### "):
            m = re.search(r"([A-Za-z_]\w*:[A-Za-z_]\w*)", s[4:])
            if m:
                out.append(m.group(1))
        else:
            m = re.match(r"^-\s*(?:bmm:)?([A-Za-z_][\w:]*)\b[^\n=]*=", s)
            if m:
                out.append(m.group(1))
    return out


def replace_attr_section(content: str, page_type: str, attributes: dict, skip_keys=()) -> str:
    """把正文里「## 属性（数据属性）」**整段替换**成当前渲染口径（标题式、不截断）。

    - 只动这一段：其它小节（`## 原文依据` / `## 被引用` / `## 本体关系` …）与手写正文原样保留；
    - 原来没有该段 → 插到「## 原文依据」**之前**（没有就追加到末尾）；
    - 渲染结果为空（属性全空）→ 删掉整段。
    """
    lines = (content or "").rstrip().splitlines()
    # 顺序口径（2026-10-13）：**先按正文现有顺序**排（保持历史/人工顺序，避免"只换了顺序也写一版"），
    # 新增键按传入顺序追加 —— 这样重跑替换是**逐字幂等**的（dry-run 会报 `正文会变=False`）。
    ordered: dict = {}
    for local in [k.split(":")[-1].lower() for k in _keys_in_section(content)]:
        for k, v in (attributes or {}).items():
            if str(k).split(":")[-1].lower() == local and k not in ordered:
                ordered[k] = v
                break
    for k, v in (attributes or {}).items():
        ordered.setdefault(k, v)
    section = render_attributes_section(page_type, ordered, skip_keys=skip_keys)
    start = next((i for i, ln in enumerate(lines) if ln.strip() == ATTR_SECTION), -1)
    if start >= 0:
        end = next((i for i in range(start + 1, len(lines)) if lines[i].startswith("## ")), len(lines))
        out = lines[:start] + (section + [""] if section else []) + lines[end:]
    else:
        ins = next((i for i, ln in enumerate(lines) if ln.startswith(EVIDENCE_SECTION)), -1)
        out = (lines[:ins] + section + [""] + lines[ins:]) if ins >= 0 \
            else (lines + (["", ""] + section if section else []))
    text = "\n".join(out).rstrip() + "\n"
    return re.sub(r"\n{4,}", "\n\n\n", text)          # 换段后可能多出空行，压一下


def page_attributes(kb_id: str, slug: str) -> dict:
    """**读**一页的数据属性现值（只读；前端「数据属性」面板 + 自检用）。

    - `attributes`：事实源现值（元数据 → 图节点属性 → 正文属性段，`source` 标出实际用的那一处）；
    - `editable`：**只有 entity 模式**为真；document/report 模式**以正文为主**（面板应转成只读提示）；
    - `allowed`：该类声明过的全部数据属性（`<本体键>（<本地名>）`），面板据此列可编辑项；
    - `rendered` / `in_meta` / `in_graph`：三处现值（正文段 / 元数据 / 图）；
    - `drift`：**三处不一致**的键清单（面板据此提醒"该刷新了"）。
    """
    import json  # noqa: PLC0415

    rows = ke_db.psql_csv(
        "SELECT title, COALESCE(page_type,'') AS page_type, COALESCE(version,1) AS version, "
        "       COALESCE(content,'') AS content, COALESCE(page_metadata::text,'{}') AS meta, "
        "       COALESCE(summary,'') AS summary "
        "  FROM wiki_pages WHERE knowledge_base_id = %s AND slug = %s AND deleted_at IS NULL LIMIT 1"
        % (ke_db.sql_str(kb_id), ke_db.sql_str(slug)))
    if not rows:
        return {"ok": False, "slug": slug, "error": "页面不存在：%s/%s" % (kb_id[:8], slug)}
    row = rows[0]
    page_type = row["page_type"]
    try:
        meta = json.loads(row["meta"] or "{}")
    except Exception:  # noqa: BLE001
        meta = {}
    onto = meta.get("ontology") or {}
    props = ke_ontology.data_properties_for(page_type) or {}
    # 2026-10-23（T1）：**正文载体不是可编辑数据属性** —— 面板不列、写口拒收（正文走 `stage="report"`）
    body_keys = sorted({k for k in props if ke_ontology.is_body_property(k)})
    props = {k: v for k, v in props.items() if not ke_ontology.is_body_property(k)}
    mode = ke_ontology.render_mode(page_type)

    def _norm(data: dict) -> dict:
        return {_attr_key_canonical(k, props): str(v).strip()
                for k, v in (data or {}).items() if str(v or "").strip()}

    _raw_meta_keys = {str(k) for k in (onto.get("attributes") or {})}   # 规范化前的键（含 skos:definition）
    in_meta = _norm((onto.get("attributes") or {}))
    in_graph = _norm(_attrs_from_graph(kb_id, slug, props))
    in_body = _norm(_attrs_seed_from_content(page_type, row["content"], props))
    # 正文载体键（`schema:articleBody` / `bmm:wikiContent`）**不算属性**：从三处现值里剔出，
    # 只作为 `body_keys` 告诉面板/调用方"正文别在属性里改"（2026-10-23 T1）
    body_keys = sorted(set(body_keys) | {k for k in (_raw_meta_keys | set(in_meta) | set(in_graph))
                                         if ke_ontology.is_body_property(k)})
    for _d in (in_meta, in_graph, in_body):
        for _k in [k for k in _d if ke_ontology.is_body_property(k)]:
            _d.pop(_k, None)
    _raw_meta_keys = {k for k in _raw_meta_keys if not ke_ontology.is_body_property(k)}
    # ④ `summary`（WeKnora 原生「定义」框）——2026-10-15 用户口径：**不再当取值兜底**
    #   （否则会把"摘要里有、知识里没有"误判成一致 = 假一致）；它只参与 **drift 比对** 与写侧同步。
    skey = next((k for k in sorted(_raw_meta_keys)     # 页面实际在用的"定义键"优先（如 skos:definition）
                 if str(k).split(":")[-1].lower() in SUMMARY_SYNC_LOCALS), "")
    skey = skey or _summary_sync_key(props)
    sval = str(row.get("summary") or "").strip()
    in_summary = {skey: sval} if (skey and sval) else {}
    if in_meta:
        attrs, source = in_meta, "meta"
    elif in_graph:
        attrs, source = in_graph, "graph"
    else:
        attrs, source = in_body, ("content" if in_body else "none")
    # drift：**三处现值**互比；另外**摘要必须与定义键一致** —— 摘要非空而三处缺该键，
    # 或三处现值与摘要不同 → 都算 drift（这样"摘要里有、知识里没有"不再被当成一致）。
    _MISS = object()
    drift = []
    for k in set(in_meta) | set(in_graph) | set(in_body):
        vals = {d.get(k, _MISS) for d in (in_meta, in_graph, in_body)}
        vals.discard(_MISS)
        if len(vals) > 1:
            drift.append(k)
    if sval and not str(page_type).startswith("ontology:"):
        known = {d[skey] for d in (in_meta, in_graph, in_body) if skey in d}
        if known != {sval}:
            drift.append(skey)
    drift = sorted(set(drift))
    hint = (("正文为主（document）：正文改 `stage=\"report\"`（同 slug 覆盖）；"
             "下面的数据属性只作结构化标记（如 `schema:applicableReportType`）")
            if mode == "document" else
            ("数据属性 = 事实源（三处同写：元数据 + 图节点 + 正文属性段）；"
             "与原生「定义」框同步的键（%s）同时写 `summary`。"
             "改值走「数据属性」面板（HTTP `POST /bodhi/page/attributes`）或 `page_attributes_set`；不要改正文。"
             % skey))
    allowed = []
    for k, meta in props.items():                      # 面板要显示「中文标签（本体键）」
        allowed.append("%s（%s）" % ((meta or {}).get("label") or str(k), str(k)))
    for k in sorted(set(attrs) | set(in_meta) | set(in_summary) | set(_raw_meta_keys)):  # 页上已有也列出来
        if not any(k == a.rsplit("（", 1)[-1].rstrip("）") for a in allowed):
            allowed.append("%s（%s）" % (_attr_meta(page_type, k).get("label") or str(k), str(k)))
    return {"ok": True, "kb_id": kb_id, "slug": slug, "title": row["title"], "page_type": page_type,
            "version": int(row["version"] or 1), "mode": mode,
            "editable": True,                  # 2026-10-15 用户口径：**不再判断**（面板一律可编辑；
                                               # 非法键由写口 `set_page_attributes` 拒绝）
            "attributes": attrs, "source": source, "allowed": allowed, "summary_key": skey,
            "body_keys": body_keys, "body_hint": ("正文不是数据属性：改正文请走 "
                                                  "`save_knowledge(stage=\"report\")`（同 slug 覆盖）"),
            "rendered": in_body, "in_meta": in_meta, "in_graph": in_graph, "in_summary": in_summary,
            "drift": drift, "hint": hint}


def set_page_attributes(kb_id: str, slug: str, patch: dict, *, actor: str = "",
                        dry_run: bool = True, sync_folders: bool = False,
                        sync_from_summary: bool = False) -> dict:
    """**改一页的数据属性**（方案 D 的写口，2026-10-13 用户口径）。

    口径（2026-10-14 用户口径，取代 2026-10-13 的"仅 entity"）：
      · **任何模式都可改**：只要该 `page_type` 在本体面里声明过数据属性，就走本入口
        （document/report 页若声明了属性同样可编辑；没声明属性的类没有可改项，自然引导改正文）；
      · **与原生「定义」框同步**：本地名 ∈ `SUMMARY_SYNC_LOCALS`（`skos:definition`）的键
        ↔ `wiki_pages.summary`（WeKnora 编辑弹窗的「定义」框），**写属性时一起写 `summary`**，
        并在正文首段保留定义原文（属性段不重复渲染该键）。

    规则：
    - 键必须该 `page_type` 在本体面里声明过的**数据属性**（与 `ontology_types` 同一份元数据）；
      未声明的键一律拒，并把可用键（`<本体键>（<本地名>）`）列全；
    - 值：字符串；**空串 / None = 删除该键**；
    - 写三处保持一致：① PG `page_metadata.ontology.attributes` ② 图节点属性
      ③ **重渲染正文的属性段**（标题式、不截断）；
    - 幂等：值与正文都没变 → 零写入；写前 `wiki_page_revisions` 快照 + `version+1`（可回退）；
    - **默认 `dry_run=True`**（只看 diff 不写）。
    """
    import json  # noqa: PLC0415

    rows = ke_db.psql_csv(
        "SELECT title, COALESCE(page_type,'') AS page_type, COALESCE(version,1) AS version, "
        "       COALESCE(content,'') AS content, COALESCE(page_metadata::text,'{}') AS meta, "
        "       COALESCE(summary,'') AS summary "
        "  FROM wiki_pages WHERE knowledge_base_id = %s AND slug = %s AND deleted_at IS NULL LIMIT 1"
        % (ke_db.sql_str(kb_id), ke_db.sql_str(slug)))
    if not rows:
        return {"ok": False, "slug": slug, "error": "页面不存在：%s/%s" % (kb_id[:8], slug)}
    row = rows[0]
    page_type, cur_ver = row["page_type"], int(row["version"] or 1)
    # 本体模型库只读（2026-10-15 用户口径）：数据属性修改属于"直接改本体库内容" → 拒绝
    ke_ontology.assert_writable_kb(kb_id, "「数据属性」修改")
    content = row["content"]
    try:
        meta = json.loads(row["meta"] or "{}")
    except Exception:  # noqa: BLE001
        meta = {}
    onto = dict(meta.get("ontology") or {})
    mode = ke_ontology.render_mode(page_type)
    # 2026-10-14 用户口径：**不再按渲染模式拒改**（document/report 页若有数据属性，同样可编辑）。
    # 2026-10-23（T1）：**正文载体键一律拒收** —— 正文只有一份事实源（图 `articleBody`），
    # 属性里再存一份必然漂移 ✗；改正文走 `save_knowledge(stage="report")`。
    _spa_body = [k for k in (patch or {}) if ke_ontology.is_body_property(k)]
    if _spa_body:
        return {"ok": False, "slug": slug, "page_type": page_type, "body_keys": _spa_body,
                "error": ("正文不是数据属性：%s —— 改正文请走 `save_knowledge(stage=\"report\")`"
                          "（同 slug 覆盖）" % "、".join(_spa_body))}
    # 2026-10-25（外部接口口径）：`projection` 是**投射**（来源 = MCP `tools/list`），写口一律拒收 ✓
    _spa_proj = [k for k in (patch or {}) if str(k).split(":")[-1].lower() == "projection"]
    if _spa_proj:
        return {"ok": False, "slug": slug, "page_type": page_type,
                "error": ("`projection` 由 `ext_describe` 生成（来源 = MCP `tools/list`），不能手写："
                          "调 `ext_describe(kb_id, object=…)` 重新订阅 ✓")}
    # 2026-10-25（用户口径）：绑定是**技术串**，只允许 ASCII 标点（全角/空格一律拒收 ✓）
    _spa_bind = {k: v for k, v in (patch or {}).items()
                 if str(k).split(":")[-1].lower() in
                 ("dataobjectinterface", "dataobjectaccessmode",
                  "dataobjectkeyfields", "dataobjectinterfaceref")}
    _bad_fmt = [k for k, v in _spa_bind.items() if not _ASCII_BINDING.match(str(v or "").strip())]
    if _bad_fmt:
        return {"ok": False, "slug": slug, "page_type": page_type,
                "bad_binding_format": _bad_fmt,
                "error": ("绑定串只允许 ASCII（字母/数字与 `_ . , ; : / ~ -`）：%s"
                          % "、".join(_bad_fmt))}
    _spa_allowed = {str(k).split(":")[-1].lower()
                    for k in (ke_ontology.data_properties_for(page_type) or {})
                    if not ke_ontology.is_body_property(k)}
    if _spa_allowed and patch:
        _spa_bad = [k for k in patch if str(k).split(":")[-1].lower() not in _spa_allowed]
        if _spa_bad:
            return {"ok": False, "slug": slug, "page_type": page_type,
                    "unknown_keys": _spa_bad, "allowed": sorted(_spa_allowed),
                    "error": "这些键不是 %s 的本体数据属性：%s" % (page_type, "、".join(_spa_bad))}
    props = {k: v for k, v in (ke_ontology.data_properties_for(page_type) or {}).items()
             if not ke_ontology.is_body_property(k)}
    if not props:
        return {"ok": False, "slug": slug, "page_type": page_type,
                "error": "本体面里取不到 %s 的数据属性（确认 page_type 正确、本体模型/图可用）" % page_type}
    allowed = {str(name).split(":")[-1].lower(): str(name) for name in props}
    # 沿用历史键（2026-10-14）：**页上已有的属性键一律可改** —— 本体面还没声明（如 TTL 尚未加
    # `skos:definition`）或键名换代（`bmm:definition`→`skos:definition`）时，面板/工具照样能编辑它。
    _seed_now = dict(onto.get("attributes") or {})
    for k in (_seed_now or _attrs_from_graph(kb_id, slug, props) or {}):
        allowed.setdefault(str(k).split(":")[-1].lower(), str(k))
    # 种子（老页元数据可能为空！本机实测：规则页 `ontology.attributes = {}`，值只在图/正文里）：
    #   ① 元数据（新页，机器口径）→ ② **图节点属性** → ③ 正文属性段（双读）
    base, seeded_from = dict(onto.get("attributes") or {}), ""
    if base:
        seeded_from = "meta"
    else:
        base = _attrs_from_graph(kb_id, slug, props)
        seeded_from = "graph" if base else ""
        if not base:
            base = _attrs_seed_from_content(page_type, content, props)
            seeded_from = "content" if base else ""
    merged, changed, unknown = dict(base), [], []
    if sync_from_summary:
        # 「以摘要为准同步」按钮的后端（2026-10-15 用户口径）：PG `summary` → 定义键的
        # **元数据 + 图节点属性**（**不含正文**：正文只展示"其它数据属性的取值"）。
        _sval = str(row["summary"] or "").strip()
        _skey_now = next((k for k in merged
                          if str(k).split(":")[-1].lower() in SUMMARY_SYNC_LOCALS), "") \
            or _summary_sync_key(props)
        if _sval:
            if merged.get(_skey_now) != _sval:
                changed.append(_skey_now)
            merged[_skey_now] = _sval
    for k, v in (patch or {}).items():
        local = str(k).split(":")[-1].lower()
        if local not in allowed:
            unknown.append(str(k))
            continue
        ckey = allowed[local]
        val = "" if v is None else str(v).strip()
        for other in [k for k in merged                          # 同"本地名"只留一个键：
                      if str(k) != ckey                          # 避免 `skos:definition` 与 `bmm:definition` 并存
                      and str(k).split(":")[-1].lower() == local]:
            merged.pop(other, None)
            changed.append(other)
        if val:
            if merged.get(ckey) != val:
                changed.append(ckey)
            merged[ckey] = val
        elif ckey in merged:
            changed.append(ckey)
            merged.pop(ckey, None)
    if unknown:
        return {"ok": False, "slug": slug, "page_type": page_type, "unknown_keys": unknown,
                "error": "这些键不是 %s 的数据属性：%s" % (page_type, "、".join(unknown)),
                "allowed": sorted("%s（%s）" % (allowed[k], k) for k in allowed)}


    # ⚠️ 护栏（2026-10-13，dry-run 实测踩到）：三处都读不到值时，`replace_attr_section` 会把**整段删掉**
    # —— 那是数据丢失，不是"迁移"。所以：**空 patch 且结果为空 → 直接中止**；只有调用方
    # 显式用 patch 传了键（哪怕全给空值 = 明确要清空）才继续。
    if not merged and not patch:
        return {"ok": False, "slug": slug, "mode": mode, "seeded_from": seeded_from,
                "error": "三处都没读到数据属性（元数据 / 图节点属性 / 正文属性段 皆空或读失败）："
                         "已中止，避免把正文属性段清空。先确认图可用（`graph_query` 能查到该节点）、"
                         "或改用 patch 显式给出要写的键值。"}
    # 2026-10-15 用户口径（两处修正）：
    #   ① **正文不再渲染定义**：`render_attributes_section()` 已按 `SUMMARY_SYNC_LOCALS` 跳过定义键；
    #      这里也**不再把定义写进首段** —— 旧逻辑"值不在正文就插一行"会让摘要每改一次正文多一行 ✗，
    #      而没有 `## 属性（数据属性）` 段的页又插不进去 ✗ → 同一操作两种现象（已修）。
    #   ② **`summary` 同步只覆盖 元数据 + 图节点**，不动正文（正文只展示其它数据属性的取值）。
    skey = next((k for k in merged                      # 页面实际在用的"定义键"优先
                 if str(k).split(":")[-1].lower() in SUMMARY_SYNC_LOCALS), "") or _summary_sync_key(props)
    new_summary = row["summary"]
    if skey:
        sval = str(merged.get(skey) or "").strip()
        if sval:
            new_summary = sval
    new_content = content if sync_from_summary else replace_attr_section(content, page_type, merged)
    content_changed = (new_content != content)
    summary_changed = (new_summary != (row["summary"] or ""))
    if not changed and not content_changed and not summary_changed:
        return {"ok": True, "applied": False, "slug": slug, "mode": mode,
                "reason": "无变化（值 / 正文 / summary 都未改）", "keys_changed": [],
                "before_version": cur_ver, "after_version": cur_ver}
    result = {"ok": True, "applied": False, "dry_run": bool(dry_run), "slug": slug, "mode": mode,
              "keys_changed": changed, "content_changed": content_changed,
              "summary_changed": summary_changed, "summary_key": skey,
              "summary_preview": str(new_summary or "")[:80],
              "seeded_from": seeded_from, "before_version": cur_ver, "after_version": cur_ver + 1,
              "attributes": merged, "content_preview": new_content[:900]}
    if dry_run:
        return result
    # ① 图节点（属性 + 正文）—— 契约 §8「图本优先」
    import ke_graph as _kg  # noqa: PLC0415  懒导入（ke_pages ↔ ke_graph 有环）
    graph = {"ok": True}
    try:
        _kg.upsert_node(kb_id, slug, row["title"], page_type, attributes=merged,
                        wiki_content=new_content)
    except Exception as exc:  # noqa: BLE001
        # 2026-10-17 用户口径 ✓：图写不成 ⇒ **不更新 PG** ✓（fail-closed，避免半成品 ✗）
        raise RuntimeError("图节点写入失败，已中止 PG 更新（契约 §8 图本优先）：%s" % str(exc)[:180])
    # ② PG：快照（旧版）→ version+1；content / page_metadata 一起更新（可回退）
    onto2 = dict(onto)
    onto2.update({"model": (page_type.split(":", 1)[0] if page_type else onto2.get("model")),
                  "class": page_type or onto2.get("class"), "name": row["title"],
                  "attributes": merged})
    meta2 = dict(meta)
    meta2["ontology"] = onto2
    res = upsert_page(kb_id, slug, row["title"], page_type, new_content, summary=new_summary,
                      tag="bodhi-attr-edit", metadata=meta2, sync_folders=sync_folders,
                      preloaded={"exists": True, "version": cur_ver})
    result.update({"applied": True, "graph": graph, "actor": actor,
                   "after_version": res.get("after_version") or (cur_ver + 1),
                   "note": "事实源 = page_metadata.ontology.attributes + 图节点属性；"
                           "正文属性段按「标题式」重渲染（只读渲染，别改正文）"})
    return result


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
    # P0-c-2（2026-10-10 用户口径）：**「首个版本生成」只在新建页写** —— 有既有值就沿用
    # （`spec["created_at"]`，由调用方/适配器从既有页头或 `page_metadata.ontology.created_at` 带过来），
    # 只有真新建时才取当前时间。否则每次写库这一行都会变（实测差异就卡在这一行）。
    lines.append("> **首个版本生成**：%s" % (spec.get("created_at") or ke_db.now_text()))
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


def _squash(text: str) -> str:
    """归一化（去空白/标点，小写）——意图/文档标识比较用。"""
    return re.sub(r"[\s\u3000·、，,。.（）()【】\[\]:：;；\-—_/]+", "", (text or "").lower())


def session_part_plan(kb_id: str, session_no: str, *, agent_name: str = "", role: str = "",
                      doc_id: str = "", intent: str = "") -> dict:
    """**会话分页判定（只读）**——用户口径 2026-10-22（内网问题 4.6）。

    分页规则（有判断依据、要留证据）：
      · **强信号**（客观、确定性）：智能体 / 角色 / 文档变化 ⇒ 必须开新分页；
      · **弱信号**（需人确认）：同一场景下 `intent`（任务意图）变化 ⇒ `need_confirm`，
        由调用方问用户"是否进入新任务"，**不静默换任务** ✗；
      · 都没变 ⇒ 复用当前分页。

    返回 `{action: create|paginate|reuse|need_confirm, part_no, slug, reasons[], evidence,
           ask_user, fingerprint}`（**只读，不建页**）。
    """
    sno = (session_no or "").strip()
    if not sno:
        return {"action": "need_confirm", "reasons": [{"signal": "session_no", "why": "缺会话编号"}],
                "ask_user": "缺少会话编号：请把 WeKnora 会话编号作为 session_no 传入"}
    rows = ke_db.psql_csv(
        "SELECT slug, COALESCE(page_metadata::text,'{}') AS md FROM wiki_pages "
        "WHERE knowledge_base_id=%s AND page_type=%s AND deleted_at IS NULL "
        "AND (content LIKE %s OR slug LIKE %s) ORDER BY length(slug) ASC"
        % (ke_db.sql_str(kb_id), ke_db.sql_str(SESSION_CLASS),
           ke_db.sql_str("%" + sno + "%"), ke_db.sql_str("session/" + sno + "%")))
    if not rows:
        return {"action": "create", "part_no": 1, "slug": "session/%s" % sno, "reasons": [],
                "evidence": {"agent": agent_name, "role": role, "doc_id": doc_id, "intent": intent},
                "fingerprint": "|".join([agent_name or "", role or "", doc_id or ""])}
    base = rows[0]["slug"]
    last = max(rows, key=lambda r: _part_no(r["slug"]))
    md = {}
    try:
        md = json.loads(last.get("md") or "{}") or {}
    except Exception:  # noqa: BLE001
        md = {}
    prev = (md.get("session_part") or {}) if isinstance(md, dict) else {}
    attrs = ((md.get("ontology") or {}).get("attributes") or {}) if isinstance(md, dict) else {}
    prev_agent = str(prev.get("agent") or attrs.get("bmm:agentName") or attrs.get("agentName") or "")
    prev_role = str(prev.get("role") or attrs.get("bmm:role") or attrs.get("role") or "")
    prev_doc = str(prev.get("doc_id") or "")
    prev_intent = str(prev.get("intent") or "")
    reasons = []
    if agent_name and prev_agent and _squash(agent_name) != _squash(prev_agent):
        reasons.append({"signal": "agent", "from": prev_agent, "to": agent_name})
    if role and prev_role and _squash(role) != _squash(prev_role):
        reasons.append({"signal": "role", "from": prev_role, "to": role})
    if doc_id and prev_doc and _squash(doc_id) != _squash(prev_doc):
        reasons.append({"signal": "doc", "from": prev_doc, "to": doc_id})
    evidence = {"agent": agent_name or prev_agent, "role": role or prev_role,
                "doc_id": doc_id or prev_doc, "intent": intent or prev_intent}
    fp = "|".join([evidence["agent"], evidence["role"], evidence["doc_id"]])
    if reasons:                                    # 强信号 ⇒ 开新分页（确定性，不需要问）
        part_no = max([_part_no(x["slug"]) for x in rows] or [1]) + 1
        return {"action": "paginate", "part_no": part_no,
                "slug": "session/%s/p%d" % (sno, part_no), "reasons": reasons,
                "evidence": evidence, "fingerprint": fp, "from_part": _part_no(last["slug"])}
    if intent and prev_intent and _squash(intent) != _squash(prev_intent):
        return {"action": "need_confirm", "part_no": _part_no(last["slug"]),
                "slug": last["slug"], "evidence": evidence, "fingerprint": fp,
                "reasons": [{"signal": "intent", "from": prev_intent, "to": intent}],
                "ask_user": ("疑似进入新任务（意图已变化：%s → %s）。请确认："
                             "① 开新分页（先建分页再写知识）；② 仍在原任务（带 "
                             "acknowledge_intent_change=true 重试）" % (prev_intent, intent))}
    return {"action": "reuse", "part_no": _part_no(last["slug"]), "slug": last["slug"],
            "reasons": [], "evidence": evidence, "fingerprint": fp}

def ensure_session_page(kb_id: str, session_no: str, *, session_name: str = "",
                        agent_name: str = "", role: str = "", initial_question: str = "",
                        tenant_id: int | None = None, doc_id: str = "", intent: str = "",
                        acknowledge_intent_change: bool = False) -> dict:
    """**核心会话页机制**（契约 §5.1；2026-10-22 扩成**四信号**）——确保「会话页/分页」存在。

    判定在 `session_part_plan`（只读），这里只**执行**：
    - 无会话页 ⇒ 建起始页（`partNo=1`）；
    - **强信号**变化（智能体 / 角色 / **文档**）⇒ 建新分页 `session/<no>/p<n>` + 起始页「## 分页索引」登记；
    - **弱信号**（意图变化）且未 ack ⇒ **不建页**，回 `need_confirm` + `ask_user`（让人确认是否进入新任务）；
    - 每页把判定**证据**写进 `page_metadata.session_part`（谁→谁、哪条信号 ⇒ 可审计）。

    返回 `{slug, part_no, created, paginated, need_confirm?, plan}`。
    """
    sno = str(session_no or "").strip()
    if not sno:
        return {"slug": "", "created": False, "paginated": False, "reason": "缺 session_no"}
    plan = session_part_plan(kb_id, sno, agent_name=agent_name, role=role,
                             doc_id=doc_id, intent=intent)
    act = plan.get("action")
    if act == "need_confirm":
        if not acknowledge_intent_change:
            return {"slug": plan.get("slug") or "", "part_no": int(plan.get("part_no") or 1),
                    "created": False, "paginated": False, "need_confirm": True,
                    "plan": plan, "ask_user": plan.get("ask_user", "")}
        # 用户已确认"仍在原任务" ⇒ **复用当前分页**，并把新意图记进该页证据（否则每次写都会再问 ✗）
        try:
            _part = {**(plan.get("evidence") or {}), "action": "intent_change_acked",
                     "at": ke_db.now_text()}
            ke_db.psql("UPDATE wiki_pages SET page_metadata = COALESCE(page_metadata,'{}'::jsonb)"
                       " || %s::jsonb, updated_at = now() WHERE knowledge_base_id = %s"
                       " AND slug = %s AND deleted_at IS NULL"
                       % (ke_db.sql_json({"session_part": _part}),
                          ke_db.sql_str(kb_id), ke_db.sql_str(plan["slug"])))
        except Exception:  # noqa: BLE001
            pass
        return {"slug": plan["slug"], "part_no": int(plan["part_no"]), "created": False,
                "paginated": False, "plan": plan, "intent_change_acked": True}
    if act == "reuse":
        return {"slug": plan["slug"], "part_no": int(plan["part_no"]), "created": False,
                "paginated": False, "plan": plan}
    part_no = int(plan.get("part_no") or 1)
    slug = plan["slug"]
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
    if plan.get("reasons"):                       # 分页理由（意图变更/场景变更的**证据**）
        head += ["## 分页理由", ""]
        head += ["- %s：`%s` → `%s`" % (r.get("signal"), r.get("from", ""), r.get("to", ""))
                 for r in plan["reasons"]]
        head += [""]
    if part_no > 1:                               # 起始页登记分页索引
        _base = ke_db.psql_csv(
            "SELECT slug FROM wiki_pages WHERE knowledge_base_id=%s AND page_type=%s "
            "AND deleted_at IS NULL AND (content LIKE %s OR slug LIKE %s) "
            "ORDER BY length(slug) ASC LIMIT 1"
            % (ke_db.sql_str(kb_id), ke_db.sql_str(SESSION_CLASS),
               ke_db.sql_str("%" + sno + "%"), ke_db.sql_str("session/" + sno + "%")))
        if _base:
            _register_session_part(kb_id, _base[0]["slug"], slug, title)
    part_meta = {"action": act, "fingerprint": plan.get("fingerprint"),
                 "at": ke_db.now_text()}
    part_meta.update(plan.get("evidence") or {})
    if plan.get("reasons"):
        part_meta["reasons"] = plan["reasons"]
    upsert_page(kb_id, slug, title, SESSION_CLASS, "\n".join(head).rstrip() + "\n",
                summary=(initial_question or title)[:200], tag=TAG_CONTRACT,
                metadata={"ontology": {"model": "bmm", "class": SESSION_CLASS, "name": title,
                                       "attributes": attrs, "generator": TAG_CONTRACT},
                          "session_part": part_meta})
    return {"slug": slug, "part_no": part_no, "created": (part_no == 1),
            "paginated": (part_no > 1), "plan": plan}


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


def _register_session_output(kb_id: str, part_slug: str, page_slug: str, title: str) -> None:
    """在**会话分页页**的「## 本轮产出」追加一条（幂等，溯源反列 ✓）。"""
    if not (part_slug and page_slug):
        return
    row = ke_db.psql_csv("SELECT COALESCE(content,'') AS c FROM wiki_pages "
                         "WHERE knowledge_base_id=%s AND slug=%s AND deleted_at IS NULL"
                         % (ke_db.sql_str(kb_id), ke_db.sql_str(part_slug)))
    if not row:
        return
    c = row[0]["c"]
    if page_slug in c:
        return
    line = "- %s · %s" % (page_slug, title or page_slug)
    if "## 本轮产出" in c:
        c = c.rstrip() + "\n" + line + "\n"
    else:
        c = c.rstrip() + "\n\n## 本轮产出\n" + line + "\n"
    ke_db.psql("UPDATE wiki_pages SET content=%s, version=version+1, updated_at=now() "
               "WHERE knowledge_base_id=%s AND slug=%s;"
               % (ke_db.sql_str(c), ke_db.sql_str(kb_id), ke_db.sql_str(part_slug)),
               stdin=True)


def write_knowledge(kb_id: str, spec: dict, *, dry_run: bool = False,
                    strict_source: bool = True, sync_folders: bool = True,
                    preloaded: dict | None = None, skip_graph: bool = False,
                    sink: list | None = None) -> dict:
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
    # 2026-10-22（4.6）：分页判定**提前**算 —— ① `sourceLocator` 必须精确到 `/pN`；
    # ② 意图变化（弱信号）在未 ack 时拒写（见下方门禁）。
    _part_plan = None
    if src.get("session_no"):
        try:
            _part_plan = session_part_plan(
                kb_id, str(src["session_no"]),
                agent_name=str(src.get("agent_name") or ""),
                role=str(src.get("role") or ""),
                doc_id=str(src.get("doc_id") or next(iter(src.get("doc_refs") or []), "")),
                intent=str(src.get("intent") or ""))
        except Exception:  # noqa: BLE001  判定失败不阻断（graph 阶段仍会确保会话页）
            _part_plan = None
        if _part_plan and not src.get("locator"):
            src["locator"] = "session/%s/p%d" % (src["session_no"],
                                                 int(_part_plan.get("part_no") or 1))
    slug = (spec.get("slug") or "").strip() or _derive_slug(page_type, title)
    # 2026-10-26（用户实测修正）：「首个版本生成」**以 DB 首版时间为准** —— 既有页一律沿用，
    # 不再取 `page_metadata`/页头（两者都被重置过，会变成"最新版本时间"✗）。
    if not spec.get("created_at") and slug:
        _first = first_version_text(kb_id, slug)
        if _first:
            spec["created_at"] = _first
    # 2026-10-22（内网问题 4.6 · 1-C）：**apply 前门禁** —— 意图变化（弱信号）未 ack ⇒ 拒写。
    # 判定已在上面提前算好（`_part_plan`）；这里只做 fail-closed 判断，并把结论原样回给调用方。
    if (not dry_run and _part_plan and _part_plan.get("action") == "need_confirm"
            and not src.get("acknowledge_intent_change")):
        return {"ok": False, "applied": False, "created": False, "kb_id": kb_id,
                "slug": slug, "title": title, "mode": mode,
                "need_session_part": True, "session_part": _part_plan,
                "ask_user": _part_plan.get("ask_user", ""),
                "hint": ("疑似进入新任务（意图变化）：请先让用户确认；确认后带 "
                         "`acknowledge_intent_change=true` 重试，或先建分页再写")}
    attrs = dict(spec.get("attributes") or {})
    # 2026-10-22（4.2 前置）：**正文不进属性** —— 事实源是图节点 `articleBody`（PG `content` 只是渲染缓存）；
    # 属性里再存一份 `wikiContent` 只会漂移、且让页面/回执体积翻倍 ✗（迁移见 ke_graph.strip_wiki_content_attr）
    # 2026-10-23（T1）：按**正文载体属性**统一剔除（`schema:articleBody` 与等价别名 `bmm:wikiContent`）
    dropped_body = sorted(k for k in attrs if ke_ontology.is_body_property(k))
    for _k in dropped_body:
        attrs.pop(_k, None)
    # 2026-10-17（用户口径 ✓）：**属性必须是本体声明的数据属性** ✓ —— 否则拒写 ✗
    #   反例：`verdicts` / `ruleSet` / `columns`（统计与列规范 ✗）—— 统计写正文 ✓、
    #   工具元数据放 metadata_extra ✓，都不许进 attributes ✗。
    if attrs:
        try:
            _allowed = {str(k).split(":")[-1].lower()
                        for k in (ke_ontology.data_properties_for(page_type) or {})}
        except Exception:  # noqa: BLE001
            _allowed = set()
        if _allowed:
            _bad = [k for k in attrs if str(k).split(":")[-1].lower() not in _allowed]
            if _bad:
                raise ValueError(
                    "属性不在 %s 的本体声明里：%s（允许：%s）—— 汇总统计写正文 ✓，"
                    "工具元数据放 metadata_extra ✓，不要塞 attributes ✗"
                    % (page_type, "、".join(_bad), "、".join(sorted(_allowed))))
    dkey = spec.get("definition_key", "definition")
    render_attrs = spec.get("render_attributes")
    if render_attrs is None:
        render_attrs = (mode == "entity")
    if mode == "entity":
        content = render_entity_content(kb_id, {**spec, "slug": slug, "attributes": attrs})
    else:
        content = spec.get("wiki_content") or spec.get("article_body")
        if not content:
            raise ValueError("mode=%s 需要 wiki_content（等价别名 article_body）" % mode)
        if mode == "document":
            # 2026-10-26（用户实测修正）：**幂等拼头** —— 正文里若已带页头（历史重复累积，实测 9 段），
            # 先剥干净再拼一段；否则每保存一次就多一段 ✗。
            _h0, _body0 = strip_heads(content)
            if not spec.get("created_at") and _h0.get("首个版本生成"):
                spec["created_at"] = _h0["首个版本生成"]
            content = ("\n".join(_contract_head({**spec, "slug": slug})) + "\n"
                       + _body0.lstrip())
        if render_attrs:
            content = content.rstrip() + "\n\n" + "\n".join(
                render_attributes_section(page_type, attrs, skip_keys=[dkey])) + "\n"
    if src.get("locator") and "sourceLocator" not in {str(k).split(":")[-1] for k in attrs}:
        attrs["bmm:sourceLocator"] = src["locator"]     # L2：片段定位（数据属性）
    metadata = {"ontology": {"model": page_type.split(":", 1)[0], "class": page_type,
                             "name": title, "attributes": attrs, "generator": TAG_CONTRACT,
                             "created_at": spec.get("created_at") or ke_db.now_text()}}
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
            if sink is None:
                # 2026-10-17 用户口径 ✓：单条写路径 **fail-closed** —— 图写不成 ⇒ 不落 PG ✓
                # （批量路径 `sink` 由图节点由调用方一条 UNWIND 写 ✓，不在此处阻断）
                raise RuntimeError(graph["reason"])
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
    if sink is not None:
        # P0-c-3（2026-10-10）：**批量 PG 写** —— 调用方给了 `sink` 就只**构建**这一页（不碰 PG），
        # 由调用方用 `_bulk_write_pages` **一次**写完：31 页从「31 个小事务」降到「2 次 psql」。
        # 版本/存在性来自 `preloaded`（批量路径已预读），所以这里不需要任何 PG 往返。
        _exists = bool((preloaded or {}).get("exists"))
        _before = int((preloaded or {}).get("version") or 1) if _exists else 0
        try:
            _cat = ke_ontology.category_path(page_type) or []
        except Exception:  # noqa: BLE001
            _cat = []
        sink.append({"slug": slug, "title": title, "page_type": page_type, "content": content,
                     "summary": (spec.get("summary") or "").strip()[:500],
                     "metadata": metadata, "category_path": _cat,
                     "wiki_path": "/".join([str(x) for x in _cat] + [title]),
                     "source_refs": _doc_refs, "chunk_refs": _chunk_refs, "exists": _exists})
        res = {"slug": slug, "created": not _exists, "before_version": _before,
               "after_version": (_before + 1) if _exists else 1,
               "content": content, "attrs": attrs}
    else:
        res = upsert_page(kb_id, slug, title, page_type, content,
                          summary=(spec.get("summary") or "").strip()[:500], tag=TAG_CONTRACT,
                          metadata=metadata, sync_folders=sync_folders,
                          source_refs=_doc_refs, chunk_refs=_chunk_refs, preloaded=preloaded)
    # ③ 关系（节点已就绪）—— P0-c-2（2026-10-10）：**类型一次取（图优先）+ 边一次批量建**。
    #    旧实现每条边 `add_edge`（无 ctx）= 2 次 `_page_type`(PG) + 2 次 `_ensure_instance`(PG+Neo4j)
    #    + 1 次查重 + 1 次 MERGE + 1 次 `relations_of` —— 实测 27 条边 ≈ 108 次 psql。
    #    现在：① 类型映射优先读图（1 Neo4j；图上没有的才**批量**问 PG 一次）
    #          ② 目标节点一次 UNWIND 确保 ③ 边按关系类型分组 UNWIND（N 条 → 去重后类型数次 Neo4j）。
    _rels = [r for r in (spec.get("relations") or [])
             if (r.get("type") and r.get("target_slug"))]
    if _rels:
        _targets = sorted({str(r["target_slug"]).strip() for r in _rels})
        _tinfo = (_kg.info_from_graph(kb_id, [slug] + _targets)
                  if _kg._validate_from_graph() else {})
        _missing = [t for t in _targets if not ((_tinfo.get(t) or {}).get("pt"))]
        if _missing:                   # 图上没有 → 一次 PG 兜底（"在 PG 但还没投影进图"的页）
            _tinfo.update(_kg.page_info_map(kb_id, _missing))
        _tinfo.setdefault(slug, {"pt": page_type, "title": title, "tenant": None})
        if _targets:
            try:
                _kg.ensure_instances(kb_id, [
                    {"slug": t, "name": (_tinfo.get(t) or {}).get("title") or t.rsplit("/", 1)[-1],
                     "page_type": (_tinfo.get(t) or {}).get("pt") or "",
                     "tenant_id": (_tinfo.get(t) or {}).get("tenant")}
                    for t in _targets])
            except Exception as exc:  # noqa: BLE001
                graph["edge_errors"].append({"scope": "ensure_instances", "error": str(exc)[:160]})
        try:
            _eres = _kg.add_edges_batch(
                kb_id, [(slug, r["type"], r["target_slug"]) for r in _rels], {"info": _tinfo})
            # 回执口径与旧版一致：`edges_written` = 校验通过并入图的**条目数**；
            # `edge_merge_hits` 才是 MERGE 命中数（含"同样的边已存在"）。
            _errs = list(_eres.get("errors") or [])
            graph["edges_written"] = len(_rels) - len(_errs)
            graph["edge_merge_hits"] = int(_eres.get("written") or 0)
            graph["edge_types"] = _eres.get("types")
            for e in _errs:
                graph["edge_errors"].append({"type": e.get("type"),
                                             "target_slug": e.get("target_slug"),
                                             "error": str(e.get("error"))[:160]})
        except Exception as exc:  # noqa: BLE001
            graph["edge_errors"].append({"scope": "add_edges_batch", "error": str(exc)[:160]})
    # ④ 会话溯源边（L1）—— **核心自动确保会话页**（没有则建；换智能体/角色→新建分页）。
    #    外围只需传 `source.session_no` + 场景（session_name/agent_name/role）。
    session_slug = (src.get("session_slug") or "").strip()
    if not session_slug and src.get("session_no"):
        try:
            _sp = ensure_session_page(kb_id, src["session_no"],
                                      session_name=src.get("session_name", ""),
                                      agent_name=src.get("agent_name", ""),
                                      role=src.get("role", ""),
                                      initial_question=src.get("initial_question", ""),
                                      doc_id=str(src.get("doc_id") or ""),
                                      intent=str(src.get("intent") or ""),
                                      acknowledge_intent_change=True)
            session_slug = (_sp.get("slug") or "")
            if _sp.get("created") or _sp.get("paginated"):
                graph["session_page"] = {"slug": session_slug, "part_no": _sp.get("part_no"),
                                         "created": _sp.get("created"), "paginated": _sp.get("paginated"),
                                         "reasons": (_sp.get("plan") or {}).get("reasons") or []}
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
            # 2026-10-22（4.6）：分页页**反列本轮产出** —— 知识→分页→会话双向可溯 ✓
            _register_session_output(kb_id, session_slug, slug, title)
        except Exception as exc:  # noqa: BLE001
            warnings.append("会话溯源边写入失败：%s" % str(exc)[:160])
    return {"applied": True, "kb_id": kb_id, "slug": slug, "title": title, "mode": mode,
            "created": res.get("created"), "version": res.get("after_version"),
            # 2026-10-21 修：原实现漏回 `before_version` ⇒ 外层 `page_versions[].before`
            # **永远是 null**（实测回执 `{'action':'created','before':None}` ✗），
            # 调用方无法判断"是新建还是幂等更新" ⇒ 幂等性不可验证 ✗。这里如实回传 ✓。
            "before_version": res.get("before_version"),
            "graph": graph, "warnings": warnings,
            # P3-0d：回传"已构建好的"正文/属性，供批量路径一条 UNWIND 写图节点
            "content": res.get("content"), "attrs": res.get("attrs")}


def _parse_legacy_head(content: str) -> tuple[dict, str]:
    """解析 legacy 页头 → `({字段: 值}, 去掉头部的正文)`。

    legacy 页头形如：`# 标题` + `> **本体类型**：目的（\\`bmm:Goal\\`）` +
    `> **生成方式**：领域建模智能体（未指定来源文档）` + `> **首个版本生成**：<时间>（\\`tag\\`）`。

    为什么要解析（P0-c-2，2026-10-10 用户口径：内核要写「生成方式」、「首个版本生成」**只在新建页写**）：
    切内核后页头由 `_contract_head` 重写，其中这两项的**值必须沿用 legacy 的**，否则
    ① 每次写库「首个版本生成」都会变成当前时间；②「生成方式」丢失。
    实测（`_p0c2_diff.py`）：解析 + 回填后，重写正文与库里现有正文**逐字一致**（差异 0 行）。
    """
    lines = (content or "").splitlines()
    head: dict = {}
    i = 0
    if i < len(lines) and lines[i].lstrip().startswith("# "):
        i += 1
    while i < len(lines) and not lines[i].strip():
        i += 1
    while i < len(lines):
        s = lines[i].strip()
        if not (s.startswith("> **") and ("：" in s or ":" in s)):
            break
        body_txt = s[4:]
        sep = "：" if "：" in body_txt else ":"
        key, _, val = body_txt.partition(sep)
        head[key.strip().strip("*").strip()] = val.strip()
        i += 1
    while i < len(lines) and not lines[i].strip():
        i += 1
    return head, "\n".join(lines[i:])



def strip_heads(content: str) -> tuple[dict, str]:
    """剥掉**所有**页头块（legacy 头 + 契约头可能被重复累积，实测出现过 9 段）✓。

    返回 `(首个 head 字段字典, 去掉全部页头后的正文)`。
    判据：页头块以 `# 标题` 起、紧跟 `> **本体类型**…`，且**含 `首个版本生成`** 这一行 ——
    只认这种块，避免把正文自己的 `# 标题` 或引用块误删 ✗。
    """
    head_all: dict = {}
    body = content or ""
    for _ in range(20):                      # 上限保护（正常 1–2 次即收敛）
        head, rest = _parse_legacy_head(body)
        if "首个版本生成" not in head:
            break
        for k, v in head.items():
            head_all.setdefault(k, v)
        body = rest
    return head_all, body


def first_version_text(kb_id: str, slug: str) -> str:
    """**「首个版本生成」的权威值** = 该页**首次落库时间**（DB `created_at`，格式化为 +0800）。

    为什么不取页头/`page_metadata`（2026-10-26 用户实测）：
    - 页头这一行会被重写，历史上被重置成**最新版本时间**；
    - `page_metadata.ontology.created_at` 也随写路径漂移过。
    DB 行的 `created_at` 是**首版时间的天花板事实**（同 slug 覆盖不新建行）⇒ 以它为准 ✓。
    """
    rows = ke_db.psql_csv(
        "SELECT to_char(created_at AT TIME ZONE 'Asia/Shanghai', 'YYYY-MM-DD HH24:MI:SS') AS t "
        "  FROM wiki_pages WHERE knowledge_base_id = %s AND slug = %s AND deleted_at IS NULL "
        " ORDER BY created_at LIMIT 1" % (ke_db.sql_str(kb_id), ke_db.sql_str(slug)))
    if rows and rows[0].get("t"):
        return "%s +0800" % rows[0]["t"]
    return ""


def repair_head(kb_id: str, slug: str, created_at: str = "", dry_run: bool = True) -> dict:
    """**修复页头**（2026-10-26，用户实测问题）：把重复累积的页头收敛成一段 + 校准「首个版本生成」。

    - 收敛：正文里若有多段页头（实测 9 段），只保留一段；
    - 校准：`created_at` 参数 > **DB 首版时间**（`first_version_text`）> 页头现值
      （页头现值可能本身就是被重置过的，所以排在 DB 之后 ✓）；
    - 写回走内核 `write_knowledge(mode="document")`（写序/版本/索引一致 ✓），默认 dry_run。
    """
    rows = ke_db.psql_csv(
        "SELECT title, page_type, content, COALESCE(page_metadata::text,'{}') AS meta "
        "  FROM wiki_pages WHERE knowledge_base_id = %s AND slug = %s AND deleted_at IS NULL"
        % (ke_db.sql_str(kb_id), ke_db.sql_str(slug)))
    if not rows:
        return {"ok": False, "error": "page_not_found", "slug": slug, "kb_id": kb_id}
    row = rows[0]
    content = row.get("content") or ""
    heads_before = content.count("**首个版本生成**")
    head, body = strip_heads(content)
    target = str(created_at or first_version_text(kb_id, slug)
                 or head.get("首个版本生成") or "").strip()
    try:
        meta = json.loads(row.get("meta") or "{}")
    except json.JSONDecodeError:
        meta = {}
    ont = dict(meta.get("ontology") or {})
    spec = {"slug": slug, "title": row.get("title") or "",
            "page_type": row.get("page_type") or "", "mode": "document",
            "wiki_content": body, "created_at": target,
            "attributes": dict(ont.get("attributes") or {}),
            "type_label": ont.get("class_label") or "",
            "source": {"doc_refs": [], "chunk_refs": []},
            "metadata_extra": {k: v for k, v in meta.items()
                               if k not in ("ontology", "projection", "anchor")}}
    res = write_knowledge(kb_id, spec, dry_run=dry_run, strict_source=False, sync_folders=False)
    return {"ok": True, "slug": slug, "heads_before": heads_before, "head_found": head,
            "created_at_used": target, "dry_run": dry_run,
            "applied": res.get("applied"), "before_version": res.get("before_version"),
            "content_preview": (res.get("content_preview") or "")[:300]}


def spec_from_page(page: dict, element: dict | None = None, *, source: dict | None = None,
                   mode: str = "", relations: list | None = None,
                   strip_legacy_head: bool = False) -> dict:
    """**legacy 页/要素 → 契约内核 spec**（P0-c-2，2026-10-10 适配器）。

    目的：让领域建模/设计那条 legacy 路径（`save_elements` 的 payload + 已渲染好的 page）
    能喂给内核 `write_knowledge(_batch)` —— 统一契约 §8 写序、顺路打开 `_bulk_write_pages`。
    **默认不接线**：`mode=document` 时内核会**再拼一段 `_contract_head`**、`entity` 时内核**重新
    渲染正文**，所以与 legacy 正文**不逐字相同**，必须先在真实库上做 diff（见 `_p0c2_diff.py`）
    再决定用哪种 mode（或先把渲染器统一）。

    字段映射（逐条对齐 legacy `save_elements`）：
      slug/title/page_type ← `page['slug'/'title'/'page_type']`
      wiki_content        ← `page['content']`（**仅 document 模式传**；entity 模式由内核渲染）
      attributes          ← `page_metadata.ontology.attributes`（无则 `element['attributes']`）
      relations           ← 参数 `relations` 或 `page['relations']`（须已解析出 `target_slug`）
      summary/source_refs/chunk_refs/metadata_extra ← 同名键（`metadata_extra` = page_metadata 里
      除 `ontology` 外的其余键，如 `design`）
    """
    import ke_ontology  # noqa: PLC0415
    page = dict(page or {})
    element = dict(element or {})
    page_type = (page.get("page_type") or element.get("type") or "").strip()
    meta = dict(page.get("page_metadata") or {})
    ont = dict(meta.get("ontology") or {})
    attrs = dict(ont.get("attributes") or element.get("attributes") or {})
    rels = relations if relations is not None else (page.get("relations") or element.get("relations") or [])
    # 本体**中文标签**：内核页头用 `spec["type_label"]`（`_contract_head`），legacy 也写它
    # （"目的（`bmm:Goal`）"）。不补这个字段，切内核后页头会从中文标签退化成 prefixed —— 实测这就是
    # `document+strip-head` 残余差异里的第一条（详见 `_p0c2_diff.py`）。
    try:
        type_label = (ke_ontology.class_meta().get(page_type) or {}).get("label") or ""
    except Exception:  # noqa: BLE001
        type_label = ""
    # 页头的**值**沿用 legacy 解析结果（内核要写「生成方式」；「首个版本生成」只在新建页写）
    _head, _legacy_body = _parse_legacy_head(page.get("content") or "")
    spec = {
        "slug": (page.get("slug") or element.get("slug") or "").strip(),
        "title": (page.get("title") or element.get("name") or "").strip(),
        "page_type": page_type,
        "type_label": type_label,
        "generated_by": (page.get("generated_by") or element.get("generated_by")
                         or _head.get("生成方式") or ""),
        "created_at": (page.get("created_at") or _head.get("首个版本生成")
                       or ont.get("created_at") or ""),
        "mode": (mode or "").strip().lower() or ke_ontology.render_mode(page_type),
        "attributes": attrs,
        "definition_key": page.get("definition_key", "definition"),
        "summary": (page.get("summary") or element.get("definition") or "")[:500],
        "relations": [{"type": r.get("type"), "target_slug": r.get("target_slug")}
                      for r in rels if r.get("type") and r.get("target_slug")],
        "source_refs": page.get("source_refs"),
        "chunk_refs": page.get("chunk_refs"),
        "source": dict(source or {}),
    }
    extra = {k: v for k, v in meta.items() if k != "ontology"}
    if extra:
        spec["metadata_extra"] = extra
    if spec["mode"] != "entity":          # document/raw 需要正文（legacy 已渲染好）
        spec["wiki_content"] = _legacy_body if strip_legacy_head else (page.get("content") or "")
    return spec


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
    （`replica_guard` 在调用方 `save_elements` 里做）。
    **2026-10-10 起已接线**：`write_knowledge(sink=[...])` 只构建不落库，`write_knowledge_batch`
    收集全批后**一次**调用本函数（整批 2 次 psql），回执 `kernel.bulk_write:{inserted,updated}`。
    """
    news = [x for x in items if not x.get("exists")]
    olds = [x for x in items if x.get("exists")]
    out = {"inserted": 0, "updated": 0}
    if news:
        # 2026-10-16（收编 `ke_import`）：列清单与 legacy `PAGE_COLUMNS` **逐列对齐**（24 列）。
        #   新增列全部走 `_opt_sql`/`_sql_id`：**没传就写 `DEFAULT`** ⇒ 老调用方（面板/会话/设计页）
        #   行为零变化；`ke_import` 委派后传什么写什么（uuid5 派生 id、status='published'、
        #   parent_slug/folder_id=''、sort_order=0、in_links=[]、last_editor_id=actor）。
        #   `ON CONFLICT DO NOTHING` 一并加：批量建模幂等（同 slug 重跑不再是硬错；老路径靠它）。
        cols = ("id, tenant_id, knowledge_base_id, slug, title, page_type, status, content, summary, "
                "parent_slug, folder_id, category_path, wiki_path, depth, sort_order, source_refs, "
                "chunk_refs, in_links, out_links, page_metadata, aliases, version, last_edit_source, "
                "last_editor_id")
        rows = ", ".join(
            "(%s, %d, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s, %d, %s, %s::jsonb, "
            "%s::jsonb, %s, '[]'::jsonb, %s::jsonb, %s::jsonb, 1, %s, %s)"
            % (_sql_id(x), int(x.get("tenant_id") or tenant_id or 0), ke_db.sql_str(kb_id),
               ke_db.sql_str(x["slug"]), ke_db.sql_str(x["title"]), ke_db.sql_str(x["page_type"]),
               _opt_sql(x, "status"), ke_db.sql_str(x["content"]),
               ke_db.sql_str(x.get("summary") or ""),
               _opt_sql(x, "parent_slug"), _opt_sql(x, "folder_id"),
               ke_db.sql_json(x.get("category_path") or []), ke_db.sql_str(x.get("wiki_path") or ""),
               len(x.get("category_path") or []), _opt_sql(x, "sort_order", "int"),
               ke_db.sql_json(_uniq_str(x.get("source_refs"))),
               ke_db.sql_json(_uniq_str(x.get("chunk_refs"))),
               _opt_sql(x, "in_links", "jsonb"),
               ke_db.sql_json(x.get("metadata") or {}),
               ke_db.sql_json(_page_aliases(x)),
               ke_db.sql_str(tag), _opt_sql(x, "editor_id")) for x in news)
        ke_db.psql("BEGIN;\nINSERT INTO wiki_pages (%s) VALUES %s ON CONFLICT DO NOTHING;\nCOMMIT;\n"
                   % (cols, rows), stdin=True)
        out["inserted"] = len(news)
    if olds:
        # 快照：逐条 `_snapshot_stmt` 拼（与单页版**逐字一致**）；与下面的 UPDATE 同一事务
        snap = "\n".join(_snapshot_stmt(kb_id, x["slug"], tag) for x in olds)
        vals = ", ".join(
            "(%s, %s, %s, %s, %s::jsonb, %s, %s::jsonb, %d, %s, %s::jsonb, %s::jsonb, %s::jsonb)"
            % (ke_db.sql_str(x["slug"]), ke_db.sql_str(x["content"]),
               ke_db.sql_str(x["title"]), ke_db.sql_str(x.get("summary") or ""),
               ke_db.sql_json(x.get("metadata") or {}), ke_db.sql_str(tag),
               ke_db.sql_json(x.get("category_path") or []), len(x.get("category_path") or []),
               ke_db.sql_str(x.get("wiki_path") or ""),
               ke_db.sql_json(_uniq_str(x.get("source_refs"))),
               ke_db.sql_json(_uniq_str(x.get("chunk_refs"))),
               ke_db.sql_json(_page_aliases(x))) for x in olds)
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
            "  '[]'::jsonb), "
            "aliases = COALESCE((SELECT jsonb_agg(DISTINCT e) FROM "
            "  jsonb_array_elements_text(COALESCE(p.aliases,'[]'::jsonb) || v.al::jsonb) AS t(e)), "
            "  '[]'::jsonb) "
            "FROM (VALUES %s) AS v(slug, content, title, summary, metadata, tag, cat, depth, wp, "
            "refs, cr, al) "
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
    items: list = []                     # P0-c-3：批量 PG 写（sink 收集，最后一次性写）
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
                                sync_folders=False, preloaded=_pre, skip_graph=not dry_run,
                                sink=None if dry_run else items)
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
    _graph_batch_ok = True
    if not dry_run and _node_rows:
        try:
            _kg.upsert_nodes_batch(kb_id, _node_rows)
        except Exception as exc:  # noqa: BLE001
            _graph_batch_ok = False       # 2026-10-17：图批写失败 ⇒ **跳过 PG 批写** ✓（fail-closed）
            edge_errors.append({"scope": "upsert_nodes_batch", "error": str(exc)[:160]})
    # ③-0b PG 页：**一次批量写**（P0-c-3，2026-10-10）—— 旧路径每页一个小事务
    #      （31 页 ≈ 31 × ~250ms ≈ 8 s，正是 apply 的主成本）；`_bulk_write_pages` = 新建一条
    #      多行 INSERT + 更新一条事务（快照 INSERT..SELECT + UPDATE..FROM(VALUES)），整批 **2 次 psql**。
    bulk_write = None
    if not dry_run and items and _graph_batch_ok:
        try:
            bulk_write = _bulk_write_pages(kb_id, items, _kb_tenant, TAG_CONTRACT)
        except Exception as exc:  # noqa: BLE001
            errors.append({"scope": "_bulk_write_pages", "error": str(exc)[:200]})
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
            "folders": folders, "bulk_write": bulk_write,
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
    """复用既有目录同步脚本（子进程，避免 ke-core ↔ ontology-mcp 的循环导入）。

    2026-10-17（用户口径 ✓）：**先重算 `category_path`（收敛历史坏值 ✓），再建树挂页 ✓** ——
    只建树不重算的话，页面上的坏值（如 `["bpmn:Task","bpmn:Task"]` ✗）会被永远照建出来 ✗。
    """
    try:
        import ke_category as _kc  # noqa: PLC0415
        _rec = _kc.recompute(kb_id, only_bad=True)
    except Exception as exc:  # noqa: BLE001
        _rec = {"ok": False, "error": str(exc)[:160]}
    import subprocess
    script = HERE.parents[0] / "ontology-mcp" / "sync_folders.py"
    if not script.is_file():
        return {"ok": False, "error": "找不到 %s" % script, "recompute": _rec}
    done = subprocess.run([sys.executable, str(script), "--kb-id", kb_id, "--link-pages"],
                          capture_output=True, text=True, encoding="utf-8", check=False)
    tail = (done.stdout or done.stderr or "").strip().splitlines()[-3:]
    return {"ok": done.returncode == 0, "tail": tail,
            "recompute": {k: _rec.get(k) for k in ("fixed", "unresolved", "was_empty", "error")}}


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


def retag_preview(kb_id: str, slug: str, new_type: str, pages: list | None = None) -> dict:
    """**只读**影响面：新 slug、引用页清单、会话命中、预计 violations、风险与 ticket。

    `pages`（P3-0h，2026-10-10）：调用方若**已持有**本库活页（`_kb_pages_raw` 的形状），
    传进来即可**免掉"每项一次全库页 dump"**（`retag_queue` 有多项时是个 pg 热点）。
    """
    import hashlib
    import json as _json

    new_type = (new_type or "").strip()
    meta_all = ke_ontology.class_meta()
    if new_type and new_type not in meta_all:
        raise ValueError("未知本体类型：%s" % new_type)
    page = _load_page(kb_id, slug)
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
    # P3-0h（2026-10-10）：**"已是最新状态"的快路径提到全库页 dump 之前** ——
    # 旧顺序先 `_kb_pages_raw()`（把全库页**含正文**拉一遍，每项 1 次 psql）再判 already，
    # 于是"根本不需要迁移"的项也白白拉全库（`retag_queue` 每项一次）→ pg 热点。
    if new_type == page["page_type"] and new_slug == slug:
        out["already"] = True
        out["conflict"] = None      # 新旧 slug 相同 → 不存在"目标被占用"（旧实现会把它误报为自己）
        out["ticket"] = ""
        out["apply_hint"] = "类型与 slug 都已是目标状态，无需迁移"
        return out

    # 只有**真的需要迁移**才拉全库页（调用方给了 `pages` 就直接复用，不再重拉）
    pages = pages if pages is not None else _kb_pages_raw(kb_id)
    conflict = next((p for p in pages if p["slug"] == new_slug), None)
    out["conflict"] = ({"slug": new_slug, "title": conflict.get("title", "")} if conflict else None)

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



