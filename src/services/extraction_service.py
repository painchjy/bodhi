"""BMM 实体提取服务 — 使用 LLM 从文档中提取 BMM 模型要素"""

from __future__ import annotations
import json, re
import uuid
from typing import List, Optional
from openai import OpenAI

from ..config import settings
from ..models.bmm_models import (
    ExtractionResult, BmmNode, BmmRelationship, SourceInfo,
    ExcerptNode, SourceDocumentNode,
)
import httpx
from pathlib import Path as P


def _build_instruction(model_label: str = "", expert_role: str = "") -> str:
    """根据本体模型配置生成对应提取指令"""
    role = expert_role or "企业架构分析专家"
    label = model_label or "BMM 业务动机"
    return f"""

## 提取指令

你是一个{role}。以上为 {label} 本体模型定义。

请从下方用户提示词列出的文档摘录中，识别 {label} 模型要素（elements）和关系（relationships）。

输出严格的 JSON 格式：
{{
  "elements": [
    {{
      "action": "create 或 merge",
      "existing_id": "合并时填已有要素id，新建时为null",
      "type": "要素类型",
      "name": "要素名称",
      "definition": "简要定义",
      "description": "从原文中识别该要素的理由",
      "excerpt_id": "提取该要素的来源摘录id（必须填）",
      "source_text": "支持该要素提取的原文片段",
      "ai_skill": "IT 服务的 AI 技能定义（JSON 字符串，仅 Service 类型填写，其它类型填空字符串）",
      "extra": {{}}
    }}
  ],
  "relationships": [
    {{
      "type": "关系类型（从本体定义中选取）",
      "source_element_index": 0,
      "target_element_index": 1,
      "excerpt_id": "提取该关系的摘录id",
      "source_text": "支持该关系提取的原文片段"
    }}
  ]
}}

注意：
- 要素只从摘录内容中提取，action 可选 create 或 merge
- 关系可以连接：新要素↔新要素（用 source/target_element_index）、新要素↔已有要素（用 source/target_existing_id）
- 已有要素的 id 从下方\"知识图谱已有要素\"列表获取
- 关系也可关联已有要素到已有要素（都用 existing_id），只要摘录语义支持
- excerpt_id 必须从用户提示词提供的 [摘录id] 中选取
- source_text 必须从该摘录文本中逐字引用，做好 JSON 转义
- ai_skill 仅适用于 Service（IT服务）类型要素：当 IT 服务的描述采用"输入参数…… 预期结果……"结构时，按本体中 ai_skill 属性的格式转换为 JSON 字符串填入（name/description/parameters/expected_results），其它类型填空字符串 ""
- 只输出 JSON，禁止输出任何解释性文字、分析过程或 Markdown 代码块
- 如果本次摘录中没有任何新要素或新关系可提取，必须输出：{{"elements": [], "relationships": [],"reasons": "返回空的简要原因"}}

## 对比规则：
已有要素列表见用户提示词，语义相同或高度相似 → merge（填 existing_id），否则 create。关系在摘录支持时创建。
"""


def _build_annotate_instruction(model_label: str = "", expert_role: str = "") -> str:
    """自动标注摘录的系统提示词：知识标注优先，本体标注兜底"""
    role = expert_role or "企业架构领域的知识标注助手"
    label = model_label or "BMM 业务动机"
    return f"""
## 标注指令

你是一个{role}。以上为 {label} 本体模型定义。

你的任务是对用户提示词中给出的文档摘录进行 Wiki 标注，帮助读者理解摘录中的专业词汇。

标注规则：
1. 只对摘录正文中出现的词汇进行标注，不得增删、改写、遗漏摘录的原文内容（包括"📍 来源位置"行、所有段落与换行）。
2. 第一优先——知识标注：摘录中能用"已有知识"解释的词汇，包裹为知识链接：
   [词汇](./kn/类型/名称)
   其中"类型"为该知识节点的 node_type（把冒号:替换为点号.），"名称"为该知识节点的 name。
3. 第二优先——本体标注：摘录中重要、专业，但"已有知识"里找不到对应解释的词汇，用最贴近的本体概念标注其归属：
   [概念](./onto/模型前缀:概念名)
   例如：[头寸监控](./onto/bmm:BusinessProcess)。本体概念必须从用户提示词给出的"可用本体概念"列表中选择，禁止编造。
4. 不要过度标注：
  4.1 知识只标注关键术语、领域名词、缩写、专有名词；普通动词、连词、数量词、标点不标注。
  4.2 本体标注要尽量选择完整的短语或句子，完整表达概念的定义要求的语义，不要标注简单的术语。
5. 同一词汇在整段摘录中只标注首次出现即可。
6. 保留原文换行与段落结构，输出内容应与原文一一对应。

输出严格的 JSON 格式（禁止输出 Markdown 代码块、禁止任何解释性文字）：
{{
  "excerpts": [
    {{"excerpt_id": "摘录id（必须与用户提示词中的 [摘录id] 一致）", "text": "标注后的完整 markdown 文本"}}
  ]
}}
"""


