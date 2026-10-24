"""配置 ↔ 代码一致性（2026-10-24，批 1）：**不许再出现"配置说一套、代码做一套"**。

为什么单独一个用例（真实事故）：
- `deploy/weknora-fork/gen_agents.py` 给智能体下发了 9 个**已退役**工具
  （`rules_of_policy` / `graph_query` / `review_apply` / `rebuild_wiki_from_graph` /
  `link_candidates` / `list_link_candidates` / `resolve_link_candidate` /
  `import_state` / `import_refresh` / `service_overview`）⇒ 智能体调一次就吃
  "该工具已退役"回执，白烧 token 还以为工具能用 ✗；
- `skills/**` 的"工具面"同样漂移过（`rules_of_policy` / `review_apply` 等）。

口径（**结构判定，不碰业务语义**）：
1. `gen_agents.py` 里每个 `mcp_bodhi_ontology_*` ⇒ 必须同时满足
   **① 在 `TOOL_SCHEMAS` 里**（真的对外暴露过）**② 不在 `_RETIRED_TOOLS` 里**（不是退役工具）；
2. `skills/**/*.md` 里出现的 `mcp_bodhi_ontology_*`（若有）同样校验；
3. `skills/README.md` 表格里反引号写的裸工具名（不含 `_`/`(`/`/` 的简单名）若能对上
   本体工具的命名空间，也必须可解析 —— 这里只校验**显式列入退役表**的那些名字不得出现。

免检白名单：`wiki_search` / `wiki_read_page` / `wiki_write_page` / `grep_chunks` /
`list_knowledge_chunks` / `get_document_info` —— 这些是 **WeKnora 原生工具**，不属本仓库 MCP。
"""
import io
import os
import re
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "tests"))

import _helpers as H  # noqa: E402

GEN_AGENTS = os.path.join(REPO, "deploy", "weknora-fork", "gen_agents.py")
SKILLS = os.path.join(REPO, "skills")

NATIVE_OK = {"wiki_search", "wiki_read_page", "wiki_write_page", "grep_chunks",
             "list_knowledge_chunks", "get_document_info"}


def _read(path: str) -> str:
    return io.open(path, encoding="utf-8").read()


def _retired() -> set:
    return set(H.retired_tools())


def _mcp_names(sql: str) -> set:
    """`mcp_bodhi_ontology_<tool>` → `<tool>`。"""
    return set(re.findall(r"mcp_bodhi_ontology_([a-z_0-9]+)", sql))


def test_gen_agents_only_exposes_live_tools():
    src = _read(GEN_AGENTS)
    names = _mcp_names(src)
    assert names, "gen_agents.py 里没解析到任何 MCP 工具（结构变了？）✗"
    tools = set(H.tool_definitions())
    retired = _retired()
    dead = sorted(names & retired)
    unknown = sorted(names - tools - retired)
    msg = []
    if dead:
        msg.append("下发了**已退役**工具（调用只会拿到 deprecated 回执 ✗）：%s" % dead)
    if unknown:
        msg.append("下发了**schema 里没有**的工具（客户端看不到定义 ✗）：%s" % unknown)
    assert not msg, "\n".join(msg)


def test_skills_do_not_reference_retired_tools():
    """`skills/**/*.md` 里不得**作为工具面**出现退役工具名。

    两类豁免（都要求**同一行**有明确标记，防止把"悄悄漂移"混进历史叙述）：
    · 归档目录/归档文件（`_deprecated`）不体检；
    · 同一行写着「退役 / 下架 / 已删 / 已移除」⇒ 属历史说明 ✓（如 README 的沿革注）。
    """
    retired = _retired()
    marks = ("退役", "下架", "已删", "已移除")
    hits = []
    for base, _dirs, files in os.walk(SKILLS):
        for f in files:
            path = os.path.join(base, f)
            rel = os.path.relpath(path, REPO).replace(os.sep, "/")
            if not f.endswith(".md") or "_deprecated" in rel:
                continue                      # 归档技能/归档说明不体检（历史保留 ✓）
            for lineno, line in enumerate(_read(path).splitlines(), 1):
                if any(m in line for m in marks):
                    continue
                for name in retired:
                    # 允许写成 `rules_of_policy(` 或 `mcp_bodhi_ontology_rules_of_policy`
                    if re.search(r"(?<![a-z_0-9])%s(?![a-z_0-9])" % re.escape(name), line):
                        hits.append("%s:%d → %s" % (rel, lineno, name))
    assert not hits, ("技能文档仍在把**已退役**工具当工具面（工具面是错的 ✗）：\n  %s"
                      % "\n  ".join(sorted(hits)))


def test_retired_tools_have_no_handler_or_branch():
    """退役工具的**实现入口**必须已经删干净（退役表是唯一事实源 ✓）。

    例外：`*_legacy` 别名（历史兼容入口）可以保留实现 —— 但它不许出现在 schema 里。
    """
    retired = _retired()
    handlers = H.dispatcher_names()
    leaked = sorted(n for n in retired if n in handlers)
    assert not leaked, ("退役工具还留着分发入口（应删分支/handler，只留退役表 ✓）：%s" % leaked)


def test_retired_table_matches_runtime_table():
    """`ke_eval.RETIRED`（评测口径）必须是 `server._RETIRED_TOOLS` 的**子集**且不含现役工具。

    真实事故（2026-10-24）：`ke_eval.RETIRED` 把 `kb_import` / `retag_rollback` /
    `context_concept_rollback` / `context_authority_apply` 这些**现役**工具也列了 ⇒ 评测会把
    正常调用记成"用了退役工具" ✗。这里用"退役集 ∩ 现役 schema = ∅"把这类脏数据挡住。
    """
    sys.path.insert(0, os.path.join(REPO, "tools", "ke-core"))
    import ke_eval  # noqa: PLC0415

    live = set(H.tool_definitions())
    bad = sorted(set(ke_eval.RETIRED) & live)
    assert not bad, ("ke_eval.RETIRED 里混进了**现役**工具（会把正常调用误判为退役 ✗）：%s" % bad)

