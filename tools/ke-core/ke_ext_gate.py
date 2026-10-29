"""报告写入闸门：**条目编号必须来自外部台账**（G3 硬失败），行数/契约漂移给软警告（G1/G2）。

口径（2026-10-25 用户确认"分级"）
- **G3 硬**：报告表里出现的条目编号 ⊄ 台账主键 ⇒ 拒写（`ext_unknown_items`）
  —— 堵住"模型自己编号 001…"这条路；
- **G2 软**：表行数 ≠ 台账条目数 ⇒ `warnings: ["ext_count_mismatch"]`；
- **G1 软**：正文若带 `schema_hash: sha256:…`（`bodhi-ext` 引用块）且 ≠ 当前 `x-ext.schemaHash`
  ⇒ `warnings: ["ext_schema_stale"]`；没有引用块 ⇒ `ext_no_ref_block`（也算软提示）。

**不抛异常**（`check_report` 永远回 dict）；台账目录不存在 ⇒ 只给 `no_ledger` 提示，不拦写。
"""
from __future__ import annotations

import glob
import json
import os
import re

_LEDGER_GLOB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                            "state", "ext", "it-platform", "issues", "*.json")
_SPLIT = re.compile(r"(?<!\\)\|")


def _ledger_dir() -> str:
    try:
        import ke_ext_store as S
        return os.path.join(S.ext_root(), S.DEFAULT_SYSTEM, "issues")
    except Exception:                                  # noqa: BLE001
        return os.path.dirname(_LEDGER_GLOB)


def _review_no_hint(md: str) -> str:
    """从正文里取「评审编号」值（① 表的首列含"编号"那一行）——不写死表头措辞。"""
    for ln in (md or "").splitlines():
        if not ln.strip().startswith("|"):
            continue
        cells = _cells(ln)
        if len(cells) >= 2 and cells[0].strip().endswith("编号"):
            val = cells[1].strip()
            if val and val not in ("—", "-", "无"):
                return val
    return ""


def _ledgers(md: str = "") -> dict:
    """`{review_no: {"items": [...], "rev": n}}`。

    2026-10-29（用户口径）：02 与 04 **不共享磁盘** ⇒ 本地没有台账分片时，
    **按正文里的评审编号调 04 的 `ext_issue_list`** 取该编号的台账（rev/items）；
    两者都没有 ⇒ 空（= 没有台账，报告不必补块）。
    """
    out = {}
    for path in glob.glob(os.path.join(_ledger_dir(), "*.json")):
        try:
            with open(path, encoding="utf-8") as fh:
                doc = json.load(fh)
        except Exception:                              # noqa: BLE001  坏文件跳过，不影响写库
            continue
        if isinstance(doc, dict) and isinstance(doc.get("items"), list):
            out[str(doc.get("review_no") or os.path.basename(path)[:-5])] = doc
    if out or not md:
        return out
    rno = _review_no_hint(md)
    if not rno:
        return {}
    try:
        import ke_ext_client as _C  # noqa: PLC0415
        got = _C.call("ext_issue_list", {"mode": "query", "action": "list",
                                         "review_no": rno, "view": "rows"})
        if got.get("ok") and isinstance(got.get("rows"), list):
            return {rno: {"review_no": rno, "rev": int(got.get("rev") or 0),
                          "items": got["rows"]}}
    except Exception:                                  # noqa: BLE001  04 不可达 ⇒ 当作没有台账
        pass
    return {}



def _cells(line: str) -> list:
    return [c.strip() for c in _SPLIT.split(line.strip().strip("|"))]


def _report_items(md: str) -> list:
    """把正文里\"带条目编号列\"的表读成 `[{issue_no, cells}]`（按表头定位列，不靠固定下标 ✓）。

    **只认第一张**这样的表：报告里别处（如\"表后补充块\"）也可能有含「编号」的表头，
    全算进来会让 G2 行数虚高（实测：11 行报成 12 行 ✗）。
    """
    try:
        import ke_ext_issues as I
        label = (I._schema().get("labels") or {}).get("issue_no") or "编号"   # noqa: SLF001
    except Exception:                                  # noqa: BLE001
        label = "编号"
    out, col, done = [], None, False
    for line in (md or "").splitlines():
        if not line.strip().startswith("|"):
            if col is not None:
                done = True                            # 第一张表到此结束 ⇒ 后面的表不再算
            col = None
            continue
        if done:
            continue
        cells = _cells(line)
        if col is None:
            if label in cells:
                col = cells.index(label)
            continue
        if all(set(c) <= set("-: ") for c in cells if c):        # 分隔行
            continue
        if col < len(cells) and cells[col]:
            out.append({"issue_no": cells[col], "cells": cells})
    return out


