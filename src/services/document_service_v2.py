"""文档管理服务 v2 — 文档上传、智能分段与原文摘录管理"""

from __future__ import annotations
import uuid, re
from datetime import date
from pathlib import Path
from typing import Optional, List
import aiofiles
from PyPDF2 import PdfReader
from docx import Document as DocxDocument
from ..config import settings
from ..models.bmm_models import SourceDocumentNode, ExcerptNode, SourceInfo
from .graph_service import graph_service

_HD = [
    re.compile(r'^第[一二三四五六七八九十\d]+[章节]'),
    re.compile(r'^[一二三四五六七八九十]+[、．.]'),
    re.compile(r'^\d+[\.、)）]\s'),
    re.compile(r'^\d+\.\d+[\.\s]'),
    re.compile(r'^（[一二三四五六七八九十\d]+）'),
    re.compile(r'^[A-Z][\.、]\s'),
]

def _is_heading(line: str) -> bool:
    s = line.strip()
    if not s: return False
    if len(s) <= 30 and not re.search(r'[。！？；，]$', s): return True
    for p in _HD:
        if p.match(s): return True
    return False

def _strip_prefix(line: str) -> str:
    s = line.strip()
    for p in _HD:
        m = p.match(s)
        if m: return s[m.end():].strip()
    return s

class DocumentService:
    ALLOWED_EXTENSIONS = {".txt", ".md", ".pdf", ".docx"}

    def __init__(self):
        self.upload_dir = Path(settings.upload_dir)
        self.upload_dir.mkdir(parents=True, exist_ok=True)

    async def upload_document(self, filename: str, content: bytes) -> SourceDocumentNode:
        ext = Path(filename).suffix.lower()
        if ext not in self.ALLOWED_EXTENSIONS:
            raise ValueError(f"不支持格式: {ext}")
        doc_id = f"doc_{uuid.uuid4().hex[:12]}"
        fp = self.upload_dir / f"{doc_id}{ext}"
        async with aiofiles.open(fp, "wb") as f:
            await f.write(content)
        text = await self._extract_text(fp, ext)
        doc_node = SourceDocumentNode(
            name=filename,
            document_id=doc_id, document_title=Path(filename).stem,
            publication_date=date.today(), description="上传的技术方案文档")
        graph_service.create_document_node(doc_node)
        print(f"[Bodhi]   智能分段中...", flush=True)
        excerpts = self._smart_chunk(doc_node, text)
        print(f"[Bodhi]   生成 {len(excerpts)} 个摘录", flush=True)
        for exc in excerpts:
            print(f"[Bodhi]     📝 {exc.name}: {exc.text[:60]}...", flush=True)
        return doc_node

    async def _extract_text(self, fp: Path, ext: str) -> str:
        if ext in (".txt", ".md"):
            async with aiofiles.open(fp, "r", encoding="utf-8") as f:
                return await f.read()
        elif ext == ".pdf":
            r = PdfReader(str(fp))
            return "\n".join([p.extract_text() or "" for p in r.pages])
        elif ext == ".docx":
            d = DocxDocument(str(fp))
            return "\n".join([p.text for p in d.paragraphs if p.text.strip()])
        return ""

    def _smart_chunk(self, doc, text: str) -> List[ExcerptNode]:
        lines = [l for l in text.split("\n") if l.strip()]
        sections = []; ch = ""; cl = []
        for line in lines:
            s = line.strip()
            if _is_heading(s):
                if cl: sections.append((ch, "\n".join(cl)))
                ch = _strip_prefix(s); cl = [s]
            else: cl.append(s)
        if cl: sections.append((ch, "\n".join(cl)))
        excerpts = []
        for hd, ct in sections:
            ps = [p.strip() for p in ct.split("\n") if p.strip()]
            ck = ""; sg = 0
            for p in ps:
                if len(ck) + len(p) > 600 and ck:
                    sg += 1
                    t = f"{hd} ({sg})" if hd else f"段落{sg}"
                    excerpts.append(self._mk(doc, ck.strip(), t, len(excerpts)))
                    ck = p
                else: ck = (ck + "\n" + p).strip()
            if ck.strip():
                sg += 1
                t = hd if (hd and sg == 1) else (f"{hd} ({sg})" if hd else f"段落{len(excerpts)+1}")
                excerpts.append(self._mk(doc, ck.strip(), t, len(excerpts)))
        return excerpts

    def _mk(self, doc, text, title, idx):
        eid = f"exc_{uuid.uuid4().hex[:12]}"
        exc = ExcerptNode(id=eid, name=title,
                          text=text, source_position=f"#{idx+1}", document_id=doc.document_id or "")
        graph_service.create_excerpt_node(exc)
        graph_service.link_excerpt_to_document(eid, doc.document_id or "")
        return exc

    def get_document(self, doc_id): return graph_service.get_document(doc_id)
    def get_document_excerpts(self, doc_id): return graph_service.get_document_excerpts(doc_id)
    def list_documents(self): return graph_service.list_documents()

    def add_excerpt(self, doc_id, title, text, position=""):
        eid = f"exc_{uuid.uuid4().hex[:12]}"
        exc = ExcerptNode(id=eid, name=title,
                          text=text, source_position=position, document_id=doc_id)
        graph_service.create_excerpt_node(exc)
        graph_service.link_excerpt_to_document(eid, doc_id)
        return {"id": eid, "title": title, "text": text, "position": position}

    def update_excerpt(self, excerpt_id, title=None, text=None):
        parts = []
        if title: parts.append(f"e.name='{graph_service._q(title)}'")
        if text: parts.append(f"e.text='{graph_service._q(text)}'")
        if parts:
            graph_service._run(
                f"MATCH (e:Excerpt) WHERE e.id='{graph_service._q(excerpt_id)}' SET {', '.join(parts)} RETURN 1")
        return True

    def delete_excerpt(self, excerpt_id):
        graph_service._run(f"MATCH (e:Excerpt) WHERE e.id='{graph_service._q(excerpt_id)}' DETACH DELETE e RETURN 1")
        return True

document_service = DocumentService()
