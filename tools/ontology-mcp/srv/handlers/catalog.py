"""本体面/技能/任务切片（A1-7）：`ontology_types` / `skills` / `job_status`。

迁移来源：`server.py::_call_tool_impl` 里对应的 `if name == "…"` 分支（**参数解析逐字照搬**）✓
实现：`srv/meta.py`（本体面 + 技能目录）、`srv/jobs.py`（异步任务表）

注意：`skills()` 内部会调 `ontology_types()` 收窄本体面 —— 两者在**同一个模块**里，所以这里不用管依赖顺序 ✓
"""
from __future__ import annotations

import ke_analytics
import ke_ontology

from srv.handlers import handle
from srv.jobs import job_status
from srv.meta import ontology_types, skills


@handle("ontology_paths")
def ontology_paths_tool(args: dict) -> dict:
    return ke_ontology.ontology_paths(
        str(args.get("from_class", "")), str(args.get("to_class", "")),
        int(args.get("max_hops", 3) or 3), int(args.get("limit", 50) or 50))


_OLD_MAPPING_KEYS = ("object_type", "object_id", "fact_id", "dimension_id",
                     "canonical_id", "service_id")


def _legacy_args(args: dict) -> dict:
    used = [k for k in _OLD_MAPPING_KEYS if k in (args or {})]
    if not used:
        return {}
    return {"ok": False, "status": "invalid_argument", "error": "legacy_argument",
            "legacy_keys": used,
            "hint": "请改用 mapping_object_type / mapping_object_id / mapping_service_id"}


@handle("mapping_lookup")
def mapping_lookup_tool(args: dict) -> dict:
    bad = _legacy_args(args)
    if bad:
        return bad
    return ke_analytics.mapping_lookup(
        str(args.get("mapping_object_type", "")),
        str(args.get("mapping_object_id", "")),
        str(args.get("mapping_service_id", "")))


@handle("scenario_lookup")
def scenario_lookup_tool(args: dict) -> dict:
    bad = _legacy_args(args)
    if bad:
        return bad
    return ke_analytics.scenario_lookup(
        str(args.get("scenario_id", "")),
        str(args.get("mapping_object_id", "")),
        str(args.get("mapping_service_id", "")), str(args.get("intent", "")))


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
