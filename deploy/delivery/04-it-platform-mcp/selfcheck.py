#!/usr/bin/env python3
"""04-it-platform-mcp 自检（发布后必跑）：契约 / 工具面 / 种子 / 配置漂移。

用法
----
    python3 selfcheck.py                 # 在本包根目录跑（默认）
    python3 selfcheck.py --base /opt/bodhi-ext

检查项（任一失败 ⇒ 退出码 1）：
  ① 契约可读且带 `x_ext`（含 `schemaHash`）；
  ② 两份 `schema.json`（服务目录 / 种子目录）**内容一致**（防"改了服务忘了种子"）；
  ③ 工具面数量与 `x_ext` 覆盖（`ext_*` 工具必须都有契约）；
  ④ 种子文件齐（allowlist / denylist），二进制目录**允许缺失**（边界口径）；
  ⑤ 智能体配置无漂移（调用 `tools/delivery/apply_config.py --check`，可选）。
"""
import argparse
import hashlib
import json
import os
import subprocess
import sys

PKG = os.path.dirname(os.path.abspath(__file__))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default=PKG)
    ap.add_argument("--repo", default=os.path.dirname(os.path.dirname(PKG)))
    ap.add_argument("--skip-agent", action="store_true")
    args = ap.parse_args()
    base = args.base
    svc = os.path.join(base, "tools", "it-platform-mcp")
    seed = os.path.join(base, "deploy", "mock-ext", "it-platform")
    bad = 0

    def ok(label, cond, extra=""):
        nonlocal bad
        print("%s %s%s" % ("✓" if cond else "✗", label, ("　" + extra) if extra else ""))
        if not cond:
            bad += 1

    # ① 契约
    doc = {}
    p1 = os.path.join(svc, "schema.json")
    if os.path.exists(p1):
        with open(p1, encoding="utf-8") as fh:
            doc = json.load(fh)
    ok("契约可读：%s" % p1, bool(doc.get("x_ext")))
    ok("契约含 x_ext（%d 个工具）" % len(doc.get("x_ext") or {}), bool(doc.get("x_ext")))

    # ② 两份一致
    p2 = os.path.join(seed, "schema.json")
    if os.path.exists(p2):
        h = lambda p: hashlib.sha256(open(p, "rb").read()).hexdigest()[:12]
        ok("服务/种子 schema.json 一致", h(p1) == h(p2), "%s vs %s" % (h(p1), h(p2)))
    else:
        ok("种子 schema.json 存在（运行期 bootstrap 需要）", False, p2)

    # ③ 工具面
    # 契约读取依赖 ke_ext_store（在 `ke-core`）：04 包不含它（与 02 共用）⇒ 依次找
    for cand in (os.path.join(base, "tools", "ke-core"), os.environ.get("BODHI_KECORE", ""),
                 "/opt/bodhi2/tools/ke-core", os.path.join(args.repo, "tools", "ke-core")):
        if cand and os.path.isdir(cand):
            sys.path.insert(0, cand)
            break
    sys.path.insert(0, svc)
    try:
        import schemas  # noqa: PLC0415
        names = [t.get("name") for t in getattr(schemas, "TOOLS", [])]
    except Exception as exc:  # noqa: BLE001
        names = []
        ok("可导入 schemas.TOOLS", False, repr(exc))
    if names:
        ok("工具数 = %d" % len(names), len(names) > 0)
        ext = [n for n in names if n.startswith("ext_")]
        covered = [n for n in ext if n in (doc.get("x_ext") or {})]
        missing = [n for n in ext if n not in (doc.get("x_ext") or {})]
        ok("ext_* 工具都有契约（%d 个）" % len(ext), not missing,
           "未覆盖=%s" % missing if missing else "")
        hashes = [schemas.x_ext(n).get("schemaHash") for n in covered]
        ok("x-ext 下发 schemaHash（%d 个）" % len(covered),
           all(hashes) and bool(covered),
           str(hashes[0]) if hashes else "no contract")

    # ④ 种子
    for f in ("allowlist.json", "denylist.json"):
        ok("种子 %s" % f, os.path.exists(os.path.join(seed, f)))
    for d in ("docs", "files", "images"):
        print("· %s/ %s（边界口径：可不带）" % (d, "存在" if os.path.exists(os.path.join(seed, d)) else "缺失"))

    # ⑤ 智能体配置
    if not args.skip_agent:
        script = os.path.join(args.repo, "tools", "delivery", "apply_config.py")
        if os.path.exists(script):
            r = subprocess.run([sys.executable, script, "--check"], capture_output=True, text=True)
            ok("智能体配置无漂移", r.returncode == 0,
               (r.stdout or "").strip().splitlines()[-1] if (r.stdout or "").strip() else "")
        else:
            print("· 跳过智能体检查（没找到 %s）" % script)

    print("\n%s（失败 %d 项）" % ("全绿 ✓" if not bad else "失败 ✗", bad))
    return 0 if not bad else 1


if __name__ == "__main__":
    raise SystemExit(main())