def _kb_objects(kb_id: str) -> list:
    """该库里声明了接口的 DataObject 页（**本库没有就全库找**：报告在工作库、绑定在领域库 ✓）。"""
    import ke_ext_store as S  # noqa: PLC0415
    try:
        import ke_db  # noqa: PLC0415
    except Exception:                                  # noqa: BLE001
        return []

    def _rows(where: str) -> list:
        try:
            return ke_db.psql_csv(
                "SELECT slug, knowledge_base_id AS kb FROM wiki_pages WHERE %sdeleted_at IS NULL "
                " AND page_type LIKE '%%DataObject%%'" % where) or []
        except Exception:                              # noqa: BLE001
            return []

    rows = _rows("knowledge_base_id = %s AND " % ke_db.sql_str(kb_id)) if kb_id else []
    if not rows:
        rows = _rows("")
    out = []
    for r in rows:
        slug, kb = r.get("slug") or "", r.get("kb") or kb_id
        try:
            for b in S.parse_binding(S._attrs_of(kb, slug)):       # noqa: SLF001
                out.append({"slug": slug, "kb_id": kb, "binding": b})
        except Exception:                              # noqa: BLE001
            continue
    return out


def _contract_of(obj: dict) -> dict:
    """接口契约（`{labels, order, source}`）：**只认页面投射**（`x-ext`）；没有 ⇒ source=none，不猜 ✗。"""
    try:
        import json  # noqa: PLC0415

        import ke_db  # noqa: PLC0415
        rows = ke_db.psql_csv(
            "SELECT page_metadata FROM wiki_pages WHERE deleted_at IS NULL "
            " AND slug = %s LIMIT 1" % ke_db.sql_str(obj["slug"]))
        meta = json.loads((rows[0].get("page_metadata") if rows else "") or "{}")
        cols = ((((meta.get("ontology") or {}).get("projection") or {})
                 .get("contract") or {}).get("columns") or [])
        labels = {c.get("field"): c.get("label") for c in cols if c.get("field")}
        order = [c["field"] for c in cols if c.get("field")]
        if labels:
            return {"labels": labels, "order": order, "source": "projection"}
    except Exception:                                  # noqa: BLE001
        pass
    # 2026-10-29（用户口径）：**没有 projection 也能补块** —— 回落工具契约文件
    # （04 的 `schema.json`，随包同版本；`source="contract_file"` 让回执可追溯）。
    try:
        import ke_ext_issues as _I  # noqa: PLC0415
        sc = _I._schema()           # noqa: SLF001
        labels = dict(sc.get("labels") or {})
        order = list(sc.get("order") or [])
        if labels and order:
            return {"labels": labels, "order": order, "source": "contract_file"}
    except Exception:                                  # noqa: BLE001
        pass
    return {"labels": {}, "order": [], "source": "none"}



def _tables(md: str) -> list:
    """正文里所有表格 → `[{start, end, header}]`（行号含表头与分隔行）。"""
    lines = (md or "").splitlines()
    out, i = [], 0
    while i < len(lines):
        if not lines[i].strip().startswith("|"):
            i += 1
            continue
        start = i
        while i + 1 < len(lines) and lines[i + 1].strip().startswith("|"):
            i += 1
        header = [c.strip() for c in lines[start].strip().strip("|").split("|")]
        out.append({"start": start, "end": i, "header": header})
        i += 1
    return out


def _service_of(binding: dict) -> str:
    impl = str((binding or {}).get("impl") or "")
    return impl.split(":", 1)[0].strip() if ":" in impl else ""


