#!/usr/bin/env python3
"""02 包内**跨文件符号一致性**（2026-11-02 内网事故回归）。

事故
----
内网 `doc parse` 报 `module 'ke_docx' has no attribute '_section_form_text'` ——
02 包里 `ke_doc_parse.py` 与 `ke_docx.py` **版本错配**：前者还调用旧函数，后者已改成
`sections()`（该函数在 `ed88661`「断 02→04 代码依赖」时删除）。

为什么会有这条测试
------------------
打包按目录整体拷，**不会发现**「A 文件引用了 B 文件里已不存在的名字」。这里做**静态**扫描：
解析 `ke_doc_parse.py` 里 `D.<attr>` / `S.<attr>` 的引用，再解析目标模块的顶层符号，
断言「引用的都导出」—— 零 import、零副作用。

跑法：`python3 tests/test_doc_parse_symbols.py`
"""
import ast
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
CORE = os.path.join(REPO, "tools", "ke-core")


def _top_symbols(path: str) -> set:
    """模块顶层可被 `mod.attr` 访问的名字（含 import 进来的）。"""
    with open(path, encoding="utf-8") as fh:
        tree = ast.parse(fh.read())
    names = set()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name):
                    names.add(t.id)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names.add(node.target.id)
        elif isinstance(node, ast.ImportFrom):
            for a in node.names:
                names.add(a.asname or a.name)
        elif isinstance(node, ast.Import):
            for a in node.names:
                names.add((a.asname or a.name).split(".")[0])
        elif isinstance(node, ast.Try):          # try/except ImportError 里定义的也算
            for sub in node.body:
                if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    names.add(sub.name)
    return names


def test_doc_parse_symbols_exist():
    src_path = os.path.join(CORE, "ke_doc_parse.py")
    with open(src_path, encoding="utf-8") as fh:
        tree = ast.parse(fh.read())

    aliases = {}                                  # 别名 -> 模块名
    for node in tree.body:
        if isinstance(node, ast.Import):
            for a in node.names:
                if a.asname:
                    aliases[a.asname] = a.name

    used = {}                                     # 别名 -> {attr, …}
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
            mod = aliases.get(node.value.id)
            if mod:
                used.setdefault(mod, set()).add(node.attr)

    assert used, "没解析到任何 `X.attr` 引用（ke_doc_parse.py 结构变了？）"
    missing = []
    for mod, attrs in sorted(used.items()):
        path = os.path.join(CORE, mod.replace(".", os.sep) + ".py")
        assert os.path.isfile(path), "模块文件不存在：%s（02 包漏文件）" % path
        have = _top_symbols(path)
        gone = sorted(a for a in attrs if a not in have)
        if gone:
            missing.append("%s → 缺 %s" % (mod, "、".join(gone)))
    if missing:
        raise AssertionError("02 包内版本错配：ke_doc_parse.py 引用了不存在的符号 → %s"
                             % "；".join(missing))


def test_docx_sections_and_images():
    """行为回归：最小 docx（含 2 个编号标题 + 1 张 media 图）→ 切节 ≥2、图能落盘。"""
    import io
    import os
    import tempfile
    import zipfile

    sys.path.insert(0, CORE)
    import ke_docx as D                            # noqa: PLC0415

    doc = (
        '<?xml version="1.0"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        "<w:body>"
        "<w:p><w:r><w:t>1 总则</w:t></w:r></w:p>"
        "<w:p><w:r><w:t>正文一段</w:t></w:r></w:p>"
        "<w:p><w:r><w:t>2 部署架构</w:t></w:r></w:p>"
        "<w:p><w:r><w:t>正文二段</w:t></w:r></w:p>"
        "</w:body></w:document>")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("word/document.xml", doc)
        z.writestr("word/media/pic1.png", b"\x89PNG\r\n\x1a\n" + b"0" * 16)
    raw = buf.getvalue()
    with tempfile.TemporaryDirectory() as td:
        path = os.path.join(td, "a.docx")
        with open(path, "wb") as fh:
            fh.write(raw)
        secs = D.sections(path)
        assert len(secs) >= 2, secs
        imgs = D._docx_images(raw, "t1", root=td)
        assert imgs and imgs[0]["path"].startswith("files/"), imgs
        assert os.path.isfile(os.path.join(td, imgs[0]["path"])), imgs
    plain = D._sections_from_text("1 总则\n正文\n2 部署\n正文2", "标题")
    assert [s["title"] for s in plain] == ["1 总则", "2 部署"], plain


if __name__ == "__main__":
    test_doc_parse_symbols_exist()
    print("✓ ke_doc_parse 引用的 ke_docx / ke_ext_client 符号都存在")
    test_docx_sections_and_images()
    print("✓ docx 切节 + 取图（最小样本）通过")
