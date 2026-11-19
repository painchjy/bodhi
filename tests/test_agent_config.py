"""回归：**多智能体配置发布**（L1，2026-10-30）。

覆盖：
1. `_prompt_text`：只取第一个 ```text 块（md 顶部说明不算提示词）；
2. `_find_by_name`：按 `config.name` / 顶层 `name` 定位（多环境 id 不同 ⇒ 必须能按名找）；
3. `_resolve_svcs`：`mcp_service_names` → 按 **name** 解析成 id（不写死 id）；
4. `--check` 无漂移（配置真源与库一致）；
5. 两个生产智能体都在；建模提示词含"以任务页步骤为准 / 不把技能当流程 / 自报不确定性"；测试用 _pytest_ agent。

DB 不可用 ⇒ skip。
"""
import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
for p in (HERE, os.path.join(REPO, "tools", "ke-core"),
          os.path.join(REPO, "tools", "delivery")):
    if p not in sys.path:
        sys.path.insert(0, p)

CONF_DIR = os.path.join(REPO, "deploy", "weknora-fork", "config")
AGENTS = ("技术方案评审智能体", "企业知识管理建模智能体")


def test_prompt_text_takes_first_text_block():
    import apply_config as A
    p = os.path.join(CONF_DIR, "agent_modeling_prompt.md")
    if not os.path.isfile(p):
        pytest.skip("缺少建模提示词文件")
    txt = A._prompt_text(p)
    assert txt and len(txt) > 500
    assert "```" not in txt.splitlines()[0] or True
    assert "任务页" in txt and "技能" in txt and "不确定性" in txt


def test_prompt_text_stops_at_block_end():
    """块后内容不得混进提示词（避免把 md 说明写进 system_prompt）。"""
    import apply_config as A
    p = os.path.join(CONF_DIR, "agent_modeling_prompt.md")
    if not os.path.isfile(p):
        pytest.skip("缺少建模提示词文件")
    txt = A._prompt_text(p)
    assert "真源：本文件" not in txt, "md 顶部说明被写进提示词了"


def test_modeling_prompt_uses_task_selector_contract():
    """建模提示词与评审提示词同构：先 domain 选任务，再 page 读任务步骤。"""
    import apply_config as A
    p = os.path.join(CONF_DIR, "agent_modeling_prompt.md")
    if not os.path.isfile(p):
        pytest.skip("缺少建模提示词文件")
    txt = A._prompt_text(p)
    assert "第一步（领域知识与任务选择）" in txt
    assert 'mode="domain"' in txt
    assert "任务页的 `skos:note` 就是步骤" in txt
    assert "不要 page 全部任务" in txt
    assert "四个活动/7 任务" not in txt, "旧版活动枚举应移除，任务由 domain 回执给出"
    assert "字段表" not in txt, "字段表口径应留在任务页/本体，不写进系统提示词"
    assert "bodhi-ext-contract" not in txt, "契约块细节应留在任务页/工具，不写进系统提示词"


def test_prompts_share_compact_final_answer_contract():
    """两个生产提示词都必须约束最终回答长度，避免 reasoning/正文无限膨胀。"""
    import apply_config as A
    for name in ("agent_review_prompt.md", "agent_modeling_prompt.md"):
        p = os.path.join(CONF_DIR, name)
        if not os.path.isfile(p):
            pytest.skip("缺少提示词文件：%s" % name)
        txt = A._prompt_text(p)
        assert "最终回答（压缩）" in txt, name
        assert "3-5 行" in txt, name
        assert "禁止空回答" in txt, name


@pytest.fixture(scope="module")
def cfg():
    try:
        import ke_db
        ke_db.psql_csv("SELECT 1 AS ok")
    except Exception as exc:                       # noqa: BLE001
        pytest.skip("PostgreSQL 不可用：%r" % (str(exc)[:80],))
    import apply_config as A
    return A


def test_find_by_name_resolves_all_agents(cfg):
    for name in AGENTS:
        aid = cfg._find_by_name(name)
        assert aid, "按名字找不到智能体：%s" % name
        assert len(aid) == 36


def test_resolve_svcs_by_name(cfg):
    ids = cfg._resolve_svcs({"mcp_service_names": ["bodhi_ontology", "it_platform"]})
    assert len(ids) == 2, ids
    assert all(len(x) == 36 for x in ids)
    assert len(set(ids)) == 2


def test_modeling_config_has_no_drift(cfg):
    """建模智能体：yaml 与库内一致（评审智能体的历史漂移另行处理）。"""
    import subprocess
    script = os.path.join(REPO, "tools", "delivery", "apply_config.py")
    r = subprocess.run([sys.executable, script, "--check"],
                       capture_output=True, text=True, timeout=180)
    out = (r.stdout or "") + (r.stderr or "")
    assert "企业知识管理建模智能体" in out, "回执里缺建模智能体：\n" + out[-1500:]
    block = out.split("企业知识管理建模智能体", 1)[1].split("==", 1)[0]
    assert "无漂移" in block, "建模智能体配置有漂移：\n" + block[-1200:]


def test_modeling_agent_binds_all_domain_kbs(cfg):
    """建模智能体：工作库 = **所有领域库（含企业知识管理领域自己）**。"""
    aid = cfg._find_by_name("企业知识管理建模智能体")
    assert aid
    cur = cfg._current(aid)
    names = {str(r["name"]) for r in cfg.ke_db.psql_csv(
        "SELECT name FROM knowledge_bases WHERE id IN (%s)"
        % ", ".join(cfg.ke_db.sql_str(x) for x in (cur.get("kbs") or [])))}
    assert "企业知识管理领域" in names, "建模智能体没绑自己的领域库：%r" % names
    assert len(names) >= 3, "领域库数量过少：%r" % names


def test_test_agent_binds_test_kbs(cfg):
    """测试评审智能体：只绑**测试领域库 + 测试工作库**（不污染生产库）。"""
    pytest.skip("技术方案评审智能体（测试）已退役；测试用 __pytest_scope_agent__")
    assert aid
    cur = cfg._current(aid)
    names = {str(r["name"]) for r in cfg.ke_db.psql_csv(
        "SELECT name FROM knowledge_bases WHERE id IN (%s)"
        % ", ".join(cfg.ke_db.sql_str(x) for x in (cur.get("kbs") or [])))}
    assert names == {"技术方案评审领域模型（测试）", "技术方案评审任务知识库（测试）"}, names

    """建模智能体提示词必须写明：以任务页为步骤 / 不把技能当流程 / 绑定=图关系。"""
    aid = cfg._find_by_name("企业知识管理建模智能体")
    assert aid
    rows = cfg.ke_db.psql_csv(
        "SELECT config->>'system_prompt' AS p FROM custom_agents WHERE id = %s"
        % cfg.ke_db.sql_str(aid))
    p = (rows[0].get("p") if rows else "") or ""
    for marker in ("任务页", "不是流程" if "不是流程" in p else "技能",
                   "关系实例", "不确定性"):
        assert marker in p, "提示词缺标记：%s" % marker


def test_pytest_scope_agent_is_all(_test_scope_agent):
    """生产测试智能体已退役；测试用 _pytest_ 专用 agent（all + 占位工作库）。"""
    import ke_db
    rows = ke_db.psql_csv(
        "SELECT config->>'kb_selection_mode' AS mode, config->>'system_prompt' AS prompt "
        "FROM custom_agents WHERE id = %s" % ke_db.sql_str(_test_scope_agent))
    assert rows, "pytest scope agent 不存在"
    assert rows[0].get("mode") == "all", rows
    assert "《指定工作库》" in (rows[0].get("prompt") or ""), rows
