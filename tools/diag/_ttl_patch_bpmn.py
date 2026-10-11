"""新增 **BPMN 模块** + agent 侧"技能/智能体是活动/任务实现"的关系（2026-10-11）。

· 新建 `ontology/sources/bpmn.ttl`（按需子集：活动/任务/业务实体/业务属性 + 4 关系 + 6 数据属性）
· `agent.ttl`（+ 完整版副本）：加 `@prefix bpmn:`；加 `:skillImplementsActivity`（Skill→bpmn:Activity）
  与 `:agentPerformsTask`（Agent→bpmn:Task）

用法：python3 tools/diag/_ttl_patch_bpmn.py [--apply]
"""
import difflib
import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parents[2]
SRC = REPO / "ontology" / "sources"
TMP = REPO / "tools" / "diag"
APPLY = "--apply" in sys.argv

BPMN_NEW = ((TMP / "_bpmn1.txt").read_text(encoding="utf-8").rstrip("\n") + "\n"
            + (TMP / "_bpmn2.txt").read_text(encoding="utf-8"))

AG_ADD = '''
# ============ 与 BPMN 的连接（2026-10-11 新增；本模块 imports bpmn）============
# 技能 / 智能体是**活动 / 任务的具体实现**（活动↔业务流程、任务→业务规则 在 bpmn 侧表达）
:skillImplementsActivity rdf:type owl:ObjectProperty ;
    rdfs:label "实现活动"@zh ;
    rdfs:domain :Skill ;
    rdfs:range bpmn:Activity ;
    rdfs:comment "技能是**活动**的具体实现（如 `model_recommendation` 实现 A1 立项与需求分析）。"@zh .

:agentPerformsTask rdf:type owl:ObjectProperty ;
    rdfs:label "执行任务"@zh ;
    rdfs:domain :Agent ;
    rdfs:range bpmn:Task ;
    rdfs:comment "智能体**执行任务**（任务执行业务规则）—— 与技能、工具共同构成能力的实现面。"@zh .
'''


def patch_agent(t: str) -> str:
    if "@prefix bpmn:" not in t:
        anchor = "@prefix bmm: <http://example.org/bmm#> .\n"
        i = t.index(anchor) + len(anchor)
        t = t[:i] + "@prefix bpmn: <http://example.org/bpmn#> .\n" + t[i:]
    if ":skillImplementsActivity rdf:type owl:ObjectProperty" not in t:
        t = t.rstrip("\n") + "\n" + AG_ADD
    return t


def patch_new(path: pathlib.Path, content: str) -> None:
    print("\n===== %s（新建）=====" % path.relative_to(REPO).as_posix())
    if path.exists():
        print("!! 已存在，跳过（避免覆盖）")
        return
    print("".join(content.splitlines(True)[:12]))
    print("  … 共 %d 行 / %d 字" % (len(content.splitlines()), len(content)))
    if APPLY:
        path.write_text(content, encoding="utf-8")
        print("→ 已写入")


patch_new(SRC / "bpmn.ttl", BPMN_NEW)

for p in (SRC / "agent.ttl", REPO / "ontology" / "智能体开发完整版.ttl"):
    before = p.read_text(encoding="utf-8")
    after = patch_agent(before)
    print("\n===== %s =====" % p.relative_to(REPO).as_posix())
    d = list(difflib.unified_diff(before.splitlines(True), after.splitlines(True),
                                  "before", "after", n=2))
    print("".join(d[:40]) if d else "（无变化）")
    if APPLY and before != after:
        p.write_text(after, encoding="utf-8")
        print("→ 已写入")

print("\n%s" % ("已应用。" if APPLY else "**dry-run**（未写文件）；确认后加 --apply。"))
