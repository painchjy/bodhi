"""ke-core · **外部系统接口的参考实现：本地容器存储层**（零第三方依赖）

定位（2026-10-25 用户口径）
--------------------------
这三个 `ext_*` 工具是**给内网存量系统接口打样的参考实现**：本文件用"容器文件"模拟
外部系统的库表读写；内网落地时**只把这一层换成自己的库表访问**（SQL/ORM），
上层的绑定解析、两种模式、幂等/审计/分页口径可原样复用 ✓。

为什么单独一层
--------------
三个模拟工具（清单查询 / 条目维护 / 文档与附件访问）都要"读写容器文件"，
若各写一套，**原子性 / 并发 / 审计 / 幂等**四个口径必然漂移。这里统一：

    load_binding()      读某 DataObject 页的 `bmm:sourceLocator` → 解析成绑定
    parse_locator()     `mcp:<tool>?mode=query,maintain&key=k1,k2&stub=<slug>`
    read_json() / write_json()   原子写（临时文件 + `os.replace`）
    locked()            `fcntl.flock` 文件锁（拿不到 ⇒ 直接报 busy，不阻塞）
    audit()             写操作审计（append-only jsonl，一行一条）
    shard_path()        条目按主键分片（少锁竞争、单文件可小到几十行）

口径（与仓库既有习惯一致）
- **写默认 `dry_run=True`**：只回 diff，不落盘；
- 幂等键来自绑定（`key=`），同键重复写 = 更新而非新增；
- 代码里**不出现任何业务名词**（业务取值只在 DataObject 属性与容器数据里）。
"""
from __future__ import annotations

import contextlib
import fcntl
import json
import os
import re
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

DEFAULT_SYSTEM = "it-platform"


# ---------------------------------------------------------------------------
# 目录与原子写
# ---------------------------------------------------------------------------
def ext_root() -> str:
    """容器根目录：`BODHI_EXT_ROOT`（默认 `<repo>/state/ext`）。"""
    return os.environ.get("BODHI_EXT_ROOT") or os.path.join(REPO, "state", "ext")


def system_dir(system: str = DEFAULT_SYSTEM) -> str:
    d = os.path.join(ext_root(), system or DEFAULT_SYSTEM)
    os.makedirs(d, exist_ok=True)
    return d


def read_json(path: str, default=None):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:  # noqa: BLE001  文件不存在/坏 JSON ⇒ 用默认值（调用方自己判）
        return default


def write_json(path: str, data) -> dict:
    """原子写：同目录临时文件 → `os.replace`（读者永远看不到半截文件 ✓）。"""
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = "%s.tmp.%d" % (path, os.getpid())
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=1)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)
    return {"path": path, "bytes": os.path.getsize(path)}


@contextlib.contextmanager
def locked(path: str, timeout: float = 3.0):
    """对 `<path>.lock` 加排它锁；超时 ⇒ `RuntimeError("busy")`（调用方照实回执）。"""
    lock_path = path + ".lock"
    os.makedirs(os.path.dirname(lock_path) or ".", exist_ok=True)
    fh = open(lock_path, "a+")
    deadline = time.time() + max(0.1, float(timeout))
    while True:
        try:
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            break
        except BlockingIOError:
            if time.time() >= deadline:
                fh.close()
                raise RuntimeError("busy: 容器文件被占用（请重试）")
            time.sleep(0.05)
    try:
        yield
    finally:
        try:
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
        finally:
            fh.close()


def shard_path(name: str, key: str, subdir: str = "") -> str:
    """按主键分片：`<root>/<system>/<subdir>/<安全名>.json`（主键为空 ⇒ `_all`）。"""
    safe = re.sub(r"[^\w\-.\u4e00-\u9fff]+", "_", str(key or "")).strip("_") or "_all"
    base = system_dir()
    if subdir:
        base = os.path.join(base, subdir)
        os.makedirs(base, exist_ok=True)
    return os.path.join(base, safe + ".json")


def public_base(default: str = "http://127.0.0.1:8770") -> str:
    """本服务的**对外基地址**（附件下载 URL 用它拼；env 可覆盖，便于内网域名/IP 切换）。"""
    for k in ("IT_MCP_PUBLIC_BASE", "BODHI_EXT_PUBLIC_BASE"):
        v = (os.environ.get(k) or "").strip().rstrip("/")
        if v:
            return v
    return default


# ---------------------------------------------------------------------------
# 参考规范**抓取留痕**（2026-10-26）：`reference_lookup` 写、巡检 G6 读
# ---------------------------------------------------------------------------
def ref_cache_path() -> str:
    """留痕文件（`<root>/ref_cache.json`）：`{rev, urls: {url: {found, reason, at, …}}}`。"""
    os.makedirs(ext_root(), exist_ok=True)
    return os.path.join(ext_root(), "ref_cache.json")


