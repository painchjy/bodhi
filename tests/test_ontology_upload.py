"""本体上传回归（**走真实 `/bodhi/ontology/upload`**，覆盖级联删除 / 备份 / 报错恢复）。

用户口径（2026-10-22）：
- 前端上传本体文件 → 级联备份 + 删除下游 → 新模型文件复制到 `ontology/sources/` → **编译并生成产物**；
- 编译源头 = `ontology/sources/*.ttl` ⇒ **先确认这里的文件正确**，再确认产物完整性；
- 顺序期望：上传 bmm ⇒ 只剩 `bmm.ttl`；再上传 bpmn ⇒ `bmm.ttl + bpmn.ttl`；
  再上传智能体开发 ⇒ 3 个 ttl；
- **不许自己跑编译器**：一律调用现有 `upload_ttl`（HTTP）。

为何默认 skip：该用例会**真重编本体 + 重灌图库**（几十秒级、且改动 `artifacts/`），
不宜进日常全量回归。显式开启：`BODHI_TEST_ONTOLOGY_UPLOAD=1 pytest tests/test_ontology_upload.py`。
跑完的终态 = 上传前的 3 个模块（顺序即自愈），并核对产物模块集合一致。
"""
import json
import os
import pathlib
import urllib.error
import urllib.request

import pytest

REPO = pathlib.Path(__file__).resolve().parent.parent
SOURCES = REPO / "ontology" / "sources"
INDEX = REPO / "artifacts" / "weknora" / "ontology_index.json"
BASE = os.environ.get("BODHI_HTTP_BASE", "http://127.0.0.1:8765")

pytestmark = pytest.mark.skipif(
    os.environ.get("BODHI_TEST_ONTOLOGY_UPLOAD") != "1",
    reason="会真重编本体（改 artifacts/ 并重灌图库）：设 BODHI_TEST_ONTOLOGY_UPLOAD=1 才跑")

UPLOADS = {
    "bmm": "BMM完整版.ttl",
    "bpmn": "BPMN完整版.ttl",
    "agent": "智能体开发完整版.ttl",
}
# 2026-10-23：手工维护的本体文件已迁到 `docs/ontology/`（`ontology/sources/*.ttl` 是上传生成的真源，**不手改**）
SRC_DIR = REPO / "docs" / "ontology"


def _post(path: str, body: dict, timeout: int = 600) -> dict:
    req = urllib.request.Request(BASE + path, data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode() or "{}")
    except urllib.error.HTTPError as exc:                 # 400/500 也要把回执读出来 ✓
        raw = exc.read().decode("utf-8", "replace")
        try:
            return json.loads(raw or "{}")
        except Exception:  # noqa: BLE001
            return {"ok": False, "http_status": exc.code, "raw": raw[:400]}


def _upload(key: str) -> dict:
    f = SRC_DIR / UPLOADS[key]
    assert f.is_file(), "缺本体源文件：%s" % f
    return _post("/bodhi/ontology/upload",
                 {"filename": f.name, "content": f.read_text(encoding="utf-8"),
                  "write_source": True, "compile_after": True, "apply_after": False})


def _sources() -> set:
    return {p.name for p in SOURCES.glob("*.ttl")}


def _index_modules() -> list:
    data = json.loads(INDEX.read_text(encoding="utf-8"))
    return sorted(m.get("key") for m in (data.get("models") or []))


def _index_totals() -> dict:
    data = json.loads(INDEX.read_text(encoding="utf-8"))
    return {k: v for k, v in (data.get("counts") or {}).items()} if isinstance(
        data.get("counts"), dict) else {}


def _ok(r: dict) -> bool:
    """上传回执**没有顶层 `ok`**（HTTP 包装层只把失败转成 error）⇒ 按真实字段判定 ✓"""
    if r.get("error"):
        return False
    comp = r.get("compile") or {}
    return bool(comp.get("ok")) and int(comp.get("exit") or 0) == 0


def test_upload_sequence_sources_and_artifacts():
    original = _sources()
    assert original == {"bmm.ttl", "bpmn.ttl", "agent.ttl"}, \
        "起始状态不是 3 个模块（%s）⇒ 先人工确认再跑本用例" % sorted(original)
    totals_before = _index_totals()

    # ① 上传 bmm ⇒ 级联删下游 ⇒ sources 只剩 bmm.ttl
    r1 = _upload("bmm")
    assert _ok(r1), "上传 bmm 失败：%s" % json.dumps(r1, ensure_ascii=False)[:600]
    assert r1.get("cascade_purge"), "没有级联删除回执 ✗"
    assert r1.get("sources_backup"), "没有真源备份回执 ✗"
    assert _sources() == {"bmm.ttl"}, "sources 应只剩 bmm.ttl，实际 %s" % sorted(_sources())
    assert "bmm" in _index_modules(), "产物里没有 bmm 模块 ✗：%s" % _index_modules()

    # ② 再上传 bpmn ⇒ bmm + bpmn
    r2 = _upload("bpmn")
    assert _ok(r2), "上传 bpmn 失败：%s" % json.dumps(r2, ensure_ascii=False)[:600]
    assert _sources() == {"bmm.ttl", "bpmn.ttl"}, "实际 %s" % sorted(_sources())
    mods = set(_index_modules())
    assert {"bmm", "bpmn"} <= mods, "产物模块缺 bmm/bpmn ✗：%s" % sorted(mods)

    # ③ 再上传智能体开发 ⇒ 3 个 ttl（回到起始状态）
    r3 = _upload("agent")
    assert _ok(r3), "上传 agent 失败：%s" % json.dumps(r3, ensure_ascii=False)[:600]
    assert _sources() == {"bmm.ttl", "bpmn.ttl", "agent.ttl"}, "实际 %s" % sorted(_sources())
    mods3 = set(_index_modules())
    assert {"bmm", "bpmn", "agent"} <= mods3, "产物模块不全 ✗：%s" % sorted(mods3)

    # ④ 产物完整性：全量总数回到上传前（模块集合相同 ⇒ 数字应一致）
    assert _index_totals() == totals_before, \
        "产物总数变了：before=%s after=%s" % (totals_before, _index_totals())


def test_broken_upload_leaves_state_untouched():
    """报错恢复：不可解析的 TTL ⇒ 直接中止，**不删任何真源/产物/图**。"""
    before_files = _sources()
    before_mods = _index_modules()

    bad = _post("/bodhi/ontology/upload",
                {"filename": "broken.ttl", "content": "这不是 TTL @@@ <<<",
                 "write_source": True, "compile_after": True})
    err = str(bad.get("error") or "")
    assert err, "坏 TTL 居然没有报错 ✗：%s" % json.dumps(bad, ensure_ascii=False)[:400]
    assert "ttl_unparsable" in err or "preflight_failed" in err, err[:400]
    assert "没有改动真源" in err or "没有删除" in err, "错误里应说明未改动任何数据 ✗：%s" % err[:300]
    assert _sources() == before_files, "坏上传改动了 sources ✗：%s" % sorted(_sources())
    assert _index_modules() == before_mods, "坏上传改动了产物 ✗：%s" % _index_modules()
