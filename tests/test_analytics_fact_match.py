#!/usr/bin/env python3
"""事实选择算法 · 纯函数回归（静态，不连库）。

验证：
1. 指标 / 事实表 / 场景 / 分组分类字段 / 筛选条件都能参与匹配；
2. 多个事实分数接近时判为 ambiguous，不自动选；
3. 唯一高分才判为 confident；
4. 提示词要求的输入分组能被解析。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _helpers as H  # noqa: E402

KE_CORE = os.path.join(H.REPO, "tools", "ke-core")
if KE_CORE not in sys.path:
    sys.path.insert(0, KE_CORE)

import ke_analytics  # noqa: E402


def _profile(fid: str, name: str, metric: str, table: str,
             scenario: str, field: str, filter_text: str) -> dict:
    return {
        "fact": {"fact_id": fid, "code": fid, "name": name,
                 "description": name, "grain": "k"},
        "metrics": [{"item_id": fid + "_m", "kind": "base_metric",
                     "name": metric, "match_text": metric}],
        "fact_tables": [{"table_id": table, "source_table": table,
                         "match_text": table + " " + name}],
        "intent": [{"scenario_id": fid + "_s", "name": scenario,
                    "match_text": scenario}],
        "fields": [{"item_id": fid + "_d", "kind": "dimension",
                    "dimension_name": field, "match_text": field}],
        "filters": [{"item_id": fid + "_f", "kind": "fact_field",
                     "name": filter_text, "match_text": filter_text}],
        "services": [],
    }


def main() -> int:
    f1 = _profile("f1", "工具调用事实", "工具耗时", "tool_call_fact",
                  "工具使用分析", "工具分类", "失败")
    f2 = _profile("f2", "轮次事实", "轮次耗时", "turn_fact",
                  "Token 消耗", "日期", "失败")
    inputs = ke_analytics._fact_match_inputs(
        "工具耗时", ["tool_call_fact"], "工具使用分析",
        "失败", "工具分类", "工具小类")
    s1 = ke_analytics._score_fact_profile(f1, inputs)
    s2 = ke_analytics._score_fact_profile(f2, inputs)

    decision1 = ke_analytics._fact_match_decision([
        {"score": 0.82, "strong_hits": 2},
        {"score": 0.40, "strong_hits": 1},
    ])
    decision2 = ke_analytics._fact_match_decision([
        {"score": 0.72, "strong_hits": 2},
        {"score": 0.70, "strong_hits": 2},
    ])
    decision3 = ke_analytics._fact_match_decision([
        {"score": 0.20, "strong_hits": 0},
    ])

    checks = {
        "输入分组解析": (inputs["metrics"] == ["工具耗时"]
                         and inputs["filters"] == ["失败"]
                         and inputs["fields"] == ["工具分类", "工具小类"]
                         and inputs["fact_tables"] == ["tool_call_fact"]),
        "指标+事实表+场景+字段+筛选命中 f1": s1["score"] > s2["score"]
                                              and s1["score"] >= 0.55,
        "f2 仍能被自身指标命中": ke_analytics._score_fact_profile(
            f2, ke_analytics._fact_match_inputs("轮次耗时", [], "Token 消耗",
                                                None, None, None))["score"] > 0,
        "唯一高分 = confident": (decision1["decision"] == "confident"
                                 and not decision1["requires_user_confirmation"]),
        "分数接近 = ambiguous": (decision2["decision"] == "ambiguous"
                                 and decision2["requires_user_confirmation"]),
        "低分 = no_match": decision3["decision"] == "no_match",
    }
    return H.report(checks, "事实选择算法")


if __name__ == "__main__":
    sys.exit(main())
