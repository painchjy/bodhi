"""本体面/技能/任务切片（A1-7）：`ontology_types` / `skills` / `job_status`。

迁移来源：`server.py::_call_tool_impl` 里对应的 `if name == "…"` 分支（**参数解析逐字照搬**）✓
实现：`srv/meta.py`（本体面 + 技能目录）、`srv/jobs.py`（异步任务表）

注意：`skills()` 内部会调 `ontology_types()` 收窄本体面 —— 两者在**同一个模块**里，所以这里不用管依赖顺序 ✓
"""
from __future__ import annotations

from srv.handlers import handle
from srv.jobs import job_status
from srv.meta import ontology_types, skills


@handle("ontology_types")
def ontology_types_tool(args: dict) -> dict:
    return ontology_types(str(args["model"]), str(args.get("focus", "")),
                          args.get("classes") or None, args.get("relations") or None,
                          bool(args.get("with_attributes", True)))


@handle("skills")
def skills_tool(args: dict) -> dict:
    return skills(str(args.get("skill", "")), str(args.get("model", "")))


@handle("job_status")
def job_status_tool(args: dict) -> dict:
    return job_status(str(args.get("job_id", "")))
