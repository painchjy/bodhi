"""L2 日志还原用例回放（P0.5-3 ✓）

两层：
1. **静态**（全量 625 条）：每条挖掘用例的 `tool` 必须在注册表里、`args` 必须是 dict ✓
   —— 防止"日志里出现过、代码里已删"的工具悄悄消失 ✗
2. **实调**（每工具×每形态抽 1 条）：把 `kb_id` **换成测试库**（生产库一律不碰 ✓），
   调一次，断言：返回 dict、无 `traceback`、工具名被接受（不出现 unknown tool）✓

口径：只回放 `replayable` 用例；危险工具的 `apply` 形态只做静态检查 ✗。
"""
import glob
import json
import os

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
CASES = os.path.join(HERE, "cases", "mined")
INDEX = os.path.join(HERE, "golden", "mined_index.json")


def _index():
    if not os.path.isfile(INDEX):
        pytest.skip("未生成 mined_index.json（先跑 python3 tests/mine_logs.py）")
    return json.load(open(INDEX, encoding="utf-8"))


def _load(fname):
    return json.load(open(os.path.join(CASES, fname), encoding="utf-8"))


def test_mined_cases_exist():
    idx = _index()
    assert idx["totals"]["unique_cases"] > 0, "日志挖掘为空（logs/ 没内容？）✗"
    files = glob.glob(os.path.join(CASES, "*.json"))
    assert files, "未生成用例文件 ✗"


def test_all_mined_tools_registered():
    """全量静态：日志里出现过的工具必须是**在用**的（注册表 ✓ 或退役表 ✓）

    为什么不要求"都在注册表"：日志跨度 13 天，期间**有工具按计划退役** ✓
    （`graph_query`/`rules_of_policy`/`review_apply` 等，见 `server._RETIRED_TOOLS`）。
    真正要防的是"**悄悄消失**"：既不在注册表、也不在退役表 ⇒ 才是 ✗。
    """
    import _helpers as H
    reg = set(H.tool_definitions())
    retired = H.retired_tools()
    idx = _index()
    missing = sorted({c["tool"] for c in idx["cases"]
                      if c["tool"] not in reg and c["tool"] not in retired})
    assert not missing, "日志里用过、既不在注册表也不在退役表（= 悄悄消失 ✗）：%s" % missing


def test_all_mined_args_are_dicts():
    bad = []
    for f in glob.glob(os.path.join(CASES, "*.json")):
        c = json.load(open(f, encoding="utf-8"))
        if not isinstance(c.get("args"), dict):
            bad.append(os.path.basename(f))
    assert not bad, "挖掘用例的 args 不是对象 ✗：%s" % bad[:6]


def _sample(limit: int = 20):
    """每 (tool, shape) 抽 1 条可回放用例（**确定性**：按文件名排序 ✓），最多 `limit` 条。

    上限原因：每条实调都要走一次 MCP（部分工具 1–7s ✗）⇒ 全量跑不动；全量的**静态**校验
    在 `test_all_mined_*` 里已覆盖 ✓。
    """
    seen, out = set(), []
    for f in sorted(glob.glob(os.path.join(CASES, "*.json"))):
        if len(out) >= limit:
            break
        c = json.load(open(f, encoding="utf-8"))
        if not (c.get("meta") or {}).get("replayable"):
            continue
        k = (c["tool"], (c.get("expect") or {}).get("shape"))
        if k in seen:
            continue
        seen.add(k)
        out.append(c)
    return out


@pytest.mark.parametrize("case", _sample(), ids=lambda c: "%s-%s" % (
    c["tool"], (c.get("expect") or {}).get("shape")))
def test_replay_single_case(mcp, test_kb_s, case):
    """实调：**测试库替换**所有库参数 ✓（生产库零接触 ✗）"""
    args = dict(case["args"])
    for k in ("kb_id", "kb_ids", "kb", "work_kb", "domain_kb", "target_kb"):
        if k in args:
            args[k] = test_kb_s if k != "kb_ids" else [test_kb_s]
    args.pop("session_no", None)          # 读类用例不需要真实会话（写类已单独覆盖 ✓）
    r = mcp.call(case["tool"], args)
    assert isinstance(r, dict), "回执不是对象 ✗（%s）" % case["tool"]
    txt = json.dumps(r, ensure_ascii=False)
    assert "traceback" not in txt.lower(), "服务端异常 ✗：%s" % txt[:200]
    assert "unknown tool" not in txt.lower(), "工具名不被接受 ✗：%s" % txt[:200]
