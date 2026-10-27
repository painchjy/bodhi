"""上传回执留档 + 「0 类」告警单测 —— 2026-10-27。

为什么要有：`/bodhi/ontology/upload` 走 HTTP，**不进** `logs/mcp_calls_*.log`；"解析出 0 类"这种
情况**不抛异常**（旧版既无回执、无日志）。现在 `upload_ttl` 的所有返回点都会过
`_audit_upload()`：① 把回执写成 `logs/uploads/<ts>-<module>.json`；② `module_classes==0` 时挂 `warn`。

断言（不碰真库、不重编，只在 `logs/` 下建临时目录）：
  ① 0 类 ⇒ 回执带 `warn`（说清症状与"本次不写图"）+ `audit_file` 真落盘；
  ② 非 0 类 ⇒ 无 `warn`，但一样留档（success 也要能查）。
跑法：`python3 tests/test_upload_audit_receipt.py`
"""
import json
import os
import pathlib
import shutil
import sys
import tempfile
import traceback

REPO = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "tools" / "ke-core"))

import ke_admin  # noqa: E402


def _with_tmp_dir(fn):
    """把留档目录指到 `logs/` 下的临时目录（`audit_file` 是相对仓库根的路径 ⇒ 必须在仓库内）。"""
    tmp = pathlib.Path(tempfile.mkdtemp(dir=str(REPO / "logs")))
    old = ke_admin.UPLOAD_AUDIT_DIR
    ke_admin.UPLOAD_AUDIT_DIR = tmp
    try:
        return fn(tmp)
    finally:
        ke_admin.UPLOAD_AUDIT_DIR = old
        shutil.rmtree(tmp, ignore_errors=True)


def test_zero_classes_warns_and_writes_receipt():
    def run(tmp):
        src = tmp / "demo.ttl"
        src.write_text("@prefix a: <http://example.org/> .", encoding="utf-8")
        rec = ke_admin._audit_upload({"module_classes": 0, "note": "本模块类：0"}, 0.0, "demo", src)
        assert "0 个类" in (rec.get("warn") or ""), rec
        af = rec.get("audit_file") or ""
        assert af.startswith("logs/") and af.endswith(".json"), rec
        path = REPO / af
        assert path.is_file(), af
        doc = json.loads(path.read_text(encoding="utf-8"))
        assert doc["module_classes"] == 0 and doc["module_key"] == "demo", doc
        assert doc["file"]["sha256"] and doc["file"]["bytes"] > 0, doc
        assert "ms" in doc and doc["warn"], doc
    _with_tmp_dir(run)


def test_nonzero_classes_has_no_warn():
    def run(tmp):
        src = tmp / "ok.ttl"
        src.write_text("@prefix a: <http://example.org/> .", encoding="utf-8")
        rec = ke_admin._audit_upload({"module_classes": 12, "note": "ok"}, 0.0, "okmod", src)
        assert not rec.get("warn"), rec
        assert (REPO / rec["audit_file"]).is_file(), rec
    _with_tmp_dir(run)


def main() -> int:
    tests = [(n, f) for n, f in sorted(globals().items())
             if n.startswith("test_") and callable(f)]
    bad = 0
    for name, fn in tests:
        try:
            fn()
            print("  ✓ %s" % name)
        except Exception as exc:  # noqa: BLE001
            bad += 1
            print("  ✗ %s：%s" % (name, exc))
            traceback.print_exc()
    print("上传回执留档用例：%d 个 ｜ 通过 %d ｜ 失败 %d" % (len(tests), len(tests) - bad, bad))
    return 0 if not bad else 1


if __name__ == "__main__":
    sys.exit(main())
