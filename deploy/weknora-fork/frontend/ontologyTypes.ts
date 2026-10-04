// 由 deploy/weknora-fork/gen_frontend_types.py 生成 —— 不要手改。
// 数据来源：artifacts/weknora/ontology_index.json（本体 TTL 的编译产物）。

export interface OntologyTypeMeta {
  label: string;
  color: string;
  module: string;
  moduleLabel: string;
  /** 大类（顶层父类的中文名），用于列表/树的二级分组 */
  group: string;
  order: number;
}

export const ONTOLOGY_TYPES: Record<string, OntologyTypeMeta> = {
  "bmm:Assessment": {"label": "评估", "color": "#3b82f6", "module": "bmm", "moduleLabel": "BMM 扩展本体（含信息来源追踪与枚举类型）", "group": "评估", "order": 0},
  "bmm:AssessmentType": {"label": "评估类型", "color": "#10b981", "module": "bmm", "moduleLabel": "BMM 扩展本体（含信息来源追踪与枚举类型）", "group": "评估类型", "order": 1},
  "bmm:Asset": {"label": "资产", "color": "#f59e0b", "module": "bmm", "moduleLabel": "BMM 扩展本体（含信息来源追踪与枚举类型）", "group": "影响因素", "order": 2},
  "bmm:BusinessPolicy": {"label": "业务政策", "color": "#ef4444", "module": "bmm", "moduleLabel": "BMM 扩展本体（含信息来源追踪与枚举类型）", "group": "手段", "order": 3},
  "bmm:BusinessProcess": {"label": "业务流程", "color": "#8b5cf6", "module": "bmm", "moduleLabel": "BMM 扩展本体（含信息来源追踪与枚举类型）", "group": "业务流程", "order": 4},
  "bmm:BusinessRule": {"label": "业务规则", "color": "#06b6d4", "module": "bmm", "moduleLabel": "BMM 扩展本体（含信息来源追踪与枚举类型）", "group": "手段", "order": 5},
  "bmm:BusinessService": {"label": "业务服务", "color": "#84cc16", "module": "bmm", "moduleLabel": "BMM 扩展本体（含信息来源追踪与枚举类型）", "group": "影响因素", "order": 6},
  "bmm:CourseOfAction": {"label": "行动方案", "color": "#ec4899", "module": "bmm", "moduleLabel": "BMM 扩展本体（含信息来源追踪与枚举类型）", "group": "手段", "order": 7},
  "bmm:DesiredResult": {"label": "预期成果", "color": "#f97316", "module": "bmm", "moduleLabel": "BMM 扩展本体（含信息来源追踪与枚举类型）", "group": "预期成果", "order": 8},
  "bmm:Directive": {"label": "指导规范", "color": "#14b8a6", "module": "bmm", "moduleLabel": "BMM 扩展本体（含信息来源追踪与枚举类型）", "group": "手段", "order": 9},
  "bmm:EnforcementLevel": {"label": "执行级别", "color": "#6366f1", "module": "bmm", "moduleLabel": "BMM 扩展本体（含信息来源追踪与枚举类型）", "group": "执行级别", "order": 10},
  "bmm:ExpiringProduct": {"label": "即将到期的产品", "color": "#eab308", "module": "bmm", "moduleLabel": "BMM 扩展本体（含信息来源追踪与枚举类型）", "group": "影响因素", "order": 11},
  "bmm:ExternalInfluencer": {"label": "外部影响因素", "color": "#3b82f6", "module": "bmm", "moduleLabel": "BMM 扩展本体（含信息来源追踪与枚举类型）", "group": "影响因素", "order": 12},
  "bmm:FixedAsset": {"label": "固定资产", "color": "#10b981", "module": "bmm", "moduleLabel": "BMM 扩展本体（含信息来源追踪与枚举类型）", "group": "影响因素", "order": 13},
  "bmm:Goal": {"label": "目的", "color": "#f59e0b", "module": "bmm", "moduleLabel": "BMM 扩展本体（含信息来源追踪与枚举类型）", "group": "预期成果", "order": 14},
  "bmm:HardwareAsset": {"label": "硬件资产", "color": "#ef4444", "module": "bmm", "moduleLabel": "BMM 扩展本体（含信息来源追踪与枚举类型）", "group": "影响因素", "order": 15},
  "bmm:ITAsset": {"label": "IT资产", "color": "#8b5cf6", "module": "bmm", "moduleLabel": "BMM 扩展本体（含信息来源追踪与枚举类型）", "group": "影响因素", "order": 16},
  "bmm:Influencer": {"label": "影响因素", "color": "#06b6d4", "module": "bmm", "moduleLabel": "BMM 扩展本体（含信息来源追踪与枚举类型）", "group": "影响因素", "order": 17},
  "bmm:InternalInfluencer": {"label": "内部影响因素", "color": "#84cc16", "module": "bmm", "moduleLabel": "BMM 扩展本体（含信息来源追踪与枚举类型）", "group": "影响因素", "order": 18},
  "bmm:KnowledgeSession": {"label": "知识会话", "color": "#ec4899", "module": "bmm", "moduleLabel": "BMM 扩展本体（含信息来源追踪与枚举类型）", "group": "知识会话", "order": 19},
  "bmm:Liability": {"label": "责任", "color": "#f97316", "module": "bmm", "moduleLabel": "BMM 扩展本体（含信息来源追踪与枚举类型）", "group": "责任", "order": 20},
  "bmm:MainSystem": {"label": "主系统", "color": "#14b8a6", "module": "bmm", "moduleLabel": "BMM 扩展本体（含信息来源追踪与枚举类型）", "group": "影响因素", "order": 21},
  "bmm:Means": {"label": "手段", "color": "#6366f1", "module": "bmm", "moduleLabel": "BMM 扩展本体（含信息来源追踪与枚举类型）", "group": "手段", "order": 22},
  "bmm:Objective": {"label": "目标", "color": "#eab308", "module": "bmm", "moduleLabel": "BMM 扩展本体（含信息来源追踪与枚举类型）", "group": "预期成果", "order": 23},
  "bmm:Offering": {"label": "对外交付物", "color": "#3b82f6", "module": "bmm", "moduleLabel": "BMM 扩展本体（含信息来源追踪与枚举类型）", "group": "影响因素", "order": 24},
  "bmm:OperativeBusinessRule": {"label": "操作性业务规则", "color": "#10b981", "module": "bmm", "moduleLabel": "BMM 扩展本体（含信息来源追踪与枚举类型）", "group": "手段", "order": 25},
  "bmm:OrganizationUnit": {"label": "组织机构", "color": "#f59e0b", "module": "bmm", "moduleLabel": "BMM 扩展本体（含信息来源追踪与枚举类型）", "group": "组织机构", "order": 26},
  "bmm:Product": {"label": "产品", "color": "#ef4444", "module": "bmm", "moduleLabel": "BMM 扩展本体（含信息来源追踪与枚举类型）", "group": "影响因素", "order": 27},
  "bmm:Resource": {"label": "资源", "color": "#8b5cf6", "module": "bmm", "moduleLabel": "BMM 扩展本体（含信息来源追踪与枚举类型）", "group": "影响因素", "order": 28},
  "bmm:SessionStatus": {"label": "会话状态", "color": "#06b6d4", "module": "bmm", "moduleLabel": "BMM 扩展本体（含信息来源追踪与枚举类型）", "group": "会话状态", "order": 29},
  "bmm:StructuralBusinessRule": {"label": "结构性业务规则", "color": "#84cc16", "module": "bmm", "moduleLabel": "BMM 扩展本体（含信息来源追踪与枚举类型）", "group": "手段", "order": 30},
  "bmm:SubSystem": {"label": "子系统", "color": "#ec4899", "module": "bmm", "moduleLabel": "BMM 扩展本体（含信息来源追踪与枚举类型）", "group": "影响因素", "order": 31},
  "agent:Advice": {"label": "改进建议", "color": "#06b6d4", "module": "agent", "moduleLabel": "智能体开发本体（业务智能体 + 技能/工具开发 + 评估与改进建议）", "group": "改进建议", "order": 1000},
  "agent:AdviceCategory": {"label": "建议类别", "color": "#84cc16", "module": "agent", "moduleLabel": "智能体开发本体（业务智能体 + 技能/工具开发 + 评估与改进建议）", "group": "建议类别", "order": 1001},
  "agent:AdviceStatus": {"label": "建议状态", "color": "#ec4899", "module": "agent", "moduleLabel": "智能体开发本体（业务智能体 + 技能/工具开发 + 评估与改进建议）", "group": "建议状态", "order": 1002},
  "agent:Agent": {"label": "业务智能体", "color": "#f97316", "module": "agent", "moduleLabel": "智能体开发本体（业务智能体 + 技能/工具开发 + 评估与改进建议）", "group": "业务智能体", "order": 1003},
  "agent:DesignSpec": {"label": "设计单", "color": "#14b8a6", "module": "agent", "moduleLabel": "智能体开发本体（业务智能体 + 技能/工具开发 + 评估与改进建议）", "group": "设计单", "order": 1004},
  "agent:Evaluation": {"label": "评估", "color": "#6366f1", "module": "agent", "moduleLabel": "智能体开发本体（业务智能体 + 技能/工具开发 + 评估与改进建议）", "group": "评估", "order": 1005},
  "agent:InstallGuide": {"label": "安装指引", "color": "#eab308", "module": "agent", "moduleLabel": "智能体开发本体（业务智能体 + 技能/工具开发 + 评估与改进建议）", "group": "安装指引", "order": 1006},
  "agent:KbRole": {"label": "知识库角色", "color": "#3b82f6", "module": "agent", "moduleLabel": "智能体开发本体（业务智能体 + 技能/工具开发 + 评估与改进建议）", "group": "知识库角色", "order": 1007},
  "agent:MCPService": {"label": "MCP服务", "color": "#10b981", "module": "agent", "moduleLabel": "智能体开发本体（业务智能体 + 技能/工具开发 + 评估与改进建议）", "group": "MCP服务", "order": 1008},
  "agent:Metric": {"label": "指标", "color": "#f59e0b", "module": "agent", "moduleLabel": "智能体开发本体（业务智能体 + 技能/工具开发 + 评估与改进建议）", "group": "指标", "order": 1009},
  "agent:Skill": {"label": "技能", "color": "#ef4444", "module": "agent", "moduleLabel": "智能体开发本体（业务智能体 + 技能/工具开发 + 评估与改进建议）", "group": "技能", "order": 1010},
  "agent:Stub": {"label": "挡板", "color": "#8b5cf6", "module": "agent", "moduleLabel": "智能体开发本体（业务智能体 + 技能/工具开发 + 评估与改进建议）", "group": "挡板", "order": 1011},
  "agent:StubStatus": {"label": "挡板状态", "color": "#06b6d4", "module": "agent", "moduleLabel": "智能体开发本体（业务智能体 + 技能/工具开发 + 评估与改进建议）", "group": "挡板状态", "order": 1012},
  "agent:Tool": {"label": "工具", "color": "#84cc16", "module": "agent", "moduleLabel": "智能体开发本体（业务智能体 + 技能/工具开发 + 评估与改进建议）", "group": "工具", "order": 1013},
  "agent:ToolContract": {"label": "工具契约", "color": "#ec4899", "module": "agent", "moduleLabel": "智能体开发本体（业务智能体 + 技能/工具开发 + 评估与改进建议）", "group": "工具契约", "order": 1014},
  "ontology:Class": {"label": "本体类", "color": "#2563eb", "module": "ontology", "moduleLabel": "本体模型", "group": "本体类", "order": 90002},
  "ontology:LightDoc": {"label": "轻量版提示词", "color": "#a16207", "module": "ontology", "moduleLabel": "本体模型", "group": "轻量版", "order": 90005},
  "ontology:Module": {"label": "本体模块", "color": "#0f766e", "module": "ontology", "moduleLabel": "本体模型", "group": "模块", "order": 90001},
  "ontology:Property": {"label": "本体属性", "color": "#0891b2", "module": "ontology", "moduleLabel": "本体模型", "group": "本体属性", "order": 90004},
  "ontology:Relation": {"label": "本体关系", "color": "#7c3aed", "module": "ontology", "moduleLabel": "本体模型", "group": "本体关系", "order": 90003},
};

