"""给 WeKnora 建「本体知识提取」智能体（BMM / EA）—— 从已有智能体克隆 config 再覆盖。

为什么这样做（见 docs/weknora-fork.md §10.13）
--------------------------------------------
- agent-type preset（`agent_type_presets.yaml`）只是**编辑器里的模板**：前端
  `stores/editorResources.ts -> AgentEditorModal.vue` 才用它，**不会出现在智能体列表**；
- 运行时用的是 `custom_agents.config.system_prompt`（**提示词全文**），
  见 `session_agent_qa.go:360`：`if config.SystemPrompt != "" { UseCustomSystemPrompt = true }`；
- 所以直接把提示词文本 + 工具集写进 `custom_agents` 就能得到一个**开箱可用的智能体**。

用法
----
    python deploy/weknora-fork/gen_agents.py            # 只生成 SQL（config/agents.sql）
    python deploy/weknora-fork/gen_agents.py --apply    # 生成并执行（写入 WeKnora 库）

注意
----
- `config/agents.sql` 是**本次运行**的产物：带 `--only ops` 时文件里只有 ops 的行（不会动 bmm/ea）。
- **别用全量重跑覆盖 bmm/ea 的提示词**：它们的精简版归 `set_agent_prompt_lean.py` 管；
  运维智能体只跑 `--only ops`（id `bodhi-kb-ops`，不在 `bodhi-ontology-%` 里，不被精简脚本误伤）。
"""

from __future__ import annotations

import argparse
import os
import pathlib
import subprocess
import sys
import yaml

HERE = pathlib.Path(__file__).resolve().parent
REPO = HERE.parents[1]
CONFIG = HERE / "config"
OUT_SQL = CONFIG / "agents.sql"

TEMPLATE_IDS = {"bmm": "ontology_extract_agent_bmm", "ea": "ontology_extract_agent_ea",
                "ops": "knowledge_ops_agent", "design": "ea_overview_design_agent",
                "modeler": "skill_modeler_agent"}
NAMES = {"bmm": "本体知识提取 · BMM 业务动机模型", "ea": "本体知识提取 · EA 企业架构",
         "ops": "知识运维 · 一致性巡检与清理",
         "design": "EA 概要设计 · IT 服务与系统定位",
         "modeler": "本体建模与设计（技能驱动）"}
DESCRIPTIONS = {
    "bmm": "按 BMM 业务动机模型从知识库片段抽取要素与关系，并为每个要素写入本体类型（bmm:*）的 wiki 页面。",
    "ea": "按 EA 企业架构本体从知识库片段抽取要素与关系，并为每个要素写入本体类型（ea:*）的 wiki 页面。",
    "ops": "只读巡检 wiki / 本体图谱 / 本体模型 的一致性（含无来源等异常数据），并按需生成清理计划；"
           "执行由人工确认后走 CLI/HTTP，智能体不执行。",
    "design": "读用 EA 本体建模好的业务流程（任务/步骤/实体），做概要设计：产出「概要设计报告」（IT 服务定义、"
              "输入输出、归属任务步骤、正常/异常案例 ASSERTION 规范、服务新建或修改、归属系统与需新建资源），"
              "再把报告细分成图谱节点与关系；两段都先 dry_run、人工确认后 apply。",
    "modeler": "**一个入口、按技能做事**：先 `skills()` 看技能目录，再 `skills(skill=…)` 取该技能完整指令与"
               "本体面，然后照做。技能：领域知识建模（文档→知识，可按对话收窄类/关系）、"
               "企架概要设计（业务模型→IT 服务两层落库）、服务详细设计（服务→操作/属性/主外键/CRUD）。",
}
ALLOWED_TOOLS = [
    # 读片段（一次）+ 写页；**不放 thinking / todo_write**：
    # 2026-09-19 实测它们让智能体空转 40 轮、每轮撞 4096 token 截断，最终 0 保存。
    "grep_chunks", "list_knowledge_chunks", "get_document_info",
    "wiki_search", "wiki_read_page", "wiki_write_page",
]
# 「知识运维」智能体：**只给只读工具 + 巡检工具**（不给写页、不给抽取）
OPS_TOOLS = [
    "grep_chunks", "list_knowledge_chunks", "get_document_info",
    "wiki_search", "wiki_read_page",
    "mcp_bodhi_ontology_audit_scan", "mcp_bodhi_ontology_audit_plan",
]
# 「EA 概要设计」智能体：只读 wiki + 看本体类型 + **设计落库工具**（不给原生写页、不给抽取）
DESIGN_TOOLS = [
    "grep_chunks", "list_knowledge_chunks", "get_document_info",
    "wiki_search", "wiki_read_page",
    "mcp_bodhi_ontology_ontology_types", "mcp_bodhi_ontology_save_knowledge",
]
# 「本体建模与设计（技能驱动）」：**合并智能体**（= 抽取 + 概要设计 + 详细设计，差异全在技能里）。
# 工具是上面几套的并集 + `skills`（技能目录/指令）+ `audit_scan/audit_plan`（详设后念巡检结论）。
# 仍然**不给原生写页工具**：写库只能走 `save_knowledge`（分批建模；原 `extract_and_save` 已于 2026-09-22 移除）。
MODELER_TOOLS = [
    "grep_chunks", "list_knowledge_chunks", "get_document_info",
    "wiki_search", "wiki_read_page",
    "mcp_bodhi_ontology_skills",
    "mcp_bodhi_ontology_ontology_types",
    # 领域建模 v2（2026-09-21）：按切片分批、可交互续跑 —— 不再用异步一次性抽取
    "mcp_bodhi_ontology_doc_outline", "mcp_bodhi_ontology_extract_state",
    "mcp_bodhi_ontology_link_candidates", "mcp_bodhi_ontology_list_link_candidates",
    "mcp_bodhi_ontology_resolve_link_candidate",
    "mcp_bodhi_ontology_list_pending_merges", "mcp_bodhi_ontology_resolve_pending_merge",
    "mcp_bodhi_ontology_save_knowledge",
    "mcp_bodhi_ontology_audit_scan", "mcp_bodhi_ontology_audit_plan",
    "mcp_bodhi_ontology_service_overview",
]
TOOLS_BY_AGENT = {"bmm": ALLOWED_TOOLS, "ea": ALLOWED_TOOLS, "ops": OPS_TOOLS,
                  "design": DESIGN_TOOLS, "modeler": MODELER_TOOLS}
