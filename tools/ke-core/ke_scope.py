"""会话**库范围**（domain_kb / work_kb）—— 权威来自**智能体配置**，不靠智能体自报。

为什么要它（2026-11-02 内网实测）
---------------------------------
智能体反复把 `b1` 当 kb_id 传；解析失败时旧的归一化逻辑把「清单第一个库」放进 `retry_args`
⇒ 模型照抄 ⇒ 拿一个没有任何领域信息的库去查，**必然失败**。
用户口径：会话开始就确定「领域库（只读）/ 工作库（读写）」，全程不许换；智能体不遵守就纠偏；
取不到就让用户找管理员改提示词。

权威链（不新增工具、不需要智能体确认）
--------------------------------------
    session_no → sessions.agent_config.agent_id → custom_agents.config
                   ├─ knowledge_bases（uuid[]）   # 机器口径
                   └─ system_prompt（文本）        # 角色口径：哪个是领域库/工作库

三级容错
--------
1. 提示词机器标记（首选，管理员写一行）：
       【库范围】domain_kb: 技术方案评审领域模型 | work_kb: 技术方案评审任务知识库
2. 中文兜底：`领域库（只读）：X` / `工作库（读写）：X`（尽力解析；解析不出就报错，不猜）
3. `config.knowledge_bases` 交叉校验（名字解析到的 id 必须在挂载的 uuid 里）

返回
----
`{ok, session_no, agent_id, agent_name, domain_kb:{id,name}, work_kb:{id,name}, work_multi,
  source, error, hint}`；`error` ∈ need_session / scope_unresolved / scope_invalid /
scope_conflict / scope_changed。

「锁库」= 会话首次解析落快照（`state/session_scope/<sid>.json`）；之后重算**不同**时
`scope_changed`：**沿用快照** + 提示「配置已变更，请新开会话」，不静默切换 ✓。
"""
from __future__ import annotations

import json
import os
import re
import time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
SCOPE_DIR = os.path.join(REPO, "state", "session_scope")

_TTL = 60.0
_CACHE: dict = {}          # session_no -> (ts, scope)

_TAG_RE = re.compile(r"【库范围】\s*([^\n]*)")
_DOMAIN_TAG = re.compile(r"(?:domain_kb)\s*[:：=]\s*([^|｜;\n]+)")
_WORK_TAG = re.compile(r"(?:work_kb)\s*[:：=]\s*([^|｜;\n]+)")
_MULTI = ("所有领域库", "全部领域库", "各领域库", "按目标库", "本轮目标库")
_ROLE_WORDS = re.compile(r"[（(](?:只读|读写|可选|说明|含[^）)]*)[）)]\s*$")


def _clean(name: str) -> str:
    """去掉名字尾巴上的说明（`—— 说明` / `（含…）` / `（只读）`），保留 `（测试）` 这类名字成分。"""
    s = str(name or "").strip().strip("：:;；,，。").strip()
    s = re.split(r"——|--|（含|\(含", s, maxsplit=1)[0].strip()
    s = s.replace("**", "").strip()
    return _ROLE_WORDS.sub("", s).strip()


def parse_scope_text(text: str) -> dict:
    """提示词 → `{domain, work, source}`。**只认机器标记**（2026-11-03 用户口径）：

        【库范围】domain_kb: <库名或uuid> | work_kb: <库名或uuid>

    不再支持中文兜底（`领域库（只读）：X`）—— 两套格式只会互相干扰，缺标记就是配置错误。
    """
    s = str(text or "")
    m = _TAG_RE.search(s)
    if not m:
        return {"domain": "", "work": "", "source": ""}
    line = m.group(1)
    d, w = _DOMAIN_TAG.search(line), _WORK_TAG.search(line)
    return {"domain": _clean(d.group(1)) if d else "",
            "work": _clean(w.group(1)) if w else "",
            "source": "tag" if (d or w) else ""}


def _kb_of(name: str) -> dict:
    """库名/id → `{id,name}`；解析不到 → `{}`（不猜 ✗）。"""
    import ke_db  # noqa: PLC0415

    if not str(name or "").strip():
        return {}
    try:
        kb, kb_name, _note = ke_db.resolve_kb_id(name)
    except Exception:  # noqa: BLE001
        return {}
    return {"id": kb, "name": kb_name}


def _snapshot_path(sid: str) -> str:
    return os.path.join(SCOPE_DIR, "%s.json" % sid)


def _read_snapshot(sid: str) -> dict:
    try:
        with open(_snapshot_path(sid), encoding="utf-8") as fh:
            return json.load(fh) or {}
    except Exception:  # noqa: BLE001
        return {}


