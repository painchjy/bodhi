"""P5 · 内网问题 4.6 —— **会话分页判定与溯源**（1-C 口径，全部走 MCP）。

用户口径（2026-10-22）：
- 分页有**判断规则**：换智能体 / 换知识库 / 换文档 / 换用户角色（**强信号**）⇒ 开新分页；
  用户**意图变化**（弱信号）⇒ 必须确认（问用户是否进入新任务），确认后才继续写；
- 变化要**留证据**（写在分页页 `page_metadata.session_part` + 正文「## 分页理由」）；
- 知识→分页→会话**双向可溯**：知识页 `bmm:sourceLocator` 必带 `/pN`，分页页反列「## 本轮产出」。

跑法：`python3 -m pytest tests/test_session_paging.py`（MCP/DB 不可用 ⇒ skip）
"""
import json

import pytest


def _write(mcp, kb, session_no, *, slug, title, doc_id, intent, ack=False):
    return mcp.call("save_knowledge", {
        "kb_id": kb, "stage": "report", "mode": "apply", "session_no": session_no,
        "doc_id": doc_id, "intent": intent, "acknowledge_intent_change": ack,
        "report": {"title": title, "slug": slug,
                   "content_md": "# %s\n\n## ① 范围与依据\n- 分页用例\n" % title}})


def _part(mcp, kb, session_no):
    """当前会话在库里的**最大分页**（slug, part_no, content）。"""
    import ke_db
    rows = ke_db.psql_csv(
        "SELECT slug, COALESCE(content,'') AS c, COALESCE(page_metadata::text,'{}') AS md "
        "  FROM wiki_pages WHERE knowledge_base_id = %s AND page_type = 'bmm:KnowledgeSession' "
        "   AND deleted_at IS NULL AND (content LIKE %s OR slug LIKE %s)"
        % (ke_db.sql_str(kb), ke_db.sql_str("%" + session_no + "%"),
           ke_db.sql_str("session/" + session_no + "%")))
    assert rows, "没有会话页 ✗"
    best = max(rows, key=lambda r: int((r["slug"].rsplit("/p", 1)[-1] or 1))
               if "/p" in r["slug"] else 1)
    return best


def test_strong_signal_doc_change_creates_new_part(mcp, test_kb, session_no):
    a = _write(mcp, test_kb, session_no, slug="review/_pg-a", title="分页用例A",
               doc_id="DOC-A", intent="任务甲")
    assert a.get("applied") is True, json.dumps(a, ensure_ascii=False)[:400]
    p1 = _part(mcp, test_kb, session_no)

    b = _write(mcp, test_kb, session_no, slug="review/_pg-b", title="分页用例B",
               doc_id="DOC-B", intent="任务甲")
    assert b.get("applied") is True, json.dumps(b, ensure_ascii=False)[:400]
    sp = (b.get("graph") or {}).get("session_page") or {}
    assert sp.get("paginated") is True, "换文档属强信号 ⇒ 必须开新分页 ✗：%s" % sp
    assert int(sp.get("part_no") or 0) == 2, sp
    sig = [r.get("signal") for r in (sp.get("reasons") or [])]
    assert "doc" in sig, "分页理由里必须有 doc 证据 ✗：%s" % sp.get("reasons")
    p2 = _part(mcp, test_kb, session_no)
    assert p2["slug"] != p1["slug"], "分页 slug 未变 ✗"


def test_weak_signal_intent_change_requires_ack(mcp, test_kb, session_no):
    _write(mcp, test_kb, session_no, slug="review/_pg-c", title="分页用例C",
           doc_id="DOC-B", intent="任务甲")
    r = _write(mcp, test_kb, session_no, slug="review/_pg-d", title="分页用例D",
               doc_id="DOC-B", intent="任务乙")          # 意图变了、未 ack
    assert r.get("applied") is not True, "意图变化未确认就写进去了 ✗：%s" % \
        json.dumps(r, ensure_ascii=False)[:300]
    assert r.get("need_session_part") is True, r
    assert (r.get("session_part") or {}).get("action") == "need_confirm", r.get("session_part")
    assert r.get("ask_user"), "必须让调用方去问用户 ✗"


def test_ack_intent_change_writes_into_same_part(mcp, test_kb, session_no):
    _write(mcp, test_kb, session_no, slug="review/_pg-e", title="分页用例E",
           doc_id="DOC-B", intent="任务甲")
    before = _part(mcp, test_kb, session_no)["slug"]
    ok = _write(mcp, test_kb, session_no, slug="review/_pg-f", title="分页用例F",
                doc_id="DOC-B", intent="任务丙", ack=True)   # 用户确认"仍在原任务"
    assert ok.get("applied") is True, json.dumps(ok, ensure_ascii=False)[:400]
    sp = (ok.get("graph") or {}).get("session_page") or {}
    assert sp.get("paginated") is not True, "ack 后不应再开新分页 ✗：%s" % sp
    after = _part(mcp, test_kb, session_no)
    assert after["slug"] == before, "ack 后换页了 ✗"
    assert "任务丙" in after["md"], "确认后的意图应记进分页证据（否则每次都会再问）✗"


def test_locator_has_part_no(mcp, test_kb, session_no):
    _write(mcp, test_kb, session_no, slug="review/_pg-g", title="分页用例G",
           doc_id="DOC-G", intent="任务甲")
    r = mcp.call("knowledge_lookup", {"kb_id": test_kb, "mode": "keyword",
                                      "query": "分页用例G"})
    hit = next((h for h in (r.get("hits") or []) if h.get("slug") == "review/_pg-g"), None)
    assert hit, "知识页未落库 ✗：%s" % json.dumps(r, ensure_ascii=False)[:300]
    loc = str((hit.get("attributes") or {}).get("bmm:sourceLocator") or "")
    assert loc.startswith("session/%s/p" % session_no), "sourceLocator 必须带 /pN ✗：%r" % loc


def test_part_page_lists_outputs(mcp, test_kb, session_no):
    _write(mcp, test_kb, session_no, slug="review/_pg-h", title="分页用例H",
           doc_id="DOC-H", intent="任务甲")
    part = _part(mcp, test_kb, session_no)
    assert "## 本轮产出" in part["c"], "分页页缺「## 本轮产出」（溯源反列）✗"
    assert "review/_pg-h" in part["c"], "反列里没有刚写的知识页 ✗"
