"""把本体 MCP 工具真正绑进智能体 + 给提示词加"只准走 MCP 落库"的硬约束。

背景（2026-09-19 实测，app 日志为证）
-----------------------------------
智能体 15 次调用 `mcp_bodhi_ontology_extract_and_save` 全部在 1ms 内失败：

    ERROR [PIPELINE] stage=AgentTool action=execute_failed error="tool not found: ..."

于是它改用**原生** `wiki_write_page` 自己写了 540 页 → 英文 slug、`category_path` 为空、
关系写成「## 与其他要素的关系」自然语言、图谱 edges=0、`last_edit_source` 只有
agent/pipeline（**没有** bodhi-onto-mcp）。

根因：`custom_agents.config` 里缺 `mcp_selection_mode`（对照内置智能体该键存在），
MCP 服务虽然登记在 `mcp_services` 且 enabled，但没进该智能体运行时的工具注册表。

本脚本做两件事（幂等，可反复跑）
--------------------------------
1. **绑定**：给两个 bodhi 智能体补 `mcp_selection_mode='all'`、确保 `mcp_services`
   含本体服务 id、并把 `mcp_bodhi_ontology_extract_and_save` 加进 `allowed_tools`；
2. **提示词硬约束**：往 `config.system_prompt` 追加治理段落（幂等标记 `## 落库硬约束`），
   禁止原生写页工具、禁止自然语言关系章节、强制真实 kb_id/knowledge_id 入参。

用法：
    python deploy/weknora-fork/fix_agent_mcp.py [--dry-run]
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "tools" / "ontology-mcp"))
import server  # noqa: E402

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            pass

SERVICE_ID = "a7c1f0d2-1b2e-4f3a-9c4d-b0d100000001"   # mcp_services.bodhi_ontology
TOOL_NAME = "mcp_bodhi_ontology_extract_and_save"
# 原生写页工具：允许就会绕过本体校验与关系生成（实测 540 次），一律从允许清单移除
FORBIDDEN_TOOLS = ("wiki_write_page", "wiki_page_modify", "wiki_delete_page",
                   "wiki_create_page", "wiki_update_page")
MARK = "## 落库硬约束"

GOVERNANCE = """

## 落库硬约束（bodhi2 治理规则，优先级高于本文前述任何描述）

1. **只能用 MCP 工具落库**：每一批抽取结果都必须调用
   `mcp_bodhi_ontology_extract_and_save`（我们自己的本体保存工具）完成校验与写入。
   **禁止**调用 `wiki_write_page` / `wiki_page_modify` / `wiki_*` 等原生写页工具——
   它们不会做本体合规校验、不会建立要素关系、也不会写版本快照。
2. **禁止自撰关系文字**：不得编写「## 与其他要素的关系」这类由自然语言描述的关系章节，
   不得在正文里用一句话挂多个目标。关系一律由工具按本体定义生成：
   必须是本体里存在的关系（严格满足其 domain/range），由 LLM 只在结构化字段里给出
   `{type, target, target_slug?, label?}`，页面正文的「## 本体关系」小节由服务端渲染为
   `- 关系中文名（`前缀:关系名`）→ [[目标slug|目标名称]]`。
3. **入参必须是真实 id**：`kb_id` 用当前知识库的真实 UUID、`knowledge_id` 用当前文档的真实 id；
   严禁使用 `b1` / `d1` 之类的占位符（曾因此整批失败）。
4. **枚举类不抽取**：本体里的枚举类（is_enum 标记的类）取值不得作为实例抽取为知识页；
   它们只能作为属性取值使用。
5. **来源文档不是关系**：文档自身（如《…方案》）不作为关系目标抽取；出处写在「原文依据」里。
6. 抽取后必须**自查**：工具返回的 `created/merged/pending/violations` 要在回答里如实汇报；
   有 `violations` 时必须说明并修正，不得默默忽略。
