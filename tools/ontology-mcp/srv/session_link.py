"""会话身份注入 + 来源会话边（A1-8 迁自 `server.py`，**实现逐字未改**）

- `_pick_session_no`：从 参数 / `session` 字典 / `bmm:KnowledgeSession` 节点 / report 里挑**合法 uuid**；
- `_session_page_ref` / `_session_part_no`：会话页 → `(title, slug)` / `bmm:partNo`（带请求级 memo）；
- `_upsert_source_session_line` / `link_source_session`：写完后按 slug 补 `bmm:sourceSession` 双链（幂等）；
- `adopt_session`：把本轮落库结果记进会话索引（编号 + 游标 + 预算）。

身份**只由服务端注入**（客户端传值一律丢弃）；取不到身份 ⇒ 调用方拒绝写库（fail-closed ✓）。
"""
from __future__ import annotations

import re

import os
import sys

_CORE = os.path.abspath(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                     "..", "ke-core"))
if _CORE not in sys.path:
    sys.path.insert(0, _CORE)

from ke_db import psql_csv, sql_str  # noqa: E402
from srv.clock import now_text  # noqa: E402
from srv.constants import DEFAULT_ROUND_BUDGET  # noqa: E402
from srv.kbresolve import resolve_kb_id  # noqa: E402
from srv.reqctx import _req_cache  # noqa: E402
from srv.session import _session_ident  # noqa: E402
from srv.state import _state_path, load_domain_state, save_domain_state  # noqa: E402


def adopt_session(kb_id: str, session: dict | None, summary: dict) -> dict | None:
    """把本轮落库结果记进会话索引：给新建页编**会话编号**并回写 `session` 段。"""
    if not session:
        return None
    knowledge_id = str(session.get("knowledge_id") or "")
    state = load_domain_state(kb_id, knowledge_id)
    budget = int(session.get("budget_tokens") or state.get("budget_tokens") or DEFAULT_ROUND_BUDGET)
    cursor = int(session.get("cursor") or 0)
    dry = bool(summary.get("dry_run"))
    state["budget_tokens"] = budget
    if session.get("doc_title"):
        state["doc_title"] = str(session["doc_title"])
    assigned = []
    no_next = int(state.get("next_no") or 1)
    for entry in summary.get("created") or []:
        no = no_next
        entry["no"] = no
        no_next += 1          # 草稿计数：dry_run 也递增（只用于回执，不落盘）
        assigned.append({"no": no, "title": entry.get("name"), "slug": entry.get("slug"),
                         "type": entry.get("type")})
        if not dry:
            state["next_no"] = no_next
            state.setdefault("pages", []).append(
                {"no": no, "title": entry.get("name"), "slug": entry.get("slug"),
                 "type": entry.get("type"), "round_no": int(session.get("round_no") or 0),
                 "at": now_text()})
    if not dry:
        state.setdefault("rounds", []).append(
            {"round_no": int(session.get("round_no") or (len(state.get("rounds", [])) + 1)),
             "cursor": cursor, "budget_tokens": budget, "created": len(assigned), "at": now_text()})
        if session.get("next_cursor") is not None:
            state["cursor"] = int(session["next_cursor"])
        save_domain_state(kb_id, knowledge_id, state)
    return {"knowledge_id": knowledge_id, "budget_tokens": budget, "cursor": cursor,
            "assigned_no": assigned, "pages_total": len(state.get("pages", [])),
            "next_no": int(state.get("next_no") or 1), "next_cursor": state.get("cursor"),
            "dry_run": dry, "state_file": str(_state_path(kb_id, knowledge_id))}


# ---------------------------------------------------------------------------
# 身份注入 & 来源定位（2026-10-01，闭环一）
#   · 会话类节点（`bmm:KnowledgeSession`）的**租户/智能体身份只能由服务端注入**：
#     客户端（智能体/用户）传的同名属性一律丢弃 —— 否则对话内容即可伪造身份。
#   · 来源定位 `bmm:sourceLocator`：显式值优先，缺失时用 `chunk_index` 兜底成「片段 #n」。
# ---------------------------------------------------------------------------
IDENTITY_CLASS = "bmm:KnowledgeSession"


