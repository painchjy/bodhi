"""domain 模式库范围回归（2026-11-17 用户口径）。

- 有 session：固定用 scope.domain_kb，**完全忽略 kb_id**；
- 无 session：必须报 kb_scope_required；
- scope 没有 domain_kb：报 domain_kb_missing，不回退到 allowed 第一个库；
- 非 domain 显式 kb_id 解析失败：报 kb_unresolved，不静默回退。
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(REPO, "tools", "ke-core"))

import ke_db  # noqa: E402
import ke_lookup  # noqa: E402


def _scope(domain="d", allowed=None):
    return {"ok": True, "domain_kb": {"id": domain, "name": "领域库"} if domain else {},
            "work_kb": {}, "allowed": allowed or ([domain] if domain else []),
            "kb_mode": "all", "agent_kbs": [], "method_kb": {}}


def _empty_query(monkeypatch):
    monkeypatch.setattr(ke_db, "psql_csv", lambda sql: [])


def test_domain_uses_domain_kb_and_ignores_given_kb(monkeypatch):
    _empty_query(monkeypatch)
    r = ke_lookup._lookup_impl(kb_id="other-kb", mode="domain", session_no="sid",
                               scope=_scope(), query="")
    assert r.get("ok") is True, r
    assert r["kb"]["id"] == "d", r
    assert r.get("kb_ignored", {}).get("given") == "other-kb", r


def test_domain_with_out_of_scope_kb_still_queries_domain(monkeypatch):
    _empty_query(monkeypatch)
    r = ke_lookup._lookup_impl(kb_id="outside", mode="domain", session_no="sid",
                               scope=_scope(allowed=["d"]), query="")
    assert r.get("ok") is True and r["kb"]["id"] == "d", r
    assert not r.get("kb_check"), "domain 不应再因 kb_id 产生 kb_check"


def test_domain_requires_session_even_with_kb(monkeypatch):
    _empty_query(monkeypatch)
    r = ke_lookup._lookup_impl(kb_id="other-kb", mode="domain", scope=_scope(), query="")
    assert r.get("ok") is False and r.get("error") == "kb_scope_required", r


def test_domain_missing_domain_kb_is_error(monkeypatch):
    _empty_query(monkeypatch)
    r = ke_lookup._lookup_impl(mode="domain", session_no="sid",
                               scope=_scope(domain="", allowed=["w"]), query="")
    assert r.get("ok") is False and r.get("error") == "domain_kb_missing", r


def test_non_domain_unresolved_kb_is_not_silently_defaulted(monkeypatch):
    _empty_query(monkeypatch)

    def _raise(raw):
        raise ValueError("not found")

    monkeypatch.setattr(ke_db, "resolve_kb_id", _raise)
    r = ke_lookup._lookup_impl(kb_id="unknown", mode="keyword", session_no="sid",
                               scope=_scope(), query="x")
    assert r.get("ok") is False and r.get("error") == "kb_unresolved", r
