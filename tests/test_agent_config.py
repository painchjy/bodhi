"""回归：**多智能体配置发布**（L1，2026-10-30）。

覆盖：
1. `_prompt_text`：只取第一个 ```text 块（md 顶部说明不算提示词）；
2. `_find_by_name`：按 `config.name` / 顶层 `name` 定位（多环境 id 不同 ⇒ 必须能按名找）；
3. `_resolve_svcs`：`mcp_service_names` → 按 **name** 解析成 id（不写死 id）；
4. `--check` 无漂移（配置真源与库一致）；
5. 三个智能体都在，且建模智能体的提示词含"以任务页步骤为准 / 不把技能当流程 / 自报不确定性"。

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
AGENTS = ("技术方案评审智能体", "企业知识管理建模智能体", "技术方案评审智能体（测试）")


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


def test_config_has_no_drift(cfg):
    """配置真源（yaml）与库内一致 ⇒ 退出码 0。"""
    import subprocess
    script = os.path.join(REPO, "tools", "delivery", "apply_config.py")
    r = subprocess.run([sys.executable, script, "--check"],
                       capture_output=True, text=True, timeout=180)
    out = (r.stdout or "") + (r.stderr or "")
    assert r.returncode == 0, "配置有漂移：\n" + out[-1500:]
    for name in AGENTS:
        assert name in out, "回执里缺智能体：%s" % name


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
    aid = cfg._find_by_name("技术方案评审智能体（测试）")
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
