"""映射引擎：**领域口径 ↔ 工具契约**的双向转换（2026-10-30，P3-b）。

为什么要它
----------
以前只有"记录映射"（`fieldMap` 写在绑定边上），**取数/渲染完全没用它** ⇒
领域里列名/枚举只要和外部工具不一致，报告就跑不通。这一层把三件事收敛：

    business_columns(fmap, tool_order)   业务列名 + **领域列序**（展示用）
    to_business_value / to_tool_value    枚举值双向转换（未知值**原样透传** + warn）
    render_table(rows, schema, fmap)     直接产出**业务口径**的 Markdown 表
    to_tool_items(items, fmap)           写入前把业务键/业务值翻成工具口径

`fieldMap` 结构（写在绑定边属性上，兼容旧的最小结构）：

    {"order": ["issue_no", ...],            # **工具字段**，顺序＝领域展示顺序
     "items": [{"field": "issue_no",        # 工具字段（机器）
                "label": "编号",             # 业务列名（展示）
                "enum": {"不合格": "不符合"}}], # 业务值 → 工具值（反向即读时转换）
     "updatedAt": "..."}

口径
----
- **展示以领域为准**：列名用 `label`、列序用 `order`、值用业务枚举；
- **写入以工具为准**：提交前用 `to_tool_items` 翻回工具键/工具值；
- 未知枚举/未知列 **不丢、不猜**：原样透传并回 `unmapped`（调用方据此 warn）。
"""
from __future__ import annotations

import json


def parse(field_map) -> dict:
    """容错解析 `fieldMap`（dict / JSON 字符串 / 空）→ `{order:[], items:[], enums:{}}`。"""
    doc = field_map
    if isinstance(doc, str):
        try:
            doc = json.loads(doc or "{}")
        except Exception:                    # noqa: BLE001  坏 JSON ⇒ 当作没映射
            doc = {}
    doc = doc if isinstance(doc, dict) else {}
    items = [i for i in (doc.get("items") or []) if isinstance(i, dict) and i.get("field")]
    order = [f for f in (doc.get("order") or []) if isinstance(f, str) and f]
    if not order:
        order = [i["field"] for i in items]
    enums = {i["field"]: (i.get("enum") or {}) for i in items if i.get("enum")}
    labels = {i["field"]: (i.get("label") or "") for i in items}
    return {"order": order, "items": items, "enums": enums, "labels": labels,
            "has": bool(items or order)}


def business_columns(fmap, tool_order: list | None = None) -> list:
    """`[(工具字段, 业务列名)]`，**顺序＝领域顺序**；映射里没有的字段按原顺序补在后面。"""
    m = parse(fmap)
    base = list(m["order"] or [])
    for f in (tool_order or []):
        if f not in base:
            base.append(f)
    return [(f, m["labels"].get(f) or "") for f in base]


def to_business_value(field: str, value, fmap) -> tuple:
    """工具值 → 业务值。→ `(业务值, 是否命中映射)`；未命中原样返回 `False`（调用方 warn）。"""
    m = parse(fmap)
    emap = m["enums"].get(field) or {}
    s = "" if value is None else str(value)
    for biz, tool in emap.items():           # enum: 业务值 → 工具值
        if str(tool) == s:
            return biz, True
    return value, (not emap)


def to_tool_value(field: str, value, fmap) -> tuple:
    """业务值 → 工具值（写入前）。→ `(工具值, 是否命中映射)`。"""
    m = parse(fmap)
    emap = m["enums"].get(field) or {}
    s = "" if value is None else str(value)
    if s in emap:
        return emap[s], True
    return value, (not emap)


def render_table(rows: list, schema: dict, fmap, columns=None) -> tuple:
    """按**业务口径**渲染 Markdown 表。→ `(markdown, unmapped[])`

    - 表头用 `label`（业务列名），列序＝`order`（领域顺序）；
    - 值用业务枚举（工具值 → 业务值）；
    - `columns`（工具字段子集）若给了 ⇒ 只显示这几列（顺序仍按领域顺序）。
    """
    cols = columns or []
    if isinstance(cols, str):
        cols = [c.strip() for c in cols.split(",") if c.strip()]
    m = parse(fmap)
    pairs = business_columns(fmap, [c for c in (schema.get("order") or [])])
    if cols:
        pairs = [(f, lb) for f, lb in pairs if f in cols]
    if not pairs:
        return "（无条目）", []
    unmapped = []
    head = ["| " + " | ".join(lb or f for f, lb in pairs) + " |",
            "|" + "---|" * len(pairs)]
    body = []
    for row in rows or []:
        cells = []
        for f, _lb in pairs:
            v = row.get(f)
            if v is None:
                v = ((row.get("fields") or {}) if isinstance(row.get("fields"), dict) else {}).get(f)
            v, ok = to_business_value(f, v, fmap)
            if not ok:
                unmapped.append("%s=%s" % (f, v))
            cells.append("" if v is None else str(v))
        body.append("| " + " | ".join(cells) + " |")
    return "\n".join(head + body), unmapped[:20]


def to_tool_items(items: list, fmap) -> tuple:
    """业务口径条目 → 工具口径（写入前）。→ `(工具条目[], unmapped[])`

    - 键：业务列名（label）→ 工具字段；已是工具字段则保持；
    - 值：业务枚举 → 工具枚举；未命中 ⇒ 原样透传并计入 `unmapped`。
    """
    m = parse(fmap)
    by_label = {v: k for k, v in m["labels"].items() if v}
    out, unmapped = [], []
    for it in items or []:
        if not isinstance(it, dict):
            continue
        new = {}
        for k, v in it.items():
            field = k if k in m["labels"] else by_label.get(k, k)
            if isinstance(v, dict):                      # 嵌套（如 fields）
                new[k] = v
                continue
            val, ok = to_tool_value(field, v, fmap)
            if not ok:
                unmapped.append("%s=%s" % (field, v))
            new[field] = val
        out.append(new)
    return out, unmapped[:20]

