"""相似度检测与人工审核服务"""

from __future__ import annotations
import uuid
from typing import List, Optional
import numpy as np

from ..config import settings
from ..models.bmm_models import (
    BmmNode, BmmRelationship, SourceInfo, SourceDocumentNode, ExcerptNode,
    ExtractionResult, SimilarNode, MergeCandidate, ReviewDecision, ReviewBatchRequest,
)
from .graph_service import graph_service


# 延迟加载嵌入模型
_embedding_model = None


def _get_embedding_model():
    """延迟加载 SentenceTransformer 模型"""
    global _embedding_model
    if _embedding_model is None:
        from sentence_transformers import SentenceTransformer
        _embedding_model = SentenceTransformer(settings.embedding_model)
    return _embedding_model


class SimilarityService:
    """计算新提取节点与已有节点的相似度"""

    def find_similar_nodes(self, new_node: BmmNode) -> List[SimilarNode]:
        """查找与 new_node 相似的已有节点"""

        # 获取同类型的已有节点
        existing = graph_service.search_nodes(
            node_type=new_node.node_type, limit=200
        )

        if not existing:
            return []

        # 获取所有节点名（含新节点）
        all_names = [n.name for n in existing] + [new_node.name]
        all_defs = [(n.definition or "") for n in existing] + [new_node.definition or ""]

        # 使用嵌入模型计算语义相似度
        try:
            model = _get_embedding_model()
            name_embeddings = model.encode(all_names)
            def_embeddings = model.encode(all_defs)

            # 新节点在最后
            new_name_emb = name_embeddings[-1]
            new_def_emb = def_embeddings[-1]

            similar = []
            for i, ex_node in enumerate(existing):
                name_sim = float(np.dot(name_embeddings[i], new_name_emb) /
                                 (np.linalg.norm(name_embeddings[i]) * np.linalg.norm(new_name_emb) + 1e-10))
                def_sim = float(np.dot(def_embeddings[i], new_def_emb) /
                                (np.linalg.norm(def_embeddings[i]) * np.linalg.norm(new_def_emb) + 1e-10))

                # 综合相似度
                combined = name_sim * 0.6 + def_sim * 0.4

                if combined >= settings.similarity_threshold:
                    sources = graph_service.get_node_sources(ex_node.id)
                    similar.append(SimilarNode(
                        node=ex_node,
                        similarity_score=round(combined, 4),
                        existing_sources=sources,
                    ))

            # 按相似度排序
            similar.sort(key=lambda x: x.similarity_score, reverse=True)
            return similar

        except Exception as e:
            print(f"Similarity computation error: {e}")
            # 降级到简单关键词匹配
            return self._fallback_similarity(new_node, existing)

    def _fallback_similarity(self, new_node: BmmNode,
                              existing: List[BmmNode]) -> List[SimilarNode]:
        """关键词降级匹配"""
        similar = []
        new_words = set(new_node.name)
        for ex_node in existing:
            ex_words = set(ex_node.name)
            if not new_words or not ex_words:
                continue
            jaccard = len(new_words & ex_words) / len(new_words | ex_words)
            if jaccard >= 0.5:
                sources = graph_service.get_node_sources(ex_node.id)
                similar.append(SimilarNode(
                    node=ex_node,
                    similarity_score=round(jaccard, 4),
                    existing_sources=sources,
                ))
        return sorted(similar, key=lambda x: x.similarity_score, reverse=True)

    def build_merge_candidates(self, extraction: ExtractionResult) -> List[MergeCandidate]:
        """为提取的所有节点构建合并候选列表"""
        candidates = []

        for new_node in extraction.nodes:
            similar = self.find_similar_nodes(new_node)

            if not similar:
                action = "create_new"
            elif similar[0].similarity_score >= 0.95:
                action = "merge"
            else:
                action = "review"

            candidates.append(MergeCandidate(
                new_node=new_node,
                similar_nodes=similar,
                suggested_action=action,
            ))

        return candidates


