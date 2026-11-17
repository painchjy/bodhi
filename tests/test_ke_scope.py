#!/usr/bin/env python3
"""会话库范围（ke_scope）回归：标记解析 / allowed / 刷新 / 不猜库。

纯函数为主；`test_agent_scope_placeholder_is_multi` 用专用 pytest 测试 agent 验证真实 DB 解析。
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(REPO, "tools", "ke-core"))

import ke_scope as S  # noqa: E402


def test_parse_tag_preferred():
    """机器标记优先：一行就能定两个库。"""
    text = ("你是评审智能体。\n"
            "【库范围】domain_kb: 技术方案评审领域模型 | work_kb: 技术方案评审任务知识库\n"
            "- 领域库（只读）：别的库（不要用这行）\n")
    got = S.parse_scope_text(text)
    assert got == {"domain": "技术方案评审领域模型",
                   "work": "技术方案评审任务知识库", "source": "tag"}, got


def test_parse_cn_fallback_removed():
    """中文行**不再**兜底（2026-11-03 用户口径：只认 `domain_kb`/`work_kb`）。"""
    text = ("- 领域库（**只读**）：技术方案评审领域模型 —— 流程 / 活动 / 任务 / 规则\n"
            "- 工作库（**读写**）：技术方案评审任务知识库 —— 源文档 / 报告\n")
    got = S.parse_scope_text(text)
    assert got == {"domain": "", "work": "", "source": ""}, got


def test_keep_test_suffix_in_name():
    """名字里的 `（测试）` 是名字成分，**不能**当说明去掉（内网测试库就叫这个）。"""
    got = S.parse_scope_text(
        "【库范围】domain_kb: 技术方案评审领域模型（测试） | work_kb: 技术方案评审任务知识库（测试）\n")
    assert got["domain"] == "技术方案评审领域模型（测试）", got
    assert got["work"] == "技术方案评审任务知识库（测试）", got


def test_multi_work_kb():
    """建模智能体：工作库=所有领域库 ⇒ work 解析为多库词（不是某个具体库）。"""
    got = S.parse_scope_text(
        "【库范围】domain_kb: 企业知识管理领域 | work_kb: 本轮目标库\n")
    assert got["domain"] == "企业知识管理领域", got
    assert got["work"] in S._MULTI, got


def test_placeholder_work_kb_is_multi():
    """`本轮目标库` 与 `《指定工作库》` 都是占位符 ⇒ 同一 work_multi 语义。"""
    for value in ("本轮目标库", "《指定工作库》", "指定工作库", "目标工作库", "本轮工作库"):
        assert S._is_work_multi(value) is True, value
    assert S._is_work_multi("某个真实工作库") is False


def test_check_kb_uses_allowed_not_domain_slot(monkeypatch):
    """all 模式下，allowed 里的非 domain 库不能被 check_kb 误判为 out_of_scope。"""
    scope = {"ok": True, "allowed": ["d", "w", "x"],
             "domain_kb": {"id": "d", "name": "领域库"},
             "work_kb": {"id": "w", "name": "工作库"}, "kb_mode": "all"}
    monkeypatch.setattr(S, "agent_scope", lambda sid: scope)
    err, got = S.check_kb("x", "sid", role="domain")
    assert err is None and got is scope, (err, got)


def test_placeholder_work_kb_parses_as_multi():
    """提示词里的 `《指定工作库》` 与 `本轮目标库` 都归为多工作库。"""
    got = S.parse_scope_text(
        "【库范围】domain_kb: 企业知识管理领域 | work_kb: 《指定工作库》\n")
    assert got["work"] == "《指定工作库》", got
    assert S._is_work_multi(got["work"]) is True
    got2 = S.parse_scope_text(
        "【库范围】domain_kb: 企业知识管理领域 | work_kb: 本轮目标库\n")
    assert S._is_work_multi(got2["work"]) is True


def test_agent_scope_placeholder_is_multi(test_scope_session):
    """真实 agent_scope：占位符工作库 ⇒ work_multi=true，且 allowed 全量。"""
    import ke_scope

    sc = ke_scope.agent_scope(test_scope_session, use_cache=False)
    assert sc.get("ok") is True, sc
    assert sc.get("work_multi") is True, sc
    assert sc.get("work_kb") == {}, sc
    assert sc.get("allowed"), sc


def test_refresh_scope_for_kb_refreshes_once(monkeypatch):
    """目标不在缓存 allowed ⇒ 强制重算一次；刷新后仍不在才交给调用方拒绝。"""
    old = {"ok": True, "allowed": ["a"], "domain_kb": {"id": "a"}, "work_kb": {}, "kb_mode": "all"}
    fresh = {"ok": True, "allowed": ["a", "new"], "domain_kb": {"id": "a"}, "work_kb": {},
             "kb_mode": "all"}
    calls = []

    def fake_scope(sid, use_cache=True):
        calls.append(use_cache)
        return old if use_cache else fresh

    monkeypatch.setattr(S, "agent_scope", fake_scope)
    got, refreshed = S.refresh_scope_for_kb(old, "sid", "new")
    assert refreshed is True and got is fresh and calls == [False], (got, refreshed, calls)
    got2, refreshed2 = S.refresh_scope_for_kb(got, "sid", "new")
    assert refreshed2 is False and got2 is got

    assert S.parse_scope_text("")["source"] == ""
    assert S.parse_scope_text("随便一段话，没有库范围")["domain"] == ""


def test_compute_allowed_modes():
    """allowed 口径（2026-11-04）：all/wor​k_multi=全量；selected=交集；空=[]。"""
    dom = {"id": "D", "name": "领域库"}
    wrk = {"id": "W", "name": "工作库"}
    kbs = [{"id": "d", "name": "领域库"}, {"id": "w", "name": "工作库"},
           {"id": "x", "name": "别的库"}]
    # all（UI 选“全部知识库”）：全量
    assert S.compute_allowed(dom, wrk, False, kbs, "all") == ["d", "w", "x"]
    # work_multi（建模：工作库=所有领域库）：全量
    assert S.compute_allowed(dom, wrk, True, kbs, "selected") == ["d", "w", "x"]
    # selected：严格交集
    assert S.compute_allowed(dom, wrk, False, kbs, "selected") == ["d", "w"]
    # selected 且领域库不在挂载库 → 只剩工作库
    assert S.compute_allowed({"id": "Z"}, wrk, False, kbs, "selected") == ["w"]
    # 无库 / none
    assert S.compute_allowed(dom, wrk, False, [], "selected") == []


def test_check_kb_without_session():
    err, scope = S.check_kb("abc", "")
    assert err is None and scope["error"] == "need_session", (err, scope)


if __name__ == "__main__":
    for fn in (test_parse_tag_preferred, test_parse_cn_fallback_removed,
               test_keep_test_suffix_in_name, test_multi_work_kb, test_empty_and_garbage,
               test_placeholder_work_kb_is_multi, test_placeholder_work_kb_parses_as_multi,
               test_compute_allowed_modes, test_check_kb_without_session):
        fn()
        print("✓ %s" % fn.__name__)