_UUID_RE = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
                      r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")


def _pick_session_no(session_no, session, nodes, report) -> str:
    """取**会话编号**（= WeKnora `sessions.id`，uuid）。

    来源优先级：显式参数 `session_no` → `session` 字典 → `bmm:KnowledgeSession` 节点属性 → `report`。
    只接受 **uuid 形态**的值：非 uuid 一律忽略（防止把「会话-2026-10-04」这种自编号当会话编号）。
    """
    cands = [session_no]
    if isinstance(session, dict):
        cands += [session.get(k) for k in ("session_no", "sessionNo", "id")]
    for nd in (nodes or []):
        if isinstance(nd, dict) and (nd.get("type") or "").strip() == IDENTITY_CLASS:
            at = nd.get("attributes") or {}
            cands += [at.get("sessionNo"), at.get("session_no"), nd.get("session_no")]
    if isinstance(report, dict):
        cands += [report.get("session_no"), report.get("sessionNo")]
    for c in cands:
        v = str(c or "").strip()
        if v and _UUID_RE.match(v):
            return v
    return ""


def _session_page_ref(kb_id: str, session_no: str) -> tuple[str, str]:
    """本会话在**目标库**里的会话页 → `(title, slug)`；优先**分页页**（partNo 最大的 p<n>）。

    用于把 `bmm:sourceSession`（**对象属性**）连到会话页 —— 工具侧不再把它当数据属性写。
    库里没有该会话页时返回 `("", "")`（**不编造**）。
    """
    sid = (session_no or "").strip()
    if not sid:
        return "", ""
    # P3-0f（2026-10-10）：**命中即 memo**。`save_knowledge` 建页阶段每页要一次 `bmm:partNo`
    # （`_session_part_no`）→ 实测 33 页 = **32 次**同一个查询（1 次 psql ≈ 150 ms，白花 ~4.8 s）。
    # **只缓存命中**（查不到时不缓存）：会话页可能正是**本批**要建的，写库后应重新查
    # （`save_elements` 写库后会 `_reset_request_cache()`，双保险）。
    memo = _req_cache()["session_ref"]
    key = (kb_id, sid)
    if key in memo:
        return memo[key]
    hit = ("", "")
    try:
        # 2026-10-04 修：**不能只按 slug 前缀 `session/<编号>` 找** —— 实测智能体常把会话页
        # 命名成 `bmm/knowledgesession/会话-XXX`（slug 里没有编号前缀）⇒ 匹配不到、自动边挂不上。
        # 改为：在本库的 `bmm:KnowledgeSession` 页里，按 **`bmm:sessionNo` 属性值**（正文里有
        # `bmm:sessionNo（会话编号…） = <uuid>`）或 slug 前缀匹配；**分页页优先**（`/p<n>`）。
        rows = psql_csv(
            "SELECT title, slug FROM wiki_pages WHERE knowledge_base_id = %s AND deleted_at IS NULL "
            "AND page_type = 'bmm:KnowledgeSession' AND (content LIKE %s OR slug LIKE %s) "
            "ORDER BY (slug LIKE '%%/p%%') DESC, length(slug) DESC LIMIT 1"
            % (sql_str(kb_id), sql_str("%" + sid + "%"), sql_str("session/" + sid + "%")))
        if rows:
            hit = ((rows[0].get("title") or "").strip(), (rows[0].get("slug") or "").strip())
    except Exception:  # noqa: BLE001
        hit = ("", "")
    # P3-0f：命中与**未命中**都记（本次实测：本库当时没有该会话页 → 33 页白问 32 次、
    # 每次 ~150 ms）。不担心"会话页稍后由本批创建"：`save_elements` 落库后会
    # `_reset_request_cache()`、`call_tool` 入口也会清 —— memo 生命周期严格限定在"写入之前"。
    memo[key] = hit
    return hit


def _session_part_no(kb_id: str, session_no: str) -> str:
    """会话分页序号（`bmm:partNo`）——`sourceLocator` 用它定位到**具体分页**。"""
    title, slug = _session_page_ref(kb_id, session_no)
    if not slug:
        return ""
    m = re.search(r"/p(\d+)$", slug)
    # 2026-10-22（4.6）：**起始页也算 p1** —— `sourceLocator` 必须精确到分页（`/pN` 不能缺 ✓）
    return m.group(1) if m else "1"


