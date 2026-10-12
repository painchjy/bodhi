#!/usr/bin/env python3
"""只读：最新扫描报告的候选组 + verdict 建议（equivalent / unknown）+ 涉及上下文。"""
import json
import pathlib

REPO = pathlib.Path(__file__).resolve().parents[2]
S = REPO / "state" / "context_map" / "scan"
f = sorted(S.glob("cs-*.json"))[-1]
d = json.loads(f.read_text(encoding="utf-8"))
print("scan=%s" % f.name)
print("stats=%s" % json.dumps(d.get("stats"), ensure_ascii=False))
print("%-16s %-10s %-6s %s" % ("cand", "verdict", "score", "涉及上下文:slug"))
for c in d.get("candidates", []):
    pages = c.get("pages") or []
    ks = "  +  ".join("%s:%s" % (p.get("kb_name"), p.get("slug")) for p in pages)
    print("%-16s %-10s %-6s %s" % (c.get("id"), c.get("verdict_suggestion"),
                                   round(float(c.get("confidence") or 0), 3), ks))
