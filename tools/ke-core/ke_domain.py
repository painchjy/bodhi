"""domain 模式的**任务选择器**（页面级只读）。

为什么单独成模块（2026-11-18 用户口径）：domain 不再把所有任务页正文塞给智能体，
而是按 流程 → 活动 → 任务 分组；任务只带 goal / entry / exit / roles，
智能体先用这些字段选 1 个任务，再用 knowledge_lookup(mode="page") 取该任务步骤。

本模块不引入领域名词清单：所有类/属性/关系都来自本体的通用 BPMN 结构。
"""
from __future__ import annotations

import json
import re

import ke_db
import ke_graph

CLASSES = ("bmm:BusinessProcess", "bpmn:Activity", "bpmn:Task", "bpmn:Role")
IO_MAX_CHARS = 1200


def _aliases(value) -> list:
    try:
        got = json.loads(value or "[]")
        return [x for x in got if str(x or "").strip()] if isinstance(got, list) else []
    except Exception:  # noqa: BLE001
        return []


def _clip(value, cap: int = IO_MAX_CHARS) -> tuple[str, bool]:
    text = str(value or "").strip()
    if len(text) <= cap:
        return text, False
    return text[:cap].rstrip() + "…", True


def _task_io(attrs: dict) -> tuple[dict, bool]:
    entry, et = _clip(attrs.get("bpmn:activityEntry"))
    exit_, xt = _clip(attrs.get("bpmn:activityExit"))
    return {"entry": entry, "exit": exit_,
            "entry_truncated": et, "exit_truncated": xt}, (not entry and not exit_)


