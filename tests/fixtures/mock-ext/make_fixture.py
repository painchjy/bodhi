#!/usr/bin/env python3
"""生成**合成**测试用 docx（≈2KB，两节标题；无版权内容）。

为什么不用真实客户文档（2026-10-26 用户裁定）：
  `deploy/mock-ext/{docs,files,images}/` 不进 git / 不进包 ⇒ 单测不能依赖它们；
  这里用标准库 zipfile 造一份最小 docx，随仓库走 ✓。

用法：python3 tests/fixtures/mock-ext/make_fixture.py [输出路径]
"""
import os
import sys
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "it-platform", "files", "doc-fixture.docx")

CT = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
<Default Extension="xml" ContentType="application/xml"/>
<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
</Types>"""
RELS = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>
</Relationships>"""


def _p(text: str, style: str = "") -> str:
    ppr = ('<w:pPr><w:pStyle w:val="%s"/></w:pPr>' % style) if style else ""
    return "<w:p>%s<w:r><w:t xml:space=\"preserve\">%s</w:t></w:r></w:p>" % (ppr, text)


DOC = ("<?xml version=\"1.0\" encoding=\"UTF-8\" standalone=\"yes\"?>\n"
       "<w:document xmlns:w=\"http://schemas.openxmlformats.org/wordprocessingml/2006/main\">"
       "<w:body>"
       + _p("1 概述", "Heading1") + _p("这是一份合成的测试技术方案，用于单测。")
       + _p("2 架构", "Heading1") + _p("系统分三层：接入、服务、数据。")
       + "<w:sectPr/></w:body></w:document>")

os.makedirs(os.path.dirname(OUT), exist_ok=True)
with zipfile.ZipFile(OUT, "w", zipfile.ZIP_DEFLATED) as z:
    z.writestr("[Content_Types].xml", CT)
    z.writestr("_rels/.rels", RELS)
    z.writestr("word/document.xml", DOC)
print("生成 %s（%d 字节）" % (OUT, os.path.getsize(OUT)))
