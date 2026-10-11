"""(A)+(C) 落地后的「口径同步」脚本：删掉两个已删工具的所有引用、加上新运维工具、计数 38→37。

用法：/opt/bodhi-venv/bin/python3 tools/diag/_sync_ac.py > tools/diag/_sync_ac.log 2>&1
"""
import pathlib

REPO = pathlib.Path(__file__).resolve().parents[2]
MISS = []


def sub(path, old, new, want=None):
    p = REPO / path
    if not p.exists():
        print("!! 无此文件 %s" % path)
        MISS.append(path)
        return
    t = p.read_text(encoding="utf-8")
    n = t.count(old)
    if not n:
        print("!! 未命中 %-46s :: %s" % (path, old[:56].replace("\n", "\\n")))
        MISS.append("%s :: %s" % (path, old[:40]))
        return
    if want is not None and n != want:
        print("?? 命中数异常 %-40s 期望 %d 实际 %d" % (path, want, n))
    p.write_text(t.replace(old, new), encoding="utf-8")
    print("OK %-52s x%d :: %s" % (path, n, old[:52].replace("\n", "\\n")))


# ── 1) agents.sql：allowed_tools 去掉两个已删工具，加入运维工具 ───────────────
sub("deploy/weknora-fork/config/agents.sql",
    "'mcp_bodhi_ontology_list_pending_merges', 'mcp_bodhi_ontology_resolve_pending_merge', ", "")
sub("deploy/weknora-fork/config/agents.sql",
    "'mcp_bodhi_ontology_save_knowledge', 'mcp_bodhi_ontology_audit_scan'",
    "'mcp_bodhi_ontology_save_knowledge', 'mcp_bodhi_ontology_rebuild_wiki_from_graph',"
    " 'mcp_bodhi_ontology_audit_scan'")

# ── 2) gen_agents.py ──────────────────────────────────────────────────────
sub("deploy/weknora-fork/gen_agents.py",
    '    "mcp_bodhi_ontology_list_pending_merges", "mcp_bodhi_ontology_resolve_pending_merge",\n', "")
sub("deploy/weknora-fork/gen_agents.py",
    '    "mcp_bodhi_ontology_save_knowledge",\n',
    '    "mcp_bodhi_ontology_save_knowledge",\n'
    '    "mcp_bodhi_ontology_rebuild_wiki_from_graph",\n')

# ── 3) MCP-SERVER.md：工具数 + 已删变量 + selfcheck 期望输出 + 变更注记 ──────
sub("deploy/delivery/MCP-SERVER.md", "**38 个 MCP 工具**", "**37 个 MCP 工具**")
sub("deploy/delivery/MCP-SERVER.md", "tools/list **38 个**", "tools/list **37 个**")
sub("deploy/delivery/MCP-SERVER.md",
    "**多模态识别（`image_extract` 取文档内嵌图）** …）",
    "**多模态识别（`image_extract` 取文档内嵌图）** / **运维·图→正文重建（`rebuild_wiki_from_graph`）** …）")
sub("deploy/delivery/MCP-SERVER.md",
    "`selfcheck.py` 期望输出（14 个工具）：", "`selfcheck.py` 期望输出（37 个工具）：")
sub("deploy/delivery/MCP-SERVER.md",
    "tools/list  OK（14 个）：list_pending_merges, resolve_pending_merge, ontology_types, skills, job_status,",
    "tools/list  OK（37 个）：audit_scan, audit_plan, save_knowledge, doc_outline, extract_state, skills,\n"
    "                        ontology_types, job_status, rebuild_wiki_from_graph,")
sub("deploy/delivery/MCP-SERVER.md",
    "skills()    OK（3 个：document_review / domain_modeling / structured_modeling）",
    "skills()    OK（3 个：document_review / domain_modeling / structured_modeling）\n"
    "```\n\n"
    "> **2026-10-10 工具增删（38 → 37）**：**删除** `list_pending_merges` / `resolve_pending_merge`\n"
    "> （「相似度合并」永久取消，待确认合并页不再产生；相似知识裁决统一走 `audit_scan` 的 S1/S2）；\n"
    "> **新增**运维工具 `rebuild_wiki_from_graph(kb_id, dry_run=true)`（按图节点 `wiki_content` 重建 PG 正文）。\n"
    "> 内网智能体注册请重跑 `deploy/weknora-fork` 的 SQL（`allowed_tools` 已同步）。")

