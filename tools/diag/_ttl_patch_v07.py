"""v0.7：合并用户对 `BPMN完整版.ttl` 的手工改动 + 本轮口径调整（dry-run / --apply）。

用户手工改动（改在完整版里）：`业务实体`→**业务对象**、`activityUsesEntity`→**activityUsesDataObject**。
本轮口径（2026-10-11）：
  · 新增 **`:Role`（角色）** + 关系 **`:taskHasRole`**（Task → Role）—— 角色是**概念+关系**，不是数据属性；
  · **删** `:taskRole`、`:taskMeans`（不需要"实现手段"数据属性）；
  · 两份 bpmn 文件**写成完全一致**（sources 是编译真源）。
bmm：新增 **`bmm:TechnicalReviewReport`（技术方案评审报告）** —— 按"不建 Issue、用评审报告跟踪问题"。

用法：python3 tools/diag/_ttl_patch_v07.py [--apply]
"""
import difflib
import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parents[2]
SRC = REPO / "ontology" / "sources"
APPLY = "--apply" in sys.argv

HDR1_OLD = """# 参考 **BPMN 2.0**，但只落**当前需要的概念**：活动 / 任务 / 业务实体 / 业务属性，
# 以及三条关键关联：**活动 ↔ 业务流程**（`processHasActivity` → bmm:BusinessProcess）、
# **任务 → 业务规则**（`taskExecutesRule` → bmm:BusinessRule，故本模块 **imports bmm**）、
# **活动 → 报告**（`activityProducesReport` → bmm:Report）。
# 技能/智能体是活动/任务的具体实现 → 由 `agent.ttl` **imports bpmn** 表达
# （`agent:skillImplementsActivity` / `agent:agentPerformsTask`）。"""
HDR1_NEW = """# 参考 **BPMN 2.0**，但只落**当前需要的概念**：活动 / 任务 / 业务对象 / 角色，
# 以及关键关联：**活动 ↔ 业务流程**（`processHasActivity` → bmm:BusinessProcess）、
# **任务 → 业务规则**（`taskExecutesRule` → bmm:BusinessRule，故本模块 **imports bmm**）、
# **任务 → 角色**（`taskHasRole` → `Role`）、**活动 → 报告**（`activityProducesReport` → bmm:Report）。
# `agent.ttl` **imports bpmn**：**智能体协助活动**（`agent:agentAssistsActivity`）。"""

TASK_HDR_OLD = """# 口径（2026-10-11 用户手工修订）：**为达成某个目标、由一个角色连续执行的工作单元**。
# · 任务**指定"角色"与"目标"**（数据属性 `taskRole` / `taskGoal`）；
# · **人工 / 自动 / 线下取决于"实现的手段"**（`taskMeans`），**不是任务的子类特性** → 不分子类；
# · 任务执行**业务规则**（`taskExecutesRule` → `bmm:BusinessRule`），可由**智能体协助**（`agent:agentAssistsActivity`）。"""
TASK_HDR_NEW = """# 口径（2026-10-11 用户修订）：**为达成某个目标、由一个角色连续执行的工作单元**。
# · **角色**用**关系**表达（`:taskHasRole` → `:Role`，角色实例在各**领域库**定义）；**目标**用 `taskGoal`；
# · 人工 / 自动 / 线下取决于**实现的手段** —— 不建数据属性（需要时在活动/任务页说明），**不分子类**；
# · 任务执行**业务规则**（`taskExecutesRule` → `bmm:BusinessRule`），可由**智能体协助**（`agent:agentAssistsActivity`）。"""

TASK_C_OLD = '    rdfs:comment "为达成某个目标、由一个角色**连续执行**的工作单元（BPMN Task，是 Activity 的一种）：指定**角色**（`taskRole`）与**目标**（`taskGoal`）；人工/自动/线下由**实现的手段**（`taskMeans`）决定；执行一条/一组业务规则。"@zh ;'
TASK_C_NEW = '    rdfs:comment "为达成某个目标、由一个角色**连续执行**的工作单元（BPMN Task，是 Activity 的一种）：**角色**用关系表达（`taskHasRole`），**目标**用 `taskGoal`；执行一条/一组业务规则。"@zh ;'

ROLE_BLOCK = """
# ============ 4. 角色 Role（2026-10-11 新增；**概念 + 关系，不是数据属性**）============
# 各领域建模时**定义自己的角色**（如评审组长 / 评审专家）—— 实例落各自**领域知识库**。
:Role rdf:type owl:Class ;
    rdfs:label "角色"@zh ;
    rdfs:comment "承担任务的主体类型（如评审组长 / 评审专家）：**由各领域在自己的领域知识库里定义实例**；任务通过 `:taskHasRole` 关联角色。人工/自动/线下由**实现的手段**决定，故不建子类。"@zh ;
    rdfs:subClassOf owl:Thing .
"""

ROLE_REL = """:taskHasRole rdf:type owl:ObjectProperty ;
    rdfs:label "由角色执行"@zh ;
    rdfs:domain :Task ;
    rdfs:range :Role ;
    rdfs:comment "任务由**角色**执行（角色是**概念+关系**，不是数据属性）；角色实例在领域知识库里定义。"@zh .
"""

