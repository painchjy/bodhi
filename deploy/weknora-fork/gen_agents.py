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
                "modeler": "skill_modeler_agent", "dev": "skill_development_agent"}

# 2026-10-24（检索 A/B · C1 口径）：**每问第一个工具调用必须是 `lookup_turn_begin`**，
# 再 `knowledge_lookup(..., turn_id=<回执>, first_step=true|false, session_no=…, question=…)`。
# 为什么写进提示词：服务端看不到 WeKnora 原生检索（`wiki_search`/`grep_chunks`），
# 只能靠"轮次边界 + 自报"判"首步是不是 knowledge_lookup"⇒ 这是 A/B 样本准入的纪律 ✓
LOOKUP_DISCIPLINE = (
    "\n\n【检索纪律（A/B 样本准入，2026-10-24）】收到**每一个**用户问题后，**第一个工具调用**必须是 "
    "`lookup_turn_begin(session_no=<当前会话编号>, question=<用户原问题>)`；随后查知识用 "
    "`knowledge_lookup(..., turn_id=<回执的 turn_id>, first_step=true, session_no=…, question=…)`"
    "（若本问在此之前已经调用过 `wiki_search`/`grep_chunks`/`context_lookup` 等其它检索，则 `first_step=false` ✓）；"
    "**不要**在本问里先做 wiki 检索再回来调 `knowledge_lookup`（那样这一问不计入对比 ✗）。"
)

NAMES = {"bmm": "本体知识提取 · BMM 业务动机模型", "ea": "本体知识提取 · EA 企业架构",
         "ops": "知识运维 · 一致性巡检与清理",
         "design": "EA 概要设计 · IT 服务与系统定位",
         "modeler": "本体建模与设计（技能驱动）",
         "dev": "设计开发（技能驱动）"}