export const ONTOLOGY_MODULES: { key: string; label: string; order: number }[] = [
  {
    "key": "bmm",
    "label": "BMM 扩展本体（含信息来源追踪与枚举类型）",
    "order": 0
  },
  {
    "key": "agent",
    "label": "智能体开发本体（业务智能体 + 技能/工具开发 + 评估与改进建议）",
    "order": 1
  }
];

/** 所有本体类型 key（图过滤/请求参数用） */
export const ONTOLOGY_TYPE_KEYS: string[] = Object.keys(ONTOLOGY_TYPES);

/**
 * 本体模型库（「企业本体模型」知识库）的页面类型：**统一 5 种**，
 * 不按类区分（类/关系/属性各一页，页类型只表达“这一页是什么”）。
 * 因此这些页的颜色必须按**模块**取（见 ontologyModuleColor）。
 */
export const ONTOLOGY_PAGE_TYPES: Record<string, { label: string; color: string; module: string }> = {
  'ontology:Module': { label: '本体模块', color: '#0f766e', module: 'ontology' },
  'ontology:Class': { label: '本体类', color: '#2563eb', module: 'ontology' },
  'ontology:Relation': { label: '本体关系', color: '#7c3aed', module: 'ontology' },
  'ontology:Property': { label: '本体属性', color: '#0891b2', module: 'ontology' },
  'ontology:LightDoc': { label: '轻量版提示词', color: '#a16207', module: 'ontology' },
};

