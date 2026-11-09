"""`work_locked` 门禁语义（纯函数，无 DB）：
- dry_run 只读：即使会话已锁到别的库，也只给 warning，不拒绝；
- apply 才拒绝，且没有锁时只返回“待落锁”，真正落锁在写成功之后。
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
for p in (os.path.join(REPO, "tools", "ke-core"),
          os.path.join(REPO, "tools", "ontology-mcp")):
    if p not in sys.path:
        sys.path.insert(0, p)

from srv.knowledge_write import _work_scope_decision  # noqa: E402

LOCKED = {"ok": True, "work_multi": True,
          "work_kb": {"id": "K1", "name": "库1", "locked_by": "first_write"},
          "allowed": ["K1", "K2"]}


def test_work_locked_dry_run_is_warning_not_refusal():
    refuse, warn, pending = _work_scope_decision(LOCKED, "K2", "dry_run")
    assert refuse == {}, refuse
    assert warn.get("code") == "work_kb_locked", warn
    assert pending == {}, pending


def test_work_locked_apply_is_refused():
    refuse, warn, pending = _work_scope_decision(LOCKED, "K2", "apply")
    assert refuse.get("refused") == "work_locked", refuse
    assert warn == {} and pending == {}, (warn, pending)


def test_apply_without_lock_is_pending_until_success():
    sc = {"ok": True, "work_multi": True, "work_kb": {}, "allowed": ["K2"]}
    refuse, warn, pending = _work_scope_decision(sc, "K2", "apply", "库2")
    assert refuse == {} and warn == {}, (refuse, warn)
    assert pending == {"id": "K2", "name": "库2"}, pending
    refuse, warn, pending = _work_scope_decision(sc, "K2", "dry_run", "库2")
    assert pending == {}, pending


def test_fixed_work_kb_still_rejects_other_kb_for_all_modes():
    sc = {"ok": True, "work_multi": False, "work_kb": {"id": "K1", "name": "库1"},
          "allowed": ["K1", "K2"]}
    for mode in ("dry_run", "apply"):
        refuse, _warn, pending = _work_scope_decision(sc, "K2", mode)
        assert refuse.get("refused") == "out_of_scope", (mode, refuse)
        assert pending == {}
