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

# ===== P3 slice B: folder/category convergence moved from mcp server (verbatim) =====
import json  # noqa: E402
import ke_ontology  # noqa: E402
import ke_db  # noqa: E402

psql = ke_db.psql
psql_csv = ke_db.psql_csv
sql_json = ke_db.sql_json
sql_str = ke_db.sql_str
now_text = ke_db.now_text


def _resolve_kb_id(raw: str) -> tuple[str, str]:
    """(id, note) —— 与 server 侧同名 helper 同口径（ke_db 返回三元组）。"""
    uid, _name, note = ke_db.resolve_kb_id(raw)
    return uid, note


# 目录层级参数（2026-10-04 用户口径）：
#   第一层永远是**模块短名**；`depth` = 模块下再展开几层类（0=直接到本类；1=直接 Thing 子类；上限 5）；
#   **weknora 自带类型**（无 `:`）固定第一层 `weknora`、第二层是类型名（与 depth 无关）。
MAX_DIR_DEPTH = 5
WEKNORA_MODULE_SHORT = "weknora"
WEKNORA_TYPE_LABELS = {"summary": "摘要", "entity": "实体", "concept": "概念",
                       "synthesis": "综合", "comparison": "对比", "question": "问题",
                       "faq": "FAQ", "pipeline": "流水线", "chunk": "切片",
                       "image": "图片", "table": "表格"}


def _realign_wiki_paths(kb_id: str) -> dict:
    """对齐**派生列**：`depth = len(category_path)` 与 `wiki_path = 目录路径/标题`（**幂等**）。

    ⚠️ 为什么两列都要（2026-10-16 实测，用户 UI 报的「评估目录 9 页、列表只有 8 页」）：
    前端目录列表按 `category_path::jsonb = [...] AND depth = N` **精确匹配**（见 `TROUBLESHOOTING`
    §目录名过长 / §目录缺失）—— 只要 `depth` 与 `category_path` 层数不一致，**该页就不出现在目录里**
    （实测差的正是那 1 页 `depth=1` 而 `category_path` 是 2 层 ✗）；
    `wiki_path` 则是「目录路径/标题」的显示串（历史上有页被写成 slug → 展开取不到页 ✗）。
    只动这两列 —— **不改** `category_path` 本身、目录层级选择、正文、`slug`（那是 `recategorize` 的职责）。
    `category_path` 为空的页跳过。返回 `{"depths_fixed", "paths_fixed"}`（本次修复页数）。
    """
    out = {"depths_fixed": 0, "paths_fixed": 0}
    try:
        rows = psql_csv(
            "UPDATE wiki_pages "
            "   SET depth = jsonb_array_length(COALESCE(category_path,'[]'::jsonb)), updated_at = now() "
            " WHERE knowledge_base_id = %s AND deleted_at IS NULL "
            "   AND jsonb_array_length(COALESCE(category_path,'[]'::jsonb)) > 0 "
            "   AND COALESCE(depth,-1) <> jsonb_array_length(COALESCE(category_path,'[]'::jsonb)) "
            " RETURNING slug" % sql_str(kb_id))
        out["depths_fixed"] = len(rows)
    except Exception as exc:  # noqa: BLE001  修复失败不影响刷新目录本身
        print("[mcp] depth 对齐失败：%s" % exc)
        out["depths_fixed"] = -1
    try:
        rows = psql_csv(
            "WITH t AS ("
            "  SELECT slug, COALESCE(wiki_path,'') AS old_wp,"
            "         array_to_string(ARRAY(SELECT jsonb_array_elements_text(category_path))"
            "                         || ARRAY[COALESCE(title,'')], '/') AS new_wp"
            "    FROM wiki_pages"
            "   WHERE knowledge_base_id = %s AND deleted_at IS NULL"
            "     AND jsonb_array_length(COALESCE(category_path,'[]'::jsonb)) > 0) "
            "UPDATE wiki_pages p SET wiki_path = t.new_wp, updated_at = now()"
            "  FROM t WHERE p.knowledge_base_id = %s AND p.slug = t.slug AND t.old_wp <> t.new_wp"
            " RETURNING p.slug"
            % (sql_str(kb_id), sql_str(kb_id)))
        out["paths_fixed"] = len(rows)
    except Exception as exc:  # noqa: BLE001
        print("[mcp] wiki_path 对齐失败：%s" % exc)
        out["paths_fixed"] = -1
    return out


