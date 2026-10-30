#!/usr/bin/env python3
"""智能体配置发布器（**幂等**）：`agent_tools.yaml` → `custom_agents.config`。

用户裁定（2026-10-26）：**不做 `/admin`**。工具面与提示词由**发布程序**写，写完由
`deploy/delivery/*/selfcheck.py` 校验；UI 上只保留"能改的项"（名字/库绑定/mcp 勾选）。

用法
----
    python3 tools/delivery/apply_config.py --check     # 只比对（默认），有漂移 ⇒ 退出码 1
    python3 tools/delivery/apply_config.py --apply     # 写入（无变化 ⇒ 一条 UPDATE 都不发）
    python3 tools/delivery/apply_config.py --apply --only <agent_id>

约定
----
- 真源：`deploy/weknora-fork/config/agent_tools.yaml`（内置工具 `builtin_tools` + 提示词文件 + `mcp_services`）；
- **MCP 工具不在这里配**（由 `mcp_services` 挂载决定，工具清单不落库）⇒ 别往 `allowed_tools` 写 `mcp_*`（写了也不生效）；
- `system_prompt` 从 md 里**第一个 ```text 代码块**取（md 顶部说明不算提示词）；
- 只写三键：`allowed_tools`（值来自 `builtin_tools`）/ `system_prompt` / `mcp_services`（其它键原样保留 ✓）；
- 每次写前打印 `before → after`（数量 + 增删项），写完**立刻回读复核**。
"""
import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
CONF = os.path.join(REPO, "deploy", "weknora-fork", "config")
sys.path.insert(0, os.path.join(REPO, "tools", "ke-core"))

import ke_db  # noqa: E402


