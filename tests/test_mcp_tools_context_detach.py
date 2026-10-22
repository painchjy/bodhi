"""`context_authority_apply{action:"detach"}`（P5 · 内网问题 4.5 / 方案 1-A）L1 用例。

口径（2026-10-22 用户裁定）：
- 解绑 = 把该库从概念页「## 权威与副本」表摘掉 + 副本页自述改 `role=detached`；
- **不删副本页**；解绑后该库**恢复可本地编辑**（写保护解除）；
- 幂等：再解绑一次 ⇒ `already_detached=true`。

链路（全部走 MCP）：save_knowledge ×2 库 → concept_apply（自预览取 ticket）→
authority decide → 副本写被拒 → detach 预览 → detach 执行 → 副本写通过 → 再 detach 幂等。
teardown 用 `context_concept_rollback` 删掉测试概念页并刷新缓存 ✓
"""
import json
import uuid

import pytest

from kb_factory import TestKB as _TestKB

SLUG = "review/_detach_%s" % uuid.uuid4().hex[:8]


def _wait_visible(mcp, kb_id: str, timeout: float = 90.0) -> None:
    """服务端 `ke_db` 有 60s 清单缓存 ⇒ 新建库要轮询到**服务端可见**再用 ✓"""
    import time
    t0 = time.time()
    while True:
        r = mcp.call("knowledge_lookup", {"kb_id": kb_id, "mode": "keyword", "query": "_t"})
        miss = (r.get("kb_unresolved") or r.get("need_kb_selection")
                or "知识库不存在" in str(r.get("error") or ""))
        if not miss:
            return
        if time.time() - t0 > timeout:
            pytest.skip("服务端 90s 内仍未看到测试库（kb 清单缓存 60s）")
        time.sleep(5)


def _save_page(mcp, kb_id: str, session_no: str, title: str) -> dict:
    return mcp.call("save_knowledge", {
        "kb_id": kb_id, "stage": "report", "mode": "apply", "session_no": session_no,
        "report": {"title": title, "slug": SLUG,
                   "content_md": "# %s\n\n## ① 范围与依据\n- 解绑用例\n" % title}})


def _concept_ticket(mcp, slug: str) -> dict:
    """`concept_apply` 会**自预览**：先空 ticket 拿到 ticket/risks，再带齐参数执行 ✓

    ⚠️ `concept_preview` 是重查询（~10s/次）⇒ 成功后把 ticket 从 `record` 文件名取回，
    **不要在 teardown 再调一次**（会白白多 10s+）✓
    """
    out: dict = {}
    ticket, ack = "", []
    for _ in range(3):
        out = mcp.call("context_concept_apply", {"slug": slug, "ticket": ticket,
                                                 "acknowledge_risks": ack, "actor": "pytest"})
        if out.get("ok") and out.get("applied"):
            rec = str(out.get("record") or "")
            if rec.endswith(".json"):
                out["ticket"] = rec.rsplit("/", 1)[-1][:-5]
            return out
        if out.get("required_risks"):
            ack = list(out["required_risks"])
        if out.get("ticket"):
            ticket = str(out["ticket"])
        if out.get("error") and not (out.get("need_repreview") or out.get("required_risks")):
            return out
    return out


def _decide(mcp, slug: str, master: str) -> dict:
    prev = mcp.call("context_authority", {"slug": slug, "master": master})
    assert not prev.get("error"), json.dumps(prev, ensure_ascii=False)[:400]
    return mcp.call("context_authority_apply", {
        "action": "decide", "slug": slug, "master": master, "ticket": prev.get("ticket"),
        "acknowledge_risks": prev.get("required_risks") or [], "actor": "pytest"})


def test_detach_lifts_replica_write_guard(mcp, test_kb, session_no):
    kb_a = test_kb
    f_b = _TestKB("detach_b")
    kb_b = f_b.create()
    concept_ticket = ""
    try:
        _wait_visible(mcp, kb_b)
        # 两库各建同名页 ⇒ 构成跨库同名组
        assert _save_page(mcp, kb_a, session_no, "解绑目标页").get("applied") is True
        assert _save_page(mcp, kb_b, session_no, "解绑目标页").get("applied") is True

        concept = _concept_ticket(mcp, SLUG)
        assert concept.get("ok") and concept.get("applied"), \
            json.dumps(concept, ensure_ascii=False)[:500]
        concept_ticket = str(concept.get("ticket") or "")

        d = _decide(mcp, SLUG, kb_a)
        assert d.get("ok") is True, json.dumps(d, ensure_ascii=False)[:400]

        # ① 解绑前：副本库写该页 ⇒ 被写保护拒绝
        blocked = _save_page(mcp, kb_b, session_no, "解绑目标页（副本本地改）")
        blob = json.dumps(blocked, ensure_ascii=False)
        assert blocked.get("applied") is not True, "副本页居然可写 ✗：%s" % blob[:300]
        assert ("权威副本" in blob) or ("authority_pull" in blob), blob[:300]

        # ② detach 预览（apply=false）⇒ 表里在册 + 风险 + ticket
        prev = mcp.call("context_authority_apply",
                        {"action": "detach", "slug": SLUG, "kb": kb_b, "apply": False})
        assert prev.get("ok") is True, json.dumps(prev, ensure_ascii=False)[:400]
        assert prev.get("in_table") is True, prev
        assert "replica_detach" in (prev.get("required_risks") or []), prev
        assert prev.get("ticket"), prev
        assert prev.get("detached") is not True, "预览不应真的解绑 ✗"

        # ③ 执行
        done = mcp.call("context_authority_apply", {
            "action": "detach", "slug": SLUG, "kb": kb_b, "ticket": prev["ticket"],
            "acknowledge_risks": prev.get("required_risks") or [], "actor": "pytest",
            "apply": True})
        assert done.get("ok") is True and done.get("detached") is True, \
            json.dumps(done, ensure_ascii=False)[:500]
        assert (done.get("replica") or {}).get("metadata") == "detached", done.get("replica")

        # ④ 解绑后：副本库可本地写 ✓
        ok = _save_page(mcp, kb_b, session_no, "解绑目标页（副本本地改）")
        assert ok.get("applied") is True, "解绑后副本页仍不可写 ✗：%s" % \
            json.dumps(ok, ensure_ascii=False)[:300]

        # ⑤ 登记表已摘掉该库
        after = mcp.call("context_authority", {"slug": SLUG})
        rep_kbs = {r.get("kb") for r in (after.get("replicas") or [])}
        assert kb_b not in rep_kbs, after.get("replicas")

        # ⑥ 幂等
        again = mcp.call("context_authority_apply",
                         {"action": "detach", "slug": SLUG, "kb": kb_b, "apply": True})
        assert again.get("already_detached") is True, \
            json.dumps(again, ensure_ascii=False)[:400]
    finally:
        if concept_ticket:
            mcp.call("context_concept_rollback", {"ticket": concept_ticket})
        f_b.drop()
