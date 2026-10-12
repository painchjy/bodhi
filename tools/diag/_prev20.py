#!/usr/bin/env python3
"""只读：把 concept_preview 的全量建议清单打成一行一组（CLI 的 preview 写死了小 limit）。"""
import pathlib
import sys
import json

H = pathlib.Path(__file__).resolve().parents[1] / "ke-core"
sys.path.insert(0, str(H))
import ke_context  # noqa: E402

r = ke_context.concept_preview(include_unknown=True, limit=50)
print("stats=%s ticket=%s" % (json.dumps(r.get("stats"), ensure_ascii=False), r.get("ticket")))
print("%-56s %-26s %-6s %s" % ("slug", "page_type", "exists", "required_risks"))
for p in r.get("pages", []):
    print("%-56s %-26s %-6s %s" % (p.get("slug"), p.get("page_type"), p.get("exists"),
                                   ",".join(p.get("required_risks") or []) or "-"))
print("skipped=%s" % json.dumps(r.get("skipped"), ensure_ascii=False))