# 智能体 id：提取智能体沿用 `bodhi-ontology-<key>`；**运维/设计/合并**各用独立 id，
# 免得被 `set_agent_prompt_lean.py`（按 `bodhi-ontology-%` 前缀改提示词）误伤。
AGENT_IDS = {"bmm": "bodhi-ontology-bmm", "ea": "bodhi-ontology-ea", "ops": "bodhi-kb-ops",
             "design": "bodhi-ea-design", "modeler": "bodhi-ea-modeler"}
# 目标知识库：企业知识（抽取源）+ 企业本体模型（类型定义查询）
KNOWLEDGE_BASES = [
    "dbc2528f-611b-48da-9a71-d7c93975adb4",
    "08810cbd-af86-48d1-bd25-3b2c338e3d68",
]
# 本体知识保存工具（MCP 服务）的 id：tools/ontology-mcp/server.py
MCPSERVICE_IDS = ["a7c1f0d2-1b2e-4f3a-9c4d-b0d100000001"]
DB_CONTAINER, DB_USER, DB_NAME = "WeKnora-postgres", "postgres", "WeKnora"
# 口令不内置：env `BODHI_DB_PASSWORD` → WeKnora `.env` 的 `DB_PASSWORD`/`POSTGRES_PASSWORD`
WEKNORA_DIR = pathlib.Path(os.environ.get("BODHI_WEKNORA_DIR", "/mnt/c/Users/PHJY/source/WeKnora"))

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            pass


def load_templates(only: str = "") -> dict[str, str]:
    doc = yaml.safe_load((CONFIG / "agent_system_prompt.yaml").read_text(encoding="utf-8"))
    by_id = {t["id"]: t.get("content", "") for t in doc.get("templates", [])}
    out = {}
    for key, tid in TEMPLATE_IDS.items():
        if only and key != only:
            continue
        if tid not in by_id:
            raise SystemExit("模板 %s 不存在（先跑 gen_agent_config.py）" % tid)
        out[key] = by_id[tid]
    if not out:
        raise SystemExit("没有匹配的智能体 key：%s" % only)
    return out


def sql_literal(text: str) -> str:
    return "'" + text.replace("'", "''") + "'"