def _write_snapshot(sid: str, scope: dict) -> None:
    try:
        os.makedirs(SCOPE_DIR, exist_ok=True)
        with open(_snapshot_path(sid), "w", encoding="utf-8") as fh:
            json.dump(scope, fh, ensure_ascii=False, indent=1)
    except Exception:  # noqa: BLE001  快照只是防漂移，写不了不影响主流程
        pass


def _unresolved(sid: str, agent_name: str, parsed: dict, why: str) -> dict:
    return {"ok": False, "session_no": sid, "agent_name": agent_name,
            "domain_kb": {}, "work_kb": {}, "parsed": parsed, "error": "scope_unresolved",
            "hint": ("智能体「%s」的提示词里没有可解析的库范围（%s）⇒ 让管理员在提示词加一行："
                     "`【库范围】domain_kb: <领域库名> | work_kb: <工作库名>`"
                     "（或在智能体配置的 knowledge_bases 里挂好两个库）" % (agent_name or "?", why))}


def agent_scope(session_no: str, use_cache: bool = True) -> dict:
    """会话 → 智能体配置 → 库范围（60s 缓存 + 会话快照防漂移）。"""
    sid = str(session_no or "").strip()
    if not sid:
        return {"ok": False, "session_no": "", "domain_kb": {}, "work_kb": {},
                "error": "need_session",
                "hint": "缺少会话编号（session_no）：库范围由**会话绑定的智能体**决定，"
                        "请把 WeKnora 会话 uuid 传入"}
    now = time.time()
    hit = _CACHE.get(sid)
    if use_cache and hit and now - hit[0] < _TTL:
        return hit[1]

    import ke_db  # noqa: PLC0415

    try:
        rows = ke_db.psql_csv(
            "SELECT id, tenant_id, COALESCE(agent_config->>'agent_id','') AS agent_id, "
            "  COALESCE(title,'') AS title FROM sessions "
            "  WHERE id = %s AND deleted_at IS NULL LIMIT 1" % ke_db.sql_str(sid))
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "session_no": sid, "domain_kb": {}, "work_kb": {},
                "error": "need_session", "hint": "查会话失败：%s" % exc}
    if not rows:
        return {"ok": False, "session_no": sid, "domain_kb": {}, "work_kb": {},
                "error": "need_session",
                "hint": "会话编号在 WeKnora 里不存在：%s（请用真实会话 uuid）" % sid}
    agent_id = str(rows[0].get("agent_id") or "").strip()
    if not agent_id:
        return {"ok": False, "session_no": sid, "domain_kb": {}, "work_kb": {},
                "error": "scope_unresolved",
                "hint": "该会话未绑定智能体（sessions.agent_config.agent_id 为空）⇒ 会话开始先选智能体"}
    try:
        ar = ke_db.psql_csv(
            "SELECT name, COALESCE(config::text,'{}') AS cfg FROM custom_agents "
            " WHERE id = %s LIMIT 1" % ke_db.sql_str(agent_id))
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "session_no": sid, "agent_id": agent_id,
                "domain_kb": {}, "work_kb": {},
                "error": "scope_unresolved", "hint": "查智能体配置失败：%s" % exc}
    if not ar:
        return {"ok": False, "session_no": sid, "agent_id": agent_id,
                "domain_kb": {}, "work_kb": {},
                "error": "scope_unresolved",
                "hint": "会话绑定的智能体 %s 在 custom_agents 里不存在" % agent_id}
    agent_name = str(ar[0].get("name") or "").strip()
    try:
        cfg = json.loads(ar[0].get("cfg") or "{}")
    except Exception:  # noqa: BLE001
        cfg = {}
    cfg = cfg if isinstance(cfg, dict) else {}
    parsed = parse_scope_text(cfg.get("system_prompt") or "")
    kbs = [str(x) for x in (cfg.get("knowledge_bases") or []) if str(x).strip()]
    if not parsed.get("domain") and not parsed.get("work") and not kbs:
        return _unresolved(sid, agent_name, parsed, "没有【库范围】标记，也没有 knowledge_bases")

    dom = _kb_of(parsed.get("domain")) if parsed.get("domain") else {}
    work_multi = bool(parsed.get("work") in _MULTI)
    work = _kb_of(parsed.get("work")) if parsed.get("work") and not work_multi else {}
    if parsed.get("domain") and not dom:
        # 用户口径（2026-11-03）：**domain_kb 不存在 ⇒ 不能用用户指定的库**（fail-closed）
        return {"ok": False, "session_no": sid, "agent_id": agent_id, "agent_name": agent_name,
                "domain_kb": {}, "work_kb": {}, "parsed": parsed, "error": "scope_invalid",
                "hint": ("提示词写的领域库「%s」在系统里不存在 ⇒ 让管理员核对库名/uuid；"
                         "**不允许**用调用方传的 kb_id 顶替领域库" % parsed.get("domain"))}
    # work_kb 不存在：**允许用户指定**（写路径），标 work_missing 由调用方决定
    work_missing = bool(parsed.get("work") and not work_multi and not work)
    if not dom and work:
        dom = work                                    # 只挂一个库的智能体：它就是领域库
    method = {}
    try:                                              # 方法层库（任务页/步骤所在，**只读**）
        mn = (ke_db.env_value("BODHI_METHOD_KB")
              or ke_db.env_value("BODHI_METHOD_KB_NAME")
              or "企业知识管理领域").strip()
        method = _kb_of(mn) if mn else {}
    except Exception:  # noqa: BLE001
        method = {}
    if kbs:                                           # 交叉校验（名字 vs 挂载 uuid）
        allowed = {k.lower() for k in kbs}
        for label, kb in (("领域库", dom), ("工作库", work)):
            if kb and kb["id"].lower() not in allowed:
                return {"ok": False, "session_no": sid, "agent_id": agent_id,
                        "agent_name": agent_name, "domain_kb": dom, "work_kb": work,
                        "parsed": parsed, "error": "scope_conflict",
                        "hint": "%s「%s」不在智能体配置的 knowledge_bases 里（%s）⇒ 让管理员统一配置"
                                % (label, kb["name"], "、".join(kbs))}

    scope = {"ok": True, "session_no": sid, "agent_id": agent_id, "agent_name": agent_name,
             "domain_kb": dom, "work_kb": work, "method_kb": method, "work_multi": work_multi,
             "work_missing": work_missing,
             "source": parsed.get("source") or "knowledge_bases", "error": "", "hint": ""}
    # 2026-11-03：**配置改了立即生效**（不再沿用旧快照锁库）；快照只用于提示"变更过"
    snap = _read_snapshot(sid)
    if snap.get("ok"):
        _old_dom = (snap.get("domain_kb") or {}).get("id") or ""
        _old_work = (snap.get("work_kb") or {}).get("id") or ""
        if _old_dom != (dom.get("id") or "") or _old_work != (work.get("id") or ""):
            scope["scope_changed"] = True
            scope["hint"] = ("智能体「%s」的库范围已变更（旧：领域库 %s／工作库 %s）⇒ **已按新配置生效**；"
                             "建议新开会话避免上下文混淆"
                             % (agent_name,
                                (snap.get("domain_kb") or {}).get("name") or "-",
                                (snap.get("work_kb") or {}).get("name") or "-"))
            scope["snapshot_at"] = ke_db.now_text()
            _write_snapshot(sid, scope)
        else:
            scope["snapshot_at"] = snap.get("snapshot_at")
            scope["method_kb"] = method or snap.get("method_kb") or {}
    else:
        scope["snapshot_at"] = ke_db.now_text()
        _write_snapshot(sid, scope)
    _CACHE[sid] = (now, scope)
    return scope