def refresh_folders(kb_id: str = "", force: bool = False) -> dict:
    kid = (kb_id or "").strip()
    if not kid:
        return {"ok": False, "reason": "缺少 kb_id"}
    kb_id, note = _resolve_kb_id(kid)
    # 2026-10-16（用户口径）：刷新目录**顺带把派生列对齐** —— `depth=len(category_path)` 与
    #   `wiki_path="目录路径/标题"`。存量漂移（如「评估」目录 depth=1 却有 2 层 → 页面不进列表 ✗）
    #   在这里一次修好，幂等；只看这两列，不动 category_path/层级/正文/slug。
    #   回执字段：`paths_fixed` / `depths_fixed`。
    fixed = _realign_wiki_paths(kb_id)
    row = psql_csv(
        "SELECT COALESCE(max(updated_at)::text,'') AS pages, "
        "COALESCE((SELECT max(updated_at)::text FROM wiki_folders WHERE knowledge_base_id = %s "
        "AND deleted_at IS NULL), '') AS folders "
        "FROM wiki_pages WHERE knowledge_base_id = %s AND deleted_at IS NULL"
        % (sql_str(kb_id), sql_str(kb_id)))
    pages_at = ((row[0].get("pages") if row else "") or "")
    folders_at = ((row[0].get("folders") if row else "") or "")
    out = {"ok": True, "kb_id": kb_id, "kb_note": note, "refreshed": False,
           "paths_fixed": fixed.get("paths_fixed"), "depths_fixed": fixed.get("depths_fixed"),
           "stale": bool(force or (pages_at > folders_at)),
           "max_page_updated_at": pages_at, "max_folder_updated_at": folders_at}
    if not out["stale"]:
        return out
    try:
        import sync_folders  # noqa: PLC0415  惰性导入：它 import server（避免环）
        out["statements"] = sync_folders.sync_kb(kb_id, dry_run=False, link_pages=True, prune=True)
        out["refreshed"] = True
    except Exception as exc:  # noqa: BLE001
        out.update({"ok": False, "reason": str(exc)[:300]})
        return out
    row2 = psql_csv("SELECT COALESCE(max(updated_at)::text,'') AS folders FROM wiki_folders "
                    "WHERE knowledge_base_id = %s AND deleted_at IS NULL" % sql_str(kb_id))
    out["max_folder_updated_at"] = ((row2[0].get("folders") if row2 else "") or "")
    return out


def _class_chain_labels(ptype: str, meta: dict[str, dict]) -> list[str]:
    """`[根, …, 本类]` 的中文名（**确定性**：沿 `parents[0]` 上溯再反转）。"""
    names, cur, seen = [], ptype, set()
    while cur and cur not in seen:
        seen.add(cur)
        names.append(cur)
        plist = (meta.get(cur) or {}).get("parents") or []
        cur = plist[0] if plist else ""
    names.reverse()
    return [(meta.get(n) or {}).get("label") or n for n in names]


def _category_path_for(ptype: str, depth: int, meta: dict[str, dict]) -> list[str]:
    """按**层级参数**生成目录路径（2026-10-04 用户口径）。

    - **第一层永远是模块短名**（`bodhi:shortName`）；
    - 本体实例类型（含 `:`）：`depth=0` → `[模块, 本类]`（第一层下直接展开**最底层的类**）；
      `depth=N≥1` → `[模块, 根, …, 第 N 层类]`（N=1 即"模块下 1 层 = 直接 Thing 的子类"，
      共展示 2 层；链不足 N 层时到本类为止）；N 上限 5；
    - **weknora 自带类型**（无 `:`：`concept`/`entity`/`summary`…）：固定 `[weknora, 类型中文名]`
      —— **与 depth 无关**（用户口径："weknora 也算一个模块放第一层，下面的类型放第二层"）。
    """
    info = meta.get(ptype) or {}
    module = (info.get("module_short") or info.get("module_label")
              or info.get("module") or ptype)
    if ":" not in ptype:
        return [WEKNORA_MODULE_SHORT, WEKNORA_TYPE_LABELS.get(ptype, ptype)]
    chain = _class_chain_labels(ptype, meta)
    if int(depth) <= 0:
        tail = chain[-1:]
    else:
        tail = chain[:max(1, min(int(depth), MAX_DIR_DEPTH))]
    return [module] + tail


