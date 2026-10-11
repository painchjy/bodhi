"""按 2026-10-11 用户口径**调整 bpmn / agent 模块**（dry-run 打印 diff，`--apply` 才写）。

bpmn.ttl：
  · `:Task` 定义改为用户口径：**为达成某个目标、由一个角色连续执行的工作单元**；
    任务**指定角色与目标**；**人工/自动/线下取决于"实现的手段"**（不是任务的子类特性）→ 不加子类
  · 任务数据属性补充：`taskGoal`（实现的目标）/ `taskRole`（执行角色）/ `taskMeans`（实现手段）
  · 活动数据属性补充：`activityRound`（轮次；第二轮/第三轮…每次都更新报告）
  · **删除 `:Property`（业务属性）及其关系/数据属性**（用户口径：先去掉）
agent.ttl：
  · 删掉 `:skillImplementsActivity` / `:agentPerformsTask` 两条
  · 换成**一条**连接两模块的关系：`:agentAssistsActivity`（智能体 --协助--> 活动）
    （放在 agent 侧是为了保持依赖方向无环：agent → bpmn）

用法：python3 tools/diag/_ttl_patch_v06.py [--apply]
"""
import difflib
import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parents[2]
SRC = REPO / "ontology" / "sources"
APPLY = "--apply" in sys.argv

TASK_OLD = """# ============ 2. 任务 Task（BPMN: Task ⊂ Activity） ============
:Task rdf:type owl:Class ;
    rdfs:label "任务"@zh ;
    rdfs:comment "**不可再分**的工作单元（BPMN Task，是 Activity 的一种）：执行一条/一组**业务规则**（`taskExecutesRule` → `bmm:BusinessRule`），由**技能/智能体**实现。"@zh ;
    rdfs:subClassOf :Activity ."""
TASK_NEW = """# ============ 2. 任务 Task（BPMN: Task ⊂ Activity） ============
# 口径（2026-10-11 用户手工修订）：**为达成某个目标、由一个角色连续执行的工作单元**。
# · 任务**指定"角色"与"目标"**（数据属性 `taskRole` / `taskGoal`）；
# · **人工 / 自动 / 线下取决于"实现的手段"**（`taskMeans`），**不是任务的子类特性** → 不分子类；
# · 任务执行**业务规则**（`taskExecutesRule` → `bmm:BusinessRule`），可由**智能体协助**（`agent:agentAssistsActivity`）。
:Task rdf:type owl:Class ;
    rdfs:label "任务"@zh ;
    rdfs:comment "为达成某个目标、由一个角色**连续执行**的工作单元（BPMN Task，是 Activity 的一种）：指定**角色**（`taskRole`）与**目标**（`taskGoal`）；人工/自动/线下由**实现的手段**（`taskMeans`）决定；执行一条/一组业务规则。"@zh ;
    rdfs:subClassOf :Activity ."""

PROP_BLOCK_OLD = """# ============ 4. 业务属性 Property（BPMN: Property） ============
:Property rdf:type owl:Class ;
    rdfs:label "业务属性"@zh ;
    rdfs:comment "业务实体在**本流程范围内**的属性（BPMN Property）：如「本次评审的方案等级」。⚠️ 与 OWL 数据属性分工：**跨流程稳定的类属性**用 `owl:DatatypeProperty`；**某活动/任务范围内的取值**用本类实例。"@zh ;
    rdfs:subClassOf owl:Thing .
"""
PROP_BLOCK_NEW = """# ============ 4.（业务属性 Property：**2026-10-11 用户口径先去除**）============
# 需要时再回来：BPMN Property 表达"流程范围内的属性"；当前用 OWL 数据属性承载即可。
"""