DROP_TASKROLE = """:taskRole rdf:type owl:DatatypeProperty ;
    rdfs:label "执行角色"@zh ;
    rdfs:domain :Task ;
    rdfs:range xsd:string ;
    rdfs:comment "执行该任务的角色（如评审组长 / 评审专家）。"@zh .

"""
DROP_TASKMEANS = """:taskMeans rdf:type owl:DatatypeProperty ;
    rdfs:label "实现手段"@zh ;
    rdfs:domain :Task ;
    rdfs:range xsd:string ;
    rdfs:comment "实现手段：**人工 / 自动 / 线下**（用户口径：人工或自动取决于手段，不是任务的特性）；同时也是智能体介入的位置（`agent:agentAssistsActivity`）。"@zh .
"""

BMM_BLOCK = '''
### 38. 技术方案评审报告 TechnicalReviewReport（2026-10-11 新增；**评审问题的跟踪载体**）
# 用户口径（2026-10-11）：**不建"评审问题"独立概念，用评审报告跟踪问题** —— 报告必然汇总问题并给出结论；
# 多轮评审**更新同一份报告**（同 slug 覆盖、版本+1；轮次见 `bpmn:activityRound`）。
:TechnicalReviewReport rdf:type owl:Class ;
    rdfs:label "技术方案评审报告"@zh ;
    rdfs:comment "技术方案评审活动的产出：汇总智能体/专家提出的问题与处置（同意·否决·自建）并给出评审结论；多轮评审更新同一份。**替代「评审问题」独立概念的跟踪载体。**"@zh ;
    rdfs:subClassOf :Report .
'''


PAIRS = [(SRC / "bmm.ttl", False), (REPO / "ontology" / "BMM完整版.ttl", False),
         (SRC / "bpmn.ttl", True), (REPO / "ontology" / "BPMN完整版.ttl", True)]

for p, is_bpmn in PAIRS:
    before = p.read_text(encoding="utf-8")
    t = before
    if is_bpmn:
        if '"业务实体"' in t or "activityUsesEntity" in t:
            t = (t.replace(HDR1_OLD, HDR1_NEW)
                  .replace("BPMN 2.0 的按需子集：活动/任务/业务实体/业务属性 + 活动↔业务流程、任务→业务规则。",
                           "BPMN 2.0 的按需子集：活动/任务/业务对象/角色 + 活动↔业务流程、任务→业务规则、任务→角色。")
                  .replace("# ============ 3. 业务实体 DataObject", "# ============ 3. 业务对象 DataObject")
                  .replace('    rdfs:label "业务实体"@zh ;', '    rdfs:label "业务对象"@zh ;')
                  .replace("**各领域模型里的「业务实体」类建议继承本类**", "**各领域模型里的「业务对象」类建议继承本类**")
                  .replace(":activityUsesEntity rdf:type owl:ObjectProperty ;",
                           ":activityUsesDataObject rdf:type owl:ObjectProperty ;")
                  .replace('    rdfs:label "使用业务实体"@zh ;', '    rdfs:label "使用业务对象"@zh ;')
                  .replace('rdfs:comment "活动读写的业务实体（输入/输出对象）。"@zh .',
                           'rdfs:comment "活动读写的业务对象（输入/输出对象）。"@zh .'))
        else:
            t = t.replace(HDR1_OLD, HDR1_NEW)      # 已改名的那份：只更新头部说明
        t = t.replace(TASK_HDR_OLD, TASK_HDR_NEW).replace(TASK_C_OLD, TASK_C_NEW)
        if ":Role rdf:type owl:Class" not in t:
            t = t.replace("\n# ============ 关系（对象属性） ============",
                          ROLE_BLOCK + "\n# ============ 关系（对象属性） ============")
        if ":taskHasRole rdf:type owl:ObjectProperty" not in t:
            t = t.replace(":taskExecutesRule rdf:type owl:ObjectProperty ;",
                          ROLE_REL + "\n:taskExecutesRule rdf:type owl:ObjectProperty ;")
        t = t.replace(DROP_TASKROLE, "").replace(DROP_TASKMEANS, "")
        assert ":taskRole rdf:type" not in t and ":taskMeans rdf:type" not in t, "taskRole/taskMeans 未删净"
        assert t.count(":Role rdf:type owl:Class") == 1 and t.count(":taskHasRole rdf:type") == 1
    else:
        if ":TechnicalReviewReport rdf:type owl:Class" not in t:
            t = t.rstrip("\n") + "\n" + BMM_BLOCK
    print("\n===== %s =====" % p.relative_to(REPO).as_posix())
    d = list(difflib.unified_diff(before.splitlines(True), t.splitlines(True), "before", "after", n=1))
    print("".join(d[:24]) if d else "（无变化）")
    if APPLY and before != t:
        p.write_text(t, encoding="utf-8")
        print("→ 已写入")
print("\n%s" % ("已应用。" if APPLY else "**dry-run**（未写文件）。"))