def work_multi(scope: dict) -> bool:
    """该智能体是否「工作库=所有领域库」（建模类：写库在会话内首次锁定）。"""
    return bool((scope or {}).get("work_multi"))


def check_kb(kb_id: str, session_no: str, role: str = "domain") -> tuple:
    """校验调用方给的 kb 是否落在会话范围内 → `(error|None, scope)`。

    - `out_of_scope`：给了别的库 ⇒ 回执正确 id（**不照它查、也不静默换库** ✗）
    - scope 不可用（need_session/unresolved/…）⇒ 原样返回，由调用方决定降级
    """
    scope = agent_scope(session_no) if session_no else {
        "ok": False, "error": "need_session", "hint": "缺少 session_no"}
    if not scope.get("ok"):
        return None, scope
    want = scope.get("domain_kb") if role == "domain" else scope.get("work_kb")
    if role != "domain" and work_multi(scope):
        return None, scope                            # 多工作库：写路径首次锁定（下游处理）
    if kb_id and want and str(kb_id) != str(want.get("id")):
        allowed = {str((scope.get("domain_kb") or {}).get("id") or ""),
                   str((scope.get("work_kb") or {}).get("id") or "")} - {""}
        if str(kb_id) not in allowed:
            return ({"error": "out_of_scope", "expected": want,
                     "scope": {"domain_kb": scope.get("domain_kb"),
                               "work_kb": scope.get("work_kb")},
                     "hint": "本会话的%s是「%s」；请用它（或省略 kb_id 由工具取）—— "
                             "不要传别的库 ✗"
                             % ("领域库" if role == "domain" else "工作库",
                                (want or {}).get("name") or (want or {}).get("id") or "-")},
                    scope)
    return None, scope