DESCRIPTIONS = {
    "bmm": "按 BMM 业务动机模型从知识库片段抽取要素与关系，并为每个要素写入本体类型（bmm:*）的 wiki 页面。",
    "ea": "按 EA 企业架构本体从知识库片段抽取要素与关系，并为每个要素写入本体类型（ea:*）的 wiki 页面。",
    "ops": "巡检 wiki / 本体图谱 / 本体模型 的一致性。**巡检优先只读**（`audit_scan` 出报告）；"
           "**核实后可以处理数据**（2026-10-12 用户口径放开）：清理类动作一律走**两段式** —— "
           "先 `audit_plan` 拿影响面与 ticket 念给用户，用户明确同意后再 `audit_purge` 执行；"
           "另可执行知识库导出/导入（`kb_export` / `kb_import`，导入默认比对一致性）、"
           "知识库残留硬删（`kb_purge`，只处理**已软删**的库）。",
    "design": "读用 EA 本体建模好的业务流程（任务/步骤/实体），做概要设计：产出「概要设计报告」（IT 服务定义、"
              "输入输出、归属任务步骤、正常/异常案例 ASSERTION 规范、服务新建或修改、归属系统与需新建资源），"
              "再把报告细分成图谱节点与关系；两段都先 dry_run、人工确认后 apply。",
    "modeler": "**一个入口、按技能做事**：先 `skills()` 看技能目录，再 `skills(skill=…)` 取该技能完整指令，"
               "然后照做。技能**只限模型、不限本体类型**：先与用户**单选模型**（评审/设计可能跨模型 → "
               "单选底层模型），类/关系/数据属性一律 `ontology_types(\"<模型>\")` 现查（关联模型按依赖引入）。"
               "现有技能：领域知识建模（文档→知识）、结构化数据批量建模（Excel/CSV→类与关系）、"
               "文档评审（按策略逐条评）；设计类技能（概要/详细设计）重构中。",
    "dev": "**一个入口、按技能做事**（设计与开发）：`skill_development` 主技能 —— 先出设计单（业务智能体/"
           "业务技能：目标·触发·技能面·工具面·工作知识库范围·人机分工）→ 用户确认 → 落 `skills/<id>/SKILL.md`"
           "（front-matter 单行值）→ 交付安装指引（含工具状态表：挡板/已投产）与挡板计划。"
           "知识库边界：只写领域知识库；企业本体模型=手工维护（不写）、企业共享概念模型=仅运维智能体可写（只读）；"
           "其余知识库（含原「企业知识管理领域」）一律按**普通领域库**对待、不设特殊边界。"
           "不改本体（需要就写改进建议：adviceCategory=本体类）。",
}
ALLOWED_TOOLS = [
    # 读片段（一次）+ 写页；**不放 thinking / todo_write**：
    # 2026-09-19 实测它们让智能体空转 40 轮、每轮撞 4096 token 截断，最终 0 保存。
    "grep_chunks", "list_knowledge_chunks", "get_document_info",
    "wiki_search", "wiki_read_page", "wiki_write_page",
]
# 「知识运维」智能体：**只给只读工具 + 巡检工具**（不给写页、不给抽取）
#   + `retag_preview`（只读影响面，2026-09-27）—— 回执给运维/用户看，执行仍在人手里
#   + `context_scan` / `context_lookup`（跨库同名/同义/异义 + 依赖，**只读**，2026-09-28）
OPS_TOOLS = [
    "grep_chunks", "list_knowledge_chunks", "get_document_info",
    "wiki_search", "wiki_read_page",
    "mcp_bodhi_ontology_audit_scan", "mcp_bodhi_ontology_audit_plan",
    "mcp_bodhi_ontology_retag_preview",
    "mcp_bodhi_ontology_context_scan", "mcp_bodhi_ontology_context_lookup",
    "mcp_bodhi_ontology_context_page",
    # 巡检清理（用户口径）：有库写权限即可一步硬删；参考规范取文（只读）
    "mcp_bodhi_ontology_audit_purge",
    # 2026-10-24：`rules_of_policy` / `graph_query` 是**已退役**工具（`server._RETIRED_TOOLS`
    # 第一道拦截）⇒ 不再下发给智能体，否则只会拿到"已退役"回执（白烧 token ✗）。
    # 取规则统一 `knowledge_lookup(mode="rules", spec="policy:…"|"direct:…"|"ask")` ✓
    "mcp_bodhi_ontology_reference_lookup",
    "mcp_bodhi_ontology_image_extract",     # 文档内嵌图识别（只读，2026-10-08）
    # 迁移运维（2026-10-12 用户口径 B+C）：**技能目录**（能看到/读出技能指令→按技能做事）
    #   + 知识库导出/导入（导出落 exports/ 并可经 /bodhi/kb-export 下载；导入默认做 PG+Neo4j 六项比对）
    "mcp_bodhi_ontology_skills",
    "mcp_bodhi_ontology_kb_export", "mcp_bodhi_ontology_kb_import",
    # 维护清理（2026-10-24）：清别名噪音（默认 dry_run 只念清单；执行前写版本快照）
    "mcp_bodhi_ontology_clean_alias_noise",
]
# 「EA 概要设计」智能体：只读 wiki + 看本体类型 + **设计落库工具**（不给原生写页、不给抽取）
DESIGN_TOOLS = [
    "grep_chunks", "list_knowledge_chunks", "get_document_info",
    "wiki_search", "wiki_read_page",
    "mcp_bodhi_ontology_ontology_types", "mcp_bodhi_ontology_save_knowledge",
    "mcp_bodhi_ontology_image_extract",     # 文档内嵌图识别（只读，2026-10-08）
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
    # 2026-10-24：`link_candidates` / `list_link_candidates` / `resolve_link_candidate` 已退役
    # （关系随 `save_knowledge` 的 relations 一次写入 ✓）⇒ 不再下发
    "mcp_bodhi_ontology_doc_outline", "mcp_bodhi_ontology_extract_state",
    "mcp_bodhi_ontology_save_knowledge",
    "mcp_bodhi_ontology_audit_scan", "mcp_bodhi_ontology_audit_plan",
    # 改本体类型（2026-09-27）：**两段式** —— 先 retag_preview（只读）把影响面+风险念给用户，
    # 用户明确同意后用同一 ticket 调 retag_apply（缺 ticket/风险确认会被服务端拒）。
    "mcp_bodhi_ontology_retag_preview", "mcp_bodhi_ontology_retag_apply",
    # 跨库上下文映射：只读（候选/查询/渲染）+ **写路径**（概念页 apply/rollback，带写权限校验）
    "mcp_bodhi_ontology_context_scan", "mcp_bodhi_ontology_context_lookup",
    "mcp_bodhi_ontology_context_page",
    "mcp_bodhi_ontology_context_concept_apply", "mcp_bodhi_ontology_context_concept_rollback",
    # 权威/副本（2026-09-29）：同义知识认定一个领域为权威，其它领域只读 + 只能从权威复制
    "mcp_bodhi_ontology_context_authority", "mcp_bodhi_ontology_context_authority_apply",
    # 结构化批量建模（2026-09-29）：一次一个类/一条关系；工具只执行，语义由技能理解
    # 2026-10-24：`import_state` / `import_refresh` 已退役（进度看 `import_plan` 回执 ✓）⇒ 不再下发
    "mcp_bodhi_ontology_import_probe", "mcp_bodhi_ontology_import_plan",
    "mcp_bodhi_ontology_import_apply",
    # 巡检清理（2026-09-29 用户口径）：有库写权限即可**一步硬删**（不再走后台 plan/confirm）
    "mcp_bodhi_ontology_audit_purge",
    # 文档评审所需的"参考规范取文"（只读）
    # 2026-10-24：`rules_of_policy` / `graph_query` / `review_apply` 均已退役 ⇒ 不再下发；
    # 取规则 → `knowledge_lookup(mode="rules", spec=…)`，落评审结论 → `save_knowledge`
    "mcp_bodhi_ontology_reference_lookup",
    "mcp_bodhi_ontology_image_extract",     # 文档内嵌图识别（只读，2026-10-08）
]
TOOLS_BY_AGENT = {"bmm": ALLOWED_TOOLS, "ea": ALLOWED_TOOLS, "ops": OPS_TOOLS,
                  "design": DESIGN_TOOLS, "modeler": MODELER_TOOLS, "dev": MODELER_TOOLS}