REL_OLD = """:dataObjectHasProperty rdf:type owl:ObjectProperty ;
    rdfs:label "具有业务属性"@zh ;
    rdfs:domain :DataObject ;
    rdfs:range :Property ;
    rdfs:comment "业务实体具有的业务属性。"@zh .

"""
PROP_DP_OLD = """:propertyName rdf:type owl:DatatypeProperty ;
    rdfs:label "属性名"@zh ;
    rdfs:domain :Property ;
    rdfs:range xsd:string .

:propertyValue rdf:type owl:DatatypeProperty ;
    rdfs:label "属性值"@zh ;
    rdfs:domain :Property ;
    rdfs:range xsd:string .
"""
NEW_DPS = """:activityRound rdf:type owl:DatatypeProperty ;
    rdfs:label "轮次"@zh ;
    rdfs:domain :Activity ;
    rdfs:range xsd:integer ;
    rdfs:comment "第几轮（第二轮、第三轮…）：每轮评审都**更新同一份报告**（同 slug 覆盖、版本+1）。"@zh .

:taskGoal rdf:type owl:DatatypeProperty ;
    rdfs:label "实现的目标"@zh ;
    rdfs:domain :Task ;
    rdfs:range xsd:string ;
    rdfs:comment "该任务要达成的目标（用户口径：任务 = 为达成某个目标、由一个角色连续执行的工作单元）。"@zh .

:taskRole rdf:type owl:DatatypeProperty ;
    rdfs:label "执行角色"@zh ;
    rdfs:domain :Task ;
    rdfs:range xsd:string ;
    rdfs:comment "执行该任务的角色（如评审组长 / 评审专家）。"@zh .

:taskMeans rdf:type owl:DatatypeProperty ;
    rdfs:label "实现手段"@zh ;
    rdfs:domain :Task ;
    rdfs:range xsd:string ;
    rdfs:comment "实现手段：**人工 / 自动 / 线下**（用户口径：人工或自动取决于手段，不是任务的特性）；同时也是智能体介入的位置（`agent:agentAssistsActivity`）。"@zh .
"""

AG_OLD = """
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
"""
AG_NEW = """
# ============ 与 BPMN 的连接（2026-10-11；本模块 imports bpmn）============
# **一条关系**连接两个模块：**智能体协助活动**（智能体的工作由技能/工具支撑，见 :agentUsesSkill / :skillUsesTool）。
:agentAssistsActivity rdf:type owl:ObjectProperty ;
    rdfs:label "协助活动"@zh ;
    rdfs:domain :Agent ;
    rdfs:range bpmn:Activity ;
    rdfs:comment "智能体**协助**完成活动（活动下的任务由人或智能体执行，取决于任务的**实现手段**）。"@zh .
"""


def patch_bpmn(t: str) -> str:
    assert t.count(TASK_OLD) == 1, "Task 块不唯一"
    t = t.replace(TASK_OLD, TASK_NEW)
    assert t.count(PROP_BLOCK_OLD) == 1, "Property 块不唯一"
    t = t.replace(PROP_BLOCK_OLD, PROP_BLOCK_NEW)
    assert t.count(REL_OLD) == 1, "dataObjectHasProperty 块不唯一"
    t = t.replace(REL_OLD, "")
    assert t.count(PROP_DP_OLD) == 1, "property 数据属性块不唯一"
    t = t.replace(PROP_DP_OLD, NEW_DPS)
    return t


def patch_agent(t: str) -> str:
    assert t.count(AG_OLD) == 1, "agent 侧 BPMN 段不唯一"
    return t.replace(AG_OLD, AG_NEW)


for p, fn in ((SRC / "bpmn.ttl", patch_bpmn),
              (REPO / "ontology" / "BPMN完整版.ttl", patch_bpmn),
              (SRC / "agent.ttl", patch_agent),
              (REPO / "ontology" / "智能体开发完整版.ttl", patch_agent)):
    before = p.read_text(encoding="utf-8")
    after = fn(before)
    print("\n===== %s =====" % p.relative_to(REPO).as_posix())
    d = list(difflib.unified_diff(before.splitlines(True), after.splitlines(True),
                                  "before", "after", n=2))
    print("".join(d[:70]) if d else "（无变化）")
    if APPLY and before != after:
        p.write_text(after, encoding="utf-8")
        print("→ 已写入")
print("\n%s" % ("已应用。" if APPLY else "**dry-run**（未写文件）；确认后加 --apply。"))
