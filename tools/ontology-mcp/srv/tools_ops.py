"""运维工具实现：知识库导出 / 导入（P2 切片 S8b-2a ✓ 从 `server.py` 抽出，**实现逐字未改**）

真逻辑在 `kb_transfer`（`cmd_export` / `cmd_import` / `cmd_verify`）✓
本模块只做参数解析（`exports/` 相对名 → 绝对路径）与回执组装（**download 链接**给前端）。
"""
from __future__ import annotations

import pathlib

from srv.constants import EXPORT_DIR
from srv.kbresolve import resolve_kb_id


def kb_export(kb_id: str = "", out_dir: str = "", make_zip: bool = True) -> dict:
    """**运维 · 导出知识库**：把指定知识库的内容导出成文件（PG 页/目录/版本 + Neo4j 节点/边）。

    产物落在服务端 `exports/`（或 `out_dir`），并默认打成 **zip**；回执给 **download** 链接
    （`GET /bodhi/kb-export?name=…`）→ 下载到本地后拷到内网，用 `kb_import` 导入。
    """
    import kb_transfer as _kbx  # noqa: PLC0415
    from datetime import datetime as _dt  # noqa: PLC0415
    kid = (kb_id or "").strip()
    if not kid:
        return {"ok": False, "reason": "缺少 kb_id"}
    real_kb, note = resolve_kb_id(kid)
    ts = _dt.now().strftime("%Y%m%d-%H%M%S")
    base = (out_dir or "").strip() or str(EXPORT_DIR / ("kb-%s" % real_kb[:8]))
    if not base.endswith(ts):
        base = "%s-%s" % (base, ts)
    man = _kbx.cmd_export(real_kb, base, make_zip=bool(make_zip))
    z = man.get("zip") or ""
    name = pathlib.Path(z).name if z else ""
    return {"ok": True, "kb_id": real_kb, "kb_note": note, "dir": base, "zip": z,
            "download": ("/bodhi/kb-export?name=%s" % name) if name else "",
            "counts": man.get("counts"),
            "note": "下载 zip（download 链接）→ 内网解包或直接 `kb_import(source=<zip或目录>)`"}


def kb_import(source: str = "", kb_id: str = "", new_kb: str = "", keep_ids: bool = False,
              verify: bool = True) -> dict:
    """**运维 · 导入知识库**：把导出的**目录或 zip** 导入到指定知识库（或现建一个），并默认校验一致性。

    · `source` 可为绝对路径、导出目录名/zip 名（默认在服务端 `exports/` 下找）；
    · `new_kb`（且未给 `kb_id`）：建/复用同名库；
    · 按 `(kb, slug)` 先删后插 → **幂等**，可重复执行；
    · `verify=true`：导入后立刻做 **PG + Neo4j 六项比对**，回执给 `verify_ok`。
    """
    import kb_transfer as _kbx  # noqa: PLC0415
    src = (source or "").strip()
    if not src:
        return {"ok": False, "reason": "缺少 source（导出目录或 zip 路径/文件名）"}
    p = pathlib.Path(src)
    if not p.exists():
        cand = EXPORT_DIR / src
        if cand.exists():
            p = cand
        else:
            return {"ok": False, "reason": "找不到源：%s（也不在 %s 下）" % (src, EXPORT_DIR)}
    tgt, tnote = "", ""
    if new_kb and not kb_id:
        tgt = _kbx.ensure_kb(new_kb)
    else:
        kid = (kb_id or "").strip()
        if not kid:
            return {"ok": False, "reason": "要么给 kb_id（导入到既有库），要么给 new_kb（新建/复用）"}
        tgt, tnote = resolve_kb_id(kid)
    counts = _kbx.cmd_import(str(p), to_kb=tgt, keep_ids=bool(keep_ids))
    out = {"ok": True, "target_kb": tgt, "kb_note": tnote, "counts": counts,
           "source": str(p)}
    if verify:
        v = _kbx.cmd_verify(str(p), tgt)
        out["verify_ok"] = v["ok"]
        out["verify"] = {k: bool(v["detail"][k].get("ok")) for k in v["detail"]}
        out["note"] = ("一致性 ✅：PG + Neo4j 六项一致" if v["ok"] else
                       "一致性 ❌：看 verify 各项（页内容/页链/目录/版本/图节点/图边）")
    return out
