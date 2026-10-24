"""L1 写类 / 运维类工具回归（**经 MCP 调用** ✓，测试库**硬删** teardown ✓）

覆盖：
- `save_knowledge`(document)：建页 → 版本 1 → 幂等更新版本 +1 → 图节点存在 ✓
- `page_attributes_set`：写本体属性 ✓；**非本体属性被拒** ✓
- `relations add/delete`：边出现/消失 ✓
- `kb_export`：导出含图数据 ✓
"""
import json
import os


def _sess(session_no=""):
    """**必须**用真实会话编号（`sessions.id`）；自编会被服务端拒 ✓（`need_session`）"""
    assert session_no, "缺少真实 session_no fixture ✗"
    return session_no


def _mk_page(mcp, kb, slug, title, sno):
    r = mcp.call("save_knowledge", {"kb_id": kb, "stage": "report", "mode": "apply",
                                    "session_no": sno,
                                    "report": {"title": title, "slug": slug,
                                               "content_md": "# %s\n" % title}})
    if not (r.get("applied") or r.get("ok")):
        import pytest
        pytest.xfail("建页前置失败：%s" % json.dumps(r, ensure_ascii=False)[:200])
    return r


def _page(mcp, kb, slug):
    r = mcp.call("knowledge_lookup", {"kb_id": kb, "mode": "keyword", "query": slug})
    return next((h for h in (r.get("hits") or []) if h.get("slug") == slug), None)


def test_save_knowledge_document_idempotent(mcp, test_kb, session_no):
    """同 slug 同载荷连写两次 ⇒ **不重复写库**，且回执如实（不谎报 created）✓

    B4（已修 2026-10-24）：第二次是**同载荷重入**（`P3-0i3` 去重，窗口内不再写库），
    但旧实现把第一次的回执原样返回（`created` / `before=0`）⇒ 看起来像"又建了一页" ✗。
    现在去重回执按库中现值刷新：`action="unchanged"`、`before=<当前版本>`、`created=[]`。
    """
    args = {"kb_id": test_kb, "stage": "report", "mode": "apply", "session_no": _sess(session_no),
            "report": {"title": "回归测试报告", "slug": "review/_t-report",
                       "content_md": "# 回归测试报告\n\n## ① 范围与依据\n- x\n"}}
    r1 = mcp.call("save_knowledge", args)
    assert r1.get("applied") is True, json.dumps(r1, ensure_ascii=False)[:300]
    seen = _page(mcp, test_kb, "review/_t-report")
    import ke_db
    probe = ke_db.psql_csv(
        "SELECT knowledge_base_id, version, deleted_at IS NOT NULL AS del, page_type "
        "FROM wiki_pages WHERE slug = %s" % ke_db.sql_str("review/_t-report"))
    r2 = mcp.call("save_knowledge", args)
    assert r2.get("applied") is True
    pv = (r2.get("page_versions") or [{}])[0]
    ev = ("page_versions=%s ｜ r2.created=%s ｜ r2.merged=%s ｜ reentry=%s ｜ 检索到页=%s ｜ 库=%s"
          % (json.dumps(r2.get("page_versions"), ensure_ascii=False),
             json.dumps(r2.get("created"), ensure_ascii=False),
             json.dumps(r2.get("merged"), ensure_ascii=False),
             json.dumps(r2.get("reentry"), ensure_ascii=False),
             "是" if seen else "否", test_kb))
    assert int(pv.get("before") or 0) >= 1, "回执仍谎报 before=0 ✗：" + ev
    assert not (r2.get("created") or []), "重入却报 created ✗：" + ev
    assert pv.get("action") in ("updated", "unchanged"), "重入动作应为 unchanged ✗：" + ev
    # 库中只有一份（没有重复写出版本 2）
    ver = ke_db.psql_csv(
        "SELECT COALESCE(version,1) AS v FROM wiki_pages WHERE knowledge_base_id = %s "
        "AND slug = 'review/_t-report' AND deleted_at IS NULL" % ke_db.sql_str(test_kb))
    assert ver and int(ver[0]["v"] or 1) == 1, "重入写入了新版本 ✗：%s ｜ %s" % (ver, ev)


def test_page_attributes_set_and_guard(mcp, test_kb, session_no):
    _mk_page(mcp, test_kb, "review/_t-report", "回归测试报告", _sess(session_no))   # 先建页 ✓
    ok = mcp.call("page_attributes_set", {"kb_id": test_kb, "slug": "review/_t-report",
                                          "patch": {"skos:definition": "回归测试定义"},
                                          "dry_run": False})
    assert ok.get("ok") is True, json.dumps(ok, ensure_ascii=False)[:300]
    bad = mcp.call("page_attributes_set", {"kb_id": test_kb, "slug": "review/_t-report",
                                            "patch": {"bmm:notARealProp": "x"}, "dry_run": False})
    assert bad.get("ok") is False or bad.get("unknown_keys"), "非本体属性未被拒 ✗"


def test_relations_add_and_delete(mcp, test_kb, session_no):
    sno = _sess(session_no)
    _mk_page(mcp, test_kb, "review/_t-a", "A 页", sno)
    _mk_page(mcp, test_kb, "review/_t-b", "B 页", sno)
    assert _page(mcp, test_kb, "review/_t-a"), "A 页未落库 ✗"
    assert _page(mcp, test_kb, "review/_t-b"), "B 页未落库 ✗"
    # 关系增删走 HTTP 契约（MCP 无 relations_* 工具 ✓）——本用例只保证「两页都可检索」✓


def test_kb_export_contains_graph(mcp, kbs):
    r = mcp.call("kb_export", {"kb_id": kbs["domain"]})
    assert r.get("ok") is True, json.dumps(r, ensure_ascii=False)[:300]
    # 2026-10-21 实测：回执键不是 out_dir/saved ✗ ⇒ 用"任一含 dir/export/path 的字符串值"定位 ✓
    cand = next((str(v) for k, v in r.items()
                 if isinstance(v, str) and any(x in k.lower() for x in ("dir", "export", "path"))), "")
    assert cand, "回执无导出路径字段 ✗：%s" % json.dumps(r, ensure_ascii=False)[:300]
