"""metric_compute（P3.5-3）：指标可复算（同输入同 hash）+ 每个指标带 formula 口径。"""
import json


def test_metric_compute_recomputable(mcp, kbs):
    r = mcp.call("metric_compute", {"kb_id": kbs["domain"]})
    assert r.get("ok") is True, json.dumps(r, ensure_ascii=False)[:300]
    metrics = r.get("metrics") or []
    assert metrics, "无指标 ✗"
    names = {m.get("name") for m in metrics}
    assert "pages_total" in names, "缺 pages_total ✗：%s" % sorted(names)
    assert all(m.get("formula") for m in metrics), "有指标缺 formula 口径 ✗"
    assert isinstance(r.get("rows"), list) and r["rows"], "缺明细 rows ✗"
    r2 = mcp.call("metric_compute", {"kb_id": kbs["domain"]})
    assert r2.get("hash") == r.get("hash"), "同输入两次 hash 不一致（不可复算 ✗）"
    assert len(str(r["hash"])) == 40, "hash 不是 sha1 ✗"