def ref_cache_load() -> dict:
    d = read_json(ref_cache_path(), {}) or {}
    return d.get("urls") if isinstance(d.get("urls"), dict) else {}


def ref_cache_put(url: str, entry: dict, keep: int = 300) -> dict:
    """记一条抓取结果（**同 URL 覆盖**，只留最近 `keep` 条）。失败不当业务失败 ✓。"""
    url = str(url or "").strip()
    if not url:
        return {"ok": False, "error": "need_url"}
    path = ref_cache_path()
    try:
        with locked(path):
            cur = read_json(path, {}) or {}
            urls = cur.get("urls") if isinstance(cur.get("urls"), dict) else {}
            urls[url] = dict(entry or {}, url=url, at=time.strftime("%Y-%m-%d %H:%M:%S"))
            if len(urls) > int(keep):
                for k in list(urls)[:len(urls) - int(keep)]:
                    urls.pop(k, None)
            write_json(path, {"note": "参考规范抓取留痕（reference_lookup 写 / 巡检 G6 读）",
                              "rev": int(cur.get("rev") or 0) + 1, "urls": urls})
        return {"ok": True, "path": path, "urls": len(urls)}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": repr(exc)[:200]}


def ledger_names(system: str = DEFAULT_SYSTEM, subdir: str = "issues") -> list:
    """台账分片名（= 外层主键值）：`<root>/<system>/<subdir>/*.json` 的文件名去后缀。"""
    d = os.path.join(system_dir(system), subdir)
    if not os.path.isdir(d):
        return []
    return sorted(os.path.splitext(f)[0] for f in os.listdir(d)
                  if f.endswith(".json") and not f.startswith("_"))


def audit(action: str, payload: dict, system: str = DEFAULT_SYSTEM) -> str:
    """写操作审计（append-only）：**只在真正落盘时写** ✓"""
    path = os.path.join(system_dir(system), "audit.jsonl")
    rec = {"ts": time.strftime("%Y-%m-%d %H:%M:%S"), "action": action,
           "actor": str((payload or {}).get("actor") or ""),
           "session_no": str((payload or {}).get("session_no") or ""),
           "tool": str((payload or {}).get("tool") or ""),
           "key": str((payload or {}).get("key") or ""),
           "dry_run": bool((payload or {}).get("dry_run")),
           "before": (payload or {}).get("before"),
           "after": (payload or {}).get("after")}
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return path


# ---------------------------------------------------------------------------
# 绑定：**业务对象的数据属性**（2026-10-25 起）
#   · `bpmn:dataObjectInterface`     接口工具（多个用逗号分隔）
#   · `bpmn:dataObjectAccessMode`    每接口模式（`;` 分隔，与接口顺序对齐）
#   · `bpmn:dataObjectKeyFields`     每接口主键（`;` 分隔，顺序对齐）
#   · `bpmn:dataObjectInterfaceRef`  接口提供方（挡板页 slug；`;` 分隔）
# **不再借用** `bmm:sourceLocator`（那是落库内核的"来源定位"，语义不同 ✗）
# ---------------------------------------------------------------------------
BINDING_ATTRS = ("dataObjectInterface", "dataObjectAccessMode",
                 "dataObjectKeyFields", "dataObjectInterfaceRef")


def _split(text: str, sep: str) -> list:
    return [x.strip() for x in str(text or "").split(sep) if str(x).strip()]


def parse_binding(attrs: dict) -> list:
    """4 个数据属性 → `[{tool, modes, keys, stub}, …]`（按接口顺序对齐）。"""
    attrs = attrs or {}
    tools = _split(attrs.get("dataObjectInterface"), ",")
    modes = str(attrs.get("dataObjectAccessMode") or "").split(";")
    keys = str(attrs.get("dataObjectKeyFields") or "").split(";")
    refs = str(attrs.get("dataObjectInterfaceRef") or "").split(";")

    def _at(arr, i, default=""):
        v = arr[i].strip() if i < len(arr) else ""
        return v or default

    out = []
    for i, t in enumerate(tools):
        out.append({"tool": t,
                    "modes": _split(_at(modes, i, "query"), ","),
                    "keys": _split(_at(keys, i, ""), ","),
                    "impl": _at(refs, i, "")})
    return out


def _dataobject_types() -> list:
    try:
        import ke_ontology  # noqa: PLC0415
        out = ke_ontology.descendants("bpmn:DataObject") or []
        return sorted({str(x) for x in out if x}) or ["bpmn:DataObject"]
    except Exception:  # noqa: BLE001
        return ["bpmn:DataObject"]


