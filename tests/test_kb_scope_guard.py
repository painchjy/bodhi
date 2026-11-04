#!/usr/bin/env python3
"""kb-any 统一校验（`srv.handlers._kb_scope_guard`）的纯逻辑回归。

为什么用单测：真库里目前没有「selected 且 scope 一致」的智能体（评审测试智能体的提示词=正式库、
挂载=测试库 ⇒ `scope_conflict`），所以拦截路径无法用真会话验证；这里 mock `agent_scope`。
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(REPO, "tools", "ontology-mcp"))
sys.path.insert(0, os.path.join(REPO, "tools", "ke-core"))

import ke_db  # noqa: E402
import ke_scope  # noqa: E402
import srv.handlers as H  # noqa: E402


def _mock(monkeypatch, scope):
    monkeypatch.setattr(ke_scope, "agent_scope",
                        lambda sid, use_cache=True: scope)
    monkeypatch.setattr(ke_db, "resolve_kb_id",
                        lambda raw: (str(raw), "库-" + str(raw), ""))


def _scope(allowed, method="", mode="selected"):
    return {"ok": True, "allowed": list(allowed), "kb_mode": mode,
            "agent_kbs": [{"id": x, "name": x.upper()} for x in allowed],
            "method_kb": ({"id": method, "name": "方法层"} if method else {})}


def test_write_out_of_scope_denied(monkeypatch):
    _mock(monkeypatch, _scope(["a"]))
    r = H._kb_scope_guard("audit_purge", {"session_no": "s", "kb_id": "b"})
    assert r and r["error"] == "out_of_scope", r


def test_write_in_scope_allowed(monkeypatch):
    _mock(monkeypatch, _scope(["a"]))
    assert H._kb_scope_guard("audit_purge", {"session_no": "s", "kb_id": "a"}) is None


def test_read_allows_method_kb(monkeypatch):
    """只读工具额外允许方法层库（任务页在那里）——写工具不允许。"""
    _mock(monkeypatch, _scope(["a"], method="m"))
    assert H._kb_scope_guard("doc_outline", {"session_no": "s", "kb_id": "m"}) is None
    r = H._kb_scope_guard("page_attributes_set", {"session_no": "s", "kb_id": "m"})
    assert r and r["error"] == "out_of_scope", r


def test_no_session_or_no_kb_passes(monkeypatch):
    _mock(monkeypatch, _scope(["a"]))
    assert H._kb_scope_guard("audit_purge", {"kb_id": "b"}) is None
    assert H._kb_scope_guard("audit_purge", {"session_no": "s"}) is None


def test_scope_not_ok_passes(monkeypatch):
    """scope 不可用（scope_conflict / 无会话）⇒ 不阻断（各工具自己的身份门禁仍在）。"""
    _mock(monkeypatch, {"ok": False, "error": "scope_conflict"})
    assert H._kb_scope_guard("audit_purge", {"session_no": "s", "kb_id": "b"}) is None


def test_unlisted_tool_passes(monkeypatch):
    _mock(monkeypatch, _scope(["a"]))
    assert H._kb_scope_guard("context_scan", {"session_no": "s", "kb_id": "b"}) is None


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-q"]))
