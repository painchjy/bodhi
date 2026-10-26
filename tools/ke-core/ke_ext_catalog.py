"""ke-core · **外部接口（模拟）：清单查询** —— 白名单 + 黑名单（按版本）

口径
----
- 白名单 = `(software, version, vendor, license, status)`：**版本逐字比对（含补丁位）** ✓
- 黑名单 = `(software, version)` + 原因：**命中即拒**，优先于白名单（同版本两边都有 ⇒ 拒）
- 查不到 ⇒ `unknown`（**不猜**：调用方按"外部源不可得"处置）
- 两种模式：`query`（只读）/ `maintain`（写；默认 `dry_run=True`，落盘才写审计）
- 字段与取值域来自容器数据（`schema.json`），代码里不写业务取值 ✓
"""
from __future__ import annotations

import os
import re
import time

import ke_ext_store as S

TOOL = "ext_catalog_query"
_FALLBACK_STATUS = ["已认证", "未认证", "待定"]


def _now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def _schema() -> dict:
    sc = S.read_json(os.path.join(S.system_dir(), "schema.json"), {}) or {}
    return {"fields": ["software", "version", "vendor", "license", "status"],
            "status_enum": sc.get("status_enum") or _FALLBACK_STATUS,
            "labels": sc.get("labels") or {}}


def _allow_path() -> str:
    return os.path.join(S.system_dir(), "allowlist.json")


def _deny_path() -> str:
    return os.path.join(S.system_dir(), "denylist.json")


def _load(path: str) -> dict:
    d = S.read_json(path, {}) or {}
    if not isinstance(d.get("items"), list):
        d["items"] = []
    d.setdefault("rev", 0)
    return d


def _same(a: str, b: str) -> bool:
    """名称/版本比对：**先逐字**，再大小写无关（补丁位、前缀都不放过 ✓）。"""
    a, b = str(a or "").strip(), str(b or "").strip()
    return bool(a) and bool(b) and (a == b or a.lower() == b.lower())


def _hit(rows: list, software: str, version: str = "") -> list:
    out = []
    for r in rows:
        if not _same(r.get("software"), software):
            continue
        if version and not _same(r.get("version"), version):
            continue
        out.append(r)
    return out


def _near(rows: list, software: str, version: str) -> list:
    """同软件、**不同版本** ⇒ 近版本提示（要求逐字，所以把"看着像"的也摆出来 ✓）。"""
    out = []
    for r in rows:
        if _same(r.get("software"), software) and not _same(r.get("version"), version):
            out.append({"version": r.get("version"), "status": r.get("status"),
                        "reason": r.get("reason")})
    return out[:5]


def _vtuple(v: str) -> tuple:
    """版本 → 可比较元组（逐段取数字；非数字段按字符串位）。"""
    out = []
    for seg in re.split(r"[.\-_]", str(v or "").strip()):
        out.append(int(seg) if seg.isdigit() else -1)
    return tuple(out)


def _in_range(version: str, rng) -> bool:
    """`rng = {"min": "8.0.0", "max": "8.1.99"}`（含端点；任一端可省）。"""
    if not isinstance(rng, dict) or not version:
        return False
    v = _vtuple(version)
    lo, hi = rng.get("min"), rng.get("max")
    if lo and v < _vtuple(lo):
        return False
    if hi and v > _vtuple(hi):
        return False
    return True


def _expired(row: dict, now: str = "") -> bool:
    """白名单条目的**有效期**：`expires_at`（YYYY-MM-DD 或带时间）早于今天 ⇒ 过期。"""
    exp = str((row or {}).get("expires_at") or "").strip()
    if not exp:
        return False
    today = (now or time.strftime("%Y-%m-%d"))[:10]
    return exp[:10] < today


def _range_hit(rows: list, software: str, version: str) -> list:
    """按**允许版本范围**命中的白名单条目（`versionRange.min/max`）。"""
    out = []
    for r in rows:
        if not _same(r.get("software"), software):
            continue
        if _in_range(version, r.get("versionRange")):
            out.append(r)
    return out


def _md(rows: list, labels: dict) -> str:
    if not rows:
        return "（无记录）"
    head = ["software", "version", "vendor", "license", "status"]
    out = ["| " + " | ".join(labels.get(k, k) for k in head) + " |",
           "|" + "---|" * len(head)]
    for r in rows:
        out.append("| " + " | ".join(str(r.get(k) or "") for k in head) + " |")
    return "\n".join(out)


def _pack(mode: str, obj: dict, binding: dict, schema: dict, **kw) -> dict:
    now = _now()
    out = {"ok": True, "tool": TOOL, "mode": mode, "object": obj.get("slug"),
           "binding": {"tool": binding.get("tool"), "modes": binding.get("modes"),
                       "keys": binding.get("keys"), "impl": binding.get("impl")},
           "schema": schema, "fetched_at": now,
           "stale_after": time.strftime("%Y-%m-%d %H:%M:%S",
                                        time.localtime(time.time() + 300))}
    out.update(kw)
    return out