# 智能体 id：提取智能体沿用 `bodhi-ontology-<key>`；**运维/设计/合并**各用独立 id，
# 免得被 `set_agent_prompt_lean.py`（按 `bodhi-ontology-%` 前缀改提示词）误伤。
AGENT_IDS = {"bmm": "bodhi-ontology-bmm", "ea": "bodhi-ontology-ea", "ops": "bodhi-kb-ops",
             "design": "bodhi-ea-design", "modeler": "bodhi-ea-modeler",
             "dev": "bodhi-skill-dev"}
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
        # 2026-10-24：**统一追加检索纪律**（A/B C1：每问先 lookup_turn_begin）——
        # 与技能文档的分工：这是"每次交互的行为纪律"，不属于任何单个业务技能 ✓
        out[key] = (by_id[tid] or "").rstrip() + LOOKUP_DISCIPLINE
    if not out:
        raise SystemExit("没有匹配的智能体 key：%s" % only)
    return out


def sql_literal(text: str) -> str:
    return "'" + text.replace("'", "''") + "'"


def build_sql(templates: dict[str, str]) -> str:
    parts = ["-- 由 deploy/weknora-fork/gen_agents.py 生成：克隆已有智能体的 config，覆盖本体提取相关字段",
             "-- ⚠️ bmm/ea 的提示词这里是 yaml 里的**长版**；线上用的是 set_agent_prompt_lean.py 的精简版。",
             "--    只想新建/更新运维智能体：python gen_agents.py --only ops（避免覆盖精简提示词）。",
             "-- 租户固定 10000（2026-09-22 用户口径：mcp 服务与智能体都用 10000，客户自己会改）；",
             "-- 模型与知识库**不预置**（留空，装完在界面里配）。"]
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
            "mcp_services": MCPSERVICE_IDS,
            "mcp_selection_mode": "all",             # 与既有两个智能体一致（不设时上游可能只暴露部分工具）
            "knowledge_bases": [],                   # 2026-09-22：留空，装完在界面里绑
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
-- 租户固定 10000；模型/知识库留空（装完在界面里配）；MCP 服务 id 见 mcp_service.sql
DELETE FROM custom_agents WHERE id = %s;
INSERT INTO custom_agents (id, name, description, avatar, is_builtin, tenant_id, created_by,
                           config, created_at, updated_at, runnable_by_viewer)
SELECT %s, %s, %s, '', false, 10000, '',
       COALESCE((SELECT config FROM custom_agents WHERE is_builtin = true
                  ORDER BY created_at LIMIT 1), '{}'::jsonb) || %s, now(), now(), true;
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
    parser.add_argument("--only", default="", choices=["", "bmm", "ea", "ops", "design", "modeler", "dev"],
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