class ReviewService:
    """人工审核工作流 — 处理审核决策"""

    def __init__(self):
        self.similarity_service = SimilarityService()

    def process_review(self, decisions: ReviewBatchRequest) -> dict:
        """处理一批审核决策"""
        results = {
            "created": [],
            "updated": [],
            "merged": [],
            "skipped": [],
            "errors": [],
        }

        for decision in decisions.decisions:
            try:
                if decision.action == "create":
                    self._handle_create(decision, results)
                elif decision.action == "update":
                    self._handle_update(decision, results)
                elif decision.action == "merge":
                    self._handle_merge(decision, results)
                elif decision.action == "skip":
                    results["skipped"].append(decision.entity_id)
            except Exception as e:
                results["errors"].append({
                    "entity_id": decision.entity_id,
                    "error": str(e),
                })

        return results

    def _handle_create(self, decision: ReviewDecision, results: dict):
        """创建新节点"""
        node = graph_service.get_node(decision.entity_id)
        if not node:
            # 可能还没持久化，尝试从临时存储获取
            results["errors"].append({
                "entity_id": decision.entity_id,
                "error": "未找到待创建的节点",
            })
            return

        results["created"].append({
            "id": node.id,
            "name": node.name,
            "type": node.node_type,
        })

        # 如果指定了权威来源
        if decision.set_authoritative and node.sources:
            auth_source = node.sources[0]
            graph_service.set_authoritative_source(
                node.id, auth_source.document_id, auth_source.excerpt_id
            )

    def _handle_update(self, decision: ReviewDecision, results: dict):
        """更新已有节点（添加新的来源引用）"""
        target_id = decision.merged_into_id or decision.entity_id
        existing = graph_service.get_node(target_id)
        new_node = graph_service.get_node(decision.entity_id)

        if not existing:
            results["errors"].append({
                "entity_id": decision.entity_id,
                "error": f"目标节点 {target_id} 不存在",
            })
            return

        # 将新节点的来源添加到已有节点
        if new_node and new_node.sources:
            for source in new_node.sources:
                graph_service.link_source(target_id, source)

        # 如果设为权威来源
        if decision.set_authoritative and new_node and new_node.sources:
            auth_source = new_node.sources[0]
            graph_service.set_authoritative_source(
                target_id, auth_source.document_id, auth_source.excerpt_id
            )

        results["updated"].append({
            "id": target_id,
            "name": existing.name,
            "type": existing.node_type,
        })

    def _handle_merge(self, decision: ReviewDecision, results: dict):
        """合并节点 — 将新节点合并到已有节点"""
        target_id = decision.merged_into_id
        if not target_id:
            results["errors"].append({
                "entity_id": decision.entity_id,
                "error": "合并操作需要指定 merged_into_id",
            })
            return

        existing = graph_service.get_node(target_id)
        new_node = graph_service.get_node(decision.entity_id)

        if not existing:
            results["errors"].append({
                "entity_id": decision.entity_id,
                "error": f"目标节点 {target_id} 不存在",
            })
            return

        # 转移所有关系
        if new_node:
            relationships = graph_service.get_node_relationships(new_node.id)
            for rel in relationships:
                if rel.get("target_id") != target_id:
                    new_rel = BmmRelationship(
                        id=f"rel_{uuid.uuid4().hex[:12]}",
                        rel_type=rel["rel_type"],
                        source_node_id=target_id,
                        target_node_id=rel["target_id"],
                    )
                    graph_service.create_relationship(new_rel)

            # 转移来源
            for source in new_node.sources:
                graph_service.link_source(target_id, source)

            # 如果设为权威来源
            if decision.set_authoritative and new_node.sources:
                auth_source = new_node.sources[0]
                graph_service.set_authoritative_source(
                    target_id, auth_source.document_id, auth_source.excerpt_id
                )

            # 删除旧节点
            graph_service.delete_node(new_node.id)

        results["merged"].append({
            "source_id": decision.entity_id,
            "target_id": target_id,
            "target_name": existing.name,
        })

    def compare_nodes(self, node_id1: str, node_id2: str) -> dict:
        """对比两个节点的原文"""
        node1 = graph_service.get_node(node_id1)
        node2 = graph_service.get_node(node_id2)

        if not node1 or not node2:
            return {"error": "节点不存在"}

        sources1 = graph_service.get_node_sources(node_id1)
        sources2 = graph_service.get_node_sources(node_id2)

        return {
            "node1": {
                "id": node1.id,
                "name": node1.name,
                "type": node1.node_type,
                "sources": [s.model_dump() for s in sources1],
            },
            "node2": {
                "id": node2.id,
                "name": node2.name,
                "type": node2.node_type,
                "sources": [s.model_dump() for s in sources2],
            },
        }


similarity_service = SimilarityService()
review_service = ReviewService()