def domain_view(kb: str, query: str = "", limit: int = 200) -> dict:
    """返回按活动分组的任务选择视图（只读；不返回任务正文）。"""
    q = str(query or "").strip()
    kws = [x for x in re.split(r"[\s,，、]+", q) if x][:10]
    rows = ke_db.psql_csv(
        "SELECT slug, title, page_type, COALESCE(aliases::text,'[]') AS aliases, "
        "       COALESCE(page_metadata->'ontology'->'attributes'->>'bpmn:taskGoal','') AS task_goal, "
        "       COALESCE(page_metadata->'ontology'->'attributes'->>'bpmn:activityEntry','') AS task_entry, "
        "       COALESCE(page_metadata->'ontology'->'attributes'->>'bpmn:activityExit','') AS task_exit, "
        "       COALESCE(page_metadata->'ontology'->'attributes'->>'bpmn:activityId','') AS activity_id "
        "  FROM wiki_pages WHERE deleted_at IS NULL AND knowledge_base_id = %s "
        "   AND page_type IN (%s) ORDER BY page_type, slug LIMIT %d"
        % (ke_db.sql_str(kb), ", ".join(ke_db.sql_str(c) for c in CLASSES),
           max(200, int(limit or 200))))
    raw = {str(r.get("slug") or ""): r for r in rows if r.get("slug")}
    nodes = {slug: {"type": str(r.get("page_type") or ""), "slug": slug,
                    "title": str(r.get("title") or ""), "aliases": _aliases(r.get("aliases"))}
             for slug, r in raw.items()}
    relations = []
    if nodes:
        try:
            rel = ke_graph._run(
                "MATCH (n:BodhiInstance {kb_id:$kb})-[r]->(m:BodhiInstance {kb_id:$kb}) "
                "WHERE n.slug IN $slugs RETURN n.slug AS s, type(r) AS t, m.slug AS o LIMIT 400",
                {"kb": kb, "slugs": list(nodes)[:400]}) or []
            relations = [{"from": x.get("s"), "type": x.get("t"), "to": x.get("o")}
                         for x in rel]
        except Exception:  # noqa: BLE001  图不可用不影响骨架；角色会显式标 role_source=unavailable
            relations = []
    activity_tasks, process_activities, task_roles = {}, {}, {}
    for rel in relations:
        src, typ, dst = (str(rel.get("from") or ""), str(rel.get("type") or ""),
                         str(rel.get("to") or ""))
        if not (src and typ and dst):
            continue
        if typ == "bpmn:activityContainsTask":
            activity_tasks.setdefault(src, []).append(dst)
        elif typ == "bpmn:processHasActivity":
            process_activities.setdefault(src, []).append(dst)
        elif typ == "bpmn:taskHasRole":
            task_roles.setdefault(src, []).append(dst)

    def _role(slug: str) -> dict:
        n = nodes.get(slug) or {}
        return {"slug": slug, "title": str(n.get("title") or slug.rsplit("/", 1)[-1])}

    def _task(slug: str):
        n, r = nodes.get(slug) or {}, raw.get(slug) or {}
        if n.get("type") != "bpmn:Task":
            return None
        io, io_missing = _task_io({"bpmn:activityEntry": r.get("task_entry"),
                                   "bpmn:activityExit": r.get("task_exit")})
        roles, seen = [], set()
        for rid in (task_roles.get(slug) or []):
            if rid and rid not in seen:
                seen.add(rid)
                roles.append(_role(rid))
        out = {**n, "activity_id": str(r.get("activity_id") or ""),
               "goal": str(r.get("task_goal") or "").strip(),
               "io": io, "roles": roles}
        if io_missing:
            out["io_missing"] = True
        if not roles:
            out["role_missing"] = True
        return out

    task_objs = {s: _task(s) for s in nodes if (nodes.get(s) or {}).get("type") == "bpmn:Task"}
    task_objs = {s: t for s, t in task_objs.items() if t}
    if kws:
        def _match_task(t):
            fields = [t.get("slug") or "", t.get("title") or "",
                      " ".join(t.get("aliases") or []), t.get("goal") or "",
                      (t.get("io") or {}).get("entry") or "",
                      (t.get("io") or {}).get("exit") or "",
                      " ".join((r.get("title") or "") for r in (t.get("roles") or []))]
            hay = " ".join(fields).lower()
            return any(str(w).lower() in hay for w in kws)
        task_objs = {s: t for s, t in task_objs.items() if _match_task(t)}

    task_parents = {}
    for act_slug, task_slugs in activity_tasks.items():
        for task_slug in task_slugs:
            if task_slug in task_objs:
                task_parents.setdefault(task_slug, []).append(act_slug)
    for task_slug, parents in task_parents.items():
        uniq = list(dict.fromkeys(parents))
        if len(uniq) > 1:
            task_objs[task_slug]["other_activities"] = [
                {"slug": a, "title": (nodes.get(a) or {}).get("title") or a} for a in uniq[1:]]
        task_parents[task_slug] = uniq[:1]

    activity_out = {}
    for slug, n in nodes.items():
        if n.get("type") != "bpmn:Activity":
            continue
        task_slugs = list(dict.fromkeys(activity_tasks.get(slug) or []))
        tasks = [task_objs[t] for t in task_slugs if t in task_objs]
        if kws and not tasks:
            continue
        activity_out[slug] = {**n, "tasks": tasks}

    process_out, used_activities = [], set()
    for slug, n in nodes.items():
        if n.get("type") != "bmm:BusinessProcess":
            continue
        activities = []
        for act_slug in dict.fromkeys(process_activities.get(slug) or []):
            if act_slug in activity_out:
                activities.append(activity_out[act_slug])
                used_activities.add(act_slug)
        if kws and not activities:
            continue
        process_out.append({**n, "activities": activities})

    unassigned_activities = [a for s, a in activity_out.items() if s not in used_activities]
    unassigned_tasks = [t for s, t in task_objs.items() if not task_parents.get(s)]
    shown_tasks = [t for p in process_out for a in (p.get("activities") or [])
                   for t in (a.get("tasks") or [])] + unassigned_tasks
    shown_roles = {r.get("slug") for t in shown_tasks for r in (t.get("roles") or [])}
    task_io_chars = sum(len((t.get("goal") or "")) + len((t.get("io") or {}).get("entry") or "")
                        + len((t.get("io") or {}).get("exit") or "") for t in shown_tasks)
    next_txt = ("先用 tasks[].goal / io.entry / io.exit / roles 选择 1 个任务；"
                "再用 mode=page, query=<processes[].activities[].tasks[].slug> 读取该任务步骤。不要 page 全部任务。"
                if shown_tasks else "没有匹配任务：去掉 query 重试，或先确认领域库是否已有任务页")
    return {"mode": "domain",
            "processes": process_out,
            "unassigned": {"activities": unassigned_activities, "tasks": unassigned_tasks},
            "task_io_schema": {
                "entry": "输入要求：dataobject:<slug> / text:<locator> / ontology:<Class> [rules=...]",
                "exit": "输出要求：dataobject:<slug> / text:<locator> / ontology:<Class>",
                "usage": "按 goal + entry/exit + roles 选择任务；选中后才用 page 读步骤"},
            "selection": {"max_task_pages": 1,
                          "fields": ["goal", "io.entry", "io.exit", "roles"],
                          "page_args": {"mode": "page", "query": "<processes[].activities[].tasks[].slug>"},
                          "instruction": "不要 page 全部任务；每轮只选一个任务"},
            "counts": {"processes": len(process_out), "activities": len(activity_out),
                       "tasks": len(shown_tasks), "roles": len(shown_roles),
                       "items": len(process_out) + len(activity_out) + len(shown_tasks),
                       "relations": len(relations), "task_io_chars": task_io_chars},
            "role_source": "graph" if relations else "unavailable",
            "next": next_txt}
