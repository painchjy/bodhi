"""domain 任务选择契约：按活动分组 + goal/io/roles，不返回任务正文。"""

import json


def _tasks(resp):
    out = []
    for proc in (resp.get("processes") or []):
        for act in (proc.get("activities") or []):
            out.extend(act.get("tasks") or [])
    for task in ((resp.get("unassigned") or {}).get("tasks") or []):
        out.append(task)
    return out


def test_domain_groups_tasks_with_io_and_roles(mcp, kbs, review_session_no):
    r = mcp.call("knowledge_lookup", {"mode": "domain", "query": "",
                                      "session_no": review_session_no})
    assert r.get("ok") is True, json.dumps(r, ensure_ascii=False)[:800]
    tasks = _tasks(r)
    assert tasks, json.dumps(r, ensure_ascii=False)[:800]
    assert "items" not in r, "domain 仍返回扁平 items（会重复活动/任务）"
    assert "relations" not in r, "domain 仍返回重复 relations"
    for t in tasks:
        assert t.get("activity_id") is not None
        assert t.get("goal"), t
        io = t.get("io") or {}
        assert io.get("entry"), t
        assert io.get("exit"), t
        roles = t.get("roles") or []
        assert isinstance(roles, list), t
        assert any(x.get("slug") and x.get("title") for x in roles), t
    assert r.get("role_source") == "graph", r.get("role_source")
    assert "content_md" not in json.dumps(r, ensure_ascii=False)
    assert int((r.get("counts") or {}).get("task_io_chars") or 0) < 5000


def test_domain_query_keeps_parent_group(mcp, kbs, review_session_no):
    r = mcp.call("knowledge_lookup", {"mode": "domain", "query": "逐项评审",
                                      "session_no": review_session_no})
    assert r.get("ok") is True, r
    tasks = _tasks(r)
    assert tasks and all("逐项评审" in (t.get("title") or "") for t in tasks), tasks
    assert r.get("processes"), "过滤任务后父流程/活动被丢掉"
    for proc in r.get("processes") or []:
        for act in proc.get("activities") or []:
            if any("逐项评审" in (t.get("title") or "") for t in act.get("tasks") or []):
                assert act.get("title"), act


def test_domain_selected_task_page_has_steps(mcp, kbs, review_session_no):
    r = mcp.call("knowledge_lookup", {"mode": "domain", "query": "",
                                      "session_no": review_session_no})
    tasks = _tasks(r)
    assert tasks, r
    slug = tasks[0].get("slug")
    p = mcp.call("knowledge_lookup", {"mode": "page", "query": slug,
                                      "session_no": review_session_no})
    assert p.get("ok") is True, p
    page = p.get("page") or {}
    assert page.get("content_md"), p
    assert page.get("attributes") is not None, p