def _kbs_with_slug(slug: str, exclude: str = "") -> list:
    """哪些库里有这个 slug（2026-10-25：绑定可能不在调用方传的那个库里）。"""
    try:
        import ke_db  # noqa: PLC0415
        rows = ke_db.psql_csv(
            "SELECT DISTINCT knowledge_base_id FROM wiki_pages WHERE slug = %s AND deleted_at IS NULL"
            % ke_db.sql_str(slug))
        return [str(r["knowledge_base_id"]) for r in rows
                if r.get("knowledge_base_id") and str(r["knowledge_base_id"]) != exclude]
    except Exception:  # noqa: BLE001
        return []


def _attrs_of(kb_id: str, slug: str) -> dict:
    """读 4 个绑定属性（图节点属性优先，回退 PG 元数据；键可带/不带前缀）。"""
    blank = {a: "" for a in BINDING_ATTRS}
    try:
        import ke_neo4j  # noqa: PLC0415
        rows = ke_neo4j.query(
            "MATCH (n:BodhiInstance {kb_id:$kb, slug:$s}) "
            "RETURN coalesce(n.dataObjectInterface,'') AS i, "
            "       coalesce(n.dataObjectAccessMode,'') AS m, "
            "       coalesce(n.dataObjectKeyFields,'') AS k, "
            "       coalesce(n.dataObjectInterfaceRef,'') AS r",
            {"kb": kb_id, "s": slug}, timeout=8) or []
        if rows:
            r0 = rows[0]
            got = {"dataObjectInterface": str(r0.get("i") or ""),
                   "dataObjectAccessMode": str(r0.get("m") or ""),
                   "dataObjectKeyFields": str(r0.get("k") or ""),
                   "dataObjectInterfaceRef": str(r0.get("r") or "")}
            if any(v.strip() for v in got.values()):
                return got
    except Exception:  # noqa: BLE001  图不可用 ⇒ 回 PG
        pass
    try:
        import ke_db  # noqa: PLC0415
        sel = ", ".join(
            "COALESCE(page_metadata->'ontology'->'attributes'->>'bpmn:%s','')" % a
            for a in BINDING_ATTRS)
        rows = ke_db.psql_csv(
            "SELECT %s FROM wiki_pages WHERE knowledge_base_id = %s AND slug = %s "
            " AND deleted_at IS NULL LIMIT 1"
            % (sel, ke_db.sql_str(kb_id), ke_db.sql_str(slug)))
        if rows:
            vals = list(rows[0].values())
            return {a: str(vals[i] or "") for i, a in enumerate(BINDING_ATTRS)}
    except Exception:  # noqa: BLE001
        pass
    return blank


def _objects_by_tool(kb_id: str, tool: str) -> list:
    """反查：哪些业务对象页声明的接口 = 本工具（`object` 省略时用）。

    2026-10-25：`kb_id` 为空（或本库查不到）⇒ **全库兜底**（调用方常传"工作库"，
    而绑定写在领域库）→ 每条候选带 `kb_id`，调用方据此回填 ✓。
    """
    needle = str(tool or "")
    out = []
    try:
        import ke_neo4j  # noqa: PLC0415
        rows = ke_neo4j.query(
            "MATCH (n:BodhiInstance) WHERE ($kb = '' OR n.kb_id = $kb) AND n.page_type IN $types "
            "  AND coalesce(n.dataObjectInterface,'') CONTAINS $q "
            "RETURN n.slug AS slug, coalesce(n.name,'') AS name, coalesce(n.kb_id,'') AS kb "
            "ORDER BY name", {"kb": kb_id or "", "types": _dataobject_types(), "q": needle},
            timeout=8) or []
        out = [{"slug": str(r.get("slug")), "name": str(r.get("name") or ""),
                "kb_id": str(r.get("kb") or "")} for r in rows]
    except Exception:  # noqa: BLE001  图不可用 ⇒ 回 PG
        try:
            import ke_db  # noqa: PLC0415
            where_kb = (" AND knowledge_base_id = %s" % ke_db.sql_str(kb_id)) if kb_id else ""
            rows = ke_db.psql_csv(
                "SELECT slug, COALESCE(title,'') AS name, knowledge_base_id AS kb "
                "  FROM wiki_pages WHERE deleted_at IS NULL AND page_type IN (%s) %s "
                "   AND page_metadata::text LIKE %s ORDER BY title LIMIT 10"
                % (", ".join(ke_db.sql_str(t) for t in _dataobject_types()), where_kb,
                   ke_db.sql_str("%" + needle + "%")))
            out = [{"slug": str(r["slug"]), "name": str(r.get("name") or ""),
                    "kb_id": str(r.get("kb") or "")} for r in rows]
        except Exception:  # noqa: BLE001
            out = []
    return [x for x in out if x.get("slug")]


