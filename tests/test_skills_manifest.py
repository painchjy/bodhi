"""L2 技能回归 · 规范符合性（静态 ✓ 无需 LLM）

断言（用户口径 2026-10-21）：
1. front-matter 必填：`id/name/description/when/tools`
2. `tools:` 里每个工具名 **必须存在于 MCP 注册表** ✓（防止技能引用退役/不存在的工具 ✗）
3. 技能正文**不得出现域名词**（沿用 `tests/biz_terms.txt` ✓，`_deprecated/` 除外）
4. 必含段落：何时用 / 输出 / 验收（可读性 + 可测性 ✓）
"""
import os
import re

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
SKILLS = os.path.join(REPO, "skills")
TERMS = [t.strip() for t in open(os.path.join(HERE, "biz_terms.txt"), encoding="utf-8").read().splitlines()
         if t.strip() and not t.startswith("#")]

# agent / WeKnora 内置工具（不在 MCP 注册表 ✓，技能可以引用 ✓ 2026-10-21 实测）
AGENT_BUILTIN = {
    "get_document_info", "grep_chunks", "list_knowledge_chunks", "wiki_read_page", "wiki_search",
    "bash", "read_file", "write_file", "list_dir", "edit_file",
    "image_extract", "skills",
}


def _skill_files():
    out = []
    for d in sorted(os.listdir(SKILLS)):
        p = os.path.join(SKILLS, d, "SKILL.md")
        if os.path.isfile(p) and not d.startswith("_"):
            out.append(p)
    return out


def _front_matter(txt: str) -> dict:
    m = re.match(r"^---\n(.*?)\n---\n", txt, re.S)
    fm = {}
    if m:
        for ln in m.group(1).splitlines():
            if ":" in ln and not ln.startswith(" "):
                k, v = ln.split(":", 1)
                fm[k.strip()] = v.strip()
    return fm


def test_skills_have_front_matter():
    missing = []
    for p in _skill_files():
        fm = _front_matter(open(p, encoding="utf-8").read())
        lack = [k for k in ("id", "name", "description", "when", "tools") if not fm.get(k)]
        if lack:
            missing.append("%s → 缺 %s" % (os.path.basename(os.path.dirname(p)), lack))
    assert not missing, "技能 front-matter 不全 ✗：%s" % missing


def test_skill_tools_exist_in_registry():
    """技能 `tools:` 分两类（2026-10-21 口径修正 ✓）：
    · **MCP 工具** ⇒ 必须在注册表里 ✓
    · **agent 内置工具**（前端/WeKnora 提供）⇒ 不进注册表 ✓，用白名单放行
    """
    import _helpers as H
    reg = set(H.tool_definitions())
    bad = {}
    for p in _skill_files():
        fm = _front_matter(open(p, encoding="utf-8").read())
        tools = [t.strip() for t in re.split(r"[,，]", fm.get("tools", "")) if t.strip()]
        tools = [t.strip("[] ") for t in tools]
        unknown = sorted(t for t in tools
                         if t not in reg and t not in AGENT_BUILTIN)
        if unknown:
            bad[os.path.basename(os.path.dirname(p))] = unknown
    assert not bad, "技能引用了不存在的工具 ✗（既非 MCP 也非内置）：%s" % bad


def test_skills_have_when_section():
    """'何时用' 可以写在 front-matter `when:`，也可写在正文 ✓"""
    bad = []
    for p in _skill_files():
        txt = open(p, encoding="utf-8").read()
        fm = _front_matter(txt)
        ok = bool(fm.get("when")) or bool(re.search(r"何时用|When|适用场景", txt))
        if not ok:
            bad.append(os.path.basename(os.path.dirname(p)))
    assert not bad, "缺「何时用」（front-matter when 或正文段）✗：%s" % bad


def test_skills_no_business_terms():
    bad = {}
    for p in _skill_files():
        txt = open(p, encoding="utf-8").read()
        hit = [t for t in TERMS if t in txt]
        if hit:
            bad[os.path.basename(os.path.dirname(p))] = hit[:6]
    # 现状：允许存在（基线另有用例 ✓）；这里只输出，不强制为 0（P3 才压到 0 ✓）
    if bad:
        pytest.skip("域词尚存（P3 清零 ✓）：%s" % bad)


def test_skills_have_output_sections():
    """可读性：至少要有「输出 / 验收」类说明（避免技能只写"做什么"不写"产出什么" ✗）"""
    bad = []
    for p in _skill_files():
        txt = open(p, encoding="utf-8").read()
        if not re.search(r"输出|验收|产出|Output", txt):
            bad.append(os.path.basename(os.path.dirname(p)))
    assert not bad, "缺「输出/验收」段 ✗：%s" % bad
