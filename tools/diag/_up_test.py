"""上传端点端到端（不依赖 UI/curl）：
   ① GET  /bodhi/kb-upload            → 上传页（HTML 200）
   ② POST /bodhi/kb-upload?…&new_kb=… → 原始字节上传 + 顺带导入（回执含 verify_ok）
用法：/opt/bodhi-venv/bin/python3 tools/diag/_up_test.py [zip路径]
"""
import json
import pathlib
import sys
import urllib.parse
import urllib.request

BASE = "http://127.0.0.1:8765"
ZIP = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else "/tmp/kb-xfer3.zip")

with urllib.request.urlopen(BASE + "/bodhi/kb-upload", timeout=30) as r:
    html = r.read().decode("utf-8", "replace")
print("① 上传页 GET：HTTP %s，%d 字节，含表单=%s"
      % (r.status, len(html), "<form" in html and "kb-upload" in html))

q = urllib.parse.urlencode({"name": ZIP.name, "new_kb": "迁移测试-上传导入", "verify": "1"})
req = urllib.request.Request(BASE + "/bodhi/kb-upload?" + q, data=ZIP.read_bytes(),
                             method="POST",
                             headers={"Content-Type": "application/octet-stream"})
with urllib.request.urlopen(req, timeout=900) as r:
    d = json.loads(r.read().decode("utf-8", "replace"))
print("② 上传 POST：HTTP %s  ok=%s  saved=%s  bytes=%s"
      % (r.status, d.get("ok"), d.get("saved"), d.get("bytes")))
imp = d.get("import") or {}
print("   import.ok=%s  target=%s" % (imp.get("ok"), imp.get("target_kb")))
print("   counts=%s" % json.dumps(imp.get("counts"), ensure_ascii=False))
print("   verify_ok=%s  verify=%s"
      % (imp.get("verify_ok"), json.dumps(imp.get("verify"), ensure_ascii=False)))
raise SystemExit(0 if imp.get("verify_ok") else 1)