# ---------------------------------------------------------------------------
# 来源会话边（`bmm:sourceSession`）：**写入后按 slug 补双链**（2026-10-04 用户实测修正）
#   为什么不能用 payload 的 relations 自动追加：`ke_pages.relation_line()` 在**拿不到 slug**
#   时会把关系渲染成**纯文本**（`→ <标题>`）→ 页面 `in_links` 解析不到、会话页「被引用」为空
#   （用户实测："知识没有建立和会话的关系"）。
#   所以：① 写入时**不再**往 payload.relations 里塞（避免产生纯文本行）；
#         ② 写完后用本函数按 **slug** 补 `- 知识来源会话（`bmm:sourceSession`）→ [[slug|title]]`；
#         ③ 同一函数也是**显式补挂入口**（存量页补一次 / 巡检用）。
# ---------------------------------------------------------------------------
SOURCE_SESSION_REL = "bmm:sourceSession"
SOURCE_SESSION_LABEL = "知识来源会话"
REL_SECTION = "## 本体关系"
_SRC_SESS_LINE_RE = re.compile(r"^-\s*[^（(]*（`%s`）→\s*(.*)$" % re.escape(SOURCE_SESSION_REL))


def _upsert_source_session_line(content: str, line: str) -> tuple[str, bool]:
    """把来源会话行写进「## 本体关系」节（**幂等**：同目标已存在则替换成双链形态）。

    返回 `(新正文, 是否改动)`；没有该小节则**补建**小节。
    """
    lines = (content or "").splitlines()
    idx = next((i for i, l in enumerate(lines) if l.strip() == REL_SECTION), -1)
    if idx < 0:
        body = (content or "").rstrip() + "\n\n" + REL_SECTION + "\n\n" + line + "\n"
        return body, True
    # 节范围 = 标题后到下一个二级标题（或 EOF）
    end = next((j for j in range(idx + 1, len(lines)) if lines[j].startswith("## ")), len(lines))
    for j in range(idx + 1, end):
        m = _SRC_SESS_LINE_RE.match(lines[j].strip())
        if m:
            if lines[j].strip() == line:
                return content, False
            lines[j] = line
            return "\n".join(lines) + ("\n" if content.endswith("\n") else ""), True
    # 没有该行 → 插到标题后的第一个空行之后（保持小节排版）
    ins = idx + 1
    while ins < end and not lines[ins].strip():
        ins += 1
    lines.insert(ins, line)
    return "\n".join(lines) + ("\n" if content.endswith("\n") else ""), True


def _resolve_session_for(kb_id: str, session_no: str = "") -> tuple[str, str]:
    """`session_no`（WeKnora 会话 id，缺省取本线程已解析的会话身份）→ `(session_no, session_slug)`。

    解析不到 → 返回空串（**不阻断写**，由内核给 warn）。`session_no` 显式传入时优先
    （供 `review_apply` 等非 `save_knowledge` 路径——它们的会话身份**不由** `_IDENT` 承载）。
    """
    sno = str(session_no or "").strip()
    if not sno:
        se = _session_ident()
        sno = str(se.get("session_no") or "") if se.get("ok") else ""
    if not sno or not kb_id:
        return sno, ""
    try:
        kid, _ = resolve_kb_id(kb_id)
        _title, slug = _session_page_ref(kid, sno)
        return sno, (slug or "")
    except Exception:  # noqa: BLE001
        return sno, ""