def _schema_hash(kb_id: str, slug: str) -> str:
    try:
        import json  # noqa: PLC0415

        import ke_db  # noqa: PLC0415
        rows = ke_db.psql_csv(                       # 绑定在**领域库**、报告在工作库 ⇒ 按 slug 找 ✓
            "SELECT page_metadata FROM wiki_pages WHERE deleted_at IS NULL "
            " AND slug = %s LIMIT 1" % ke_db.sql_str(slug))
        meta = json.loads((rows[0].get("page_metadata") if rows else "") or "{}")
        return str((((meta.get("ontology") or {}).get("projection") or {})
                    .get("schemaHash")) or "")
    except Exception:                                  # noqa: BLE001
        return ""


def auto_ref_block(md: str, kb_id: str = "") -> dict:
    """报告**缺引用块**时由工具补齐（值全来自台账/契约，**不让模型手写编号** ✗）。

    为什么要它（2026-10-25 用户实测）：智能体漏写引用块 ⇒ 页面没有锚点 ⇒ **面板不出现**；
    而"编号"本来就在报告 ① 表里（上下文）⇒ 工具拿它去对台账分片，反查 `review_no` + `rev`，
    再按"哪张表的表头与某个 DataObject 的契约标签对得上"确定 `object`
    （**多个命中不猜** ✗，回 `object_ambiguous` 交人定）。

    返回 `{changed, md, object?, review_no?, rev?, columns?, reason}`。
    """
    if not md or "```bodhi-ext" in md:
        return {"changed": False, "md": md, "reason": "has_block"}
    if not kb_id:
        return {"changed": False, "md": md, "reason": "need_kb"}
    ledgers = _ledgers(md)
    if not ledgers:
        return {"changed": False, "md": md, "reason": "no_ledger"}
    items = _report_items(md)
    if not items:
        return {"changed": False, "md": md, "reason": "no_item_table"}
    nums = [x["issue_no"] for x in items]
    # 编号优先**取自报告正文**（台账分片名 = 外层主键值；唯一命中才算 ✓）
    # —— 不能用"条目编号重叠"定分片：不同台账常有同名条目（P-01…），会选错 ✗
    best, best_hit, src = "", -1, "ledger_overlap"
    try:
        import ke_ext_store as _S  # noqa: PLC0415
        names = [n for n in _S.ledger_names() if n and n in md]
    except Exception:                                  # noqa: BLE001
        names = []
    if len(names) == 1:
        best, best_hit, src = names[0], len(nums), "report_context"
    else:
        for rno, doc in ledgers.items():
            known = {str(i.get("issue_no") or "") for i in doc["items"]}
            hit = len([n for n in nums if n in known])
            if hit > best_hit or (hit == best_hit and best and rno < best):
                best, best_hit = rno, hit
    if not best or best_hit <= 0:
        return {"changed": False, "md": md, "reason": "ledger_no_match"}
    ledger = ledgers.get(best) or {}
    tables = _tables(md)
    cands = {}
    for obj in _kb_objects(kb_id):
        con = _contract_of(obj)
        labels, order = con["labels"], con["order"]
        if not order:
            continue                                 # 契约拿不到（projection/契约文件都没有）⇒ 不猜 ✗

        anchor = labels.get(order[0]) or ""
        if not anchor:
            continue
        for t in tables:
            if anchor not in t["header"]:
                continue
            hitf = [f for f in order if labels.get(f) and labels[f] in t["header"]]
            if len(hitf) < 3:
                continue
            cur = cands.get(obj["slug"])
            if cur is None or len(hitf) > len(cur["fields"]):
                cands[obj["slug"]] = {"obj": obj, "labels": labels, "fields": hitf,
                                      "anchor": anchor, "table": t}
    if not cands:
        return {"changed": False, "md": md, "reason": "no_object_match"}
    best_n = max(len(c["fields"]) for c in cands.values())
    top = [c for c in cands.values() if len(c["fields"]) == best_n]
    if len({c["obj"]["slug"] for c in top}) > 1:
        return {"changed": False, "md": md, "reason": "object_ambiguous",
                "candidates": sorted({c["obj"]["slug"] for c in top})}
    hit = top[0]
    t = hit["table"]
    cols = sorted(hit["fields"], key=lambda f: t["header"].index(hit["labels"][f]))
    block = ["```bodhi-ext", "object: %s" % hit["obj"]["slug"], "review_no: %s" % best,
             "basis: %s / %s" % (_service_of(hit["obj"]["binding"]),
                                 hit["obj"]["binding"].get("tool") or ""),
             "columns: %s" % ",".join(cols),
             "rev: %s" % int(ledger.get("rev") or 0)]
    h = _schema_hash(kb_id, hit["obj"]["slug"])
    if h:
        block.append("schema_hash: %s" % h)
    block.append("```")
    lines = md.splitlines()
    j = t["end"]                                     # 块紧跟**匹配到的那张表**之后 ✓
    lines = lines[:j + 1] + [""] + block + lines[j + 1:]
    return {"changed": True, "md": "\n".join(lines) + ("\n" if md.endswith("\n") else ""),
            "object": hit["obj"]["slug"], "review_no": best, "rev": int(ledger.get("rev") or 0),
            "columns": cols, "review_no_source": src, "reason": "added"}


