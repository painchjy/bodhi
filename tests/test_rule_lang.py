"""规则语言回归（2026-11-02）：模式 → 语言 → `knowledgeNeed` 的**格式**校验与迁移。

不连库：只测纯函数（validate / 迁移 / 归一化）。
"""
import json
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "tools", "ke-core"))

import ke_rule_lang as L  # noqa: E402

NEED_TL = {
    "concepts": [{"id": "c1", "kind": "text", "facts": ["停机窗口", "回退步骤"]},
                 {"id": "c2", "kind": "image", "note": "部署图"}],
    "derived": [{"id": "v", "by": "inference", "from": ["c1", "c2"],
                 "rule": "两项都可定位 ⇒ 符合"}],
    "answer": {"conclusion": "符合|不符合|不适用|无法判定",
               "evidence": "原文逐字", "locator": "章节号"},
}


def test_mode_language_mapping():
    """三种模式各有一套语言，且一一对应（ruleMode ⇄ ruleLanguage）。"""
    assert L.MODES == ("text-logic", "ontology-extract", "compute")
    for m in L.MODES:
        assert L.MODE_BY_LANG[L.LANG_BY_MODE[m]] == m


def test_text_logic_ok():
    assert L.validate("text-logic", NEED_TL) == []


def test_text_logic_errors():
    bad = json.loads(json.dumps(NEED_TL))
    bad["concepts"][0]["kind"] = "video"                  # 不受控的 kind
    bad["derived"][0]["from"] = ["nope"]                  # 引用不存在的 concept
    bad["answer"]["conclusion"] = "通过"                  # 非四态
    errs = L.validate("text-logic", bad)
    assert any("kind" in e for e in errs), errs
    assert any("不存在的 concept" in e for e in errs), errs
    assert any("conclusion" in e for e in errs), errs


def test_ontology_extract_ok_and_errors():
    good = {"types": [{"class": "技术方案"}, {"class": "应用系统"}],
            "relations": [{"from": "技术方案", "rel": "uses", "to": "应用系统"}],
            "answer": NEED_TL["answer"]}
    assert L.validate("ontology-extract", good) == []
    assert L.validate("ontology-extract", {"answer": NEED_TL["answer"]}), "缺 types 必须报错"


def test_compute_ok_and_errors():
    good = {"inputs": [{"id": "p95", "source": "监控"}], "formula": "pass = p95 <= 200",
            "answer": NEED_TL["answer"]}
    assert L.validate("compute", good) == []
    errs = L.validate("compute", {"inputs": [{"id": "p95"}], "answer": NEED_TL["answer"]})
    assert any("formula" in e for e in errs), errs


def test_unknown_mode_and_bad_json():
    assert L.validate("checklist", NEED_TL), "未定义模式必须报错"
    need, err = L.parse_need("技术方案、应用系统")
    assert need is None and err == "not_json", (need, err)


# ── 迁移：knowledgeSpec（note 块）/ 中文清单 → knowledgeNeed ─────────────────
SPEC = {"version": 1,
        "applies_when": 'title_or_objective CONTAINS_ANY ["搬迁","迁移"]',
        "evidence_required": ["搬迁前置条件", "回退方案措施"],
        "pass_when": ["两项都可定位"],
        "fail_when": ["缺回退方案"]}


def test_from_legacy_spec():
    need = L.from_legacy_spec(SPEC)
    assert need["applies_when"].startswith("title_or_objective")
    assert need["concepts"][0]["facts"] == ["搬迁前置条件", "回退方案措施"]
    assert need["derived"][0]["by"] == "inference"
    assert "【pass】" in need["derived"][0]["rule"] and "【fail】" in need["derived"][0]["rule"]
    assert L.validate("text-logic", need) == [], L.validate("text-logic", need)


def test_normalize_note_block():
    """note 里带 ```knowledgeSpec 块（旧写法）⇒ 迁移成 text-logic 的 knowledgeNeed。"""
    note = "【适用条件】…\n```knowledgeSpec\n%s\n```\n已发布 2026-10-27" % json.dumps(SPEC, ensure_ascii=False)
    mode, need, notes = L.normalize(note)
    assert mode == "text-logic", (mode, need)
    assert need["concepts"][0]["facts"], need
    assert any("knowledgeSpec" in n for n in notes), notes


def test_normalize_chinese_term_list():
    """测试库那种"中文类名列表"⇒ ontology-extract 的 types[]。"""
    mode, need, notes = L.normalize("技术方案、应用系统、设备申请")
    assert mode == "ontology-extract", (mode, need)
    assert [t["class"] for t in need["types"]] == ["技术方案", "应用系统", "设备申请"]
    assert L.validate(mode, need) == []
    assert any("中文清单" in n for n in notes), notes


def test_normalize_json_passthrough():
    mode, need, _ = L.normalize(json.dumps(NEED_TL, ensure_ascii=False))
    assert mode == "text-logic" and need == NEED_TL


def test_spec_regex_strips_block():
    text = "前言\n```knowledgeSpec\n{}\n```\n后缀"
    assert "knowledgeSpec" not in L.SPEC_RE.sub("", text)


# ── 类型用**子类**表达（2026-11-02 用户口径），语言写在 note 里 ─────────────
def test_mode_from_class():
    """文本/图像+逻辑推理 ⇒ TextLogicOperationalRule；本体提取 ⇒ StructuralBusinessRule（不建新类）。"""
    assert L.mode_of("bmm:TextLogicOperationalRule") == "text-logic"
    assert L.mode_of("bmm/TextLogicOperationalRule") == "text-logic"
    assert L.mode_of("bmm:StructuralBusinessRule") == "ontology-extract"
    assert L.mode_of("bmm:OperativeBusinessRule") == "", "基类不足以判模式（要么子类、要么 note 声明）"


def test_language_from_note():
    assert L.language_of("备注\nlanguage: kn/text-logic-v1\n已发布") == "kn/text-logic-v1"
    assert L.language_of("schema: kn/compute-v1") == "kn/compute-v1", "schema 别名也认"
    assert L.language_of("没有声明") == ""


def test_mode_from_note_fallback():
    """类认不出时，用 note 里的 language 反推模式（计算类暂无专属子类）。"""
    assert L.mode_of("bmm:OperativeBusinessRule", "language: kn/compute-v1") == "compute"


def test_note_with_language_is_idempotent():
    once = L.note_with_language("步骤说明", "text-logic")
    assert once.endswith("language: kn/text-logic-v1")
    assert L.note_with_language(once, "text-logic") == once
    assert "kn/compute-v1" in L.note_with_language(once, "compute"), "换语言要替换旧行"


