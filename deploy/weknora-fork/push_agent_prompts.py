"""把 `config/agent_system_prompt.yaml` 里的提示词**定向**推到线上智能体。

为什么需要它（而不是 `gen_agents.py --apply`）
--------------------------------------------------------
`gen_agents.py --apply` 是 **DELETE + INSERT 整行重建**：会把 `knowledge_bases` 清空（它刻意留空）、
把 `model_id` 换回内置智能体的默认值、并覆盖 bmm/ea 的精简提示词（脚本自己的注释也这么警告）。
本脚本只做**一次最小写**：

    UPDATE custom_agents SET config = jsonb_set(config, '{system_prompt}', <yaml 里的文本>)

其它字段（`allowed_tools` / `mcp_services` / `knowledge_bases` / `model_id` / 温度…）**一律不动**。

用法
----
    # 只看差异（不写库）：哪些智能体的线上提示词与 yaml 不一致
    python3 deploy/weknora-fork/push_agent_prompts.py

    # 只推指定的（逗号分隔，key 与 gen_agents.TEMPLATE_IDS 一致：bmm|ea|ops|design|modeler）
    python3 deploy/weknora-fork/push_agent_prompts.py --only modeler --apply

    # 回滚：备份在 <临时目录>/agents_config_backup.json（整份 config，含写前提示词）

注意
----
- **bmm / ea**（抽取智能体）的线上提示词归 `archive/set_agent_prompt_lean.py` 管，推之前想清楚；
- **design** 的线上版本历史上是手改过的 → 默认**不**在目标里，要推必须显式 `--only design`。
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys
import tempfile

REPO = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "tools" / "ke-core"))
sys.path.insert(0, str(REPO / "deploy" / "weknora-fork"))

import ke_db  # noqa: E402
import gen_agents  # noqa: E402

BACKUP = pathlib.Path(tempfile.gettempdir()) / "agents_config_backup.json"


def main() -> int:
    ap = argparse.ArgumentParser(description="定向推送智能体提示词（只改 config.system_prompt）")
    ap.add_argument("--only", default="", help="只推这些 key（逗号分隔）：bmm,ea,ops,design,modeler")
    ap.add_argument("--apply", action="store_true", help="真正写库（默认只比对）")
    args = ap.parse_args()

    wanted = [k.strip() for k in args.only.split(",") if k.strip()]
    tpl = gen_agents.load_templates("")
    targets = {k: v for k, v in tpl.items() if not wanted or k in wanted}
    if wanted and not targets:
        print("!! --only 里没有一个合法 key：%s" % args.only)
        return 2

    ids = [gen_agents.AGENT_IDS.get(k, "bodhi-ontology-%s" % k) for k in tpl]
    rows = ke_db.psql_csv(
        "SELECT id, COALESCE(config::text,'{}') AS config FROM custom_agents WHERE id = ANY(ARRAY['%s'])"
        % "','".join(ids))
    Backup = {r["id"]: r["config"] for r in rows}
    backup_path = BACKUP
    backup_path.write_text(json.dumps(Backup, ensure_ascii=False, indent=2), encoding="utf-8")
    print("备份整份 config → %s（%d 个智能体）" % (backup_path, len(Backup)))
    print()

    changed = 0
    for key, content in targets.items():
        agent = gen_agents.AGENT_IDS.get(key, "bodhi-ontology-%s" % key)
        if agent not in Backup:
            print("   %-18s 线上不存在（跳过）" % agent)
            continue
        cfg = json.loads(Backup[agent])
        cur = cfg.get("system_prompt") or ""
        same = cur.strip() == content.strip()
        print("   %-18s 线上 %5d 字符 ｜ yaml %5d 字符 ｜ %s"
              % (agent, len(cur), len(content), "一致" if same else "**有差异**"))
        if same or not args.apply:
            continue
        ke_db.psql("UPDATE custom_agents SET config = jsonb_set(config, '{system_prompt}', %s::jsonb, true),"
                   " updated_at = now() WHERE id = %s;" % (ke_db.sql_json(content), ke_db.sql_str(agent)))
        changed += 1
        after = json.loads(ke_db.psql_csv(
            "SELECT config::text AS config FROM custom_agents WHERE id = %s" % ke_db.sql_str(agent))[0]["config"])
        others = sorted(k for k, v in after.items() if k != "system_prompt" and v != cfg.get(k))
        print("      → 已更新（%d 字符）；其它字段变化：%s" % (len(after.get("system_prompt") or ""),
                                                       others or "无"))
    print()
    print("本次写入 %d 个智能体%s" % (changed, "" if args.apply else "（未加 --apply，仅比对）"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
