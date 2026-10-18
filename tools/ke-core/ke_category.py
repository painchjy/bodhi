"""`category_path` 重算（目录收敛 ✓）

背景（2026-10-17 用户口径 ✓）：
- 目录 = 页面 `category_path` 的 distinct 树 ✓（`sync_folders.py` 只建树+挂页 ✓，**不重算** ✗）；
- 历史页因 `category_path()` 回落类名 ✗ 存成了 `["bpmn:Task","bpmn:Task"]` ✗ ⇒ 目录里出现带冒号的一级目录 ✗；
- 所以"刷新目录"必须带一步**重算** ✓，否则坏值永远存在 ✗。

重算来源（按优先级 ✓）：
1. `ke_ontology.category_path(page_type)` ✓（bmm 有类元数据 ✓）；
2. 本体库页 `ontology/<module>/<class>` 的标题 ✓；
3. **TTL** 的 `:Class rdfs:label "…"@zh` ＋ 模块中文名映射 ✓（bpmn/agent 常走这条 ✓）。
"""

import json
import re
from functools import lru_cache

TTL_FILES = {"bmm": "BMM完整版.ttl", "bpmn": "BPMN完整版.ttl", "agent": "AGENT完整版.ttl",
             "ea": "EA完整版.ttl", "agent_dev": "智能体开发完整版.ttl"}
_ROOT = "/mnt/c/Users/PHJY/source/bodhi2"


@lru_cache(maxsize=32)
def _ttl_short(module: str) -> str:
    """模块目录名 = TTL 里的 **`bodhi:shortName`** ✓（不是 rdfs:label ✗，也不是硬编码映射 ✗）。

    实测（2026-10-17）：
      · `<http://example.org/bmm>   bodhi:shortName "BMM业务动机模型"` ✓
      · `<http://example.org/bpmn>  bodhi:shortName "BPMN流程模型"` ✓  ← 注意：不是 rdfs:label 的
        "BPMN业务流程模型（按需子集）" ✗（两者并存会拆出两棵目录 ✗）
      · `<http://example.org/agent> bodhi:shortName "智能体开发本体"` ✓
    """
    import io
    import os
    import re
    name = TTL_FILES.get(module) or TTL_FILES.get(module.lower())
    if name:
        p = os.path.join(_ROOT, "ontology", name)
        if os.path.exists(p):
            txt = io.open(p, encoding="utf-8").read()
            m = re.search(r"<http://example\.org/%s>\s+bodhi:shortName\s+\"([^\"]+)\"" % re.escape(module),
                          txt)
            if m:
                return m.group(1).strip()
    # 兜底：扫全部 TTL（模块文件名与 module key 不一致时 ✓，如 agent 的开发本体）
    d = os.path.join(_ROOT, "ontology")
    try:
        for fn in os.listdir(d):
            if not fn.endswith(".ttl"):
                continue
            txt = io.open(os.path.join(d, fn), encoding="utf-8").read()
            m = re.search(r"<http://example\.org/%s>\s+bodhi:shortName\s+\"([^\"]+)\"" % re.escape(module),
                          txt)
            if m:
                return m.group(1).strip()
    except Exception:  # noqa: BLE001
        pass
    return ""


@lru_cache(maxsize=8)
def _ttl_labels(module: str) -> dict:
    """从 TTL 取 `类本地名 → 中文 label` ✓（轻量正则 ✓，只读 ✓）。"""
    out: dict = {}
    import io
    import os
    name = TTL_FILES.get(module)
    if not name:
        return out
    p = os.path.join(_ROOT, "ontology", name)
    if not os.path.exists(p):
        return out
    txt = io.open(p, encoding="utf-8").read()
    for m in re.finditer(r"^:([A-Za-z][\w]*)\s+rdf:type\s+owl:Class\s*;(.*?)(?=\n:[A-Za-z]|\Z)",
                         txt, re.S | re.M):
        kl, body = m.group(1), m.group(2)
        lab = re.search(r'rdfs:label\s+"([^"]+)"', body)
        if lab:
            out[kl.lower()] = lab.group(1).strip()
    return out


@lru_cache(maxsize=8)
def _page_labels() -> dict:
    """本体库页 `ontology/<module>/<class>` → 类中文名 ✓（去掉“（bpmn:Task）”后缀 ✓）。"""
    import ke_db
    out: dict = {}
    try:
        rows = ke_db.psql_csv(
            "SELECT slug, COALESCE(title,'') AS t FROM wiki_pages "
            " WHERE deleted_at IS NULL AND slug LIKE 'ontology/%/%' "
            "   AND COALESCE(page_type,'') = 'ontology:Class'")
    except Exception:  # noqa: BLE001
        return out
    for r in rows:
        parts = (r["slug"] or "").split("/")
        if len(parts) == 3:
            out[(parts[1].lower(), parts[2].lower())] = re.sub(r"（[^）]*）\s*$", "", r["t"] or "").strip()
    return out


