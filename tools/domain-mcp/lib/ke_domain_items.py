"""ke-core · **外部接口（模拟）：条目清单维护**（只读查询 + 维护两种模式）

口径
----
- 分片：`issues/<review_no>.json`（按外层主键分片 ⇒ 小文件、少锁竞争）
- 幂等键：`(review_no, issue_no)` —— 同键 `create` = 覆盖更新（回执标 `deduped` ✓）
- 乐观并发：`update/close/delete` 支持 `if_rev`；不匹配 ⇒ `conflict`（**不静默覆盖** ✗）
- 写默认 `dry_run=True`（只回 diff）；落盘才写审计、`rev+1`
- **新增栏位**：`expert_feedback`（外部专家反馈，可空；报告侧据此回显）
- 列顺序/表头来自容器数据（`schema.json` 的 `issue_labels/issue_order`），代码不写业务词 ✓
"""
from __future__ import annotations

import hashlib
import os
import re
import time

import ke_domain_store as S

TOOL = "ext_issue_list"


def _now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def _fingerprint(item: dict) -> str:
    """版本指纹：**按契约声明算**（`x_ext.ext_issue_list.fingerprint`），代码不写死要素。

    - `fields`：参与哈希的字段（缺一个 ⇒ 回空，**宁缺不编** ✓）；
    - `algo` / `length` / `prefix`：算法、截断长度、前缀（如 `r-`）；
    - 契约没声明 ⇒ 不算（不猜、不内置默认 ✓）；由**工具侧**统一算，避免各端算法漂移。
    """
    spec = _schema().get("fingerprint") or {}
    fields = [str(f) for f in (spec.get("fields") or []) if str(f).strip()]
    if not fields:
        return ""
    parts = [istr(item.get(f)) for f in fields]
    if not all(parts):
        return ""
    algo = str(spec.get("algo") or "sha256").lower()
    if algo != "sha256":
        return ""
    raw = "|".join(parts)
    n = int(spec.get("length") or 6)
    return str(spec.get("prefix") or "") + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:n]


def _fill_fp(item: dict) -> dict:
    """就地补指纹（只补空值；`rule_fp=""` 且带 `human_input` 的人工条目不动 ✓）。"""
    if istr(item.get("rule_fp")):
        return item
    if item.get("human_input"):
        return item
    fp = _fingerprint(item)
    if fp:
        item["rule_fp"] = fp
    return item


def istr(v) -> str:
    return str(v if v is not None else "").strip()


def _schema() -> dict:
    """列集 / 列序 / 标签 / 枚举 / 指纹口径 **全部来自契约**（`x_ext.<tool>`）。

    2026-11-02（P3）：删掉代码里的 `FIELDS` 常量与指纹四要素 —— 契约是唯一真源；
    旧键 `issue_order` / `issue_labels` 仅在 `x_ext` 缺失的**旧部署**上兜底（过渡用）。
    """
    sc = S.read_json(os.path.join(S.system_dir(), "schema.json"), {}) or {}
    tool = ((sc.get("x_ext") or {}).get(TOOL) or {})
    cols = [c for c in (tool.get("columns") or []) if isinstance(c, dict) and c.get("field")]
    fields = [str(c["field"]) for c in cols]
    labels = {str(c["field"]): (c.get("label") or str(c["field"])) for c in cols}
    fingerprint = tool.get("fingerprint") or {}
    if not fields:                                   # 过渡：旧契约键
        fields = [str(f) for f in (sc.get("issue_order") or []) if str(f).strip()]
        labels = dict(sc.get("issue_labels") or {})
        fingerprint = sc.get("issue_fingerprint") or {}
    return {"fields": fields, "order": list(fields), "labels": labels,
            "verdict_enum": sc.get("verdict_enum") or [],
            "severity_enum": sc.get("severity_enum") or [],
            "fingerprint": fingerprint}


def _path(review_no: str) -> str:
    return S.shard_path("issues", review_no, subdir="issues")


def _load(review_no: str) -> dict:
    d = S.read_json(_path(review_no), {}) or {}
    if not isinstance(d.get("items"), list):
        d["items"] = []
    d.setdefault("review_no", str(review_no or ""))
    d.setdefault("rev", 0)
    # 2026-10-25：读时就补指纹（只补空值）—— 老条目没指纹也能在回执/报告里显示 ✓；
    # 下次写该分片时随 `write_json` 一并落盘（自愈 ✓）。
    for it in d["items"]:
        if isinstance(it, dict):
            _fill_fp(it)
    d["items"] = _sorted_items(d["items"])      # 行序稳定（P-2 < P-10）✓
    return d


