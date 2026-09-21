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
  "bmm:Assessment": {"label": "评估", "color": "#3b82f6", "module": "bmm", "moduleLabel": "BMM 业务动机模型", "group": "评估", "order": 0},
  "bmm:AssessmentType": {"label": "评估类型", "color": "#10b981", "module": "bmm", "moduleLabel": "BMM 业务动机模型", "group": "评估类型", "order": 1},
  "bmm:Asset": {"label": "资产", "color": "#f59e0b", "module": "bmm", "moduleLabel": "BMM 业务动机模型", "group": "影响因素", "order": 2},
  "bmm:BusinessPolicy": {"label": "业务政策", "color": "#ef4444", "module": "bmm", "moduleLabel": "BMM 业务动机模型", "group": "手段", "order": 3},
  "bmm:BusinessProcess": {"label": "业务流程", "color": "#8b5cf6", "module": "bmm", "moduleLabel": "BMM 业务动机模型", "group": "业务流程", "order": 4},
  "bmm:BusinessRule": {"label": "业务规则", "color": "#06b6d4", "module": "bmm", "moduleLabel": "BMM 业务动机模型", "group": "手段", "order": 5},
  "bmm:BusinessService": {"label": "业务服务", "color": "#84cc16", "module": "bmm", "moduleLabel": "BMM 业务动机模型", "group": "影响因素", "order": 6},
  "bmm:CourseOfAction": {"label": "行动方案", "color": "#ec4899", "module": "bmm", "moduleLabel": "BMM 业务动机模型", "group": "手段", "order": 7},
  "bmm:DesiredResult": {"label": "预期成果", "color": "#f97316", "module": "bmm", "moduleLabel": "BMM 业务动机模型", "group": "预期成果", "order": 8},
  "bmm:Directive": {"label": "指导规范", "color": "#14b8a6", "module": "bmm", "moduleLabel": "BMM 业务动机模型", "group": "手段", "order": 9},
  "bmm:EnforcementLevel": {"label": "执行级别", "color": "#6366f1", "module": "bmm", "moduleLabel": "BMM 业务动机模型", "group": "执行级别", "order": 10},
  "bmm:ExpiringProduct": {"label": "即将到期的产品", "color": "#eab308", "module": "bmm", "moduleLabel": "BMM 业务动机模型", "group": "影响因素", "order": 11},
  "bmm:ExternalInfluencer": {"label": "外部影响因素", "color": "#3b82f6", "module": "bmm", "moduleLabel": "BMM 业务动机模型", "group": "影响因素", "order": 12},
  "bmm:FixedAsset": {"label": "固定资产", "color": "#10b981", "module": "bmm", "moduleLabel": "BMM 业务动机模型", "group": "影响因素", "order": 13},
  "bmm:Goal": {"label": "目的", "color": "#f59e0b", "module": "bmm", "moduleLabel": "BMM 业务动机模型", "group": "预期成果", "order": 14},
  "bmm:Influencer": {"label": "影响因素", "color": "#ef4444", "module": "bmm", "moduleLabel": "BMM 业务动机模型", "group": "影响因素", "order": 15},
  "bmm:InternalInfluencer": {"label": "内部影响因素", "color": "#8b5cf6", "module": "bmm", "moduleLabel": "BMM 业务动机模型", "group": "影响因素", "order": 16},
  "bmm:Liability": {"label": "责任", "color": "#06b6d4", "module": "bmm", "moduleLabel": "BMM 业务动机模型", "group": "责任", "order": 17},
  "bmm:Means": {"label": "手段", "color": "#84cc16", "module": "bmm", "moduleLabel": "BMM 业务动机模型", "group": "手段", "order": 18},
  "bmm:Objective": {"label": "目标", "color": "#ec4899", "module": "bmm", "moduleLabel": "BMM 业务动机模型", "group": "预期成果", "order": 19},
  "bmm:Offering": {"label": "对外交付物", "color": "#f97316", "module": "bmm", "moduleLabel": "BMM 业务动机模型", "group": "影响因素", "order": 20},
  "bmm:OperativeBusinessRule": {"label": "操作性业务规则", "color": "#14b8a6", "module": "bmm", "moduleLabel": "BMM 业务动机模型", "group": "手段", "order": 21},
  "bmm:OrganizationUnit": {"label": "组织机构", "color": "#6366f1", "module": "bmm", "moduleLabel": "BMM 业务动机模型", "group": "组织机构", "order": 22},
  "bmm:Product": {"label": "产品", "color": "#eab308", "module": "bmm", "moduleLabel": "BMM 业务动机模型", "group": "影响因素", "order": 23},
  "bmm:Resource": {"label": "资源", "color": "#3b82f6", "module": "bmm", "moduleLabel": "BMM 业务动机模型", "group": "影响因素", "order": 24},
  "bmm:StructuralBusinessRule": {"label": "结构性业务规则", "color": "#10b981", "module": "bmm", "moduleLabel": "BMM 业务动机模型", "group": "手段", "order": 25},
  "ea:APIService": {"label": "API服务", "color": "#06b6d4", "module": "ea", "moduleLabel": "EA 企业架构", "group": "IT服务", "order": 1000},
  "ea:Activity": {"label": "活动", "color": "#84cc16", "module": "ea", "moduleLabel": "EA 企业架构", "group": "活动", "order": 1001},
  "ea:Application": {"label": "应用系统", "color": "#ec4899", "module": "ea", "moduleLabel": "EA 企业架构", "group": "影响因素", "order": 1002},
  "ea:BusinessEntity": {"label": "业务实体", "color": "#f97316", "module": "ea", "moduleLabel": "EA 企业架构", "group": "业务实体", "order": 1003},
  "ea:BusinessRole": {"label": "业务角色", "color": "#14b8a6", "module": "ea", "moduleLabel": "EA 企业架构", "group": "业务角色", "order": 1004},
  "ea:Customer": {"label": "客户", "color": "#6366f1", "module": "ea", "moduleLabel": "EA 企业架构", "group": "业务实体", "order": 1005},
  "ea:CustomerWithExpiringProduct": {"label": "有到期产品的客户", "color": "#eab308", "module": "ea", "moduleLabel": "EA 企业架构", "group": "有到期产品的客户", "order": 1006},
  "ea:CustomerWithoutExpiringProduct": {"label": "无到期产品的客户", "color": "#3b82f6", "module": "ea", "moduleLabel": "EA 企业架构", "group": "无到期产品的客户", "order": 1007},
  "ea:HardwareAsset": {"label": "硬件资产", "color": "#10b981", "module": "ea", "moduleLabel": "EA 企业架构", "group": "影响因素", "order": 1008},
  "ea:ITAsset": {"label": "IT资产", "color": "#f59e0b", "module": "ea", "moduleLabel": "EA 企业架构", "group": "影响因素", "order": 1009},
  "ea:MCPService": {"label": "MCP服务", "color": "#ef4444", "module": "ea", "moduleLabel": "EA 企业架构", "group": "IT服务", "order": 1010},
  "ea:Service": {"label": "IT服务", "color": "#8b5cf6", "module": "ea", "moduleLabel": "EA 企业架构", "group": "IT服务", "order": 1011},
  "ea:SkillService": {"label": "Skill服务", "color": "#06b6d4", "module": "ea", "moduleLabel": "EA 企业架构", "group": "IT服务", "order": 1012},
  "ea:Step": {"label": "步骤", "color": "#84cc16", "module": "ea", "moduleLabel": "EA 企业架构", "group": "步骤", "order": 1013},
  "ea:Task": {"label": "任务", "color": "#ec4899", "module": "ea", "moduleLabel": "EA 企业架构", "group": "任务", "order": 1014},
  "easvc:BusinessAttribute": {"label": "业务属性", "color": "#6366f1", "module": "ea-service", "moduleLabel": "EA 服务契约扩展", "group": "业务属性", "order": 2000},
  "easvc:FunctionalDependency": {"label": "函数依赖", "color": "#eab308", "module": "ea-service", "moduleLabel": "EA 服务契约扩展", "group": "函数依赖", "order": 2001},
  "easvc:ServiceContract": {"label": "服务契约", "color": "#3b82f6", "module": "ea-service", "moduleLabel": "EA 服务契约扩展", "group": "服务契约", "order": 2002},
  "easvc:ServiceOperation": {"label": "服务操作", "color": "#10b981", "module": "ea-service", "moduleLabel": "EA 服务契约扩展", "group": "服务操作", "order": 2003},
  "easvc:SideEffect": {"label": "副作用", "color": "#f59e0b", "module": "ea-service", "moduleLabel": "EA 服务契约扩展", "group": "副作用", "order": 2004},
  "eaown:ControlRelation": {"label": "控制关系", "color": "#ef4444", "module": "ea-ownership", "moduleLabel": "EA 所有权与控制关系扩展", "group": "控制关系", "order": 3000},
  "eaown:LegalEntity": {"label": "法人主体", "color": "#8b5cf6", "module": "ea-ownership", "moduleLabel": "EA 所有权与控制关系扩展", "group": "业务实体", "order": 3001},
  "eaown:OwnershipRelation": {"label": "股权关系", "color": "#06b6d4", "module": "ea-ownership", "moduleLabel": "EA 所有权与控制关系扩展", "group": "股权关系", "order": 3002},
  "eaown:Person": {"label": "自然人", "color": "#84cc16", "module": "ea-ownership", "moduleLabel": "EA 所有权与控制关系扩展", "group": "自然人", "order": 3003},
  "bmmfd:AccessControlRule": {"label": "访问控制规则", "color": "#f97316", "module": "bmm-fd", "moduleLabel": "BMM 规则可执行化扩展", "group": "手段", "order": 4000},
  "bmmfd:DataQualityRule": {"label": "数据质量规则", "color": "#14b8a6", "module": "bmm-fd", "moduleLabel": "BMM 规则可执行化扩展", "group": "手段", "order": 4001},
  "ontology:Class": {"label": "本体类", "color": "#2563eb", "module": "ontology", "moduleLabel": "本体模型", "group": "本体类", "order": 90002},
  "ontology:LightDoc": {"label": "轻量版提示词", "color": "#a16207", "module": "ontology", "moduleLabel": "本体模型", "group": "轻量版", "order": 90005},
  "ontology:Module": {"label": "本体模块", "color": "#0f766e", "module": "ontology", "moduleLabel": "本体模型", "group": "模块", "order": 90001},
  "ontology:Property": {"label": "本体属性", "color": "#0891b2", "module": "ontology", "moduleLabel": "本体模型", "group": "本体属性", "order": 90004},
  "ontology:Relation": {"label": "本体关系", "color": "#7c3aed", "module": "ontology", "moduleLabel": "本体模型", "group": "本体关系", "order": 90003},
};

export const ONTOLOGY_MODULES: { key: string; label: string; order: number }[] = [
  {
    "key": "bmm",
    "label": "BMM 业务动机模型",
    "order": 0
  },
  {
    "key": "ea",
    "label": "EA 企业架构",
    "order": 1
  },
  {
    "key": "ea-service",
    "label": "EA 服务契约扩展",
    "order": 2
  },
  {
    "key": "ea-ownership",
    "label": "EA 所有权与控制关系扩展",
    "order": 3
  },
  {
    "key": "bmm-fd",
    "label": "BMM 规则可执行化扩展",
    "order": 4
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
  return !!pageType && !!ONTOLOGY_TYPES[pageType];
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
