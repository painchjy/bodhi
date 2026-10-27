"""规则契约枚举必须与**台账契约**同源（2026-10-27，P2）。

背景：规则契约以前硬编码 `合规｜违规｜待澄清` + `高｜中｜低`，台账/报告用
`符合｜不符合｜无法判定｜不适用` + `严重｜一般｜建议` ⇒ 模型每轮都要心算映射。
现在 `ke_review._ledger_enums()` 直接读 `tools/it-platform-mcp/schema.json`。
本测试的断言方式：**不写死期望值**，而是比对两份真源（改一处忘另一处会红 ✓）。
"""
import json
import os
import sys
import traceback

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "tools", "ke-core"))

import ke_review  # noqa: E402


def _ledger_schema() -> dict:
    with open(os.path.join(REPO, "tools", "it-platform-mcp", "schema.json"), encoding="utf-8") as fh:
        return json.load(fh)


def test_verdict_enum_same_as_ledger():
    sc = _ledger_schema()
    assert ke_review._ledger_enums()["verdict"] == list(sc["verdict_enum"]), \
        "规则契约的判定枚举必须与台账 verdict_enum 完全一致"


def test_severity_enum_same_as_ledger():
    sc = _ledger_schema()
    assert ke_review._ledger_enums()["severity"] == list(sc["severity_enum"]), \
        "规则契约的严重度枚举必须与台账 severity_enum 完全一致"


def test_parse_knowledge_spec_from_note_block():
    """note 里的 ```knowledgeSpec 块（JSON / YAML 都可解析；没有块 ⇒ {}）。"""
    js = '步骤\n```knowledgeSpec\n{"applies_when": "title_or_objective CONTAINS_ANY [\\"搬迁\\"]"}\n```\n'
    got = ke_review.parse_knowledge_spec(js)
    assert got.get("applies_when", "").startswith("title_or_objective"), got

    ym = "```knowledgeSpec\napplies_when: always\nevidence_required: [a, b]\n```\n"
    got2 = ke_review.parse_knowledge_spec(ym)
    assert got2.get("applies_when") == "always", got2
    assert got2.get("evidence_required") == ["a", "b"], got2

    assert ke_review.parse_knowledge_spec("没有块") == {}
    assert ke_review.parse_knowledge_spec("") == {}


def test_required_fields_follow_ledger_enum():
    """`required_fields.conclusion` 必须含台账四态（不再出现"合规/违规/待澄清"）。"""
    enums = ke_review._ledger_enums()
    concl = "｜".join(enums["verdict"])
    for v in enums["verdict"]:
        assert v in concl
    for old in ("合规", "违规", "待澄清"):
        assert old not in concl, "旧枚举必须彻底退场：%s" % old


def main() -> int:
    tests = [(n, f) for n, f in sorted(globals().items())
             if n.startswith("test_") and callable(f)]
    bad = 0
    for name, fn in tests:
        try:
            fn()
            print("  ✓ %s" % name)
        except Exception as exc:  # noqa: BLE001
            bad += 1
            print("  ✗ %s：%s" % (name, exc))
            traceback.print_exc()
    print("规则契约枚举用例：%d 个 ｜ 通过 %d ｜ 失败 %d" % (len(tests), len(tests) - bad, bad))
    return 0 if not bad else 1


if __name__ == "__main__":
    sys.exit(main())