def resolve_binding(tool: str, kb_id: str = "", object_slug: str = "") -> dict:
    """→ `{ok, binding, object, candidates, why}`（**不抛**；入口据此给可读回执）。"""
    tool = str(tool or "").strip()
    try:                                     # 首次使用：把种子数据补进容器根（幂等 ✓）
        bootstrap()
    except Exception:  # noqa: BLE001  种子复制失败不影响"能不能用"
        pass
    slug = str(object_slug or "").strip()
    # 2026-10-25：`kb_id` 可省 —— 只给 slug 也能反查；`kb_id + slug` 都给了但本库查不到绑定时
    # 会跨库兜底（智能体常传"工作库"，而绑定写在领域库 ⇒ 不能直接判 no_binding ✗）
    if not kb_id and slug:
        cands_kb = _kbs_with_slug(slug)
        if len(cands_kb) == 1:
            kb_id = cands_kb[0]
    if not slug:
        cands = _objects_by_tool(kb_id, tool)
        if len(cands) == 1:
            slug = cands[0]["slug"]
            kb_id = cands[0].get("kb_id") or kb_id
        elif not cands:
            return {"ok": False, "why": "no_binding", "candidates": [],
                    "hint": ("没有业务对象声明接口 %r（先在对象页写 "
                             "`bpmn:dataObjectInterface` 等 4 个数据属性）" % tool)}
        else:
            return {"ok": False, "why": "need_object", "candidates": cands[:10],
                    "hint": "多个对象都声明了本接口 ⇒ 请用 object=<slug> 指定"}
    if not kb_id:
        return {"ok": False, "why": "need_kb", "candidates": [],
                "hint": "需要 kb_id 或 object（业务对象页 slug）；绑定在哪个库都能解析"}
    searched = [kb_id]
    binds = parse_binding(_attrs_of(kb_id, slug))
    binding_kb = kb_id
    if not binds:
        for other in _kbs_with_slug(slug, exclude=kb_id):
            searched.append(other)
            b2 = parse_binding(_attrs_of(other, slug))
            if b2:
                binds, binding_kb = b2, other
                break
    if not binds:
        return {"ok": False, "why": "no_binding", "object": {"slug": slug},
                "searched_kbs": searched[:5], "candidates": [],
                "hint": ("该页没有声明接口（`bpmn:dataObjectInterface` 为空）；"
                         "多个接口用逗号分隔，模式/主键用分号按序对齐")}
    hit = [b for b in binds if b.get("tool") == tool]
    if not hit:
        return {"ok": False, "why": "tool_mismatch", "object": {"slug": slug},
                "binding": binds[0], "candidates": [],
                "available_tools": [b.get("tool") for b in binds],
                "hint": "该页声明的接口是 %r，不含 %r"
                        % ([b.get("tool") for b in binds], tool)}
    return {"ok": True, "binding": hit[0], "object": {"slug": slug},
            "binding_kb": binding_kb, "binding_count": len(binds)}


# ---------------------------------------------------------------------------
# 种子数据：随交付走 `deploy/mock-ext/<system>/`，首次使用时**只补缺失文件** ✓
# （`state/` 在 .gitignore 里 ⇒ 种子不能放那儿，否则交付包里没有）
# ---------------------------------------------------------------------------
SEED_DIR = os.path.join(REPO, "deploy", "mock-ext")


def bootstrap(system: str = DEFAULT_SYSTEM) -> dict:
    """把种子数据复制到容器根（幂等；**绝不覆盖**已存在的文件）。"""
    copied = []
    src = os.path.join(SEED_DIR, system or DEFAULT_SYSTEM)
    dst = system_dir(system)
    if not os.path.isdir(src):
        return {"copied": copied, "dir": dst, "seed": src}
    for root, _dirs, files in os.walk(src):
        rel = os.path.relpath(root, src)
        out_dir = dst if rel == "." else os.path.join(dst, rel)
        os.makedirs(out_dir, exist_ok=True)
        for name in files:
            s, d = os.path.join(root, name), os.path.join(out_dir, name)
            if os.path.isfile(s) and not os.path.exists(d):
                try:
                    import shutil  # noqa: PLC0415
                    shutil.copy2(s, d)
                    copied.append(os.path.join(rel, name) if rel != "." else name)
                except Exception:  # noqa: BLE001  复制失败不影响主流程
                    pass
    return {"copied": copied, "dir": dst, "seed": src}
