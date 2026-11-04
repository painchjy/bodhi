#!/usr/bin/env python3
"""PDF 切节质量回归（2026-11-03 内网 49 页 PDF：7 段 / 5 万字巨段 / 空段 / 重复段）。

覆盖：
- `_strip_running_headers`：跨页重复页眉（公司名/logo 文字）剔除；
- `_sections_from_text`：中文编号识别、空段丢弃、重复段去重、超长段二次切、无标题兜底。
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(REPO, "tools", "ke-core"))

import ke_doc_parse as P  # noqa: E402
import ke_docx as D  # noqa: E402


def test_strip_running_headers():
    pages = [{"text": "某某公司\n正文A\n第1页"},
             {"text": "某某公司\n正文B\n第2页"},
             {"text": "某某公司\n正文C\n第3页"}]
    clean, dropped = P._strip_running_headers(pages)
    assert all("某某公司" not in x for x in clean), clean
    assert dropped == 3, dropped
    assert "正文B" in clean[1], clean[1]


def test_cn_headings_recognized():
    text = ("第一章 总则\n正文A\n"
            "一、适用范围\n正文B\n"
            "（一）部署要求\n正文C\n")
    secs = D._sections_from_text(text, "t")
    titles = [s["title"] for s in secs]
    assert any("第一章" in t for t in titles), titles
    assert any(t.startswith("一、") for t in titles), titles
    assert any(t.startswith("（一）") for t in titles), titles
    assert all(s["text"].strip() for s in secs), secs


def test_empty_sections_dropped():
    text = "1 标题\n\n2 标题\n\n正文\n"
    secs = D._sections_from_text(text, "t")
    assert all(s["text"].strip() for s in secs), secs


def test_duplicate_sections_deduped():
    text = "1 A\n同一段正文\n2 B\n同一段正文\n"
    secs = D._sections_from_text(text, "t")
    texts = [s["text"] for s in secs]
    assert texts.count("同一段正文") == 1, texts


def test_long_section_split():
    body = "这是一句正文。" * 2000          # ≈14000 字
    secs = D._sections_from_text("1 大段\n" + body, "t")
    assert len(secs) >= 3, [len(s["text"]) for s in secs]
    assert all(len(s["text"]) <= D._MAX_SECTION_CHARS + 100 for s in secs), \
        [len(s["text"]) for s in secs]
    assert "".join(s["text"] for s in secs) == body, "切分不得丢字"


def test_no_heading_fallback_split():
    body = "这是一句正文。" * 2000
    secs = D._sections_from_text(body, "标题")
    assert len(secs) >= 3 and secs[0]["path"].startswith("全文"), secs[0]


if __name__ == "__main__":
    for fn in (test_strip_running_headers, test_cn_headings_recognized,
               test_empty_sections_dropped, test_duplicate_sections_deduped,
               test_long_section_split, test_no_heading_fallback_split):
        fn()
        print("✓ %s" % fn.__name__)