/** 模块配色（与 Neo4j 侧 MODULE_COLORS 同色值）：节点/圆点颜色 = 模块差异 */
export const MODULE_COLORS: Record<string, string> = {
  bmm: '#3b82f6',
  ea: '#10b981',
  'ea-service': '#f59e0b',
  'ea-ownership': '#ef4444',
  'bmm-fd': '#8b5cf6',
  external: '#94a3b8',
};

export function ontologyModuleColor(moduleKey: string | undefined | null): string {
  return MODULE_COLORS[moduleKey || ''] || '#64748b';
}

/** 页面圆点颜色：本体库页按 page_metadata.ontology.model（模块），其余按页类型 */
export function pageDotColor(page: any): string {
  const mod = page && page.page_metadata && page.page_metadata.ontology
    ? page.page_metadata.ontology.model : ''
  if (mod) return ontologyModuleColor(mod)
  return ontologyColor(page && page.page_type)
}

/** 待确认合并页的类型（由 MCP 保存工具生成，人工裁决后消失） */
export const PENDING_MERGE_TYPE = 'ontology:PendingMerge';

export function isOntologyType(pageType: string | undefined | null): boolean {
  // 2026-10-04（用户口径）：**结构判据** + 白名单兜底。
  //   白名单（ONTOLOGY_TYPES）是**构建期**从编译产物生成的（带中文标签/颜色）；
  //   若只认白名单，则「闭环二新增本体类」必须先重建前端才能编目展示（实测踩过：
  //   agent:WorkKnowledgeBase 建了页、挂了目录，前端仍看不到）。
  //   所以：凡 `模块:类` 形态（含 ':'）一律按**本体类型**处理 —— 类型集合来自
  //   `stats.pages_by_type`（后端动态），新类自动进「本体」tab、自动编目。
  return !!pageType && (!!ONTOLOGY_TYPES[pageType] || pageType.includes(':'));
}