7. **异步受理**：`extract_and_save` 会在 100ms 内返回 `{"status":"started","job_id":"..."}`
   （抽取在后台跑 1-2 分钟，因为单次 LLM 调用就要约 1 分钟，而调用方有 60 秒超时限制）。
   拿到 job_id 后**必须**用 `extract_status(job_id=...)` 轮询，直到 status 变成 `done`
   或 `failed`，再据结果汇报；**绝不要**因为看到 `started` 就再次调用 `extract_and_save`
   （那会重复抽取）。若 status=`failed`，把 `error` 原样说明，不要自行重试写页。
"""


def patch_config(cfg: dict) -> tuple[dict, list[str]]:
    changes: list[str] = []
    if cfg.get("mcp_selection_mode") != "all":
        cfg["mcp_selection_mode"] = "all"
        changes.append("mcp_selection_mode → all")
    services = cfg.get("mcp_services")
    if not isinstance(services, list):
        services = []
    if SERVICE_ID not in services:
        services.append(SERVICE_ID)
        cfg["mcp_services"] = services
        changes.append("mcp_services += bodhi_ontology")
    tools = cfg.get("allowed_tools")
    if isinstance(tools, list) and TOOL_NAME not in tools:
        tools.append(TOOL_NAME)
        cfg["allowed_tools"] = tools
        changes.append("allowed_tools += %s" % TOOL_NAME)
    # 硬封锁：把原生写页工具摘掉。只靠提示词禁止不够——实测智能体在被拒后仍会用
    # wiki_write_page 绕过我们（540 次），直接从不允许清单里移除才是确定性保障。
    tools = cfg.get("allowed_tools")
    if isinstance(tools, list):
        blocked = [t for t in tools if t in FORBIDDEN_TOOLS]
        if blocked:
            cfg["allowed_tools"] = [t for t in tools if t not in FORBIDDEN_TOOLS]
            changes.append("allowed_tools -= %s" % ", ".join(blocked))
    prompt = cfg.get("system_prompt") or ""
    if MARK not in prompt:
        cfg["system_prompt"] = prompt.rstrip() + GOVERNANCE
        changes.append("system_prompt += 落库硬约束（%d 字符）" % len(GOVERNANCE))
    return cfg, changes


def main() -> int:
    parser = argparse.ArgumentParser(description="绑定 MCP 工具 + 提示词硬约束")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    rows = server.psql_csv(
        "SELECT id, name, config::text AS cfg FROM custom_agents "
        "WHERE id LIKE 'bodhi-ontology-%' AND deleted_at IS NULL ORDER BY id")
    print("== 找到 %d 个 bodhi 智能体 ==" % len(rows))
    statements = []
    for row in rows:
        cfg = json.loads(row["cfg"] or "{}")
        before = {k: cfg.get(k) for k in ("mcp_selection_mode", "mcp_services", "allowed_tools")}
        cfg, changes = patch_config(cfg)
        after = {k: cfg.get(k) for k in ("mcp_selection_mode", "mcp_services", "allowed_tools")}
        print("  %s（%s）" % (row["id"], row["name"]))
        print("    改前: %s" % json.dumps(before, ensure_ascii=False))
        print("    改后: %s" % json.dumps(after, ensure_ascii=False))
        for ch in changes:
            print("      + %s" % ch)
        if not changes:
            print("      - 无需改动")
            continue
        statements.append(
            "UPDATE custom_agents SET config = %s, updated_at = now() WHERE id = %s;"
            % (server.sql_json(cfg), server.sql_str(row["id"])))

    if args.dry_run or not statements:
        print("（%s）" % ("--dry-run，未写入" if args.dry_run else "无改动"))
        return 0
    server.psql("BEGIN;\n" + "\n".join(statements) + "\nCOMMIT;\n", stdin=True)
    print("== 已更新 %d 个智能体 ==" % len(statements))
    for row in server.psql_csv(
            "SELECT id, config->>'mcp_selection_mode' AS mode, "
            "jsonb_array_length(coalesce(config->'mcp_services','[]'::jsonb)) AS svc, "
            "position('## 落库硬约束' in coalesce(config->>'system_prompt','')) > 0 AS has_rule "
            "FROM custom_agents WHERE id LIKE 'bodhi-ontology-%' AND deleted_at IS NULL ORDER BY id"):
        print("  %s: mode=%s services=%s 硬约束=%s"
              % (row["id"], row["mode"], row["svc"], row["has_rule"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
