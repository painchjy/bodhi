"""P2 #10 回归：`work_multi` 会话内首次写入锁定工作库（纯函数，无 DB）。"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(REPO, "tools", "ke-core"))

import ke_scope as S  # noqa: E402


def test_lock_work_kb_first_write_then_reject_other(tmp_path, monkeypatch):
    monkeypatch.setattr(S, "SCOPE_DIR", str(tmp_path))
    S._CACHE.clear()
    got = S.lock_work_kb("sid-work", {"id": "K1", "name": "库1"})
    assert got.get("id") == "K1", got
    assert got.get("locked_by") == "first_write", got
    snap = json.loads((tmp_path / "sid-work.json").read_text(encoding="utf-8"))
    assert snap["work_kb"]["id"] == "K1", snap
    # 第二次写别的库：仍返回第一次锁定的库（调用方据此拒绝 work_locked）
    got2 = S.lock_work_kb("sid-work", {"id": "K2", "name": "库2"})
    assert got2.get("id") == "K1", got2


def test_unlock_work_kb_clears_only_work_and_keeps_snapshot(tmp_path, monkeypatch):
    monkeypatch.setattr(S, "SCOPE_DIR", str(tmp_path))
    S._CACHE.clear()
    S.lock_work_kb("sid-unlock", {"id": "K1", "name": "库1"})
    got = S.unlock_work_kb("sid-unlock", reason="pytest", actor="test")
    assert got.get("ok") is True and got.get("unlocked") is True, got
    assert got.get("previous", {}).get("id") == "K1", got
    assert os.path.isfile(got.get("backup") or ""), got
    snap = json.loads((tmp_path / "sid-unlock.json").read_text(encoding="utf-8"))
    assert snap["work_kb"] == {}, snap
    assert "locked_at" not in snap, snap
    assert snap.get("unlock_audit", [])[0].get("previous", {}).get("id") == "K1", snap
    again = S.unlock_work_kb("sid-unlock", reason="pytest", actor="test")
    assert again.get("ok") is True and again.get("unlocked") is False, again
    assert S.work_multi({"work_multi": True}) is True
    assert S.work_multi({"work_multi": False}) is False
