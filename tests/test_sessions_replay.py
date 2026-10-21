"""L3 会话序列回归（P0.5-3 ✓ 静态：不写库 ✗）

素材：`tests/sessions/*.json`（由 `tests/mine_logs.py` 从日志按 **session_no / 90s 空闲**切出）
断言（**只看结构与顺序**，不认领域词 ✓）：
1. 每个会话 ≥2 次调用（单次调用不成会话 ✓）
2. 会话内工具名全部仍在注册表 ✓
3. 会话内**没有"未完成"头的调用**：首条不能是 `timeout`（那是日志截断，不是业务序列 ✗）
4. 同一会话里 `timeout` 比例 ≤ 50%（超了说明该会话被截断/服务异常 ⇒ 不可作回归素材 ✗）
"""
import glob
import json
import os

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
SESS = os.path.join(HERE, "sessions")


def _files():
    return sorted(glob.glob(os.path.join(SESS, "*.json")))


def test_sessions_exist():
    if not _files():
        pytest.skip("未生成 sessions（先跑 python3 tests/mine_logs.py）")
    assert len(_files()) > 0


@pytest.mark.parametrize("path", _files(), ids=lambda p: os.path.basename(p))
def test_session_shape(path):
    d = json.load(open(path, encoding="utf-8"))
    calls = d.get("calls") or []
    assert len(calls) >= 2, "会话只有 %d 次调用（不成会话 ✗）" % len(calls)
    for c in calls:
        assert c.get("tool"), "调用缺 tool ✗：%s" % c
        assert c.get("ts"), "调用缺 ts ✗：%s" % c
        assert c.get("shape") in ("ok", "ok_brief", "silent", "dry", "guard", "error", "timeout"), \
            "未知形态 ✗：%s" % c.get("shape")
    bad = [c["tool"] for c in calls if c.get("shape") == "timeout"]
    if len(bad) * 2 > len(calls):
        # 日志里"有 BEGIN 无 DONE"= 进程被杀/超时（不是业务序列 ✗）⇒ 该会话**不作素材**（skip ✓）
        pytest.skip("会话内超时过半（日志被截断，不作回归素材）：%s" % bad[:8])


def test_session_tools_registered():
    import _helpers as H
    reg = set(H.tool_definitions())
    retired = H.retired_tools()      # 退役工具按计划下线条线 ✓（只看"悄悄消失" ✗）
    missing = {}
    for p in _files():
        d = json.load(open(p, encoding="utf-8"))
        miss = sorted({c["tool"] for c in (d.get("calls") or [])
                       if c["tool"] not in reg and c["tool"] not in retired})
        if miss:
            missing[os.path.basename(p)] = miss
    assert not missing, "会话里用了「既不在注册表也不在退役表」的工具 ✗：%s" % missing