export function isPendingMergeType(pageType: string | undefined | null): boolean {
  return pageType === PENDING_MERGE_TYPE;
}

export function ontologyLabel(pageType: string): string {
  const meta = ONTOLOGY_TYPES[pageType];
  return meta ? meta.label : pageType;
}

/** 列表/树里显示成「目标（bmm:Goal）」这种形式，类名即标签、同时保留本体 key */
export function ontologyDisplayLabel(pageType: string): string {
  const meta = ONTOLOGY_TYPES[pageType];
  return meta ? `${meta.label}（${pageType}）` : pageType;
}

export function ontologyColor(pageType: string): string {
  const meta = ONTOLOGY_TYPES[pageType];
  return meta ? meta.color : '#94a3b8';
}

/** TDesign 主题：按模块给一个稳定可区分的标签色 */
export function ontologyTheme(pageType: string): string {
  const meta = ONTOLOGY_TYPES[pageType];
  if (!meta) return 'default';
  const themes = ['primary', 'success', 'warning', 'danger'];
  const idx = Math.abs(meta.module.split('').reduce((a, c) => a + c.charCodeAt(0), 0)) % themes.length;
  return themes[idx];
}

export function ontologyGroup(pageType: string): string {
  const meta = ONTOLOGY_TYPES[pageType];
  return meta ? meta.group : '';
}

export function ontologyModule(pageType: string): string {
  const meta = ONTOLOGY_TYPES[pageType];
  return meta ? meta.module : '';
}

export function ontologyOrder(pageType: string): number {
  const meta = ONTOLOGY_TYPES[pageType];
  return meta ? meta.order : 999999;
}
