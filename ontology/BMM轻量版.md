# BMM 扩展本体 - 轻量级摘要（用于 LLM 文档提取）
版本: 1.2 | 命名空间: <http://example.org/bmm#>

## 一、核心类（按语义分组）

### 预期成果（DesiredResult）- 企业期望实现的状态
- Goal（目的）：长期、定性、普遍性、持续性
- Objective（目标）：短期、定量、具体性、可度量

### 手段（Means）- 为实现预期成果而调用的资源/方法
- CourseOfAction（行动方案）：业务流程或业务计划，包含：
- Directive（指导规范）：定义或约束企业行为，包含：
  - BusinessPolicy（业务政策）：结构化松散、不可直接强制执行
  - BusinessRule（业务规则）：结构化、原子化、可强制执行，包含：
    - OperativeBusinessRule（操作性）：有执行级别
    - StructuralBusinessRule（结构性）：无条件真实

### 其他核心类
- Influencer（影响因素）：中立的客观事实或潜在变化
  - InternalInfluencer（内部）：假设/价值观/惯例/基础设施/资源等
  - ExternalInfluencer（外部）：竞争者/客户/环境/法规/技术等
- Assessment（评估）：判断影响因素对 Means/DesiredResult 的影响
- BusinessProcess（业务流程）：一组组织内/跨组织的活动
- OrganizationUnit（组织机构）：组织的组成单元

### 资源、资产与交付物
- Resource（资源）：组织拥有、可用于实现其目标的资产或能力
  - Asset（资产）：企业拥有的一种资源，可用于实现目标或产生价值
    - FixedAsset（固定资产）：使用期限超过一个会计期间的资产
      - Offering（对外交付物）：产品/服务的规格说明；可由行动方案定义、由业务流程交付、需要资源、使用固定资产
        - Product（产品）：具体产品
          - ExpiringProduct（即将到期的产品）：即将到期的产品
        - BusinessService（业务服务）：对外提供的业务服务（非 IT 服务）
- Liability（责任）：企业所承担的义务或债务（如未偿债务、保修承诺、合同义务）；由组织机构负责、可由行动方案免除（清偿）、占用（索取）资源

### 信息来源追踪类
- SourceDocument（来源文档）：企业内部文档
  - Regulation（规章制度）
  - BusinessRequirementDoc（业务需求文档）
  - TechnicalSolutionDoc（技术方案文档）
- Excerpt（原文摘录）：从文档中摘取的原文片段

## 二、枚举类（固定取值）

### EnforcementLevel（执行级别）- 用于 OperativeBusinessRule
- Strict（严格执行）：必须无条件遵守
- Override（授权覆盖）：经授权可例外
- Advisory（建议）：推荐遵守，可灵活调整

### AssessmentType（评估类型）- 用于 Assessment，限定为 SWOT 四维度
- Strength（优势）：内部有利条件
- Weakness（劣势）：内部不利因素
- Opportunity（机会）：外部有利趋势
- Threat（威胁）：外部风险挑战

## 三、关键对象属性（按功能分组）

### 实现与量化
- realizes（手段 → 预期成果）：手段用于实现预期成果
- quantifies（目标 → 目的）：目标量化了目的
- containsResult（预期成果 → 预期成果）：包含关系

### 管辖与来源
- governs / isGovernedBy（指导规范 ↔ 行动方案）：管辖/受管辖
- isSourceOf（指导规范 → 行动方案）：指导规范是行动方案的来源

### 流程关联
- realizedBy（行动方案 → 业务流程）：行动方案由业务流程实现
- realizesProcess（业务流程 → 行动方案）：业务流程实现行动方案
- enables（行动方案 → 行动方案）：一个行动方案可以启用其他行动方案
- includes（行动方案 → 行动方案）：一个行动方案可以包含其他行动方案

### 政策与规则
- isBasisFor / isDerivedFrom（业务政策 ↔ 业务规则）：政策是规则的基础/规则源自政策
- guides（业务规则 → 业务流程）：业务规则指导流程
- manages（业务政策 → 业务流程）：业务政策管理流程

### 影响与评估
- influences（影响因素 → 预期成果）：影响预期成果
- influencesMeans（影响因素 → 手段）：影响手段
- assesses（评估 → 影响因素）：评估影响因素
- assessesDesiredResult（评估 → 预期成果）：评估对预期成果的影响
- assessesMeans（评估 → 手段）：评估对手段的影响
- promotesDirective（评估 → 指导规范）：评估促进指导规范

### 组织机构
- definedBy（预期成果 → 组织机构）：由...定义
- createdBy（手段 → 组织机构）：由...创建
- assessedBy（评估 → 组织机构）：由...完成评估
- publishes（组织机构 → 来源文档）：发布

### 资产、交付物与责任
- definesOffering（行动方案 → 对外交付物）：行动方案可以定义对外交付物（产品或服务）
- deploysAsset（行动方案 → 资源）：行动方案可以部署资产
- deliversOffering（业务流程 → 对外交付物）：业务流程可以交付对外交付物
- requiresResource（对外交付物 → 资源）：对外交付物可能需要资源
- usesFixedAsset（对外交付物 → 固定资产）：对外交付物可能使用固定资产
- dischargesLiability（行动方案 → 责任）：行动方案可以免除（清偿）责任
- managesLiability（组织机构 → 责任）：组织机构对责任负责（承担管理职责）
- claimsResource（责任 → 资源）：责任占用（索取）企业资源，如债务需要资金清偿

### 信息来源追踪
- hasSource（DesiredResult/Means/Influencer/Assessment → 来源文档）
- fromSource（原文摘录 → 来源文档）
- definesConcept（原文摘录 → 手段/预期成果/影响因素/评估）
- expressesAssessment（原文摘录 → 评估）

### 枚举属性
- hasEnforcementLevel（操作性业务规则 → 执行级别）：必须恰好一个
- hasAssessmentType（评估 → 评估类型）：必须恰好一个

## 四、数据属性（常用）
- name（名称）、englishName（英文名称）、definition（定义）、description（描述）
- CourseOfActionlevel（行动方案级别：战略/战术）
- influencerCategory（影响因素具体分类：假设/竞争者等）
- documentTitle、documentId、publicationDate（来源文档）
- text、sourcePosition（原文摘录）

## 五、提取模式速查表

### 识别关键词 → 映射类型
| 关键词 | → 类型 |
|--------|--------|
| 愿景、成为、打造、提升（长期） | Goal |
| 达到 X%、< X ms、X 个月内、指标 | Objective |
| 战略、策略、长期规划 | CourseOfAction (Strategy) |
| 方案、计划、措施、行动 | CourseOfAction (Tactic) |
| 政策、方针、原则 | BusinessPolicy |
| 必须、应当、禁止、规则 | BusinessRule |
| 严格执行、强制、不得 | OperativeBusinessRule (Strict) |
| 经审批可例外、可覆盖 | OperativeBusinessRule (Override) |
| 建议、推荐、宜 | OperativeBusinessRule (Advisory) |
| 监管、法规、竞争、市场 | ExternalInfluencer |
| 存量系统、资源、团队 | InternalInfluencer |
| 导致、带来、引发、不利于 | Assessment（需标注 SWOT 类型）|

## 六、基数约束简记
- 每个预期成果、评估必须关联至少一个组织机构
- 每个操作性业务规则必须恰好一个执行级别
- 每个评估必须恰好一个评估类型（SWOT）
- 每段原文摘录必须来自一个来源文档