def recategorize(kb_id: str = "", dry_run: bool = True, depth: int = 1) -> dict:
    """**全量重刷目录（按钮/显式入口触发）**：按**当前本体 + 层级参数**重算该库所有页的
    `category_path` / `wiki_path`，再重建目录树（挂页 + prune 空目录）。

    层级参数 `depth`（**缺省 1**，范围 **0–5**；用户口径 2026-10-04）：
    `1` = 模块下 1 层（= 直接 Thing 的子类，界面共 2 层）；`0` = 第一层模块下直接展开最底层的类；
    `2–5` = 再往下细化到第 N 层类。**weknora 自带类型**（`concept`/`entity`/`summary`…）
    固定放 `weknora/<类型>`（与 `depth` 无关）。

    **本体模型库不支持**（它的目录是另一套 `ontology:*`，由本体投影维护）→ 直接拒绝。
    只动「本体实例页 + weknora 类型页」，跳过 `ontology:*` 与 `index`。
    """
    kid = (kb_id or "").strip()
    if not kid:
        return {"ok": False, "reason": "缺少 kb_id"}
    kb_id, note = _resolve_kb_id(kid)
    rep = ke_ontology.ontology_kb_report(kb_id)
    if (rep.get("asked") or {}).get("is_ontology_kb"):
        return {"ok": False, "kb_id": kb_id, "reason": "本体模型库的目录是另一套（ontology:* 由本体投影维护），"
                                                      "本功能（按本体重算目录）不适用"}
    depth = max(0, min(int(depth or 0), MAX_DIR_DEPTH))
    meta = ke_ontology.class_meta()
    try:
        rows = psql_csv("SELECT slug, title, page_type, COALESCE(category_path::text,'[]') AS cat "
                        "FROM wiki_pages WHERE knowledge_base_id = %s AND deleted_at IS NULL "
                        "AND COALESCE(page_type,'') NOT LIKE 'ontology:%%' AND page_type <> 'index'"
                        % sql_str(kb_id))
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "reason": str(exc)[:300]}
    changes, skipped = [], 0
    for row in rows:
        ptype = (row.get("page_type") or "").strip()
        if not ptype:
            skipped += 1
            continue
        if ":" in ptype and ptype not in meta:
            skipped += 1                      # 未知本体类型（本体里没有）→ 不动
            continue
        new_path = _category_path_for(ptype, depth, meta)
        new_wp = "/".join(new_path + [(row.get("title") or "").strip()])
        old_path = row.get("cat") or "[]"
        if old_path == json.dumps(new_path, ensure_ascii=False):
            continue
        changes.append({"slug": row["slug"], "title": row.get("title"), "page_type": ptype,
                        "old_path": json.loads(old_path or "[]"), "new_path": new_path,
                        "new_wiki_path": new_wp})
    out = {"ok": True, "kb_id": kb_id, "kb_note": note, "dry_run": bool(dry_run), "depth": depth,
           "scanned": len(rows), "skipped_unknown_type": skipped, "changes": changes,
           "changed": len(changes)}
    if dry_run or not changes:
        out["hint"] = ("dry_run：加 dry_run=false（或 HTTP `dry_run=0`）才写库；"
                       "写库后会自动重建该库目录树（挂页 + prune 空目录）")
        return out
    statements = []
    for ch in changes:
        statements.append(
            "UPDATE wiki_pages SET category_path = %s::jsonb, wiki_path = %s, updated_at = now() "
            "WHERE knowledge_base_id = %s AND slug = %s;"
            % (sql_json(ch["new_path"]), sql_str(ch["new_wiki_path"]), sql_str(kb_id), sql_str(ch["slug"])))
    psql("BEGIN;\n" + "\n".join(statements) + "\nCOMMIT;\n", stdin=True)
    out["folders"] = refresh_folders(kb_id, force=True)
    return out