def query(kb_id: str = "", object_slug: str = "", mode: str = "query", action: str = "check",
          name: str = "", vendor: str = "", license: str = "", version: str = "",
          status: str = "", limit: int = 20, dry_run: bool = True,
          actor: str = "", session_no: str = "") -> dict:
    """统一入口（`mode=query` 只读；`mode=maintain` 写，默认 dry_run）。"""
    rb = S.resolve_binding(TOOL, kb_id, object_slug)
    if not rb.get("ok"):
        return {"ok": False, "tool": TOOL,
                **{k: rb.get(k) for k in ("why", "hint", "candidates", "object", "binding")
                   if rb.get(k) is not None}}
    binding, obj, schema = rb["binding"], rb["object"], _schema()
    mode = (mode or "query").strip().lower()
    action = (action or "check").strip().lower()
    allow, deny = _load(_allow_path()), _load(_deny_path())

    if mode == "query" or action in ("check", "ls", "denied"):
        if action == "denied":
            rows = [r for r in deny["items"] if not name or _same(r.get("software"), name)]
            return _pack(mode, obj, binding, schema, rows=rows[:max(1, int(limit))],
                         counts={"rows": len(rows)}, rev=deny.get("rev", 0),
                         markdown=_md(rows, schema["labels"]))
        if action == "ls":
            rows = [r for r in allow["items"]
                    if (not name or str(name).lower() in str(r.get("software") or "").lower())
                    and (not vendor or str(vendor).lower() in str(r.get("vendor") or "").lower())
                    and (not license or str(license).lower() in str(r.get("license") or "").lower())
                    and (not status or _same(r.get("status"), status))]
            return _pack(mode, obj, binding, schema, rows=rows[:max(1, int(limit))],
                         counts={"rows": len(rows)}, rev=allow.get("rev", 0),
                         markdown=_md(rows, schema["labels"]))
        if not str(name or "").strip():
            return {"ok": False, "tool": TOOL, "error": "check 需要 name",
                    "hint": "name=软件名；version 建议一并给（逐字比对含补丁位）"}
        denied = _hit(deny["items"], name, version)      # **黑名单优先** ✓
        revs = {"allow": allow.get("rev", 0), "deny": deny.get("rev", 0)}
        if denied:
            return _pack(mode, obj, binding, schema, verdict="denied", match=denied[0],
                         match_kind="deny", reason=denied[0].get("reason") or "",
                         near_misses=_near(allow["items"], name, version), rev=revs)
        ok_rows = _hit(allow["items"], name, version)
        if ok_rows:
            row = ok_rows[0]
            expired = _expired(row)
            return _pack(mode, obj, binding, schema,
                         verdict=("expired" if expired else "allowed"), match=row,
                         match_kind="exact", expired=expired,
                         near_misses=_near(allow["items"], name, version),
                         hint=("该条目的有效期已过（%s）⇒ 视为未认证，需重新认证 ✓"
                               % row.get("expires_at")) if expired else "", rev=revs)
        ranged = _range_hit(allow["items"], name, version)   # **允许版本范围**（2026-10-26）
        if ranged:
            row = ranged[0]
            expired = _expired(row)
            return _pack(mode, obj, binding, schema,
                         verdict=("expired" if expired else "allowed"), match=row,
                         match_kind="range", expired=expired,
                         range=row.get("versionRange"),
                         near_misses=_near(allow["items"], name, version),
                         hint=("命中允许版本范围 %s（非逐字条目）⇒ 以范围为准 ✓"
                               % (row.get("versionRange"),)), rev=revs)
        return _pack(mode, obj, binding, schema, verdict="unknown",
                     near_misses=_near(allow["items"], name, version),
                     hint=("白名单没有该（名称,版本），也没落在任何 versionRange 内 ⇒ "
                           "按外部源不可得处置；若名称有、版本不同，看 near_misses ✓"), rev=revs)

    # ── maintain（写）───────────────────────────────────────────────────
    if "maintain" not in (binding.get("modes") or []):
        return {"ok": False, "tool": TOOL, "error": "mode_not_supported",
                "hint": "该对象的绑定没有声明 maintain 模式（bmm:sourceLocator 的 mode= 里加）",
                "object": obj.get("slug"), "modes": binding.get("modes")}
    if not str(name or "").strip() or not str(version or "").strip():
        return {"ok": False, "tool": TOOL, "error": "maintain 需要 name+version",
                "hint": "两者共同构成幂等键（逐字，含补丁位）"}
    if not actor:
        return {"ok": False, "tool": TOOL, "error": "maintain 需要 actor",
                "hint": "谁改的（人/智能体标识）"}
    if status and status not in schema["status_enum"]:
        return {"ok": False, "tool": TOOL, "error": "bad_status",
                "allowed": schema["status_enum"]}
    row = {"software": name, "version": version, "vendor": vendor, "license": license,
           "status": ("未认证" if action == "disable" else (status or "未认证")),
           "updated_at": _now()}
    before = _hit(allow["items"], name, version)
    after = dict(before[0] if before else {}, **row)
    diff = {"before": before[0] if before else None, "after": after}
    if dry_run:
        return _pack("maintain", obj, binding, schema, dry_run=True, action=action,
                     diff=diff, rev=allow.get("rev", 0),
                     next="确认无误后传 dry_run=false 才落盘")
    path = _allow_path()
    with S.locked(path):
        cur = _load(path)
        cur["items"] = [r for r in cur["items"]
                        if not (_same(r.get("software"), name) and _same(r.get("version"), version))]
        cur["items"].append(after)
        cur["rev"] = int(cur.get("rev") or 0) + 1
        cur["updated_at"] = _now()
        S.write_json(path, cur)
        S.audit("catalog:%s" % action, {"tool": TOOL, "actor": actor, "session_no": session_no,
                                        "key": "%s@%s" % (name, version), "dry_run": False,
                                        "before": diff["before"], "after": after})
    return _pack("maintain", obj, binding, schema, dry_run=False, action=action,
                 diff=diff, rev=cur["rev"], audit="已记录")