# ── 4) agent-design-flow.md / knowledge-write-contract.md ─────────────────
sub("docs/agent-design-flow.md",
    "`audit_scan` / `list_pending_merges` 支持 `kb_ids`", "`audit_scan` 支持 `kb_ids`")
sub("docs/knowledge-write-contract.md",
    "现状：正文有 7 条生成路径（`build_new_page` / `merge_content` / `build_pending_page` /",
    "现状：正文有 5 条生成路径（`build_new_page` /")

sub("deploy/delivery/MCP-SERVER.md",
    "| `BODHI_SIMILARITY_MERGE` | `0` | 相似度合并：",
    "| ~~`BODHI_SIMILARITY_MERGE`~~ | — | **已删除**（2026-10-10 相似度合并永久取消）：")
sub("deploy/delivery/MCP-SERVER.md",
    "`0`=停用（一律按 `(kb,slug)` 幂等 upsert，`pending` 恒空、`merged` 表示\"同 slug 覆盖\"）；"
    "`1`=旧行为（相似度并页 + 待确认页）。",
    "一律按 `(kb,slug)` 幂等 upsert（`merged` = \"同 slug 覆盖\"）；相似知识治理走巡检 `audit_scan`（S1/S2）。")

# ── 5) 智能体系统提示：两处重复的「抽取工具 + 待确认合并」段落 → 当前口径 ─────
YAML_OLD = """      2. **调用 `mcp_bodhi_ontology_extract_and_save` 一次**（参数：`model` / `kb_id` / `knowledge_id`）：
         - 它内部完成：读片段 → 抽取 → 本体合规校验 → 与存量向量比对 → **合并 / 新增 / 待确认**；
         - **不要自己读源文、不要自己检索存量、不要自己写页面** —— 这些都由这个工具负责；
         - 返回里给出 `created` / `merged` / `pending` 清单，以及 `violations`（不合规项）与 `unmatched`（未归类）。
      3. **汇报**：把返回里的要素数、新增/合并/待确认的数量与清单转述给用户；
         若出现「待确认合并」，告诉用户可以说“合并/新增”来裁决 —— 届时调用
         `mcp_bodhi_ontology_resolve_pending_merge`（参数 `kb_id`、`pending_slug`、`action`）。
"""
YAML_NEW = """      2. **分批落库**：`mcp_bodhi_ontology_doc_outline` → `extract_state` → `mcp_bodhi_ontology_save_knowledge`
         （`stage="graph"` 建节点与边、`stage="report"` 落整篇正文；`mode="dry_run"` 先预览再 apply）；
         - 落库**不再做相似度合并**：一律按 `(kb, slug)` 幂等 upsert（同 slug 覆盖、否则新建）；
         - 返回里给出 `created` / `merged` 清单，以及 `violations`（不合规项）与 `unmatched`（未归类）。
      3. **汇报 + 巡检**：把要素数、新增/覆盖清单转述给用户，并调一次 `mcp_bodhi_ontology_audit_scan`
         （`scope=all` 已含 S1 库内近重复 / S2 域外同名）—— 相似知识**只在巡检里裁决**；
         已无「待确认合并」页、也无 `resolve_pending_merge` 工具。
"""
sub("deploy/weknora-fork/config/agent_system_prompt.yaml", YAML_OLD, YAML_NEW, want=2)

# ── 6) 环境变量样例：确认已不存在的开关（命中与否都容忍） ────────────────────
for envf in ("deploy/delivery/payload/mcp/.env.example", "docker/.env.example", ".env.example"):
    p = REPO / envf
    if p.exists():
        print("·· %s 里 BODHI_SIMILARITY_MERGE 出现 %d 次"
              % (envf, p.read_text(encoding="utf-8").count("BODHI_SIMILARITY_MERGE")))
    else:
        print("·· 无 %s（跳过）" % envf)

print("\n=== 剩余未命中（需人工看）= %d ===" % len(MISS))
for m in MISS:
    print("   " + m)
