"""rule_publish（P3.5-2）：dry_run 回执给规则集 hash_before/after，且同输入可复算。"""
import json


SPEC = "policy:技术方案逐项评审策略"


def test_rule_publish_dry_run_hash_stable(mcp, kbs):
    r = mcp.call("rule_publish", {"kb_id": kbs["domain"], "spec": SPEC})
    assert isinstance(r, dict), "回执不是对象 ✗"
    for k in ("hash_before", "hash_after", "changed", "apply", "published", "rules_before"):
        assert k in r, "回执缺 %s ✗：%s" % (k, json.dumps(r, ensure_ascii=False)[:240])
    assert len(str(r["hash_before"])) == 40, "hash 不是 sha1 ✗：%s" % r["hash_before"]
    assert r["apply"] is False and r["published"] is False, "dry_run 不得写库 ✗"

    r2 = mcp.call("rule_publish", {"kb_id": kbs["domain"], "spec": SPEC})
    assert r2["hash_before"] == r["hash_before"], "同输入两次 hash 不一致（不可复算 ✗）"
    assert r2["hash_after"] == r["hash_after"]
