"""ke-core · **外部接口（模拟）：流程上下文**（阶段/状态 / 轮次 / 文档清单）

为什么要有它（2026-10-26 用户口径）
----------------------------------
内网真实口径：智能体只提交「**编号 + 文件名称**」，**不需要上传文档** ——
流程编号、阶段/状态、轮次、责任人、文档都在外部工作台。本工具把"流程上下文"作为
**外部对象**提供：

    action=get      key → 流程编号/名称/阶段/状态/当前轮次/责任人/文档数
    action=docs     key [round] → [{doc_id,title,mime,size,sha256,url,round}]
    action=rounds   key → 轮次表（第 n 轮/起止/状态/is_current）
    action=open     key [round] [filenames] → **建档** + 绑文档 + next
    action=maintain upsert_round | close（默认 dry_run=true；落盘写审计 + rev+1）

编号/轮次口径（模拟侧）
-----------------------
- 智能体给的 key **一律视为平台已存在**：无档案 ⇒ `get/open` 时**直接建档**
  （状态/阶段取 `schema.json` 的 `defaults`，`round=1`，`authority=agent_requested`）；
- 智能体指定轮次 ⇒ **以它为准**（回执 `round_source=agent`）；档案已有更大轮次 ⇒
  `warn=round_behind` 且**以档案为准**（不静默 ✗）；
- 真内网把 `authority` 置 `internal` 时一律以平台为准（工具面/入参不变 ✓）。
"""
from __future__ import annotations

import os
import re
import time

import ke_ext_store as S

TOOL = "ext_review_flow"


def _now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def _safe(key: str) -> str:
    return re.sub(r"[^\w\-.\u4e00-\u9fff]+", "_", str(key or "")).strip("_") or "_all"


def _flows_dir() -> str:
    d = os.path.join(S.system_dir(), "flows")
    os.makedirs(d, exist_ok=True)
    return d


def _archive_path(review_no: str) -> str:
    return os.path.join(_flows_dir(), _safe(review_no) + ".json")


def _load(review_no: str) -> dict:
    d = S.read_json(_archive_path(review_no), {}) or {}
    return d if isinstance(d, dict) and d.get("review_no") else {}


def _docs_index() -> dict:
    d = S.read_json(os.path.join(S.system_dir(), "docs", "index.json"), {}) or {}
    return d if isinstance(d, dict) else {}


def _doc(doc_id: str) -> dict:
    return S.read_json(os.path.join(S.system_dir(), "docs", _safe(doc_id) + ".json"), {}) or {}


def _cnt(v) -> int:
    """计数（list ⇒ 长度；int ⇒ 原值；其它 ⇒ 0）—— 老档案里可能存的是"数量"而不是列表 ✓。"""
    if isinstance(v, (list, tuple)):
        return len(v)
    try:
        return int(v or 0)
    except (TypeError, ValueError):
        return 0


def _doc_brief(d: dict) -> dict:
    return {"doc_id": d.get("doc_id"), "title": d.get("title") or "",
            "mime": d.get("mime") or "", "size": d.get("size") or 0,
            "sha256": (d.get("sha256") or "")[:16], "round": int(d.get("round") or 1),
            "sections": _cnt(d.get("sections")),
            "attachments": _cnt(d.get("attachments")),
            "images": _cnt(d.get("images")),
            "url": ("%s/files/%s" % (S.public_base(), d.get("doc_id") or ""))}


def _doc_rows(review_no: str, rnd: int = 0) -> list:
    """该编号下的文档清单（含**可下载 URL** ✓）。`rnd`=0 ⇒ 不过滤轮次。"""
    idx = _docs_index()
    ids = [str(x) for x in (idx.get(str(review_no or "")) or [])]
    if not ids:                                  # 无该编号 ⇒ `_default`（再退：所有清单里的 doc_id 去重）
        ids = [str(x) for x in (idx.get("_default") or [])]
    if not ids:
        flat = [str(x) for v in idx.values() if isinstance(v, list) for x in v]
        ids = list(dict.fromkeys(flat))
    out = []
    for did in ids:
        d = _doc(did)
        if not d:
            continue
        d.setdefault("doc_id", did)
        if rnd and int(d.get("round") or 1) != int(rnd):
            continue
        out.append(_doc_brief(d))
    return out


def _defaults() -> dict:
    """状态/阶段的**默认值**取自契约数据（`schema.json` 的 `x_ext.<tool>.defaults`）——代码不写业务取值 ✓。"""
    sc = S.read_json(os.path.join(S.system_dir(), "schema.json"), {}) or {}
    d = (((sc.get("x_ext") or {}).get(TOOL) or {}).get("defaults") or {})
    return {"status": str(d.get("status") or ""), "stage": str(d.get("stage") or "")}