class _DbModelCompat:
    """图库本体模型的轻量兼容对象，用于替换 .env 模型配置（label / expert_role）"""
    def __init__(self, label: str = "", expert_role: str = ""):
        self.label = label
        self.expert_role = expert_role


class ExtractionService:
    """使用 LLM 从文档中提取 BMM 要素"""

    def __init__(self):
        self.client: Optional[OpenAI] = None
        self._last_model_name = ""

    def _get_client(self) -> OpenAI:
        if self.client is None:
            self.client = OpenAI(
                api_key=settings.llm_api_key,
                base_url=settings.llm_base_url,
            )
        return self.client

    def _get_concept_map(self, model_name: str = "", use_full: bool = False) -> dict:
        """从本体图库动态构建概念映射（{父类型: [直接子类型]}）"""
        from .ontology_service import ontology_service
        try:
            cm = ontology_service.get_concept_map(model_name)
            if cm:
                return cm
        except Exception:
            pass
        return {}

    def _find_env_config(self, model_name: str = ""):
        """按名称或图库源文件匹配 .env 模型配置"""
        if model_name:
            mc = settings.get_ontology_model(model_name)
            if mc:
                return mc
            try:
                from .ontology_service import ontology_service
                m = ontology_service.get_model(model_name)
                if m and m.get("source_file"):
                    for cfg in settings.ontology_models:
                        if m["source_file"] in (cfg.full_file, cfg.light_file):
                            return cfg
            except Exception:
                pass
        return None

    def _resolve_model_file(self, model_name: str = "", use_full: bool = False):
        """解析本体文件路径：优先 .env（区分完整版/轻量版），图库兜底"""
        # 1) .env 配置优先（可按名称或图库源文件匹配）
        mc = self._find_env_config(model_name)
        if mc:
            rel = mc.get_file(use_full)
            if rel:
                fp = P(settings.ontology_dir) / rel
                if not fp.exists():
                    fp = P(rel)
                if fp.exists():
                    return str(fp), mc
        # 2) 图库兜底（无轻量版区分，用入库的源文件）
        try:
            from .ontology_service import ontology_service
            m = ontology_service.get_model(model_name) if model_name else None
            if m and m.get("source_file"):
                fp = P(settings.ontology_dir) / m["source_file"]
                if not fp.exists():
                    fp = P(m["source_file"])
                if fp.exists():
                    return str(fp), _DbModelCompat(m.get("label", ""), "")
        except Exception:
            pass
        # 3) 最终兜底：第一个 .env 模型
        models = settings.ontology_models
        if models:
            mc = models[0]
            rel = mc.get_file(use_full)
            fp = P(settings.ontology_dir) / rel
            if not fp.exists():
                fp = P(rel)
            if fp.exists():
                return str(fp), mc
        return "", None

    def _load_ontology(self, model_name: str = "", use_full: bool = False) -> str:
        """加载本体文件内容作为系统提示词"""
        fp, mc = self._resolve_model_file(model_name, use_full)
        ont_text = ""
        if fp:
            ont_text = P(fp).read_text(encoding="utf-8")
        return ont_text + _build_instruction(
            model_label=mc.label if mc else "",
            expert_role=mc.expert_role if mc else "",
        )
    def _load_annotate_ontology(self, model_name: str = "", use_full: bool = False) -> str:
        """加载本体文件内容作为系统提示词"""
        fp, mc = self._resolve_model_file(model_name, use_full)
        ont_text = ""
        if fp:
            ont_text = P(fp).read_text(encoding="utf-8")
        return ont_text + _build_annotate_instruction(
            model_label=mc.label if mc else "",
            expert_role=mc.expert_role if mc else "",
        )
    def build_prompt(self, doc_node, excerpts, concept_filters, rel_scopes,
                     model_name: str = "", use_full: bool = False) -> dict:
        """构建提示词：系统=本体定义，用户=筛选条件+摘录内容+已有要素"""
        concept_filters = ([concept_filters] if isinstance(concept_filters, str) else (concept_filters or []))
        # 动态概念映射
        cm = self._get_concept_map(model_name, use_full)
        # 系统提示词：本体定义（可缓存）
        sp = self._load_ontology(model_name, use_full)
        # 用户提示词：筛选条件 + 已有要素 + 摘录
        parts = []
        if concept_filters:
            from .ontology_service import ontology_service
            labels = [ontology_service.node_label(c) for c in concept_filters if c]
            if labels: parts.append(f"提取范围: {', '.join(labels)}")
        ec = self._build_existing_context(concept_filters, [], cm, doc_node.document_id or "")
        if ec: parts.append(ec)
        # 按文档分组列出摘录，每组标注文档名称与文档ID
        doc_groups = {}  # document_id -> {"title": str, "items": []}
        for exc in excerpts:
            did = exc.document_id or ""
            g = doc_groups.setdefault(did, {"title": (exc.document_title if hasattr(exc, "document_title") and exc.document_title else "") or doc_node.name or "", "items": []})
            g["items"].append(exc)
        doc_blocks = []
        for did, g in doc_groups.items():
            header = f"### 文档: {g['title'] or '未知文档'} (document_id: {did or '未知'})"
            items = "\n\n---\n\n".join(
                f"[摘录id: {exc.id}]\n{exc.text}"
                for exc in g["items"]
            )
            doc_blocks.append(header + "\n\n" + items)
        parts.append("## 文档摘录\n\n" + "\n\n".join(doc_blocks))
        up = "\n\n".join(parts)
        # 保存当前概念映射与模型，供 preview_extraction / commit 使用
        self._last_concept_map = cm
        self._last_model_name = model_name or ""
        return {"system_prompt": sp, "user_prompt": up, "graph_context": ec,
                "excerpt_count": len(excerpts)}

    async def preview_extraction(
        self, doc_node, excerpts, concept_filters=None, rel_scopes=None,
        system_prompt="", user_prompt="",
    ) -> dict:
        concept_filters = ([concept_filters] if isinstance(concept_filters, str) else (concept_filters or []))
        sp = system_prompt or _build_instruction("")
        up = user_prompt or ("## Doc: " + doc_node.name)
        client = self._get_client()

        messages = [{"role": "system", "content": sp},
                    {"role": "user", "content": up}]
        print(f"[Bodhi]   🤖 LLM ({settings.llm_model}), {len(excerpts)} excerpts...", flush=True)
        resp = client.chat.completions.create(
            model=settings.llm_model,
            messages=messages,
            temperature=settings.llm_temperature, max_tokens=settings.llm_max_tokens,
            response_format={"type": "json_object"},
        )
        finish = resp.choices[0].finish_reason
        print(f"[Bodhi]   📡 finish_reason={finish}, usage={resp.usage}", flush=True)
        raw = resp.choices[0].message.content
        rd, parse_ok = self._safe_json_parse(raw)
        #记录提取要素的LLM调用调式信息
        self._save_log(messages, raw, finish, resp.usage, method='extraction')
        fd = self._filter_result(rd, concept_filters, rel_scopes,
                                 getattr(self, '_last_concept_map', None))
        el = fd.get("elements",[]); rl = fd.get("relationships",[])
        return {
            "raw_json": rd, "filtered_json": fd,
            "stats": {
                "total_elements": len(rd.get("elements",[])),
                "total_relationships": len(rd.get("relationships",[])),
                "new_nodes": sum(1 for e in el if e.get("action")!="merge"),
                "merge_nodes": sum(1 for e in el if e.get("action")=="merge"),
                "filtered_nodes": len(rd.get("elements",[]))-len(el),
                "filtered_relationships": len(rd.get("relationships",[]))-len(rl),
                "final_nodes": len(el), "final_relationships": len(rl),
            }
        }

    def _build_ontology_concepts_context(self, model_name: str = "") -> str:
        """列出可用的本体概念（短URI + 标签），供自动标注选择"""
        from .ontology_service import ontology_service
        try:
            types = ontology_service.get_node_types(model_name)
        except Exception:
            types = []
        if not types:
            return "## 可用本体概念\n（无）"
        lines = ["## 可用本体概念（标注 [词汇](./onto/前缀:概念名) 时只能从下列选取）"]
        for t in types[:200]:
            lines.append(f"- {t['name']}（{t.get('label') or t['name']}）")
        return "\n".join(lines)

    def build_annotate_prompt(self, doc_node, excerpts, concept_filters=None,
                              model_name: str = "", use_full: bool = False) -> dict:
        """构建自动标注提示词：系统=本体定义+标注指令，用户=可用概念+已有知识+摘录"""
        concept_filters = ([concept_filters] if isinstance(concept_filters, str) else (concept_filters or []))
        cm = self._get_concept_map(model_name, use_full)
        sp = self._load_annotate_ontology(model_name, use_full)
        parts = []
        parts.append(self._build_ontology_concepts_context(model_name))
        # 已有知识筛选与知识提取一致（按概念范围 + 来源文档过滤）
        ec = self._build_existing_context(concept_filters, [], cm, doc_node.document_id or "",
                                          section_title="知识图谱已有知识（用于解释摘录词汇）", empty_text="无")
        parts.append(ec)
        # 按文档分组列出摘录，每组标注文档名称与文档ID
        doc_groups = {}
        for exc in excerpts:
            did = exc.document_id or ""
            g = doc_groups.setdefault(did, {"title": (exc.document_title if hasattr(exc, "document_title") and exc.document_title else "") or doc_node.name or "", "items": []})
            g["items"].append(exc)
        doc_blocks = []
        for did, g in doc_groups.items():
            header = f"### 文档: {g['title'] or '未知文档'} (document_id: {did or '未知'})"
            items = "\n\n---\n\n".join(
                f"[摘录id: {exc.id}]\n{exc.text}"
                for exc in g["items"]
            )
            doc_blocks.append(header + "\n\n" + items)
        parts.append("## 文档摘录\n\n" + "\n\n".join(doc_blocks))
        up = "\n\n".join(parts)
        self._last_concept_map = cm
        self._last_model_name = model_name or ""
        return {"system_prompt": sp, "user_prompt": up, "graph_context": ec,
                "excerpt_count": len(excerpts)}

    async def annotate_preview(self, doc_node, excerpts, system_prompt="", user_prompt="") -> dict:
        """调用 LLM 自动标注摘录，返回每个摘录的 markdown 结果"""
        sp = system_prompt or _build_annotate_instruction()
        up = user_prompt or ("## Doc: " + doc_node.name)
        client = self._get_client()
        messages = [{"role": "system", "content": sp},
                    {"role": "user", "content": up}]
        print(f"[Bodhi]   🏷️ LLM 自动标注 ({settings.llm_model}), {len(excerpts)} excerpts...", flush=True)
        resp = client.chat.completions.create(
            model=settings.llm_model,
            messages=messages,
            temperature=settings.llm_temperature, max_tokens=settings.llm_max_tokens,
            response_format={"type": "json_object"},
        )
        finish = resp.choices[0].finish_reason
        print(f"[Bodhi]   📡 finish_reason={finish}, usage={resp.usage}", flush=True)
        raw = resp.choices[0].message.content
        rd, parse_ok = self._safe_json_parse(raw)
        self._save_log(messages, raw, finish, resp.usage, method='annotate')
        valid = []
        if isinstance(rd, dict):
            for it in rd.get("excerpts", []) or []:
                if isinstance(it, dict) and it.get("excerpt_id") and it.get("text"):
                    valid.append({"excerpt_id": it["excerpt_id"], "text": it["text"]})
        return {
            "raw": raw,
            "excerpts": valid,
            "parse_ok": parse_ok,
            "stats": {"total": len(excerpts), "annotated": len(valid)},
        }

    def commit_extraction(self, doc_node, excerpts, result_json):
        return self._parse_extraction_result(result_json, doc_node, excerpts, 0)


    def _build_existing_context(self, concept_filters, _unused, concept_map: dict = None, document_id: str = "",
                                section_title: str = "", empty_text: str = "") -> str:
        """查询图谱已有节点，构建对比上下文（可按来源文档与概念范围过滤）"""
        from ..services.graph_service import graph_service as gs
        cm = concept_map or {}
        concept_filters = ([concept_filters] if isinstance(concept_filters, str) else (concept_filters or []))
        # 展开概念大类为具体类型
        allowed = set()
        for cf in concept_filters:
            if cf in cm:
                allowed.update(cm[cf])
            elif cf:
                allowed.add(cf)
        # 若指定了来源文档，仅保留与该文档关联的存量节点
        doc_node_ids = None
        if document_id:
            doc_node_ids = gs.get_doc_node_ids(document_id)
        existing = gs.get_all_nodes(limit=500)
        items = []
        for n in existing:
            if doc_node_ids is not None and n.id not in doc_node_ids:
                continue
            if allowed and n.node_type not in allowed:
                continue
            items.append({
                "id": n.id, "type": n.node_type,
                "name": n.name, "definition": n.definition or "",
            })
        if not items:
            return f"## {section_title or '知识图谱已有要素'}\n（{empty_text or '无，所有识别均为新建'}）"
        lines = [f"## {section_title or '知识图谱已有要素（请对比以下要素决定 create 还是 merge）'}"]
        for it in items[:180]:
            lines.append(f"- [{it['type']}] id={it['id']} | {it['name']} | {it['definition'][:560]}")
        return "\n".join(lines)

    def _safe_json_parse(self, content: str) -> tuple:
        """安全解析 LLM 返回的 JSON，返回 (结果, 是否解析成功)"""
        content = content.strip() if content else ""
        # 去除 markdown 代码块标记 ```json ... ```
        m = re.search(r'```(?:json)?\s*\n(.*?)\n\s*```', content, re.DOTALL)
        if m:
            content = m.group(1).strip()
        # 先尝试直接解析
        try:
            return (json.loads(content), True)
        except json.JSONDecodeError as e:
            print(f"[Bodhi]   🔧 JSON 解析失败 ({e})，尝试修复...", flush=True)

        # 尝试补全截断的 JSON：找到最后一个完整的元素
        content = content.strip()
        if not content.endswith("}"):
            last_brace = content.rfind('}')
            if last_brace > 0:
                content = content[:last_brace + 1]

        if content.endswith(","):
            content = content[:-1]
        if content.endswith('"') and not content.endswith('}"'):
            content = content[:-1]

        open_braces = content.count('{') - content.count('}')
        open_brackets = content.count('[') - content.count(']')
        content += ']' * open_brackets + '}' * open_braces

        try:
            result = json.loads(content)
            print(f"[Bodhi]   ✅ JSON 修复成功", flush=True)
            return (result, True)
        except json.JSONDecodeError as e:
            print(f"[Bodhi]   ❌ JSON 修复失败: {e}", flush=True)
            return ({"elements": [], "relationships": []}, False)

    def _save_log(self, messages: list, raw_response: str, finish_reason: str = "", usage=None,method='extraction'):
        """保存LLM提取调试日志"""
        from datetime import datetime
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        fn = f"logs/{method}_{ts}.log"
        with open(fn, "w", encoding="utf-8") as f:
            f.write(f"=== 提取调试日志 {ts} ===\n\n")
            f.write(f"finish_reason: {finish_reason}\n")
            f.write(f"usage: {usage}\n\n")
            for i, msg in enumerate(messages):
                f.write(f"--- Message[{i}] role={msg['role']} ---\n")
                f.write(msg["content"])
                f.write("\n\n")
            f.write("--- LLM 原始返回 ---\n")
            f.write(raw_response or "(空)")
            f.write("\n")
        print(f"[Bodhi]   📝 LLM调试日志已保存: {fn}", flush=True)

    def _filter_result(self, data: dict, concept_filters, _unused, concept_map: dict = None) -> dict:
        cm = concept_map or {}
        concept_filters = ([concept_filters] if isinstance(concept_filters, str) else (concept_filters or []))
        if not concept_filters:
            return data

        allowed_types = set()
        for cf in concept_filters:
            if cf in cm:
                allowed_types.update(cm[cf])
            elif cf:
                allowed_types.add(cf)

        # 过滤要素（允许类型按本地名比较，兼容 LLM 返回的带/不带前缀类型名）
        allowed_local = {t.split(":")[-1] for t in allowed_types}
        elements = data.get("elements", [])
        kept_indices = set()
        filtered_elements = []
        for idx, elem in enumerate(elements):
            raw_type = elem.get("type", "")
            norm_type = raw_type.split(":")[-1] if ":" in raw_type else raw_type
            if not allowed_types or norm_type in allowed_local or raw_type in allowed_types:
                filtered_elements.append(elem)
                kept_indices.add(idx)

        # 过滤关系：允许新↔新、新↔存量、存量↔存量
        rels = data.get("relationships", [])
        filtered_rels = []
        for rel in rels:
            si = rel.get("source_element_index")
            ti = rel.get("target_element_index")
            si_eid = rel.get("source_existing_id")
            ti_eid = rel.get("target_existing_id")
            # 源端：index 在保留范围内，或有 existing_id
            si_ok = (si is not None and si in kept_indices) or (si is None and bool(si_eid))
            # 目标端：同理
            ti_ok = (ti is not None and ti in kept_indices) or (ti is None and bool(ti_eid))
            if si_ok and ti_ok:
                filtered_rels.append(rel)

        skipped_nodes = len(elements) - len(filtered_elements)
        skipped_rels = len(rels) - len(filtered_rels)
        if skipped_nodes or skipped_rels:
            print(f"[Bodhi]   🔍 服务端过滤: 移除 {skipped_nodes} 个节点, {skipped_rels} 个关系", flush=True)

        return {"elements": filtered_elements, "relationships": filtered_rels}

    def _parse_rel_type(self, raw: str) -> str:
        """从 LLM 返回的关系类型解析为带前缀的关系标识（本体图库实时解析）"""
        if not raw:
            return ""
        from .ontology_service import ontology_service
        return ontology_service.resolve_rel_type(raw.strip(), self._last_model_name)

    def _parse_extraction_result(
        self, data: dict, doc_node: SourceDocumentNode,
        excerpts: List[ExcerptNode], offset: int,
    ) -> ExtractionResult:
        """解析 LLM 返回的 JSON，处理 create/merge 动作"""
        nodes = []
        relationships = []
        elements = data.get("elements", [])
        rels = data.get("relationships", [])

        # 构建 source_info
        def find_source(exc_id, source_text):
            for exc in excerpts:
                if exc.id == exc_id:
                    return SourceInfo(
                        document_id=exc.document_id or doc_node.document_id or "",
                        document_title=doc_node.document_title or "",
                        excerpt_id=exc.id or "",
                        excerpt_text=source_text or exc.text[:200],
                        source_position=exc.source_position or "",
                        is_authoritative=False,
                    )
            # 降级：用数字索引
            try:
                idx = int(exc_id) if exc_id else -1
                if 0 <= idx < len(excerpts):
                    exc = excerpts[idx]
                    return SourceInfo(
                        document_id=exc.document_id or doc_node.document_id or "",
                        document_title=doc_node.document_title or "",
                        excerpt_id=exc.id or "",
                        excerpt_text=source_text or exc.text[:200],
                        source_position=exc.source_position or "",
                        is_authoritative=False,
                    )
            except (ValueError, IndexError):
                pass
            return None

        node_id_map = {}  # element_index -> node_id (可能是已有节点的id)
        for idx, elem in enumerate(elements):
            action = elem.get("action", "create")
            exc_id = elem.get("excerpt_id", "")
            source_text = elem.get("source_text", "")
            src = find_source(exc_id, source_text)

            if action == "merge":
                existing_id = elem.get("existing_id")
                if existing_id:
                    node_id_map[idx] = existing_id
                    from ..services.graph_service import graph_service as gs
                    if src:
                        gs.link_source(existing_id, src)
                        if src.excerpt_id:
                            gs.conn.execute(
                                f"MATCH (e:Excerpt), (n:BmmNode) WHERE e.id='{gs._q(src.excerpt_id)}' AND n.id='{gs._q(existing_id)}' "
                                f"MERGE (e)-[:DefinesConcept]->(n) RETURN 1")
                else:
                    # merge 但没有 existing_id → 降级为 create
                    action = "create"

            if action == "create" or (action == "merge" and not elem.get("existing_id")):
                node = self._create_node_from_element(elem, doc_node, excerpts, offset)
                if node:
                    if src:
                        node.sources = [src]
                    nodes.append(node)
                    node_id_map[idx] = node.id

        for rel in rels:
            try:
                rt = self._parse_rel_type(rel.get("type", ""))
                if not rt: continue
                # 解析两端节点：优先用 index，其次用 existing_id
                src_id = None; tgt_id = None
                src_idx = rel.get("source_element_index")
                tgt_idx = rel.get("target_element_index")
                if src_idx is not None and src_idx in node_id_map:
                    src_id = node_id_map[src_idx]
                else:
                    src_id = rel.get("source_existing_id") or ""
                if tgt_idx is not None and tgt_idx in node_id_map:
                    tgt_id = node_id_map[tgt_idx]
                else:
                    tgt_id = rel.get("target_existing_id") or ""
                if src_id and tgt_id:
                    relationship = BmmRelationship(
                        id=f"rel_{uuid.uuid4().hex[:12]}",
                        rel_type=rt,
                        source_node_id=src_id,
                        target_node_id=tgt_id,
                    )
                    exc_id = rel.get("excerpt_id", "")
                    rsrc = find_source(exc_id, rel.get("source_text", ""))
                    if rsrc:
                        relationship.sources = [rsrc]
                    relationships.append(relationship)
            except (KeyError, TypeError) as e:
                print(f"[Bodhi]   关系解析跳过: {e}", flush=True)

        return ExtractionResult(nodes=nodes, relationships=relationships)

    def _create_node_from_element(
        self, elem: dict, doc_node: SourceDocumentNode,
        excerpts: List[ExcerptNode], offset: int,
    ) -> Optional[BmmNode]:
        """从元素字典创建节点（类型由本体图库实时解析，来源在 _parse_extraction_result 中统一设置）"""
        raw_type = elem.get("type", "")
        name = elem.get("name", "")
        if not name:
            return None
        from .ontology_service import ontology_service
        node_type = ontology_service.resolve_node_type(raw_type, self._last_model_name)
        if not node_type:
            # 兜底：保留原始类型（去掉可能的 : 前缀）
            node_type = raw_type.split(":")[-1] if ":" in raw_type else raw_type
        extra = dict(elem.get("extra") or {})
        ai_skill = elem.get("ai_skill") or extra.pop("ai_skill", None) or ""
        node = BmmNode(
            id=f"bmm_{uuid.uuid4().hex[:12]}",
            node_type=node_type,
            name=name,
            definition=elem.get("definition", ""),
            description=elem.get("description", ""),
            ai_skill=ai_skill or None,
            extra=extra,
        )
        return node

    def _find_matching_excerpt(
        self, source_text: str,
        excerpts: List[ExcerptNode],
        doc_node: SourceDocumentNode,
    ) -> Optional[SourceInfo]:
        """在原文摘录中查找匹配的文本，构建 SourceInfo"""
        if not source_text:
            return None

        # 在摘录中搜索匹配文本
        for exc in excerpts:
            if source_text[:50] in exc.text or source_text[-50:] in exc.text:
                return SourceInfo(
                    document_id=doc_node.document_id or "",
                    document_title=doc_node.document_title or "",
                    excerpt_id=exc.id or "",
                    excerpt_text=source_text,
                    source_position=exc.source_position or "",
                    is_authoritative=False,
                )

        # 宽松匹配
        for exc in excerpts:
            # 检查是否有一定的文本重叠
            if self._text_overlap(source_text, exc.text, threshold=0.3):
                return SourceInfo(
                    document_id=doc_node.document_id or "",
                    document_title=doc_node.document_title or "",
                    excerpt_id=exc.id or "",
                    excerpt_text=source_text,
                    source_position=exc.source_position or "",
                    is_authoritative=False,
                )

        return None

    def _text_overlap(self, text1: str, text2: str, threshold: float = 0.3) -> bool:
        """计算两个文本的重叠度"""
        words1 = set(text1[:200])
        words2 = set(text2[:200])
        if not words1 or not words2:
            return False
        overlap = len(words1 & words2) / min(len(words1), len(words2))
        return overlap >= threshold

    def _parse_enum(self, value: Optional[str], enum_cls):
        """安全解析枚举值"""
        if value is None:
            return None
        try:
            return enum_cls(value)
        except ValueError:
            return None

    async def update_api_key(self, scene_code: str = "") -> dict:
        """向 LLM 网关申请新的 API Key（委托给 update_api_key 模块）"""
        from .update_api_key import update_api_key as _do_update
        return await _do_update(self, scene_code)


extraction_service = ExtractionService()
