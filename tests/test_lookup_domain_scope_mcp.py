"""domain 模式 + scope 的 MCP 集成回归（2026-11-17 口径）。"""
import json


def _call(mcp, args):
    return mcp.call("knowledge_lookup", args)


def test_domain_ignores_explicit_kb(mcp, kbs, review_session_no):
    """有 session：domain 固定查 domain_kb，传 work 库也只回 domain。"""
    r = _call(mcp, {"mode": "domain", "kb_id": kbs["work"] or "技术方案评审工作库",
                    "session_no": review_session_no, "query": ""})
    assert r.get("ok") is True, json.dumps(r, ensure_ascii=False)[:500]
    assert r.get("kb", {}).get("id") == kbs["domain"], r
    assert (r.get("kb_ignored") or {}).get("given"), r


def test_domain_without_session_is_rejected(mcp):
    """domain 不给 session：必须拒绝，不得用 kb_id 兜底。"""
    r = _call(mcp, {"mode": "domain", "kb_id": "技术方案评审领域", "query": ""})
    assert r.get("ok") is not True, r
    assert r.get("error") in ("kb_scope_required", "invalid_params", "need_session"), r


def test_non_domain_out_of_scope_still_rejected(mcp, kbs, review_session_no):
    """非 domain 模式仍按 allowed 拒绝范围外库。"""
    r = _call(mcp, {"mode": "keyword", "kb_id": "企业本体模型",
                    "session_no": review_session_no, "query": "_t"})
    assert r.get("ok") is False, r
    assert r.get("error") in ("out_of_scope", "kb_unresolved"), r