def _find(doc: dict, issue_no: str):
    for i, it in enumerate(doc["items"]):
        if str(it.get("issue_no") or "") == str(issue_no or ""):
            return i, it
    return -1, None


def _sorted_items(items: list) -> list:
    """按 `issue_no` 自然排序（P-2 < P-10）—— 台账里"删了重建"会把行序打乱，渲染前统一 ✓。"""
    import re as _re

    def key(it: dict):
        s = str(it.get("issue_no") or "")
        m = _re.match(r"^([A-Za-z]*)[-_ ]?(\d+)$", s)
        return ((m.group(1), int(m.group(2))) if m else (s, 0))
    return sorted(items or [], key=key)


def _columns(schema: dict, columns=None) -> list:
    """把模板给的字段子集解析成**有序列表**（非法字段抛 ValueError，由入口转可读回执）。"""
    order = list(schema.get("order") or schema.get("fields") or [])
    if columns in (None, "", [], ()):
        return order
    want = columns if isinstance(columns, (list, tuple)) else str(columns).replace("，", ",").split(",")
    want = [str(c).strip() for c in want if str(c).strip()]
    bad = [c for c in want if c not in order]
    if bad:
        raise ValueError("字段不在台账契约里：%s（可用：%s）" % ("、".join(bad), "、".join(order)))
    return want or order


def _summary(items: list) -> dict:
    out: dict = {}
    for it in items:
        k = str(it.get("verdict") or "未填")
        out[k] = out.get(k, 0) + 1
    return out


def _pack(mode: str, obj: dict, binding: dict, schema: dict, **kw) -> dict:
    out = {"ok": True, "tool": TOOL, "mode": mode, "object": obj.get("slug"),
           "binding": {"tool": binding.get("tool"), "modes": binding.get("modes"),
                       "keys": binding.get("keys"), "impl": binding.get("impl")},
           "schema": {"fields": schema["fields"], "order": schema["order"],
                      "columns": schema.get("columns") or schema["order"],
                      "labels": schema["labels"],
                      "verdict_enum": schema["verdict_enum"],
                      "severity_enum": schema["severity_enum"]},
           "fetched_at": _now()}
    out.update(kw)
    return out