def _new_archive(review_no: str, rnd: int, docs: list) -> dict:
    dflt = _defaults()
    return {"review_no": review_no, "flow_no": review_no,
            "name": "%s 流程" % (docs[0]["title"] if docs else review_no),
            "stage": dflt["stage"], "status": dflt["status"], "round": int(rnd or 1),
            "rounds": [{"round": int(rnd or 1), "status": "进行中",
                        "started_at": _now(), "ended_at": ""}],
            "owner": "", "reviewers": [], "authority": "agent_requested",
            "docs": docs, "rev": 1, "updated_at": _now()}


def _save(a: dict, actor: str = "", session_no: str = "", action: str = "") -> dict:
    a["rev"] = int(a.get("rev") or 0) + 1
    a["updated_at"] = _now()
    path = _archive_path(a["review_no"])
    with S.locked(path):
        S.write_json(path, a)
        S.audit("flow:%s" % (action or "save"),
                {"tool": TOOL, "actor": actor, "session_no": session_no,
                 "key": a["review_no"], "after": {"round": a.get("round"),
                                                  "status": a.get("status")}})
    return {"written": True, "path": path, "rev": a["rev"]}


def call(kb_id: str = "", object_slug: str = "", mode: str = "query", action: str = "get",
         review_no: str = "", round: int = 0, filenames=None, status: str = "",
         dry_run: bool = True, actor: str = "", session_no: str = "") -> dict:
    """统一入口（与其余 `ext_*` 同口径：绑定解析 → 动作 → 可读回执 ✓）。"""
    rb = S.resolve_binding(TOOL, kb_id, object_slug)
    if not rb.get("ok"):
        return {"ok": False, "tool": TOOL,
                **{k: rb.get(k) for k in ("why", "hint", "candidates", "object", "binding")
                   if rb.get(k) is not None}}
    binding, obj = rb["binding"], rb["object"]
    mode = (mode or "query").strip().lower()
    action = (action or "get").strip().lower()
    rno = str(review_no or "").strip()
    base = {"ok": True, "tool": TOOL, "object": obj.get("slug"), "action": action,
            "mode": mode, "review_no": rno,
            "binding": {"tool": binding.get("tool"), "modes": binding.get("modes"),
                        "keys": binding.get("keys"), "impl": binding.get("impl")},
            "fetched_at": _now()}
    if not rno:
        return {"ok": False, "tool": TOOL, "error": "need_review_no",
                "hint": "外层主键 = 流程编号（智能体提交的编号视为平台已存在 ✓）"}

    arch = _load(rno)
    docs = _doc_rows(rno, 0)

    # ── 只读：get / docs / rounds ───────────────────────────────────────
    if mode == "query" and action in ("get", "docs", "rounds"):
        if not arch:                              # 用户口径：编号一律视为已存在 ⇒ 自动建档
            arch = _new_archive(rno, int(round or 1), docs)
            arch["created_by"] = "auto"
            _save(arch, actor=actor or "auto", session_no=session_no, action="auto_create")
        elif not (arch.get("docs")) and docs:     # 读路径**自愈**：档案没文档但索引里有 ⇒ 补上并落盘
            arch["docs"] = docs
            _save(arch, actor=actor or "auto", session_no=session_no, action="heal_docs")
        else:                                     # 档案里的文档可能**过期**（种子更新）⇒ 按索引刷新
            live = {d["doc_id"]: d for d in docs}
            cur, dirty = list(arch.get("docs") or []), False
            for i, d in enumerate(cur):
                ld = live.get(d.get("doc_id") or "")
                if ld and (d.get("title") != ld.get("title")
                           or int(d.get("size") or 0) != int(ld.get("size") or 0)):
                    cur[i], dirty = ld, True
            if dirty:
                arch["docs"] = cur
                _save(arch, actor=actor or "auto", session_no=session_no, action="refresh_docs")
        if action == "get":
            return {**base, "flow_no": arch.get("flow_no"), "name": arch.get("name"),
                    "stage": arch.get("stage"), "status": arch.get("status"),
                    "round": arch.get("round"), "rounds": len(arch.get("rounds") or []),
                    "owner": arch.get("owner"), "reviewers": arch.get("reviewers") or [],
                    "authority": arch.get("authority"), "rev": arch.get("rev"),
                    "counts": {"docs": len(arch.get("docs") or [])},
                    "docs": [_doc_brief(d) for d in (arch.get("docs") or [])],
                    "next": ("文档正文用 `ext_doc_access(action=outline, doc_id=…)`；"
                             "**不需要上传文档** ✓")}
        if action == "docs":
            rows = ([d for d in (arch.get("docs") or [])
                     if not round or int(d.get("round") or 1) == int(round)]
                    if arch.get("docs") else _doc_rows(rno, int(round or 0)))
            return {**base, "round": int(round or arch.get("round") or 0), "rows": rows,
                    "counts": {"docs": len(rows)},
                    "hint": "取正文：`ext_doc_access(action=outline|section, doc_id=…)`；"
                            "下载：`url`（GET 附件流）✓"}
        rnds = arch.get("rounds") or []
        cur = int(arch.get("round") or 1)
        return {**base, "round": cur,
                "rounds": [dict(x, is_current=(int(x.get("round") or 0) == cur)) for x in rnds],
                "counts": {"rounds": len(rnds)}}

    # ── 开工：open（建档 + 绑文档；只写"档案"，不动业务数据） ────────────
    if mode == "query" and action == "open":
        want = int(round or 0)
        notes, warn = [], ""
        if not arch:
            arch = _new_archive(rno, want or 1, docs)
            notes.append("编号不存在 ⇒ 按\"平台已存在\"口径**建档**（status/round=%d）"
                         % arch["round"])
        elif want and want != int(arch.get("round") or 1):
            if want > int(arch.get("round") or 1):
                arch["round"] = want
                arch.setdefault("rounds", []).append({"round": want, "status": "进行中",
                                                      "started_at": _now(), "ended_at": ""})
                notes.append("按智能体要求推进到第 %d 轮（round_source=agent）" % want)
            else:
                warn = "round_behind"
                notes.append("智能体要求第 %d 轮 < 档案第 %s 轮 ⇒ **以档案为准**"
                             % (want, arch.get("round")))
        names = [str(x).strip() for x in (filenames or []) if str(x).strip()]
        if names:
            picked, missing = [], []
            for n in names:
                hit = [d for d in (arch.get("docs") or [])
                       if n in (d.get("title") or "") or (d.get("title") or "") in n]
                if hit:
                    picked.extend(hit)
                else:
                    missing.append(n)
            if picked:
                arch["docs"] = picked
                notes.append("按文件名绑定 %d 份文档" % len(picked))
            if missing:
                notes.append("未匹配到文档：%s" % "、".join(missing))
        if not arch.get("docs"):
            notes.append("该编号下没有任何文档 ⇒ `docs_missing`；报告按「文档不可得（待澄清）」写 ✓")
        arch["authority"] = arch.get("authority") or "agent_requested"
        _save(arch, actor=actor, session_no=session_no, action="open")
        out = {**base, "flow_no": arch.get("flow_no"), "name": arch.get("name"),
               "status": arch.get("status"), "stage": arch.get("stage"),
               "round": arch.get("round"),
               "round_source": ("agent" if want and want >= int(arch.get("round") or 1)
                                else "archive"),
               "authority": arch.get("authority"), "rev": arch.get("rev"),
               "docs": [_doc_brief(d) for d in (arch.get("docs") or [])],
               "notes": notes,
               "next": "ext_doc_access(action=outline, doc_id=<第一个 doc_id>) ⇒ 再 section 逐节取正文"}
        if warn:
            out["warn"] = warn
        if not arch.get("docs"):
            out["docs_missing"] = True
        return out

    # ── 维护（写）─────────────────────────────────────────────────────
    if "maintain" not in (binding.get("modes") or []):
        return {"ok": False, "tool": TOOL, "error": "mode_not_supported",
                "hint": "该对象的 AccessMode 没声明 maintain（分号分隔、与接口顺序对齐）"}
    if action not in ("upsert_round", "close"):
        return {"ok": False, "tool": TOOL, "error": "bad_action",
                "allowed": ["upsert_round", "close"]}
    if not actor:
        return {"ok": False, "tool": TOOL, "error": "need_actor",
                "hint": "写档案要带执行者（审计用）"}
    if not arch:
        arch = _new_archive(rno, int(round or 1), docs)
    before = {"round": arch.get("round"), "status": arch.get("status")}
    if action == "upsert_round":
        r = int(round or arch.get("round") or 1)
        arch["round"] = r
        arch.setdefault("rounds", []).append({"round": r, "status": "进行中",
                                              "started_at": _now(), "ended_at": ""})
    else:
        arch["status"] = status or "已完成"
        for x in (arch.get("rounds") or []):
            if int(x.get("round") or 0) == int(arch.get("round") or 0):
                x["status"] = "已结束"
                x["ended_at"] = _now()
    diff = {"before": before, "after": {"round": arch.get("round"),
                                       "status": arch.get("status")}}
    if dry_run:
        return {**base, "dry_run": True, "diff": diff, "rev": arch.get("rev"),
                "hint": "dry_run 未落盘；确认后 dry_run=false 才写"}
    w = _save(arch, actor=actor, session_no=session_no, action=action)
    return {**base, "dry_run": False, "rev": w["rev"], "diff": diff,
            "round": arch.get("round"), "status": arch.get("status")}
