"""文档管理服务 v2 — 文档上传、智能分段与原文摘录管理"""

from __future__ import annotations
import uuid, re
from datetime import date
from pathlib import Path
from typing import Optional, List
import aiofiles
from docx import Document as DocxDocument
from ..config import settings
from ..models.bmm_models import SourceDocumentNode, ExcerptNode, SourceInfo
from .graph_service import graph_service

class DocumentService:
    # 仅支持带明确标题层级标签的格式：Word 用标题样式，Markdown 用 # 号
    ALLOWED_EXTENSIONS = {".md", ".docx"}

    def __init__(self):
        self.upload_dir = Path(settings.upload_dir)
        self.upload_dir.mkdir(parents=True, exist_ok=True)

    async def upload_document(self, filename: str, content: bytes) -> SourceDocumentNode:
        ext = Path(filename).suffix.lower()
        if ext not in self.ALLOWED_EXTENSIONS:
            raise ValueError(
                f"暂不支持 {ext} 格式导入：该格式无明确的标题/标题层级标签。"
                f"请使用 Word(.docx，标题样式) 或 Markdown(.md，# 号标题) 文档")
        doc_id = f"doc_{uuid.uuid4().hex[:12]}"
        fp = self.upload_dir / f"{doc_id}{ext}"
        async with aiofiles.open(fp, "wb") as f:
            await f.write(content)
        blocks = self._extract_structure(fp, ext)
        doc_node = SourceDocumentNode(
            name=filename,
            document_id=doc_id, document_title=Path(filename).stem,
            publication_date=date.today(), description="上传的技术方案文档")
        graph_service.create_document_node(doc_node)
        print(f"[Bodhi]   智能分段中...", flush=True)
        excerpts = self._smart_chunk(doc_node, blocks)
        print(f"[Bodhi]   生成 {len(excerpts)} 个摘录", flush=True)
        for exc in excerpts:
            print(f"[Bodhi]     📝 {exc.name}: {exc.text[:60]}...", flush=True)
        return doc_node

    def _extract_structure(self, fp: Path, ext: str) -> list:
        """按文档类型提取结构化内容，返回 [(level, heading, content)]"""
        if ext == ".md":
            return self._parse_markdown(fp)
        elif ext == ".docx":
            return self._parse_docx(fp)
        return []

    def _parse_markdown(self, fp: Path) -> list:
        """Markdown：以 # / ## / ### 判定标题与标题层级"""
        text = fp.read_text(encoding="utf-8")
        blocks = []
        cur_level = 0
        cur_heading = ""
        cur_content = []
        for raw in text.split("\n"):
            line = raw.strip()
            m = re.match(r'^(#{1,6})\s+(.*)$', line)
            if m:
                if cur_content or cur_heading:
                    blocks.append((cur_level, cur_heading, "\n".join(cur_content)))
                cur_level = len(m.group(1))
                cur_heading = m.group(2).strip()
                cur_content = []
            elif line:
                cur_content.append(line)
        if cur_content or cur_heading:
            blocks.append((cur_level, cur_heading, "\n".join(cur_content)))
        return blocks

    def _parse_docx(self, fp: Path) -> list:
        """Word：以段落样式 Heading/标题 1/2/3 判定标题与标题层级"""
        d = DocxDocument(str(fp))
        blocks = []
        cur_level = 0
        cur_heading = ""
        cur_content = []
        for p in d.paragraphs:
            text = p.text.strip()
            if not text:
                continue
            level = self._docx_heading_level(p.style.name if p.style else "")
            if level > 0:
                if cur_content or cur_heading:
                    blocks.append((cur_level, cur_heading, "\n".join(cur_content)))
                cur_level = level
                cur_heading = text
                cur_content = []
            else:
                cur_content.append(text)
        if cur_content or cur_heading:
            blocks.append((cur_level, cur_heading, "\n".join(cur_content)))
        return blocks

    @staticmethod
    def _docx_heading_level(style_name: str) -> int:
        """识别 Word 标题样式：Heading 1/2/3 或 标题 1/2/3"""
        m = re.search(r'(heading|标题)\s*(\d+)', (style_name or "").lower())
        return int(m.group(2)) if m else 0

    def _smart_chunk(self, doc, blocks) -> List[ExcerptNode]:
        """智能分段：标题不入摘录，下属段落继承上层标题路径作为 source_position"""
        sections = []
        heading_stack = []  # [(level, heading_text)]
        for level, heading, content in blocks:
            if not heading:
                # 无明确标题的内容不参与分段
                continue
            while heading_stack and heading_stack[-1][0] >= level:
                heading_stack.pop()
            heading_stack.append((level, heading))
            path = " > ".join(h[1] for h in heading_stack)
            sections.append((path, heading, content))

        excerpts = []; idx = 0
        for path, hd, ct in sections:
            ps = [p.strip() for p in ct.split("\n") if p.strip()]
            ck = ""
            for p in ps:
                if len(ck) + len(p) > 600 and ck:
                    idx += 1
                    excerpts.append(self._mk(doc, ck.strip(), self._make_title(hd, ck), path, idx))
                    ck = p
                else: ck = (ck + "\n" + p).strip()
            if ck.strip():
                idx += 1
                excerpts.append(self._mk(doc, ck.strip(), self._make_title(hd, ck), path, idx))
        return excerpts

    def _make_title(self, heading: str, content: str) -> str:
        """标题：优先用章节标题，否则取内容首句"""
        if heading:
            return heading[:200]
        first = re.split(r'[。！？\n]', content.strip())[0].strip()
        if len(first) > 200:
            first = first[:197] + "..."
        return first if first else "未命名"

    def _mk(self, doc, text, title, path, idx):
        eid = f"exc_{uuid.uuid4().hex[:12]}"
        pos = path if path else f"#{idx}"
        # 来源位置放入正文前方，便于在摘录编辑时对来源位置进行标注
        body = f"📍 来源位置：{pos}\n\n{text}" if pos else text
        exc = ExcerptNode(id=eid, name=title,
                          text=body, source_position=pos, document_id=doc.document_id or "")
        graph_service.create_excerpt_node(exc)
        graph_service.link_excerpt_to_document(eid, doc.document_id or "")
        return exc

    def get_document(self, doc_id): return graph_service.get_document(doc_id)
    def get_document_excerpts(self, doc_id): return graph_service.get_document_excerpts(doc_id)
    def list_documents(self): return graph_service.list_documents()

    def add_excerpt(self, doc_id, title, text, position=""):
        if not position:
            position = f"手动添加 #{uuid.uuid4().hex[:8]}"
        eid = f"exc_{uuid.uuid4().hex[:12]}"
        exc = ExcerptNode(id=eid, name=title,
                          text=text, source_position=position, document_id=doc_id)
        graph_service.create_excerpt_node(exc)
        graph_service.link_excerpt_to_document(eid, doc_id)
        return {"id": eid, "title": title, "text": text, "position": position}

    def update_excerpt(self, excerpt_id, title=None, text=None):
        parts = []
        if title is not None: parts.append(f"e.name='{graph_service._q(title)}'")
        if text is not None: parts.append(f"e.text='{graph_service._q(text)}'")
        if parts:
            graph_service.conn.execute(
                f"MATCH (e:Excerpt) WHERE e.id='{graph_service._q(excerpt_id)}' SET {', '.join(parts)} RETURN 1")
        return True

    _CLEAN_ONTO = re.compile(r'\[([^\]]*)\]\(\.\/onto\/[^)]+\)')
    _CLEAN_KN = re.compile(r'\[([^\]]*)\]\(\.\/kn\/[^)]+\)')

    def clean_marks(self, excerpt_ids, kind="all"):
        """清理选中摘录的 wiki 链接标记（onto/kn/all），保留链接文字"""
        gs = graph_service
        cleaned = 0
        for eid in excerpt_ids:
            r = gs.conn.execute(f"MATCH (e:Excerpt) WHERE e.id='{gs._q(eid)}' RETURN e.text")
            cols = r.get_column_names()
            text = None
            while r.has_next():
                text = dict(zip(cols, r.get_next())).get("e.text", "")
            if text is None:
                continue
            new = text
            if kind in ("all", "onto"):
                new = self._CLEAN_ONTO.sub(r'\1', new)
            if kind in ("all", "kn"):
                new = self._CLEAN_KN.sub(r'\1', new)
            if new != text:
                gs.conn.execute(f"MATCH (e:Excerpt) WHERE e.id='{gs._q(eid)}' SET e.text='{gs._q(new)}' RETURN 1")
                cleaned += 1
        return cleaned

    def delete_excerpt(self, excerpt_id):
        graph_service.conn.execute(f"MATCH (e:Excerpt) WHERE e.id='{graph_service._q(excerpt_id)}' DETACH DELETE e RETURN 1")
        return True

document_service = DocumentService()