def maintain(kb_id: str = "", object_slug: str = "", mode: str = "query", action: str = "list",
             review_no: str = "", issue_no: str = "", fields: dict | None = None,
             items: list | None = None,
             view: str = "rows", if_rev=None, dry_run: bool = True,
             actor: str = "", session_no: str = "", columns=None) -> dict:
    """统一入口（`mode=query` 只读；`mode=maintain` 写，默认 dry_run）。

    `columns`：**报告要用哪几列、什么顺序**（模板配置的字段子集，逗号分隔或列表）——
    工具字段可以多于某份报告所需（服务端字段集 ⊋ 报告字段集），这里只做筛选 ✓。

    `items`：**批量写入**（2026-10-26 用户口径）—— 一次 `create` 提交全部条目，`rev` 只 +1。
    逐条 create 会让"新建档案"的 rev 变成 9（8 条 + 1）✗；批量写入后 rev = **编号级写入版本** ✓。
    """
    rb = S.resolve_binding(TOOL, kb_id, object_slug)
    if not rb.get("ok"):
        return {"ok": False, "tool": TOOL,
                **{k: rb.get(k) for k in ("why", "hint", "candidates", "object", "binding")
                   if rb.get(k) is not None}}
    binding, obj, schema = rb["binding"], rb["object"], _schema()
    mode = (mode or "query").strip().lower()
    action = (action or "list").strip().lower()
    view = (view or "rows").strip().lower()
    try:                                   # 字段子集先校验：非法字段给可读回执（不抛）
        sel = _columns(schema, columns)
    except ValueError as exc:
        return {"ok": False, "tool": TOOL, "error": "bad_columns", "detail": str(exc),
                "allowed": list(schema.get("order") or [])}
    schema = dict(schema, columns=sel)
    rno = str(review_no or "").strip()
    if not rno:
        return {"ok": False, "tool": TOOL, "error": "需要 review_no",
                "hint": "外层主键（分片依据）；issue_no 是内层主键"}
    doc = _load(rno)

    # ── 只读 ────────────────────────────────────────────────────────────
    if mode == "query" or action in ("list", "get"):
        if action == "get":
            _i, it = _find(doc, issue_no)
            if it is None:
                return {"ok": False, "tool": TOOL, "error": "not_found", "issue_no": issue_no}
            return _pack(mode, obj, binding, schema, row=it, rev=doc.get("rev", 0))
        items = doc["items"]
        if issue_no:                      # 兼容：用 issue_no 过滤单条
            _i, it = _find(doc, issue_no)
            items = [it] if it else []
        out = _pack(mode, obj, binding, schema, rows=items, rev=doc.get("rev", 0),
                    updated_at=doc.get("updated_at") or "",
                    counts={"items": len(items)}, summary=_summary(items))
        return out

    # ── 维护（写）───────────────────────────────────────────────────────
    if "maintain" not in (binding.get("modes") or []):
        return {"ok": False, "tool": TOOL, "error": "mode_not_supported",
                "hint": "该对象的绑定没有声明 maintain 模式（bmm:sourceLocator 的 mode= 里加）",
                "object": obj.get("slug"), "modes": binding.get("modes")}
    if action not in ("create", "update", "close", "delete"):
        return {"ok": False, "tool": TOOL, "error": "未知 action",
                "allowed": ["list", "get", "create", "update", "close", "delete"]}
    if not actor:
        return {"ok": False, "tool": TOOL, "error": "maintain 需要 actor"}
    payload = dict(fields or {})
    if action in ("create", "update") and not payload and not items:
        return {"ok": False, "tool": TOOL, "error": "需要 fields（要写的列）或 items（批量）"}
    # ── 批量 create（一次调用 ⇒ rev 只 +1）────────────────────────────────
    if action == "create" and items:
        rows, bad = [], []
        for i, raw in enumerate(items):
            it = dict(raw or {})
            no = str(it.pop("issue_no", "") or "").strip()
            if not no:
                bad.append({"index": i, "error": "need_issue_no"})
                continue
            if schema["verdict_enum"] and it.get("verdict") \
                    and it["verdict"] not in schema["verdict_enum"]:
                bad.append({"index": i, "issue_no": no, "error": "bad_verdict"})
                continue
            if schema["severity_enum"] and it.get("severity") \
                    and it["severity"] not in schema["severity_enum"]:
                bad.append({"index": i, "issue_no": no, "error": "bad_severity"})
                continue
            if not istr(it.get("rule_fp")):     # 调用方给了就**原样保留**（模板口径：= 规则页 ruleHash ✓）
                fp = _fingerprint(it)
                if fp:
                    it["rule_fp"] = fp
            rows.append((no, it))
        path = _path(rno)
        if dry_run:
            return _pack("maintain", obj, binding, schema, dry_run=True, action="create_batch",
                         rev=int(doc.get("rev") or 0), planned=len(rows), rejected=bad,
                         next="确认无误后传 dry_run=false 才落盘（一条批量 = rev+1 ✓）")
        with S.locked(path):
            fresh = _load(rno)
            created = updated = 0
            for no, it in rows:
                idx2, cur2 = _find(fresh, no)
                if cur2 is None:
                    rec = dict({"issue_no": no}, **it)
                    rec.setdefault("created_at", _now())
                    rec["updated_at"] = _now()
                    fresh["items"].append(rec)
                    created += 1
                else:
                    rec = dict(cur2, **it)
                    rec["updated_at"] = _now()
                    fresh["items"][idx2] = rec
                    updated += 1
            fresh["rev"] = int(fresh.get("rev") or 0) + 1
            fresh["updated_at"] = _now()
            S.write_json(path, fresh)
            S.audit("issues:create_batch",
                    {"tool": TOOL, "actor": actor, "session_no": session_no,
                     "key": rno, "dry_run": False,
                     "created": created, "updated": updated, "rejected": bad})
        out = _pack("maintain", obj, binding, schema, dry_run=False, action="create_batch",
                    rev=fresh["rev"], created=created, updated=updated, rejected=bad,
                    counts={"items": len(fresh["items"])},
                    rev_note="rev = 该编号台账的写入版本（本次 +1，不等于业务轮次）")
        return out
    # 2026-10-25：**工具侧**补版本指纹（本体口径：机器生成必须有；人工条目带 human_input 则留空 ✓）
    if not istr(payload.get("rule_fp")):
        _cur_for_fp = _find(doc, issue_no)[1] or {}     # 注意：`cur` 在下面才赋值，这里单独取
        merged = dict(_cur_for_fp, **payload)
        fp = _fingerprint(merged)
        if fp:
            payload["rule_fp"] = fp
    if if_rev not in (None, "") and int(if_rev) != int(doc.get("rev") or 0):
        return {"ok": False, "tool": TOOL, "error": "conflict",
                "if_rev": int(if_rev), "rev": int(doc.get("rev") or 0),
                "hint": "有人改过 ⇒ 先 list 拿最新 rev 再改（不静默覆盖 ✗）"}
    if schema["verdict_enum"] and payload.get("verdict") \
            and payload["verdict"] not in schema["verdict_enum"]:
        return {"ok": False, "tool": TOOL, "error": "bad_verdict",
                "allowed": schema["verdict_enum"]}
    if schema["severity_enum"] and payload.get("severity") \
            and payload["severity"] not in schema["severity_enum"]:
        return {"ok": False, "tool": TOOL, "error": "bad_severity",
                "allowed": schema["severity_enum"]}

    idx, cur = _find(doc, issue_no)
    before = cur
    if action == "create":
        if cur is not None:                       # 幂等：同键 = 覆盖更新 ✓
            after = dict(cur, **payload)
            after["updated_at"] = _now()
            doc["items"][idx] = after
            deduped = True
        else:
            if not str(issue_no or "").strip():
                return {"ok": False, "tool": TOOL, "error": "create 需要 issue_no"}
            after = dict({"issue_no": issue_no}, **payload)
            after.setdefault("created_at", _now())
            after["updated_at"] = _now()
            doc["items"].append(after)
            deduped = False
    elif action == "update":
        if cur is None:
            return {"ok": False, "tool": TOOL, "error": "not_found", "issue_no": issue_no}
        after = dict(cur, **payload)
        after["updated_at"] = _now()
        doc["items"][idx] = after
        deduped = False
    elif action == "close":
        if cur is None:
            return {"ok": False, "tool": TOOL, "error": "not_found", "issue_no": issue_no}
        after = dict(cur, **payload)
        after["closed_at"] = _now()
        after["updated_at"] = _now()
        doc["items"][idx] = after
        deduped = False
    else:                                          # delete
        if cur is None:
            return {"ok": False, "tool": TOOL, "error": "not_found", "issue_no": issue_no}
        after = None
        doc["items"].pop(idx)
        deduped = False

    diff = {"before": before, "after": after}
    if dry_run:
        return _pack("maintain", obj, binding, schema, dry_run=True, action=action,
                     diff=diff, rev=doc.get("rev", 0), deduped=deduped,
                     next="确认无误后传 dry_run=false 才落盘")
    path = _path(rno)
    with S.locked(path):
        fresh = _load(rno)                              # 锁内重读，避免丢更新
        idx2, cur2 = _find(fresh, issue_no)
        if action == "create":
            if cur2 is None:
                fresh["items"].append(after)
            else:
                fresh["items"][idx2] = after
        elif action in ("update", "close"):
            if cur2 is None:
                return {"ok": False, "tool": TOOL, "error": "not_found",
                        "hint": "并发下被删除了 ⇒ 重新 list"}
            fresh["items"][idx2] = after
        else:
            if cur2 is not None:
                fresh["items"].pop(idx2)
        fresh["rev"] = int(fresh.get("rev") or 0) + 1
        fresh["updated_at"] = _now()
        S.write_json(path, fresh)
        S.audit("issues:%s" % action, {"tool": TOOL, "actor": actor, "session_no": session_no,
                                       "key": "%s/%s" % (rno, issue_no), "dry_run": False,
                                       "before": before, "after": after})
    out = _pack("maintain", obj, binding, schema, dry_run=False, action=action, diff=diff,
                rev=fresh["rev"], deduped=deduped, counts={"items": len(fresh["items"])})
    if view == "table":
        out["markdown"] = _md(fresh["items"], schema, sel)
    return out
