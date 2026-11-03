#!/usr/bin/env python3
"""会话库范围（ke_scope）的**纯函数**回归：标记解析 / 中文兜底 / 不猜库。

不连库：只测 `parse_scope_text` 与 `check_kb` 的入参保护。
（`agent_scope` 要读 WeKnora 库，走 MCP 实测，见 tests/test_rule_engineering 的做法）
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(REPO, "tools", "ke-core"))

import ke_scope as S  # noqa: E402


def test_parse_tag_preferred():
    """机器标记优先：一行就能定两个库。"""
    text = ("你是评审智能体。\n"
            "【库范围】domain_kb: 技术方案评审领域模型 | work_kb: 技术方案评审任务知识库\n"
            "- 领域库（只读）：别的库（不要用这行）\n")
    got = S.parse_scope_text(text)
    assert got == {"domain": "技术方案评审领域模型",
                   "work": "技术方案评审任务知识库", "source": "tag"}, got


def test_parse_cn_fallback():
    """没标记时用中文行兜底，并去掉 `—— 说明` / `（只读）`。"""
    text = ("- 领域库（**只读**）：技术方案评审领域模型 —— 流程 / 活动 / 任务 / 规则\n"
            "- 工作库（**读写**）：技术方案评审任务知识库 —— 源文档 / 报告\n")
    got = S.parse_scope_text(text)
    assert got["domain"] == "技术方案评审领域模型", got
    assert got["work"] == "技术方案评审任务知识库", got
    assert got["source"] == "cn", got


def test_keep_test_suffix_in_name():
    """名字里的 `（测试）` 是名字成分，**不能**当说明去掉（内网测试库就叫这个）。"""
    got = S.parse_scope_text("- 领域库（只读）：技术方案评审领域模型（测试）\n"
                             "- 工作库（读写）：技术方案评审任务知识库（测试）\n")
    assert got["domain"] == "技术方案评审领域模型（测试）", got
    assert got["work"] == "技术方案评审任务知识库（测试）", got


def test_multi_work_kb():
    """建模智能体：工作库=所有领域库 ⇒ work 解析为多库词（不是某个具体库）。"""
    text = ("- **方法层领域库（只读）**：企业知识管理领域 —— 任务页\n"
            "- **工作库（读写）**：**所有领域库**（含企业知识管理领域自己）\n")
    got = S.parse_scope_text(text)
    assert got["domain"] == "企业知识管理领域", got
    assert got["work"] in S._MULTI, got


def test_empty_and_garbage():
    assert S.parse_scope_text("")["source"] == ""
    assert S.parse_scope_text("随便一段话，没有库范围")["domain"] == ""


def test_check_kb_without_session():
    err, scope = S.check_kb("abc", "")
    assert err is None and scope["error"] == "need_session", (err, scope)


if __name__ == "__main__":
    for fn in (test_parse_tag_preferred, test_parse_cn_fallback, test_keep_test_suffix_in_name,
               test_multi_work_kb, test_empty_and_garbage, test_check_kb_without_session):
        fn()
        print("✓ %s" % fn.__name__)
