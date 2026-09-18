"""BMM 图谱数据模型 — Pydantic 定义"""

from __future__ import annotations
from datetime import date, datetime
from enum import Enum
from typing import Optional, List
from pydantic import BaseModel, Field


# ============================================================
# 枚举类型
# ============================================================

class EnforcementLevel(str, Enum):
    STRICT = "Strict"
    OVERRIDE = "Override"
    ADVISORY = "Advisory"


class AssessmentType(str, Enum):
    STRENGTH = "Strength"
    WEAKNESS = "Weakness"
    OPPORTUNITY = "Opportunity"
    THREAT = "Threat"


# ============================================================
# 基础模型
# ============================================================

class SourceInfo(BaseModel):
    """来源信息 — 关联原文摘录与文档"""
    document_id: str = Field(..., description="来源文档ID")
    document_title: str = Field("", description="文档标题")
    excerpt_id: str = Field(..., description="原文摘录ID")
    excerpt_text: str = Field(..., description="原文内容")
    source_position: str = Field("", description="来源位置（章节/条目）")
    is_authoritative: bool = Field(False, description="是否为权威来源")


class BmmNode(BaseModel):
    """BMM 图谱节点基类（node_type 为带模型前缀的类型标识，如 bmm:Goal）"""
    id: Optional[str] = Field(None, description="节点唯一ID")
    node_type: str = Field("", description="节点类型（前缀:名称）")
    name: str = Field(..., description="名称")
    english_name: Optional[str] = Field(None, description="英文名称")
    definition: Optional[str] = Field(None, description="定义")
    description: Optional[str] = Field(None, description="描述")
    ai_skill: Optional[str] = Field(None, description="AI技能/工具定义（JSON）")
    extra: dict = Field(default_factory=dict, description="类型扩展属性")
    sources: List[SourceInfo] = Field(default_factory=list, description="来源信息列表")
    created_at: Optional[datetime] = Field(None, description="创建时间")
    updated_at: Optional[datetime] = Field(None, description="更新时间")


class BmmRelationship(BaseModel):
    """BMM 图谱关系"""
    id: Optional[str] = Field(None, description="关系ID")
    rel_type: str = Field("", description="关系类型（前缀:名称）")
    source_node_id: str = Field(..., description="起始节点ID")
    target_node_id: str = Field(..., description="目标节点ID")
    sources: List[SourceInfo] = Field(default_factory=list, description="来源信息")
    created_at: Optional[datetime] = Field(None)


# ============================================================
# 结构型节点模型（文档/摘录，非知识节点）
# ============================================================

class SourceDocumentNode(BmmNode):
    """来源文档节点（结构型，非知识节点）"""
    document_title: Optional[str] = Field(None, description="文档标题")
    document_id: Optional[str] = Field(None, description="文档标识")
    publication_date: Optional[date] = Field(None, description="发布日期")


class ExcerptNode(BmmNode):
    """原文摘录节点（结构型，非知识节点）"""
    text: str = Field(..., description="原文文本")
    source_position: Optional[str] = Field(None, description="来源位置")
    document_id: str = Field(..., description="所属文档ID")
    document_title: Optional[str] = Field(None, description="所属文档标题")


# ============================================================
# 请求/响应模型
# ============================================================

class ExtractionResult(BaseModel):
    """文档提取结果"""
    nodes: List[BmmNode] = Field(default_factory=list, description="识别出的节点")
    relationships: List[BmmRelationship] = Field(default_factory=list, description="识别出的关系")
    excerpts: List[ExcerptNode] = Field(default_factory=list, description="原文摘录")


class SimilarNode(BaseModel):
    """相似节点信息"""
    node: BmmNode
    similarity_score: float = Field(..., description="相似度分数")
    existing_sources: List[SourceInfo] = Field(default_factory=list)


class MergeCandidate(BaseModel):
    """合并候选"""
    new_node: BmmNode
    similar_nodes: List[SimilarNode] = Field(default_factory=list)
    suggested_action: str = Field("", description="建议操作: create_new / merge / skip")


class ReviewDecision(BaseModel):
    """审核决策"""
    entity_id: str = Field(..., description="实体ID")
    action: str = Field(..., description="create / update / skip")
    merged_into_id: Optional[str] = Field(None, description="合并到哪个已有节点")
    set_authoritative: bool = Field(False, description="是否设为权威来源")
    comment: Optional[str] = Field(None, description="审核备注")


class ReviewBatchRequest(BaseModel):
    """批量审核请求"""
    decisions: List[ReviewDecision] = Field(default_factory=list)


class DocumentUploadResponse(BaseModel):
    """文档上传响应"""
    document_id: str
    document_title: str
    extraction: ExtractionResult
    merge_candidates: List[MergeCandidate]