def check_report(md: str, kb_id: str = "", enforce: bool = False) -> dict:
    """→ `{ok, ledger, unknown_items[], warnings[], counts}`；`ok=False` 仅当 `enforce` 且 G3 命中。"""
    ledgers = _ledgers()
    items = _report_items(md)
    nums = [x["issue_no"] for x in items]
    res = {"ok": True, "report_items": len(nums), "ledger": "", "ledger_rev": 0,
           "unknown_items": [], "warnings": [], "counts": {}}
    # 与外部接口**无关**的文档（模板页、普通说明页）：既没有条目表、也没有引用块 ⇒ 直接跳过，
    # 不要报 `ext_no_ref_block` / 行数不符（那会污染模板页等写库回执 ✗）
    if not nums and "```bodhi-ext" not in (md or ""):
        res["skipped"] = "no_ext_ref"
        return res
    if not ledgers:
        res["warnings"].append("no_ledger")
        res["hint"] = "容器根没有台账分片（外部服务未建过条目）⇒ 只提示不拦写"
        return res
    # 选与报告编号重叠最多的分片；**但引用块里写了编号 ⇒ 以它为准**（编号取自上下文 ✓）
    best, best_hit = "", -1
    blk = re.search(r"(?ms)^```bodhi-ext\s*$.*?^```\s*$", md or "")
    if blk:
        hit = re.search(r"(?m)^\s*review_no\s*:\s*(.+?)\s*$", blk.group(0))
        if hit and hit.group(1).strip() in ledgers:
            best = hit.group(1).strip()
    if not best:
        for rno, doc in ledgers.items():
            known = {str(i.get("issue_no") or "") for i in doc["items"]}
            hit = len([n for n in nums if n in known])
            if hit > best_hit or (hit == best_hit and best and rno < best):
                best, best_hit = rno, hit
    doc = ledgers.get(best) or {}
    known = {str(i.get("issue_no") or "") for i in doc.get("items") or []}
    res.update({"ledger": best, "ledger_rev": int(doc.get("rev") or 0),
                "counts": {"report": len(nums), "ledger": len(known)}})
    res["unknown_items"] = [n for n in nums if n not in known]
    if res["unknown_items"]:
        res["ok"] = not enforce
        res["warnings"].append("ext_unknown_items")
    if len(nums) != len(known):
        res["warnings"].append("ext_count_mismatch")
    m = re.search(r"schema_hash\s*:\s*(sha256:[0-9a-f]+)", md or "", re.I)
    if not m:
        res["warnings"].append("ext_no_ref_block")
    else:
        try:
            import ke_ext_client as C
            got = C.describe("it_platform", "ext_issue_list")
            cur = ((got.get("contract") or {}).get("schemaHash") or "")
            res["schema_hash"] = {"report": m.group(1), "current": cur}
            if cur and cur != m.group(1):
                res["warnings"].append("ext_schema_stale")
        except Exception:                              # noqa: BLE001  服务不可达不拦写
            res["warnings"].append("ext_schema_unchecked")
    return res
