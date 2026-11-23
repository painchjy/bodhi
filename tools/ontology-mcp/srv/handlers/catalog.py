"""本体面/技能/任务切片（A1-7）：`ontology_types` / `skills` / `job_status`。

迁移来源：`server.py::_call_tool_impl` 里对应的 `if name == "…"` 分支（**参数解析逐字照搬**）✓
实现：`srv/meta.py`（本体面 + 技能目录）、`srv/jobs.py`（异步任务表）

注意：`skills()` 内部会调 `ontology_types()` 收窄本体面 —— 两者在**同一个模块**里，所以这里不用管依赖顺序 ✓
"""
from __future__ import annotations

import ke_analytics
import ke_ontology
import ke_semantic

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


@handle("fact_match")
def fact_match_tool(args: dict) -> dict:
    return ke_analytics.fact_match(
        args.get("metrics"), args.get("fact_tables"), args.get("intent", ""),
        args.get("filters"), args.get("group_by"),
        args.get("classification_fields"), str(args.get("mapping_service_id", "")),
        int(args.get("top_k", 5) or 5))


@handle("scenario_lookup")
def scenario_lookup_tool(args: dict) -> dict:
    bad = _legacy_args(args)
    if bad:
        return bad
    return ke_analytics.scenario_lookup(
        str(args.get("scenario_id", "")),
        str(args.get("mapping_object_id", "")),
        str(args.get("mapping_service_id", "")), str(args.get("intent", "")))


@handle("scenario_semantic_validate")
def scenario_semantic_validate_tool(args: dict) -> dict:
    return ke_semantic.scenario_semantic_validate(
        str(args.get("scenario_id", "")),
        str(args.get("mapping_service_id", "")))


@handle("scenario_semantic_plan")
def scenario_semantic_plan_tool(args: dict) -> dict:
    return ke_semantic.scenario_semantic_plan(
        str(args.get("scenario_id", "")), str(args.get("fact_id", "")),
        args.get("answers") or {}, str(args.get("mapping_service_id", "")))


@handle("scenario_semantic_apply")
def scenario_semantic_apply_tool(args: dict) -> dict:
    return ke_semantic.scenario_semantic_apply(
        str(args.get("scenario_id", "")), str(args.get("kb_id", "")),
        str(args.get("fact_id", "")), args.get("answers") or {},
        str(args.get("plan_id", "")), bool(args.get("confirm", False)),
        bool(args.get("dry_run", True)), str(args.get("mapping_service_id", "")),
        str(args.get("session_no", "")))


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
