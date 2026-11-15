"""VLM 请求体落盘 + 响应结构诊断（纯函数，不调网络）。"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools" / "ke-core"))
import ke_doc_parse as P  # noqa: E402


def test_dump_body_writes_exact_bytes_and_attempt_name() -> None:
    raw = json.dumps({"model": "m", "messages": [{"content": "中文"}]},
                     ensure_ascii=False).encode("utf-8")
    with tempfile.TemporaryDirectory() as td:
        base = str(Path(td) / "req")
        path = P._vlm_dump_body(raw, base, 1)
        assert path and path.endswith(".attempt1.json")
        assert Path(path).read_bytes() == raw
        assert json.loads(Path(path).read_text(encoding="utf-8"))["model"] == "m"


def test_response_diag_explains_empty_choices() -> None:
    raw = b'{"choices": [], "usage": {}}'
    diag = P._vlm_response_diag(
        {"choices": [], "usage": {}}, 200, "application/json", raw, include_head=True)
    assert diag["http_status"] == 200
    assert diag["choices_count"] == 0
    assert diag["response_keys"] == ["choices", "usage"]
    assert diag["response_bytes"] == len(raw)
    assert diag["response_head"].startswith("{")


def test_dump_disabled_by_default(monkeypatch) -> None:
    monkeypatch.delenv("BODHI_VLM_DUMP_REQUEST", raising=False)
    assert P._vlm_dump_enabled() is False
    monkeypatch.setenv("BODHI_VLM_DUMP_REQUEST", "1")
    assert P._vlm_dump_enabled() is True


def test_vlm_chat_dumps_actual_body(monkeypatch) -> None:
    import urllib.request

    class _Resp:
        status = 200
        headers = {"Content-Type": "application/json"}

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self):
            return b'{"choices": [{"message": {"content": ""}, "finish_reason": ""}], "usage": {}}'

    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: _Resp())
    with tempfile.TemporaryDirectory() as td:
        base = str(Path(td) / "vlm" / "req")
        got = P._vlm_chat(b"fake-jpeg", "问题", dump_base=base)
        dump = Path(got["request"]["request_dump"][0])
        raw = dump.read_bytes()
        assert b"data:image/jpeg;base64" in raw
        assert got["response"]["choices_count"] == 1
        assert got["content"] == ""
