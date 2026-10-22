#!/usr/bin/env python3
"""`reference_lookup` 内网可用性（P5 · 内网问题 4.3）—— **真断言**（不再 skip）。

`urlopen error timed out` ✗ —— 无代理 / 无超时口径 / 无白名单 / 失败只有一句话。

落地（2026-10-22）：
- 配置：`BODHI_HTTP_PROXY` / `BODHI_REF_TIMEOUT`(默认 8s) / `BODHI_REF_HOSTS`(白名单)
- 失败返回**结构化** `{ok:false, found:false, reason: timeout|dns|tls|blocked|conn_refused|http_4xx|http_5xx}`
- `allow_fetch=false` + URL ⇒ `reason=fetch_disabled`（提示下一步，不静默）
- 技能侧：取不到 ⇒ 标「不可得/无法判定」，**不得编造** ✗

跑法：`python3 -m pytest tests/test_reference_lookup.py`（MCP 不可用 ⇒ skip）
"""
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

MARK = "参考规范正文MARK"


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        if self.path.startswith("/doc"):
            body = ("<html><head><style>x{}</style></head><body><h1>规范</h1>"
                    "<p>%s</p><script>bad()</script></body></html>" % MARK).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        self.send_error(404, "not found")

    def log_message(self, *a):  # noqa: A003  静音
        return


@pytest.fixture(scope="module")
def web():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    try:
        yield "http://127.0.0.1:%d" % srv.server_address[1]
    finally:
        srv.shutdown()


@pytest.fixture(scope="module")
def blackhole():
    """收下连接但**永不回包** ⇒ 触发读超时（验证 timeout 分类与总预算）"""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    s.listen(8)
    port = s.getsockname()[1]
    held = []

    def _run():
        while True:
            try:
                conn, _ = s.accept()
            except OSError:
                return
            held.append(conn)          # 不读不回

    threading.Thread(target=_run, daemon=True).start()
    try:
        yield "http://127.0.0.1:%d/doc.html" % port
    finally:
        for c in held:
            try:
                c.close()
            except OSError:
                pass
        s.close()


def test_url_200_returns_text(mcp, web):
    r = mcp.call("reference_lookup", {"reference": web + "/doc.html", "allow_fetch": True,
                                      "max_chars": 4000})
    assert r.get("ok") is True, r
    assert r.get("found") is True, r
    assert r.get("source") == "url", r
    assert r.get("http_status") == 200, r
    assert MARK in (r.get("text") or ""), r
    assert "<script" not in (r.get("text") or ""), "HTML 未剥离 ✗"


def test_url_404_is_structured(mcp, web):
    r = mcp.call("reference_lookup", {"reference": web + "/missing.html", "allow_fetch": True})
    assert r.get("found") is False, r
    assert r.get("reason") == "http_4xx", r
    assert r.get("http_status") == 404, r


def test_url_without_fetch_is_explicit(mcp, web):
    r = mcp.call("reference_lookup", {"reference": web + "/doc.html"})
    assert r.get("found") is False, r
    assert r.get("reason") == "fetch_disabled", r
    assert r.get("hint"), "应提示下一步（传 allow_fetch=true）✗"


def test_timeout_is_structured_and_bounded(mcp, blackhole):
    t0 = time.time()
    r = mcp.call("reference_lookup", {"reference": blackhole, "allow_fetch": True, "timeout": 0.5})
    elapsed = time.time() - t0
    assert r.get("found") is False, r
    assert r.get("reason") == "timeout", r
    assert int(r.get("attempts") or 0) >= 1, r
    assert elapsed < 20, "总预算超 20s ✗（反代 60s 会 504）"


def test_hosts_whitelist_blocks(monkeypatch):
    """白名单是**服务端 env**，MCP 侧无法逐例改 ⇒ 这里单测纯函数（不联网）✓"""
    import ke_review  # noqa: PLC0415

    monkeypatch.setenv("BODHI_REF_HOSTS", "allowed.example")
    r = ke_review._ref_fetch("http://blocked.example/doc.html", timeout=0.2)
    assert r["ok"] is False and r["reason"] == "blocked", r
    monkeypatch.setenv("BODHI_REF_HOSTS", "example")
    r2 = ke_review._ref_fetch("http://blocked.example/doc.html", timeout=0.2)
    assert r2["reason"] != "blocked", "子域后缀匹配应放行 ✓：%s" % r2