def link_source_session(kb_id: str = "", session_no: str = "", slugs: list | None = None,
                        all_pages: bool = False, dry_run: bool = True) -> dict:
    """给知识页补 `bmm:sourceSession`（**双链**）→ **会话页（分页优先）**，并重建 `in_links`。

    - 目标会话页：优先 `session_no` 指定；未给则用**本库唯一的** `bmm:KnowledgeSession` 页（多于一页时拒，不猜）；
      分页存在时**优先分页**（`_session_page_ref` 已按 partNo 优先）。
    - 要补的页：`slugs` 指定；或 `all_pages=True` 时=本库所有**缺该行**的非会话页。
    - `dry_run=True`（默认）只出清单，不写库。
    """
    kid = (kb_id or "").strip()
    if not kid:
        return {"ok": False, "reason": "缺少 kb_id"}
    kb_id, note = resolve_kb_id(kid)
    if session_no:
        title, slug = _session_page_ref(kb_id, session_no)
    else:
        rows = psql_csv("SELECT title, slug FROM wiki_pages WHERE knowledge_base_id = %s AND deleted_at IS NULL "
                        "AND page_type = 'bmm:KnowledgeSession' ORDER BY (slug LIKE '%%/p%%') DESC, "
                        "length(slug) DESC" % sql_str(kb_id))
        if len(rows) > 1:
            return {"ok": False, "kb_id": kb_id, "reason": "本库有多个会话页且未指定 session_no（不猜）",
                    "candidates": [r["slug"] for r in rows]}
        title, slug = ((rows[0].get("title") or "").strip(), (rows[0].get("slug") or "").strip()) if rows else ("", "")
    if not slug:
        return {"ok": False, "kb_id": kb_id, "reason": "本库没有会话页（先在会话里落一张 bmm:KnowledgeSession 页）"}
    line = "- %s（`%s`）→ [[%s|%s]]" % (SOURCE_SESSION_LABEL, SOURCE_SESSION_REL, slug, title)
    if slugs:
        rows = psql_csv("SELECT slug, content, out_links FROM wiki_pages WHERE knowledge_base_id = %s AND deleted_at IS NULL "
                        "AND slug IN (%s) AND COALESCE(page_type,'') NOT IN ('bmm:KnowledgeSession', 'index')"
                        % (sql_str(kb_id), ", ".join(sql_str(s) for s in slugs)))
    else:
        # 2026-10-04 修正：**不要**只挑"没有该行"的页 —— 存量页常常**有行但是纯文本形态**
        # （`→ <标题>`，正是要修的那种）。统一取本库全部非会话页，由下面两步幂等判断。
        rows = psql_csv("SELECT slug, content, out_links FROM wiki_pages WHERE knowledge_base_id = %s AND deleted_at IS NULL "
                        "AND COALESCE(page_type,'') NOT IN ('bmm:KnowledgeSession', 'index')"
                        % sql_str(kb_id))
    changes = []
    if dry_run:
        changes = [{"slug": row["slug"]} for row in rows]
    elif rows:
        try:
            import ke_graph  # noqa: PLC0415
            # 2026-10-09 P0：**批量**（旧实现每页 2 次 PG + 2 次 Neo4j；40 页 = ~160 次往返）：
            #   ① 一次预读页信息 ② 一次 UNWIND 确保节点 ③ 一次 UNWIND 建全部 sourceSession 边
            targets = [str(row["slug"]) for row in rows]
            info = ke_graph.page_info_map(kb_id, targets + [slug])
            if slug not in info:
                info[slug] = {"pt": ke_graph._page_type(kb_id, slug), "title": title, "tenant": None}
            ke_graph.ensure_instances(kb_id, [
                {"slug": k, "name": (v or {}).get("title") or k.rsplit("/", 1)[-1],
                 "page_type": (v or {}).get("pt") or "", "tenant_id": (v or {}).get("tenant")}
                for k, v in info.items()])
            ke_graph._run(
                "UNWIND $slugs AS s MATCH (a:BodhiInstance {kb_id:$kb, slug:s}) "
                "MATCH (b:BodhiInstance {kb_id:$kb, slug:$t}) "
                "MERGE (a)-[r:`bmm:sourceSession`]->(b) RETURN count(r) AS n",
                {"kb": kb_id, "slugs": targets, "t": slug})
            changes = [{"slug": t, "added": True} for t in targets]
        except Exception as exc:  # noqa: BLE001
            changes = [{"slug": str(row["slug"]), "error": str(exc)[:120]} for row in rows]
    out = {"ok": True, "kb_id": kb_id, "session_page": {"slug": slug, "title": title},
           "dry_run": bool(dry_run), "changed": len(changes), "changes": changes[:50]}
    return out
