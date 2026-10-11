"""改 TTL 后让变更**生效**（`render_mode` 只读 Neo4j，必须重灌投影）。

用法：
  /opt/bodhi-venv/bin/python3 tools/diag/_repair.py            # /repair：编译 → 灌投影 → 重投影 wiki → 体检
  /opt/bodhi-venv/bin/python3 tools/diag/_repair.py load-bmm   # /load：purge bmm → 重灌投影（清掉陈旧 domain 边）
"""
import json
import sys
import urllib.request

ONT_KB = "08810cbd-af86-48d1-bd25-3b2c338e3d68"
mode = (sys.argv[1] if len(sys.argv) > 1 else "repair").strip()

if mode == "load-bmm":
    url = "http://127.0.0.1:8765/bodhi/ontology/load"
    body = {"model_id": "bmm", "kb_id": ONT_KB, "purge": True,
            "compile": True, "project_wiki": True}
else:
    url = "http://127.0.0.1:8765/bodhi/ontology/repair"
    body = {"kb_id": ONT_KB, "compile": True, "project_wiki": True}

print("POST %s  body=%s" % (url, json.dumps(body, ensure_ascii=False)))
req = urllib.request.Request(url, data=json.dumps(body).encode(),
                             headers={"Content-Type": "application/json"})
with urllib.request.urlopen(req, timeout=900) as r:
    print(r.read().decode("utf-8", "replace")[:6000])

