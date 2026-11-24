"""管理台同步与语义 apply 共用 knowledge_save 批量内核（DB-free）。"""
from __future__ import annotations

import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools", "ke-core"))

import ke_analytics  # noqa: E402
import ke_pages  # noqa: E402
import ke_semantic  # noqa: E402


def _fact_spec():
    return {
        "fact_id": "fact_x", "code": "x", "name": "XFact", "grain": "k",
        "description": "d", "base_metrics": [], "derived_metrics": [],
        "fact_fields": [], "dimensions": [], "mapping_services": [],
        "mapping_object_type": "fact", "mapping_object_id": "fact_x",
    }


def _scenario_spec():
    return {
        "scenario_id": "sc_semantic_x", "name": "X 场景", "description": "d",
        "database_type": "postgresql", "source_locator": "page:x",
        "source_sql": "select 1", "semantic_json": {"version": 1,
                                                    "answers": {"labels": {"a": "甲"}},
                                                    "definitions": {"columns": []}},
        "joins": [], "filters": [], "facts": [], "common_services": [],
        "service_id": "svc_x", "service_name": "svc", "status": "active",
    }


@pytest.fixture
def batch_calls(monkeypatch):
    calls = []

    def fake_batch(kb_id, specs, **kwargs):
        calls.append({"kb_id": kb_id, "specs": specs, **kwargs})
        return {"applied_count": len(specs),
                "pages": [{"slug": p["slug"], "applied": True} for p in specs],
                "errors": []}

    monkeypatch.setattr(ke_pages, "write_knowledge_batch", fake_batch)
    monkeypatch.setattr(ke_pages, "sync_folders", lambda kb: None)
    monkeypatch.setattr(ke_analytics, "fact_spec", lambda _id: _fact_spec())
    monkeypatch.setattr(ke_analytics, "scenario_spec", lambda _id: _scenario_spec())
    monkeypatch.setattr(ke_analytics, "_semantic_edges", lambda: [])
    return calls


def test_admin_sync_and_apply_both_hit_batch(batch_calls, monkeypatch):
    # 管理台 /api/sync → sync_pages 默认 batch=true → sync_pages_affected
    admin = ke_analytics.sync_pages("kb", kind="scenario", ids=["sc_semantic_x"],
                                     dry_run=False)
    assert admin["written"] == 1
    assert len(batch_calls) == 1
    assert [p["slug"] for p in batch_calls[0]["specs"]] == [
        "analytics/scenario/sc_semantic_x"]

    plan = {
        "ok": True, "ready_to_apply": True, "plan_id": "plan-1",
        "actions": [{"kind": "base_metric", "data": {"code": "a"}}],
        "plan": {"fact_id": "fact_x", "fact_dimensions": []},
        "scenario": {"service_id": "svc_x"},
    }
    after = {"ok": True, "actions": [], "ready_to_apply": True}
    monkeypatch.setattr(ke_semantic, "_write_enabled", lambda: True)
    monkeypatch.setattr(ke_semantic, "_action_sql", lambda _a: "SELECT 1;")
    monkeypatch.setattr(ke_semantic.ke_db, "psql", lambda *a, **k: "")
    monkeypatch.setattr(ke_analytics, "mapping_lookup", lambda *a, **k: {"ok": True})
    monkeypatch.setattr(ke_analytics, "scenario_lookup", lambda **k: {"ok": True, "count": 1})
    # apply 成功后重新 plan：用 side_effect 保证第二次返回空动作
    calls = {"n": 0}
    def fake_plan(*args, **kwargs):
        calls["n"] += 1
        return plan if calls["n"] == 1 else after
    monkeypatch.setattr(ke_semantic, "_plan_core", fake_plan)

    out = ke_semantic.scenario_semantic_apply(
        scenario_id="sc_semantic_x", kb_id="kb", plan_id="plan-1",
        confirm=True, dry_run=False)
    assert out["ok"] and out["applied"], out
    assert len(batch_calls) == 2
    assert any(p["slug"] == "analytics/fact/x" for p in batch_calls[1]["specs"])
    assert any(p["slug"] == "analytics/scenario/sc_semantic_x"
               for p in batch_calls[1]["specs"])
