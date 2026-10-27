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
        "       config->'mcp_services' AS svcs "
        "  FROM custom_agents WHERE id = %s" % ke_db.sql_str(agent_id))
    if not rows:
        return {}
    r = rows[0]
    for k in ("tools", "svcs"):
        try:
            r[k] = json.loads(r[k] or "[]")
        except Exception:  # noqa: BLE001
            r[k] = []
    return r


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
    want_svcs = list(agent.get("mcp_services") or [])
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
        "_want": {"tools": want_list, "prompt": want_prompt, "svcs": want_svcs},
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
    return expr


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="写入（默认只比对）")
    ap.add_argument("--check", action="store_true", help="只比对（默认行为，保留以便脚本自解释）")
    ap.add_argument("--only", default="", help="只处理某个 agent id")
    ap.add_argument("--conf", default=os.path.join(CONF, "agent_tools.yaml"))
    args = ap.parse_args()

    doc = _yaml(args.conf)
    agents = [a for a in (doc.get("agents") or []) if a.get("id")]
    if args.only:
        agents = [a for a in agents if a["id"] == args.only]
    if not agents:
        print("没有要处理的智能体（检查 --only / agent_tools.yaml）")
        return 2

    drift, bad = 0, 0
    for a in agents:
        aid = a["id"]
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
                 if a.get("mcp_services") else "保持 UI 现值（本文件未指定）"))
        if not (d["tools"]["changed"] or d["prompt"]["changed"] or d["svcs"]["changed"]):
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
        print("   %s 已写入并复核（工具 %d 个 / 提示词 %d 字）"
              % ("✓" if ok else "✗", len(cur2.get("tools") or []),
                 len((cur2.get("prompt") or "").strip())))
        if not ok:
            bad += 1
    return 1 if (bad or (drift and not args.apply)) else 0


if __name__ == "__main__":
    raise SystemExit(main())