def resolve_class(page_type: str) -> list:
    """类 → `[模块中文名, 类中文名]`（取不到 ⇒ `[]` ✓ 不回落类名 ✗）。"""
    import ke_ontology
    try:
        cp = ke_ontology.category_path(page_type) or []
    except Exception:  # noqa: BLE001
        cp = []
    if cp:
        return cp
    mod, _, klass = (page_type or "").partition(":")
    mod, klass = mod.strip().lower(), klass.strip()
    if not mod or not klass:
        return []
    lab = _page_labels().get((mod, klass.lower())) or _ttl_labels(mod).get(klass.lower())
    mlab = _ttl_short(mod)          # ← 目录一级名取 `bodhi:shortName` ✓（不再用硬编码映射 ✗）
    if not lab or not mlab:
        return []
    return [mlab, lab]


@lru_cache(maxsize=1)
def _all_shorts() -> frozenset:
    """所有模块的 `bodhi:shortName` 集合 ✓（扫 TTL ✓；用于识别"非标准一级目录名" ✗）。"""
    import io
    import os
    import re
    out = set()
    d = os.path.join(_ROOT, "ontology")
    try:
        for fn in os.listdir(d):
            if not fn.endswith(".ttl"):
                continue
            txt = io.open(os.path.join(d, fn), encoding="utf-8").read()
            for m in re.finditer(r"<http://example\.org/[\w-]+>\s+bodhi:shortName\s+\"([^\"]+)\"", txt):
                out.add(m.group(1).strip())
    except Exception:  # noqa: BLE001
        pass
    return out


def audit_short_names() -> dict:
    """**检查 2**：类元数据的 `module_short` 是否等于本体 `bodhi:shortName` ✓。

    `category_path()` 用的是 `class_meta()[..]["module_short"]` ✓；若编译产物里没有 shortName
    （只有长名 `rdfs:label` ✗），目录就会被拆成两棵 ✗ —— 本函数把这种偏差列出来 ✓。
    """
    import ke_ontology
    meta = ke_ontology.class_meta()
    bad, ok = [], 0
    for key, info in meta.items():
        mod = str(info.get("module") or "")
        want = _ttl_short(mod)
        got = str(info.get("module_short") or "")
        if not want:
            continue
        if want == got:
            ok += 1
        else:
            bad.append({"class": key, "module_short": got, "ttl_shortName": want})
    return {"ok": not bad, "checked": ok + len(bad), "matched": ok, "mismatched": bad[:30],
            "note": "mismatch ⇒ 编译产物缺 shortName ✗ ⇒ 目录会按长名拆树 ✗"}


def recompute(kb_id: str, only_bad: bool = True) -> dict:
    """重算页面的 `category_path/depth/wiki_path` ✓（默认只修"异常"页 ✓）。

    判为异常 ✓（任一成立）：
      ① `[K, K]` 两段相同且含冒号 ✗；② 空数组 ✓；③ **一级名不是任何模块的 `bodhi:shortName`** ✗
      （即历史上用长名 `rdfs:label` 当过目录名 ✗ —— 这正是"两棵目录"的来源 ✓）。
    """
    import ke_db
    std = _all_shorts()
    rows = ke_db.psql_csv(
        "SELECT slug, COALESCE(page_type,'') AS pt, COALESCE(category_path::text,'[]') AS cp "
        "  FROM wiki_pages WHERE knowledge_base_id = %s AND deleted_at IS NULL"
        % ke_db.sql_str(kb_id))
    fixed, skipped, empty = [], 0, 0
    for r in rows:
        try:
            cp = json.loads(r["cp"] or "[]")
        except Exception:  # noqa: BLE001
            cp = []
        new = resolve_class(r["pt"])
        bad = ((len(cp) >= 2 and cp[0] == cp[1] and ":" in str(cp[0]))
               or not cp
               or (bool(cp) and bool(new) and std and str(cp[0]) not in std))
        if only_bad and not bad:
            continue
        if not new:
            skipped += 1
            if not cp:
                empty += 1
            continue
        if new == cp:
            continue
        title = str(r["slug"] or "").rsplit("/", 1)[-1]
        ke_db.psql(
            "UPDATE wiki_pages SET category_path = %s::jsonb, depth = %d, wiki_path = %s "
            " WHERE knowledge_base_id = %s AND slug = %s;"
            % (ke_db.sql_json(new), len(new), ke_db.sql_str("/".join(new + [title])),
               ke_db.sql_str(kb_id), ke_db.sql_str(r["slug"])), stdin=True)
        fixed.append({"slug": r["slug"], "from": cp, "to": new})
    return {"ok": True, "kb_id": kb_id, "scanned": len(rows), "fixed": len(fixed),
            "unresolved": skipped, "was_empty": empty, "items": fixed[:20]}
