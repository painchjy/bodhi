"""临时诊断脚本：把一次真实 `save_knowledge` 入参**复打**到本地 MCP（P3-0i 生效性验证用）。

背景（2026-10-10）：systemd 的 `bodhi-mcp` 进程自 04:59:47 起未重启，跑的是旧字节码，
导致 P3-0g/0g2/0h/0i 全部未生效（日志里没有任何 `|调用者:` 行）。重启到 08:44:34 后，
用同一份 payload 复打即可与 08:30 的基线（pg:138次/22360ms，neo4j:1214次/6420ms）直接对比。

只读保证：默认**强制 `mode=dry_run`**（不写 PG/Neo4j）；确实要写才显式加 `--apply`。

用法：
    /opt/bodhi-venv/bin/python3 _replay_sk.py <args.json> [--apply]
"""
import json
import pathlib
import sys
import time
import urllib.request

URL = "http://127.0.0.1:8765/mcp"


def main() -> int:
    if len(sys.argv) < 2:
        print("用法：_replay_sk.py <args.json> [--apply]")
        return 2
    args_path = pathlib.Path(sys.argv[1])
    args = json.loads(args_path.read_text(encoding="utf-8"))
    if "--apply" not in sys.argv:
        args["mode"] = "dry_run"          # 只读复测（默认）
    nodes = args.get("nodes") or []
    edges = args.get("edges") or []
    print("payload=%s mode=%s nodes=%d edges=%d kb_id=%s"
          % (args_path.name, args.get("mode"), len(nodes), len(edges), args.get("kb_id")))

    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                       "params": {"name": "save_knowledge", "arguments": args}},
                      ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(URL, data=body, method="POST", headers={
        "Content-Type": "application/json",
        "Accept": "application/json",              # 不给 text/event-stream → 回纯 JSON
    })
    t0 = time.perf_counter()
    with urllib.request.urlopen(req, timeout=900) as resp:      # noqa: S310
        status, raw = resp.status, resp.read().decode("utf-8", "replace")
    ms = (time.perf_counter() - t0) * 1000.0
    print("http=%s wall=%.0fms" % (status, ms))

    try:
        payload = json.loads(raw)
        block = (payload.get("result") or {}).get("content") or []
        text = block[0].get("text", "") if block else ""
        data = json.loads(text) if text else {}
    except Exception as exc:                       # noqa: BLE001
        print("解析回执失败：%s\n原文前 800 字：%s" % (exc, raw[:800]))
        return 1
    print("ok=%s applied=%s dry_run=%s created=%s pending=%s violations=%s unmatched=%s"
          % (data.get("ok"), data.get("applied"), data.get("dry_run"),
             len(data.get("created") or []), len(data.get("pending") or []),
             len(data.get("violations") or []), len(data.get("unmatched") or [])))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