def _yaml(path: str) -> dict:
    try:
        import yaml
    except ImportError:  # pragma: no cover
        raise SystemExit("缺 PyYAML：pip install pyyaml")
    with open(path, encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def _prompt_text(path: str) -> str:
    """取 md 里第一个 ```text 代码块（**唯一**来源，避免两份提示词）。"""
    with open(path, encoding="utf-8") as fh:
        lines = fh.read().split("\n")
    out, inside = [], False
    for ln in lines:
        if not inside and ln.strip().lower() in ("```text", "```txt"):
            inside = True
            continue
        if inside and ln.strip() == "```":
            break
        if inside:
            out.append(ln)
    return "\n".join(out).strip()


def _current(agent_id: str) -> dict:
    rows = ke_db.psql_csv(
        "SELECT config->>'system_prompt' AS prompt, config->'allowed_tools' AS tools, "
        "       config->'mcp_services' AS svcs, config->'knowledge_bases' AS kbs "
        "  FROM custom_agents WHERE id = %s" % ke_db.sql_str(agent_id))
    if not rows:
        return {}
    r = rows[0]
    for k in ("tools", "svcs", "kbs"):
        try:
            r[k] = json.loads(r[k] or "[]")
        except Exception:  # noqa: BLE001
            r[k] = []
    return r



def _find_by_name(name: str) -> str:
    """按**名字**找智能体 id（2026-10-30：多环境 id 不同 ⇒ 发布器要能按名定位）。"""
    name = (name or "").strip()
    if not name:
        return ""
    rows = ke_db.psql_csv(
        "SELECT id FROM custom_agents WHERE coalesce(config->>'name','') = %s "
        " OR name = %s ORDER BY updated_at DESC LIMIT 2"
        % (ke_db.sql_str(name), ke_db.sql_str(name)))
    ids = [str(r.get("id") or "") for r in rows if r.get("id")]
    return ids[0] if len(set(ids)) == 1 else ""


def _create_agent(name: str, want_prompt: str, svcs: list) -> str:
    """建一行 `custom_agents`（最小配置：名字 + 提示词 + MCP 服务），返回新 id。"""
    import uuid  # noqa: PLC0415
    aid = str(uuid.uuid4())
    cfg = {"name": name, "system_prompt": want_prompt,
           "mcp_services": list(svcs or []), "allowed_tools": []}
    ke_db.psql(
        "INSERT INTO custom_agents (id, name, config, tenant_id, created_at, updated_at) "
        "VALUES (%s, %s, %s::jsonb, 10000, now(), now());"
        % (ke_db.sql_str(aid), ke_db.sql_str(name),
           ke_db.sql_str(json.dumps(cfg, ensure_ascii=False))),
        stdin=True)
    return aid




def _resolve_svcs(agent: dict) -> list:
    """MCP 服务：`mcp_services` 直接给 id；`mcp_service_names` 按 **name** 解析成 id。

    为什么要按 name（2026-10-30）：`mcp_services` 的 id 是**每套环境自己的**，
    yaml 里写死 id 换环境就错。按 name 解析后，内网/本机都能用同一份配置真源 ✓。
    """
    ids = [str(x) for x in (agent.get("mcp_services") or [])]
    names = [str(x) for x in (agent.get("mcp_service_names") or []) if str(x).strip()]
    if not names:
        return ids
    rows = ke_db.psql_csv(
        "SELECT id, name FROM mcp_services WHERE name IN (%s) ORDER BY name"
        % ", ".join(ke_db.sql_str(n) for n in names))
    got = {str(r.get("name")): str(r.get("id")) for r in rows}
    out = [got[n] for n in names if n in got] + ids
    missing = [n for n in names if n not in got]
    if missing:
        print("   ⚠ mcp_service_names 未登记：%s（跳过；先在 UI/DB 建服务）" % "、".join(missing))
    return out


def _resolve_kbs(agent: dict) -> list:
    """知识库绑定：`knowledge_base_names` 按名解析；`all_domain_kbs: true` ⇒ 所有领域库。

    用户口径（2026-10-30）：**建模智能体**的工作库 = **所有领域库（含企业知识管理领域自己）**；
    其"方法层领域库"仍是企业知识管理领域。`knowledge_bases` 直接给 id 时原样保留。
    """
    ids = [str(x) for x in (agent.get("knowledge_bases") or [])]
    names = [str(x) for x in (agent.get("knowledge_base_names") or []) if str(x).strip()]
    if agent.get("all_domain_kbs"):
        rows = ke_db.psql_csv(
            "SELECT id, name FROM knowledge_bases WHERE deleted_at IS NULL "
            "AND name LIKE %s AND name NOT LIKE %s ORDER BY name"
            % (ke_db.sql_str("%领域%"), ke_db.sql_str("\\_test\\_%")))
        got = [str(r["id"]) for r in rows if r.get("id")]
        print("   领域库（%d）：%s" % (len(got), "、".join(str(r["name"]) for r in rows)))
        ids = got + [i for i in ids if i not in got]
    if names:
        rows = ke_db.psql_csv(
            "SELECT id, name FROM knowledge_bases WHERE deleted_at IS NULL AND name IN (%s)"
            % ", ".join(ke_db.sql_str(n) for n in names))
        got = {str(r.get("name")): str(r.get("id")) for r in rows}
        ids += [got[n] for n in names if n in got and got[n] not in ids]
        missing = [n for n in names if n not in got]
        if missing:
            print("   ⚠ knowledge_base_names 未找到：%s" % "、".join(missing))
    return ids


def _want_tools(agent: dict):
    """**内置工具**清单（`allowed_tools` 的语义，2026-10-27 修正）。

    - 优先 `builtin_tools`（新口径）；兼容旧 `allowed_tools`（自动剔除 `mcp_*`）；
    - **两者都没有 ⇒ 返回 None = 不写该键**（UI 留空 = 平台默认集，不要覆盖）；
    - MCP 工具**不在**这里：由 `config.mcp_services` 挂载决定（见 CONFIG-MATRIX.md §1）。
    """
    if "builtin_tools" in (agent or {}):
        return list(agent.get("builtin_tools") or [])
    if "allowed_tools" in (agent or {}):
        return [t for t in (agent.get("allowed_tools") or []) if not str(t).startswith("mcp_")]
    return None


def _diff(agent: dict, cur: dict) -> dict:
    want_list = _want_tools(agent)
    have_tools = list(cur.get("tools") or [])
    want_tools = want_list if want_list is not None else have_tools
    stale = sorted(t for t in have_tools if str(t).startswith("mcp_"))
    want_prompt = _prompt_text(os.path.join(CONF, agent.get("prompt_file") or ""))
    have_prompt = (cur.get("prompt") or "").strip()
    want_svcs = _resolve_svcs(agent)
    managed_kbs = bool(agent.get("knowledge_bases") or agent.get("knowledge_base_names")
                       or agent.get("all_domain_kbs"))
    want_kbs = _resolve_kbs(agent) if managed_kbs else []
    kbs = list(cur.get("kbs") or [])
    svcs = list(cur.get("svcs") or [])
    return {
        "tools": {"add": sorted(set(want_tools) - set(have_tools)),
                  "del": sorted(set(have_tools) - set(want_tools)),
                  "changed": bool(stale) or (want_list is not None
                                             and sorted(want_tools) != sorted(have_tools)),
                  "stale_mcp": stale, "managed": want_list is not None,
                  "n_from": len(have_tools), "n_to": len(want_tools)},
        "prompt": {"changed": want_prompt != have_prompt,
                   "chars_from": len(have_prompt), "chars_to": len(want_prompt)},
        "svcs": {"changed": bool(want_svcs) and sorted(want_svcs) != sorted(svcs),
                 "n_from": len(svcs), "n_to": len(want_svcs or svcs)},
        "kbs": {"changed": managed_kbs and sorted(want_kbs) != sorted(kbs),
                "n_from": len(kbs), "n_to": len(want_kbs or kbs), "managed": managed_kbs},
        "_want": {"tools": want_list, "prompt": want_prompt, "svcs": want_svcs,
                  "kbs": want_kbs if managed_kbs else None},
    }



def _apply(agent_id: str, want: dict) -> str:
    """一次 UPDATE 写键（`jsonb_set` 逐键 ⇒ 其它配置不丢 ✓）；没给 svcs 就不动勾选。"""
    sql = "UPDATE custom_agents SET config = %s, updated_at = now() WHERE id = %s" % (
        _nest(want), ke_db.sql_str(agent_id))
    return ke_db.psql(sql)


def _nest(want: dict) -> str:
    """`jsonb_set` 逐键嵌套（只覆盖需要写的键，其它键保留）。

    `tools is None` ⇒ **不写** `allowed_tools`（yaml 没声明就别动 UI 的设置 ✓）。
    """
    expr = "config"
    if want.get("tools") is not None:
        expr = ("jsonb_set(%s, '{allowed_tools}', %s::jsonb, true)"
                % (expr, ke_db.sql_str(json.dumps(want["tools"], ensure_ascii=False))))
    expr = ("jsonb_set(%s, '{system_prompt}', to_jsonb(%s::text), true)"
            % (expr, ke_db.sql_str(want["prompt"])))
    if want.get("svcs"):
        expr = ("jsonb_set(%s, '{mcp_services}', %s::jsonb, true)"
                % (expr, ke_db.sql_str(json.dumps(want["svcs"], ensure_ascii=False))))
    if want.get("kbs") is not None:
        expr = ("jsonb_set(%s, '{knowledge_bases}', %s::jsonb, true)"
                % (expr, ke_db.sql_str(json.dumps(want["kbs"], ensure_ascii=False))))
    return expr



def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="写入（默认只比对）")
    ap.add_argument("--check", action="store_true", help="只比对（默认行为，保留以便脚本自解释）")
    ap.add_argument("--only", default="", help="只处理某个 agent id")
    ap.add_argument("--create", action="store_true",
                    help="按 name 找不到时新建智能体（2026-10-30；内网无需 UI 手建）")
    ap.add_argument("--conf", default=os.path.join(CONF, "agent_tools.yaml"))
    args = ap.parse_args()

    doc = _yaml(args.conf)
    # 2026-10-30：`id` 可空 ⇒ 按 `name` 解析（多环境 id 不同）；`--create` 时库里没有就建。
    agents = [a for a in (doc.get("agents") or []) if a.get("id") or a.get("name")]
    if args.only:
        agents = [a for a in agents if a["id"] == args.only]
    if not agents:
        print("没有要处理的智能体（检查 --only / agent_tools.yaml）")
        return 2

    drift, bad = 0, 0
    for a in agents:
        aid = str(a.get("id") or "").strip()
        if not aid:                                   # 按名字解析
            aid = _find_by_name(a.get("name") or "")
            if not aid and args.create:
                want_prompt = _prompt_text(os.path.join(CONF, a.get("prompt_file") or ""))
                aid = _create_agent(a.get("name") or "", want_prompt,
                                    _resolve_svcs(a))
                print("== 新建智能体 %s（%s）id=%s" % (a.get("name") or "", aid,
                                                       aid[:8] + "…"))
            if not aid:
                print("✗ %s：库里找不到同名智能体（加 `--create` 由发布器新建，"
                      "或先在 UI 建好并填 id）" % (a.get("name") or "?"))
                bad += 1
                continue
        cur = _current(aid)
        if not cur:
            print("✗ %s：库里没有这个智能体（先按 MANUAL 建 custom_agents 行）" % aid)
            bad += 1
            continue
        d = _diff(a, cur)
        print("== %s（%s）" % (aid, a.get("name") or ""))
        print("   内置工具：%d → %d  增=%s 删=%s%s"
              % (d["tools"]["n_from"], d["tools"]["n_to"],
                 d["tools"]["add"] or "—", d["tools"]["del"] or "—",
                 ("   ⚠ 库里还有 %d 个 mcp_* 无效项（MCP 工具不在此配置）"
                  % len(d["tools"]["stale_mcp"])) if d["tools"]["stale_mcp"] else ""))
        if not d["tools"]["managed"]:
            print("   （yaml 未声明 builtin_tools ⇒ **不写** allowed_tools；UI 留空=平台默认集）")
        print("   提示词：%d → %d 字  %s"
              % (d["prompt"]["chars_from"], d["prompt"]["chars_to"],
                 "有变化" if d["prompt"]["changed"] else "一致"))
        print("   mcp_services：%d → %d  %s"
              % (d["svcs"]["n_from"], d["svcs"]["n_to"],
                 ("有变化" if d["svcs"]["changed"] else "一致")
                 if _resolve_svcs(a) else "保持 UI 现值（本文件未指定）"))
        if d["kbs"]["managed"]:
            print("   知识库：%d → %d  %s"
                  % (d["kbs"]["n_from"], d["kbs"]["n_to"],
                     "有变化" if d["kbs"]["changed"] else "一致"))
        else:
            print("   知识库：保持 UI 现值（本文件未指定）")
        if not (d["tools"]["changed"] or d["prompt"]["changed"] or d["svcs"]["changed"]
                or d["kbs"]["changed"]):
            print("   ✓ 无漂移")
            continue
        drift += 1
        if not args.apply:
            print("   → 需要发布：加 `--apply` 写入")
            continue
        _apply(aid, d["_want"])
        cur2 = _current(aid)
        tools_ok = ((d["_want"]["tools"] is None and not d["tools"]["stale_mcp"])
                    or (d["_want"]["tools"] is not None
                        and sorted(cur2.get("tools") or []) == sorted(d["_want"]["tools"])))
        ok = tools_ok and (cur2.get("prompt") or "").strip() == d["_want"]["prompt"]
        if d["kbs"]["managed"]:
            ok = ok and sorted(cur2.get("kbs") or []) == sorted(d["_want"]["kbs"] or [])
        print("   %s 已写入并复核（工具 %d 个 / 提示词 %d 字 / 库 %d 个）"
              % ("✓" if ok else "✗", len(cur2.get("tools") or []),
                 len((cur2.get("prompt") or "").strip()), len(cur2.get("kbs") or [])))

        if not ok:
            bad += 1
    return 1 if (bad or (drift and not args.apply)) else 0


if __name__ == "__main__":
    raise SystemExit(main())
