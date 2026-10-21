#!/usr/bin/env python3
"""P0 回归：**导出包完整性**（本轮 KB 导入 bug 的静态回归 ✓）

背景（2026-10-20 实测）：导入到新库时 `wiki_page_revisions` 撞 `(page_id, version)` ✗，
异常发生在图写入之前 ⇒ **PG 有、图空**，且 HTTP 无响应 ⇒ 前端卡"上传中"✗。
本用例锁住"导出包本身齐不齐"（manifest 计数 == 实际行数 ✓）：
- 若导出缺 graph_nodes/graph_edges ⇒ 导入必然丢图 ✗（先在这里红 ✓）
- 导入端的幂等（ON CONFLICT）另有端到端用例（`BODHI_SLOW=1` 时跑，见 test_import_roundtrip.py）

跑法：`python3 tests/test_export_integrity.py [导出目录]`（默认取 exports/ 下最新一个）
"""
import glob
import io
import json
import os
import sys
import zipfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _helpers as H  # noqa: E402


def _count_lines(data: bytes) -> int:
    return data.count(b"\n") + (1 if data and not data.endswith(b"\n") else 0)


def main() -> int:
    arg = next((a for a in sys.argv[1:] if not a.startswith("-")), "")
    cands = [arg] if arg else sorted(glob.glob(os.path.join(H.REPO, "exports", "kb-*")))
    # 只挑**含 manifest.json** 的目录（uploads/ 等杂目录不算 ✓）
    dirs = [d for d in cands if os.path.isfile(os.path.join(d, "manifest.json"))]
    if not dirs:
        print("（无导出包，跳过 ✓）")
        return 0
    d = dirs[-1]
    m_path = os.path.join(d, "manifest.json")
    checks = {}
    if not os.path.isfile(m_path):
        return H.report({"存在 manifest.json": False}, "导出包完整性")
    m = json.loads(H.read(m_path))
    counts = m.get("counts") or {}
    checks["manifest 含 graph_nodes/graph_edges 计数"] = ("graph_nodes" in counts
                                                          and "graph_edges" in counts)
    for key in ("pages.jsonl", "graph_nodes.jsonl", "graph_edges.jsonl"):
        p = os.path.join(d, key)
        if not os.path.isfile(p):
            checks["%s 存在" % key] = False
            continue
        n = _count_lines(io.open(p, "rb").read())
        exp = counts.get(key.replace(".jsonl", ""))
        checks["%s 行数 %d == manifest %s" % (key, n, exp)] = (exp is None or int(exp) == n)
    zp = glob.glob(os.path.join(d, "*.zip"))
    if zp:
        with zipfile.ZipFile(zp[0]) as z:
            names = z.namelist()
            checks["zip 内含 graph_nodes.jsonl"] = any(n.endswith("graph_nodes.jsonl") for n in names)
            checks["zip 内含 graph_edges.jsonl"] = any(n.endswith("graph_edges.jsonl") for n in names)
    print("检查对象：%s" % os.path.basename(d))
    return H.report(checks, "导出包完整性")


if __name__ == "__main__":
    sys.exit(main())