def build_sql(templates: dict[str, str]) -> str:
    parts = ["-- 由 deploy/weknora-fork/gen_agents.py 生成：克隆已有智能体的 config，覆盖本体提取相关字段",
             "-- ⚠️ bmm/ea 的提示词这里是 yaml 里的**长版**；线上用的是 set_agent_prompt_lean.py 的精简版。",
             "--    只想新建/更新运维智能体：python gen_agents.py --only ops（避免覆盖精简提示词）。"]
    for key, content in templates.items():
        agent_id = AGENT_IDS.get(key, "bodhi-ontology-%s" % key)
        overrides = {
            "agent_mode": "smart-reasoning",
            "agent_type": "custom",                 # Go 端只认这五个常量值，用 custom 最安全
            "system_prompt_id": TEMPLATE_IDS[key],
            "system_prompt": content,               # 运行时用的是这个文本
            "temperature": 0.1,
            # 轮数与单轮预算：0 会回落到 4096（写页 JSON 必被截断）；12 轮足够走完
            # 「读一次片段 → 逐个写页 → 汇报」，实测 40 轮会空转到超时。
            "max_iterations": 12,
            "max_completion_tokens": 16384,
            "thinking": False,
            "enable_rewrite": False,
            "allowed_tools": TOOLS_BY_AGENT.get(key, ALLOWED_TOOLS),
            # 本体知识「保存工具」通过 MCP 挂载（tools/ontology-mcp/server.py）：
            # 一次调用完成抽取+合规+两阈值合并，智能体不再自己判断重复。
            "mcp_services": MCPSERVICE_IDS,
            "mcp_selection_mode": "all",             # 与既有两个智能体一致（不设时上游可能只暴露部分工具）
            "knowledge_bases": KNOWLEDGE_BASES,      # 会话里"能选哪些库"取决于这个字段
            "kb_selection_mode": "selected",
            "retain_retrieval_history": True,
            "faq_priority_enabled": False,
            "web_search_enabled": False,
        }
        pairs = []
        for field, value in overrides.items():
            if isinstance(value, bool):
                literal = "true" if value else "false"
            elif isinstance(value, (int, float)):
                literal = str(value)
            elif isinstance(value, list):
                literal = "jsonb_build_array(%s)" % ", ".join(sql_literal(v) for v in value)
            else:
                literal = sql_literal(value)
            pairs.append("%s, %s" % (sql_literal(field), literal))
        overrides_sql = "jsonb_build_object(%s)" % ", ".join(pairs)
        parts.append("""
-- %s
DELETE FROM custom_agents WHERE id = %s;
INSERT INTO custom_agents (id, name, description, avatar, is_builtin, tenant_id, created_by,
                           config, created_at, updated_at, runnable_by_viewer)
SELECT %s, %s, %s, '', false, t.tenant_id, COALESCE(t.created_by, ''),
       (t.config || %s), now(), now(), true
FROM (SELECT * FROM custom_agents WHERE is_builtin = true ORDER BY created_at LIMIT 1) t;
""" % (NAMES[key], sql_literal(agent_id), sql_literal(agent_id), sql_literal(NAMES[key]),
       sql_literal(DESCRIPTIONS[key]), overrides_sql))
    return "".join(parts)


def db_password() -> str:
    """数据库口令：env `BODHI_DB_PASSWORD` → WeKnora `.env` 的 `DB_PASSWORD`（不内置任何默认口令）。"""
    env_value = os.environ.get("BODHI_DB_PASSWORD")
    if env_value:
        return env_value
    env_file = WEKNORA_DIR / ".env"
    if env_file.is_file():
        for line in env_file.read_text(encoding="utf-8", errors="ignore").splitlines():
            stripped = line.strip()
            if stripped.startswith("#") or "=" not in stripped:
                continue
            key, _, value = stripped.partition("=")
            if key.strip() in ("DB_PASSWORD", "POSTGRES_PASSWORD"):
                return value.strip().strip("'\"")
    raise SystemExit("未设置数据库口令：请设 BODHI_DB_PASSWORD，或把 BODHI_WEKNORA_DIR 指向含 .env 的 WeKnora 目录")


def run_sql(sql: str) -> None:
    cmd = ["wsl", "-d", "Ubuntu", "-u", "root", "docker", "exec", "-i",
           "-e", "PGPASSWORD=" + db_password(), DB_CONTAINER,
           "psql", "-U", DB_USER, "-d", DB_NAME, "-v", "ON_ERROR_STOP=1", "-q", "-f", "-"]
    done = subprocess.run(cmd, input=sql, text=True, encoding="utf-8", capture_output=True, check=False)
    print("psql rc=%s %s" % (done.returncode, (done.stderr or "").strip()[:500]))
    if done.returncode != 0:
        raise SystemExit("写入失败")


def main() -> int:
    parser = argparse.ArgumentParser(description="生成/写入「本体知识提取」智能体")
    parser.add_argument("--apply", action="store_true", help="同时写入数据库")
    parser.add_argument("--only", default="", choices=["", "bmm", "ea", "ops", "design", "modeler"],
                        help="只处理某一个智能体（默认全部；改 bmm/ea 会覆盖它们的精简提示词，慎用）")
    args = parser.parse_args()

    templates = load_templates(args.only)
    sql = build_sql(templates)
    CONFIG.mkdir(parents=True, exist_ok=True)
    OUT_SQL.write_text(sql, encoding="utf-8")
    print("写出 %s（%d 字节）" % (OUT_SQL.relative_to(REPO).as_posix(), len(sql.encode("utf-8"))))
    for key, content in templates.items():
        print("  智能体 %-18s 提示词 %5d 字符  tools=%d"
              % (AGENT_IDS.get(key, key), len(content), len(TOOLS_BY_AGENT.get(key, ALLOWED_TOOLS))))
    if args.apply:
        run_sql(sql)
        print("已写入 WeKnora 数据库")
    return 0


if __name__ == "__main__":
    sys.exit(main())
